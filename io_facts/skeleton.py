"""The OS skeleton: one host -> bus -> device list, read passively, plus the sysfs helpers every source shares.

Sources (each ``devices(root)`` returns the frozen dict shape of docs/build-plan-v1.md, "OS skeleton sources"):

- USB      ``usb_devices`` below, over the existing ``io_facts.usb`` reader (+ root hubs: they are the buses)
- PCI      ``io_facts.pci``     /sys/bus/pci/devices: class, ids, driver, parent bridge, link, frame grabbers
- network  ``io_facts.netdev``  /sys/class/net: physical NICs, EtherCAT masters detected passively (never scanned)
- serial   ``io_facts.tty``     /sys/class/tty: ttys with a real device
- video    ``io_facts.v4l``     /sys/class/video4linux: video nodes on the device that really exposes them

``skeleton(root)`` merges them into one list, parents before children::

    host                       bus "host": the one node outside the five source buses
    ├── usb:usb6               USB bus 6 = root hub; attrs.controller = "pci:0000:0f:00.4" (the cross-bus edge)
    │   └── usb:6-1            Intel RealSense D405 on port 6-1
    │       └── v4l:6-1:1.0#0  /dev/video0   function nodes (tty, video, NIC) hang off the device exposing them
    ├── pci:root-0000:00       PCI root bus; bridges folded away by default, kept as attrs.upstream
    │   ├── pci:0000:06:00.0   Ethernet controller
    │   │   └── net:enp6s0     EtherCAT segment (not scanned) when a master is bound to it
    │   └── pci:0000:0d:00.0   USB controller; attrs.usb_buses = ["usb:usb1", "usb:usb2"]
    └── tty:on-board           ttys / NICs / video with no PCI or USB parent (motherboard COM ports, SoC UARTs)

Ids are stable for a given plug state: ``<bus>:<kernel topology name>`` (USB port path, PCI BDF, NIC name, the
USB interface plus node index for ttys and video nodes, the way udev's by-path links are built). ``path`` is the
tree segment; ``attrs.hardware_id`` is the repeatable "what" (record v0 §4).

Every read is a file read under ``root`` (default ``/``): sysfs, /dev symlinks, the udev database in
/run/udev/data, /proc and the hwdata id files. Nothing opens a device node, a socket or a bus. Tests and demos
pass a fake root built by ``fake_root`` from tests/data/sysfs-rack-fixture.json.

    /usr/bin/python3 /home/bam/io-diagnostics/io_facts/skeleton.py
    /usr/bin/python3 /home/bam/io-diagnostics/io_facts/skeleton.py --fixture /home/bam/io-diagnostics/tests/data/sysfs-rack-fixture.json
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__" and not __package__:  # run as a file: hand over to the package module (stdlib only)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from io_facts.skeleton import main as _main

    raise SystemExit(_main())

from . import usb  # noqa: E402

ROOT = Path("/")
BUSES = ("usb", "pci", "net", "tty", "v4l")
NODE_KEYS = ("bus", "id", "path", "label", "vendor", "product", "serial", "parent", "attrs")

# WHY a 4+ hex domain: Intel VMD puts NVMe drives in a synthetic domain like 10000:e0.
BDF = re.compile(r"^[0-9a-f]{4,}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
PCI_ROOT_BUS = re.compile(r"^pci([0-9a-f]{4,}:[0-9a-f]{2})$")
USB_DEVICE = re.compile(r"^(\d+-\d+(\.\d+)*|usb\d+)$")  # a device or root hub, never an interface (1-3:1.0)
USB_ROOT_HUB = re.compile(r"^usb(\d+)$")
USB_INTERFACE = re.compile(r"^\d+-[\d.]+:\d+\.\d+$")
IDS_DIRS = ("usr/share/misc", "usr/share/hwdata", "usr/share")
ON_BOARD = {"net": "On-board network", "tty": "On-board serial ports", "v4l": "On-board video"}


# ----- the node shape (frozen: docs/build-plan-v1.md) ------------------------------------------------
def node(bus: str, node_id: str, path: str, label: str, *, vendor: str | None = None, product: str | None = None,
         serial: str | None = None, parent: str | None = None, attrs: Mapping | None = None) -> dict:
    """One skeleton entry: ``{bus, id, path, label, vendor, product, serial, parent, attrs}``; empty strings become None."""

    return {"bus": bus, "id": node_id, "path": path, "label": label, "vendor": vendor or None, "product": product or None,
            "serial": serial or None, "parent": parent, "attrs": dict(attrs or {})}


def natural_key(text: str) -> list:
    """``usb10`` after ``usb9``, ``video12`` after ``video2``."""

    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", text) if part]


# ----- passive sysfs reads shared by every source ----------------------------------------------------
def read_text(path: Path) -> str | None:
    """A sysfs attribute without its trailing newline; None when absent or unreadable.

    WHY only the newline: descriptor strings are rendered as authored (the D405's manufacturer ends in a space).
    WHY None on any OSError: sysfs answers EINVAL for ``speed`` on a down link and EACCES for some attributes;
    "could not be read" is the fact, never a crash.
    """

    try:
        return path.read_text(errors="replace").removesuffix("\n")
    except (OSError, ValueError):
        return None


def read_int(path: Path, base: int = 10) -> int | None:
    text = read_text(path)
    try:
        return int(text.strip(), base) if text and text.strip() else None
    except ValueError:
        return None


def read_number(path: Path) -> float | int | None:
    """``1.5`` / ``480`` / ``10000``: USB speeds are not always integers (low-speed devices read 1.5)."""

    text = (read_text(path) or "").strip()
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def link_name(path: Path) -> str | None:
    """The last component of a symlink's target (``driver`` -> ``r8169``), without following it."""

    try:
        return Path(os.readlink(path)).name
    except OSError:
        return None


def resolved(path: Path) -> Path | None:
    return Path(os.path.realpath(path)) if os.path.lexists(path) else None


def as_dev_path(target: Path | str, root: Path) -> str:
    """``<root>/dev/ttyACM0`` -> ``/dev/ttyACM0`` (what a user types), whatever the fake root is."""

    return "/" + os.path.relpath(target, root)


@dataclass(frozen=True)
class Owner:
    """The physical device a class device (tty, video node, NIC) belongs to."""

    bus: str  # "usb" or "pci"
    name: str  # "1-3" / "0000:06:00.0"
    interface: str | None = None  # the USB interface right below the device ("1-3:1.0")

    @property
    def id(self) -> str:
        return f"{self.bus}:{self.name}"


def physical_owner(device_dir: Path | None) -> Owner | None:
    """The NEAREST USB device or PCI function at or above ``device_dir``; None for platform/pnp/virtual devices.

    WHY the nearest one: V6 matched by path prefix, so a hub owned every video node below it. The nearest USB
    device above an interface is the device that exposes it, never a hub further up.
    """

    if device_dir is None:
        return None
    below: Path | None = None
    for current in (device_dir, *device_dir.parents):
        name = current.name
        if USB_DEVICE.match(name) and (current / "idVendor").is_file():
            interface = below.name if below is not None and USB_INTERFACE.match(below.name) else None
            return Owner("usb", name, interface)
        if BDF.match(name) and (current / "vendor").is_file():
            return Owner("pci", name)
        if name in ("devices", "") or current == current.parent:
            return None
        below = current
    return None


def uevent(path: Path) -> dict[str, str]:
    """A sysfs ``uevent`` file as a dict (DEVNAME, MAJOR, MINOR, DRIVER ...)."""

    pairs = (line.partition("=") for line in (read_text(path / "uevent") or "").splitlines())
    return {key: value for key, _, value in pairs if key}


@dataclass(frozen=True)
class UdevRecord:
    links: tuple[str, ...] = ()  # /dev/serial/by-id/..., /dev/v4l/by-path/..., custom rule links (/dev/fdcanusb)
    properties: Mapping[str, str] = field(default_factory=dict)  # ID_V4L_CAPABILITIES, ID_SERIAL_SHORT ...


def udev_record(root: Path, major_minor: str | None, kind: str = "c") -> UdevRecord:
    """The udev database entry for a char device (``/run/udev/data/c166:0``): what ``udevadm info`` prints.

    WHY the database file and not ``udevadm``: it is a plain read under ``root`` (fixtures work) and gives the
    capabilities udev learned when it probed the node, so nothing here has to open the node to ask again.
    """

    if not major_minor:
        return UdevRecord()
    links, properties = [], {}
    for line in (read_text(root / "run/udev/data" / f"{kind}{major_minor}") or "").splitlines():
        key, _, value = line.partition(":")
        if key == "S":
            links.append(f"/dev/{value}")
        elif key == "E":
            name, _, setting = value.partition("=")
            properties[name] = setting
    return UdevRecord(tuple(links), properties)


def dev_links(root: Path, *directories: str) -> dict[str, list[str]]:
    """``{"/dev/ttyACM0": ["/dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00"]}`` for symlinks in /dev/<dir>.

    The fallback when the udev database is missing (containers); never follows a link into a device.
    """

    found: dict[str, list[str]] = {}
    for directory in directories:
        base = root / "dev" / directory
        if not base.is_dir():
            continue
        for link in sorted(base.iterdir()):
            if link.is_symlink():
                found.setdefault(as_dev_path(os.path.realpath(link), root), []).append(as_dev_path(link, root))
    return found


def split_links(links: Iterable[str]) -> dict[str, list[str]]:
    unique = sorted(set(links))
    return {"by_id": [link for link in unique if "/by-id/" in link],
            "by_path": [link for link in unique if "/by-path/" in link],
            "other_links": [link for link in unique if "/by-id/" not in link and "/by-path/" not in link]}


# ----- hwdata id databases (the files lspci / lsusb read) --------------------------------------------
@dataclass
class IdsDatabase:
    vendors: dict[str, str] = field(default_factory=dict)
    devices: dict[tuple[str, str], str] = field(default_factory=dict)
    subsystems: dict[tuple[str, str, str, str], str] = field(default_factory=dict)
    classes: dict[tuple[str, ...], str] = field(default_factory=dict)  # ("02",) ("02", "00") ("0c", "03", "30")


def parse_ids(text: str) -> IdsDatabase:
    """Parse pci.ids / usb.ids: vendors, devices, subsystems and the ``C`` class tables."""

    database = IdsDatabase()
    section = vendor = device = base_class = sub_class = None
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if not line.startswith("\t"):
            if line.startswith("C "):
                section, base_class = "class", line[2:4].lower()
                database.classes[(base_class,)] = line[4:].strip()
            elif re.match(r"^[0-9a-fA-F]{4}\s", line):
                section, vendor, device = "vendor", line[:4].lower(), None
                database.vendors[vendor] = line[4:].strip()
            else:
                section = None  # usb.ids' later tables (AT, HID, R, BIAS, PHY, HUT, L, HCC, VT)
            continue
        if section == "vendor":
            if line.startswith("\t\t"):
                parts = line.strip().split(None, 2)
                if device and len(parts) == 3:
                    database.subsystems[(vendor, device, parts[0].lower(), parts[1].lower())] = parts[2]
            else:
                device = line[1:5].lower()
                database.devices[(vendor, device)] = line[5:].strip()
        elif section == "class":
            if line.startswith("\t\t"):
                code, _, name = line.strip().partition(" ")
                database.classes[(base_class, sub_class, code.lower())] = name.strip()
            else:
                sub_class = line[1:3].lower()
                database.classes[(base_class, sub_class)] = line[3:].strip()
    return database


@functools.lru_cache(maxsize=8)
def _parse_ids_file(path: str, _mtime: float) -> IdsDatabase:
    return parse_ids(Path(path).read_text(encoding="utf-8", errors="replace"))


def ids_database(root: Path, filename: str) -> IdsDatabase:
    """``pci.ids`` / ``usb.ids`` from the usual hwdata places under ``root``; empty when none is installed."""

    for directory in IDS_DIRS:
        path = root / directory / filename
        if path.is_file():
            return _parse_ids_file(str(path), path.stat().st_mtime)
    return IdsDatabase()


# ----- USB: the existing io_facts.usb reader as skeleton dicts -------------------------------------
USB_INTERFACE_KINDS = {"0e": "camera", "03": "hid", "08": "storage", "01": "audio", "e0": "wireless"}


def _format_speed(mbps: float | int | None) -> str:
    if not mbps:
        return "speed unknown"
    return f"{mbps / 1000:g} Gb/s" if mbps >= 1000 else f"{mbps:g} Mb/s"


def _usb_bus(entry: Path, names: IdsDatabase, device_count: int) -> dict:
    number = int(USB_ROOT_HUB.match(entry.name)[1])
    controller = physical_owner(resolved(entry).parent)
    speed = read_number(entry / "speed")
    attrs = {
        "kind": "bus",
        "busnum": number,
        "speed_mbps": speed,
        "usb_version": (read_text(entry / "version") or "").strip() or None,
        "ports": read_int(entry / "maxchild"),
        "controller": controller.id if controller else None,  # the PCI function (xHCI) this bus lives on
        "device_count": device_count,
        "sysfs": str(resolved(entry)),
        "source": "io_facts.usb (sysfs)",
    }
    return node("usb", f"usb:{entry.name}", entry.name, f"USB bus {number} · {_format_speed(speed)}",
                vendor=read_text(entry / "manufacturer"), product=read_text(entry / "product"), parent=None, attrs=attrs)


def _port_facts(device_dir: Path) -> dict | None:
    """The hub port a device sits in: hotplug/hardwired, over-current count, USB2<->USB3 peer, ACPI location."""

    port = resolved(device_dir / "port")
    if port is None or not port.is_dir():
        return None
    location = {key: read_text(port / "physical_location" / key) for key in ("panel", "vertical_position", "horizontal_position")}
    return {
        "name": port.name,  # "usb6-port1"
        "connect_type": read_text(port / "connect_type"),
        "over_current_count": read_int(port / "over_current_count"),
        "peer": link_name(port / "peer"),  # the other half of a USB3 connector ("usb5-port1")
        # WHY labelled firmware: ACPI _PLD panel/position is what the BIOS claims, often generic on desktop boards.
        "location_firmware": location if any(location.values()) else None,
    }


def _usb_device(device_dir: Path, device: usb.UsbDevice, names: IdsDatabase) -> dict:
    port = device.port
    parent = f"usb:{port.rsplit('.', 1)[0]}" if "." in port else f"usb:usb{port.split('-', 1)[0]}"
    interfaces = sorted((child for child in device_dir.iterdir() if child.name.startswith(f"{port}:")), key=lambda p: natural_key(p.name))
    interface_classes = sorted({(read_text(child / "bInterfaceClass") or "").strip().lower() for child in interfaces} - {""})
    device_class = (read_text(device_dir / "bDeviceClass") or "").strip().lower()
    is_hub = device_class == "09"
    kind = "hub" if is_hub else next((USB_INTERFACE_KINDS[code] for code in ("0e", "08", "01", "e0") if code in interface_classes),
                                     "hid" if interface_classes == ["03"] else "device")
    vid, pid = device.vid.lower(), device.pid.lower()
    vendor = device.manufacturer or names.vendors.get(vid)
    product = device.product or names.devices.get((vid, pid))
    attrs = {
        "kind": kind,
        "port": port,
        "busnum": int(port.split("-", 1)[0]),
        "devnum": device.devnum,
        "vid": vid,
        "pid": pid,
        "usb_id": f"{vid}:{pid}",
        "speed_mbps": read_number(device_dir / "speed"),
        "usb_version": (read_text(device_dir / "version") or "").strip() or None,
        "bcd_device": (read_text(device_dir / "bcdDevice") or "").strip() or None,
        "device_class": device_class or None,
        "interface_classes": interface_classes,
        "is_hub": is_hub,
        "ports": read_int(device_dir / "maxchild") if is_hub else None,
        "removable": read_text(device_dir / "removable"),
        "plugged_at": device.plugged_at,  # sysfs node creation time = the kernel's enumeration (io_facts.usb)
        "vendor_source": "descriptor" if device.manufacturer else ("usb.ids" if vendor else None),
        "product_source": "descriptor" if device.product else ("usb.ids" if product else None),
        "hub_port": _port_facts(device_dir),
        # record v0 §4: usb:<vid>:<pid>:<descriptor serial>; without a serial only the port tells two apart
        "hardware_id": f"usb:{vid}:{pid}:{device.serial}" if device.serial else f"usb:{vid}:{pid}@{port}",
        "sysfs": str(resolved(device_dir)),
        "source": "io_facts.usb (sysfs + usb.ids)",
    }
    return node("usb", f"usb:{port}", f"usb-{port}", product or f"USB device {vid}:{pid}", vendor=vendor,
                product=product, serial=device.serial, parent=parent, attrs=attrs)


def usb_devices(root: Path = ROOT) -> list[dict]:
    """USB buses (root hubs) and devices as skeleton dicts; descriptors come from ``io_facts.usb``.

    ``io_facts.usb``'s own ``ttys`` / ``by_id`` / ``video_nodes`` are not used: the tty and v4l sources own
    those nodes (and V6's ``video_nodes`` gave a hub every camera below it).
    """

    sys_usb = root / "sys/bus/usb/devices"
    if not sys_usb.is_dir():
        return []
    names = ids_database(root, "usb.ids")
    devices = usb.list_usb_devices(sys_usb, nodes=False)  # WHY: the nodes it would look up are not used here (see the docstring)
    per_bus: dict[int, int] = {}
    for device in devices:
        bus_number = int(device.port.split("-", 1)[0])
        per_bus[bus_number] = per_bus.get(bus_number, 0) + 1
    hubs = sorted((entry for entry in sys_usb.iterdir() if USB_ROOT_HUB.match(entry.name) and (entry / "idVendor").is_file()),
                  key=lambda entry: natural_key(entry.name))
    nodes = [_usb_bus(entry, names, per_bus.get(int(USB_ROOT_HUB.match(entry.name)[1]), 0)) for entry in hubs]
    nodes += [_usb_device(sys_usb / device.port, device, names) for device in devices]
    return nodes


# ----- the merged tree -----------------------------------------------------------------------------
def host_node(root: Path = ROOT) -> dict:
    hostname = (read_text(root / "proc/sys/kernel/hostname") or "").strip() or "host"
    os_release = dict(line.partition("=")[::2] for line in (read_text(root / "etc/os-release") or "").splitlines() if "=" in line)
    attrs = {
        "kind": "host",
        "source": "io_facts.skeleton (/proc/sys/kernel, /etc/os-release)",
        "kernel": (read_text(root / "proc/sys/kernel/osrelease") or "").strip() or None,
        "os": os_release.get("PRETTY_NAME", "").strip('"') or None,
        "boot_id": (read_text(root / "proc/sys/kernel/random/boot_id") or "").strip() or None,
    }
    return node("host", "host", "host", hostname, attrs=attrs)


def _collect(bus: str, read: Callable[[Path], list[dict]], root: Path, notes: list[str]) -> list[dict]:
    """One source's nodes; a source that raises is skipped with a note (one broken reader never blanks the tree)."""

    try:
        return [dict(item) for item in read(root)]
    except Exception as error:  # noqa: BLE001 - any failure of a passive reader is a note, not a crash
        notes.append(f"{bus}: skipped ({type(error).__name__}: {error})")
        return []


def skeleton(root: Path = ROOT, *, keep_bridges: bool = False) -> list[dict]:
    """Every source merged into one host -> bus -> device list, parents before children, deterministic order.

    ``keep_bridges=False`` (default) folds PCI bridges away: endpoints hang off their root bus and keep the
    bridge chain as ``attrs.upstream`` (a capture card behind the chipset shares its uplink with USB and SATA).
    """

    # WHY imported here: those modules import this one's sysfs helpers.
    from . import netdev, pci, tty, v4l

    root = Path(root)
    notes: list[str] = []
    host = host_node(root)
    usb_nodes = _collect("usb", usb_devices, root, notes)
    pci_nodes = _collect("pci", pci.devices, root, notes)
    detected = _collect("ethercat", netdev.detect_masters, root, notes)  # one /proc scan for the NICs and the host
    net_nodes = _collect("net", lambda at: netdev.devices(at, detected=detected), root, notes)
    tty_nodes = _collect("tty", tty.devices, root, notes)
    v4l_nodes = _collect("v4l", v4l.devices, root, notes)

    nodes: dict[str, dict] = {host["id"]: host}

    def add(item: dict) -> None:
        if item["id"] in nodes:
            notes.append(f"{item['bus']}: duplicate id {item['id']} dropped")
            return
        nodes[item["id"]] = item

    for item in usb_nodes:
        add({**item, "parent": item["parent"] or host["id"]})

    for item in pci_nodes:
        if not keep_bridges and item["attrs"].get("kind") == "bridge":
            continue
        root_bus = item["attrs"].get("root_bus") or "unknown"
        root_id = f"pci:root-{root_bus}"
        if root_id not in nodes:
            nodes[root_id] = node("pci", root_id, f"pci-{root_bus}", f"PCI root bus {root_bus}", parent=host["id"],
                                  attrs={"kind": "bus", "root_bus": root_bus, "source": "io_facts.skeleton"})
        parent = item["parent"] if keep_bridges and item["parent"] else root_id
        add({**item, "parent": parent})

    for item in net_nodes + tty_nodes + v4l_nodes:
        parent = item["parent"]
        if parent not in nodes:
            group_id = f"{item['bus']}:on-board"
            if group_id not in nodes:
                nodes[group_id] = node(item["bus"], group_id, f"on-board-{item['bus']}", ON_BOARD[item["bus"]],
                                       parent=host["id"], attrs={"kind": "group", "source": "io_facts.skeleton"})
            if parent is not None:  # its USB/PCI owner was not read (source skipped): keep the edge as a fact
                item = {**item, "attrs": {**item["attrs"], "owner_not_in_tree": parent}}
            parent = group_id
        add({**item, "parent": parent})

    # incl. masters bound to no NIC: "EtherCAT master running (pid 4242), NIC unknown" is still worth a row
    host["attrs"]["ethercat_masters"] = _collect("ethercat", lambda at: netdev.ethercat_masters(at, detected=detected), root, notes)
    host["attrs"]["notes"] = notes
    return _depth_first(nodes, host["id"], notes)


BUS_RANK = {"host": 0, **{bus: rank + 1 for rank, bus in enumerate(BUSES)}}


def _sibling_order(item: dict) -> tuple:
    # WHY plain text for PCI: BDFs are fixed-width hex, where "natural" digit runs would put 0d before 01.
    name = item["id"] if item["bus"] == "pci" else natural_key(item["id"])
    return BUS_RANK.get(item["bus"], 9), item["attrs"].get("kind") == "group", name


def _depth_first(nodes: dict[str, dict], root_id: str, notes: list[str]) -> list[dict]:
    children: dict[str, list[dict]] = {}
    for item in nodes.values():
        if item["id"] == root_id:
            continue
        if item["parent"] not in nodes:
            notes.append(f"{item['id']}: parent {item['parent']} missing; attached to the host")
            item["parent"] = root_id
        children.setdefault(item["parent"], []).append(item)
    for siblings in children.values():
        siblings.sort(key=_sibling_order)
    ordered, stack, seen = [], [nodes[root_id]], set()
    while stack:
        item = stack.pop()
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        ordered.append(item)
        stack.extend(reversed(children.get(item["id"], [])))
    for item in nodes.values():  # a parent cycle cannot happen with kernel topology; keep every node anyway
        if item["id"] not in seen:
            notes.append(f"{item['id']}: unreachable from the host; attached to it")
            ordered.append({**item, "parent": root_id})
    return ordered


# ----- fake machines for tests and demos ----------------------------------------------------------
CLASS_DIRS = {"net", "tty", "video4linux"}


def _bus_of(name: str) -> str:
    if BDF.match(name):
        return "pci"
    if USB_DEVICE.match(name) or USB_INTERFACE.match(name) or re.match(r"^\d+-0:\d+\.\d+$", name):
        return "usb"
    return "platform"


def _relative_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(link):
        link.unlink()
    os.symlink(os.path.relpath(target, link.parent), link)


def _write_attrs(directory: Path, attrs: Mapping, root: Path) -> None:
    for key, value in attrs.items():
        if key == "driver":
            bus, _, name = value.rpartition("/")
            target = root / "sys/bus" / (bus or _bus_of(directory.name)) / "drivers" / name
            target.mkdir(parents=True, exist_ok=True)
            _relative_symlink(directory / "driver", target)
        elif key == "_links":  # literal symlinks inside this directory, targets relative to it
            for name, target in value.items():
                os.symlink(target, directory / name)
        elif isinstance(value, Mapping):
            (directory / key).mkdir(parents=True, exist_ok=True)
            _write_attrs(directory / key, value, root)
        else:
            (directory / key).write_text(f"{value}\n")


def fake_root(spec: Mapping | Path | str, root: Path | str) -> Path:
    """Build a fake machine under ``root`` from a compact JSON spec. Tests and demos only.

    ``spec = {"devices": {<path under sys/devices>: {attribute: value | {subdir...}, "driver": name,
    "_links": {name: target}}}, "files": {<path>: text}, "symlinks": {<path>: target as written}}``.
    Derived the way the kernel does it: /sys/bus/{pci,usb}/devices links, /sys/class/{net,tty,video4linux}
    links and each class device's ``device`` link (never for /sys/devices/virtual).
    """

    if not isinstance(spec, Mapping):
        spec = json.loads(Path(spec).read_text())
    root = Path(root)
    for relative, attrs in spec.get("devices", {}).items():
        directory = root / "sys/devices" / relative
        directory.mkdir(parents=True, exist_ok=True)
        _write_attrs(directory, attrs, root)
        name, parent = directory.name, directory.parent
        if parent.name in CLASS_DIRS:
            _relative_symlink(root / "sys/class" / parent.name / name, directory)
            if not relative.startswith("virtual/"):
                _relative_symlink(directory / "device", parent.parent)
        elif BDF.match(name):
            _relative_symlink(root / "sys/bus/pci/devices" / name, directory)
        elif _bus_of(name) == "usb":
            _relative_symlink(root / "sys/bus/usb/devices" / name, directory)
    for relative, text in spec.get("files", {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for relative, target in spec.get("symlinks", {}).items():
        link = root / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, link)
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print this machine's OS skeleton (passive reads only) as JSON.")
    parser.add_argument("--root", type=Path, default=ROOT, help="read under this root instead of /")
    parser.add_argument("--fixture", type=Path, help="build this fake-machine spec in a temp dir and read that")
    parser.add_argument("--keep-bridges", action="store_true", help="keep PCI bridges as tree nodes")
    args = parser.parse_args(argv)
    if args.fixture:
        with tempfile.TemporaryDirectory(prefix="io-skeleton-") as scratch:
            print(json.dumps(skeleton(fake_root(args.fixture, scratch), keep_bridges=args.keep_bridges), indent=1))
        return 0
    print(json.dumps(skeleton(args.root, keep_bridges=args.keep_bridges), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
