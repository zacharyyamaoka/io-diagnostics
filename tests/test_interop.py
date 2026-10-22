"""Interop (lane C): our v0 records in stock viewers, through the contract's own adapter.

The oracles are outside our code: the golden files in io_diagnostics/interop/ros2_check/golden/ were written by
real ROS 2 Humble (the installed .msg files, and rclpy's serialize_message of the fixture array), captured by
``ros2_check/run.py --write-golden``. The live bridge test speaks the Foxglove WebSocket protocol with a stock
client, as Lichtblick does.
"""

from __future__ import annotations

import dataclasses
import json
import struct
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "io_diagnostics" / "interop" / "ros2_check"))

from contract.record import (  # noqa: E402
    ERROR, OK, UNKNOWN, WARN, DiagnosticStatus, Event, KeyValue, from_diagnostic_array, from_diagnostic_status,
    to_diagnostic_array, to_diagnostic_status, to_dict, validate,
)
from io_diagnostics.interop import rack_fixture, stock  # noqa: E402
from wire_check import fields, padding_offsets, same_wire, schema_sections  # noqa: E402

NOW = rack_fixture.FIXTURE_NOW
GOLDEN = ROOT / "io_diagnostics" / "interop" / "ros2_check" / "golden"
STOCK_KEYS = {"level", "name", "message", "hardware_id", "values"}


def levels_by_name(array: dict) -> dict[str, int]:
    return {status["name"]: status["level"] for status in array["status"]}


# ----- the fixture and the adapter (no extra deps) ---------------------------------------------------------------
def test_fixture_is_todays_rack_and_every_record_conforms():
    records = rack_fixture.entities() + rack_fixture.statuses(NOW)
    assert [problem for record in records for problem in validate(record)] == []
    names = {status.name for status in rack_fixture.statuses(NOW)}
    assert {"CAN 1: comms", "CAN 14: comms", "CAN 15: comms", "CAN 15: driver"} <= names
    assert sum(name.startswith("D405 ") and name.endswith(": USB") for name in names) == 2


def test_adapter_round_trip_on_the_fixture_strips_ours_and_keeps_the_stock_five():
    for status in rack_fixture.statuses(NOW):
        projected = to_diagnostic_status(status)
        assert set(projected) == STOCK_KEYS  # path, code, mode, clocks, period, source: all stripped
        back = from_diagnostic_status(projected)
        assert to_diagnostic_status(back) == projected  # stock -> v0 -> stock is the identity
        assert (back.level, back.name, back.message, back.hardware_id) == (
            status.level, status.name, status.message, status.hardware_id)
        # typed measurements arrive as the strings a person reads, after the producer's own values
        assert back.values == status.values + tuple(KeyValue(m.key, m.render()) for m in status.measurements)


def test_stock_array_judges_staleness_at_publish_time():
    array = stock.stock_array(rack_fixture.statuses(NOW), NOW)
    assert all(set(status) == STOCK_KEYS for status in array["status"])
    expected = {name: 0 for name in levels_by_name(array)}
    expected.update({"CAN 14: comms": 3, "CAN 15: driver": 2, "D405 ·6036: firmware": 1})
    assert levels_by_name(array) == expected
    stale = next(status for status in array["status"] if status["name"] == "CAN 14: comms")
    assert stale["message"] == "no update for 47 s (last: answers · 36.5 V · stopped)"
    assert array["header"]["stamp"] == {"sec": int(NOW), "nanosec": 0}  # the observer's clock, not the old report's
    # the same board, freshly reported, goes out as its own OK
    fresh = stock.stock_array(rack_fixture.statuses(NOW, stale_since=NOW), NOW)
    assert levels_by_name(fresh)["CAN 14: comms"] == 0
    # and stock STALE comes back into v0 as UNKNOWN ("can't tell"), never as a level we did not define
    back = {record.name: record.level for record in from_diagnostic_array(array)}
    assert (back["CAN 14: comms"], back["CAN 15: driver"], back["D405 ·6036: firmware"]) == (UNKNOWN, ERROR, WARN)


def test_latest_record_per_check_wins_and_an_undefined_level_is_never_green():
    old = DiagnosticStatus(OK, "CAN 1: comms", "answers", "moteus:32bb145a", path="p", stamp=NOW, received=NOW)
    new = dataclasses.replace(old, level=ERROR, message="no reply", code="NoReply")
    assert levels_by_name(stock.stock_array([old, new], NOW)) == {"CAN 1: comms": 2}
    weird = DiagnosticStatus("purple", "x: y", path="q")  # a level nobody defined
    assert levels_by_name(stock.stock_array([weird], NOW)) == {"x: y": 3}


# ----- the ROS 2 wire, against real ROS 2's own files -------------------------------------------------------------
def test_schema_matches_the_msg_files_ros2_installs():
    pytest.importorskip("rosbags")
    sections = schema_sections(stock.ros2msg_schema())
    golden = {"diagnostic_msgs/DiagnosticArray": "DiagnosticArray.msg", "diagnostic_msgs/DiagnosticStatus":
              "DiagnosticStatus.msg", "diagnostic_msgs/KeyValue": "KeyValue.msg", "std_msgs/Header": "Header.msg",
              "builtin_interfaces/Time": "Time.msg"}
    assert set(sections) == set(golden)
    for type_name, file_name in golden.items():
        package = type_name.split("/")[0]
        assert fields(sections[type_name], package) == fields((GOLDEN / file_name).read_text(), package), type_name
    assert fields(sections["diagnostic_msgs/DiagnosticStatus"], "diagnostic_msgs")[:5] == [
        "byte OK=0", "byte WARN=1", "byte ERROR=2", "byte STALE=3", "byte level"]


def test_cdr_bytes_are_what_rclpy_writes_for_the_same_array():
    pytest.importorskip("rosbags")
    ours = stock.to_cdr(stock.stock_array(rack_fixture.statuses(NOW), NOW))
    theirs = (GOLDEN / "rclpy_fixture_array.cdr").read_bytes()
    passed, detail = same_wire(ours, theirs)
    assert passed, detail
    # the ruler can fail: one payload byte off (a level) is caught, padding noise is not a difference
    first_level = 4 + 4 + 4 + 4 + 1 + 3 + 4  # sec, nanosec, frame_id "" (len+NUL, pad), status count
    assert ours[first_level] == 0
    broken = bytearray(theirs)
    broken[first_level] = 2
    assert not same_wire(ours, bytes(broken))[0]
    assert first_level not in padding_offsets(ours)


def test_cdr_round_trip_returns_the_same_stock_array_and_records():
    pytest.importorskip("rosbags")
    array = stock.stock_array(rack_fixture.statuses(NOW), NOW)
    data = stock.to_cdr(array)
    assert data[:4] == b"\x00\x01\x00\x00"  # CDR little-endian encapsulation
    assert stock.from_cdr(data) == array
    records = stock.records_from_cdr(data, received=NOW + 0.01)
    assert to_diagnostic_array(records, stamp=NOW) == array
    assert {record.stamp for record in records} == {NOW}


# ----- the live bridge: a stock Foxglove WebSocket client, like Lichtblick -----------------------------------------
def test_bridge_publishes_ros2_diagnostics_over_the_foxglove_websocket_protocol():
    pytest.importorskip("foxglove")
    pytest.importorskip("rosbags")
    websockets_client = pytest.importorskip("websockets.sync.client")
    from io_diagnostics.interop.foxglove_bridge import DiagnosticsBridge, PortBusy, fixture_source, port_in_use

    source = fixture_source(started=NOW)
    with DiagnosticsBridge(source, port=0) as bridge:
        assert port_in_use("127.0.0.1", bridge.port)
        with pytest.raises(PortBusy):  # never fights a port someone holds
            DiagnosticsBridge(source, port=bridge.port).start()
        # Lichtblick offers both subprotocols; foxglove-sdk 0.30 answers the newer one
        with websockets_client.connect(bridge.url, subprotocols=["foxglove.websocket.v1", "foxglove.sdk.v1"],
                                       open_timeout=5) as client:
            assert client.subprotocol == "foxglove.sdk.v1"
            channel = None
            while channel is None:
                message = json.loads(client.recv(timeout=5))
                if message["op"] == "advertise":
                    channel = message["channels"][0]
            assert (channel["topic"], channel["encoding"], channel["schemaName"], channel["schemaEncoding"]) == (
                "/diagnostics", "cdr", "diagnostic_msgs/msg/DiagnosticArray", "ros2msg")
            assert channel["schema"] == stock.ros2msg_schema()
            client.send(json.dumps({"op": "subscribe", "subscriptions": [{"id": 7, "channelId": channel["id"]}]}))
            expected, frame = {}, None
            for tick in range(20):  # the subscription lands asynchronously: publish until a frame arrives
                now = NOW + 60 + tick
                expected[int(now)] = bridge.publish(now)
                try:
                    frame = client.recv(timeout=0.5)
                    break
                except TimeoutError:
                    continue
            assert isinstance(frame, bytes)
            opcode, subscription, log_time = struct.unpack_from("<BIQ", frame)
            assert (opcode, subscription) == (1, 7)  # MessageData on our subscription
            received = stock.from_cdr(frame[13:])
            assert received == expected[received["header"]["stamp"]["sec"]]
            assert log_time == received["header"]["stamp"]["sec"] * 1_000_000_000  # one clock for Lichtblick
            stale = next(status for status in received["status"] if status["name"] == "CAN 14: comms")
            assert stale["level"] == 3 and stale["message"].startswith("no update for")
        port = bridge.port
    deadline = time.monotonic() + 3
    while port_in_use("127.0.0.1", port) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not port_in_use("127.0.0.1", port)  # stopped means stopped


def test_records_file_source_reads_plugin_json_lines(tmp_path):
    from io_diagnostics.interop.foxglove_bridge import records_file_source

    lines = [to_dict(record) for record in rack_fixture.entities() + rack_fixture.statuses(NOW)]
    lines.append(to_dict(Event("Plugged", stamp=NOW, path=rack_fixture.ADAPTER)))
    path = tmp_path / "rack.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines) + "\n")
    read = records_file_source(path)(NOW)
    assert read == rack_fixture.statuses(NOW)  # entities and events have no stock form; statuses come back whole
    assert records_file_source(tmp_path / "missing.jsonl")(NOW) == []


def test_host_api_source_reads_records_and_keeps_the_last_ones_when_the_host_goes_quiet():
    import http.server
    import threading

    from io_diagnostics.interop.foxglove_bridge import host_api_source

    statuses = rack_fixture.statuses(NOW)
    body = json.dumps({"entities": [to_dict(entity) for entity in rack_fixture.entities()],
                       "statuses": [to_dict(status) for status in statuses], "events": [], "faults": []}).encode()

    class Records(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib name)
            found = self.path == "/api/v1/records"
            self.send_response(200 if found else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body if found else b"{}")

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Records)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    read = host_api_source(f"http://127.0.0.1:{server.server_address[1]}/")
    try:
        assert read(NOW) == statuses
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    # the host went away: its last records stay, and age into STALE on their own clocks
    assert read(NOW + 30) == statuses
    later = levels_by_name(stock.stock_array(read(NOW + 30), NOW + 30))
    assert later["CAN 1: comms"] == 3 and later["D405 ·4411: USB"] == 0  # periodic goes stale, event-driven does not


def test_lichtblick_layout_is_the_committed_file_and_names_stock_panels():
    from io_diagnostics.interop import lichtblick

    committed = json.loads(lichtblick.LAYOUT_FILE.read_text(encoding="utf-8"))
    assert committed == lichtblick.layout()
    panels = committed["configById"]
    summary, detail = panels[lichtblick.SUMMARY_PANEL], panels[lichtblick.DETAIL_PANEL]
    assert lichtblick.SUMMARY_PANEL.startswith("DiagnosticSummary!")
    assert lichtblick.DETAIL_PANEL.startswith("DiagnosticStatusPanel!")
    assert summary["topicToRender"] == detail["topicToRender"] == stock.TOPIC
    fixture = {(status.hardware_id, status.name) for status in rack_fixture.statuses(NOW)}
    assert (detail["selectedHardwareId"], detail["selectedName"]) in fixture
