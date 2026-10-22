"""V5 drift: expected (station.toml) vs present (USB + each driver's diagnostics), per story frame.

    /usr/bin/python3 /home/bam/io-diagnostics/prototypes/v5-station/drift.py --frame F3
"""

from __future__ import annotations

import argparse
import tempfile
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from contract.prototypes_loader import binding  # noqa: E402
from contract.views import views  # noqa: E402
from io_facts import demo  # noqa: E402

# Ids that answer in each frame: F1/F2 from the rack_inventory snapshots, F3 reported after the restart.
ANSWERING = {"F1": {"272813EF": [1, 14, 15], "28B3A25D": []}, "F2": {"272813EF": [1, 14, 15]}, "F3": {"272813EF": []}}


def drift(frame: str) -> tuple[list, list[str]]:
    flags = {"F1": (True, False), "F2": (False, False), "F3": (False, True)}[frame]
    devices = demo.demo_devices(include_28b3=flags[0], include_obsbot=flags[1])
    if not flags[0]:
        devices = [device for device in devices if device.serial != "28B3A25D"]
    station = HERE / "station.toml"
    if frame != "F3":  # the PTZ slot is the 19:02 addition; before that the station did not expect it
        station = Path(tempfile.mkdtemp()) / "station.toml"
        station.write_text((HERE / "station.toml").read_text().split("# Adding the OBSBOT")[0])
    slots, unassigned = binding("v5-station").check(devices, ANSWERING[frame], station)
    by_key = {view.key: view for view in views(frame)}
    lines = []
    for slot in slots:
        lines += [f"{slot.name}: {text}" for text in slot.drift]
        view = by_key.get(slot.device.serial) if slot.device else None
        if view and view.gate == "blocked":
            lines.append(f"{slot.name}: checks wait, {view.gate_reason}")
        elif view and not slot.drift:
            lines += [f"{slot.name}: {text.split(' · ', 1)[-1]}" for _id, text, level in view.ids if level == "error"]
        if view and view.level == "warn" and "moved" in view.headline:
            lines.append(f"{slot.name}: {view.headline}")
    lines += [f"unassigned: {device.usb_id} {device.product} on {device.port}" for device in unassigned]
    return slots, lines


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame", default="F3")
    for line in drift(parser.parse_args().frame)[1]:
        print(line)
