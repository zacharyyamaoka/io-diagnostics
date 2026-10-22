"""Live USB events from ``udevadm monitor --udev --property`` (passive: it only listens).

Feeds the same ``journal.Coalescer`` as the kernel-log replay, so live and replayed behaviour match.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from collections.abc import Iterator

from .journal import UsbEvent

MONITOR = ["udevadm", "monitor", "--udev", "--property", "--subsystem-match=usb"]


def parse_blocks(lines: Iterator[str]) -> Iterator[UsbEvent]:
    """Yield an add/remove ``UsbEvent`` per usb_device block (interfaces and hubs' ports skipped)."""

    block: dict[str, str] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if line.strip():
            if "=" in line and not line.startswith(("UDEV", "KERNEL")):
                key, _, value = line.partition("=")
                block[key] = value
            continue
        event = _event(block)
        block = {}
        if event:
            yield event
    if event := _event(block):
        yield event


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
        product=block.get("ID_MODEL", "").replace("_", " "),
        serial=block.get("ID_SERIAL_SHORT", ""),
    )


def monitor() -> subprocess.Popen:
    return subprocess.Popen(MONITOR, stdout=subprocess.PIPE, text=True, bufsize=1)
