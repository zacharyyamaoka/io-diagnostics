"""Runs (docs/runs-v1.md): every real act is a process on a PTY with live output, input, Stop and a recording.

All on the fixture plugin tests/fixtures/plugins-terminal/terminal_plugin.py, a REAL subprocess that prints like a tool
(progress with ``\\r``, a ``Continue? [y/N]`` prompt, records on the record fd) and never touches hardware. The page is
played by urllib with the page's headers; the output stream is read the way the page reads it (SSE, base64 bytes).
Every refusal is checked against the plugin's own log: nothing reached the process.
"""

from __future__ import annotations

import base64
import http.client
import json
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_host_support import FakeOs, SetClock, read_log, standard_skeleton, usb_device

from io_diagnostics.host import Host
from io_diagnostics.server import ActGate, make_server, serve_in_thread

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "tests" / "fixtures" / "plugins-terminal" / "terminal_plugin.py"
ADAPTER = usb_device("9-1", "0483", "5740", "FIX1", label="fdcanusb")
PAGE = {"Content-Type": "application/json", "X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin"}
CAN1 = "can/1"


class Rig:
    """A host with the terminal fixture plugin behind a real server, runs recorded under tmp."""

    def __init__(self, tmp_path: Path, *, act_timeout_s: float = 60, env: dict | None = None, **host_options):
        self.tmp = tmp_path
        self.log = tmp_path / "terminal.jsonl"
        self.runs_dir = tmp_path / "runs"
        self.env = {"TERMINAL_STEP_S": "0.2", **(env or {})}
        (tmp_path / "plugins.d").mkdir(exist_ok=True)
        env_text = ", ".join(f'{key} = "{value}"' for key, value in {"TERMINAL_LOG": str(self.log), **self.env}.items())
        (tmp_path / "plugins.d" / "terminal.toml").write_text(
            f'name = "terminal_fixture"\ncommand = ["{{python}}", "{PLUGIN}"]\ntimeout_s = 20\nperiod_s = 30\n'
            f'act_timeout_s = {act_timeout_s}\nmatches = ["usb:0483:5740"]\nexclusive = "bus"\nenv = {{ {env_text} }}\n')
        self.host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(standard_skeleton(ADAPTER)),
                         runs_dir=self.runs_dir, run_grace_s=0.4, **host_options)
        self.host.run_all_once()
        self.clock = Clock()
        self.http = make_server(self.host, 0, gate=ActGate(window_s=300.0, now=self.clock))
        serve_in_thread(self.http)
        self.url = self.host.url
        self.device = self.host.layer.device_paths["usb:9-1"]
        self.can1 = f"{self.device}/{CAN1}"

    def close(self):
        self.http.shutdown()
        self.host.stop()

    # --- the page's calls -------------------------------------------------------------------------------------------
    def call(self, method: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(self.url.rstrip("/") + path, method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers=(PAGE if headers is None else headers) if method == "POST" else {})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def start(self, action: str = "calibrate", node: int | None = 1, **extra) -> dict:
        """Dry run, then the run with its token, detached: the run's summary."""

        path = self.can1 if node is not None else self.device
        status, dry = self.call("POST", "/api/v1/act", {"path": path, "action": action, "node": node, "dry_run": True})
        assert status == 200 and dry["token"], dry
        status, answer = self.call("POST", "/api/v1/act", {"path": path, "action": action, "node": node, "token": dry["token"],
                                                           "detach": True, **extra})
        assert status == 202 and answer["run"]["id"], answer
        return answer["run"]

    def run(self, run_id: str) -> dict:
        return self.call("GET", f"/api/v1/runs/{run_id}")[1]

    def write(self, run_id: str, op: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
        return self.call("POST", f"/api/v1/runs/{run_id}/{op}", body, headers)

    def wait_ended(self, run_id: str, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.run(run_id)
            if not run["active"]:
                return run
            time.sleep(0.05)
        raise AssertionError(f"run {run_id} still active after {timeout} s: {self.run(run_id)}")

    def log_lines(self, **match) -> list[dict]:
        return [entry for entry in read_log(self.log) if all(entry.get(key) == value for key, value in match.items())]


class Clock:
    def __init__(self):
        self.at = 100.0

    def __call__(self):
        return self.at


class Watcher(threading.Thread):
    """Reads one run's SSE stream the way the page does and keeps what arrived, with arrival times."""

    def __init__(self, rig: Rig, run_id: str, frm: int | None = None):
        super().__init__(daemon=True)
        self.rig, self.run_id, self.frm = rig, run_id, frm
        self.events: list[tuple[float, str, dict]] = []
        self.data = bytearray()
        self.chunks: list[tuple[float, bytes]] = []
        self.end: dict | None = None
        self.hello: dict | None = None
        self.cond = threading.Condition()
        self.start()

    def run(self):
        host = self.rig.url.removeprefix("http://").rstrip("/")
        connection = http.client.HTTPConnection(host, timeout=60)
        query = "" if self.frm is None else f"?from={self.frm}"
        connection.request("GET", f"/api/v1/runs/{self.run_id}/stream{query}")
        response = connection.getresponse()
        name, data = None, []
        while True:
            line = response.fp.readline()
            if not line:
                break
            text = line.decode().rstrip("\r\n")
            if text == "":
                if name:
                    self._take(time.time(), name, json.loads("\n".join(data)))
                name, data = None, []
            elif text.startswith("event:"):
                name = text[6:].strip()
            elif text.startswith("data:"):
                data.append(text[5:].lstrip())
        connection.close()

    def _take(self, at: float, name: str, payload: dict) -> None:
        with self.cond:
            self.events.append((at, name, payload))
            if name == "hello":
                self.hello = payload
            elif name == "output":
                raw = base64.b64decode(payload["b64"])
                self.data.extend(raw)
                self.chunks.append((at, raw))
            elif name == "end":
                self.end = payload["run"]
            self.cond.notify_all()

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")

    def wait_for(self, needle: str, timeout: float = 15.0) -> float:
        """The arrival time of the first chunk after which the output contains ``needle``."""

        deadline = time.monotonic() + timeout
        with self.cond:
            while needle not in self.text:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"{needle!r} never arrived; got {self.text!r}")
                self.cond.wait(left)
            seen = b""
            for at, raw in self.chunks:
                seen += raw
                if needle in seen.decode("utf-8", "replace"):
                    return at
        raise AssertionError("unreachable")

    def wait_end(self, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        with self.cond:
            while self.end is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"the stream never ended; got {self.text!r}")
                self.cond.wait(left)
        return self.end


@pytest.fixture()
def rig(tmp_path):
    made = Rig(tmp_path)
    yield made
    made.close()


def make_rig(tmp_path, **options) -> Rig:
    return Rig(tmp_path, **options)


# ----- live output, input, the plugin's view of the terminal ----------------------------------------------------------
def test_output_arrives_while_the_run_is_going_and_within_a_quarter_second(rig, capsys):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    # WHY the stamps: the plugin prints t=<time.time()> in every progress frame; arrival minus stamp is the latency
    # from print to a page-side reader (same machine, same clock)
    latencies = []
    for arrived, raw in watcher.chunks:
        for stamp in re.findall(rb"t=(\d+\.\d+)", raw):
            latencies.append(arrived - float(stamp))
    assert len(latencies) >= 8, watcher.text
    assert max(latencies) < 0.25, latencies
    first_at = watcher.wait_for("calibrating")
    prompt_at = watcher.wait_for("Continue? [y/N]")
    assert prompt_at - first_at > 1.0  # the progress frames were on the page seconds before the run could end
    assert rig.run(run["id"])["state"] == "running"  # and the run is still going
    assert "\r" in watcher.text  # the \r progress bar is delivered as the tool drew it
    with capsys.disabled():
        print(f"\noutput latency over {len(latencies)} frames: median {statistics.median(latencies) * 1000:.1f} ms,"
              f" max {max(latencies) * 1000:.1f} ms")
    rig.write(run["id"], "input", {"text": "y\n"})
    watcher.wait_end()


def test_the_plugin_runs_on_a_terminal_with_the_record_fd(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    rig.write(run["id"], "input", {"text": "y\n"})
    watcher.wait_end()
    seen = next(entry for entry in rig.log_lines(verb="act") if "tty" in entry and "--dry-run" not in entry["argv"])
    assert seen["tty"] is True and seen["cwd_tty"] is True  # stdin and stdout are the PTY
    assert seen["term"] == "xterm-256color" and seen["run_id"] == run["id"]
    assert int(seen["record_fd"]) > 2  # the record channel is another descriptor


def test_input_reaches_the_prompt_and_the_run_finishes(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    assert rig.run(run["id"])["state"] == "running"  # it is waiting for an answer
    status, answer = rig.write(run["id"], "input", {"text": "y\n"})
    assert (status, answer["ok"], answer["written"]) == (200, True, 2)
    final = watcher.wait_end()
    assert (final["state"], final["exit_code"]) == ("done", 0)
    assert "saved the calibration to CAN 1" in watcher.text
    assert rig.log_lines(answer="y")  # the prompt read exactly what was typed
    detail = rig.run(run["id"])
    assert detail["result"]["outcome"] == "ok"
    assert [record["code"] for record in detail["result"]["records"]] == ["ActionCompleted"]  # from the record fd
    assert "ActionCompleted" not in watcher.text  # and records never reach the terminal
    assert detail["result"]["message"].startswith("ran in ")
    assert not rig.log_lines(stopped=True)  # a clean run needs no stop


def test_an_answer_of_no_fails_the_run_and_a_moves_run_gets_the_plugins_stop(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    rig.write(run["id"], "input", {"text": "n\n"})
    final = watcher.wait_end()
    assert (final["state"], final["exit_code"]) == ("failed", 1)
    assert final["stop"]["plugin_stop"]["ran"] is True  # moves-class and not done: the host sent the plugin's stop
    assert rig.log_lines(stopped=True, node="1")


# ----- Stop ---------------------------------------------------------------------------------------------------------
def test_interrupt_escalates_sigint_sigterm_sigkill(rig):
    run = rig.start("hang", node=None)
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("hanging")
    began = time.monotonic()
    status, answer = rig.write(run["id"], "signal", {"signal": "interrupt"})
    assert (status, answer) == (200, {"ok": True, "stage": "SIGINT"})
    final = watcher.wait_end()
    took = time.monotonic() - began
    assert final["state"] == "stopped" and final["exit_code"] == -9
    # WHY a prefix and a loose upper bound (2026-10-24, failed once under a loaded full-suite run, passes alone): the
    # host's own record (signals, below) is the escalation's proof; whether the hung process gets scheduled to log
    # SIGTERM inside the 0.4 s before SIGKILL, and the wall-clock ceiling, depend on machine load
    got = [entry["got_signal"] for entry in rig.log_lines() if "got_signal" in entry]
    assert got and got == ["SIGINT", "SIGTERM"][: len(got)], got
    assert 0.7 < took < 10.0  # SIGTERM after the 0.4 s grace, SIGKILL after another 0.4 s: never sooner
    assert final["stop"]["signals"] == ["SIGINT", "SIGTERM", "SIGKILL"]
    assert final["stop"]["plugin_stop"]["ran"] is False  # not a moves-class action: only the signals
    assert not rig.log_lines(stopped=True)


def test_stop_on_a_moves_run_runs_the_plugins_own_stop_for_that_board(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    rig.write(run["id"], "signal", {"signal": "interrupt"})
    final = watcher.wait_end()
    assert final["state"] == "stopped" and final["exit_code"] in (130, -2)  # the fixture catches SIGINT like moteus_servo
    plugin_stop = final["stop"]["plugin_stop"]
    assert (plugin_stop["ran"], plugin_stop["outcome"]) == (True, "ok")
    assert plugin_stop["records"][0]["code"] == "StopSent" and "CAN 1 mode 0" in plugin_stop["message"]
    (called,) = rig.log_lines(stopped=True)
    assert called["node"] == "1" and called["record_fd"] is None  # no confirm token, on a pipe: no terminal for a stop
    assert "--confirm" not in called["argv"]
    # it is said in the terminal (and so in the recording), after the process was gone and before the run ended
    assert "stopping CAN 1" in watcher.text and "stop: CAN 1 mode 0 (stopped)" in watcher.text
    assert watcher.text.index("stopping CAN 1") < watcher.text.index("[exit")
    assert rig.run(run["id"])["state"] == "stopped"


def test_a_queued_run_is_cancelled_by_stop_and_never_runs(rig):
    status, dry = rig.call("POST", "/api/v1/act", {"path": rig.can1, "action": "calibrate", "node": 1, "dry_run": True})
    with rig.host.gates.hold("bus"):  # a diagnose, say, holds the rack bus
        status, answer = rig.call("POST", "/api/v1/act", {"path": rig.can1, "action": "calibrate", "node": 1,
                                                          "token": dry["token"], "detach": True})
        assert status == 202
        run = answer["run"]
        assert rig.run(run["id"])["state"] == "queued"
        status, answer = rig.write(run["id"], "signal", {"signal": "interrupt"})
        assert (status, answer["stage"]) == (200, "cancelled")
        assert rig.run(run["id"])["state"] == "stopped"
    time.sleep(0.3)
    assert not rig.log_lines(action="calibrate", verb="act") or all("--dry-run" in e["argv"] for e in rig.log_lines(verb="act"))
    assert rig.run(run["id"])["state"] == "stopped" and rig.run(run["id"])["exit_code"] is None


def test_a_run_past_act_timeout_is_killed_and_a_moves_run_is_stopped(tmp_path):
    rig = make_rig(tmp_path, act_timeout_s=1.5)
    try:
        run = rig.start("calibrate")
        final = rig.wait_ended(run["id"])
        assert (final["state"], final["exit_code"]) == ("timed_out", -9)
        assert final["result"]["outcome"] == "timeout" and final["stop"]["plugin_stop"]["ran"] is True
    finally:
        rig.close()


# ----- the gates ----------------------------------------------------------------------------------------------------
def test_the_writes_reject_anything_but_the_pages_own_request(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    cases = [({"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}, "NotFromPage"),
             ({"Content-Type": "application/json", "X-IO-Diagnostics": "ui"}, "NotSameOrigin"),
             ({**PAGE, "Sec-Fetch-Site": "cross-site"}, "NotSameOrigin"),
             ({**PAGE, "Origin": "http://evil.example"}, "NotSameOrigin"),
             ({**PAGE, "Content-Type": "text/plain"}, "NotJson")]
    for op, body in (("input", {"text": "y\n"}), ("signal", {"signal": "interrupt"}), ("resize", {"cols": 80, "rows": 24})):
        for headers, code in cases:
            status, answer = rig.write(run["id"], op, body, headers)
            assert (status, answer["refused"]) == (403, code), (op, headers, answer)
    time.sleep(0.2)
    assert not rig.log_lines(answer="y") and rig.run(run["id"])["state"] == "running"  # nothing reached the process
    assert rig.run(run["id"])["stop"] is None and (rig.run(run["id"])["cols"], rig.run(run["id"])["rows"]) == (120, 30)
    status, answer = rig.write(run["id"], "input", {"text": "y\n"})  # the page's own request works
    assert status == 200
    watcher.wait_end()


def test_behind_the_stable_proxy_the_page_origin_is_accepted(rig, monkeypatch):
    monkeypatch.setenv("IO_DIAGNOSTICS_PAGE_ORIGINS", "http://127.0.0.1:8820")
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    assert rig.write(run["id"], "input", {"text": "x"}, {**PAGE, "Origin": "http://127.0.0.1:8820"})[0] == 200
    assert rig.write(run["id"], "input", {"text": "\x03"}, {**PAGE, "Origin": "http://127.0.0.1:8821"})[0] == 403
    rig.write(run["id"], "signal", {"signal": "interrupt"})
    watcher.wait_end()


def test_refusals_come_in_the_contracts_order(rig):
    assert rig.write("nope", "input", {"text": "y"})[1]["refused"] == "NoSuchRun"
    assert rig.write("nope", "input", {"text": "y"})[0] == 404
    # a run that was not started from the page in this process takes no input
    run, _out = rig.host.start_act(rig.can1, "calibrate", node=1, confirm="tok-calibrate", interactive=False)
    status, answer = rig.write(run.id, "input", {"text": "y\n"})
    assert (status, answer["refused"]) == (403, "NotInteractive")
    assert rig.write(run.id, "signal", {"signal": "interrupt"})[1]["refused"] == "NotInteractive"
    assert rig.run(run.id)["interactive"] is False
    rig.host.runs.shutdown()  # ending it is the host's to do: it is not the page's run
    assert run.wait(15) and not run.active
    status, answer = rig.write(run.id, "input", {"text": "y\n"})
    assert (status, answer["refused"]) == (403, "NotInteractive")  # not interactive outranks ended
    # a page run that has ended: 409
    page_run = rig.start("calibrate")
    watcher = Watcher(rig, page_run["id"])
    watcher.wait_for("Continue? [y/N]")
    assert rig.write(page_run["id"], "signal", {"signal": "kill"})[1]["refused"] == "BadRequest"
    assert rig.write(page_run["id"], "input", {"text": 5})[1]["refused"] == "BadRequest"
    assert rig.write(page_run["id"], "input", {"text": "x" * 5000})[1]["refused"] == "BadRequest"
    assert rig.write(page_run["id"], "resize", {"cols": 0, "rows": 24})[1]["refused"] == "BadRequest"
    rig.write(page_run["id"], "input", {"text": "y\n"})
    watcher.wait_end()
    status, answer = rig.write(page_run["id"], "input", {"text": "y\n"})
    assert (status, answer["refused"]) == (409, "NotRunning")


def test_resize_reaches_the_terminal(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    assert rig.write(run["id"], "resize", {"cols": 90, "rows": 20})[1] == {"ok": True, "cols": 90, "rows": 20}
    assert (rig.run(run["id"])["cols"], rig.run(run["id"])["rows"]) == (90, 20)
    rig.write(run["id"], "input", {"text": "y\n"})
    watcher.wait_end()


# ----- a run's end is one moment for everyone outside ------------------------------------------------------------------
# Found on the landed instant pages, 2026-11-04, by the page suites under load: a read answers from a published view, so the ending waits for a publish, and in that wait a
# client read `state: done` with `active: true`. The page keeps the stream's `end` as its last word on a run, so a finished run stayed a running one: a spinner, Stop, no cast link.
def _hold_the_ending(rig) -> tuple[threading.Event, threading.Event]:
    """Holds a run inside its ending, where the publish is: deterministic, where a slow machine only sometimes lands in it."""

    reached, release = threading.Event(), threading.Event()
    hook = rig.host.runs.on_end

    def slow_publish(run):
        reached.set()
        assert release.wait(15)
        hook(run)

    rig.host.runs.on_end = slow_publish
    return reached, release


def test_a_client_sees_a_runs_end_as_one_moment(rig):
    reached, release = _hold_the_ending(rig)
    run = rig.start("old", node=None)
    watcher = Watcher(rig, run["id"])
    assert reached.wait(15)
    during = rig.run(run["id"])
    summary = rig.call("GET", "/api/v1/runs")[1]
    try:
        assert (during["state"], during["active"], during["exit_code"], during["ended"]) == ("running", True, None, None) and "result" not in during
        assert (summary["active"], summary["runs"][0]["state"]) == (1, "running")
        time.sleep(0.3)  # time for a stream that was going to say `end` to say it
        assert watcher.end is None
    finally:
        release.set()
    final = watcher.wait_end()
    assert (final["state"], final["active"], final["exit_code"]) == ("done", False, 0) and final["result"]["outcome"] == "ok"
    assert [e["args"]["state"] for e in rig.call("GET", "/api/v1/activity?types=run")[1]["events"] if e["code"] == "RunEnded"] == ["done"]
    assert rig.run(run["id"])["active"] is False


def test_a_run_is_over_on_disk_and_in_the_host_before_clients_are_told(rig):
    reached, release = _hold_the_ending(rig)
    run = rig.start("old", node=None)
    assert reached.wait(15)
    try:
        # WHY the truth on disk at once: a host that dies inside the ending must not read this run back as "the host stopped while it ran"
        meta = json.loads((rig.runs_dir / f"{run['id']}.json").read_text())
        assert (meta["state"], meta["exit_code"], meta["result"]["outcome"]) == ("done", 0, "ok")
        assert rig.host.runs.active() == []  # a next run on this board is not refused by one that is over
    finally:
        release.set()
    rig.wait_ended(run["id"])


def test_the_last_word_on_a_run_is_that_it_is_over(rig):
    told = []
    notify = rig.host.runs.notify

    def listening(reason):
        told.append(rig.host.runs.json()["items"][0]["active"])
        notify(reason)

    rig.host.runs.notify = listening
    run = rig.start("old", node=None)
    rig.wait_ended(run["id"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and told[-1] is not False:
        time.sleep(0.02)
    assert told[-1] is False, told  # WHY: a page re-reads on this, and nothing comes after it


def test_a_run_says_one_duration(rig, monkeypatch):
    # WHY 0.5447: "%.2g" of it is 0.54 and of its round(_, 3) is 0.55, and the message and duration_s were printed from one each, so about one run in twenty disagreed with itself
    monkeypatch.setattr("io_diagnostics.terminal.Run.elapsed", lambda _run: 0.5447)
    status, dry = rig.call("POST", "/api/v1/act", {"path": rig.device, "action": "check_comms", "node": None, "dry_run": True})
    status, answer = rig.call("POST", "/api/v1/act", {"path": rig.device, "action": "check_comms", "node": None, "token": dry["token"]})
    assert status == 200 and (answer["result"]["duration_s"], answer["result"]["message"]) == (0.545, "ran in 0.55 s · 1 lines")


# ----- records leave the terminal, old plugins still work -------------------------------------------------------------
def test_an_old_plugin_that_ignores_the_record_fd_still_works(rig):
    run = rig.start("old", node=None)
    final = rig.wait_ended(run["id"])
    assert (final["state"], final["result"]["outcome"]) == ("done", "ok")
    codes = [record["code"] for record in final["result"]["records"]]
    assert codes == ["ActionCompleted"]  # found among the terminal's lines
    assert final["result"]["rejected"] == []  # the terminal's other text is not a rejected record
    watcher = Watcher(rig, run["id"])
    watcher.wait_end()
    assert "old plugin: asking every id" in watcher.text  # its stdout is the terminal now, and it shows


def test_the_blocking_call_is_what_it_was_and_a_dry_run_is_not_a_run(rig):
    status, dry = rig.call("POST", "/api/v1/act", {"path": rig.device, "action": "check_comms", "node": None, "dry_run": True})
    assert status == 200 and rig.call("GET", "/api/v1/runs")[1]["runs"] == []  # a dry run is not a run
    status, answer = rig.call("POST", "/api/v1/act", {"path": rig.device, "action": "check_comms", "node": None,
                                                      "token": dry["token"]})
    assert status == 200 and answer["dry_run"] is False
    result = answer["result"]
    assert result["outcome"] == "ok" and result["run_id"] and result["returncode"] == 0
    assert result["records"][0]["code"] == "ActionCompleted" and result["message"] == "ran in %.2g s · 1 lines" % result["duration_s"]
    assert len(rig.call("GET", "/api/v1/runs")[1]["runs"]) == 1


def test_host_act_waits_and_a_refusal_starts_no_run(rig):
    refused = rig.host.act(rig.can1, "calibrate", node=1, human=True)
    assert refused["refused"] == "ConfirmRequired" and rig.host.runs.list() == []
    run, out = rig.host.start_act(rig.can1, "calibrate", node=1)
    assert run is None and out["refused"] == "ConfirmRequired"
    status, answer = rig.call("POST", "/api/v1/act", {"path": rig.can1, "action": "calibrate", "node": 1, "token": "made-up",
                                                      "detach": True})
    assert (status, answer["refused"]) == (403, "NoDryRun") and rig.host.runs.list() == []


# ----- the list, the state, activity ---------------------------------------------------------------------------------
def test_runs_in_the_list_the_state_and_the_log(rig):
    run = rig.start("calibrate")
    assert run["label"] == "CAN 1 · Run calibration" and run["danger"] == "moves" and run["node"] == 1
    assert run["interactive"] is True and "<token>" in run["argv"] and "tok-calibrate" not in run["argv"]
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    listing = rig.call("GET", "/api/v1/runs")[1]
    assert [item["id"] for item in listing["runs"]] == [run["id"]] and listing["active"] == 1
    assert listing["limits"]["ring_bytes"] == 2 * 1024 * 1024 and listing["limits"]["input_max"] == 4096
    state = rig.call("GET", "/api/v1/state")[1]["runs"]
    assert state["active"] == 1 and state["items"][0]["id"] == run["id"] and state["items"][0]["state"] == "running"
    assert rig.call("GET", "/api/v1/runs/nope")[0] == 404
    rig.write(run["id"], "input", {"text": "y\n"})
    watcher.wait_end()
    rig.wait_ended(run["id"])
    events = rig.call("GET", "/api/v1/activity?types=run")[1]["events"]
    assert [event["code"] for event in reversed(events)] == ["RunStarted", "RunEnded"]
    ended, started = events
    assert started["args"]["run_id"] == ended["args"]["run_id"] == run["id"]
    assert started["path"] == rig.can1 and started["severity"] == "info" and ended["severity"] == "info"
    assert started["message"] == "Run calibration started on CAN 1"
    assert ended["args"]["state"] == "done" and ended["args"]["exit_code"] == 0 and "done after" in ended["message"]
    assert started["id"] == f"run:{run['id']}:started" and ended["id"] == f"run:{run['id']}:ended"
    assert rig.call("GET", "/api/v1/activity?types=run,usb")[0] == 200


def test_a_stopped_run_ends_with_a_warning_event_that_says_the_plugin_stop_was_sent(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    rig.write(run["id"], "signal", {"signal": "interrupt"})
    watcher.wait_end()
    rig.wait_ended(run["id"])
    ended = next(event for event in rig.call("GET", "/api/v1/activity?types=run")[1]["events"] if event["code"] == "RunEnded")
    assert ended["severity"] == "warn" and "stopped after" in ended["message"] and "the plugin's stop sent (ok)" in ended["message"]
    assert ended["args"]["state"] == "stopped" and ended["args"]["stop"]["plugin_stop"]["ran"] is True


# ----- the recording and the ring -----------------------------------------------------------------------------------
def test_the_cast_file_is_an_asciinema_v2_recording_that_replays_the_stream(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    rig.write(run["id"], "input", {"text": "y\n"})
    watcher.wait_end()
    rig.wait_ended(run["id"])
    path = rig.runs_dir / f"{run['id']}.cast"
    assert path.is_file() and path.parent == rig.runs_dir
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2 and (header["width"], header["height"]) == (120, 30) and header["title"] == "CAN 1 · Run calibration"
    events = [json.loads(line) for line in lines[1:]]
    assert events and all(event[1] == "o" and isinstance(event[0], float) and isinstance(event[2], str) for event in events)
    times = [event[0] for event in events]
    assert times == sorted(times) and times[-1] > 1.0  # real time stamps: the run lasted seconds
    replayed = "".join(event[2] for event in events)
    assert replayed.encode() == bytes(watcher.data)  # replaying the cast reproduces what the page received
    assert "\rcalibrating" in replayed and "Continue? [y/N] y" in replayed.replace("\r\n", "") or "saved the calibration" in replayed
    assert replayed.startswith("\x1b[2m$ ") and "[exit 0 ·" in replayed  # the host's own first and last lines
    served = rig.url.rstrip("/") + f"/api/v1/runs/{run['id']}/cast"
    with urllib.request.urlopen(served) as response:
        assert response.headers["Content-Type"].startswith("application/x-asciicast")
        assert response.read().decode() == path.read_text()


def test_the_stream_replays_the_ring_and_a_resumed_one_starts_where_asked(rig):
    run = rig.start("check_comms", node=None)
    rig.wait_ended(run["id"])
    whole = Watcher(rig, run["id"])
    final = whole.wait_end()
    assert final["state"] == "done" and "line 4" in whole.text and whole.hello["from"] == 0
    cut = len(whole.data) // 2
    resumed = Watcher(rig, run["id"], frm=cut)
    resumed.wait_end()
    assert bytes(resumed.data) == bytes(whole.data)[cut:] and resumed.hello["from"] == cut


def test_the_ring_drops_the_oldest_bytes_and_the_stream_says_where_it_starts(tmp_path):
    rig = make_rig(tmp_path, env={"TERMINAL_LINES": "300", "TERMINAL_LINE_S": "0"}, run_ring_bytes=4096)
    try:
        run = rig.start("check_comms", node=None)
        final = rig.wait_ended(run["id"], timeout=60)
        assert final["dropped_bytes"] > 0 and final["output_bytes"] > 4096
        watcher = Watcher(rig, run["id"])
        watcher.wait_end()
        assert watcher.hello["available_from"] == final["dropped_bytes"] and len(watcher.data) <= 4096 + 100
        assert "line 299" in watcher.text and "line 0 " not in watcher.text
        cast = (rig.runs_dir / f"{run['id']}.cast").read_text()
        assert "line 0 " in cast  # the recording keeps all of it
    finally:
        rig.close()


# ----- persistence --------------------------------------------------------------------------------------------------
def test_finished_runs_persist_and_replay_after_a_restart(rig, tmp_path):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    rig.write(run["id"], "input", {"text": "y\n"})
    watcher.wait_end()
    final = rig.wait_ended(run["id"])
    sent = bytes(watcher.data)
    assert (rig.runs_dir / f"{run['id']}.json").is_file()
    # a stale record of a run that was still going when its host died
    dead = json.loads((rig.runs_dir / f"{run['id']}.json").read_text())
    dead.update(id="20200101T000000-dead", state="running", active=True, ended=None, exit_code=None)
    (rig.runs_dir / "20200101T000000-dead.json").write_text(json.dumps(dead))
    second = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(standard_skeleton(ADAPTER)), runs_dir=rig.runs_dir)
    loaded = second.run_json(run["id"])
    assert loaded["state"] == "done" and loaded["interactive"] is False and loaded["result"]["records"][0]["code"] == "ActionCompleted"
    assert loaded["elapsed_s"] == pytest.approx(final["elapsed_s"], abs=0.01)
    gone = second.run_json("20200101T000000-dead")
    assert gone["state"] == "failed" and gone["result"]["message"] == "the host stopped while it ran"
    again = make_server(second, 0)
    serve_in_thread(again)
    try:
        host = second.url.removeprefix("http://").rstrip("/")
        connection = http.client.HTTPConnection(host, timeout=20)
        connection.request("GET", f"/api/v1/runs/{run['id']}/stream")
        body = connection.getresponse().read().decode()
        data = b"".join(base64.b64decode(json.loads(line[5:])["b64"]) for line in body.splitlines()
                        if line.startswith("data:") and '"b64"' in line)
        assert data == sent and "event: end" in body
    finally:
        again.shutdown()


def test_old_runs_are_pruned_past_the_size_budget_and_an_active_one_never(tmp_path):
    rig = make_rig(tmp_path, run_budget_bytes=6000)
    try:
        ids = []
        for _ in range(4):
            run = rig.start("check_comms", node=None)
            rig.wait_ended(run["id"])
            ids.append(run["id"])
            time.sleep(1.1)  # ids sort by start second
        left = [item["id"] for item in rig.call("GET", "/api/v1/runs")[1]["runs"]]
        assert ids[-1] in left and ids[0] not in left and len(left) < 4
        assert not (rig.runs_dir / f"{ids[0]}.cast").exists() and not (rig.runs_dir / f"{ids[0]}.json").exists()
        total = sum(path.stat().st_size for path in rig.runs_dir.iterdir())
        assert total <= 6000 + 3000
    finally:
        rig.close()


def test_a_dry_run_on_a_busy_bus_is_refused_at_once_not_left_waiting(rig):
    run = rig.start("calibrate")
    watcher = Watcher(rig, run["id"])
    watcher.wait_for("Continue? [y/N]")
    began = time.monotonic()
    status, answer = rig.call("POST", "/api/v1/act", {"path": rig.device, "action": "check_comms", "node": None, "dry_run": True})
    assert status == 200 and answer["result"]["refused"] == "Busy" and answer["token"] is None
    assert "CAN 1 · Run calibration is running on the same bus" in answer["result"]["message"]
    assert time.monotonic() - began < 2.0  # it did not wait for the run
    rig.write(run["id"], "input", {"text": "y\n"})
    watcher.wait_end()


# ----- a real act waits its turn for the rack bus lock ----------------------------------------------------------------
class HeldLock:
    """Another process's hold on the rack bus lock (a routine diagnose, an experiment): flock on the file the fixture
    plugin's bus gate takes, with the holder named in <lock>.holder."""

    def __init__(self, path: Path, holder: str = "356724 moteus_servo.diagnostics"):
        import fcntl

        self.fcntl, self.path = fcntl, path
        path.with_name(path.name + ".holder").write_text(holder)
        self.handle = open(path, "a")
        self.fcntl.flock(self.handle, self.fcntl.LOCK_EX | self.fcntl.LOCK_NB)

    def release(self):
        if self.handle:
            self.fcntl.flock(self.handle, self.fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def bus_rig(tmp_path, **options) -> tuple[Rig, Path]:
    lock = tmp_path / "bus.lock"
    rig = make_rig(tmp_path, env={"TERMINAL_BUS_LOCK": str(lock), "TERMINAL_STEP_S": "0.05"}, run_bus_poll_s=0.2, **options)
    return rig, lock


def test_a_real_act_waits_for_a_held_bus_lock_and_then_runs(tmp_path):
    rig, lock = bus_rig(tmp_path)
    held = HeldLock(lock)
    try:
        run = rig.start("calibrate")
        watcher = Watcher(rig, run["id"])
        watcher.wait_for("waiting for the bus: held by moteus_servo.diagnostics (pid 356724) · checking usb:9-1")
        assert rig.run(run["id"])["state"] == "waiting" and rig.run(run["id"])["active"] is True
        # WHY release only after the host has asked again and been refused: a 2 s timer started before the run raced the
        # plugin's own start under a loaded full-suite run (the lock was free before the act ever saw it held)
        deadline = time.monotonic() + 15
        while len(rig.log_lines(blocked=True)) < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        held.release()
        watcher.wait_for("Continue? [y/N]", timeout=15)  # the lock was released: the act went through the gates
        rig.write(run["id"], "input", {"text": "y\n"})
        final = watcher.wait_end()
        assert (final["state"], final["exit_code"]) == ("done", 0) and final["result"]["records"][0]["code"] == "ActionCompleted"
        assert "the bus is free" in watcher.text
        blocked_starts = [entry for entry in rig.log_lines(blocked=True)]
        assert len(blocked_starts) >= 2  # it asked again while the lock was held
        assert not rig.log_lines(stopped=True)  # nothing was stopped: it ran to the end
        assert [e["args"]["state"] for e in rig.call("GET", "/api/v1/activity?types=run")[1]["events"] if e["code"] == "RunEnded"] == ["done"]
    finally:
        held.release()
        rig.close()


def test_a_lock_held_past_the_bound_refuses_with_the_holder_named_and_never_stops_the_board(tmp_path):
    rig, lock = bus_rig(tmp_path, run_bus_wait_s=1.5)
    held = HeldLock(lock)
    try:
        run = rig.start("calibrate")
        watcher = Watcher(rig, run["id"])
        final = watcher.wait_end(timeout=20)
        assert (final["state"], final["result"]["outcome"]) == ("failed", "blocked")
        assert "rack bus lock held by 356724 moteus_servo.diagnostics" in final["result"]["message"]
        assert "waited" in final["result"]["message"]
        assert "waiting for the bus: held by moteus_servo.diagnostics (pid 356724)" in watcher.text
        assert not rig.log_lines(stopped=True)  # a calibration that never started needs no stop, even a moves one
        assert final["stop"] is None
        ended = next(e for e in rig.call("GET", "/api/v1/activity?types=run")[1]["events"] if e["code"] == "RunEnded")
        assert ended["severity"] == "error" and ended["args"]["state"] == "failed"
    finally:
        held.release()
        rig.close()


def test_stop_cancels_the_wait_for_the_bus(tmp_path):
    rig, lock = bus_rig(tmp_path)
    held = HeldLock(lock)
    try:
        run = rig.start("calibrate")
        watcher = Watcher(rig, run["id"])
        watcher.wait_for("waiting for the bus")
        status, answer = rig.write(run["id"], "signal", {"signal": "interrupt"})
        assert status == 200
        final = watcher.wait_end(timeout=10)
        assert final["state"] == "stopped" and final["result"]["outcome"] == "stopped"
        assert not rig.log_lines(stopped=True) and "never got the bus" in final["stop"]["plugin_stop"]["reason"]
    finally:
        held.release()
        rig.close()


def test_a_waiting_act_keeps_scheduled_checks_of_its_group_from_starting():
    from io_diagnostics.runner import Gates

    gates = Gates()
    order, release_routine = [], threading.Event()

    def routine_running():
        with gates.hold("bus"):  # a diagnose already going
            order.append("routine start")
            release_routine.wait(5)

    def person():
        with gates.hold("bus", person=True):
            order.append("person")

    def later_routine():
        with gates.hold("bus"):
            order.append("later routine")

    first = threading.Thread(target=routine_running)
    first.start()
    time.sleep(0.2)
    waiting = threading.Thread(target=person)
    waiting.start()
    time.sleep(0.2)
    queued = threading.Thread(target=later_routine)
    queued.start()
    time.sleep(0.2)
    assert gates.person_waiting("bus") and gates.try_take("bus") is False  # the scheduler's non-blocking take holds back
    release_routine.set()
    for thread in (first, waiting, queued):
        thread.join(10)
    assert order == ["routine start", "person", "later routine"]  # the person went before the routine that queued after it
    assert not gates.person_waiting("bus") and gates.try_take("bus") is True
    gates.release("bus")


# ----- Open console (tview): the row says so while the console RobotDx launched is open -------------------------------
def test_while_the_launched_console_is_open_the_adapters_row_says_so(tmp_path):
    import os
    import signal

    rig = make_rig(tmp_path, env={"TERMINAL_CONSOLE_S": "60"})
    try:
        run = rig.start("open_console", node=None)
        assert run["label"] == "fdcanusb fixture · Open console (tview)" and run["danger"] == "interrupts"
        # one name at both scopes: the board's row offers it too, and its run carries --node
        board = rig.call("GET", f"/api/v1/node?path={rig.can1}")[1]["actions"]
        assert [a["scope"] for a in board if a["name"] == "open_console"] == ["node"]

        final = rig.wait_ended(run["id"])
        assert final["state"] == "done"
        pid = final["result"]["records"][0]["args"]["pid"]  # the process the run launched (a stand-in for tview)
        assert pid in {record["args"]["pid"] for record in final["result"]["records"]}

        def console_check():
            checks = rig.call("GET", f"/api/v1/node?path={rig.device}")[1]["checks"]
            return next((check for check in checks if check["code"] == "ConsoleOpen"), None)

        check = console_check()
        assert check and check["message"] == f"tview open (launched from RobotDx, pid {pid})" and check["level"] == "unknown"
        assert check["name"] == "fdcanusb fixture: console"
        os.kill(pid, signal.SIGKILL)  # the person closes it
        deadline = time.monotonic() + 10
        while console_check() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert console_check() is None  # and the row stops saying it
        on_board = rig.start("open_console", node=1)  # on one board: the same name at node scope, --node on its argv
        assert on_board["label"] == "CAN 1 · Open console (tview)" and on_board["node"] == 1 and "--node" in on_board["argv"]
        finished = rig.wait_ended(on_board["id"])
        board_pid = finished["result"]["records"][0]["args"]["pid"]
        assert console_check()["message"] == f"tview open (launched from RobotDx, pid {board_pid})"  # the adapter's row says it too
        os.kill(board_pid, signal.SIGKILL)
    finally:
        rig.close()
