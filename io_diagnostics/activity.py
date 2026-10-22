"""Activity: plug / unplug / re-enumerate from the kernel log, fault transitions, and link health.

USB events are the kernel's own log (``journalctl -k``) through ``io_facts.journal``'s coalescer: one notice per
flap burst, "dropped and came back" instead of unplug + plug (28B3A25D enumerated five times in 17 s on 10-21).
The whole boot is replayed on every refresh, so the live view and a fixture replay of a recorded log run the SAME
code; ``udevadm monitor`` (``io_facts.udev``, passive) only tells the host to refresh sooner.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

from io_facts import journal

from contract.record import RANK, STALE, Event, effective_level
from io_diagnostics.tree import PortHistory, Rows, speed_text

BOOT_WINDOW_S = 15.0  # enumerations this soon after boot are the boot, not a (re-)plug (V6, io_facts)
HOUR_S = 3600.0
JOURNAL_SOURCE = "io_facts.journal (journalctl -k, coalesced)"
NOTICE_CODE = {"plugged": "Plugged", "unplugged": "Unplugged", "dropped": "ReEnumerated"}
KIND_LABEL = {"Booted": "boot", "ShutDown": "shutdown", "Plugged": "plugged", "Unplugged": "unplugged",
              "ReEnumerated": "re-enumerated", "FaultConfirmed": "fault confirmed", "FaultHealed": "fault healed",
              "FaultCleared": "fault cleared"}


def kind_label(code: str) -> str:
    return KIND_LABEL.get(code) or re.sub(r"(?<!^)(?=[A-Z])", " ", code.split(".")[0]).lower()


def zone(utc_offset_s: int | None) -> dt.tzinfo | None:
    return None if utc_offset_s is None else dt.timezone(dt.timedelta(seconds=utc_offset_s))


def to_unix(naive: dt.datetime, utc_offset_s: int | None = None) -> float:
    """A naive kernel-log time (local wall clock where it was logged) -> Unix seconds."""

    if utc_offset_s is None:
        return naive.timestamp()
    return naive.replace(tzinfo=zone(utc_offset_s)).timestamp()


def iso(stamp: float | None, utc_offset_s: int | None = None) -> str | None:
    if stamp is None:
        return None
    if utc_offset_s is None:
        return dt.datetime.fromtimestamp(stamp).astimezone().isoformat(timespec="seconds")
    return dt.datetime.fromtimestamp(stamp, zone(utc_offset_s)).isoformat(timespec="seconds")


@dataclass
class Boot:
    start: float  # Unix seconds
    lines: list[str]  # journalctl -k -o short-iso lines (" kernel: usb " ones are enough)
    end: float | None = None  # when it shut down, if it did


@dataclass
class UsbActivity:
    events: list[Event] = field(default_factory=list)
    history: dict[str, PortHistory] = field(default_factory=dict)
    recent_ports: set[str] = field(default_factory=set)  # ports with non-boot activity in the last hour


def usb_activity(boots: list[Boot], now: float, utc_offset_s: int | None = None,
                 port_path: Callable[[str], str] | None = None) -> UsbActivity:
    """Kernel-log facts up to ``now``: events for every boot, per-port history for the boot ``now`` is in."""

    out = UsbActivity()
    port_path = port_path or (lambda port: f"host/usb/port-{port}")
    seq = 0
    current = max((boot for boot in boots if boot.start <= now), key=lambda boot: boot.start, default=None)
    for boot in sorted(boots, key=lambda boot: boot.start):
        if boot.start > now:
            continue
        raw = [event for event in journal.parse_kernel_log(boot.lines) if to_unix(event.time, utc_offset_s) <= now]
        last_add = {journal.Coalescer.identity(event): event for event in raw if event.kind == "add"}
        notices = [notice for notice in journal.replay(raw) if to_unix(notice.time, utc_offset_s) <= now]
        at_boot = [notice for notice in notices if notice.kind == "plugged"
                   and to_unix(notice.time, utc_offset_s) - boot.start <= BOOT_WINDOW_S]
        seq += 1
        out.events.append(Event("Booted", boot.start, "info", f"PC booted · {len(at_boot)} USB devices enumerated",
                                path="host", args={"devices": len(at_boot)}, seq=seq, source=JOURNAL_SOURCE))
        for notice in notices:
            if notice in at_boot:
                continue
            seq += 1
            added = last_add.get(notice.identity)
            hardware_id = ""
            if added is not None and added.vid:
                hardware_id = f"usb:{added.vid}:{added.pid}:{added.serial}" if added.serial else f"usb:{added.vid}:{added.pid}@{added.port}"
            code = NOTICE_CODE[notice.kind]
            out.events.append(Event(code, to_unix(notice.time, utc_offset_s), "warn" if code == "ReEnumerated" else "info",
                                    notice.text(), path=port_path(notice.port), hardware_id=hardware_id,
                                    args={"port": notice.port, "product": notice.product, "identity": notice.identity,
                                          "flaps": len(notice.flaps), "flap_ports": sorted(set(notice.flaps)),
                                          "gap_s": round(notice.gap_s, 1)},
                                    seq=seq, source=JOURNAL_SOURCE))
        if boot.end is not None and boot.end <= now:
            seq += 1
            out.events.append(Event("ShutDown", boot.end, "info", "PC shut down", path="host", seq=seq, source="journalctl --list-boots"))
        if boot is not current:
            continue
        for event in raw:
            stamp = to_unix(event.time, utc_offset_s)
            if stamp - boot.start <= BOOT_WINDOW_S:
                continue
            if stamp >= now - HOUR_S:
                out.recent_ports.add(event.port)
                if event.kind == "add":
                    out.history.setdefault(event.port, PortHistory()).enumerations += 1
        for event in raw:  # last word per port, for an empty port's "last: ... left 18:20:36"
            history = out.history.setdefault(event.port, PortHistory())
            history.last_text = f"{event.product or 'device'} {event.serial}".strip() + \
                f" {'left' if event.kind == 'remove' else 'arrived'} {event.time:%H:%M:%S}"
    return out


def boot_start() -> float | None:
    """This boot's start, from /proc/stat ``btime`` (passive)."""

    try:
        for line in open("/proc/stat", encoding="ascii"):
            if line.startswith("btime "):
                return float(line.split()[1])
    except OSError:
        pass
    return None


class LiveKernelLog:
    """This boot's USB lines from ``journalctl -k``, re-read at most every ``ttl_s`` (or at once after a udev event)."""

    def __init__(self, ttl_s: float = 10.0):
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._at = -1e18
        self._boot: Boot | None = None
        self.note = ""

    def invalidate(self) -> None:
        self._at = -1e18

    def boots(self, monotonic_now: float) -> list[Boot]:
        with self._lock:
            if monotonic_now - self._at > self.ttl_s:
                start = boot_start()
                try:
                    since = dt.datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M:%S") if start else "today"
                    lines = journal.todays_kernel_log(since=since)
                    self.note = f"{len(lines)} kernel USB lines since boot"
                except (OSError, subprocess.SubprocessError) as error:  # no journal access: no history, never a crash
                    lines, self.note = [], f"journalctl unavailable: {type(error).__name__}: {error}"
                self._boot = Boot(start or 0.0, lines)
                self._at = monotonic_now
            return [self._boot] if self._boot else []


class UdevWatcher:
    """``udevadm monitor`` (listens only) -> a callback per USB add/remove, so the tree refreshes within a second."""

    def __init__(self, on_change: Callable[[], None]):
        self.on_change = on_change
        self.process = None
        self.note = ""

    def start(self) -> None:
        from io_facts import udev

        try:
            self.process = udev.monitor()
        except OSError as error:
            self.note = f"udevadm unavailable: {error}"
            return
        threading.Thread(target=self._pump, name="io-udev", daemon=True).start()

    def _pump(self) -> None:
        from io_facts import udev

        for _event in udev.parse_blocks(iter(self.process.stdout.readline, "")):
            self.on_change()

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()


def event_json(event: Event, utc_offset_s: int | None, path_names: dict[str, str] | None = None) -> dict:
    stamp = event.stamp
    when = iso(stamp, utc_offset_s)
    return {"stamp": stamp, "iso": when, "time": when[11:19] if when else "", "code": event.code,
            "kind": kind_label(event.code), "severity": event.severity, "message": event.message, "path": event.path,
            "name": (path_names or {}).get(event.path, ""), "hardware_id": event.hardware_id,
            "port": str(event.args.get("port", "")) if isinstance(event.args, dict) else "", "source": event.source,
            "seq": event.seq, "args": dict(event.args)}


def link_health(rows: Rows, layer, utc_offset_s: int | None = None) -> list[dict]:
    """§5 "the computer diagnoses its own wires": one row per (edge, metric), from the same records as the tree."""

    merged, now, out = rows.merged, rows.now, []
    for port, path in layer.port_paths.items():
        device_id = layer.port_devices.get(port)
        device_path = layer.device_paths.get(device_id) if device_id else None
        device_name = merged.entities[device_path].name if device_path else "empty"
        for status in rows.own.get(path, []):
            count = next((measurement.value for measurement in status.measurements if measurement.key == "enumerations / hour"), None)
            if count is None:
                continue
            out.append({"edge": f"Port {port} → {device_name}", "path": path, "kind": "USB", "metric": "enumerations / hour",
                        "value": str(count), "level": effective_level(status, now), "source": status.source})
        if device_id:
            speed = (layer.devices[device_id].get("attrs") or {}).get("speed_mbps")
            if speed:
                out.append({"edge": f"Port {port} → {device_name}", "path": device_path, "kind": "USB", "metric": "link speed",
                            "value": speed_text(speed), "level": "ok", "source": "sysfs speed"})
    for path, entity in merged.entities.items():
        if entity.kind != "bus":
            continue
        label = "CAN" if re.search(r"\bcan\b", f"{entity.name} {path.rsplit('/', 1)[-1]}", re.I) else "bus"
        own = rows.own.get(path, [])
        for status in own:
            metric = status.name.split(": ", 1)[-1]
            out.append({"edge": entity.name, "path": path, "kind": label, "metric": metric, "value": status.message,
                        "level": effective_level(status, now), "source": status.source})
        received = [status.received for status in own if status.received is not None]
        if received:
            newest = max(received)
            stale = any(effective_level(status, now) == STALE for status in own)
            when = iso(newest, utc_offset_s) or ""
            out.append({"edge": entity.name, "path": path, "kind": label, "metric": "last report",
                        "value": f"{when[11:19]} ({now - newest:.0f} s ago){' · STALE' if stale else ''}",
                        "level": STALE if stale else "ok", "source": "host receive clock"})
    out.sort(key=lambda row: (row["kind"] != "USB", -RANK.get(row["level"], 0)))
    return out
