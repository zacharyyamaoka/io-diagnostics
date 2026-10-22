"""V4 binding: packaging entry points, group ``io_diagnostics.drivers`` (stock importlib.metadata).

A driver declares, in its own pyproject.toml:

    [project.entry-points."io_diagnostics.drivers"]
    obsbot_ptz_camera = "obsbot_ptz_camera.diagnostics:DIAGNOSTICS"

and is found once it is installed in the registry's environment (uv add / uv sync). The same
mechanism pytest uses for its plugins. Prototype: ``site/`` holds the dist-info that an install
would write, so the real importlib.metadata discovery is exercised without installing anything.

Cost to add a device type: diagnostics.py + 2 lines of pyproject + an install.
"""

from __future__ import annotations

import sys
from importlib.metadata import entry_points
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from contract.host import import_object  # noqa: E402

GROUP = "io_diagnostics.drivers"


def find_drivers(site_dir: Path = HERE / "site") -> list:
    if str(site_dir) not in sys.path:
        sys.path.insert(0, str(site_dir))
    return [import_object(entry.value) for entry in sorted(entry_points(group=GROUP), key=lambda entry: entry.name)]
