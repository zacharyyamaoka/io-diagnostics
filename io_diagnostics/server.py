"""The local web window's server: stdlib ``http.server``, the page in ``web/`` and the JSON API of docs/api-v1.md.

GET everywhere, plus ONE write: ``POST /api/v1/act``, the page's Utilities panel (docs/api-v1.md "Actions from the
page"). Every click is a dry run first; a real run needs that dry run's token and must come from the page itself
(same origin, the page's own header). Agents never call it: they use ``io-diagnostics act --dry-run``. The Expected
setup card's ``POST /api/v1/expect | forget | retire`` rewrite the person's rig file under the same page-only rule
(no device is touched, so no dry run and no token). Every other POST / PUT / DELETE / PATCH is 405. Binds to 127.0.0.1 by default; the page polls ``/api/v1/state`` once a second.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from io_diagnostics import WEB
from io_diagnostics.host import Host

DEFAULT_PORT = 8812
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json", ".ico": "image/x-icon"}
NO_ACTIONS = ("actions never execute through this endpoint: the page runs one only through POST /api/v1/act, a dry run "
              "first, then a person's confirmed click (docs/api-v1.md)")
# WHY 300 s: a moteus confirm token names one 5-minute window and is honoured for the next one too, so a run within
# 300 s of its dry run always carries a token the plugin still accepts; a page left open longer dry-runs again.
TOKEN_WINDOW_S = 300.0
SETUP_EDITS = {"/api/v1/expect": "expect", "/api/v1/forget": "forget", "/api/v1/retire": "retire"}
UI_HEADER = "X-IO-Diagnostics"
UI_HEADER_VALUE = "ui"
MAX_BODY = 64 * 1024


class ActGate:
    """The page's dry-run tokens: one per dry run, for that path / action / node / new id, single use, valid
    ``window_s``.

    WHY a token of the server's own, besides the plugin's: the plugin's token proves what the person read (moteus:
    action, adapter, board, time window); this one proves the click follows a dry run the page showed, so a request
    that never dry-ran (an agent, a replayed command line, another tab's stale dialog) cannot act."""

    def __init__(self, window_s: float = TOKEN_WINDOW_S, now=time.monotonic):
        self.window_s = window_s
        self.now = now
        self.lock = threading.Lock()
        self.tokens: dict[str, dict] = {}

    def issue(self, path: str, action: str, node: int | None, plugin_token: str | None, new_id: int | None = None) -> str:
        token = secrets.token_urlsafe(18)
        with self.lock:
            now = self.now()
            self.tokens = {key: value for key, value in self.tokens.items() if now - value["issued"] <= self.window_s}
            self.tokens[token] = {"path": path, "action": action, "node": node, "new_id": new_id, "plugin_token": plugin_token,
                                  "issued": now}
        return token

    def redeem(self, token: str, path: str, action: str, node: int | None,
               new_id: int | None = None) -> tuple[dict | None, str, str]:
        """``(entry, "", "")`` once per token, else ``(None, code, reason)``; a wrong or expired token is spent."""

        with self.lock:
            entry = self.tokens.pop(token, None) if token else None
            if entry is None:
                return None, "NoDryRun", "no dry run issued this token (or it was used already): click the utility again to dry-run it"
            age = self.now() - entry["issued"]
            if age > self.window_s:
                return None, "TokenExpired", f"the dry run is {age:.0f} s old (a token lasts {self.window_s:.0f} s): click the utility again"
            # WHY the new id too: a token issued for "CAN 15 -> CAN 7" must never run "CAN 15 -> CAN 9"
            if (entry["path"], entry["action"], entry["node"], entry["new_id"]) != (path, action, node, new_id):
                board = "" if entry["node"] is None else f" node {entry['node']}"
                new = "" if entry["new_id"] is None else f" (new id {entry['new_id']})"
                return None, "TokenMismatch", f"the token is for {entry['action']} on {entry['path']}{board}{new}, not this action"
            return entry, "", ""


def _int_or_null(value) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


class Handler(BaseHTTPRequestHandler):
    host: Host  # set on a subclass by make_server
    gate: ActGate  # likewise
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
            if parsed.path == "/api/v1/act" or parsed.path in SETUP_EDITS:
                return self._json({"error": "POST only, and only from the page (docs/api-v1.md)"}, 405, extra={"Allow": "POST"})
            if parsed.path.startswith("/api/"):
                return self._json({"error": f"no endpoint {parsed.path!r} (see docs/api-v1.md)"}, 404)
        except Exception as error:  # noqa: BLE001 - show the failure, keep serving
            return self._json({"error": f"{type(error).__name__}: {error}"}, 500)
        name = "index.html" if parsed.path in ("/", "") else parsed.path.lstrip("/")
        path = (WEB / name).resolve()
        if WEB.resolve() not in path.parents or not path.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        return self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

    def do_POST(self):  # noqa: N802 - http.server's name
        endpoint = urlparse(self.path).path
        if endpoint not in ("/api/v1/act", *SETUP_EDITS):
            return self._refuse()
        try:
            return self._act() if endpoint == "/api/v1/act" else self._setup_edit(SETUP_EDITS[endpoint])
        except Exception as error:  # noqa: BLE001 - show the failure, keep serving
            return self._json({"error": f"{type(error).__name__}: {error}"}, 500)

    # --- the Expected setup card: host-side writes of the person's rig file, never a device --------------------------
    def _body(self) -> tuple[dict | None, tuple]:
        """The page's JSON body, or ``(None, (payload, code))`` to answer with."""

        refused, reason = self._from_page()
        if refused:
            return None, ({"refused": refused, "error": reason}, 403)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return None, ({"refused": "BadRequest", "error": f"a JSON body of 1..{MAX_BODY} bytes is required"}, 400)
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            return None, ({"refused": "BadRequest", "error": f"not JSON: {error.msg}"}, 400)
        if not isinstance(body, dict):
            return None, ({"refused": "BadRequest", "error": "the body is a JSON object"}, 400)
        return body, ()

    def _setup_edit(self, op: str):
        # WHY the same page-only rule as /act and no token: the write is the person's own file, never a device, so a
        # plain confirm on the page is enough (Zach, 2026-10-23); a script still cannot reach it from another origin
        body, answer = self._body()
        if body is None:
            return self._json(*answer)
        path, rig, reason = body.get("path"), body.get("rig"), body.get("reason", "")
        if not isinstance(path, str) or not (rig is None or isinstance(rig, str)) or not isinstance(reason, str):
            return self._json({"refused": "BadRequest", "error": "path is a string; rig a string or null; reason a string"}, 400)
        if self.host.node(path) is None:
            return self._json({"refused": "NoSuchEntity", "error": f"no entity {path!r}"}, 404)
        result = self.host.setup_edit(op, path, rig=rig, reason=reason)
        return self._json(result, 200 if result["ok"] else 400)

    # --- the one write: a utility from the page ------------------------------------------------------------------
    def _from_page(self) -> tuple[str, str]:
        """``("", "")`` when the request is the page's own fetch, else ``(code, reason)``.

        WHY these three: a browser sets ``Sec-Fetch-Site`` itself and no page can forge it; another origin's page
        cannot add a custom header without a CORS preflight this server never answers; and a JSON body forces that
        preflight too. A command-line client can forge all of it, which is why agents are told never to call it."""

        if self.headers.get(UI_HEADER, "") != UI_HEADER_VALUE:
            return "NotFromPage", f"only the IO diagnostics page runs actions (the {UI_HEADER}: {UI_HEADER_VALUE} header is missing)"
        if self.headers.get("Sec-Fetch-Site", "") != "same-origin":
            return "NotSameOrigin", (f"not a same-origin request from the page (Sec-Fetch-Site: "
                                     f"{self.headers.get('Sec-Fetch-Site') or 'absent'})")
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host', '')}":
            return "NotSameOrigin", f"the request comes from {origin}, not from this page"
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return "NotJson", "the body must be JSON (Content-Type: application/json)"
        return "", ""

    def _act(self):
        refused, reason = self._from_page()
        if refused:
            return self._json({"refused": refused, "error": reason}, 403)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._json({"refused": "BadRequest", "error": f"a JSON body of 1..{MAX_BODY} bytes is required"}, 400)
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            return self._json({"refused": "BadRequest", "error": f"not JSON: {error.msg}"}, 400)
        path, action, node, new_id = body.get("path"), body.get("action"), body.get("node"), body.get("new_id")
        if not isinstance(path, str) or not isinstance(action, str) or not _int_or_null(node) or not _int_or_null(new_id):
            return self._json({"refused": "BadRequest", "error": "path and action are strings; node and new_id are integers or null"}, 400)
        if body.get("dry_run") is True:
            result = self.host.act(path, action, node=node, dry_run=True, human=False, new_id=new_id)
            token = None
            if result.get("refused") is None and result.get("outcome") == "ok":
                answer = result.get("action_result") or {}
                plugin_token = answer.get("token") or answer.get("confirm_token") or None
                token = self.gate.issue(path, action, node, plugin_token, new_id)
            return self._json({"dry_run": True, "result": result, "token": token, "window_s": self.gate.window_s})
        entry, refused, reason = self.gate.redeem(str(body.get("token") or ""), path, action, node, new_id)
        if entry is None:
            return self._json({"refused": refused, "error": reason}, 403)
        # WHY human=True here and nowhere else in the server: the person read the dry run on the page and clicked
        # (a confirm modal for anything but a query); the plugin still checks its own token against the device
        result = self.host.act(path, action, node=node, confirm=entry["plugin_token"], dry_run=False, human=True,
                               new_id=new_id)
        return self._json({"dry_run": False, "result": result})

    def _refuse(self):
        self._json({"error": NO_ACTIONS}, 405, extra={"Allow": "GET"})

    do_PUT = do_DELETE = do_PATCH = _refuse  # noqa: N815

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


def make_server(host: Host, port: int = DEFAULT_PORT, bind: str = "127.0.0.1", gate: ActGate | None = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"host": host, "gate": gate or ActGate()})
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    host.url = f"http://{bind}:{server.server_address[1]}/"
    return server


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="io-http", daemon=True)
    thread.start()
    return thread
