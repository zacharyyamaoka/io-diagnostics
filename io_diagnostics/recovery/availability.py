"""Which rungs this machine can run for a device right now, and in plain words why each other one cannot.

Read-only: it looks at file modes, installed tools, the hub's power switching and the plugins declared, and never opens a
device. The ladder asks it before each rung; ``python -m io_diagnostics.recovery check`` prints it for every device here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from io_diagnostics import PLUGINS_DIR, usb_reset
from io_diagnostics.recovery import device as devices
from io_diagnostics.recovery.profile import RecoveryProfile
from io_diagnostics.recovery.rung import (AUTHORIZED, NATIVE, PAGE, POWER_CYCLE, REOPEN, USBRESET, VIA_COMMAND, VIA_PLUGIN, Rung)
from io_diagnostics.recovery.system import DEFAULT_HUB_FACTS, HUB_FACTS_ENV, Seams, fill, tool_paths
from io_diagnostics.usb_reset import dev_root, sysfs_root

INSTALLER = "sudo bash /home/bam/io-diagnostics/tools/install_device_reset_access.sh"


@dataclass(frozen=True)
class Availability:
    usable: bool
    why: str  # plain words: how it would run, or why it cannot
    needs: str = ""  # what would make it usable: "the one-time installer", "a hub with per-port power switching", "hardware"

    def to_json(self) -> dict:
        return {"usable": self.usable, "why": self.why, "needs": self.needs}


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def hub_facts() -> dict[str, tuple[str, str]]:
    """``{hub sysfs name: (vid:pid, individual | ganged | none | unknown)}`` from the file the installer wrote as root (a hub's
    power switching is in its hub descriptor, which only a process that may open the hub can read)."""

    path = Path(os.environ.get(HUB_FACTS_ENV) or DEFAULT_HUB_FACTS)
    out = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        words = line.split()
        if len(words) >= 3 and not line.startswith("#"):
            out[words[0]] = (words[1].lower(), words[2])
    return out


def hub_power(hub: str, seams: Seams) -> tuple[str, str]:
    """``(individual | ganged | none | unknown, where that came from)`` for a hub by its sysfs name (``usb7``, ``1-5``)."""

    directory = sysfs_root() / hub
    ids = f"{_read(directory / 'idVendor')}:{_read(directory / 'idProduct')}".lower()
    known = hub_facts().get(hub)
    if known and (known[0] == ids or not directory.is_dir()):
        return known[1], "read by the installer"
    busnum, devnum = _read(directory / "busnum"), _read(directory / "devnum")
    node = dev_root() / "bus" / "usb" / f"{int(busnum):03d}" / f"{int(devnum):03d}" if busnum.isdigit() and devnum.isdigit() else None
    if node is not None and (os.geteuid() == 0 or _writable(node)):
        return seams.lsusb_power(int(busnum), int(devnum)), "read from its hub descriptor now"
    return "unknown", "its hub descriptor needs root to read; the installer reads it once"


def plugin_declared(name: str, plugins_dir: Path | None = None):
    from io_diagnostics.plugins import load_plugins

    specs, _problems = load_plugins(plugins_dir or PLUGINS_DIR)
    return next((spec for spec in specs if spec.name == name and spec.enabled), None)


def rung_availability(rung: Rung, device: devices.Device, profile: RecoveryProfile, seams: Seams,
                      plugins_dir: Path | None = None) -> Availability:
    if rung.kind == PAGE:
        return Availability(True, "always: the alarms log, an event, and the page command when one is set")
    if rung.kind == REOPEN:
        return Availability(True, f"a fresh {profile.probe.replace('_', ' ')} check; resets nothing")
    if rung.kind == NATIVE:
        return _native(rung, device, plugins_dir)
    if device.is_can:
        return Availability(False, "a CAN board is not a USB device: its adapter's ladder resets the bus")
    if not device.port:
        return Availability(False, "RobotDx does not know which USB port it is on")
    target = device.target()
    # WHY: the installer opens only the rig's device classes (a keyboard's node stays root's), so a device with no rule of its
    # own is told it needs one, not the installer
    grant = "the one-time installer" if profile.udev_match else "a udev rule line of its own (the installer covers the rig's classes only)"
    if rung.kind == USBRESET:
        method = usb_reset.methods(target)[0]
        if method.usable:
            return Availability(True, f"usbreset {target.bus_dev}")
        if not target.present:
            return Availability(False, "it is not on USB now (a port reset needs its node)")
        return Availability(False, method.why_not, grant if "write" in method.why_not else "usbutils")
    if rung.kind == AUTHORIZED:
        path = sysfs_root() / device.port / "authorized"
        if not path.exists():
            return Availability(False, "it is not on USB now (its authorized switch is gone with it)")
        if _writable(path):
            return Availability(True, f"write 0, then 1, to {path}")
        return Availability(False, f"{path} is root only", grant)
    if rung.kind == POWER_CYCLE:
        return _power_cycle(device, seams, grant)
    return Availability(False, f"unknown rung {rung.kind}")


def _native(rung: Rung, device: devices.Device, plugins_dir: Path | None) -> Availability:
    if rung.via == VIA_COMMAND:
        tool = fill(rung.command[0], tool_paths())
        if not os.access(tool, os.X_OK):
            return Availability(False, f"{tool} is not installed", "Sim to Real's fdcanusb-reset")
        target = device.target() if device.port else None
        node = target.node if target else None
        if node is not None and target.present and not _writable(node):
            return Availability(False, f"you cannot write to {node}", "the one-time installer")
        shown = fill(" ".join(rung.command), {"fdcanusb_reset": Path(tool).name, "serial": device.serial or "?",
                                              "wait_s": f"{rung.settle_s:g}", "port": device.port})
        return Availability(True, shown)
    if rung.via == VIA_PLUGIN:
        spec = plugin_declared(rung.plugin, plugins_dir)
        if spec is None:
            return Availability(False, f"no enabled {rung.plugin} driver in plugins.d", f"the {rung.plugin} plugin")
        return Availability(True, f"the {rung.plugin} driver's {rung.action} action")
    return Availability(False, "no native reset")


def _power_cycle(device: devices.Device, seams: Seams, grant: str) -> Availability:
    here = devices.port_dir(device.port)
    hub = devices.hub_name(device.port)
    mode, source = hub_power(hub, seams)
    if mode in ("ganged", "none"):
        what = "switches all its ports together" if mode == "ganged" else "cannot switch port power"
        return Availability(False, f"hub {hub} {what} ({source}): its port disable is only a logical disconnect",
                            "a hub with per-port power switching (hardware)")
    if mode != "individual":
        return Availability(False, f"unknown whether hub {hub} can switch each port's power: {source}", "the one-time installer")
    if here is None or not (here / "disable").exists():
        return Availability(False, f"the kernel has no port disable switch for {device.port} (Linux 6.0+)")
    switches = [here / "disable"] + ([here / "peer" / "disable"] if (here / "peer" / "disable").exists() else [])
    blocked = [str(path) for path in switches if not _writable(path)]
    if blocked:
        return Availability(False, f"{', '.join(blocked)} is root only", grant)
    return Availability(True, f"hub {hub} switches port power per port: write 1, then 0, to {here.name}/disable"
                              + (" and its USB 3 peer" if len(switches) > 1 else ""))
