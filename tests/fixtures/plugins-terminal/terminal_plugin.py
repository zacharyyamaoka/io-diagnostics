"""Fixture plugin for runs (docs/runs-v1.md): a driver whose ``act`` prints like a real tool and never touches hardware.

    <python> terminal_plugin.py describe
    <python> terminal_plugin.py diagnose --device usb:1-3
    <python> terminal_plugin.py act --device usb:1-3 --action calibrate --node 1 [--confirm tok-calibrate] [--dry-run]
    <python> terminal_plugin.py stop --device usb:1-3 [--node 1]

Actions (``act``, a real run):

    calibrate    (moves, CAN 1)  progress lines drawn with ``\\r`` (each carries ``t=<time.time()>`` so a test measures the
                 latency from print to page), then ``Continue? [y/N]``, reads one line of stdin; ``y`` -> "saved",
                 records ``ActionCompleted``, exit 0; anything else -> "aborted", exit 1. Steps take ``$TERMINAL_STEP_S``.
    check_comms  (query)  ``$TERMINAL_LINES`` plain lines, ``$TERMINAL_LINE_S`` (0.1) s apart, each with ``t=``; exit 0.
    hang         (changes)  ignores SIGINT and SIGTERM (and logs that it got them) until SIGKILL.
    old          (query)  an OLD plugin: ignores ``IO_DIAGNOSTICS_RECORD_FD`` and prints its records on stdout.
    crash        (changes)  prints, then exits 3.

Records go to the fd named by ``IO_DIAGNOSTICS_RECORD_FD`` when it is set, else to stdout (the old protocol). Every verb
appends a JSON line to ``$TERMINAL_LOG`` (verb, argv, the signals it got, the record fd, whether stdin is a tty).
``stop`` is the plugin's own stop verb (no ``--confirm``): it logs and prints an Event. Stdlib only.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

SCHEMA = "diagnostics-record/0"
ARGS = sys.argv[1:]
VERB = next((word for word in ARGS if word in ("describe", "diagnose", "act", "stop")), "")
ACTIONS = [
    {"name": "check_comms", "title": "Check communication", "description": "ask who answers", "danger": "query",
     "scope": "device", "confirm": "Ask every id on {name}?", "kind": "touches"},
    {"name": "calibrate", "title": "Run calibration…", "description": "spins the rotor", "danger": "moves",
     "scope": "node", "confirm": "Calibrate CAN {id} on {name}? The rotor spins.", "kind": "changes",
     "preconditions": [["joint_free", "CAN {id}'s joint is free to spin"]]},
    {"name": "open_console", "title": "Open console (tview)", "description": "launches a harmless detached process", "danger": "interrupts",
     "scope": "device", "confirm": "Open the console on {name}?", "kind": "changes"},
    {"name": "hang", "title": "Hang (ignores Stop)", "description": "ignores SIGINT and SIGTERM", "danger": "changes",
     "scope": "device", "confirm": "Hang on {name}?", "kind": "changes"},
    {"name": "old", "title": "Old plugin", "description": "prints its records on stdout", "danger": "query",
     "scope": "device", "confirm": "Run the old plugin on {name}?", "kind": "touches"},
    {"name": "crash", "title": "Crash", "description": "exits 3", "danger": "changes", "scope": "device",
     "confirm": "Crash on {name}?", "kind": "changes"},
]


def log(**fields) -> None:
    path = os.environ.get("TERMINAL_LOG")
    if path:
        with open(path, "a") as handle:
            handle.write(json.dumps({"verb": VERB, "argv": ARGS, "pid": os.getpid(), **fields}) + "\n")


def arg(name: str) -> str | None:
    return ARGS[ARGS.index(name) + 1] if name in ARGS[:-1] else None


def records():
    """Where records go: the record fd when the host gave one, else stdout (the old protocol)."""

    fd = os.environ.get("IO_DIAGNOSTICS_RECORD_FD")
    return os.fdopen(int(fd), "w") if fd else sys.stdout


def emit(handle, record: dict) -> None:
    handle.write(json.dumps(record) + "\n")
    handle.flush()


def progress_line(percent: int) -> str:
    return f"\rcalibrating {'#' * (percent // 5):<20} {percent:3d}%  t={time.time():.6f}"


def calibrate(out) -> int:
    step = float(os.environ.get("TERMINAL_STEP_S", "0.1"))
    print("moteus_tool 0.3.99 (fixture): calibrating CAN 1", flush=True)
    for percent in range(0, 101, 10):
        sys.stdout.write(progress_line(percent))
        sys.stdout.flush()
        time.sleep(step)
    sys.stdout.write("\n")
    sys.stdout.write("Continue? [y/N] ")
    sys.stdout.flush()
    answer = sys.stdin.readline().strip().lower()
    log(answer=answer, got_input_at=time.time())
    if answer.startswith("y"):
        print("saved the calibration to CAN 1", flush=True)
        emit(out, {"record": "event", "code": "ActionCompleted", "severity": "info", "message": "CAN 1 calibrated"})
        return 0
    print("aborted: nothing saved", flush=True)
    emit(out, {"record": "event", "code": "ActionRefused", "severity": "warn", "message": "you did not continue"})
    return 1


def main() -> int:
    if VERB == "describe":
        print(json.dumps({"name": "terminal_fixture", "schema": SCHEMA, "matches": ["usb:0483:5740"], "actions": ACTIONS,
                          "stop": True}))
        return 0
    if VERB == "diagnose":
        print(json.dumps({"schema": SCHEMA, "record": "entity", "path": "", "name": "fdcanusb fixture", "kind": "adapter",
                          "identity": {"serial_number": "FIX1"}, "actions": [a for a in ACTIONS if a["scope"] == "device"]}))
        print(json.dumps({"schema": SCHEMA, "record": "entity", "path": "can", "name": "CAN bus", "kind": "bus", "via": ""}))
        print(json.dumps({"schema": SCHEMA, "record": "entity", "path": "can/1", "name": "CAN 1", "kind": "servo", "via": "can",
                          "actions": [a for a in ACTIONS if a["scope"] == "node"]}))
        print(json.dumps({"schema": SCHEMA, "record": "status", "level": "ok", "name": "fixture: ran", "path": "",
                          "expected_period_s": 30}))
        return 0
    action = arg("--action") or ""
    token = "tok-" + action
    if VERB == "stop":
        log(stopped=True, node=arg("--node"), record_fd=os.environ.get("IO_DIAGNOSTICS_RECORD_FD"))
        node = arg("--node")
        print(json.dumps({"record": "event", "code": "StopSent", "severity": "info",
                          "message": f"CAN {node} mode 0 (stopped)" if node else "every board mode 0 (stopped)"}))
        return 0
    log(action=action, record_fd=os.environ.get("IO_DIAGNOSTICS_RECORD_FD"), tty=sys.stdin.isatty(),
        run_id=os.environ.get("IO_DIAGNOSTICS_RUN_ID"), term=os.environ.get("TERM"), cwd_tty=os.isatty(1))
    if "--dry-run" in ARGS:
        print(json.dumps({"record": "action-result", "action": action, "dry_run": True, "command": ["fixture-tool", "--" + action],
                          "steps": [{"purpose": "do it", "runs": "subprocess", "argv": ["fixture-tool", "--" + action]}],
                          "preconditions": [{"check": "joint_free", "text": "the joint is free to spin", "state": "the person confirms it"}],
                          "confirm": f"Do {action}?", "token": token}))
        return 0
    if action not in ("check_comms", "old") and arg("--confirm") != token:
        emit(records(), {"record": "event", "code": "ConfirmationRequired", "severity": "info", "message": "pass the token"})
        return 0
    # the rack bus lock, like moteus_servo's bus_gate: taken with LOCK_NB for the length of the act; held = ActionBlocked
    # BusLockHeld naming the holder ("<pid> <program>", read from <lock>.holder, which a test writes)
    lock_path = os.environ.get("TERMINAL_BUS_LOCK")
    if lock_path and action != "old":
        import fcntl

        handle = open(lock_path, "a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = open(lock_path + ".holder").read().strip() if os.path.exists(lock_path + ".holder") else "another process"
            log(blocked=True)
            emit(records(), {"record": "event", "code": "ActionBlocked", "severity": "warn",
                             "message": f"blocked: rack bus lock held by {holder}", "args": {"reason": "BusLockHeld", "action": action}})
            return 0
    if action == "open_console":
        import subprocess

        # stands in for tview: a detached process that outlives this one and holds nothing
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(float(__import__('os').environ.get('TERMINAL_CONSOLE_S', '30')))"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        print(f"tview opened on CAN 1 (pid {child.pid}); it holds the port until you close it", flush=True)
        emit(records(), {"record": "event", "code": "ActionCompleted", "severity": "info", "message": f"tview opened (pid {child.pid})",
                         "args": {"pid": child.pid}})
        return 0
    if action == "old":
        # an old plugin knows nothing of the record fd: its records are stdout lines
        print("old plugin: asking every id ...")
        print(json.dumps({"record": "event", "code": "ActionCompleted", "severity": "info", "message": "old plugin ran"}))
        return 0
    out = records()
    if action == "calibrate":
        try:
            return calibrate(out)
        except KeyboardInterrupt:  # a Stop from the page: a record and exit 130, like moteus_servo, not a traceback
            print("\ninterrupted: the tool was stopped", flush=True)
            emit(out, {"record": "event", "code": "ActionInterrupted", "severity": "warn", "message": "calibrate was interrupted"})
            return 130
    if action == "check_comms":
        for number in range(int(os.environ.get("TERMINAL_LINES", "5"))):
            print(f"line {number} t={time.time():.6f}", flush=True)
            time.sleep(float(os.environ.get("TERMINAL_LINE_S", "0.1")))
        emit(out, {"record": "event", "code": "ActionCompleted", "severity": "info", "message": "comms checked"})
        return 0
    if action == "hang":
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, lambda got, _frame: (log(got_signal=signal.Signals(got).name), print(f"ignoring {signal.Signals(got).name}", flush=True)))
        print("hanging: SIGINT and SIGTERM are ignored", flush=True)
        while True:
            time.sleep(0.1)
    if action == "crash":
        print("about to fail", flush=True)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
