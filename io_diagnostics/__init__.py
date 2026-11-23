"""IO diagnostics v1: one host process that turns the OS skeleton and driver plugins into one entity tree.

    uv run --project /home/bam/io-diagnostics python /home/bam/io-diagnostics/io_diagnostics/cli.py serve

- ``plugins``  loads ``plugins.d/*.toml`` and matches OS skeleton devices to plugins.
- ``runner``   runs a plugin subprocess per matched device, parses its JSON lines into ``contract.record``
               types, flags anything malformed as a host check on the device row, never crashes.
- ``tree``     the OS skeleton (``io_facts``) + plugin children with two parent edges, and the host rules from
               ``contract.record`` (``summarize``: black boxes self-report, rollup is a hint; STALE derived on
               the host receive clock; knock-on collapse).
- ``faults``   a thin use of ``contract.record.FaultManager``.
- ``activity`` plug / unplug / re-enumerate from the kernel log (``io_facts.journal``), fault transitions,
               link health.
- ``host``     ties them together on one clock; ``server`` serves it (``docs/api-v1.md``); ``tray`` is the
               top-bar chip; ``cli`` is ``io-diagnostics [serve|tray|once --json]``.

Stdlib only, and Python 3.12-compatible on purpose: the tray needs the system python's ``gi`` (GTK,
AppIndicator), which the project's uv environment does not have, so the same package must run under both.
Nothing here opens a CAN adapter or a camera; actions are shown, never executed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).resolve().parent
WEB = PACKAGE / "web"
PLUGINS_DIR = ROOT / "plugins.d"
FIXTURE_PLUGINS_DIR = ROOT / "tests" / "fixtures" / "plugins"
API = "io-diagnostics/1"

# WHY a path bootstrap: contract/ and io_facts/ are top-level packages beside this one and the project is not
# installed (no build backend yet), so a script entry point must find them from any working directory.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _find_robot_diagnostics() -> None:
    """Put the robot-diagnostics library on the path when no environment installed it.

    WHY (2026-11-23, the library split out of this repo into /home/bam/robot_diagnostics): Stable and Preview run this checkout (or its
    snapshot) under /usr/bin/python3 with nothing installed, as contract/ and io_facts/ above. In a uv environment the package is
    installed and this does nothing. Otherwise: $ROBOT_DIAGNOSTICS_SRC, else the sibling checkout, else ~/robot_diagnostics (a Stable
    snapshot has no sibling)."""

    import importlib.util
    import os

    if importlib.util.find_spec("robot_diagnostics") is not None:
        return
    candidates = [os.environ.get("ROBOT_DIAGNOSTICS_SRC", ""), str(ROOT.parent / "robot_diagnostics" / "src"),
                  str(Path.home() / "robot_diagnostics" / "src")]
    for candidate in candidates:
        if candidate and (Path(candidate) / "robot_diagnostics" / "__init__.py").is_file():
            sys.path.append(candidate)
            return


_find_robot_diagnostics()

__all__ = ["ROOT", "PACKAGE", "WEB", "PLUGINS_DIR", "FIXTURE_PLUGINS_DIR", "API"]
