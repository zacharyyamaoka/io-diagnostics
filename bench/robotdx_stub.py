#!/usr/bin/env python3
"""A TEST-ONLY stand-in for a RobotDx host at a rate no fixture reaches: it serves the page (io_diagnostics/web) and a
synthetic /api/v1 whose GET /api/v1/live speaks the real feed's shape (docs/api-v1.md "Live"): a `snapshot` of /state, then
`samples` events in the /values?since= shape. It runs no plugin and opens no device: every number is made here.

    /usr/bin/python3 bench/robotdx_stub.py --port 5650 --rate 6000 --entities 6 --keys 5

  --rate      samples per second in all (spread evenly over every entity's keys), each point at its own exact time
  --entities  synthetic entities stub/0 .. stub/M-1 (one plot panel each in the bench's dashboard)
  --keys      State keys per entity
  --coalesce  seconds between a WATCHED entity's samples events (the host's SAMPLE_COALESCE_S, 0.1: since 5cc429f a watched
              entity's points are sent as they arrive, at most one event per entity per 100 ms). 0 is a host before 5cc429f
              (watched entities wait for the tick too); 0.0167 is phase 3b's sweep (an event per entity every frame)
  --tick      seconds between the change ticks that send every other entity's new points (the host's ~1 s)
  --past-warn seconds: in /history/tree, stub/0 (and the host row above it) was "warn" for this long after the stub
              started, and is OK since (a past that differs from now, for the tree's rewind check; 0, the default: OK)

An entity is watched for 10 s after a POST /api/v1/watch {path} (the page's heartbeat, every 3 s while its State is on
screen), as the host's watch is. Every event carries `emitted` (5cdb4b9: the stub's wall clock at send, unix ms; in the
snapshot a top-level key beside /state's), and the stub's `now` is its wall clock too (seconds), the same clock the browser
reads, so ingest-to-pixel is timed from the host's emit. GET /api/v1/checks (one check per entity) and
/api/v1/history/tree (every row OK since the stub started) answer in the host's shapes. A point is sent once: the feed
and /values?since= read one serial cursor per entity. The bench (bench/robotdx_driver.js, tools/bench_robotdx.py) opens
`/dx/?go=dashboards&board=bench`: the dashboard `bench.base` (one plot per entity), served as a host file.
WHY a stub and not a fixture plugin: the fixture host's watch streams 10 Hz; the frame budget is about 60 frames a second
at thousands of samples. Stdlib only.
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
    """The synthetic State: K keys per entity, smooth signals with a little noise, each point at its exact time (the rate is
    the rate: a slow one is slow), a serial number per entity that /values?since= and the feed share."""

    RING_S = 2.0   # /values' backlog: the last 2 s of points per entity

    def __init__(self, entities: int, keys: int, rate: float, tick: float, coalesce: float, past_warn: float = 0.0):
        self.paths = [f"stub/{i}" for i in range(entities)]
        self.keys = [f"s{k}" for k in range(keys)]
        self.rate, self.tick, self.coalesce = rate, tick, coalesce
        self.period = (entities * keys) / rate if rate > 0 else math.inf   # seconds between two points of one series
        self.serial = {p: 0 for p in self.paths}
        self.ring: dict[str, collections.deque] = {p: collections.deque() for p in self.paths}   # (key, [t, v, serial])
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.next_t = {p: self.t0 + self.period for p in self.paths}   # every key of an entity takes a point at the same t
        self.watched: dict[str, float] = {}   # path -> until (a heartbeat holds the watch 10 s, as the host's grace_s)
        self.past_warn = past_warn

    def is_watched(self, path: str, now: float) -> bool:
        return self.watched.get(path, 0.0) > now

    def watch_json(self, path: str, now: float) -> dict:
        state = "running" if self.is_watched(path, now) else "idle"
        return {"supported": path != "host", "state": state, "reason": "", "holders": [], "period_s": self.period if self.rate > 0 else None}

    def generate(self, now: float) -> None:
        """Every point due by `now` into the ring, at its own time."""

        with self.lock:
            for i, path in enumerate(self.paths):
                ring = self.ring[path]
                while self.next_t[path] <= now:
                    t = self.next_t[path]
                    for k, key in enumerate(self.keys):
                        self.serial[path] += 1
                        v = math.sin(2 * math.pi * (0.2 + 0.07 * k) * (t - self.t0) + i) + 0.02 * math.sin(97 * t + k)
                        ring.append((key, [round(t, 6), round(v, 5), self.serial[path]]))
                    self.next_t[path] = t + self.period
                cut = now - self.RING_S
                while ring and ring[0][1][0] < cut:
                    ring.popleft()

    def soonest(self) -> float:
        return min(self.next_t.values()) if self.paths else math.inf

    def answer(self, path: str, now: float, since: int) -> dict:
        """The /values?since= answer (the shape a samples event has too): the ring's points after `since`."""

        with self.lock:
            rows: dict[str, list] = {}
            for key, p in self.ring.get(path, []):
                if p[2] > since:
                    rows.setdefault(key, []).append(p)
            cursor = self.serial.get(path, 0)
        return {"path": path, "now": now, "cursor": cursor, "streams": [], "heard": {"received": now, "age_s": 0.0},
                "watch": self.watch_json(path, now),
                "groups": [{"name": "motion", "stale": False, "age_s": 0.0,
                            "rows": [{"key": key, "value": (pts[-1][1] if pts else None), "unit": "", "text": "", "meaning": "", "level": None,
                                      "limits": {}, "source": "stub", "points": pts} for key, pts in rows.items()]}]}

    def check(self, path: str, now: float) -> dict:
        return {"name": "stub: stream", "level": "ok", "effective_level": "ok", "stale": False, "message": f"{self.rate:g} samples/s in all (synthetic)",
                "code": "", "mode": None, "source": "bench-stub", "hardware_id": "", "stamp": self.t0, "received": self.t0, "received_iso": "",
                "age_s": now - self.t0, "expected_period_s": None, "stale_after_s": None, "uptime_s": None, "caused_by": [], "values": [], "measurements": []}

    def checks_json(self, now: float) -> dict:
        return {"clock": clock_json(now), "checks": {p: [self.check(p, now)] for p in self.paths}}

    def tree_bar_json(self, now: float, bins: int, window: float) -> dict:
        start = now - window
        bin_s = window / bins
        def level(i: int, warned: bool) -> str | None:
            lo, hi = start + i * bin_s, start + (i + 1) * bin_s
            if hi <= self.t0:
                return None
            # the worst level held in the bin, as the host's bar: a bin that overlaps the warn span is "warn"
            return "warn" if warned and lo < self.t0 + self.past_warn else "ok"
        rows = [{"path": "host", "name": "bench stub (synthetic)", "depth": 0, "basis": "below", "statuses": len(self.paths),
                 "bar": [level(i, True) for i in range(bins)]}]
        rows += [{"path": p, "name": f"Stub {i}", "depth": 1, "basis": "own", "statuses": 1, "bar": [level(i, i_ == 0) for i in range(bins)]}
                 for i_, p in enumerate(self.paths)]
        return {"path": "host", "window_s": window, "from": start, "to": now, "bins": bins, "bin_s": bin_s, "rows": rows}


def clock_json(now: float) -> dict:
    return {"now": now, "iso": "", "time": "", "mode": "stub", "utc_offset_s": 0, "rank": ["ok", "warn", "unknown", "stale", "error"]}


def node_row(path: str, parent: str | None, name: str, now: float, kind: str = "device") -> dict:
    return {"path": path, "name": name, "kind": kind, "icon": "device", "parent": parent, "edge": "via" if parent else None,
            "depth": 0 if parent is None else 1, "level": "ok", "message": "synthetic (bench stub)", "own": {"level": "ok", "message": ""},
            "rollup": {"level": "ok", "origin": None, "origin_name": None, "message": "", "counts": {}, "knock_on": []}, "hint": None,
            "knock_on_of": None, "stale": False, "connected": True, "hardware_id": "", "identity": {"model": "stub"}, "owner": "bench-stub",
            "id_text": "", "last_received": now, "age_s": 0.0, "expected_period_s": None, "stale_after_s": None, "checks": 0 if parent is None else 1, "actions": 0,
            "faults": 0, "collapsed": False, "presence": {"state": "live", "age_s": 0.0, "restored": False, "text": ""}, "claimed": True,
            "usb": None, "rate": None, "children": []}


def make_state(stream: Stream, now: float) -> dict:
    host = node_row("host", None, "bench stub (synthetic)", now, kind="host")
    host["children"] = [node_row(p, "host", f"Stub {i}", now) for i, p in enumerate(stream.paths)]
    return {"api": "io-diagnostics/1", "schema": "diagnostics-record/0", "record_version": "0.2",
            "clock": clock_json(now),
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
    """One thread makes the points and sends them: a watched entity's as they come, one event per --coalesce at most (the host's
    samples lane since 5cc429f), every other entity's with the --tick (its change tick). Each client gets its own queue of frames
    (a slow client is cut loose, never waited for)."""

    STEP_S = 0.005   # the longest the thread sleeps: points are stamped at their own time whatever it wakes at

    def __init__(self, stream: Stream):
        self.stream = stream
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()
        self.counter = 0
        self.sent = {p: 0 for p in stream.paths}       # the feed's cursor per entity (a point is sent once)
        self.last_sent = {p: 0.0 for p in stream.paths}
        threading.Thread(target=self.run, daemon=True).start()

    def frame(self, name: str, data: dict) -> bytes:
        self.counter += 1
        data = {**data, "emitted": time.time() * 1000}   # 5cdb4b9: the wall clock when the event is put on the feed
        return f"id: {self.counter}\nevent: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()

    def run(self) -> None:
        st = self.stream
        next_tick = time.time() + st.tick
        while True:
            now = time.time()
            st.generate(now)
            tick = now >= next_tick
            if tick:
                next_tick += st.tick * max(1, math.ceil((now - next_tick) / st.tick + 1e-9))
            due = []
            for path in st.paths:
                fresh = st.serial[path] > self.sent[path]
                watched = st.coalesce > 0 and st.is_watched(path, now)
                if fresh and (tick or (watched and now - self.last_sent[path] >= st.coalesce)):
                    due.append(path)
            if due:
                self.send(due, now)
            # wake for the soonest of: a point, the end of a watched entity's coalescing period (one with points to send), the tick
            wake = min(next_tick, st.soonest(), now + self.STEP_S)
            for path in st.paths:
                if st.coalesce > 0 and st.is_watched(path, now) and st.serial[path] > self.sent[path]:
                    wake = min(wake, self.last_sent[path] + st.coalesce)
            time.sleep(max(0.0, wake - time.time()))

    def send(self, paths: list[str], now: float) -> None:
        with self.lock:
            frames = []
            for path in paths:
                answer = self.stream.answer(path, now, self.sent[path])
                self.sent[path] = answer["cursor"]
                self.last_sent[path] = now
                if self.clients:
                    frames.append(self.frame("samples", answer))
            if not frames:
                return
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
                return self.json({"node": node_row(path, "host", path, now), "ancestors": [], "checks": [stream.check(path, now)] if path in stream.paths else [],
                                  "actions": [], "faults": [], "events": [], "config": [], "watch": stream.watch_json(path, now),
                                  "streams": [], "tabs": [], "heard": {"received": now, "age_s": 0.0}})
            if p == "/api/v1/values":
                try:
                    since = int(q.get("since", "0") or 0)
                except ValueError:
                    return self.json({"error": "since is a whole number"}, 400)
                return self.json(stream.answer(q.get("path", ""), now, since))
            if p == "/api/v1/checks":
                return self.json(stream.checks_json(now))
            if p == "/api/v1/history/tree":
                try:
                    bins = max(1, min(1000, int(q.get("bins", "60"))))
                except ValueError:
                    return self.json({"error": "bins is a whole number"}, 400)
                return self.json(stream.tree_bar_json(now, bins, 900.0))
            if p == "/api/v1/history":
                return self.json({"path": q.get("path", ""), "window_s": 900, "from": now - 900, "to": now, "statuses": []})
            if p == "/api/v1/dashboards":
                return self.json({"files": [{"name": "bench", "file": "bench.base", "size": len(base_text), "mtime": stream.t0, "etag": "bench"}]})
            if p == "/api/v1/dashboards/bench.base":
                return self.send(200, base_text.encode(), "text/plain; charset=utf-8", {"ETag": '"bench"'})
            if p.startswith("/api/"):
                return self.json({"error": f"no endpoint {p!r} in the bench stub"}, 404)
            name = p.lstrip("/") + "index.html" if p.endswith("/") else p.lstrip("/")   # a path ending in / is its index.html (4e2ec56)
            target = (WEB / name).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self.send(404, b"not found", "text/plain")
            return self.send(200, target.read_bytes(), TYPES.get(target.suffix, mimetypes.guess_type(target.name)[0] or "application/octet-stream"))

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            if urlparse(self.path).path == "/api/v1/watch":
                try:
                    path = json.loads(body or b"{}").get("path", "")
                except ValueError:
                    path = ""
                if path not in stream.paths:
                    return self.json({"error": f"no entity {path!r} with a watch in the bench stub"}, 404)
                stream.watched[path] = time.time() + 10.0
                return self.json({"watch": stream.watch_json(path, time.time())})
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
    ap.add_argument("--tick", type=float, default=1.0, help="seconds between change ticks (every unwatched entity's points)")
    ap.add_argument("--coalesce", type=float, default=0.1, help="seconds between a watched entity's samples events (0: they wait for the tick)")
    ap.add_argument("--past-warn", type=float, default=0.0, help="seconds stub/0 was warn after the start, in /history/tree")
    ap.add_argument("--window", default="10s", help="each plot's at: window")
    args = ap.parse_args(argv)
    stream = Stream(args.entities, args.keys, args.rate, args.tick, args.coalesce, args.past_warn)
    feed = Feed(stream)
    server = ThreadingHTTPServer((args.bind, args.port), handler(stream, feed, bench_base(stream, args.window)))
    server.daemon_threads = True
    feed_text = f"watched every {args.coalesce:g} s" if args.coalesce > 0 else "watched with the tick"
    print(f"bench stub on http://{args.bind}:{args.port}/ · {args.rate:g} samples/s over {args.entities} entities x {args.keys} keys · {feed_text}, tick {args.tick:g} s", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
