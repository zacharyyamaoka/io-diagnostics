"""Which device a reset is for, as sysfs shows it NOW, and what it looked like before and after a rung (the evidence).

A device is named by a key that survives re-enumeration (its device number changes, its serial does not):

    usb:0483:5740:0607623A      vendor:product:serial (best: two identical adapters are never mixed up)
    usb:7-1                     a USB port (a device with no serial, like the OBSBOT: it is where it is plugged in)
    serial:0607623A             a serial alone
    can:0607623A/12             CAN id 12 on the fdcanusb whose serial is 0607623A

Paths come from io_diagnostics.usb_reset (IO_DIAGNOSTICS_USB_SYSFS / _DEV_ROOT / _PROC), so a fake tree tests all of it.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from io_diagnostics import usb_reset
from io_diagnostics.usb_reset import PORT, dev_root, sysfs_root

KEY_USB_IDS = re.compile(r"^usb:([0-9a-fA-F]{4}):([0-9a-fA-F]{4})(?::(.+))?$")
KEY_PORT = re.compile(r"^usb:(\d+-\d+(?:\.\d+)*)$")
KEY_CAN = re.compile(r"^can:([^/]+)/(\d+)$")


class DeviceError(ValueError):
    """The key names no device RobotDx can tell apart (two identical devices with no serial, a malformed key)."""


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


@dataclass(frozen=True)
class Device:
    key: str
    port: str  # the sysfs port name now ("7-1"), or the last one known when it is gone; a CAN board: its adapter's
    vid: str = ""
    pid: str = ""
    serial: str = ""
    product: str = ""
    manufacturer: str = ""
    present: bool = False
    node: int | None = None  # a CAN board's id

    @property
    def is_can(self) -> bool:
        return self.node is not None

    @property
    def name(self) -> str:
        if self.is_can:
            return f"CAN {self.node} on {self.product or 'fdcanusb'} {self.serial}".strip()
        label = self.product or f"USB device {self.vid}:{self.pid}"
        return f"{label} {self.serial}".strip() if self.serial else label

    @property
    def state_key(self) -> str:
        """One stable file name per device: its serial when it has one, else its port."""

        if self.is_can:
            return f"can-{self.serial}-{self.node}"
        if self.serial:
            return f"usb-{self.vid}-{self.pid}-{self.serial}"
        return f"usb-port-{self.port}"

    def keys(self) -> list[str]:
        """What profiles match on (profile.matches): most specific first."""

        if self.is_can:
            return [f"can:{self.serial}/{self.node}"]
        keys = []
        if self.vid and self.pid:
            if self.serial:
                keys.append(f"usb:{self.vid}:{self.pid}:{self.serial}")
            keys.append(f"usb:{self.vid}:{self.pid}")
        if self.product:
            keys.append(f"product:{self.product}")
        if self.port:
            keys.append(f"usb:{self.port}")
        return keys

    def target(self) -> usb_reset.Target:
        """The usb_reset view of it (its node, bus/device number), for the USB rungs and the holder check."""

        return usb_reset.target_of({"id": f"usb:{self.port}", "label": self.name, "attrs": {"port": self.port}})


def _ports() -> list[str]:
    try:
        return sorted(entry.name for entry in sysfs_root().iterdir() if PORT.match(entry.name))
    except OSError:
        return []


def _device_at(port: str, key: str) -> Device:
    directory = sysfs_root() / port
    return Device(key=key, port=port, vid=_read(directory / "idVendor").lower(), pid=_read(directory / "idProduct").lower(),
                  serial=_read(directory / "serial"), product=_read(directory / "product"),
                  manufacturer=_read(directory / "manufacturer"), present=directory.is_dir())


def resolve(key: str, last_port: str = "") -> Device:
    """The device as sysfs shows it now. Not there: a Device with ``present=False`` at ``last_port`` (the incident's memory of
    where it was), so a device that vanished after a reset can still be power-cycled at the port it left."""

    key = key.strip()
    found = KEY_PORT.match(key)
    if found:
        device = _device_at(found[1], key)
        return device
    found = KEY_CAN.match(key)
    if found:
        adapter = resolve(f"serial:{found[1]}", last_port)
        return Device(key=key, port=adapter.port, vid=adapter.vid, pid=adapter.pid, serial=found[1], product=adapter.product,
                      manufacturer=adapter.manufacturer, present=adapter.present, node=int(found[2]))
    found = KEY_USB_IDS.match(key)
    if found:
        vid, pid, serial = found[1].lower(), found[2].lower(), found[3] or ""
    elif key.startswith("serial:") and key.removeprefix("serial:"):
        vid, pid, serial = "", "", key.removeprefix("serial:")
    else:
        raise DeviceError(f"{key!r} is not a device key (usb:VID:PID[:SERIAL], usb:<port>, serial:<serial>, can:<serial>/<id>)")
    matches = []
    for port in _ports():
        here = _device_at(port, key)
        if (not vid or here.vid == vid) and (not pid or here.pid == pid) and (not serial or here.serial == serial):
            matches.append(here)
    if len(matches) > 1:
        raise DeviceError(f"{key!r} matches {len(matches)} devices ({', '.join(m.port for m in matches)}): name one by its serial "
                          "or its port")
    if matches:
        return matches[0]
    return Device(key=key, port=last_port, vid=vid, pid=pid, serial=serial, present=False)


def from_skeleton(device: dict) -> Device:
    """The host's skeleton dict (IO_DIAGNOSTICS_DEVICE) to a Device, keyed by serial when it has one."""

    attrs = device.get("attrs") or {}
    port = usb_reset.port_of(device)
    if not port:
        raise DeviceError(f"{device.get('id')!r} is not a USB device with a port")
    here = _device_at(port, f"usb:{port}")
    serial = here.serial or str(attrs.get("serial") or device.get("serial") or "")
    vid, pid = here.vid or str(attrs.get("vid") or "").lower(), here.pid or str(attrs.get("pid") or "").lower()
    key = f"usb:{vid}:{pid}:{serial}" if serial and vid and pid else f"usb:{port}"
    return Device(key=key, port=port, vid=vid, pid=pid, serial=serial, product=here.product or str(device.get("product") or ""),
                  manufacturer=here.manufacturer, present=here.present)


# ----- what it looks like (before and after each rung) ------------------------------------------------------------------
def interfaces(port: str) -> list[dict]:
    """Its interfaces, the driver bound to each, and the /dev nodes each exposes (tty/ttyACM0, video4linux/video0)."""

    directory, out = sysfs_root() / port, []
    try:
        names = sorted(entry.name for entry in directory.iterdir() if entry.name.startswith(port + ":"))
    except OSError:
        return out
    for name in names:
        interface = directory / name
        try:
            driver = os.path.basename(os.readlink(interface / "driver"))
        except OSError:
            driver = ""
        nodes = {}
        for kind in usb_reset.NODE_DIRS:
            try:
                nodes[kind] = sorted(entry.name for entry in (interface / kind).iterdir())
            except OSError:
                continue
        out.append({"name": name, "driver": driver, "nodes": nodes})
    return out


def nodes_of(port: str, kind: str) -> list[str]:
    """The /dev paths of one node kind (``tty`` -> /dev/ttyACM0, ``video4linux`` -> /dev/video0), lowest first."""

    names = sorted({name for interface in interfaces(port) for name in interface["nodes"].get(kind, [])},
                   key=lambda name: (len(name), name))
    return [str(dev_root() / name) for name in names]


def by_id_links(serial: str) -> list[str]:
    if not serial:
        return []
    out = []
    for folder in (dev_root() / "serial" / "by-id", dev_root() / "v4l" / "by-id"):
        try:
            out += sorted(str(folder / entry.name) for entry in folder.iterdir() if serial in entry.name)
        except OSError:
            continue
    return out


def port_dir(port: str) -> Path | None:
    """The hub's sysfs directory for the port a device is plugged into (holds ``disable``, ``state``, ``peer``), found from the
    hub side so it is there even when the device is gone: ``7-1`` -> ``7-0:1.0/usb7-port1``, ``1-5.1`` -> ``1-5:1.0/1-5-port1``."""

    if not PORT.match(port or ""):
        return None
    hub, number = usb_reset.hub_and_port(port)
    if "-" in hub:
        interface, name = f"{hub}:1.0", f"{hub}-port{number}"
    else:
        interface, name = f"{hub}-0:1.0", f"usb{hub}-port{number}"
    return sysfs_root() / interface / name


def hub_name(port: str) -> str:
    """The hub's sysfs name: ``usb7`` for a root-hub port ``7-1``, ``1-5.1`` for ``1-5.1.4``."""

    hub, _number = usb_reset.hub_and_port(port)
    return hub if "-" in hub else f"usb{hub}"


def snapshot(device: Device, holders: list | None = None) -> dict:
    """Everything a person would look at to judge a rung: is it on USB, which device number, which drivers are bound, which
    nodes and by-id links exist, the port's own state, and who had it open."""

    port = device.port
    directory = sysfs_root() / port if port else None
    present = bool(directory and directory.is_dir())
    here = port_dir(port)
    out = {"at": time.time(), "port": port, "present": present}
    if present and directory is not None:
        out.update(serial=_read(directory / "serial"), busnum=_read(directory / "busnum"), devnum=_read(directory / "devnum"),
                   speed=_read(directory / "speed"), authorized=_read(directory / "authorized"),
                   configuration=_read(directory / "bConfigurationValue"), interfaces=interfaces(port))
    out["by_id"] = by_id_links(device.serial)
    if here is not None and here.is_dir():
        out["port_state"] = {"disable": _read(here / "disable"), "state": _read(here / "state")}
    if holders is not None:
        out["holders"] = [{"pid": holder.pid, "program": holder.program, "node": holder.node} for holder in holders]
    return out


def present_now(device: Device, expects_nodes: tuple[str, ...] = ()) -> tuple[bool, str]:
    """``present`` probe: on USB with its serial, configured, at least one interface bound to a driver, and its /dev nodes
    there. Plain words for each way it is not."""

    port = device.port
    if not port:
        return False, "RobotDx does not know which port it was on"
    directory = sysfs_root() / port
    if not directory.is_dir():
        return False, f"nothing is plugged in at USB port {port}"
    serial = _read(directory / "serial")
    if device.serial and serial != device.serial:
        return False, f"USB port {port} now holds {serial or 'a device with no serial'}, not {device.serial}"
    if _read(directory / "authorized") == "0":
        return False, f"{device.name} is deauthorized (authorized = 0)"
    if not _read(directory / "bConfigurationValue"):
        return False, f"{device.name} is on USB but not configured"
    found = interfaces(port)
    if not any(interface["driver"] for interface in found):
        return False, f"{device.name} is on USB but no driver is bound to it"
    for kind in expects_nodes:
        if not any(interface["nodes"].get(kind) for interface in found):
            return False, f"{device.name} is on USB but has no {kind} node yet"
    nodes = [name for interface in found for names in interface["nodes"].values() for name in names]
    return True, f"{device.name} is on USB at {port}" + (f" ({', '.join(nodes)})" if nodes else "")
