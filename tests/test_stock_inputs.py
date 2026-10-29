"""Every default binding is fed from what a RobotDx entity actually publishes, or says which source it lacks: code ``NoSource`` with "no <input>
source on this entity: <what would provide it>", unknown and never OK. Never a silent ``NoInput`` (Codex audit 2026-10-29 round 2, gap 1:
"only 23/61 bindings use primitive types the host can feed"; reports/media/audits/2026-10-29-sim2real-three-areas-r2.md in bam_ws).

The fixture entity of each part type publishes what RobotDx's real entity of that type publishes today, through the real host:

- bldc_driver: the state fixture's CAN 1 (a moteus-like board): State (position ... fault), configuration with expected values, its comms
  status's reply counts (queries / replies, as moteus's per-board status), a driver fault the fault memory confirms;
- camera_rgb + camera_rgbd: the state fixture's camera (a RealSense-like D405): State and colour + depth frames, PNG files in the frame
  directory (the real driver writes JPEG: test_a_jpeg_frame_is_not_run_with_the_decoder_it_lacks);
- the composites of every other part type (composition.toml): their members' levels and events; the arm also runs the arm composite plugin
  (its own State: joint angles, end effector x / y). The deployment holds the servo and the arm.

The default set is the person's file here, its State windows shortened to 4 s so a few seconds of watching fill them (the event-counting
bindings keep 600 s, enough to hold the fault confirmed at start). Fixture-only: real subprocesses that never touch hardware."""

from __future__ import annotations

import dataclasses
import json
import struct
import time
import tomllib
import urllib.request
import zlib
from pathlib import Path

import pytest

from contract.record import Frame, Measurement
from io_diagnostics import default_diagnostics as dd
from io_diagnostics import stock_diagnostics as sd
from io_diagnostics.composite import composite_path
from io_diagnostics.history import History
from io_diagnostics.host import Clock, Host
from io_diagnostics.server import make_server, serve_in_thread
from robotdx.diagnostics import catalog

from test_host_support import FakeOs, standard_skeleton, usb_device

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plugins-state"
JOINT, CAMERA = ("0483", "5740", "SER1"), ("8086", "0b5b", "CAM00001")
LIVE = sorted(entry.id for entry in catalog.ENTRIES if entry.consumers in dd.LIVE)
RAN = ("ok", "warn", "error")  # pass / warn / fail as a record's level (an agent's or proposed limit turns a fail into a warn)

# The bindings no RobotDx entity has a source for yet, each with a word its reason must name (what would provide it). Everything else
# must evaluate on the fixture entities above.
NO_SOURCE = {
    # nothing RobotDx publishes carries this input
    "servo.hold_effort": "hold", "servo.torque_truth_kt": "Kv", "servo.live_reid": "floor", "gearbox.wear": "commissioning",
    "encoder.homing": "homing", "camera.frame_rate": "per-frame stamps", "camera.extrinsic_drift": "tag", "camera.tag_detect": "tag",
    "depth.plane_rms": "depth values", "depth.fill_rate": "depth values", "depth.temporal_noise": "depth values", "depth.health_check": "health",
    "hsi.snr": "cube", "hsi.references": "white", "hsi.spectral_angle": "spectra", "hsi.frame_rate": "per-frame stamps",
    "imu.gravity": "accel", "ft.known_mass": "mass", "gripper.detect": "truth", "gripper.grasp_success": "grasp",
    "arm.tool_error_at_stop": "tool pose", "arm.path_tracking": "tool pose", "arm.completion_time": "planned", "arm.calibration_drift": "tag",
    "pick.sppm": "pick", "pick.vs_human": "human", "pick.rate": "pick", "pick.mtti": "intervention", "pick.grasp_pose_error": "grasp pose",
    "rack.gate_matrix": "gate", "rack.throughput": "material",
    # read from State, but no RobotDx entity of this type reports these keys (test_a_state_input_is_fed_once_the_state_carries_it)
    "servo.tracking_rms": "State", "servo.tracking_peak": "State", "servo.stop_error": "State", "encoder.standstill_noise": "State",
    "encoder.validity": "State", "imu.static_bias": "State", "imu.rate": "State", "ft.zero_drift": "State", "ft.noise": "State",
    "ft.saturation": "State", "gripper.cycle": "State", "gripper.force": "State", "gripper.compute_time": "State",
    "arm.overshoot": "q_target_rad", "arm.hold_effort": "tau_ff_nm", "rack.host": "State",
}
FED = sorted(set(LIVE) - set(NO_SOURCE))
LONG_WINDOW = {"servo.mttf", "rack.uptime", "rack.links", "pick.mtti"}  # counted over time: 600 s holds the fault confirmed at start

# one composite per part type that is not a device of its own here; a joint each, the arm three, the deployment the servo and the arm
COMPOSITES = {"servo": ("joint", [1]), "gearbox": ("gearbox", [2]), "encoder": ("encoder", [3]), "camera_hsi": ("hyperspectral_camera", [4]),
              "imu": ("imu", [5]), "ft_sensor": ("ft_sensor", [6]), "gripper": ("gripper", [7]), "arm": ("arm", [8, 9, 10]),
              "arm_gripper": ("pick", [11]), "deployment": ("rack", [12])}


def composition() -> str:
    out = ["version = 1"]
    for part, (kind, nodes) in COMPOSITES.items():
        children = [f'"usb:{JOINT[0]}:{JOINT[1]}:{JOINT[2]}#can/{node}"' for node in nodes]
        if part == "deployment":
            children += ['"fixture/servo"', '"fixture/arm"']
        out += ["", "[[composite]]", f'id = "fixture/{part}"', f'kind = "{kind}"', f'title = "{part} fixture"',
                *(['plugin = "state_arm"'] if part == "arm" else []), f"children = [{', '.join(children)}]"]
    return "\n".join(out) + "\n"


def person_file() -> str:
    """The default set, all thirteen files, as one file of the person's with the windows shortened."""

    bindings, specs, parts = [], [], []
    for file in sorted(sd.DEFAULT_DIR.glob("*.toml")):
        document = tomllib.loads(file.read_text())
        found, custom = sd.parse(document)
        bindings += [dataclasses.replace(b, window_s=600.0 if b.id in LONG_WINDOW else 4.0) for b in found]
        specs += custom
        parts += sd.parse_parts(document)
    return sd.dump(bindings, specs, parts)


def wait_for(check, *, timeout=20.0, what="the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture(scope="module")
def fed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fed")
    directory = tmp / "plugins.d"
    directory.mkdir()
    (directory / "state_joint.toml").write_text(
        f'name = "state_joint"\ncommand = ["{{python}}", "{FIXTURES}/state_plugin.py", "--role", "joint"]\ntimeout_s = 10\nperiod_s = 30\n'
        f'matches = ["usb:{JOINT[0]}:{JOINT[1]}"]\nenv = {{ STATE_PERIOD_S = "0.05", STATE_NODES = "12", STATE_REPLY_COUNTS = "1" }}\n')
    (directory / "state_camera.toml").write_text(
        f'name = "state_camera"\ncommand = ["{{python}}", "{FIXTURES}/state_plugin.py", "--role", "camera"]\ntimeout_s = 10\nperiod_s = 30\n'
        f'matches = ["usb:{CAMERA[0]}:{CAMERA[1]}"]\n')
    (directory / "state_arm.toml").write_text(
        f'name = "state_arm"\ncommand = ["{{python}}", "{FIXTURES}/arm_plugin.py"]\ntimeout_s = 10\nperiod_s = 30\n'
        f'matches = ["composite:arm"]\nenv = {{ ARM_PERIOD_S = "0.05" }}\n')
    (tmp / "expected.toml").write_text(composition())
    config = tmp / "config"
    config.mkdir()
    (config / sd.DIAGNOSTICS_FILE).write_text(person_file())
    devices = [usb_device("9-1", *JOINT, label="fdcanusb"), usb_device("9-3", *CAMERA, label="camera")]
    host = Host(plugins_dir=directory, clock=Clock(), os_source=FakeOs(standard_skeleton(*devices)), expected_file=tmp / "expected.toml",
                watch_grace_s=30.0, watch_tick_s=0.1, follow_backoff_s=(0.2, 0.4), frames_dir=tmp / "frames",
                settings_file=config / "settings.toml", diagnostics_beat_s=0.5)
    host.run_all_once()
    host.run_all_once()
    entities = host.view().merged.entities
    board = host.layer.device_paths["usb:9-1"] + "/can/1"
    camera = next(path for path, entity in entities.items() if (entity.identity or {}).get("model_id") == "intel-realsense-d405")
    host.paths = {"bldc_driver": board, "camera_rgb": camera, "camera_rgbd": camera,
                  **{part: composite_path(f"fixture/{part}") for part in COMPOSITES}}
    assert all(path in entities for path in host.paths.values()), sorted(set(host.paths.values()) - set(entities))
    watched = [board, camera, host.paths["arm"]]

    def filled():
        for path in watched:
            host.watch_heartbeat(path)
        time.sleep(0.3)
        mine = {(status.path, sd.binding_of(status)): status for status in host.run_stock_diagnostics()}
        return all(mine.get((board, entry_id)) is not None and mine[(board, entry_id)].level in RAN
                   for entry_id in ("driver.board_temp", "driver.loop_rate")) and \
            mine.get((camera, "camera.blur")) is not None and mine[(camera, "camera.blur")].level in RAN

    try:
        wait_for(filled, timeout=30.0, what="4 s of watched State and frames")
    except AssertionError:
        pass  # each test below says which binding did not run
    for path in watched:
        host.watch_heartbeat(path)
    host.statuses = host.run_stock_diagnostics()
    server = make_server(host, 0)
    serve_in_thread(server)
    host.base = host.url.rstrip("/")
    yield host
    server.shutdown()
    server.server_close()
    host.stop()


def get(host, path: str):
    with urllib.request.urlopen(host.base + path, timeout=10) as response:
        return json.loads(response.read())


def status_of(host, entry_id: str):
    part = catalog.entry(entry_id).part
    path = host.paths[part]
    found = [status for status in host.statuses if status.path == path and sd.binding_of(status) == entry_id]
    assert found, f"{entry_id}: no status on the {part} fixture {path}"
    return found[0]


def test_the_list_covers_every_live_binding():
    assert set(NO_SOURCE) <= set(LIVE) and len(LIVE) == 61
    assert len(FED) == 14, FED


@pytest.mark.parametrize("entry_id", LIVE)
def test_every_default_binding_is_fed_or_names_the_source_it_lacks(fed, entry_id):
    status = status_of(fed, entry_id)
    if entry_id in NO_SOURCE:
        assert (status.level, status.code) == ("unknown", "NoSource"), (entry_id, status.code, status.message)
        assert status.message.startswith("no ") and " source on " in status.message, status.message
        assert NO_SOURCE[entry_id] in status.message, (entry_id, status.message)
    else:
        assert status.level in RAN, (entry_id, status.level, status.code, status.message)
        assert status.code not in ("NoInput", "NoSource", "NoState", "NotEnoughHistory", "NoFrames"), (entry_id, status.code, status.message)
        assert sd.window_of(status) is not None, entry_id  # the window it ran on, for the drill-in


def test_no_binding_is_ever_a_silent_no_input(fed):
    assert not [sd.binding_of(status) for status in fed.statuses if status.code == "NoInput"]


def test_a_no_source_row_shows_where_the_entity_reports_state_and_stays_quiet_where_it_reports_none(fed):
    arm, servo = fed.paths["arm"], fed.paths["servo"]
    arm_checks = {check["name"]: check for check in fed.node(arm)["checks"]}
    hold = next(check for name, check in arm_checks.items() if name.endswith(catalog.entry("arm.hold_effort").title))
    assert (hold["level"], hold["code"]) == ("unknown", "NoSource") and "tau_ff_nm" in hold["message"]
    # the servo composite reports no State of its own (no composite plugin): its NoSource rows post nothing, as a board with no State
    servo_checks = [check["name"] for check in fed.node(servo)["checks"]]
    assert not any(name.endswith(catalog.entry("servo.tracking_rms").title) for name in servo_checks), servo_checks
    # its fed bindings still post: the fault count over the servo's members
    assert any(name.endswith(catalog.entry("servo.mttf").title) for name in servo_checks), servo_checks


@pytest.mark.parametrize("part", sorted(catalog.PARTS))
def test_the_drill_in_says_what_feeds_each_binding(fed, part):
    body = get(fed, f"/api/v1/diagnostics/config?path={fed.paths[part]}")
    for item in body["diagnostics"]:
        if catalog.entry(item["id"]).part != part:
            continue
        source = item["source"]
        assert source["input"] and source["kind"], item["id"]
        if source["kind"] == "none":
            assert item["id"] in NO_SOURCE and source["missing"], item["id"]
        assert item["status"] is not None, item["id"]  # the source state, even where no row is posted
        if item["id"] in NO_SOURCE:
            assert item["status"]["code"] == "NoSource" and NO_SOURCE[item["id"]] in item["status"]["message"], item["id"]


# ----- the adapters on their own: State keys no RobotDx driver reports yet, a JPEG, the decoder's laziness ---------------------------
STATE_FED = {  # binding -> the State it needs: {key: (unit, value at t)}
    "servo.tracking_rms": {"position": ("rev", lambda t: 0.1 * t), "q_target_rad": ("rad", lambda t: 0.2 * 3.1416 * t + 0.001),
                           "mode": ("", lambda t: 10)},
    "servo.tracking_peak": {"position": ("rev", lambda t: 0.1 * t), "q_target_rad": ("rad", lambda t: 0.2 * 3.1416 * t + 0.002),
                            "mode": ("", lambda t: 10)},
    "servo.stop_error": {"position": ("rev", lambda t: 0.0), "q_target_rad": ("rad", lambda t: 0.001), "qd_target_rad_s": ("rad/s", lambda t: 0.0)},
    "encoder.standstill_noise": {"position": ("rev", lambda t: 1e-5 * ((t * 7) % 3))},
    "encoder.validity": {"position": ("rev", lambda t: 0.0), "fault": ("", lambda t: 0), "mode": ("", lambda t: 10)},
    "imu.static_bias": {"gyro_rad_s": ("rad/s", lambda t: 0.001)},
    "imu.rate": {"gyro_rad_s": ("rad/s", lambda t: 0.001)},
    "ft.zero_drift": {"force_n": ("N", lambda t: 0.05)},
    "ft.noise": {"force_n": ("N", lambda t: 0.05 * ((t * 3) % 2))},
    "ft.saturation": {"force_n": ("N", lambda t: 0.0), "fault": ("", lambda t: 0)},
    "gripper.cycle": {"width_mm": ("mm", lambda t: 40.0), "width_target_mm": ("mm", lambda t: 40.5)},
    "gripper.force": {"force_n": ("N", lambda t: 20.0), "force_target_n": ("N", lambda t: 21.0)},
    "gripper.compute_time": {"compute_s": ("s", lambda t: 0.2)},
    "arm.overshoot": {"position": ("rev", lambda t: min(t, 2.0) / 10), "q_target_rad": ("rad", lambda t: (0.0 if t < 1 else 0.2 * 3.1416))},
    "arm.hold_effort": {"torque": ("N.m", lambda t: 0.5), "tau_ff_nm": ("N.m", lambda t: 0.45), "position": ("rev", lambda t: 0.0)},
    "rack.host": {"cpu_temp_c": ("Cel", lambda t: 55.0)},
}


def history_of(path: str, group: str, keys: dict, seconds: float, rate_hz: float, now: float):
    history = History()
    arrivals = []
    for index in range(int(seconds * rate_hz) + 1):
        at = now - seconds + index / rate_hz
        arrivals.append(at)
        history.measure(path, group, [Measurement(key, value(at - now + seconds), unit) for key, (unit, value) in keys.items()], at, now,
                        min_gap_s=0.0)
    groups = {group: history.series((path, group), now - 2 * seconds, now)}
    return groups, arrivals


@pytest.mark.parametrize("entry_id", sorted(STATE_FED))
def test_a_state_input_is_fed_once_the_state_carries_it(entry_id):
    runner = sd.StockDiagnostics(None)  # the default set
    binding = dataclasses.replace(next(b for b in runner.bindings if b.id == entry_id), window_s=4.0)
    now = 1_800_000_000.0
    groups, arrivals = history_of("fixture/x", "part", STATE_FED[entry_id], 6.0, 50.0, now)
    inputs = sd.Inputs(state_arrivals={"part": arrivals}, has_state=True)
    status = runner.evaluate(binding, "fixture/x", "x", groups, now, inputs=inputs)
    assert status.level in RAN and status.code not in ("NoSource", "NoInput", "NotEnoughHistory"), (entry_id, status.code, status.message)


def png(width: int, height: int, pixel, filter_type: int = 0) -> bytes:
    """An 8-bit RGB PNG, every row with ``filter_type`` (0 none, 1 Sub, 2 Up), as a writer would."""

    rows, previous = [], bytes(width * 3)
    for y in range(height):
        raw = bytes(channel for x in range(width) for channel in pixel(x, y))
        if filter_type == 1:
            raw = bytes((raw[i] - (raw[i - 3] if i >= 3 else 0)) & 0xFF for i in range(len(raw)))
        elif filter_type == 2:
            raw = bytes((raw[i] - previous[i]) & 0xFF for i in range(len(raw)))
        previous = bytes(channel for x in range(width) for channel in pixel(x, y))
        rows.append(bytes([filter_type]) + raw)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def checker(x, y):
    value = 255 if (x // 4 + y // 4) % 2 else 0
    return value, value, value


def frames_input(tmp_path: Path, data: bytes, fmt: str, now: float):
    (tmp_path / f"color.{fmt}").write_bytes(data)
    records = [Frame(path="", stream="color", file=f"color.{fmt}", format=fmt, width=32, height=32, stamp=now - 3 + i * 0.2,
                     seq=i, received=now - 3 + i * 0.2 + 0.01) for i in range(15)]
    return sd.Inputs(frames={"color": records}, image=lambda frame: tmp_path / frame.file, has_state=True)


@pytest.mark.parametrize("filter_type", [0, 1, 2])
def test_a_png_frame_is_decoded_with_the_stdlib_and_judged(tmp_path, filter_type):
    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == "camera.blur")
    now = 1_800_000_000.0
    sharp = runner.evaluate(binding, "cam", "cam", {}, now, inputs=frames_input(tmp_path, png(32, 32, checker, filter_type), "png", now))
    assert sharp.level in RAN and sharp.code not in ("NoSource", "NoDecoder"), (sharp.code, sharp.message)
    flat = runner.evaluate(binding, "cam", "cam", {}, now, inputs=frames_input(tmp_path, png(32, 32, lambda x, y: (90, 90, 90)), "png", now))
    # the headline is the variance of the Laplacian: a checkerboard is sharp, a flat grey frame has none
    assert sharp.measurements[0].value > flat.measurements[0].value == 0.0, (sharp.measurements, flat.measurements)


def test_a_jpeg_frame_is_not_run_with_the_decoder_it_lacks(tmp_path):
    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == "camera.blur")
    now = 1_800_000_000.0
    status = runner.evaluate(binding, "cam", "cam", {}, now, inputs=frames_input(tmp_path, b"\xff\xd8\xff\xe0 not decoded", "jpeg", now))
    assert (status.level, status.code) == ("unknown", "NoDecoder") and "JPEG" in status.message and "PNG" in status.message


def test_frames_are_decoded_on_the_beat_never_on_the_watch_path(fed, monkeypatch):
    calls = []
    original = sd.decode_image
    monkeypatch.setattr(sd, "decode_image", lambda *args, **kwargs: calls.append(1) or original(*args, **kwargs))
    camera = fed.paths["camera_rgb"]
    end = time.monotonic() + 1.5
    while time.monotonic() < end:  # frames arrive and are absorbed; nothing decodes them
        fed.watch_heartbeat(camera)
        time.sleep(0.25)
    assert calls == []
    fed.run_stock_diagnostics()
    assert len(calls) == 1  # one binding reads images (camera.blur), once per beat
