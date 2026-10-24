"""Editing a board's configuration from the page, made safe (docs/state-config-v1.md section 8): the edit session, a live write
that the board accepts or rejects, "not saved" and Save, Revert, a safety key's extra conditions, the gates, the page-only rule
and the Activity log of every write; and the arrangement (pins, manual order, folded folders) the page keeps beside settings.
The plugin is tests/fixtures/plugins-state/state_plugin.py (the "board" is a file), a real subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_host_support import FakeOs
from test_state_config import PAGE, Rig, alive, fetch, wait_for

from io_diagnostics.arrangements import ArrangementError, Arrangements
from io_diagnostics.host import _without_own_watchers
from io_diagnostics.runner import RUNNING
from io_diagnostics.server import make_server, serve_in_thread

SAFETY = "servo.max_current_A"
PLAIN = "servo.default_timeout_s"
KP = "servo.pid_position.kp"


class Board(Rig):
    """The state fixture's joint with its board file and mode file in ``tmp_path``; ``mode`` is what the board reports."""

    def __init__(self, tmp_path: Path, *, gates=None, mode: int = 10, grace_s: float = 10.0):
        self.board_file, self.mode_file = tmp_path / "board.json", tmp_path / "mode"
        self.mode_file.write_text(str(mode))
        super().__init__(tmp_path, gates=gates, grace_s=grace_s,
                         env={"STATE_CONFIG_FILE": str(self.board_file), "STATE_MODE_FILE": str(self.mode_file)})
        self.editor = self.host.config_editor

    def set_mode(self, mode: int) -> None:
        self.mode_file.write_text(str(mode))
        self.host.run_all_once()  # the board reports it in its State at the next check

    def open(self) -> str:
        opened = self.editor.open(self.board, True)
        assert opened["ok"], opened
        return opened["token"]

    def row(self, key: str) -> dict:
        return next(row for row in self.host.node(self.board)["config"] if row["key"] == key)

    def acts(self, action: str) -> list[dict]:
        return [row for row in self.verbs("act") if row["argv"][row["argv"].index("--action") + 1] == action and "--dry-run" not in row["argv"]]

    def events(self, code: str) -> list:
        return [event for event in self.editor.events if event.code == code]


@pytest.fixture
def board(tmp_path):
    rig = Board(tmp_path)
    yield rig
    rig.stop()


@pytest.fixture
def stopped(tmp_path):
    rig = Board(tmp_path, mode=0)
    yield rig
    rig.stop()


# ----- the record and the row -------------------------------------------------------------------------------------
def test_a_setting_says_how_to_edit_it_and_what_the_board_stores(board):
    row = board.row(SAFETY)
    assert (row["writable"], row["type"], row["range"], row["default"], row["stored"], row["safety"], row["unsaved"]) == (
        True, "float", [0.0, 30.0], 20.0, 20.0, True, False)
    assert row["expected"] == 25.0 and row["differs"] and row["edit"] is None
    assert board.row("id.id")["writable"] is False and board.row("aux1.pins.0.mode")["choices"] == ["pwm", "digital_out", "off"]
    assert board.host.node(board.board)["editable"] is True and board.host.node(board.device)["editable"] is False


# ----- the edit session -------------------------------------------------------------------------------------------
def test_editing_starts_with_one_confirm_names_the_board_and_ends_with_the_page(board):
    assert board.editor.open(board.board, False)["refused"] == "ConfirmRequired"
    assert board.editor.open(board.device, True)["refused"] == "NotEditable"  # the adapter registers no set_config
    assert board.editor.open("host/none", True)["refused"] == "NoSuchEntity"
    opened = board.editor.open(board.board, True)
    assert opened["ok"] and opened["board"] == "CAN 1 · fdcanusb fixture" and opened["token"]
    assert board.host.node(board.board)["editing"]["active"] is True
    assert board.editor.beat(board.board, opened["token"])["ok"]
    assert board.editor.beat(board.board, "wrong")["refused"] == "NoEditSession"
    assert board.editor.close(board.board, opened["token"])["ok"]
    assert board.editor.set(board.board, opened["token"], PLAIN, 0.3)["refused"] == "NoEditSession"  # closed: the token is dead
    assert board.host.node(board.board)["editing"]["active"] is False


def test_a_session_ends_by_itself_when_the_page_stops_sending_heartbeats(tmp_path):
    board = Board(tmp_path, grace_s=1.5)
    try:
        token = board.open()
        time.sleep(0.8)
        assert board.editor.beat(board.board, token)["ok"]  # a heartbeat keeps it
        time.sleep(0.8)
        assert board.editor.beat(board.board, token)["ok"]
        time.sleep(2.0)  # the grace period is 1.5 s
        assert board.editor.set(board.board, token, PLAIN, 0.3)["refused"] == "NoEditSession"
        assert [event.code for event in board.editor.events][-1] == "ConfigEditEnded"
    finally:
        board.stop()


def test_a_second_session_replaces_the_first(board):
    first, second = board.open(), board.open()
    assert board.editor.set(board.board, first, PLAIN, 0.3)["refused"] == "NoEditSession"
    assert board.editor.set(board.board, second, PLAIN, 0.3)["ok"]


# ----- a live write ---------------------------------------------------------------------------------------------------
def test_a_live_edit_is_run_by_the_driver_read_back_and_accepted_but_not_saved(board):
    token = board.open()
    answer = board.editor.set(board.board, token, PLAIN, 0.3)
    assert answer["ok"] and answer["state"] == "accepted" and answer["value"] == 0.3 and answer["old"] == 0.1 and answer["run_id"]
    row = board.row(PLAIN)
    assert row["value"] == 0.3 and row["stored"] == 0.1 and row["unsaved"] is True  # live, not stored
    assert row["edit"]["state"] == "accepted" and row["edit"]["asked"] == 0.3
    [written] = board.acts("set_config")
    assert written["argv"][written["argv"].index("--key") + 1] == PLAIN and "--node" in written["argv"]  # a run of the plugin's own action
    run = board.host.runs.get(answer["run_id"])
    assert run is not None and run.state == "done" and run.spec.action == "set_config"  # recorded as a run
    assert json.loads(board.board_file.read_text())["live"][PLAIN] == 0.3


def test_a_value_the_board_clamps_is_rejected_with_what_it_kept(board):
    token = board.open()
    answer = board.editor.set(board.board, token, PLAIN, 50)  # the board's range is 0..10 and it keeps 10
    assert answer["state"] == "rejected" and answer["value"] == 10.0 and answer["message"] == "the board kept 10.0 (asked 50.0)"
    row = board.row(PLAIN)
    assert row["edit"]["state"] == "rejected" and row["edit"]["kept"] == 10.0 and row["value"] == 10.0


def test_a_value_the_driver_errors_on_is_rejected_with_its_reason(stopped):
    token = stopped.open()
    answer = stopped.editor.set(stopped.board, token, KP, -1, True)
    assert answer["state"] == "rejected" and answer["message"] == "kp must be above 0" and stopped.row(KP)["value"] == 12.0


def test_a_value_of_the_wrong_type_or_choice_is_refused_before_anything_is_sent(board):
    token = board.open()
    for key, value, why in ((PLAIN, "fast", "not a float"), ("aux1.pins.0.mode", "analog", "is one of pwm, digital_out, off"),
                            ("servo.motor_position.output.reference_source", 7, "is one of 0, 1, 2")):
        answer = board.editor.set(board.board, token, key, value)
        assert answer["refused"] == "BadValue" and why in answer["message"], (key, answer)
    assert board.editor.set(board.board, token, "id.id", 2)["refused"] == "NotWritable"  # a read-only setting
    assert board.editor.set(board.board, token, "nothing", 1)["refused"] == "NotWritable"
    assert board.acts("set_config") == []


def test_an_enum_and_a_number_given_as_text_are_taken_as_their_type(board):
    token = board.open()
    assert board.editor.set(board.board, token, "aux1.pins.0.mode", "off")["ok"] and board.row("aux1.pins.0.mode")["value"] == "off"
    assert board.editor.set(board.board, token, PLAIN, "0.25")["ok"] and board.row(PLAIN)["value"] == 0.25


# ----- not saved, saved, revert -------------------------------------------------------------------------------------
def test_live_changes_are_not_saved_until_save_and_then_they_are(board):
    token = board.open()
    board.editor.set(board.board, token, PLAIN, 0.3)
    board.editor.set(board.board, token, "aux1.pins.0.mode", "off")
    unsaved = [row["key"] for row in board.host.node(board.board)["config"] if row["unsaved"]]
    assert sorted(unsaved) == sorted([PLAIN, "aux1.pins.0.mode"])
    saved = board.editor.persist(board.board, token)
    assert saved["ok"] and sorted(saved["saved"]) == sorted(unsaved)
    node = board.host.node(board.board)
    assert not any(row["unsaved"] for row in node["config"]) and all(row["edit"] is None for row in node["config"])
    assert json.loads(board.board_file.read_text())["stored"][PLAIN] == 0.3
    assert [event.code for event in board.editor.events if event.code == "ConfigPersisted"] == ["ConfigPersisted"]


def test_revert_all_puts_every_live_change_back_to_what_is_stored(board):
    token = board.open()
    board.editor.set(board.board, token, PLAIN, 0.3)
    board.editor.set(board.board, token, "aux1.pins.0.mode", "off")
    answer = board.editor.reset(board.board, token, "stored")
    assert answer["ok"] and sorted(answer["reverted"]) == sorted([PLAIN, "aux1.pins.0.mode"])
    node = board.host.node(board.board)
    assert board.row(PLAIN)["value"] == 0.1 and board.row("aux1.pins.0.mode")["value"] == "pwm"
    assert not any(row["unsaved"] for row in node["config"])


def test_one_setting_can_go_back_to_stored_expected_or_default(board):
    token = board.open()
    board.editor.set(board.board, token, PLAIN, 0.3)
    assert board.editor.reset(board.board, token, "stored", PLAIN)["ok"] and board.row(PLAIN)["value"] == 0.1
    assert board.editor.reset(board.board, token, "expected", PLAIN)["ok"] and board.row(PLAIN)["value"] == 0.5  # the deployment record's
    assert board.row(PLAIN)["edit"]["state"] == "accepted" and board.row(PLAIN)["edit"]["reset"] == "expected"
    assert board.editor.reset(board.board, token, "default", PLAIN)["ok"] and board.row(PLAIN)["value"] == 0.1
    assert board.editor.reset(board.board, token, "default", "aux1.pins.0.mode")["ok"] and board.row("aux1.pins.0.mode")["value"] == "off"
    assert board.editor.reset(board.board, token, "expected", "aux1.pins.0.mode")["refused"] == "NoTarget"  # no deployment value for it
    assert board.editor.reset(board.board, token, "sometime", PLAIN)["refused"] == "BadRequest"
    assert board.editor.reset(board.board, token, "default")["refused"] == "BadRequest"  # every setting only goes back to stored


# ----- the safety keys -----------------------------------------------------------------------------------------------
def test_a_safety_key_needs_the_second_confirm_and_a_stopped_board(board):
    token = board.open()
    assert board.row(SAFETY)["safety"]
    first = board.editor.set(board.board, token, SAFETY, 15)
    assert first["state"] == "refused" and first["refused"] == "SafetyConfirmRequired"
    second = board.editor.set(board.board, token, SAFETY, 15, True)  # confirmed, but the board reports mode 10 (position)
    assert second["refused"] == "SafetyRefused" and "must be stopped (mode 0)" in second["message"] and "mode 10 (position)" in second["message"]
    assert board.acts("set_config") == [] and board.row(SAFETY)["value"] == 20.0  # nothing was ever sent
    assert board.row(SAFETY)["edit"]["state"] == "rejected"


def test_a_stopped_board_takes_a_confirmed_safety_write(stopped):
    token = stopped.open()
    answer = stopped.editor.set(stopped.board, token, SAFETY, 15, True)
    assert answer["ok"] and answer["value"] == 15.0 and stopped.row(SAFETY)["unsaved"] is True


def test_a_safety_key_is_refused_when_the_boards_mode_is_unknown_or_old(tmp_path):
    rig = Board(tmp_path, mode=0)
    try:
        token = rig.open()
        job = next(iter(rig.host.jobs.values()))
        state = job.cache.states[("can/1", "motor")]
        job.cache.states[("can/1", "motor")] = type(state)(**{**state.__dict__, "received": state.received - 600})  # an old report
        old = rig.editor.set(rig.board, token, SAFETY, 15, True)
        assert old["refused"] == "SafetyRefused" and "not current" in old["message"]
        del job.cache.states[("can/1", "motor")]
        unknown = rig.editor.set(rig.board, token, SAFETY, 15, True)
        assert unknown["refused"] == "SafetyRefused" and "reports no mode" in unknown["message"]
        assert rig.acts("set_config") == []
    finally:
        rig.stop()


def test_a_safety_key_is_refused_while_a_run_is_active_on_its_bus(tmp_path, monkeypatch):
    rig = Board(tmp_path, mode=0)
    try:
        rig.host.specs = [type(spec)(**{**spec.__dict__, "exclusive": "rack-bus"}) for spec in rig.host.specs]
        for job in rig.host.jobs.values():
            job.spec = rig.host.specs[0]

        class Spec:
            group, label = "rack-bus", "CAN 1 · Run calibration"

        class FakeRun:
            spec = Spec()

        token = rig.open()
        monkeypatch.setattr(rig.host.runs, "active", lambda: [FakeRun()])
        answer = rig.editor.set(rig.board, token, SAFETY, 15, True)
        assert answer["refused"] == "SafetyRefused" and "is running on this bus" in answer["message"]
        plain = rig.editor.set(rig.board, token, PLAIN, 0.3)  # a plain key is gated by the bus too
        assert plain["refused"] == "Blocked" and "CAN 1 · Run calibration is running" in plain["message"]
    finally:
        monkeypatch.undo()
        rig.stop()


def test_reverting_safety_changes_needs_the_second_confirm_and_a_stopped_board(stopped):
    token = stopped.open()
    stopped.editor.set(stopped.board, token, SAFETY, 15, True)
    stopped.editor.set(stopped.board, token, PLAIN, 0.3)
    assert stopped.editor.reset(stopped.board, token, "stored")["refused"] == "SafetyConfirmRequired"
    stopped.set_mode(10)
    assert stopped.editor.reset(stopped.board, token, "stored", confirm_safety=True)["refused"] == "SafetyRefused"
    stopped.set_mode(0)
    assert stopped.editor.reset(stopped.board, token, "stored", confirm_safety=True)["ok"] and stopped.row(SAFETY)["value"] == 20.0


# ----- the gates ------------------------------------------------------------------------------------------------------
def test_a_persons_tool_holding_the_port_blocks_every_write_and_nothing_runs(tmp_path):
    rig = Board(tmp_path, gates={"port_free": (False, "port held by tview pid 640623")})
    try:
        token = rig.open()
        for answer in (rig.editor.set(rig.board, token, PLAIN, 0.3), rig.editor.persist(rig.board, token), rig.editor.reset(rig.board, token, "stored")):
            assert answer["refused"] == "Blocked" and "tview has the port (pid 640623)" in answer["message"], answer
        assert rig.acts("set_config") == [] and rig.acts("persist_config") == []
        rig.os.gates["port_free"] = (True, "no holder")
        assert rig.editor.set(rig.board, token, PLAIN, 0.3)["ok"]  # clear: it goes through
    finally:
        rig.stop()


def holding_port(rig, *, tools=()):
    """Make the rig's ``port_free`` gate read like the live one: the port is held by every live ``watch`` process of the
    fixture (and by ``tools``, a person's pids), judged by the host's own ``_without_own_watchers`` rule."""

    def holders(_node):
        return [f"{pid} state_plugin" for pid in (*live_watch_pids(rig), *tools)]

    judge = _without_own_watchers(holders)

    def gate(_device, check):
        if check != "port_free":
            return None, f"{check}: not recorded"
        held = judge("port")
        return (False, "held by " + ", ".join(held)) if held else (True, "no holder")

    rig.os.gate = gate
    return holders


def live_watch_pids(rig) -> list[int]:
    found = []
    for entry in os.listdir("/proc"):
        try:
            words = Path(f"/proc/{entry}/cmdline").read_bytes().split(b"\0")
            environment = Path(f"/proc/{entry}/environ").read_bytes().split(b"\0")
            zombie = Path(f"/proc/{entry}/stat").read_text().rsplit(")", 1)[-1].split()[:1] == ["Z"]
        except OSError:
            continue
        if entry.isdigit() and not zombie and b"watch" in words and f"STATE_LOG={rig.log}".encode() in environment:
            found.append(int(entry))
    return found


def test_the_hosts_own_watch_is_not_a_holder_to_its_own_gates_and_a_persons_tool_still_is(tmp_path):
    passive = subprocess.Popen(["sleep", "30"], start_new_session=True)
    person = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        with RUNNING.run(passive, "state_joint watch usb:9-1", passive=True):
            kept = _without_own_watchers(lambda node: [f"{passive.pid} sleep", f"{person.pid} tview", "weird"])("port")
            assert kept == [f"{person.pid} tview", "weird"]
            assert RUNNING.is_passive(passive.pid) and not RUNNING.is_passive(person.pid)
        assert not RUNNING.is_passive(passive.pid)  # finished: no longer ours
    finally:
        for process in (passive, person):
            process.kill()
            process.wait()


def test_a_write_goes_through_at_once_while_the_watch_runs_and_the_watch_resumes_after_it(tmp_path):
    rig = Board(tmp_path, grace_s=30.0)
    try:
        holding_port(rig)
        token = rig.open()
        wait_for(lambda: rig.host.watch_heartbeat(rig.board)["watch"]["state"] == "running", what="the watch running")
        first = rig.host.watch_json(*rig.host._watch_target(rig.board))["pid"]
        assert live_watch_pids(rig) == [first]  # the port IS held, by the host's own watch ...
        assert rig.host.watch_heartbeat(rig.board)["watch"]["state"] == "running"  # ... and its own gate does not mind
        began = time.monotonic()
        answer = rig.editor.set(rig.board, token, PLAIN, 0.3)
        assert answer["ok"] and answer["state"] == "accepted", answer
        assert time.monotonic() - began < 5.0
        # the driver found no watch holding its port: the host stopped it first and waited for it to exit
        assert rig.acts("set_config")[-1]["watchers"] == []
        assert not alive(first)
        # the page still beats: the watch is back, a new process
        resumed = wait_for(lambda: (rig.host.watch_heartbeat(rig.board), rig.host.watch_json(*rig.host._watch_target(rig.board)))[1]
                           if rig.host.watch_json(*rig.host._watch_target(rig.board))["state"] == "running" else None, what="the watch resuming")
        assert resumed["pid"] != first and len(rig.verbs("watch")) == 2
        # a save is a write too
        assert rig.editor.persist(rig.board, token)["ok"] and rig.acts("persist_config")[-1]["watchers"] == []
        wait_for(lambda: rig.host.watch_json(*rig.host._watch_target(rig.board))["state"] == "running", what="the watch resuming again")
    finally:
        rig.stop()


def test_a_write_after_the_page_left_the_state_tab_does_not_wait_for_the_grace_and_the_watch_does_not_outlive_it(tmp_path):
    rig = Board(tmp_path, grace_s=30.0)
    try:
        holding_port(rig)
        token = rig.open()
        wait_for(lambda: rig.host.watch_heartbeat(rig.board)["watch"]["state"] == "running", what="the watch running")
        watch = rig.host.watches[(rig.host._watch_target(rig.board)[0].key, "can/1")]
        watch.last_beat = time.monotonic() - 29.0  # the tab was left 29 s ago: the watch lives one more second
        assert rig.editor.set(rig.board, token, PLAIN, 0.4)["ok"]
        assert rig.acts("set_config")[-1]["watchers"] == []  # it did not wait out the 29 s: the host stopped its own watch
        wait_for(lambda: not rig.host.watches, what="the watch ending at its grace")
        wait_for(lambda: live_watch_pids(rig) == [], what="its process gone")
    finally:
        rig.stop()


def test_a_persons_tool_holding_the_port_still_blocks_a_write_while_the_watch_is_ignored(tmp_path):
    rig = Board(tmp_path, grace_s=30.0)
    person = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        holding_port(rig, tools=(person.pid,))
        token = rig.open()
        answer = rig.editor.set(rig.board, token, PLAIN, 0.3)
        assert answer["refused"] == "Blocked" and rig.acts("set_config") == []
    finally:
        person.kill()
        person.wait()
        rig.stop()


# ----- Activity: every write -------------------------------------------------------------------------------------------
def test_every_write_is_an_activity_event_with_who_old_new_and_the_verdict(stopped):
    token = stopped.open()
    stopped.editor.set(stopped.board, token, PLAIN, 0.3)
    stopped.editor.set(stopped.board, token, PLAIN, 50)
    stopped.editor.set(stopped.board, token, "id.id", 2)
    stopped.editor.persist(stopped.board, token)
    stopped.editor.close(stopped.board, token)
    events = [(event.code, event.severity) for event in stopped.editor.events]
    assert events == [("ConfigEditStarted", "info"), ("ConfigSet", "info"), ("ConfigRejected", "warn"), ("ConfigRejected", "warn"),
                      ("ConfigPersisted", "info"), ("ConfigEditEnded", "info")]
    accepted = stopped.editor.events[1]
    assert accepted.args["by"] == "page" and (accepted.args["key"], accepted.args["old"], accepted.args["asked"], accepted.args["accepted"]) == (PLAIN, 0.1, 0.3, True)
    assert accepted.args["run_id"] and accepted.path == stopped.board and "0.1 → 0.3" in accepted.message and "accepted by the board" in accepted.message
    kept = stopped.editor.events[2]
    assert kept.args["kept"] == 10.0 and kept.args["accepted"] is False and "the board kept 10.0" in kept.message
    assert stopped.editor.events[3].args["refused"] == "NotWritable"
    # the page's Activity log reads them
    codes = [event["code"] for event in stopped.host.activity(200)["events"]]
    assert "ConfigSet" in codes and "ConfigRejected" in codes and "ConfigPersisted" in codes


# ----- the endpoints: page-only, token-bound -------------------------------------------------------------------------------
@pytest.fixture
def served(tmp_path):
    rig = Board(tmp_path, mode=0)
    server = make_server(rig.host, 0)
    serve_in_thread(server)
    yield rig, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    rig.stop()


def post(base: str, endpoint: str, body: dict, headers: dict | None = None):
    status, _headers, payload = fetch(f"{base}/api/v1/{endpoint}", data=json.dumps(body).encode(), headers=PAGE if headers is None else headers)
    return status, json.loads(payload)


def test_the_configuration_writes_are_the_pages_alone(served):
    rig, base = served
    body = {"path": rig.board, "op": "open", "confirm": True}
    for headers in ({"Content-Type": "application/json"},  # a script: no page header
                    {**PAGE, "Sec-Fetch-Site": "cross-site"},  # another site's page
                    {**PAGE, "Origin": "http://evil.example"},  # another origin
                    {"X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin", "Content-Type": "text/plain"}):
        status, answer = post(base, "config/edit", body, headers)
        assert status == 403 and answer["refused"] in ("NotFromPage", "NotSameOrigin", "NotJson"), (headers, answer)
    assert rig.host.node(rig.board)["editing"]["active"] is False and rig.events("ConfigEditStarted") == []
    status, opened = post(base, "config/edit", body)
    assert status == 200 and opened["ok"]
    token = opened["token"]
    for endpoint, extra in (("config/set", {"key": PLAIN, "value": 0.3}), ("config/persist", {}), ("config/reset", {"to": "stored"})):
        status, answer = post(base, endpoint, {"path": rig.board, "token": token, **extra}, {"Content-Type": "application/json"})
        assert status == 403, endpoint  # each write is the page's alone
        status, answer = post(base, endpoint, {"path": rig.board, "token": "forged", **extra})
        assert status == 403 and answer["refused"] == "NoEditSession", endpoint
        status, answer = post(base, endpoint, {"path": rig.board, **extra})
        assert status == 400, endpoint  # no token at all
    assert rig.acts("set_config") == [] and rig.row(PLAIN)["value"] == 0.1
    assert fetch(f"{base}/api/v1/config/set")[0] == 405  # a GET is not a write


def test_the_pages_session_runs_a_whole_edit_over_http(served):
    rig, base = served
    _status, opened = post(base, "config/edit", {"path": rig.board, "op": "open", "confirm": True})
    token = opened["token"]
    assert post(base, "config/edit", {"path": rig.board, "op": "beat", "token": token})[1]["ok"]
    status, answer = post(base, "config/set", {"path": rig.board, "token": token, "key": PLAIN, "value": 0.3})
    assert status == 200 and answer["state"] == "accepted" and answer["value"] == 0.3
    status, answer = post(base, "config/set", {"path": rig.board, "token": token, "key": PLAIN, "value": 99})
    assert status == 200 and answer["state"] == "rejected"  # a rejection is an answer, not a server error
    status, answer = post(base, "config/set", {"path": rig.board, "token": token, "key": SAFETY, "value": 15})
    assert status == 200 and answer["refused"] == "SafetyConfirmRequired"
    status, answer = post(base, "config/set", {"path": rig.board, "token": token, "key": SAFETY, "value": 15, "confirm_safety": True})
    assert status == 200 and answer["state"] == "accepted"
    status, answer = post(base, "config/persist", {"path": rig.board, "token": token})
    assert status == 200 and answer["ok"] and sorted(answer["saved"]) == sorted([PLAIN, SAFETY])
    assert post(base, "config/edit", {"path": rig.board, "op": "close", "token": token})[1]["ok"]
    node = json.loads(fetch(f"{base}/api/v1/node?path={rig.board}")[2])
    assert node["editing"]["active"] is False and not any(row["unsaved"] for row in node["config"])


# ----- the arrangement: pins, manual order, folded folders ---------------------------------------------------------------
def test_an_arrangement_is_kept_per_kind_and_section_and_survives_a_restart(tmp_path):
    file = tmp_path / "arrangements.json"
    arrangements = Arrangements(file)
    value = {"pins": ["servo.max_current_A", "id.id"], "order": {"": ["servo", "id"], "servo": ["max_current_A"]}, "folded": ["aux1"], "sort": "manual"}
    answer = arrangements.set("servo", "config", value)
    assert answer["arrangements"]["servo"]["config"] == value and answer["file"] == str(file)
    again = Arrangements(file).json()["arrangements"]  # a fresh process reads the same file
    assert again == {"servo": {"config": value}}
    arrangements.set("servo", "state", {"pins": ["position"]})
    arrangements.set("camera", "config", {"sort": "name"})
    assert Arrangements(file).json()["arrangements"].keys() == {"servo", "camera"} and set(Arrangements(file).json()["arrangements"]["servo"]) == {"config", "state"}


@pytest.mark.parametrize("value", [{"pins": "a"}, {"pins": [1]}, {"sort": "random"}, {"order": {"": "a"}}, {"surprise": 1}, [], {"pins": [""]}])
def test_a_bad_arrangement_is_refused_and_nothing_is_written(tmp_path, value):
    arrangements = Arrangements(tmp_path / "a.json")
    with pytest.raises(ArrangementError):
        arrangements.set("servo", "config", value)
    with pytest.raises(ArrangementError):
        arrangements.set("servo", "nowhere", {})
    assert not (tmp_path / "a.json").exists()


def test_an_arrangement_file_that_is_broken_starts_empty_and_says_so(tmp_path):
    (tmp_path / "a.json").write_text("{not json")
    arrangements = Arrangements(tmp_path / "a.json")
    assert arrangements.json()["arrangements"] == {} and "unreadable" in arrangements.json()["note"]


def test_the_arrangement_endpoint_is_the_pages_alone(tmp_path):
    from test_state_config import Rig as _Rig

    rig = _Rig(tmp_path)
    rig.host.arrangements = Arrangements(tmp_path / "arr.json")
    server = make_server(rig.host, 0)
    serve_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        body = {"kind": "servo", "section": "config", "value": {"pins": ["id.id"]}}
        assert post(base, "arrangements", body, {"Content-Type": "application/json"})[0] == 403
        assert not (tmp_path / "arr.json").exists()
        status, answer = post(base, "arrangements", body)
        assert status == 200 and answer["arrangements"]["servo"]["config"]["pins"] == ["id.id"]
        assert json.loads(fetch(f"{base}/api/v1/arrangements")[2])["arrangements"]["servo"]["config"] == {"pins": ["id.id"]}
        assert post(base, "arrangements", {**body, "value": {"sort": "x"}})[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        rig.stop()
