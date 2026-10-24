"""The host's rolling history (io_diagnostics/history.py) and its API: level spans and measurement series per (entity
path, status name), the health bar with the honest async rule, the bounds, and the app-wide window setting
(``serve --history-window``, ``GET/POST /api/v1/settings``, ``settings.toml``). Fakes only: stdlib plugins, skeleton
dicts, a hand-moved clock, temporary files.
"""

from __future__ import annotations

import json
import tracemalloc
import urllib.error
import urllib.request

import pytest
from test_host_api import example, same_keys
from test_host_support import ROOT, FakeOs, SetClock, make_host, standard_skeleton, usb_device

from contract.record import ERROR, OK, STALE, WARN, DiagnosticStatus, Measurement
from io_diagnostics.history import DEFAULT_WINDOW_S, History, bar
from io_diagnostics.host import Host
from io_diagnostics.server import make_server, serve_in_thread
from io_diagnostics.settings import SETTINGS_FILE, Settings, SettingsError, parse_duration

PAGE = {"Content-Type": "application/json", "X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin"}


def report(at, level=OK, *, name="CAN 1: servo", path="rack/can/1", period=2.0, code="", message="", values=()):
    if level != OK and not code:
        code = "Failed"
    return DiagnosticStatus(level, name, message or level, path=path, code=code, stamp=at, received=at,
                            expected_period_s=period, measurements=tuple(Measurement(key, value, "V") for key, value in values))


def levels(history, key, start, end):
    return [(piece["from"], piece["to"], piece["level"]) for piece in history.timeline(key, start, end)]


# ----- the honest async rule ------------------------------------------------------------------------------------
def test_a_status_holds_its_level_until_it_goes_stale_and_no_data_is_its_own_state():
    history = History()
    history.observe([report(100), report(102), report(104), report(115), report(117, WARN)], now=120)
    # 3 x 2 s = 6 s, floored at 5 s: held to 110, stale until the next arrival; nothing before the first one
    assert levels(history, ("rack/can/1", "CAN 1: servo"), 90, 120) == [
        (90, 100, None), (100, 110, OK), (110, 115, STALE), (115, 117, OK), (117, 120, WARN)]


def test_an_event_driven_status_holds_until_its_next_report():
    history = History()
    history.observe([report(100, period=None), report(500, WARN, period=None)], now=600)
    assert levels(history, ("rack/can/1", "CAN 1: servo"), 0, 600) == [(0, 100, None), (100, 500, OK), (500, 600, WARN)]


def test_out_of_sync_arrivals_never_fabricate_a_step():
    """Two checks of one board, 10 s periods, arriving 3 s apart: a "latest snapshot" would flicker; the bar must not."""

    history = History()
    for at in (0, 10, 20, 30):
        history.observe([report(at, period=10, name="servo"), report(at + 3, period=10, name="config")], now=at + 3)
    timelines = [history.timeline(("rack/can/1", name), 0, 33) for name in ("servo", "config")]
    assert bar(timelines, 0, 33, 33) == [OK] * 33  # no gap and no step anywhere: each holds its own last level
    # one WARN arrival on the config check shows from its own arrival, held for its own period, never earlier
    history.observe([report(36, WARN, period=10, name="config")], now=40)
    timelines = [history.timeline(("rack/can/1", name), 30, 40) for name in ("servo", "config")]
    assert bar(timelines, 30, 40, 10) == [OK] * 6 + [WARN] * 4
    # a bin that no status held anything in is no data, never a level
    assert bar([history.timeline(("rack/can/1", "servo"), -20, 0)], -20, 0, 4) == [None, None, None, None]


def test_every_measurement_value_is_kept_on_its_own_receive_stamp():
    history = History()
    history.observe([report(100, values=[("voltage", 36.5)]), report(102, values=[("voltage", 36.4)])], now=103)
    history.observe([report(101, values=[("voltage", 36.6)])], now=103)  # a late arrival is placed, not appended
    [series] = history.series(("rack/can/1", "CAN 1: servo"), 0, 200)
    assert (series["key"], series["unit"], series["points"]) == ("voltage", "V", [[100, 36.5], [101, 36.6], [102, 36.4]])
    # ... and cannot move a level backwards in time
    assert levels(history, ("rack/can/1", "CAN 1: servo"), 100, 103) == [(100, 103, OK)]


def test_a_recomputed_set_arrives_on_change_and_a_status_no_longer_computed_ends():
    history = History()
    present = DiagnosticStatus(OK, "thing: presence", "on port 9-1", path="host/usb-9-1", received=100)
    history.sync("os", [present], 100)
    history.sync("os", [DiagnosticStatus(OK, "thing: presence", "on port 9-1", path="host/usb-9-1", received=102)], 102)
    assert history.points == 1  # unchanged: one span, not one per refresh
    history.sync("os", [], 150)  # unplugged
    assert levels(history, ("host/usb-9-1", "thing: presence"), 90, 200) == [(90, 100, None), (100, 150, OK), (150, 200, None)]
    history.observe([report(160)], 160, source=("job", "rig", "usb:9-1"))
    history.end(("job", "rig", "usb:9-1"), 161)
    assert levels(history, ("rack/can/1", "CAN 1: servo"), 150, 170) == [(150, 160, None), (160, 161, OK), (161, 170, None)]


def test_the_history_is_bounded():
    history = History(max_points=100, max_series=3)
    for at in range(1000):
        history.observe([report(float(at), WARN if at % 2 else OK, values=[("voltage", at)])], now=float(at))
    assert history.points <= 100
    for number in range(5):
        history.observe([report(2000.0 + number, name=f"check {number}")], now=2000.0 + number)
    assert len(history.keys("rack/can/1")) == 3  # the least recently heard went first
    assert [name for _path, name in history.keys("rack/can/1")] == ["check 2", "check 3", "check 4"]
    # retention: what is older than the window (at least 15 min) is pruned on arrival
    old = History(window_s=60)
    old.observe([report(0.0, values=[("voltage", 1)])], now=0.0)
    old.observe([report(DEFAULT_WINDOW_S + 10, WARN, values=[("voltage", 2)])], now=DEFAULT_WINDOW_S + 10)
    assert [series["points"] for series in old.series(("rack/can/1", "CAN 1: servo"), 0, 1e9)] == [[[DEFAULT_WINDOW_S + 10, 2]]]


def test_a_full_history_stays_within_its_stated_memory():
    """The bound history.py states (MAX_POINTS points, ~25 MB): measured, so the number in the doc is not a guess."""

    tracemalloc.start()
    history = History()
    before = tracemalloc.get_traced_memory()[0]
    for number in range(25_000):  # 275,000 points inside the retention: the cap must hold it at 200,000
        at = number / 100
        history.observe([report(at, OK if number % 3 else WARN, name=f"check {number % 20}",
                                values=[(f"value {index}", at) for index in range(10)])], now=at)
    used = tracemalloc.get_traced_memory()[0] - before
    assert history.points == 200_000
    assert used < 30 * 1024 * 1024, used
    # the dearer shape: every point a level change (a span object, not a pair of numbers)
    history = History()
    before = tracemalloc.get_traced_memory()[0]
    for number in range(210_000):
        history.observe([report(number / 1000, OK if (number // 2) % 2 else WARN, name=f"check {number % 2}")], now=number / 1000)
    used = tracemalloc.get_traced_memory()[0] - before
    tracemalloc.stop()
    assert history.points == 200_000
    assert used < 30 * 1024 * 1024, used


# ----- the host: what it feeds, and the API ------------------------------------------------------------------------
@pytest.fixture
def rack(tmp_path):
    host = make_host(tmp_path, standard_skeleton(usb_device("9-1", "1234", "abcd", "DEAD")), [("deadbus", "deadbus", ["usb:1234:abcd"])])
    for at in (1000.0, 1004.0, 1012.0, 1016.0):  # the bus answers on the first two polls, then dies
        host.clock.at = at
        host.run_all_once()
    host.clock.at = 1030.0
    return host


def test_the_host_keeps_each_plugin_status_and_each_os_check(rack):
    device = rack.layer.device_paths["usb:9-1"]
    bus = rack.history_json(f"{device}/can")
    # the host adds one more on the bus once its remembered boards go silent (roster.stopped_answering)
    assert {status["name"] for status in bus["statuses"]} == {"DEAD CAN bus: bus", "DEAD CAN bus: boards stopped answering"}
    [status] = [status for status in bus["statuses"] if status["name"] == "DEAD CAN bus: bus"]
    assert (status["name"], status["expected_period_s"], status["stale_after_s"]) == ("DEAD CAN bus: bus", 2, 6.0)
    assert [(piece["from"], piece["level"], piece["code"]) for piece in status["levels"]] == [
        (130.0, None, ""), (1000.0, OK, ""), (1010.0, STALE, ""), (1012.0, ERROR, "NoAck"), (1022.0, STALE, "")]
    names = {status["name"] for status in rack.history_json(device)["statuses"]}
    assert {"thing: presence", "deadbus: plugin run", "fdcanusb DEAD: adapter"} <= names  # OS, host and plugin checks
    assert rack.history_json(f"{device}/can", status="nope")["statuses"] == []
    assert rack.history_json("host/nope") is None


def test_the_bar_covers_the_entity_and_each_child_row(rack):
    device = rack.layer.device_paths["usb:9-1"]
    answer = rack.bar_json(device, bins=10, window=60)
    assert (answer["from"], answer["to"], answer["bin_s"]) == (970.0, 1030.0, 6.0)
    rows = {row["path"]: row for row in answer["rows"]}
    assert list(rows) == [device, f"{device}/can"]
    assert rows[f"{device}/can"]["bar"] == [None] * 5 + [OK, STALE, ERROR, ERROR, STALE]
    assert rows[device]["basis"] == "own" and rows[device]["depth"] == 0
    # a pure grouping row has no records of its own: it shows what everything below it reported
    port = rack.bar_json("host/usb9", bins=10, window=60)["rows"]
    assert port[0]["basis"] == "below" and ERROR in port[0]["bar"]


def test_a_device_that_leaves_ends_its_history(rack):
    device = rack.layer.device_paths["usb:9-1"]
    rack.os.devices = standard_skeleton()  # unplugged at 1030
    rack.refresh()
    rack.clock.at = 1040.0
    [status] = rack.history_json(f"{device}/can")["statuses"]
    assert status["levels"][-1]["level"] is None and status["levels"][-1]["from"] == 1030.0
    presence = next(status for status in rack.history_json(device)["statuses"] if status["name"] == "thing: presence")
    assert (presence["levels"][-1]["from"], presence["levels"][-1]["level"]) == (1030.0, None)


# ----- the window: one app-wide setting ------------------------------------------------------------------------------
def test_durations_and_their_limits():
    assert [parse_duration(text) for text in (900, "900", "90s", "15m", "1h", "2.5 min")] == [900, 900, 90, 900, 3600, 150]
    for bad in ("soon", "-5", True, "15 parsecs"):
        with pytest.raises(SettingsError):
            parse_duration(bad)
    with pytest.raises(SettingsError):
        Settings(None, 10.0)  # under a minute


def test_the_window_comes_from_the_flag_then_the_file_then_the_default(tmp_path):
    file = tmp_path / SETTINGS_FILE
    assert (Settings(file).history_window_s, Settings(file).source) == (900.0, "default")
    Settings(file).set_history_window("10m")
    assert "history_window_s = 600" in file.read_text()
    assert (Settings(file).history_window_s, Settings(file).source) == (600.0, "file")
    flagged = Settings(file, 300.0)
    assert (flagged.history_window_s, flagged.source) == (300.0, "flag")
    assert "history_window_s = 600" in file.read_text()  # a run-time flag is never written
    file.write_text("history_window_s = 'soon'\n")
    broken = Settings(file)
    assert broken.history_window_s == 900.0 and "unreadable" in broken.note


def test_the_cli_flag_reaches_the_host(tmp_path, monkeypatch):
    from io_diagnostics.cli import _parser
    from io_diagnostics.host import make_host as make_real_host

    monkeypatch.setenv("IO_DIAGNOSTICS_CONFIG_DIR", str(tmp_path))
    args = _parser().parse_args(["serve", "--fixture", "F1", "--history-window", "5m"])
    assert args.history_window == "5m"
    host = make_real_host(fixture="F1", once=True, history_window_s=parse_duration(args.history_window))
    assert (host.settings.history_window_s, host.history.window_s, host.settings.source) == (300.0, 300.0, "flag")
    assert host.settings.path == tmp_path / SETTINGS_FILE


@pytest.fixture
def served(tmp_path):
    file = tmp_path / "config" / SETTINGS_FILE
    host = Host(plugins_dir=tmp_path / "none", clock=SetClock(5000.0), os_source=FakeOs(standard_skeleton()), settings_file=file)
    host.refresh()
    http = make_server(host, 0)
    serve_in_thread(http)
    yield host, file
    http.shutdown()
    http.server_close()


def call(host, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(host.url.rstrip("/") + path, data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_settings_are_read_by_anyone_and_written_only_by_the_page(served):
    host, file = served
    status, answer = call(host, "/api/v1/settings")
    assert status == 200 and answer["history_window_s"] == 900.0 and answer["file"] == str(file)
    status, answer = call(host, "/api/v1/settings", {"history_window_s": 600}, {"Content-Type": "application/json"})
    assert (status, answer["refused"]) == (403, "NotFromPage") and not file.exists()
    status, answer = call(host, "/api/v1/settings", {"history_window_s": "5m"}, PAGE)
    assert status == 200 and (answer["history_window_s"], answer["source"]) == (300.0, "page")
    assert host.history.window_s == 300.0 and "history_window_s = 300" in file.read_text()
    for bad in ({"history_window_s": 5}, {"history_window_s": "soon"}, {"colour": "red"}):
        status, answer = call(host, "/api/v1/settings", bad, PAGE)
        assert (status, answer["refused"]) == (400, "BadSetting"), bad
    assert call(host, "/api/v1/settings")[1]["history_window_s"] == 300.0


def test_history_endpoints_answer_and_refuse_bad_queries(served):
    host, _file = served
    assert call(host, "/api/v1/history?path=host")[0] == 200
    status, answer = call(host, "/api/v1/history/bar?path=host&bins=12&window=2m")
    assert status == 200 and (answer["bins"], answer["window_s"], len(answer["rows"][0]["bar"])) == (12, 120.0, 12)
    assert call(host, "/api/v1/history?path=host/nope")[0] == 404
    assert call(host, "/api/v1/history/bar?path=host&bins=many")[0] == 400
    assert call(host, "/api/v1/history?path=host&window=30s")[0] == 400  # under a minute
    assert call(host, "/api/v1/history?path=host&window=2h")[0] == 400  # beyond what the history keeps


def test_the_history_answers_match_the_doc_examples(rack):
    device = rack.layer.device_paths["usb:9-1"]
    answer, documented = rack.history_json(f"{device}/can"), example("`GET /api/v1/history?")
    same_keys(answer, documented, "history")
    same_keys(answer["statuses"][0], documented["statuses"][0], "history.statuses[]")
    same_keys(answer["statuses"][0]["levels"][0], documented["statuses"][0]["levels"][0], "levels[]")
    speed = next(status for status in rack.history_json(device)["statuses"] if status["measurements"])
    same_keys(speed["measurements"][0], documented["statuses"][0]["measurements"][0], "measurements[]")
    answer, documented = rack.bar_json(device, bins=10, window=60), example("`GET /api/v1/history/bar")
    same_keys(answer, documented, "bar")
    same_keys(answer["rows"][0], documented["rows"][0], "bar.rows[]")
    same_keys(rack.settings_json(), example("Settings"), "settings")
    assert (ROOT / "docs" / "api-v1.md").read_text().count("| `GET /api/v1/history") == 2
