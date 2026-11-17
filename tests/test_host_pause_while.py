"""``pause_while`` (plugins.d/README.md): while one of a plugin's globs matches an existing path, the host starts none of its
processes (diagnose, probe, follow, watch, presence, a person's act), its device row says "paused" in plain words at level OK,
a running watch is stopped, and an act is refused ``PausedForArm``. When the paths are gone its jobs run again on their
normal schedule. WHY (2026-11-16 05:42:42Z): a pick's look on the overhead D435 got "Device or resource busy" mid arm
session, and nothing outside the arm session may open the camera while an arm job runs.

Fake plugins only: tests/test_host_support.py (its hardware tripwire is armed by importing it) and the state fixture plugin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_host_support import FakeOs, SetClock, read_log, standard_skeleton, usb_device, write_fake_plugin
from test_port_activity import follow_host
from test_state_config import Rig, alive, wait_for

from io_diagnostics.host import Host
from io_diagnostics.plugins import PluginConfigError, load_plugin

WIDGET = usb_device("9-1", "1234", "abcd", "W1", label="widget")
GADGET = usb_device("9-2", "5678", "ef01", "G1", label="gadget")


def with_pause(toml: Path, *globs: str) -> Path:
    toml.write_text(toml.read_text() + "pause_while = [" + ", ".join(f'"{glob}"' for glob in globs) + "]\n")
    return toml


def widget_host(tmp_path: Path, *, paused: bool = True) -> tuple[Host, str, Path, Path]:
    """A host over one widget whose plugin pauses while ``<tmp>/running/*.json`` exists; ``(host, device path, log, job file)``."""

    log, running = tmp_path / "runs.jsonl", tmp_path / "running"
    running.mkdir()
    job_file = running / "job1.json"
    if paused:
        job_file.write_text("{}")
    with_pause(write_fake_plugin(tmp_path / "plugins.d", "widgets", ["usb:1234:*"], log, sleep_s=0.0), f"{running}/*.json")
    write_fake_plugin(tmp_path / "plugins.d", "gadgets", ["usb:5678:*"], log, sleep_s=0.0)
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(standard_skeleton(WIDGET, GADGET)))
    host.refresh()
    return host, host.layer.device_paths["usb:9-1"], log, job_file


def runs(log: Path, plugin: str, verb: str) -> list[dict]:
    return [run for run in read_log(log) if run["plugin"] == plugin and run["verb"] == verb]


def plugin_run(host: Host, path: str, plugin: str) -> dict:
    return next(check for check in host.node(path)["checks"] if check["name"] == f"{plugin}: plugin run")


def test_pause_while_is_parsed_with_home_expanded_and_must_be_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    toml = tmp_path / "cam.toml"
    toml.write_text('name = "cam"\ncommand = ["true"]\npause_while = ["~/.local/state/rack/queue/running/*.json", "/abs/hold"]\n')
    spec, notes = load_plugin(toml)
    assert spec.pause_while == (f"{tmp_path}/home/.local/state/rack/queue/running/*.json", "/abs/hold") and notes == []
    toml.write_text('name = "cam"\ncommand = ["true"]\npause_while = ["relative/*.json"]\n')
    with pytest.raises(PluginConfigError, match="pause_while"):
        load_plugin(toml)
    toml.write_text('name = "cam"\ncommand = ["true"]\npause_while = "/abs/hold"\n')
    with pytest.raises(PluginConfigError, match="pause_while"):
        load_plugin(toml)
    toml.write_text('name = "cam"\ncommand = ["true"]\n')
    assert load_plugin(toml)[0].pause_while == ()


def test_a_pause_path_starts_no_diagnose_and_the_row_says_paused(tmp_path):
    host, path, log, job_file = widget_host(tmp_path)
    host.run_all_once()
    assert runs(log, "widgets", "diagnose") == []  # no process was started for it
    check = plugin_run(host, path, "widgets")
    assert check["level"] == "ok" and check["code"] == "PausedForArm"
    assert check["message"].startswith("paused: an arm job is running") and str(job_file) in check["message"]
    assert host.plugins()["plugins"][-1]["paused_by"] == str(job_file)


def test_the_next_due_diagnose_runs_once_the_pause_path_is_gone(tmp_path):
    host, path, log, job_file = widget_host(tmp_path)
    [job] = [job for job in host.jobs.values() if job.spec.name == "widgets"]
    host.run_job(job)
    assert runs(log, "widgets", "diagnose") == []
    job_file.unlink()
    host.run_job(job)  # the scheduler's next turn for it
    assert len(runs(log, "widgets", "diagnose")) == 1
    check = plugin_run(host, path, "widgets")
    assert check["code"] != "PausedForArm" and not check["message"].startswith("paused")


def test_a_running_watch_is_stopped_when_a_pause_starts_and_resumes_after(tmp_path):
    running = tmp_path / "running"
    running.mkdir()
    rig = Rig(tmp_path, "camera", plugin_extra=f'pause_while = ["{running}/*.json"]\n')
    try:
        rig.host.watch_heartbeat(rig.device)
        started = wait_for(lambda: (state := rig.host.watch_json(*rig.host._watch_target(rig.device)))["state"] == "running" and state,
                           what="the watch running")
        (running / "job1.json").write_text("{}")
        paused = wait_for(lambda: (rig.host.watch_heartbeat(rig.device),
                                   state := rig.host.watch_json(*rig.host._watch_target(rig.device)))[1]["state"] == "paused" and state,
                          what="the watch pausing")
        assert paused["reason"].startswith("paused: an arm job is running") and paused["holders"] == []
        wait_for(lambda: not alive(started["pid"]), what="the watch process stopped")
        (running / "job1.json").unlink()
        wait_for(lambda: rig.host.watch_heartbeat(rig.device)["watch"]["state"] == "running", what="the watch resuming")
    finally:
        rig.stop()


def test_a_follow_process_is_stopped_while_paused_and_starts_again_after(tmp_path):
    running = tmp_path / "running"
    running.mkdir()
    host, log = follow_host(tmp_path)
    with_pause(tmp_path / "plugins.d" / "ticker.toml", f"{running}/*.json")
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=host.clock, os_source=host.os, follow_backoff_s=(0.2, 0.4))
    host.start(udev=False)
    try:
        wait_for(lambda: "follow " in (log.read_text() if log.exists() else ""), what="the follow process")
        [job] = host.jobs.values()
        pid = host.followers[job.key].process.pid
        (running / "job1.json").write_text("{}")
        wait_for(lambda: job.key not in host.followers and not alive(pid), what="the follow process stopped")
        run = next(check for check in host.node("host")["checks"] if check["name"] == "ticker: plugin run")
        assert run["level"] == "ok" and run["code"] == "PausedForArm" and run["message"].startswith("paused: an arm job is running")
        (running / "job1.json").unlink()
        host.wake()  # the next refresh starts it again
        wait_for(lambda: job.key in host.followers and host.followers[job.key].state()["state"] == "running", what="following again")
    finally:
        host.stop()


def test_an_act_is_refused_paused_for_arm_and_its_dry_run_shows_the_failing_precondition(tmp_path):
    host, path, log, job_file = widget_host(tmp_path, paused=False)
    host.run_all_once()  # the widget's row and its actions exist
    job_file.write_text("{}")
    refused = host.act(path, "check_comms", human=True)
    assert (refused["refused"], refused["outcome"]) == ("PausedForArm", "refused") and str(job_file) in refused["message"]
    assert host.act(path, "reboot", node=15, confirm="TOKEN", human=True)["refused"] == "PausedForArm"
    dry = host.act(path, "reboot", node=15, dry_run=True, human=False)
    assert dry["refused"] == "PausedForArm"
    [failing] = [pre for pre in dry["preconditions"] if pre["state"] == "fail"]
    assert failing["check"] == "not_paused_for_arm" and failing["resolves"] == "blocks" and str(job_file) in failing["detail"]
    assert runs(log, "widgets", "act") == []  # neither the act nor its dry run started a process
    actions = {action["name"]: action for action in host.node(path)["actions"]}
    assert actions["reboot"]["state"] == "blocked" and "paused: an arm job is running" in actions["reboot"]["reason"]
    job_file.unlink()
    assert host.act(path, "check_comms", human=True)["refused"] is None
    assert {action["name"]: action for action in host.node(path)["actions"]}["reboot"]["state"] == "ready"


def test_a_plugin_without_pause_while_is_unaffected(tmp_path):
    host, _path, log, job_file = widget_host(tmp_path)
    gadget = host.layer.device_paths["usb:9-2"]
    host.run_all_once()
    assert runs(log, "widgets", "diagnose") == [] and len(runs(log, "gadgets", "diagnose")) == 1
    check = plugin_run(host, gadget, "gadgets")
    assert check["code"] != "PausedForArm" and not check["message"].startswith("paused")
    assert host.act(gadget, "check_comms", human=True)["refused"] is None
    actions = host.node(gadget)["actions"]
    assert not any(pre["check"] == "not_paused_for_arm" for action in actions for pre in action["preconditions"])
    assert all(action["state"] == "ready" for action in actions if action["owner"] == "gadgets")
    assert host.plugins()["plugins"][0]["paused_by"] is None
