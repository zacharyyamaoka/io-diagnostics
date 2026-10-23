"""Fixture plugin: replays the DRIVER side of V6's recorded frames (v6_frames.json), as a real plugin subprocess.

    <python> v6_scenario.py describe
    <python> v6_scenario.py diagnose --device usb:1-3        (frame from IO_DIAGNOSTICS_FIXTURE, set by the host)
    <python> v6_scenario.py act --device usb:1-3 --action calibrate --node 15 [--new-id N] [--confirm TOKEN] [--dry-run]

It speaks the plugin boundary exactly like a live driver (docs/build-plan-v1.md, plugins.d/README.md): JSON lines,
one v0 record each, paths relative to the device. It is declared ``replay = true``, so the host keeps the receive
times the recording carries (``received``) instead of stamping its own; records without one (a live publisher such
as a stream's frame rate, or a check the driver repeats such as PortBusy) get the host's clock like any live record.

``act`` answers like the moteus driver (moteus_servo/diagnostics.py ``act``): a dry run prints one ``action-result``
line with the argv a live driver would run, its steps, its preconditions, the confirm text and a token naming the
action, the device, the board and a 5-minute window; a run without that token gets ``ConfirmationRequired``; a run
with it prints ``ActionCompleted`` and says that NOTHING ran (a recorded scenario has no hardware behind it).
Stdlib only, so it runs under whichever python the host runs. Never touches hardware.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import string
import sys
from pathlib import Path

FIXTURE = Path(os.environ.get("IO_DIAGNOSTICS_FIXTURE_FILE") or Path(__file__).with_name("v6_frames.json"))
SCHEMA = "diagnostics-record/0"
WINDOW_S = 300.0  # moteus_servo CONFIRM_WINDOW_S: a token names one 5-minute window, the next one still honours it
UPDATER = "~/zach_brain/Settings/Skills/update-moteus-firmware/scripts/update_moteus_firmware.py"
CACHE = "~/.cache/moteus-firmware"


def token_for(action: str, serial: str, node: int | None, at: float, extra: str = "") -> str:
    text = f"{action}|{serial.upper()}|{node if node is not None else '-'}|{int(at // WINDOW_S)}"
    if extra:  # set_id: the new id, like moteus_servo confirm_token
        text += f"|{extra}"
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def plan(action: str, serial: str, node: int | None, camera: bool, new_id: int | None = None) -> list[dict]:
    """The steps a live driver's dry run lists for this action (moteus_servo plan_action / intel_rgbd act)."""

    if camera:
        sdk = {"check_comms": "rs.context().query_devices() + camera_info (no stream)",
               "check_firmware": "camera_info: firmware_version, recommended_firmware_version",
               "calibrate": "auto_calibrated_device.run_on_chip_calibration()",
               "reset": "RealSenseCamera.hardware_reset()",
               "identify": "pipeline.start (one colour stream), keep the 16th frame, save a JPEG under ~/.cache/robotdx/identify/"}
        if action == "update_firmware":
            return [{"purpose": "flash the recommended image", "runs": "subprocess",
                     "argv": ["rs-fw-update", "-s", serial, "-f", "~/.cache/intel_rgbd/firmware/Signed_Image_UVC_5_16_0_1.bin"]}]
        if action == "open_console":
            return [{"purpose": "start realsense-viewer, detached", "runs": "detached", "argv": ["realsense-viewer"]}]
        return [{"purpose": sdk.get(action, action), "runs": "in-process", "argv": []}]
    by_id = f"/dev/serial/by-id/usb-mjbots_fdcanusb_{serial}-if00"
    target = str(node)
    tool = "moteus_tool"
    steps = {
        "check_comms": [{"purpose": "addressed scan of ids 1-126, reply loss, id check, read-only config and identity, can status",
                         "runs": "in-process", "argv": ["python", "-m", "moteus_servo.diagnostics", "diagnose", "--device", by_id, "--range", "1", "126"],
                         "sends": ["query frames, one id per transaction", "UUID register reads", "conf get / tel reads (read-only)", "can status"]}],
        "check_firmware": [{"purpose": "fetch the latest official release into the firmware cache (no CAN)", "runs": "subprocess",
                            "argv": ["python3", UPDATER, "--download-only", "--cache-dir", CACHE]},
                           {"purpose": "read each board's firmware and compare it with the cache", "runs": "in-process",
                            "argv": ["python", "-m", "moteus_servo.diagnostics", "diagnose", "--device", by_id, "--queries", "0", "--no-config"]}],
        "update_firmware": [{"purpose": f"stop every board, back up CAN {node}'s config, flash, verify, restore", "runs": "subprocess",
                             "argv": ["python3", UPDATER, "--target", target, "--port", by_id, "--cache-dir", CACHE, "--tool", tool]}],
        "calibrate": [{"purpose": f"calibrate CAN {node}: the rotor spins", "runs": "subprocess",
                       "argv": [tool, "--target", target, "--calibrate", "--fdcanusb", by_id]}],
        "update_config": [{"purpose": f"back up CAN {node}'s config", "runs": "subprocess",
                           "argv": [tool, "--target", target, "--dump-config", "--fdcanusb", by_id]},
                          {"purpose": "check the recorded config names this board (its uuid lines against the backup)", "runs": "in-process", "argv": []},
                          {"purpose": f"write the recorded config to CAN {node}", "runs": "subprocess",
                           "argv": [tool, "--target", target, "--restore-config", f"~/.local/share/moteus-config/can-{node}.cfg", "--fdcanusb", by_id]}],
        "clear_faults": [{"purpose": f"query CAN {node}, then stop it (mode 0): a latched fault clears", "runs": "in-process", "argv": [],
                          "sends": ["query", "stop"]}],
        "reset": [{"purpose": f"query CAN {node}, then d reset", "runs": "in-process", "argv": [], "sends": ["query", "d reset"]}],
        "set_id": [{"purpose": f"5 UUID reads of CAN {node}, one query of the new id, then reassign", "runs": "subprocess",
                    "argv": ["python3", "utils/moteus_reassign_id.py", "--target", target, "--new", str(new_id)]}],
        "identify": ([{"purpose": f"query: CAN {node} answers, mode 0, no fault", "runs": "in-process", "argv": [], "sends": ["query frame"]},
                      {"purpose": "config interlock: read the safety keys back, compare with the deployment record and check the ratio",
                       "runs": "in-process", "argv": [], "sends": ["tel stop", "conf get (read-only)"]},
                      {"purpose": "wiggle, aborting on a fault, a limiter code, a lost reply, overspeed or travel", "runs": "in-process", "argv": [],
                       "sends": ["set_position about the start: +-5 degrees at the joint output, 3 cycles at 1 Hz, 50 commands/s",
                                 "maximum_torque <= 0.5 N.m at the joint, velocity_limit <= 0.25 rev/s, watchdog_timeout 0.25 s",
                                 "stop (mode 0), always, whatever ends the wiggle"]}]
                     if node is not None else
                     [{"purpose": "find the boards: addressed scan of ids 1-32 and the expected ids (1,14,15)", "runs": "in-process", "argv": [],
                       "sends": ["query frames, one id per transaction"]},
                      {"purpose": "then each board that answers, in CAN-id order, 1.5 s apart: query, config interlock, wiggle, stop", "runs": "in-process", "argv": []}]),
        "stop_all": [{"purpose": "scan ids 1-32 and the expected ids, then stop every board that answers", "runs": "subprocess",
                      "argv": ["python3", "utils/moteus_with_targets.py", tool, "-t", "1,14,15", "--stop"]}],
        "open_console": [{"purpose": "open tview on the boards that answer, detached", "runs": "detached",
                          "argv": ["python3", "utils/moteus_with_targets.py", "tview", "-t", "1,14,15"]}],
    }
    return steps.get(action, [{"purpose": action, "runs": "in-process", "argv": []}])


def act(args, frame: dict, now: float) -> int:
    records = frame["plugin_records"].get(args.device, [])
    entities = {record.get("path") or "": record for record in records if record.get("record") == "entity"}
    device = entities.get("", {})
    camera = device.get("icon") != "adapter"
    serial = str((device.get("identity") or {}).get("serial_number") or args.device)
    path = f"can/{args.node}" if args.node is not None else ""
    entity = entities.get(path, device)
    action = next((item for item in entity.get("actions") or [] if item.get("name") == args.action), None)

    def line(**fields) -> None:
        print(json.dumps({"schema": SCHEMA, **fields}, ensure_ascii=False))

    if action is None:
        line(record="event", code="ActionRefused", severity="warn", stamp=now, path=path,
             message=f"fixture: {entity.get('name', args.device)} registers no action {args.action!r}")
        return 0
    if action.get("scope") == "node" and args.node is None:
        line(record="event", code="ActionRefused", severity="warn", stamp=now, path=path,
             message=f"{args.action} acts on one board: pass --node <CAN id>")
        return 0
    if args.action == "set_id" and (args.new_id is None or not 1 <= args.new_id <= 126 or args.new_id == args.node):
        line(record="event", code="ActionRefused", severity="warn", stamp=now, path=path,
             message="set_id needs the new id: pass --new-id <CAN id>" if args.new_id is None
             else f"CAN {args.new_id} is not a new id for CAN {args.node} (1-126)")
        return 0
    fill = {"name": device.get("name", ""), "id": "" if args.node is None else str(args.node), "serial": serial, "latest": "1.1.2"}
    confirm = string.Formatter().vformat(action.get("confirm") or "", (), fill)
    if args.action == "set_id":
        confirm += f" New id: CAN {args.new_id}."
    steps = plan(args.action, serial, args.node, camera, args.new_id)
    command = next((step["argv"] for step in reversed(steps) if step["runs"] != "in-process" and step["argv"]), None) \
        or next((step["argv"] for step in steps if step["argv"]), [])
    extra = str(args.new_id) if args.action == "set_id" else ""
    token = token_for(args.action, serial, args.node, now, extra)
    valid = bool(args.confirm) and args.confirm in (token, token_for(args.action, serial, args.node, now - WINDOW_S, extra))
    if args.dry_run:
        line(record="action-result", action=args.action, title=action.get("title", ""), dry_run=True, stamp=now, path=path,
             hardware_id=device.get("hardware_id", ""), node=args.node, danger=action.get("danger"), scope=action.get("scope"),
             command_kind="argv", command=command, steps=steps,
             preconditions=[{"check": check, "text": string.Formatter().vformat(text, (), fill),
                             "state": "the person confirms it" if check in ("joint_free", "joint_clear", "joints_clear", "target_in_view") else "fixture: as recorded"}
                            for check, text in action.get("preconditions") or []],
             confirm=confirm, token=token, token_state="valid" if valid else ("wrong" if args.confirm else "missing"),
             message=f"dry run (fixture {args.frame}): {action.get('title')} would run {' '.join(command) or 'in-process'}; nothing ran")
        return 0
    if not valid:
        line(record="event", code="ConfirmationRequired", severity="info", stamp=now, path=path, message=confirm,
             args={"action": args.action, "node": args.node, "token": token, "wrong_token": bool(args.confirm)})
        return 0
    line(record="event", code="ActionCompleted", severity="info", stamp=now, path=path,
         message=f"fixture {args.frame}: {action.get('title')} answered from the recording; nothing ran "
                 f"(a live driver runs {' '.join(command) or 'it in-process'})",
         args={"action": args.action, "node": args.node, **({"new_id": args.new_id} if args.new_id is not None else {})})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="v6_scenario")
    parser.add_argument("verb", choices=["describe", "diagnose", "act"])
    parser.add_argument("--device", default="")
    parser.add_argument("--frame", default=os.environ.get("IO_DIAGNOSTICS_FIXTURE", "F1"))
    parser.add_argument("--action", default="")
    parser.add_argument("--node", type=int)
    parser.add_argument("--new-id", type=int)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.verb == "describe":
        print(json.dumps({"name": "v6_scenario", "version": "1", "schema": SCHEMA,
                          "matches": ["usb:0483:5740", "usb:8086:0b5b"], "actions": []}))
        return 0
    document = json.loads(FIXTURE.read_text())
    frame = document["frames"].get(args.frame)
    if frame is None:
        print(f"unknown frame {args.frame!r}", file=sys.stderr)
        return 2
    now = float(os.environ.get("IO_DIAGNOSTICS_NOW", frame["clock"]))
    if args.verb == "act":
        return act(args, frame, now)
    for line in frame["plugin_records"].get(args.device, []):
        if line.get("record") == "event" and (line.get("stamp") or 0) > now:
            continue  # the recording never shows the future
        print(json.dumps(line, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
