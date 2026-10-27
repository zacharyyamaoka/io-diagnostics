# RobotDx page KPIs (the data-ui page, `io_diagnostics/web/dx/`)

What `tools/bench_robotdx.py` measures on every run, why, and the budget each is held to. The machine definitions live in
`bench/kpi_catalog.json` (published as the producer's `kpi_catalog.json`); this file is the reasoning. Background: PEP 0001
§8 (the KPIs K1-K7 proposed with the inventory) and the prior-art note *C - Live data with rewind* (Rerun's and Foxglove's
budgets: 60 frames a second while streaming, under 30 ms from a log call to the pixel).

```bash
/usr/bin/python3 /home/bam/io-diagnostics/tools/bench_robotdx.py --publish --to /home/bam/vt/zach/producers/robotdx
```

## The KPIs

All lower is better. Every number is read by the browser (performance.now, requestAnimationFrame, CDP), in headless Chrome
at 1400×900 with data-ui's bench flags (no background throttling) on the GPU (ANGLE on Vulkan, `--software` for the CPU's
raster); each point says which renderer in `labels.renderer`.

| KPI | What is timed | Budget | Where the budget comes from |
|---|---|---|---|
| `first_paint_ms` | navigation start → the entity tree's rows drawn (an in-page 10 ms timer started before the page's code), cold, p95 of 5 | **1,500 ms** | PEP 0001 K7 |
| `ingest_to_pixel_ms` | a samples batch's stamp → the first plot draw at its newest sample, p95 (p50 as a slice) | **30 ms** | prior art: log to render under 30 ms |
| `frame_time_ms` | requestAnimationFrame intervals while streaming N samples/s into M plots, p95 | **16.7 ms** | 60 frames a second (Rerun, Foxglove) |
| `heap_mb` | JS heap after a forced GC once the live store streamed into its byte cap | diagnostic (near the cap, never past it) | PEP 0008 §3: the page's buffer is the budget |
| `scrub_ms` | `clock.seek(t)` → the last panel's first draw at t on Explore (7 live panels), p95 of 30 | **50 ms** | PEP 0001 K5 |
| `back_to_live_ms` | `clock.goLive()` after a 2 s pause → the last panel's first following draw, p95 of 10 | **100 ms** | one frame of "back to live" plus the panels' draw |
| `idle_traffic_kb_s` | every byte from `/api/` (streams included) over 30 s on Explore, nothing selected | **8 KB/s** | PEP 0001 K2 |
| gate `no_page_errors` | no phase threw, no console error | 0 | a page that throws is a fact on any machine |

## Where each is measured, and what that means for reading it

- **The fixture host** (`tools/dx_demo.py`: F1 with the fixture plugins only, a scratch config dir): first paint, idle
  traffic, scrub, back to live, and ingest stamped at the **adapter's receive** (the page's samples event): the host's
  replay clock is not the browser's, so its emit cannot be compared; and its feed ticks about once a second while a watch
  runs, which this number does not include. F1 replays every record each ~2 s tick, so most tree rows change every tick and
  idle traffic is near the worst a rig sends.
- **The stub** (`bench/robotdx_stub.py`, TEST-ONLY: no plugin, no device): the frame budget is about thousands of samples a
  second, which no fixture plugin reaches (the fixture joint streams 10 Hz). It speaks `/api/v1/live`'s shape and stamps each
  batch with its wall clock at emit, the clock the browser reads, so `ingest_to_pixel_ms` is from the **host's emit**
  (`labels.stamp: host-emit`). The sweep: 600 samples/s into 2 plots, 6,000 into 6 (the headline), 24,000 into 12; 5 keys a plot.
- **Heap**: a default run streams the sweep's highest rate into a **64 MB cap for 90 s** (said in `labels.window`);
  `--long` streams **10 minutes into the page's 256 MB** cap.

## Trust, missing values, publishing (data-ui `bench/publish.py`'s conventions)

- A run is **noisy** when its calibration (a fixed loop of JavaScript, timed in the browser at each phase's start and end) is
  over 1.3× this host's best (kept in `~/.cache/robotdx-bench/calibration.json`), or the load per CPU is over 1.0. A noisy
  run's points go to `kpi_diagnostics.jsonl` (`--include-noisy` publishes them, drawn hollow); the gate publishes either way.
- A number that was not measured is **null with `labels.why`**, never 0.
- `--publish --to <folder>` writes only there: `kpi_catalog.json`, `kpi_series.jsonl` (vt-kpi-point/1), `runs_table.jsonl`
  (vt-run/1, one per phase), `items/<run_id>.jsonl` (every sample), `kpi_diagnostics.jsonl`. A run already in
  `runs_table.jsonl` is skipped: publishing twice changes nothing.
