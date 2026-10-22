"""PCI / PCIe functions from /sys/bus/pci/devices: class, ids, driver, parent bridge, link and error counters.

Read-only sysfs. Names come from the hwdata ``pci.ids`` database, the file ``lspci`` reads, so a label here
matches what ``lspci`` prints. PCIe link width/speed and the AER error totals are the PCIe twin of "a D405 on
USB 2 instead of 3.2": a frame grabber trained at x4 in an x8 slot, or a riser logging correctable errors.

Frame grabbers and capture cards are flagged (``attrs.likely_capture_card``) with the reasons that fired.
Zach's hyperspectral notes carry a Sapera capture config for a Teledyne DALSA Xtium-CL MX4 (Camera Link frame
grabber, PCIe); none is installed on the rack PC today, so tests cover one with a fixture.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .skeleton import (BDF, PCI_ROOT_BUS, ROOT, USB_ROOT_HUB, IdsDatabase, ids_database, link_name, natural_key, node,
                       read_int, read_text)

SYS_PCI = Path("sys/bus/pci/devices")

# The PCI-SIG base classes, as pci.ids names them: the fallback when no pci.ids is installed.
BASE_CLASSES = {
    0x00: "Unclassified device", 0x01: "Mass storage controller", 0x02: "Network controller", 0x03: "Display controller",
    0x04: "Multimedia controller", 0x05: "Memory controller", 0x06: "Bridge", 0x07: "Communication controller",
    0x08: "Generic system peripheral", 0x09: "Input device controller", 0x0a: "Docking station", 0x0b: "Processor",
    0x0c: "Serial bus controller", 0x0d: "Wireless controller", 0x0e: "Intelligent controller",
    0x0f: "Satellite communications controller", 0x10: "Encryption controller", 0x11: "Signal processing controller",
    0x12: "Processing accelerators", 0x13: "Non-Essential Instrumentation", 0x40: "Coprocessor", 0xff: "Unassigned class",
}

# Vendors whose PCI(e) cards are frame grabbers or video capture cards. Each id is in pci.ids 2024.03.31 under
# that vendor name; the product families in brackets are from vendor knowledge, not pci.ids (it lists no Xtium):
# confirm 11ec with `lspci -nn` the first time a DALSA card is installed.
FRAME_GRABBER_VENDORS = {
    "11ec": "Coreco / Teledyne DALSA (Xtium, X64)",
    "112f": "Dalsa",
    "1805": "Euresys (Coaxlink, Grablink, Picolo)",
    "118d": "BitFlow",
    "1ae8": "Basler (microEnable, ex Silicon Software)",
    "165a": "Epix (PIXCI)",
    "102b": "Matrox (Imaging frame grabbers)",
    "1cd7": "Magewell (Pro Capture)",
    "bdbd": "Blackmagic Design (DeckLink)",
    "dada": "Datapath (VisionRGB)",
    "1b66": "DELTACAST",
    "f1d0": "AJA Video",
    "12ab": "YUAN High-Tech",
}
# Kernel modules of grabber SDKs: Hikrobot's gevframegrabber / cxpframegrabber / cmlframegrabber (Zach's
# line-scan setup notes), Sapera, Euresys MultiCam / Coaxlink, BitFlow, microEnable, Magewell ProCapture ...
FRAME_GRABBER_DRIVER = re.compile(
    r"grabber|xtium|sapera|coreco|euresys|coaxlink|grablink|multicam|bitflow|menable|pixci|decklink|blackmagic"
    r"|procapture|magewell|deltacast|ajantv2", re.IGNORECASE)

KINDS_BY_BASE = {0x01: "storage", 0x02: "network", 0x03: "display", 0x06: "bridge", 0x0d: "wireless", 0x12: "accelerator"}
KINDS_BY_SUBCLASS = {(0x04, 0x00): "video", (0x04, 0x01): "audio", (0x04, 0x03): "audio", (0x04, 0x80): "multimedia",
                     (0x0c, 0x03): "usb_controller",
                     (0x07, 0x00): "serial_controller", (0x0c, 0x05): "system", (0x0c, 0x00): "firewire"}
SYSTEM_BASES = {0x05, 0x08, 0x0b, 0x10, 0x13}


def _hex4(path: Path) -> str:
    value = read_int(path, 16)
    return f"{value:04x}" if value is not None else "????"


def _aer_totals(device_dir: Path) -> dict | None:
    """``{"correctable": 0, "nonfatal": 0, "fatal": 0}`` from the AER counters, None without AER."""

    totals = {}
    for kind, filename, key in (("correctable", "aer_dev_correctable", "TOTAL_ERR_COR"),
                                ("nonfatal", "aer_dev_nonfatal", "TOTAL_ERR_NONFATAL"),
                                ("fatal", "aer_dev_fatal", "TOTAL_ERR_FATAL")):
        for line in (read_text(device_dir / filename) or "").splitlines():
            name, _, value = line.strip().rpartition(" ")
            if name.strip() == key and value.isdigit():
                totals[kind] = int(value)
    return totals or None


def _link(device_dir: Path) -> dict | None:
    """Negotiated vs maximum PCIe link; None for functions without a PCIe link (integrated root-complex ones)."""

    if not (device_dir / "current_link_speed").exists():
        return None
    return {"speed": read_text(device_dir / "current_link_speed"), "width": read_int(device_dir / "current_link_width"),
            "max_speed": read_text(device_dir / "max_link_speed"), "max_width": read_int(device_dir / "max_link_width")}


def _video_nodes(device_dir: Path) -> list[str]:
    """video4linux nodes the function itself registers (a capture card, maybe via one sub-device).

    Never nodes of another PCI function below a bridge, nor of USB cameras on this controller's buses.
    """

    below = [path for path in device_dir.glob("*/video4linux/*")
             if not (BDF.match(path.parent.parent.name) or USB_ROOT_HUB.match(path.parent.parent.name))]
    return sorted((path.name for path in [*device_dir.glob("video4linux/*"), *below]), key=natural_key)


def capture_card_reasons(vendor_id: str, class_code: int, driver: str | None, video_nodes: list[str]) -> list[str]:
    """Why a function looks like a frame grabber / capture card; empty when nothing points that way.

    WHY not class 0x0480 ("other multimedia") alone: AMD's audio co-processor and Intel's IPU report it too.
    WHY vendors only off display class: Matrox's frame grabbers share a vendor id with its server VGA chips.
    """

    base, sub = class_code >> 16, (class_code >> 8) & 0xFF
    reasons = []
    if base == 0x06:  # a bridge is plumbing, whatever sits behind it
        return reasons
    if vendor_id in FRAME_GRABBER_VENDORS and base != 0x03:
        reasons.append(f"vendor {vendor_id} = {FRAME_GRABBER_VENDORS[vendor_id]}, a frame grabber / capture card maker")
    if (base, sub) == (0x04, 0x00):
        reasons.append("class 0400: multimedia video controller")
    if driver and FRAME_GRABBER_DRIVER.search(driver):
        reasons.append(f"driver {driver} is a grabber driver")
    if video_nodes:
        reasons.append(f"registers video4linux nodes ({', '.join(video_nodes)})")
    return reasons


def _kind(class_code: int, capture: bool) -> str:
    base, sub = class_code >> 16, (class_code >> 8) & 0xFF
    if capture:
        return "capture"
    if (base, sub) in KINDS_BY_SUBCLASS:
        return KINDS_BY_SUBCLASS[(base, sub)]
    if base in KINDS_BY_BASE:
        return KINDS_BY_BASE[base]
    return "system" if base in SYSTEM_BASES else "other"


def _upstream(device_dir: Path) -> tuple[list[str], str | None]:
    """The bridge chain from the root port down to this function's parent bridge, and the root bus."""

    upstream = []
    for ancestor in device_dir.parents:
        if BDF.match(ancestor.name):
            upstream.append(ancestor.name)
            continue
        match = PCI_ROOT_BUS.match(ancestor.name)
        return list(reversed(upstream)), match[1] if match else None
    return list(reversed(upstream)), None


def function(bdf: str, device_dir: Path, names: IdsDatabase) -> dict:
    """One PCI function as a skeleton dict."""

    class_code = read_int(device_dir / "class", 16) or 0
    base, sub, prog_if = class_code >> 16, (class_code >> 8) & 0xFF, class_code & 0xFF
    vendor_id, device_id = _hex4(device_dir / "vendor"), _hex4(device_dir / "device")
    subsystem_vendor_id, subsystem_device_id = _hex4(device_dir / "subsystem_vendor"), _hex4(device_dir / "subsystem_device")
    driver = link_name(device_dir / "driver")
    upstream, root_bus = _upstream(device_dir)
    video_nodes = _video_nodes(device_dir)
    reasons = capture_card_reasons(vendor_id, class_code, driver, video_nodes)
    vendor_name = names.vendors.get(vendor_id)
    device_name = names.devices.get((vendor_id, device_id))
    class_name = names.classes.get((f"{base:02x}",)) or BASE_CLASSES.get(base) or f"class {base:02x}"
    subclass_name = names.classes.get((f"{base:02x}", f"{sub:02x}"))
    link = _link(device_dir)
    # WHY width only: GPUs may drop link SPEED when idle to save power, so a lower speed is not a fault by itself;
    # a width trained below the card's maximum (x4 of x8: a slot wired narrower, or a bad seat) costs bandwidth always.
    narrower = bool(link and link["width"] and link["max_width"] and link["width"] < link["max_width"])
    net_dir = device_dir / "net"
    attrs = {
        "kind": _kind(class_code, bool(reasons)),
        "bdf": bdf,
        "root_bus": root_bus,
        "parent_bridge": upstream[-1] if upstream else None,
        "upstream": upstream,  # root port first: "behind the chipset" is visible here even when bridges are folded
        "class_code": f"{class_code:06x}",
        "class": class_name,
        "subclass": subclass_name,
        "prog_if": names.classes.get((f"{base:02x}", f"{sub:02x}", f"{prog_if:02x}")),
        "vendor_id": vendor_id,
        "device_id": device_id,
        "subsystem_vendor_id": subsystem_vendor_id,
        "subsystem_device_id": subsystem_device_id,
        "subsystem": names.subsystems.get((vendor_id, device_id, subsystem_vendor_id, subsystem_device_id)),
        "revision": (read_text(device_dir / "revision") or "").strip() or None,
        "driver": driver,  # None = no driver bound (e.g. the grabber SDK's module is not loaded)
        "link": link,
        "link_narrower_than_max": narrower,
        "aer": _aer_totals(device_dir),
        "power_state": read_text(device_dir / "power_state"),
        "usb_buses": sorted((f"usb:{path.name}" for path in device_dir.glob("usb*") if USB_ROOT_HUB.match(path.name)), key=natural_key),
        "net": sorted(path.name for path in net_dir.iterdir()) if net_dir.is_dir() else [],
        "video_nodes": video_nodes,
        "likely_capture_card": bool(reasons),
        "capture_card_reasons": reasons,
        "names_source": "pci.ids" if vendor_name or device_name else None,
        "hardware_id": f"pci:{vendor_id}:{device_id}:{subsystem_vendor_id}:{subsystem_device_id}@{bdf}",
        "sysfs": str(device_dir),
        "source": "io_facts.pci (sysfs + pci.ids)",
    }
    if device_name:
        label = device_name
    elif reasons:
        label = f"Likely frame grabber · {vendor_name or FRAME_GRABBER_VENDORS.get(vendor_id) or vendor_id}"
    else:
        label = subclass_name or class_name
    return node("pci", f"pci:{bdf}", f"pci-{bdf}", label, vendor=vendor_name, product=device_name,
                parent=f"pci:{upstream[-1]}" if upstream else None, attrs=attrs)


def devices(root: Path = ROOT) -> list[dict]:
    """Every PCI function, bridges included, each with its real parent bridge (None on a root bus)."""

    sys_pci = Path(root) / SYS_PCI
    if not sys_pci.is_dir():
        return []
    names = ids_database(Path(root), "pci.ids")
    return [function(entry.name, Path(os.path.realpath(entry)), names) for entry in sorted(sys_pci.iterdir(), key=lambda p: p.name)]
