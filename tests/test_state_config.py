"""The entity page's host side (docs/state-config-v1.md): the state / config / frame records, State and Configuration as the
host serves them, the watch heartbeat's lifecycle (start, stop after the grace period, pause on a holder, resume), the
frame endpoints and the state / health split. Every plugin here is tests/fixtures/plugins-state/state_plugin.py, a real
subprocess that never touches hardware."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from contract.record import (Config, DiagnosticStatus, Frame, Measurement, State, from_dict, to_dict, validate)
from io_diagnostics import entity_state, tree
from io_diagnostics.host import Clock, Host
from io_diagnostics.runner import JobCache, RunOutcome, parse_lines
from io_diagnostics.server import make_server, serve_in_thread

from test_host_support import FakeOs, standard_skeleton, usb_device

PLUGIN = Path(__file__).resolve().parent / "fixtures" / "plugins-state" / "state_plugin.py"
JOINT = ("0483", "5740")
CAMERA = ("8086", "0b5b")
PAGE = {"X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}


def wait_for(check, *, timeout=8.0, what="the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[-1].split()[:1] != ["Z"]


class Rig:
    """A host over a fake OS with the state fixture plugin on one device; ``path(rel)`` is an entity's tree path."""

    def __init__(self, tmp_path: Path, role: str = "joint", *, gates=None, env: dict | None = None, grace_s: float = 1.0,
                 plugin_extra: str = ""):
        vid, pid = JOINT if role == "joint" else CAMERA
        self.log = tmp_path / "state.log"
        directory = tmp_path / "plugins.d"
        directory.mkdir()
        environment = {"STATE_LOG": str(self.log), "STATE_PERIOD_S": "0.05", "STATE_FRAME_PERIOD_S": "0.1", **(env or {})}
        (directory / f"state_{role}.toml").write_text(
            f'name = "state_{role}"\ncommand = ["{{python}}", "{PLUGIN}", "--role", "{role}"]\ntimeout_s = 10\nperiod_s = 30\n'
            f'matches = ["usb:{vid}:{pid}"]\n{plugin_extra}env = {{ {", ".join(f"{k} = {json.dumps(v)}" for k, v in environment.items())} }}\n')
        self.os = FakeOs(standard_skeleton(usb_device("9-1", vid, pid, "SER1", label="thing")), gates or {})
        self.host = Host(plugins_dir=directory, clock=Clock(), os_source=self.os, watch_grace_s=grace_s, watch_tick_s=0.1,
                         follow_backoff_s=(0.2, 0.4), frames_dir=tmp_path / "frames")
        self.host.run_all_once()
        self.device = self.host.layer.device_paths["usb:9-1"]
        self.role = role

    def path(self, rel: str = "") -> str:
        return f"{self.device}/{rel}" if rel else self.device

    @property
    def board(self) -> str:
        return self.path("can/1")

    def verbs(self, verb: str) -> list[dict]:
        if not self.log.exists():
            return []
        return [row for row in map(json.loads, self.log.read_text().splitlines()) if row["verb"] == verb]

    def stop(self) -> None:
        self.host.stop()


@pytest.fixture
def joint(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.stop()


@pytest.fixture
def camera(tmp_path):
    rig = Rig(tmp_path, "camera")
    yield rig
    rig.stop()


# ----- the records ------------------------------------------------------------------------------------------------
def test_state_config_and_frame_records_round_trip_and_state_has_no_level():
    state = State(path="can/1", name="motor", stamp=5.0, expected_period_s=0.1,
                  measurements=(Measurement("fault", 33, meaning="motor driver fault on bridge enable"),
                                Measurement("temperature", 41.5, "Cel", warn_high=60.0)))
    line = to_dict(state)
    assert line["record"] == "state" and "level" not in line and "code" not in line
    assert line["measurements"][0]["meaning"] == "motor driver fault on bridge enable"
    assert from_dict(line) == state and validate(state) == []
    config = Config(path="can/1", key="servo.max_current_A", value=20.0, unit="A", expected=25.0, expected_source="deployment record")
    assert from_dict(to_dict(config)) == config and validate(config) == []
    frame = Frame(path="", stream="color", file="color.png", format="png", width=160, height=120, seq=3)
    assert from_dict(to_dict(frame)) == frame and validate(frame) == []


def test_the_validator_names_what_is_wrong_with_the_new_records():
    assert any("plain file name" in problem for problem in validate(Frame(stream="color", file="../etc/passwd")))
    assert any("plain file name" in problem for problem in validate(Frame(stream="color", file="")))
    assert any("format" in problem for problem in validate(Frame(stream="color", file="a.bmp", format="bmp")))
    assert any("JSON scalar" in problem for problem in validate(Config(key="k", value={"a": 1})))
    assert any("expected_source" in problem for problem in validate(Config(key="k", value=1, expected_source="x")))
    assert any("appears 2 times" in problem for problem in validate(State(measurements=(Measurement("a", 1), Measurement("a", 2)))))
    assert any("finite" in problem for problem in validate(State(measurements=(Measurement("a", float("inf")),))))


def test_a_state_line_is_parsed_and_a_fault_still_is_not():
    text = "\n".join([json.dumps({"record": "state", "path": "", "name": "m", "measurements": [{"key": "v", "value": 1}]}),
                      json.dumps({"record": "config", "path": "", "key": "k", "value": 2}),
                      json.dumps({"record": "frame", "path": "", "stream": "s", "file": "s.png"}),
                      json.dumps({"record": "fault", "path": "", "code": "X"})])
    parsed = parse_lines(text)
    assert [type(record) for record in parsed.records] == [State, Config, Frame]
    assert len(parsed.rejected) == 1 and "FaultManager" in parsed.rejected[0][1]


# ----- the job cache ----------------------------------------------------------------------------------------------
def outcome(*records, plugin="p") -> RunOutcome:
    return RunOutcome(plugin=plugin, device_id="d", device_path="host/d", started=0.0, duration_s=0.0, outcome="ok", returncode=None,
                      records=list(records), received={}, icons={}, rejected=[], problems=[], stderr_tail="", message="")


def test_the_cache_keeps_the_latest_state_numbers_every_point_and_who_was_heard():
    cache = JobCache()
    for at, value in ((10.0, 1.0), (10.1, 2.0), (10.2, 3.0)):
        cache.apply(outcome(State(path="can/1", name="motor", measurements=(Measurement("position", value, "rev"),))), received=at)
    assert cache.states[("can/1", "motor")].measurements[0].value == 3.0
    assert [point[1] for point in cache.series[("can/1", "motor", "position")]] == [1.0, 2.0, 3.0]
    assert cache.heard("can/1") == 10.2 and cache.heard("can/2") is None
    rows = entity_state.state_groups(cache, "can/1", now=12.0, since=0)[0]["rows"]
    assert rows[0]["key"] == "position" and len(rows[0]["points"]) == 3
    newer = entity_state.state_groups(cache, "can/1", now=12.0, since=cache.series[("can/1", "motor", "position")][0][2])[0]["rows"][0]
    assert [point[1] for point in newer["points"]] == [2.0, 3.0]  # only what is newer than the cursor


def test_a_status_measurements_are_lifted_into_state_but_the_hosts_own_are_not():
    cache = JobCache()
    cache.apply(outcome(DiagnosticStatus(level="ok", name="CPU: load", path="", source="computer",
                                         measurements=(Measurement("load", 0.4),)),
                        DiagnosticStatus(level="ok", name="p: plugin run", path="", source="io_diagnostics.runner",
                                         measurements=(Measurement("duration", 1.0, "s"),))), received=5.0)
    groups = entity_state.state_groups(cache, "", now=5.0)
    assert [(group["name"], group["kind"]) for group in groups] == [("CPU: load", "status")]


def test_a_state_group_is_stale_after_its_promise_and_said_so_never_a_level():
    cache = JobCache()
    cache.apply(outcome(State(path="", name="m", expected_period_s=1.0, measurements=(Measurement("v", 1),))), received=100.0)
    fresh, late = (entity_state.state_groups(cache, "", now=now)[0] for now in (102.0, 130.0))
    assert fresh["stale"] is False and late["stale"] is True and late["age_s"] == 30.0
    assert "level" not in late  # a group makes no health claim


def test_configuration_marks_a_value_that_differs_from_what_is_expected():
    cache = JobCache()
    cache.apply(outcome(Config(path="b", key="a", value=20.0, expected=25.0, expected_source="record"),
                        Config(path="b", key="b", value=0.1 + 0.2, expected=0.3),
                        Config(path="b", key="c", value="pwm", expected="pwm"), Config(path="b", key="d", value=1),
                        Config(path="b", key="e", value=True, expected=1)), received=7.0)
    rows = {row["key"]: row for row in entity_state.config_rows(cache, "b", now=9.0)}
    assert rows["a"]["differs"] and rows["a"]["expected"] == 25.0 and rows["a"]["expected_source"] == "record"
    assert not rows["b"]["differs"] and not rows["c"]["differs"] and not rows["d"]["differs"]  # float noise; equal text; no expectation
    assert rows["e"]["differs"]  # a bool is not the number 1
    assert rows["a"]["stamp"] == 7.0 and rows["a"]["age_s"] == 2.0


def test_a_holder_gate_reads_in_plain_words():
    assert entity_state.holder_reason("port_free", "port held by tview pid 640623")["reason"] == "tview has the port (pid 640623)"
    assert entity_state.holder_reason("camera_free", "open by 2300632 realsense-viewer")["reason"] == "realsense-viewer has the camera (pid 2300632)"
    assert entity_state.holder_reason("bus_lock_free", "blocked by pid 77 (moteus_tool)")["reason"] == "moteus_tool has the rack bus lock (pid 77)"
    assert entity_state.holder_reason("port_free", "no idea")["holders"] == []


# ----- USB: the host sends the link, the page names it (web/usb.js, the one mapping) ----------------------------------
def test_the_badge_carries_the_usb_link_raw_and_the_page_names_it(joint):
    badge = joint.host.node(joint.device)["badge"]
    assert badge == {"kind": "usb", "text": "", "usb": {"speed_mbps": 12, "version": None}}
    assert joint.host.node(joint.board)["badge"]["usb"] == {"speed_mbps": 12, "version": None}


# ----- the node: badge, heard, configuration ----------------------------------------------------------------------
def test_the_node_carries_badge_configuration_and_the_watch_declaration(joint):
    node = joint.host.node(joint.board)
    assert node["badge"]["text"] == "CAN 1 · fdcanusb fixture" and node["badge"]["usb"]["speed_mbps"] == 12
    assert node["watch"]["supported"] and node["watch"]["state"] == "idle" and node["watch"]["gates"] == ["port_free"]
    by_key = {row["key"]: row for row in node["config"]}
    assert by_key["servo.max_current_A"]["differs"] and not by_key["servo.pid_position.kp"]["differs"]
    assert by_key["servo.default_timeout_s"]["expected_source"].startswith("deployment record")
    assert node["heard"]["age_s"] is not None
    device = joint.host.node(joint.device)
    assert device["badge"]["kind"] == "usb" and device["badge"]["usb"]["speed_mbps"] == 12


def test_the_state_and_the_health_are_two_things_on_one_entity(joint):
    node = joint.host.node(joint.board)
    names = {check["name"] for check in node["checks"]}
    assert "CAN 1: driver" in names and "motor" not in names  # State is not a check: no level, no row in Diagnostics
    values = joint.host.values_json(joint.board)
    assert [group["name"] for group in values["groups"]] == ["motor"]
    assert all("level" not in group for group in values["groups"])
    keys = [row["key"] for row in values["groups"][0]["rows"]]
    assert keys == ["position", "velocity", "torque", "voltage", "temperature", "mode", "fault"]
    fault = values["groups"][0]["rows"][-1]
    assert fault["value"] == 33 and fault["meaning"] == "motor driver fault on bridge enable"
    # the health side keeps its code's meaning as values, and nothing numeric
    driver = next(check for check in node["checks"] if check["name"] == "CAN 1: driver")
    assert {value["key"]: value["value"] for value in driver["values"]}["fault_meaning"] == "motor driver fault on bridge enable"
    assert driver["measurements"] == []


def test_a_state_series_is_history_but_never_a_health_bar(joint):
    bar = joint.host.bar_json(joint.board)
    own = next(row for row in bar["rows"] if row["depth"] == 0)
    assert own["statuses"] == 3 and own["basis"] == "own"  # comms, driver, firmware: the state series is not counted
    history = joint.host.history_json(joint.board)
    state = [status for status in history["statuses"] if status["name"] == "motor"]
    assert len(state) == 1 and state[0]["state"] is True and state[0]["levels"][0]["level"] is None
    assert {m["key"] for m in state[0]["measurements"]} >= {"position", "temperature"}


# ----- the watch: heartbeat, grace, gates -------------------------------------------------------------------------
def test_a_heartbeat_starts_the_watch_with_the_entity_path_and_it_stops_after_the_grace_period(joint):
    answer = joint.host.watch_heartbeat(joint.board)["watch"]
    assert answer["supported"] and answer["state"] in ("starting", "running")
    running = wait_for(lambda: (state := joint.host.watch_json(*joint.host._watch_target(joint.board)))["state"] == "running" and state,
                       what="the watch running")
    pid = running["pid"]
    assert alive(pid)
    [started] = joint.verbs("watch")
    assert started["path"] == "can/1" and started["frame_dir"] and Path(started["frame_dir"]).is_dir()
    before = joint.host.values_json(joint.board)["cursor"]
    wait_for(lambda: joint.host.values_json(joint.board)["cursor"] > before + 5, what="live points")
    # no more heartbeats: the process is stopped about `grace` after the last one, and not before
    time.sleep(0.5)
    assert alive(pid) and joint.host.watches
    wait_for(lambda: not joint.host.watches and not alive(pid), what="the watch stopping", timeout=5.0)
    assert joint.host.watch_json(*joint.host._watch_target(joint.board))["state"] == "idle"


def test_heartbeats_keep_one_watch_alive_and_never_start_a_second(joint):
    joint.host.watch_heartbeat(joint.board)
    wait_for(lambda: joint.verbs("watch"), what="the watch")
    for _ in range(6):
        time.sleep(0.4)
        joint.host.watch_heartbeat(joint.board)
    assert len(joint.verbs("watch")) == 1 and len(joint.host.watches) == 1
    joint.host.watch_heartbeat(joint.device)  # another entity of the same device is another watch
    wait_for(lambda: len(joint.verbs("watch")) == 2, what="the device's own watch")
    assert {row["path"] for row in joint.verbs("watch")} == {"can/1", ""}


def test_a_holder_pauses_the_watch_in_plain_words_and_clearing_it_resumes(tmp_path):
    rig = Rig(tmp_path, gates={"port_free": (False, "port held by tview pid 640623")})
    try:
        answer = rig.host.watch_heartbeat(rig.board)["watch"]
        assert answer["state"] == "paused" and answer["reason"] == "tview has the port (pid 640623)"
        assert answer["holders"] == [{"pid": 640623, "program": "tview"}]
        time.sleep(0.5)
        assert rig.verbs("watch") == []  # nothing ran while the port was held
        for _ in range(8):  # the page keeps asking
            rig.host.watch_heartbeat(rig.board)
            time.sleep(0.05)
        rig.os.gates["port_free"] = (True, "no holder")
        resumed = wait_for(lambda: rig.host.watch_heartbeat(rig.board)["watch"]["state"] == "running" and rig.host.watch_json(
            *rig.host._watch_target(rig.board)), what="the watch resuming")
        assert resumed["reason"] == "" and len(rig.verbs("watch")) == 1
        rig.os.gates["port_free"] = (False, "port held by tview pid 640623")  # a holder arrives while it runs: it is stopped
        pid = resumed["pid"]
        wait_for(lambda: (rig.host.watch_heartbeat(rig.board), rig.host.watch_json(*rig.host._watch_target(rig.board))["state"] == "paused")[1],
                 what="pausing again")
        wait_for(lambda: not alive(pid), what="the process stopped")
    finally:
        rig.stop()


def test_an_active_run_of_the_same_bus_pauses_the_watch(tmp_path, monkeypatch):
    rig = Rig(tmp_path, plugin_extra='exclusive = "rack-bus"\n')
    try:
        class Spec:
            group, label = "rack-bus", "CAN 1 · Run calibration"

        class FakeRun:
            spec = Spec()

        monkeypatch.setattr(rig.host.runs, "active", lambda: [FakeRun()])
        answer = rig.host.watch_heartbeat(rig.board)["watch"]
        assert answer["state"] == "paused" and answer["reason"] == "CAN 1 · Run calibration is running" and rig.verbs("watch") == []
    finally:
        monkeypatch.undo()
        rig.stop()


def test_a_plugin_that_finds_the_port_held_itself_pauses_the_watch_with_its_holders(tmp_path):
    rig = Rig(tmp_path, env={"STATE_HOLD_PORT": "1"})
    try:
        rig.host.watch_heartbeat(rig.board)
        paused = wait_for(lambda: (state := rig.host.watch_json(*rig.host._watch_target(rig.board)))["state"] == "paused" and state,
                          what="the plugin's own pause")
        assert paused["holders"][0]["pid"] == 640623 and "tview" in paused["reason"]
    finally:
        rig.stop()


def test_only_a_query_watch_that_a_plugin_described_runs(tmp_path):
    rig = Rig(tmp_path)
    try:
        rig.host.describes["state_joint"]["watch"]["danger"] = "moves"
        answer = rig.host.watch_heartbeat(rig.board)["watch"]
        assert answer["supported"] is False and "query-only" in answer["reason"] and rig.verbs("watch") == []
        rig.host.describes["state_joint"].pop("watch")
        assert rig.host.watch_heartbeat(rig.board)["watch"]["state"] == "unsupported"
        assert rig.host.watch_heartbeat("host/nowhere")["watch"]["supported"] is False
        assert rig.host.values_json("host/nowhere") is None
    finally:
        rig.stop()


def test_the_watch_never_runs_on_a_schedule(joint):
    time.sleep(1.0)
    assert joint.verbs("watch") == [] and not joint.host.watches
    assert all(job.spec.name == "state_joint" for job in joint.host.scheduled_jobs())  # the diagnose is the only scheduled verb


# ----- image state ------------------------------------------------------------------------------------------------
def test_a_camera_streams_png_frames_into_the_named_directory_and_the_host_serves_the_latest(camera):
    node = camera.host.node(camera.device)
    assert [stream["name"] for stream in node["streams"]] == ["color", "depth"] and node["streams"][1]["colormap"] == "turbo"
    assert camera.host.frame_file(camera.device, "color") is None  # nothing before a watch
    camera.host.watch_heartbeat(camera.device)
    data, meta = wait_for(lambda: camera.host.frame_file(camera.device, "depth"), what="a depth frame")
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and meta["width"] == 160 and meta["height"] == 120 and meta["format"] == "png"
    first = camera.host.frame_seq(camera.device, "color")
    wait_for(lambda: camera.host.frame_seq(camera.device, "color") > first + 2, what="newer frames")
    streams = {stream["name"]: stream for stream in camera.host.values_json(camera.device)["streams"]}
    assert streams["color"]["width"] == 160 and streams["color"]["fps"] and streams["color"]["fps"] > 1
    [started] = camera.verbs("watch")
    assert Path(started["frame_dir"]).parent == camera.host._frames_base()


def test_a_frame_names_a_plain_file_inside_its_directory_or_nothing_is_read(camera, tmp_path):
    camera.host.watch_heartbeat(camera.device)
    wait_for(lambda: camera.host.frame_file(camera.device, "color"), what="a frame")
    secret = tmp_path / "secret.txt"
    secret.write_text("not an image")
    job = next(iter(camera.host.jobs.values()))
    for name in ("../../secret.txt", str(secret), "..", "nothing.png"):
        job.cache.frames[("", "color")] = Frame(path="", stream="color", file=name, seq=99)
        assert camera.host.frame_file(camera.device, "color") is None, name


# ----- the endpoints ----------------------------------------------------------------------------------------------
@pytest.fixture
def served(camera):
    server = make_server(camera.host, 0)
    serve_in_thread(server)
    yield camera, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def fetch(url, *, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_the_watch_heartbeat_is_the_pages_alone(served):
    camera, base = served
    body = json.dumps({"path": camera.device}).encode()
    status, _headers, payload = fetch(f"{base}/api/v1/watch", data=body, headers={"Content-Type": "application/json"})
    assert status == 403 and json.loads(payload)["refused"] == "NotFromPage" and camera.verbs("watch") == []
    status, _headers, payload = fetch(f"{base}/api/v1/watch", data=body, headers=PAGE)
    assert status == 200 and json.loads(payload)["watch"]["state"] in ("starting", "running")
    status, _headers, payload = fetch(f"{base}/api/v1/watch", data=b'{"path": 3}', headers=PAGE)
    assert status == 400
    assert fetch(f"{base}/api/v1/watch")[0] == 405  # a GET is not a heartbeat


def test_values_and_frames_endpoints(served):
    camera, base = served
    fetch(f"{base}/api/v1/watch", data=json.dumps({"path": camera.device}).encode(), headers=PAGE)
    status, _h, payload = wait_for(lambda: (r := fetch(f"{base}/api/v1/frames?path={camera.device}&stream=color"))[0] == 200 and r,
                                   what="a frame over HTTP")
    assert payload[:4] == b"\x89PNG" and int(_h["X-Frame-Seq"]) >= 1 and _h["Content-Type"] == "image/png" and _h["X-Frame-Width"] == "160"
    assert fetch(f"{base}/api/v1/frames?path={camera.device}&stream=nope")[0] == 404
    status, _h, payload = fetch(f"{base}/api/v1/values?path={camera.device}")
    values = json.loads(payload)
    assert status == 200 and values["watch"]["supported"] and [s["name"] for s in values["streams"]] == ["color", "depth"]
    assert fetch(f"{base}/api/v1/values?path={camera.device}&since=x")[0] == 400
    assert fetch(f"{base}/api/v1/values?path=host/none")[0] == 404
    cursor = values["cursor"]
    time.sleep(0.5)
    newer = json.loads(fetch(f"{base}/api/v1/values?path={camera.device}&since={cursor}")[2])
    assert newer["cursor"] > cursor and all(len(row["points"]) <= 40 for group in newer["groups"] for row in group["rows"])


def test_the_mjpeg_stream_sends_each_new_frame_once(served):
    camera, base = served
    fetch(f"{base}/api/v1/watch", data=json.dumps({"path": camera.device}).encode(), headers=PAGE)
    wait_for(lambda: camera.host.frame_file(camera.device, "color"), what="a frame")
    with urllib.request.urlopen(f"{base}/api/v1/frames/stream?path={camera.device}&stream=color&fps=10", timeout=10) as response:
        assert response.headers["Content-Type"].startswith("multipart/x-mixed-replace; boundary=ioframe")
        seen = []
        buffer = b""
        while len(seen) < 3:
            buffer += response.read(2048)
            while b"X-Frame-Seq: " in buffer:
                head, _, buffer = buffer.partition(b"X-Frame-Seq: ")
                number, _, buffer = buffer.partition(b"\r\n")
                seen.append(int(number))
    assert seen == sorted(set(seen)) and len(seen) == 3  # never the same frame twice, in order


def test_the_unsupported_page_still_gets_a_node(served):
    camera, base = served
    node = json.loads(fetch(f"{base}/api/v1/node?path=host")[2])
    assert node["watch"]["supported"] is False and node["config"] == [] and node["streams"] == [] and node["tabs"] == []
