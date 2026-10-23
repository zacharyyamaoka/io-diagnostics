"""Live USB events from ``udevadm monitor --udev --property`` (passive: it only listens).

Feeds the same ``journal.Coalescer`` as the kernel-log replay, so live and replayed behaviour match. ``blocks`` yields
every event's properties (USB devices and interfaces, and the tty / video4linux / net nodes a driver makes), which
the host turns into provisional plug events and driver binds within a second (io_diagnostics.activity.LiveActivity).
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from collections.abc import Iterator, Sequence

from .journal import UsbEvent

# WHY these four subsystems (several --subsystem-match are OR'ed): usb for plugs and interface binds, tty and
# video4linux for the /dev nodes a driver makes (ttyACM0, video0), net for a USB NIC's interface
MONITOR = ["udevadm", "monitor", "--udev", "--property", "--subsystem-match=usb", "--subsystem-match=tty",
           "--subsystem-match=video4linux", "--subsystem-match=net"]


def blocks(lines: Iterator[str]) -> Iterator[dict[str, str]]:
    """One ``{property: value}`` per event block (the ``UDEV [..] add ...`` header line and blank lines skipped)."""

    block: dict[str, str] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if line.strip():
            if "=" in line and not line.startswith(("UDEV", "KERNEL")):
                key, _, value = line.partition("=")
                block[key] = value
            continue
        if block:
            yield block
        block = {}
    if block:
        yield block


def parse_blocks(lines: Iterator[str]) -> Iterator[UsbEvent]:
    """Yield an add/remove ``UsbEvent`` per usb_device block (interfaces and hubs' ports skipped)."""

    for block in blocks(lines):
        if event := _event(block):
            yield event


def decode(text: str) -> str:
    """udev's ``\\x20`` escapes (``ID_MODEL_ENC``) -> the descriptor string as the device wrote it."""

    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda found: chr(int(found[1], 16)), text)


def product(block: dict[str, str]) -> str:
    """The USB product string: the descriptor's own (``ID_MODEL_ENC``, trailing blanks dropped as the kernel log
    does), else the usb.ids name, else udev's underscored ``ID_MODEL``."""

    if block.get("ID_MODEL_ENC"):
        return decode(block["ID_MODEL_ENC"]).strip()
    return (block.get("ID_MODEL_FROM_DATABASE") or block.get("ID_MODEL", "").replace("_", " ")).strip()


def _event(block: dict[str, str]) -> UsbEvent | None:
    if block.get("SUBSYSTEM") != "usb" or block.get("DEVTYPE") != "usb_device" or block.get("ACTION") not in ("add", "remove"):
        return None
    return UsbEvent(
        time=dt.datetime.now(),
        port=block.get("DEVPATH", "").rsplit("/", 1)[-1],
        kind=block["ACTION"],
        devnum=int(block.get("DEVNUM", "0") or 0),
        vid=block.get("ID_VENDOR_ID", ""),
        pid=block.get("ID_MODEL_ID", ""),
        product=product(block),
        serial=block.get("ID_SERIAL_SHORT", ""),
    )


def monitor(command: Sequence[str] = MONITOR) -> subprocess.Popen:
    return subprocess.Popen(list(command), stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True, bufsize=1)
