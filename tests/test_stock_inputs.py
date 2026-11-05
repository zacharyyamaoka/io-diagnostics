"""Every default binding is fed from what a RobotDx entity actually publishes, or says which source it lacks, as one of two codes (Codex
audit 2026-11-04 round 3, gap 2: "distinguish genuinely absent measurements from missing producer wiring"):

- ``NotMeasured``: no instrument on this entity can produce the input (no IMU, no hyperspectral camera, no pick log, no fiducial tag);
- ``NotWired``: the data exists, or an existing producer (the moteus driver, intel_rgbd, the rack's recorded runs) could publish it, but
  nothing maps it onto the binding yet.

Both are unknown and never OK, with "no <input> source on this entity: <what would provide it>" and ``dx.gap`` = not_measured | not_wired.
Round 2's list (audit 2026-10-29 r2, gap 1) is the ruler this replaced; reports/media/audits/2026-11-04-sim2real-three-areas-r3.md in bam_ws.

The fixture entity of each part type publishes what RobotDx's real entity of that type publishes today, through the real host:

- bldc_driver: the state fixture's CAN 1 (a moteus-like board): State (position ... fault), configuration with expected values, its comms
  status's reply counts (queries / replies, as moteus's per-board status), a driver fault the fault memory confirms;
- camera_rgb + camera_rgbd: the state fixture's camera (a RealSense-like D405): State and colour + depth frames, JPEG files in the frame
  directory, as the real intel_rgbd driver writes them (STATE_FRAME_FORMAT=jpeg);
- the composites of every other part type (composition.toml): their members' levels and events; the arm also runs the arm composite plugin
  (its own State: joint angles, end effector x / y). The deployment holds the servo and the arm. The servo and the encoder read the State
  of the one board under them (a moteus reads its own encoder);
- the rack archive (a bam_rig-shaped directory): CAN 1's recorded run (bam_rack_twin npz + sidecar) feeds the servo's tracking, stop and
  hold bindings, the newest acceptance (bam_rack_twin.acceptance/1) the deployment's gate matrix;
- the Computer plugin's "Temperature CPU" status on the host feeds the deployment's rack.host.

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
REALSENSE, BOARD_SERIAL = "218622274411", "9A31"  # the fixture camera's SDK serial (STATE_REALSENSE_SERIAL), CAN 1's board serial
LIVE = sorted(entry.id for entry in catalog.ENTRIES if entry.consumers in dd.LIVE)
RAN = ("ok", "warn", "error")  # pass / warn / fail as a record's level (an agent's or proposed limit turns a fail into a warn)

# The bindings no RobotDx entity has a source for yet, each with a word its reason must name (what would provide it), split by why.
# Everything else must evaluate on the fixture entities above.
NOT_MEASURED = {
    # no instrument on the entity can produce the input: no such sensor, no log of picks, no fiducial tag mounted in view
    "camera.extrinsic_drift": "tag", "camera.tag_detect": "tag", "arm.calibration_drift": "tag",
    "hsi.snr": "cube", "hsi.references": "white", "hsi.spectral_angle": "spectra", "hsi.frame_rate": "per-frame stamps",
    "imu.static_bias": "gyro", "imu.gravity": "accel", "imu.rate": "IMU",
    "ft.known_mass": "mass", "ft.zero_drift": "force", "ft.noise": "force", "ft.saturation": "status codes",
    "gripper.cycle": "width", "gripper.detect": "truth", "gripper.grasp_success": "grasp", "gripper.compute_time": "compute",
    "gripper.force": "force",
    "pick.sppm": "pick", "pick.vs_human": "human", "pick.rate": "pick", "pick.mtti": "intervention", "pick.grasp_pose_error": "grasp pose",
    "rack.throughput": "material",
    # round 4 (Codex audit 2026-11-04 r4, gap 1) reclassified these four with their reasons: the instrument is a physical setup that
    # does not exist yet (hard stops for homing; a flat target at a measured distance for Intel's depth-quality metrics)
    "encoder.homing": "hard stop", "depth.plane_rms": "flat target", "depth.fill_rate": "flat target", "depth.temporal_noise": "flat target",
}
NOT_WIRED: dict[str, str] = {}  # round 4 wired or reclassified all fourteen
# wired in round 3 (gap 2) and round 4 (gap 1), each from an existing producer; they must now run on the fixture
WIRED = {"encoder.standstill_noise": "the board's State", "encoder.validity": "the board's State", "servo.tracking_rms": "a recorded run",
         "servo.tracking_peak": "a recorded run", "servo.stop_error": "a recorded run", "servo.hold_effort": "a recorded run's holds",
         "rack.gate_matrix": "the rack acceptance", "rack.host": "the Computer's CPU temperature",
         "camera.frame_rate": "a recorded RealSense capture's hardware stamps", "depth.health_check": "intel_rgbd's calibrate run",
         "servo.torque_truth_kt": "a moteus_tool calibration log vs the board's motor.Kv", "servo.live_reid": "the calibration follow-up",
         "gearbox.wear": "the calibration round vs the model card", "arm.tool_error_at_stop": "the UR5e sim cell",
         "arm.path_tracking": "the UR5e sim cell", "arm.completion_time": "the UR5e sim cell", "arm.overshoot": "the UR5e sim cell",
         "arm.hold_effort": "the UR5e sim cell"}
NO_SOURCE = {**NOT_MEASURED, **NOT_WIRED}
CODE_OF = {**{entry_id: "NotMeasured" for entry_id in NOT_MEASURED}, **{entry_id: "NotWired" for entry_id in NOT_WIRED}}
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
        f'matches = ["usb:{JOINT[0]}:{JOINT[1]}"]\nenv = {{ STATE_PERIOD_S = "0.05", STATE_NODES = "12", STATE_REPLY_COUNTS = "1", STATE_MOTOR_KV = "100.0" }}\n')
    (directory / "state_camera.toml").write_text(
        f'name = "state_camera"\ncommand = ["{{python}}", "{FIXTURES}/state_plugin.py", "--role", "camera"]\ntimeout_s = 10\nperiod_s = 30\n'
        f'matches = ["usb:{CAMERA[0]}:{CAMERA[1]}"]\nenv = {{ STATE_FRAME_FORMAT = "jpeg", STATE_REALSENSE_SERIAL = "{REALSENSE}" }}\n')
    (directory / "state_arm.toml").write_text(
        f'name = "state_arm"\ncommand = ["{{python}}", "{FIXTURES}/arm_plugin.py"]\ntimeout_s = 10\nperiod_s = 30\n'
        f'matches = ["composite:arm"]\nenv = {{ ARM_PERIOD_S = "0.05" }}\n')
    (tmp / "expected.toml").write_text(composition())
    config = tmp / "config"
    config.mkdir()
    (config / sd.DIAGNOSTICS_FILE).write_text(person_file())
    devices = [usb_device("9-1", *JOINT, label="fdcanusb"), usb_device("9-3", *CAMERA, label="camera")]
    archive = rack_archive(tmp / "bam_rig")
    calibrations, cells = moteus_calibrations(tmp / "calibrations"), sim_cells(tmp / "sim_cells")
    host = Host(rack_archive=archive, moteus_calibrations=calibrations, sim_cells=cells, runs_dir=tmp / "runs", plugins_dir=directory, clock=Clock(), os_source=FakeOs(standard_skeleton(*devices)), expected_file=tmp / "expected.toml",
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
    encoder_board = host.layer.device_paths["usb:9-1"] + f"/can/{COMPOSITES['encoder'][1][0]}"
    watched = [board, camera, host.paths["arm"], encoder_board]

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
    computer_temperatures(host)
    calibrate_run(host, camera)
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
    assert set(NO_SOURCE) <= set(LIVE) and len(LIVE) == 61 and not set(NOT_MEASURED) & set(NOT_WIRED)
    assert set(WIRED) <= set(FED)
    # runnable / not_wired / not_measured out of 61: round 2 14 / 22 / 25, round 3 22 / 14 / 25, round 4 32 / 0 / 29
    assert (len(FED), len(NOT_WIRED), len(NOT_MEASURED)) == (32, 0, 29), (len(FED), len(NOT_WIRED), len(NOT_MEASURED))


def test_every_source_says_which_gap_its_absence_is():
    for entry_id in LIVE:
        source = sd.SOURCES[entry_id]
        assert source.gap in ("not_wired", "not_measured"), entry_id
        if entry_id in NO_SOURCE:
            assert source.gap == ("not_measured" if entry_id in NOT_MEASURED else "not_wired"), entry_id


@pytest.mark.parametrize("entry_id", LIVE)
def test_every_default_binding_is_fed_or_names_the_source_it_lacks(fed, entry_id):
    status = status_of(fed, entry_id)
    if entry_id in NO_SOURCE:
        assert (status.level, status.code) == ("unknown", CODE_OF[entry_id]), (entry_id, status.code, status.message)
        assert status.message.startswith("no ") and " source on " in status.message, status.message
        assert NO_SOURCE[entry_id] in status.message, (entry_id, status.message)
        assert dict(status.values)["dx.gap"] == ("not_measured" if entry_id in NOT_MEASURED else "not_wired"), entry_id
    else:
        assert status.level in RAN, (entry_id, status.level, status.code, status.message)
        assert status.code not in ("NoInput", "NoSource", "NotWired", "NotMeasured", "NoState", "NotEnoughHistory", "NoFrames", "NoDecoder"), \
            (entry_id, status.code, status.message)
        assert sd.window_of(status) is not None, entry_id  # the window it ran on, for the drill-in


def test_no_binding_is_ever_a_silent_no_input(fed):
    assert not [sd.binding_of(status) for status in fed.statuses if status.code == "NoInput"]


def test_a_no_source_row_shows_where_the_entity_reports_state_and_stays_quiet_where_it_reports_none(fed):
    arm = fed.paths["arm"]
    arm_checks = {check["name"]: check for check in fed.node(arm)["checks"]}
    drift = next(check for name, check in arm_checks.items() if name.endswith(catalog.entry("arm.calibration_drift").title))
    assert (drift["level"], drift["code"]) == ("unknown", "NotMeasured") and "tag" in drift["message"]
    # the imu composite reports no State of its own (no composite plugin, and an IMU is not its board): its rows post nothing
    imu_checks = [check["name"] for check in fed.node(fed.paths["imu"])["checks"]]
    assert not any(name.endswith(catalog.entry("imu.gravity").title) for name in imu_checks), imu_checks


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
        assert source["gap"] in ("not_wired", "not_measured"), item["id"]
        assert item["status"] is not None, item["id"]  # the source state, even where no row is posted
        if item["id"] in NO_SOURCE:
            assert item["status"]["code"] == CODE_OF[item["id"]] and NO_SOURCE[item["id"]] in item["status"]["message"], item["id"]


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


def jpeg(width: int, height: int, pixel, mode: str = "RGB") -> bytes:
    """A baseline JPEG, encoded with Pillow as the intel_rgbd driver writes its frames (entity_page.save_jpeg)."""

    import io

    from PIL import Image

    image = Image.new(mode, (width, height))
    image.putdata([pixel(x, y) if mode == "RGB" else pixel(x, y)[0] for y in range(height) for x in range(width)])
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    return out.getvalue()


@pytest.mark.parametrize("mode", ["RGB", "L"])
def test_a_jpeg_frame_is_decoded_and_judged(tmp_path, mode):
    # WHY this replaced test_a_jpeg_frame_is_not_run_with_the_decoder_it_lacks (round 3, gap 2): the configured camera driver writes JPEG,
    # so a NoDecoder there meant camera.blur never ran on the real rack
    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == "camera.blur")
    now = 1_800_000_000.0
    sharp = runner.evaluate(binding, "cam", "cam", {}, now, inputs=frames_input(tmp_path, jpeg(32, 32, checker, mode), "jpeg", now))
    assert sharp.level in RAN and sharp.code not in ("NoSource", "NoDecoder"), (sharp.code, sharp.message)
    flat = runner.evaluate(binding, "cam", "cam", {}, now, inputs=frames_input(tmp_path, jpeg(32, 32, lambda x, y: (90, 90, 90), mode),
                                                                                "jpeg", now))
    assert isinstance(sharp.measurements[0].value, float) and sharp.measurements[0].value > 100 * max(flat.measurements[0].value, 1.0)


def test_a_jpeg_frame_decodes_to_the_same_grey_as_its_png(tmp_path):
    import io

    import numpy as np
    from PIL import Image

    data = jpeg(32, 32, checker)
    pixels = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), float)
    grey = sd.decode_image(data, "jpeg")
    assert grey.shape == (32, 32)
    assert np.allclose(grey, 0.299 * pixels[..., 0] + 0.587 * pixels[..., 1] + 0.114 * pixels[..., 2])  # BT.601, as the PNG path


def test_a_jpeg_frame_without_pillow_is_not_run_with_the_decoder_it_lacks(tmp_path, monkeypatch):
    import builtins

    real = builtins.__import__

    def no_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ModuleNotFoundError("No module named 'PIL'", name="PIL")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pillow)
    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == "camera.blur")
    now = 1_800_000_000.0
    status = runner.evaluate(binding, "cam", "cam", {}, now, inputs=frames_input(tmp_path, jpeg_bytes_without_pillow(), "jpeg", now))
    assert (status.level, status.code) == ("unknown", "NoDecoder") and "JPEG" in status.message and "Pillow" in status.message


def jpeg_bytes_without_pillow() -> bytes:
    return b"\xff\xd8\xff\xe0 a JPEG this host cannot open"


# ----- the wirings from existing producers (round 3, gap 2) ---------------------------------------------------------------------------
def waypoints_run(seconds_per_hold: float = 1.5, rate_hz: float = 100.0, error_rad: float = 0.0005):
    """A waypoints tape as bam_rack_twin.hardware records it: holds at 0, 0.1, 0.2, 0.1 rad joined by 0.5 s ramps; the joint lags
    2 mrad on a ramp and settles to ``error_rad`` in a hold."""

    import numpy as np

    targets, pieces = [0.0, 0.1, 0.2, 0.1], []
    for index, target in enumerate(targets):
        hold = np.full(int(seconds_per_hold * rate_hz), target)
        pieces.append((hold, np.zeros_like(hold), np.full_like(hold, error_rad)))
        if index + 1 < len(targets):
            ramp = np.linspace(target, targets[index + 1], int(0.5 * rate_hz), endpoint=False)
            pieces.append((ramp, np.full_like(ramp, (targets[index + 1] - target) / 0.5), np.full_like(ramp, 0.002)))
    q_target = np.concatenate([piece[0] for piece in pieces])
    qd_target = np.concatenate([piece[1] for piece in pieces])
    q = q_target - np.concatenate([piece[2] for piece in pieces])
    t = np.arange(len(q)) / rate_hz
    n = len(t)
    return {"t": t, "wall": 1_793_000_000.0 + t, "q_target": q_target, "qd_target": qd_target, "q": q, "qd": qd_target.copy(),
            "tau": np.full(n, 0.3), "mode": np.full(n, 10.0), "fault": np.zeros(n), "voltage": np.full(n, 24.0),
            "i_q": np.full(n, 1.0), "temp_c": np.full(n, 30.0), "i_d": np.zeros(n), "power_w": np.zeros(n), "rotor_rev": np.zeros(n),
            "ff_cog_nm": np.zeros(n)}


def gates(station: str, statuses: dict[str, str]) -> list[dict]:
    # WHY one value a string: the real acceptance writes T11's count as "18/25" (acceptance_limits_explicit.json), never a float
    return [{"id": gate, "name": gate.lower(), "status": status, "value": "18/25" if gate == "T8" else 1.0, "target": "fixture",
             "detail": f"CAN {station} {gate}"} for gate, status in statuses.items()]


def rack_archive(root: Path) -> Path:
    """A bam_rig-shaped archive: CAN 1's run, a NEWER CAN 11 run (can1 must never match can11), an older and a newer acceptance and an
    even newer file of another schema (ignored)."""

    import os

    import numpy as np

    day = root / "rack_2026-11-01"
    for can_id, folder, mtime in ((1, "can1_waypoints_fixture", 1_793_000_100), (11, "can11_waypoints_fixture", 1_793_000_200)):
        runs = day / folder / "runs"
        runs.mkdir(parents=True)
        np.savez(runs / f"s02_can{can_id}_waypoints.npz", **waypoints_run())
        (runs / f"s02_can{can_id}_waypoints.json").write_text(json.dumps({"can_id": can_id, "kind": "waypoints", "args": {}}))
        for file in runs.iterdir():
            os.utime(file, (mtime, mtime))
    analysis = day / "analysis"
    analysis.mkdir()
    old = {"1": gates("1", {f"T{i}": "fail" for i in range(1, 9)})}
    new = {"1": gates("1", {"T1": "pass", "T2": "fail", "T3": "pass", "T4": "partial", "T5": "pass", "T6": "pass", "T7": "pass",
                            "T8": "not_run"}),
           "12": gates("12", {f"T{i}": "pass" for i in range(1, 9)}),
           "99": gates("99", {f"T{i}": "pass" for i in range(1, 9)})}  # a station not in the fixture's composite
    for name, stamp, stations in (("acceptance_old.json", "2026-10-28T10:00:00Z", old), ("acceptance_new.json", "2026-10-29T22:17:33Z", new)):
        (analysis / name).write_text(json.dumps({"schema": "bam_rack_twin.acceptance/1", "generated_at": stamp,
                                                 "stations": {key: {"facts": {}, "gates": value} for key, value in stations.items()}}))
    (analysis / "acceptance_extra_z.json").write_text(json.dumps({"stations": {"1": {"gates": []}}}))
    # round 4: the calibration follow-up of CAN 1 (live_reid), the model card and a later calibration round of CAN 2 (gearbox.wear), a
    # RealSense capture of the fixture camera (camera.frame_rate)
    rows = [{"run": f"{day}/can1_heldout/runs/s0{i}_can1_waypoints.npz", "class": cls, "seed": i, "q_rms_mrad": error}
            for i, (cls, error) in enumerate((("pickplace", 4.0), ("pickplace", 4.4), ("sweep", 5.0), ("jog", 3.9)), 1)]
    (analysis / "followup_scored_can1.json").write_text(json.dumps({
        "schema": "bam_rack_twin.calibration_followup/1", "scored_at": "2026-11-01T08:00:00Z", "station": "can1", "declared_pick": "A",
        "floor": {"floor_mrad": 3.0, "repeats": 5, "session": f"{day}/can1_floor"},
        "variants": {"A": {"rows": rows}, "C": {"rows": [{**row, "q_rms_mrad": 99.0} for row in rows]}}}))
    cards = analysis / "model_cards"
    cards.mkdir()
    (cards / "can2.json").write_text(json.dumps({"schema": "bam-rack-twin/model-card-1", "can_id": 2, "terms": [
        {"term": "kv", "value": 112.9, "unit": "rpm/V", "status": "board"},
        {"term": "coulomb", "value": 0.8, "unit": "N.m", "status": "measured"},
        {"term": "backlash", "value": 4.5, "unit": "arcmin", "status": "refused"}]}))
    os.utime(cards / "can2.json", (1_793_000_000, 1_793_000_000))  # 2026-10-28: before the calibration round below
    (analysis / "calibration_can2.json").write_text(json.dumps({
        "schema": "bam_rack_twin.calibration_round/1", "station": "can2", "identified_at": "2026-11-02T00:00:00Z",
        "point": {"coulomb_nm": 0.76, "viscous_nm_s_rad": 0.15}}))
    cameras = day / "session_01" / "cameras"
    cameras.mkdir(parents=True)
    hw_ms = 1000.0 + np.arange(90) * (1000.0 / 30.0)
    np.savez(cameras / f"cam_{REALSENSE}.npz", frames=np.zeros((90, 2, 2), np.uint8), wall=1_793_000_000.0 + hw_ms / 1000.0, hw_ms=hw_ms,
             fps=np.int64(30), serial=np.str_(REALSENSE), recorder=np.str_("bam_rack_twin.hardware.record 0.1.0"))
    return root


def moteus_calibrations(root: Path) -> Path:
    """moteus_tool's calibration logs (~/.cache/moteus-firmware/calibrations/<date>-<adapter>-can<N>-<board>/moteus-cal-*.log): one
    of CAN 1's board (serial 9A31) measuring Kv 99, and a NEWER one of another board on CAN 1 (never used: the serial decides)."""

    for folder, serial, kv, stamp in (("2026-11-01-fda81d29-can1-c1-fixture", BOARD_SERIAL, 99.0, "2026-11-01 10:00:00.0"),
                                      ("2026-11-03-fda81d29-can1-c1-other", "ZZZZ", 150.0, "2026-11-03 10:00:00.0")):
        (root / folder).mkdir(parents=True)
        (root / folder / f"moteus-cal-{serial}-x.log").write_text(json.dumps({
            "timestamp": stamp, "device_info": {"serial_number": serial, "uuid": "x"}, "kv": kv, "winding_resistance": 0.2}))
    return root


def arm_payload():
    """The traj_compare report payload of a UR5e sim cell (traj_integration_tests ur_compare): a joint reach that steps every joint
    0 -> 0.5 rad and holds, the sim overshooting 4 % and settling; and a Cartesian move that ends in a 0.6 s dwell, the sim 1 mm off."""

    import numpy as np

    t = np.round(np.arange(0, 3.0, 0.01), 4)
    ramp = np.clip(t / 1.0, 0, 1)
    target = 0.5 * (10 * ramp ** 3 - 15 * ramp ** 4 + 6 * ramp ** 5)
    late = np.clip((t - 0.05) / 1.0, 0, 1)
    sim = 0.5 * (10 * late ** 3 - 15 * late ** 4 + 6 * late ** 5) + 0.02 * np.exp(-np.maximum(t - 1.05, 0) * 6) * np.sin(
        np.maximum(t - 1.05, 0) * 12) * (t > 1.05)
    tau_target = 2.0 + 0.0 * t
    tau_sim = tau_target + 0.3 * np.sin(t * 7)
    reach = {"name": "joint_reach", "has_cartesian": False, "plot_t": t.tolist(), "joint_names": [f"j{i}" for i in range(6)],
             "situations": [
                 {"name": "target", "role": "target", "plot": {"q": [target.tolist()] * 6, "tau": [tau_target.tolist()] * 6}, "three": None},
                 {"name": "sim_a", "role": "situation", "title": "Sim A", "plot": {"q": [sim.tolist()] * 6, "tau": [tau_sim.tolist()] * 6},
                  "three": None}]}
    move = np.clip(t / 2.4, 0, 1)
    tp = np.stack([0.4 + 0.2 * move, 0.1 * move, 0.3 + 0 * t], axis=1)
    p = tp + np.array([0.001, 0.0, 0.0])
    track = {"name": "conveyor_track", "has_cartesian": True, "plot_t": t.tolist(), "joint_names": [f"j{i}" for i in range(6)],
             "situations": [
                 {"name": "target", "role": "target", "plot": {"q": [move.tolist()] * 6, "tau": [tau_target.tolist()] * 6},
                  "three": {"t": t.tolist(), "p": tp.tolist(), "quat": [[1, 0, 0, 0]] * len(t), "tp": None, "tquat": None}},
                 {"name": "sim_a", "role": "situation", "title": "Sim A", "plot": {"q": [move.tolist()] * 6, "tau": [tau_sim.tolist()] * 6},
                  "three": {"t": t.tolist(), "p": p.tolist(), "quat": [[1, 0, 0, 0]] * len(t), "tp": tp.tolist(),
                            "tquat": [[1, 0, 0, 0]] * len(t)}}]}
    return {"title": "UR5e A/B", "comparisons": [track, reach]}


def sim_cells(root: Path) -> Path:
    folder = root / "ur_compare_2026-11-01"
    folder.mkdir(parents=True)
    (folder / "report.html").write_text('<!doctype html><title>UR</title><script type="application/json" id="traj-compare-data">'
                                        + json.dumps(arm_payload()).replace("</", "<\\/") + "</script>")
    return root


def calibrate_run(host, camera: str, health_before: float = 0.41, health_after: float = 0.12, wrote: bool = True):
    """intel_rgbd's calibrate act as the host keeps it on disk (docs/runs-v1.md): its ActionCompleted carries the on-chip calibration's
    health before and after (intel_rgbd.diagnostics.run_on_chip_calibration)."""

    now = host.clock.now()
    run = {"id": "20261101T080000-cal1", "path": camera, "device_id": "usb:9-3", "device_path": camera, "plugin": "state_camera",
           "action": "calibrate", "title": "Calibrate…", "label": "camera · Calibrate", "danger": "changes", "node": None, "new_id": None,
           "argv": ["act"], "started": now - 40, "ended": now - 10, "state": "done", "exit_code": 0, "elapsed_s": 30.0,
           "result": {"outcome": "ok", "returncode": 0, "records": [
               {"record": "event", "code": "ActionCompleted", "severity": "info", "stamp": now - 10, "path": camera,
                "message": f"calibrate: health {health_before:+.2f} -> {health_after:+.2f}",
                "args": {"realsense_serial": REALSENSE, "health_before": health_before, "health_after": health_after, "wrote": wrote}}]}}
    host.runs.directory.mkdir(parents=True, exist_ok=True)
    (host.runs.directory / f"{run['id']}.json").write_text(json.dumps(run))
    host.runs._load()  # WHY the registry's own loader: a run finished by an earlier host is read exactly so (terminal.RunRegistry)
    return run


def computer_temperatures(host, seconds: float = 6.0):
    """The Computer plugin's CPU temperature status on the host entity (computer_diagnostics.temperatures), at 2 Hz."""

    from contract.record import DiagnosticStatus
    from io_diagnostics import tree

    now = host.clock.now()
    for index in range(int(seconds * 2) + 1):
        at = now - seconds + index / 2
        status = DiagnosticStatus(level="ok", name="Temperature CPU (k10temp)", path=tree.HOST_PATH, message="CPU Tctl 55 °C",
                                  measurements=(Measurement("Tctl", 55.0 + 0.1 * index, "Cel", warn_high=85.0, error_high=95.0),
                                                Measurement("Tccd1", 48.0, "Cel")),
                                  stamp=at, received=at, expected_period_s=1.0, source="computer")
        host.history.observe([status], at)


def test_the_archive_finds_a_joints_newest_run_by_its_can_id_never_a_prefix(tmp_path):
    archive = sd.RackArchive(rack_archive(tmp_path))
    run = archive.newest_run(1)
    assert run is not None and run.run.endswith("s02_can1_waypoints.npz") and run.labels["can_id"] == 1
    assert archive.newest_run(11).run.endswith("s02_can11_waypoints.npz")
    assert archive.newest_run(2) is None


def test_the_archive_reads_the_newest_acceptance_by_its_generated_at(tmp_path):
    path, document = sd.RackArchive(rack_archive(tmp_path)).newest_acceptance()
    assert path.name == "acceptance_new.json" and document["generated_at"] == "2026-10-29T22:17:33Z"


def servo_inputs(tmp_path, can_ids=(1,)):
    root = tmp_path / "bam_rig"
    return sd.Inputs(composite=True, plugin=False, can_ids=tuple(can_ids), archive=sd.RackArchive(root if root.is_dir() else rack_archive(root)))


@pytest.mark.parametrize("entry_id", ["servo.tracking_rms", "servo.tracking_peak", "servo.stop_error", "servo.hold_effort"])
def test_a_servo_is_judged_on_its_newest_recorded_run_when_its_state_has_no_target(tmp_path, entry_id):
    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == entry_id)
    status = runner.evaluate(binding, "fixture/servo", "servo", {}, 1_800_000_000.0, inputs=servo_inputs(tmp_path))
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (entry_id, status.code, status.message)
    window = sd.window_of(status)
    assert window["source"] == "recorded_run" and window["run"].endswith("s02_can1_waypoints.npz"), window
    assert window["from"] == pytest.approx(1_793_000_000.0) and window["to"] > window["from"]  # the run's own wall clock, not now
    if entry_id == "servo.hold_effort":  # four holds, each settled to 0.5 mrad: all converged
        assert status.measurements[0].value == pytest.approx(1.0) and status.level == "ok", status.measurements
    if entry_id == "servo.stop_error":  # three stops followed by motion, 0.5 mrad each: within 3.6, but the run is the encoder side
        assert status.measurements[0].value == pytest.approx(0.5, rel=1e-3), status.measurements
        assert (status.level, status.code) == ("warn", "MeasuredOnEncoderSide"), (status.code, status.message)


def test_a_hold_that_does_not_settle_within_1_mrad_counts_against_the_share(tmp_path):
    import numpy as np

    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == "servo.hold_effort")
    root = rack_archive(tmp_path)
    run = waypoints_run(error_rad=0.003)  # every hold ends 3 mrad off
    file = next(root.glob("rack_*/can1_*/runs/*.npz"))
    np.savez(file, **run)
    status = runner.evaluate(binding, "fixture/servo", "servo", {}, 1_800_000_000.0,
                             inputs=sd.Inputs(composite=True, plugin=False, can_ids=(1,), archive=sd.RackArchive(root)))
    # none converged: below the 0.8 floor, a warning while that floor is an agent's proposal (catalog.advisory), never OK
    assert status.measurements[0].value == pytest.approx(0.0) and status.level in ("warn", "error"), (status.code, status.message)
    assert "Below" in status.code, status.code


def test_a_servo_with_no_recorded_run_says_it_is_not_wired(tmp_path):
    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == "servo.tracking_rms")
    status = runner.evaluate(binding, "fixture/servo", "servo", {}, 1_800_000_000.0, inputs=servo_inputs(tmp_path, can_ids=(5,)))
    assert (status.level, status.code) == ("unknown", "NotWired") and "recorded run" in status.message, status.message
    no_archive = runner.evaluate(binding, "fixture/servo", "servo", {}, 1_800_000_000.0,
                                 inputs=sd.Inputs(composite=True, plugin=False, can_ids=(1,)))
    assert no_archive.code == "NotWired", no_archive.message


def test_live_state_with_the_target_wins_over_a_recorded_run(tmp_path):
    runner = sd.StockDiagnostics(None)
    binding = dataclasses.replace(next(b for b in runner.bindings if b.id == "servo.tracking_rms"), window_s=4.0)
    now = 1_800_000_000.0
    groups, arrivals = history_of("fixture/x", "part", STATE_FED["servo.tracking_rms"], 6.0, 50.0, now)
    inputs = dataclasses.replace(servo_inputs(tmp_path), state_arrivals={"part": arrivals}, has_state=True, composite=False, plugin=True)
    status = runner.evaluate(binding, "fixture/x", "x", groups, now, inputs=inputs)
    assert sd.window_of(status)["source"] == "state", sd.window_of(status)


@pytest.mark.parametrize("entry_id", ["encoder.standstill_noise", "encoder.validity"])
def test_an_encoder_reads_the_state_of_the_one_board_under_it(entry_id):
    runner = sd.StockDiagnostics(None)
    binding = dataclasses.replace(next(b for b in runner.bindings if b.id == entry_id), window_s=4.0)
    now = 1_800_000_000.0
    leaf, _arrivals = history_of("adapter/can/3", "motor", STATE_FED[entry_id], 6.0, 50.0, now)
    status = runner.evaluate(binding, "fixture/encoder", "encoder", {}, now, inputs=sd.Inputs(composite=True, plugin=False, leaf_state=leaf))
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (entry_id, status.code, status.message)
    assert sd.window_of(status)["source"] == "leaf_state"


@pytest.mark.parametrize("entry_id", ["imu.static_bias", "ft.saturation", "gripper.cycle"])
def test_a_sensor_composite_never_borrows_its_boards_state(entry_id):
    # WHY: a moteus board's State is its joint's and its onboard encoder's; an IMU, FT sensor or gripper over a board is not that board
    runner = sd.StockDiagnostics(None)
    binding = dataclasses.replace(next(b for b in runner.bindings if b.id == entry_id), window_s=4.0)
    now = 1_800_000_000.0
    keys = {**STATE_FED["encoder.validity"], **STATE_FED[entry_id]}
    leaf, _arrivals = history_of("adapter/can/5", "motor", keys, 6.0, 50.0, now)
    status = runner.evaluate(binding, "fixture/x", "x", {}, now, inputs=sd.Inputs(composite=True, plugin=False, leaf_state=leaf))
    assert (status.level, status.code) == ("unknown", "NotMeasured"), (entry_id, status.code, status.message)


def test_the_gate_matrix_reads_the_newest_acceptance_for_the_stations_in_the_composite(tmp_path):
    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == "rack.gate_matrix")
    status = runner.evaluate(binding, "fixture/rack", "rack", {}, 1_800_000_000.0, inputs=servo_inputs(tmp_path, can_ids=(1, 12, 8)))
    assert status.code not in sd.NO_SOURCE_CODES, status.message
    # CAN 1 passes T1, T3, T5, T6, T7 and CAN 12 all eight: 13 of 16; station 99 is not in the composite, CAN 8 is not in the acceptance
    assert status.measurements[0].value == 13 and status.level == "error", (status.measurements, status.message)
    window = sd.window_of(status)
    assert window["source"] == "acceptance" and window["file"].endswith("acceptance_new.json") and window["generated_at"].startswith("2026-10-29")
    none_here = runner.evaluate(binding, "fixture/rack", "rack", {}, 1_800_000_000.0, inputs=servo_inputs(tmp_path, can_ids=(7,)))
    assert none_here.code == "NotWired" and "CAN 7" in none_here.message, none_here.message


def r4_archive(tmp_path) -> "sd.RackArchive":
    root = tmp_path / "bam_rig"
    return sd.RackArchive(root if root.is_dir() else rack_archive(root), calibrations=moteus_calibrations(tmp_path / "calibrations"),
                          sim_cells=sim_cells(tmp_path / "sim_cells"))


def evaluate_one(entry_id, path, inputs, window_s=None):
    runner = sd.StockDiagnostics(None)
    binding = next(b for b in runner.bindings if b.id == entry_id)
    if window_s is not None:
        binding = dataclasses.replace(binding, window_s=window_s)
    return runner.evaluate(binding, path, path, {}, 1_800_000_000.0, inputs=inputs)


def test_camera_frame_rate_reads_the_newest_capture_of_its_realsense_serial(tmp_path):
    archive = r4_archive(tmp_path)
    status = evaluate_one("camera.frame_rate", "cam", sd.Inputs(has_state=True, realsense_serials=(REALSENSE,), archive=archive))
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (status.code, status.message)
    assert status.measurements[0].value == pytest.approx(30.0, rel=1e-3), status.measurements
    window = sd.window_of(status)
    assert window["source"] == "capture" and window["file"].endswith(f"cam_{REALSENSE}.npz"), window
    other = evaluate_one("camera.frame_rate", "cam", sd.Inputs(has_state=True, realsense_serials=("000000000000",), archive=archive))
    assert other.code == "NotWired" and "000000000000" in other.message, other.message


def test_depth_health_check_reads_the_drivers_newest_calibrate_run():
    run = {"path": "cam", "action": "calibrate", "ended": 1_799_999_000.0, "id": "r1", "result": {"records": [
        {"record": "event", "code": "ActionCompleted", "args": {"health_before": 0.41, "health_after": 0.12, "wrote": True}}]}}
    kept = {**run, "id": "r2", "ended": 1_799_999_500.0, "result": {"records": [
        {"record": "event", "code": "ActionCompleted", "args": {"health_before": 0.30, "health_after": 0.33, "wrote": False}}]}}
    status = evaluate_one("depth.health_check", "cam", sd.Inputs(has_state=True, action_runs=(run, kept)))
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (status.code, status.message)
    # the newest run kept the stored calibration (not improved), so the camera's health is the one it measured before: 0.30
    assert status.measurements[0].value == pytest.approx(0.30), status.measurements
    assert sd.window_of(status)["source"] == "action" and sd.window_of(status)["run"] == "r2"
    none = evaluate_one("depth.health_check", "cam", sd.Inputs(has_state=True))
    assert none.code == "NotWired" and "calibrate" in none.message, none.message


def test_servo_torque_truth_compares_the_boards_kv_with_its_calibration_log(tmp_path):
    from contract.record import Config

    inputs = sd.Inputs(composite=True, plugin=False, can_ids=(1,), archive=r4_archive(tmp_path), leaf_identity={"serial_number": BOARD_SERIAL},
                       leaf_configs=(Config(path="adapter/can/1", key="motor.Kv", value=100.0),))
    status = evaluate_one("servo.torque_truth_kt", "fixture/servo", inputs)
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (status.code, status.message)
    assert status.measurements[0].value == pytest.approx(0.99, rel=1e-6), status.measurements  # Kv_true / Kv_board, the board's own log
    assert sd.window_of(status)["source"] == "calibration_log" and "fixture" in sd.window_of(status)["file"]
    no_kv = evaluate_one("servo.torque_truth_kt", "fixture/servo", dataclasses.replace(inputs, leaf_configs=()))
    assert no_kv.code == "NotWired" and "motor.Kv" in no_kv.message, no_kv.message


def test_servo_live_reid_reads_the_declared_pick_of_the_newest_follow_up(tmp_path):
    status = evaluate_one("servo.live_reid", "fixture/servo", sd.Inputs(composite=True, plugin=False, can_ids=(1,), archive=r4_archive(tmp_path)))
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (status.code, status.message)
    assert next(m.value for m in status.measurements if m.key == "floor") == pytest.approx(3.0)
    # the declared pick A (4.0, 4.4, 5.0, 3.9 mrad over a 3.0 floor), never the other variant: median-high of the ratios, 4.4 / 3
    assert status.measurements[0].value == pytest.approx(4.4 / 3.0), status.measurements
    assert sd.window_of(status)["source"] == "followup" and sd.window_of(status)["variant"] == "A"


def test_gearbox_wear_compares_the_newest_calibration_round_with_the_model_card(tmp_path):
    status = evaluate_one("gearbox.wear", "fixture/gearbox", sd.Inputs(composite=True, plugin=False, can_ids=(2,), archive=r4_archive(tmp_path)))
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (status.code, status.message)
    assert status.measurements[0].value == pytest.approx(-5.0), status.measurements  # 0.76 vs the card's 0.8 N.m: -5 %, within 20 %
    window = sd.window_of(status)
    assert window["source"] == "model_card" and window["card"].endswith("can2.json") and window["round"].endswith("calibration_can2.json")


ARM = ("arm.tool_error_at_stop", "arm.path_tracking", "arm.completion_time", "arm.overshoot", "arm.hold_effort")


@pytest.mark.parametrize("entry_id", ARM)
def test_an_arm_is_judged_on_the_newest_ur5e_sim_cell_and_says_so(tmp_path, entry_id):
    status = evaluate_one(entry_id, "fixture/arm", sd.Inputs(composite=True, plugin=True, has_state=True, archive=r4_archive(tmp_path)))
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (entry_id, status.code, status.message)
    window = sd.window_of(status)
    assert window["source"] == "sim_cell" and window["file"].endswith("report.html") and window["situation"] == "sim_a", window
    assert status.message.startswith("UR5e sim cell (sim_a): "), status.message  # never mistaken for the arm's own measurement
    expected = {"arm.tool_error_at_stop": 1.0, "arm.path_tracking": None, "arm.completion_time": None, "arm.overshoot": None,
                "arm.hold_effort": None}[entry_id]
    if expected is not None:  # the sim sits 1 mm off the dwell
        assert status.measurements[0].value == pytest.approx(expected, rel=1e-3), status.measurements


def test_an_arm_without_a_sim_cell_is_not_wired():
    status = evaluate_one("arm.overshoot", "fixture/arm", sd.Inputs(composite=True, plugin=True, has_state=True))
    assert status.code == "NotWired" and "sim cell" in status.message, status.message


@pytest.mark.parametrize("entry_id", ["encoder.homing", "depth.plane_rms", "depth.fill_rate", "depth.temporal_noise"])
def test_the_reclassified_bindings_say_which_instrument_is_missing(entry_id):
    status = evaluate_one(entry_id, "x", sd.Inputs(has_state=True))
    assert (status.level, status.code) == ("unknown", "NotMeasured") and NOT_MEASURED[entry_id] in status.message, status.message


def test_the_rack_host_reads_the_computers_cpu_temperature():
    runner = sd.StockDiagnostics(None)
    binding = dataclasses.replace(next(b for b in runner.bindings if b.id == "rack.host"), window_s=4.0)
    now = 1_800_000_000.0
    series = [{"key": "Tccd1", "unit": "Cel", "points": [[now - 5 + i * 0.5, 48.0] for i in range(11)]},
              {"key": "Tctl", "unit": "Cel", "points": [[now - 5 + i * 0.5, 61.0] for i in range(11)]}]
    status = runner.evaluate(binding, "fixture/rack", "rack", {}, now,
                             inputs=sd.Inputs(composite=True, plugin=False, computer=sd.cpu_temperature(series)))
    assert status.level in RAN and status.code not in sd.NO_SOURCE_CODES, (status.code, status.message)
    assert sd.window_of(status)["source"] == "computer"
    assert any(m.key == "max" and m.value == pytest.approx(61.0) for m in status.measurements), status.measurements  # Tctl, not a CCD


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
