"""Connected only (Zach, 2026-11-09): "I wanted to quickly just plug in the can FD and be able to kinda see what is live
connected". While a page shows the Connected view it beats ``POST /api/v1/presence``; the host keeps one query-only
``presence`` process per device whose plugin declares it, and reads its passes (docs/api-v1.md "Presence"):

- a board a pass hears is ``live`` at once, and a board no row knew gets one;
- a board that two passes in a row that really looked did not hear is not heard (the roster's NotHeard), and heard again
  the moment it answers;
- a blocked pass (an experiment holds the rack bus lock) judges nothing;
- a pass never overwrites what a full read said about the same board (its model, firmware), and its PresencePass lines
  are the host's, never the log's;
- nothing runs once the page stops asking.

A stand-in plugin (stdlib, a subprocess as the real one is) reads what answers from a JSON file on every pass. Fakes only.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from test_host_support import Clock, FakeOs, standard_skeleton, usb_device

from io_diagnostics.host import Host
from io_diagnostics.server import make_server, serve_in_thread

SERIAL = "272813EF"
UUID = {1: "32bb145a-4c4a-48c6-a903-7ee761bba37c", 14: "e00da862-a481-4d73-afb2-483e908ea415",
        22: "31cac8ec-7749-4ce8-997c-61fa85ddf454", 99: "c8542062-8b0e-473d-bfac-c36c7d039517"}
PAGE = {"X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}

# What the real moteus_servo plugin prints (bam_ws moteus_servo/diagnostics.py, ``presence``): per pass the bus entity, an
# entity and an ``: answers`` status per board that answered, then one PresencePass event; a blocked pass only the event.
PLUGIN = r'''
import json, os, sys, time
args = sys.argv[1:]
verb = next(word for word in args if word in ("describe", "diagnose", "presence"))
bus_file = os.environ["BUS_FILE"]
def emit(record):
    print(json.dumps({"schema": "diagnostics-record/0", **record}), flush=True)
def log(row):
    with open(os.environ["BUS_LOG"], "a") as handle:
        handle.write(json.dumps(row) + "\n")
if verb == "describe":
    print(json.dumps({"name": "bus", "schema": "diagnostics-record/0",
                      "presence": {"period_s": float(os.environ.get("BUS_PERIOD_S", "0.15")), "danger": "query"}}))
    raise SystemExit
uuids = json.loads(os.environ["BUS_UUIDS"])
if verb == "diagnose":
    spec = json.load(open(bus_file))
    log({"verb": "diagnose", "t": time.time()})
    emit({"record": "entity", "path": "", "name": "fdcanusb X", "kind": "device"})
    emit({"record": "entity", "path": "can", "name": "CAN bus", "kind": "bus", "part_of": ""})
    if spec.get("diagnose_crash_s"):  # the adapter is pulled while it is read: the read fails as pyserial does
        time.sleep(spec["diagnose_crash_s"])
        sys.stderr.write("serial.serialutil.SerialException: device reports readiness to read but returned no data\n")
        raise SystemExit(1)
    if spec.get("diagnose_blocked"):  # an experiment holds the bus at the restart: the full read looks at nothing
        emit({"record": "status", "level": "unknown", "name": "CAN bus", "path": "can", "code": "BusLockHeld",
              "message": "blocked: rack bus lock held by 4242 run_experiment"})
        raise SystemExit
    emit({"record": "status", "level": "ok", "name": "CAN bus", "path": "can", "expected_period_s": 300})
    for can_id in spec["diagnose"]:
        emit({"record": "entity", "path": f"can/{can_id}", "name": f"CAN {can_id}", "kind": "node", "via": "can",
              "hardware_id": "moteus:" + uuids[str(can_id)],
              "identity": {"manufacturer": "mjbots", "model": "moteus-r4.11", "serial_number": f"SN{can_id}", "sw_version": "1.1.2"}})
        emit({"record": "status", "level": "ok", "name": f"CAN {can_id}: servo", "path": f"can/{can_id}",
              "message": "stopped, no fault", "expected_period_s": 300})
    raise SystemExit
log({"verb": "presence", "known": os.environ.get("IO_DIAGNOSTICS_KNOWN_NODES"), "pid": os.getpid()})
period = float(os.environ.get("BUS_PERIOD_S", "0.15"))
while True:
    spec = json.load(open(bus_file))  # re-read every pass: a test plugs and unplugs boards between passes
    now = time.time()
    if spec.get("hang"):  # stuck mid-pass with the bus lock held, as seen on the rack 2026-11-09: silent, never ends by itself
        import fcntl
        held = open(spec["hang"], "a")
        fcntl.flock(held, fcntl.LOCK_SH)
        log({"verb": "hung", "pid": os.getpid()})
        json.dump({**spec, "hang": None}, open(bus_file + ".tmp", "w"))
        os.replace(bus_file + ".tmp", bus_file)
        while True:
            time.sleep(60)
    if spec.get("blocked"):
        emit({"record": "event", "code": "PresencePass", "path": "can", "severity": "debug", "stamp": now,
              "message": "blocked: rack bus lock held by 4242 run_experiment",
              "args": {"outcome": "BusLockHeld", "holders": ["4242 run_experiment"]}})
    else:
        answer = {int(key): value for key, value in spec["presence"].items()}
        skip = spec.get("skip_once") or []
        if skip:  # one lost reply: these ids miss this pass only
            answer = {can_id: uuid for can_id, uuid in answer.items() if can_id not in skip}
            json.dump({**spec, "skip_once": [], "skipped": spec.get("skipped", 0) + 1}, open(bus_file + ".tmp", "w"))
            os.replace(bus_file + ".tmp", bus_file)
        emit({"record": "entity", "path": "can", "name": "CAN bus", "kind": "bus", "part_of": ""})
        for can_id in sorted(answer):
            uuid = answer[can_id]
            emit({"record": "entity", "path": f"can/{can_id}", "name": f"CAN {can_id}", "kind": "node", "via": "can",
                  "hardware_id": "moteus:" + uuid, "identity": {"manufacturer": "mjbots", "serial_number": f"P{can_id}"}})
        for can_id in sorted(answer):
            emit({"record": "status", "level": "ok", "name": f"fdcanusb X CAN {can_id}: answers", "path": f"can/{can_id}",
                  "message": "answers discovery · UUID " + answer[can_id][:8], "stamp": now})
        emit({"record": "event", "code": "PresencePass", "path": "can", "severity": "debug", "stamp": now,
              "message": f"{len(answer)} boards answer", "args": {"outcome": "looked", "answering": sorted(answer)}})
        if not globals().get("first_said"):
            first_said = True
            log({"verb": "first_pass", "t": time.time()})
    time.sleep(period)
'''


def wait_for(check, *, timeout=10.0, what="the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class Bench:
    """A host over one fdcanusb whose stand-in plugin's boards come from ``bus.json``: ``diagnose`` the full read's
    answer, ``presence`` each pass's (CAN id -> UUID)."""

    def __init__(self, tmp_path: Path, *, diagnose=(1, 14), presence=None, grace_s: float = 1.0, ledger=None, diagnose_blocked=False,
                 plugged=True):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.file = tmp_path / "bus.json"
        self.log = tmp_path / "bus.log"
        self.set(diagnose=list(diagnose), presence=presence if presence is not None else {can_id: UUID[can_id] for can_id in diagnose},
                 diagnose_blocked=diagnose_blocked)
        plugins = tmp_path / "plugins.d"
        plugins.mkdir()
        script = plugins / "bus_plugin.py"
        script.write_text(PLUGIN)
        env = {"BUS_FILE": str(self.file), "BUS_LOG": str(self.log), "BUS_UUIDS": json.dumps({str(k): v for k, v in UUID.items()})}
        (plugins / "bus.toml").write_text(
            f'name = "bus"\ncommand = ["{sys.executable}", "{script}"]\ntimeout_s = 10\nperiod_s = 300\nmatches = ["usb:0483:5740"]\n'
            f'env = {{ {", ".join(f"{key} = {json.dumps(value)}" for key, value in env.items())} }}\n')
        self.adapter = usb_device("9-3", "0483", "5740", SERIAL, label="fdcanusb")
        self.os = FakeOs(standard_skeleton(self.adapter) if plugged else standard_skeleton())
        self.host = Host(plugins_dir=plugins, clock=Clock(), os_source=self.os,
                         watch_grace_s=grace_s, watch_tick_s=0.1, follow_backoff_s=(0.2, 0.4), frames_dir=tmp_path / "frames", ledger_file=ledger)
        self.host.run_all_once()
        self.device = self.host.layer.device_paths.get("usb:9-3")

    def plug(self) -> None:
        """The adapter is plugged in: the next refresh makes its job, whose first run is what a plug runs."""

        self.os.devices[:] = standard_skeleton(self.adapter)
        self.host.run_all_once()
        self.device = self.host.layer.device_paths["usb:9-3"]

    def set(self, **changes) -> None:
        current = json.loads(self.file.read_text()) if self.file.exists() else {}
        current.update({key: ({str(k): v for k, v in value.items()} if isinstance(value, dict) else value) for key, value in changes.items()})
        self.file.write_text(json.dumps(current))

    def board(self, can_id: int) -> str:
        return f"{self.device}/can/{can_id}"

    def node(self, can_id: int) -> dict | None:
        found = self.host.node(self.board(can_id))
        return found["node"] if found else None

    def presence(self, can_id: int) -> str | None:
        node = self.node(can_id)
        return node["presence"]["state"] if node and node["presence"] else None

    def passes(self) -> int:
        return sum(1 for row in self.verbs("presence"))

    def verbs(self, verb: str) -> list[dict]:
        if not self.log.exists():
            return []
        return [row for row in map(json.loads, self.log.read_text().splitlines()) if row["verb"] == verb]

    def beat(self) -> dict:
        return self.host.presence_heartbeat()

    def keep_beating(self, check, *, what: str, timeout: float = 10.0):
        return wait_for(lambda: (self.beat(), check())[1], timeout=timeout, what=what)

    def stop(self) -> None:
        self.host.stop()


@pytest.fixture
def bench(tmp_path):
    rig = Bench(tmp_path)
    yield rig
    rig.stop()


def processes(host: Host) -> list:
    return [watch.follower.process for watch in host.watches.values() if watch.follower is not None and watch.follower.process is not None]


# ----- running only while a page asks ---------------------------------------------------------------------------------------
def connected_and_lapsed(tmp_path, monkeypatch) -> "Bench":
    """A bench whose adapter's connect window (CONNECT_PRESENCE_S, shortened here) has run out: one presence process ran and ended."""

    from io_diagnostics import host as host_module

    monkeypatch.setattr(host_module, "CONNECT_PRESENCE_S", 0.3)
    rig = Bench(tmp_path)
    wait_for(lambda: not processes(rig.host), timeout=8.0, what="the connection's presence process lapsing")
    assert len(rig.verbs("presence")) == 1, "the connection was looked at by presence first"
    return rig


def test_after_the_connect_window_nothing_runs_until_a_page_asks_and_it_stops_after_the_grace(tmp_path, monkeypatch):
    bench = connected_and_lapsed(tmp_path, monkeypatch)
    try:
        nothing_runs_until_asked(bench)
    finally:
        bench.stop()


def nothing_runs_until_asked(bench):
    time.sleep(0.4)
    assert len(bench.verbs("presence")) == 1 and processes(bench.host) == []  # the full read alone, as before
    answer = bench.beat()["presence"]
    assert answer["supported"] and answer["period_s"] == pytest.approx(0.15)
    [adapter] = answer["adapters"]
    assert adapter["path"] == bench.device and adapter["state"] in ("starting", "running")
    started = wait_for(lambda: len(bench.verbs("presence")) == 2 and bench.verbs("presence"), what="the presence process")
    # WHY the rows it knows: a board on firmware without discovery is asked by id, so the plugin is told which ids have rows
    assert started[1]["known"] == "1,14"
    [process] = wait_for(lambda: processes(bench.host), what="its process")
    # the page stops asking: gone about a grace later, and nothing starts again
    wait_for(lambda: process.poll() is not None, timeout=6.0, what="the presence process to stop")
    time.sleep(0.5)
    assert processes(bench.host) == [] and len(bench.verbs("presence")) == 2
    assert bench.host.presence_json()["adapters"][0]["state"] == "idle"


def test_a_board_that_answers_is_live_and_one_that_stops_is_not_heard_within_two_passes(bench):
    bench.keep_beating(lambda: bench.presence(14) == "live" and bench.presence(1) == "live", what="both boards live")
    assert bench.node(14)["presence"]["text"] == "answers now"
    bench.set(presence={1: UUID[1]})  # CAN 14 unplugged
    bench.keep_beating(lambda: bench.presence(14) == "stale", what="CAN 14 not heard")
    node = bench.node(14)
    assert node["own"]["level"] == "stale" and node["presence"]["text"].startswith("last heard")
    check = next(check for check in bench.host.node(bench.board(14))["checks"] if check["name"].endswith(": last heard"))
    assert (check["code"], check["source"]) == ("NotHeard", "io_diagnostics.roster")
    assert bench.presence(1) == "live"
    # plugged back: heard at the next pass, and the not-heard line is gone
    bench.set(presence={1: UUID[1], 14: UUID[14]})
    bench.keep_beating(lambda: bench.presence(14) == "live", what="CAN 14 back")
    assert not [check for check in bench.host.node(bench.board(14))["checks"] if check["name"].endswith(": last heard")]
    assert bench.node(14)["own"]["level"] == "ok"


def test_one_missed_pass_is_not_enough(tmp_path):
    """A lost reply is not an unplug: a board must miss two passes that looked before it reads not heard."""

    rig = Bench(tmp_path)
    try:
        rig.keep_beating(lambda: rig.presence(14) == "live", what="CAN 14 live")
        watch = next(iter(rig.host.watches.values()))
        rig.set(skip_once=[14])
        rig.keep_beating(lambda: json.loads(rig.file.read_text()).get("skipped") == 1, what="the pass that missed CAN 14")
        before = watch.passes
        rig.keep_beating(lambda: watch.passes >= before + 3, what="three more passes")
        assert watch.misses.get("can/14") == 0 and rig.presence(14) == "live"
        assert not [check for check in rig.host.node(rig.board(14))["checks"] if check["name"].endswith(": last heard")]
    finally:
        rig.stop()


def test_a_new_board_gets_a_row_at_once_and_a_known_board_keeps_what_the_full_read_said(bench):
    bench.set(presence={1: UUID[1], 14: UUID[14], 22: UUID[22]})
    bench.keep_beating(lambda: bench.presence(22) == "live", what="CAN 22's row")
    new = bench.node(22)
    assert (new["name"], new["kind"], new["hardware_id"]) == ("CAN 22", "node", f"moteus:{UUID[22]}")
    assert new["identity"] == {"manufacturer": "mjbots", "serial_number": "P22"}
    # CAN 1 is the same board the full read described (same UUID): its model, serial and firmware stay
    known = bench.node(1)
    assert known["identity"]["model"] == "moteus-r4.11" and known["identity"]["sw_version"] == "1.1.2"
    assert known["identity"]["serial_number"] == "SN1"


def test_another_board_on_a_known_id_replaces_what_the_row_says(bench):
    """Fresh moteus boards all ship as CAN 1: swapping one for another must not keep the first board's identity."""

    bench.set(presence={1: UUID[99], 14: UUID[14]})
    bench.keep_beating(lambda: (bench.node(1) or {}).get("hardware_id") == f"moteus:{UUID[99]}", what="CAN 1's new board")
    assert bench.node(1)["identity"] == {"manufacturer": "mjbots", "serial_number": "P1"}
    assert bench.presence(1) == "live"


def test_a_blocked_pass_judges_nothing_and_says_who_holds_the_bus(bench):
    bench.keep_beating(lambda: bench.presence(14) == "live", what="CAN 14 live")
    bench.set(blocked=True, presence={})
    adapter = bench.keep_beating(lambda: (found := bench.beat()["presence"]["adapters"][0])["last"]
                                 and found["last"]["outcome"] == "BusLockHeld" and found, what="a blocked pass")
    assert adapter["last"]["holders"] == ["4242 run_experiment"] and "run_experiment" in adapter["last"]["message"]
    time.sleep(0.6)  # several blocked passes
    bench.beat()
    assert not [check for check in bench.host.node(bench.board(14))["checks"] if check["name"].endswith(": last heard")]
    assert bench.node(14)["own"]["level"] == "ok"  # nothing marked dead while it could not look


def test_a_remembered_board_that_no_pass_hears_says_when_it_was_really_last_heard(tmp_path):
    """Seen live 2026-11-09 on the scratch host: a row restored from the ledger, then not heard by two presence passes, read "last heard
    20767 days ago" (the epoch). Its last-heard time is the ledger's, as when a full read does not report it."""

    ledger = tmp_path / "tree.json"
    first = Bench(tmp_path / "first", ledger=ledger)
    first.stop()
    remembered = json.loads(ledger.read_text())
    [stamp] = [item["last_heard"] for entry in remembered["devices"].values() for rel, item in entry["entities"].items() if rel == "can/14"]
    # a restart with an experiment holding the bus: the full read looks at nothing, so CAN 1 and 14 are only restored
    rig = Bench(tmp_path / "second", ledger=ledger, diagnose_blocked=True, presence={1: UUID[1]})
    try:
        rig.keep_beating(lambda: rig.presence(14) == "stale" and not (rig.node(14) or {}).get("presence", {}).get("restored"), what="CAN 14 not heard")
        presence = rig.node(14)["presence"]
        assert presence["age_s"] is not None and presence["age_s"] < 600, presence
        assert presence["text"].startswith("last heard") and "days" not in presence["text"], presence
        check = next(check for check in rig.host.node(rig.board(14))["checks"] if check["name"].endswith(": last heard"))
        assert check["received"] == pytest.approx(stamp)
        assert rig.presence(1) == "live"
    finally:
        rig.stop()


def test_the_answer_names_each_adapter_as_its_row_does(bench):
    bench.keep_beating(lambda: bench.beat()["presence"]["adapters"][0]["last"], what="a pass")
    assert bench.beat()["presence"]["adapters"][0]["name"] == "fdcanusb X"


def test_a_hung_presence_process_is_killed_within_seconds_and_its_bus_lock_released(tmp_path, monkeypatch):
    """Every pass prints a line, so a silent presence process is a hung one, likely holding the rack bus lock (an experiment then
    refuses to start). The host kills it after max(5 x period, 10 s), not a follower's 100 s, and starts a fresh one."""

    import fcntl

    from io_diagnostics.runner import Presencer

    assert Presencer.silence_floor_s == 10.0 and Presencer.silence_factor == 5.0
    monkeypatch.setattr(Presencer, "silence_floor_s", 1.0)  # 1 s here; 10 s on the rack (moteus passes every 2 s)
    rig = Bench(tmp_path)
    lock = tmp_path / "bus.lock"
    try:
        rig.keep_beating(lambda: rig.presence(14) == "live", what="CAN 14 live")
        rig.set(hang=str(lock))
        [hung] = rig.keep_beating(lambda: rig.verbs("hung"), what="the process hanging with the lock held")
        with open(lock, "a") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = False
            except BlockingIOError:
                held = True
        assert held, "the stand-in holds the lock while hung"
        started = time.monotonic()
        rig.keep_beating(lambda: not Path(f"/proc/{hung['pid']}").exists() or Path(f"/proc/{hung['pid']}/stat").read_text().split(") ")[1][0] == "Z",
                         what="the hung process killed", timeout=8.0)
        assert time.monotonic() - started < 5.0
        with open(lock, "a") as probe:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free: an experiment could start
        rig.keep_beating(lambda: len(rig.verbs("presence")) >= 2 and rig.presence(14) == "live", what="a fresh presence process")
    finally:
        rig.stop()


def test_with_the_view_on_a_plugged_adapter_is_first_looked_at_by_presence_not_the_full_read(tmp_path):
    """Seen on Preview 2026-11-09: after a restart its probe held 0607623A's port for ~40 s, so the Connected view showed that adapter's
    boards only after it ended. On a plug the probe and the first presence pass race for the port lock: 3 s or 40 s by chance. While
    the view beats, the presence pass goes first (the full read waits for it, at most PRESENCE_FIRST_S)."""

    from io_diagnostics import host as host_module

    rig = Bench(tmp_path, plugged=False)
    try:
        rig.beat()  # the page shows Connected only; nothing is plugged in yet
        assert rig.beat()["presence"]["adapters"] == []
        rig.plug()
        [first] = rig.verbs("first_pass")
        [full] = rig.verbs("diagnose")
        assert first["t"] < full["t"], (first, full)
        assert full["t"] - first["t"] < host_module.PRESENCE_FIRST_S
        assert rig.presence(14) == "live" and rig.presence(1) == "live"
    finally:
        rig.stop()


def test_a_plug_is_first_looked_at_by_presence_with_no_page_open_and_it_lapses_after_the_connect_window(tmp_path, monkeypatch):
    """Zach, 2026-11-10: boards "like 1 second after the port shows up". Seen: the probe alone took 40-90 s (whole-config reads), and
    longer behind the other host's probe. A connection now gets its presence process whether or not a page shows Connected only."""

    from io_diagnostics import host as host_module

    monkeypatch.setattr(host_module, "CONNECT_PRESENCE_S", 1.5)
    rig = Bench(tmp_path, plugged=False)
    try:
        rig.plug()  # no beat at all
        [first] = rig.verbs("first_pass")
        [full] = rig.verbs("diagnose")
        assert first["t"] < full["t"], (first, full)
        assert rig.presence(14) == "live" and rig.presence(1) == "live"
        [process] = wait_for(lambda: processes(rig.host), what="the connection's presence process")
        wait_for(lambda: process.poll() is not None, timeout=8.0, what="it lapsing with no page")
        assert not processes(rig.host)
    finally:
        rig.stop()


def test_an_adapter_that_drops_off_usb_during_a_read_is_not_a_plugin_crash(tmp_path):
    """Seen 2026-11-10: the wrist fdcanusb dropped off USB twice in 30 s; the probe reading it died with "SerialException ... device
    disconnected" and was confirmed as an ERROR fault, PluginCrashed. The host knows the device re-enumerated under the run."""

    import threading

    rig = Bench(tmp_path)
    try:
        rig.adapter["attrs"]["devnum"] = 7
        rig.os.devices[:] = standard_skeleton(rig.adapter)
        rig.host.refresh()
        job = next(iter(rig.host.jobs.values()))
        rig.set(diagnose_crash_s=1.0)
        runner = threading.Thread(target=rig.host.run_job, args=(job,))
        runner.start()
        time.sleep(0.3)
        rig.adapter["attrs"]["devnum"] = 9  # unplugged and back under the read
        rig.os.devices[:] = standard_skeleton(rig.adapter)
        rig.host.refresh()
        runner.join(10)
        status = job.cache.host_status
        assert (status.level, status.code) == ("unknown", "DeviceLeftDuringRun"), status
        assert "dropped off USB" in status.message
        assert not [fault for fault in rig.host.faults.all() if fault.code == "PluginCrashed"]
        # the same failure with the adapter left alone is still a crash
        rig.set(diagnose_crash_s=0.2)
        rig.host.run_job(job)
        assert job.cache.host_status.code == "PluginCrashed"
    finally:
        rig.stop()


def test_presence_passes_stay_out_of_the_activity_log(bench):
    bench.keep_beating(lambda: bench.passes() and bench.presence(14) == "live", what="passes")
    time.sleep(0.5)
    codes = {event["code"] for event in bench.host.activity()["events"]}
    assert "PresencePass" not in codes


def test_a_device_whose_plugin_declares_no_presence_is_not_asked(tmp_path, monkeypatch):
    rig = connected_and_lapsed(tmp_path, monkeypatch)
    try:
        rig.host.describes["bus"] = {k: v for k, v in rig.host.describes["bus"].items() if k != "presence"}
        answer = rig.beat()["presence"]
        assert answer["supported"] is False and answer["adapters"] == []
        time.sleep(0.4)
        assert len(rig.verbs("presence")) == 1
    finally:
        rig.stop()


def test_the_page_beats_over_http_and_only_the_page_may(tmp_path):
    import urllib.error
    import urllib.request

    rig = Bench(tmp_path)
    server = make_server(rig.host, port=0)
    serve_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        request = urllib.request.Request(f"{base}/api/v1/presence", data=b"{}", method="POST", headers=PAGE)
        with urllib.request.urlopen(request, timeout=5) as response:
            answer = json.load(response)
        assert answer["presence"]["supported"] and answer["presence"]["adapters"][0]["path"] == rig.device
        bare = urllib.request.Request(f"{base}/api/v1/presence", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(bare, timeout=5)
        assert refused.value.code == 403
    finally:
        server.shutdown()
        rig.stop()
