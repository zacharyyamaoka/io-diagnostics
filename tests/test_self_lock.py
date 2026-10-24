"""The rack bus lock held by THIS host's own running check is not "blocked" (round 3 brief item 10): a precondition
reads "this host's own check is running (a few s)" with the state ``waiting``; only a foreign holder is "blocked by
pid … (cmdline)". The holder here is a real process of a fake check the host runs (``sleep``); the lock itself is
never taken: /proc/locks is replaced by the holder list a test names.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from test_host_support import FakeOs, SetClock, standard_skeleton, usb_device

from io_diagnostics.host import WAITING, Host, LiveOs, bus_lock_state
from io_diagnostics.plugins import load_plugin
from io_diagnostics.runner import RUNNING, run_check
from io_facts.lock import LockHolder

BOARD_PLUGIN = r'''
import json, sys
args = sys.argv[1:]
if "describe" in args:
    print(json.dumps({"name": "boards", "schema": "diagnostics-record/0", "actions": []}))
    raise SystemExit
action = {"name": "check_comms", "title": "Check communication", "description": "ask who answers", "danger": "query",
          "scope": "device", "confirm": "", "kind": "touches",
          "preconditions": [["bus_lock_free", "The rack bus lock is free (no experiment running)"]]}
print(json.dumps({"schema": "diagnostics-record/0", "record": "entity", "path": "", "name": "fdcanusb", "kind": "device",
                  "actions": [action]}))
print(json.dumps({"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": "fdcanusb: ran", "path": ""}))
'''


def sleeper(tmp_path: Path, seconds: float = 3.0):
    """A check whose child (a sleep) stands in for the lock holder: started by the host's runner, in its own session."""

    pid_file = tmp_path / "holder.pid"
    path = tmp_path / "checks" / "sleeper.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    script = f"sleep {seconds} & echo $! > {pid_file}; wait"
    path.write_text(f'name = "sleeper"\nkind = "check"\ncommand = {json.dumps(["/bin/sh", "-c", script])}\n'
                    'period_s = 30\ntimeout_s = 20\nattach = "host"\n')
    spec, _notes = load_plugin(path)
    thread = threading.Thread(target=run_check, args=(spec, time.time), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not (pid_file.exists() and pid_file.read_text().strip()) and time.monotonic() < deadline:
        time.sleep(0.02)
    return int(pid_file.read_text()), thread


def test_a_lock_held_by_this_hosts_own_check_is_waiting_not_blocked(tmp_path):
    holder, thread = sleeper(tmp_path)
    assert RUNNING.owner(holder) is not None  # the sleep is a child of the check: same session
    state, detail = bus_lock_state([LockHolder(holder, "sleep 4", "FLOCK")])
    assert state == WAITING and detail.startswith("this host's own check is running (a few s): check sleeper, ")
    assert "blocked" not in detail and "pid" not in detail
    # anyone else, alone or beside our own, blocks it, named with its command line
    me = LockHolder(os.getpid(), "python3 -m pytest", "FLOCK")
    assert bus_lock_state([me]) == (False, f"blocked by pid {os.getpid()} (python3 -m pytest)")
    assert bus_lock_state([LockHolder(holder, "sleep 4", "FLOCK"), me])[0] is False
    thread.join(10)
    assert RUNNING.owner(holder) is None  # finished: no longer ours


def test_the_live_gate_reads_proc_locks_through_the_same_rule(tmp_path, monkeypatch):
    from io_facts import lock

    holder, thread = sleeper(tmp_path)
    monkeypatch.setattr(lock, "lock_holders", lambda *args, **kwargs: [LockHolder(holder, "sleep 4", "FLOCK")])
    live = LiveOs.__new__(LiveOs)  # WHY not __init__: no kernel log is read; the gate is all this test needs
    live._gate_cache, live._holders = {}, None
    assert live.gate(None, "bus_lock_free")[0] == WAITING
    monkeypatch.setattr(lock, "lock_holders", lambda *args, **kwargs: [])
    assert live._gate({}, "bus_lock_free") == (True, "rack bus lock free (/proc/locks, never taken)")
    thread.join(10)


class LockOs(FakeOs):
    """A fake machine whose rack bus lock is held by ``holders()``, judged by the host's real rule."""

    def __init__(self, skeleton, holders):
        super().__init__(skeleton)
        self.holders = holders

    def gate(self, device, check):
        if check == "bus_lock_free":
            holders = self.holders()
            return (True, "free") if not holders else bus_lock_state(holders)
        return super().gate(device, check)


def test_an_action_waits_quietly_while_the_hosts_own_check_holds_the_lock(tmp_path):
    plugins = tmp_path / "plugins.d"
    plugins.mkdir()
    (plugins / "boards.py").write_text(BOARD_PLUGIN)
    (plugins / "boards.toml").write_text(f'name = "boards"\ncommand = ["{{python}}", "{plugins / "boards.py"}"]\n'
                                         'matches = ["usb:0483:5740"]\n')
    holder, thread = sleeper(tmp_path)
    who = [LockHolder(holder, "sleep 4", "FLOCK")]
    host = Host(plugins_dir=plugins, clock=SetClock(1000.0), os_source=LockOs(standard_skeleton(
        usb_device("9-1", "0483", "5740", "272813EF", label="fdcanusb")), lambda: who))
    host.run_all_once()
    device = host.layer.device_paths["usb:9-1"]
    [action] = [found for found in host.node(device)["actions"] if found["owner"] != "io_diagnostics"]  # not the host's own reset_port
    [precondition] = action["preconditions"]
    assert (precondition["state"], action["state"]) == (WAITING, WAITING)
    assert action["reason"].startswith("this host's own check is running (a few s)") and "blocked" not in action["reason"]
    who[:] = [LockHolder(os.getpid(), "python3 -m rack_session", "FLOCK")]
    [action] = [found for found in host.node(device)["actions"] if found["owner"] != "io_diagnostics"]
    assert (action["preconditions"][0]["state"], action["state"]) == ("fail", "blocked")
    assert action["reason"] == f"blocked by pid {os.getpid()} (python3 -m rack_session)"
    who[:] = []
    assert host.node(device)["actions"][0]["state"] == "ready"
    thread.join(10)
