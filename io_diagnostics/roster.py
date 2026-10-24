"""What the tree remembers: rows exist for what was observed, and stay until a person removes them.

Zach, 2026-10-24: "if you hear from one then detach it … how to know if it was actually detached you cannot", and
"I don't like how the entity tree keeps on changing from showing all of CAN ids to not". So:

- **A row exists only for something heard from.** A plugin reports an entity for a board that answered; one that never
  answered is a status on its bus (the plugin says "CAN 15 silent"), never a ghost row.
- **A row stays once heard.** The host keeps every entity a plugin has reported (the ``JobCache`` already keeps it
  while the host runs) and, with this module, across a host restart: the ``Ledger`` is a small JSON file of the
  last-known entities per (plugin, device), each with when it was last heard. A restarted host shows the same rows,
  hollow (not heard lately), until fresh data replaces them. Nothing about health is restored: no status comes back
  from the file, so a cached row never claims OK, WARN or ERROR it did not just earn.
- **A silent row says so.** When a run that really looked at the bus (not blocked, not failed, not timed out) does not
  report a remembered entity, it gets one stale ``NotHeard`` status stamped with its true last-heard time, so it counts
  as stale in the header; before such a run it is only "restored" (hollow, not counted). A blocked run (the port is
  held by tview) therefore never removes a row and never makes one look dead.
- **Re-ID keeps the row.** A board is matched by its repeatable ``hardware_id`` (moteus: its UUID); a board that
  answers from a new CAN id replaces its old row.
- **Only a person removes a row** (``Roster.forget``: the row menu's "Remove from bus"). A removed board that answers
  again comes back: ``forget`` only keeps the file from restoring it.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import time
from pathlib import Path

from contract.record import STALE, UNKNOWN, WARN, DiagnosticStatus, Entity, KeyValue, effective_level, from_dict, to_dict

LEDGER_FILE = "tree.json"
LEDGER_WRITE_S = 300.0  # a known entity's last-heard time is refreshed on disk at most this often; a new entity at once
ROSTER_SOURCE = "io_diagnostics.roster"
NOT_HEARD_CODE = "NotHeard"
# A run whose records carry one of these did not look at the device (the port is held, the bus lock is held, a passive
# run, a check that failed or ran out of time): what it did not report says nothing about what is there.
BLOCKED_CODES = frozenset({"PortBusy", "BusLockHeld", "PassiveOnly", "CheckFailed", "CheckTimedOut", "ProbeBlocked",
                           "AdapterOpenFailed", "AdapterNotFound", "AdapterNoTty"})
# A status that says "nothing answered": it is not a sign the entity is alive.
SILENT_CODES = frozenset({"NoReply", "NoAck", "PortBusy", "BoardNotAnswering", "BusLockHeld"})
# WHY 1 s: the synthetic status goes stale after max(3 x period, 5 s) = 5 s, so a restored row is hollow at once
# whatever the plugin's own period is.
NOT_HEARD_PERIOD_S = 1.0
FOLLOW_TOUCH_S = 5.0  # a followed job's lines refresh the ledger at most this often


def memory_key(plugin: str, hardware_id: str) -> str:
    return f"{plugin}|{hardware_id}"


def _heard_text(stamp: float, now: float) -> str:
    seconds = max(0.0, now - stamp)
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} days ago"


class Roster:
    """The remembered entities of every (plugin, device), and what is still waiting for a first fresh run.

    ``path=None`` keeps it in memory only (tests, a host built by hand, a fixture replay). All methods take the
    host's lock-free view: they run on the thread that absorbed the run."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        # key -> {"plugin", "device", "name", "entities": {rel path: {"entity": dict, "icon": str, "last_heard": float}},
        #         "order": [rel path]}
        self.devices: dict[str, dict] = {}
        self.forgotten: dict[str, dict[str, float]] = {}  # key -> {hardware_id or rel path: when}
        self.restored: dict[str, dict[str, float]] = {}  # key -> {rel path: last_heard} not yet re-reported by a good run
        self.note = ""  # why the file could not be read at start (sticky: the content was dropped)
        self.write_note = ""
        self._written = -1e18
        self._touched: dict[str, float] = {}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.devices = {str(key): dict(value) for key, value in (data.get("devices") or {}).items() if isinstance(value, dict)}
                self.forgotten = {str(key): {str(item): float(when) for item, when in dict(value).items()}
                                  for key, value in (data.get("forgotten") or {}).items() if isinstance(value, dict)}
            except (OSError, ValueError, AttributeError, TypeError) as error:
                self.devices, self.forgotten = {}, {}
                self.note = f"{self.path}: unreadable ({type(error).__name__}: {error}); starting empty"

    # --- restore: a job is made for a device ------------------------------------------------------------------------
    def restore(self, job, hardware_id: str) -> int:
        """Put the last-known entities of this (plugin, device) into the job's cache, with no statuses. Returns how many.

        WHY no statuses: a cached row must never claim a health it did not just earn; an entity without records is a
        grouping row (no mark), drawn hollow by its ``restored`` presence until a run reports it."""

        key = memory_key(job.spec.name, hardware_id)
        entry = self.devices.get(key)
        if not entry:
            return 0
        skip = self.forgotten.get(key, {})
        count = 0
        pending = self.restored.setdefault(key, {})
        for rel in entry.get("order") or list(entry.get("entities") or {}):
            item = (entry.get("entities") or {}).get(rel)
            if not item or rel in job.cache.entities:
                continue
            try:
                entity = from_dict(item["entity"])
            except (KeyError, TypeError, ValueError):
                continue
            if rel in skip or (entity.hardware_id and entity.hardware_id in skip):
                continue
            job.cache.entities[rel] = entity
            job.cache.order.append(rel)
            if item.get("icon"):
                job.cache.icons[rel] = item["icon"]
            if rel:  # the device row ("") is the OS's; only its name and identity are restored, it is never "silent"
                pending[rel] = float(item.get("last_heard") or 0.0)
            count += 1
        return count

    # --- observe: a run was absorbed ----------------------------------------------------------------------------------
    @staticmethod
    def blocked(outcome) -> bool:
        """The run did not look at the device: it crashed, timed out, or said its port or bus was held."""

        if outcome is None:
            return False
        if outcome.outcome != "ok":
            return True
        return any(getattr(record, "code", "") in BLOCKED_CODES for record in outcome.records
                   if isinstance(record, DiagnosticStatus))

    def observe(self, job, hardware_id: str, outcome, now: float) -> bool:
        """Record what the cache holds, mark what a good run did not report, and drop a re-ID'd board's old row.

        ``outcome`` None is a touch (a followed job's line): the ledger is only refreshed, throttled. Returns True when
        the cache changed in a way a page should redraw."""

        key = memory_key(job.spec.name, hardware_id)
        if outcome is None and time.monotonic() - self._touched.get(key, -1e18) < FOLLOW_TOUCH_S:
            return False
        self._touched[key] = time.monotonic()
        cache = job.cache
        heard = self._heard_paths(outcome, cache) if outcome is not None else self._heard_from_statuses(cache, now)
        changed = False
        pending = self.restored.setdefault(key, {})
        entry = self.devices.setdefault(key, {"plugin": job.spec.name, "device": hardware_id, "name": "", "entities": {}, "order": []})
        forgotten = self.forgotten.setdefault(key, {})
        # a board that answers again is no longer removed
        for rel in heard:
            entity = cache.entities.get(rel)
            for item in (rel, entity.hardware_id if entity else ""):
                if item and forgotten.pop(item, None) is not None:
                    changed = True
            pending.pop(rel, None)
        # a good run that did not report a restored entity: it is silent, with its true last-heard time
        if outcome is not None and not self.blocked(outcome):
            for rel in [rel for rel in pending if rel not in heard]:
                stamp = pending.pop(rel)
                entity = cache.entities.get(rel)
                if entity is None:
                    continue
                cache.statuses[(rel, f"{entity.name or rel}: last heard")] = self._not_heard(entity, rel, stamp, now, job)
                changed = True
        changed |= self._supersede(cache, heard, pending, entry, now)
        new = False
        for rel, entity in cache.entities.items():
            stored = entry["entities"].get(rel)
            last = now if rel in heard else (stored or {}).get("last_heard")
            if last is None:
                continue  # never heard (a plugin that reports a silent board): not remembered
            record = {"entity": to_dict(entity), "icon": cache.icons.get(rel, ""), "last_heard": float(last)}
            if stored is None or stored["entity"] != record["entity"]:
                new = True
            entry["entities"][rel] = record
        entry["order"] = [rel for rel in cache.order if rel in entry["entities"]]
        entry["name"] = (cache.entities.get("") or Entity("")).name or entry.get("name", "")
        for rel in [rel for rel in entry["entities"] if rel not in cache.entities]:
            entry["entities"].pop(rel)  # superseded or forgotten
        self._save(new or changed)
        return changed

    def _heard_paths(self, outcome, cache) -> set[str]:
        """The relative paths this run heard from: an entity it reported that carries a non-silent status or none at all,
        and any path with a non-silent status."""

        statuses: dict[str, list[DiagnosticStatus]] = {}
        entities: set[str] = set()
        for record in outcome.records:
            if isinstance(record, Entity):
                entities.add(record.path)
            elif isinstance(record, DiagnosticStatus):
                statuses.setdefault(record.path, []).append(record)
        heard = {path for path, found in statuses.items() if any(status.code not in SILENT_CODES for status in found)}
        heard |= {path for path in entities if path not in statuses}
        return {path for path in heard if path in cache.entities}

    def _heard_from_statuses(self, cache, now: float) -> set[str]:
        """A touch: the paths whose newest non-silent status was received in the last few seconds."""

        out = set()
        for (path, _name), status in cache.statuses.items():
            if status.code not in SILENT_CODES and status.received is not None and now - status.received < FOLLOW_TOUCH_S * 3:
                if path in cache.entities:
                    out.add(path)
        return out

    @staticmethod
    def _not_heard(entity: Entity, rel: str, stamp: float, now: float, job) -> DiagnosticStatus:
        return DiagnosticStatus(
            level=UNKNOWN, name=f"{entity.name or rel}: last heard", code=NOT_HEARD_CODE, path=rel,
            hardware_id=entity.hardware_id, stamp=stamp, received=stamp, expected_period_s=NOT_HEARD_PERIOD_S,
            source=ROSTER_SOURCE,
            message=f"not heard from this session; last heard {_heard_text(stamp, now)}" if stamp else "not heard from this session",
            values=(KeyValue("what_to_check", "1. Is it powered and connected? Its cable and connectors\n"
                                              "2. Remove it from the row's menu if it is gone for good"),
                    KeyValue("last_heard", f"{stamp:.0f}" if stamp else "unknown")))

    def _supersede(self, cache, heard: set[str], pending: dict, entry: dict, now: float) -> bool:
        """A board re-ID'd to a new path: its old row (same hardware_id, not heard in this run) goes."""

        changed = False
        by_id: dict[str, list[str]] = {}
        for rel, entity in cache.entities.items():
            if rel and entity.hardware_id and entity.kind == "node":
                by_id.setdefault(entity.hardware_id, []).append(rel)
        for rels in by_id.values():
            if len(rels) < 2:
                continue
            fresh = [rel for rel in rels if rel in heard]
            if not fresh:
                continue
            for rel in rels:
                if rel not in fresh:
                    self._drop(cache, rel)
                    pending.pop(rel, None)
                    entry["entities"].pop(rel, None)
                    changed = True
        return changed

    @staticmethod
    def _drop(cache, rel: str) -> list[str]:
        """Remove ``rel`` and everything under it from a job cache."""

        gone = [path for path in list(cache.entities) if path == rel or path.startswith(rel + "/")]
        for path in gone:
            cache.entities.pop(path, None)
            cache.icons.pop(path, None)
        cache.order[:] = [path for path in cache.order if path not in set(gone)]
        for key in [key for key in cache.statuses if key[0] == rel or key[0].startswith(rel + "/")]:
            cache.statuses.pop(key)
        return gone

    # --- forget: "Remove from bus" ------------------------------------------------------------------------------------
    def forget(self, job, hardware_id: str, rel: str, now: float) -> list[str]:
        """Remove a row and everything under it. It is not restored again; it comes back if it answers again."""

        key = memory_key(job.spec.name, hardware_id)
        entity = job.cache.entities.get(rel)
        gone = self._drop(job.cache, rel)
        forgotten = self.forgotten.setdefault(key, {})
        for path in gone:
            self.restored.get(key, {}).pop(path, None)
            self.devices.get(key, {}).get("entities", {}).pop(path, None)
            forgotten[path] = now
        if entity is not None:
            forgotten[rel] = now
            if entity.hardware_id:
                forgotten[entity.hardware_id] = now
        if key in self.devices:
            self.devices[key]["order"] = [path for path in self.devices[key].get("order", []) if path not in set(gone)]
        self._save(True)
        return gone

    # --- views --------------------------------------------------------------------------------------------------------
    def pending(self, keyed: list[tuple[str, str]]) -> dict[str, float]:
        """Tree path -> last-heard time of every restored entity no good run has reported yet. ``keyed``: one
        ``(memory key, device path)`` per job."""

        from io_diagnostics.runner import absolute

        out: dict[str, float] = {}
        for key, base in keyed:
            for rel, stamp in self.restored.get(key, {}).items():
                out[absolute(rel, base)] = stamp
        return out

    # --- persistence --------------------------------------------------------------------------------------------------
    def _save(self, urgent: bool) -> None:
        if not self.path or not (urgent or time.monotonic() - self._written > LEDGER_WRITE_S):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(handle, "w") as file:
                json.dump({"version": 1, "devices": self.devices,
                           "forgotten": {key: value for key, value in self.forgotten.items() if value}},
                          file, indent=1, sort_keys=True, default=str)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self._written = time.monotonic()
            self.write_note = ""
        except OSError as error:  # a read-only home must not stop the host; the reason shows in host.sources
            self.write_note = f"{self.path}: not written ({type(error).__name__}: {error})"


def stopped_answering(merged, now: float) -> list[DiagnosticStatus]:
    """One WARN per bus whose remembered boards went silent: "CAN 15 stopped answering" (Zach, 2026-10-24).

    ``caused_by`` names the silent boards, so ``summarize`` counts them once, as the boards (stale), and the bus is their
    consequence. WHY the host says it and not the plugin: only the host remembers a board that answered before. A board
    some plugin status already names in its ``caused_by`` (moteus: "CAN 15 silent" for an expected id) is not said twice."""

    latest: dict[tuple[str, str], DiagnosticStatus] = {}
    for status in merged.statuses:
        latest[(status.path, status.name)] = status
    levels: dict[str, list[str]] = {}
    for (path, _name), status in latest.items():
        levels.setdefault(path, []).append(effective_level(status, now))
    named = {member for status in latest.values() for member in status.caused_by}
    silent: dict[str, list[str]] = {}
    for path, entity in merged.entities.items():
        if entity.kind != "node" or path in named or not levels.get(path) or any(level != STALE for level in levels[path]):
            continue
        parent = merged.parent.get(path)
        if parent:
            silent.setdefault(parent, []).append(path)
    out = []
    for parent, paths in silent.items():
        names = [merged.entities[path].name or path for path in paths]
        label = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        out.append(DiagnosticStatus(
            level=WARN, name=f"{merged.entities[parent].name}: boards stopped answering", code="BoardsStoppedAnswering",
            path=parent, caused_by=tuple(paths), stamp=now, received=now, source=ROSTER_SOURCE,
            hardware_id=merged.entities[parent].hardware_id,
            message=f"{label} stopped answering",
            values=(KeyValue("what_to_check", "1. Is it powered? Its power lead and its CAN lead\n"
                                              "2. Another adapter or port, if the whole bus went quiet\n"
                                              "3. Remove it from the bus (its row's menu) if it is gone for good"),
                    KeyValue("silent", ", ".join(names)))))
    return out


__all__ = ["Roster", "LEDGER_FILE", "ROSTER_SOURCE", "NOT_HEARD_CODE", "BLOCKED_CODES", "SILENT_CODES", "memory_key",
           "stopped_answering"]
