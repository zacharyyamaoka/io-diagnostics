"""Host action plumbing (no HTTP in v1): ``Host.act`` and ``io-diagnostics act`` run a plugin's ``act`` for one action
registered on an entity, for a person only.

The rule (docs/api-v1.md "Actions"): stdin not a TTY (an agent) -> everything refused except ``--dry-run``; a danger
other than ``query`` -> refused without ``--confirm``; the token, ``--node`` and ``--dry-run`` pass through to the
plugin; the run takes the plugin's ``exclusive`` group. Fake plugins only (tests/test_host_support.py FAKE_PLUGIN).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

from test_host_support import ROOT, FakeOs, SetClock, read_log, standard_skeleton, usb_device, write_fake_plugin

from io_diagnostics.host import Host, MachineOs

WIDGET = usb_device("9-1", "1234", "abcd", "W1", label="widget")
CLI = ROOT / "io_diagnostics" / "cli.py"


def widget_host(tmp_path, *, diagnosed: bool = True, exclusive: str = "") -> tuple[Host, str, object]:
    log = tmp_path / "runs.jsonl"
    write_fake_plugin(tmp_path / "plugins.d", "widgets", ["usb:1234:*"], log, exclusive=exclusive, sleep_s=0.0)
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(standard_skeleton(WIDGET)))
    if diagnosed:
        host.run_all_once()
    else:
        host.refresh()
    return host, host.layer.device_paths["usb:9-1"], log


def acts(log) -> list[dict]:
    return [run for run in read_log(log) if run["verb"] == "act"]


def test_not_a_person_runs_only_a_dry_run(tmp_path):
    host, path, log = widget_host(tmp_path)
    refused = host.act(path, "check_comms", human=False)
    assert (refused["refused"], refused["outcome"]) == ("NotInteractive", "refused") and acts(log) == []
    refused = host.act(path, "reboot", node=15, confirm="TOKEN", human=False)  # a token does not make an agent a person
    assert refused["refused"] == "NotInteractive" and acts(log) == []
    dry = host.act(path, "reboot", node=15, dry_run=True, human=False)  # no token needed: nothing runs
    assert (dry["refused"], dry["outcome"], dry["returncode"]) == (None, "ok", 0)
    assert dry["argv"][-8:] == ["act", "--device", "usb:9-1", "--action", "reboot", "--node", "15", "--dry-run"]
    [run] = acts(log)
    assert run["argv"] == ["act", "--device", "usb:9-1", "--action", "reboot", "--node", "15", "--dry-run"]
    assert dry["records"] == [{"record": "action-result", "action": "reboot", "dry_run": True, "command": ["would", "run", "this"]}]


def test_the_default_asks_stdin_and_a_test_is_not_a_person(tmp_path):
    host, path, log = widget_host(tmp_path)
    assert host.act(path, "check_comms")["refused"] == "NotInteractive" and acts(log) == []  # pytest's stdin is no TTY


def test_a_person_needs_a_confirm_for_anything_but_a_query(tmp_path):
    host, path, log = widget_host(tmp_path)
    for action in ("reboot", "calibrate"):  # interrupts, moves
        refused = host.act(path, action, node=15, human=True)
        assert refused["refused"] == "ConfirmRequired" and acts(log) == []
    assert "Calibrate widgets device? The rotor spins." in refused["message"] and refused["danger"] == "moves"
    reboot = host.act(path, "reboot", node=15, confirm="TOKEN-15", human=True)
    assert (reboot["refused"], reboot["outcome"]) == (None, "ok")
    query = host.act(path, "check_comms", human=True)  # a query needs no token
    assert (query["refused"], query["outcome"]) == (None, "ok")
    assert [run["argv"] for run in acts(log)] == [
        ["act", "--device", "usb:9-1", "--action", "reboot", "--node", "15", "--confirm", "TOKEN-15"],
        ["act", "--device", "usb:9-1", "--action", "check_comms"]]


def test_refusals_before_anything_runs(tmp_path):
    host, path, log = widget_host(tmp_path)
    assert host.act("host/nowhere", "reboot", human=True)["refused"] == "NoSuchEntity"
    assert host.act(path, "format_disk", human=True)["refused"] == "ActionNotRegistered"
    usb_bus = next(entity_path for entity_path, entity in host.view().merged.entities.items() if entity.kind == "bus")
    assert host.act(usb_bus, "reboot", human=True)["refused"] == "NoPlugin"
    assert acts(log) == []


def test_the_action_comes_from_the_entity_after_a_diagnose_else_from_describe(tmp_path):
    host, path, _log = widget_host(tmp_path)
    assert host.act(path, "check_comms", dry_run=True, human=False)["registered_by"] == "entity"
    fresh, fresh_path, log = widget_host(tmp_path / "fresh", diagnosed=False)  # a fresh `io-diagnostics act` process
    result = fresh.act(fresh_path, "reboot", node=2, dry_run=True, human=False)
    assert (result["registered_by"], result["title"], result["danger"], result["outcome"]) == ("describe", "Reboot…",
                                                                                               "interrupts", "ok")
    assert [run["verb"] for run in read_log(log)] == ["describe", "act"]


def test_an_action_waits_for_its_exclusive_group(tmp_path):
    host, path, log = widget_host(tmp_path, exclusive="rack-bus")
    assert host.gates.try_take("rack-bus")  # a diagnose of the group is running
    answer = {}
    worker = threading.Thread(target=lambda: answer.update(host.act(path, "check_comms", dry_run=True, human=False)))
    worker.start()
    time.sleep(0.5)
    assert acts(log) == []  # queued behind the group, not failed
    released = time.time()
    host.gates.release("rack-bus")
    worker.join(timeout=20)
    [run] = acts(log)
    assert run["start"] >= released and answer["outcome"] == "ok"


def test_io_diagnostics_act_cli_refuses_an_agent_and_runs_its_dry_run(tmp_path):
    machine = ROOT / "tests" / "data" / "sysfs-rack-fixture.json"
    log = tmp_path / "runs.jsonl"
    write_fake_plugin(tmp_path / "plugins.d", "uarts", ["usb:0403:*"], log, sleep_s=0.0)  # the FT232R
    probe = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=MachineOs(machine))
    probe.refresh()
    [job] = probe.jobs.values()

    def act(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CLI), "act", "--machine", str(machine), "--plugins", str(tmp_path / "plugins.d"),
                               "--path", job.device_path, "--action", "reboot", "--node", "3", *extra],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, cwd="/")

    refused = act("--confirm", "TOKEN")
    assert refused.returncode == 2 and json.loads(refused.stdout)["refused"] == "NotInteractive", refused
    assert "refused: NotInteractive" in refused.stderr and [run for run in read_log(log) if run["verb"] == "act"] == []
    dry = act("--dry-run")
    assert dry.returncode == 0, dry.stderr
    out = json.loads(dry.stdout)
    assert (out["plugin"], out["outcome"], out["argv"][-1]) == ("uarts", "ok", "--dry-run")
    [run] = [run for run in read_log(log) if run["verb"] == "act"]
    assert run["argv"] == ["act", "--device", job.device["id"], "--action", "reboot", "--node", "3", "--dry-run"]


# An adapter with one board, in the live moteus shape (the CAN bus part_of the adapter, the board via the bus): the
# board's confirm texts say "CAN {id} on {name}" like the driver's. Logs every act's argv. Never touches hardware.
BOARD_PLUGIN = r'''
import json, os, sys

args = sys.argv[1:]
verb = next(word for word in args if word in ("describe", "diagnose", "act"))
ADAPTER = [{"name": "check_comms", "title": "Check communication", "description": "ask who answers", "danger": "query",
            "scope": "device", "confirm": "Query every id on {name}?", "kind": "touches"}]
BOARD = [{"name": "calibrate", "title": "Run calibration…", "description": "spins the rotor", "danger": "moves",
          "scope": "node", "confirm": "Calibrate CAN {id} on {name}? The rotor spins.", "kind": "changes"},
         {"name": "set_id", "title": "Change CAN id…", "description": "a new bus id", "danger": "changes",
          "scope": "node", "confirm": "Change the CAN id of CAN {id} on {name}?", "kind": "changes"}]
if verb == "describe":
    print(json.dumps({"name": "boards", "schema": "diagnostics-record/0", "actions": ADAPTER + BOARD}))
    raise SystemExit(0)
if verb == "diagnose":
    for record in ({"path": "", "name": "fdcanusb W1", "kind": "device", "identity": {"serial_number": "W1"}, "actions": ADAPTER},
                   {"path": "can", "name": "W1 CAN bus", "kind": "bus", "part_of": ""},
                   {"path": "can/15", "name": "CAN 15", "kind": "node", "via": "can", "actions": BOARD}):
        print(json.dumps({"schema": "diagnostics-record/0", "record": "entity", **record}))
    raise SystemExit(0)
with open(os.environ["BOARD_LOG"], "a") as log:
    log.write(json.dumps({"verb": "act", "argv": args}) + "\n")
print(json.dumps({"record": "action-result", "action": args[args.index("--action") + 1], "dry_run": "--dry-run" in args,
                  "command": ["would", "run", "this"]}))
'''


def board_host(tmp_path) -> tuple[Host, str, object]:
    log = tmp_path / "boards.jsonl"
    script = tmp_path / "board_plugin.py"
    script.write_text(BOARD_PLUGIN)
    (tmp_path / "plugins.d").mkdir()
    (tmp_path / "plugins.d" / "boards.toml").write_text(
        f'name = "boards"\ncommand = ["{{python}}", "{script}"]\ntimeout_s = 20\nperiod_s = 30\nmatches = ["usb:1234:*"]\n'
        f'env = {{ BOARD_LOG = "{log}" }}\n')
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(standard_skeleton(WIDGET)))
    host.run_all_once()
    return host, host.layer.device_paths["usb:9-1"], log


def test_set_id_passes_its_new_id_to_the_plugin(tmp_path):
    host, adapter, log = board_host(tmp_path)
    board = f"{adapter}/can/15"
    dry = host.act(board, "set_id", node=15, new_id=7, dry_run=True, human=False)
    assert (dry["refused"], dry["outcome"], dry["new_id"]) == (None, "ok", 7), dry
    ran = host.act(board, "set_id", node=15, new_id=7, confirm="TOKEN", human=True)
    assert ran["outcome"] == "ok", ran
    assert [run["argv"] for run in acts(log)] == [
        ["act", "--device", "usb:9-1", "--action", "set_id", "--node", "15", "--new-id", "7", "--dry-run"],
        ["act", "--device", "usb:9-1", "--action", "set_id", "--node", "15", "--new-id", "7", "--confirm", "TOKEN"]]
    host.act(board, "calibrate", node=15, dry_run=True, human=False)  # no new id: no --new-id
    assert "--new-id" not in acts(log)[-1]["argv"]


def test_a_confirm_names_the_device_that_owns_the_action(tmp_path):
    """``{name}`` on a board is its adapter (the drivers' convention), on a device the device itself."""

    host, adapter, _log = board_host(tmp_path)
    board = f"{adapter}/can/15"
    confirms = {action["name"]: action["confirm"] for action in host.node(board)["actions"]}
    assert confirms == {"calibrate": "Calibrate CAN 15 on fdcanusb W1? The rotor spins.",
                        "set_id": "Change the CAN id of CAN 15 on fdcanusb W1?"}, confirms
    refused = host.act(board, "calibrate", node=15, human=True)  # the ConfirmRequired text is filled the same way
    assert refused["refused"] == "ConfirmRequired" and "Calibrate CAN 15 on fdcanusb W1?" in refused["message"], refused
    [check] = [action for action in host.node(adapter)["actions"] if action["owner"] != "io_diagnostics"]  # not the host's own reset_port
    assert check["confirm"] == "Query every id on fdcanusb W1?", check


def test_io_diagnostics_act_cli_passes_new_id(tmp_path):
    machine = ROOT / "tests" / "data" / "sysfs-rack-fixture.json"
    log = tmp_path / "runs.jsonl"
    write_fake_plugin(tmp_path / "plugins.d", "uarts", ["usb:0403:*"], log, sleep_s=0.0)  # the FT232R
    probe = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=MachineOs(machine))
    probe.refresh()
    [job] = probe.jobs.values()
    dry = subprocess.run([sys.executable, str(CLI), "act", "--machine", str(machine), "--plugins", str(tmp_path / "plugins.d"),
                          "--path", job.device_path, "--action", "reboot", "--node", "3", "--new-id", "9", "--dry-run"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, cwd="/")
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["new_id"] == 9
    [run] = [run for run in read_log(log) if run["verb"] == "act"]
    assert run["argv"] == ["act", "--device", job.device["id"], "--action", "reboot", "--node", "3", "--new-id", "9", "--dry-run"]


def test_an_act_is_bounded_by_act_timeout_s_never_by_the_diagnose_timeout(tmp_path):
    log = tmp_path / "runs.jsonl"
    path = write_fake_plugin(tmp_path / "plugins.d", "slow", ["usb:1234:*"], log, sleep_s=0.0, timeout_s=0.5, act_sleep_s=1.0)
    host = Host(plugins_dir=path.parent, clock=SetClock(), os_source=FakeOs(standard_skeleton(WIDGET)))
    host.run_all_once()
    device_path = host.layer.device_paths["usb:9-1"]
    assert host.plugins()["plugins"][0]["act_timeout_s"] == 900.0  # the default
    slow = host.act(device_path, "check_comms", human=True)  # 1 s act under a 0.5 s diagnose timeout
    assert (slow["outcome"], slow["action_result"]["action"]) == ("ok", "check_comms"), slow
    path.write_text(path.read_text() + "act_timeout = 0.3\n")  # the alias spelling, like timeout / period
    short = Host(plugins_dir=path.parent, clock=SetClock(), os_source=FakeOs(standard_skeleton(WIDGET)))
    short.run_all_once()
    timed_out = short.act(device_path, "check_comms", human=True)
    assert timed_out["outcome"] == "timeout" and "act_timeout_s" in timed_out["message"], timed_out
