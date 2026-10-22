"""The repo's existing, read-only hardware checks, imported rather than rewritten.

WHY import (not copy): Zach's off-the-shelf-first rule, and these encode traps already paid for:
``find_port_holders`` (pyserial opens the fdcanusb without a lock, so a second reader silently
steals bytes) and ``camera_holders`` (never take a RealSense another process holds; a shell that
merely mentions realsense-viewer is not a viewer). Each import records where it came from so a
snapshot can say which code decided "busy". If a checkout is missing, the function reports
itself unavailable instead of guessing "free".
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

WORKTREE = Path("/home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack")
MAIN = Path("/home/bam/bam_ws")
MOTEUS_UTILS = "src/core/mdp/agent/actuators/moteus_servo/utils"
MOTEUS_SRC = "src/core/mdp/agent/actuators/moteus_servo/src"
RACK_TWIN = "src/dev/bam_rack_twin"


@dataclass
class Imported:
    function: Callable | None
    source: str  # file it came from, or why it is unavailable


def _import_from(module: str, attribute: str, search_paths: list[Path]) -> Imported:
    # WHY set aside same-named packages: a proposed driver module on sys.path (a moteus_servo
    # namespace package holding only diagnostics.py) would shadow the real moteus_servo that
    # config_probe imports; the gate would then say "unknown" for every adapter.
    shadowed = {name: sys.modules.pop(name) for name in list(sys.modules)
                if name.split(".")[0] in ("moteus_servo", "bam_rack_twin")}
    try:
        return _import_first(module, attribute, search_paths)
    finally:
        for name in [name for name in sys.modules if name.split(".")[0] in ("moteus_servo", "bam_rack_twin")]:
            sys.modules.pop(name, None)
        sys.modules.update(shadowed)


def _import_first(module: str, attribute: str, search_paths: list[Path]) -> Imported:
    for path in search_paths:
        if not path.is_dir():
            continue
        sys.path.insert(0, str(path))
        try:
            loaded = importlib.import_module(module)
            return Imported(getattr(loaded, attribute), f"{Path(loaded.__file__)}::{attribute}")
        except Exception as error:  # noqa: BLE001 - an unimportable checkout is "unavailable", never "free"
            last_error = f"{type(error).__name__}: {error}"
        finally:
            sys.path.remove(str(path))
            sys.modules.pop(module, None)
    return Imported(None, f"unavailable ({locals().get('last_error', 'no checkout found')})")


def port_holders_function() -> Imported:
    """``config_probe.find_port_holders``: ``pid COMMAND`` of every process with the port open."""

    for root in (WORKTREE, MAIN):
        # the real package must win over any proposed-driver namespace package already on sys.path
        sys.path.insert(0, str(root / MOTEUS_SRC))
        try:
            found = _import_from("config_probe", "find_port_holders", [root / MOTEUS_UTILS])
        finally:
            sys.path.remove(str(root / MOTEUS_SRC))
        if found.function:
            return found
    return found


def camera_holders_function() -> Imported:
    """``bam_rack_twin.hardware.record.camera_holders``: other processes holding a RealSense node."""

    found = _import_from("bam_rack_twin.hardware.record", "camera_holders", [WORKTREE / RACK_TWIN, MAIN / RACK_TWIN])
    for name in [name for name in sys.modules if name.startswith("bam_rack_twin")]:
        sys.modules.pop(name, None)
    return found


def active_scan_commands(adapter_by_id: str) -> dict[str, list[str]]:
    """The read-only active checks the gate would run (never run in this prototype round).

    ``moteus_find_ids`` does an addressed id scan (no broadcast burst); ``can_bus_triage`` reads
    bus health with addressed queries. Both already wrap their transport in the frame-budget guard.
    """

    utils = WORKTREE / MOTEUS_UTILS
    return {
        "ids": ["python3", str(utils / "moteus_find_ids.py"), "--path", adapter_by_id, "--range", "1", "126", "--quiet"],
        "health": ["python3", str(utils / "can_bus_triage.py"), "--path", adapter_by_id, "--json"],
    }
