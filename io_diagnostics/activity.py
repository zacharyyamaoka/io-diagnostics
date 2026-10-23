"""Activity: every port event the computer sees, typed (``TYPES``), plus fault and level transitions and link health.

USB events are the kernel's own log (``journalctl -k``) through ``io_facts.journal``'s coalescer: one notice per
flap burst, "dropped and came back" instead of unplug + plug (28B3A25D enumerated five times in 17 s on 10-21).
The whole boot is replayed on every refresh, so the live view and a fixture replay of a recorded log run the SAME
code. The same lines also give link speeds, USB errors, driver binds and PCIe AER (``io_facts.kernel_log``).

``udevadm monitor`` (``io_facts.udev``, passive) is the instant half (``LiveActivity``): a plug is a PROVISIONAL event
("plugging in: ... on 1-1…") within a second of the kernel's uevent, and driver binds carry their /dev nodes. The kernel
log then confirms, merges or relabels it IN PLACE: both name it ``usb:<port>:<devnum>:<add|remove>`` (the kernel's
device number, which udev reports as ``DEVNUM``), so the log never shows it twice (``merge``). WHY (Zach, 2026-10-23):
"I am plugging in devices but I don't see any change in the activity log"; the kernel-log path alone showed a plug
3 s late (the settle window) and an unplug 10 s late (the return window), plus up to 10 s of re-read lag.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from io_facts import journal, kernel_log

from contract.record import ERROR, OK, RANK, STALE, UNKNOWN, WARN, Event, effective_level
from io_diagnostics.tree import PortHistory, Rows, speed_text

BOOT_WINDOW_S = 15.0  # enumerations this soon after boot are the boot, not a (re-)plug (V6, io_facts)
HOUR_S = 3600.0
JOURNAL_SOURCE = "io_facts.journal (journalctl -k, coalesced)"
KERNEL_SOURCE = "io_facts.kernel_log (journalctl -k)"
UDEV_SOURCE = "io_facts.udev (udevadm monitor)"
SYSFS_SOURCE = "io_facts.netdev (sysfs carrier)"
HISTORY_SOURCE = "io_diagnostics.history"
NOTICE_CODE = {"plugged": "Plugged", "unplugged": "Unplugged", "dropped": "ReEnumerated", "flapped": "Flapped"}
KIND_LABEL = {"Booted": "boot", "ShutDown": "shutdown", "Plugged": "plugged", "Unplugged": "unplugged",
              "ReEnumerated": "re-enumerated", "Flapped": "flapped", "Plugging": "plugging in", "Unplugging": "unplugging",
              "FaultConfirmed": "fault confirmed", "FaultHealed": "fault healed", "FaultCleared": "fault cleared",
              "LinkSpeed": "link speed", "LinkDowngraded": "link downgraded", "DriverBound": "driver bound",
              "DriverUnbound": "driver unbound", "NetCarrierUp": "link up", "NetCarrierDown": "link down",
              "PcieAerError": "PCIe AER error", "LevelChanged": "level changed"}
# Every event's ``type`` (docs/api-v1.md "Activity"): what ``?types=`` filters on. Any code not listed is a plugin's.
TYPES = ("usb", "link", "usb_error", "driver", "network", "pcie", "level", "fault", "boot", "plugin")
EVENT_TYPES = {"Booted": "boot", "ShutDown": "boot",
               **{code: "usb" for code in ("Plugged", "Unplugged", "ReEnumerated", "Flapped", "Plugging", "Unplugging")},
               "LinkSpeed": "link", "LinkDowngraded": "link",
               **{code: "usb_error" for _pattern, code, _severity in kernel_log.USB_ERRORS},
               "DriverBound": "driver", "DriverUnbound": "driver", "NetCarrierUp": "network", "NetCarrierDown": "network",
               "PcieAerError": "pcie", "FaultConfirmed": "fault", "FaultHealed": "fault", "FaultCleared": "fault",
               "LevelChanged": "level"}
DRIVER_CODES = ("DriverBound", "DriverUnbound")
SUPERSPEED_MBPS = 5000.0
MAX_LIVE_EVENTS = 500
FOLLOW_RETRY_S = 30.0  # a kernel-log follower that died is restarted at most this often (the TTL re-read covers the gap)
MAX_KERNEL_LINES = 200_000  # wanted kernel lines kept for this boot (a flapping port logs ~10 a second at worst)


def event_type(code: str) -> str:
    return EVENT_TYPES.get(code, "plugin")


def event_id(event: Event) -> str:
    """The event's stable id: the host's own events carry it in ``args["id"]``; a plugin's is its source and seq."""

    args = event.args if isinstance(event.args, dict) else {}
    if args.get("id"):
        return str(args["id"])
    if event.seq is not None:
        return f"{event.source or 'plugin'}#{event.seq}"
    return f"{event.code}@{event.stamp or 0:.3f}:{event.path}:{args.get('fault', '')}"


def generation(mbps: float | None) -> str:
    return speed_text(mbps) or "an unknown speed"


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
    consumed: set[str] = field(default_factory=set)  # raw enumeration ids a settled notice folds (provisional ones end)


def driver_text(driver: str, port: str, nodes, bound: bool) -> str:
    listed = ", ".join(sorted(nodes))
    if bound:
        return f"{driver} bound on {port}" + (f" → {listed}" if listed else "")
    return f"{driver} unbound from {port}" + (f" ({listed} gone)" if listed else "")


def _who(product: str, identity: str) -> str:
    return " ".join(text for text in (product, identity) if text) or "a USB device"


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
                                path="host", args={"devices": len(at_boot), "id": f"boot:{boot.start:.0f}"}, seq=seq,
                                source=JOURNAL_SOURCE))
        fastest: dict[str, float] = {}  # identity -> the fastest link it had this boot (a later slower one is a downgrade)
        for notice in notices:
            out.consumed.update(journal.raw_id(event) for event in notice.raws)
            settled = next((event for event in reversed(notice.raws) if event.kind == "add"), None)
            if notice in at_boot:
                if settled is not None and settled.speed_mbps:
                    fastest[notice.identity] = max(fastest.get(notice.identity, 0.0), settled.speed_mbps)
                continue
            seq += 1
            added = last_add.get(notice.identity)
            hardware_id = ""
            if added is not None and added.vid:
                hardware_id = f"usb:{added.vid}:{added.pid}:{added.serial}" if added.serial else f"usb:{added.vid}:{added.pid}@{added.port}"
            code = NOTICE_CODE[notice.kind]
            stamp = to_unix(notice.time, utc_offset_s)
            out.events.append(Event(code, stamp, "warn" if code in ("ReEnumerated", "Flapped") else "info",
                                    notice.text(), path=port_path(notice.port), hardware_id=hardware_id,
                                    args={"port": notice.port, "product": notice.product, "identity": notice.identity,
                                          "flaps": len(notice.flaps), "flap_ports": sorted(set(notice.flaps)),
                                          "gap_s": round(notice.gap_s, 1), "id": notice.id,
                                          "merged": [journal.raw_id(event) for event in notice.raws]},
                                    seq=seq, source=JOURNAL_SOURCE))
            if notice.kind in ("plugged", "dropped") and settled is not None and settled.speed_mbps:
                seq += 1
                speed, before = settled.speed_mbps, fastest.get(notice.identity, 0.0)
                fastest[notice.identity] = max(before, speed)
                who = _who(notice.product, notice.identity)
                args = {"port": notice.port, "speed_mbps": speed, "identity": notice.identity,
                        "id": f"{journal.raw_id(settled)}:link"}
                if speed < SUPERSPEED_MBPS <= before:
                    # WHY from the log alone: the same device linked at USB 3 earlier this boot, so this link is a cable,
                    # port or hub problem, even when the device has left by the time anyone looks
                    out.events.append(Event("LinkDowngraded", stamp, "warn", f"{who} on {notice.port} linked at {generation(speed)};"
                                            f" it linked at {generation(before)} earlier this boot", path=port_path(notice.port),
                                            hardware_id=hardware_id, args={**args, "before_mbps": before}, seq=seq,
                                            source=JOURNAL_SOURCE))
                else:
                    out.events.append(Event("LinkSpeed", stamp, "info", f"{who} on {notice.port} linked at {generation(speed)}",
                                            path=port_path(notice.port), hardware_id=hardware_id, args=args, seq=seq,
                                            source=JOURNAL_SOURCE))
        adds: dict[str, list[journal.UsbEvent]] = {}
        for event in raw:
            if event.kind == "add":
                adds.setdefault(event.port, []).append(event)
        for note in kernel_log.parse_port_notes(boot.lines):
            stamp = to_unix(note.time, utc_offset_s)
            if stamp > now or stamp - boot.start <= BOOT_WINDOW_S and note.code in DRIVER_CODES:
                continue  # WHY boot binds are skipped: like boot plugs, they are the boot, summed up by "PC booted"
            seq += 1
            out.events.append(note_event(note, stamp, adds, port_path, seq))
        if boot.end is not None and boot.end <= now:
            seq += 1
            out.events.append(Event("ShutDown", boot.end, "info", "PC shut down", path="host", seq=seq,
                                    args={"id": f"shutdown:{boot.end:.0f}"}, source="journalctl --list-boots"))
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


def note_event(note: kernel_log.PortNote, stamp: float, adds: dict[str, list[journal.UsbEvent]],
               port_path: Callable[[str], str], seq: int) -> Event:
    """One ``io_facts.kernel_log`` notice as an Event. A driver bind's id names the enumeration it belongs to (the
    port's latest add before it), the same way ``LiveActivity`` names a bind it saw from udev."""

    args: dict = {"port": note.port, "count": note.count, "kernel": note.message, "id": note.id}
    span = f" ({note.count}× in {(note.last - note.time).total_seconds():.0f} s)" if note.count > 1 and note.last else ""
    if note.code in DRIVER_CODES:
        enumeration = next((event for event in reversed(adds.get(note.port, [])) if event.time <= note.time + dt.timedelta(seconds=1)),
                           None)
        bound = note.code == "DriverBound"
        if enumeration is not None:
            args["id"] = f"{journal.raw_id(enumeration)}/{note.driver}:{'bind' if bound else 'unbind'}"
        args.update(driver=note.driver, nodes=sorted(note.nodes))
        return Event(note.code, stamp, "info", driver_text(note.driver, note.port or "?", note.nodes, bound),
                     path=port_path(note.port) if note.port else "host", args=args, seq=seq, source=KERNEL_SOURCE)
    if note.code == "PcieAerError":
        args.update(device=note.device, driver=note.driver)
        return Event(note.code, stamp, note.severity, f"PCIe AER on {note.device}: {note.message}{span}", path="host",
                     args=args, seq=seq, source=KERNEL_SOURCE)
    return Event(note.code, stamp, note.severity, f"{note.port}: {note.message}{span}", path=port_path(note.port),
                 args=args, seq=seq, source=KERNEL_SOURCE)


_USB_ID = re.compile(r"^usb:(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4}):(?P<serial>.+)$")
# What to try, by what the kernel saw (Zach, 2026-10-22: say it in plain words, not just "missing").
WHAT_TO_CHECK = {
    "never": "a data cable (a charge-only cable powers it but it never enumerates) · another USB port, not through a hub"
             " · the adapter itself: does its LED light?",
    "left": "the cable and both connectors (it enumerated, then dropped off) · its power · replug it",
    "unknown": "a data cable (not charge-only) · another USB port · the adapter itself",
}


def usb_id(hardware_id: str) -> tuple[str, str, str] | None:
    """``usb:<vid>:<pid>:<serial>`` -> (vid, pid, serial); None for any other id (a port-bound ``...@1-3`` too)."""

    found = _USB_ID.match(hardware_id or "")
    return (found["vid"].lower(), found["pid"].lower(), found["serial"]) if found and "@" not in found["serial"] else None


def kernel_evidence(boots: list[Boot], hardware_id: str, now: float, utc_offset_s: int | None = None,
                    unreadable: str = "") -> dict:
    """What this boot's kernel log says about one USB device that is absent now: ``kind`` ``never`` (no plug event
    since boot), ``left`` (it plugged in, then went away), or ``unknown`` (the log could not be read), plus ``text``.

    WHY the kernel log: it tells a device the computer never saw enumerate (charge-only cable, bad port, dead
    adapter) from one that came and went (connector, power), which "missing" alone does not."""

    ids = usb_id(hardware_id)
    current = max((boot for boot in boots if boot.start <= now), key=lambda boot: boot.start, default=None)
    if ids is None:
        return {"kind": "unknown", "text": f"{hardware_id} is not a USB id: no kernel evidence", "events": 0}
    if unreadable or current is None or not current.lines:
        why = unreadable or "no kernel USB lines since boot"
        return {"kind": "unknown", "text": f"the kernel log could not be read ({why}): no evidence whether it ever enumerated",
                "events": 0}
    vid, pid, serial = ids
    seen = [event for event in journal.parse_kernel_log(current.lines)
            if to_unix(event.time, utc_offset_s) <= now and event.vid.lower() == vid and event.pid.lower() == pid
            and (not event.serial or event.serial == serial)]
    if not seen:
        since = (iso(current.start, utc_offset_s) or "")[:16].replace("T", " ")
        return {"kind": "never", "text": f"the kernel saw no plug event since boot ({since})", "events": 0}
    first_add = next((event for event in seen if event.kind == "add"), seen[0])
    plugs = sum(1 for event in seen if event.kind == "add")
    text = f"plugged in at {first_add.time:%H:%M:%S} on {first_add.port}"
    if plugs > 1:
        text += f" ({plugs} times)"
    last = seen[-1]
    text += f", gone at {last.time:%H:%M:%S}" if last.kind == "remove" else ", not on USB now (no disconnect logged)"
    return {"kind": "left", "text": text, "events": len(seen)}


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
    """This boot's wanted kernel lines from ``journalctl -k``: read once, then FOLLOWED (``journalctl -f`` after the
    read's cursor), so a line is here within a second of the kernel logging it. Without a follower (it could not start
    or it died), the boot is re-read at most every ``ttl_s`` (or at once after a udev event), as before."""

    def __init__(self, ttl_s: float = 10.0, *, journalctl: tuple[str, ...] = ("journalctl",), follow: bool = True,
                 on_lines: Callable[[], None] | None = None, max_lines: int = MAX_KERNEL_LINES):
        self.ttl_s = ttl_s
        self.journalctl, self.follow, self.on_lines, self.max_lines = tuple(journalctl), follow, on_lines, max_lines
        self._lock = threading.Lock()
        self._at = -1e18
        self._start = 0.0
        self._lines: list[str] = []
        self._read = False
        self._follower: journal.KernelLogFollower | None = None
        self._follow_tried = -1e18
        self.note = ""

    def invalidate(self) -> None:
        self._at = -1e18

    def boots(self, monotonic_now: float) -> list[Boot]:
        with self._lock:
            if self._follower is not None and self._follower.alive():
                return [Boot(self._start, list(self._lines))]
            if monotonic_now - self._at > self.ttl_s:
                start = boot_start()
                cursor = ""
                try:
                    since = dt.datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M:%S") if start else "today"
                    lines, cursor = journal.read_kernel_log(since=since, journalctl=self.journalctl)
                    self.note = f"{len(lines)} kernel USB lines since boot"
                except (OSError, subprocess.SubprocessError) as error:  # no journal access: no history, never a crash
                    lines, self.note = [], f"journalctl unavailable: {type(error).__name__}: {error}"
                self._start, self._lines, self._read = start or 0.0, lines[-self.max_lines:], True
                self._at = monotonic_now
                if self.follow and cursor and monotonic_now - self._follow_tried >= FOLLOW_RETRY_S:
                    self._follow_tried = monotonic_now
                    follower = journal.KernelLogFollower(cursor, self._line, self.journalctl)
                    try:
                        follower.start()
                        self._follower = follower
                    except OSError as error:
                        self.note += f" · not followed ({type(error).__name__}: {error}): re-read every {self.ttl_s:g} s"
            return [Boot(self._start, list(self._lines))] if self._read else []

    def _line(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > self.max_lines:
                del self._lines[: len(self._lines) - self.max_lines]
        if self.on_lines:
            self.on_lines()

    def following(self) -> bool:
        return self._follower is not None and self._follower.alive()

    def close(self) -> None:
        if self._follower is not None:
            self._follower.stop()


class UdevWatcher:
    """``udevadm monitor`` (listens only) -> ``on_block`` per event (the provisional plug, a driver's node), then
    ``on_change``, so the activity log shows a plug and the tree refreshes within a second."""

    def __init__(self, on_change: Callable[[], None], on_block: Callable[[dict], object] | None = None,
                 command: list[str] | None = None):
        self.on_change, self.on_block, self.command = on_change, on_block, command
        self.process = None
        self.note = ""

    def start(self) -> None:
        from io_facts import udev

        try:
            self.process = udev.monitor(self.command or udev.MONITOR)
        except OSError as error:
            self.note = f"udevadm unavailable: {error}"
            return
        threading.Thread(target=self._pump, name="io-udev", daemon=True).start()

    def _pump(self) -> None:
        from io_facts import udev

        for block in udev.blocks(iter(self.process.stdout.readline, "")):
            if self.on_block is not None:
                self.on_block(block)
            self.on_change()

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


_INTERFACE_SEGMENT = re.compile(r"^(?P<port>\d+-[\d.]+):\d+\.\d+$")
_PORT = re.compile(r"^\d+-[\d.]+$")


def port_of_devpath(devpath: str) -> str:
    """The USB port a sysfs DEVPATH runs through (``.../usb1/1-1/1-1:1.0/video4linux/video0`` -> ``1-1``), or ""."""

    for segment in reversed(devpath.split("/")):
        if found := _INTERFACE_SEGMENT.match(segment):
            return found["port"]
    return ""


class LiveActivity:
    """What udev and sysfs say NOW, before the kernel log does: provisional plugs and unplugs, driver binds with the
    /dev nodes they made, NIC carrier changes. In memory, at most ``max_events`` (the oldest go first); passive only.

    Ids are the kernel log's (``usb:<port>:<devnum>:<add|remove>``, a bind ``<its enumeration's add>/<driver>:bind``),
    so ``merge`` folds an event the kernel log has confirmed into that one: one row, provisional -> confirmed."""

    def __init__(self, now: Callable[[], float], sys_root: Path = Path("/sys"), max_events: int = MAX_LIVE_EVENTS):
        self.now, self.sys_root, self.max_events = now, Path(sys_root), max_events
        self._events: dict[str, Event] = {}  # id -> event, oldest first
        self._devnum: dict[str, int] = {}  # port -> the kernel device number of its latest enumeration
        self._product: dict[str, str] = {}
        self._iface_driver: dict[str, str] = {}  # an interface's DEVPATH -> the driver bound to it (unbind names none)
        self._carrier: dict[str, bool | None] = {}
        self._lock = threading.Lock()

    def udev(self, block: dict) -> bool:
        """One ``udevadm monitor --property`` block; True when it made or changed an event."""

        from io_facts import udev

        action, subsystem, devpath = block.get("ACTION", ""), block.get("SUBSYSTEM", ""), block.get("DEVPATH", "")
        with self._lock:
            if subsystem == "usb" and block.get("DEVTYPE") == "usb_device" and action in ("add", "remove"):
                port = devpath.rsplit("/", 1)[-1]
                if not _PORT.match(port):
                    return False  # a root hub ("usb1"): host structure, not a plug
                devnum = int(block.get("DEVNUM") or 0)
                product = udev.product(block) or self._product.get(port, "")
                serial, vid, pid = block.get("ID_SERIAL_SHORT", ""), block.get("ID_VENDOR_ID", ""), block.get("ID_MODEL_ID", "")
                if action == "add":
                    self._devnum[port], self._product[port] = devnum, product
                who = _who(product, serial)
                hardware_id = (f"usb:{vid}:{pid}:{serial}" if serial else f"usb:{vid}:{pid}@{port}") if vid else ""
                plugging = action == "add"
                self._put(Event("Plugging" if plugging else "Unplugging", self.now(), "info",
                                f"plugging in: {who} on {port}…" if plugging else f"unplugging: {who} from {port}…",
                                path="host", hardware_id=hardware_id, source=UDEV_SOURCE,
                                args={"id": f"usb:{port}:{devnum}:{action}", "provisional": True, "port": port,
                                      "product": product, "serial": serial, "devnum": devnum}))
                return True
            if subsystem == "usb" and block.get("DEVTYPE") == "usb_interface" and action in ("bind", "unbind"):
                driver = block.get("DRIVER") or self._iface_driver.get(devpath, "")
                if action == "bind":
                    self._iface_driver[devpath] = driver
                else:
                    self._iface_driver.pop(devpath, None)
                port = port_of_devpath(devpath)
                if not driver or not port or driver in ("hub", "usbfs"):
                    return False
                return self._driver(port, driver, action == "bind", [])
            if subsystem in ("tty", "video4linux", "net") and action in ("add", "remove"):
                port = port_of_devpath(devpath)
                driver = block.get("ID_USB_DRIVER") or block.get("ID_NET_DRIVER") or ""
                if not port or not driver:
                    return False  # not on USB (an on-board serial port, a PCIe NIC), or no driver named
                node = block.get("DEVNAME") or block.get("INTERFACE") or devpath.rsplit("/", 1)[-1]
                return self._driver(port, driver, action == "add", [node])
        return False

    def _enumeration(self, port: str) -> str:
        devnum = self._devnum.get(port)
        if devnum is None:  # plugged before the host started: sysfs says which enumeration it is (passive read)
            try:
                devnum = int((self.sys_root / "bus" / "usb" / "devices" / port / "devnum").read_text().strip())
            except (OSError, ValueError):
                return f"usb:{port}:?:add"
        return f"usb:{port}:{devnum}:add"

    def _driver(self, port: str, driver: str, bound: bool, nodes: list[str]) -> bool:
        key = f"{self._enumeration(port)}/{driver}:{'bind' if bound else 'unbind'}"
        held = self._events.get(key)
        if held is not None:
            merged = sorted(set(held.args.get("nodes", [])) | set(nodes))
            if merged == held.args.get("nodes"):
                return False
            self._events[key] = dataclasses.replace(held, message=driver_text(driver, port, merged, bound),
                                                    args={**held.args, "nodes": merged})
            return True
        self._put(Event("DriverBound" if bound else "DriverUnbound", self.now(), "info", driver_text(driver, port, nodes, bound),
                        path="host", source=UDEV_SOURCE, args={"id": key, "port": port, "driver": driver, "nodes": sorted(set(nodes))}))
        return True

    def carriers(self, nics: list[dict]) -> bool:
        """The skeleton's NICs (sysfs ``carrier``): an event per change since the last look (the first look is none)."""

        changed = False
        with self._lock:
            for nic in nics:
                attrs = nic.get("attrs") or {}
                name = str(attrs.get("ifname") or nic.get("id", ""))
                carrier = attrs.get("carrier")
                before = self._carrier.get(name, "unseen")
                self._carrier[name] = carrier
                if before == "unseen" or carrier is None or before is None or before == carrier:
                    continue
                changes = attrs.get("carrier_changes")
                speed = attrs.get("speed_mbps")
                link = f" · {speed_text(speed)}" if carrier and speed and speed > 0 else ""
                self._put(Event("NetCarrierUp" if carrier else "NetCarrierDown", self.now(), "info" if carrier else "warn",
                                f"{name}: link {'up' if carrier else 'down (no carrier)'}{link}", path="host", source=SYSFS_SOURCE,
                                args={"id": f"net:{name}:{changes if changes is not None else self.now()}", "interface": name,
                                      "device_id": str(nic.get("id", "")), "carrier_changes": changes}))
                changed = True
        return changed

    def _put(self, event: Event) -> None:
        self._events[event_id(event)] = event
        while len(self._events) > self.max_events:
            self._events.pop(next(iter(self._events)))

    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events.values())


def merge(journal_events: list[Event], consumed: set[str], live: list[Event]) -> tuple[list[Event], list[str]]:
    """The kernel log's events plus the live ones it has not said yet; ``(events, pending provisional ids)``.

    One id is one event: a provisional plug the log has settled (its id, or a raw enumeration a notice folded) is
    dropped for the log's own; a driver bind both saw keeps the log's words and gains the /dev nodes only udev names."""

    out = list(journal_events)
    index = {event_id(event): number for number, event in enumerate(out)}
    pending = []
    for event in live:
        key = event_id(event)
        if key in index:
            held = out[index[key]]
            if held.code in DRIVER_CODES:
                nodes = sorted(set(held.args.get("nodes", [])) | set(event.args.get("nodes", [])))
                if nodes != held.args.get("nodes"):
                    out[index[key]] = dataclasses.replace(held, args={**held.args, "nodes": nodes}, message=driver_text(
                        held.args.get("driver", ""), held.args.get("port", "?"), nodes, held.code == "DriverBound"))
            continue
        if event.args.get("provisional"):
            if key in consumed:
                continue
            pending.append(key)
        out.append(event)
    return out, pending


def level_events(transitions: list[tuple], names: dict[str, str] | None = None) -> list[Event]:
    """``History.transitions`` -> one ``LevelChanged`` event each (type ``level``): "<check>: ok → error (Code: why)"."""

    out = []
    severity = {OK: "info", WARN: "warn", UNKNOWN: "warn", STALE: "warn", ERROR: "error"}
    for at, path, name, before, after, code, message in transitions:
        why = ": ".join(text for text in (code, message) if text) if after != OK else ""
        out.append(Event("LevelChanged", at, severity.get(after, "warn"), f"{name}: {before} → {after}" + (f" ({why})" if why else ""),
                         path=path, source=HISTORY_SOURCE,
                         args={"id": f"level:{path}|{name}|{at:.3f}|{after}", "status": name, "from": before, "to": after,
                               "status_code": code}))
    return out


def event_json(event: Event, utc_offset_s: int | None, path_names: dict[str, str] | None = None) -> dict:
    stamp = event.stamp
    when = iso(stamp, utc_offset_s)
    args = {key: value for key, value in dict(event.args).items() if key not in ("id", "provisional")}
    return {"stamp": stamp, "iso": when, "time": when[11:19] if when else "", "code": event.code,
            "kind": kind_label(event.code), "type": event_type(event.code), "id": event_id(event),
            "provisional": bool(dict(event.args).get("provisional")), "severity": event.severity, "message": event.message,
            "path": event.path, "name": (path_names or {}).get(event.path, ""), "hardware_id": event.hardware_id,
            "port": str(event.args.get("port", "")) if isinstance(event.args, dict) else "", "source": event.source,
            "seq": event.seq, "args": args}


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
