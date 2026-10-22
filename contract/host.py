"""The thin host every direction shares: match plugged-in devices to drivers, diagnose, list leftovers.

How a direction FINDS drivers differs (manifest, naming convention, CLI, entry points, station
file); that lives in each prototype's binding.py. Everything after that is this file.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contract.diagnostics import Report, diagnose  # noqa: E402

DRIVER_SRC_ROOTS = [ROOT / "drivers_proposed" / name / "src" for name in ("moteus_servo", "intel_rgbd", "obsbot_ptz_camera")]


@dataclass
class Scan:
    reports: list[Report] = field(default_factory=list)
    unrecognised: list = field(default_factory=list)  # no driver claims it: the cue to add diagnostics to its driver


def import_object(reference: str, src_roots: list[Path] | None = None):
    """``"moteus_servo.diagnostics:DIAGNOSTICS"`` -> the object (prototype: from drivers_proposed)."""

    for root in src_roots or DRIVER_SRC_ROOTS:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    module_name, _, attribute = reference.partition(":")
    return getattr(importlib.import_module(module_name), attribute or "DIAGNOSTICS")


def scan(devices: list, drivers: list, gate_options: dict | None = None) -> Scan:
    result = Scan()
    for device in devices:
        driver = next((candidate for candidate in drivers if any(match.accepts(device) for match in candidate.matches)), None)
        if driver is None:
            result.unrecognised.append(device)
        else:
            result.reports.append(diagnose(driver, device, gate_options=gate_options))
    return result
