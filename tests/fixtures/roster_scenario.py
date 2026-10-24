"""The roster scenario (Zach, 2026-10-24): what the entity tree shows when things are not all there.

A fake rack PC, a stdlib plugin whose report is read from a JSON file on every run, a clock read from a file, and the
person's expected setup. Nothing here touches hardware.

    fdcanusb 272813EF   on USB. Its boards: CAN 1 and CAN 14 answer; CAN 15 is expected and NEVER answered (no row; its
                        bus says "CAN 15 silent"); CAN 21 answered once and then went quiet (its row stays, hollow).
    fdcanusb 0607623A   expected, NOT on USB (a status on the Computer, no row).
    RealSense D405      on USB, its plugin reports it.
    keyboard, mouse, webcam   on USB, no driver claims them (hidden by default).

Run it as a page for the browser tests and the screenshots:

    python tests/fixtures/roster_scenario.py --dir DIR [--port 0] [--aged] [--ledger]

It prints ``IO diagnostics on http://127.0.0.1:PORT/``, then watches ``DIR/control.json``: a changed ``seq`` runs every
plugin once more; ``DIR/clock.txt`` (seconds) moves the host clock; ``DIR/rack.json`` is what the plugin reports.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_host_support import FakeOs, SetClock, host_dict, usb_bus, usb_device  # noqa: E402

from io_diagnostics.host import Host  # noqa: E402

START = 1792800000.0  # 2026-10-23
A, B, CAM = "272813EF", "0607623A", "219323070276"
PLUGIN = r'''
import json, os, sys

args = sys.argv[1:]
verb = next(word for word in args if word in ("describe", "diagnose", "probe", "act"))
SCHEMA = "diagnostics-record/0"
def emit(record):
    print(json.dumps({"schema": SCHEMA, **record}), flush=True)
if verb == "describe":
    emit({"name": "rack", "record_version": "0.1", "actions": []})
    raise SystemExit
device = json.loads(os.environ["IO_DIAGNOSTICS_DEVICE"])
serial = device.get("serial") or ""
rack = json.load(open(os.environ["ROSTER_RACK"]))
spec = rack.get(serial, {})
expected = []
try:
    expected = [item if isinstance(item, int) else int(str(item).split("/")[-1]) for item in json.loads(os.environ.get("IO_DIAGNOSTICS_EXPECTED") or "{}").get("nodes", [])]
except ValueError:
    pass
actions = [{"name": "identify", "title": "Identify (wiggle)", "description": "wiggles the board", "danger": "moves", "scope": "node", "confirm": "", "kind": "touches"},
           {"name": "open_console", "title": "Open console (tview)", "description": "opens tview", "danger": "query", "scope": "node", "confirm": "", "kind": "touches"}]
emit({"record": "entity", "path": "", "name": spec.get("name", "thing " + serial), "kind": "device",
      "identity": {"serial_number": spec.get("sdk_serial", serial), "model_id": spec.get("model_id", "")}})
emit({"record": "status", "level": "ok", "name": "ran", "path": ""})
if spec.get("camera"):
    emit({"record": "status", "level": "ok", "name": "D405: streams", "path": "", "message": "depth + colour", "expected_period_s": 30})
    raise SystemExit
emit({"record": "entity", "path": "can", "name": "CAN bus", "kind": "bus", "part_of": ""})
if spec.get("hold"):  # tview has the port: nothing is opened, nothing is reported about any board
    emit({"record": "status", "level": "unknown", "name": "CAN bus", "path": "can", "code": "PortBusy",
          "message": "blocked: port held by 640623 tview"})
    raise SystemExit
answer = [int(can_id) for can_id in spec.get("answer", [])]
silent = [can_id for can_id in expected if can_id not in answer]
if silent:
    emit({"record": "status", "level": "warn", "name": "CAN bus", "path": "can", "code": "ExpectedBoardsSilent", "expected_period_s": 30,
          "message": f"{len(set(expected) & set(answer))} of {len(expected)} expected boards answer · " + ", ".join(f"CAN {i}" for i in silent) + " silent",
          "caused_by": [f"can/{i}" for i in silent]})
else:
    emit({"record": "status", "level": "ok", "name": "CAN bus", "path": "can", "message": "boards answer", "expected_period_s": 30})
for can_id in answer:
    emit({"record": "entity", "path": f"can/{can_id}", "name": f"CAN {can_id}", "kind": "node", "via": "can", "icon": "servo",
          "hardware_id": "moteus:" + spec.get("uuids", {}).get(str(can_id), f"{serial}-{can_id}"), "actions": actions,
          "identity": {"model": "moteus-c1", "model_id": "mjbots-moteus-c1"}})
    emit({"record": "status", "level": "warn" if can_id in spec.get("warn", []) else "ok", "name": f"CAN {can_id}: servo", "path": f"can/{can_id}",
          "code": "Limiter" if can_id in spec.get("warn", []) else "", "message": "torque limited" if can_id in spec.get("warn", []) else "stopped", "expected_period_s": 30})
'''
RIG_FILE = f'''version = 1

[[rig]]
name = "1-DOF test rack"

[[rig.device]]
name = "fdcanusb {A}"
kind = "adapter"
hardware_id = "usb:0483:5740:{A}"
nodes = ["can/1", "can/14", "can/15", "can/21"]

[[rig.device]]
name = "fdcanusb {B}"
kind = "adapter"
hardware_id = "usb:0483:5740:{B}"
nodes = ["can/11", "can/12"]
'''
RACK = {A: {"name": f"fdcanusb {A}", "model_id": "mjbots-fdcanusb", "answer": [1, 14, 21]},
        CAM: {"name": "RealSense D405 · 2186", "sdk_serial": "218622274411", "model_id": "realsense-d405", "camera": True}}


class FileClock(SetClock):
    """A host clock moved by writing seconds to a file (START + the number): the browser test ages the tree with it."""

    def __init__(self, path: Path):
        super().__init__(START)
        self.path = path
        self.utc_offset_s = -7 * 3600

    def now(self) -> float:
        try:
            return START + float(self.path.read_text().strip() or 0)
        except (OSError, ValueError):
            return START


def devices() -> list[dict]:
    adapter = usb_device("1-3", "0483", "5740", A, label="fdcanusb")
    adapter["attrs"]["usb_version"] = "2.00"
    camera = usb_device("1-4", "8086", "0b5b", CAM, label="Intel(R) RealSense(TM) Depth Camera 405")
    camera["attrs"].update(usb_version="3.20", speed_mbps=5000)
    keyboard = usb_device("1-5", "046d", "c34b", "KB1", label="DURGOD Taurus K320", kind="hid")
    mouse = usb_device("1-6", "046d", "c092", "MS1", label="G203 LIGHTSYNC Gaming Mouse", kind="hid")
    webcam = usb_device("1-7", "046d", "08e5", "WC1", label="Logi Webcam C920e")
    for each in (keyboard, mouse, webcam):
        each["attrs"]["speed_mbps"] = 480
    bus = usb_bus(1)
    bus["label"] = "USB bus 1 · 480 Mb/s"
    return [host_dict(), bus, adapter, camera, keyboard, mouse, webcam]


def build(directory: Path, *, ledger: bool = False, state_file: bool = False) -> Host:
    """The host over the scenario, with its files under ``directory``. Nothing is started: ``run_all_once`` runs it."""

    directory.mkdir(parents=True, exist_ok=True)
    plugins = directory / "plugins.d"
    plugins.mkdir(exist_ok=True)
    (plugins / "rack_plugin.py").write_text(PLUGIN)
    (plugins / "rack.toml").write_text(
        f'name = "rack"\ncommand = ["{{python}}", "{plugins / "rack_plugin.py"}"]\ntimeout_s = 20\nperiod_s = 30\n'
        f'matches = ["usb:0483:5740", "usb:8086:0b5b"]\nenv = {{ ROSTER_RACK = "{directory / "rack.json"}" }}\n')
    if not (directory / "rack.json").exists():
        (directory / "rack.json").write_text(json.dumps(RACK))
    (directory / "expected.toml").write_text(RIG_FILE) if not (directory / "expected.toml").exists() else None
    (directory / "clock.txt").write_text("0") if not (directory / "clock.txt").exists() else None
    return Host(plugins_dir=plugins, clock=FileClock(directory / "clock.txt"), os_source=FakeOs(devices(), hostname="bam-GPU"),
                expected_file=directory / "expected.toml", state_file=(directory / "seen.json") if state_file else None,
                ledger_file=(directory / "tree.json") if ledger else None)


def set_rack(directory: Path, **changes) -> None:
    rack = json.loads((directory / "rack.json").read_text())
    for serial, change in changes.items():
        rack.setdefault(serial, {}).update(change)
    (directory / "rack.json").write_text(json.dumps(rack))


def serve(directory: Path, port: int, ledger: bool, aged: bool) -> int:
    from io_diagnostics.server import make_server

    host = build(directory, ledger=ledger, state_file=ledger)
    host.run_all_once()
    if aged:  # CAN 21 answered once, ten minutes ago, and has been quiet since
        set_rack(directory, **{A: {"answer": [1, 14]}})
        (directory / "clock.txt").write_text("600")
        host.run_all_once()
    server = make_server(host, port, "127.0.0.1")
    print(f"IO diagnostics on {host.url} · roster scenario", flush=True)
    control = directory / "control.json"

    def watch() -> None:
        seen = control.stat().st_mtime_ns if control.exists() else 0
        while True:
            time.sleep(0.25)
            try:
                mtime = control.stat().st_mtime_ns if control.exists() else 0
                if mtime != seen:
                    seen = mtime
                    host.run_all_once()
                else:
                    host.refresh()
            except Exception as error:  # noqa: BLE001 - a test scenario keeps serving
                print(f"scenario: {type(error).__name__}: {error}", file=sys.stderr, flush=True)

    threading.Thread(target=watch, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--aged", action="store_true", help="CAN 21 answered ten minutes ago and has been silent since")
    parser.add_argument("--ledger", action="store_true", help="keep the last-known tree and the seen devices under --dir")
    arguments = parser.parse_args()
    raise SystemExit(serve(arguments.dir, arguments.port, arguments.ledger, arguments.aged))
