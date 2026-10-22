"""Fixture plugin: replays the DRIVER side of V6's recorded frames (v6_frames.json), as a real plugin subprocess.

    <python> v6_scenario.py describe
    <python> v6_scenario.py diagnose --device usb:1-3        (frame from IO_DIAGNOSTICS_FIXTURE, set by the host)

It speaks the plugin boundary exactly like a live driver (docs/build-plan-v1.md, plugins.d/README.md): JSON lines,
one v0 record each, paths relative to the device. It is declared ``replay = true``, so the host keeps the receive
times the recording carries (``received``) instead of stamping its own; records without one (a live publisher such
as a stream's frame rate, or a check the driver repeats such as PortBusy) get the host's clock like any live record.
Stdlib only, so it runs under whichever python the host runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

FIXTURE = Path(os.environ.get("IO_DIAGNOSTICS_FIXTURE_FILE") or Path(__file__).with_name("v6_frames.json"))
SCHEMA = "diagnostics-record/0"


def main() -> int:
    parser = argparse.ArgumentParser(prog="v6_scenario")
    parser.add_argument("verb", choices=["describe", "diagnose", "act"])
    parser.add_argument("--device", default="")
    parser.add_argument("--frame", default=os.environ.get("IO_DIAGNOSTICS_FIXTURE", "F1"))
    parser.add_argument("--action", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.verb == "describe":
        print(json.dumps({"name": "v6_scenario", "version": "1", "schema": SCHEMA,
                          "matches": ["usb:0483:5740", "usb:8086:0b5b"], "actions": []}))
        return 0
    if args.verb == "act":  # the host never calls act in v1; answer like a driver that refuses
        print(json.dumps({"schema": SCHEMA, "record": "event", "code": "ActionRefused", "severity": "warn",
                          "message": "fixture plugin: actions never execute", "stamp": float(os.environ.get("IO_DIAGNOSTICS_NOW", "0"))}))
        return 3
    document = json.loads(FIXTURE.read_text())
    frame = document["frames"].get(args.frame)
    if frame is None:
        print(f"unknown frame {args.frame!r}", file=sys.stderr)
        return 2
    now = float(os.environ.get("IO_DIAGNOSTICS_NOW", frame["clock"]))
    for line in frame["plugin_records"].get(args.device, []):
        if line.get("record") == "event" and (line.get("stamp") or 0) > now:
            continue  # the recording never shows the future
        print(json.dumps(line, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
