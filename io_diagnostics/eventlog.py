"""The host's events on disk: append-only JSONL, one file per boot, size-capped (Zach, 2026-10-24: "I will take your
defaults" on docs/activity-log-v1.md decision 4).

WHY: the fault memory, the level history and a run's events live in the host's RAM, so a restart or a reboot erased
them (the 10-23 15:00 reboot wiped every fault). The log keeps every event the host sees, so "what happened last
night" has an answer after the process, or the machine, has gone.

- ``<directory>/<boot start UTC>-<boot id>-<segment>.jsonl``, one JSON object per line:
  ``{"key": "<event id>", "event": <contract.record.to_dict(event)>}``. A boot is the machine's boot (the kernel's
  ``boot_id``), so a host restart inside one boot appends to the same file and a reboot starts a new one.
- A line is a fact as the host saw it. An event whose content changes later (a ``LinkSpeed`` the sysfs reading turns
  into a ``LinkDowngraded``) is written again with the same key; the newest line wins on load.
- ``Event.received`` is when the host FIRST saw the event (OpenTelemetry's ObservedTimestamp); it is written once
  and survives restarts.
- Capped at ``cap_bytes`` in all (default 20 MB): a segment is closed at ``segment_bytes`` and the OLDEST segments
  are deleted while the total is over the cap, never the segment being written. Memory holds at most ``max_loaded``
  events (the newest).
- Several hosts may write one boot's file (Stable and Preview run side by side): each write is one O_APPEND line,
  and before writing a host reads what the others appended, so a fact the other already wrote is not written twice.
  A duplicate that still slips through is harmless: the newest line of a key wins on load.
- Only SETTLED events are written (``settled``): a kernel-derived USB event is not final until the journal
  coalescer's windows have passed (a "Plugged" can turn into "ReEnumerated" 2 s later), so it is written once it is
  ``SETTLE_S`` old; every other event is final the moment the host has it.

Provisional events (udev's "plugging in…") are never written. ``directory`` None keeps the same memory without a disk.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Iterable

from contract.record import Event, from_dict, to_dict
from io_diagnostics import activity as activity_module

CAP_BYTES = 20 * 1024 * 1024
SEGMENT_BYTES = 5 * 1024 * 1024
MAX_LOADED = 50_000
MAX_FIRST_SEEN = 100_000
SETTLE_S = 30.0  # a kernel-derived event this old can no longer be merged into a different one (journal.Coalescer's windows are < 15 s)
SUFFIX = ".jsonl"
# the sources whose events are re-derived from a log on every view (and can change shape until it has settled)
VOLATILE_SOURCES = (activity_module.JOURNAL_SOURCE, activity_module.KERNEL_SOURCE, activity_module.UDEV_SOURCE)


def settled(event: Event, now: float) -> bool:
    """Is this event final? Provisional ones never are; a kernel-derived one is once ``SETTLE_S`` old."""

    args = event.args if isinstance(event.args, dict) else {}
    if args.get("provisional"):
        return False
    if event.source in VOLATILE_SOURCES:
        return event.stamp is not None and now - event.stamp >= SETTLE_S
    return True


def plain(value):
    """JSON's own shapes (tuple -> list, set -> sorted list), so an event equals itself after a trip through the file."""

    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(plain(item) for item in value)
    return value


def _order(event: Event) -> tuple[float, int]:
    return (event.stamp or 0.0, event.seq or 0)


def boot_file_prefix(boot_start: float, boot_id: str) -> str:
    """``<boot start UTC>-<8 hex of the boot id>``: the kernel's own ids keep their first 8 digits (what ``journalctl
    --list-boots`` shows), any other id (a replayed frame's name) is hashed so two cannot share a prefix."""

    compact = (boot_id or "unknown").replace("-", "").lower()
    short = compact[:8] if re.fullmatch(r"[0-9a-f]{8,}", compact) else hashlib.sha1(compact.encode()).hexdigest()[:8]
    return f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(boot_start))}-{short}"


class EventLog:
    def __init__(self, directory: Path | None, *, boot_id: str = "", boot_start: float = 0.0, cap_bytes: int = CAP_BYTES,
                 segment_bytes: int = SEGMENT_BYTES, max_loaded: int = MAX_LOADED):
        self.directory = Path(directory) if directory is not None else None
        self.boot_id, self.boot_start = boot_id, boot_start
        self.prefix = boot_file_prefix(boot_start, boot_id)
        self.cap_bytes, self.segment_bytes, self.max_loaded = cap_bytes, segment_bytes, max_loaded
        self.note = ""
        self.revision = 0
        self.written = 0  # lines this process wrote
        self._lock = threading.RLock()
        self._index: dict[str, Event] = {}  # key -> the newest version
        self._where: dict[str, str] = {}  # key -> the file of that version
        self._first_seen: dict[str, float] = {}
        self._offsets: dict[str, int] = {}  # file name -> bytes of it already read
        self._sorted: list[tuple[str, Event]] | None = None
        self._load()

    # --- reading ---------------------------------------------------------------------------------------------------
    def files(self) -> list[Path]:
        if self.directory is None or not self.directory.is_dir():
            return []
        return sorted(path for path in self.directory.iterdir() if path.suffix == SUFFIX and path.is_file())

    def _load(self) -> None:
        for path in self.files():
            self._read_new(path)
        if len(self._index) > self.max_loaded:
            self._trim()

    def _read_new(self, path: Path) -> int:
        """Ingest what ``path`` holds beyond what was read; -> events that were new or changed."""

        start = self._offsets.get(path.name, 0)
        try:
            size = path.stat().st_size
            if size <= start:
                return 0
            with path.open("rb") as handle:
                handle.seek(start)
                data = handle.read(size - start)
        except OSError as error:
            self.note = f"the event log {path.name} could not be read: {error}"
            return 0
        end = data.rfind(b"\n") + 1  # a line another host is still writing waits for its newline
        self._offsets[path.name] = start + end
        changed = 0
        for line in data[:end].splitlines():
            try:
                entry = json.loads(line)
                event = from_dict(entry["event"])
                key = str(entry["key"])
            except (ValueError, KeyError, TypeError):
                continue  # a torn or foreign line: skipped, never fatal
            if not isinstance(event, Event):
                continue
            event = dataclasses.replace(event, args=plain(event.args))
            if self._index.get(key) != event:
                changed += 1
            self._index[key] = event
            self._where[key] = path.name
        if changed:
            self._touch()
        return changed

    def _touch(self) -> None:
        self.revision += 1
        self._sorted = None

    def _trim(self) -> None:
        keep = sorted(self._index.items(), key=lambda item: _order(item[1]), reverse=True)[: self.max_loaded]
        self._index = dict(keep)
        self._where = {key: self._where[key] for key in self._index if key in self._where}
        self._touch()

    def _ordered(self) -> list[tuple[str, Event]]:
        if self._sorted is None:
            self._sorted = sorted(self._index.items(), key=lambda item: _order(item[1]), reverse=True)
        return self._sorted

    def events(self) -> list[Event]:
        """Every event kept, newest first."""

        with self._lock:
            return [event for _key, event in self._ordered()]

    def __len__(self) -> int:
        return len(self._index)

    # --- the host's side -------------------------------------------------------------------------------------------
    def stamp_received(self, events: Iterable[Event], now: float) -> list[Event]:
        """Each event with ``received``: the one the log already holds for its id, else the first time any view saw it."""

        out = []
        with self._lock:
            for event in events:
                if event.received is not None:
                    out.append(event)
                    continue
                key = activity_module.event_id(event)
                held = self._index.get(key)
                first = held.received if held is not None and held.received is not None else self._first_seen.setdefault(key, now)
                out.append(dataclasses.replace(event, received=first))
            if len(self._first_seen) > MAX_FIRST_SEEN:
                for key in list(self._first_seen)[: len(self._first_seen) - MAX_FIRST_SEEN // 2]:
                    del self._first_seen[key]
        return out

    def overlay(self, current: list[Event], now: float) -> list[Event]:
        """What the host knows now over what the log remembers (the current event wins by id), newest first."""

        with self._lock:
            current = self.stamp_received(current, now)
            ids = {activity_module.event_id(event) for event in current}
            kept = [event for key, event in self._ordered() if key not in ids]
        merged = kept + current
        merged.sort(key=_order, reverse=True)
        return merged

    def record(self, events: Iterable[Event], now: float) -> int:
        """Write the events that are new or changed (settled ones only); -> how many lines were written."""

        with self._lock:
            self._catch_up()
            lines, fresh = [], []
            for event in self.stamp_received([event for event in events if settled(event, now)], now):
                key = activity_module.event_id(event)
                event = dataclasses.replace(event, args=plain(event.args))
                held = self._index.get(key)
                # WHY the sequence is not compared: it is the order a process happened to make events in (which plugin
                # answered first), so the same fact seen by a restarted process differs in it alone
                if held is not None and dataclasses.replace(held, seq=event.seq) == event:
                    continue
                fresh.append((key, event))
                lines.append(json.dumps({"key": key, "event": to_dict(event)}, ensure_ascii=False, default=str) + "\n")
            if not fresh:
                return 0
            for key, event in fresh:
                self._index[key] = event
            self._touch()
            name = self._append("".join(lines))
            for key, _event in fresh:
                if name:
                    self._where[key] = name
            if len(self._index) > self.max_loaded + self.max_loaded // 5:
                self._trim()
            return len(fresh)

    # --- the disk --------------------------------------------------------------------------------------------------
    def _catch_up(self) -> None:
        """Read what another host appended to this boot's files since we last looked."""

        for path in self.files():
            if path.name.startswith(self.prefix):
                self._read_new(path)

    def _current(self) -> Path:
        """The segment being written: this boot's newest, or the next one when it is full."""

        mine = [path for path in self.files() if path.name.startswith(self.prefix)]
        if mine and mine[-1].stat().st_size < self.segment_bytes:
            return mine[-1]
        number = int(mine[-1].stem.rsplit("-", 1)[-1]) + 1 if mine else 0
        return self.directory / f"{self.prefix}-{number:03d}{SUFFIX}"

    def _append(self, text: str) -> str:
        if self.directory is None:
            return ""
        data = text.encode("utf-8")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            path = self._current()
            before = self._offsets.get(path.name, 0)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            self._offsets[path.name] = before + len(data)
            self.written += text.count("\n")
            self._prune(path)
            self.note = ""
            return path.name
        except OSError as error:  # a full or read-only disk must not stop the host: it keeps the log in memory
            self.note = f"the event log could not be written ({self.directory}): {error}"
            return ""

    def _prune(self, current: Path) -> None:
        """Delete the oldest segments while the total is over the cap; the one being written stays."""

        files = self.files()
        sizes = {path: path.stat().st_size for path in files}
        total = sum(sizes.values())
        gone = set()
        for path in files:
            if total <= self.cap_bytes or path == current:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= sizes[path]
            gone.add(path.name)
            self._offsets.pop(path.name, None)
        if gone:
            for key in [key for key, name in self._where.items() if name in gone]:
                self._index.pop(key, None)
                del self._where[key]
            self._touch()
