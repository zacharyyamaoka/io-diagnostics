"""The ladder's memory: one incident per device outage (each rung at most once in it), the ledger of every attempt with its
before/after evidence, the alarms a page leaves, a person's hold, and the switch that turns unattended recovery off.

All under ``$IO_DIAGNOSTICS_STATE_DIR/recovery`` (``~/.local/state/io-diagnostics/recovery``):

    incidents/<device>.json   the open incident: when it started, which rungs ran, whether a human was paged
    ledger.jsonl              one line per attempt (schema recovery-attempt/1): rung, outcome, why, before, after, probe
    alarms.jsonl              one line per page (schema recovery-alarm/1): what a human is asked to do
    holds/<device>.json       {"until": <epoch>, "by": "...", "reason": "..."}: nothing resets it until then (a firmware flash)
    locks/<device>.lock       flock: one ladder per device at a time

and ``$IO_DIAGNOSTICS_CONFIG_DIR/recovery.off`` (``~/.config/io-diagnostics/recovery.off``): no unattended rung runs anywhere.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from io_diagnostics.expected import default_config_dir

DAY_S = 86400.0
LEDGER_SCHEMA = "recovery-attempt/1"
ALARM_SCHEMA = "recovery-alarm/1"


def state_dir() -> Path:
    base = Path(os.environ.get("IO_DIAGNOSTICS_STATE_DIR") or Path.home() / ".local" / "state" / "io-diagnostics")
    return base / "recovery"


def off_switch() -> Path:
    return default_config_dir() / "recovery.off"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=1, sort_keys=True))
    os.replace(temporary, path)  # WHY atomic: a ladder killed mid-write must not leave a half incident that re-runs every rung


def _append(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        out.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


@dataclass
class Incident:
    device: str  # the device's state key
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    opened: float = field(default_factory=time.time)
    tried: dict[str, float] = field(default_factory=dict)  # rung kind -> when it ran (or the owner said it did)
    tried_by: dict[str, str] = field(default_factory=dict)  # rung kind -> "robotdx" | "owner pid N" | "person"
    paged: float | None = None
    cured_by: str = ""
    healthy_since: float | None = None
    last_port: str = ""
    profile: str = ""
    blocked_since: float | None = None  # a holder or an owner lock has kept every rung from running since then
    detected: str = ""  # what started it: "no answer", "stale frames", the probe's words
    closed: float | None = None

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_json(cls, value: dict) -> "Incident":
        known = {key: value[key] for key in cls.__dataclass_fields__ if key in value}
        return cls(**known)


class Store:
    """One device's incident file, plus the shared ledger and alarms. ``now`` is the ladder's clock (a test's fake one)."""

    def __init__(self, state_key: str, now=time.time):
        self.key = state_key
        self.now = now
        self.root = state_dir()

    # ----- the incident -----
    @property
    def incident_path(self) -> Path:
        return self.root / "incidents" / f"{self.key}.json"

    def current(self) -> Incident | None:
        try:
            value = json.loads(self.incident_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        found = Incident.from_json(value)
        return None if found.closed else found

    def open(self, detected: str, port: str) -> Incident:
        found = self.current()
        if found is not None:
            found.healthy_since = None  # a failure inside the quiet window continues the incident, its used rungs stay used
            if port:
                found.last_port = port
            self.save(found)
            return found
        found = Incident(device=self.key, opened=self.now(), detected=detected, last_port=port)
        self.save(found)
        self._history_add(found)
        return found

    def save(self, incident: Incident) -> None:
        _write_json(self.incident_path, incident.to_json())

    def close(self, incident: Incident) -> None:
        incident.closed = self.now()
        self.save(incident)
        _write_json(self.root / "closed" / f"{self.key}-{incident.id}.json", incident.to_json())

    def healthy(self, quiet_s: float) -> Incident | None:
        """It answered: start (or continue) the quiet window, and close the incident once it has answered ``quiet_s``."""

        found = self.current()
        if found is None:
            return None
        if found.healthy_since is None:
            found.healthy_since = self.now()
        if self.now() - found.healthy_since >= quiet_s:
            self.close(found)
        else:
            self.save(found)
        return found

    # ----- how many incidents today -----
    def _history_add(self, incident: Incident) -> None:
        _append(self.root / "history" / f"{self.key}.jsonl", {"id": incident.id, "opened": incident.opened})

    def opened_in_last_day(self) -> int:
        try:
            lines = (self.root / "history" / f"{self.key}.jsonl").read_text().splitlines()
        except OSError:
            return 0
        cutoff = self.now() - DAY_S
        count = 0
        for line in lines:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                if float(json.loads(line)["opened"]) >= cutoff:
                    count += 1
        return count

    # ----- the person's hold -----
    def hold(self) -> dict | None:
        try:
            value = json.loads((self.root / "holds" / f"{self.key}.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return value if float(value.get("until") or 0) > self.now() else None

    def set_hold(self, until: float, by: str, reason: str) -> None:
        _write_json(self.root / "holds" / f"{self.key}.json", {"until": until, "by": by, "reason": reason})

    # ----- the ledger and the alarms -----
    def record(self, entry: dict) -> dict:
        line = {"schema": LEDGER_SCHEMA, "device": self.key, "utc": self.now(), **entry}
        _append(self.root / "ledger.jsonl", line)
        return line

    def alarm(self, entry: dict) -> dict:
        line = {"schema": ALARM_SCHEMA, "device": self.key, "utc": self.now(), **entry}
        _append(self.root / "alarms.jsonl", line)
        return line

    @contextlib.contextmanager
    def ladder_lock(self):
        """One ladder per device: a second one (two owners, the supervisor and a person) is refused, never queued."""

        path = self.root / "locks" / f"{self.key}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            yield True
        finally:
            os.close(fd)


def ledger(limit: int = 50, device: str = "") -> list[dict]:
    try:
        lines = (state_dir() / "ledger.jsonl").read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError):
            value = json.loads(line)
            if not device or value.get("device") == device:
                out.append(value)
    return out[-limit:]
