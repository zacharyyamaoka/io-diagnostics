"""The host runs robotdx.diagnostics (io_diagnostics/stock_diagnostics.py): which stock diagnostic runs on which entity from
``<config dir>/diagnostics.toml`` (else the default set), evaluated on a slow beat over the entity's State history, each Result an
ordinary record-v0.2 status on the entity, in the Diagnostics tab's checks, /checks, /history and the history bar. not_run is never
OK, a diagnostic that raises is a not_run with its reason, and numpy missing is a not_run that says so. Every plugin here is the
state fixture (tests/fixtures/plugins-state/state_plugin.py), a real subprocess that never touches hardware."""

from __future__ import annotations

import json
import os
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

from contract.record import validate
from io_diagnostics import stock_diagnostics as sd
from io_diagnostics.host import Clock, Host
from robotdx.diagnostics import registry as dx_registry
from robotdx.diagnostics.core import Limits
from robotdx.diagnostics.registry import Spec

from test_host_support import FakeOs, standard_skeleton, usb_device

PLUGIN = Path(__file__).resolve().parent / "fixtures" / "plugins-state" / "state_plugin.py"
JOINT = ("0483", "5740")
FAST = """
[[diagnostic]]
id = "driver.board_temp"
diagnostic = "stock.box"
title = "board temperature"
model_id = "mjbots-moteus-*"
state = "motor"
window_s = 2

[diagnostic.config]
channel = "temp_c"
unit = "Cel"
max = { warn_high = 60.0, error_high = 70.0 }

[[diagnostic]]
id = "driver.faults"
diagnostic = "stock.fault_codes"
title = "fault and limiter codes"
path = "*/can/1"
window_s = 2
"""


def wait_for(check, *, timeout=15.0, what="the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


class Rig:
    """A host over a fake OS with the state fixture's joint (an fdcanusb and CAN 1, a moteus c1); its diagnostics.toml in ``tmp``."""

    def __init__(self, tmp_path: Path, toml: str | None = FAST, *, beat_s: float = 0.5):
        directory = tmp_path / "plugins.d"
        directory.mkdir()
        environment = {"STATE_PERIOD_S": "0.05"}
        (directory / "state_joint.toml").write_text(
            f'name = "state_joint"\ncommand = ["{{python}}", "{PLUGIN}", "--role", "joint"]\ntimeout_s = 10\nperiod_s = 30\n'
            f'matches = ["usb:{JOINT[0]}:{JOINT[1]}"]\nenv = {{ {", ".join(f"{k} = {json.dumps(v)}" for k, v in environment.items())} }}\n')
        self.config = tmp_path / "config"
        self.config.mkdir()
        if toml is not None:
            (self.config / sd.DIAGNOSTICS_FILE).write_text(toml)
        self.os = FakeOs(standard_skeleton(usb_device("9-1", *JOINT, "SER1", label="fdcanusb")), {})
        self.host = Host(plugins_dir=directory, clock=Clock(), os_source=self.os, watch_grace_s=30.0, watch_tick_s=0.1,
                         follow_backoff_s=(0.2, 0.4), frames_dir=tmp_path / "frames", settings_file=self.config / "settings.toml",
                         diagnostics_beat_s=beat_s)
        self.host.run_all_once()
        self.board = self.host.layer.device_paths["usb:9-1"] + "/can/1"

    def watch(self, seconds: float) -> None:
        """Keep the board's watch running (20 Hz State; the history keeps 1 Hz) for ``seconds``."""

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.host.watch_heartbeat(self.board)
            time.sleep(0.25)

    def ready(self, timeout: float = 20.0) -> None:
        """Watch until the board's history holds one 2 s window with 2 samples (the board temperature has a value)."""

        def has_value():
            self.watch(0.5)
            temp = self.mine()["board temperature"]
            return temp.measurements and temp.measurements[0].value is not None

        wait_for(has_value, timeout=timeout, what="a 2 s window of State history")

    def mine(self) -> dict:
        return {status.name.split(": ", 1)[-1]: status for status in self.host.run_stock_diagnostics() if status.path == self.board}


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    yield made
    made.host.stop()


def test_the_default_set_is_read_when_the_person_has_no_file(tmp_path):
    runner = sd.StockDiagnostics(tmp_path / sd.DIAGNOSTICS_FILE)
    assert runner.source == str(sd.DEFAULT_DIR) and runner.problem == ""
    board = runner.bindings_for("host/usb9/port-9-1/usb-9-1/can/1", "mjbots-moteus-c1")
    assert [(b.id, b.diagnostic) for b in board][:3] == [("driver.faults", "stock.fault_codes"), ("driver.board_temp", "stock.box"),
                                                         ("driver.bus_voltage", "stock.box")]
    # the default set is the catalog's own driver rows, his numbers: board temperature max 60 / 70 and mean 55 / 65 Cel
    _spec, config = runner.build_config(board[1])
    assert (config.max, config.mean) == (Limits(warn_high=60.0, error_high=70.0), Limits(warn_high=55.0, error_high=65.0))
    assert sd.catalog_entry("driver.board_temp").primitive == "stock.box" and sd.catalog_entry("driver.faults").primitive == "stock.fault_codes"
    assert all(dx_registry.REGISTRY[b.diagnostic] for b in runner.bindings)


def test_a_fixture_replay_runs_only_the_persons_file_never_the_default_set(tmp_path, monkeypatch):
    from io_diagnostics.host import make_host

    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(tmp_path))
    host = make_host(fixture="F1", plugins_dir=PLUGIN.parent, once=True)
    assert host.stock_diagnostics.bindings == [] and host.stock_diagnostics.source == ""
    (tmp_path / sd.DIAGNOSTICS_FILE).write_text(FAST)
    host.stock_diagnostics.reload_if_changed()
    assert [b.id for b in host.stock_diagnostics.bindings] == ["driver.board_temp", "driver.faults"]


def test_a_watched_board_gets_its_stock_diagnostics_as_ordinary_statuses(rig):
    rig.ready()
    found = rig.mine()
    temp, faults = found["board temperature"], found["fault and limiter codes"]
    assert temp.level == "ok" and temp.source == sd.SOURCE and temp.expected_period_s == 0.5
    assert validate(temp) == [] and validate(faults) == []
    assert temp.name == "CAN 1: board temperature"  # "<entity>: <title>": the page folds it into the board's folder
    primary = temp.measurements[0]
    assert primary.key == "max" and primary.unit == "Cel" and (primary.warn_high, primary.error_high) == (60.0, 70.0)
    assert 38.0 <= primary.value <= 45.0  # the fixture's motor runs 38 .. 44 Cel
    values = dict(temp.values)
    assert values["dx.diagnostic"] == "stock.box" and values["dx.binding"] == "driver.board_temp"
    window = json.loads(values["dx.window"])
    assert window["window_s"] == 2 and window["to"] - window["from"] == 2 and window["samples"] >= 2
    # the fixture's board reports fault 33 (motor driver fault): the library calls it a hard fault, as the driver's own status does
    assert faults.level == "error" and faults.code == "HardFault"
    # the Diagnostics tab's checks, /checks and the history all carry them, with no API change
    checks = {check["name"]: check for check in rig.host.node(rig.board)["checks"]}
    assert checks["CAN 1: board temperature"]["source"] == "robotdx.diagnostics" and checks["CAN 1: board temperature"]["effective_level"] == "ok"
    assert {"CAN 1: board temperature", "CAN 1: fault and limiter codes"} <= {check["name"] for check in rig.host.checks_json()["checks"][rig.board]}
    history = rig.host.history_json(rig.board)
    assert {"CAN 1: board temperature", "CAN 1: fault and limiter codes"} <= {status["name"] for status in history["statuses"]}



def _row(tree: dict, path: str) -> dict | None:
    stack = [tree]
    while stack:
        node = stack.pop()
        if node["path"] == path:
            return node
        stack += node.get("children") or []
    return None


def test_the_live_feed_upserts_the_row_when_the_beat_adds_its_diagnostics(rig):
    before = _row(rig.host.state()["tree"], rig.board)["checks"]
    sub, first = rig.host.feed.subscribe(None)
    try:
        assert first  # the snapshot
        rig.ready()  # beats run here: the board's row gains the library's two statuses
        frames = b"".join(rig.host.feed.wait(sub, 5.0))
        deadline = time.monotonic() + 10
        while f'"path": "{rig.board}"'.encode() not in frames and f'"path":"{rig.board}"'.encode() not in frames and time.monotonic() < deadline:
            frames += b"".join(rig.host.feed.wait(sub, 1.0))
        assert b"upsert" in frames and rig.board.encode() in frames
        assert _row(rig.host.state()["tree"], rig.board)["checks"] == before + 2
    finally:
        rig.host.feed.unsubscribe(sub)

def test_not_enough_history_is_not_run_never_ok(tmp_path):
    rig = Rig(tmp_path, FAST.replace("window_s = 2\n", "window_s = 600\n", 1))
    try:
        rig.watch(1.5)
        temp = rig.mine()["board temperature"]
        assert temp.level == "unknown" and temp.code == "NotEnoughHistory" and "600 s window" in temp.message
        shown = next(check for check in rig.host.node(rig.board)["checks"] if check["name"] == "CAN 1: board temperature")
        assert shown["effective_level"] == "unknown" and shown["source"] == "robotdx.diagnostics"
    finally:
        rig.host.stop()


def test_a_board_with_no_state_yet_is_not_run(tmp_path):
    rig = Rig(tmp_path, FAST.replace('state = "motor"', 'state = "nothing"'))
    try:
        temp = rig.mine()["board temperature"]
        assert temp.level == "unknown" and temp.code == "NoState"
        # no row on the board for it (an unwatched board keeps no State), and never OK; the drill-in API still says why
        names = [check["name"] for check in rig.host.node(rig.board)["checks"]]
        assert "CAN 1: board temperature" not in names
        about = next(item for item in rig.host.diagnostics_config_json(rig.board)["diagnostics"] if item["id"] == "driver.board_temp")
        assert about["status"]["code"] == "NoState" and about["status"]["level"] == "unknown"
    finally:
        rig.host.stop()


def test_a_diagnostic_that_raises_is_a_not_run_with_its_reason_and_the_host_goes_on(tmp_path):
    def broken(config, runs):
        raise RuntimeError("the arithmetic broke")

    dx_registry.register(Spec("test.broken", broken, type(dx_registry.REGISTRY["stock.box"].config_type()), "broken", "health", "health",
                              "threshold_band", "series", "1"))
    try:
        rig = Rig(tmp_path, FAST + '\n[[diagnostic]]\nid = "x.broken"\ndiagnostic = "test.broken"\ntitle = "broken one"\npath = "*/can/1"\nwindow_s = 2\n')
        try:
            rig.ready()
            found = rig.mine()
            broken_one = found["broken one"]
            assert broken_one.level == "unknown" and broken_one.code == "DiagnosticRaised"
            assert "RuntimeError: the arithmetic broke" in broken_one.message
            assert found["board temperature"].level == "ok"  # the others still ran
            assert rig.host.node(rig.board) is not None
        finally:
            rig.host.stop()
    finally:
        dx_registry.REGISTRY.pop("test.broken", None)


def test_numpy_missing_is_a_not_run_that_says_so(tmp_path, monkeypatch):
    import robotdx.diagnostics._numpy as lazy

    rig = Rig(tmp_path)
    try:
        rig.ready()
        monkeypatch.setitem(__import__("sys").modules, "numpy", None)  # `import numpy` now fails as it does without the extra
        monkeypatch.setattr(lazy, "np", lazy._Numpy())  # a fresh proxy: nothing cached from the import before
        monkeypatch.setattr("robotdx.diagnostics.stock.health.np", lazy.np)
        monkeypatch.setattr("robotdx.diagnostics.stream.np", lazy.np)
        temp = rig.mine()["board temperature"]
        assert temp.level == "unknown" and temp.code == "NumpyMissing" and "diagnostics extra" in temp.message
    finally:
        rig.host.stop()


def test_the_beat_runs_on_its_own_thread_and_its_rows_go_stale_when_it_stops(tmp_path):
    rig = Rig(tmp_path, beat_s=0.3)
    try:
        rig.host.start(udev=False)
        rig.ready()
        names = wait_for(lambda: {c["name"] for c in rig.host.node(rig.board)["checks"]} >= {"CAN 1: board temperature"} or None, what="the beat")
        assert names
        rig.host._stop.set()  # the beat stops: its statuses promised 0.3 s, so they go STALE after the 5 s floor
        time.sleep(5.6)
        checks = {check["name"]: check for check in rig.host.node(rig.board)["checks"]}
        assert checks["CAN 1: board temperature"]["effective_level"] == "stale"
    finally:
        rig.host.stop()


def test_a_broken_file_runs_nothing_and_says_why(tmp_path):
    runner = sd.StockDiagnostics(_write(tmp_path, '[[diagnostic]]\nid = "a"\ndiagnostic = "stock.box"\n'))
    assert runner.bindings == [] and "says no path, model_id or part" in runner.problem


def test_a_custom_diagnostic_registers_from_the_same_file_with_load_toml(tmp_path):
    text = ('[[diagnostic]]\nname = "custom.box_again"\nfunction = "robotdx.diagnostics.stock.health:box"\n'
            'config = "robotdx.diagnostics.stock.health:BoxConfig"\ntitle = "box again"\nfamily = "health"\nrole = "health"\n'
            'hint = "threshold_band"\nemits = "series"\nunit = "Cel"\n\n'
            '[[diagnostic]]\nid = "mine"\ndiagnostic = "custom.box_again"\npath = "host/*"\n')
    try:
        runner = sd.StockDiagnostics(_write(tmp_path, text))
        assert runner.problem == "" and "custom.box_again" in dx_registry.REGISTRY
        assert [b.diagnostic for b in runner.bindings] == ["custom.box_again"]
        assert runner.specs[0]["name"] == "custom.box_again"
        again = sd.parse(tomllib.loads(sd.dump(runner.bindings, runner.specs)))
        assert again == (runner.bindings, runner.specs)
    finally:
        dx_registry.REGISTRY.pop("custom.box_again", None)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / sd.DIAGNOSTICS_FILE
    path.write_text(text)
    return path


def test_trajectory_maps_moteus_state_keys_and_converts_revolutions():
    points = [[100.0, 0.25], [101.0, 0.5]]
    run, first = sd.trajectory([{"key": "position", "unit": "rev", "points": points},
                                {"key": "fault", "unit": "", "points": [[100.0, 33]]},
                                {"key": "temperature", "unit": "Cel", "points": [[100.0, 40.0], [101.0, 41.0]]}],
                               columns=sd.COLUMNS, run="r", device="CAN 1")
    assert first == 100.0 and list(run.t) == [0.0, 1.0]
    assert list(run.q_rad) == pytest.approx([0.25 * 2 * 3.141592653589793, 0.5 * 2 * 3.141592653589793])
    assert list(run.fault) == [33.0, 33.0]  # a code holds until the next report
    assert list(run.temp_c) == [40.0, 41.0] and run.v_bus_v is None


def test_the_record_round_trips_through_the_library(rig):
    from robotdx.diagnostics.record import from_record
    from contract.record import to_dict

    rig.ready()
    temp = rig.mine()["board temperature"]
    back = from_record(to_dict(temp))
    assert back.diagnostic == "stock.box" and back.verdict == "pass" and back.limits == Limits(warn_high=60.0, error_high=70.0)
    assert back.config["max"] == {"warn_low": None, "warn_high": 60.0, "error_low": None, "error_high": 70.0}


HOST_PROBE = r"""
import importlib.util, json, sys
from pathlib import Path
from io_diagnostics.host import make_host
host = make_host(fixture="F1", plugins_dir=Path(sys.argv[1]), once=True)
host.run_all_once()
found = [{"path": s.path, "name": s.name, "level": s.level, "code": s.code, "message": s.message} for s in host.run_stock_diagnostics()]
from robotdx.diagnostics import REGISTRY
print(json.dumps({"numpy": importlib.util.find_spec("numpy") is not None, "found": found, "listed": sorted(REGISTRY)}))
"""


def test_the_host_starts_without_numpy_and_each_diagnostic_says_why(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env.update(IO_DIAGNOSTICS_CONFIG_DIR=str(tmp_path / "config"), IO_DIAGNOSTICS_STATE_DIR=str(tmp_path / "state"))
    (tmp_path / "config").mkdir()
    # a replay runs only the person's file: the default set's driver-board file
    (tmp_path / "config" / sd.DIAGNOSTICS_FILE).write_text((sd.DEFAULT_DIR / "02-bldc_driver.toml").read_text())
    done = subprocess.run(["/home/bam/.local/bin/uv", "run", "--isolated", "--no-dev", "--project", str(root), "python", "-c", HOST_PROBE,
                           str(root / "tests" / "fixtures" / "plugins-state")], capture_output=True, text=True, timeout=300, env=env, cwd=root)
    assert done.returncode == 0, done.stderr[-3000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["numpy"] is False and "stock.box" in out["listed"]
    found = [row for row in out["found"] if row["path"].endswith("/can/1")]
    board = [row for row in found if row["code"] == "NumpyMissing"]
    assert {row["name"] for row in board} == {"CAN 1: board temperature", "CAN 1: fault and limiter codes", "CAN 1: bus voltage"}
    assert all(row["level"] == "unknown" and "diagnostics extra" in row["message"] for row in board), board
    # the others say why without numpy: the rate waits for a watch, F1's boards report no reply counts, and the expected config is compared
    # with the stdlib alone (stock.agreement needs no numpy)
    by_name = {row["name"]: row for row in found}
    assert by_name["CAN 1: command / telemetry rate"]["code"] == sd.NOT_STREAMING
    assert by_name["CAN 1: CAN reply loss"]["code"] == sd.NOT_WIRED and "queries and replies" in by_name["CAN 1: CAN reply loss"]["message"]
    assert by_name["CAN 1: config matches expected"]["level"] == "warn" and "differ" in by_name["CAN 1: config matches expected"]["message"]
