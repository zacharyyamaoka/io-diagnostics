"""Fixture plugin for the 3D view's tests (docs/robot-description.md): fdcanusb-like adapters with boards whose levels a test sets,
and a camera. Stdlib only; it never opens a port or a camera: every value is made up.

    <python> rd_plugin.py describe --role joint|camera
    <python> rd_plugin.py diagnose --role joint|camera --device <skeleton id>

``$RD_LEVELS`` (a JSON file, re-read on every run) sets a board's level by "<USB serial>/can/<n>", e.g.
``{"272813EF/can/1": "warn"}``; a board it does not name is ``ok``. ``$RD_NODES`` (default 2): how many boards each adapter
reports (``can/1`` .. ``can/N``). ``$RD_LOG`` (a file) gets one JSON line per run (the verb and the device), so a test can see it ran.
"""

from __future__ import annotations

import json
import os
import sys
import time

SCHEMA = "diagnostics-record/0"
ARGS = sys.argv[1:]
VERB = next((word for word in ARGS if word in ("describe", "diagnose")), "")
ROLE = ARGS[ARGS.index("--role") + 1] if "--role" in ARGS[:-1] else "joint"
NODES = max(1, int(os.environ.get("RD_NODES") or 2))
_BASE = float(os.environ.get("IO_DIAGNOSTICS_NOW") or time.time())
_START = time.monotonic()
MESSAGES = {"ok": "answers in 1.1 ms", "warn": "board temperature 68 °C (warn above 65 °C)", "error": "the driver is in fault (33)",
            "unknown": "could not ask: the port is busy"}


def clock() -> float:
    return _BASE + (time.monotonic() - _START)


def emit(**record) -> None:
    sys.stdout.write(json.dumps({"schema": SCHEMA, **record}) + "\n")
    sys.stdout.flush()


def levels() -> dict:
    path = os.environ.get("RD_LEVELS")
    try:
        return json.loads(open(path, encoding="utf-8").read()) if path else {}
    except (OSError, ValueError):
        return {}


def log(**entry) -> None:
    path = os.environ.get("RD_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def diagnose() -> None:
    now = clock()
    device = json.loads(os.environ.get("IO_DIAGNOSTICS_DEVICE") or "{}")
    serial = str(device.get("serial") or "")
    log(verb="diagnose", role=ROLE, serial=serial)
    if ROLE == "joint":
        emit(record="entity", path="", name=f"fdcanusb {serial}", kind="adapter",
             identity={"manufacturer": "mjbots", "model": "fdcanusb", "serial_number": serial})
        emit(record="entity", path="can", name="CAN bus", kind="bus", via="")
        chosen = levels()
        for number in range(1, NODES + 1):
            node = f"can/{number}"
            level = chosen.get(f"{serial}/{node}", "ok")
            emit(record="entity", path=node, name=f"CAN {number}", kind="servo", via="can",
                 identity={"manufacturer": "mjbots", "model": "moteus r4.11", "model_id": "mjbots-moteus-r4-11", "serial_number": f"{serial[:4]}{number}"})
            emit(record="status", level=level, name=f"CAN {number}: comms", path=node, message=MESSAGES.get(level, level), stamp=now,
                 expected_period_s=30, code=None if level == "ok" else f"Fixture.{level}")
            emit(record="status", level="ok", name=f"CAN {number}: firmware", path=node, message="1.1.2 is the latest", stamp=now, expected_period_s=30)
            emit(record="state", path=node, name="motor", stamp=now, expected_period_s=30,
                 measurements=[{"key": "position", "value": round(0.1 * number, 3), "unit": "rev"},
                               {"key": "temperature", "value": 38.5 + number, "unit": "Cel"},
                               {"key": "voltage", "value": 24.1, "unit": "V"}])
    else:
        emit(record="entity", path="", name=f"RealSense D405 {serial}", kind="camera",
             identity={"manufacturer": "Intel", "model": "D405", "model_id": "intel-realsense-d405", "serial_number": serial})
        emit(record="status", level="ok", name="camera: comms", path="", message="answers", stamp=now, expected_period_s=30)


def main() -> int:
    if VERB == "describe":
        print(json.dumps({"name": f"rd_{ROLE}", "version": "1.0", "schema": SCHEMA,
                          "matches": ["usb:0483:5740"] if ROLE == "joint" else ["usb:8086:0b5b"], "actions": []}))
        return 0
    if VERB == "diagnose":
        diagnose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
