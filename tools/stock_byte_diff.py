"""Byte-diff harness for moving stock diagnostics onto robot_metrics (docs/peps/0004): every moved check, every statistic and config
variant, over the real rack runs. Run it before a change and after it, into two directories, and compare them byte for byte:

    env -u VIRTUAL_ENV taskset -c 2-5,16,17 uv run --project /home/bam/io-diagnostics --extra diagnostics python /home/bam/io-diagnostics/tools/stock_byte_diff.py /tmp/before

Runs (read-only, /archive):
- rack_2026-10-24 dataset + floors + ff runs (the runs the stock diagnostics were proven on, Oct 24-25)
- round 8 (onedof_2026-11-15/round8/real), single-board files via rack_npz.load, paired files split per board

Writes one JSON line per Result (every field, floats as repr) to <out>/rack.jsonl and <out>/round8.jsonl, plus the public
tracking.stops() tuples. Uses only RobotDx's public API, so the same script runs before and after a migration. Never writes into
/archive or sim2real.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

from robotdx.diagnostics import evaluate
from robotdx.diagnostics.adapters.rack_npz import load
from robotdx.diagnostics.data import Trajectory
from robotdx.diagnostics.stock import FaultCodesConfig, StopErrorConfig, TrackingErrorConfig
from robotdx.diagnostics.stock.tracking import stops
from robotdx.diagnostics.stream import monitor

# WHY robot_metrics' private per-board split: round 8's paired files hold two boards' columns; its reader is the one rule for them
from robot_metrics.datasets.onedof_round8 import _board_columns  # noqa: E402

RACK = Path("/archive/datasets/bam_rig")
DAY = RACK / "rack_2026-10-24"
ROUND8 = RACK / "onedof_2026-11-15" / "round8" / "real"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ["RDX_OUT"])

TRACKING = [TrackingErrorConfig(statistic=s, channel=c, controlled_only=co, skip_prelude=sp, across_runs=a)
            for s in ("rms", "p95", "max", "final") for c in ("q_rad", "qd_rad_s") for co in (False, True)
            for sp in (False, True) for a in ("median", "median_high")]
TRACKING += [TrackingErrorConfig(statistic="final", final_window_s=0.1), TrackingErrorConfig(statistic="max", scale=180 / math.pi * 60, unit="arcmin")]
STOPS = [StopErrorConfig(percentile=p, scale=sc, skip_prelude=sp, side=side)
         for p in (90.0, 95.0, 50.0, 99.0, 75.0) for sc in (1e3, 1.0) for sp in (True, False) for side in ("encoder", "output")]
STOPS += [StopErrorConfig(dwell_min_s=0.5), StopErrorConfig(dwell_min_s=0.1, settle_window_s=0.02), StopErrorConfig(settle_window_s=0.1)]
FAULTS = [FaultCodesConfig(), FaultCodesConfig(limiter_codes=(102, 102), limiter_share_warn=0.02), FaultCodesConfig(timeout_is_fault=False)]


def dump(result) -> str:
    return json.dumps(dataclasses.asdict(result), sort_keys=True, default=repr)


def round8_runs() -> list[Trajectory]:
    names = {"q": "q_rad", "q_target": "q_target_rad", "qd": "qd_rad_s", "qd_target": "qd_target_rad_s", "tau": "tau_nm",
             "mode": "mode", "fault": "fault", "temp_c": "temp_c", "voltage": "v_bus_v"}
    out = []
    for folder in sorted(p for p in ROUND8.iterdir() if p.is_dir() and p.name.startswith("can")):
        boards = [int(b[3:]) for b in folder.name.split("+")]
        for npz in sorted((folder / "runs").glob("*.npz")):
            if len(boards) == 1:
                out.append(load(npz))
                continue
            for board in boards:
                columns, info = _board_columns(npz, board)
                arrays = {names[k]: np.asarray(v, float) for k, v in columns.items() if k in names}
                out.append(Trajectory(t=np.asarray(columns["t"], float), run=f"{npz}#can{board}", device=f"CAN {board}",
                                      prelude_s=info["prelude_s"], **arrays))
    return out


def rack_runs() -> dict[str, list[Trajectory]]:
    groups: dict[str, list[Trajectory]] = {}
    for kind in ("dataset", "floors", "ff"):
        for folder in sorted((DAY / kind).glob("can*")):
            runs = [load(p) for p in sorted((folder / "runs").glob("*.npz"))]
            if runs:
                groups[f"{kind}/{folder.name}"] = runs
    return groups


def sweep(name: str, groups: dict[str, list[Trajectory]]) -> int:
    lines = []
    for key, runs in groups.items():
        # pooled over the group, then each run alone
        for scope, members in [("pooled", runs)] + [(run.run, [run]) for run in runs]:
            for index, config in enumerate(TRACKING):
                lines.append(f"{key}|{scope}|tracking#{index}|" + dump(evaluate("stock.tracking_error", config, members, subject=key)))
            for index, config in enumerate(STOPS):
                lines.append(f"{key}|{scope}|stop#{index}|" + dump(evaluate("stock.stop_error", config, members, subject=key)))
            for index, config in enumerate(FAULTS):
                lines.append(f"{key}|{scope}|fault#{index}|" + dump(evaluate("stock.fault_codes", config, members, subject=key)))
        for run in runs:
            if run.has("q_target_rad") and run.has("qd_target_rad_s"):
                for index, config in enumerate(STOPS):
                    lines.append(f"{key}|{run.run}|stops()#{index}|" + json.dumps([[repr(v) for v in row] for row in stops(run, config)]))
        # live windows: a stop still going at a window's end counts (open_end); tracking per window
        first = runs[0]
        for window_s in (2.0, 5.0):
            for result, record in monitor("stock.stop_error", StopErrorConfig(), first, subject=key, window_s=window_s, step_s=window_s / 2):
                lines.append(f"{key}|window{window_s}|stop|" + dump(result))
            for result, record in monitor("stock.tracking_error", TrackingErrorConfig(statistic="p95", controlled_only=True), first,
                                          subject=key, window_s=window_s, step_s=window_s / 2):
                lines.append(f"{key}|window{window_s}|tracking|" + dump(result))
            for result, record in monitor("stock.fault_codes", FaultCodesConfig(), first, subject=key, window_s=window_s, step_s=window_s / 2):
                lines.append(f"{key}|window{window_s}|fault|" + dump(result))
    (OUT / f"{name}.jsonl").write_text("\n".join(lines) + "\n")
    return len(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    which = sys.argv[2:] or ["rack", "round8"]
    if "round8" in which:
        runs = round8_runs()
        groups: dict[str, list[Trajectory]] = {}
        for run in runs:
            groups.setdefault(run.device or Path(run.run).parent.parent.name, []).append(run)
        print("round8", sweep("round8", groups), "runs", len(runs), flush=True)
    if "rack" in which:
        print("rack", sweep("rack", rack_runs()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
