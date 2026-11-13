"""Open in Rerun: the discovery doc a recorder writes, read passively, and the `rerun` field on a node (docs/api-v1.md "The rerun field").

Key derivation uses the shapes a real host builds: a D435 whose identity serial (042222070792) differs from the one inside its
hardware_id (usb:8086:0b07:037323022737), an OBSBOT without a serial (usb:3564:fef4@1-2), a moteus board under an fdcanusb, a rig.
The http source is a local http.server on a scratch port; nothing here touches hardware.
"""

from __future__ import annotations

import http.server
import json
import threading
import time

import pytest
from test_host_setup import Rack
from test_host_support import host_dict, make_host, standard_skeleton, usb_device

from io_diagnostics import rerun_links as rr

NOW = 1_794_260_000.0
D435_KEY = "usb:8086:0b07:042222070792"
OBSBOT_ID = "usb:3564:fef4@1-2"
ADAPTER = "host/usb1/port-1-3/usb-1-3"
BOARD = f"{ADAPTER}/can/1"


def node(path, kind, hardware_id="", identity=None, children=()):
    return {"path": path, "kind": kind, "hardware_id": hardware_id, "identity": identity or {}, "children": list(children)}


def shape_tree():
    """The tree a state read has: host > rig, USB bus > D435 (with v4l children), OBSBOT, an fdcanusb with one moteus board, a mouse."""

    d435 = node("host/usb1/port-1-1/usb-1-1", "device", "usb:8086:0b07:037323022737",
                {"serial_number": "042222070792", "model": "Intel(R) RealSense(TM) Depth Camera 435"},
                [node("host/usb1/port-1-1/usb-1-1/video0", "device", "v4l:1-1:1.0#0")])
    obsbot = node("host/usb1/port-1-2/usb-1-2", "device", OBSBOT_ID, {"manufacturer": "Remo Tech Co., Ltd.", "model": "OBSBOT Tiny 4K"})
    board = node(BOARD, "servo", "moteus:272813EF-1")
    adapter = node(ADAPTER, "device", "usb:0483:5740:272813EF", {"serial_number": "272813EF", "model": "fdcanusb"},
                   [node(f"{ADAPTER}/can", "bus", children=[board])])
    mouse = node("host/usb1/port-1-4/usb-1-4", "device", "usb:046d:c092:206636804B32", {"serial_number": "206636804B32"})
    rig = node("host/rig-1-dof-test-rack", "rig")
    return node("host", "host", children=[rig, node("host/usb1", "bus", children=[d435, obsbot, adapter, mouse])])


def doc(devices=(), *, updated=NOW, **extra):
    return {"schema": "rerun-discovery/1", "updated": updated, "viewer_url": "http://127.0.0.1:9876", "cell_url": "http://127.0.0.1:9876/cell",
            "devices": list(devices), **extra}


def entry(key, live=True):
    return {"key": key, "label": f"label of {key}", "entity_root": f"/{key}", "url": f"http://127.0.0.1:9876/?d={key}", "live": live, "last_t": NOW - 1}


def by_path(root):
    out, stack = {}, [root]
    while stack:
        current = stack.pop()
        out[current["path"]] = current
        stack.extend(current["children"])
    return out


# ----- keys ------------------------------------------------------------------------------------------------------------------
def test_keys_follow_the_proposed_contract():
    found = by_path(rr.attach_tree(shape_tree(), {}, rr.Links(doc([entry(D435_KEY)]), NOW)))
    # a USB device with a serial: the IDENTITY's serial, not the one embedded in the hardware_id
    assert found["host/usb1/port-1-1/usb-1-1"]["rerun"]["label"] == f"label of {D435_KEY}"
    # no serial: the hardware_id as-is (listed under another key, it is not found)
    assert found["host/usb1/port-1-2/usb-1-2"]["rerun"] == {"absent": rr.ABSENT_NOT_LISTED}
    assert rr.usb_key(OBSBOT_ID, {"model": "OBSBOT Tiny 4K"}) == OBSBOT_ID
    assert rr.usb_key("usb:8086:0b07:037323022737", {"serial_number": "042222070792"}) == D435_KEY


@pytest.mark.parametrize("key, path", [(OBSBOT_ID, "host/usb1/port-1-2/usb-1-2"), ("moteus:272813EF/can:1", BOARD)])
def test_obsbot_and_moteus_board_match_their_key(key, path):
    found = by_path(rr.attach_tree(shape_tree(), {}, rr.Links(doc([entry(key, live=False)]), NOW)))
    assert found[path]["rerun"] == {"url": f"http://127.0.0.1:9876/?d={key}", "label": f"label of {key}", "live": False}


def test_a_rig_is_named_by_the_setup_then_by_its_slug():
    named = by_path(rr.attach_tree(shape_tree(), {"host/rig-1-dof-test-rack": "1-DOF test rack"},
                                   rr.Links(doc([entry("rig:1-DOF test rack")]), NOW)))
    assert named["host/rig-1-dof-test-rack"]["rerun"]["label"] == "label of rig:1-DOF test rack"
    slugged = by_path(rr.attach_tree(shape_tree(), {}, rr.Links(doc([entry("rig:1-dof-test-rack")]), NOW)))
    assert slugged["host/rig-1-dof-test-rack"]["rerun"]["label"] == "label of rig:1-dof-test-rack"


def test_the_host_and_an_unlisted_rig_link_to_the_cell():
    found = by_path(rr.attach_tree(shape_tree(), {}, rr.Links(doc([entry(D435_KEY, live=True)]), NOW)))
    assert found["host"]["rerun"] == {"url": "http://127.0.0.1:9876/cell", "label": rr.CELL_LABEL, "live": True}
    assert found["host/rig-1-dof-test-rack"]["rerun"]["url"] == "http://127.0.0.1:9876/cell"


def test_only_eligible_nodes_get_the_field_and_the_cached_tree_is_untouched():
    cached = shape_tree()
    found = by_path(rr.attach_tree(cached, {}, rr.Links(doc([]), NOW)))
    assert {path for path, one in found.items() if "rerun" in one} == {
        "host", "host/rig-1-dof-test-rack", "host/usb1/port-1-1/usb-1-1", "host/usb1/port-1-2/usb-1-2", BOARD}
    assert not any("rerun" in one for one in by_path(cached).values())
    # a subtree with nothing eligible is shared, not copied
    assert rr.attach_tree(node("host", "host", children=[node("host/x", "bus")]), {}, rr.Links(doc([]), NOW))["children"][0]["path"] == "host/x"


def test_a_board_without_an_adapter_serial_is_not_eligible():
    lone = node("host/usb1/port-1-3/usb-1-3", "device", "usb:0483:5740@1-3", {}, [node("host/usb1/port-1-3/usb-1-3/can/1", "servo")])
    found = by_path(rr.attach_tree(node("host", "host", children=[lone]), {}, rr.Links(doc([]), NOW)))
    assert "rerun" not in found["host/usb1/port-1-3/usb-1-3/can/1"]


# ----- staleness -------------------------------------------------------------------------------------------------------------
def test_a_missing_doc_says_the_recorder_is_not_running_with_no_start_command():
    found = by_path(rr.attach_tree(shape_tree(), {}, rr.Links(None, NOW)))
    assert found[BOARD]["rerun"] == {"absent": rr.ABSENT_NO_RECORDER}
    assert found["host"]["rerun"] == {"absent": rr.ABSENT_NO_RECORDER}


def test_a_doc_older_than_ten_seconds_is_stale_and_brings_its_start_command():
    stale = doc([entry(D435_KEY)], updated=NOW - 10.5, start_cmd="data-recorder serve")
    found = by_path(rr.attach_tree(shape_tree(), {}, rr.Links(stale, NOW)))
    assert found["host/usb1/port-1-1/usb-1-1"]["rerun"] == {"absent": rr.ABSENT_NO_RECORDER, "start_cmd": "data-recorder serve"}
    edge = by_path(rr.attach_tree(shape_tree(), {}, rr.Links(doc([entry(D435_KEY)], updated=NOW - 10.0), NOW)))
    assert "url" in edge["host/usb1/port-1-1/usb-1-1"]["rerun"], "10 s old is still fresh"


def test_a_doc_of_another_schema_or_not_json_is_no_doc():
    assert rr.parse("not json") is None
    assert rr.parse(json.dumps({"schema": "other/1", "updated": NOW})) is None
    assert rr.parse(json.dumps({"schema": "rerun-discovery/1"})) is None
    assert rr.parse(json.dumps(doc()))["updated"] == NOW


# ----- sources: a file (cached ~2 s) and an http URL (a thread, never blocking) -------------------------------------------------
def test_a_file_is_read_inline_and_cached_for_two_seconds(tmp_path):
    path = tmp_path / "rerun.json"
    source = rr.Source(str(path))
    assert source.read(now=100.0) is None  # no file: no doc
    path.write_text(json.dumps(doc()))
    assert source.read(now=101.0) is None  # still inside the cache window
    assert source.read(now=102.5)["updated"] == NOW
    path.write_text(json.dumps(doc(updated=NOW + 5)))
    assert source.read(now=103.0)["updated"] == NOW
    assert source.read(now=105.0)["updated"] == NOW + 5


def test_the_env_names_the_source(tmp_path, monkeypatch):
    path = tmp_path / "elsewhere.json"
    path.write_text(json.dumps(doc([entry(D435_KEY)])))
    monkeypatch.setenv(rr.ENV_SOURCE, str(path))
    assert rr.current_source().spec == str(path)
    monkeypatch.delenv(rr.ENV_SOURCE)
    assert rr.current_source().spec == str(rr.DEFAULT_FILE)


class Recorder(http.server.BaseHTTPRequestHandler):
    body = b""
    delay_s = 0.0

    def do_GET(self):
        time.sleep(self.delay_s)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


def serve(body: bytes, delay_s: float = 0.0):
    handler = type("Handler", (Recorder,), {"body": body, "delay_s": delay_s})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_an_http_doc_is_fetched_on_a_thread_and_a_slow_url_never_delays_a_read():
    server = serve(json.dumps(doc([entry(D435_KEY)])).encode())
    try:
        source = rr.Source(f"http://127.0.0.1:{server.server_address[1]}/rerun.json")
        started = time.monotonic()
        first = source.read(now=0.0)
        assert time.monotonic() - started < 0.2 and first is None, "the first read answers at once, before the fetch lands"
        deadline = time.monotonic() + 5
        while source.read(now=0.0) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert source.read(now=0.0)["devices"][0]["key"] == D435_KEY
    finally:
        server.shutdown()
    slow = serve(b"{}", delay_s=3.0)
    try:
        source = rr.Source(f"http://127.0.0.1:{slow.server_address[1]}/")
        started = time.monotonic()
        assert source.read(now=0.0) is None
        assert source.read(now=10.0) is None
        assert time.monotonic() - started < 0.3, "a URL that does not answer in 1 s costs a read nothing"
    finally:
        slow.shutdown()


def test_a_dead_url_drops_the_doc_it_had():
    server = serve(json.dumps(doc()).encode())
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    source = rr.Source(url)
    source.read(now=0.0)
    deadline = time.monotonic() + 5
    while source.read(now=0.0) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    server.shutdown()
    server.server_close()
    source.read(now=10.0)
    deadline = time.monotonic() + 5
    while source.read(now=10.0) is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert source.read(now=10.0) is None


# ----- through the host: state() and node() -----------------------------------------------------------------------------------
@pytest.fixture
def recorder(tmp_path, monkeypatch):
    path = tmp_path / "rerun.json"
    monkeypatch.setenv(rr.ENV_SOURCE, str(path))
    rr._SOURCES.clear()

    def write(body, **options):
        path.write_text(json.dumps(body))
        rr._SOURCES.clear()
        return path
    yield write
    rr._SOURCES.clear()


def test_the_host_attaches_rerun_to_state_and_node_and_leaves_its_cache_alone(tmp_path, recorder):
    d435 = usb_device("9-1", "8086", "0b07", "042222070792", label="Intel RealSense D435")
    d435["attrs"]["hardware_id"] = "usb:8086:0b07:037323022737"  # the id says one serial, the device's own another
    obsbot = usb_device("9-2", "3564", "fef4", label="OBSBOT Tiny 4K")
    mouse = usb_device("9-3", "046d", "c092", "206636804B32", label="mouse")
    host = make_host(tmp_path, standard_skeleton(d435, obsbot, mouse), [])
    host.refresh()
    view = host.view()
    host.read_view = lambda: view  # one published view, as a serving host answers many reads from it
    recorder(doc([entry(D435_KEY)], updated=time.time()))
    found = by_path(host.state()["tree"])
    camera = next(one for one in found.values() if one["hardware_id"] == "usb:8086:0b07:037323022737")
    assert camera["rerun"]["url"].endswith(D435_KEY) and camera["rerun"]["live"] is True
    other = next(one for one in found.values() if one["hardware_id"].startswith("usb:3564:fef4@"))
    assert other["rerun"] == {"absent": rr.ABSENT_NOT_LISTED}
    assert "rerun" not in next(one for one in found.values() if one["hardware_id"] == "usb:046d:c092:206636804B32")
    assert found["host"]["rerun"]["url"] == "http://127.0.0.1:9876/cell"
    # the page's own read: the node, with the same answer
    assert host.node(camera["path"])["node"]["rerun"] == camera["rerun"]
    assert host.node("host")["node"]["rerun"] == found["host"]["rerun"]
    # the recorder goes quiet: the very next state says so, with no new published view in between
    recorder(doc([entry(D435_KEY)], updated=time.time() - 60, start_cmd="data-recorder serve"))
    quiet = by_path(host.state()["tree"])[camera["path"]]
    assert quiet["rerun"] == {"absent": rr.ABSENT_NO_RECORDER, "start_cmd": "data-recorder serve"}
    cached = view.memo["state"]["tree"]
    assert not any("rerun" in one for one in by_path(cached).values())


def test_a_rig_row_links_by_its_manifest_name(tmp_path, recorder):
    rack = Rack(tmp_path)
    recorder(doc([entry("rig:1-DOF test rack")], updated=time.time()))
    found = by_path(rack.host.state()["tree"])
    assert found["host/rig-1-dof-test-rack"]["rerun"]["label"] == "label of rig:1-DOF test rack"
    assert rack.host.node("host/rig-1-dof-test-rack")["node"]["rerun"]["label"] == "label of rig:1-DOF test rack"


def test_no_doc_at_all_still_leaves_the_state_whole(tmp_path, monkeypatch):
    monkeypatch.setenv(rr.ENV_SOURCE, str(tmp_path / "none.json"))
    rr._SOURCES.clear()
    host = make_host(tmp_path, [host_dict()], [])
    host.refresh()
    assert host.state()["tree"]["rerun"] == {"absent": rr.ABSENT_NO_RECORDER}


def test_a_live_moteus_board_of_kind_node_gets_its_key():
    """The live moteus plugin emits boards as kind "node" (Stable 2026-11-13, host/usb7/port-7-1/usb-7-1/can/14); the key comes from
    where it sits, <adapter>/can/<id>, so it still links. A bus node itself (…/can) is not a board."""

    adapter = ("usb:0483:5740:272813EF", {"serial_number": "FDA81D29", "model": "fdcanusb"})
    of = lambda path: adapter if path == "host/usb7/port-7-1/usb-7-1" else None
    key = rr.node_key("host/usb7/port-7-1/usb-7-1/can/14", "node", "", {"manufacturer": "mjbots", "model": "moteus-c1"}, [], of, {})
    assert key == "moteus:FDA81D29/can:14"
    assert rr.node_key("host/usb7/port-7-1/usb-7-1/can", "bus", "", {}, [], of, {}) is None
