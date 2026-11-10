"""The status dots' third word, "pulsing" (Zach, 2026-11-10: "pulsing: the entity is actively doing something: a utility or action is
running on it, it's being commanded or flashed, or it's being brought up or probed right after plug-in ... a merely-connected idle
entity must NOT pulse. Routine background state polling does not count"). The host says it per row, ``activity`` in /state and /node:
``None``, or ``{"kind", "text"}`` while one of these is true of the row (docs/api-v1.md "activity"):

- ``run``: a real run (docs/runs-v1.md) is active on the row or on a board below the same adapter's bus (queued, waiting or going);
- ``console``: a console RobotDx launched (tview, realsense-viewer) is still open on its adapter;
- ``held``: a person's tool holds the port or the rack bus lock (a flash, an experiment): the probe was blocked by a live holder;
- ``probe``: the adapter's first look of this connection is in flight (the on-connect probe, docs/probe-on-connect.md).

Everything else the host does on its own (the periodic diagnose, a follower's lines, a presence pass) is none of them. Fakes and real
subprocesses of the fixture plugins only; nothing touches hardware.
"""

from __future__ import annotations

import os
import time

from test_probe import Rig as ProbeRig
from test_probe import wait_for
from test_runs import Rig as RunRig
from test_runs import Watcher


def rows(host) -> dict[str, dict]:
    """/state's tree as path -> row."""

    found, stack = {}, [host.state()["tree"]]
    while stack:
        row = stack.pop()
        found[row["path"]] = row
        stack.extend(row["children"])
    return found


def active(host) -> dict[str, dict]:
    return {path: row["activity"] for path, row in rows(host).items() if row.get("activity")}


# ----- the rows are quiet until something happens ---------------------------------------------------------------------------
def test_no_row_pulses_while_nothing_runs_on_it(tmp_path):
    rig = RunRig(tmp_path)
    try:
        assert all("activity" in row for row in rows(rig.host).values()), "every row says it, None or a cause"
        assert active(rig.host) == {}
        rig.host.run_all_once()  # the routine read: polling is not activity
        assert active(rig.host) == {}
    finally:
        rig.close()


# ----- a run -------------------------------------------------------------------------------------------------------------------
def test_a_run_pulses_its_board_its_bus_and_the_adapter_and_nothing_above(tmp_path):
    rig = RunRig(tmp_path)
    try:
        run = rig.start()
        found = wait_for(lambda: active(rig.host) or None, what="the run's rows")
        assert set(found) == {rig.device, f"{rig.device}/can", rig.can1}, found  # the adapter, its CAN bus, the board
        assert all(cause["kind"] == "run" for cause in found.values())
        assert "Run calibration" in found[rig.can1]["text"], found[rig.can1]
        # the host, the USB bus and the port are above the adapter: they never pulse for what a board is doing
        assert not [path for path in found if not path.startswith(rig.device)], found
        Watcher(rig, run["id"]).wait_for("Continue? [y/N]")  # the stand-in waits at its prompt until told to go on
        rig.write(run["id"], "input", {"text": "y\n"})
        rig.wait_ended(run["id"])
        wait_for(lambda: not active(rig.host), what="the pulse to end with the run")
    finally:
        rig.close()


def test_a_queued_run_pulses_as_well(tmp_path):
    """``queued`` is waiting for the plugin's exclusive group: the person pressed it, and it is about to run."""

    rig = RunRig(tmp_path)
    try:
        status, dry = rig.call("POST", "/api/v1/act", {"path": rig.can1, "action": "calibrate", "node": 1, "dry_run": True})
        with rig.host.gates.hold("bus"):  # a diagnose, say, holds the rack bus
            status, answer = rig.call("POST", "/api/v1/act", {"path": rig.can1, "action": "calibrate", "node": 1,
                                                              "token": dry["token"], "detach": True})
            run = answer["run"]
            assert rig.run(run["id"])["state"] == "queued"
            found = wait_for(lambda: active(rig.host) or None, what="the queued run's rows")
            assert set(found) == {rig.device, f"{rig.device}/can", rig.can1}, found  # the adapter, its CAN bus, the board
            rig.write(run["id"], "signal", {"signal": "interrupt"})
        wait_for(lambda: not active(rig.host), what="quiet once it is cancelled")
    finally:
        rig.close()


# ----- a console ---------------------------------------------------------------------------------------------------------------
def test_an_open_console_pulses_its_adapter_until_the_process_closes(tmp_path):
    rig = RunRig(tmp_path)
    try:
        device_id = "usb:9-1"
        rig.host.consoles[device_id] = {"pid": os.getpid(), "program": "tview", "device_path": rig.device, "since": 1.0}
        found = active(rig.host)
        assert list(found) == [rig.device] and found[rig.device]["kind"] == "console" and "tview" in found[rig.device]["text"], found
        rig.host.consoles[device_id]["pid"] = 2 ** 22 + 12345  # no such process
        assert active(rig.host) == {}
    finally:
        rig.close()


# ----- held by a person's tool (a flash, an experiment) ----------------------------------------------------------------------
def test_a_port_held_by_a_tool_pulses_the_adapter_and_stops_when_it_lets_go(tmp_path):
    rig = ProbeRig(tmp_path / "held")
    try:
        holder = rig.hold("update_moteus_firmware")
        rig.state.update(port_holders=[f"{holder.pid} python3"], say_blocked=True)
        rig.write()
        rig.start()
        device = rig.host.layer.device_paths["usb:9-3"]
        found = wait_for(lambda: {path: cause for path, cause in active(rig.host).items() if cause["kind"] == "held"} or None, what="the held adapter")
        assert list(found) == [device] and found[device]["kind"] == "held", found
        assert "update_moteus_firmware" in found[device]["text"], found[device]
        rig.state.pop("port_holders")
        rig.write()
        holder.kill()
        holder.wait()
        wait_for(lambda: not active(rig.host), timeout=8, what="the pulse to end when the tool exits")
    finally:
        rig.stop()


# ----- being brought up --------------------------------------------------------------------------------------------------------
def test_the_on_connect_probe_pulses_the_adapter_while_it_runs_and_not_after(tmp_path):
    rig = ProbeRig(tmp_path / "probe", state={"sleep": 1.5}).start()
    try:
        device = rig.host.layer.device_paths["usb:9-3"]
        found = wait_for(lambda: active(rig.host) or None, timeout=6, what="the probe's pulse")
        assert list(found) == [device] and found[device]["kind"] == "probe", found
        wait_for(lambda: rig.job().probed, timeout=10, what="the probe to end")
        wait_for(lambda: not active(rig.host), what="quiet after the probe")
    finally:
        rig.stop()


def test_a_plugin_without_a_probe_is_brought_up_by_its_first_read_only(tmp_path):
    rig = ProbeRig(tmp_path / "first", probe_declared=False, state={"sleep": 1.0}).start()
    try:
        device = rig.host.layer.device_paths["usb:9-3"]
        found = wait_for(lambda: active(rig.host) or None, timeout=6, what="the first read's pulse")
        assert found[device]["kind"] == "probe", found
        wait_for(lambda: rig.job().last is not None and not rig.job().running, timeout=10, what="the first read to end")
        wait_for(lambda: not active(rig.host), what="quiet after the first read")
        rig.due_now()  # the routine read again: the same plugin, the same sleep, and no pulse
        wait_for(lambda: rig.job().running or None, timeout=8, what="the routine read to start")
        time.sleep(0.3)
        assert active(rig.host) == {}, "routine polling is not activity"
        wait_for(lambda: not rig.job().running, timeout=8, what="the routine read to end")
    finally:
        rig.stop()


def test_a_replug_reprobes_and_pulses_again(tmp_path):
    rig = ProbeRig(tmp_path / "replug", state={"sleep": 0.8}).start()
    try:
        wait_for(lambda: rig.job().probed, timeout=10, what="the first probe")
        wait_for(lambda: not active(rig.host), what="quiet")
        rig.job().probed = False  # what a re-enumeration does (host.py: probed, blocked, next_due reset)
        rig.job().next_due = 0.0
        found = wait_for(lambda: active(rig.host) or None, timeout=6, what="the second probe's pulse")
        assert next(iter(found.values()))["kind"] == "probe", found
    finally:
        rig.stop()
