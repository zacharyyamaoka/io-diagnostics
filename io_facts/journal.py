"""USB plug / unplug events from the kernel log, and a coalescer that turns flaps into one notice.

WHY coalesce: on 2026-10-21 adapter 28B3A25D enumerated five times in 17 s (four ~2 s flaps on
port 1-7, then it settled on 1-5). One pop-up per enumeration would have been five pop-ups.
The D405 on 6-2 dropped for 4 s at 18:07 and came back; that is one "dropped and came back"
notice, not "unplugged" + "plugged".

The same ``Coalescer`` runs live (fed by ``udevadm monitor``) and offline (fed by
``journalctl -k``), so the replay of today's real log is a test of the live logic.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from dataclasses import dataclass, field

SETTLE_S = 3.0  # a device must stay this long before "plugged" is announced
RETURN_S = 10.0  # a device that comes back within this is "dropped", not "unplugged"

_LINE = re.compile(r"^(?P<time>\S+) \S+ kernel: usb (?P<port>\d+-[\d.]+): (?P<message>.*)$")
_NEW = re.compile(r"new .*USB device number (?P<devnum>\d+)")
_IDS = re.compile(r"New USB device found, idVendor=(?P<vid>[0-9a-f]{4}), idProduct=(?P<pid>[0-9a-f]{4})")
_GONE = re.compile(r"USB disconnect, device number (?P<devnum>\d+)")


@dataclass
class UsbEvent:
    time: dt.datetime
    port: str
    kind: str  # "add" or "remove"
    devnum: int
    vid: str = ""
    pid: str = ""
    product: str = ""
    serial: str = ""


@dataclass
class Notice:
    time: dt.datetime
    kind: str  # "plugged", "unplugged", "dropped"
    identity: str
    port: str
    product: str
    flaps: list[str] = field(default_factory=list)  # ports of short-lived enumerations folded in
    gap_s: float = 0.0  # for "dropped": how long it was gone

    def text(self) -> str:
        if self.kind == "plugged":
            flapped = f" after {len(self.flaps)} flaps on {', '.join(sorted(set(self.flaps)))}" if self.flaps else ""
            return f"{self.product} {self.identity} plugged on {self.port}{flapped}"
        if self.kind == "dropped":
            return f"{self.product} {self.identity} dropped off USB for {self.gap_s:.0f} s and came back on {self.port}"
        return f"{self.product} {self.identity} unplugged from {self.port}"


def parse_kernel_log(lines: list[str]) -> list[UsbEvent]:
    """``journalctl -k -o short-iso`` lines -> add/remove events with descriptors filled in."""

    events: list[UsbEvent] = []
    open_by_port: dict[str, UsbEvent] = {}
    for line in lines:
        match = _LINE.match(line.strip())
        if not match:
            continue
        time = dt.datetime.fromisoformat(match["time"]).replace(tzinfo=None)
        port, message = match["port"], match["message"]
        if new := _NEW.search(message):
            event = UsbEvent(time, port, "add", int(new["devnum"]))
            events.append(event)
            open_by_port[port] = event
        elif (ids := _IDS.search(message)) and port in open_by_port:
            open_by_port[port].vid, open_by_port[port].pid = ids["vid"], ids["pid"]
        elif message.startswith("Product: ") and port in open_by_port:
            open_by_port[port].product = message.removeprefix("Product: ").strip()
        elif message.startswith("SerialNumber: ") and port in open_by_port:
            open_by_port[port].serial = message.removeprefix("SerialNumber: ").strip()
            # WHY ready time, not the "new device" line: today's 4th flap took 2 s to enumerate and
            # was usable for only 2 s; presence counts from when the device can actually be used.
            open_by_port[port].time = time
        elif gone := _GONE.search(message):
            added = open_by_port.pop(port, None)
            events.append(
                UsbEvent(time, port, "remove", int(gone["devnum"]), *(
                    (added.vid, added.pid, added.product, added.serial) if added else ("", "", "", "")
                ))
            )
    _fill_missing_serials(events)
    return sorted(events, key=lambda event: event.time)


def _fill_missing_serials(events: list[UsbEvent]) -> None:
    """An enumeration that dropped before its SerialNumber line takes the next same-model serial seen."""

    for index, event in enumerate(events):
        if event.serial or not event.vid:
            continue
        for later in events[index + 1 :]:
            if later.vid == event.vid and later.pid == event.pid and later.serial and (later.time - event.time).total_seconds() < 30:
                event.serial = later.serial
                break


class Coalescer:
    """Feed events in time order, call ``tick(now)``; get back settled notices."""

    def __init__(self, settle_s: float = SETTLE_S, return_s: float = RETURN_S):
        self.settle_s, self.return_s = settle_s, return_s
        self.present: dict[str, UsbEvent] = {}  # announced and currently attached
        self.pending_add: dict[str, tuple[UsbEvent, list[str]]] = {}  # attached, not yet settled
        self.pending_remove: dict[str, tuple[UsbEvent, dt.datetime]] = {}  # announced, now gone
        self.flaps: dict[str, list[str]] = {}

    @staticmethod
    def identity(event: UsbEvent) -> str:
        return event.serial or f"{event.vid}:{event.pid}@{event.port}"

    def feed(self, event: UsbEvent) -> list[Notice]:
        notices = self.tick(event.time)
        key = self.identity(event)
        if event.kind == "add":
            if key in self.pending_remove:  # came back inside RETURN_S
                _gone_event, gone_at = self.pending_remove.pop(key)
                self.present[key] = event
                notices.append(Notice(event.time, "dropped", key, event.port, event.product, gap_s=(event.time - gone_at).total_seconds()))
            else:
                self.pending_add[key] = (event, self.flaps.pop(key, []))
        else:
            if key not in self.pending_add and key not in self.present:
                # its add predates the log window: match the present device on the same port
                key = next((known for known, held in self.present.items() if held.port == event.port), key)
            if key in self.pending_add:  # left before settling: a flap
                added, flaps = self.pending_add.pop(key)
                self.flaps[key] = flaps + [added.port]
            elif key in self.present:
                self.present.pop(key)
                self.pending_remove[key] = (event, event.time)
        return notices

    def tick(self, now: dt.datetime) -> list[Notice]:
        notices = []
        for key, (added, flaps) in list(self.pending_add.items()):
            if (now - added.time).total_seconds() >= self.settle_s:
                self.pending_add.pop(key)
                self.present[key] = added
                notices.append(Notice(added.time + dt.timedelta(seconds=self.settle_s), "plugged", key, added.port, added.product, flaps))
        for key, (removed, gone_at) in list(self.pending_remove.items()):
            if (now - gone_at).total_seconds() >= self.return_s:
                self.pending_remove.pop(key)
                notices.append(Notice(gone_at + dt.timedelta(seconds=self.return_s), "unplugged", key, removed.port, removed.product))
        return notices


def replay(events: list[UsbEvent], already_present: dict[str, str] | None = None) -> list[Notice]:
    """Run the live coalescer over a recorded event list; ``already_present`` is ``{serial: port}``."""

    coalescer = Coalescer()
    for key, port in (already_present or {}).items():
        coalescer.present[key] = UsbEvent(dt.datetime.min, port, "add", 0, serial=key)
    notices: list[Notice] = []
    for event in events:
        notices += coalescer.feed(event)
    if events:
        notices += coalescer.tick(events[-1].time + dt.timedelta(seconds=RETURN_S + SETTLE_S))
    return notices


def todays_kernel_log(since: str = "today") -> list[str]:
    """Read-only: the kernel's own USB log lines (``journalctl -k``)."""

    result = subprocess.run(
        ["journalctl", "-k", "--since", since, "-o", "short-iso", "--no-pager"],
        capture_output=True, text=True, timeout=20,
    )
    return [line for line in result.stdout.splitlines() if " kernel: usb " in line]
