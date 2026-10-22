"""Serial ttys with a real device behind them, from /sys/class/tty, on the USB or PCI device that exposes them.

Read-only. Kept: ttyACM (CDC-ACM, e.g. the mjbots fdcanusb), ttyUSB (usb-serial: FTDI, CP210x, CH34x), ttyS
with a UART actually present, and SoC UARTs (ttyAMA, ttyTHS ...). Skipped: virtual consoles, pty, console and
ptmx (no ``device`` link), and the 8250 driver's placeholder ttyS ports (``type`` 0 = no UART at that address;
every PC lists ttyS0-31 whether or not a COM port exists).

Each node carries its stable links: /dev/serial/by-id (serial number based, the name to put in configs),
/dev/serial/by-path and custom udev links such as /dev/fdcanusb, read from the udev database or /dev.
"""

from __future__ import annotations

from pathlib import Path

from .skeleton import (ROOT, dev_links, link_name, natural_key, node, physical_owner, read_int, read_text, resolved,
                       split_links, udev_record, uevent)

SYS_TTY = Path("sys/class/tty")
UART_UNKNOWN = 0  # include/uapi/linux/serial_core.h PORT_UNKNOWN: the 8250 driver found no UART there


def _uart(entry: Path) -> dict | None:
    if not (entry / "type").exists():
        return None
    return {"type": read_int(entry / "type"), "port": read_text(entry / "port"), "irq": read_int(entry / "irq"),
            "uartclk": read_int(entry / "uartclk")}


def tty_id(name: str, device_dir: Path, owner) -> str:
    """``tty:<USB interface>`` (``#<port>`` for multi-port usb-serial) so ttyACM0/1 swapping on replug keeps ids.

    WHY not the kernel name for USB ttys: two fdcanusbs take ttyACM0/ttyACM1 in enumeration order, which changes
    across replugs and reboots; the interface path is what udev's by-path link is built from. ttyS numbers are
    fixed by I/O address, so they keep their kernel name.
    """

    if owner and owner.bus == "usb" and owner.interface:
        port_number = read_int(device_dir / "port_number")  # usb-serial port device; absent for cdc_acm
        return f"tty:{owner.interface}" + (f"#{port_number}" if port_number else "")
    return f"tty:{name}"


def devices(root: Path = ROOT) -> list[dict]:
    """Serial ttys with a real device, parent = the USB device or PCI function exposing them (None on-board)."""

    root = Path(root)
    base = root / SYS_TTY
    if not base.is_dir():
        return []
    fallback_links = dev_links(root, "serial/by-id", "serial/by-path")
    nodes = []
    for entry in sorted(base.iterdir(), key=lambda path: natural_key(path.name)):
        device_dir = resolved(entry / "device")
        if device_dir is None or "/devices/virtual/" in str(device_dir):
            continue
        uart = _uart(entry)
        if uart and uart["type"] == UART_UNKNOWN:
            continue
        name = entry.name
        event = uevent(entry)
        dev = f"/dev/{event.get('DEVNAME', name)}"
        major_minor = (read_text(entry / "dev") or "").strip() or None
        record = udev_record(root, major_minor)
        links = split_links([*record.links, *fallback_links.get(dev, [])])
        owner = physical_owner(device_dir)
        interface_dir = next((path for path in (device_dir, *device_dir.parents) if owner and path.name == owner.interface), None)
        attrs = {
            "kind": "serial",
            "name": name,
            "dev": dev,
            "major_minor": major_minor,
            # cdc_acm binds the interface; usb-serial drivers (ftdi_sio, cp210x) bind the ttyUSB port device
            "driver": link_name(device_dir / "driver") or (link_name(interface_dir / "driver") if interface_dir else None),
            "device_bus": owner.bus if owner else None,
            "device": owner.name if owner else None,
            "interface": owner.interface if owner else None,
            "interface_name": read_text(interface_dir / "interface") if interface_dir else None,
            **links,
            "uart": uart,
            "sysfs": str(device_dir),
            "source": "io_facts.tty (sysfs + udev database)",
        }
        nodes.append(node("tty", tty_id(name, device_dir, owner), name, dev, parent=owner.id if owner else None, attrs=attrs))
    return nodes
