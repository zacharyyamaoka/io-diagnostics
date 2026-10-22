"""V6 "converged": IO diagnostics as an entity tree + detail, table mode and activity log, in a local web window.

    uv run --project /home/bam/io-diagnostics python /home/bam/io-diagnostics/prototypes/v6-converged/dashboard.py --port 8812

Same single small Python process as V2 (stdlib only): it reads sysfs, /dev/serial/by-id, the kernel log
and the rack's own files, builds the tree (frames.py), and serves it to the page in web/. The page polls
once a second, so "last seen" ticks and a record turns STALE on its own. The top-bar chip is rendered in
the page header; its model is the one V2's real AppIndicator (contract/indicator.py) renders.
Buttons are placeholders: the server has no endpoint that could execute an action.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import activity  # noqa: E402
import frames  # noqa: E402
from tree import LEVELS, STALE_FACTOR, to_json  # noqa: E402

WEB = HERE / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
MAX_ELAPSED_S = 4 * 3600


def frame_and_now(query: dict) -> tuple[str, dt.datetime]:
    frame_id = query.get("frame", ["F1"])[0]
    frame_id = frame_id if frame_id in frames.FRAMES else "F1"
    elapsed = min(max(float(query.get("t", ["0"])[0] or 0), 0.0), MAX_ELAPSED_S)
    return frame_id, frames.FRAMES[frame_id].clock + dt.timedelta(seconds=elapsed)


def state(query: dict, url: str) -> dict:
    frame_id, now = frame_and_now(query)
    tree = to_json(frames.build(frame_id, now), now)
    recent = activity.events(now)
    return {"frame": frame_id, "clock": now.isoformat(timespec="seconds"), "levels": LEVELS, "stale_factor": STALE_FACTOR,
            "frames": [{"id": frame.id, "label": frame.label, "story": frame.story, "clock": frame.clock.isoformat()}
                       for frame in frames.FRAMES.values()],
            "tree": tree, "chip": activity.chip(tree, recent, url), "recent": recent[:6]}


def activity_state(query: dict) -> dict:
    frame_id, now = frame_and_now(query)
    root = frames.build(frame_id, now)
    return {"frame": frame_id, "clock": now.isoformat(timespec="seconds"), "events": activity.events(now),
            "strips": activity.strips(frame_id, now), "link_health": activity.link_health(root, now)}


class Handler(BaseHTTPRequestHandler):
    url = ""

    def do_GET(self):  # noqa: N802 - http.server's name
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/state":
                return self._json(state(query, self.url))
            if parsed.path == "/api/activity":
                return self._json(activity_state(query))
        except Exception as error:  # noqa: BLE001 - show the failure in the page, keep serving
            return self._json({"error": f"{type(error).__name__}: {error}"}, 500)
        name = "index.html" if parsed.path in ("/", "") else parsed.path.lstrip("/")
        path = (WEB / name).resolve()
        if path.parent != WEB.resolve() or not path.is_file():
            return self._send(404, b"not found", "text/plain")
        return self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

    def _json(self, payload: dict, code: int = 200):
        self._send(code, json.dumps(payload, default=str).encode(), "application/json")

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # quiet: the page polls every second
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8812)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    Handler.url = f"http://{args.host}:{args.port}/"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"IO diagnostics V6 on {Handler.url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
