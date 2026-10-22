"""moteus_servo diagnostics: what IO diagnostics shows for an fdcanusb and the moteus boards on it.

PROPOSED file: lands at src/core/mdp/agent/actuators/moteus_servo/src/moteus_servo/diagnostics.py
(this prototype keeps it in io-diagnostics/drivers_proposed/ and never writes into bam_ws).

It wraps code the driver and its tools already have; nothing here is a parallel implementation:
- adapters: the by-id rule from utils/list_fdcanusb.py (find_fdcanusb_paths / serial_of);
- ids: utils/moteus_find_ids.py (addressed scan, no broadcast burst), gated;
- identity + firmware: moteus_tool --info, the same call the update-moteus-firmware flow records
  in ~/.cache/moteus-firmware/runs/*/run.json (read passively here, with the run's age);
- latest firmware: the release cache ~/.cache/moteus-firmware/releases (offline);
- config: conf enumerate after tel stop (utils/config_probe.py), gated;
- comms: utils/can_bus_triage.py (reply fraction, cycle latency) + `can status` err/warn/tdc
  (new: triage does not read the adapter's error counters today);
- reset: `d reboot` per board (new; no helper exists), confirmed and gated by the host.
"""

from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # io-diagnostics root (prototype only)

from contract.diagnostics import CHANGES, PASSIVE, TOUCHES, Reading, UsbMatch  # noqa: E402

# WHY absolute, not Path.home(): sandboxes and services run with another HOME but read the same flash records.
FIRMWARE_CACHE = Path("/home/bam/.cache/moteus-firmware")
INVENTORY = Path("/archive/datasets/bam_rig/rack_inventory")
DRIVER_PYPROJECT = Path("/home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/core/mdp/agent/actuators/moteus_servo/pyproject.toml")
_RELEASE_ELF = re.compile(r"moteus-fw-(?P<version>[\d.]+)\+g(?P<hash>[0-9a-f]+)\.elf$")


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def releases() -> dict[str, str]:
    """``{git hash prefix: version}`` from the offline release cache."""

    found = {}
    for elf in glob.glob(str(FIRMWARE_CACHE / "releases" / "*" / "moteus-fw-*.elf")):
        if match := _RELEASE_ELF.search(elf):
            found[match["hash"]] = match["version"]
    return found


def flash_records() -> dict[int, dict]:
    """Newest ``moteus_tool --info`` per CAN id, as recorded by the firmware tool's runs (read-only)."""

    newest: dict[int, dict] = {}
    for run in sorted(glob.glob(str(FIRMWARE_CACHE / "runs" / "*" / "run.json"))):
        record = json.loads(Path(run).read_text())
        for target in record.get("targets", []):
            info = target.get("after_info") or target.get("before_info")
            if info and "can_id" in target:
                newest[target["can_id"]] = {**info, "at": record.get("completed_at") or record.get("started_at"), "run": Path(run).parent.name}
    return newest


class MoteusDiagnostics:
    name = "moteus_servo"
    matches = (UsbMatch("0483", "5740"),)
    kinds = {"identity": PASSIVE, "versions": PASSIVE, "config": TOUCHES, "comms": TOUCHES, "reset": CHANGES}
    uses_rack_lock = True

    def nodes(self, device) -> list[str]:
        return list(device.by_id or device.ttys)

    def identity(self, device) -> dict[str, Reading]:
        readings = {"adapter": Reading(f"fdcanusb {device.serial}", "sysfs + /dev/serial/by-id"),
                    "by_id": Reading(device.by_id[0] if device.by_id else device.ttys[0], "/dev/serial/by-id")}
        ids, source, at = self._last_ids(device.serial)
        readings["ids"] = Reading(ids, source, at, "ok" if ids else "warn")
        return readings

    def versions(self, device) -> dict[str, Reading]:
        known = releases()
        latest = max(known.values(), key=_version_tuple) if known else None
        driver = re.search(r'^version\s*=\s*"([^"]+)"', DRIVER_PYPROJECT.read_text(), re.M) if DRIVER_PYPROJECT.exists() else None
        readings = {"driver": Reading(f"moteus_servo {driver[1] if driver else '?'}", "pyproject.toml"),
                    "latest": Reading(latest, "release cache ~/.cache/moteus-firmware/releases")}
        on_this_bus, _source, _at = self._last_ids(device.serial)
        for can_id, info in sorted(flash_records().items()):
            if on_this_bus and can_id not in on_this_bus:
                continue  # flash records span every rig; show only boards last seen on this adapter
            installed = next((version for prefix, version in known.items() if info["git_hash"].startswith(prefix)), info["git_hash"][:10])
            behind = latest is not None and _version_tuple(installed) < _version_tuple(latest)
            readings[f"CAN {can_id}"] = Reading(
                {"firmware": installed, "serial": info["serial_number"], "update_available": behind},
                f"moteus_tool --info, firmware run {info['run'][:15]}", info["at"], "warn" if behind else "ok")
        return readings

    def config(self, device) -> dict[str, Reading]:  # TOUCHES: only ever called through the gate
        raise RuntimeError("config touches the bus; call it through contract.diagnose")

    def comms(self, device) -> dict[str, Reading]:  # TOUCHES
        raise RuntimeError("comms touches the bus; call it through contract.diagnose")

    def reset(self, device, *, confirm: bool) -> Reading:  # CHANGES
        raise RuntimeError("reset changes state; call it through contract.request_reset")

    # ----- last known values, shown while a touching section is blocked or not run -------------
    def _last_ids(self, adapter_serial: str) -> tuple[list[int], str, str]:
        for snapshot in sorted(INVENTORY.glob("2026-*.json"), reverse=True)[:40]:
            adapters = json.loads(snapshot.read_text()).get("adapters", {})
            value = adapters.get(adapter_serial)
            if isinstance(value, dict) and "skipped" not in value:
                return sorted(int(can_id) for can_id in value), f"rack_inventory {snapshot.stem}", snapshot.stem
        return [], "no inventory snapshot has this adapter", ""

    def last_known_config(self, device) -> dict[str, Reading]:
        for snapshot in sorted(INVENTORY.glob("2026-*.json"), reverse=True)[:40]:
            boards = json.loads(snapshot.read_text()).get("adapters", {}).get(device.serial)
            if isinstance(boards, dict) and boards and "skipped" not in boards:
                return {f"CAN {can_id}": Reading({key: value for key, value in board.items() if "." in key},
                                                 f"rack_inventory {snapshot.stem}", snapshot.stem)
                        for can_id, board in boards.items()}
        return {}


DIAGNOSTICS = MoteusDiagnostics()
