"""The local web window's server: stdlib ``http.server``, the page in ``web/`` and the JSON API of docs/api-v1.md.

GET only. Every other method is 405: actions never execute in v1, so there is nothing to accept. Binds to
127.0.0.1 by default; the page polls ``/api/v1/state`` once a second.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from io_diagnostics import WEB
from io_diagnostics.host import Host

DEFAULT_PORT = 8812
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json", ".ico": "image/x-icon"}
NO_ACTIONS = "actions never execute in IO diagnostics v1: the host shows them and their gate state only"


class Handler(BaseHTTPRequestHandler):
    host: Host  # set on a subclass by make_server
    server_version = "io-diagnostics/1"

    def do_GET(self):  # noqa: N802 - http.server's name
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/v1/state":
                return self._json(self.host.state())
            if parsed.path == "/api/v1/node":
                path = (query.get("path") or [""])[0]
                found = self.host.node(path)
                return self._json(found) if found else self._json({"error": f"no entity {path!r}"}, 404)
            if parsed.path == "/api/v1/activity":
                limit = int((query.get("limit") or ["200"])[0])
                return self._json(self.host.activity(max(1, min(limit, 5000))))
            if parsed.path == "/api/v1/records":
                return self._json(self.host.records())
            if parsed.path == "/api/v1/plugins":
                return self._json(self.host.plugins())
            if parsed.path == "/api/v1/health":
                return self._json(self.host.health())
            if parsed.path.startswith("/api/"):
                return self._json({"error": f"no endpoint {parsed.path!r} (see docs/api-v1.md)"}, 404)
        except Exception as error:  # noqa: BLE001 - show the failure, keep serving
            return self._json({"error": f"{type(error).__name__}: {error}"}, 500)
        name = "index.html" if parsed.path in ("/", "") else parsed.path.lstrip("/")
        path = (WEB / name).resolve()
        if WEB.resolve() not in path.parents or not path.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        return self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

    def _refuse(self):
        self._json({"error": NO_ACTIONS}, 405, extra={"Allow": "GET"})

    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse  # noqa: N815

    def _json(self, payload: dict, code: int = 200, extra: dict | None = None):
        self._send(code, json.dumps(payload, default=str, ensure_ascii=False).encode(), "application/json; charset=utf-8", extra)

    def _send(self, code: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # quiet: the page polls every second
        pass


def make_server(host: Host, port: int = DEFAULT_PORT, bind: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"host": host})
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    host.url = f"http://{bind}:{server.server_address[1]}/"
    return server


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="io-http", daemon=True)
    thread.start()
    return thread
