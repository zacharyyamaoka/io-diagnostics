"""The on-connect probe (docs/probe-on-connect.md; Zach, 2026-10-23: "each device tells diagnostics what to do ... than the
feedback can just go back via the standard diagnostics or logs").

- a plugin that declares ``probe`` in describe gets ``probe --device <id>`` once per connection, in place of the first
  diagnose; its own Found / Probed / ProbeBlocked events reach the Activity log as they are, and the host derives none;
- the periodic diagnose (or follow) starts ``period_s`` after the probe; the probe never runs on the period;
- a probe that ended blocked (PortBusy / BusLockHeld) runs again ~1 s after the last holder pid exits, and on a 10/20/40 s
  backoff capped at ``period_s`` when no holder can be watched, inside the ``exclusive`` queue;
- a plugin without ``probe`` keeps diagnose-on-connect.

The plugin is tests/fixtures/plugins/rack_plugin.py (the shape of moteus_servo's records and events, the rig read from a JSON
file on every run); the holders are real processes the test starts and kills. Nothing here touches hardware.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_host_support import FakeOs, read_log, standard_skeleton, usb_device

from contract.record import DiagnosticStatus, Event
from io_diagnostics import probe
from io_diagnostics.host import Clock, Host

RACK = Path(__file__).resolve().parent / "fixtures" / "plugins" / "rack_plugin.py"
ADAPTER = usb_device("9-3", "5678", "0001", "0607623A", label="fdcanusb 0607623A")
ADAPTER_2 = usb_device("9-4", "5678", "0001", "0607623B", label="fdcanusb 0607623B")
NAME = "fdcanusb 0607623A"
BOARDS = {"11": {"answers": False}, "12": {"answers": True, "model": "moteus-c1", "sw_version": "1.1.2"},
          "13": {"answers": False}, "21": {"answers": False}}
HOLDER_SCRIPT = "import time\ntime.sleep(120)\n"
FOUND_12 = "CAN 12 answers on fdcanusb 0607623A (moteus-c1 \u00b7 fw 1.1.2)"
PROBED = "fdcanusb 0607623A: 1 of 4 expected boards answer (CAN 12) \u00b7 CAN 11, CAN 13, CAN 21 silent"


# ----- the rig -----------------------------------------------------------------------------------------------------
class Rig:
    """One host over a fake skeleton and the rack_plugin; the test rewrites ``state`` between runs."""

    def __init__(self, tmp_path: Path, *, devices=(ADAPTER,), period_s: float = 30, exclusive: str = "", boards=None,
                 state: dict | None = None, probe_declared: bool = True, follow: bool = False, **host_options):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.log = tmp_path / "runs.jsonl"
        self.state_path = tmp_path / "state.json"
        self.state = {"name": NAME, "boards": json.loads(json.dumps(BOARDS if boards is None else boards)), **(state or {})}
        self.write()
        directory = tmp_path / "plugins.d"
        directory.mkdir()
        (directory / "rack.toml").write_text(
            f'name = "rack"\ncommand = ["{{python}}", "{RACK}"]\ntimeout_s = 20\nperiod_s = {period_s}\nmatches = ["usb:5678:0001"]\n'
            + (f'exclusive = "{exclusive}"\n' if exclusive else "")
            + f'env = {{ RACK_STATE = "{self.state_path}", RACK_LOG = "{self.log}", RACK_PROBE = "{int(probe_declared)}", '
              f'RACK_FOLLOW = "{int(follow)}" }}\n')
        self.os = FakeOs(standard_skeleton(*devices))
        self.host = Host(plugins_dir=directory, clock=Clock(), os_source=self.os, **host_options)
        self.holders: list[subprocess.Popen] = []

    def write(self) -> None:
        self.state_path.write_text(json.dumps(self.state))

    def start(self) -> "Rig":
        self.host.start(udev=False)
        return self

    def stop(self) -> None:
        self.host.stop()
        for holder in self.holders:
            holder.kill()
            holder.wait()

    def hold(self, command: str = "tview") -> subprocess.Popen:
        """A real process named like a program (``python <tmp>/tview``), standing in for whoever holds the port."""

        script = self.tmp / command
        script.write_text(HOLDER_SCRIPT)
        holder = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.DEVNULL)
        self.holders.append(holder)
        deadline = time.monotonic() + 5
        while probe.start_time(holder.pid) is None and time.monotonic() < deadline:
            time.sleep(0.01)
        return holder

    def runs(self, verb: str | None = None, device: str | None = None) -> list[dict]:
        return sorted((run for run in read_log(self.log) if verb in (None, run["verb"]) and device in (None, run["device"])),
                      key=lambda run: run["start"])

    def verbs(self) -> list[str]:
        return [run["verb"] for run in self.runs()]

    def job(self, device: str = "usb:9-3"):
        return self.host.jobs[("rack", device)]

    def events(self, *codes: str) -> list[dict]:
        """The Activity log's plugin events, oldest first."""

        found = self.host.activity()["events"]
        return [event for event in reversed(found) if event["type"] == "plugin" and (not codes or event["code"] in codes)]

    def run_check(self, device: str = "usb:9-3") -> DiagnosticStatus:
        return self.job(device).cache.host_status

    def due_now(self, device: str = "usb:9-3") -> None:
        self.job(device).next_due = 0.0


def wait_for(condition, timeout: float = 15.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")


@pytest.fixture
def rig(tmp_path):
    made: list[Rig] = []

    def make(**options) -> Rig:
        made.append(Rig(tmp_path / f"rig{len(made)}", **options))
        return made[-1].start()

    yield make
    for each in made:
        each.stop()


# ----- plug: the driver says what it found --------------------------------------------------------------------------------
def test_plug_probes_once_and_the_drivers_own_events_reach_the_log_as_they_are(rig):
    plugged = time.time()
    rig_ = rig()
    wait_for(lambda: rig_.events("Probed"), what="the Probed event")
    took = time.time() - plugged
    print(f"\nMEASURED host start (plug) -> Found/Probed in the Activity log: {took:.2f} s")
    assert rig_.verbs() == ["probe"], "the connection's first run is its probe, in place of a diagnose"
    assert [(e["code"], e["message"]) for e in rig_.events()] == [("Found", FOUND_12), ("Probed", PROBED)]
    found = rig_.events("Found")[0]
    assert found["source"] == "rack" and found["path"].endswith("/can/12") and found["name"] == "CAN 12"
    assert rig_.events("Probed")[0]["severity"] == "warn"
    check = rig_.run_check()
    assert check.level == "ok" and re.fullmatch(r"probed \d\d:\d\d:\d\d \u00b7 \d\.\d s", check.message), check.message
    assert rig_.job().probed and rig_.job().blocked is None
    assert took < 3.0


def test_the_periodic_diagnose_starts_a_period_after_the_probe_and_the_host_adds_no_event_of_its_own(rig):
    rig_ = rig(period_s=1.5)
    wait_for(lambda: rig_.events("Probed"))
    assert rig_.verbs() == ["probe"]
    events = rig_.events()
    rig_.state["boards"]["12"]["answers"] = False  # CAN 12 goes silent: the driver's status says so, not a host event
    rig_.write()
    wait_for(lambda: len(rig_.runs("diagnose")) >= 2, what="two periodic diagnoses")
    assert rig_.runs("diagnose")[0]["start"] - rig_.runs("probe")[0]["end"] >= 1.2, "the period starts after the probe"
    assert rig_.verbs()[0] == "probe" and rig_.verbs().count("probe") == 1, "the probe never runs on the period"
    assert rig_.events() == events, "no Lost, no Found, no summary from the host"
    # the driver's status says so on the bus; the board that answered once keeps its row, hollow (docs/api-v1.md)
    wait_for(lambda: rig_.job().cache.statuses[("can", "fdcanusb 0607623A: CAN bus")].code == "ExpectedBoardsSilent")
    assert "can/12" in rig_.job().cache.entities


def test_a_plugin_without_probe_keeps_diagnose_on_connect(rig):
    rig_ = rig(probe_declared=False, state={"port_holders": ["999999 python3"]})
    wait_for(lambda: rig_.job().last is not None)
    assert rig_.verbs() == ["diagnose"] and rig_.job().last.verb == "diagnose"
    assert re.match(r"ran in ", rig_.run_check().message)
    assert rig_.job().blocked is None, "no probe, no probe-again"


def test_a_new_enumeration_probes_again(rig):
    plugged = {**ADAPTER, "attrs": {**ADAPTER["attrs"], "devnum": 6}}
    rig_ = rig(devices=(plugged,))
    wait_for(lambda: rig_.events("Probed"))
    assert rig_.verbs() == ["probe"]
    rig_.host.refresh()
    assert rig_.verbs() == ["probe"] and rig_.job().probed, "the same enumeration is not probed again"
    plugged["attrs"]["devnum"] = 7  # unplugged and back between two refreshes: a new kernel device number
    rig_.host.refresh()
    wait_for(lambda: rig_.verbs() == ["probe", "probe"], what="the second probe")
    assert rig_.job().connection == 7


def test_a_probe_before_a_follower_then_follow_starts_after_it(rig):
    rig_ = rig(follow=True, period_s=30)
    wait_for(lambda: "follow" in rig_.verbs(), what="the follower")
    assert rig_.verbs()[0] == "probe" and "diagnose" not in rig_.verbs()
    assert rig_.runs("follow")[0]["start"] > rig_.runs("probe")[0]["end"]
    assert rig_.verbs().count("probe") == 1


# ----- blocked: who, and probing again the moment they let go --------------------------------------------------------------
def test_plugged_while_tview_holds_it_then_probed_again_within_two_seconds_of_it_closing(rig):
    """Zach's 15:12 story: plug, blocked by tview, tview closes, and the boards are in the activity log within seconds."""

    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdin=subprocess.DEVNULL)
    try:
        rig_ = rig(state={"port_holders": [f"{holder.pid} python3"], "say_blocked": True})
        rig_.holders.append(holder)
        wait_for(lambda: rig_.job().blocked is not None, what="the first probe, blocked")
        check = rig_.run_check()
        assert check.level == "unknown" and check.code == "PortBusy", "nothing was checked: unknown, with the holder's code"
        assert check.message == f"python3 has the port (pid {holder.pid}) \u00b7 RobotDx probes again when it closes", \
            "the driver's own ProbeBlocked sentence is the row's message"
        assert [event["code"] for event in rig_.events()] == ["ProbeBlocked"]
        assert not rig_.job().probed
        runs = len(rig_.runs())
        time.sleep(2.2)  # watcher polls pass; the holder lives; nothing runs, least of all on the 30 s period
        assert len(rig_.runs()) == runs
        rig_.state.pop("port_holders")
        rig_.write()
        holder.kill()
        holder.wait()
        exited = time.time()
        wait_for(lambda: len(rig_.runs()) > runs, timeout=6, what="the probe again")
        again = rig_.runs()[-1]["start"] - exited
        wait_for(lambda: rig_.events("Probed"), timeout=6, what="the Probed event")
        listed = time.time() - exited
        print(f"\nMEASURED holder exit -> probe started again: {again:.2f} s; -> Found/Probed in the Activity log: {listed:.2f} s")
        assert 0 <= again <= 2.5 and listed < 3.5
        assert rig_.verbs() == ["probe", "probe"], "both runs are probes; no diagnose ran in between"
        assert [event["code"] for event in rig_.events()] == ["ProbeBlocked", "Found", "Probed"]
        assert rig_.job().probed and rig_.job().blocked is None
        assert re.match(r"probed \d\d:\d\d:\d\d", rig_.run_check().message) and rig_.run_check().level == "ok"
    finally:
        holder.kill()
        holder.wait()


def test_the_host_words_a_blocked_probe_when_the_driver_sent_no_sentence(tmp_path):
    rig_ = Rig(tmp_path, state={"port_holders": ["4242 python3"]})
    try:
        rig_.start()
        wait_for(lambda: rig_.job().blocked is not None)
        assert rig_.run_check().message == "python3 has the port (pid 4242) \u00b7 RobotDx probes again when it closes"
        assert dict(rig_.run_check().values)["blocked_by"] == "python3 (pid 4242)"
    finally:
        rig_.stop()


def test_a_lock_holder_that_cannot_be_watched_is_probed_again_on_a_growing_backoff(rig):
    rig_ = rig(period_s=30, recheck_backoff_s=0.4, state={"lock_holders": ["another process"]})
    wait_for(lambda: len(rig_.runs("probe")) >= 4, timeout=10, what="four blocked probes")
    check = rig_.run_check()
    assert check.code == "BusLockHeld" and check.level == "unknown" and rig_.job().blocked.holders == ()
    assert re.search(r"RobotDx probes again in \d+ s$", check.message), check.message
    starts = [run["start"] for run in rig_.runs("probe")]
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
    print(f"\nMEASURED backoff gaps (base 0.4 s): {[round(gap, 2) for gap in gaps]}")
    assert gaps[0] >= 0.35 and gaps[1] > gaps[0] * 1.6 and gaps[2] > gaps[1] * 1.6, gaps
    assert rig_.verbs() == ["probe"] * len(starts), "a blocked probe never turns into the periodic diagnose"
    rig_.state.pop("lock_holders")
    rig_.write()
    wait_for(lambda: rig_.job().probed, timeout=10, what="the retry after the lock freed")
    assert rig_.run_check().level == "ok" and rig_.events("Probed")


def test_the_backoff_never_goes_beyond_the_plugins_period():
    assert [probe.backoff_s(attempt, 300) for attempt in (1, 2, 3, 4, 5, 6, 7, 8)] == [10, 20, 40, 80, 160, 300, 300, 300]
    assert probe.backoff_s(1, 5) == 5, "a plugin that runs every 5 s is not asked less often because it is blocked"


def test_probes_take_their_turn_in_the_exclusive_group(rig):
    rig_ = rig(devices=(ADAPTER, ADAPTER_2), exclusive="rack-bus", boards={"1": {"answers": True}})
    wait_for(lambda: len(rig_.runs()) >= 2 and all(job.probed for job in rig_.host.jobs.values()))
    holder = rig_.hold("tview")
    rig_.state["port_holders"] = [f"{holder.pid} python3"]
    rig_.write()
    for job in rig_.host.jobs.values():  # a new connection of both (re-enumerated): probe, blocked
        job.probed, job.next_due = False, 0.0
    wait_for(lambda: all(job.blocked for job in rig_.host.jobs.values()), what="both probes blocked")
    runs = len(rig_.runs())
    rig_.state.pop("port_holders")
    rig_.state["sleep"] = 0.4
    rig_.write()
    holder.kill()
    holder.wait()
    wait_for(lambda: len(rig_.runs()) >= runs + 2 and all(job.probed for job in rig_.host.jobs.values()), timeout=10)
    late = rig_.runs()[runs:]
    assert len(late) == 2 and late[1]["start"] >= late[0]["end"], "both were due at once: the group queues them, never overlaps"


# ----- the pieces --------------------------------------------------------------------------------------------------------
def test_probe_is_declared_in_describe():
    assert probe.probe_timeout(None, 20) is None and probe.probe_timeout({"name": "x"}, 20) is None
    assert probe.probe_timeout({"probe": True}, 20) == 20.0, "true = the plugin's own timeout_s"
    assert probe.probe_timeout({"probe": {"timeout_s": 30}}, 20) == 30.0
    assert probe.probe_timeout({"probe": {}}, 20) == 20.0 and probe.probe_timeout({"probe": False}, 20) is None


def test_holder_strings_and_sentences():
    assert probe.parse_holders("78183 python3, 90 tview") == [(78183, "python3"), (90, "tview")]
    assert probe.parse_holders("another process") == [] and probe.parse_holders("") == []
    status = DiagnosticStatus(level="unknown", name="x", code="PortBusy", message="blocked: port held by 1 sleep, 2 tview",
                              path="can")
    blocked = probe.blocked_by([status], proc=Path("/nonexistent"))
    assert [(holder.pid, holder.program) for holder in blocked.holders] == [(1, "sleep"), (2, "tview")]
    assert probe.blocked_sentence(blocked, 10) == \
        "sleep (pid 1) and tview (pid 2) have the port \u00b7 RobotDx probes again when they close"
    lock = DiagnosticStatus(level="unknown", name="x", code="BusLockHeld", message="blocked: rack bus lock held by 5 experiment",
                            path="can")
    held = probe.blocked_by([lock], proc=Path("/nonexistent"))
    assert probe.blocked_sentence(held, 10) == "experiment has the rack bus lock (pid 5) \u00b7 RobotDx probes again when it closes"
    said = probe.blocked_by([lock, Event("ProbeBlocked", message="my own words")], proc=Path("/nonexistent"))
    assert probe.blocked_sentence(said, 10) == "my own words", "the driver's ProbeBlocked sentence wins"
    assert probe.blocked_by([DiagnosticStatus(level="ok", name="x", path="")]) is None


def test_program_name_reads_the_script_behind_an_interpreter(tmp_path):
    for pid, argv in ((10, ["/usr/bin/python3", "/home/bam/.local/bin/tview", "-t", "1"]),
                      (11, ["python3", "-u", "-m", "moteus_gui.tview"]), (12, ["/usr/bin/sleep", "60"]),
                      (13, ["python3", "script.py"])):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
    assert probe.program_name(10, "python3", tmp_path) == "tview"
    assert probe.program_name(11, "python3", tmp_path) == "moteus_gui.tview"
    assert probe.program_name(12, "python3", tmp_path) == "sleep"
    assert probe.program_name(13, "python3", tmp_path) == "script"
    assert probe.program_name(99, "python3", tmp_path) == "python3", "a process already gone keeps the plugin's word"
    assert probe.program_name(10, "tview", tmp_path) == "tview", "a plugin that already names the program is believed"


def test_holders_are_watched_by_reading_proc_never_by_a_subprocess(tmp_path, monkeypatch):
    def stat(pid: int, state: str, started: int) -> None:
        (tmp_path / str(pid)).mkdir(exist_ok=True)
        (tmp_path / str(pid) / "stat").write_text(
            f"{pid} (py thon) {state} 1 1 1 0 -1 4194304 0 0 0 0 0 0 0 0 20 0 1 0 {started} 1 2 3\n")

    stat(5, "S", 777)
    blocked = probe.Blocked("PortBusy", (probe.Holder(5, "python3", "tview", probe.start_time(5, tmp_path)),), "")
    assert blocked.holders[0].started == "777"

    def refuse(*_args, **_kwargs):
        raise AssertionError("the holder watch started a subprocess")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    assert not probe.holders_gone(blocked, tmp_path)
    stat(5, "Z", 777)  # exited, not yet reaped by its parent: it holds nothing
    assert probe.holders_gone(blocked, tmp_path)
    stat(5, "S", 999)  # the pid came back as another process
    assert probe.holders_gone(blocked, tmp_path)
    (tmp_path / "5" / "stat").unlink()  # gone
    assert probe.holders_gone(blocked, tmp_path)
    assert not probe.holders_gone(probe.Blocked("BusLockHeld", (), ""), tmp_path), "no pid to watch is never 'gone'"
