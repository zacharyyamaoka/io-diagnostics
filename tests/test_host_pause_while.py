"""``pause_while`` (plugins.d/README.md): while one of a plugin's globs matches an existing path, the host starts none of its
processes (diagnose, probe, follow, watch, presence, a person's act), its device row says "paused" in plain words at level OK,
a running watch is stopped, and an act is refused ``PausedForArm``. When the paths are gone its jobs run again on their
normal schedule. WHY (2026-11-16 05:42:42Z): a pick's look on the overhead D435 got "Device or resource busy" mid arm
session, and nothing outside the arm session may open the camera while an arm job runs.

Fake plugins only: tests/test_host_support.py (its hardware tripwire is armed by importing it) and the state fixture plugin.
"""

from __future__ import annotations

import json
import os
import time
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


# ----- the tail after the last arm job, a p0 job that is ready, the port and hub rows, the headline (2026-11-16, follow-up) ---------
# WHY (the rig dispatcher): a pick's look runs just BEFORE its rackq job starts, so running/*.json alone misses the moment that
# matters; the camera stays paused for a while after the last job and while a p0 job is ready to start.
HUB = usb_device("9-2", "05e3", "0610", "", kind="hub", label="Hub")
BEHIND_HUB = usb_device("9-2.4", "1234", "abcd", "C1", label="camera", parent="usb:9-2")
PERIODIC_PLUGIN = r'''
import json, os, sys

args = sys.argv[1:]
verb = next(word for word in args if word in ("describe", "diagnose", "act"))
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"plugin": "cams", "verb": verb}) + "\n")
if verb == "describe":
    print(json.dumps({"name": "cams", "schema": "diagnostics-record/0", "actions": []}))
elif verb == "diagnose":
    print(json.dumps({"schema": "diagnostics-record/0", "record": "entity", "path": "", "name": "cam", "kind": "device"}))
    print(json.dumps({"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": "cam: streams", "path": "",
                      "message": "color and depth configured", "expected_period_s": 30}))
'''


def queue_host(tmp_path: Path, extra: str, *devices: dict) -> tuple[Host, Path]:
    """A host over ``devices`` (a widget by default) whose fake plugin's declaration ends with ``extra``; ``(host, log)``."""

    log = tmp_path / "runs.jsonl"
    toml = write_fake_plugin(tmp_path / "plugins.d", "widgets", ["usb:1234:*"], log, sleep_s=0.0)
    toml.write_text(toml.read_text() + extra)
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(standard_skeleton(*(devices or (WIDGET,)))))
    host.refresh()
    return host, log


def test_the_tail_and_json_keys_are_parsed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    toml = tmp_path / "cam.toml"
    toml.write_text('name = "cam"\ncommand = ["true"]\npause_tail_s = 180\npause_tail_dirs = ["~/q/running", "/q/done"]\n'
                    'pause_while_json = [{glob = "~/q/pending/*.json", match = {priority = 0, ready = true}, reason = "a p0 job is ready"}]\n')
    spec, notes = load_plugin(toml)
    assert notes == [] and spec.pause_tail_s == 180.0 and spec.pause_tail_dirs == (f"{tmp_path}/home/q/running", "/q/done")
    [rule] = spec.pause_while_json
    assert (rule.glob, rule.match, rule.reason) == (f"{tmp_path}/home/q/pending/*.json", (("priority", 0), ("ready", True)),
                                                   "a p0 job is ready")
    for bad in ('pause_tail_s = 180\n', 'pause_tail_dirs = ["/q/done"]\n', 'pause_tail_s = -1\npause_tail_dirs = ["/q"]\n',
                'pause_while_json = [{glob = "rel/*.json", match = {ready = true}}]\n',
                'pause_while_json = [{glob = "/q/*.json"}]\n', 'pause_while_json = [{glob = "/q/*.json", match = {a = [1]}}]\n'):
        toml.write_text('name = "cam"\ncommand = ["true"]\n' + bad)
        with pytest.raises(PluginConfigError, match="pause_"):
            load_plugin(toml)


@pytest.mark.parametrize(("age_s", "paused"), [(170.0, True), (190.0, False)])
def test_the_tail_pauses_within_180_s_of_the_newest_file_in_running_or_done(tmp_path, age_s, paused):
    running, done = tmp_path / "running", tmp_path / "done"
    running.mkdir()
    done.mkdir()
    old, newest = done / "1116-220000-aaaa.json", done / "1116-232212-850a.json"
    for path, age in ((old, 3600.0), (newest, age_s)):
        path.write_text("{}")
        os.utime(path, (time.time() - age, time.time() - age))
    host, log = queue_host(tmp_path, f'pause_tail_s = 180\npause_tail_dirs = ["{running}", "{done}"]\n')
    host.run_all_once()
    path = host.layer.device_paths["usb:9-1"]
    check = plugin_run(host, path, "widgets")
    if paused:
        ended = time.strftime("%H:%M:%S", time.localtime(newest.stat().st_mtime))
        assert runs(log, "widgets", "diagnose") == [] and check["code"] == "PausedForArm"
        assert check["message"].startswith(f"paused: within 180 s of the last arm job (ended {ended})")
        assert host.plugins()["plugins"][0]["paused_by"] == str(newest)
    else:
        assert len(runs(log, "widgets", "diagnose")) == 1 and check["code"] != "PausedForArm"


@pytest.mark.parametrize(("job", "paused"), [({"priority": 0, "ready": True}, True), ({"priority": 1, "ready": True}, False),
                                            ({"priority": 0, "ready": False}, False), ({"priority": False, "ready": 1}, False),
                                            ({"ready": True}, False)])
def test_a_p0_job_that_is_ready_pauses_and_nothing_else_in_pending_does(tmp_path, job, paused):
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "1116-232325-b1e2.json").write_text(json.dumps({"schema": "bam-rack-job/1", "id": "1116-232325-b1e2", **job}))
    (pending / "1116-000000-torn.json").write_text('{"priority": 0, "ready": tr')  # a file mid-write is no reason either way
    host, log = queue_host(tmp_path, f'pause_while_json = [{{glob = "{pending}/*.json", match = {{priority = 0, ready = true}},'
                                     ' reason = "a p0 job is ready"}]\n')
    host.run_all_once()
    check = plugin_run(host, host.layer.device_paths["usb:9-1"], "widgets")
    if paused:
        assert runs(log, "widgets", "diagnose") == [] and check["code"] == "PausedForArm"
        assert check["message"].startswith("paused: a p0 job is ready (1116-232325-b1e2)")
    else:
        assert len(runs(log, "widgets", "diagnose")) == 1 and check["code"] != "PausedForArm"


def test_reset_port_is_refused_on_the_cameras_port_row_and_on_the_hub_above_it(tmp_path):
    running = tmp_path / "running"
    running.mkdir()
    host, log = queue_host(tmp_path, f'pause_while = ["{running}/*.json"]\n', HUB, BEHIND_HUB)
    host.run_all_once()
    layer = host.layer
    rows = {"camera": layer.device_paths["usb:9-2.4"], "camera port": layer.port_paths["9-2.4"],
            "hub": layer.device_paths["usb:9-2"], "hub port": layer.port_paths["9-2"]}

    def pause_preconditions(path: str) -> dict[str, list]:
        return {action["name"]: [pre for pre in action["preconditions"] if pre["check"] == "not_paused_for_arm"]
                for action in host.node(path)["actions"]}

    for where, path in rows.items():
        assert not any(pause_preconditions(path).values()), where  # not paused: nothing says so
    (running / "job1.json").write_text("{}")
    for where, path in rows.items():
        for dry_run, human in ((True, False), (False, True)):
            answer = host.act(path, "reset_port", confirm="TOKEN", dry_run=dry_run, human=human)
            assert answer["refused"] == "PausedForArm" and "paused: an arm job is running" in answer["message"], (where, answer)
        found = pause_preconditions(path)
        assert found["reset_port"] and found["reset_port"][0]["state"] == "fail", where
        action = next(action for action in host.node(path)["actions"] if action["name"] == "reset_port")
        assert action["state"] == "blocked", where
    assert host.act(rows["camera"], "reset", confirm="TOKEN", human=True)["refused"] == "PausedForArm"
    assert host.act(rows["camera"], "reset", dry_run=True, human=False)["refused"] == "PausedForArm"
    assert runs(log, "widgets", "act") == [] and len(runs(log, "widgets", "diagnose")) == 1  # the read before the pause only


def test_the_headline_reads_paused_not_stale_after_four_minutes_and_stale_rules_return_after(tmp_path):
    log, running = tmp_path / "runs.jsonl", tmp_path / "running"
    running.mkdir()
    directory = tmp_path / "plugins.d"
    directory.mkdir()
    (directory / "cams.py").write_text(PERIODIC_PLUGIN)
    (directory / "cams.toml").write_text(f'name = "cams"\ncommand = ["{{python}}", "{{here}}/cams.py"]\nperiod_s = 30\n'
                                         f'matches = ["usb:1234:*"]\nenv = {{ FAKE_LOG = "{log}" }}\npause_while = ["{running}/*.json"]\n')
    clock = SetClock(1000.0)
    host = Host(plugins_dir=directory, clock=clock, os_source=FakeOs(standard_skeleton(WIDGET)))
    host.run_all_once()  # a normal read at t=1000: its checks promise a report every 30 s
    path = host.layer.device_paths["usb:9-1"]
    assert host.node(path)["node"]["level"] == "ok"
    (running / "job1.json").write_text("{}")
    clock.at = 1000.0 + 250  # more than four minutes paused: the 30 s checks are long overdue
    host.run_all_once()
    node = host.node(path)["node"]
    assert node["level"] == "ok" and node["message"].startswith("paused: an arm job is running"), node
    assert next(check for check in host.node(path)["checks"] if check["name"] == "cam: streams")["effective_level"] == "ok"
    (running / "job1.json").unlink()  # the pause ends; the next read is not due yet
    clock.at += 5
    node = host.node(path)["node"]
    assert node["level"] == "stale" and not node["message"].startswith("paused"), node
    assert next(check for check in host.node(path)["checks"] if check["name"] == "cam: streams")["effective_level"] == "stale"
