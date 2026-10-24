"""The Activity log through the real host and server (docs/activity-log-v1.md), on the rack session's fixture frames:
what survives a restart of the host and a reboot of the machine, the folded rows `GET /api/v1/activity?fold=1`
answers for an entity and its scope, the Raw channel, and the same rows on the Server-Sent Events stream."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from test_host_support import ROOT  # noqa: F401  (puts the repo on sys.path)

from io_diagnostics.host import make_host
from io_diagnostics.server import make_server, serve_in_thread

CAN = "host/usb1/port-1-5/usb-1-5/can"
CAN15_DRIVER = "host/usb1/port-1-3/usb-1-3/can/15/driver"
PORT_1_7 = "host/usb1/port-1-7"


def played(fixture: str, state_dir, monkeypatch):
    """A fixture host whose event log lives in ``state_dir`` (the rule: a replay writes only where the state dir is named)."""

    monkeypatch.setenv("IO_DIAGNOSTICS_STATE_DIR", str(state_dir))
    host = make_host(fixture=fixture, once=True)
    host.run_all_once()
    return host


def rows(host, **options):
    return host.activity(1000, None, fold=True, **options)["events"]


def codes(events):
    return [event["code"] for event in events]


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    state = tmp_path_factory.mktemp("state")
    mp = pytest.MonkeyPatch()
    mp.setenv("IO_DIAGNOSTICS_STATE_DIR", str(state))
    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    http = make_server(host, 0)
    serve_in_thread(http)
    yield host
    http.shutdown()
    http.server_close()
    mp.undo()


def get(host, path: str):
    try:
        with urllib.request.urlopen(host.url.rstrip("/") + path, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def get_json(host, path: str) -> dict:
    status, body = get(host, path)
    return {"_status": status, **json.loads(body)}


# ----- the rack session, folded ---------------------------------------------------------------------------------------
def test_the_plug_frame_folds_its_faults_onto_their_causes_and_groups_the_start(served):
    log = get_json(served, "/api/v1/activity?fold=1&limit=500")
    events = log["events"]
    by_code = {}
    for event in events:
        by_code.setdefault(event["code"], []).append(event)
    # no FaultConfirmed row of its own: the two with a cause ride on it, the four WARNs at start are one row
    assert "FaultConfirmed" not in by_code
    bus = by_code["BusErrorPassive"][0]
    assert [(badge["label"], badge["fault"], badge["state"]) for badge in bus["badges"]] == [("fault confirmed", "NoAck", "confirmed")]
    board = by_code["BoardFault"][0]
    assert [badge["fault"] for badge in board["badges"]] == ["MoteusFault.33"] and board["severity"] == "error"
    found = by_code["FoundAtStart"]
    assert len(found) == 1 and found[0]["path"] == "host" and len(found[0]["args"]["faults"]) == 4
    assert {fault["fault"] for fault in found[0]["args"]["faults"]} == {"UpdateAvailable", "CameraMoved", "PortFlapping"}
    # the raw list had 22 events of which 6 were confirmations; the log has one row fewer than events minus the folded ones
    raw = get_json(served, "/api/v1/activity?limit=500")["events"]
    assert len(raw) == 22 and len(events) == 17
    assert log["log"]["matching"] == 17 and log["log"]["persisted"] is True and log["log"]["channel"] == "activity"
    # every row carries every key, newest first by its last stamp
    assert all(set(event) == set(raw[0]) for event in events)
    stamps = [event["last_stamp"] if event["last_stamp"] is not None else event["stamp"] for event in events]
    assert stamps == sorted(stamps, reverse=True)


def test_the_unfolded_list_is_what_it_was_plus_the_new_fields(served):
    raw = get_json(served, "/api/v1/activity?limit=500")["events"]
    assert {"FaultConfirmed", "BusErrorPassive"} <= set(codes(raw))
    assert all(event["channel"] == "activity" and event["count"] == 1 and event["received"] is not None for event in raw)
    assert all(event["badges"] == [] and event["members"] == [] for event in raw)


def test_an_entitys_log_is_it_and_its_children_and_optionally_the_path_up_to_the_host(served):
    children = rows_of(served, path="host/usb1/port-1-5", scope="children")
    assert set(event["path"] for event in children) == {"host/usb1/port-1-5", CAN}  # the port's two plug rows and the bus below it
    own = rows_of(served, path="host/usb1/port-1-5", scope="self")
    assert set(event["path"] for event in own) == {"host/usb1/port-1-5"}
    up = rows_of(served, path=CAN, scope="host")
    assert {"host", "host/usb1/port-1-5"} >= {event["path"] for event in up} - {CAN} and {"Booted", "Plugged"} <= set(codes(up))
    only = rows_of(served, path=CAN, scope="children")
    assert codes(only) == ["BusErrorPassive"]
    assert get_json(served, "/api/v1/activity?fold=1&path=host/nowhere")["_status"] == 400


def rows_of(host, **options):
    query = "&".join(f"{key}={value}" for key, value in options.items())
    return get_json(host, f"/api/v1/activity?fold=1&limit=500&{query}")["events"]


def test_the_severity_floor_and_the_types_narrow_the_rows(served):
    warn = rows_of(served, severity="warn")
    assert warn and all(event["severity"] in ("warn", "error", "fatal") for event in warn)
    assert len(rows_of(served, severity="debug")) >= len(rows_of(served, severity="info")) >= len(warn)
    faults = rows_of(served, types="fault")
    assert codes(faults) == ["FoundAtStart"]
    assert get_json(served, "/api/v1/activity?fold=1&severity=loud")["_status"] == 400
    assert get_json(served, "/api/v1/activity?fold=1&scope=sideways")["_status"] == 400
    assert get_json(served, "/api/v1/activity?fold=1&channel=everything")["_status"] == 400


def test_the_raw_channel_is_the_entitys_kernel_lines_and_never_mixed_into_activity(served):
    raw = rows_of(served, channel="raw", path=PORT_1_7, scope="self")
    assert raw and all(event["channel"] == "raw" and event["code"] == "" and event["source"] == "kernel" and event["path"] == PORT_1_7
                       for event in raw)
    assert any("device descriptor read/64, error -71" in event["message"] for event in raw)
    assert {event["severity"] for event in raw} <= {"info", "warn", "error"}
    assert all(event["channel"] == "activity" for event in rows_of(served, path=PORT_1_7))
    both = rows_of(served, channel="all", path=PORT_1_7)
    assert {"raw", "activity"} == {event["channel"] for event in both}
    quiet = rows_of(served, channel="raw", path=PORT_1_7, scope="self", severity="warn")
    assert quiet and all(event["severity"] == "warn" for event in quiet) and len(quiet) < len(raw)


def test_the_stream_carries_the_same_rows(served):
    with urllib.request.urlopen(served.url.rstrip("/") + "/api/v1/events?fold=1&severity=warn", timeout=15) as response:
        assert response.status == 200 and response.readline().startswith(b"retry: ")
        assert response.readline().strip() == b"event: hello"
    status, body = get(served, "/api/v1/events?fold=1&scope=sideways")
    assert status == 400 and "scope" in json.loads(body)["error"]


# ----- the host's events on disk --------------------------------------------------------------------------------------
def test_a_restarted_host_still_has_the_faults_and_the_folded_rows(tmp_path, monkeypatch):
    first = played("F1", tmp_path, monkeypatch)
    before = rows(first)
    log = tmp_path / "log"
    files = sorted(log.glob("*.jsonl"))
    assert len(files) == 1 and files[0].name.endswith("-000.jsonl")
    written = len(files[0].read_text().splitlines())
    assert written >= 15  # the kernel-derived events younger than 30 s (on the frame's clock) are not final yet: not written
    # the next process has not asked a single plugin yet: everything it shows is what the first one kept
    second = make_host(fixture="F1", once=True)
    again = rows(second)
    assert len(again) >= 12 and set(codes(again)) <= set(codes(before)) and {"FoundAtStart", "BusErrorPassive", "BoardFault"} <= set(codes(again))
    folded = {event["code"]: event for event in again}
    assert folded["BusErrorPassive"]["badges"][0]["fault"] == "NoAck" and len(folded["FoundAtStart"]["members"]) == 4
    seen = {event["id"]: event["received"] for event in before}
    assert all(seen[event["id"]] == event["received"] for event in again)  # when it FIRST saw them, not when it restarted
    assert len(sorted(log.glob("*.jsonl"))) == 1 and len(files[0].read_text().splitlines()) == written  # and wrote nothing twice


def test_a_restart_that_runs_the_plugins_again_adds_nothing(tmp_path, monkeypatch):
    first = played("F1", tmp_path, monkeypatch)
    before = rows(first)
    written = len((next((tmp_path / "log").glob("*.jsonl"))).read_text().splitlines())
    second = played("F1", tmp_path, monkeypatch)
    assert codes(rows(second)) == codes(before)
    assert len((next((tmp_path / "log").glob("*.jsonl"))).read_text().splitlines()) == written


def test_a_rebooted_machine_keeps_the_last_boots_events_as_history(tmp_path, monkeypatch):
    rows(played("F1", tmp_path, monkeypatch))
    monkeypatch.setenv("IO_DIAGNOSTICS_STATE_DIR", str(tmp_path))
    other = make_host(fixture="F2", once=True)  # another recorded frame is another boot (its own file)
    other.run_all_once()
    after = rows(other)
    assert len(sorted((tmp_path / "log").glob("*.jsonl"))) == 2
    assert {"BusErrorPassive", "BoardFault", "FoundAtStart"} <= set(codes(after))  # F1's story, kept
    assert codes(after).count("Booted") >= 1


def test_a_host_without_a_state_dir_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("IO_DIAGNOSTICS_STATE_DIR", raising=False)
    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    assert host.eventlog.directory is None and host.eventlog.files() == []
    assert len(rows(host)) == 17  # the same rows, kept in memory only
