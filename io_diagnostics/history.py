"""The host's rolling history: per (entity path, status name), its level spans and every measurement value, each on its
own receive stamp; and the health bar the page draws over one app-wide window (docs/api-v1.md "History").

The honest async rule (Zach, 2026-10-23: "per-status timestamps make out-of-sync arrivals honest"):

- each status holds its last level until it goes stale, ``max(3 x expected_period_s, 5 s)`` after its last arrival
  (contract ``STALE_FACTOR`` / ``STALE_FLOOR_S``, the same rule as ``effective_level``); an event-driven status
  (no period) holds until its next report;
- before a status's first arrival in the window it has **no data**, its own state (``None``), never a level;
- a bin of the bar shows the worst level any status of the row held in it; a bin no status held anything in is no data.

So two checks of one board that arrive 3 s apart never draw a step between them (rqt_robot_monitor's timeline shows
each status's own last level, not a snapshot of whoever spoke last), and a gap is drawn only where a promise was broken.

Bounded, in memory, nothing on disk: at most ``MAX_SERIES`` statuses (the least recently heard is dropped), at most
``MAX_MEASUREMENTS`` measurement keys per status, and at most ``MAX_POINTS`` points in all (a level span or one
measurement value each; past it the longest series loses its oldest point). Points older than the retention, the
window but at least ``DEFAULT_WINDOW_S``, are pruned on arrival. Measured (tests/test_history.py, tracemalloc): a full
history is 16 MB when its points are mostly measurement values and 28 MB when every point is a level change; long,
distinct messages add their own length.
"""

from __future__ import annotations

import bisect
import threading
from collections import deque
from dataclasses import dataclass, field

from contract.record import RANK, STALE, STALE_FACTOR, STALE_FLOOR_S, UNKNOWN, DiagnosticStatus

DEFAULT_WINDOW_S = 900.0  # 15 min (Zach, round 3 brief: "a health bar over a rolling window")
MIN_WINDOW_S = 60.0
MAX_WINDOW_S = 86400.0
MAX_SERIES = 4096
MAX_MEASUREMENTS = 32
MAX_POINTS = 200_000
MAX_BINS = 1000


def stale_after(period: float | None) -> float | None:
    return None if period is None else max(STALE_FACTOR * period, STALE_FLOOR_S)


@dataclass(slots=True)
class _Span:
    """One run of reports with the same level and code, none overdue: [start, last] arrivals, then held for the period."""

    start: float
    last: float
    level: str | None  # None = the status ended here (its source stopped computing it): no data from now on
    period: float | None
    code: str = ""
    message: str = ""


@dataclass
class _Series:
    path: str
    name: str
    spans: deque = field(default_factory=deque)  # _Span, oldest first
    values: dict[str, deque] = field(default_factory=dict)  # measurement key -> deque of (received, value)
    units: dict[str, dict] = field(default_factory=dict)  # measurement key -> its latest unit and limits
    heard: float = 0.0  # latest arrival, for eviction

    def points(self) -> int:
        return len(self.spans) + sum(len(points) for points in self.values.values())


class History:
    def __init__(self, window_s: float = DEFAULT_WINDOW_S, *, max_points: int = MAX_POINTS, max_series: int = MAX_SERIES,
                 max_measurements: int = MAX_MEASUREMENTS):
        self.window_s = window_s
        self.max_points, self.max_series, self.max_measurements = max_points, max_series, max_measurements
        self._series: dict[tuple[str, str], _Series] = {}
        self._sources: dict[object, dict[tuple[str, str], tuple]] = {}  # source -> key -> last signature
        self._points = 0
        self._lock = threading.Lock()

    @property
    def retention_s(self) -> float:
        # WHY at least the default: narrowing the window to look closely must not throw away the last 15 minutes
        return max(self.window_s, DEFAULT_WINDOW_S)

    @property
    def points(self) -> int:
        return self._points

    # --- feeding ---------------------------------------------------------------------------------------------------
    def observe(self, statuses, now: float, source: object = None) -> None:
        """Each status is one ARRIVAL at its ``received`` stamp (else ``stamp``, else ``now``): a new evaluation."""

        with self._lock:
            for status in statuses:
                self._arrive(status, now)
                if source is not None:
                    self._sources.setdefault(source, {})[(status.path, status.name)] = _signature(status)
            self._enforce(now)

    def sync(self, source: object, statuses, now: float) -> None:
        """A set the host RECOMPUTES on every pass (OS checks, the rig rows): a status that changed is an arrival, an
        unchanged event-driven one is not (it holds anyway), and one that is no longer computed ends now."""

        with self._lock:
            known = self._sources.setdefault(source, {})
            present = set()
            for status in statuses:
                key, signature = (status.path, status.name), _signature(status)
                present.add(key)
                if known.get(key) != signature or status.expected_period_s is not None:
                    known[key] = signature
                    self._arrive(status, now)
            for key in set(known) - present:
                del known[key]
                self._end(key, now)
            self._enforce(now)

    def end(self, source: object, now: float) -> None:
        """A source went away (a job whose device left): its statuses have no data from now on."""

        with self._lock:
            for key in self._sources.pop(source, {}):
                self._end(key, now)

    def _arrive(self, status: DiagnosticStatus, now: float) -> None:
        key = (status.path, status.name)
        series = self._series.get(key)
        if series is None:
            series = self._series[key] = _Series(status.path, status.name)
        at = next((stamp for stamp in (status.received, status.stamp) if stamp is not None), now)
        series.heard = max(series.heard, at)
        level = status.level if status.level in RANK else UNKNOWN  # a level nobody defined reads "can't tell"
        spans = series.spans
        last = spans[-1] if spans else None
        if last is not None and at < last.last:
            pass  # WHY: an older report than one already held (a late replay) cannot move a level backwards in time
        elif (last is not None and last.level == level and last.code == status.code
              and last.period == status.expected_period_s
              and (status.expected_period_s is None or at - last.last <= stale_after(status.expected_period_s))):
            last.last, last.message = at, status.message
        else:
            spans.append(_Span(at, at, level, status.expected_period_s, status.code, status.message))
            self._points += 1
        for measurement in status.measurements:
            points = series.values.get(measurement.key)
            if points is None:
                if len(series.values) >= self.max_measurements:
                    continue
                points = series.values[measurement.key] = deque()
            series.units[measurement.key] = {"unit": measurement.unit, "warn_low": measurement.warn_low,
                                             "warn_high": measurement.warn_high, "error_low": measurement.error_low,
                                             "error_high": measurement.error_high}
            point = (at, measurement.value)
            if points and at < points[-1][0]:
                times = [stamp for stamp, _value in points]
                points.insert(bisect.bisect_right(times, at), point)
            else:
                points.append(point)
            self._points += 1
        self._prune(series, now)

    def _end(self, key: tuple[str, str], now: float) -> None:
        series = self._series.get(key)
        if series is None or (series.spans and series.spans[-1].level is None):
            return
        series.spans.append(_Span(now, now, None, None))
        self._points += 1

    def _prune(self, series: _Series, now: float) -> None:
        oldest = now - self.retention_s
        spans = series.spans
        # a span stays while its successor starts inside the retention: it is what holds the level at the window's start
        while len(spans) > 1 and spans[1].start <= oldest:
            spans.popleft()
            self._points -= 1
        for points in series.values.values():
            while points and points[0][0] < oldest:
                points.popleft()
                self._points -= 1

    def _enforce(self, now: float) -> None:
        while len(self._series) > self.max_series:
            key = min(self._series, key=lambda key: self._series[key].heard)
            self._points -= self._series.pop(key).points()
        while self._points > self.max_points:
            longest, spare = None, 0  # the longest series, and how many of its oldest points it can give up
            for series in self._series.values():
                for points in (series.spans, *series.values.values()):
                    # WHY a series keeps its last span: it is what holds the level now
                    can_give = len(points) - 1 if points is series.spans else len(points)
                    if can_give > spare:
                        longest, spare = points, can_give
            if longest is None:
                break
            for _ in range(min(spare, self._points - self.max_points)):
                longest.popleft()
                self._points -= 1

    # --- reading -------------------------------------------------------------------------------------------------
    def keys(self, path: str, *, below: bool = False) -> list[tuple[str, str]]:
        with self._lock:
            return [key for key in self._series if key[0] == path or (below and key[0].startswith(path + "/"))]

    def timeline(self, key: tuple[str, str], start: float, end: float) -> list[dict]:
        """``[{"from", "to", "level", "code", "message"}]`` covering [start, end] exactly; ``level`` None = no data."""

        with self._lock:
            series = self._series.get(key)
            spans = list(series.spans) if series else []
        pieces: list[tuple[float, float, str | None, str, str]] = []
        for index, span in enumerate(spans):
            following = spans[index + 1].start if index + 1 < len(spans) else end
            if span.level is None:
                pieces.append((span.start, following, None, "", ""))
                continue
            limit = stale_after(span.period)
            held = following if limit is None else min(following, span.last + limit)
            pieces.append((span.start, held, span.level, span.code, span.message))
            if held < following:
                pieces.append((held, following, STALE, "", f"no update for {limit:g} s (last: {span.message})"
                               if span.message else f"no update for {limit:g} s"))
        if not pieces or pieces[0][0] > start:
            pieces.insert(0, (start, pieces[0][0] if pieces else end, None, "", ""))
        out: list[dict] = []
        for begin, finish, level, code, message in pieces:
            begin, finish = max(begin, start), min(finish, end)
            if finish <= begin:
                continue
            if out and out[-1]["level"] == level and out[-1]["code"] == code and out[-1]["to"] == begin:
                out[-1]["to"], out[-1]["message"] = finish, message
                continue
            out.append({"from": begin, "to": finish, "level": level, "code": code, "message": message})
        return out

    def series(self, key: tuple[str, str], start: float, end: float) -> list[dict]:
        """Every measurement of one status in [start, end]: ``[{"key", "unit", limits..., "points": [[t, v], ...]}]``."""

        with self._lock:
            found = self._series.get(key)
            if found is None:
                return []
            values = {name: [list(point) for point in points if start <= point[0] <= end] for name, points in found.values.items()}
            units = {name: dict(meta) for name, meta in found.units.items()}
        return [{"key": name, **units[name], "points": points} for name, points in values.items()]

    def period(self, key: tuple[str, str]) -> float | None:
        with self._lock:
            series = self._series.get(key)
            live = [span for span in (series.spans if series else ()) if span.level is not None]
            return live[-1].period if live else None


def bar(timelines: list[list[dict]], start: float, end: float, bins: int) -> list[str | None]:
    """The worst level any timeline held in each of ``bins`` equal bins of [start, end]; None = no data in that bin."""

    width = (end - start) / bins
    out: list[str | None] = []
    for number in range(bins):
        low, high = start + number * width, start + (number + 1) * width
        worst = None
        for pieces in timelines:
            for piece in pieces:
                if piece["level"] is None or piece["to"] <= low or piece["from"] >= high:
                    continue
                if worst is None or RANK[piece["level"]] > RANK[worst]:
                    worst = piece["level"]
        out.append(worst)
    return out


def _signature(status: DiagnosticStatus) -> tuple:
    return (status.level, status.code, status.message, status.expected_period_s,
            tuple((measurement.key, measurement.value) for measurement in status.measurements))


__all__ = ["History", "bar", "stale_after", "DEFAULT_WINDOW_S", "MIN_WINDOW_S", "MAX_WINDOW_S", "MAX_SERIES",
           "MAX_MEASUREMENTS", "MAX_POINTS", "MAX_BINS"]
