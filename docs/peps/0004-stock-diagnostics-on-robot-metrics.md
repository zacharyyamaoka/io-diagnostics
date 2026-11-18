# 0004: The stock diagnostics robot_metrics already computes take their metric from it

- **Status:** decided 2026-11-17 and landed on main. The dispatcher's recommendation, taken by Zach's standing rule for reversible work;
  undo is `git revert` of this repo's commits (and of robot_metrics' two, which only add). Preview and Stable are untouched: this lands on
  main only.
- **Applies to:** `robotdx/diagnostics/stock/tracking.py` (`tracking_error`, `stop_error`, `stops`) and `stock/health.py` (`fault_codes`'
  limiter shares). Every other stock check is unchanged and listed below with what it waits for.
- **Follows:** Zach, 2026-11-17: "a diagnostic = a metric + a threshold"; robot_metrics PEP 0001 §8 step 1 (`/home/bam/robot_metrics`);
  the design note *Stock Diagnostics per Robot Part Type — Catalog and Library (2026-10-28)*; the prior-art note's fix: `Limits` ride
  inside each config, so a new target makes a new config.

## Decision

1. **robot_metrics owns the math, RobotDx owns the threshold.** A covered check finds its metric with `metric(config)` /
   `stop_metric(config)` / `limiter_metric(code)` and computes through it. RobotDx keeps its configs (their fields and defaults unchanged),
   `Limits`, `judge`, `Result`, records, staleness and the UI.
2. **A metric's identity is never a limit.** The config still carries `limits` (its public API), but the metric it computes through is
   the same object whatever the target, the unit, the scale or the side: `tracking.metric(TrackingErrorConfig(limits=at_most(1)))` ==
   `tracking.metric(TrackingErrorConfig(limits=at_most(2), unit="rad"))` == `ErrorStat(statistic="rms")`.
3. **Byte-identical, or it does not land.** Every covered number was re-run over the real rack runs before and after and compared byte for
   byte (below). Where robot_metrics' own convention would move a published number by round-off, RobotDx keeps its convention and
   robot_metrics gained the option instead.
4. **robot_metrics is lazy and in the `diagnostics` extra** (`robotdx/diagnostics/_robot_metrics.py`, as `_numpy` and `_sim2real`): the
   host imports and lists every check without it.

## What moved, and onto what

| stock check | config | robot_metrics metric | notes |
|---|---|---|---|
| `tracking_error` rms | `TrackingErrorConfig(statistic="rms")` | `ErrorStat(statistic="rms")` | |
| `tracking_error` max | `statistic="max"` | `ErrorStat(statistic="max_abs")` | |
| `tracking_error` final | `statistic="final"` | `ErrorStat(statistic="mean_abs")` on the last `final_window_s` | the rows are RobotDx's |
| `tracking_error` p95 | `statistic="p95"` | `ErrorPercentile(percentile=95.0)` | **new in robot_metrics** |
| `stop_error`, `stops()` | `StopErrorConfig` | `StopStat(of="error", statistic="p<percentile>", count_first_dwell=True, include_last_dwell=<live window>, dwell_min_s, settle_window_s)` + `functional.stop_summary` | **rule options, any percentile and the summary new in robot_metrics** |
| `fault_codes`' limiter shares | `FaultCodesConfig` | `CodeShare(codes=(c,), channel="fault")` per limiter code | hard faults and timeouts stay rising-edge counts here |

What robot_metrics needed (its commits on main, `tests/test_robotdx_rules.py` committed red first, each held bit for bit to
sim2real.metrics.actuator at main 6c6e964):

- **StopStat's rule options** `dwell_min_s`, `settle_window_s`, `count_first_dwell` (defaults: the pendulum's rule, unchanged). RobotDx's T4
  counts a dwell its tape starts in (after the prelude a tape may open on a held target) and drops a tape's last dwell (parking); the
  pendulum's rule is the other way round on both.
- **Any percentile as a StopStat statistic** (`"p90"`, `"p97.5"`), so a config's `percentile` is part of its metric's identity.
- **`ErrorPercentile`**: a percentile of |e|; its state is the magnitudes (no fixed-size statistic gives an exact p95).
- **`stops.summary` / `functional.stop_summary`**: T4's numbers (percentile, n, median, max, signed mean, worst) over the stops in the
  caller's unit. WHY not `StopStat.compute` times the scale: a percentile interpolates, so a percentile of mrad is not 1e3 x a percentile
  of rad to the last bit, and RobotDx's published T4 numbers were taken in mrad.

## What did not move, and what it waits for

| stock check | why not yet | next step |
|---|---|---|
| `repeatability_floor`, `model_error`, `fidelity_ratio` | fidelity: the floor's common-clock pairwise RMS, model / floor per class | a fidelity family in robot_metrics (PEP 0001 §8 step 2), then sim2real.metrics' copy goes |
| `move_hold_split`, `rest_offset`, `split_fidelity` | T2's split (moving part vs hold part, rest offsets) | the same fidelity family |
| `feedback_share` | RMS(tau - tau_ff) / RMS(tau) over rows pooled across runs: ErrorStat's math, but a pooled ratio has no v0 input (runs on no common clock); merging per-run states re-associates the sums (round-off) | a pooled-rows input or a ratio metric |
| `step_response` | MATLAB stepinfo's definitions (overshoot in % of the move, 10-90 % rise, interpolated 2 % settle from target arrival), not `StopStat`'s overshoot / settle | a `StepInfo` metric |
| `pose_accuracy`, `path_accuracy` | ISO 9283 AP / RP over attained 3-D points per pose; v0's `Repeatability` reads 1-D stop errors of joint-trajectory repeats | a points input for `Repeatability`, plus AP |
| `coverage` | the envelope's cell binning (robot_msgs.coverage through sim2real) | a coverage metric |
| `box`, `sample_rate`, `stamp_delay`, `fault_codes`' fault / timeout edges | health statistics and event counts v0 has no metric for | health metrics |
| `share`, `event_interval`, `staleness`, `rollup`, `gate_matrix` | Wilson shares over events, MTBF, evidence age, roll-ups: RobotDx's own layer | `share` could read `robot_metrics.wilson` once events are a metric input |
| `known_load_gain`, `kt_consistency`, `agreement` | truthfulness | a truthfulness family |
| `reprojection_error`, `depth_plane`, `image_sharpness`, `marker_drift` | camera | a camera family |
| `spectral_quality`, `spectral_angle` | spectral | a spectral family |
| `hysteresis_loop`, `allan_variance` | characteristic curves | later |
| tracking's across-runs centre (`data.center`, median / median_high) | it is the floor's ruler too, so it stays on sim2real.metrics.series with fidelity | robot_metrics' `Alias.across` gains median_high |

v0 also has `TimeOffset` (lag) and `Repeatability` (RP over repeats), which no stock check computes yet: no RobotDx consumer to move.

`_sim2real.py` stays for the checks above (the rulers in `data.py`, fidelity, the T2 split, coverage). `tracking.py` and `health.py` import
nothing from sim2real; `stop_error` and `fault_codes` run with sim2real absent (`tests/diagnostics/test_robot_metrics_seam.py`).

## Proof

Every number below was produced before the change (io-diagnostics main a7fc658, sim2real.metrics) and after it, into scratch
directories, and compared with `cmp`. A difference would have been a FAIL unless it was named round-off below 1e-12 relative; there
was none to name.

- **The rack's own producer** (sim2real `tools/diagnostics_run_rack.py`, run from a copy whose only change is its output directory, over
  rack_2026-10-24/25): 109 Results, 105 oracle comparisons. All six files byte-identical before and after (`results.json` 458,252 B,
  `diagnostics.json`, `gates.json`, `oracle.json`, `stream.json`, `payloads.json`), and identical to the data sim2real's report commits.
- **Every moved check, every variant** (`tools/stock_byte_diff.py`): `tracking_error` in 66 configs (rms, p95, max, final x position and
  velocity x controlled rows x prelude x median / median_high, plus a 0.1 s final window and arcmin), `stop_error` in 43 (percentiles 50,
  75, 90, 95, 99 x mrad and mm x prelude x side, plus three dwell / settle windows), `fault_codes` in 3, each pooled per group and per run;
  the public `stops()` tuples for all 43; and live windows (2 s, 5 s) where a window's last dwell counts.
  - rack_2026-10-24 dataset + floors + ff, 230 runs: 39,216 lines, byte-identical (sha256 `ad2f0ee9c2fc7d70`).
  - round 8 (onedof_2026-11-15/round8/real, paired files split per board), 254 runs: 40,156 lines, byte-identical (`cd72787731a6d942`).
  - 45,432 stops; 19,960 stop results, 34,398 tracking results and 1,701 fault results with a value; 71 limiter warnings.
  - The rule options are not decoration: 36 of round 8's 288 T4 stops (default config) are dwells a tape opens in. Without
    `count_first_dwell` those would have gone and the diff would have shown it.
- **Tests.** io-diagnostics, whole suite (`--group dev --group interop --extra diagnostics`, CPUs 2-5,16,17, one process):
  before 1704 passed, 3 failed, 8 skipped; after 1713 passed (one superseded test moved out, ten added), the same 3 failed, 8 skipped; the named suites (`test_stock_diagnostics*`, `test_stock_inputs*`, `test_default_diagnostics`, `tests/diagnostics`) 623 passed, 4 skipped. The 3 failures are the same before and after and outside diagnostics
  (`test_integration` plugins.d now lists dora_netbelt / obsbot drivers, `test_port_activity` computer page labels, `test_v6_converged`
  the live rack poller). robot_metrics: 54 passed before, 68 after (14 new), against round 8's scorecard; its live
  `scorecards/latest_real.json` was rewritten to round 9 at 2026-11-18T04:38Z by another session, so 4 parity tests fail against it
  before and after alike.
- **Isolation.** `uv run --isolated --no-dev --project /home/bam/io-diagnostics --extra diagnostics python -c "import robotdx.diagnostics"`
  imports, and the env holds exactly dataclass-values, io-diagnostics, numpy, pillow, robot-metrics, robot-msgs and sim2real; stop error,
  tracking p95 and the limiter share compute in it. Without the extra the env is io-diagnostics alone and still imports.

## Decided here (each reversible)

- **The one existing test that changed:** `test_sim2real_metrics.py::test_tracking_and_stops_are_sim2reals` spied on sim2real's
  `tracking_statistic` / `stops` / `stop_summary`, the wiring this PEP replaces. Its spy-and-sentinel twin is now
  `test_robot_metrics_seam.py`; the numbers stay held by the unchanged oracles. Undo: revert, and the old test passes again.
- **A config's `percentile` outside [0, 100] raises when the metric is built** (StopStat refuses it), where it raised inside
  `np.percentile` only when a stop was found. Both are a bad config. Undo: build the StopStat with a clamped label.
- **A stop's error is the mean over the dwell's own last `settle_window_s`.** sim2real's loop took every row of the run inside that
  window, so a config with `dwell_min_s` shorter than `settle_window_s` would have mixed moving rows into a stop's error. No config in
  `defaults.py` or the catalog does; on every real run the numbers are identical.
- **The compared signal rides in the robot_msgs stream's `positions`** whatever it is (an angle, a velocity, a width), named by its channel:
  a base metric is math only (Zach, 2026-11-17). Undo: map velocities onto `field="velocities"`.
- **A run whose clock does not strictly increase is refused** by the moved checks (robot_msgs' streams require it: interpolation needs
  it), where sim2real's loops read such a clock as given. None of the 484 real runs above has one. Undo: sort or de-duplicate the rows in
  `_robot_metrics.streams` before building the streams.
