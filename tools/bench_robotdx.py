#!/usr/bin/env python3
"""The RobotDx page's KPIs (bench/KPIS.md), measured in headless Chrome on a fixture host and on the test-only stub.

    /usr/bin/python3 /home/bam/io-diagnostics/tools/bench_robotdx.py                       # a short run: JSON on stdout
    /usr/bin/python3 /home/bam/io-diagnostics/tools/bench_robotdx.py --long                # + 10 minutes at the 256 MB cap
    /usr/bin/python3 /home/bam/io-diagnostics/tools/bench_robotdx.py --publish --to /home/bam/vt/zach/producers/robotdx
    /usr/bin/python3 /home/bam/io-diagnostics/tools/bench_robotdx.py --from results.json --publish --to DIR

What runs (every host and browser it starts, it stops):
  fixture   tools/dx_demo.py (fixture F1, the fixture plugins only, a scratch config and state dir): first_paint_ms (cold
            loads), idle_traffic_kb_s (Explore, nothing selected), scrub_ms and back_to_live_ms (Explore on the joint, seven
            live panels), ingest_to_pixel_ms from the adapter's receive (the host's replay clock is not the browser's)
  sweep     bench/robotdx_stub.py, a synthetic host (no plugin, no device) speaking /api/v1/live's shape: frame_time_ms while
            streaming N samples/s into M plot panels, at three N x M, and ingest_to_pixel_ms from the host's emit (the stub
            stamps its wall clock, which the browser shares)
  heap      the stub at the sweep's highest rate: heap_mb after a GC at the retention cap. Short: a 64 MB cap for 90 s (said
            in the point's labels). --long: the page's 256 MB cap for 10 minutes.
Trust (data-ui bench/runner.py's rule): a run is noisy when its calibration (a fixed loop of JavaScript) is over 1.3x this
host's best, or the load per CPU is over 1.0. Noisy points still publish, to kpi_diagnostics.jsonl unless --include-noisy.
Missing numbers are null with a reason, never 0. Publishing (vt-kpi/1, data-ui bench/publish.py's conventions) writes only
in --to: kpi_catalog.json, kpi_series.jsonl, runs_table.jsonl, items/<run_id>.jsonl, kpi_diagnostics.jsonl; a run already
published (its run ids in runs_table.jsonl) is skipped, so publishing twice changes nothing. Stdlib only, plus the Node driver
bench/robotdx_driver.js (Playwright, headless Chrome on the GPU unless --software).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "bench"
DRIVER = BENCH / "robotdx_driver.js"
STUB = BENCH / "robotdx_stub.py"
DEMO = ROOT / "tools" / "dx_demo.py"
CATALOG_FILE = BENCH / "kpi_catalog.json"
NODE = "/usr/bin/node"
PYTHON = sys.executable or "/usr/bin/python3"
BOARD = "host/usb1/port-1-3/usb-1-3/can/1"   # the fixture's joint
SWEEP = ((600, 2), (6000, 6), (24000, 12))   # samples/s x plot panels (5 keys each)
STATE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "robotdx-bench"
NOISY_CALIB, NOISY_LOAD = 1.3, 1.0
BENCH_VERSION = "robotdx-bench/1"


def pct(values, p):
    v = sorted(x for x in values if isinstance(x, (int, float)) and math.isfinite(x))
    return None if not v else v[max(1, math.ceil(p / 100 * len(v))) - 1]


def rnd(x, d=1):
    return None if x is None else round(x, d)


def free_port(first=5640, last=5699) -> int:
    for port in range(first, last + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit("no free port in 5640-5699")


class Process:
    """A host or stub this run starts: its own session, stopped by its pid's group, always."""

    def __init__(self, argv, ready: str, log: Path, env=None):
        self.log = open(log, "w")
        self.p = subprocess.Popen(argv, cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True, env=env)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.p.poll() is not None:
                raise RuntimeError(f"{argv[1]} exited: {Path(log).read_text()[-800:]}")
            text = Path(log).read_text()
            found = re.search(ready, text)
            if found:
                self.url = found.group(1)
                return
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"{argv[1]} did not start: {Path(log).read_text()[-800:]}")

    def stop(self):
        try:
            os.killpg(self.p.pid, signal.SIGTERM)
            self.p.wait(timeout=15)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.log.close()


def drive(cfg: dict, timeout: float) -> dict:
    out = subprocess.run([NODE, str(DRIVER), json.dumps(cfg)], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    line = (out.stdout.strip().splitlines() or [""])[-1]
    try:
        return json.loads(line)
    except ValueError:
        return {"phase": cfg.get("phase"), "failed": f"driver printed no JSON (exit {out.returncode}): {out.stderr[-600:]}"}


def git(*args) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def best_calibration(host: str, measured: float) -> float:
    """This host's best calibration ever (the quiet-machine reference), kept outside git; updated when beaten."""

    f = STATE / "calibration.json"
    try:
        known = json.loads(f.read_text())
    except (OSError, ValueError):
        known = {}
    best = min(measured, known.get(host, measured))
    if best != known.get(host):
        STATE.mkdir(parents=True, exist_ok=True)
        known[host] = best
        f.write_text(json.dumps(known, indent=1) + "\n")
    return best


def run(args) -> dict:
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    scratch = Path(tempfile.mkdtemp(prefix="robotdx-bench-"))
    load0 = os.getloadavg()[0]
    phases, procs = {}, []
    base_cfg = {"software": args.software}
    try:
        # ---- the fixture host: first paint, idle traffic, scrub, back to live, ingest (adapter receive) ----------------------------
        demo = Process([PYTHON, str(DEMO), "--port", str(free_port()), "--state", str(scratch / "fixture"), "--quiet"],
                       r"RobotDx on data-ui: (http://\S+?)/dx/index\.html", scratch / "fixture.log")
        procs.append(demo)
        with urllib.request.urlopen(f"{demo.url}/api/v1/plugins", timeout=10) as answer:
            plugins = json.load(answer)
        names = sorted(p["name"] for p in plugins.get("plugins", []))
        if not set(names) <= {"state_joint", "state_camera"}:
            raise SystemExit(f"refusing to go on: the fixture host loaded {names}")
        time.sleep(20)   # the joint's State and levels history grow a little before the scrub
        phases["fixture"] = drive({**base_cfg, "phase": "fixture", "base": demo.url, "board": BOARD, "loads": args.loads, "seeks": args.seeks,
                                   "lives": args.lives, "idleS": args.idle_s, "ingestS": args.ingest_s}, timeout=900)
        demo.stop()
        procs.remove(demo)
        # ---- the stub: frame time and ingest (host emit) at three N x M; the heap at the highest -----------------------------------
        for rate, panels in SWEEP:
            stub = Process([PYTHON, str(STUB), "--port", str(free_port()), "--rate", str(rate), "--entities", str(panels), "--keys", "5"],
                           r"bench stub on (http://\S+?)/ ", scratch / f"stub-{rate}x{panels}.log")
            procs.append(stub)
            phases[f"sweep@{rate}x{panels}"] = drive({**base_cfg, "phase": "sweep", "base": stub.url, "seconds": args.sweep_s}, timeout=300)
            stub.stop()
            procs.remove(stub)
        rate, panels = SWEEP[-1]
        stub = Process([PYTHON, str(STUB), "--port", str(free_port()), "--rate", str(rate), "--entities", str(panels), "--keys", "5"],
                       r"bench stub on (http://\S+?)/ ", scratch / "stub-heap.log")
        procs.append(stub)
        cap, seconds = (256, 600) if args.long else (64, 90)
        phases["heap"] = drive({**base_cfg, "phase": "heap", "base": stub.url, "capMB": cap, "seconds": seconds}, timeout=seconds + 300)
        stub.stop()
        procs.remove(stub)
    finally:
        for p in procs:
            p.stop()
    load1 = os.getloadavg()[0]
    finished = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return summarize(phases, started, finished, (load0, load1), args)


def summarize(phases: dict, started, finished, loads, args) -> dict:
    host = socket.gethostname()
    calib = [c for ph in phases.values() for c in (ph.get("calibration_ms") or []) if c is not None]
    best = best_calibration(host, min(calib)) if calib else None
    why = []
    if calib and best and max(calib) > NOISY_CALIB * best:
        why.append(f"calibration {max(calib):.0f} ms is {max(calib) / best:.2f}x this host's best {best:.0f} ms")
    cpus = os.cpu_count() or 1
    if max(loads) / cpus > NOISY_LOAD:
        why.append(f"load average {max(loads):.1f} on {cpus} CPUs")
    fx = phases.get("fixture", {})
    errors = [f"{name}: {e}" for name, ph in phases.items() for e in (ph.get("errors") or [])]
    errors += [f"{name}: {ph['failed']}" for name, ph in phases.items() if ph.get("failed")]
    renderer = next((ph.get("renderer") for ph in phases.values() if ph.get("renderer")), None)
    kind = next((ph.get("renderer_kind") for ph in phases.values() if ph.get("renderer_kind")), "none")

    def missing(why_):
        return {"value": None, "n": 0, "why": why_}

    k = {}
    fp = [x for x in (fx.get("first_paint") or []) if x is not None]
    k["first_paint_ms"] = {"value": pct(fp, 95), "n": len(fp), "samples": fx.get("first_paint")} if fp else missing("no cold load drew the tree")
    idle = fx.get("idle") or {}
    k["idle_traffic_kb_s"] = {"value": idle.get("kb_s"), "n": 1, "seconds": idle.get("seconds"), "feed": idle.get("mode")} if idle.get("kb_s") is not None else missing("the idle measurement did not run")
    scr = [x["ms"] for x in fx.get("scrub") or [] if x.get("ms") is not None]
    k["scrub_ms"] = {"value": rnd(pct(scr, 95)), "p50": rnd(pct(scr, 50)), "n": len(scr), "of": len(fx.get("scrub") or [])} if scr else missing("no seek drew every panel")
    back = [x["ms"] for x in fx.get("back") or [] if x.get("ms") is not None]
    k["back_to_live_ms"] = {"value": rnd(pct(back, 95)), "p50": rnd(pct(back, 50)), "n": len(back), "of": len(fx.get("back") or [])} if back else missing("no go-live drew every panel")
    sweeps = {name.split("@", 1)[1]: ph.get("sweep") or {} for name, ph in phases.items() if name.startswith("sweep@")}
    head = f"{SWEEP[1][0]}x{SWEEP[1][1]}"   # the middle point is the headline: 6,000 samples/s into 6 plots
    frame, ingest = {}, {}
    for nm, sw in sweeps.items():
        gaps = sw.get("gaps") or []
        frame[nm] = {"value": rnd(pct(gaps, 95)), "p50": rnd(pct(gaps, 50)), "max": rnd(max(gaps)) if gaps else None, "n": len(gaps),
                     "dropped": sum(1 for g in gaps if g > 25), "loop_p95_ms": rnd(pct(sw.get("loop") or [], 95)), "samples_s": rnd(sw.get("samples_s"), 0),
                     "panels": sw.get("panels"), "long_tasks": len(sw.get("longs") or [])} if gaps else missing("no frames recorded while streaming")
        lat = sw.get("fromEmit") or []
        ingest[nm] = {"value": rnd(pct(lat, 95)), "p50": rnd(pct(lat, 50)), "n": len(lat), "stamp": "host-emit",
                      "from_receive_p95": rnd(pct(sw.get("fromGot") or [], 95))} if lat else missing("no batch was drawn")
    k["frame_time_ms"] = {**frame.get(head, missing(f"the {head} sweep did not run")), "slice": head}
    k["ingest_to_pixel_ms"] = {**ingest.get(head, missing(f"the {head} sweep did not run")), "slice": head}
    flat = (fx.get("ingest") or {}).get("lat") or []
    ingest_fixture = {"value": rnd(pct(flat, 95)), "p50": rnd(pct(flat, 50)), "n": len(flat), "stamp": "adapter-receive",
                      "note": "the fixture host's samples come in its ~1 s change ticks; this is the page's part only"} if flat else missing("no batch drawn on the fixture host")
    hp = (phases.get("heap") or {}).get("heap") or {}
    window = f"{hp.get('cap_mb')} MB cap, {hp.get('seconds')} s{' (short: --long runs 10 min at 256 MB)' if not args.long else ''}"
    k["heap_mb"] = {"value": hp.get("heap_mb"), "n": 1, "window": window, "store_mb": hp.get("store_mb"), "at_cap_s": hp.get("at_cap_s"),
                    "reached_cap": hp.get("at_cap_s") is not None} if hp.get("heap_mb") is not None else missing("the heap run did not finish")
    sha = git("rev-parse", "--short=8", "HEAD")
    dirty = bool(git("status", "--porcelain", "--", "io_diagnostics/web/dx", "bench", "tools/bench_robotdx.py"))
    return {"schema": "robotdx-bench/1", "bench_version": BENCH_VERSION, "started": started.isoformat(), "finished": finished.isoformat(),
            "mode": "long" if args.long else "short", "code": {"sha": sha, "dirty": dirty, "branch": git("rev-parse", "--abbrev-ref", "HEAD")},
            "host": {"hostname": host, "cpus": os.cpu_count(), "os": platform.platform(), "renderer": renderer, "renderer_kind": kind,
                     "browser": next((ph.get("browser") for ph in phases.values() if ph.get("browser")), None)},
            "trust": {"verdict": "noisy" if why else "quiet", "why": why, "calibration_ms": calib, "best_calibration_ms": best, "loadavg": list(loads), "cpus": cpus},
            "kpis": k, "slices": {"frame_time_ms": frame, "ingest_to_pixel_ms": {**ingest, "fixture": ingest_fixture}},
            "errors": errors, "phases": phases}


# ---- publishing (vt-kpi/1; data-ui bench/publish.py's conventions) ------------------------------------------------------------------

def publish(res: dict, out: Path, include_noisy: bool, event: str | None) -> int:
    catalog = json.loads(CATALOG_FILE.read_text())
    (out / "items").mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.fromisoformat(res["started"]).strftime("%Y%m%d-%H%M%S")
    runs_file = out / "runs_table.jsonl"
    seen = set()
    if runs_file.exists():
        for line in runs_file.read_text().splitlines():
            try:
                seen.add(json.loads(line)["run_id"])
            except (ValueError, KeyError):
                pass
    phases = res["phases"]
    run_ids = {name: f"{stamp}-{name.replace('@', '-')}" for name in phases}
    if any(rid in seen for rid in run_ids.values()):
        print(f"already published: {stamp}; nothing written", file=sys.stderr)
        return 0
    t = res["finished"]
    sha = res["code"]["sha"] + ("+dirty" if res["code"].get("dirty") else "")
    host = res["host"]["hostname"]
    trust = res["trust"]["verdict"]
    lane = f"chrome-headless-{res['host']['renderer_kind']}"
    labels = {"lane": lane, "host": host, "trust": trust, "renderer": res["host"]["renderer_kind"], "renderer_string": (res["host"]["renderer"] or "")[:160],
              "mode": res["mode"]}
    if res["trust"]["why"]:
        labels["noise"] = "; ".join(res["trust"]["why"])[:280]
    if event:
        labels["event"] = event
    (out / "kpi_catalog.json").write_text(json.dumps({**catalog, "generated_at": t}, indent=1) + "\n")
    runs = []
    for name, ph in phases.items():
        rid = run_ids[name]
        items = []
        for key in ("first_paint", "scrub", "back"):
            for i, x in enumerate(ph.get(key) or []):
                items.append({"item_id": f"{rid}-{key}-{i}", "facets": {"phase": key}, "value": x if not isinstance(x, dict) else x.get("ms"), **(x if isinstance(x, dict) else {})})
        sw = ph.get("sweep") or {}
        for i, g in enumerate(sw.get("fromEmit") or []):
            items.append({"item_id": f"{rid}-ingest-{i}", "facets": {"phase": "ingest", "stamp": "host-emit"}, "value": round(g, 2)})
        for i, g in enumerate((ph.get("ingest") or {}).get("lat") or []):
            items.append({"item_id": f"{rid}-ingest-{i}", "facets": {"phase": "ingest", "stamp": "adapter-receive"}, "value": round(g, 2)})
        if ph.get("heap"):
            items.append({"item_id": f"{rid}-heap", "facets": {"phase": "heap"}, **ph["heap"]})
        with (out / "items" / f"{rid}.jsonl").open("w") as f:
            for x in items:
                f.write(json.dumps(x) + "\n")
        runs.append({"schema": "vt-run/1", "run_id": rid, "t": t,
                     "facets": {"phase": name, "host": host, "lane": lane, "trust": trust, "code_sha": sha, "mode": res["mode"], "counted": True},
                     "metrics": {"items": len(items), "errors": len(ph.get("errors") or []) + (1 if ph.get("failed") else 0),
                                 "first_paint_ms_sum": sum(x for x in (ph.get("first_paint") or []) if x is not None),
                                 "frames": len(sw.get("gaps") or []), "frame_ms_sum": round(sum(sw.get("gaps") or []), 1)},
                     "items": {"path": f"items/{rid}.jsonl", "schema": "robotdx-bench-item/1", "n": len(items)}})
    src = {"code_sha": sha, "bench_version": BENCH_VERSION, "run_ids": list(run_ids.values())}
    points = []

    def point(kpi, entry, slice_=None, extra=None):
        value = entry.get("value")
        p = {"schema": "vt-kpi-point/1", "kpi": kpi, "t": t, "value": value, "n": entry.get("n") or 0,
             "status": "measured" if value is not None else "not_measured", "labels": {**labels, **(extra or {})}, "source": src}
        if slice_:
            p["slice"] = slice_
        if value is None:
            p["labels"]["why"] = (entry.get("why") or "no sample")[:300]
        return p

    k = res["kpis"]
    for kpi in ("first_paint_ms", "scrub_ms", "back_to_live_ms", "idle_traffic_kb_s"):
        points.append(point(kpi, k[kpi]))
    points.append(point("frame_time_ms", k["frame_time_ms"], extra={"sweep": k["frame_time_ms"].get("slice")}))
    points.append(point("ingest_to_pixel_ms", k["ingest_to_pixel_ms"], extra={"stamp": "host-emit", "sweep": k["ingest_to_pixel_ms"].get("slice")}))
    points.append(point("heap_mb", k["heap_mb"], extra={"window": k["heap_mb"].get("window", "")}))
    for nm, e in res["slices"]["frame_time_ms"].items():
        points.append(point("frame_time_ms", e, {"sweep": nm}))
    for nm, e in res["slices"]["ingest_to_pixel_ms"].items():
        points.append(point("ingest_to_pixel_ms", e, {"sweep": nm} if nm != "fixture" else {"source": "fixture-host"}, extra={"stamp": e.get("stamp", "")}))
    for kpi, key in (("scrub_ms", "p50"), ("back_to_live_ms", "p50")):
        if k[kpi].get(key) is not None:
            points.append(point(kpi, {"value": k[kpi][key], "n": k[kpi]["n"]}, {"stat": "p50"}))
    points.append(point("ingest_to_pixel_ms", {"value": k["ingest_to_pixel_ms"].get("p50"), "n": k["ingest_to_pixel_ms"].get("n"), "why": k["ingest_to_pixel_ms"].get("why")},
                        {"stat": "p50"}, extra={"stamp": "host-emit"}))
    # the gate: no phase threw, no page error (published whatever the trust: a page that throws is a fact on any machine)
    failing = [name for name, ph in phases.items() if ph.get("failed") or ph.get("errors")]
    gate = {"schema": "vt-kpi-point/1", "kpi": "no_page_errors", "t": t, "value": len(failing), "n": len(phases), "status": "measured",
            "pass": not failing, "labels": {"lane": lane, "host": host, **({"failing": ", ".join(failing)[:300]} if failing else {})}, "source": src}
    noisy = trust == "noisy" and not include_noisy
    with runs_file.open("a") as f:
        for r in runs:
            f.write(json.dumps(r) + "\n")
    with (out / ("kpi_diagnostics.jsonl" if noisy else "kpi_series.jsonl")).open("a") as f:
        for p in points:
            f.write(json.dumps(p) + "\n")
    with (out / "kpi_series.jsonl").open("a") as f:
        f.write(json.dumps(gate) + "\n")
    print(f"published {len(runs)} runs and {len(points)} KPI points ({stamp}, {sha}, trust {trust}) to {out}"
          + ("; the points went to kpi_diagnostics.jsonl (a noisy run; --include-noisy publishes them)" if noisy else ""), file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--long", action="store_true", help="the 10-minute memory run at the page's 256 MB cap (default: 64 MB for 90 s)")
    ap.add_argument("--software", action="store_true", help="headless Chrome's CPU raster instead of the GPU")
    ap.add_argument("--loads", type=int, default=5, help="cold loads for first_paint_ms")
    ap.add_argument("--seeks", type=int, default=30, help="seeks for scrub_ms")
    ap.add_argument("--lives", type=int, default=10, help="go-lives for back_to_live_ms")
    ap.add_argument("--idle-s", type=float, default=30, help="seconds of idle traffic")
    ap.add_argument("--ingest-s", type=float, default=20, help="seconds of ingest on the fixture host")
    ap.add_argument("--sweep-s", type=float, default=8, help="seconds per sweep point")
    ap.add_argument("--out", type=Path, help="also write the result JSON here")
    ap.add_argument("--from", dest="source", type=Path, help="publish this result instead of running")
    ap.add_argument("--publish", action="store_true", help="write vt-kpi/1 files into --to")
    ap.add_argument("--to", type=Path, help="the producer folder, e.g. /home/bam/vt/zach/producers/robotdx")
    ap.add_argument("--include-noisy", action="store_true", help="publish a noisy run's points as KPI points (default: kpi_diagnostics.jsonl)")
    ap.add_argument("--event", help="a milestone label on the points (e.g. 'phase 3b: /api/v1/live')")
    args = ap.parse_args(argv)
    if args.publish and not args.to:
        ap.error("--publish needs --to <producer folder>")
    res = json.loads(args.source.read_text()) if args.source else run(args)
    if args.out:
        args.out.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({key: res[key] for key in ("schema", "started", "finished", "mode", "code", "host", "trust", "kpis", "errors")}, indent=1))
    if args.publish:
        return publish(res, args.to.expanduser(), args.include_noisy, args.event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
