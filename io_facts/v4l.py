"""video4linux nodes from /sys/class/video4linux, each attached to the device that really exposes it.

Read-only: nothing opens /dev/videoN (opening even for VIDIOC_QUERYCAP is off-limits while a camera driver or
experiment may hold it). What a node can do comes from the udev database: udev's v4l_id probed it once at
plug time and stored ``ID_V4L_CAPABILITIES`` (``:capture:`` for a stream node, ``:`` for a UVC metadata node).

WHY a source of its own: V6 attached video nodes by sysfs path prefix, so a hub owned every camera node below
it (the C920e's nodes showed under both hubs above it). Here the owner is the NEAREST USB device or PCI
function above the node's interface (``skeleton.physical_owner``): the camera itself, or a PCIe capture card.
"""

from __future__ import annotations

from pathlib import Path

from .skeleton import (ROOT, dev_links, link_name, natural_key, node, physical_owner, read_int, read_text, resolved,
                       split_links, udev_record, uevent)

SYS_V4L = Path("sys/class/video4linux")
NODE_KINDS = (("v4l-subdev", "subdev"), ("v4l-touch", "touch"), ("swradio", "sdr"), ("video", "video"), ("vbi", "vbi"),
              ("radio", "radio"))


def _kind(name: str) -> str:
    return next((kind for prefix, kind in NODE_KINDS if name.startswith(prefix)), "other")


def _capabilities(value: str | None) -> list[str] | None:
    """``":capture:"`` -> ``["capture"]``; ``":"`` -> ``[]`` (no stream: a UVC metadata node); None = never probed."""

    return None if value is None else [part for part in value.split(":") if part]


def v4l_id(name: str, owner, index: int | None) -> str:
    """``v4l:<USB interface or PCI function>#<index>``: udev's by-path recipe, stable when two D405s swap numbers."""

    if owner and index is not None:
        return f"v4l:{owner.interface or owner.name}#{index}"
    return f"v4l:{name}"


def devices(root: Path = ROOT) -> list[dict]:
    """Every video4linux node, parent = its camera / capture card (None for SoC-internal video blocks)."""

    root = Path(root)
    base = root / SYS_V4L
    if not base.is_dir():
        return []
    fallback_links = dev_links(root, "v4l/by-id", "v4l/by-path")
    nodes = []
    for entry in sorted(base.iterdir(), key=lambda path: natural_key(path.name)):
        name = entry.name
        device_dir = resolved(entry / "device")
        owner = physical_owner(device_dir)
        event = uevent(entry)
        dev = f"/dev/{event.get('DEVNAME', name)}"
        major_minor = (read_text(entry / "dev") or "").strip() or None
        record = udev_record(root, major_minor)
        capabilities = _capabilities(record.properties.get("ID_V4L_CAPABILITIES"))
        index = read_int(entry / "index")
        interface_dir = device_dir if owner and owner.interface and device_dir and device_dir.name == owner.interface else None
        attrs = {
            "kind": _kind(name),
            "name": name,
            "dev": dev,
            "major_minor": major_minor,
            "index": index,
            "v4l_name": read_text(entry / "name"),  # the driver's card name; the kernel cuts it at 31 characters
            "capabilities": capabilities,
            "driver": link_name(device_dir / "driver") if device_dir else None,
            "device_bus": owner.bus if owner else None,
            "device": owner.name if owner else None,
            "interface": owner.interface if owner else None,
            "interface_name": read_text(interface_dir / "interface") if interface_dir else None,
            **split_links([*record.links, *fallback_links.get(dev, [])]),
            "sysfs": str(device_dir) if device_dir else None,
            "source": "io_facts.v4l (sysfs + udev database)",
        }
        label = dev + (" · capture" if capabilities and "capture" in capabilities else "")
        nodes.append(node("v4l", v4l_id(name, owner, index), name, label, product=attrs["v4l_name"],
                          parent=owner.id if owner else None, attrs=attrs))
    return nodes

