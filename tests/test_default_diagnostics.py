"""The host's default diagnostics set (io_diagnostics/diagnostics_default.d/, one file per part type, generated from the catalog): an entity of
each part type gets its stock diagnostics with no file of the person's, the drill-in API lists them with their config, provenance and data-ui
hint, a binding whose input the State history does not carry is not run and posts no row (never a grey row forever), and an agent's or
proposed limit shows as a warning, never a fail. Fixture-only: the state fixture's plugin (a real subprocess, never hardware) and composites."""

from __future__ import annotations

import json
import tomllib
import urllib.request
from pathlib import Path

import pytest

from io_diagnostics import default_diagnostics as dd
from io_diagnostics.composite import composite_path
from io_diagnostics import stock_diagnostics as sd
from io_diagnostics.host import Clock, Host
from io_diagnostics.server import make_server, serve_in_thread
from robotdx.diagnostics import catalog
from robotdx.diagnostics.core import HINTS
from robotdx.diagnostics.registry import config_from_dict
from robotdx.diagnostics import REGISTRY

from test_host_support import FakeOs, standard_skeleton, usb_device

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plugins-state"
JOINT, CAMERA = ("0483", "5740", "SER1"), ("8086", "0b5b", "CAM00001")
# one fixture entity per part type: a device's model_id (what its driver reports) or a composite's kind
BY_MODEL = {"bldc_driver": "mjbots-moteus-c1", "encoder": "ams-as5047p", "camera_rgb": "obsbot-tiny-4k", "camera_rgbd": "intel-realsense-d405",
            "camera_hsi": "specim-fx17", "imu": "bosch-bno085", "ft_sensor": "ati-mini45", "gripper": "robotiq-2f-85", "arm": "universal-robots-ur5e"}
BY_COMPOSITE = {"servo": "joint", "gearbox": "gearbox", "encoder": "encoder", "camera_hsi": "hyperspectral_camera", "imu": "imu",
                "ft_sensor": "ft_sensor", "gripper": "gripper", "arm": "arm", "arm_gripper": "pick", "deployment": "rack"}


def expected_ids(*parts: str) -> set[str]:
    return {entry.id for part in parts for entry in dd.bound(part)}


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    made = sd.StockDiagnostics(tmp_path_factory.mktemp("cfg") / sd.DIAGNOSTICS_FILE)  # no file of the person's: the default set
    assert made.problem == "" and made.source == str(sd.DEFAULT_DIR)
    return made


def test_the_committed_files_are_what_the_catalog_writes():
    files = dd.render()
    on_disk = {path.name: path.read_text() for path in sorted(sd.DEFAULT_DIR.glob("*.toml"))}
    assert on_disk == files, "regenerate: uv run --project /home/bam/io-diagnostics python -m io_diagnostics.default_diagnostics --write"
    assert len(files) == len(catalog.PARTS) == 13


def test_every_live_catalog_entry_is_bound_once_with_its_default_config(runner):
    live = [entry for entry in catalog.ENTRIES if entry.consumers in ("robotdx", "both")]
    assert sorted(b.id for b in runner.bindings) == sorted(entry.id for entry in live)
    for binding in runner.bindings:
        entry = catalog.entry(binding.id)
        assert (binding.diagnostic, binding.part, binding.title) == (entry.primitive, entry.part, entry.title)
        _spec, config = runner.build_config(binding)
        assert config == catalog.config(binding.id), binding.id  # the file says the catalog's numbers, all of them
    assert {rule.type for rule in runner.parts} == set(catalog.PARTS)


@pytest.mark.parametrize("part", sorted(catalog.PARTS))
def test_a_fixture_entity_of_each_part_type_gets_its_stock_diagnostics(runner, part):
    found = set()
    if part in BY_MODEL:
        ids = {b.id for b in runner.bindings_for(f"host/usb/9-9/{part}", BY_MODEL[part])}
        parts = set(runner.parts_of("x", BY_MODEL[part]))
        assert part in parts
        assert ids == expected_ids(*parts), part
        found |= ids
    if part in BY_COMPOSITE:
        ids = {b.id for b in runner.bindings_for(f"composite/{part}", "", composite_kind=BY_COMPOSITE[part])}
        assert ids == expected_ids(part), part
        found |= ids
    assert found >= expected_ids(part) and expected_ids(part), part


def test_a_realsense_is_an_rgb_camera_and_an_rgbd_one(runner):
    assert runner.parts_of("x", "intel-realsense-d405") == ("camera_rgb", "camera_rgbd")
    assert len(expected_ids("camera_rgb", "camera_rgbd")) == 9


def test_an_unknown_model_gets_nothing(runner):
    assert runner.bindings_for("host/usb/9-9", "mjbots-fdcanusb") == [] and runner.parts_of("host/usb/9-9", "mjbots-fdcanusb") == ()


def test_parts_survive_the_drill_ins_rewrite():
    document = tomllib.loads("\n".join((sd.DEFAULT_DIR / name).read_text() for name in ("02-bldc_driver.toml",)))
    bindings, specs = sd.parse(document)
    parts = sd.parse_parts(document)
    back = tomllib.loads(sd.dump(bindings, specs, parts))
    assert sd.parse(back)[0] == bindings and sd.parse_parts(back) == parts


def test_a_part_table_with_an_unknown_type_is_refused():
    with pytest.raises(sd.DiagnosticsError, match="part types"):
        sd.parse_parts({"part": [{"type": "toaster", "model_id": ["*"]}]})


# ----- through a real host and its HTTP API -------------------------------------------------------------------------------------
COMPOSITES = "".join(f'''
[[composite]]
id = "fixture/{part}"
kind = "{kind}"
title = "{part} fixture"
children = ["usb:{JOINT[0]}:{JOINT[1]}:{JOINT[2]}#can/{number}"]
''' for number, (part, kind) in enumerate(BY_COMPOSITE.items(), 1))  # WHY a joint each: a leaf belongs to one composite


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("host")
    directory = tmp / "plugins.d"
    directory.mkdir()
    (directory / "state_joint.toml").write_text(
        f'name = "state_joint"\ncommand = ["{{python}}", "{FIXTURES}/state_plugin.py", "--role", "joint"]\ntimeout_s = 10\nperiod_s = 30\n'
        f'matches = ["usb:{JOINT[0]}:{JOINT[1]}"]\nenv = {{ STATE_PERIOD_S = "0.05", STATE_NODES = "{len(BY_COMPOSITE)}" }}\n')
    (directory / "state_camera.toml").write_text(
        f'name = "state_camera"\ncommand = ["{{python}}", "{FIXTURES}/state_plugin.py", "--role", "camera"]\ntimeout_s = 10\nperiod_s = 30\n'
        f'matches = ["usb:{CAMERA[0]}:{CAMERA[1]}"]\n')
    (tmp / "expected.toml").write_text("version = 1\n" + COMPOSITES)
    config = tmp / "config"
    config.mkdir()  # no diagnostics.toml: the host reads its default set
    devices = [usb_device("9-1", *JOINT, label="fdcanusb"), usb_device("9-3", *CAMERA, label="camera")]
    host = Host(plugins_dir=directory, clock=Clock(), os_source=FakeOs(standard_skeleton(*devices)), expected_file=tmp / "expected.toml",
                watch_grace_s=30.0, watch_tick_s=0.1, follow_backoff_s=(0.2, 0.4), frames_dir=tmp / "frames",
                settings_file=config / "settings.toml", diagnostics_beat_s=0.5)
    host.run_all_once()
    host.run_all_once()
    server = make_server(host, 0)
    serve_in_thread(server)
    host.base = host.url.rstrip("/")
    entities = host.view().merged.entities
    host.paths = {
        "bldc_driver": host.layer.device_paths["usb:9-1"] + "/can/1",
        "camera_rgbd": next(path for path, entity in entities.items() if (entity.identity or {}).get("model_id") == "intel-realsense-d405"),
        **{part: composite_path(f"fixture/{part}") for part in BY_COMPOSITE},
    }
    assert all(path in entities for path in host.paths.values()), ([p for p in host.paths.values() if p not in entities], sorted(entities), host.state().get("composition", {}).get("problems"))
    yield host
    server.shutdown()
    server.server_close()
    host.stop()


def get(host, path: str):
    with urllib.request.urlopen(host.base + path, timeout=10) as response:
        return json.loads(response.read())


@pytest.mark.parametrize("part", sorted(catalog.PARTS))
def test_the_drill_in_api_lists_each_part_types_stock_diagnostics(served, part):
    path = served.paths.get(part) or served.paths["camera_rgbd"]  # camera_rgb: the RealSense is one
    body = get(served, f"/api/v1/diagnostics/config?path={path}")
    assert part in body["parts"]
    by_id = {item["id"]: item for item in body["diagnostics"]}
    assert set(by_id) >= expected_ids(part) and expected_ids(part)
    for entry_id in expected_ids(part):
        item = by_id[entry_id]
        entry = catalog.entry(entry_id)
        assert item["match"]["part"] == part and item["diagnostic"] == entry.primitive
        assert item["catalog"]["hint"] == entry.the_hint in HINTS and item["catalog"]["part"] == part
        assert item["catalog"]["limits_provenance"] == catalog.default(entry_id).limits
        assert item["catalog"]["gating"] == catalog.default(entry_id).gating
        rows = {row["field"]: row for row in item["fields"]}
        assert set(rows) == {row["field"] for row in sd.schema(REGISTRY[entry.primitive].config_type)}  # every field drillable
        assert all(row["source"] in ("toml", "default") for row in rows.values())


def test_an_unwatched_camera_waits_for_its_frames_quietly_and_names_the_sources_it_lacks(served):
    statuses = served.run_stock_diagnostics()
    camera = served.paths["camera_rgbd"]
    mine = {sd.binding_of(status): status for status in statuses if status.path == camera}
    assert set(mine) == expected_ids("camera_rgb", "camera_rgbd")
    assert all(status.level == "unknown" for status in mine.values())  # nobody watches it: no frames, and the rest has no source
    # its frames feed the latency and the blur once a page watches it: until then not run, and no row (tests/test_stock_inputs.py runs them)
    assert {entry_id for entry_id, status in mine.items() if status.code == sd.NOT_STREAMING} == {"camera.latency", "camera.blur"}
    no_source = {entry_id for entry_id, status in mine.items() if status.code == sd.NO_SOURCE}
    assert no_source == set(mine) - {"camera.latency", "camera.blur"}
    # the camera reports State, so each binding with no source is a row of its own (unknown, never OK) that says what would provide it
    posted = {check["name"] for check in served.node(camera)["checks"]}
    assert {entry_id for entry_id in mine if any(name.endswith(catalog.entry(entry_id).title) for name in posted)} == no_source
    body = get(served, f"/api/v1/diagnostics/config?path={camera}")
    reasons = {item["id"]: item["status"]["message"] for item in body["diagnostics"] if item["status"]}
    assert all(reasons.values()) and "per-frame stamps" in reasons["camera.frame_rate"]  # the drill-in says why, rows or not


def test_state_inputs_missing_names_what_is_absent():
    import numpy as np

    from robotdx.diagnostics.data import Trajectory
    from robotdx.diagnostics.stock import BoxConfig, TrackingErrorConfig

    run = Trajectory(t=np.arange(5.0), q_rad=np.zeros(5), temp_c=np.full(5, 30.0), v_bus_v=np.full(5, np.nan))
    assert sd.state_inputs_missing("stock.box", BoxConfig(channel="temp_c"), run) == ""
    assert sd.state_inputs_missing("stock.box", BoxConfig(channel="v_bus_v"), run) == "v_bus_v"
    assert sd.state_inputs_missing("stock.tracking_error", TrackingErrorConfig(), run) == "q_target_rad"
    assert "stock.sample_rate" in sd.state_inputs_missing("stock.sample_rate", None, run)
    # a column that is not one of the Trajectory's own is read from its extra channels
    gyro = Trajectory(t=np.arange(5.0), q_rad=np.zeros(5), extra={"gyro_rad_s": np.full(5, 0.01)})
    assert sd.state_inputs_missing("stock.box", BoxConfig(channel="gyro_rad_s"), gyro) == ""


def test_a_watched_board_runs_what_its_state_feeds_and_a_proposed_limit_only_warns(tmp_path):
    from test_stock_diagnostics import Rig

    # the default bldc_driver file as the person's, on a 2 s window so a short watch fills it
    text = (sd.DEFAULT_DIR / "02-bldc_driver.toml").read_text().replace("window_s = 60", "window_s = 2")
    rig = Rig(tmp_path, text)
    try:
        rig.ready()
        mine = {sd.binding_of(status): status for status in rig.host.run_stock_diagnostics() if status.path == rig.board}
        assert set(mine) == expected_ids("bldc_driver")
        # the fixture board sits at 24 V: under the proposed 30 V floor, a warning that says so, never an error
        voltage = mine["driver.bus_voltage"]
        assert (voltage.level, voltage.code) == ("warn", "ProposedMinBelowErrorLimit") and "not a gate" in voltage.message
        assert mine["driver.board_temp"].level in ("ok", "warn", "error") and mine["driver.board_temp"].measurements[0].value is not None
        # the loop rate counts every State record of the watch, the config is compared with its expected values; this fixture board's
        # status reports no reply counts, so the reply loss says it has no source
        assert mine["driver.loop_rate"].level in ("ok", "warn", "error") and mine["driver.loop_rate"].measurements[0].value > 0
        assert mine["driver.config"].level in ("ok", "warn", "error") and "differ" in mine["driver.config"].message
        assert (mine["driver.link_loss"].level, mine["driver.link_loss"].code) == ("unknown", sd.NO_SOURCE)
        for entry_id, status in mine.items():  # each row carries its entry's data-ui view, as the library's record writes it
            assert dict(status.values)["dx.hint"] == catalog.entry(entry_id).the_hint in HINTS, entry_id
        posted = {check["name"].split(": ", 1)[-1] for check in rig.host.node(rig.board)["checks"]}
        assert {"board temperature", "bus voltage", "fault and limiter codes", "command / telemetry rate", "config matches expected",
                "CAN reply loss"} <= posted
    finally:
        rig.host.stop()
