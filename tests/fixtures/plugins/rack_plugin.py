"""A stand-in for a CAN-adapter driver (the shape moteus_servo's diagnose and probe write), for tests/test_probe.py.

    <python> rack_plugin.py probe --device usb:9-3        # declared in describe unless $RACK_PROBE is "0"
    <python> rack_plugin.py diagnose --device usb:9-3

``probe`` prints diagnose's records plus the driver's own events (docs/probe-on-connect.md): a ``Found`` per board that
answers and ``Probed`` last; blocked, a ``ProbeBlocked`` event when the state says ``"say_blocked": true``.

What it prints is read from the JSON file named by $RACK_STATE on EVERY run, so a test changes the rig between
runs by rewriting that file:

    {"name": "fdcanusb 0607623A",                      # the adapter's entity name
     "boards": {"11": {"answers": false}, "12": {"answers": true, "model": "moteus-c1", "sw_version": "1.1.2"}},
     "port_holders": ["78183 python3"],                # the port is held: every check reads PortBusy
     "lock_holders": ["5 experiment"],                 # the rack bus lock is held: BusLockHeld
     "say_blocked": true,                              # a blocked probe sends its own ProbeBlocked sentence
     "sleep": 0.4}                                     # seconds a run takes (to see runs overlap, or not)

Every run appends {"verb", "device", "start", "end", "blocked"} (wall clock) to $RACK_LOG. Never touches hardware.
"""

from __future__ import annotations

import json
import os
import sys
import time

SCHEMA = "diagnostics-record/0"


def line(record: str, **fields) -> None:
    print(json.dumps({"schema": SCHEMA, "record": record, **fields}), flush=True)


def status(level: str, name: str, path: str, message: str, code: str = "", **fields) -> None:
    line("status", level=level, name=name, path=path, message=message, expected_period_s=30,
         **({"code": code} if code else {}), **fields)


def event(code: str, message: str, path: str = "", severity: str = "info") -> None:
    line("event", code=code, message=message, path=path, severity=severity, stamp=time.time())


def probe_events(state: dict, blocked: str) -> None:
    name = state.get("name", "fdcanusb 0607623A")
    boards = sorted(state.get("boards", {}).items(), key=lambda item: int(item[0]))
    if blocked:
        if state.get("say_blocked"):
            held = (state.get("port_holders") or state.get("lock_holders") or ["?"])[0].split(" ", 1)
            event("ProbeBlocked", f"{held[1] if len(held) > 1 else '?'} has the port (pid {held[0]}) · RobotDx probes again "
                                  "when it closes", severity="warn")
        return
    heard = []
    for can_id, board in boards:
        if board.get("answers", True):
            who = " · ".join(bit for bit in (board.get("model"), f"fw {board['sw_version']}" if board.get("sw_version") else "") if bit)
            event("Found", f"CAN {can_id} answers on {name}" + (f" ({who})" if who else ""), f"can/{can_id}")
            heard.append(f"CAN {can_id}")
    silent = [f"CAN {can_id}" for can_id, board in boards if not board.get("answers", True)]
    event("Probed", f"{name}: {len(heard)} of {len(boards)} expected boards answer ({', '.join(heard)})"
          + (f" · {', '.join(silent)} silent" if silent else ""), severity="warn" if silent else "info")


def diagnose(state: dict) -> str:
    name = state.get("name", "fdcanusb 0607623A")
    time.sleep(float(state.get("sleep", 0)))
    port_holders, lock_holders = state.get("port_holders") or [], state.get("lock_holders") or []
    line("entity", path="", name=name, kind="device", hardware_id=state.get("hardware_id", ""))
    line("entity", path="can", name="CAN bus", kind="bus", part_of="")
    status("ok", f"{name}: usb", "", "on USB 9-3 as /dev/ttyACM0")
    if port_holders or lock_holders:
        code = "PortBusy" if port_holders else "BusLockHeld"
        held = port_holders or lock_holders
        reason = f"blocked: {'port' if port_holders else 'rack bus lock'} held by {', '.join(held)}"
        values = [{"key": "port_holders", "value": ", ".join(port_holders) or "none"}] if port_holders else []
        status("unknown", f"{name}: CAN bus", "can", reason, code, values=values + [{"key": "what_to_check", "value": "1. close it"}])
        return code  # blocked: nothing was heard, so no board is reported (the host keeps the rows it has)
    # the moteus_servo contract (Zach, 2026-10-24: a row exists only for something heard from): a board that does not
    # answer has no entity; the bus names it
    for can_id, board in sorted(state.get("boards", {}).items(), key=lambda item: int(item[0])):
        if not board.get("answers", True):
            continue
        identity = {key: board[key] for key in ("model", "sw_version") if key in board}
        line("entity", path=f"can/{can_id}", name=f"CAN {can_id}", kind="node", via="can", identity=identity,
             hardware_id=f"moteus:{can_id}")
    silent = [can_id for can_id, board in sorted(state.get("boards", {}).items(), key=lambda item: int(item[0]))
              if not board.get("answers", True)]
    if silent:
        total = len(state.get("boards", {}))
        status("warn", f"{name}: CAN bus", "can", f"{total - len(silent)} of {total} expected boards answer · "
               + ", ".join(f"CAN {can_id}" for can_id in silent) + " silent", "ExpectedBoardsSilent",
               caused_by=[f"can/{can_id}" for can_id in silent])
    else:
        status("ok", f"{name}: CAN bus", "can", "error-active")
    for can_id, board in sorted(state.get("boards", {}).items(), key=lambda item: int(item[0])):
        if board.get("answers", True):
            status("warn" if board.get("fault") else "ok", f"CAN {can_id}: servo", f"can/{can_id}",
                   "fault 33" if board.get("fault") else "ok", f"MoteusFault.{board['fault']}" if board.get("fault") else "")
    return ""


def main() -> int:
    verb = next(word for word in sys.argv[1:] if word in ("describe", "diagnose", "probe", "follow"))
    device = sys.argv[sys.argv.index("--device") + 1] if "--device" in sys.argv else ""
    if verb == "describe":
        print(json.dumps({"name": "rack_plugin", "schema": SCHEMA, "record_version": "0.1", "matches": [], "actions": [],
                          **({"probe": {"timeout_s": 10}} if os.environ.get("RACK_PROBE", "1") != "0" else {}),
                          **({"follow": {"period_s": 0.3}} if os.environ.get("RACK_FOLLOW") == "1" else {})}))
        return 0
    if verb == "follow":  # what a following driver does: its records every period, forever
        with open(os.environ["RACK_LOG"], "a") as log:
            log.write(json.dumps({"verb": verb, "device": device, "start": time.time(), "end": None, "blocked": ""}) + "\n")
        while True:
            with open(os.environ["RACK_STATE"]) as handle:
                diagnose(json.load(handle))
            time.sleep(0.3)
    start = time.time()
    with open(os.environ["RACK_STATE"]) as handle:
        state = json.load(handle)
    blocked = diagnose(state)
    if verb == "probe":
        probe_events(state, blocked)
    with open(os.environ["RACK_LOG"], "a") as log:
        log.write(json.dumps({"verb": verb, "device": device, "start": start, "end": time.time(), "blocked": blocked}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
