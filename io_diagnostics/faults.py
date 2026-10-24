"""The host's fault memory: a thin use of ``contract.record.FaultManager`` (the rules live there, not here).

Fed every NEW status evaluation once, on arrival (``runner.JobCache.apply`` decides what is new, so a replay that
re-sends its recording is one report, not N). Lifecycle transitions come back as ``Event`` records
(``FaultConfirmed`` / ``FaultHealed``) for the activity log. A fault never feeds back into a row's level: the
self-report stands (docs/diagnostics-record-v0.md rule 5).
"""

from __future__ import annotations

import dataclasses
import threading

from contract.record import CONFIRMED, PENDING, Event, Fault, FaultManager, to_dict


# WHY: a confirmation in the host's first reports is the memory catching up with a state that was already there (a WARN
# confirms on its 3rd report, so four of them land in the same second at start), not something that happened then.
# The activity log groups those into one "Found at start" row (logview.py); the mark is written when the event is made,
# because only the host that made it knows whether it was still starting.
FOUND_AT_START_S = 30.0


class Faults:
    def __init__(self, manager: FaultManager | None = None, start_window_s: float = FOUND_AT_START_S):
        self.manager = manager or FaultManager()
        self.events: list[Event] = []
        self.start_window_s = start_window_s
        self._first: float | None = None  # the host clock at the first report this process saw
        self._lock = threading.Lock()

    def _marked(self, events: list[Event], now: float) -> list[Event]:
        """Each lifecycle event with an id that names the fact (``fault:<code>:<path>|<fault>|<stamp>``), and
        ``args["at_start"]`` = when this process first heard a report on a confirmation made within the window.

        WHY an id of its own: the manager's sequence restarts with every process and depends on which plugin answered
        first, so ``fault_manager#3`` is not the same fact twice; the log (eventlog.py) keeps events across restarts."""

        starting = self._first is not None and now - self._first <= self.start_window_s
        out = []
        for event in events:
            args = {**event.args, "id": f"fault:{event.code}:{event.path}|{event.args.get('fault', '')}|{event.stamp or 0:.3f}"}
            if starting and event.code == "FaultConfirmed":
                args["at_start"] = round(self._first, 3)
            out.append(dataclasses.replace(event, args=args))
        return out

    def observe(self, statuses, now: float) -> list[Event]:
        events: list[Event] = []
        with self._lock:
            statuses = list(statuses)
            if self._first is None and statuses:
                self._first = now
            for status in statuses:
                events += self.manager.observe(status, now)
            events += self.manager.tick(now)
            events = self._marked(events, now)
            self.events += events
        return events

    def tick(self, now: float) -> list[Event]:
        with self._lock:
            events = self._marked(self.manager.tick(now), now)
            self.events += events
        return events

    def all(self) -> list[Fault]:
        with self._lock:
            return [dataclasses.replace(fault) for fault in self.manager.faults]

    def open_by_path(self) -> dict[str, int]:
        """Faults a row should count: pending or confirmed (healed and cleared ones are history)."""

        counts: dict[str, int] = {}
        for fault in self.all():
            if fault.status in (PENDING, CONFIRMED):
                counts[fault.path] = counts.get(fault.path, 0) + 1
        return counts

    def records(self) -> list[dict]:
        return [to_dict(fault) for fault in self.all()]
