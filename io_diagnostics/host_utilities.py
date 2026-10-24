"""Utilities the HOST offers on rows whatever driver owns them (docs/usb-reset.md): today ``reset_port``.

A driver registers its own actions on its entities; these are the operating system's. They use the same machinery
end to end (an ``Action`` in the node's ``actions``, a dry run, a token, a RUN on a terminal), the only difference being
who executes: ``io_diagnostics.usb_reset`` in this checkout, declared here as a ``PluginSpec`` the host never loads from a
file. Same name means the same thing everywhere (docs/actions-vocabulary-v0.1.md): a driver that registers ``reset_port``
itself takes the place of this one on its entity, and a driver opts its devices out in ``describe``::

    {"name": "...", "opt_out": ["reset_port"]}      # e.g. a device that must not be reset while an experiment runs
"""

from __future__ import annotations

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

OFFERED = (RESET_PORT,)
NAMES = tuple(action.name for action in OFFERED)
# the preconditions the host evaluates itself for these utilities (Host._evaluate_utility)
CHECKS = frozenset({"device_present", "no_run_active", "reset_privilege", "device_free"})


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


def subtree_device_ids(port: str, layer) -> list[str]:
    """The skeleton devices at ``port`` and below it (a hub takes its children down with it)."""

    ids = []
    for found, device_id in layer.port_devices.items():
        if found == port or found.startswith(port + "."):
            ids.append(device_id)
    return ids
