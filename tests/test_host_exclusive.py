"""``exclusive`` groups: the host never runs two jobs of one group at once (queued, never failed), in ``once`` and in
the periodic scheduler; jobs of different groups, or of no group, still run in parallel.

The bug this pins (live, 2026-10-22 17:41): ``once --enable moteus_servo --enable intel_rgbd`` started every job
together; both drivers take the rack bus lock with flock LOCK_NB, so the losers reported ``BusLockHeld`` against the
host's own sibling job (moteus vs RealSense, and camera 6-1 vs 6-2). Timestamps come from the fake plugin processes
themselves (wall clock), not from the host.
"""

from __future__ import annotations

import time

import pytest
from test_host_support import FakeOs, SetClock, read_log, standard_skeleton, usb_device, write_fake_plugin

from io_diagnostics.host import Host
from io_diagnostics.plugins import PluginConfigError, load_plugin
from io_diagnostics.runner import Gates

CAMERA_1 = usb_device("9-1", "1234", "0001", "CAM1", label="camera 1")
CAMERA_2 = usb_device("9-2", "1234", "0002", "CAM2", label="camera 2")
ADAPTER = usb_device("9-3", "5678", "0001", "ADAPT", label="adapter")
SKELETON = standard_skeleton(CAMERA_1, CAMERA_2, ADAPTER)


def diagnoses(log) -> list[dict]:
    return sorted((run for run in read_log(log) if run["verb"] == "diagnose"), key=lambda run: run["start"])


def never_overlap(runs: list[dict]) -> bool:
    return all(later["start"] >= earlier["end"] for earlier, later in zip(runs, runs[1:]))


def all_overlap(runs: list[dict]) -> bool:
    return max(run["start"] for run in runs) < min(run["end"] for run in runs)


def test_one_group_never_overlaps_across_plugins_and_across_one_plugins_devices(tmp_path):
    log = tmp_path / "runs.jsonl"
    write_fake_plugin(tmp_path / "plugins.d", "cameras", ["usb:1234:*"], log, exclusive="rack-bus")  # two devices
    write_fake_plugin(tmp_path / "plugins.d", "servos", ["usb:5678:*"], log, exclusive="rack-bus")
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(SKELETON))
    host.run_all_once()
    runs = diagnoses(log)
    assert sorted((run["plugin"], run["device"]) for run in runs) == [("cameras", "usb:9-1"), ("cameras", "usb:9-2"),
                                                                      ("servos", "usb:9-3")]
    assert never_overlap(runs), runs
    outcomes = [job.last.outcome for job in host.jobs.values()]
    assert outcomes == ["ok", "ok", "ok"], "a job queued behind its group must run, not fail"
    assert {plugin["name"]: plugin["exclusive"] for plugin in host.plugins()["plugins"]} == {"cameras": "rack-bus",
                                                                                           "servos": "rack-bus"}


def test_different_groups_and_no_group_still_run_in_parallel(tmp_path):
    log = tmp_path / "runs.jsonl"
    write_fake_plugin(tmp_path / "plugins.d", "first", ["usb:1234:0001"], log, exclusive="bus-a", sleep_s=0.8)
    write_fake_plugin(tmp_path / "plugins.d", "second", ["usb:1234:0002"], log, exclusive="bus-b", sleep_s=0.8)
    write_fake_plugin(tmp_path / "plugins.d", "third", ["usb:5678:*"], log, sleep_s=0.8)  # no group
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(SKELETON))
    host.run_all_once()
    runs = diagnoses(log)
    assert len(runs) == 3 and all_overlap(runs), runs


def test_the_scheduler_queues_a_busy_group_and_keeps_other_groups_parallel(tmp_path):
    log = tmp_path / "runs.jsonl"
    for name, matches in (("cameras", ["usb:1234:*"]), ("servos", ["usb:5678:*"])):
        write_fake_plugin(tmp_path / "plugins.d", name, matches, log, exclusive="rack-bus", sleep_s=0.3, period_s=1)
    write_fake_plugin(tmp_path / "plugins.d", "elsewhere", ["usb:1234:0001"], log, exclusive="other-bus", sleep_s=0.3,
                      period_s=1)
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(SKELETON))
    host.start(udev=False)
    try:
        time.sleep(3.0)
    finally:
        host.stop()
    time.sleep(0.6)  # let the runs in flight finish and log before reading
    runs = diagnoses(log)
    group = [run for run in runs if run["plugin"] in ("cameras", "servos")]
    assert never_overlap(group), group
    assert {(run["plugin"], run["device"]) for run in group} == {("cameras", "usb:9-1"), ("cameras", "usb:9-2"),
                                                                ("servos", "usb:9-3")}
    elsewhere = [run for run in runs if run["plugin"] == "elsewhere"]
    assert elsewhere and any(other["start"] < run["end"] and run["start"] < other["end"] for run in elsewhere for other in group), \
        "a job of another group must run beside the busy one"
    failed = [job.last.message for job in host.jobs.values() if job.last and job.last.outcome != "ok"]
    assert failed == []


def test_gates_take_turns_per_group_and_never_wait_without_one():
    gates = Gates()
    assert gates.try_take("rack-bus") and not gates.try_take("rack-bus") and gates.busy() == ["rack-bus"]
    assert gates.try_take("other") and gates.try_take("") and gates.try_take("")
    gates.release("rack-bus")
    gates.release("other")
    assert gates.busy() == [] and gates.try_take("rack-bus")


def test_exclusive_must_be_a_group_name(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('name = "bad"\ncommand = ["true"]\nexclusive = ["rack-bus"]\n')
    with pytest.raises(PluginConfigError, match="exclusive must be a group name"):
        load_plugin(path)
    path.write_text('name = "good"\ncommand = ["true"]\nexclusive = "rack-bus"\n')
    spec, notes = load_plugin(path)
    assert spec.exclusive == "rack-bus" and notes == []
