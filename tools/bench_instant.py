#!/usr/bin/env python3
"""How long a page waits for the host, and what the host costs when nobody asks: the numbers tools/bench_classic.js does not take.

    /usr/bin/python3 /home/bam/io-diagnostics/tools/bench_instant.py --label after

On a fixture host (never hardware, never a port of Stable or Preview), with Preview's load: 26,867 events on disk, 300 boards a bus, a driver whose ``describe`` takes 3 s
(what ``uv run`` costs under a load of 60):

- ``cold_start``: seconds from the process start to the first answer, the first ``/state``, and the first ``/activity`` that has the log on disk.
- ``idle_cpu_pct``: the whole process group's CPU with nobody connected, over ``--idle-seconds``, as a percentage of one core.
- ``node_ms``: ``/api/v1/node`` p50 / p95 / max with every CPU taken by busy processes, which it starts and stops by PID.

``--checkout DIR`` measures another tree with the same ruler (main's, for a before). The JSON goes to bench/classic/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
EVENTS = 26_867
BOARDS = 300
ADAPTER = "host/usb1/port-1-3/usb-1-3"
BOARD = f"{ADAPTER}/can/1"
SLOW_DESCRIBE_S = 3.0
SLOW_PLUGIN = """
import json, sys, time
verb = next(word for word in sys.argv[1:] if word in ("describe", "diagnose", "probe"))
if verb == "describe":
    time.sleep(%r)
    print(json.dumps({"name": "slow", "schema": "diagnostics-record/0", "record_version": "0.1", "actions": []}))
else:
    print(json.dumps({"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": "slow: ran", "path": ""}))
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def percentiles(values: list[float]) -> dict:
    ordered = sorted(values)
    at = lambda fraction: round(ordered[min(len(ordered) - 1, int(fraction * len(ordered) + 0.999999) - 1)], 1)  # noqa: E731
    return {"p50": at(0.5), "p95": at(0.95), "max": round(ordered[-1], 1), "n": len(ordered)}


def group_cpu_s(group: int) -> float:
    """CPU seconds of every process in ``group`` that is alive now."""

    ticks = 0
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            fields = Path(f"/proc/{name}/stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(fields[2]) == group:
            ticks += int(fields[11]) + int(fields[12])
    return ticks / os.sysconf("SC_CLK_TCK")


def write_log(directory: Path, count: int, clock: float, checkout: Path) -> None:
    code = f"""
import json, sys
sys.path.insert(0, {str(checkout)!r})
from contract.record import Event, to_dict
from io_diagnostics.activity import event_id
paths = ["host", {ADAPTER!r}, {BOARD!r}, "host/usb6/port-6-1/usb-6-1", "host/usb1/port-1-5/usb-1-5"]
codes = ["LevelChanged", "ProbeBlocked", "Plugged", "PluginReloaded"]
with open({str(directory / "20261025T120718Z-5a67a0fa-000.jsonl")!r}, "w") as file:
    for number in range({count}):
        event = Event(codes[number % 4], {clock} - 60 - number * 7.0, "info", f"event {{number}}", path=paths[number % 5], seq=number, source="bench", args={{"id": f"bench:{{number}}"}})
        file.write(json.dumps({{"key": event_id(event), "event": to_dict(event)}}) + "\\n")
"""
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-c", code], check=True)


class Fixture:
    def __init__(self, checkout: Path, root: Path):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        plugins = root / "plugins"
        plugins.mkdir()
        source = checkout / "tests" / "fixtures" / "plugins-state"
        for name in ("state_joint.toml", "state_camera.toml"):
            (plugins / name).write_text((source / name).read_text().replace("{here}", str(source)))
        (plugins / "slow.py").write_text(SLOW_PLUGIN % SLOW_DESCRIBE_S)
        (plugins / "slow.toml").write_text(f'name = "slow"\ncommand = ["{{python}}", "{plugins}/slow.py"]\nperiod_s = 300\nmatches = ["host"]\n')
        environment = {**os.environ, "IO_DIAGNOSTICS_STATE_DIR": str(root / "state"), "IO_DIAGNOSTICS_CONFIG_DIR": str(root / "config"),
                       "STATE_NODES": str(BOARDS), "STATE_EXTRA_ROWS": "200", "STATE_PERIOD_S": "0.01", "STATE_CONFIG_FILE": str(root / "board.json"),
                       "STATE_PAUSE_FILE": str(root / "pause")}
        environment.pop("VIRTUAL_ENV", None)
        self.started = time.monotonic()
        self.process = subprocess.Popen([sys.executable, str(checkout / "io_diagnostics" / "cli.py"), "serve", "--fixture", "F1", "--port", str(self.port), "--no-udev",
                                         "--plugins", str(plugins)], cwd=checkout, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def get(self, path: str, timeout: float = 30.0) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=timeout) as response:
            return json.load(response)

    def until(self, path: str, good, seconds: float = 90.0) -> float:
        """Seconds from the process start until ``good(answer)``."""

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                if good(self.get(path, timeout=10.0)):
                    return round(time.monotonic() - self.started, 2)
            except (OSError, ValueError):
                pass
            time.sleep(0.02)
        raise SystemExit(f"{path}: no good answer in {seconds} s")

    def stop(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(8)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkout", type=Path, default=HERE, help="the tree to measure (default: this one)")
    parser.add_argument("--label", default="run")
    parser.add_argument("--idle-seconds", type=float, default=30.0)
    parser.add_argument("--load-seconds", type=float, default=15.0)
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    sha = subprocess.run(["git", "-C", str(checkout), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    clock = json.loads((checkout / "tests" / "fixtures" / "plugins" / "v6_frames.json").read_text())["frames"]["F1"]["clock"]
    root = Path(tempfile.mkdtemp(prefix="bench-instant-"))
    report = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sha": sha, "label": args.label, "checkout": str(checkout), "machine_load": os.getloadavg()[0],
              "load": {"events": EVENTS, "boards_per_adapter": BOARDS, "describe_s": SLOW_DESCRIBE_S, "busy_processes": os.cpu_count()}}
    burners: list[subprocess.Popen] = []
    fixture = None
    try:
        (root / "state").mkdir()
        (root / "config").mkdir()
        write_log(root / "state" / "log", EVENTS, clock, checkout)
        fixture = Fixture(checkout, root)
        report["cold_start"] = {
            "to_first_answer_s": fixture.until("/api/v1/health", lambda answer: answer.get("ok")),
            "to_state_s": fixture.until("/api/v1/state", lambda answer: answer.get("tree", {}).get("path") == "host"),
            "to_log_s": fixture.until("/api/v1/activity?fold=1&limit=50&severity=debug", lambda answer: len(answer.get("events", [])) >= 50)}
        fixture.until(f"/api/v1/node?path={urllib.parse.quote(BOARD)}", lambda answer: answer.get("config"))
        time.sleep(3.0)  # the first runs settle
        before, began = group_cpu_s(fixture.process.pid), time.monotonic()
        time.sleep(args.idle_seconds)
        report["idle_cpu_pct"] = round(100 * (group_cpu_s(fixture.process.pid) - before) / (time.monotonic() - began), 1)
        burners = [subprocess.Popen([sys.executable, "-c", "while True: pass"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(os.cpu_count() or 4)]
        samples: list[float] = []
        end = time.monotonic() + args.load_seconds
        while time.monotonic() < end:
            for path in ("host", ADAPTER, BOARD):
                began = time.perf_counter()
                fixture.get(f"/api/v1/node?path={urllib.parse.quote(path)}")
                samples.append((time.perf_counter() - began) * 1000)
        report["node_ms"] = percentiles(samples)
        report["status"] = "measured"
    finally:
        for burner in burners:
            burner.kill()
        for burner in burners:
            burner.wait(5)
        if fixture is not None:
            fixture.stop()
        shutil.rmtree(root, ignore_errors=True)
    output = HERE / "bench" / "classic" / f"{time.strftime('%Y-%m-%d', time.gmtime())}-{sha}-instant-{args.label}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
