"""The expected setup (docs/expected-setup.md): a rig manifest drawn as one composite row per rig, passed to each
plugin as IO_DIAGNOSTICS_EXPECTED, edited from the panel (Expect what's here / Forget / Retire...) and the CLI, plus
the dead-adapter comparison (FramesNotSent while another adapter works).

The rack of 2026-10-23: fdcanusb 272813EF (CAN 1, 14; CAN 15's board being replaced), fdcanusb 0607623A (CAN 1, 11,
12, 13), two D405s known by their RealSense serials, and fdcanusb 28B3A25D retired after it never put a frame on the
wire. Everything here is a fake: a stdlib plugin subprocess, skeleton dicts, temporary files.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

import pytest
from test_host_support import ROOT, FakeOs, SetClock, standard_skeleton, usb_device

from io_diagnostics import expected as expected_module
from io_diagnostics.host import SEEN_FILE, Host, make_host
from io_diagnostics.tree import HOST_PATH
from io_diagnostics.server import make_server, serve_in_thread

NOW = 1792800000.0  # 2026-10-23
A, B, C, DEAD = "272813EF", "0607623A", "11112222", "28B3A25D"
CAM1_USB, CAM1_SDK = "219323070276", "218622274411"
CAM2_USB, CAM2_SDK = "130523071221", "218622276036"
REASON = "frames never reached the bus (2026-10-22); replaced by 0607623A"

# One stdlib plugin for adapters and cameras: what it reports per USB serial comes from $RIG (JSON). It logs the
# IO_DIAGNOSTICS_EXPECTED it was given, per device, to $RIG_LOG.
RIG_PLUGIN = r'''
import json, os, sys
args = sys.argv[1:]
verb = next(word for word in args if word in ("describe", "diagnose", "act"))
if verb == "describe":
    print(json.dumps({"name": "rig", "schema": "diagnostics-record/0", "expected": json.loads(os.environ.get("RIG_EXPECTED", "[]"))}))
    raise SystemExit
device = json.loads(os.environ["IO_DIAGNOSTICS_DEVICE"])
serial = device.get("serial") or ""
spec = json.load(open(os.environ["RIG_FILE"])).get(serial, {})  # re-read on every run: a test changes the rack between runs
with open(os.environ["RIG_LOG"], "a") as log:
    log.write(json.dumps({"serial": serial, "expected": os.environ.get("IO_DIAGNOSTICS_EXPECTED")}) + "\n")
def emit(record):
    print(json.dumps({"schema": "diagnostics-record/0", **record}))
emit({"record": "entity", "path": "", "name": spec.get("name", "thing " + serial), "kind": "device",
      "identity": {"serial_number": spec.get("sdk_serial", serial), "model_id": spec.get("model_id", "")}})
emit({"record": "status", "level": "ok", "name": "ran", "path": ""})
if spec.get("camera"):
    raise SystemExit
emit({"record": "entity", "path": "can", "name": "CAN bus", "kind": "bus", "part_of": ""})
silent = spec.get("silent", [])
if spec.get("hold"):  # tview has the port: nothing is opened, so nothing is reported about any board
    emit({"record": "status", "level": "unknown", "name": "CAN bus", "path": "can", "code": "PortBusy",
          "message": "blocked: port held by 640623 tview"})
    raise SystemExit
if spec.get("frames_not_sent"):
    emit({"record": "status", "level": "error", "name": "CAN bus", "path": "can", "code": "FramesNotSent",
          "message": "no board answered, and this adapter's frames never reached the bus"})
elif silent:
    # the moteus_servo contract: a board that did not answer has no row; its bus names it
    emit({"record": "status", "level": "warn", "name": "CAN bus", "path": "can", "code": "ExpectedBoardsSilent",
          "message": f"{len(spec.get('answer', []))} of {len(spec.get('answer', [])) + len(silent)} expected boards answer"
                     f" · {', '.join('CAN %s' % can_id for can_id in silent)} silent",
          "caused_by": [f"can/{can_id}" for can_id in silent], "expected_period_s": 30})
else:
    emit({"record": "status", "level": "ok", "name": "CAN bus", "path": "can"})
for can_id in spec.get("answer", []):
    emit({"record": "entity", "path": f"can/{can_id}", "name": f"CAN {can_id}", "kind": "node", "via": "can",
          "hardware_id": "moteus:" + spec.get("uuids", {}).get(str(can_id), f"{serial}-{can_id}"),
          "identity": {"model_id": spec.get("node_model", "mjbots-moteus-c1")}})
    emit({"record": "status", "level": "ok", "name": f"CAN {can_id}: servo", "path": f"can/{can_id}", "message": "stopped",
          "expected_period_s": 30})
    if str(can_id) in spec.get("ratios", {}):
        emit({"record": "status", "level": "ok", "name": f"CAN {can_id}: config", "path": f"can/{can_id}", "message": "read-only",
              "expected_period_s": 30, "measurements": [{"key": "rotor_to_output_ratio", "value": spec["ratios"][str(can_id)], "unit": "1"}]})
'''

RACK = {A: {"name": f"fdcanusb {A}", "model_id": "mjbots-fdcanusb", "answer": [1, 14], "silent": [15]},
        B: {"name": f"fdcanusb {B}", "model_id": "mjbots-fdcanusb", "answer": [1, 11, 12, 13], "node_model": "mjbots-moteus-r4-11"},
        CAM1_USB: {"name": f"RealSense D405 {CAM1_SDK}", "sdk_serial": CAM1_SDK, "model_id": "realsense-d405", "camera": True},
        CAM2_USB: {"name": f"RealSense D405 {CAM2_SDK}", "sdk_serial": CAM2_SDK, "model_id": "realsense-d405", "camera": True}}

SEED = f'''version = 1

[[rig]]
name = "1-DOF test rack"

[[rig.device]]
name = "fdcanusb {A}"
kind = "adapter"
hardware_id = "usb:0483:5740:{A}"
model_id = "mjbots-fdcanusb"
nodes = [
  {{ path = "can/1", model_id = "mjbots-moteus-c1" }},
  {{ path = "can/14", model_id = "mjbots-moteus-c1" }},
  {{ path = "can/15", model_id = "mjbots-moteus-c1" }},
]

[[rig.device]]
name = "fdcanusb {B}"
kind = "adapter"
hardware_id = "usb:0483:5740:{B}"
nodes = ["can/1", "can/11", "can/12", "can/13"]

[[rig.device]]
name = "RealSense D405 {CAM1_SDK}"
kind = "camera"
serial = "{CAM1_SDK}"
nodes = []

[[rig.device]]
name = "RealSense D405 {CAM2_SDK}"
kind = "camera"
serial = "{CAM2_SDK}"
nodes = []

[[retired]]
name = "fdcanusb {DEAD}"
hardware_id = "usb:0483:5740:{DEAD}"
date = "2026-10-23"
reason = "{REASON}"
'''


def adapter(port: str, serial: str) -> dict:
    return usb_device(port, "0483", "5740", serial, label="fdcanusb")


def camera(port: str, serial: str) -> dict:
    return usb_device(port, "8086", "0b5b", serial, label="Intel(R) RealSense(TM) Depth Camera 405")


PRESENT = (adapter("9-3", B), adapter("9-5", A), camera("9-6", CAM1_USB), camera("9-7", CAM2_USB))


class Rack:
    """A host over a fake rack: ``rack`` says what each adapter's plugin reports, ``devices`` what is on USB."""

    def __init__(self, tmp_path, *, manifest: str | None = SEED, rack=None, devices=PRESENT, describe_expected=(),
                 state_file=None, ledger_file=None, at=NOW, run=True):
        self.tmp = tmp_path
        self.log = tmp_path / "rig.log"
        self.file = tmp_path / "config" / "expected.toml"
        if manifest is not None:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(manifest)
        plugins = tmp_path / "plugins.d"
        plugins.mkdir(parents=True, exist_ok=True)
        script = plugins / "rig_plugin.py"
        script.write_text(RIG_PLUGIN)
        self.rack_file = tmp_path / "rack.json"
        self.rack_file.write_text(json.dumps(rack or RACK))
        env = {"RIG_FILE": str(self.rack_file), "RIG_LOG": str(self.log), "RIG_EXPECTED": json.dumps(list(describe_expected))}
        (plugins / "rig.toml").write_text(
            f'name = "rig"\ncommand = ["{{python}}", "{script}"]\ntimeout_s = 20\nperiod_s = 30\n'
            'matches = ["usb:0483:5740", "usb:8086:0b5b"]\n'
            "env = { " + ", ".join(f"{key} = '{value}'" for key, value in env.items()) + " }\n")
        self.host = Host(plugins_dir=plugins, clock=SetClock(at), os_source=FakeOs(standard_skeleton(*devices)),
                         expected_file=self.file, state_file=state_file, ledger_file=ledger_file)
        self.host.run_all_once() if run else self.host.refresh()

    def set_rack(self, rack: dict) -> None:
        """What the plugin reports from its next run on."""

        self.rack_file.write_text(json.dumps(rack))

    def state(self) -> dict:
        return self.host.state()

    def rig_row(self) -> dict:
        return next(node for node in self.state()["tree"]["children"] if node["kind"] == "rig")

    def check(self, path: str, suffix: str = "") -> dict:
        return next(check for check in self.host.node(path)["checks"] if check["name"].endswith(suffix))

    def path_of(self, serial: str) -> str:
        return next(path for path, entity in self.host.view().merged.entities.items()
                    if entity.kind == "device" and entity.hardware_id.endswith(":" + serial))

    def logged(self) -> dict:
        return {entry["serial"]: entry["expected"] for entry in map(json.loads, self.log.read_text().splitlines())}


def values(check: dict) -> dict:
    return {pair["key"]: pair["value"] for pair in check["values"]}


# ----- the rig row ----------------------------------------------------------------------------------------------
def test_the_rig_row_counts_at_a_glance_and_names_what_is_missing(tmp_path):
    rack = Rack(tmp_path)
    tree = rack.state()["tree"]
    rig = tree["children"][0]  # WHY first: it is the at-a-glance answer
    assert (rig["kind"], rig["path"], rig["icon"]) == ("rig", "host/rig-1-dof-test-rack", "group")
    assert rig["name"] == "1-DOF test rack · 2/2 adapters · 6/7 boards · 2/2 cameras"
    check = rack.check(rig["path"])
    assert (rig["level"], check["code"]) == ("warn", "ExpectedMissing")
    assert check["message"] == f"missing: CAN 15 on fdcanusb {A}"
    assert values(check)["what_to_check"].startswith(f"1. CAN 15 on fdcanusb {A} does not answer: see its bus's status")
    # record v0.1 caused_by (2026-10-23): a board that never answered has no row (Zach, 2026-10-24), so the rig names the
    # row that says it, the adapter's CAN bus ("2 of 3 expected boards answer · CAN 15 silent"); one missing board is one
    # cause, the bus's report; the rig row keeps its WARN and its words (above) and reads as the bus's knock-on
    bus = f"{rack.path_of(A)}/can"
    assert f"{rack.path_of(A)}/can/15" not in rack.host.view().merged.entities
    assert rack.check(bus)["message"] == "2 of 3 expected boards answer · CAN 15 silent"
    assert check["caused_by"] == [bus] and rig["knock_on_of"] == bus
    attention = rack.state()["summary"]["attention"]
    assert [item["path"] for item in attention if item["path"] in (rig["path"], bus)] == [bus]
    assert rack.state()["summary"]["counts"] == {"warn": 1}


def test_everything_here_is_ok_and_an_extra_node_is_a_note_not_a_problem(tmp_path):
    rack = Rack(tmp_path, rack={**RACK, A: {**RACK[A], "answer": [1, 14, 15], "silent": []},
                                B: {**RACK[B], "answer": [1, 2, 11, 12, 13]}})
    rig = rack.rig_row()
    check = rack.check(rig["path"])
    assert rig["name"] == "1-DOF test rack · 2/2 adapters · 7/7 boards · 2/2 cameras"
    assert (rig["level"], check["code"]) == ("ok", "")
    assert check["message"] == f"everything expected is here · also here, not expected: CAN 2 on fdcanusb {B}"
    assert values(check)["not_expected"] == f"CAN 2 on fdcanusb {B}"
    assert rig["path"] not in [item["path"] for item in rack.state()["summary"]["attention"]]


def test_a_node_whose_check_could_not_run_is_not_checked_not_missing(tmp_path):
    rack = Rack(tmp_path, rack={**RACK, A: {**RACK[A], "answer": [1, 14, 15], "silent": []}})
    rack.host.clock.at = NOW + 3600  # every board's last answer is past STALE (3 x 30 s): nothing fresh either way
    rig = rack.rig_row()
    check = rack.check(rig["path"])
    assert (rig["level"], check["code"]) == ("unknown", "ExpectedNotChecked")
    assert rig["name"] == "1-DOF test rack · 2/2 adapters · ?/7 boards · 2/2 cameras"


def test_a_node_its_driver_never_reports_is_missing_once_the_driver_ran(tmp_path):
    seed = SEED.replace('{ path = "can/15", model_id = "mjbots-moteus-c1" },', '{ path = "can/15", model_id = "mjbots-moteus-c1" },\n  "can/16",')
    rack = Rack(tmp_path, manifest=seed)
    assert rack.check(rack.rig_row()["path"])["message"] == f"missing: CAN 15 on fdcanusb {A} · CAN 16 on fdcanusb {A}"
    before = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(NOW), os_source=FakeOs(standard_skeleton(*PRESENT)),
                  expected_file=rack.file)
    before.refresh()  # no plugin has run yet: nobody looked, so nothing is missing, it is not checked
    rig = next(node for node in before.state()["tree"]["children"] if node["kind"] == "rig")
    # WHY no count: a count taken before any driver has looked is not a count (a restarted host said "0/7 boards" and "?")
    assert (rig["level"], rig["name"]) == (None, "1-DOF test rack · checking…")


def test_a_ratio_that_differs_from_the_file_is_one_warning_on_that_board(tmp_path):
    """0607623A, 2026-10-23: CAN 13 drives tag 6 through a 10:1 gearbox, but its config says 0.1667."""

    seed = SEED.replace('nodes = ["can/1", "can/11", "can/12", "can/13"]', 'nodes = [\n'
                        '  { path = "can/1", note = "tag 5 · direct", rotor_to_output_ratio = 1.0 },\n'
                        '  { path = "can/11", note = "link tags 8-12 · 10:1", rotor_to_output_ratio = 0.1 },\n'
                        '  "can/12",\n  { path = "can/13", note = "tag 6 · 10:1", rotor_to_output_ratio = 0.1 },\n]')
    rack = Rack(tmp_path, manifest=seed, rack={**RACK, A: {**RACK[A], "answer": [1, 14, 15], "silent": []},
                                               B: {**RACK[B], "ratios": {"1": 1.0, "11": 0.1000001, "13": 0.1667}}})
    can13 = f"{rack.path_of(B)}/can/13"
    check = rack.check(can13, ": expected ratio")
    assert (check["level"], check["code"], check["message"]) == ("warn", "RatioMismatch", "config ratio 0.1667, expected 0.1 (10:1 gearbox)")
    assert values(check)["note"] == "tag 6 · 10:1"
    assert rack.host.node(can13)["node"]["level"] == "warn"
    for quiet in ("can/1", "can/11", "can/12"):  # equal (float noise), or no expected ratio
        assert not [check for check in rack.host.node(f"{rack.path_of(B)}/{quiet}")["checks"] if check["code"] == "RatioMismatch"]
    assert rack.rig_row()["level"] == "ok"  # the board says it; the rig row only counts
    assert rack.host.node(rack.path_of(B))["setup"]["node_notes"]["can/13"] == "tag 6 · 10:1"
    assert rack.host.setup_edit("expect", rack.rig_row()["path"])["ok"]  # a re-snapshot keeps what the person wrote
    kept = expected_module.parse(rack.file.read_text()).rigs[0].devices[1].nodes
    assert [(node.path, node.note, node.rotor_to_output_ratio) for node in kept if node.path == "can/13"] == [("can/13", "tag 6 · 10:1", 0.1)]
    assert expected_module.gearbox_text(1.0) == "direct drive" and expected_module.gearbox_text(0.1) == "10:1 gearbox"


def test_cameras_are_matched_by_the_serial_their_driver_reports(tmp_path):
    rack = Rack(tmp_path, devices=PRESENT[:3])  # camera 2 (SDK 218622276036) is unplugged
    rig = rack.rig_row()
    assert rig["name"] == "1-DOF test rack · 2/2 adapters · 6/7 boards · 1/2 cameras"
    assert f"RealSense D405 {CAM2_SDK} (not here)" in rack.check(rig["path"])["message"]
    # a missing thing is a status on the Computer, never a row (Zach, 2026-10-24)
    check = rack.check(HOST_PATH, f"RealSense D405 {CAM2_SDK}: presence")
    assert (check["level"], check["code"]) == ("warn", "ExpectedAbsent")
    assert check["message"] == f"RealSense D405 {CAM2_SDK} expected (1-DOF test rack), no connected device reports serial {CAM2_SDK}"
    assert not [path for path in rack.host.view().merged.entities if "serial-" in path or path.endswith("/expected")]


def test_an_absent_adapter_with_a_usb_id_gets_the_kernel_evidence_row(tmp_path):
    rack = Rack(tmp_path, devices=(PRESENT[0], *PRESENT[2:]))  # 272813EF unplugged
    rig = rack.rig_row()
    assert rig["name"] == "1-DOF test rack · 1/2 adapters · 4/7 boards · 2/2 cameras"
    assert rack.check(rig["path"])["message"] == f"missing: fdcanusb {A} (not here)"
    check = rack.check(HOST_PATH, f"fdcanusb {A}: presence")
    assert check["code"] == "ExpectedAbsent" and f"fdcanusb {A} expected (1-DOF test rack), not on USB" in check["message"]
    assert rack.host.node(HOST_PATH)["node"]["level"] == "warn"  # the Computer carries it


def test_the_rig_row_says_which_adapter_the_boards_moved_to(tmp_path):
    seed = SEED.replace(f'hardware_id = "usb:0483:5740:{B}"', f'hardware_id = "usb:0483:5740:{C}"').replace(f"fdcanusb {B}", f"fdcanusb {C}")
    rack = Rack(tmp_path, manifest=seed)  # C is expected with CAN 1, 11, 12, 13; it is gone and B (unlisted) answers them
    check = rack.check(rack.rig_row()["path"])
    assert check["message"] == (f"missing: CAN 15 on fdcanusb {A} · fdcanusb {C} (not here) · fdcanusb {C}'s boards"
                                f" answer on fdcanusb {B} now")
    assert rack.rig_row()["name"] == "1-DOF test rack · 1/2 adapters · 6/7 boards · 2/2 cameras"
    assert f"use Expect what's here on fdcanusb {B}, then Retire… on fdcanusb {C}" in values(check)["what_to_check"]


# ----- what each plugin is told ---------------------------------------------------------------------------------
def test_each_expected_device_gets_its_nodes_and_any_other_gets_nothing(tmp_path):
    seed = SEED.replace(f'[[rig.device]]\nname = "fdcanusb {B}"', f'[[rig.device]]\nname = "fdcanusb {C}"').replace(
        f"usb:0483:5740:{B}", f"usb:0483:5740:{C}")
    rack = Rack(tmp_path, manifest=seed)
    first = rack.logged()
    assert json.loads(first[A]) == {"nodes": ["can/1", "can/14", "can/15"], "rig": "1-DOF test rack"}
    assert first[B] == ""  # not in the expected setup: the driver uses its own source (moteus: the rack inventory)
    # a camera listed only by its SDK serial is known from its driver's first report on: the next run carries it
    assert first[CAM1_USB] == ""
    rack.host.run_all_once()
    assert json.loads(rack.logged()[CAM1_USB]) == {"nodes": [], "rig": "1-DOF test rack"}


def test_a_fixture_reads_no_manifest_unless_named_and_live_reads_the_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "expected.toml").write_text(SEED)
    assert make_host(fixture="F1", once=True).setup.path is None
    assert make_host(plugins_dir=tmp_path / "none", once=True).setup.path == tmp_path / "cfg" / "expected.toml"
    named = make_host(fixture="F1", once=True, expected=tmp_path / "cfg" / "expected.toml")
    assert [rig.name for rig in named.setup.manifest.rigs] == ["1-DOF test rack"]


# ----- retired --------------------------------------------------------------------------------------------------
def test_a_retired_adapter_plugged_in_again_says_so_at_once(tmp_path):
    rack = Rack(tmp_path, devices=(*PRESENT, adapter("9-1", DEAD)))
    path = rack.path_of(DEAD)
    check = rack.check(path, ": retired")
    assert (check["level"], check["code"]) == ("warn", "RetiredDevice")
    assert check["message"] == f"retired 2026-10-23: {REASON}"
    assert rack.host.node(path)["node"]["level"] == "warn"
    assert rack.state()["expected_setup"]["retired"][0]["path"] == path


def test_a_retired_adapter_unplugged_is_never_expected_even_if_its_driver_lists_it(tmp_path):
    listed = [{"hardware_id": f"usb:0483:5740:{DEAD}", "name": f"fdcanusb {DEAD}", "source": "rack inventory"}]
    rack = Rack(tmp_path, describe_expected=listed)
    assert not [check for check in rack.host.node(HOST_PATH)["checks"] if check["name"] == f"fdcanusb {DEAD}: presence"]
    rack.file.write_text(SEED.split("[[retired]]")[0])  # un-retire by hand: the host re-reads the file
    rack.host.refresh()
    assert [check for check in rack.host.node(HOST_PATH)["checks"] if check["name"] == f"fdcanusb {DEAD}: presence"]


# ----- the dead adapter: another one works on this PC -------------------------------------------------------------
def test_frames_not_sent_names_the_adapter_that_works(tmp_path):
    rack = Rack(tmp_path, devices=(*PRESENT, adapter("9-2", C)),
                rack={**RACK, C: {"name": f"fdcanusb {C}", "frames_not_sent": True}})
    bus = f"{rack.path_of(C)}/can"
    note = rack.check(bus, ": another adapter")
    assert (note["level"], note["code"], note["source"]) == ("warn", "OtherAdapterWorks", "io_diagnostics.host")
    assert note["message"] in (f"fdcanusb {B} works on this PC: plug this bus into it to tell adapter from bus",
                               f"fdcanusb {A} works on this PC: plug this bus into it to tell adapter from bus")
    assert rack.host.node(bus)["node"]["level"] == "error"  # the driver's own verdict stands


def test_frames_not_sent_alone_gets_no_note(tmp_path):
    rack = Rack(tmp_path, manifest=None, devices=(adapter("9-2", C),),
                rack={C: {"name": f"fdcanusb {C}", "frames_not_sent": True}})
    assert [check["code"] for check in rack.host.node(f"{rack.path_of(C)}/can")["checks"]] == ["FramesNotSent"]


# ----- edits: Expect what's here / Forget / Retire... --------------------------------------------------------------
def test_expect_whats_here_adds_a_device_with_the_nodes_answering_now(tmp_path):
    rack = Rack(tmp_path, devices=(*PRESENT, adapter("9-2", C)), rack={**RACK, C: {"name": f"fdcanusb {C}", "answer": [3, 4]}})
    path = rack.path_of(C)
    setup = rack.host.node(path)["setup"]
    assert (setup["role"], setup["expected"], setup["nodes_here"]) == ("device", False, ["can/3", "can/4"])
    assert setup["can"] == {"expect": True, "forget": False, "retire": True}
    assert setup["confirm"]["expect"] == f"Add fdcanusb {C} to 1-DOF test rack, expecting can/3, can/4?"
    result = rack.host.setup_edit("expect", path)
    assert result["ok"] and result["message"] == f"done: Add fdcanusb {C} to 1-DOF test rack, expecting can/3, can/4"
    device = expected_module.parse(rack.file.read_text()).rigs[0].devices[-1]
    assert (device.hardware_id, device.kind, [node.path for node in device.nodes]) == (f"usb:0483:5740:{C}", "device", ["can/3", "can/4"])
    assert device.nodes[0].hardware_id == f"moteus:{C}-3"
    assert rack.rig_row()["name"] == "1-DOF test rack · 2/2 adapters · 8/9 boards · 2/2 cameras · 1/1 device"


def test_expect_whats_here_on_the_rig_row_takes_the_nodes_as_they_answer_now(tmp_path):
    rack = Rack(tmp_path)
    rig = rack.rig_row()["path"]
    assert rack.host.node(rig)["setup"]["confirm"]["expect"] == (
        f"Expect what is here now in 1-DOF test rack: fdcanusb {A}: can/15 no longer expected?")
    assert rack.host.setup_edit("expect", rig)["ok"]
    assert rack.rig_row()["name"] == "1-DOF test rack · 2/2 adapters · 6/6 boards · 2/2 cameras"
    assert rack.rig_row()["level"] == "ok"


def test_expect_needs_a_rig_when_there_are_several(tmp_path):
    rack = Rack(tmp_path, manifest=SEED + '\n[[rig]]\nname = "Bench"\n', devices=(*PRESENT, adapter("9-2", C)),
                rack={**RACK, C: {"name": f"fdcanusb {C}", "answer": [3]}})
    path = rack.path_of(C)
    assert rack.host.node(path)["setup"]["needs_rig"] is True
    refused = rack.host.setup_edit("expect", path)
    assert (refused["ok"], refused["refused"], refused["rigs"]) == (False, "RigRequired", ["1-DOF test rack", "Bench"])
    assert rack.host.setup_edit("expect", path, rig="Bench")["ok"]
    assert [device.name for device in expected_module.parse(rack.file.read_text()).rigs[1].devices] == [f"fdcanusb {C}"]


def test_forget_by_id_drops_an_absent_device_from_the_file_and_the_memory(tmp_path):
    """No row to click any more (Zach, 2026-10-24): an absent expected device is forgotten by its id (the CLI)."""

    state = tmp_path / "state" / SEEN_FILE
    listed = [{"hardware_id": f"usb:0483:5740:{A}", "name": f"fdcanusb {A}", "source": "rack inventory"}]
    Rack(tmp_path / "before", state_file=state)  # A was seen and remembered
    rack = Rack(tmp_path, devices=(PRESENT[0], *PRESENT[2:]), describe_expected=listed, state_file=state)
    name = f"fdcanusb {A}: presence"
    assert [check for check in rack.host.node(HOST_PATH)["checks"] if check["name"] == name]
    assert rack.host.setup_edit_ids("forget", hardware_id=f"usb:0483:5740:{A}", name=f"fdcanusb {A}")["ok"]
    rack.host.refresh()
    assert not [check for check in rack.host.node(HOST_PATH)["checks"] if check["name"] == name]  # the driver still lists it: forgotten wins
    assert f"usb:0483:5740:{A}" not in rack.file.read_text()
    saved = json.loads(state.read_text())
    assert f"usb:0483:5740:{A}" not in saved["devices"] and f"usb:0483:5740:{A}" in saved["forgotten"]
    rack.host.refresh()
    assert not [check for check in rack.host.node(HOST_PATH)["checks"] if check["name"] == name]
    assert rack.rig_row()["name"] == "1-DOF test rack · 1/1 adapter · 4/4 boards · 2/2 cameras"


def test_dont_expect_on_a_device_row_takes_it_out_of_the_file_and_leaves_it_on_the_bus(tmp_path):
    rack = Rack(tmp_path)
    path = rack.path_of(A)
    setup = rack.host.node(path)["setup"]
    assert setup["can"] == {"expect": True, "forget": True, "retire": True}
    assert setup["confirm"]["forget"] == f"Stop expecting fdcanusb {A}? It leaves the expected setup (1-DOF test rack); it stays on the bus as an ordinary device."
    assert rack.host.setup_edit("forget", path)["ok"]
    assert f"usb:0483:5740:{A}" not in rack.file.read_text()
    assert path in rack.host.view().merged.entities
    assert rack.host.setup_edit("forget", path)["refused"] == "NotExpected"


def test_a_board_can_be_removed_from_the_bus_and_comes_back_when_it_answers(tmp_path):
    """Zach, 2026-10-24: "a button there to say remove from the bus". The row is the host's memory; the plugin still
    reports the board, so it comes back on the next run."""

    rack = Rack(tmp_path)
    can14 = f"{rack.path_of(A)}/can/14"
    setup = rack.host.node(can14)["setup"]
    assert (setup["role"], setup["expected"], setup["can"]) == ("board", True, {"expect": True, "forget": True, "retire": False})
    assert setup["confirm"]["forget"] == "Remove CAN 14 from the bus? RobotDx forgets the row; if CAN 14 answers again, it comes back."
    assert setup["confirm"]["expect"] == f"Stop expecting CAN 14 on fdcanusb {A}? 1-DOF test rack no longer counts it, and its bus stops saying it is silent."
    assert rack.host.setup_edit("forget", can14)["ok"]
    assert can14 not in rack.host.view().merged.entities and f"{can14}/x" not in rack.host.view().merged.entities
    assert "CAN 14" in rack.file.read_text() or "can/14" in rack.file.read_text()  # still expected: Remove from bus is not Don't expect
    rack.host.run_all_once()
    assert can14 in rack.host.view().merged.entities


def test_expect_on_a_board_toggles_whether_its_adapter_expects_it(tmp_path):
    rack = Rack(tmp_path)
    can14 = f"{rack.path_of(A)}/can/14"
    assert rack.host.setup_edit("expect", can14)["ok"]  # it was expected: Don't expect
    assert "can/14" not in rack.file.read_text()
    assert rack.rig_row()["name"] == "1-DOF test rack · 2/2 adapters · 5/6 boards · 2/2 cameras"
    setup = rack.host.node(can14)["setup"]
    assert setup["expected"] is False and setup["confirm"]["expect"] == f"Expect CAN 14 on fdcanusb {A} in 1-DOF test rack? Its bus then says when it is silent."
    assert rack.host.setup_edit("expect", can14)["ok"]
    assert "can/14" in rack.file.read_text()
    stranger = Rack(tmp_path / "other", manifest=None)
    assert stranger.host.setup_edit("expect", f"{stranger.path_of(A)}/can/14")["refused"] == "NotExpected"  # its adapter is not in the file


def test_retire_needs_a_reason_then_the_row_says_it_when_plugged_in(tmp_path):
    rack = Rack(tmp_path)
    path = rack.path_of(A)
    assert rack.host.setup_edit("retire", path, reason="  ")["refused"] == "ReasonRequired"
    assert rack.host.node(path)["setup"]["confirm"]["retire"] == (
        f"Retire fdcanusb {A}? It leaves 1-DOF test rack. Plugged in again, its row says “retired 2026-10-23: <reason>” (a warning).")
    assert rack.host.setup_edit("retire", path, reason="CAN 15 port loose")["ok"]
    manifest = expected_module.parse(rack.file.read_text())
    assert [device.name for device in manifest.rigs[0].devices] == [f"fdcanusb {B}", f"RealSense D405 {CAM1_SDK}", f"RealSense D405 {CAM2_SDK}"]
    assert (manifest.retired[-1].hardware_id, manifest.retired[-1].date, manifest.retired[-1].reason) == (
        f"usb:0483:5740:{A}", "2026-10-23", "CAN 15 port loose")
    assert rack.check(path, ": retired")["message"] == "retired 2026-10-23: CAN 15 port loose"
    assert rack.host.setup_edit("expect", path)["ok"]  # Expect what's here takes it back into service
    assert not [entry for entry in expected_module.parse(rack.file.read_text()).retired if A in entry.hardware_id]


def test_an_unreadable_file_is_named_never_overwritten(tmp_path):
    rack = Rack(tmp_path, manifest='version = 1\n[[rig]]\nname = "x"\n[[rig.device]]\nname = "no id"\n')
    [source] = [source for source in rack.state()["host"]["sources"] if source["name"] == "expected setup"]
    assert "needs a hardware_id or a serial" in source["note"]
    before = rack.file.read_text()
    result = rack.host.setup_edit("expect", rack.path_of(A))
    assert (result["ok"], result["refused"]) == (False, "ManifestUnavailable")
    assert rack.file.read_text() == before
    assert rack.host.node(rack.path_of(A))["setup"]["can"] == {"expect": False, "forget": False, "retire": False}


def test_a_fixture_without_a_file_refuses_edits(tmp_path):
    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    adapter_path = next(path for path, entity in host.view().merged.entities.items() if entity.hardware_id.endswith(A))
    assert host.setup_edit("expect", adapter_path)["refused"] == "ManifestUnavailable"
    assert not [node for node in host.state()["tree"]["children"] if node["kind"] == "rig"]


# ----- the file -------------------------------------------------------------------------------------------------
def test_the_file_round_trips_through_its_own_writer():
    manifest = expected_module.parse(SEED)
    again = expected_module.parse(expected_module.dump(manifest))
    assert again == manifest
    assert [device.label() for device in manifest.rigs[0].devices][:2] == [f"fdcanusb {A}", f"fdcanusb {B}"]
    odd = expected_module.Manifest([expected_module.Rig('Rack "2" \\ ünïcode', [expected_module.ExpectedDevice(
        "x", serial="S\n1")])])
    assert expected_module.parse(expected_module.dump(odd)) == odd
    assert expected_module.dump(manifest).startswith("# IO diagnostics · expected setup")


@pytest.mark.parametrize("text, why", [
    ("version = 2\n", "this host reads version 1"),
    ("[[rig]]\nkind = 1\n", "name is required"),
    ('[[rig]]\nname = "r"\n[[rig.device]]\nname = "d"\nhardware_id = "h"\nnodes = [3]\n', "a node is a path string"),
    ("not toml = = 1", "not TOML"),
])
def test_a_broken_file_names_what_is_wrong(text, why):
    with pytest.raises(expected_module.ManifestError, match=re.escape(why)):
        expected_module.parse(text)


def test_the_documented_example_is_a_valid_file_and_is_the_seed():
    doc = (ROOT / "docs" / "expected-setup.md").read_text()
    [block] = re.findall(r"```toml\n(.*?)\n```", doc, re.S)
    manifest = expected_module.parse(block)
    assert [rig.name for rig in manifest.rigs] == ["1-DOF test rack"]
    assert [(device.label(), [node.path for node in device.nodes]) for device in manifest.rigs[0].devices] == [
        (f"fdcanusb {A}", ["can/1", "can/14", "can/15"]), (f"fdcanusb {B}", ["can/21", "can/11", "can/12", "can/13"]),
        (f"RealSense D405 {CAM1_SDK}", []), (f"RealSense D405 {CAM2_SDK}", [])]
    assert manifest.retired[0].hardware_id == f"usb:0483:5740:{DEAD}"
    can21 = manifest.rigs[0].devices[1].nodes[0]
    assert (can21.note, can21.rotor_to_output_ratio) == ("tag 5 · direct", 1.0)
    assert [node.rotor_to_output_ratio for node in manifest.rigs[0].devices[1].nodes[1:]] == [0.1, 0.1, 0.1]
    assert expected_module.parse(expected_module.dump(manifest)) == manifest


# ----- the server and the CLI ---------------------------------------------------------------------------------------
PAGE = {"Content-Type": "application/json", "X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin"}


def post(host, endpoint: str, body: dict, headers=PAGE) -> tuple[int, dict]:
    request = urllib.request.Request(host.url.rstrip("/") + endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_panel_endpoints_write_only_from_the_page(tmp_path):
    rack = Rack(tmp_path)
    http = make_server(rack.host, 0)
    serve_in_thread(http)
    try:
        path = rack.path_of(A)
        before = rack.file.read_text()
        status, answer = post(rack.host, "/api/v1/retire", {"path": path, "reason": "x"}, headers={"Content-Type": "application/json"})
        assert (status, answer["refused"]) == (403, "NotFromPage") and rack.file.read_text() == before
        status, answer = post(rack.host, "/api/v1/retire", {"path": path, "reason": "x"}, headers={**PAGE, "Sec-Fetch-Site": "cross-site"})
        assert (status, answer["refused"]) == (403, "NotSameOrigin")
        status, answer = post(rack.host, "/api/v1/retire", {"path": path, "reason": ""})
        assert (status, answer["refused"]) == (400, "ReasonRequired") and rack.file.read_text() == before
        status, answer = post(rack.host, "/api/v1/forget", {"path": "host/nope"})
        assert (status, answer["refused"]) == (404, "NoSuchEntity")
        status, answer = post(rack.host, "/api/v1/retire", {"path": path, "reason": "loose USB socket"})
        assert (status, answer["ok"]) == (200, True)
        assert 'reason = "loose USB socket"' in rack.file.read_text()
        status, answer = post(rack.host, "/api/v1/expect", {"path": path, "rig": None})
        assert (status, answer["ok"]) == (200, True)
        with urllib.request.urlopen(rack.host.url + "api/v1/state", timeout=10) as response:
            assert json.loads(response.read())["expected_setup"]["retired"][0]["name"] == f"fdcanusb {DEAD}"
    finally:
        http.shutdown()
        http.server_close()


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "io_diagnostics" / "cli.py"), *args], capture_output=True, text=True,
                          timeout=60)


def test_the_cli_expects_forgets_and_retires_by_id(tmp_path):
    file = tmp_path / "expected.toml"
    common = ("--plugins", str(tmp_path / "no-plugins"), "--expected", str(file))
    (tmp_path / "no-plugins").mkdir()
    done = cli("expect", *common, "--hardware-id", f"usb:0483:5740:{A}", "--name", f"fdcanusb {A}", "--kind", "adapter",
               "--nodes", "can/1,can/14", "--rig", "1-DOF test rack")
    assert done.returncode == 0, done.stderr
    device = expected_module.parse(file.read_text()).rigs[0].devices[0]
    assert (device.name, device.kind, [node.path for node in device.nodes]) == (f"fdcanusb {A}", "adapter", ["can/1", "can/14"])
    refused = cli("retire", *common, "--hardware-id", f"usb:0483:5740:{A}", "--reason", " ")
    assert refused.returncode == 2 and "ReasonRequired" in refused.stderr
    assert cli("retire", *common, "--hardware-id", f"usb:0483:5740:{A}", "--reason", "loose").returncode == 0
    manifest = expected_module.parse(file.read_text())
    assert manifest.rigs[0].devices == [] and manifest.retired[0].reason == "loose" and manifest.retired[0].name == f"fdcanusb {A}"
    assert cli("forget", *common, "--serial", CAM1_SDK).returncode == 0
