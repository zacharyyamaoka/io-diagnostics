"""The pages are just there (Zach, 2026-11-04: "I don't like how it kinda just like loads... surely maybe there is like a
timeout on these things? ... Like for the log shouldn't it just be there?").

What was measured on Preview that day: ``/node?path=host`` 3.8 s, an adapter's 22 s, the page's own retries on top of that. The
server side of the fix, pinned here: a read answers from the view the host last published, never from a build of its own; a
node serializes the 20 events it sends, not all 26,867; the Activity log folds once per view and in linear time; a page never
waits for a pass over /proc; a slow ``describe`` never holds up the server; the history and the events survive a restart.

Fixture hosts and fakes only: no real hardware, no port of Stable or Preview. The load test starts its own busy processes and
stops them by PID. tests/test_instant_pages.js is the page's half.
"""

from __future__ import annotations

import json
import os
import random
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from test_host_setup import A, Rack
from test_host_support import ROOT, FakeOs, SetClock, standard_skeleton, usb_device

from contract.record import Event, to_dict
from io_diagnostics import activity as activity_module
from io_diagnostics import host as host_module
from io_diagnostics import logview, roster as roster_module
from io_diagnostics.host import Host, LiveOs, make_host
from io_diagnostics.server import make_server, serve_in_thread
from io_facts import reuse

CLI = ROOT / "io_diagnostics" / "cli.py"
BOARD = "host/usb1/port-1-3/usb-1-3/can/1"
ADAPTER = "host/usb1/port-1-3/usb-1-3"
LOGGED = 26_867  # the events Preview's log held on 2026-11-04
TARGET_P95_MS = 200.0


def now_of(frame: str = "F1") -> float:
    return host_module.load_fixture(frame)[1]["clock"]


def logged_event(number: int, clock: float) -> Event:
    """One of the log's events, spread over the devices and the codes Preview's log really holds."""

    path = ["host", ADAPTER, BOARD, "host/usb6/port-6-1/usb-6-1", "host/usb1/port-1-5/usb-1-5"][number % 5]
    code = ["LevelChanged", "ProbeBlocked", "Plugged", "PluginReloaded"][number % 4]
    return Event(code, clock - 60 - number * 7.0, "info", f"event {number} about {path}", path=path, seq=number, source="test",
                 args={"id": f"test:{number}"})


def write_log(directory: Path, count: int, clock: float) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "20261025T120718Z-5a67a0fa-000.jsonl").open("w") as file:
        for number in range(count):
            event = logged_event(number, clock)
            file.write(json.dumps({"key": activity_module.event_id(event), "event": to_dict(event)}) + "\n")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Serve:
    """``cli.py serve --fixture``, as Preview runs it, with its own state and config directories. Stopped by its process group."""

    def __init__(self, root: Path, *extra: str, env: dict | None = None, plugins: Path | None = None, port: int | None = None):
        self.root, self.port = root, port or free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        (root / "state").mkdir(parents=True, exist_ok=True)
        (root / "config").mkdir(exist_ok=True)
        environment = {**os.environ, "IO_DIAGNOSTICS_STATE_DIR": str(root / "state"), "IO_DIAGNOSTICS_CONFIG_DIR": str(root / "config"), **(env or {})}
        environment.pop("VIRTUAL_ENV", None)
        argv = [sys.executable, str(CLI), "serve", "--fixture", "F1", "--port", str(self.port), "--no-udev", *extra]
        if plugins is not None:
            argv += ["--plugins", str(plugins)]
        self.started = time.monotonic()
        self.process = subprocess.Popen(argv, cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def get(self, path: str, timeout: float = 30.0) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=timeout) as response:
            return json.load(response)

    def stop(self, sig: int = signal.SIGTERM) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, sig)
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(5)

    def wait_up(self, seconds: float = 40.0) -> float:
        """Seconds from the process start to the first answered ``/api/v1/health``."""

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                self.get("/api/v1/health", timeout=2.0)
                return time.monotonic() - self.started
            except (OSError, ValueError):
                time.sleep(0.03)
        raise AssertionError(f"no answer in {seconds} s")


def state_plugins(root: Path, *, period_s: int = 30) -> Path:
    directory = root / "plugins"
    directory.mkdir(parents=True, exist_ok=True)
    source = ROOT / "tests" / "fixtures" / "plugins-state"
    for name in ("state_joint.toml", "state_camera.toml"):
        text = (source / name).read_text().replace("{here}", str(source)).replace("period_s = 30", f"period_s = {period_s}")
        (directory / name).write_text(text)
    return directory


# ----- a node serializes what it sends ----------------------------------------------------------------------------------
def test_a_node_builds_json_only_for_the_events_it_sends(monkeypatch):
    """host.py's ``[event_json(event, ..., self._names(view)) for event in view.events ...][:20]``: every event of the log was
    serialized, and the whole name table rebuilt for each, to send 20. That was the host page's 3.8 s (26,867 x 93 entities)."""

    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    clock = host.clock.now()
    host.eventlog.record([logged_event(number, clock) for number in range(3000)], clock)
    made, names = [], []
    real_json, real_names = activity_module.event_json, Host._names
    monkeypatch.setattr(activity_module, "event_json", lambda *args, **kwargs: made.append(1) or real_json(*args, **kwargs))
    monkeypatch.setattr(Host, "_names", lambda self, view: names.append(1) or real_names(self, view))
    answer = host.node("host")
    assert len(answer["events"]) == 20
    assert len(made) <= 20, f"{len(made)} events serialized to send 20"
    assert len(names) <= 2, f"the name table was built {len(names)} times"


# ----- the Activity log folds in linear time ----------------------------------------------------------------------------
def fold_load(confirmed: int, ordinary: int) -> list[Event]:
    """A log with Preview's shape: hundreds of confirmed faults among thousands of events."""

    faults = [Event("FaultConfirmed", 1000.0 + number * 3, "error", f"fault {number}", path=f"host/d{number % 40}", seq=number, source="fault_manager",
                    args={"id": f"c{number}", "fault": f"F{number % 9}"}) for number in range(confirmed)]
    healed = [Event("FaultHealed", 1001.0 + number * 3, "info", f"healed {number}", path=f"host/d{number % 40}", seq=number, source="fault_manager",
                    args={"id": f"h{number}", "fault": f"F{number % 9}"}) for number in range(confirmed)]
    others = [Event("ProbeBlocked", 1000.5 + number * 0.05, "warn", f"blocked {number}", path=f"host/d{number % 40}", seq=number, source="plugin",
                    args={"id": f"o{number}"}) for number in range(ordinary)]
    return faults + healed + others


def test_folding_the_log_is_linear_in_its_events(monkeypatch):
    """logview.fold compared every confirmation with every event: Preview's log cost 946,693 ``fault_key`` calls and 1.5-3.5 s on
    every load of the Log tab, and again on every tick of every open /events stream."""

    events = fold_load(500, 7000)
    calls = []
    real = logview.fault_key
    monkeypatch.setattr(logview, "fault_key", lambda event: calls.append(1) or real(event))
    began = time.process_time()
    rows = logview.fold(events, active_runs=[])
    spent = time.process_time() - began
    assert rows, "the log folds to rows"
    assert len(calls) <= 6 * len(events), f"{len(calls)} fault_key calls over {len(events)} events"
    assert spent < 0.6, f"{spent:.2f} s of CPU to fold {len(events)} events"


def test_fold_finds_causes_endings_and_links_as_it_did():
    """The rules the index must keep (logview's section 1, 3, 4): the closest warn-or-worse cause, an occurrence's own ending
    and not the next one's, a healed row linked to the latest confirmation before it."""

    cause = Event("BusError", 100.0, "error", "bus error", path="host/a", seq=1, source="plugin", args={"id": "cause"})
    far = Event("BusError", 40.0, "error", "older bus error", path="host/a", seq=2, source="plugin", args={"id": "far"})
    first = Event("FaultConfirmed", 101.0, "error", "fault", path="host/a", seq=3, source="fm", args={"id": "first", "fault": "F"})
    healed = Event("FaultHealed", 110.0, "info", "healed", path="host/a", seq=4, source="fm", args={"id": "healed", "fault": "F"})
    second = Event("FaultConfirmed", 120.0, "error", "fault again", path="host/a", seq=5, source="fm", args={"id": "second", "fault": "F"})
    rows = {row.id: row for row in logview.fold([cause, far, first, healed, second], active_runs=[])}
    assert [(badge["id"], badge["state"], badge["ended"]) for badge in rows["cause"].badges] == [("first", "healed", 110.0)]
    assert "far" in rows and not rows["far"].badges
    assert rows["second"].event.args["state"] == "confirmed"
    assert rows["healed"].event.args["confirmed_row"] == "cause"


# ----- a read never builds -------------------------------------------------------------------------------------------------
@pytest.fixture
def service(tmp_path):
    """A fixture host running as a service (its threads started), with its HTTP server."""

    host = make_host(fixture="F1", plugins_dir=state_plugins(tmp_path))
    http = make_server(host, 0)
    serve_in_thread(http)
    host.start(udev=False)
    yield host
    http.shutdown()
    http.server_close()
    host.stop()


def get(host, path: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(host.url.rstrip("/") + path, timeout=timeout) as response:
        return json.load(response)


def wait_for_board(host, seconds: float = 30.0) -> str:
    """The fixture board's /node path, once its plugin has reported it."""

    path = "/api/v1/node?path=" + urllib.parse.quote(BOARD)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            get(host, path)
            return path
        except urllib.error.HTTPError:
            time.sleep(0.1)
    raise AssertionError("the fixture board never reported")


def test_reads_answer_from_the_published_view_they_never_build_their_own(service, monkeypatch):
    """Every GET built the whole view: the tree, the rollup, the rig rows, a sort of the whole log. Thirty reads are one view."""

    path = wait_for_board(service)
    builds = []
    real = Host.view
    monkeypatch.setattr(Host, "view", lambda self: builds.append(threading.current_thread().name) or real(self))
    for _ in range(10):
        get(service, "/api/v1/state")
        get(service, path)
        get(service, "/api/v1/activity?limit=5")
    on_requests = [name for name in builds if name.startswith("Thread-") or "process_request" in name]
    assert not on_requests, f"{len(on_requests)} of 30 reads built a view on the request's own thread"


def test_a_slow_build_never_holds_up_a_node(service, monkeypatch):
    """"never blocked behind a recompute or a slow beat": with every build taking 1.5 s, a node still answers at once."""

    path = wait_for_board(service)
    real = host_module.tree.build_rows

    def slow(*args, **kwargs):
        time.sleep(1.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(host_module.tree, "build_rows", slow)
    service.notify("test")
    time.sleep(0.2)  # a build is under way
    samples = []
    for _ in range(8):
        began = time.perf_counter()
        get(service, path)
        samples.append(time.perf_counter() - began)
    assert max(samples) < 0.5, f"a node waited {max(samples):.2f} s behind a build"


def test_a_run_that_just_ended_is_not_read_as_interrupted(service, monkeypatch):
    """The log pairs a run's events with whether it still runs. Both must come from one moment: read live beside a published view, a run that ended a moment ago had no
    ending in the view and no place among the active, and read "the host stopped while it ran"."""

    seen = []
    real = logview.fold
    monkeypatch.setattr(logview, "fold", lambda events, *, active_runs=None: seen.append(active_runs) or real(events, active_runs=active_runs))
    view = service.view()
    view.active_runs = frozenset({"from-the-view"})
    service.log_json(view, 5, None, channel="activity", path="", scope="children", severity="debug")
    assert seen == [frozenset({"from-the-view"})]


def test_a_change_reaches_a_read_soon(service):
    """A published view is a moment ago, never a long time ago."""

    before = get(service, "/api/v1/state")["clock"]["now"]
    time.sleep(2.5)
    after = get(service, "/api/v1/state")
    assert after["clock"]["now"] - before >= 2.0, "the clock of a read keeps moving"
    service.notify("test")
    revision = service.revision
    end = time.monotonic() + 3.0
    while time.monotonic() < end and service._published_revision < revision:
        time.sleep(0.02)
    assert service._published_revision >= revision, "a change is published within seconds"


def test_a_write_answers_only_once_it_is_published(service):
    """What a person just did is in their next read: a POST can wait for a build, a GET never does. The refusal counts: the answer is sent the same way."""

    get(service, "/api/v1/state")
    service.notify("a write changed something")
    revision = service.revision
    request = urllib.request.Request(service.url.rstrip("/") + "/api/v1/forget", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError):  # no page header: refused, and answered
        urllib.request.urlopen(request, timeout=10)
    assert service._published_revision >= revision


# ----- a page never waits for /proc ------------------------------------------------------------------------------------------
class RackOs(LiveOs):
    """The live gates on a hand-written skeleton, no kernel log: what ``/node`` meets on the real machine."""

    def __init__(self, skeleton, **options):
        super().__init__(**options)
        self.devices = skeleton

    def skeleton(self):
        return [dict(device) for device in self.devices], [{"name": "fake", "state": "used", "note": ""}]

    def boots(self):
        return []


GATED_PLUGIN = """
import json, sys
ACTION = {"name": "check_comms", "title": "Check communication", "description": "ask who answers", "danger": "query", "scope": "device",
          "confirm": "", "kind": "touches", "preconditions": [["port_free", "Nothing else has the port"]]}
verb = next(word for word in sys.argv[1:] if word in ("describe", "diagnose", "probe", "act"))
if verb == "describe":
    print(json.dumps({"name": "gated", "schema": "diagnostics-record/0", "record_version": "0.1", "actions": [ACTION]}))
else:
    print(json.dumps({"schema": "diagnostics-record/0", "record": "entity", "path": "", "name": "adapter", "kind": "device", "actions": [ACTION]}))
    print(json.dumps({"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": "adapter: ran", "path": ""}))
"""


def gated_host(tmp_path, monkeypatch, walk_s: float):
    """A host on the live gates whose /proc pass takes ``walk_s``: the driver's ``find_port_holders`` and the host's own scan alike."""

    (tmp_path / "plugins").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plugins" / "gated.py").write_text(GATED_PLUGIN)
    (tmp_path / "plugins" / "gated.toml").write_text(f'name = "gated"\ncommand = ["{{python}}", "{tmp_path}/plugins/gated.py"]\nperiod_s = 300\n'
                                                     'matches = ["usb:1234:5678"]\n')
    walks = []

    def slow_holders(*_args, **_kwargs):
        walks.append(threading.current_thread().name)
        time.sleep(walk_s)
        return []

    monkeypatch.setattr(reuse, "port_holders_function", lambda: reuse.Imported(slow_holders, "test"))
    monkeypatch.setattr(reuse, "camera_holders_function", lambda: reuse.Imported(slow_holders, "test"))
    device = usb_device("9-1", "1234", "5678", "SER")
    device["attrs"]["by_id"] = ["/dev/serial/by-id/usb-fake-if00"]  # WHY: without a port node the gate has nothing to walk for
    live = RackOs(standard_skeleton(device))
    if hasattr(live, "use_scan"):
        def slow_scan():
            walks.append(threading.current_thread().name)
            time.sleep(walk_s)
            return {"dev": {}, "viewers": [], "unseen": 0}

        live.use_scan(slow_scan)
    host = Host(plugins_dir=tmp_path / "plugins", clock=SetClock(), os_source=live)
    host.run_all_once()
    return host, walks


def test_a_node_never_waits_for_a_pass_over_proc(tmp_path, monkeypatch):
    """The adapter page's 22 s: each of its five actions asked ``port_free``, each ask walked every fd of every process twice with a
    realpath per fd, and the 2 s cache had expired before the next action asked."""

    host, _walks = gated_host(tmp_path, monkeypatch, walk_s=0.6)
    path = next(entity_path for entity_path in host.view().merged.entities if entity_path.endswith("usb-9-1"))
    began = time.perf_counter()
    answer = host.node(path)
    took = time.perf_counter() - began
    assert [action["name"] for action in answer["actions"] if action["name"] == "check_comms"], "the action is offered"
    assert took < 0.3, f"a node waited {took:.2f} s for /proc"
    host.os.close()


def test_every_precondition_shares_one_pass_over_proc(tmp_path):
    """One pass answers every gate: the holders of every /dev node, the viewers, who could not be looked at."""

    from io_facts import open_files

    proc = tmp_path / "proc"
    for pid, comm, argv, links in ((10, "tview", "tview\0-t\0", {"3": "/dev/ttyACM2", "4": "socket:[1]"}),
                                   (11, "realsense-viewe", "/usr/bin/realsense-viewer\0", {"5": "/dev/null"}),
                                   (12, "python3", "python3\0reader.py\0", {"7": "/dev/video4", "8": "/dev/bus/usb/001/007"})):
        (proc / str(pid) / "fd").mkdir(parents=True)
        (proc / str(pid) / "comm").write_text(comm + "\n")
        (proc / str(pid) / "cmdline").write_text(argv)
        for number, target in links.items():
            (proc / str(pid) / "fd" / number).symlink_to(target)
    (proc / "13").mkdir()  # a process that left between the listing and the look: skipped, never fatal
    (proc / "not-a-pid").mkdir()
    found = open_files.scan(proc)
    assert found["dev"]["/dev/ttyACM2"] == [[10, "tview"]]
    assert found["dev"]["/dev/video4"] == [[12, "python3"]]
    assert found["dev"]["/dev/bus/usb/001/007"] == [[12, "python3"]]
    assert "/dev/null" not in found["dev"], "the nodes nobody gates on are left out"
    assert found["viewers"] == [[11, "realsense-viewer"]]


def test_a_pass_finds_what_the_drivers_own_function_finds(monkeypatch):
    """The independent ruler: ``find_port_holders``, the driver's own, on the real /proc, against one pass. A symlink resolves, as it does there."""

    from io_facts import open_files

    imported = reuse.port_holders_function()
    if imported.function is None:
        pytest.skip(f"the driver's checkout: {imported.source}")
    monkeypatch.setattr(open_files, "QUIET", frozenset())  # WHY: /dev/null is the one node a test may hold, and the pass leaves it out
    holder = subprocess.Popen([sys.executable, "-c", "import time; f = open('/dev/null'); time.sleep(30)"])
    try:
        time.sleep(0.5)
        found = open_files.scan()
        ours = {int(entry.split()[0]) for entry in open_files.port_holders(found, "/dev/null")}
        theirs = {int(entry.split()[0]) for entry in imported.function("/dev/null")}
        assert holder.pid in ours and holder.pid in theirs
        assert ours & theirs, "the two read the same machine"
    finally:
        holder.kill()
        holder.wait()


def test_a_viewer_is_the_one_the_drivers_rule_names():
    from io_facts import open_files

    record = next((path for root in (reuse.WORKTREE, reuse.MAIN) if (path := root / reuse.RACK_TWIN / "bam_rack_twin" / "hardware" / "record.py").is_file()), None)
    if record is None:
        pytest.skip("the driver's checkout is not here")
    names = next(line for line in record.read_text().splitlines() if line.startswith("VIEWER_NAMES"))
    assert all(f'"{name}"' in names for name in open_files.VIEWERS), names


def test_a_write_sees_a_holder_that_appeared_a_moment_ago(tmp_path):
    """A page may show a gate a few seconds old; a watch or a configuration write may not start on one. ``gate`` keeps its 2 s
    promise, however quiet the pass has been."""

    device = {"id": "usb:1", "attrs": {"by_id": ["/dev/ttyACM9"]}}
    answers = [{"dev": {}, "viewers": [], "unseen": 0}, {"dev": {"/dev/ttyACM9": [[4242, "tview"]]}, "viewers": [], "unseen": 0}]
    live = RackOs([])
    live.use_scan(lambda: answers[0])
    try:
        assert live.gate(device, "port_free")[0] is True
        live._open_files().close()  # WHY: no background pass may refresh it for the test: gate itself must go and look
        answers.pop(0)
        assert live.peek(device, "port_free")[0] is True, "a page reads the last pass, never a new one"
        time.sleep(host_module.GATE_CACHE_S + 0.05)
        state, detail = live.gate(device, "port_free")
        assert state is False and "4242" in detail and "tview" in detail
    finally:
        live.close()


# ----- the server is up first --------------------------------------------------------------------------------------------------
SLOW_DESCRIBE = """
import json, sys, time
verb = next(word for word in sys.argv[1:] if word in ("describe", "diagnose", "probe"))
if verb == "describe":
    time.sleep(8)
    print(json.dumps({"name": "slow", "schema": "diagnostics-record/0", "record_version": "0.1", "actions": []}))
else:
    print(json.dumps({"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": "slow: ran", "path": ""}))
"""


def test_a_slow_describe_does_not_hold_up_the_server(tmp_path):
    """A restart was a dead page for as long as the drivers' ``describe`` took (``uv run`` under a load of 60): the host answered
    nothing, the page and the chip saw "unreachable". The server is up first; the drivers catch up behind it."""

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "slow.py").write_text(SLOW_DESCRIBE)
    (plugins / "slow.toml").write_text(f'name = "slow"\ncommand = ["{{python}}", "{plugins}/slow.py"]\nperiod_s = 300\nmatches = ["usb:0483:5740"]\n')
    served = Serve(tmp_path, plugins=plugins)
    try:
        up = served.wait_up()
        state = served.get("/api/v1/state", timeout=6.0)
        useful = time.monotonic() - served.started
        assert up < 6.0, f"{up:.1f} s until the first answer"
        assert useful < 7.0, f"{useful:.1f} s until the first /state"
        assert state["tree"]["path"] == "host"
    finally:
        served.stop()


def test_the_log_is_there_on_a_cold_start(tmp_path):
    """"Like for the log shouldn't it just be there?": the very first read of the Log after a restart has the log on disk."""

    write_log(tmp_path / "state" / "log", 2000, now_of())
    served = Serve(tmp_path, plugins=state_plugins(tmp_path))
    try:
        served.wait_up()
        answer = served.get("/api/v1/activity?fold=1&limit=50&severity=debug")
        assert len(answer["events"]) == 50, "the log on disk is read before the first answer"
        assert answer["log"]["matching"] >= 1000
    finally:
        served.stop()


# ----- a restart keeps what the host knew --------------------------------------------------------------------------------------
def test_the_history_survives_a_restart(tmp_path):
    """Preview restarts on every landing: every health bar and sparkline started again from nothing. The history is on disk now."""

    served = Serve(tmp_path, plugins=state_plugins(tmp_path, period_s=1))
    second = None
    try:
        served.wait_up()
        path = "/api/v1/history?path=" + urllib.parse.quote(ADAPTER)

        def points(answer: dict) -> int:
            return sum(len(status["levels"]) for status in answer.get("statuses", []))

        end = time.monotonic() + 30
        answer = {}
        while time.monotonic() < end and not points(answer):
            time.sleep(0.3)
            try:
                answer = served.get(path)
            except urllib.error.HTTPError:
                continue
        assert points(answer), "the first host keeps a history"
        names = {status["name"] for status in answer["statuses"]}
        time.sleep(3.0)
        served.stop()
        # WHY an offset: a replay's clock starts again from its frame, so the second run must start after the first one's points
        second = Serve(tmp_path, "--offset", "120", plugins=state_plugins(tmp_path, period_s=1), port=served.port)
        second.wait_up()
        restored = second.get(path)
        # WHY older than the second start: the plugin reports the same names again at once, so a name alone proves nothing
        restarted = restored["to"] - (time.monotonic() - second.started) - 30.0
        kept = {status["name"] for status in restored["statuses"]
                if any(piece["level"] and piece["from"] < restarted for piece in status["levels"])}
        assert names & kept, f"nothing of {sorted(names)} from before the restart came back: {sorted(kept)}"
    finally:
        served.stop()
        if second is not None:
            second.stop()


def test_a_stop_is_quick_and_frees_the_port(tmp_path):
    """A restart is the old process's stop plus the new one's start, and the port is busy until the first is gone. SIGTERM now saves the history on the way out, which must
    not cost the restart what the fast start gained."""

    served = Serve(tmp_path, plugins=state_plugins(tmp_path))
    try:
        served.wait_up()
        served.get("/api/v1/state")
        began = time.monotonic()
        os.killpg(served.process.pid, signal.SIGTERM)
        served.process.wait(15)
        took = time.monotonic() - began
        assert took < 3.5, f"{took:.1f} s from SIGTERM to the port being free"  # WHY 3.5: under the whole 4 s of STOP_BOUND_S, which is what a stop that leaks costs
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", served.port))
    finally:
        served.stop()


def test_checking_keeps_the_last_answer_beside_it(tmp_path):
    """The rig row said "1-DOF test rack · checking…" for as long as the first probes took, and nothing else. Zach, 2026-11-04: it
    keeps the last known answer next to it. A host with no last answer still says only "checking…"."""

    ledger = tmp_path / "state" / roster_module.LEDGER_FILE
    first = Rack(tmp_path / "before", ledger_file=ledger)
    rig_row = next(path for path, entity in first.host.view().merged.entities.items() if entity.kind == "rig")
    known = first.host.view().merged.entities[rig_row].name
    assert "checking" not in known and "·" in known, known
    first.host.refresh()
    first.host.view()
    again = Rack(tmp_path / "after", ledger_file=ledger, run=False)
    shown = again.host.view().merged.entities[rig_row].name
    assert shown == f"{known} · checking…", shown


def test_a_rig_never_seen_says_only_checking(tmp_path):
    rack = Rack(tmp_path, run=False)
    rig_row = next(path for path, entity in rack.host.view().merged.entities.items() if entity.kind == "rig")
    assert rack.host.view().merged.entities[rig_row].name.endswith(" · checking…")
    assert A  # the rack of test_host_setup: its first adapter


# ----- under load ----------------------------------------------------------------------------------------------------------------
class Burners:
    """``count`` busy processes, started and stopped by PID: the artificial load the latency check runs under."""

    def __init__(self, count: int):
        self.count, self.processes = count, []

    def __enter__(self):
        self.processes = [subprocess.Popen([sys.executable, "-c", "while True: pass"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                          for _ in range(self.count)]
        return self

    def __exit__(self, *_exc):
        for process in self.processes:
            process.kill()
        for process in self.processes:
            process.wait(5)
        assert all(process.poll() is not None for process in self.processes)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered) + 0.999999) - 1)]


def sample_node(served: Serve, paths: list[str], seconds: float) -> list[float]:
    samples, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        for path in paths:
            began = time.perf_counter()
            served.get("/api/v1/node?path=" + urllib.parse.quote(path), timeout=30.0)
            samples.append((time.perf_counter() - began) * 1000)
    return samples


def test_node_p95_stays_under_200_ms_under_cpu_load(tmp_path):
    """The ruler of the brief, on the worst fixture: Preview's 26,867 events, 300 boards a bus, every CPU taken by busy processes."""

    write_log(tmp_path / "state" / "log", LOGGED, now_of())
    served = Serve(tmp_path, plugins=state_plugins(tmp_path), env={"STATE_NODES": "300", "STATE_EXTRA_ROWS": "200", "STATE_PERIOD_S": "0.01",
                                                                  "STATE_CONFIG_FILE": str(tmp_path / "board.json"), "STATE_PAUSE_FILE": str(tmp_path / "pause")})
    try:
        served.wait_up(60)
        end = time.monotonic() + 60
        while time.monotonic() < end:
            state = served.get("/api/v1/state")
            if len(json.dumps(state)) > 200_000:
                break
            time.sleep(0.5)
        with Burners(os.cpu_count() or 4):
            samples = sample_node(served, ["host", ADAPTER, BOARD], 12.0)
        median, p95 = percentile(samples, 0.5), percentile(samples, 0.95)
        print(f"/node under load: n={len(samples)} p50 {median:.0f} ms p95 {p95:.0f} ms")
        assert p95 < TARGET_P95_MS, f"p95 {p95:.0f} ms (p50 {median:.0f} ms, n={len(samples)})"
    finally:
        served.stop()


def test_the_burners_are_stopped_by_pid():
    with Burners(2) as burners:
        pids = [process.pid for process in burners.processes]
        assert all(Path(f"/proc/{pid}").exists() for pid in pids)
    assert not any(Path(f"/proc/{pid}/cmdline").exists() and Path(f"/proc/{pid}/cmdline").read_bytes() for pid in pids)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-s"]))
