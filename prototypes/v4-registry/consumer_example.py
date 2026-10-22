"""What the rack session runner would do with the registry instead of guessing /dev/fdcanusb.

    /usr/bin/python3 /home/bam/io-diagnostics/prototypes/v4-registry/consumer_example.py /home/bam/io-diagnostics/out/io-diagnostics.json 272813EF

Resolves an adapter by serial to its stable by-id path, and refuses to start while the registry
says the port is busy or no board answers. Reads one file; talks to no hardware.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def resolve(registry: dict, serial: str) -> dict:
    device = next((device for device in registry["devices"] if device["key"] == serial), None)
    if device is None:
        return {"ok": False, "why": f"{serial} is not plugged in (registry {registry['updated']})"}
    if device["gate"] == "blocked":
        return {"ok": False, "why": f"busy: {device['gate_reason']}", "path": device["by_id"]}
    if device["kind"] == "bus" and not device["ids"]:
        return {"ok": False, "why": f"no board answers on {serial}: {device['headline']}", "path": device["by_id"]}
    return {"ok": True, "path": device["by_id"], "ids": [entry[0] for entry in device["ids"]]}


if __name__ == "__main__":
    registry = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps(resolve(registry, sys.argv[2])))
