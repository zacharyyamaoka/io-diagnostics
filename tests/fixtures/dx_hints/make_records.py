#!/usr/bin/env python3
"""The Diagnostics tab's view-hint fixture (tests/test_dx_diagnostics_hints.js; PEP 0001 §18): one robotdx.diagnostics catalog entry per
data-ui view hint (docs/CONTRACT.md §35, data-ui PEP 0010 §10), each run on small synthetic inputs and dressed by ``catalog.dress`` (the
entry's own view: its id, title, role, family and data-ui hint), in two frames whose numbers differ (so a check can tell a rewind from
the newest frame). Writes ``records.json`` beside this file:

    uv run --project /home/bam/io-diagnostics --group dev python /home/bam/io-diagnostics/tests/fixtures/dx_hints/make_records.py --robotdx <dir> --ref <what it is>

``--robotdx``: a directory holding a ``robotdx`` package whose ``catalog`` has ``dress`` (lane/stock-functions since 14b6320; on main once
it lands); default this checkout. ``--ref``: what that directory is, written into the file (``git archive lane/stock-functions`` of a
commit is not a checkout). ``--check``: compare with the committed file instead of writing it (exit 1 on any difference). The committed
file was made from ``git archive lane/stock-functions robotdx contract`` at 29f03b2 (2026-10-29).

Each record is ``record.to_record(catalog.dress(registry.evaluate(...), entry))`` exactly, with ``expected_period_s`` 30 (the fixture
plugin's other statuses' period); ``dress`` beside it is the dressed Result itself (``dataclasses.asdict``), the oracle a check compares a
page row with. The state fixture plugin emits a frame's records on its ``can/1`` when ``$STATE_DX_RECORDS`` names a file holding them
(tests/fixtures/plugins-state/state_plugin.py). One more record is not the library's: a copy of the board temperature's with a hint no
data-ui view has, which must fall back to the checks table. Stdlib + numpy (the library's own ``diagnostics`` extra).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "records.json"
SUBJECT = "host/usb1/port-1-3/usb-1-3/can/1"   # the state fixture's CAN 1 (the plugin re-bases every record onto its can/1)
STAMP = 1792631500.0                            # F1's clock; the plugin stamps each emit with its own clock
UNKNOWN_HINT = "radar_sweep"                    # no data-ui view: the page must fall back to its checks table


def git_of(path: Path) -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""
    return {"commit": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD")}


def jsonable(value):
    """A dressed Result as plain JSON (numpy scalars and tuples made plain)."""
    if dataclasses.is_dataclass(value):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return value.item()
    return value


def inputs(np, frame: str) -> dict:
    """Each entry's input in frame A or B: the numbers differ between the two (B is the worse one)."""

    from robot_diagnostics.core import FAIL, PASS, WARN, Result
    from robot_diagnostics.data import Trajectory
    from robot_diagnostics.stock.events import Event

    b = frame == "B"
    n, dt = 600, 0.01
    t = np.arange(n) * dt

    def tape(stop_offsets_rad):
        """A 1-DOF waypoint tape (move 1 s, dwell 0.5 s, ...); each dwell holds its own offset from the target."""
        q_target, qd_target, q = np.zeros(n), np.zeros(n), np.zeros(n)
        for k, start in enumerate(range(0, n, 150)):
            move = slice(start, min(start + 100, n))
            q_target[move] = np.linspace(0, 0.5, move.stop - move.start) + (start // 150) * 0.5
            qd_target[move] = 0.5 / (100 * dt)
            dwell = slice(min(start + 100, n), min(start + 150, n))
            q_target[dwell] = 0.5 + (start // 150) * 0.5
            q[move] = q_target[move]
            q[dwell] = q_target[dwell] + stop_offsets_rad[k % len(stop_offsets_rad)]
        return Trajectory(t=t, q_rad=q, q_target_rad=q_target, qd_rad_s=qd_target.copy(), qd_target_rad_s=qd_target, mode=np.full(n, 10.0),
                          fault=np.zeros(n), temp_c=np.full(n, 25.0), v_bus_v=np.full(n, 24.0), tau_nm=np.zeros(n), run=f"fixture {frame}",
                          device="CAN 1")

    def held(**columns):
        """A minute of a board holding still, at the host's State history rate (1 Hz, as a RobotDx binding reads it)."""
        m = 60
        base = dict(t=np.arange(m, dtype=float), q_rad=np.zeros(m), q_target_rad=np.zeros(m), mode=np.full(m, 10.0), fault=np.zeros(m),
                    temp_c=np.full(m, 25.0), v_bus_v=np.full(m, 24.0), tau_nm=np.zeros(m), run=f"fixture {frame}", device="CAN 1")
        return Trajectory(**{**base, **columns})

    board = held(temp_c=np.linspace(48.0, 64.0 if b else 52.0, 60))   # the board heats: B's max crosses the 60 °C warning
    fault = np.zeros(60)
    if b:
        fault[40:42] = 32.0                                             # B: a hard fault (code 32) for 2 s
    faults = held(fault=fault)
    lost = 6 if b else 1                                          # replies lost of 200 queries: 0.5 % then 3 %
    replies = [Event(float(i), i < lost, {"bus": "can"}) for i in range(200)]
    stops = tape([0.0012, 0.0021, 0.0007, 0.0016] if b else [0.0004, 0.0009, 0.0002, 0.0006])
    repeats = [dataclasses.replace(tape([0.0]), q_rad=tape([0.0]).q_rad + (0.0015 if b else 0.001) * i, run=f"repeat {i}") for i in range(5)]
    member = lambda title, verdict, code="": Result(diagnostic="servo.tracking_rms", title=title, verdict=verdict, value=1.0, unit="mrad",
                                                    subject=SUBJECT, code=code)
    joints = [member("J1", PASS), member("J2", WARN if b else PASS, "WarnLimit" if b else ""), member("J3", PASS)]
    gates = [("CAN 1", "T1", member("T1", PASS)), ("CAN 1", "T2", member("T2", FAIL if b else PASS, "ErrorLimit" if b else "")),
             ("CAN 1", "T3", member("T3", WARN, "WarnLimit")), ("CAN 2", "T1", member("T1", PASS)), ("CAN 2", "T2", member("T2", PASS))]
    return {
        "driver.board_temp": [board],          # threshold_band
        "driver.link_loss": replies,           # kpi_trend (a target and a better side of its own)
        "driver.faults": [faults],             # level_history
        "servo.stop_error": [stops],           # distribution
        "servo.floor": repeats,                # evidence
        "arm.joints": joints,                  # rollup_tree
        "rack.gate_matrix": gates,             # gate_matrix
        "arm.tool_error_at_stop": [stops],     # path_overlay (no pose data on a RobotDx entity: the page leaves it to the table)
    }


def build(robotdx_dir: Path, ref: str) -> dict:
    sys.path.insert(0, str(robotdx_dir))
    import numpy as np
    from robot_diagnostics import catalog, record, registry

    if not hasattr(catalog, "dress"):
        raise SystemExit(f"{robotdx_dir}/robotdx/diagnostics/catalog.py has no dress (it came in lane/stock-functions 14b6320): pass --robotdx")
    frames = {}
    for frame in ("A", "B"):
        out = []
        for entry_id, data in inputs(np, frame).items():
            entry = catalog.entry(entry_id)
            dressed = catalog.dress(registry.evaluate(entry.primitive, catalog.config(entry_id), data, subject=SUBJECT, stamp=STAMP), entry_id)
            out.append({"entry": entry_id, "hint": dressed.hint, "dress": jsonable(dressed),
                        "record": jsonable(record.to_record(dressed, expected_period_s=30.0))})
        # not the library's: the board temperature's record under a hint no data-ui view has (the page's fallback, the checks table)
        base = next(item for item in out if item["entry"] == "driver.board_temp")
        odd = json.loads(json.dumps(base))
        odd.update(entry=None, hint=UNKNOWN_HINT, note="a copy of driver.board_temp's record with dx.hint and dx.diagnostic replaced: not the library's")
        odd["dress"].update(hint=UNKNOWN_HINT, diagnostic="fixture.unknown_hint", title="board temperature, an unknown view")
        odd["record"]["name"] = "board temperature, an unknown view"
        for pair in odd["record"]["values"]:
            if pair["key"] == "dx.hint":
                pair["value"] = UNKNOWN_HINT
            if pair["key"] == "dx.diagnostic":
                pair["value"] = "fixture.unknown_hint"
        out.append(odd)
        frames[frame] = out
    return {"schema": "robotdx-dx-hints-fixture/1", "made_by": "tests/fixtures/dx_hints/make_records.py",
            "robotdx": {"ref": ref or git_of(robotdx_dir)["commit"], "catalog_dress": True}, "subject": SUBJECT, "frames": frames}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--robotdx", type=Path, default=HERE.parents[2], help="a directory with a robotdx package that has catalog.dress")
    parser.add_argument("--ref", default="", help="what --robotdx is (default: its git commit)")
    parser.add_argument("--check", action="store_true", help="compare with the committed records.json instead of writing it")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    built = build(args.robotdx.resolve(), args.ref)
    text = json.dumps(built, indent=1, sort_keys=True) + "\n"
    if args.check:
        old = json.loads(args.out.read_text())
        same = old["frames"] == built["frames"]
        print("records.json matches catalog.dress" if same else "records.json differs from catalog.dress: run without --check")
        return 0 if same else 1
    args.out.write_text(text)
    print(f"wrote {args.out}: {sum(len(f) for f in built['frames'].values())} records in {len(built['frames'])} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
