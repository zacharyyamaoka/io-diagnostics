"""Open in Rerun: each device row's link to a live Rerun view of that device's streams (docs/api-v1.md "The rerun field").

A recorder process owned by another agent writes a discovery doc; RobotDx only READS it. Nothing here opens a device, a bus or a
socket other than the doc's own file or URL. The contract (field names below) is a proposal: every name the doc is read by is a
constant in this module, so a change of the recorder's words is one edit here.

    {"schema": "rerun-discovery/1", "updated": <unix s>, "viewer_url": "...", "cell_url": "...", "start_cmd": "<optional>",
     "devices": [{"key": "...", "label": "...", "entity_root": "...", "url": "...", "live": true, "last_t": <unix s>}]}

WHY (Zach, 2026-11-13, via the sprint dispatcher): each pluggable device (moteus board, D435, OBSBOT) opens a live Rerun view, plus a
robot/cell view; reading stays passive, the recorder owns the devices.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

# --- the discovery doc's words (a proposal: change them here and nowhere else) -----------------------------------------------
ENV_SOURCE = "RERUN_DISCOVERY"                      # a file path or an http(s) URL
DEFAULT_FILE = Path.home() / ".cache" / "data-recorder" / "rerun.json"
SCHEMA_PREFIX = "rerun-discovery/"
DOC_UPDATED = "updated"
DOC_VIEWER_URL = "viewer_url"
DOC_CELL_URL = "cell_url"
DOC_START_CMD = "start_cmd"
DOC_DEVICES = "devices"
DEVICE_KEY = "key"
DEVICE_LABEL = "label"
DEVICE_ENTITY_ROOT = "entity_root"
DEVICE_URL = "url"
DEVICE_LIVE = "live"
DEVICE_LAST_T = "last_t"

# --- the field RobotDx attaches to a node ------------------------------------------------------------------------------------
FIELD = "rerun"
OUT_URL, OUT_LABEL, OUT_LIVE, OUT_ABSENT, OUT_START_CMD = "url", "label", "live", "absent", "start_cmd"
ABSENT_NO_RECORDER = "Recorder not running"
ABSENT_NOT_LISTED = "Not in the recorder's device list"
CELL_LABEL = "Rerun cell view"

STALE_AFTER_S = 10.0       # a doc not rewritten for this long means no recorder is running
CACHE_S = 2.0              # a file is read at most this often; an http doc is refetched at most this often
HTTP_TIMEOUT_S = 1.0

HOST_PATH = "host"
RIG_KIND = "rig"
OBSBOT_VID = "3564"
REALSENSE_WORD = "realsense"


# --- keys: how a node is named in the doc ------------------------------------------------------------------------------------
def usb_key(hardware_id: str, identity: dict) -> str:
    """``usb:<vid>:<pid>:<serial>`` with the identity's serial (the one embedded in the id can differ: a D435's hardware_id
    ``usb:8086:0b07:037323022737`` against its identity serial 042222070792); without a serial, the hardware id as-is."""

    serial = (identity or {}).get("serial_number")
    parts = hardware_id.split(":")
    if serial and len(parts) >= 3 and parts[0] == "usb":
        # WHY strip "@<port>": a serial-less hardware id carries the port (the D405's usb:8086:0b5b@1-7) while its identity
        # still reports a serial (218622274411); the key is usb:8086:0b5b:218622274411 (seen on Stable 2026-11-13).
        return f"usb:{parts[1]}:{parts[2].split('@', 1)[0]}:{serial}"
    return hardware_id


def is_usb_camera(hardware_id: str, identity: dict, child_ids: list[str]) -> bool:
    """A USB device with video4linux children, a RealSense, or an OBSBOT (USB vendor 3564)."""

    parts = hardware_id.split(":")
    if parts[0] != "usb" or len(parts) < 3:
        return False
    words = f"{(identity or {}).get('manufacturer', '')} {(identity or {}).get('model', '')}".lower()
    return (any(child.startswith("v4l:") for child in child_ids) or REALSENSE_WORD in words or parts[1].lower() == OBSBOT_VID)


def rig_key(path: str, rig_names: dict[str, str]) -> str:
    """``rig:<name>`` from the setup manifest, else ``rig:<slug>`` (the path's last word without ``rig-``)."""

    return f"rig:{rig_names.get(path) or path.rsplit('/', 1)[-1].removeprefix('rig-')}"


def node_key(path: str, kind: str, hardware_id: str, identity: dict, child_ids: list[str], entity_of, rig_names: dict[str, str]) -> str | None:
    """The doc key of an eligible node (host, rig, moteus board, USB camera), else None. ``entity_of(path)`` gives the
    ``(hardware_id, identity)`` of another node (a board's adapter)."""

    if path == HOST_PATH:
        return HOST_PATH
    if kind == RIG_KIND:
        return rig_key(path, rig_names)
    # WHY by path, not kind: the live moteus plugin emits boards as kind "node" (Stable, 2026-11-13: host/usb7/.../can/14),
    # fixtures as "servo"; what makes it a board on a CAN bus is sitting at <adapter>/can/<id>.
    adapter, _, number = path.rpartition("/can/")
    if adapter and number.isdigit():
        found = entity_of(adapter)
        serial = (found[1] or {}).get("serial_number") if found else None
        return f"moteus:{serial}/can:{number}" if serial else None
    if hardware_id and is_usb_camera(hardware_id, identity, child_ids):
        return usb_key(hardware_id, identity)
    return None


# --- the doc: a file read inline (cached), or a URL fetched on a thread ------------------------------------------------------
class Source:
    """Where the discovery doc is read from. ``read()`` never blocks on the network: a URL is fetched by a background thread and
    the last doc fetched is what a read returns, so a dead URL never delays a state read."""

    def __init__(self, spec: str | None = None):
        self.spec = spec or str(DEFAULT_FILE)
        self.is_url = self.spec.lower().startswith(("http://", "https://"))
        self._doc: dict | None = None
        self._read_at = float("-inf")
        self._lock = threading.Lock()
        self._fetching = False

    def read(self, now: float | None = None) -> dict | None:
        now = time.monotonic() if now is None else now
        if self.is_url:
            with self._lock:
                if not self._fetching and now - self._read_at >= CACHE_S:
                    self._fetching, self._read_at = True, now
                    threading.Thread(target=self._fetch, name="rerun-discovery", daemon=True).start()
                return self._doc
        if now - self._read_at >= CACHE_S:
            self._read_at = now
            self._doc = parse(self._load_file())
        return self._doc

    def _load_file(self) -> str | None:
        try:
            return Path(self.spec).expanduser().read_text()
        except OSError:
            return None

    def _fetch(self) -> None:
        doc = None
        try:
            with urllib.request.urlopen(self.spec, timeout=HTTP_TIMEOUT_S) as response:
                doc = parse(response.read(1 << 20).decode("utf-8", "replace"))
        except (OSError, ValueError):
            doc = None
        with self._lock:
            # WHY the last good doc is dropped on a failure: a recorder that is gone must read "not running", and that is what an
            # absent doc says; a doc that lingers would only go stale on its own 10 s later
            self._doc, self._fetching = doc, False


def parse(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict) or not str(doc.get("schema", "")).startswith(SCHEMA_PREFIX) or not isinstance(doc.get(DOC_UPDATED), (int, float)):
        return None
    return doc


_SOURCES: dict[str, Source] = {}
_SOURCES_LOCK = threading.Lock()


def current_source() -> Source:
    spec = os.environ.get(ENV_SOURCE) or str(DEFAULT_FILE)
    with _SOURCES_LOCK:
        return _SOURCES.setdefault(spec, Source(spec))


# --- what a node gets --------------------------------------------------------------------------------------------------------
class Links:
    """The doc as it stands now, worked out into the answer for any key."""

    def __init__(self, doc: dict | None, wall: float):
        self.doc = doc
        self.fresh = bool(doc) and wall - doc[DOC_UPDATED] <= STALE_AFTER_S
        self.devices = {entry.get(DEVICE_KEY): entry for entry in (doc or {}).get(DOC_DEVICES, []) if isinstance(entry, dict)} if self.fresh else {}

    def of(self, key: str, *, cell: bool = False) -> dict:
        if not self.fresh:
            # WHY the start command only from a stale doc: a doc that was never written has no recorder to have said how to start it
            absent = {OUT_ABSENT: ABSENT_NO_RECORDER}
            if self.doc and self.doc.get(DOC_START_CMD):
                absent[OUT_START_CMD] = str(self.doc[DOC_START_CMD])
            return absent
        entry = self.devices.get(key)
        if entry and entry.get(DEVICE_URL):
            return {OUT_URL: entry[DEVICE_URL], OUT_LABEL: entry.get(DEVICE_LABEL) or key, OUT_LIVE: bool(entry.get(DEVICE_LIVE))}
        if cell and self.doc.get(DOC_CELL_URL):
            return {OUT_URL: self.doc[DOC_CELL_URL], OUT_LABEL: CELL_LABEL,
                    OUT_LIVE: any(device.get(DEVICE_LIVE) for device in self.devices.values())}
        return {OUT_ABSENT: ABSENT_NOT_LISTED}


def links_now(source: Source | None = None, wall: float | None = None) -> Links:
    source = source or current_source()
    return Links(source.read(), time.time() if wall is None else wall)


def attach_tree(root: dict, rig_names: dict[str, str], links: Links | None = None) -> dict:
    """A copy of the nested ``tree`` with ``rerun`` on every eligible node. Copy-on-write: a subtree with nothing eligible in it is
    the very same object (the host caches its tree per published view; the cached one is never touched)."""

    links = links or links_now()
    by_path: dict[str, dict] = {}

    def index(node: dict) -> None:
        by_path[node["path"]] = node
        for child in node["children"]:
            index(child)

    index(root)

    def entity_of(path: str):
        node = by_path.get(path)
        return (node["hardware_id"], node["identity"]) if node else None

    def walk(node: dict) -> dict:
        children = [walk(child) for child in node["children"]]
        key = node_key(node["path"], node["kind"], node.get("hardware_id") or "", node.get("identity") or {},
                       [child.get("hardware_id") or "" for child in node["children"]], entity_of, rig_names)
        changed = key is not None or any(new is not old for new, old in zip(children, node["children"]))
        if not changed:
            return node
        out = dict(node, children=children)
        if key is not None:
            out[FIELD] = links.of(key, cell=node["path"] == HOST_PATH or node["kind"] == RIG_KIND)
        return out

    return walk(root)


def attach_node(node: dict, child_paths: list[str], entity_of, rig_names: dict[str, str], links: Links | None = None) -> dict:
    """``node`` (one ``node_json``, children as paths) as a copy with ``rerun`` when it is eligible. ``entity_of(path)`` gives
    ``(hardware_id, identity)`` for any path."""

    key = node_key(node["path"], node["kind"], node.get("hardware_id") or "", node.get("identity") or {},
                   [(entity_of(child) or ("", {}))[0] or "" for child in child_paths], entity_of, rig_names)
    if key is None:
        return node
    return dict(node, **{FIELD: (links or links_now()).of(key, cell=node["path"] == HOST_PATH or node["kind"] == RIG_KIND)})
