"""The roster rules (Zach, 2026-10-24): a row exists for what was heard from and stays until a person removes it; a
missing thing is a status on its parent; a restarted host shows the same rows (hollow) until fresh data replaces them;
a blocked run never removes a row.

The rack of tests/test_host_setup.py (fdcanusb 272813EF with CAN 1, 14 answering and CAN 15 expected, fdcanusb 0607623A,
two D405s) over its stand-in plugin, which follows the moteus_servo contract: a board that does not answer has no entity,
its bus names it. Fakes only.
"""

from __future__ import annotations

import json

import pytest
from test_host_setup import A, B, CAM1_USB, NOW, PRESENT, RACK, SEED, Rack, adapter, values
from test_host_support import FakeOs, SetClock, standard_skeleton, usb_device

from io_diagnostics import roster as roster_module
from io_diagnostics import settings as settings_module
from io_diagnostics import tree
from io_diagnostics.host import Host
from io_diagnostics.tree import HOST_PATH

ALL_ANSWER = {**RACK, A: {**RACK[A], "answer": [1, 14, 15], "silent": []}}


def entity_paths(host: Host) -> dict[str, str]:
    """path -> name of every entity the tree draws."""

    return {path: entity.name for path, entity in host.view().merged.entities.items()}


def node(host: Host, path: str) -> dict:
    return host.node(path)["node"]


def ledger_in(tmp_path):
    return tmp_path / "state" / roster_module.LEDGER_FILE


# ----- a restart ------------------------------------------------------------------------------------------------------
def test_a_restarted_host_with_a_blocked_run_shows_the_same_rows_hollow(tmp_path):
    """Zach, 2026-10-24: "I don't like how the entity tree keeps on changing from showing all of CAN ids to not". Preview
    restarts its host on every .py save; tview may hold the port at that moment. Nothing may disappear or look dead."""

    ledger = ledger_in(tmp_path)
    before = Rack(tmp_path / "before", rack=ALL_ANSWER, ledger_file=ledger)
    rows_before = {path: name for path, name in entity_paths(before.host).items() if "/can/" in path}
    assert len(rows_before) == 7 and all(node(before.host, path)["presence"]["state"] == "heard" for path in rows_before)
    assert ledger.exists() and {f"rig|usb:0483:5740:{A}", f"rig|usb:0483:5740:{B}"} <= set(json.loads(ledger.read_text())["devices"])

    # the restart, tview holds the port of both adapters: the first runs are blocked
    held = {**ALL_ANSWER, A: {**ALL_ANSWER[A], "hold": True}, B: {**ALL_ANSWER[B], "hold": True}}
    fresh = Rack(tmp_path / "after", rack=held, ledger_file=ledger, run=False, at=NOW + 30)
    assert {path: name for path, name in entity_paths(fresh.host).items() if "/can/" in path} == rows_before  # at once, before any run
    fresh.host.run_all_once()  # the blocked run
    assert {path: name for path, name in entity_paths(fresh.host).items() if "/can/" in path} == rows_before
    assert all(entity.hardware_id for path, entity in fresh.host.view().merged.entities.items() if path.endswith(("/can/1", "/can/14")))
    boards = [path for path in rows_before if path.rsplit("/", 1)[-1].isdigit()]
    for path in boards:
        presence = node(fresh.host, path)["presence"]
        # hollow, with when: "last heard" is the first host's, never invented; no health comes back from the file
        assert (presence["state"], presence["restored"]) == ("stale", True), path
        assert node(fresh.host, path)["own"]["level"] is None, path
    counts = fresh.state()["summary"]["counts"]
    assert not counts.get("stale") and not counts.get("error"), counts  # a blocked run marks nothing dead


def test_the_first_good_run_after_a_restart_marks_what_it_did_not_report_with_its_true_last_heard(tmp_path):
    ledger = ledger_in(tmp_path)
    Rack(tmp_path / "before", rack=ALL_ANSWER, ledger_file=ledger)
    later = NOW + 3600
    quiet = {**ALL_ANSWER, A: {**ALL_ANSWER[A], "answer": [1, 14], "silent": [15]}}  # CAN 15 stopped answering
    fresh = Rack(tmp_path / "after", rack=quiet, ledger_file=ledger, at=later)
    can15 = f"{fresh.path_of(A)}/can/15"
    assert can15 in fresh.host.view().merged.entities  # it answered before: its row stays
    state = node(fresh.host, can15)
    assert (state["presence"]["state"], state["presence"]["restored"], state["own"]["level"]) == ("stale", False, "stale")
    assert state["presence"]["age_s"] == 3600.0 and state["presence"]["text"] == "last heard 60 min ago"
    check = fresh.check(can15, ": last heard")
    assert (check["code"], check["source"], check["received"]) == ("NotHeard", "io_diagnostics.roster", NOW)
    # one cause, counted once: the plugin's bus status names CAN 15 in caused_by, so the board (stale) is the cause
    bus = f"{fresh.path_of(A)}/can"
    assert fresh.check(bus)["caused_by"] == [can15] and node(fresh.host, bus)["knock_on_of"] == can15
    assert fresh.state()["summary"]["counts"] == {"stale": 1}
    # and CAN 14, 1 answered in this run: heard
    assert node(fresh.host, f"{fresh.path_of(A)}/can/14")["presence"]["state"] == "heard"


def test_a_board_that_never_answered_has_no_row_and_its_bus_says_so(tmp_path):
    rack = Rack(tmp_path)  # CAN 15 is expected and silent from the start
    assert f"{rack.path_of(A)}/can/15" not in rack.host.view().merged.entities
    assert rack.check(f"{rack.path_of(A)}/can")["message"] == "2 of 3 expected boards answer · CAN 15 silent"
    assert not [path for path in rack.host.view().merged.entities if "/expected" in path]
    assert rack.state()["summary"]["counts"] == {"warn": 1}


def test_a_board_that_went_quiet_keeps_its_row_and_its_bus_says_it_stopped(tmp_path):
    """No expected setup: only the host knows CAN 14 answered before, so it says it, on the bus, naming the board."""

    rack = Rack(tmp_path, manifest=None, rack=ALL_ANSWER)
    bus, can14 = f"{rack.path_of(A)}/can", f"{rack.path_of(A)}/can/14"
    assert rack.state()["summary"]["counts"] == {}
    rack.set_rack({**ALL_ANSWER, A: {**ALL_ANSWER[A], "answer": [1, 15], "silent": []}})  # CAN 14 goes quiet
    rack.host.clock.at = NOW + 600
    rack.host.run_all_once()
    assert can14 in rack.host.view().merged.entities  # its row stays: CAN has no unplug event, nobody knows it was removed
    presence = node(rack.host, can14)["presence"]
    assert (presence["state"], presence["text"]) == ("stale", "last heard 10 min ago")
    says = rack.check(bus, ": boards stopped answering")
    assert (says["level"], says["code"], says["message"], says["caused_by"]) == ("warn", "BoardsStoppedAnswering", "CAN 14 stopped answering", [can14])
    assert "Remove it from the bus" in values(says)["what_to_check"]
    # counted once: the stale board is the cause, the bus is its consequence
    assert rack.state()["summary"]["counts"] == {"stale": 1} and node(rack.host, bus)["knock_on_of"] == can14
    # a person removes it: the row, the bus's line and the count all go
    assert rack.host.setup_edit("forget", can14)["ok"]
    assert can14 not in rack.host.view().merged.entities and not rack.state()["summary"]["counts"]
    assert not [check for check in rack.host.node(bus)["checks"] if "stopped answering" in check["name"]]


# ----- remove from bus, re-ID ------------------------------------------------------------------------------------------
def test_removing_a_board_survives_a_restart_and_it_comes_back_when_it_answers(tmp_path):
    ledger = ledger_in(tmp_path)
    rack = Rack(tmp_path / "one", rack=ALL_ANSWER, ledger_file=ledger)
    can14 = f"{rack.path_of(A)}/can/14"
    assert rack.host.setup_edit("forget", can14)["ok"]
    assert can14 not in entity_paths(rack.host)
    assert "can/14" not in json.loads(ledger.read_text())["devices"][f"rig|usb:0483:5740:{A}"]["entities"]
    # a restart restores everything but the removed board
    fresh = Rack(tmp_path / "two", rack={**ALL_ANSWER, A: {**ALL_ANSWER[A], "hold": True}}, ledger_file=ledger, run=False)
    assert can14 not in entity_paths(fresh.host) and f"{rack.path_of(A)}/can/1" in entity_paths(fresh.host)
    # and the plugin reports it again: it comes back, no longer removed
    again = Rack(tmp_path / "three", rack=ALL_ANSWER, ledger_file=ledger)
    assert can14 in entity_paths(again.host)
    assert not json.loads(ledger.read_text())["forgotten"].get(f"rig|usb:0483:5740:{A}")


def test_a_re_id_board_keeps_one_row_by_its_uuid(tmp_path):
    """A moteus is matched by its UUID: answering from CAN 16 now, it is the same board, not a second row."""

    ledger = ledger_in(tmp_path)
    first = Rack(tmp_path / "one", manifest=None, rack={**ALL_ANSWER, A: {**ALL_ANSWER[A], "answer": [1, 14, 15], "uuids": {"15": "UUID-15"}}}, ledger_file=ledger)
    assert f"{first.path_of(A)}/can/15" in entity_paths(first.host)
    fresh = Rack(tmp_path / "two", manifest=None, rack={**ALL_ANSWER, A: {**ALL_ANSWER[A], "answer": [1, 14, 16], "silent": [], "uuids": {"16": "UUID-15"}}}, ledger_file=ledger)
    own = f"{fresh.path_of(A)}/can/"
    boards = sorted(path.rsplit("/", 1)[-1] for path in entity_paths(fresh.host) if path.startswith(own) and path.rsplit("/", 1)[-1].isdigit())
    assert boards == ["1", "14", "16"]  # 15 is gone: the row moved with its board


# ----- presence, claimed, live -------------------------------------------------------------------------------------------
def test_presence_is_neutral_and_says_when():
    entity = tree.Entity("x/dev", "dev", "device")
    from contract.record import OK, STALE, DiagnosticStatus
    from contract.record import Summary, Rollup

    def summary(own):
        return Summary("x/dev", own, own, "", Rollup())

    record = DiagnosticStatus(level=OK, name="dev: ran", path="x/dev", stamp=100.0, received=100.0, expected_period_s=30.0)
    assert tree.presence_of("x/dev", entity, summary(OK), [record], 100.0, 101.0, {}, frozenset()) == {
        "state": "heard", "age_s": 1.0, "restored": False, "text": ""}
    live = tree.presence_of("x/dev", entity, summary(OK), [record], 100.0, 101.0, {}, frozenset({"x/dev"}))
    assert (live["state"], live["text"]) == ("live", "data arriving now")
    stale = tree.presence_of("x/dev", entity, summary(STALE), [record], 100.0, 460.0, {}, frozenset())
    assert (stale["state"], stale["text"]) == ("stale", "last heard 6 min ago")
    restored = tree.presence_of("x/dev", entity, summary(None), [], None, 460.0, {"x/dev": 100.0}, frozenset())
    assert (restored["state"], restored["restored"], restored["text"]) == ("stale", True, "last heard 6 min ago (before this session)")
    # structure has nothing to hear from
    assert tree.presence_of("x/g", tree.Entity("x/g", "g", "group"), summary(OK), [record], 100.0, 101.0, {}, frozenset()) is None
    assert tree.presence_of("x/dev", entity, summary(None), [], None, 101.0, {}, frozenset()) is None


def test_a_device_no_plugin_claims_is_unclaimed_and_a_claimed_one_is_not(tmp_path):
    keyboard = usb_device("9-9", "046d", "c34b", "KB1", label="DURGOD keyboard", kind="hid")
    rack = Rack(tmp_path, devices=(*PRESENT, keyboard))
    by_name = {row["name"]: row["claimed"] for row in _flat(rack.state()["tree"])}
    assert by_name["DURGOD keyboard"] is False
    assert by_name[f"fdcanusb {A}"] is True and by_name["bam-GPU" if "bam-GPU" in by_name else "rig"] is True


def _flat(root: dict) -> list[dict]:
    out, stack = [], [root]
    while stack:
        item = stack.pop()
        out.append(item)
        stack.extend(item["children"])
    return out


def test_live_paths_follow_a_running_follower(tmp_path):
    rack = Rack(tmp_path)
    job = next(iter(rack.host.jobs.values()))

    class Process:
        def poll(self):
            return None

    class Follower:
        process, period_s = Process(), 1.0
        last_line = __import__("time").monotonic()

    rack.host.followers[job.key] = Follower()
    live = rack.host._live_paths(list(rack.host.jobs.values()))
    assert job.device_path in live and f"{job.device_path}/can/1" in live
    assert node(rack.host, f"{job.device_path}/can/1")["presence"]["state"] == "live"
    Follower.last_line -= 60  # silent for a minute: not live
    assert not rack.host._live_paths(list(rack.host.jobs.values()))


# ----- visibility is kept with the settings --------------------------------------------------------------------------------
def test_visibility_choices_are_kept_with_the_app_settings(tmp_path):
    file = tmp_path / "settings.toml"
    host = Host(plugins_dir=tmp_path / "none", clock=SetClock(NOW), os_source=FakeOs(standard_skeleton()), settings_file=file)
    answer = host.set_settings({"visibility": {"usb:046d:c34b:KB1": "show", 'a "quoted" id': "hide"}})
    assert answer["visibility"] == {"usb:046d:c34b:KB1": "show", 'a "quoted" id': "hide"}
    again = settings_module.Settings(file)
    assert again.visibility == answer["visibility"] and "[visibility]" in file.read_text()
    assert host.set_settings({"visibility": {"usb:046d:c34b:KB1": None}})["visibility"] == {'a "quoted" id': "hide"}
    for bad in ({"x": "maybe"}, {"": "hide"}, "hide"):
        with pytest.raises(settings_module.SettingsError):
            host.set_settings({"visibility": bad})
    with pytest.raises(settings_module.SettingsError):
        host.set_settings({"nope": 1})


# ----- the ledger file --------------------------------------------------------------------------------------------------
def test_a_broken_ledger_is_named_and_the_host_starts_empty(tmp_path):
    ledger = tmp_path / "tree.json"
    ledger.write_text("{not json")
    roster = roster_module.Roster(ledger)
    assert roster.devices == {} and "unreadable" in roster.note
    rack = Rack(tmp_path / "rack", ledger_file=ledger)
    [source] = [source for source in rack.state()["host"]["sources"] if source["name"] == "last-known tree"]
    assert source["state"] == "skipped" and "unreadable" in source["note"]
    assert json.loads(ledger.read_text())["devices"]  # rewritten whole with what it saw now
