"""Is it healthy? The ladder asks before the first rung (detect) and after each one (did that cure it?).

``(True, words)`` answers · ``(False, words)`` does not · ``(None, words)`` could not be asked without disturbing whoever has it
open: the owner judges then (it said so with --detected, or it is told to reopen and check).
"""

from __future__ import annotations

from pathlib import Path

from io_diagnostics.recovery import device as devices
from io_diagnostics.recovery import mechanisms
from io_diagnostics.recovery.profile import PROBE_PLUGIN, PROBE_PRESENT, PROBE_TTY, PROBE_V4L2, RecoveryProfile
from io_diagnostics.recovery.system import Seams

TTY_REPLY_S = 1.0  # the moteus_servo driver gives `can status` 1 s (CAN_STATUS_DEADLINE_S)
QUERYCAP_S = 2.0
OWNER_PROBE_S = 20.0


def probe(profile: RecoveryProfile, device: devices.Device, seams: Seams, *, holders: list | None = None,
          probe_command: list[str] | None = None, plugins_dir: Path | None = None, skeleton: dict | None = None
          ) -> tuple[bool | None, str]:
    if device.is_can:
        if not device.present:
            return False, f"its adapter fdcanusb {device.serial} is not on USB"
        if probe_command:
            return _owner(probe_command, seams)
        if profile.probe == PROBE_PLUGIN:
            return mechanisms.plugin_check(device, seams, plugins_dir, 60.0, skeleton)
        return None, "no way to ask a CAN board without its driver"
    ok, words = devices.present_now(device, profile.expects_nodes)
    if not ok:
        return False, words
    if probe_command:
        return _owner(probe_command, seams)
    if profile.probe == PROBE_PRESENT:
        return True, words
    if profile.probe == PROBE_TTY:
        nodes = devices.nodes_of(device.port, "tty")
        if not nodes:
            return False, f"{device.name} has no tty"
        busy = [holder for holder in (holders or []) if holder.node == nodes[0]]
        if busy:
            # WHY not asked: a line written into someone else's open session corrupts its traffic
            return None, f"{busy[0].program} (pid {busy[0].pid}) has {nodes[0]} open: not asked; it judges whether it answers"
        return seams.tty_ask(nodes[0], TTY_REPLY_S)
    if profile.probe == PROBE_V4L2:
        nodes = devices.nodes_of(device.port, "video4linux")
        if not nodes:
            return False, f"{device.name} has no video node"
        return seams.querycap(nodes[0], QUERYCAP_S)
    return None, f"unknown probe {profile.probe}"


def _owner(command: list[str], seams: Seams) -> tuple[bool | None, str]:
    code, output = seams.run(command, OWNER_PROBE_S, None)
    last = output.strip().splitlines()[-1] if output.strip() else ""
    if code is None:
        return False, f"the owner's probe ran over {OWNER_PROBE_S:g} s"
    return code == 0, f"the owner's probe exited {code}" + (f": {last[:120]}" if last else "")
