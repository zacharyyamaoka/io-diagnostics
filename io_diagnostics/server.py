"""The local web window's server: stdlib ``http.server``, the page in ``web/`` and the JSON API of docs/api-v1.md.

GET everywhere, plus ONE write: ``POST /api/v1/act``, the page's Utilities panel (docs/api-v1.md "Actions from the
page"). Every click is a dry run first; a real run needs that dry run's token and must come from the page itself
(same origin, the page's own header). Agents never call it: they use ``io-diagnostics act --dry-run``. The Expected
setup card's ``POST /api/v1/expect | forget | retire`` rewrite the person's rig file under the same page-only rule
(no device is touched, so no dry run and no token), and ``POST /api/v1/settings`` (the View options: the history
window, written to settings.toml) likewise, and ``POST /api/v1/watch`` (the entity page's heartbeat: it keeps the plugin's
``watch`` running while the page looks at an entity, docs/state-config-v1.md). Every other POST / PUT / DELETE / PATCH is 405. Binds to 127.0.0.1 by
default; the page polls ``/api/v1/state`` once a second, and may listen to ``GET /api/v1/events`` (Server-Sent
Events: each new or changed activity event, and a tick per change) so a plug shows without waiting for the poll. Run by the release-channels runtime as Preview or an agent's
lane, it also answers ``/__sps/*`` for the Stable / Preview chip (release_channels.py, docs/release-channels.md).
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from io_diagnostics import WEB
from io_diagnostics import release_channels
from io_diagnostics.arrangements import ArrangementError
from io_diagnostics.history import MAX_BINS
from io_diagnostics.host import Host
from io_diagnostics.settings import SettingsError
from io_diagnostics.terminal import KEEPALIVE_S, RunError

DEFAULT_PORT = 8812
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json", ".ico": "image/x-icon"}
NO_ACTIONS = ("actions never execute through this endpoint: the page runs one only through POST /api/v1/act, a dry run "
              "first, then a person's confirmed click (docs/api-v1.md)")
# WHY 300 s: a moteus confirm token names one 5-minute window and is honoured for the next one too, so a run within
# 300 s of its dry run always carries a token the plugin still accepts; a page left open longer dry-runs again.
TOKEN_WINDOW_S = 300.0
CONFIG_WRITES = {"/api/v1/config/edit": "edit", "/api/v1/config/set": "set", "/api/v1/config/persist": "persist",
                 "/api/v1/config/reset": "reset"}
SETUP_EDITS = {"/api/v1/expect": "expect", "/api/v1/forget": "forget", "/api/v1/retire": "retire"}
UI_HEADER = "X-IO-Diagnostics"
UI_HEADER_VALUE = "ui"
MAX_BODY = 64 * 1024
FRAME_TYPES = {"jpeg": "image/jpeg", "png": "image/png"}
MJPEG_BOUNDARY = "ioframe"
MJPEG_POLL_S = 0.04  # how often an MJPEG stream looks for a newer frame
MJPEG_MAX_FPS = 30.0
RUN_PATH = re.compile(r"^/api/v1/runs/([0-9A-Za-z_-]+)(?:/(stream|cast|input|signal|resize))?$")
RUN_WRITES = ("input", "signal", "resize")
MAX_TERMINAL = 500  # columns or rows a resize may ask for
EVENTS_KEEPALIVE_S = 15.0  # an SSE comment this often while nothing changes, so a dead client is noticed
EVENTS_COALESCE_S = 0.1  # a burst of changes (a plug's ~30 udev blocks) is one diff
EVENTS_WINDOW = 200  # the newest events each diff compares


def _types(query: dict) -> tuple[str, ...] | None:
    raw = ",".join(query.get("types") or [])
    return tuple(word.strip() for word in raw.split(",") if word.strip()) or None


def _log_query(query: dict) -> dict:
    """The Activity log model's parameters (docs/api-v1.md "Activity"): ``fold=1`` turns it on, the rest narrow it."""

    def one(name: str, default: str = "") -> str:
        return (query.get(name) or [default])[0]

    return {"fold": one("fold") in ("1", "true"), "channel": one("channel", "activity"), "path": one("path"),
            "scope": one("scope", "children"), "severity": one("severity", "debug")}


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


def _signature(event: dict) -> tuple:
    # what a page draws from a row: a repeat (count), a fault folded onto it (badges, members) and a run's state change it
    return (event["code"], event["provisional"], event["message"], event["stamp"], event["severity"], event["path"], event["count"],
            event["last_stamp"], tuple((badge["id"], badge["state"]) for badge in event["badges"]), len(event["members"]),
            event["args"].get("state"))


def _int_or_null(value) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


class Handler(BaseHTTPRequestHandler):
    host: Host  # set on a subclass by make_server
    gate: ActGate  # likewise
    server_version = "io-diagnostics/1"

    def do_GET(self):  # noqa: N802 - http.server's name
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if release_channels.is_sps_path(parsed.path):
            return self._sps("GET")
        try:
            if parsed.path == "/api/v1/state":
                return self._json(self.host.state())
            if parsed.path == "/api/v1/node":
                path = (query.get("path") or [""])[0]
                found = self.host.node(path)
                return self._json(found) if found else self._json({"error": f"no entity {path!r}"}, 404)
            if parsed.path == "/api/v1/activity":
                limit = int((query.get("limit") or ["200"])[0])
                try:
                    return self._json(self.host.activity(max(1, min(limit, 5000)), _types(query), **_log_query(query)))
                except ValueError as error:
                    return self._json({"error": str(error)}, 400)
            if parsed.path == "/api/v1/events":
                return self._events(query)
            if parsed.path == "/api/v1/records":
                return self._json(self.host.records())
            if parsed.path == "/api/v1/plugins":
                return self._json(self.host.plugins())
            if parsed.path == "/api/v1/health":
                return self._json(self.host.health())
            if parsed.path == "/api/v1/runs":
                return self._json(self.host.runs_json())
            if parsed.path == "/api/v1/values":
                return self._values(query)
            if parsed.path == "/api/v1/arrangements":
                return self._json(self.host.arrangements.json())
            if parsed.path in ("/api/v1/frames", "/api/v1/frames/stream"):
                return self._frames(parsed.path, query)
            found = RUN_PATH.match(parsed.path)
            if found and found[2] not in RUN_WRITES:
                return self._run_get(found[1], found[2], query)
            if parsed.path in ("/api/v1/history", "/api/v1/history/bar"):
                return self._history(parsed.path, query)
            if parsed.path == "/api/v1/settings":
                return self._json(self.host.settings_json())
            if parsed.path in ("/api/v1/act", "/api/v1/watch") or parsed.path in SETUP_EDITS or parsed.path in CONFIG_WRITES:
                return self._json({"error": "POST only, and only from the page (docs/api-v1.md)"}, 405, extra={"Allow": "POST"})
            if parsed.path.startswith("/api/"):
                return self._json({"error": f"no endpoint {parsed.path!r} (see docs/api-v1.md)"}, 404)
        except Exception as error:  # noqa: BLE001 - show the failure, keep serving
            return self._json({"error": f"{type(error).__name__}: {error}"}, 500)
        name = "index.html" if parsed.path in ("/", "") else parsed.path.lstrip("/")
        path = (WEB / name).resolve()
        if WEB.resolve() not in path.parents or not path.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        body = path.read_bytes()
        if path.suffix == ".html" and release_channels.runtime():
            body = release_channels.tag_page(body)  # Preview / an agent's lane: the channel chip (release_channels.py)
        return self._send(200, body, TYPES.get(path.suffix, "application/octet-stream"))

    def do_POST(self):  # noqa: N802 - http.server's name
        endpoint = urlparse(self.path).path
        if release_channels.is_sps_path(endpoint):
            return self._sps("POST")
        found = RUN_PATH.match(endpoint)
        if found and found[2] in RUN_WRITES:
            try:
                return self._run_write(found[1], found[2])
            except Exception as error:  # noqa: BLE001 - show the failure, keep serving
                return self._json({"error": f"{type(error).__name__}: {error}"}, 500)
        if endpoint not in ("/api/v1/act", "/api/v1/settings", "/api/v1/watch", "/api/v1/arrangements", *CONFIG_WRITES, *SETUP_EDITS):
            return self._refuse()
        try:
            if endpoint == "/api/v1/settings":
                return self._settings()
            if endpoint == "/api/v1/watch":
                return self._watch()
            if endpoint == "/api/v1/arrangements":
                return self._arrangements()
            if endpoint in CONFIG_WRITES:
                return self._config(CONFIG_WRITES[endpoint])
            return self._act() if endpoint == "/api/v1/act" else self._setup_edit(SETUP_EDITS[endpoint])
        except Exception as error:  # noqa: BLE001 - show the failure, keep serving
            return self._json({"error": f"{type(error).__name__}: {error}"}, 500)

    # --- GET /api/v1/events: Server-Sent Events (docs/api-v1.md "Events") -------------------------------------------
    def _events(self, query: dict):
        """Stream ``activity`` (an event, new or changed: a provisional plug, then the same id confirmed) and ``tick``
        (something a page draws changed) until the client leaves or the host stops. Read-only; no CORS header, so only
        the page's own origin can read it (EventSource is same-origin unless the server allows otherwise)."""

        host, types, log = self.host, _types(query), _log_query(query)
        try:
            host.activity(1, types, **log)
        except ValueError as error:
            return self._json({"error": str(error)}, 400)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        revision = host.revision
        snapshot = host.activity(EVENTS_WINDOW, types, **log)
        sent = {event["id"]: _signature(event) for event in snapshot["events"]}
        try:
            self._sse("hello", {"api": host.health()["api"], "revision": revision, "now": snapshot["clock"]["now"],
                              "latest": snapshot["events"][0]["id"] if snapshot["events"] else None}, retry_ms=2000)
            while True:
                with host.changes:
                    host.changes.wait_for(lambda: host.revision != revision or host._stop.is_set(), timeout=EVENTS_KEEPALIVE_S)
                if host._stop.is_set():
                    return
                if host.revision == revision:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                time.sleep(EVENTS_COALESCE_S)
                revision, reason = host.revision, host.change_reason
                current = host.activity(EVENTS_WINDOW, types, **log)
                for event in reversed(current["events"]):  # oldest first, as they happened
                    if sent.get(event["id"]) != _signature(event):
                        sent[event["id"]] = _signature(event)
                        self._sse("activity", event, event_id=event["id"])
                keep = {event["id"] for event in current["events"]}
                sent = {key: value for key, value in sent.items() if key in keep}
                self._sse("tick", {"revision": revision, "reason": reason, "now": current["clock"]["now"]})
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # the client left

    def _sse(self, name: str, data: dict, *, event_id: str | None = None, retry_ms: int | None = None) -> None:
        lines = [f"retry: {retry_ms}"] if retry_ms else []
        if event_id is not None:
            lines.append(f"id: {event_id}")
        lines += [f"event: {name}", "data: " + json.dumps(data, default=str, ensure_ascii=False)]
        self.wfile.write(("\n".join(lines) + "\n\n").encode())
        self.wfile.flush()

    # --- runs (docs/runs-v1.md): reads, the output stream, and the three page-only writes ---------------------------
    def _run_get(self, run_id: str, part: str | None, query: dict):
        run = self.host.runs.get(run_id)
        if run is None:
            return self._json({"error": f"no run {run_id!r}"}, 404)
        if part is None:
            return self._json(run.to_json(detail=True))
        if part == "cast":
            path = run.cast_path
            if path is None or not path.is_file():
                return self._json({"error": "this run has no recording (the host keeps none)"}, 404)
            return self._send(200, path.read_bytes(), "application/x-asciicast; charset=utf-8")
        return self._run_stream(run, query)

    def _run_stream(self, run, query: dict):
        """Replay the ring from ``from`` (default: the oldest byte held), follow live, send ``end`` and close."""

        raw = (query.get("from") or [""])[0]
        if raw and not raw.isdigit():
            return self._json({"error": "from is a byte offset (a whole number)"}, 400)
        with run.cond:
            run._load_buffer()
            available = run.first_offset
        offset = max(int(raw), available) if raw else available
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self._sse("hello", {"run": run.to_json(), "from": offset, "available_from": available,
                              "dropped_bytes": run.dropped})
            while True:
                chunks, ended = run.read_from(offset, KEEPALIVE_S)
                for start, seconds, data in chunks:
                    offset = start + len(data)
                    self._sse("output", {"offset": start, "t": round(seconds, 4), "b64": base64.b64encode(data).decode()},
                              event_id=str(offset))
                if ended:
                    return self._sse("end", {"run": run.to_json(detail=True)})
                if not chunks:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # the client left

    def _run_write(self, run_id: str, op: str):
        """input / signal / resize: the page's own request (the same check as /act), then the contract's refusals in order."""

        body, answer = self._body()
        if body is None:
            return self._json(*answer)
        run = self.host.runs.get(run_id)
        if run is None:
            return self._json({"refused": "NoSuchRun", "error": f"no run {run_id!r}"}, 404)
        try:
            run.check_writable()
            if op == "input":
                if not isinstance(body.get("text"), str):
                    raise RunError("BadRequest", "text is a string", 400)
                return self._json({"ok": True, "written": run.send_input(body["text"])})
            if op == "signal":
                if body.get("signal") != "interrupt":
                    raise RunError("BadRequest", "signal is \"interrupt\" (the only signal a page may send)", 400)
                return self._json({"ok": True, "stage": run.interrupt()})
            cols, rows = body.get("cols"), body.get("rows")
            if not all(isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_TERMINAL for value in (cols, rows)):
                raise RunError("BadRequest", f"cols and rows are whole numbers 1..{MAX_TERMINAL}", 400)
            run.resize(cols, rows)
            return self._json({"ok": True, "cols": cols, "rows": rows})
        except RunError as error:
            return self._json({"refused": error.code, "error": str(error)}, error.status)

    # --- the Stable / Preview chip, under the release-channels runtime only (release_channels.py) -------------------
    def _sps(self, method: str):
        script = release_channels.runtime()
        if script is None:
            return self._json({"error": "not running under stable-preview-switcher"}, 404)
        if urlparse(self.path).path == release_channels.CHIP_PATH and method == "GET":
            try:
                bundle = release_channels.chip_bundle(script).read_bytes()
            except OSError:
                return self._json({"error": "the runtime has no chip bundle"}, 404)
            return self._send(200, bundle, "text/javascript")
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > release_channels.MAX_BODY:
            return self._json({"error": f"a body of at most {release_channels.MAX_BODY} bytes"}, 413)
        body = self.rfile.read(length) if length else b""
        status, payload = release_channels.api(script, method, self.path, self.headers, body)
        return self._json(payload, status)

    # --- the entity page: live values, images, the watch heartbeat (docs/state-config-v1.md) --------------------------
    def _values(self, query: dict):
        path = (query.get("path") or [""])[0]
        since = (query.get("since") or ["0"])[0]
        if not since.isdigit():
            return self._json({"error": f"since is a whole number (the cursor of the last answer), got {since!r}"}, 400)
        found = self.host.values_json(path, int(since))
        return self._json(found) if found else self._json({"error": f"no entity {path!r} a plugin describes"}, 404)

    def _frames(self, endpoint: str, query: dict):
        path, stream = (query.get("path") or [""])[0], (query.get("stream") or [""])[0]
        if endpoint == "/api/v1/frames/stream":
            return self._mjpeg(path, stream, (query.get("fps") or [""])[0])
        found = self.host.frame_file(path, stream)
        if found is None:
            return self._json({"error": f"no frame yet for stream {stream!r} of {path!r}"}, 404)
        data, meta = found
        return self._send(200, data, FRAME_TYPES.get(meta["format"], "application/octet-stream"),
                          {"X-Frame-Seq": str(meta["seq"]), "X-Frame-Stamp": str(meta["stamp"]),
                           "X-Frame-Width": str(meta["width"]), "X-Frame-Height": str(meta["height"])})

    def _mjpeg(self, path: str, stream: str, fps: str):
        """``multipart/x-mixed-replace``: each NEW frame once, at most ``fps`` a second, until the client leaves (an <img>
        whose source is this URL shows it live). WHY polled: a frame is a file the plugin replaced; nothing signals it."""

        try:
            gap = 1.0 / max(0.5, min(float(fps), MJPEG_MAX_FPS)) if fps else 1.0 / 15.0
        except ValueError:
            return self._json({"error": f"fps is a number, got {fps!r}"}, 400)
        if self.host.frame_seq(path, stream) is None and not self.host.values_json(path):
            return self._json({"error": f"no stream {stream!r} of {path!r}"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last, sent = None, 0.0
        try:
            while not self.host._stop.is_set():
                seq = self.host.frame_seq(path, stream)
                if seq is not None and seq != last and time.monotonic() - sent >= gap:
                    found = self.host.frame_file(path, stream)
                    if found is not None:
                        data, meta = found
                        self.wfile.write(f"--{MJPEG_BOUNDARY}\r\nContent-Type: {FRAME_TYPES.get(meta['format'], 'image/jpeg')}\r\n"
                                         f"Content-Length: {len(data)}\r\nX-Frame-Seq: {meta['seq']}\r\n\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()
                        last, sent = seq, time.monotonic()
                time.sleep(MJPEG_POLL_S)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # the client left

    def _watch(self):
        # WHY the same page-only rule as /act: a heartbeat makes the host run a plugin's query-only verb against hardware
        body, answer = self._body()
        if body is None:
            return self._json(*answer)
        path = body.get("path")
        if not isinstance(path, str) or not path:
            return self._json({"refused": "BadRequest", "error": "path is the entity's path (a string)"}, 400)
        return self._json(self.host.watch_heartbeat(path))

    def _arrangements(self):
        # WHY the same page-only rule as /settings: an arrangement is the person's preference, written to their file
        body, answer = self._body()
        if body is None:
            return self._json(*answer)
        try:
            return self._json(self.host.arrangements.set(body.get("kind"), body.get("section"), body.get("value")))
        except ArrangementError as error:
            return self._json({"refused": "BadArrangement", "error": str(error)}, 400)

    def _config(self, op: str):
        """The Configuration tab's edit session and writes (docs/state-config-v1.md section 9): page-only like /act, and each one
        needs the session token that the person's confirm opened. The answer is the editor's, whole."""

        body, answer = self._body()
        if body is None:
            return self._json(*answer)
        path, token = body.get("path"), body.get("token")
        if not isinstance(path, str) or not path or (op != "edit" and not isinstance(token, str)):
            return self._json({"refused": "BadRequest", "error": "path is the entity's path; token the edit session's"}, 400)
        editor = self.host.config_editor
        if op == "edit":
            action = body.get("op")
            if action == "open":
                result = editor.open(path, body.get("confirm") is True)
            elif action in ("beat", "close") and isinstance(token, str):
                result = editor.beat(path, token) if action == "beat" else editor.close(path, token)
            else:
                return self._json({"refused": "BadRequest", "error": "op is open, beat or close (the last two with the token)"}, 400)
        elif op == "set":
            key = body.get("key")
            if not isinstance(key, str) or "value" not in body:
                return self._json({"refused": "BadRequest", "error": "key is a string and value is the new value"}, 400)
            result = editor.set(path, token, key, body["value"], body.get("confirm_safety") is True)
        elif op == "persist":
            result = editor.persist(path, token)
        else:
            key = body.get("key")
            if key is not None and not isinstance(key, str):
                return self._json({"refused": "BadRequest", "error": "key is a string (or absent: every setting)"}, 400)
            result = editor.reset(path, token, str(body.get("to") or ""), key, body.get("confirm_safety") is True)
        status = 200 if result.get("ok") or result.get("state") in ("refused", "rejected") else 403 if result.get("refused") == "NoEditSession" else 400
        return self._json(result, status)

    # --- history and settings ------------------------------------------------------------------------------------
    def _history(self, endpoint: str, query: dict):
        path = (query.get("path") or [""])[0]
        window = (query.get("window") or [None])[0]
        try:
            if endpoint == "/api/v1/history":
                found = self.host.history_json(path, (query.get("status") or [None])[0], window)
            else:
                bins = (query.get("bins") or ["60"])[0]
                if not bins.isdigit():
                    return self._json({"error": f"bins is a whole number 1..{MAX_BINS}, got {bins!r}"}, 400)
                found = self.host.bar_json(path, int(bins), window)
        except SettingsError as error:
            return self._json({"error": str(error)}, 400)
        return self._json(found) if found else self._json({"error": f"no entity {path!r}"}, 404)

    def _settings(self):
        # WHY the same page-only rule as /act: a setting is the person's preference, written to their file
        body, answer = self._body()
        if body is None:
            return self._json(*answer)
        try:
            return self._json(self.host.set_settings(body))
        except SettingsError as error:
            return self._json({"refused": "BadSetting", "error": str(error)}, 400)

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
        # WHY the page origins too: on Stable the page is the runtime's Stable server's, which forwards /api/ here
        # with Host rewritten to this port (release_channels.page_origins, docs/release-channels.md)
        if origin and origin != f"http://{self.headers.get('Host', '')}" and origin not in release_channels.page_origins():
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
        # (a confirm modal for anything but a query); the plugin still checks its own token against the device.
        # The run is interactive: started from the page, so the page may type into it and stop it (docs/runs-v1.md)
        run, result = self.host.start_act(path, action, node=node, confirm=entry["plugin_token"], new_id=new_id,
                                          interactive=True)
        if run is None:
            return self._json({"dry_run": False, "result": result})
        if body.get("detach") is True:
            return self._json({"dry_run": False, "run": run.to_json()}, 202)
        run.wait()
        return self._json({"dry_run": False, "result": {**result, **(run.result or {}), "run_id": run.id}})

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
