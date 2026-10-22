"""V1 binding: a declarative manifest shipped INSIDE the driver package (io-diagnostics.json).

The GNOME Shell extension reads only these JSON files to match USB devices in-shell (data, never
plugin code, runs inside gnome-shell, so a broken driver cannot crash the top bar). Diagnostics
run out of process through helper.py, which imports the module the manifest names.

Cost to add a device type: diagnostics.py + a 6-line io-diagnostics.json in its driver package.
(Home Assistant's integration manifests declare USB matchers the same way.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contract.host import DRIVER_SRC_ROOTS, import_object  # noqa: E402


def manifests(src_roots: list[Path] | None = None) -> list[dict]:
    found = []
    for root in src_roots or DRIVER_SRC_ROOTS:
        for path in sorted(root.glob("*/io-diagnostics.json")):
            found.append({**json.loads(path.read_text()), "_path": str(path)})
    return found


def find_drivers(src_roots: list[Path] | None = None) -> list:
    return [import_object(manifest["entry"], src_roots) for manifest in manifests(src_roots)]
