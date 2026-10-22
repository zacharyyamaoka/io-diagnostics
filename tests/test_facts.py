"""The shared passive layer: today's real kernel log, a real flock, and the active-check gate."""

from __future__ import annotations

import fcntl
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from io_facts import gate, journal, lock  # noqa: E402

TODAY_LOG = ROOT / "tests" / "data" / "kernel-usb-2026-10-21.log"


def notices_for(identity: str) -> list[journal.Notice]:
    events = journal.parse_kernel_log(TODAY_LOG.read_text().splitlines())
    return [notice for notice in journal.replay(events) if notice.identity == identity]


def test_flapping_adapter_is_one_plugged_notice_then_one_unplugged():
    notices = notices_for("28B3A25D")
    assert [notice.kind for notice in notices] == ["plugged", "unplugged"]
    plugged = notices[0]
    assert plugged.port == "1-5" and plugged.flaps == ["1-7"] * 4
    assert plugged.time.strftime("%H:%M:%S") == "18:11:42"
    assert plugged.text() == "fdcanusb 28B3A25D plugged on 1-5 after 4 flaps on 1-7"


def test_four_second_camera_drop_is_one_dropped_notice():
    drops = [notice for notice in notices_for("130523071221") if notice.kind == "dropped"]
    assert len(drops) == 1 and round(drops[0].gap_s) == 4 and drops[0].port == "6-2"


def _hold(path: str, ready, release) -> None:
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        ready.set()
        release.wait(10)


def test_lock_holder_is_read_from_proc_locks_without_taking_the_lock(tmp_path):
    lock_file = tmp_path / "bus.lock"
    lock_file.touch()
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    holder = multiprocessing.Process(target=_hold, args=(str(lock_file), ready, release))
    holder.start()
    try:
        assert ready.wait(5)
        holders = lock.lock_holders(lock_file)
        assert [entry.pid for entry in holders] == [holder.pid] and holders[0].kind == "FLOCK"
    finally:
        release.set()
        holder.join(5)
    time.sleep(0.05)
    assert lock.lock_holders(lock_file) == []
    with open(lock_file, "w") as handle:  # reading never left a lock behind
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_gate_blocks_on_a_port_holder_or_the_rack_lock_and_never_runs_in_this_round():
    busy = gate.decide(["/dev/ttyACM0"], holders_fn=lambda node: ["640623 tview"], lock_fn=lambda: [])
    assert busy.state == "blocked" and busy.reason == "open by 640623 tview"

    locked = gate.decide(["/dev/ttyACM0"], holders_fn=lambda node: [], lock_fn=lambda: [lock.LockHolder(4187, "python3 -m bam_rack_twin.session", "FLOCK")])
    assert locked.state == "blocked" and "rack bus lock held by pid 4187" in locked.reason

    free = gate.run_gated(["/dev/ttyACM0"], ["false"], allow_active=True, holders_fn=lambda node: [], lock_fn=lambda: [])
    assert free["state"] == "not run"  # IO_ALLOW_ACTIVE is not set anywhere in this repo
