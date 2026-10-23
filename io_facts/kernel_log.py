"""Port activity besides plug / unplug, from the same kernel log lines (``journalctl -k``, read-only).

``journal.py`` turns enumerations into plug notices; this module reads what else the kernel says about a port:

- **USB errors** (drivers/usb/core/hub.c's own words): a descriptor read error, a device not accepting its address,
  a reset, over-current, a port disabled by the hub (EMI), a device that could not be enumerated or enabled;
- **driver binds and unbinds**: ``cdc_acm ... ttyACM0: USB ACM device``, ``Found UVC 1.50 device ...`` (uvcvideo),
  a USB serial converter ``detected`` / ``now attached to ttyUSB0`` / ``now disconnected from ttyUSB0``;
- **PCIe AER**: ``AER: ... error received`` and ``PCIe Bus Error: severity=..., type=...`` (one notice per burst).

A burst of the same message on the same port (descriptor reads retry 2-4 times in a second) is ONE notice with a
``count``. Each notice has a stable ``id`` (its code, where, and its first time), so a re-read of the whole boot gives
the same notices again.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

COALESCE_S = 5.0  # the same message on the same port within this is one notice (its count grows)

_USB = re.compile(r"^(?P<time>\S+) \S+ kernel: usb (?P<where>usb\d+-port\d+|\d+-[\d.]+(?:-port\d+)?): (?P<message>.*)$")
_DRIVER = re.compile(r"^(?P<time>\S+) \S+ kernel: (?P<driver>[\w-]+) (?P<where>\S+?):? (?P<message>.*)$")
_INTERFACE = re.compile(r"^(?P<port>\d+-[\d.]+):\d+\.\d+$")
_PCI = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
# (pattern on the message, code, severity): hub.c / usb core messages, in the kernel's own words
USB_ERRORS = (
    (re.compile(r"device descriptor read/\w+, error (?P<error>-?\d+)"), "UsbDescriptorReadError", "warn"),
    (re.compile(r"device not accepting address \d+, error (?P<error>-?\d+)"), "UsbNotAcceptingAddress", "warn"),
    (re.compile(r"^reset .*USB device number \d+"), "UsbReset", "warn"),
    (re.compile(r"over-current condition"), "UsbOverCurrent", "error"),
    (re.compile(r"disabled by hub \(EMI\?\)"), "UsbPortDisabled", "warn"),
    (re.compile(r"unable to enumerate USB device"), "UsbEnumerationFailed", "error"),
    (re.compile(r"Cannot enable\. Maybe the USB cable is bad\?"), "UsbCannotEnable", "error"),
    (re.compile(r"can't set config #\d+, error (?P<error>-?\d+)"), "UsbConfigFailed", "warn"),
    (re.compile(r"cannot reset \(err = (?P<error>-?\d+)\)"), "UsbResetFailed", "error"),
)
_UVC = re.compile(r"^Found UVC [\d.]+ device (?P<name>.*?)\s*\([0-9a-f]{4}:[0-9a-f]{4}\)")
_ACM = re.compile(r"^(?P<node>tty\w+): USB ACM device")
_DETECTED = re.compile(r"^(?P<what>.+) converter detected")
_ATTACHED = re.compile(r"^(?P<what>.+) converter now attached to (?P<node>tty\w+)")
_DISCONNECTED = re.compile(r"^(?P<what>.+) converter now disconnected from (?P<node>tty\w+)")
_AER = re.compile(r"AER: |PCIe Bus Error")
_SEVERITY = re.compile(r"severity=(?P<severity>Corrected|Uncorrected \((?:Non-)?Fatal\)|Fatal)")
_FROM = re.compile(r"from (?P<source>[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7])")


@dataclass
class PortNote:
    time: dt.datetime  # its first line (naive, as logged)
    code: str
    severity: str  # info | warn | error
    message: str  # the kernel's own words (the first line; for AER the "PCIe Bus Error" line when there is one)
    port: str = ""  # a USB port ("1-1", "1-1.2"), or "" for PCIe
    device: str = ""  # PCIe only: the PCI address the error is about
    driver: str = ""
    nodes: list[str] = field(default_factory=list)  # /dev nodes a bind made or an unbind took away
    via: str = ""  # PCIe only: the port that reported it (its lines of one burst name the device only once)
    count: int = 1
    last: dt.datetime | None = None  # its latest line

    @property
    def id(self) -> str:
        return f"{self.code}:{self.port or self.device}:{self.driver}:{self.time.isoformat()}"


def port_of(where: str) -> str:
    """``usb1-port3`` -> ``1-3``, ``1-1-port2`` -> ``1-1.2``, ``1-1`` -> ``1-1`` (the device a port leads to)."""

    if found := re.fullmatch(r"usb(\d+)-port(\d+)", where):
        return f"{found[1]}-{found[2]}"
    if found := re.fullmatch(r"(\d+-[\d.]+)-port(\d+)", where):
        return f"{found[1]}.{found[2]}"
    return where


def _time(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=None)


def parse_port_notes(lines: list[str]) -> list[PortNote]:
    """``journalctl -k`` lines -> USB errors, driver binds / unbinds and PCIe AER notices, oldest first."""

    notes: list[PortNote] = []
    open_notes: dict[tuple, PortNote] = {}  # (code, where, driver) -> the notice a repeat folds into
    node_port: dict[str, str] = {}  # /dev/ttyUSB0 -> the port a converter attached it on

    def add(note: PortNote, fold: bool = True) -> PortNote:
        key = (note.code, note.port or note.via or note.device, note.driver)
        held = open_notes.get(key)
        if fold and held is not None and (note.time - (held.last or held.time)).total_seconds() <= COALESCE_S:
            held.count += 1
            held.last = note.time
            held.nodes += [node for node in note.nodes if node not in held.nodes]
            if note.code == "PcieAerError" and "PCIe Bus Error" in note.message and "PCIe Bus Error" not in held.message:
                held.message = note.message
                held.severity = note.severity
            if note.code == "PcieAerError" and held.device == held.via and note.device != note.via:
                held.device = note.device  # a later line of the burst named the device it is about
            return held
        open_notes[key] = note
        notes.append(note)
        return note

    for raw in lines:
        line = raw.strip()
        if found := _USB.match(line):
            time, where, message = _time(found["time"]), found["where"], found["message"]
            port = port_of(where)
            for pattern, code, severity in USB_ERRORS:
                if pattern.search(message):
                    add(PortNote(time, code, severity, message, port=port))
                    break
            else:
                if _UVC.match(message):
                    add(PortNote(time, "DriverBound", "info", message, port=port, driver="uvcvideo"))
                elif attached := _ATTACHED.match(message):
                    node = f"/dev/{attached['node']}"
                    node_port[node] = port
                    held = next((note for note in reversed(notes) if note.code == "DriverBound" and note.port == port), None)
                    if held is not None and (time - (held.last or held.time)).total_seconds() <= COALESCE_S:
                        held.nodes.append(node)
                        held.last = time
                    else:
                        add(PortNote(time, "DriverBound", "info", message, port=port, driver="usbserial", nodes=[node]))
            continue
        found = _DRIVER.match(line)
        if not found:
            continue
        time, driver, where, message = _time(found["time"]), found["driver"], found["where"], found["message"]
        if _PCI.match(where) and _AER.search(message):
            severity = _SEVERITY.search(message)
            level = "warn" if severity is None or severity["severity"] == "Corrected" else "error"
            source = _FROM.search(message)
            add(PortNote(time, "PcieAerError", level, message, device=source["source"] if source else where, driver=driver, via=where))
            continue
        interface = _INTERFACE.match(where)
        if interface and (acm := _ACM.match(message)):
            add(PortNote(time, "DriverBound", "info", message, port=interface["port"], driver=driver,
                         nodes=[f"/dev/{acm['node']}"]))
        elif interface and _DETECTED.match(message):
            add(PortNote(time, "DriverBound", "info", message, port=interface["port"], driver=driver))
        elif where.startswith("tty") and (gone := _DISCONNECTED.match(message)):
            node = f"/dev/{gone['node']}"
            add(PortNote(time, "DriverUnbound", "info", message, port=node_port.get(node, ""), driver=driver, nodes=[node]),
                fold=False)
    return notes


__all__ = ["PortNote", "parse_port_notes", "port_of", "USB_ERRORS", "COALESCE_S"]
