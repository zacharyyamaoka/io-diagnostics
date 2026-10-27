#!/usr/bin/env python3
"""A TEST-ONLY stand-in for a RobotDx host at a rate no fixture reaches: it serves the page (io_diagnostics/web) and a
synthetic /api/v1 whose GET /api/v1/live speaks the real feed's shape (docs/api-v1.md "Live": a `snapshot` of /state, then
`samples` events in the /values?since= shape). It runs no plugin and opens no device: every number is made here.

    /usr/bin/python3 bench/robotdx_stub.py --port 5650 --rate 6000 --entities 6 --keys 5

  --rate      samples per second in all (spread over every entity's keys), emitted --tick times a second
  --entities  synthetic entities stub/0 .. stub/M-1 (one plot panel each in the bench's dashboard)
  --keys      State keys per entity

The bench (bench/robotdx_driver.js, tools/bench_robotdx.py) opens `/dx/index.html?go=dashboards&board=bench`: the
dashboard `bench.base` (one plot per entity) is served as a host file (GET /api/v1/dashboards). Each samples event's
`now` is the stub's own wall clock at emit, the same clock the browser reads, so ingest-to-pixel is measured from the
host's emit (`stamp: host-emit`). WHY a stub and not a fixture plugin: the fixture host's watch streams 10 Hz and its feed
ticks at most once a second; the frame budget is about 60 frames a second at thousands of samples. Stdlib only.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import mimetypes
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB = Path(__file__).resolve().parents[1] / "io_diagnostics" / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json", ".base": "text/plain; charset=utf-8", ".svg": "image/svg+xml"}


class Stream:
    """The synthetic State: K keys per entity, smooth signals with a little noise, a serial cursor per entity."""

    def __init__(self, entities: int, keys: int, rate: float, tick: float):
        self.paths = [f"stub/{i}" for i in range(entities)]
        self.keys = [f"s{k}" for k in range(keys)]
        self.rate, self.tick = rate, tick
        self.serial = {p: 0 for p in self.paths}
        self.ring: dict[str, collections.deque] = {p: collections.deque() for p in self.paths}   # the last 2 s of points per entity: /values' backlog
        self.lock = threading.Lock()
        self.t0 = time.time()

    def points(self, now: float, last: float) -> dict[str, dict[str, list]]:
        """The points of every entity and key in (last, now], evenly spaced at the rate."""

        per_series = self.rate / (len(self.paths) * len(self.keys))
        n = max(1, round((now - last) * per_series))
        out: dict[str, dict[str, list]] = {}
        with self.lock:
            for i, path in enumerate(self.paths):
                rows = {}
                for k, key in enumerate(self.keys):
                    pts = []
                    for j in range(1, n + 1):
                        t = last + (now - last) * j / n
                        self.serial[path] += 1
                        v = math.sin(2 * math.pi * (0.2 + 0.07 * k) * (t - self.t0) + i) + 0.02 * math.sin(97 * t + k)
                        pts.append([round(t, 6), round(v, 5), self.serial[path]])
                    rows[key] = pts
                out[path] = rows
                flat = [(key, p) for key, pts in rows.items() for p in pts]
                self.ring[path].extend(flat)
                cut = now - 2.0
                while self.ring[path] and self.ring[path][0][1][0] < cut:
                    self.ring[path].popleft()
        return out

    def values(self, path: str, now: float) -> dict:
        with self.lock:
            rows = {}
            for key, p in self.ring.get(path, []):
                rows.setdefault(key, []).append(p)
            cursor = self.serial.get(path, 0)
        return {"path": path, "now": now, "cursor": cursor, "streams": [], "heard": {"received": now, "age_s": 0.0},
                "watch": {"supported": True, "state": "running", "reason": "", "holders": [], "period_s": self.tick},
                "groups": [{"name": "motion", "stale": False, "age_s": 0.0,
                            "rows": [{"key": key, "value": (pts[-1][1] if pts else None), "unit": "", "text": "", "meaning": "", "level": None,
                                      "limits": {}, "source": "stub", "points": pts} for key, pts in rows.items()]}]}


def node_row(path: str, parent: str | None, name: str, now: float, kind: str = "device") -> dict:
    return {"path": path, "name": name, "kind": kind, "icon": "device", "parent": parent, "edge": "via" if parent else None,
            "depth": 0 if parent is None else 1, "level": "ok", "message": "synthetic (bench stub)", "own": {"level": "ok", "message": ""},
            "rollup": {"level": "ok", "origin": None, "origin_name": None, "message": "", "counts": {}, "knock_on": []}, "hint": None,
            "knock_on_of": None, "stale": False, "connected": True, "hardware_id": "", "identity": {"model": "stub"}, "owner": "bench-stub",
            "id_text": "", "last_received": now, "age_s": 0.0, "expected_period_s": None, "stale_after_s": None, "checks": 0, "actions": 0,
            "faults": 0, "collapsed": False, "presence": {"state": "live", "age_s": 0.0, "restored": False, "text": ""}, "claimed": True,
            "usb": None, "rate": None, "children": []}


def make_state(stream: Stream, now: float) -> dict:
    host = node_row("host", None, "bench stub (synthetic)", now, kind="host")
    host["children"] = [node_row(p, "host", f"Stub {i}", now) for i, p in enumerate(stream.paths)]
    return {"api": "io-diagnostics/1", "schema": "diagnostics-record/0", "record_version": "0.2",
            "clock": {"now": now, "iso": "", "time": "", "mode": "stub", "utc_offset_s": 0, "rank": ["ok", "warn", "unknown", "stale", "error"]},
            "fixture": {"name": "STUB", "label": "bench stub (synthetic)"}, "host": {"name": "bench", "sources": []},
            "summary": {"level": "ok", "counts": {}, "attention": [], "entities": len(stream.paths) + 1, "devices": len(stream.paths)},
            "chip": {"label": "IO stub", "status": "ok"}, "tree": host, "recent": [], "expected_setup": {}, "composition": {"composites": []},
            "runs": {"active": 0, "total": 0, "items": []}}


def bench_base(stream: Stream, window: str) -> str:
    """The dashboard the bench draws: one plot per entity (every key a series), and the time bar."""

    lines = ["# The bench's dashboard (bench/robotdx_stub.py): one live plot per synthetic entity.", "datasets:"]
    lines += [f'  e{i}:\n    data: "../datasets/samples.json?path={p}"' for i, p in enumerate(stream.paths)]
    lines += ['  summary:\n    data: "../datasets/summary.json"', "layout:", "  columns: 12", "  rowHeight: 80", "  panels:",
              "    - { view: Time, w: 12, h: 1 }"]
    w = 6 if len(stream.paths) > 1 else 12
    lines += [f"    - {{ view: Plot {i}, w: {w}, h: 3 }}" for i in range(len(stream.paths))]
    lines += ["views:", "  - type: playback", "    name: Time", "    from: summary"]
    lines += [f"  - type: plot\n    name: Plot {i}\n    from: e{i}\n    at: {window}\n    plot: {{ y: [value], series: key }}" for i in range(len(stream.paths))]
    return "\n".join(lines) + "\n"


class Feed:
    """One emitter thread; each client gets its own queue of frames (a slow client is cut loose, never waited for)."""

    def __init__(self, stream: Stream):
        self.stream = stream
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()
        self.counter = 0
        threading.Thread(target=self.run, daemon=True).start()

    def frame(self, name: str, data: dict) -> bytes:
        self.counter += 1
        return f"id: {self.counter}\nevent: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()

    def run(self) -> None:
        last = time.time()
        while True:
            time.sleep(self.stream.tick)
            now = time.time()
            points = self.stream.points(now, last)
            last = now
            with self.lock:
                if not self.clients:
                    continue
                frames = []
                for path, rows in points.items():
                    frames.append(self.frame("samples", {
                        "path": path, "now": now, "cursor": self.stream.serial[path], "streams": [], "heard": {"received": now, "age_s": 0.0},
                        "watch": {"supported": True, "state": "running", "reason": "", "holders": [], "period_s": self.stream.tick},
                        "groups": [{"name": "motion", "stale": False, "age_s": 0.0,
                                    "rows": [{"key": key, "value": pts[-1][1], "unit": "", "text": "", "meaning": "", "level": None,
                                              "limits": {}, "source": "stub", "points": pts} for key, pts in rows.items()]}]}))
                data = b"".join(frames)
                for q in list(self.clients):
                    if q.qsize() > 600:
                        self.clients.discard(q)
                        q.put(None)
                    else:
                        q.put(data)


def handler(stream: Stream, feed: Feed, base_text: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            pass

        def send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def json(self, payload, code: int = 200):
            self.send(code, json.dumps(payload).encode(), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            now = time.time()
            p = u.path
            if p == "/api/v1/live":
                return self.live()
            if p == "/api/v1/events":
                return self.events()
            if p == "/api/v1/state":
                return self.json(make_state(stream, now))
            if p == "/api/v1/settings":
                return self.json({"history_window_s": 900, "source": "stub"})
            if p == "/api/v1/plugins":
                return self.json({"plugins": [{"name": "bench-stub", "note": "synthetic, no device"}]})
            if p == "/api/v1/activity":
                return self.json({"clock": {"now": now}, "events": [], "faults": [], "link_health": []})
            if p == "/api/v1/node":
                path = q.get("path", "")
                return self.json({"node": node_row(path, "host", path, now), "ancestors": [], "checks": [], "actions": [], "faults": [],
                                  "events": [], "config": [], "watch": {"supported": path != "host", "state": "running", "reason": "", "holders": []},
                                  "streams": [], "tabs": [], "heard": {"received": now, "age_s": 0.0}})
            if p == "/api/v1/values":
                return self.json(stream.values(q.get("path", ""), now))
            if p == "/api/v1/history":
                return self.json({"path": q.get("path", ""), "window_s": 900, "from": now - 900, "to": now, "statuses": []})
            if p == "/api/v1/dashboards":
                return self.json({"files": [{"name": "bench", "file": "bench.base", "size": len(base_text), "mtime": stream.t0, "etag": "bench"}]})
            if p == "/api/v1/dashboards/bench.base":
                return self.send(200, base_text.encode(), "text/plain; charset=utf-8", {"ETag": '"bench"'})
            if p.startswith("/api/"):
                return self.json({"error": f"no endpoint {p!r} in the bench stub"}, 404)
            name = "index.html" if p in ("/", "") else p.lstrip("/")
            target = (WEB / name).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self.send(404, b"not found", "text/plain")
            return self.send(200, target.read_bytes(), TYPES.get(target.suffix, mimetypes.guess_type(target.name)[0] or "application/octet-stream"))

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if urlparse(self.path).path == "/api/v1/watch":
                return self.json({"watch": {"supported": True, "state": "running", "reason": "", "holders": [], "period_s": stream.tick}})
            return self.json({"error": "the bench stub takes no other write"}, 405)

        def stream_head(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def live(self):
            self.stream_head()
            q: queue.Queue = queue.Queue()
            with feed.lock:
                snap = feed.frame("snapshot", make_state(stream, time.time()))
                feed.clients.add(q)
            try:
                self.wfile.write(b"retry: 2000\n" + snap)
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                    except queue.Empty:
                        data = b": keepalive\n\n"
                    if data is None:
                        return
                    self.wfile.write(data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            finally:
                with feed.lock:
                    feed.clients.discard(q)

        def events(self):
            self.stream_head()
            try:
                self.wfile.write(b'retry: 2000\nevent: hello\ndata: {"api": "io-diagnostics/1", "revision": 1, "latest": null}\n\n')
                self.wfile.flush()
                while True:
                    time.sleep(15)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--rate", type=float, default=6000, help="samples per second in all")
    ap.add_argument("--entities", type=int, default=6)
    ap.add_argument("--keys", type=int, default=5)
    ap.add_argument("--tick", type=float, default=1 / 60, help="seconds between emits (default 1/60)")
    ap.add_argument("--window", default="10s", help="each plot's at: window")
    args = ap.parse_args(argv)
    stream = Stream(args.entities, args.keys, args.rate, args.tick)
    feed = Feed(stream)
    server = ThreadingHTTPServer((args.bind, args.port), handler(stream, feed, bench_base(stream, args.window)))
    server.daemon_threads = True
    print(f"bench stub on http://{args.bind}:{args.port}/ · {args.rate:g} samples/s over {args.entities} entities x {args.keys} keys", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
