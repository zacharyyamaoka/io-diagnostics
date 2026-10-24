"""The Activity log's read model (docs/activity-log-v1.md): the host's events folded into the rows a person reads.

The store (``eventlog.py``) holds facts as they happened; this module is a PURE function of them, so a restart, a
second host and a test all see the same rows. What it does, in the order a row is decided:

1. **Fault lifecycle folds onto its cause.** A ``FaultConfirmed`` is the fault memory saying what the producer's own
   event (``BoardFault``, ``BusErrorPassive``) already said, so it rides on that row as a "fault confirmed" badge
   (same entity, the event no more than ``CAUSE_WINDOW_S`` before it, warn or worse). One without a cause that the
   host confirmed in its first reports is the host catching up, not something that happened then: those join one
   "Found at start" row per host process. Any other is a row of its own. Healed and cleared stay rows (a later
   occurrence), and the confirmed badge says when it healed.
2. **Status transitions** (``LevelChanged``). A warn or error one rides on its fault's row. Silence (to stale or
   unknown) is its own warn row only when no plug or unplug on its entity's path explains it. Every other transition
   is debug.
3. **A run is one row.** ``RunStarted`` + ``RunEnded`` become ``Run``: its stamp is the start, its ``last_stamp`` the
   end, its state running / done / failed / stopped, and its output is the Raw channel under it (web/runs/r4.js).
4. **Identical repeats are one row with a count**: the same code, source, path, message and severity, each no more
   than ``COALESCE_GAP_S`` after the last. ``ProbeBlocked`` appears 5 times in a minute while the bus is busy.

The Raw channel is the uncoded lines: ``kernel_raw`` reads the kernel log's lines for one entity. Severity floors,
entity scope and the channel are the API's query (``select``); typed search and the filters are the page's.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Iterable

from contract.record import Event
from io_diagnostics import activity as activity_module
from io_diagnostics.activity import event_id

SEVERITY_ORDER = {"debug": 0, "info": 1, "warn": 2, "error": 3, "fatal": 4}
FLOORS = tuple(SEVERITY_ORDER)
CHANNELS = ("activity", "raw", "all")
SCOPES = ("children", "self", "host")
COALESCE_GAP_S = 60.0
CAUSE_WINDOW_S = 10.0  # the producer's event and the confirmation of the fault it caused are seconds apart at most
TRANSITION_FAULT_S = 300.0  # a WARN confirms on its 3rd report: its transition leads the fault by a few periods
EXPLAIN_WINDOW_S = 120.0  # a plug or unplug this long before a silence still explains it (stale = 3 periods late)
LIFECYCLE = ("FaultConfirmed", "FaultHealed", "FaultCleared")
RUN_CODES = ("RunStarted", "RunEnded")
SILENCE = ("stale", "unknown")
PLUG_CODES = ("Plugged", "Unplugged", "ReEnumerated", "Flapped", "Plugging", "Unplugging")
# state changes and boundaries: two of them a minute apart are two facts, never "x2"
NO_COALESCE = {"Booted", "ShutDown", *PLUG_CODES, *LIFECYCLE, *RUN_CODES, "Run", "FoundAtStart"}
NOT_A_CAUSE = {"Booted", "ShutDown", "LevelChanged", "Run", "FoundAtStart", *LIFECYCLE, *RUN_CODES}


@dataclass
class Row:
    """One line of the log: ``event`` as shown (first stamp, ``count``, ``last_stamp``, the severity the rules gave it),
    the events it folds (``members``) and the lifecycle badges on it."""

    event: Event
    members: list[Event] = field(default_factory=list)
    badges: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return event_id(self.event)

    @property
    def when(self) -> float:
        return self.event.last_stamp if self.event.last_stamp is not None else (self.event.stamp or 0.0)


def _args(event: Event) -> dict:
    return event.args if isinstance(event.args, dict) else {}


def _order(event: Event) -> tuple[float, int]:
    return (event.stamp or 0.0, event.seq or 0)


def _at(path: str, ancestor: str) -> bool:
    """``path`` is ``ancestor`` or below it."""

    return path == ancestor or path.startswith(ancestor + "/")


def fault_key(event: Event) -> tuple[str, str]:
    return (event.path, str(_args(event).get("fault", "")))


# ----- the rules ---------------------------------------------------------------------------------------------------
def _cause(fault: Event, candidates: list[Event]) -> Event | None:
    """The producer's own event the confirmation repeats: same entity, shortly before, warn or worse; the closest."""

    best = None
    for event in candidates:
        if event.path != fault.path or SEVERITY_ORDER.get(event.severity, 1) < SEVERITY_ORDER["warn"]:
            continue
        gap = (fault.stamp or 0.0) - (event.stamp or 0.0)
        if not -1.0 <= gap <= CAUSE_WINDOW_S:
            continue
        rank = (-abs(gap), SEVERITY_ORDER[event.severity])
        if best is None or rank > best[0]:
            best = (rank, event)
    return best[1] if best else None


def _explained(silence: Event, plugs: list[Event]) -> bool:
    for plug in plugs:
        if plug.path in ("", "host") and silence.path != "host":
            continue  # a plug the host could not place explains nothing in particular
        related = _at(silence.path, plug.path) or bool(plug.hardware_id and plug.hardware_id == silence.hardware_id)
        if related and silence.stamp - EXPLAIN_WINDOW_S <= (plug.stamp or 0.0) <= silence.stamp + 5.0:
            return True
    return False


def _badge(event: Event, state: str = "confirmed", ended: float | None = None) -> dict:
    args = _args(event)
    return {"id": event_id(event), "code": event.code, "label": "fault confirmed", "stamp": event.stamp, "severity": event.severity,
            "fault": str(args.get("fault", "")), "occurrence": args.get("occurrence"), "state": state, "ended": ended,
            "message": event.message}


def _run_row(started: Event | None, ended: Event | None, active: bool | None) -> Row:
    base = ended or started
    args = _args(started) if started else _args(ended)
    run_id = str(args.get("run_id", ""))
    if ended is not None:
        state = str(_args(ended).get("state", "done"))
        severity, message = ended.severity, ended.message
    elif active is False:
        state, severity, message = "interrupted", "warn", f"{started.message} (the host stopped while it ran)"
    else:
        state, severity, message = "running", "info", started.message
    shown = Event(code="Run", stamp=(started or ended).stamp, severity=severity, message=message, path=base.path,
                  hardware_id=base.hardware_id, seq=base.seq, source=base.source, received=(started or ended).received,
                  last_stamp=ended.stamp if ended is not None else None,
                  args={"id": f"run:{run_id}", "run_id": run_id, "state": state, "action": args.get("action"), "node": args.get("node"),
                        "plugin": args.get("plugin"), "danger": args.get("danger"),
                        "duration_s": _args(ended).get("duration_s") if ended else None,
                        "exit_code": _args(ended).get("exit_code") if ended else None,
                        "stop": _args(ended).get("stop") if ended else None})
    return Row(shown, [event for event in (started, ended) if event is not None])


def _found_at_start(start: object, faults: list[Event]) -> Row:
    faults = sorted(faults, key=_order)
    worst = max(faults, key=lambda event: SEVERITY_ORDER.get(event.severity, 1)).severity
    codes = list(dict.fromkeys(str(_args(event).get("fault", "")) for event in faults))
    listed = ", ".join(codes[:4]) + (f" and {len(codes) - 4} more" if len(codes) > 4 else "")
    count = len(faults)
    first = faults[0]
    shown = Event(code="FoundAtStart", stamp=max(event.stamp or 0.0 for event in faults), severity=worst,
                  message=f"Found at start: {count} fault{'s' if count != 1 else ''} already confirmed ({listed})",
                  path="host", seq=first.seq, source=first.source, received=first.received,
                  args={"id": f"found-at-start:{start}", "faults": [{"id": event_id(event), "path": event.path, "fault": _args(event).get("fault", ""),
                                                                   "severity": event.severity, "message": event.message} for event in faults]})
    return Row(shown, faults)


def fold(events: Iterable[Event], *, active_runs: Iterable[str] | None = None) -> list[Row]:
    """The events as the Activity log's rows, newest first. ``active_runs``: the run ids still going (None: unknown,
    every unfinished run counts as running)."""

    ordered = sorted(events, key=_order)
    active = None if active_runs is None else set(active_runs)
    lifecycle = [event for event in ordered if event.code in LIFECYCLE]
    confirmed = [event for event in lifecycle if event.code == "FaultConfirmed"]
    transitions = [event for event in ordered if event.code == "LevelChanged"]
    run_events = [event for event in ordered if event.code in RUN_CODES]
    plugs = [event for event in ordered if event.code in PLUG_CODES]
    ordinary = [event for event in ordered if event.code not in LIFECYCLE and event.code != "LevelChanged" and event.code not in RUN_CODES]

    # 1. the confirmations: onto a cause, into a start-up group, or a row of their own
    causes = [event for event in ordinary if event.code not in NOT_A_CAUSE and not _args(event).get("provisional")]
    cause_of: dict[str, Event] = {}
    started_groups: dict[object, list[Event]] = {}
    alone: list[Event] = []
    for fault in confirmed:
        cause = _cause(fault, causes)
        if cause is not None:
            cause_of[event_id(fault)] = cause
        elif _args(fault).get("at_start") is not None:
            started_groups.setdefault(_args(fault)["at_start"], []).append(fault)
        else:
            alone.append(fault)
    closing: dict[tuple[str, str], list[Event]] = {}
    for event in lifecycle:
        if event.code != "FaultConfirmed":
            closing.setdefault(fault_key(event), []).append(event)

    def ending(fault: Event) -> tuple[str, float | None]:
        """How this occurrence ended: the first healed / cleared of the same fault after it and before the next confirmation."""

        later = [e for e in confirmed if fault_key(e) == fault_key(fault) and _order(e) > _order(fault)]
        limit = _order(later[0]) if later else (float("inf"), 0)
        for event in closing.get(fault_key(fault), []):
            if _order(fault) < _order(event) < limit:
                return ("healed" if event.code == "FaultHealed" else "cleared"), event.stamp
        return "confirmed", None

    # 2. coalesce the ordinary events (a cause keeps its own row: it carries a fault)
    rows: list[Row] = []
    open_rows: dict[tuple, Row] = {}
    cause_ids = {event_id(event) for event in cause_of.values()}
    for event in ordinary:
        key = (event.code, event.source, event.path, event.message, event.severity)
        held = open_rows.get(key)
        mergeable = event.code not in NO_COALESCE and event_id(event) not in cause_ids and not _args(event).get("provisional")
        if mergeable and held is not None and (event.stamp or 0.0) - held.when <= COALESCE_GAP_S and event_id(held.event) not in cause_ids:
            held.event = dataclasses.replace(held.event, count=held.event.count + max(event.count, 1), last_stamp=event.stamp)
            continue
        row = Row(event)
        rows.append(row)
        if mergeable:
            open_rows[key] = row
    by_id = {row.id: row for row in rows}

    # 3. the badges, the start-up groups and the confirmations that stand alone
    landed: dict[str, str] = {}  # a confirmation's id -> the row it landed on
    for fault in confirmed:
        cause = cause_of.get(event_id(fault))
        if cause is None:
            continue
        row = by_id[event_id(cause)]
        state, ended = ending(fault)
        row.badges.append(_badge(fault, state, ended))
        row.members.append(fault)
        landed[event_id(fault)] = row.id
    for start, faults in started_groups.items():
        row = _found_at_start(start, faults)
        rows.append(row)
        for fault in faults:
            landed[event_id(fault)] = row.id
    for fault in alone:
        state, ended = ending(fault)
        shown = dataclasses.replace(fault, args={**_args(fault), "state": state, "ended": ended})
        rows.append(Row(shown))
        landed[event_id(fault)] = event_id(fault)
    for event in lifecycle:
        if event.code == "FaultConfirmed":
            continue
        opened = [fault for fault in confirmed if fault_key(fault) == fault_key(event) and _order(fault) < _order(event)]
        link = landed.get(event_id(opened[-1])) if opened else None
        rows.append(Row(dataclasses.replace(event, args={**_args(event), "confirmed_row": link}) if link else event))

    # 4. the transitions
    row_index = {row.id: row for row in rows}
    for event in transitions:
        args = _args(event)
        to = str(args.get("to", ""))
        code = str(args.get("status_code") or args.get("status") or "")
        if to in ("warn", "error"):
            following = [fault for fault in confirmed if fault.path == event.path and str(_args(fault).get("fault", "")) in (code, str(args.get("status", "")))
                         and -1.0 <= (fault.stamp or 0.0) - (event.stamp or 0.0) <= TRANSITION_FAULT_S]
            target = row_index.get(landed.get(event_id(following[0]), "")) if following else None
            if target is not None:
                target.members.append(event)
                continue
            shown = dataclasses.replace(event, severity="debug")
        elif to in SILENCE and not _explained(event, plugs):
            shown = dataclasses.replace(event, severity="warn")
        else:
            shown = dataclasses.replace(event, severity="debug")
        rows.append(Row(shown))

    # 5. the runs: one row each
    runs: dict[str, dict[str, Event]] = {}
    for event in run_events:
        runs.setdefault(str(_args(event).get("run_id", event_id(event))), {})["started" if event.code == "RunStarted" else "ended"] = event
    for run_id, pair in runs.items():
        rows.append(_run_row(pair.get("started"), pair.get("ended"), None if active is None else run_id in active))

    # 6. coalesce the debug transitions the same way (they are many and identical when a check flaps)
    rows = _merge_transitions(rows)
    rows.sort(key=lambda row: (row.when, row.event.seq or 0), reverse=True)
    return rows


def _merge_transitions(rows: list[Row]) -> list[Row]:
    """Identical debug transitions a few seconds apart (a check flapping) become one row with a count."""

    out: list[Row] = []
    open_rows: dict[tuple, Row] = {}
    for row in sorted(rows, key=lambda row: (row.event.stamp or 0.0, row.event.seq or 0)):
        event = row.event
        if event.code != "LevelChanged" or row.badges:
            out.append(row)
            continue
        key = (event.path, event.message, event.severity)
        held = open_rows.get(key)
        if held is not None and (event.stamp or 0.0) - held.when <= COALESCE_GAP_S:
            held.event = dataclasses.replace(held.event, count=held.event.count + 1, last_stamp=event.stamp)
            continue
        open_rows[key] = row
        out.append(row)
    return out


# ----- selecting ---------------------------------------------------------------------------------------------------
def in_scope(path: str, hardware_id: str, entity: str, entity_hardware_id: str, scope: str) -> bool:
    """Is an event of ``path`` in the log of ``entity``? ``children``: the entity and everything below it, plus the
    rows that name its hardware; ``self``: the entity alone; ``host``: children plus the ports, hubs and controller it
    hangs off (every ancestor up to the host)."""

    if not entity:
        return True
    if entity_hardware_id and hardware_id == entity_hardware_id:
        return True
    if scope == "self":
        return path == entity
    if _at(path, entity):
        return True
    return scope == "host" and bool(path) and _at(entity, path)


def severity_at_least(severity: str, floor: str) -> bool:
    return SEVERITY_ORDER.get(severity, 1) >= SEVERITY_ORDER.get(floor, 0)


def select(rows: list[Row], *, entity: str = "", entity_hardware_id: str = "", scope: str = "children", floor: str = "debug",
           types: Iterable[str] | None = None) -> list[Row]:
    wanted = set(types or ())
    return [row for row in rows
            if severity_at_least(row.event.severity, floor)
            and in_scope(row.event.path, row.event.hardware_id, entity, entity_hardware_id, scope)
            and (not wanted or activity_module.event_type(row.event.code) in wanted)]


def badge_json(badge: dict, utc_offset_s: int | None) -> dict:
    stamp, ended = badge.get("stamp"), badge.get("ended")
    when, done = activity_module.iso(stamp, utc_offset_s), activity_module.iso(ended, utc_offset_s)
    return {**badge, "time": when[11:19] if when else "", "ended_time": done[11:19] if done else ""}


def row_json(row: Row, utc_offset_s: int | None, names: dict[str, str] | None = None) -> dict:
    members = []
    for member in row.members:
        when = activity_module.iso(member.stamp, utc_offset_s)
        members.append({"id": event_id(member), "code": member.code, "severity": member.severity, "message": member.message,
                        "path": member.path, "name": (names or {}).get(member.path, ""), "stamp": member.stamp,
                        "time": when[11:19] if when else "", "args": {key: value for key, value in _args(member).items() if key != "id"}})
    return activity_module.event_json(row.event, utc_offset_s, names, badges=[badge_json(badge, utc_offset_s) for badge in row.badges],
                                      members=members)


# ----- the Raw channel: the kernel's lines --------------------------------------------------------------------------
_KERNEL_LINE = re.compile(r"^(?P<stamp>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?)\s+\S+\s+kernel:\s(?P<text>.*)$")
_KERNEL_PORT = re.compile(r"^[\w-]+ (?P<port>\d+-\d+(?:\.\d+)*)(?::\d+\.\d+)?:")


@dataclass(frozen=True)
class RawLine:
    stamp: float
    port: str
    text: str
    severity: str
    boot: float
    index: int


def parse_kernel_line(line: str, boot: float, index: int) -> RawLine | None:
    from io_facts import kernel_log

    found = _KERNEL_LINE.match(line)
    if not found:
        return None
    import datetime as dt

    try:
        stamp = dt.datetime.fromisoformat(found["stamp"].replace(",", ".")).timestamp()
    except ValueError:
        return None
    text = found["text"]
    port = _KERNEL_PORT.match(text)
    body = text[port.end():].strip() if port else text  # kernel_log's patterns are written against the words after "usb 1-7:"
    severity = next((severity for pattern, _code, severity in kernel_log.USB_ERRORS if pattern.search(body)), "info")
    return RawLine(stamp, port["port"] if port else "", text, severity if severity in ("warn", "error") else "info", boot, index)


class RawKernel:
    """The boot's kernel lines, parsed once and extended as the follower appends (the lines list only ever grows,
    except that it keeps the newest ``max_lines``; a shrink re-parses)."""

    def __init__(self):
        self._parsed: dict[float, tuple[int, list[RawLine]]] = {}

    def lines(self, boots) -> list[RawLine]:
        out: list[RawLine] = []
        for boot in boots:
            count, held = self._parsed.get(boot.start, (0, []))
            if len(boot.lines) < count:
                count, held = 0, []
            for index in range(count, len(boot.lines)):
                parsed = parse_kernel_line(boot.lines[index], boot.start, index)
                if parsed is not None:
                    held.append(parsed)
            self._parsed[boot.start] = (len(boot.lines), held)
            out += held
        return out


def raw_event(line: RawLine, path: str) -> Event:
    return Event(code="", stamp=line.stamp, severity=line.severity, message=line.text, path=path, source="kernel", received=line.stamp,
                 args={"id": f"raw:{line.boot:.0f}:{line.index}", "port": line.port})


def raw_json(line: RawLine, path: str, utc_offset_s: int | None, names: dict[str, str] | None = None) -> dict:
    out = activity_module.event_json(raw_event(line, path), utc_offset_s, names, channel="raw")
    out["kind"], out["type"] = "kernel", "raw"
    return out
