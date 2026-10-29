"""Probes share the rack bus lock, an experiment holds it exclusively: only a WRITE holder blocks the host's
``bus_lock_free`` gate (Zach, 2026-10-29: "they are likely completely different ... should not be blocked").
A camera's query had made a CAN adapter's probe show "paused" until the next one.

Real ``flock`` calls on temp files (read back through /proc/locks) and fake /proc/locks text. No hardware, no plugin.
"""

from __future__ import annotations

import fcntl
import os
import tomllib
from pathlib import Path

from io_diagnostics.host import LiveOs, bus_lock_state
from io_facts import gate, lock
from io_facts.lock import LockHolder

ROOT = Path(__file__).resolve().parent.parent


def shared(pid=111):
    return LockHolder(pid, "probe", "FLOCK", "READ")


def exclusive(pid=222):
    return LockHolder(pid, "experiment", "FLOCK", "WRITE")


def held(path, mode):
    handle = open(path, "a")
    fcntl.flock(handle, mode | fcntl.LOCK_NB)
    return handle


# ----- io_facts.lock: the holder's mode ------------------------------------------------------------------------


def test_lock_holders_reports_read_for_a_shared_holder_and_write_for_an_exclusive_one(tmp_path):
    lock_file = tmp_path / "bus.lock"
    lock_file.touch()
    probe = held(lock_file, fcntl.LOCK_SH)
    try:
        (found,) = lock.lock_holders(lock_file)
        assert (found.pid, found.mode, found.exclusive) == (os.getpid(), "READ", False)
    finally:
        probe.close()
    experiment = held(lock_file, fcntl.LOCK_EX)
    try:
        (found,) = lock.lock_holders(lock_file)
        assert (found.pid, found.mode, found.exclusive) == (os.getpid(), "WRITE", True)
    finally:
        experiment.close()


def test_lock_holders_parses_the_mode_column_of_proc_locks(tmp_path):
    lock_file = tmp_path / "bus.lock"
    lock_file.touch()
    info = lock_file.stat()
    wanted = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
    proc_locks = tmp_path / "locks"
    proc_locks.write_text(
        f"1: FLOCK  ADVISORY  READ {os.getpid()} {wanted} 0 EOF\n"
        f"2: FLOCK  ADVISORY  READ {os.getppid()} {wanted} 0 EOF\n"
        f"3: FLOCK  ADVISORY  WRITE 1 00:00:7 0 EOF\n"  # another file's lock
    )
    holders = lock.lock_holders(lock_file, proc_locks)
    assert [(holder.pid, holder.mode) for holder in holders] == [(os.getpid(), "READ"), (os.getppid(), "READ")]


# ----- io_facts.gate.decide ------------------------------------------------------------------------------------


def test_the_gate_is_free_with_only_shared_holders_and_blocked_by_a_write_holder():
    nobody = dict(holders_fn=lambda node: [])
    free = gate.decide(["/dev/ttyACM0"], lock_fn=lambda: [shared(111), shared(333)], **nobody)
    assert free.state == "free" and len(free.lock_holders) == 2  # still reported, never blocking
    blocked = gate.decide(["/dev/ttyACM0"], lock_fn=lambda: [shared(111), exclusive(222)], **nobody)
    assert blocked.state == "blocked" and "pid 222 experiment" in blocked.reason and "111" not in blocked.reason


def test_the_gate_reads_a_real_shared_flock_as_free_and_a_real_exclusive_one_as_blocked(tmp_path):
    lock_file = tmp_path / "bus.lock"
    lock_file.touch()
    read_back = lambda: lock.lock_holders(lock_file)  # noqa: E731
    probe = held(lock_file, fcntl.LOCK_SH)
    try:
        assert gate.decide(["/dev/ttyACM0"], holders_fn=lambda node: [], lock_fn=read_back).state == "free"
    finally:
        probe.close()
    experiment = held(lock_file, fcntl.LOCK_EX)
    try:
        assert gate.decide(["/dev/ttyACM0"], holders_fn=lambda node: [], lock_fn=read_back).state == "blocked"
    finally:
        experiment.close()


def test_the_gated_active_check_takes_the_lock_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("IO_ALLOW_ACTIVE", "1")
    lock_file = tmp_path / "bus.lock"
    options = dict(allow_active=True, holders_fn=lambda node: [], lock_fn=lambda: [], lock_path=lock_file)
    probe = held(lock_file, fcntl.LOCK_SH)
    try:
        assert gate.run_gated(["/dev/ttyACM0"], ["true"], **options)["state"] == "ran"
    finally:
        probe.close()
    experiment = held(lock_file, fcntl.LOCK_EX)
    try:
        assert gate.run_gated(["/dev/ttyACM0"], ["true"], **options)["state"] == "blocked"
    finally:
        experiment.close()


# ----- the host's bus_lock_free precondition -------------------------------------------------------------------


def test_bus_lock_state_ignores_shared_holders_and_names_only_the_exclusive_one():
    assert bus_lock_state([shared()])[0] is True
    assert bus_lock_state([shared(111), shared(333)])[0] is True
    state, detail = bus_lock_state([shared(111), exclusive(os.getpid())])
    assert state is False and f"pid {os.getpid()} (experiment)" in detail and "probe" not in detail


def test_the_live_precondition_is_pass_with_a_shared_holder(monkeypatch):
    monkeypatch.setattr(lock, "lock_holders", lambda *args, **kwargs: [shared()])
    live = LiveOs.__new__(LiveOs)  # WHY not __init__: no kernel log is read; the gate is all this test needs
    live._gate_cache, live._holders = {}, None
    assert live._gate({}, "bus_lock_free")[0] is True
    monkeypatch.setattr(lock, "lock_holders", lambda *args, **kwargs: [shared(), exclusive(os.getpid())])
    assert live._gate({}, "bus_lock_free")[0] is False


# ----- the plugin declarations ---------------------------------------------------------------------------------


def test_the_camera_no_longer_queues_behind_moteus_but_moteus_keeps_its_own_group():
    camera = tomllib.loads((ROOT / "plugins.d" / "intel_rgbd.toml").read_text())
    moteus = tomllib.loads((ROOT / "plugins.d" / "moteus_servo.toml").read_text())
    assert "exclusive" not in camera and moteus["exclusive"] == "rack-bus"
