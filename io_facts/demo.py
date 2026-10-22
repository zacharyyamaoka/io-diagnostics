"""The demo world every variant renders: real sysfs devices + the fixture devices the story needs.

Real: whatever ``list_usb_devices()`` sees right now (272813EF and both D405s on 2026-10-21).
Injected, each marked ``simulated=True``: 28B3A25D as it was at 18:11-18:20 (it has since been
unplugged), and the Robotiq 2F-85's FTDI adapter (never on this machine).
"""

from __future__ import annotations

import dataclasses

from . import fixture
from .usb import UsbDevice, list_usb_devices

RACK_IDS = {"0483:5740", "8086:0b5b"}


def _fdcanusb_28b3() -> UsbDevice:
    return UsbDevice(port="1-5", vid="0483", pid="5740", manufacturer="mjbots", product="fdcanusb", serial="28B3A25D",
                     speed_mbps=12, devnum=8, plugged_at="2026-10-21T18:11:39", ttys=("/dev/ttyACM1",),
                     by_id=("/dev/serial/by-id/usb-mjbots_fdcanusb_28B3A25D-if00",), simulated=True)


def _robotiq_ftdi() -> UsbDevice:
    robotiq = fixture.scenario()["new_plugin_device"]
    return UsbDevice(port=robotiq["port"], vid="0403", pid="6015", manufacturer="FTDI", product="USB <-> RS-485",
                     serial=robotiq["serial"], speed_mbps=12, devnum=9, plugged_at="2026-10-21T18:13:05",
                     ttys=("/dev/ttyUSB0",), by_id=(f"/dev/serial/by-id/usb-FTDI_USB_-_RS-485_{robotiq['serial']}-if00-port0",),
                     simulated=True)


def demo_devices(include_robotiq: bool = True) -> list[UsbDevice]:
    scenario = fixture.scenario()
    devices = []
    for device in list_usb_devices():
        if device.usb_id not in RACK_IDS:
            continue
        camera = scenario["cameras"].get(device.serial)
        if camera:
            device = dataclasses.replace(device, extra={"sdk_serial": camera["sdk_serial"]})
        devices.append(device)
    if not any(device.serial == "28B3A25D" for device in devices):
        devices.append(_fdcanusb_28b3())
    if include_robotiq:
        devices.append(_robotiq_ftdi())
    return sorted(devices, key=lambda device: device.port)


def known_state() -> dict[str, dict]:
    """``{usb serial: last known state}``: adapters, cameras and the Robotiq, from the fixture."""

    scenario = fixture.scenario()
    known = {**scenario["adapters"], **scenario["cameras"]}
    robotiq = scenario["new_plugin_device"]
    known[robotiq["serial"]] = {"modbus_ids": robotiq["ids"], "source": robotiq["source"]}
    return known
