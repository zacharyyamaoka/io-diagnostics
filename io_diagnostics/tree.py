"""The one tree: the OS skeleton (``io_facts``) + plugin children, two parent edges, the host rules from ``contract.record``.

Two sources build it (docs/converged-design-2026-10-22.md §4):

- **OS skeleton** (passive, always there): lane B's ``io_facts.skeleton.skeleton()`` (host -> bus -> device ->
  function node: tty, video node, NIC), honoured edge for edge. The host adds ONE thing to it: a ``Port <n>`` row
  between a USB device and its hub or root bus, because flapping, link speed and "empty since 18:20" belong to the
  port, not to whatever is plugged in now. ``io_facts.skeleton`` is imported defensively; missing, USB comes from
  ``io_facts.usb`` (same dict shape) and every other source is tried on its own. A missing source is skipped with a
  note, never a crash.
- **Plugin children** hang under their device: fdcanusb -> CAN bus (``via``) -> node (``via``) -> driver / motor
  (``part_of``); D405 -> streams (``part_of``).

This module is the PHYSICAL view's composition on the host side (computer -> bus -> port -> device -> function node, plus
the plugins' children): ``build_os_layer`` + ``merge`` are the only places that know about computers and ports, and the
API's ``parent`` / ``edge`` carry the result. The page draws it through a structure (``web/view/structure/p1.js``,
``view: 'physical'``); a System view (areas -> functions -> components) or a Software view (computer -> processes) is
another composition over the same entities and records, not a change to them.

The rules are ``contract.record``'s, never re-implemented here (this replaces V6's worst-child-headline rule):

- ``summarize``: an entity with records of its own is a black box and its row shows its OWN level (Zach,
  2026-10-22: "the spider can still report warning"); the worst-below rollup is an advisory hint; a pure grouping
  node shows its rollup; knock-on problems under a failed ancestor collapse to one cause.
- ``effective_level``: STALE is derived on the host's receive clock (``received``), never authored.
"""

from __future__ import annotations

import dataclasses
import importlib
import re
from dataclasses import dataclass, field
from typing import Callable

from contract.record import (ERROR, OK, RANK, STALE, STALE_FACTOR, STALE_FLOOR_S, WARN, DiagnosticStatus, Entity,
                             KeyValue, Measurement, Summary, effective_level, summarize)
from io_diagnostics.not_run import is_not_run

HOST_PATH = "host"
RECENT_PATH = f"{HOST_PATH}/recently-seen"  # devices only seen, not expected (host.py): drawn collapsed
COLLAPSED = frozenset({RECENT_PATH})  # groups a page starts closed (node "collapsed")
GROUP_NAMES = {"usb": "USB", "pci": "PCI", "net": "Network", "tty": "Serial", "v4l": "Video"}
GROUP_ICONS = {"usb": "usb", "pci": "pci", "net": "net", "tty": "tty", "v4l": "video"}
ICONS = ("host", "group", "usb", "pci", "net", "tty", "video", "port", "hub", "adapter", "bus", "servo", "chip", "motor",
         "camera", "stream", "webcam", "hid", "device")
# Skeleton kinds that are host structure, not hardware a driver owns: never matched to a plugin, no presence check.
STRUCTURE_KINDS = ("host", "bus", "group", "port")
FUNCTION_BUSES = ("tty", "v4l", "net")  # nodes a device exposes (lane B hangs them under it)
PORT_NAME = re.compile(r"^\d+-[\d.]+$")
USB_SPEED_NAMES = {1.5: "USB 1.1 low-speed", 12: "USB full-speed", 480: "USB 2 high-speed", 5000: "USB 3.2 Gen 1",
                   10000: "USB 3.2 Gen 2", 20000: "USB 3.2 Gen 2x2"}
# WHY these codes: a node that does not answer, or a check that could not run, cannot back "the board answers".
NOT_ANSWERING_CODES = ("NoReply", "NoAck", "PortBusy")
FLAPPING_ENUMERATIONS = 3  # per hour, boot enumeration excluded: V6's threshold (28B3A25D flapped 4x on 1-7)


# ----- the OS skeleton: lane B's dicts, or io_facts.usb here ------------------------------------------------------
def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")


def speed_text(mbps: float | int | None, *, usb: bool = True) -> str:
    """A link speed in words; the USB generation's name only for a USB link (``usb=False`` for a network card,
    whose 10 Gb/s is not "USB 3.2 Gen 2": found 2026-10-24)."""

    if not mbps:
        return ""
    rate = f"{mbps / 1000:g} Gb/s" if mbps >= 1000 else f"{mbps:g} Mb/s"
    name = USB_SPEED_NAMES.get(mbps, "") if usb else ""
    return f"{name} · {rate}" if name else rate


def usb_hardware_id(vid: str, pid: str, serial: str, port: str) -> str:
    """docs/diagnostics-record-v0.md rule 4: what sysfs shows, never an SDK-only serial."""

    return f"usb:{vid}:{pid}:{serial}" if serial else f"usb:{vid}:{pid}@{port}"


def kind_of(device: dict) -> str:
    return str((device.get("attrs") or {}).get("kind") or "device")


def usb_device_dict(device, *, device_class: str = "", bcd_device: str = "", source: str = "io_facts.usb") -> dict:
    """An ``io_facts.usb.UsbDevice`` in lane B's shape (``io_facts.skeleton._usb_device``): the fallback when
    ``io_facts.skeleton`` is missing, and what the fixture capture uses for a device that is not plugged in now.
    Unlike lane B it keeps ``ttys`` / ``by_id`` / ``video_nodes`` in attrs, since no tty or v4l source runs then."""

    port, vid, pid = device.port, device.vid.lower(), device.pid.lower()
    is_hub = device_class == "09"
    return {"bus": "usb", "id": f"usb:{port}", "path": f"usb-{port}", "label": device.product or f"USB device {vid}:{pid}",
            "vendor": device.manufacturer or None, "product": device.product or None, "serial": device.serial or None,
            "parent": f"usb:{port.rsplit('.', 1)[0]}" if "." in port else None,
            "attrs": {"kind": "hub" if is_hub else "device", "port": port, "vid": vid, "pid": pid, "usb_id": f"{vid}:{pid}",
                      "speed_mbps": device.speed_mbps, "devnum": device.devnum, "plugged_at": device.plugged_at,
                      "device_class": device_class or None, "bcd_device": bcd_device or None, "is_hub": is_hub,
                      "hardware_id": usb_hardware_id(vid, pid, device.serial, port),
                      "ttys": list(device.ttys), "by_id": list(device.by_id), "video_nodes": list(device.video_nodes),
                      "simulated": bool(device.simulated), "source": source}}


def live_usb_skeleton() -> list[dict]:
    from io_facts import usb

    def attribute(port: str, name: str) -> str:
        try:
            return (usb.SYSFS_USB / port / name).read_text(errors="replace").strip()
        except OSError:
            return ""

    return [usb_device_dict(device, device_class=attribute(device.port, "bDeviceClass"),
                            bcd_device=attribute(device.port, "bcdDevice"),
                            source="io_facts.usb (sysfs + /dev/serial/by-id)")
            for device in usb.list_usb_devices()]


def _import_source(module: str, function: str) -> tuple[Callable | None, str]:
    try:
        loaded = importlib.import_module(module)
    except Exception as error:  # noqa: BLE001 - a missing or broken source is "skipped with a note", never a crash
        return None, f"{type(error).__name__}: {error}"
    found = getattr(loaded, function, None)
    return (found, "") if callable(found) else (None, f"{module} has no {function}()")


def load_skeleton() -> tuple[list[dict], list[dict]]:
    """``(devices, sources)``. Each source: ``{"name", "state": "used" | "skipped", "note"}``."""

    sources: list[dict] = []
    devices: list[dict] = []
    skeleton, why = _import_source("io_facts.skeleton", "skeleton")
    if skeleton is not None:
        try:
            devices = [dict(device) for device in skeleton()]
            notes = next((list(device.get("attrs", {}).get("notes") or []) for device in devices if device.get("bus") == "host"), [])
            sources.append({"name": "io_facts.skeleton", "state": "used",
                            "note": f"{len(devices)} entries" + (f"; {'; '.join(notes)}" if notes else "")})
        except Exception as error:  # noqa: BLE001
            sources.append({"name": "io_facts.skeleton", "state": "skipped", "note": f"{type(error).__name__}: {error}"})
            skeleton = None
    else:
        sources.append({"name": "io_facts.skeleton", "state": "skipped", "note": why})
    if not any(device.get("bus") == "usb" for device in devices):
        try:
            usb_devices = live_usb_skeleton()
            devices += usb_devices
            sources.append({"name": "io_facts.usb", "state": "used", "note": f"{len(usb_devices)} devices"})
        except Exception as error:  # noqa: BLE001
            sources.append({"name": "io_facts.usb", "state": "skipped", "note": f"{type(error).__name__}: {error}"})
    if skeleton is None:
        for module in ("pci", "netdev", "tty", "v4l"):
            function, why = _import_source(f"io_facts.{module}", "devices")
            if function is None:
                sources.append({"name": f"io_facts.{module}", "state": "skipped", "note": why})
                continue
            try:
                found = [dict(device) for device in function()]
            except Exception as error:  # noqa: BLE001
                sources.append({"name": f"io_facts.{module}", "state": "skipped", "note": f"{type(error).__name__}: {error}"})
                continue
            devices += found
            sources.append({"name": f"io_facts.{module}", "state": "used", "note": f"{len(found)} entries"})
    return devices, sources


# ----- the OS layer: host -> bus -> (port) -> device -> function node --------------------------------------------
def _port_key(port: str) -> list[int]:
    return [int(part) for part in re.split(r"[-.]", port) if part.isdigit()]


@dataclass
class OsLayer:
    entities: dict[str, Entity] = field(default_factory=dict)  # path -> Entity, insertion order = display order
    icons: dict[str, str] = field(default_factory=dict)
    devices: dict[str, dict] = field(default_factory=dict)  # device id -> skeleton dict (every non-structure node)
    device_paths: dict[str, str] = field(default_factory=dict)  # device id -> path
    device_of_path: dict[str, str] = field(default_factory=dict)  # path -> device id
    functions: dict[str, list[dict]] = field(default_factory=dict)  # device id -> its tty / video / NIC nodes
    physical: set[str] = field(default_factory=set)  # device ids that are hardware, not a function node of one
    port_paths: dict[str, str] = field(default_factory=dict)  # USB port name ("1-5") -> path
    port_devices: dict[str, str] = field(default_factory=dict)  # USB port name -> device id plugged there
    # the skeleton's host node (or one made from the hostname): what a ``host`` match sees. Its id maps to HOST_PATH
    # in device_paths / device_of_path so a computer plugin's job is located like any device's; it is never in
    # ``devices`` (no presence check) nor ``physical`` (not counted as a device, never a holder-check target).
    host_device: dict | None = None

    def matchable(self, device_id: str) -> dict:
        """The skeleton dict a plugin sees: the device plus its function nodes (``functions``, an added key)."""

        if device_id not in self.devices and self.host_device is not None and device_id == str(self.host_device.get("id")):
            return {**self.host_device, "functions": []}
        return {**self.devices[device_id], "functions": self.functions.get(device_id, [])}

    def matchables(self) -> list[dict]:
        """Every device a plugin may match: the skeleton devices, then the host (the Computer)."""

        return [self.matchable(device_id) for device_id in self.devices] + (
            [self.matchable(str(self.host_device.get("id")))] if self.host_device is not None else [])


def _identity(device: dict) -> dict[str, str]:
    attrs = device.get("attrs") or {}
    identity = {"manufacturer": device.get("vendor"), "model": device.get("product") or device.get("label"),
                "serial_number": device.get("serial")}
    bcd = str(attrs.get("bcd_device") or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{4}", bcd):
        identity["hw_version"] = f"bcdDevice {int(bcd[:2], 16)}.{bcd[2:]}"
    return {key: str(value).strip() for key, value in identity.items() if value and str(value).strip()}


def _hardware_id(device: dict) -> str:
    attrs = device.get("attrs") or {}
    if attrs.get("hardware_id"):
        return str(attrs["hardware_id"])
    if device.get("bus") == "usb":
        from io_diagnostics.plugins import usb_ids

        vid, pid = usb_ids(device)
        if vid:
            return usb_hardware_id(vid, pid, device.get("serial") or "", attrs.get("port", ""))
    return str(device.get("id", ""))


def device_icon(device: dict, functions: list[dict] = ()) -> str:
    """Icon TYPE for an OS node (docs/api-v1.md vocabulary), from its bus and lane B's ``attrs.kind``."""

    attrs, bus, kind = device.get("attrs") or {}, device.get("bus"), kind_of(device)
    if kind in ("bus", "group"):
        return GROUP_ICONS.get(bus, "group") if kind == "bus" else "group"
    if kind == "port":
        return "port"
    if bus != "usb":
        return GROUP_ICONS.get(bus, "device")
    if kind == "hub" or str(attrs.get("device_class") or "") == "09":
        return "hub"
    if kind == "camera" or attrs.get("video_nodes") or any(function.get("bus") == "v4l" for function in functions):
        return "camera"
    if kind == "hid" or re.search(r"mouse|keyboard", str(device.get("label") or ""), re.I):
        return "hid"
    if attrs.get("ttys") or any(function.get("bus") == "tty" for function in functions):
        return "adapter"
    return "usb"


def build_os_layer(hostname: str, skeleton: list[dict], extra_ports: set[str] | frozenset = frozenset()) -> OsLayer:
    """Skeleton dicts -> OS entities, parents first. ``extra_ports``: USB ports with recent kernel-log activity
    and nothing plugged in now (28B3A25D's port after it left), so their flapping stays visible."""

    layer = OsLayer()
    used_paths: set[str] = set()

    def unique(path: str) -> str:
        candidate, number = path, 2
        while candidate in used_paths:
            candidate, number = f"{path}~{number}", number + 1
        used_paths.add(candidate)
        return candidate

    def add(entity: Entity, icon: str) -> None:
        layer.entities[entity.path] = entity
        layer.icons[entity.path] = icon

    hosts = [device for device in skeleton if device.get("bus") == "host" or kind_of(device) == "host"]
    host_attrs = (hosts[0].get("attrs") or {}) if hosts else {}
    software = " · ".join(text for text in (host_attrs.get("os"), f"kernel {host_attrs['kernel']}" if host_attrs.get("kernel") else "") if text)
    add(Entity(unique(HOST_PATH), hostname or (hosts[0].get("label") if hosts else "host"), "host",
               identity={"sw_version": software} if software else {}, owner="io_diagnostics"), "host")
    placed: dict[str, str] = {str(host.get("id")): HOST_PATH for host in hosts}
    layer.host_device = dict(hosts[0]) if hosts else {"bus": "host", "id": "host", "path": "host", "label": hostname or "host",
                                                      "vendor": None, "product": None, "serial": None, "parent": None,
                                                      "attrs": {"kind": "host"}}
    nodes = [device for device in skeleton if device not in hosts]
    by_id = {str(device.get("id")): device for device in nodes}

    def group_path(bus: str) -> str:
        path = f"{HOST_PATH}/{slug(bus) or 'other'}"
        if path not in layer.entities:
            used_paths.add(path)
            add(Entity(path, GROUP_NAMES.get(bus, bus.upper()), "group", via=HOST_PATH, owner="io_diagnostics"), "group")
        return path

    def add_port(port: str, parent_path: str) -> str:
        path = unique(f"{parent_path}/port-{port}")
        add(Entity(path, f"Port {port}", "port", via=parent_path, owner="io_facts.usb"), "port")
        layer.port_paths[port] = path
        return path

    def place(device: dict, parent_path: str) -> None:
        device_id, kind, attrs = str(device.get("id")), kind_of(device), device.get("attrs") or {}
        label = str(device.get("label") or device.get("product") or device_id)
        owner = str(attrs.get("source") or f"io_facts.{device.get('bus', '?')}").split(" ")[0]
        if kind in STRUCTURE_KINDS:
            path = unique(f"{parent_path}/{slug(str(device.get('path') or device_id)) or 'node'}")
            add(Entity(path, label, kind, via=parent_path, owner=owner), device_icon(device))
            placed[device_id] = path
            return
        port = str(attrs.get("port") or "")
        if device.get("bus") == "usb" and PORT_NAME.match(port) and port not in layer.port_paths:
            parent_path = add_port(port, parent_path)
            layer.port_devices[port] = device_id
        path = unique(f"{parent_path}/{slug(str(device.get('path') or device_id)) or 'device'}")
        add(Entity(path, label, "device", via=parent_path, hardware_id=_hardware_id(device), identity=_identity(device),
                   owner=owner), "device")
        layer.devices[device_id] = device
        layer.device_paths[device_id] = path
        layer.device_of_path[path] = device_id
        placed[device_id] = path

    pending = list(nodes)
    while pending:
        waiting = []
        for device in pending:
            parent = str(device.get("parent")) if device.get("parent") is not None else None
            if parent is not None and parent in by_id and parent not in placed:
                waiting.append(device)
                continue
            place(device, placed.get(parent) if parent is not None and parent in placed else group_path(device.get("bus") or "other"))
        if len(waiting) == len(pending):  # a parent cycle: put the rest under their bus groups
            for device in waiting:
                place(device, group_path(device.get("bus") or "other"))
            break
        pending = waiting

    for device_id, device in layer.devices.items():
        parent = str(device.get("parent")) if device.get("parent") is not None else ""
        # WHY by bus: a tty, video node or NIC under a device is a FUNCTION of it; a USB device under a hub is hardware
        if device.get("bus") in FUNCTION_BUSES and parent in layer.devices:
            layer.functions.setdefault(parent, []).append(device)
        else:
            layer.physical.add(device_id)
    for device_id, device in layer.devices.items():
        layer.icons[layer.device_paths[device_id]] = device_icon(device, layer.functions.get(device_id, []))
    host_id = str(layer.host_device.get("id"))
    if host_id not in layer.devices:
        layer.device_paths[host_id], layer.device_of_path[HOST_PATH] = HOST_PATH, host_id

    for port in sorted(set(extra_ports) - set(layer.port_paths), key=lambda name: (name.count("."), _port_key(name))):
        parent_port = port.rsplit(".", 1)[0] if "." in port else None
        parent_id = f"usb:{parent_port}" if parent_port else f"usb:usb{port.split('-', 1)[0]}"
        add_port(port, placed.get(parent_id) or group_path("usb"))
    return layer


# ----- OS statuses: presence and port link ------------------------------------------------------------------------
@dataclass
class PortHistory:
    """What the kernel log says about one USB port in the last hour (``activity.usb_activity`` fills it)."""

    enumerations: int = 0  # adds in the last hour, boot enumeration excluded
    last_text: str = ""  # "fdcanusb 28B3A25D left 18:20:36"
    source: str = "journalctl -k (boot enumeration excluded)"


def _value_text(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(map(str, value))
    if isinstance(value, dict):
        return ", ".join(f"{key}={item}" for key, item in value.items() if item not in (None, "", [], {}))
    return str(value)


PRESENCE_VALUE_KEYS = ("usb_id", "port", "speed_mbps", "usb_version", "plugged_at", "driver", "class", "operstate",
                       "capabilities", "ttys", "by_id", "by_path", "other_links", "video_nodes", "interface", "hub_port")


def pci_statuses(entity: Entity, attrs: dict, path: str, now: float) -> list[DiagnosticStatus]:
    """The PCIe wire's own health from lane B's passive facts (io_facts.pci): link width against its maximum, the AER
    error counters, and a capture card that no kernel driver has bound (it cannot capture). Event-driven, like presence.

    WHY width and not speed: a GPU drops its link speed at idle (ASPM) and that is healthy; a link that TRAINED
    narrower than the card's maximum (x1 of x4) stays that way until a reseat. WHY only capture cards for the driver
    check: an IOMMU or SMBus function has no driver on a healthy machine; a frame grabber without one is unusable."""

    source = str(attrs.get("source") or "io_facts.pci")
    out = []
    link = attrs.get("link") if isinstance(attrs.get("link"), dict) else None
    aer = attrs.get("aer") if isinstance(attrs.get("aer"), dict) else {}
    if link:
        text = f"x{link.get('width', '?')} of x{link.get('max_width', '?')} · {link.get('speed', '?')} (max {link.get('max_speed', '?')})"
        level, code, message = OK, "", text
        if attrs.get("link_narrower_than_max"):
            level, code, message = WARN, "PcieLinkNarrow", f"trained narrower than the card can: {text} (reseat or another slot)"
        uncorrectable = int(aer.get("nonfatal") or 0) + int(aer.get("fatal") or 0)
        if uncorrectable:
            level, code = ERROR, "PcieUncorrectableErrors"
            message = f"{uncorrectable} uncorrectable PCIe error(s) since boot (AER) · {text}"
        elif aer.get("correctable"):
            level, code = (level, code) if code else (WARN, "PcieCorrectableErrors")
            message = f"{aer['correctable']} correctable PCIe error(s) since boot (AER) · {message}"
        measurements = tuple(Measurement(f"aer {kind}", int(aer[kind]), "1", warn_high=0 if kind == "correctable" else None,
                                         error_high=None if kind == "correctable" else 0)
                             for kind in ("correctable", "nonfatal", "fatal") if aer.get(kind) is not None)
        out.append(DiagnosticStatus(level=level, name=f"{entity.name}: PCIe link", message=message, code=code, path=path,
                                    hardware_id=entity.hardware_id, stamp=now, received=now, source=source,
                                    values=(KeyValue("width", f"x{link.get('width')} of x{link.get('max_width')}"),
                                            KeyValue("speed", f"{link.get('speed')} of {link.get('max_speed')}")),
                                    measurements=measurements))
    if attrs.get("likely_capture_card") and not attrs.get("driver"):
        reasons = "; ".join(str(reason) for reason in attrs.get("capture_card_reasons") or [])
        # WHY first: equal levels headline the first check, and "cannot capture" outranks a few AER retries
        out.insert(0, DiagnosticStatus(level=WARN, name=f"{entity.name}: kernel driver", code="NoKernelDriver", path=path,
                                    message="likely frame grabber / capture card with no kernel driver bound: it cannot capture"
                                            " until its vendor driver loads", hardware_id=entity.hardware_id, stamp=now,
                                    received=now, source=source, values=(KeyValue("why_capture_card", reasons),) if reasons else ()))
    return out


def os_statuses(layer: OsLayer, history: dict[str, PortHistory], now: float, plugin_devices: set[str]) -> list[DiagnosticStatus]:
    """The OS observer's own checks: one presence check per device, one link check per USB port. Event-driven
    (``expected_period_s=None``): presence comes from sysfs + udev, so it never goes stale."""

    statuses = []
    for device_id, device in layer.devices.items():
        path = layer.device_paths[device_id]
        attrs = device.get("attrs") or {}
        bits = []
        if attrs.get("port"):
            bits.append(f"on port {attrs['port']}")
        if attrs.get("speed_mbps"):
            bits.append(speed_text(attrs["speed_mbps"], usb=device.get("bus") == "usb"))
        if attrs.get("operstate"):
            bits.append(f"link {attrs['operstate']}")
        if device.get("bus") == "pci":
            bits += [text for text in (attrs.get("class"), f"kernel driver {attrs['driver']}" if attrs.get("driver") else "") if text]
        if kind_of(device) == "hub":
            bits.append("hub")
        elif device_id in layer.physical and device_id not in plugin_devices:
            bits.append("no driver diagnostics")
        values = [KeyValue(key, _value_text(attrs[key])) for key in PRESENCE_VALUE_KEYS
                  if attrs.get(key) not in (None, "", [], {})]
        if attrs.get("simulated"):
            values.append(KeyValue("descriptors", "simulated (fixture): this device is not on the machine now"))
        measurements = ((Measurement("link speed", attrs["speed_mbps"], "Mbit/s"),)
                        if isinstance(attrs.get("speed_mbps"), (int, float)) and attrs.get("speed_mbps") else ())
        statuses.append(DiagnosticStatus(level=OK, name=f"{layer.entities[path].name}: presence", message=" · ".join(bits) or "present",
                                         hardware_id=layer.entities[path].hardware_id, values=tuple(values), path=path,
                                         stamp=now, received=now, source=str(attrs.get("source") or f"io_facts.{device.get('bus')}"),
                                         measurements=measurements))
        if device.get("bus") == "pci":
            statuses += pci_statuses(layer.entities[path], attrs, path, now)
    for port, path in layer.port_paths.items():
        seen = history.get(port, PortHistory())
        device_id = layer.port_devices.get(port)
        if device_id:
            speed = (layer.devices[device_id].get("attrs") or {}).get("speed_mbps")
            message = "1 device" + (f" · {speed_text(speed).split(' · ')[-1]}" if speed else "")
        else:
            message = f"empty · last: {seen.last_text}" if seen.last_text else "empty"
        level, code = OK, ""
        if seen.enumerations >= FLAPPING_ENUMERATIONS:
            level, code = WARN, "PortFlapping"
            message = f"{seen.enumerations} enumerations in the last hour (flapping) · {message}"
        elif seen.enumerations and device_id:
            message = f"{seen.enumerations} enumeration{'s' if seen.enumerations != 1 else ''} in the last hour · {message}"
        statuses.append(DiagnosticStatus(level=level, name=f"Port {port}: link", message=message, path=path, code=code,
                                         stamp=now, received=now, source=f"io_facts.journal ({seen.source})",
                                         values=(KeyValue("sysfs", f"/sys/bus/usb/devices/{port}"),),
                                         measurements=(Measurement("enumerations / hour", seen.enumerations, "1",
                                                                   warn_high=FLAPPING_ENUMERATIONS - 1),)))
    return statuses


# ----- merging plugin children ------------------------------------------------------------------------------------
@dataclass
class Merged:
    entities: dict[str, Entity]
    statuses: list[DiagnosticStatus]
    icons: dict[str, str]
    children: dict[str, list[str]]
    edge: dict[str, str | None]  # path -> "via" | "part_of" | None
    parent: dict[str, str | None]
    plugin_devices: set[str]  # device ids a plugin diagnoses


def merge(layer: OsLayer, jobs: list, extra_statuses: list[DiagnosticStatus]) -> Merged:
    """OS layer + every job's cached records (rebased onto the device's CURRENT path)."""

    from io_diagnostics.runner import absolute, rebase

    entities: dict[str, Entity] = dict(layer.entities)
    icons = dict(layer.icons)
    statuses: list[DiagnosticStatus] = list(extra_statuses)
    plugin_devices: set[str] = set()
    for job in jobs:
        device_id = str(job.device.get("id"))
        device_path = layer.device_paths.get(device_id)
        if device_path is None:
            continue
        plugin_devices.add(device_id)
        cache = job.cache
        for relative_path in cache.order:
            record = cache.entities[relative_path]
            path = absolute(record.path, device_path)
            if path == device_path:
                base = entities[device_path]
                entities[device_path] = dataclasses.replace(
                    base, name=record.name or base.name, identity={**base.identity, **dict(record.identity)},
                    owner=record.owner or job.spec.name, actions=record.actions or base.actions,
                    hardware_id=base.hardware_id or record.hardware_id)
            elif path in layer.entities:
                continue  # WHY: OS structure (ports, hubs, function nodes) is the OS's; a plugin cannot re-parent it
            else:
                via, part_of = absolute(record.via, device_path), absolute(record.part_of, device_path)
                if via is None and part_of is None:
                    via = device_path
                entities[path] = dataclasses.replace(record, path=path, via=via, part_of=part_of,
                                                     owner=record.owner or job.spec.name)
            if relative_path in cache.icons:
                icons[path] = cache.icons[relative_path]
        for status in cache.statuses.values():
            statuses.append(rebase(status, device_path))
        if cache.host_status is not None:
            statuses.append(dataclasses.replace(cache.host_status, path=device_path))
    for status in statuses:
        if status.path and status.path not in entities:
            # a check about an entity the plugin never described: give it a row rather than an invisible root
            name = status.name.split(":", 1)[0] if ":" in status.name else status.path.rsplit("/", 1)[-1]
            entities[status.path] = Entity(status.path, name, "node", owner=status.source)
    parent: dict[str, str | None] = {}
    edge: dict[str, str | None] = {}
    for path, entity in list(entities.items()):
        target, kind = (entity.part_of, "part_of") if entity.part_of else (entity.via, "via") if entity.via else (None, None)
        if path != HOST_PATH and (target is None or target not in entities or target == path):
            # an orphan (its parent never arrived): hang it on the nearest existing ancestor by path, else the host
            target = next((candidate for candidate in _path_ancestors(path) if candidate in entities), HOST_PATH)
            kind = "via"
            entities[path] = dataclasses.replace(entity, via=target, part_of=None)
        parent[path], edge[path] = target, kind
    children: dict[str, list[str]] = {path: [] for path in entities}
    for path in entities:
        if parent[path] is not None:
            children[parent[path]].append(path)
    for siblings in children.values():  # ports in port order (an empty port is added after the occupied ones)
        ports = sorted((path for path in siblings if entities[path].kind == "port"),
                       key=lambda path: _port_key(entities[path].name.removeprefix("Port ")))
        if ports:
            ordered = iter(ports)
            siblings[:] = [next(ordered) if entities[path].kind == "port" else path for path in siblings]
    _break_cycles(entities, parent, edge, children)
    for path, entity in entities.items():
        if path not in icons:
            icons[path] = _plugin_icon(entity)
    return Merged(entities, statuses, icons, children, edge, parent, plugin_devices)


def _path_ancestors(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(len(parts) - 1, 0, -1)]


def _break_cycles(entities, parent, edge, children) -> None:
    reachable, stack = set(), [HOST_PATH]
    while stack:
        path = stack.pop()
        if path in reachable:
            continue
        reachable.add(path)
        stack.extend(children.get(path, ()))
    for path in [path for path in entities if path not in reachable]:  # part of a parent cycle: re-hang on the host
        if parent[path] in children and path in children[parent[path]]:
            children[parent[path]].remove(path)
        parent[path], edge[path] = HOST_PATH, "via"
        children[HOST_PATH].append(path)
        entities[path] = dataclasses.replace(entities[path], via=HOST_PATH, part_of=None)


def _plugin_icon(entity: Entity) -> str:
    kind = entity.kind
    if kind in ("bus", "stream", "port", "host", "group"):
        return kind
    if kind == "part":
        return "motor" if re.search(r"motor", entity.name, re.I) else "chip"
    return "chip" if kind == "node" else "device"


# ----- rows: summarize + what the API returns per node ------------------------------------------------------------
def worse(first: str | None, second: str | None) -> bool:
    return first is not None and (second is None or RANK.get(first, 0) > RANK.get(second, 0))


@dataclass
class Rows:
    merged: Merged
    now: float
    summaries: dict[str, Summary]
    own: dict[str, list[DiagnosticStatus]]  # latest own records per path, worst first
    knock_on_of: dict[str, str]
    causes: list[str]  # independent non-OK causes, worst first
    depth: dict[str, int]


# The host's own checks (the computer plugin, the USB/PCIe links line) as summarize sees them: a leaf beside the
# host's children. WHY (2026-10-23): summarize collapses every problem below a failed ancestor into it, and the host
# is everyone's ancestor; a hot CPU or a crashed computer plugin explains nothing about a dead CAN bus, so the
# Computer's own report is one more cause, never the cause of the rest. ":" never occurs in a slugged path.
HOST_SELF = f"{HOST_PATH}/:self"


def _summaries(merged: Merged, now: float) -> dict[str, Summary]:
    entities = list(merged.entities.values())
    # WHY not the checks that did not run (io_diagnostics/not_run.py): they say nothing about an entity, yet "unknown" outranks "warn", so
    # one NoSource row turned a board of OK checks purple. They stay in ``Rows.own`` (the drill-in lists them) and nowhere else.
    ran = [status for status in merged.statuses if not is_not_run(status)]
    if not any(status.path == HOST_PATH for status in ran):
        return summarize(entities, ran, now)
    host = merged.entities[HOST_PATH]

    def to_self(path: str) -> str:
        return HOST_SELF if path == HOST_PATH else path

    def from_self(path: str) -> str:
        return HOST_PATH if path == HOST_SELF else path

    # a caused_by naming the host names its own records, which now live on the leaf
    statuses = [dataclasses.replace(status, path=to_self(status.path), caused_by=tuple(map(to_self, status.caused_by)))
                for status in ran]
    summaries = summarize([*entities, Entity(HOST_SELF, host.name, host.kind, via=HOST_PATH)], statuses, now)
    own, root = summaries.pop(HOST_SELF), summaries[HOST_PATH]
    rollup = dataclasses.replace(root.rollup, origin=from_self(root.rollup.origin),
                                 knock_on=tuple((from_self(member), from_self(cause)) for member, cause in root.rollup.knock_on))
    summaries[HOST_PATH] = Summary(HOST_PATH, own.own, own.own, own.message, rollup)
    return summaries


def build_rows(merged: Merged, now: float) -> Rows:
    summaries = _summaries(merged, now)
    latest: dict[tuple[str, str], DiagnosticStatus] = {}
    for status in merged.statuses:
        latest[(status.path, status.name)] = status
    own: dict[str, list[DiagnosticStatus]] = {}
    for (path, _name), status in latest.items():
        own.setdefault(path, []).append(status)
    for records in own.values():
        records.sort(key=lambda status: -RANK.get(effective_level(status, now), 0))
    depth: dict[str, int] = {}
    stack = [(HOST_PATH, 0)]
    while stack:
        path, level = stack.pop()
        depth[path] = level
        stack.extend((child, level + 1) for child in merged.children.get(path, ()))
    root = summaries.get(HOST_PATH)
    knock_on_of = dict(root.rollup.knock_on) if root else {}
    causes = [path for path, summary in summaries.items()
              if summary.own not in (None, OK) and path not in knock_on_of and path in merged.entities]
    causes.sort(key=lambda path: (-RANK.get(summaries[path].own, 0), depth.get(path, 0)))
    return Rows(merged, now, summaries, own, knock_on_of, causes, depth)


def physical_count(path: str, layer: OsLayer) -> int:
    return sum(1 for device_id in layer.physical if layer.device_paths[device_id].startswith(path + "/"))


def _id_text(path: str, entity: Entity, layer: OsLayer) -> str:
    if entity.kind in ("host", "group", "bus") and path in layer.entities:
        count = physical_count(path, layer)
        return f"{count} device{'s' if count != 1 else ''}"
    if entity.kind == "composite":  # the file's own word for it ("arm"): shown, never read (docs/composition-v1.md)
        return str(entity.identity.get("model", ""))
    if entity.kind == "port":
        device_id = layer.port_devices.get(entity.name.removeprefix("Port "))
        if not device_id:
            return "empty"
        speed = (layer.devices[device_id].get("attrs") or {}).get("speed_mbps")
        return speed_text(speed).split(" · ")[-1] if speed else ""
    return str(entity.identity.get("serial_number", "")) if entity.identity else ""


def last_seen(own: list[DiagnosticStatus]) -> float | None:
    """When the row was last heard from: its PERIODIC records' newest receive time when it has any (those are the
    ones staleness judges), else any record's. WHY: an event-driven check (firmware, config) is re-sent on change
    or re-stamped by a replay, and would make a silent board look freshly heard."""

    periodic = [status.received for status in own if status.expected_period_s and status.received is not None]
    received = periodic or [status.received for status in own if status.received is not None]
    return max(received) if received else None


# Kinds that are structure, not something to hear from: no presence dot (docs/api-v1.md "presence").
NO_PRESENCE_KINDS = ("group", "port", "rig")


def heard_text(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} days ago"


def presence_of(path: str, entity: Entity, summary: Summary, own: list[DiagnosticStatus], seen: float | None, now: float,
                memory: dict[str, float], live: frozenset | set, answering: dict[str, float] | None = None) -> dict | None:
    """One row's presence, the Claude-sidebar dot (Zach, 2026-10-24): ``heard`` (filled: heard within its expected
    period), ``stale`` (hollow ring: known but not heard lately, with when) or ``live`` (pulsing: data arriving now).
    None for structure with nothing to hear from. WHY never a level: presence is neutral; warning colours are
    reserved for real WARN / ERROR / UNKNOWN.

    ``answering``: path -> seconds since a presence pass heard it (docs/api-v1.md "Presence"). Such a row is ``live`` first,
    whatever its older checks say: it answers now (Connected only shows it); its level still says what the last full read found."""

    if answering and path in answering and entity.kind not in NO_PRESENCE_KINDS:
        return {"state": "live", "age_s": answering[path], "restored": False, "text": "answers now"}
    if entity.kind in NO_PRESENCE_KINDS or (not own and path not in memory):
        return None
    if path in memory and not own:  # restored from the last run, no fresh report yet
        stamp = memory[path]
        age = round(now - stamp, 3) if stamp else None
        return {"state": "stale", "age_s": age, "restored": True,
                "text": f"last heard {heard_text(age)} (before this session)" if age is not None else "not heard yet this session"}
    if summary.own == STALE:
        age = round(now - seen, 3) if seen is not None else None
        return {"state": "stale", "age_s": age, "restored": False,
                "text": f"last heard {heard_text(age)}" if age is not None else "not heard lately"}
    return {"state": "live" if path in live else "heard", "age_s": round(now - seen, 3) if seen is not None else None,
            "restored": False, "text": "data arriving now" if path in live else ""}


def usb_json(path: str, layer: OsLayer) -> dict | None:
    """A USB device row's own version and its link, for the page to say "USB 2.1" (web/usb.js): ``version`` is the
    device's bcdUSB as sysfs shows it ("2.10"), ``speed_mbps`` the link it runs at. None for anything else."""

    device = layer.devices.get(layer.device_of_path.get(path, ""))
    if not device or device.get("bus") != "usb":
        return None
    attrs = device.get("attrs") or {}
    version, speed = str(attrs.get("usb_version") or "").strip(), attrs.get("speed_mbps")
    if not version and not speed:
        return None
    return {"version": version or None, "speed_mbps": speed or None}


def is_claimed(path: str, entity: Entity, merged: Merged, layer: OsLayer) -> bool:
    """Whether some driver plugin claims this row. An OS device nobody claims (a keyboard, a mouse, a webcam, a NIC) is
    "likely not needed" (Zach, 2026-10-24) and the page hides it by default; anything a plugin reported, and all
    structure, is claimed."""

    device_id = layer.device_of_path.get(path)
    if device_id is None or device_id not in layer.physical:
        return True
    return device_id in merged.plugin_devices


def node_json(path: str, rows: Rows, layer: OsLayer, faults_by_path: dict[str, int], *, nested: bool,
              memory: dict[str, float] | None = None, live: frozenset | set = frozenset(),
              expected_paths: frozenset | set = frozenset(), rates: dict[str, dict] | None = None,
              answering: dict[str, float] | None = None) -> dict:
    merged, summary = rows.merged, rows.summaries[path]
    entity = merged.entities[path]
    listed = rows.own.get(path, [])
    # WHY the checks that ran: a check that did not run is the host's own row, re-posted every beat; it says nothing of when the entity
    # was last heard from, how often it reports or whether it is offline (io_diagnostics/not_run.py). ``checks`` still counts every row.
    own = [status for status in listed if not is_not_run(status)]
    hint = None
    if worse(summary.rollup.level, summary.level):
        hint = {"level": summary.rollup.level, "origin": summary.rollup.origin, "message": summary.rollup.message}
    seen = last_seen(own)
    periods = [status.expected_period_s for status in own if status.expected_period_s]
    connected = not any(status.mode == "offline" for status in own)
    if entity.kind == "port" and entity.name.removeprefix("Port ") not in layer.port_devices:
        connected = False
    origin = summary.rollup.origin
    node = {
        "path": path, "name": entity.name or path, "kind": entity.kind or "device",
        "icon": merged.icons.get(path) if merged.icons.get(path) in ICONS else "device",
        "parent": merged.parent.get(path), "edge": merged.edge.get(path), "depth": rows.depth.get(path, 0),
        "level": summary.level, "message": summary.message,
        "own": {"level": summary.own, "message": summary.message if summary.own is not None else ""},
        "rollup": {"level": summary.rollup.level, "origin": origin,
                   "origin_name": merged.entities[origin].name if origin in merged.entities else "",
                   "message": summary.rollup.message, "counts": dict(summary.rollup.counts),
                   "knock_on": [{"path": member, "cause": cause} for member, cause in summary.rollup.knock_on]},
        "hint": hint, "knock_on_of": rows.knock_on_of.get(path), "stale": summary.own == STALE, "connected": connected,
        "hardware_id": entity.hardware_id, "identity": dict(entity.identity), "owner": entity.owner,
        "id_text": _id_text(path, entity, layer),
        "last_received": seen,
        "age_s": round(rows.now - seen, 3) if seen is not None else None,
        "expected_period_s": min(periods) if periods else None,
        "stale_after_s": max(STALE_FACTOR * min(periods), STALE_FLOOR_S) if periods else None,
        "checks": len(listed), "not_run": len(listed) - len(own), "actions": len(entity.actions), "faults": faults_by_path.get(path, 0),
        "collapsed": path in COLLAPSED,
        "presence": presence_of(path, entity, summary, own, seen, rows.now, memory or {}, live, answering),
        "claimed": is_claimed(path, entity, merged, layer) or path in expected_paths,
        "usb": usb_json(path, layer),
        "rate": (rates or {}).get(path),
    }
    children = merged.children.get(path, [])
    node["children"] = ([node_json(child, rows, layer, faults_by_path, nested=True, memory=memory, live=live,
                                   expected_paths=expected_paths, rates=rates, answering=answering) for child in children]
                        if nested else list(children))
    return node


def nearest_device(path: str, merged: Merged, layer: OsLayer | None = None) -> str | None:
    """The nearest entity at or above ``path`` that is a device; with ``layer``, the nearest PHYSICAL one (a skeleton
    device, not a function node), which is what holder and lock checks are about."""

    current: str | None = path
    while current is not None:
        if layer is None and merged.entities[current].kind == "device":
            return current
        if layer is not None and layer.device_of_path.get(current) in layer.physical:
            return current
        current = merged.parent.get(current)
    return None


__all__ = ["presence_of", "is_claimed", "heard_text", "HOST_PATH", "GROUP_NAMES", "ICONS", "STRUCTURE_KINDS", "PortHistory", "OsLayer", "Merged", "Rows", "slug",
           "speed_text", "usb_hardware_id", "usb_device_dict", "live_usb_skeleton", "load_skeleton", "build_os_layer",
           "os_statuses", "merge", "build_rows", "node_json", "nearest_device", "worse", "device_icon", "physical_count",
           "last_seen"]
