"""Test plugin for tests/test_host_*.py: one stdlib executable, its behaviour picked by ``--case``.

    <python> case_plugin.py --case spider diagnose --device usb:9-1

Cases: ``spider`` (Zach's black-box example; ``spider_named``: at ERROR, naming leg 3 in caused_by), ``deadbus``
(an adapter whose bus dies after the first poll), ``periodic`` (a periodic check + a forged ``received``), ``malformed`` (every kind of bad line), ``crash`` (a
traceback after one good line), ``hang`` (one good line, then sleeps with a child holding stdout), ``warn_replay``
(a recorded periodic WARN), ``echo_argv`` (reports the options after ``--device``), ``hot_computer`` (the computer
plugin's shape on the host: an ERROR on the device row itself). Never touches hardware.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

SCHEMA = "diagnostics-record/0"


def line(record: str, **fields) -> None:
    print(json.dumps({"schema": SCHEMA, "record": record, **fields}), flush=True)


def spider() -> None:
    line("entity", path="", name="spider", kind="device", identity={"model": "8-legged walker"})
    for number in range(1, 9):
        line("entity", path=f"leg{number}", name=f"leg {number}", kind="part", part_of="", icon="motor")
    line("status", level="warn", name="spider: gait", message="1 of 8 legs failed; walking on 7", path="", code="LegFailed")
    for number in range(1, 9):
        failed = number == 3
        line("status", level="error" if failed else "ok", name=f"leg {number}: motor", path=f"leg{number}",
             message="motor fault" if failed else "ok", **({"code": "MotorFault"} if failed else {}))


def spider_named() -> None:
    """The spider at ERROR, naming its failed leg in caused_by (record v0.1), relative to its device like a path."""

    line("entity", path="", name="spider", kind="device", identity={"model": "8-legged walker"})
    for number in range(1, 9):
        line("entity", path=f"leg{number}", name=f"leg {number}", kind="part", part_of="", icon="motor")
    line("status", level="error", name="spider: gait", message="leg 3 failed: walking on 7", path="", code="LegFailed",
         caused_by=["leg3"])
    for number in range(1, 9):
        failed = number == 3
        line("status", level="error" if failed else "ok", name=f"leg {number}: motor", path=f"leg{number}",
             message="fault 33" if failed else "ok", **({"code": "MoteusFault.33"} if failed else {}))


def deadbus(now: float) -> None:
    line("entity", path="", name="fdcanusb DEAD", kind="device")
    line("entity", path="can", name="DEAD CAN bus", kind="bus", via="")
    for node in (1, 2):
        line("entity", path=f"can/{node}", name=f"CAN {node}", kind="node", via="can")
    line("status", level="ok", name="fdcanusb DEAD: adapter", message="adapter answers", path="")
    if now < 1010:  # the first poll: everything answers
        line("status", level="ok", name="DEAD CAN bus: bus", message="error-active", path="can", expected_period_s=2)
        for node in (1, 2):
            line("status", level="ok", name=f"CAN {node}: comms", message="answers", path=f"can/{node}", expected_period_s=2)
    else:  # later: the bus is error-passive and nobody answers; the nodes are simply not re-sent
        line("status", level="error", name="DEAD CAN bus: bus", message="error-passive: nobody ACKs", path="can",
             code="NoAck", expected_period_s=2)
        line("status", level="warn", name="CAN 2: comms", message="no reply", path="can/2", code="NoReply", expected_period_s=2)


def periodic() -> None:
    line("status", level="ok", name="thing: heartbeat", message="alive", path="", expected_period_s=2, received=0.0)


def malformed() -> None:
    line("status", level="ok", name="good: check", message="fine", path="")
    print("this is not json")
    print(json.dumps([1, 2, 3]))
    line("fault", path="", code="Forged")
    print(json.dumps({"schema": "other/9", "record": "status", "level": "ok", "name": "x"}))
    line("status", level="warn", name="no code: check", message="warn without a code", path="")
    line("widget", path="")
    line("status", level="ok", name="bad values", values=[1, 2])


def crash() -> None:
    line("status", level="ok", name="before: crash", message="printed before the crash", path="")
    raise RuntimeError("boom: the driver crashed")


def hang() -> None:
    line("status", level="ok", name="before: hang", message="printed before the hang", path="")
    subprocess.Popen(["sleep", "60"])  # inherits stdout: only a process-group kill lets the host's read finish
    time.sleep(60)


def foreign(device: str) -> None:
    """Like intel_rgbd: roots its records at its OWN path (not IO_DIAGNOSTICS_DEVICE_PATH); identity ties them."""

    root = "host/usb-9-1/cam-SER"
    line("entity", path=root, name="camera SER", kind="device", via="host/usb-9-1", hardware_id="usb:1234:abcd:SER",
         identity={"serial_number": "SDK-777"})
    line("entity", path=f"{root}/depth", name="depth stream", kind="stream", part_of=root)
    line("status", level="ok", name="camera SER: link", message=f"asked about {device}", path=root, hardware_id="usb:1234:abcd:SER")
    line("status", level="ok", name="camera SER depth: rate", message="30 Hz", path=f"{root}/depth", expected_period_s=1)


def echo_argv(extra: list[str]) -> None:
    """Says which options followed ``diagnose --device <id>`` (a declaration's ``diagnose_args``)."""

    line("status", level="ok", name="argv: diagnose", message=" ".join(sys.argv[1:]), path="",
         values=[{"key": "after_device", "value": " ".join(extra)}])


def warn_replay() -> None:
    line("status", level="warn", name="recorded: check", message="recorded warning", path="", code="Recorded",
         expected_period_s=60, stamp=1000.0, received=1000.0)


def hot_computer(device: str) -> None:
    """Like the computer plugin on the host: checks on the device row itself (""), one ERROR, one OK."""

    line("status", level="error", name="rig: Temperature CPU (k10temp)", message="CPU Tctl 99 °C: Critical Temperature",
         path="", code="CriticalTemperature", expected_period_s=10,
         measurements=[{"key": "Tctl", "value": 99.0, "unit": "Cel", "warn_high": 85.0, "error_high": 95.0}])
    line("status", level="ok", name="rig: RAM Information", message=f"RAM Average 40.0 percent (device {device})", path="",
         expected_period_s=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("verb", choices=["describe", "diagnose", "act"])
    parser.add_argument("--device", default="")
    args, extra = parser.parse_known_args()  # WHY known: echo_argv reports a declaration's diagnose_args
    if extra and args.case != "echo_argv":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if args.verb == "describe":
        matches = [{"bus": "usb", "vid": "1234", "pid": "ab*"}] if args.case == "foreign" else []
        print(json.dumps({"name": f"case_{args.case}", "version": "0", "schema": SCHEMA, "matches": matches, "actions": []}))
        return 0
    now = float(os.environ.get("IO_DIAGNOSTICS_NOW", "0"))
    {"spider": spider, "spider_named": spider_named, "deadbus": lambda: deadbus(now), "periodic": periodic,
     "malformed": malformed, "crash": crash, "hang": hang, "warn_replay": warn_replay, "foreign": lambda: foreign(args.device),
     "echo_argv": lambda: echo_argv(extra), "hot_computer": lambda: hot_computer(args.device)}[args.case]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
