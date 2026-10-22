"""USB devices from sysfs: descriptors, the tty / video nodes they expose, and when they appeared.

Read-only. ``/sys/bus/usb/devices/<port>`` is created by the kernel at enumeration, so its mtime
is the passive "plugged at" time (1-3 read 06:14:39 and 6-2 read 18:07:08 on 2026-10-21, matching
the kernel log to the millisecond).
"""

from __future__ import annotations

import datetime as dt
import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SYSFS_USB = Path("/sys/bus/usb/devices")
BY_ID_DIR = Path("/dev/serial/by-id")
PORT_NAME = re.compile(r"^\d+-[\d.]+$")  # a device node like 1-5 or 7-1.3, never an interface (1-5:1.0)


@dataclass(frozen=True)
class UsbDevice:
    port: str  # physical USB port path, e.g. "1-5"
    vid: str
    pid: str
    manufacturer: str
    product: str
    serial: str  # the USB iSerial descriptor (NOT the RealSense SDK serial; see realsense plugins)
    speed_mbps: int
    devnum: int
    plugged_at: str  # ISO time the kernel created the sysfs node
    ttys: tuple[str, ...] = ()
    by_id: tuple[str, ...] = ()
    video_nodes: tuple[str, ...] = ()
    simulated: bool = False  # True only for fixture devices injected by a demo
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def usb_id(self) -> str:
        return f"{self.vid}:{self.pid}"


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def _by_id_links() -> dict[str, list[str]]:
    """``{/dev/ttyACM0: [/dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00]}``."""

    links: dict[str, list[str]] = {}
    for link in sorted(glob.glob(str(BY_ID_DIR / "*"))):
        links.setdefault(os.path.realpath(link), []).append(link)
    return links


def _video_nodes_under(device_dir: Path) -> list[str]:
    nodes = []
    for entry in sorted(Path("/sys/class/video4linux").glob("video*")):
        if str((entry / "device").resolve()).startswith(str(device_dir.resolve()) + "/"):
            nodes.append(f"/dev/{entry.name}")
    return nodes


def list_usb_devices(sysfs_root: Path = SYSFS_USB) -> list[UsbDevice]:
    """Every enumerated USB device (root hubs excluded), sorted by port."""

    by_id = _by_id_links()
    devices = []
    for device_dir in sorted(sysfs_root.iterdir()):
        if not PORT_NAME.match(device_dir.name) or not (device_dir / "idVendor").is_file():
            continue
        ttys = sorted(f"/dev/{Path(tty).name}" for tty in glob.glob(str(device_dir / "*" / "tty" / "*")))
        plugged_at = dt.datetime.fromtimestamp(device_dir.lstat().st_mtime if device_dir.is_symlink() else device_dir.stat().st_mtime)
        devices.append(
            UsbDevice(
                port=device_dir.name,
                vid=_read(device_dir / "idVendor"),
                pid=_read(device_dir / "idProduct"),
                manufacturer=_read(device_dir / "manufacturer"),
                product=_read(device_dir / "product"),
                serial=_read(device_dir / "serial"),
                speed_mbps=int(_read(device_dir / "speed") or 0),
                devnum=int(_read(device_dir / "devnum") or 0),
                plugged_at=plugged_at.isoformat(timespec="seconds"),
                ttys=tuple(ttys),
                by_id=tuple(link for tty in ttys for link in by_id.get(tty, [])),
                video_nodes=tuple(_video_nodes_under(device_dir)),
            )
        )
    return devices


def generic_fdcanusb_target() -> str | None:
    """Where the generic ``/dev/fdcanusb`` udev symlink points now (it follows the NEWEST adapter)."""

    link = Path("/dev/fdcanusb")
    return os.path.realpath(link) if link.exists() else None
