"""Utilities the HOST offers on rows whatever driver owns them: ``reset_port`` (docs/usb-reset.md) and ``reset``
(docs/device-reset.md).

A driver registers its own actions on its entities; these are the operating system's. They use the same machinery
end to end (an ``Action`` in the node's ``actions``, a dry run, a token, a RUN on a terminal), the only difference being
who executes: ``io_diagnostics.usb_reset`` and ``io_diagnostics.recovery`` in this checkout, each declared here as a
``PluginSpec`` the host never loads from a file. Same name means the same thing everywhere
(docs/actions-vocabulary-v0.1.md): a driver that registers ``reset_port`` or ``reset`` itself takes the place of this one on
its entity, and a driver opts its devices out in ``describe``::

    {"name": "...", "opt_out": ["reset_port", "reset"]}      # e.g. a device that must not be reset while an experiment runs
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from contract.record import CHANGES, Action
from io_diagnostics import ROOT
from io_diagnostics import usb_reset
from io_diagnostics.plugins import PluginSpec

OWNER = "io_diagnostics"
# WHY long: the reset itself is seconds, but it waits up to RESET_WAIT_S for the device to come back, and a person's
# terminal may sit at a prompt (usbreset never asks, uhubctl may)
ACT_TIMEOUT_S = 90.0

SPEC = PluginSpec(name=OWNER, command=(sys.executable, "-m", "io_diagnostics.usb_reset"), cwd=ROOT,
                  file=Path(usb_reset.__file__).resolve(), act_timeout_s=ACT_TIMEOUT_S, enabled=True)

RESET_PORT = Action(
    name=usb_reset.NAME, title=usb_reset.TITLE,
    description="Disconnect and reconnect this USB port's device (usbreset, else the kernel's authorized switch, else uhubctl).",
    danger="interrupts", scope="device", confirm=usb_reset.CONFIRM,
    preconditions=(("device_present", "A device is plugged in at {port}"),
                   ("no_run_active", "No RobotDx run is active on this device"),
                   ("reset_privilege", "RobotDx may reset it as this user"),
                   ("device_free", "Nothing else has it open")),
    kind=CHANGES)

# WHY long: the ladder may run several rungs (its own reset, usbreset, deauthorize, a power cycle), each with a timeout and a
# settle wait for the device to be back on USB before it is asked again (docs/device-reset.md section 1)
RESET_ACT_TIMEOUT_S = 300.0

RESET_SPEC = PluginSpec(name=OWNER, command=(sys.executable, "-m", "io_diagnostics.recovery"), cwd=ROOT,
                        file=ROOT / "io_diagnostics" / "recovery" / "__main__.py", act_timeout_s=RESET_ACT_TIMEOUT_S,
                        enabled=True)

RESET = Action(
    name="reset", title="Reset…",
    description="Bring this device back: its own reset first, then the USB port's, one step at a time until it answers.",
    danger="interrupts", scope="device",
    confirm="Reset {target}? RobotDx tries the device's own reset first, then the USB port's, one step at a time until it "
            "answers. Whatever has it open (tview, a camera stream, an experiment) loses it.",
    preconditions=(("device_present", "{name} is on USB at {port}"),
                   ("no_run_active", "No RobotDx run is active on this device"),
                   ("host_live", "This host sees the real devices"),
                   ("device_free", "Nothing else has it open")),
    kind=CHANGES)

OFFERED = (RESET_PORT, RESET)
NAMES = tuple(action.name for action in OFFERED)
SPECS = {RESET_PORT.name: SPEC, RESET.name: RESET_SPEC}
# the preconditions the host evaluates itself for these utilities (Host._evaluate_utility)
CHECKS = frozenset({"device_present", "no_run_active", "reset_privilege", "device_free", "host_live"})
BOARD_PATH = re.compile(r"(.+)/can/(\d+)")


def builtin(name: str) -> Action | None:
    return next((action for action in OFFERED if action.name == name), None)


def is_builtin(spec) -> bool:
    return any(spec is mine for mine in SPECS.values())


def usb_target(path: str, layer) -> tuple[dict | None, str] | None:
    """For a USB device row or a USB port row: ``(the skeleton device in it or None, the port name)``. None for any other
    row (a bus, a tty, a camera stream, a CAN board): the utility is not offered there."""

    entity = layer.entities.get(path)
    if entity is None:
        return None
    if entity.kind == "port":
        port = entity.name.removeprefix("Port ")
        device_id = layer.port_devices.get(port)
        return (layer.matchable(device_id) if device_id else None), port
    device_id = layer.device_of_path.get(path)
    device = layer.devices.get(device_id) if device_id else None
    if device is None or device.get("bus") != "usb" or device_id not in layer.physical:
        return None
    port = usb_reset.port_of(device)
    return (layer.matchable(device_id), port) if port else None


def board_target(path: str, layer) -> tuple[dict, str, int] | None:
    """For a CAN board row below a USB adapter (``<adapter row>/can/<id>``, a moteus board): ``(the adapter's skeleton
    device, its port, the board's id)``. None for any other row."""

    found = BOARD_PATH.fullmatch(path)
    if found is None:
        return None
    adapter = layer.entities.get(found[1])
    usb = usb_target(found[1], layer) if adapter is not None and adapter.kind != "port" else None
    if usb is None or usb[0] is None:
        return None
    return usb[0], usb[1], int(found[2])


def row_target(path: str, layer) -> tuple[dict | None, str, int | None] | None:
    """A USB device, port or CAN board row: ``(the skeleton device it resets, the port, the board id or None)``."""

    usb = usb_target(path, layer)
    return (*usb, None) if usb is not None else board_target(path, layer)


def target(name: str, path: str, layer) -> tuple[dict | None, str, int | None] | None:
    """Where the utility ``name`` acts from this row, or None when it is not offered here. ``reset_port``: a USB device or
    port row. ``reset``: a USB device row (never a port row: the device resets, not the socket) or a CAN board row below a
    USB adapter, which resets that board (``--node``) on its adapter."""

    if name == RESET_PORT.name:
        usb = usb_target(path, layer)
        return (*usb, None) if usb is not None else None
    if name == RESET.name:
        entity = layer.entities.get(path)
        if entity is not None and entity.kind == "port":
            return None
        return row_target(path, layer)
    return None


def subtree_device_ids(port: str, layer) -> list[str]:
    """The skeleton devices at ``port`` and below it (a hub takes its children down with it)."""

    ids = []
    for found, device_id in layer.port_devices.items():
        if found == port or found.startswith(port + "."):
            ids.append(device_id)
    return ids
