"""The entity page's data, as pure functions over a job's cache (docs/state-config-v1.md): State rows with their recent
series, Configuration rows with their mismatch, the declared image streams, the watch's plain-words pause reason and
the quiet extra tabs a driver declares. ``Host`` owns the clock, the locks and the processes; nothing here touches one.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from contract.record import STALE_FACTOR, STALE_FLOOR_S, Measurement
from io_diagnostics import probe as probe_module
from io_diagnostics.runner import HOST_SOURCES, JobCache

STREAM_KINDS = ("image",)


# ----- Configuration ----------------------------------------------------------------------------------------------
def same_value(value: Any, expected: Any) -> bool:
    """Whether a read setting equals what is expected: numbers within 1e-9 relative, everything else as text."""

    numeric = (int, float)
    if isinstance(value, bool) or isinstance(expected, bool):
        return value is expected or (isinstance(value, bool) and isinstance(expected, bool) and value == expected)
    if isinstance(value, numeric) and isinstance(expected, numeric):
        return math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-12)
    return str(value) == str(expected)


def config_rows(cache: JobCache, rel: str, now: float) -> list[dict]:
    """The settings read for one entity (path relative to its device), in the order the driver sent them."""

    rows = []
    for (path, _key), config in cache.configs.items():
        if path != rel:
            continue
        differs = config.expected is not None and not same_value(config.value, config.expected)
        unsaved = config.stored is not None and not same_value(config.value, config.stored)
        rows.append({"key": config.key, "value": config.value, "unit": config.unit, "group": config.group,
                     "expected": config.expected, "expected_source": config.expected_source, "differs": differs,
                     "stamp": config.stamp, "age_s": round(now - config.stamp, 3) if config.stamp is not None else None,
                     "source": config.source, "writable": config.writable, "type": config.type,
                     "range": list(config.range) if config.range else None, "choices": list(config.choices), "default": config.default,
                     "stored": config.stored, "safety": config.safety, "unsaved": unsaved})
    return rows


# ----- State ------------------------------------------------------------------------------------------------------
def _row(measurement: Measurement, points) -> dict:
    return {"key": measurement.key, "value": measurement.value, "unit": measurement.unit, "text": measurement.render(),
            "meaning": measurement.meaning, "level": measurement.level, "stamp": measurement.stamp,
            "warn_low": measurement.warn_low, "warn_high": measurement.warn_high, "error_low": measurement.error_low,
            "error_high": measurement.error_high, "points": points,
            "source": {"path": measurement.source.path, "key": measurement.source.key} if measurement.source is not None else None}


def state_groups(cache: JobCache, rel: str, now: float, since: int = 0) -> list[dict]:
    """The entity's State: one group per ``state`` record name, then one per status that carries measurements (the host's
    own statuses are not lifted), each row with the points that arrived after serial ``since`` (0 = all it holds)."""

    groups = []
    sources = [("state", name, record.received, record.expected_period_s, record.source, record.measurements)
               for (path, name), record in cache.states.items() if path == rel]
    sources += [("status", name, status.received, status.expected_period_s, status.source, status.measurements)
                for (path, name), status in cache.statuses.items()
                if path == rel and status.measurements and not status.source.startswith(HOST_SOURCES)]
    for kind, name, received, period, source, measurements in sources:
        stale_after = max(STALE_FACTOR * period, STALE_FLOOR_S) if period else None
        age = round(now - received, 3) if received is not None else None
        rows = []
        for measurement in measurements:
            points = cache.series.get((rel, name, measurement.key), ())
            rows.append(_row(measurement, [[at, value] for at, value, serial in points if serial > since]))
        groups.append({"name": name, "kind": kind, "source": source, "received": received, "age_s": age,
                       "expected_period_s": period, "stale_after_s": stale_after,
                       "stale": bool(stale_after is not None and age is not None and age > stale_after), "rows": rows})
    return groups


# ----- image streams ----------------------------------------------------------------------------------------------
def declared_streams(describe: dict | None, rel: str) -> list[dict]:
    """The streams a plugin's ``describe`` declares for one entity: ``[{name, kind, title, colormap, bands, ...}]``."""

    out = []
    for item in (describe or {}).get("streams") or []:
        if isinstance(item, dict) and str(item.get("path", "")) == rel:
            for stream in item.get("streams") or []:
                if isinstance(stream, dict) and stream.get("name") and stream.get("kind", "image") in STREAM_KINDS:
                    out.append(dict(stream))
    return out


STREAMS_STATUS = ": watch streams"


def streams_not_streamed(cache: JobCache, rel: str) -> tuple[dict[str, str], float | None]:
    """``({stream: why it is not streamed}, when that was said)`` from the newest ``<camera>: watch streams`` status at ``rel``
    (a driver's own record: ``values`` ``not_streaming`` is a comma list, ``why_not`` the plain sentence). ``({}, None)`` when
    the driver never said.

    WHY: a driver declares every stream its camera model can have, and only the running watch knows which ones the link
    accepted; without this a tile for a stream that never starts waits for its first frame for ever."""

    newest = None
    for (path, name), status in cache.statuses.items():
        if path == rel and name.endswith(STREAMS_STATUS) and (newest is None or (status.received or 0.0) >= (newest.received or 0.0)):
            newest = status
    if newest is None:
        return {}, None
    values = {pair[0]: pair[1] for pair in newest.values}
    why = values.get("why_not") or "not streamed by this camera on this link"
    return {name.strip(): why for name in values.get("not_streaming", "").split(",") if name.strip()}, newest.received


def stream_rows(describe: dict | None, cache: JobCache, rel: str, now: float, fps_of) -> list[dict]:
    rows = []
    unstreamed, said_at = streams_not_streamed(cache, rel)
    for stream in declared_streams(describe, rel):
        frame = cache.frames.get((rel, stream["name"]))
        # a frame that arrived after the status wins: the picture is there, whatever was said before it
        off = unstreamed.get(stream["name"])
        if off is not None and frame is not None and frame.received is not None and said_at is not None and frame.received > said_at:
            off = None
        rows.append({"name": stream["name"], "kind": stream.get("kind", "image"), "title": stream.get("title") or stream["name"],
                     "colormap": stream.get("colormap"), "bands": stream.get("bands"), "preview_band": stream.get("preview_band"),
                     "width": frame.width if frame else None, "height": frame.height if frame else None,
                     "seq": frame.seq if frame else None, "stamp": frame.stamp if frame else None,
                     "band": frame.band if frame else None,
                     "age_s": round(now - frame.received, 3) if frame and frame.received is not None else None,
                     "fps": fps_of(rel, stream["name"]), "off": off})
    return rows


# ----- the pause reason in plain words ----------------------------------------------------------------------------
_HELD = (re.compile(r"held by (\S+) pid (\d+)"), re.compile(r"open by (\d+) (\S+)"), re.compile(r"blocked by pid (\d+) \(([^)]*)\)"),
         re.compile(r"pid (\d+) \(([^)]*)\)"))
_WHAT = {"port_free": "the port", "camera_free": "the camera", "bus_lock_free": "the rack bus lock"}


def program_in(command: str) -> str:
    """A holder's command line as a person names it: ``/venv/bin/python3 -m pkg.mod.fivebar_run`` -> ``fivebar_run``,
    ``python3 scripts/update_moteus_firmware.py --port x`` -> ``update_moteus_firmware.py``, ``/abs/bin/tview`` -> ``tview``;
    a bare name stays. The same naming as bam_ws moteus_servo.diagnostics.program_of.

    WHY: the pause line read "/home/bam/.../.venv/bin/python3 -m bam_rack_twin.hardware.fivebar_run has the rack bus lock"."""

    words = command.split()
    if len(words) <= 1 and "/" not in command:
        return command
    if not words:
        return command
    first = Path(words[0]).name
    if not probe_module.GENERIC_COMMANDS.match(first):
        return first
    rest = words[1:]
    index = 0
    while index < len(rest):
        word = rest[index]
        if word == "-m" and index + 1 < len(rest):
            return rest[index + 1].rsplit(".", 1)[-1]
        if word.startswith("-"):
            index += 1
            continue
        return Path(word).name or command
    return first


def holder_reason(check: str, detail: str) -> dict:
    """A gate that said no -> ``{"reason": "tview has the port (pid 640623)", "holders": [{"pid", "program"}]}``. A detail
    the host cannot read as a holder is kept whole after "blocked:"."""

    what = _WHAT.get(check, check)
    for pattern in _HELD:
        found = pattern.search(detail or "")
        if not found:
            continue
        first, second = found.groups()
        pid, command = (int(first), second) if first.isdigit() else (int(second), first)
        program = program_in(probe_module.program_name(pid, command))
        return {"reason": f"{program} has {what} (pid {pid})", "holders": [{"pid": pid, "program": program}]}
    return {"reason": f"{what} is held: {detail}" if detail else f"{what} is held", "holders": []}


# ----- driver tabs --------------------------------------------------------------------------------------------------
def declared_tabs(describe: dict | None, rel: str) -> list[dict]:
    """The extra tabs a driver declares for an entity: ``"tabs": [{"id": "processes", "title": "Processes", "path": "",
    "kind": "table", "status": "Processes", "values": ["top_cpu", "top_memory"]}]``. ``path`` is relative to the device
    (``""`` or ``"host"`` for the Computer)."""

    out = []
    for tab in (describe or {}).get("tabs") or []:
        if not isinstance(tab, dict) or not tab.get("id") or not tab.get("title"):
            continue
        if str(tab.get("path", "")).strip("/") in (rel, "host" if rel == "" else rel):
            out.append({key: tab[key] for key in ("id", "title", "kind", "status", "values", "description") if key in tab})
    return out
