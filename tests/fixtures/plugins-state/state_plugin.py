"""Fixture plugin for the entity page (docs/state-config-v1.md): a joint-like device and a camera-like device that stream
live State and images through the ``watch`` verb. Never touches hardware; stdlib only.

    <python> state_plugin.py --role joint  describe
    <python> state_plugin.py --role joint  diagnose --device usb:1-3
    <python> state_plugin.py --role joint  watch --device usb:1-3 --path can/1
    <python> state_plugin.py --role camera watch --device usb:6-1 --path ""

``joint`` (matches the fixture's fdcanusb, usb:0483:5740): the adapter ``""``, a ``can`` bus and one servo ``can/1``. Its
watch prints a ``state`` record of the motor (position, velocity, torque, voltage, temperature, mode, fault with its
meaning) every ``$STATE_PERIOD_S`` (0.1 s). ``camera`` (usb:8086:0b5b): its watch writes a colour and a depth PNG into
``$IO_DIAGNOSTICS_FRAME_DIR`` every ``$STATE_FRAME_PERIOD_S`` (0.2 s) and prints a ``frame`` record for each.
Both print ``config`` records, some with an ``expected`` that differs, in ``diagnose``.

``$STATE_LOG`` (a file) gets one JSON line per verb (verb, argv, pid, the frame dir) so a test can see what ran.
``$STATE_HOLD_PORT`` set: ``watch`` prints a ``PortBusy`` status and exits (the plugin found the port held itself).
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
import time
import zlib

SCHEMA = "diagnostics-record/0"
ARGS = sys.argv[1:]
ROLE = ARGS[ARGS.index("--role") + 1] if "--role" in ARGS else "joint"
VERB = next((word for word in ARGS if word in ("describe", "diagnose", "watch", "act")), "")
PERIOD_S = float(os.environ.get("STATE_PERIOD_S", "0.1"))
FRAME_PERIOD_S = float(os.environ.get("STATE_FRAME_PERIOD_S", "0.2"))
WIDTH, HEIGHT = 160, 120
FAULT_MEANING = "motor driver fault on bridge enable"
MODE_MEANING = {0: "stopped", 10: "position"}
EXPECTED_SOURCE = "deployment record rack-2026-10-21"


_BASE = float(os.environ.get("IO_DIAGNOSTICS_NOW") or time.time())
_START = time.monotonic()


def clock() -> float:
    """The host's clock (a replayed fixture's recorded time, running on), so a stamp and the host's receive time agree."""

    return _BASE + (time.monotonic() - _START)


def arg(name: str) -> str | None:
    return ARGS[ARGS.index(name) + 1] if name in ARGS[:-1] else None


def emit(**record) -> None:
    sys.stdout.write(json.dumps({"schema": SCHEMA, **record}) + "\n")
    sys.stdout.flush()


def records():
    """Where a run's records go: the record fd the host gave it, else stdout."""

    fd = os.environ.get("IO_DIAGNOSTICS_RECORD_FD")
    return os.fdopen(int(fd), "w") if fd else sys.stdout


def emit_to(handle, **record) -> None:
    handle.write(json.dumps({"schema": SCHEMA, **record}) + "\n")
    handle.flush()


def log(**fields) -> None:
    path = os.environ.get("STATE_LOG")
    if path:
        with open(path, "a") as handle:
            handle.write(json.dumps({"verb": VERB, "role": ROLE, "argv": ARGS, "pid": os.getpid(), **fields}) + "\n")


# ----- the utilities (dry runs only: this plugin never changes or moves anything) -----------------------------------
ACTIONS = {
    "joint": [
        {"name": "check_comms", "title": "Check communication", "description": "ask every id on the bus who answers", "danger": "query",
         "scope": "device", "confirm": "Ask every id on {name}?", "kind": "touches"},
        {"name": "clear_faults", "title": "Clear faults", "description": "clear the latched fault of this board", "danger": "changes",
         "scope": "node", "confirm": "Clear the faults of CAN {id} on {name}?", "kind": "changes"},
        {"name": "calibrate", "title": "Run calibration…", "description": "spins the rotor to measure it", "danger": "moves", "scope": "node",
         "confirm": "Calibrate CAN {id} on {name}? The rotor spins.", "kind": "changes",
         "preconditions": [["joint_free", "CAN {id}'s joint is free to spin"]]},
        {"name": "set_config", "title": "Set configuration…", "description": "one setting, live (RAM)", "danger": "changes", "scope": "node",
         "confirm": "Set a setting on CAN {id} on {name}?", "kind": "changes"},
        {"name": "persist_config", "title": "Save configuration to the board…", "description": "make the live settings survive a restart",
         "danger": "changes", "scope": "node", "confirm": "Save the configuration of CAN {id} on {name}?", "kind": "changes"},
        {"name": "reset_config", "title": "Reset configuration…", "description": "put a setting back live", "danger": "changes", "scope": "node",
         "confirm": "Reset a setting on CAN {id} on {name}?", "kind": "changes"},
    ],
    "camera": [
        {"name": "check_firmware", "title": "Check firmware", "description": "read the installed firmware and compare", "danger": "query",
         "scope": "device", "confirm": "Read the firmware of {name}?", "kind": "touches"},
        {"name": "identify", "title": "Identify (snapshot)", "description": "save one picture to show which camera this is", "danger": "query",
         "scope": "device", "confirm": "Take a snapshot with {name}?", "kind": "touches"},
    ],
}


def actions_of(scope: str) -> list[dict]:
    return [action for action in ACTIONS[ROLE] if action["scope"] == scope]


# ----- describe ---------------------------------------------------------------------------------------------------
def describe() -> dict:
    answer = {"name": f"state_{ROLE}", "schema": SCHEMA, "record_version": "0.2", "actions": ACTIONS[ROLE]}
    if ROLE == "joint":
        answer.update(matches=["usb:0483:5740"], watch={"period_s": PERIOD_S, "gates": ["port_free"], "danger": "query"},
                      config_write={"gates": ["port_free"]})
    else:
        answer.update(matches=["usb:8086:0b5b"], watch={"period_s": FRAME_PERIOD_S, "gates": ["camera_free"], "danger": "query"},
                      streams=[{"path": "", "streams": [{"name": "color", "kind": "image", "title": "Color"},
                                                        {"name": "depth", "kind": "image", "title": "Depth", "colormap": "turbo"}]}])
    return answer


# ----- the joint's State ----------------------------------------------------------------------------------------
def motor(now: float) -> dict:
    position = 0.5 * math.sin(now * 1.3)
    velocity = 0.5 * 1.3 * math.cos(now * 1.3)
    return {"position": position, "velocity": velocity, "torque": 0.8 * math.sin(now * 2.1) + 0.05 * velocity,
            "voltage": 24.0 + 0.3 * math.sin(now * 0.7), "temperature": 38.0 + (now % 600) / 100.0, "mode": mode(), "fault": 33}


def motor_state(path: str, now: float, period: float | None) -> dict:
    values = motor(now)
    units = {"position": "rev", "velocity": "rev/s", "torque": "N.m", "voltage": "V", "temperature": "Cel"}
    measurements = [{"key": key, "value": round(values[key], 4), "unit": unit} for key, unit in units.items()]
    measurements[3]["warn_low"], measurements[3]["error_low"] = 20.0, 18.0
    measurements[4].update(warn_high=60.0, error_high=75.0)
    measurements.append({"key": "mode", "value": values["mode"], "meaning": MODE_MEANING.get(values["mode"], str(values["mode"]))})
    measurements.append({"key": "fault", "value": values["fault"], "meaning": FAULT_MEANING})
    return {"record": "state", "path": path, "name": "motor", "stamp": now, "measurements": measurements,
            **({"expected_period_s": period} if period else {})}


# ----- the camera's images ----------------------------------------------------------------------------------------
def png(rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


def turbo(t: float) -> tuple[int, int, int]:
    t = min(max(t, 0.0), 1.0)
    red = 0.13572138 + t * (4.61539260 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))))
    green = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))))
    blue = 0.10667330 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))))
    return tuple(int(255 * min(max(channel, 0.0), 1.0)) for channel in (red, green, blue))


def image(kind: str, seq: int) -> bytes:
    cx, cy = WIDTH / 2 + 40 * math.sin(seq * 0.25), HEIGHT / 2 + 25 * math.cos(seq * 0.19)
    rows = []
    for y in range(HEIGHT):
        row = bytearray()
        for x in range(WIDTH):
            near = math.hypot(x - cx, y - cy)
            if kind == "color":
                if near < 18:
                    pixel = (250, 244, 232)
                else:
                    pixel = (40 + x * 150 // WIDTH, 70 + y * 120 // HEIGHT, 140 - (x ^ y) % 3 * 8)
                if abs(x - (seq * 9) % WIDTH) < 2:
                    pixel = (255, 255, 255)
            else:
                pixel = turbo(0.15 + 0.6 * min(near, 80) / 80 + 0.2 * y / HEIGHT)
            row += bytes(pixel)
        rows.append(bytes(row))
    return png(rows)


def write_frame(folder: str, stream: str, seq: int) -> None:
    final = os.path.join(folder, f"{stream}.png")
    temporary = os.path.join(folder, f".{stream}.png.tmp")
    with open(temporary, "wb") as handle:
        handle.write(image(stream, seq))
    os.replace(temporary, final)  # atomic: a reader sees the old frame or the new one
    emit(record="frame", path="", stream=stream, file=f"{stream}.png", format="png", width=WIDTH, height=HEIGHT,
         stamp=clock(), seq=seq)


# ----- configuration: read-only on the camera, live-editable on the joint (docs/state-config-v1.md section 8) ----------
# The "board": live values (RAM) and stored values (flash) in $STATE_CONFIG_FILE, shared by every process of a test; the
# board's mode in $STATE_MODE_FILE (default 10 = position; a test writes 0 to stop it).
CONFIG = {
    "id.id": dict(value=1, unit="", expected=1, type="int", writable=False),
    "servo.max_current_A": dict(value=20.0, unit="A", expected=25.0, type="float", range=[0.0, 30.0], default=20.0, safety=True),
    "servo.default_timeout_s": dict(value=0.1, unit="s", expected=0.5, type="float", range=[0.0, 10.0], default=0.1),
    "servo.pid_position.kp": dict(value=12.0, unit="", expected=12.0, type="float", range=[0.0, 1000.0], default=12.0, safety=True),
    "servopos.position_min": dict(value=-0.5, unit="rev", expected=-0.5, type="float", range=[-10.0, 0.0], default=-0.5, safety=True),
    "servopos.position_max": dict(value=0.5, unit="rev", expected=0.5, type="float", range=[0.0, 10.0], default=0.5, safety=True),
    "servo.motor_position.output.reference_source": dict(value=0, unit="", type="enum", choices=[0, 1, 2], default=0),
    "aux1.pins.0.mode": dict(value="pwm", unit="", type="enum", choices=["pwm", "digital_out", "off"], default="off"),
}
for _row in CONFIG.values():
    _row.setdefault("writable", True)


def board() -> dict:
    path = os.environ.get("STATE_CONFIG_FILE")
    if path and os.path.exists(path):
        with open(path) as handle:
            return json.load(handle)
    values = {key: row["value"] for key, row in CONFIG.items()}
    return {"live": dict(values), "stored": dict(values)}


def save_board(state: dict) -> None:
    path = os.environ.get("STATE_CONFIG_FILE")
    if path:
        with open(path, "w") as handle:
            json.dump(state, handle)


def mode() -> int:
    path = os.environ.get("STATE_MODE_FILE")
    if path and os.path.exists(path):
        with open(path) as handle:
            return int(handle.read().strip() or 10)
    return 10


def config_record(path: str, key: str, state: dict, now: float) -> dict:
    row = CONFIG[key]
    record = {"record": "config", "path": path, "key": key, "value": state["live"][key], "stored": state["stored"][key], "unit": row["unit"],
              "type": row["type"], "writable": row["writable"], "default": row.get("default"), "stamp": now}
    for field in ("range", "choices"):
        if field in row:
            record[field] = row[field]
    if row.get("safety"):
        record["safety"] = True
    if row.get("expected") is not None:
        record["expected"], record["expected_source"] = row["expected"], EXPECTED_SOURCE
    return {name: value for name, value in record.items() if value is not None}


def configs(path: str, now: float) -> list[dict]:
    state = board()
    return [config_record(path, key, state, now) for key in CONFIG]


def camera_configs(now: float) -> list[dict]:
    return [{"record": "config", "path": "", "key": "depth.laser_power_mW", "value": 150, "unit": "mW", "group": "depth", "stamp": now,
             "expected": 150, "expected_source": EXPECTED_SOURCE},
            {"record": "config", "path": "", "key": "depth.exposure_us", "value": 8500, "unit": "us", "group": "depth", "stamp": now,
             "expected": 33000, "expected_source": "expected setup"},
            {"record": "config", "path": "", "key": "color.auto_exposure", "value": True, "group": "color", "stamp": now}]


# ----- the verbs --------------------------------------------------------------------------------------------------
def diagnose(device: str) -> None:
    now = clock()
    if ROLE == "joint":
        emit(record="entity", path="", name="fdcanusb fixture", kind="adapter",
             identity={"manufacturer": "mjbots", "model": "fdcanusb", "serial_number": "FDA81D29", "sw_version": "1.2.0"}, actions=actions_of("device"))
        emit(record="entity", path="can", name="CAN bus", kind="bus", via="")
        emit(record="entity", path="can/1", name="CAN 1", kind="servo", via="can", owner="state_joint",
             identity={"manufacturer": "mjbots", "model": "moteus c1", "model_id": "mjbots-moteus-c1", "serial_number": "9A31",
                       "sw_version": "1.0.0", "hw_version": "c1 r4.5"}, actions=actions_of("node"))
        emit(record="status", level="ok", name="fixture: ran", path="", stamp=now, expected_period_s=30)
        emit(record="status", level="ok", name="CAN 1: comms", path="can/1", message="answers in 1.2 ms", stamp=now, expected_period_s=30,
             values=[{"key": "bitrate", "value": "5 Mbit/s BRS"}])
        emit(record="status", level="error", name="CAN 1: driver", path="can/1", code="MoteusFault.33",
             message="the driver is in fault", stamp=now, expected_period_s=30,
             values=[{"key": "fault_code", "value": "33"}, {"key": "fault_meaning", "value": FAULT_MEANING},
                     {"key": "what_to_check", "value": "1. Check the motor phase wiring\n2. Clear faults, then try again"}])
        emit(record="status", level="ok", name="CAN 1: firmware", path="can/1", message="1.0.0 is the latest", stamp=now, expected_period_s=30)
        emit(**motor_state("can/1", now, 30))
        for row in configs("can/1", now):
            emit(**{key: value for key, value in row.items() if key != "schema"})
    else:
        emit(record="entity", path="", name="RealSense D405 fixture", kind="camera",
             identity={"manufacturer": "Intel", "model": "D405", "model_id": "intel-realsense-d405", "serial_number": "219323070276",
                       "sw_version": "5.16.0.1"}, actions=actions_of("device"))
        emit(record="status", level="ok", name="camera: firmware", path="", message="5.16.0.1 is the latest", stamp=now, expected_period_s=30)
        emit(record="status", level="ok", name="camera: comms", path="", message="answers", stamp=now, expected_period_s=30)
        emit(record="state", path="", name="camera", stamp=now, expected_period_s=30,
             measurements=[{"key": "asic temperature", "value": 41.5, "unit": "Cel", "warn_high": 80.0}, {"key": "frames dropped", "value": 0}])
        for row in camera_configs(now):
            emit(**{key: value for key, value in row.items() if key != "schema"})


def watch(path: str) -> None:
    if os.environ.get("STATE_HOLD_PORT"):
        emit(record="status", level="unknown", name="watch: port", path=path, code="PortBusy", stamp=clock(),
             message="port held by tview", values=[{"key": "port_holders", "value": "640623 tview"}])
        return
    folder = os.environ.get("IO_DIAGNOSTICS_FRAME_DIR", "")
    seq = 0
    next_frame = 0.0
    while True:
        now = clock()
        if ROLE == "joint":
            emit(**motor_state(path, now, 2.0))
        else:
            emit(record="state", path=path, name="camera", stamp=now, expected_period_s=2.0,
                 measurements=[{"key": "asic temperature", "value": round(41.5 + (now % 60) / 30, 2), "unit": "Cel", "warn_high": 80.0},
                               {"key": "frames dropped", "value": 0}])
            if folder and now >= next_frame:
                seq += 1
                write_frame(folder, "color", seq)
                write_frame(folder, "depth", seq)
                next_frame = now + FRAME_PERIOD_S
        time.sleep(PERIOD_S if ROLE == "joint" else min(PERIOD_S, 0.1))


def act(action: str) -> int:
    """A dry run answers for any action; a REAL one only for the three configuration writes, which change this fixture's own file."""

    if "--dry-run" in ARGS:
        emit(record="action-result", action=action, dry_run=True, command=["fixture-tool", f"--{action}"],
             steps=[{"purpose": "do it", "runs": "subprocess", "argv": ["fixture-tool", f"--{action}"]}], preconditions=[],
             confirm=f"Do {action}?", token=f"tok-{action}")
        return 0
    out = records()
    if action not in ("set_config", "persist_config", "reset_config"):
        emit_to(out, record="event", code="ActionRefused", severity="warn", stamp=clock(), message="the state fixture only dry-runs that")
        return 0
    if arg("--confirm") != f"tok-{action}":
        emit_to(out, record="event", code="ConfirmationRequired", severity="info", stamp=clock(), message="pass the token")
        return 0
    state, now, key = board(), clock(), arg("--key")
    if action == "persist_config":
        state["stored"] = dict(state["live"])
        save_board(state)
        for name in CONFIG:
            emit_to(out, **config_record("can/1", name, state, now))
        emit_to(out, record="event", code="ConfigPersisted", severity="info", stamp=now, message="conf write: every live setting is stored")
        return 0
    if action == "reset_config" and key is None:  # every setting back to what is stored
        state["live"] = dict(state["stored"])
        save_board(state)
        for name in CONFIG:
            emit_to(out, **config_record("can/1", name, state, now))
        return 0
    if key not in CONFIG or not CONFIG[key]["writable"]:
        emit_to(out, record="event", code="ConfigRejected", severity="warn", stamp=now, message=f"{key}: no such writable setting")
        return 1
    row, raw = CONFIG[key], arg("--value")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        value = raw
    if row.get("safety") and mode() != 0:
        emit_to(out, record="event", code="ConfigRejected", severity="warn", stamp=now, message=f"{key}: the board is not stopped (mode {mode()})")
        return 1
    if key == "servo.pid_position.kp" and isinstance(value, (int, float)) and value <= 0:
        emit_to(out, record="event", code="ConfigRejected", severity="warn", stamp=now, message="kp must be above 0")
        return 1
    if row["type"] == "float" and "range" in row:  # the board clamps to its own limits: it ACCEPTS, but keeps another value
        value = min(max(float(value), row["range"][0]), row["range"][1])
    state["live"][key] = value
    save_board(state)
    emit_to(out, **config_record("can/1", key, state, now))
    return 0


def main() -> int:
    if VERB == "describe":
        print(json.dumps(describe()))
        return 0
    device = arg("--device") or ""
    log(device=device, path=arg("--path"), frame_dir=os.environ.get("IO_DIAGNOSTICS_FRAME_DIR"))
    if VERB == "diagnose":
        diagnose(device)
    elif VERB == "watch":
        watch(arg("--path") or "")
    elif VERB == "act":
        return act(arg("--action") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
