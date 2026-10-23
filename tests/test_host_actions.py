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
