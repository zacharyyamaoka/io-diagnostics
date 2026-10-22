"""V2 binding: naming convention. Any driver package with ``<pkg>/diagnostics.py`` defining
``DIAGNOSTICS`` is found by scanning the source roots; no metadata, no registration.

Cost to add a device type: that one file in its driver package. (Python packaging guide's first
plugin pattern, "naming convention".)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contract.host import DRIVER_SRC_ROOTS, import_object  # noqa: E402


def find_drivers(src_roots: list[Path] | None = None) -> list:
    roots = src_roots or DRIVER_SRC_ROOTS
    drivers = []
    for root in roots:
        for module in sorted(root.glob("*/diagnostics.py")):
            if "DIAGNOSTICS" in module.read_text():
                drivers.append(import_object(f"{module.parent.name}.diagnostics:DIAGNOSTICS", roots))
    return drivers
