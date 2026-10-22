"""The shared scenario every variant shows: today's rack, with every value's source and age.

Three kinds of value, always labelled:
- ``real``: read passively on this machine (sysfs, the kernel log, /proc, the rack's own files);
- ``reported``: measured by the orchestrator on the hardware today and passed in (can status, the
  RealSense SDK serials, the 28 px camera move);
- ``simulated``: invented for the prototype (the Robotiq 2F-85, the USB<->SDK serial pairing).

Nothing here talks to hardware.
"""

from __future__ import annotations

import json
from pathlib import Path

INVENTORY_DIR = Path("/archive/datasets/bam_rig/rack_inventory")
# WHY 01:13:16Z: the first snapshot after 28B3A25D settled at 18:11:42 local; the story's "last known".
STORY_SNAPSHOT = "2026-10-22T01:13:16Z.json"
DEPLOYMENT = Path("/home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/dev/bam_rack_twin/records/one-dof-test-rack/deployment.json")

# WHY only 33: the one code this rack has hit and named (deployment.json); others come from the moteus docs when needed.
MOTEUS_FAULTS = {33: "motor driver"}


def _inventory(name: str = STORY_SNAPSHOT) -> dict:
    path = INVENTORY_DIR / name
    return json.loads(path.read_text()) if path.exists() else {}


def scenario() -> dict:
    """The fixture as one dict; ``source`` on every leaf group says real / reported / simulated."""

    inventory = _inventory()
    stamp = inventory.get("stamp", "2026-10-22T01:13:16Z")
    nodes_272 = inventory.get("adapters", {}).get("272813EF", {})
    nodes = {}
    for can_id in ("1", "14", "15"):
        node = nodes_272.get(can_id, {})
        nodes[can_id] = {
            "model": "moteus-c1 + SteadyWin GIM4305-10",
            "voltage_v": node.get("voltage", 36.5),
            "mode": node.get("mode", 0),
            "fault": node.get("fault", 0),
            "source": f"real: rack_inventory snapshot {stamp} (read-only file)",
        }
    nodes["15"]["fault_on_enable"] = {
        "code": 33,
        "name": MOTEUS_FAULTS[33],
        "when": "~20 ms after its bridge enables, at zero gains and zero torque; twice on 2026-10-21",
        "source": "real: deployment.json mapping note (rack's own record)",
    }
    return {
        "story_time": "2026-10-21T18:14:00",
        "adapters": {
            "272813EF": {
                "by_id": "/dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00",
                "tty": "/dev/ttyACM0",
                "port": "1-3",
                "adapter": {"state": "ok", "source": "real: sysfs + by-id"},
                "bus": {"state": "error-active", "err": 0, "warn": 0, "tdc": 25, "replies": True,
                        "source": "reported: can status by the orchestrator, 2026-10-21"},
                "expected_ids": [1, 14, 15],
                "nodes": nodes,
                "blocked": {"by": "pid 640623 tview", "source": "real: find_port_holders, 18:25 (tview open on this adapter)"},
            },
            "28B3A25D": {
                "by_id": "/dev/serial/by-id/usb-mjbots_fdcanusb_28B3A25D-if00",
                "tty": "/dev/ttyACM1",
                "port": "1-5",
                "plug": {"settled": "18:11:42", "flaps": 4, "flap_port": "1-7", "unplugged": "18:20:46",
                         "source": "real: kernel log replayed through io_facts.journal.Coalescer"},
                "adapter": {"state": "ok", "source": "real: sysfs + by-id; it answers can status"},
                "bus": {"state": "error-passive", "err": 1, "warn": 1, "dlec": 7, "tdc": 0, "replies": False,
                        "meaning": "nobody ACKs its frames: no powered node is electrically on this bus",
                        "source": "reported: can status by the orchestrator, 2026-10-21"},
                "expected_count": 4,
                "answering": [],
                "scanned": "1-126",
                "note": "4 motors wired, one not powering on",
                "last_known": {"ids": [], "source": f"real: rack_inventory snapshot {stamp}"},
            },
        },
        "generic_symlink": {"path": "/dev/fdcanusb", "points_at": "/dev/ttyACM1 = 28B3A25D (the newest adapter, the dead bus)",
                            "source": "real: ls -l /dev/fdcanusb at 18:11-18:20"},
        "cameras": {
            "130523071221": {"model": "RealSense D405", "port": "6-2", "sdk_serial": "218622274411",
                             "event": "dropped off USB 18:07:04, back 18:07:08",
                             "source": "real: USB serial, port and drop from sysfs + kernel log; SDK serial pairing simulated"},
            "219323070276": {"model": "RealSense D405", "port": "6-1", "sdk_serial": "218622276036",
                             "moved_px": 28, "source": "real: USB serial and port; SDK pairing simulated; 28 px move reported"},
        },
        "other": {"Logitech C920e": {"port": "7-1.1.1", "source": "real: sysfs (not rack IO; folded away)"}},
        "new_plugin_device": {
            "name": "Robotiq 2F-85", "via": "FTDI USB-RS485 (0403:6015)", "bus": "Modbus RTU 115200 8N1",
            "ids": [9], "port": "7-3", "serial": "DAK1RQ2F",
            "source": "simulated: not on this machine; plugin exercised against a fake bus",
        },
    }


if __name__ == "__main__":
    print(json.dumps(scenario(), indent=1))
