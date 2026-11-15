"""The host's second built-in, ``reset`` · Reset… (docs/device-reset.md), wired like ``reset_port`` (docs/usb-reset.md):
offered on every USB device row and every CAN board below a USB adapter (never on a port row), run by
``python -m io_diagnostics.recovery`` through the same dry run -> token -> run path, with the same gates.

Everything runs against a fake ``/sys/bus/usb/devices``, ``/dev`` and ``/proc`` (``tests/test_usb_reset.py``'s ``Tree``); the
recovery module is only ever asked for a dry run or refused before it runs, and the hardware tripwire
(``tests/test_host_support.py``, imported) refuses a real ladder with no fake tree named.

    uv run --project /home/bam/io-diagnostics --group dev pytest /home/bam/io-diagnostics/tests/test_host_reset_action.py
"""

from __future__ import annotations

import dataclasses
import json
import sys

import pytest
from test_host_support import ROOT  # the import arms the hardware tripwire
from test_usb_reset import NAME, PID, PORT, TERMINAL, VID, Rig, Tree, declare, post, wait_for

from contract.record import STANDARD_ACTIONS, validate
from io_diagnostics import host_utilities
from io_diagnostics.host import Clock, Host

SERIAL = "0607623A"
BOARD = 12
CASES = ROOT / "tests" / "fixtures" / "plugins" / "cases" / "case_plugin.py"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    made = Tree(tmp_path / "usb", monkeypatch)
    (made.sysfs / PORT / "serial").write_text(SERIAL + "\n")  # the ladder keys a board by its adapter's serial (can:<serial>/<id>)
    monkeypatch.setenv("IO_DIAGNOSTICS_HUB_POWER_FACTS", str(tmp_path / "no-hub-facts.txt"))
    monkeypatch.setenv("IO_DIAGNOSTICS_FDCANUSB_RESET", str(tmp_path / "no-fdcanusb-reset"))  # not installed here
    monkeypatch.setenv("IO_DIAGNOSTICS_RACK_BUS_LOCK", str(tmp_path / "rack-bus.lock"))
    return made


@pytest.fixture
def rig(tmp_path, tree):
    made: list[Rig] = []

    def make(**options) -> Rig:
        made.append(Rig(tmp_path / f"rig{len(made)}", tree, **options))
        return made[-1]

    yield make
    for each in made:
        each.stop()


def board_path(rig_: Rig) -> str:
    rig_.host.run_all_once()  # the rack stand-in's diagnose lists its boards under can/<id>
    path = f"{rig_.path()}/can/{BOARD}"
    assert path in rig_.host.view().merged.entities, sorted(rig_.host.view().merged.entities)
    return path


def action(rig_: Rig, path: str, name: str = "reset") -> dict | None:
    return next((item for item in rig_.host.node(path)["actions"] if item["name"] == name), None)


def dry(rig_: Rig, path: str) -> dict:
    return rig_.host.act(path, "reset", dry_run=True, human=False)


# ----- the vocabulary -------------------------------------------------------------------------------------------------------
def test_the_name_title_and_danger_are_the_standard_vocabulary():
    assert STANDARD_ACTIONS["reset"] == "Reset…" == host_utilities.RESET.title
    assert host_utilities.RESET.danger == "interrupts" and host_utilities.RESET.scope == "device"
    assert validate(host_utilities.RESET) == [] and validate(dataclasses.replace(host_utilities.RESET, title="Reset")) != []
    checks = {check for check, _text in host_utilities.RESET.preconditions}
    assert {"device_present", "no_run_active", "device_free"} <= checks <= host_utilities.CHECKS
    assert host_utilities.RESET_SPEC.command[-2:] == ("-m", "io_diagnostics.recovery")
    assert host_utilities.RESET_SPEC.act_timeout_s == 300.0
    assert host_utilities.RESET_PORT.name == "reset_port" and host_utilities.SPEC.act_timeout_s == 90.0, "reset_port is as it was"


# ----- where it is offered ----------------------------------------------------------------------------------------------------
def test_a_usb_device_row_offers_reset_and_its_port_row_does_not(rig):
    rig_ = rig()
    device, port = rig_.path(), rig_.port_path()
    offered = action(rig_, device)
    assert offered and offered["title"] == "Reset…" and offered["danger"] == "interrupts" and offered["owner"] == "io_diagnostics"
    assert offered["state"] == "ready", offered
    assert offered["confirm"].startswith(f"Reset {NAME}?") and "tview" in offered["confirm"]
    assert action(rig_, device, "reset_port") is not None, "both built-ins on a device row"
    assert action(rig_, port) is None and action(rig_, port, "reset_port") is not None, "a port row keeps only reset_port"
    assert rig_.host.act(port, "reset", dry_run=True, human=False)["refused"] in ("ActionNotRegistered", "NoPlugin")
    bus = next(path for path, entity in rig_.host.layer.entities.items() if entity.kind == "bus")
    assert action(rig_, bus) is None and action(rig_, "host") is None


def test_a_can_board_row_below_the_adapter_offers_it_and_its_run_names_the_board(rig):
    rig_ = rig()
    path = board_path(rig_)
    offered = action(rig_, path)
    # no moteus_servo driver declared on this host: the board's only reset (its own `d reset`) has nothing to run it, so the
    # button is greyed with that reason rather than offering a Run that could only end "not cured"
    assert offered and offered["title"] == "Reset…" and offered["state"] == "blocked", offered
    assert "moteus_servo" in json.dumps(offered), offered
    assert offered["confirm"].startswith(f"Reset CAN {BOARD} on {NAME}?"), offered["confirm"]
    assert action(rig_, f"{rig_.path()}/can") is None, "not on the bus row"
    answer = dry(rig_, path)
    assert answer["refused"] is None and answer["node"] == BOARD and answer["device_id"] == f"usb:{PORT}"
    argv = answer["argv"]
    assert argv[argv.index("--device") + 1] == f"usb:{PORT}" and argv[argv.index("--node") + 1] == str(BOARD)
    assert argv[argv.index("--plugins") + 1] == str(rig_.host.plugins_dir), "the ladder sees the drivers this host runs"
    plan = answer["action_result"]
    assert plan["device"] == f"can:{SERIAL}/{BOARD}" and plan["profile"] == "moteus_board", plan


# ----- the dry run ------------------------------------------------------------------------------------------------------------
def test_the_dry_run_is_the_recovery_modules_plan_rung_by_rung(rig, tree):
    rig_ = rig()
    answer = dry(rig_, rig_.path())
    assert (answer["refused"], answer["outcome"], answer["plugin"], answer["registered_by"]) == (None, "ok", "io_diagnostics", "builtin")
    assert answer["argv"][:3] == [sys.executable, "-m", "io_diagnostics.recovery"]
    assert answer["argv"][3:8] == ["act", "--device", f"usb:{PORT}", "--action", "reset"]
    assert "--dry-run" in answer["argv"] and "--node" not in answer["argv"] and "--plugins" in answer["argv"]
    plan = answer["action_result"]
    assert plan["record"] == "action-result" and plan["dry_run"] is True and plan["token"]
    assert plan["steps"] and all({"kind", "title", "why", "expect", "runs", "skip", "skip_why", "available"} <= set(step)
                                 for step in plan["steps"]), plan["steps"]
    by_kind = {step["kind"]: step for step in plan["steps"]}
    assert plan["profile"] == "fdcanusb" and by_kind["native"]["runs"] is False and "not installed" in by_kind["native"]["skip_why"]
    assert by_kind["usbreset"]["runs"] is True and "usbreset 009/006" in by_kind["usbreset"]["available"]["why"]
    assert by_kind["reopen"]["runs"] is False and by_kind["reopen"]["skip_why"], "a person pressing Reset has reopened already"
    assert plan["runs"] == ["usbreset"], plan["runs"]
    assert plan["holders"] == [] and plan["second_confirm"] is None and plan["danger"] == "interrupts" and plan["confirm"]
    assert tree.calls() == [], "a dry run runs nothing"


def test_a_board_whose_driver_is_declared_plans_its_restart_as_moves(rig, tmp_path):
    def driver(directory):  # a moteus_servo declared for this host (it matches nothing here: the ladder only looks it up)
        declare(directory, "moteus_servo", f'["{sys.executable}", "{CASES}", "--case", "healthy"]', "usb:ffff:ffff")

    rig_ = rig(extra_plugins=driver)
    path = board_path(rig_)
    assert action(rig_, path)["state"] == "ready", "with its driver declared, a board's Reset… may run"
    plan = dry(rig_, path)["action_result"]
    restart = next(step for step in plan["steps"] if step["kind"] == "native")
    assert restart["runs"] is True and "moteus_servo" in restart["available"]["why"], restart
    assert plan["danger"] == "moves", "a moteus restart drops a loaded joint: the plan says so"


def test_a_holder_is_the_second_confirmation_and_it_reaches_the_argv(rig, tree):
    tree.open_file(4242, "tview", "ttyACM0")
    rig_ = rig()
    listed = action(rig_, rig_.path())
    assert listed["state"] == "ready" and "tview (pid 4242)" in listed["second_confirm"], "a holder is confirmed, never a block"
    plan = dry(rig_, rig_.path())["action_result"]
    assert plan["others"] and plan["second_confirm"] == f"tview (pid 4242) will lose {NAME}"


# ----- the gates --------------------------------------------------------------------------------------------------------------
def test_not_a_person_is_refused_and_a_person_without_the_token_is_asked_for_it(rig, tree):
    rig_ = rig()
    for path in (rig_.path(), board_path(rig_)):
        refused = rig_.host.act(path, "reset", confirm="x", human=False)
        assert refused["refused"] == "NotInteractive" and refused["plugin"] == "io_diagnostics", refused
        asked = rig_.host.act(path, "reset", human=True)
        assert asked["refused"] == "ConfirmRequired" and "Reset" in asked["message"] and "--confirm" in asked["message"], asked
    assert tree.calls() == []


def test_a_run_active_on_the_device_refuses_it_and_a_board_shares_its_adapters_runs(tmp_path, tree, monkeypatch):
    monkeypatch.setenv("TERMINAL_LINES", "30")
    rig_ = Rig(tmp_path / "busy", tree, plugin=False)
    try:
        declare(rig_.tmp / "plugins.d", "terminal", f'["{sys.executable}", "{TERMINAL}"]', f"usb:{VID}:{PID}",
                'env = { TERMINAL_LOG = "' + str(rig_.tmp / "term.jsonl") + '" }\n')
        busy = Host(plugins_dir=rig_.tmp / "plugins.d", clock=Clock(), os_source=rig_.os)
        busy.run_all_once()
        path = busy.layer.device_paths[f"usb:{PORT}"]
        run, _out = busy.start_act(path, "check_comms", interactive=True)
        assert run is not None and run.active
        listed = next(a for a in busy.node(path)["actions"] if a["name"] == "reset")
        assert listed["state"] == "blocked" and "is running on it" in listed["reason"], listed
        dry_ = busy.act(path, "reset", dry_run=True, human=False)
        assert dry_["refused"] == "RunActive" and "Check communication" in dry_["message"], dry_
        assert busy.start_act(path, "reset", confirm="whatever", interactive=True)[1]["refused"] == "RunActive"
        # a board's reset is its adapter's: a run on the adapter, or on another board of it, blocks it, and the other way round
        found = (busy.layer.devices[f"usb:{PORT}"], PORT)
        assert busy._active_run_near(f"{path}/can/{BOARD}", busy.layer, found)
        run.interrupt()
        run.wait(30)
        wait_for(lambda: not busy.runs.active(), what="the run to end")
        assert busy.act(path, "reset", dry_run=True, human=False)["refused"] is None
        busy.stop()
    finally:
        rig_.stop()


def test_a_driver_opts_its_device_out_and_its_boards_go_with_it(rig, tmp_path):
    keeper = tmp_path / "keeper.py"
    keeper.write_text('import json\nprint(json.dumps({"name": "keeper", "schema": "diagnostics-record/0", "opt_out": ["reset"],'
                      ' "actions": []}))\n')
    held = rig(extra_plugins=lambda directory: declare(directory, "keeper", f'["{sys.executable}", "{keeper}"]', f"usb:{VID}:{PID}"))
    path = board_path(held)
    for row in (held.path(), path):
        assert action(held, row) is None, row
        assert held.host.act(row, "reset", dry_run=True, human=False)["refused"] == "ActionNotRegistered", row
    assert action(held, held.path(), "reset_port") is not None, "opting out of reset leaves reset_port alone"


def test_a_driver_that_registers_reset_itself_replaces_the_built_in(rig):
    rig_ = rig()
    path = rig_.path()
    entity = rig_.host.layer.entities[path]
    own = dataclasses.replace(host_utilities.RESET, confirm="The driver's own words")
    actions = rig_.host._entity_actions(path, dataclasses.replace(entity, actions=(own,)), rig_.host.view())
    assert [item.confirm for item in actions if item.name == "reset"] == ["The driver's own words"]


def test_a_replay_host_greys_it_and_refuses_a_real_run(tmp_path, tree, monkeypatch):
    rig_ = Rig(tmp_path / "replay", tree, os_mode="fixture")
    try:
        monkeypatch.delenv("IO_DIAGNOSTICS_USB_SYSFS")  # no fake tree: this would be the real /sys
        listed = action(rig_, rig_.path())
        assert listed["state"] == "blocked" and "replays a recording" in listed["reason"], listed
        refused = rig_.host.start_act(rig_.path(), "reset", confirm="any-token", interactive=True)
        assert refused[0] is None and refused[1]["refused"] == "NotLive", refused[1]
    finally:
        rig_.stop()


# ----- after a run ------------------------------------------------------------------------------------------------------------
def test_a_reset_run_that_ended_probes_the_devices_driver_again_and_a_refusal_does_not(rig):
    rig_ = rig()
    rig_.host.run_all_once()
    job = next(job for job in rig_.host.jobs.values() if job.spec.name == "rack")

    def ended(exit_code: int) -> None:
        job.probed = True
        spec = type("Spec", (), {"action": "reset", "plugin": "io_diagnostics", "device_id": f"usb:{PORT}"})()
        rig_.host._on_run_end(type("Run", (), {"spec": spec, "exit_code": exit_code, "state": "done", "result": {}})())

    ended(2)
    assert job.probed is True, "a refusal (exit 2) changed nothing"
    ended(1)
    assert job.probed is False, "a ladder that ran (even one that did not cure it) is followed by a probe"


# ----- the page's POST /api/v1/act ---------------------------------------------------------------------------------------------
def test_confirm_holders_from_a_request_reaches_the_argv_only_for_the_built_ins(rig, tmp_path, monkeypatch):
    from test_server_act import TOKEN_PLUGIN

    from io_diagnostics.server import make_server, serve_in_thread

    script = tmp_path / "token_plugin.py"
    script.write_text(TOKEN_PLUGIN)
    rig_ = rig(extra_plugins=lambda directory: declare(directory, "tokens", f'["{sys.executable}", "{script}"]', f"usb:{VID}:{PID}",
                                                       f'env = {{ TOKEN_LOG = "{tmp_path / "acts.jsonl"}" }}\n'))
    rig_.host.run_all_once()
    seen = []
    # WHY stop at the plan: the built-in's real run would start the ladder; the argv the host builds is the claim
    monkeypatch.setattr(rig_.host, "start_act", lambda path, name, **k: (
        seen.append((name, rig_.host._plan_act(path, name, node=k.get("node"), confirm=k.get("confirm"), dry_run=False, human=True,
                                               new_id=k.get("new_id"), extra=k.get("extra"))[0]["argv"])),
        (None, {"refused": "Stopped", "message": "the test stops here"}))[1])
    http = make_server(rig_.host, 0)
    serve_in_thread(http)
    try:
        for name in ("reset", "reset_port", "check_comms"):  # the last is a driver's own action, with a token of its own
            status, answer = post(rig_.host.url, {"path": rig_.path(), "action": name, "node": None, "dry_run": True})
            assert status == 200 and answer["token"], (name, answer)
            post(rig_.host.url, {"path": rig_.path(), "action": name, "node": None, "token": answer["token"], "detach": True,
                                 "confirm_holders": True})
        flags = {name: "--confirm-holders" in argv for name, argv in seen}
        assert flags == {"reset": True, "reset_port": True, "check_comms": False}, seen
        argv = dict(seen)["reset"]
        assert argv[argv.index("--confirm") + 1] and "--dry-run" not in argv
    finally:
        http.shutdown()
        http.server_close()


def test_the_host_knows_both_specs_as_its_own():
    assert host_utilities.is_builtin(host_utilities.SPEC) and host_utilities.is_builtin(host_utilities.RESET_SPEC)
    assert set(host_utilities.SPECS) == set(host_utilities.NAMES) == {"reset_port", "reset"}
