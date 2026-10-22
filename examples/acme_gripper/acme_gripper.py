"""The smallest IO diagnostics plugin: a made-up USB gripper (1234:5678) with one part, as JSON lines.

    <python> acme_gripper.py describe
    <python> acme_gripper.py diagnose --device usb:1-4

A real plugin reads its device here (inside its own driver's environment); this one only shows the shapes.
"""

import json
import os
import sys
import time

SCHEMA = "diagnostics-record/0"


def emit(**record):
    print(json.dumps({"schema": SCHEMA, **record}))


if sys.argv[1] == "describe":  # what it claims, and the buttons it registers (none here)
    print(json.dumps({"name": "acme_gripper", "version": "1.0", "schema": SCHEMA,
                      "matches": ["usb:1234:5678"], "actions": []}))
elif sys.argv[1] == "diagnose":  # one run for one device; paths are relative to the device's own row ("")
    device = json.loads(os.environ.get("IO_DIAGNOSTICS_DEVICE", "{}"))  # the host's skeleton dict for it
    now = time.time()
    emit(record="entity", path="", name=f"Acme gripper {device.get('serial') or ''}".strip(), kind="device",
         identity={"manufacturer": "Acme", "model": "Gripper G1", "sw_version": "2.3.0"})
    emit(record="entity", path="jaw", name="jaw", kind="part", part_of="")
    emit(record="status", path="", name="gripper: comms", level="ok", message="answers in 2 ms",
         stamp=now, expected_period_s=30)
    emit(record="status", path="jaw", name="jaw: temperature", level="warn", code="JawHot",
         message="jaw motor 71 °C (warn above 70 °C)", stamp=now, expected_period_s=30,
         measurements=[{"key": "temperature", "value": 71.0, "unit": "Cel", "warn_high": 70.0}])
