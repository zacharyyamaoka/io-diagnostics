"""V5 binding: the station file declares which driver diagnoses each slot, and what to expect.

Discovery still runs (an undeclared device shows as "unassigned"), but the primary object is
the slot: drift between expected and present is what the top bar counts.
Cost to add a device type: diagnostics.py in its driver + a 4-line [[slot]].
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from contract.diagnostics import diagnose  # noqa: E402
from contract.host import import_object  # noqa: E402


@dataclass
class SlotState:
    name: str
    present: bool
    device: object = None
    report: object = None
    drift: list[str] = field(default_factory=list)


def load(station_file: Path = HERE / "station.toml") -> dict:
    return tomllib.loads(station_file.read_text())


def find_drivers(station_file: Path = HERE / "station.toml") -> list:
    seen, drivers = set(), []
    for slot in load(station_file)["slot"]:
        if slot["driver"] not in seen:
            seen.add(slot["driver"])
            drivers.append(import_object(slot["driver"]))
    return drivers


def _matches(rule: dict, device) -> bool:
    return all(getattr(device, key) == value for key, value in rule.items())


def check(devices: list, known_ids: dict[str, list[int]], station_file: Path = HERE / "station.toml") -> tuple[list[SlotState], list]:
    """Slots with their drift lines, plus devices no slot claims ("unassigned")."""

    station = load(station_file)
    states, claimed = [], set()
    for slot in station["slot"]:
        device = next((device for device in devices if _matches(slot["match"], device)), None)
        state = SlotState(slot["name"], device is not None, device)
        expect = slot.get("expect", {})
        if device is None:
            state.drift.append("expected here, not plugged in")
        else:
            claimed.add(device.port)
            state.report = diagnose(import_object(slot["driver"]), device)
            ids = known_ids.get(device.serial)
            if "ids" in expect and ids is not None and sorted(ids) != sorted(expect["ids"]):
                missing = sorted(set(expect["ids"]) - set(ids))
                state.drift.append(f"{len(ids)} of {len(expect['ids'])} boards answer" + (f" (missing {', '.join(map(str, missing))})" if missing else ""))
            if "count" in expect and ids is not None and len(ids) < expect["count"]:
                state.drift.append(f"{len(ids)} of {expect['count']} boards answer")
        states.append(state)
    return states, [device for device in devices if device.port not in claimed]
