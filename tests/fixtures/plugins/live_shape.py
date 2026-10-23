"""Bring the recorded frames (v6_frames.json) to the shape the live drivers send since 2026-10-22, in place:

    uv run --project /home/bam/io-diagnostics python /home/bam/io-diagnostics/tests/fixtures/plugins/live_shape.py

- the adapter's CAN bus is ``part_of`` the adapter and its boards stay ``via`` the bus (the live moteus plugin's
  shape, read from the 17:41 rack frame; F1-F3 had the bus ``via`` the adapter);
- a status whose cause has a checklist carries the value ``what_to_check`` (numbered lines, the moteus driver's
  checklists for its codes: docs/io_diagnostics_actions.md in moteus_servo);
- the entities register the standard action vocabulary (docs/actions-vocabulary-v0.1.md) with the moteus and
  RealSense drivers' texts. The recorded non-standard ``reset`` / ``run_comms_check`` of the cameras and the boards'
  ``reset`` stay (tests/test_host_api.py pins them), so the page shows them under "More".

Idempotent: a second run changes nothing. capture_v6_frames.py calls ``live_shape`` on a fresh capture.
"""

from __future__ import annotations

import json
from pathlib import Path

FRAMES = Path(__file__).resolve().with_name("v6_frames.json")


def numbered(items) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


# the moteus driver's checklists (moteus_servo/diagnostics.py NO_ACK_CHECKS, and its BoardNotAnswering / PortBusy rows)
NO_ACK = numbered((
    "Motor supply on: the boards are powered from it, and the fdcanusb alone gets no ACK (are the boards' LEDs lit?)",
    "XT30 power splitter: every board's XT30 lead seated",
    "CAN cable from the fdcanusb into the JST splitter, seated at both ends",
    "CAN ground: the cable's ground connected between the fdcanusb and the boards",
))
BOARD_SILENT = numbered((
    "Its XT30 power lead, seated in the splitter",
    "Its CAN lead into the JST splitter, seated at both ends",
    "Its id may have changed: run Check communication (it scans every id)",
))
PORT_BUSY = numbered((
    "tview (pid 640623) has the port: close tview",
    "Or let the named process finish; checks and buttons wait until the port is free",
))
CHECKLISTS = {("bus", "NoAck"): NO_ACK, ("bus", "NoReply"): NO_ACK, ("node", "NoReply"): BOARD_SILENT,
              ("device", "PortBusy"): PORT_BUSY}

GATED = [["port_free", "No other process has the adapter open (tview, moteus_tool, a peer)"],
         ["bus_lock_free", "The rack bus lock is free (no experiment running)"]]
BOARD_ANSWERS = [["board_answers", "CAN {id} answers a query on this bus"]]
CAMERA_GATED = [["camera_free", "No other process holds a RealSense and realsense-viewer is closed"],
                ["bus_lock_free", "No rack experiment holds the bus lock"]]

ADAPTER_ACTIONS = [
    {"name": "check_comms", "title": "Check communication", "danger": "query", "scope": "device", "kind": "touches",
     "description": "Ask every CAN id (1-126) who is there, count lost replies, and read each board's config and firmware "
                    "and the bus's health. Reads only: no board changes mode.",
     "confirm": "Query every id on {name} and read bus health? No board changes mode.", "preconditions": GATED},
    {"name": "check_firmware", "title": "Check firmware", "danger": "query", "scope": "device", "kind": "touches",
     "description": "Fetch the latest official moteus release and compare each board's installed firmware with it. Reads only.",
     "confirm": "Fetch the latest official release and read every board's firmware on {name}? No board changes mode.",
     "preconditions": GATED},
    {"name": "stop_all", "title": "Stop all", "danger": "interrupts", "scope": "device", "kind": "changes",
     "description": "Send stop to every board that answers on this bus. Every motor goes limp at once.",
     "confirm": "Stop every board on {name}? Joints under gravity fall when their servo stops.", "preconditions": GATED},
    {"name": "open_console", "title": "Open console", "danger": "interrupts", "scope": "device", "kind": "changes",
     "description": "Open tview, moteus's own console, on this adapter. It holds the port until you close it: checks and "
                    "buttons wait until then.",
     "confirm": "Open tview on {name}? It holds the port until you close it; diagnostics and every button wait until then.",
     "preconditions": GATED},
]
NODE_ACTIONS = [
    {"name": "calibrate", "title": "Run calibration…", "danger": "moves", "scope": "node", "kind": "changes",
     "description": "Run moteus's motor and encoder calibration on one board. The rotor spins for about 30 seconds.",
     "confirm": "Calibrate CAN {id}? The rotor spins for about 30 s. Only when its joint is free to spin: no load on it "
                "and no hard stop in reach.",
     "preconditions": GATED + BOARD_ANSWERS + [["joint_free", "CAN {id}'s joint is free to spin: no load on it and no hard stop in reach"]]},
    {"name": "update_config", "title": "Update configuration…", "danger": "changes", "scope": "node", "kind": "changes",
     "description": "Write this board's recorded configuration to it, after saving a backup of the one it has now.",
     "confirm": "Write the recorded config to CAN {id}? Its current config is saved first, then the recorded file replaces it.",
     "preconditions": GATED + BOARD_ANSWERS + [["config_recorded", "A recorded config for CAN {id} exists (can-{id}.cfg in the config dir)"]]},
    {"name": "set_id", "title": "Change CAN id…", "danger": "changes", "scope": "node", "kind": "changes",
     "description": "Give one board a new CAN id and save it on the board. A new board ships as CAN 1, like a board already "
                    "here: connect it alone (or on another adapter), change its id, then join it to the bus.",
     "confirm": "Change the CAN id of CAN {id}? It is stopped, and afterwards answers only on its new id.",
     "preconditions": GATED + [["source_id_unique", "Exactly one board answers CAN {id} (a new board ships as CAN 1: connect it alone)"],
                               ["new_id_free", "No board answers the new CAN id yet"]]},
]
CAMERA_ACTIONS = [
    {"name": "check_comms", "title": "Check communication", "danger": "query", "scope": "device", "kind": "touches",
     "description": "Ask the SDK which RealSense cameras answer (enumerate) and read this one's camera_info (no stream, no "
                    "option read).", "confirm": "", "preconditions": CAMERA_GATED},
    {"name": "check_firmware", "title": "Check firmware", "danger": "query", "scope": "device", "kind": "touches",
     "description": "Read the installed and the SDK's recommended firmware through camera_info (no stream, no option read).",
     "confirm": "", "preconditions": CAMERA_GATED},
    {"name": "calibrate", "title": "Run calibration…", "danger": "changes", "scope": "device", "kind": "changes",
     "description": "The SDK's on-chip self-calibration. Nothing moves. The result replaces the camera's stored calibration "
                    "only when its health factor was out of the good band and improved.",
     "confirm": "Run on-chip self-calibration on {name} ({serial})? A textured, well-lit target must fill its view; the "
                "camera's stored calibration is rewritten when the result improves it.",
     "preconditions": CAMERA_GATED + [["target_in_view", "A textured, well-lit target fills the camera's view"]]},
    {"name": "open_console", "title": "Open console", "danger": "interrupts", "scope": "device", "kind": "changes",
     "description": "Start realsense-viewer, the vendor's own console, detached. It holds every RealSense until it is closed.",
     "confirm": "Open realsense-viewer for {name} ({serial})? It holds every RealSense until you close it.",
     "preconditions": CAMERA_GATED},
]


def merge(existing: list[dict], added: list[dict], drop: tuple[str, ...] = ()) -> list[dict]:
    """The recorded actions minus ``drop``, then every added action not there yet (by name), in vocabulary order."""

    kept = [action for action in existing if action["name"] not in drop]
    names = {action["name"] for action in kept}
    return [*[action for action in added if action["name"] not in names and action["name"] in ("check_comms", "check_firmware")],
            *kept, *[action for action in added if action["name"] not in names and action["name"] not in ("check_comms", "check_firmware")]]


def shape_device(records: list[dict]) -> None:
    entities = {record.get("path") or "": record for record in records if record.get("record") == "entity"}
    adapter = entities.get("", {}).get("icon") == "adapter"
    for record in records:
        if record.get("record") == "entity":
            path = record.get("path") or ""
            if record.get("kind") == "bus" and "via" in record:  # the live moteus shape: the bus is the adapter's part
                record["part_of"] = record.pop("via")
            if path == "" and adapter:
                record["actions"] = merge(record.get("actions") or [], ADAPTER_ACTIONS, drop=("run_comms_check",))
            elif path == "":
                record["actions"] = merge(record.get("actions") or [], CAMERA_ACTIONS, drop=("run_comms_check",))
            elif record.get("kind") == "node":
                record["actions"] = merge(record.get("actions") or [], NODE_ACTIONS)
        elif record.get("record") == "status" and record.get("level") != "ok":
            kind = entities.get(record.get("path") or "", {}).get("kind")
            checklist = CHECKLISTS.get((kind, record.get("code") or ""))
            values = record.setdefault("values", [])
            if checklist and not any(value.get("key") == "what_to_check" for value in values):
                values.append({"key": "what_to_check", "value": checklist})


def live_shape(document: dict) -> dict:
    for frame in document["frames"].values():
        for records in frame["plugin_records"].values():
            shape_device(records)
    return document


def main() -> int:
    document = json.loads(FRAMES.read_text())
    FRAMES.write_text(json.dumps(live_shape(document), indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
