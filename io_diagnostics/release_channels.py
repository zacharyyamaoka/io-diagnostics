"""RobotDiagnostics' Stable / Preview channels: the three duties of a non-Vite Preview server (stable_preview_switcher
docs/CONTRACT.md §10.7), done by this server only while the runtime runs it as Preview or as an agent's ``sps dev`` lane.

- ``/__sps/*`` goes to ``python3 $SPS_RUNTIME api METHOD PATH --channel C --checkout DIR`` (body on stdin), the
  reply is the ``{"status", "body"}`` it prints: every fact on the chip is the runtime's, measured on each call.
- ``/__sps/chip.js`` is the runtime's own chip bundle (``<runtime>/stable_preview_switcher/assets/chip.js``).
- every page this server sends gets the ONE chip tag (§10.4) before its ``</head>``.

Stable is not here: the runtime's own Stable server serves the pinned page and answers ``/__sps`` itself, and proxies
``/api/`` to the release's ``tray`` process (docs/release-channels.md). Run by hand (``serve``, the tests, a fixture)
none of this exists: ``/__sps`` is a 404 and the page carries no chip, as before.

WHY in the app at all (Zach asked for the switch, 2026-10-23): the runtime's only Preview adapter is Vite's, and
§10.7 names this escape hatch until a runtime adapter for a static-plus-backend app exists.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PREFIX = "/__sps"
CHIP_PATH = f"{PREFIX}/chip.js"
CHANNELS = ("preview", "dev")  # WHY not stable: the runtime's Stable server answers /__sps and tags its own page
POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left")
TIMEOUT_S = 30.0
MAX_BODY = 1024 * 1024
FORWARDED = ("Origin", "Content-Type", "Host")
# WHY Vite's escapes (escape-html), not html.escape: §10.4 promises one tag, byte-identical whoever injects it
ATTRIBUTE_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
HEAD_END = re.compile(rb"</head\s*>", re.IGNORECASE)


def runtime() -> Path | None:
    """The runtime's ``sps.py`` when the runtime runs this server as Preview or a lane, else None."""

    script = os.environ.get("SPS_RUNTIME", "")
    if os.environ.get("SPS_CHANNEL") not in CHANNELS or not script or not Path(script).is_file():
        return None
    return Path(script)


def is_sps_path(path: str) -> bool:
    return path == PREFIX or path.startswith(PREFIX + "/")


def chip_bundle(script: Path) -> Path:
    return script.parent / "stable_preview_switcher" / "assets" / "chip.js"


def chip_tag() -> bytes:
    """The §10.4 tag, from the runtime's ``SPS_CHIP_POSITION`` / ``SPS_CHIP_AVOID`` (``chip`` in stable-preview.json)."""

    position = os.environ.get("SPS_CHIP_POSITION", "")
    position = position if position in POSITIONS else "bottom-right"
    try:
        avoid = json.loads(os.environ.get("SPS_CHIP_AVOID") or "[]")
    except json.JSONDecodeError:
        avoid = []
    avoid = [item for item in avoid if isinstance(item, str)] if isinstance(avoid, list) else []
    attribute = ""
    if avoid:
        value = json.dumps(avoid, separators=(",", ":"), ensure_ascii=False)
        attribute = ' data-avoid="' + "".join(ATTRIBUTE_ESCAPES.get(char, char) for char in value) + '"'
    return (f'<script type="module" src="{CHIP_PATH}" data-sps-chip data-position="{position}"{attribute}></script>'
            .encode("utf-8"))


def tag_page(body: bytes) -> bytes:
    """``body`` with the chip tag before its first ``</head>`` (else first). WHY no tokenizer, unlike the runtime's:
    the only pages are this repo's own web/*.html, whose first ``</head>`` is the real one."""

    match = HEAD_END.search(body)
    at = match.start() if match else 0
    return body[:at] + chip_tag() + body[at:]


def api(script: Path, method: str, path: str, headers, body: bytes) -> tuple[int, dict | None]:
    """One ``sps api`` call: ``(status, body)``; never raises. WHY Host too (the Vite bridge leaves it to Vite's own
    host check): this server has none, so the runtime's DNS-rebinding check (§9.2) is the one that guards /__sps."""

    forwarded = {name: headers[name] for name in FORWARDED if headers.get(name) is not None}
    argv = [sys.executable or "/usr/bin/python3", str(script), "api", method, path,
            "--channel", os.environ["SPS_CHANNEL"], "--checkout", os.environ.get("SPS_CHECKOUT", os.getcwd())]
    for name, value in forwarded.items():
        argv += ["--header", f"{name}={value}"]
    try:
        done = subprocess.run(argv, input=body, capture_output=True, timeout=TIMEOUT_S,
                              env={**os.environ, "SPS_API_HEADERS": json.dumps(forwarded)})
    except subprocess.TimeoutExpired:
        return 504, {"error": f"sps api {method} {path} timed out after {TIMEOUT_S:g} s"}
    except OSError as error:
        return 502, {"error": f"could not run sps: {error}"}
    for line in reversed(done.stdout.decode("utf-8", "replace").splitlines()):
        if line.startswith("{"):
            try:
                answer = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(answer, dict) and isinstance(answer.get("status"), int):
                return answer["status"], answer.get("body")
    tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-8:]
    return 502, {"error": f"sps api exited {done.returncode} without an answer", "log": tail}


def page_origins() -> tuple[str, ...]:
    """The origins the page may also be served from: ``IO_DIAGNOSTICS_PAGE_ORIGINS`` (space- or comma-separated).

    WHY: on Stable the page is the runtime's Stable server's (127.0.0.1:8820), which forwards /api/ to this process
    with ``Host`` rewritten to this process's own port, so the page's honest ``Origin`` no longer equals
    ``http://<Host>``. stable-preview.json names the Stable port here; nothing else ever sets it."""

    return tuple(origin.rstrip("/") for origin in re.split(r"[\s,]+", os.environ.get("IO_DIAGNOSTICS_PAGE_ORIGINS", ""))
                 if origin)
