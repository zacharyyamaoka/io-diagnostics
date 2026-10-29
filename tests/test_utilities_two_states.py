"""Utilities have two states (Zach, 2026-10-29: "I am confused by the can't tell: does that mean it can block or not block?
The utilities menu should just have 2 states"): every precondition resolves, an action is never "unknown", and a port held
by a RobotDx check reads as waiting, named in plain words. Fixture hosts and fake holders only; nothing opens a device."""

from __future__ import annotations

import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from io_diagnostics import entity_state
from io_diagnostics import host as host_module
from io_diagnostics.host import make_host
from io_diagnostics.host import WAITING, LiveOs, holder_words, other_robotdx, resolves
from io_diagnostics.runner import RUNNING

from test_state_config import Rig

CAN15 = "host/usb1/port-1-3/usb-1-3/can/15"


def test_every_precondition_resolves_to_one_of_five():
    assert resolves("port_free", True) == "ready"
    assert resolves("port_free", False) == "blocks"
    assert resolves("bus_lock_free", WAITING) == "waiting"
    # what only a person can check is confirmed on the Run page, whatever the host read
    assert [resolves("joint_free", state) for state in (True, None)] == ["confirm", "confirm"]
    assert resolves("joint_clear", None) == "confirm" and resolves("joints_clear", None) == "confirm"
    # what the driver checks on the bus right before it sends anything does not block while unknown
    assert [resolves(check, None) for check in ("board_idle", "config_interlock", "new_id_free", "port_free")] == ["at_run"] * 4
    # an action that needs a file nobody said exists is greyed
    assert resolves("config_recorded", None) == "blocks"


@pytest.fixture(scope="module")
def f1():
    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    yield host
    host.stop()


def test_no_action_of_any_entity_is_unknown_or_cant_tell(f1):
    view = f1.view()
    actions = [action for path in view.merged.entities for action in (f1.node(path) or {}).get("actions", [])]
    assert len(actions) > 20
    assert [a for a in actions if a["state"] == "unknown" or "can't tell" in a["reason"]] == []
    assert {a["state"] for a in actions} <= {"ready", "blocked", "waiting", "nothing_to_do"}
    for action in actions:
        assert all(pre["resolves"] in ("ready", "blocks", "waiting", "confirm", "at_run") for pre in action["preconditions"]), action


def test_identify_on_can_15_is_ready_with_its_confirmation_and_update_config_says_why_not(f1):
    actions = {action["name"]: action for action in f1.node(CAN15)["actions"]}
    identify = actions["identify"]
    pre = {item["check"]: item for item in identify["preconditions"]}
    assert identify["state"] == "ready" and identify["reason"] == ""
    assert pre["joint_clear"]["resolves"] == "confirm" and pre["joint_clear"]["detail"] == "you confirm it on the Run page"
    assert pre["board_idle"]["resolves"] == "at_run" and pre["config_interlock"]["resolves"] == "at_run"
    assert identify["confirmations"] == ["Nothing is in the way of CAN 15's joint: it will swing a few degrees"]
    assert actions["calibrate"]["state"] == "ready" and len(actions["calibrate"]["confirmations"]) == 1
    config = actions["update_config"]
    assert (config["state"], config["reason"]) == ("blocked", "blocked: no recorded configuration for this board")
    assert actions["set_id"]["state"] == "ready"
    unique = next(item for item in actions["set_id"]["preconditions"] if item["check"] == "source_id_unique")
    assert (unique["state"], unique["detail"]) == ("pass", "one board answers CAN 15")


def test_a_driver_answers_its_own_precondition_with_a_status_value(f1, monkeypatch):
    view = f1.view()
    status = next(iter(view.rows.own[CAN15]))
    for said, expected in (("fail: no recorded configuration for CAN 15 (/x/can-15.cfg is missing)", (False, "no recorded configuration for CAN 15 (/x/can-15.cfg is missing)")),
                           ("pass: /x/can-15.cfg", (True, "/x/can-15.cfg"))):
        changed = SimpleNamespace(values=(("precondition.config_recorded", said),))
        monkeypatch.setitem(view.rows.own, CAN15, [changed, status])
        assert f1.evaluate("config_recorded", CAN15, view) == expected


def test_board_idle_reads_the_boards_latest_state(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    try:
        rig.host.watch_heartbeat(rig.board)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            values = rig.host.values_json(rig.board) or {}
            if any(row["key"] == "mode" for group in values.get("groups", []) for row in group["rows"]):
                break
            time.sleep(0.1)
        view = rig.host.view()
        state, detail = rig.host.evaluate("board_idle", rig.board, view)
        # the fixture joint reports its driver fault 33 in its State
        assert state is False and detail.startswith("CAN 1 reports fault 33") and detail.endswith("Clear faults first")

        def groups(rows, stale=False):
            return lambda cache, rel, now, since=0: [{"stale": stale, "rows": rows}]

        monkeypatch.setattr(entity_state, "state_groups", groups([{"key": "mode", "value": 0}, {"key": "fault", "value": 0}]))
        assert rig.host.evaluate("board_idle", rig.board, view) == (True, "CAN 1 reports mode 0 (stopped), no fault")
        monkeypatch.setattr(entity_state, "state_groups", groups([{"key": "mode", "value": 10, "meaning": "position"}, {"key": "fault", "value": 0}]))
        state, detail = rig.host.evaluate("board_idle", rig.board, view)
        assert state is False and "is in mode 10 (position): something is driving it" in detail
        # a stale State says nothing: the driver reads the board on the bus before anything moves
        monkeypatch.setattr(entity_state, "state_groups", groups([{"key": "mode", "value": 10}], stale=True))
        state, detail = rig.host.evaluate("board_idle", rig.board, view)
        assert state is None and "read on the bus when it runs" in detail
        assert resolves("board_idle", state) == "at_run"
    finally:
        rig.stop()


def fake_proc(tmp_path, processes):
    """A /proc of {pid: (ppid, argv)}."""

    for pid, (ppid, argv) in processes.items():
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "cmdline").write_bytes(b"\0".join(part.encode() for part in argv) + b"\0")
        (directory / "stat").write_text(f"{pid} ({argv[0].rsplit('/', 1)[-1][:15]}) S {ppid} 1 1 0")
    return tmp_path


def test_a_holder_is_named_in_plain_words(tmp_path):
    proc = fake_proc(tmp_path, {
        4066: (2509, ["/usr/bin/python3", "/home/bam/.local/state/rd/source/io_diagnostics/cli.py", "tray", "--port", "8822"]),
        4100: (4066, ["uv", "run", "--project", "/x/moteus_servo", "python", "-m", "moteus_servo.diagnostics", "diagnose", "--device", "usb:1-5"]),
        1652054: (4100, ["/x/.venv/bin/python3", "-m", "moteus_servo.diagnostics", "--bus-lock", "/b", "diagnose", "--device", "usb:1-5"]),
        640623: (2509, ["/home/bam/.local/bin/tview", "--fdcanusb", "/dev/serial/by-id/x"]),
        777: (2509, ["/usr/bin/python3", "my_experiment.py"]),
    })
    assert other_robotdx(1652054, proc) == "RobotDx (tray, port 8822)"
    assert holder_words("1652054 python3", proc) == "RobotDx (tray, port 8822) has it open (moteus_servo.diagnostics diagnose, pid 1652054)"
    assert other_robotdx(640623, proc) == ""
    assert holder_words("640623 tview", proc) == "tview, moteus's console (pid 640623): close it to free the port"
    assert holder_words("777 python3", proc) == "pid 777 (/usr/bin/python3 my_experiment.py)"


def live_os(holders):
    found = LiveOs.__new__(LiveOs)
    found._gate_cache = {}
    found._holders = (SimpleNamespace(function=holders, source="test"), SimpleNamespace(function=None, source="test"))
    return found


DEVICE = {"id": "usb:1-5", "attrs": {"by_id": ["/dev/serial/by-id/usb-mjbots_fdcanusb_0607623A-if00"]}}


def test_a_port_held_by_this_hosts_own_check_is_waiting_not_blocked():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
    try:
        with RUNNING.run(process, "moteus_servo diagnose usb:1-5"):
            state, detail = live_os(lambda node: [f"{process.pid} python3"])._gate(DEVICE, "port_free")
        assert state == WAITING and detail.startswith("this host's own check is running (a few s): moteus_servo diagnose usb:1-5")
        assert resolves("port_free", state) == "waiting"
    finally:
        process.kill()
        process.wait()


def test_a_port_held_by_the_other_robotdx_host_is_waiting_and_named_anyone_else_blocks():
    # a process whose own command line runs io_diagnostics/cli.py: the other channel's host (here, itself)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)", "/x/io_diagnostics/cli.py", "tray", "--port", "8822", "--fixture", "F1"],
                             start_new_session=True)
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
    try:
        time.sleep(0.2)
        state, detail = live_os(lambda node: [f"{other.pid} python3"])._gate(DEVICE, "port_free")
        assert state == WAITING and detail == f"RobotDx (tray, port 8822) has it open (pid {other.pid}) (a few s)"
        state, detail = live_os(lambda node: [f"{stranger.pid} python3"])._gate(DEVICE, "port_free")
        assert state is False and detail.startswith(f"held by pid {stranger.pid} (")
    finally:
        for process in (other, stranger):
            process.kill()
            process.wait()


def test_the_doc_names_the_two_states():
    text = (host_module.Path(host_module.__file__).resolve().parents[1] / "docs" / "api-v1.md").read_text()
    assert "Actions: two states" in text and "never `\"unknown\"`" in text and "precondition.<check>" in text
