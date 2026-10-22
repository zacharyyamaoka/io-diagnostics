"""The CLI face of a driver's diagnostics (V3 calls drivers only through this).

    <pkg>-diagnostics describe            -> {"name", "matches", "kinds"}
    <pkg>-diagnostics diagnose --port 1-3 -> the report as JSON

Exit codes follow the Monitoring Plugins API (Nagios, 1999-): 0 OK, 1 WARNING, 2 CRITICAL,
3 UNKNOWN. That convention is what lets a vendor's own tool, in any language, join through a
10-line wrapper script instead of a Python driver.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract.diagnostics import diagnose  # noqa: E402
from io_facts import demo, usb  # noqa: E402

EXIT = {"ok": 0, "not run": 0, "warn": 1, "blocked": 1, "error": 2, "unknown": 3}


def _device(port: str, include_fixture: bool):
    devices = demo.demo_devices() if include_fixture else usb.list_usb_devices()
    return next(device for device in devices if device.port == port)


def run(driver) -> int:
    parser = argparse.ArgumentParser(prog=f"{driver.name}-diagnostics")
    parser.add_argument("verb", choices=["describe", "diagnose"])
    parser.add_argument("--port")
    parser.add_argument("--fixture", action="store_true", help="include the story's simulated devices")
    args = parser.parse_args()
    if args.verb == "describe":
        print(json.dumps({"name": driver.name, "matches": [dataclasses.asdict(match) for match in driver.matches], "kinds": driver.kinds}))
        return 0
    device = _device(args.port, args.fixture)
    if not any(match.accepts(device) for match in driver.matches):
        print(json.dumps({"driver": driver.name, "port": args.port, "error": f"{device.usb_id} is not a {driver.name} device"}))
        return 3
    report = diagnose(driver, device)
    sections = {name: {key: dataclasses.asdict(reading) for key, reading in values.items()} for name, values in report.sections.items()}
    states = [reading["state"] for values in sections.values() for reading in values.values()]
    print(json.dumps({"driver": report.driver, "port": args.port, "gate": report.gate.state, "gate_reason": report.gate.reason, "sections": sections}, default=str))
    return max((EXIT.get(state, 3) for state in states), default=0)
