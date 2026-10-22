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


def _obsbot() -> UsbDevice:
    """The worked "add a device type" example (A2): its driver exists, the camera is not plugged in."""

    return UsbDevice(port="7-1.2", vid="3564", pid="fef4", manufacturer="Remo Tech Co., Ltd.", product="OBSBOT Tiny 4K",
                     serial="RMOWAFCB3A2", speed_mbps=480, devnum=10, plugged_at="2026-10-21T19:02:10",
                     video_nodes=("/dev/video12", "/dev/video13"), simulated=True, extra={"bcd_device": "1.00"})


def demo_devices(include_robotiq: bool = False, include_obsbot: bool = True, include_28b3: bool = True) -> list[UsbDevice]:
    scenario = fixture.scenario()
    devices = []
    for device in list_usb_devices():
        if device.usb_id not in RACK_IDS:
            continue
        camera = scenario["cameras"].get(device.serial)
        if camera:
            device = dataclasses.replace(device, extra={"sdk_serial": camera["sdk_serial"]})
        devices.append(device)
    if include_28b3 and not any(device.serial == "28B3A25D" for device in devices):
        devices.append(_fdcanusb_28b3())
    if include_obsbot:
        devices.append(_obsbot())
    return sorted(devices, key=lambda device: device.port)


def known_state() -> dict[str, dict]:
    """``{usb serial: last known state}``: adapters and cameras, from the fixture."""

    scenario = fixture.scenario()
    return {**scenario["adapters"], **scenario["cameras"]}
