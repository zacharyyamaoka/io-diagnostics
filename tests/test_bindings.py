"""FR2 evidence: in every direction, adding a device type is ONLY its driver's binding unit.

For each of the five discovery mechanisms, the same OBSBOT (driver exists, camera simulated) is
unrecognised without its unit and diagnosed with it; no host or UI file changes between the two.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contract.host import scan  # noqa: E402
from io_facts import demo  # noqa: E402

PROTOTYPES = ROOT / "prototypes"


def load(variant: str):
    name = f"binding_{variant.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, PROTOTYPES / variant / "binding.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def obsbot_recognised_python(drivers) -> bool:
    result = scan([device for device in demo.demo_devices() if device.vid == "3564"], drivers)
    return bool(result.reports) and not result.unrecognised


def copy_drivers(tmp_path: Path, drop: str | None) -> list[Path]:
    roots = []
    for package in ("moteus_servo", "intel_rgbd", "obsbot_ptz_camera"):
        source = ROOT / "drivers_proposed" / package / "src"
        target = tmp_path / package / "src"
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        if drop and package == "obsbot_ptz_camera":
            (target / package / drop).unlink()
        roots.append(target)
    return roots


@pytest.mark.parametrize("variant, unit", [("v1-quick-settings", "io-diagnostics.json"), ("v2-plug-journal", "diagnostics.py")])
def test_source_tree_bindings(tmp_path, variant, unit):
    binding = load(variant)
    without = binding.find_drivers(copy_drivers(tmp_path / "without", drop=unit))
    with_unit = binding.find_drivers(copy_drivers(tmp_path / "with", drop=None))
    assert "obsbot_ptz_camera" not in [driver.name for driver in without]
    assert not obsbot_recognised_python(without)
    assert obsbot_recognised_python(with_unit)


def test_cli_binding(tmp_path):
    binding = load("v3-hub-row")
    shutil.copytree(PROTOTYPES / "v3-hub-row" / "bin", tmp_path / "bin")
    (tmp_path / "bin" / "obsbot_ptz_camera-diagnostics").unlink()
    assert "obsbot_ptz_camera" not in [driver["name"] for driver in binding.find_drivers([tmp_path / "bin"])]
    found = {driver["name"]: driver for driver in binding.find_drivers()}
    code, report = binding.diagnose(Path(found["obsbot_ptz_camera"]["executable"]), "7-1.2")
    assert report["driver"] == "obsbot_ptz_camera" and code in (0, 1, 3)


def test_entry_point_binding(tmp_path):
    binding = load("v4-registry")
    site = PROTOTYPES / "v4-registry" / "site"
    shutil.copytree(site, tmp_path / "site", ignore=shutil.ignore_patterns("obsbot_ptz_camera-*"))
    sys.path[:] = [path for path in sys.path if path != str(site)]
    without = binding.find_drivers(tmp_path / "site")
    sys.path.remove(str(tmp_path / "site"))
    assert "obsbot_ptz_camera" not in [driver.name for driver in without]
    with_unit = binding.find_drivers(site)
    assert obsbot_recognised_python(with_unit)


def test_station_binding(tmp_path, monkeypatch):
    # WHY no live USB: demo_devices() adds whatever rack device is plugged in, so a new adapter (0607623A, 2026-10-23)
    # turned up as unassigned and failed this ruler; the recorded scenario devices are the fixed input.
    monkeypatch.setattr(demo, "list_usb_devices", lambda: [])
    binding = load("v5-station")
    text = (PROTOTYPES / "v5-station" / "station.toml").read_text()
    without = tmp_path / "station.toml"
    without.write_text(text.split("# Adding the OBSBOT")[0])
    _slots, unassigned = binding.check(demo.demo_devices(), {}, without)
    assert [device.vid for device in unassigned] == ["3564"]
    slots, unassigned = binding.check(demo.demo_devices(), {})
    assert not unassigned and any(slot.name == "Cell monitor PTZ" and slot.present for slot in slots)


def test_gate_is_decided_not_unknown_with_proposed_drivers_on_path():
    drivers = load("v2-plug-journal").find_drivers()
    adapter = [device for device in demo.demo_devices() if device.serial == "272813EF"]
    if not adapter:
        pytest.skip("272813EF not plugged in")
    report = scan(adapter, drivers).reports[0]
    assert report.gate.state in ("free", "blocked"), report.gate.reason
