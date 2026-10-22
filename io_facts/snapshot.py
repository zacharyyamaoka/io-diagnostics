"""Print the real, passive snapshot of this machine's IO as JSON.

    /usr/bin/python3 /home/bam/io-diagnostics/io_facts/snapshot.py

Reads sysfs, /dev/serial/by-id, /proc (fds, locks) and the kernel log. Opens no device.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from io_facts import gate, journal, lock, usb  # noqa: E402

RACK_USB_IDS = {"0483:5740": "fdcanusb", "8086:0b5b": "RealSense D405", "8086:0b07": "RealSense D435"}


def snapshot() -> dict:
    devices = usb.list_usb_devices()
    rack = []
    for device in devices:
        kind = RACK_USB_IDS.get(device.usb_id)
        if not kind:
            continue
        camera = kind.startswith("RealSense")
        nodes = list(device.video_nodes) if camera else list(device.by_id or device.ttys)
        decision = gate.decide(nodes, camera=camera, uses_rack_lock=not camera)
        rack.append({
            "kind": kind, **dataclasses.asdict(device),
            "active_check_gate": {"state": decision.state, "reason": decision.reason, "decided_by": decision.decided_by},
        })
    notices = journal.replay(journal.parse_kernel_log(journal.todays_kernel_log()))
    return {
        "schema": "io-facts/0",
        "taken_at": dt.datetime.now().isoformat(timespec="seconds"),
        "rack_devices": rack,
        "other_devices": [f"{device.port} {device.usb_id} {device.product}" for device in devices if device.usb_id not in RACK_USB_IDS],
        "generic_fdcanusb_symlink": usb.generic_fdcanusb_target(),
        "bus_lock_holders": [dataclasses.asdict(holder) for holder in lock.lock_holders()],
        "todays_notices": [{"time": notice.time.isoformat(timespec="seconds"), "kind": notice.kind, "text": notice.text()} for notice in notices],
    }


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1))
