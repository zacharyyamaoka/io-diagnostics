"""V4 registry daemon: the registry document IS the product; the top bar and dashboard are views of it.

    /usr/bin/python3 /home/bam/io-diagnostics/prototypes/v4-registry/registryd.py --frame F3 --out /home/bam/io-diagnostics/out/io-diagnostics.json

Writes one versioned JSON document (schema io-diagnostics/1) atomically (write + rename), so any
reader (the rack session runner, an agent, the dashboard) sees a whole snapshot. In production it
lives at $XDG_RUNTIME_DIR/io-diagnostics.json and is rewritten on every udev event; this prototype
writes into out/ and never runs as a service.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from contract.views import EVENTS, FRAMES, views  # noqa: E402

SCHEMA = "io-diagnostics/1"


def document(frame: str) -> dict:
    devices = []
    for view in views(frame):
        record = dataclasses.asdict(view)
        record["by_id"] = next((row["value"] for row in record["sections"].get("identity", []) if row["label"] == "by id"), None)
        devices.append(record)
    return {"schema": SCHEMA, "updated": f"2026-10-21T{FRAMES[frame]['clock']}:00", "frame": frame,
            "devices": devices, "events": [dict(zip(("time", "kind", "text", "tag"), event)) for event in EVENTS[frame]],
            "drivers": ["moteus_servo", "intel_rgbd", "obsbot_ptz_camera"]}


def write_atomically(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=1))
    os.replace(temporary, path)  # WHY rename: readers never see half a document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", default="F3", choices=list(FRAMES))
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "io-diagnostics.json")
    args = parser.parse_args()
    write_atomically(args.out, document(args.frame))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
