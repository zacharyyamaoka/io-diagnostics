"""Fixture COMPOSITE plugin (docs/composition-v1.md section 4): an "arm" over joint boards. Stdlib only; never touches hardware.

    <python> arm_plugin.py describe
    <python> arm_plugin.py diagnose --device composite:lab/robot-1/arm
    <python> arm_plugin.py watch    --device composite:lab/robot-1/arm --path ""
    <python> arm_plugin.py act      --device composite:lab/robot-1/arm --action home_arm --dry-run

This is what someone else's plugin looks like at the seam: RobotDx knows the composite only as "a name and a list of children"; THIS file
knows what an arm is. It is told which composite it serves in ``$IO_DIAGNOSTICS_COMPOSITE`` (id, kind, title, path, ``expects``, the
children's paths) and reads the children's latest levels and State from the file named in ``$IO_DIAGNOSTICS_COMPOSITE_VALUES``.

- State (one group, ``arm``): each joint's angle with ``source`` = that joint's entity and State key (the page draws ``↳ CAN 2``),
  the planar forward kinematics of the chain (end-effector ``x`` and ``y``, link lengths ``expects.links_m``), the largest deviation of a
  joint from the arm's mean position, and how many joints answer.
- Diagnostics: "tracking error within limit" (that deviation against ``expects.tracking_error_rev``) and "all joints answer".
- Utility: "Home the arm" (``home_arm``). A real run only prints that this fixture never moves anything.
- Configuration: ``arm.dof`` and ``arm.tracking_error_rev``, read-only (what the file's ``expects`` said).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

SCHEMA = "diagnostics-record/0"
ARGS = sys.argv[1:]
VERB = next((word for word in ARGS if word in ("describe", "diagnose", "watch", "act", "stop")), "")
PERIOD_S = float(os.environ.get("ARM_PERIOD_S", "0.1"))
HOST_VERSION = tuple(int(part) for part in (os.environ.get("IO_DIAGNOSTICS_RECORD_VERSION") or "0.1").split(".") if part.isdigit())
NEW = HOST_VERSION >= (0, 2)
DEFAULT_LIMIT_REV = 0.25
DEFAULT_LINKS_M = (0.30, 0.25, 0.10)

_BASE = float(os.environ.get("IO_DIAGNOSTICS_NOW") or time.time())
_START = time.monotonic()


def clock() -> float:
    return _BASE + (time.monotonic() - _START)


def arg(name: str) -> str | None:
    return ARGS[ARGS.index(name) + 1] if name in ARGS[:-1] else None


def emit(**record) -> None:
    sys.stdout.write(json.dumps({"schema": SCHEMA, **record}) + "\n")
    sys.stdout.flush()


COMPOSITE = json.loads(os.environ.get("IO_DIAGNOSTICS_COMPOSITE") or "{}")
EXPECTS = COMPOSITE.get("expects") or {}
LIMIT_REV = float(EXPECTS.get("tracking_error_rev", DEFAULT_LIMIT_REV))
LINKS_M = tuple(float(link) for link in EXPECTS.get("links_m", DEFAULT_LINKS_M))
TITLE = COMPOSITE.get("title") or "arm"

ACTIONS = [{"name": "home_arm", "title": "Home the arm", "description": "send every joint to its home position, last joint first",
            "danger": "moves", "scope": "device", "confirm": "Home {name}? Every joint moves.", "kind": "changes"}]


def joints() -> list[dict]:
    """The arm's joints: its entity children, in the order the file lists them."""

    return [child for child in COMPOSITE.get("children", []) if child.get("kind") == "entity"]


def child_values() -> dict[str, dict]:
    """The children's latest levels and State, by entity path (the host rewrites the file about every second while watched)."""

    path = os.environ.get("IO_DIAGNOSTICS_COMPOSITE_VALUES")
    try:
        with open(path or "") as handle:
            return {entry["path"]: entry for entry in json.load(handle).get("children", []) if entry.get("path")}
    except (OSError, ValueError):
        return {}


def position_of(entry: dict | None) -> float | None:
    value = ((entry or {}).get("state", {}).get("motor", {}).get("position") or {}).get("value")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def forward_kinematics(angles_rev: list[float]) -> tuple[float, float]:
    """A planar chain: link ``i`` has length ``LINKS_M[i]`` and turns by its joint's angle on top of the ones before it."""

    x = y = total = 0.0
    for length, angle in zip(LINKS_M, angles_rev):
        total += angle * 2 * math.pi
        x += length * math.cos(total)
        y += length * math.sin(total)
    return x, y


def measure() -> dict:
    values = child_values()
    found = []
    for joint in joints():
        entry = values.get(joint.get("path") or "")
        found.append((joint, entry, position_of(entry)))
    positions = [position for _joint, _entry, position in found if position is not None]
    mean = sum(positions) / len(positions) if positions else 0.0
    deviation = max((abs(position - mean) for position in positions), default=0.0)
    return {"joints": found, "positions": positions, "deviation": deviation, "answering": len(positions)}


def state_record(measured: dict, stamp: float, period_s: float) -> dict:
    measurements = []
    for number, (joint, _entry, position) in enumerate(measured["joints"], start=1):
        if position is None:
            continue
        measurements.append({"key": f"joint {number} angle", "value": round(position, 4), "unit": "rev",
                             "source": {"path": joint["path"], "key": "position"}})
    if measured["positions"]:
        x, y = forward_kinematics(measured["positions"])
        measurements += [{"key": "end effector x", "value": round(x, 4), "unit": "m"}, {"key": "end effector y", "value": round(y, 4), "unit": "m"}]
    measurements += [{"key": "tracking error", "value": round(measured["deviation"], 4), "unit": "rev", "warn_high": LIMIT_REV},
                     {"key": "joints answering", "value": measured["answering"], "unit": "1"}]
    return {"record": "state", "path": "", "name": "arm", "stamp": stamp, "expected_period_s": period_s, "measurements": measurements}


def statuses(measured: dict, stamp: float) -> list[dict]:
    out = []
    total = len(measured["joints"])
    silent = [joint.get("name") or joint.get("ref") for joint, _entry, position in measured["joints"] if position is None]
    if silent and not measured["answering"]:
        # no joint has reported at all (the host just started, or the arm was just listed): nothing says they are broken
        out.append({"record": "status", "level": "unknown", "name": f"{TITLE}: all joints answer", "path": "", "code": "JointsNotChecked",
                    "message": "no joint has reported yet", "stamp": stamp, "expected_period_s": 30})
    elif silent:
        out.append({"record": "status", "level": "error", "name": f"{TITLE}: all joints answer", "path": "", "code": "JointsNotAnswering",
                    "message": f"{measured['answering']} of {total} joints answer: {', '.join(map(str, silent))} silent", "stamp": stamp,
                    "expected_period_s": 30,
                    "values": [{"key": "what_to_check", "value": "1. Is each joint powered and on its bus?\n2. Open the silent joint's own page"}]})
    else:
        out.append({"record": "status", "level": "ok", "name": f"{TITLE}: all joints answer", "path": "", "stamp": stamp,
                    "message": f"all {total} answer", "expected_period_s": 30})
    if measured["deviation"] > LIMIT_REV:
        out.append({"record": "status", "level": "error", "name": f"{TITLE}: tracking error", "path": "", "code": "TrackingError",
                    "message": f"{measured['deviation']:.3f} rev from the arm's mean, over the {LIMIT_REV:g} rev limit", "stamp": stamp,
                    "expected_period_s": 30,
                    "values": [{"key": "what_to_check", "value": "1. Is one joint lagging its command? Compare the joint angles in State"}]})
    else:
        out.append({"record": "status", "level": "ok", "name": f"{TITLE}: tracking error", "path": "", "stamp": stamp,
                    "message": f"{measured['deviation']:.3f} rev, within the {LIMIT_REV:g} rev limit", "expected_period_s": 30})
    return out


def describe() -> dict:
    answer = {"name": "state_arm", "schema": SCHEMA, "record_version": "0.2" if NEW else "0.1", "matches": ["composite:arm"],
              "actions": ACTIONS}
    if NEW:
        answer["watch"] = {"period_s": PERIOD_S, "gates": [], "danger": "query"}
    return answer


def entity() -> None:
    emit(record="entity", path="", name=TITLE, kind="composite", identity={"model": COMPOSITE.get("kind", "arm")},
         actions=ACTIONS)


def diagnose() -> None:
    now = clock()
    entity()
    measured = measure()
    for status in statuses(measured, now):
        emit(**status)
    if NEW:
        emit(**state_record(measured, now, 30))
        emit(record="config", path="", key="arm.dof", value=len(joints()), unit="", group="arm", stamp=now, expected=len(joints()),
             expected_source="the composite's children")
        emit(record="config", path="", key="arm.tracking_error_rev", value=LIMIT_REV, unit="rev", group="arm", stamp=now)


def watch() -> None:
    if not NEW:
        return
    last, said = None, 0.0
    while True:
        now = clock()
        measured = measure()
        emit(**state_record(measured, now, 2.0))
        found = statuses(measured, now)
        verdict = [(status["name"], status["level"], status.get("code", "")) for status in found]
        if verdict != last or now - said >= 2.0:  # a verdict when it changes (and as a heartbeat), not ten times a second
            for status in found:
                emit(**status)
            last, said = verdict, now
        time.sleep(PERIOD_S)


def act(action: str) -> int:
    if "--dry-run" in ARGS:
        emit(record="action-result", action=action, dry_run=True, command=["fixture-arm", f"--{action}"],
             steps=[{"purpose": "home the arm: last joint first", "runs": "subprocess", "argv": ["fixture-arm", f"--{action}"]}],
             preconditions=[], confirm=f"Do {action}?", token=f"tok-{action}")
        return 0
    fd = os.environ.get("IO_DIAGNOSTICS_RECORD_FD")
    handle = os.fdopen(int(fd), "w") if fd else sys.stdout
    handle.write(json.dumps({"schema": SCHEMA, "record": "event", "code": "ActionRefused", "severity": "warn", "stamp": clock(),
                             "message": "the arm fixture only dry-runs: it never moves anything"}) + "\n")
    handle.flush()
    return 0


def log() -> None:
    """``$ARM_LOG`` (a file) gets one JSON line per call: what the host told this plugin (a test reads it)."""

    path = os.environ.get("ARM_LOG")
    if path:
        with open(path, "a") as handle:
            handle.write(json.dumps({"verb": VERB, "argv": ARGS, "pid": os.getpid(), "composite": COMPOSITE,
                                     "values": os.environ.get("IO_DIAGNOSTICS_COMPOSITE_VALUES"),
                                     "device_path": os.environ.get("IO_DIAGNOSTICS_DEVICE_PATH"),
                                     "device": os.environ.get("IO_DIAGNOSTICS_DEVICE"),
                                     "record_version": os.environ.get("IO_DIAGNOSTICS_RECORD_VERSION")}) + "\n")


def main() -> int:
    log()
    if VERB == "describe":
        print(json.dumps(describe()))
    elif VERB == "diagnose":
        diagnose()
    elif VERB == "watch":
        watch()
    elif VERB == "act":
        return act(arg("--action") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
