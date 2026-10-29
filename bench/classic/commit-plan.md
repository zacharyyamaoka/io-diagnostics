# Classic performance: proposed commits

No Git mutation was performed. HEAD/base is `998a49d545eaa87f560edfc4ffd8c0754c2a0641`; branch `lane/perf-codex` in `/home/bam/io-diagnostics-lanes/perf-codex`. No merge or push.

The final working tree contains all changes. Use the staged file copies under `/tmp/classic-perf-commit-stages/` in numerical order, stage only each manifest’s explicit paths, then commit its exact `message`. The source fix is consolidated so the commit includes all compatibility fixes and passes the suites; intermediate benchmark results remain attached as evidence. These are copies, not a worktree or an index; no Git write command has run. Raw profiles/screenshots/video remain in ignored `reports/media/`; the reviewed self-contained report is the only embedded media artifact proposed for Git.

## 1. perf(classic): add the fixture-only freshness and Chrome benchmark

**Files**

- `tools/bench_classic.js`
- `tools/bench_fixture.py`
- `tests/fixtures/plugins-state/state_plugin.py`
- `tests/test_classic_bench.js`
- `bench/classic/2026-10-29-998a49d-baseline.json`

**Message**

```text
perf(classic): add the fixture-only freshness and Chrome benchmark

Measure before changing production code: fixture-only free ports, isolated State/config, headless Chrome, 60 s idle and loaded periods. The warm State clock probe pauses actual fixture receipts. JSON has exact KPI keys, t/sha, source fingerprint and CDP profiles. Production HTTP remains unchanged.

Baseline:
idle: lag p50/p95/max 143.4/258.3/279.4 ms; long tasks/min 0.0; blocked 0 ms; frame p95 16.8 ms; heard jitter p95 20.3 ms (10 ticks, 0 skips); SSE 0 B/min; heap growth 13285116 B, retained 831972 B.
loaded: lag p50/p95/max 265.0/488.1/712.9 ms; long tasks/min 253.0; blocked 23296 ms; frame p95 83.4 ms; heard jitter p95 97.2 ms (10 ticks, 0 skips); SSE 0 B/min; heap growth 28031952 B, retained 8204744 B.
```

## 2. perf(classic): keep live State current with visible rows and fair sample delivery

**Files**

- `io_diagnostics/live.py`
- `io_diagnostics/web/app.js`
- `io_diagnostics/web/detail/d6.js`
- `io_diagnostics/web/detail/d6-rows.js`
- `io_diagnostics/web/activity/log.js`
- `io_diagnostics/web/charts/core.js`
- `io_diagnostics/web/charts/c1.js`
- `io_diagnostics/web/charts/c2.js`
- `io_diagnostics/web/charts/c3.js`
- `io_diagnostics/web/charts/c4.js`
- `io_diagnostics/web/charts/c5.js`
- `tests/test_classic_perf.js`
- `tests/test_feed_fairness.py`
- `bench/classic/2026-10-29-998a49d-virtual.json`
- `bench/classic/2026-10-29-998a49d-live.json`
- `bench/classic/2026-10-29-998a49d-delivery.json`
- `bench/classic/2026-10-29-998a49d-fair.json`
- `bench/classic/2026-10-29-998a49d-final.json`

**Message**

```text
perf(classic): keep live State current with visible rows and fair sample delivery

Use native EventSource after canonical HTTP bootstrap, coalesce network invalidations in rAF, and virtualize measured State blocks before generating sparklines. Give heard one 1 Hz text ticker. Defer hidden charts, MJPEG/lightbox decoders, watches and Log loops. Preserve immediate controls, atomic selections, chart shells/titles, theme invalidation and initial run-view models.

Cold rack reads no longer hold the watched-sample lock; validate cursors so a racing watch wins exactly once, including a path revived during cleanup. API/event/record payload shapes stay unchanged. WHY comments record the behavior at each seam.

Before:
idle: lag p50/p95/max 143.4/258.3/279.4 ms; long tasks/min 0.0; blocked 0 ms; frame p95 16.8 ms; heard jitter p95 20.3 ms (10 ticks, 0 skips); SSE 0 B/min; heap growth 13285116 B, retained 831972 B.
loaded: lag p50/p95/max 265.0/488.1/712.9 ms; long tasks/min 253.0; blocked 23296 ms; frame p95 83.4 ms; heard jitter p95 97.2 ms (10 ticks, 0 skips); SSE 0 B/min; heap growth 28031952 B, retained 8204744 B.

After:
idle: lag p50/p95/max 83.4/128.4/134.9 ms; long tasks/min 0.0; blocked 0 ms; frame p95 16.7 ms; heard jitter p95 0.1 ms (9 ticks, 0 skips); SSE 2491988 B/min; heap growth 1679900 B, retained 1122384 B.
loaded: lag p50/p95/max 95.8/163.6/254.2 ms; long tasks/min 0.0; blocked 0 ms; frame p95 16.8 ms; heard jitter p95 0.2 ms (9 ticks, 0 skips); SSE 61905678 B/min; heap growth 51241520 B, retained 8911952 B.

Measured increments: visible-State p95 319.4 ms (0 long tasks/min), SSE p95 289.0 ms (emission gap p95 290.4 vs delivery 14.6 ms), server fairness p95 172.5 ms. Full intermediate JSON is retained.

Validation: Python 744 passed, 6 skipped, 31 deselected; feed/fairness 24 passed; all 23 page suites run unedited, 22 pass and only known d1b A-Z ordering fails. Required: entity 92/92, layouts 200/200, charts 102/102, utilities 89/89, tree nav 43/43, fill/rollup 10/10. New browser regressions cover real SSE, partial/replayed points, virtual scroll/folds, one-frame batching, fallback, and background stream/Log stop/resume.
```

## 3. docs(classic): retain the measured performance review and recorded journey

**Files**

- `tools/record_classic_perf.js`
- `tools/report_classic_perf.py`
- `bench/classic/commit-plan.md`
- `reports/classic-performance-2026-10-29.html`
- `README.md`

**Message**

```text
docs(classic): retain the measured performance review and recorded journey

Publish a self-contained HTML review with the actual 13 s acceptance video, GIF fallback, screenshots, interactive benchmark comparison and per-item feedback. Link it from README. No runtime code changes in this commit. Raw profiles/frames/video remain ignored.

Review compares baseline:
idle: lag p50/p95/max 143.4/258.3/279.4 ms; long tasks/min 0.0; blocked 0 ms; frame p95 16.8 ms; heard jitter p95 20.3 ms (10 ticks, 0 skips); SSE 0 B/min; heap growth 13285116 B, retained 831972 B.
loaded: lag p50/p95/max 265.0/488.1/712.9 ms; long tasks/min 253.0; blocked 23296 ms; frame p95 83.4 ms; heard jitter p95 97.2 ms (10 ticks, 0 skips); SSE 0 B/min; heap growth 28031952 B, retained 8204744 B.

with the final implementation (unchanged by this documentation commit):
idle: lag p50/p95/max 83.4/128.4/134.9 ms; long tasks/min 0.0; blocked 0 ms; frame p95 16.7 ms; heard jitter p95 0.1 ms (9 ticks, 0 skips); SSE 2491988 B/min; heap growth 1679900 B, retained 1122384 B.
loaded: lag p50/p95/max 95.8/163.6/254.2 ms; long tasks/min 0.0; blocked 0 ms; frame p95 16.8 ms; heard jitter p95 0.2 ms (9 ticks, 0 skips); SSE 61905678 B/min; heap growth 51241520 B, retained 8911952 B.

No dx, 3D, l1, host locks, io_facts or plugins.d edits. Work stays on lane/perf-codex; no merge or push.
```
