"""The stock diagnostics' drill-in API (docs/api-v1.md "Stock diagnostics"): ``GET /api/v1/diagnostics`` (the registry, each config dataclass as
a schema), ``GET /api/v1/diagnostics/config?path=`` (an entity's configured diagnostics, effective values and where each came from) and
``POST /api/v1/diagnostics/config`` (one value: validated against the dataclass field and the Limits ordering, written to diagnostics.toml
atomically, page-only). Over the state fixture's joint (tests/fixtures/plugins-state), a real subprocess that never touches hardware."""

from __future__ import annotations

import json
import tomllib
import urllib.error
import urllib.request

import pytest

from io_diagnostics import stock_diagnostics as sd
from io_diagnostics.server import make_server, serve_in_thread

from test_stock_diagnostics import Rig

PAGE = {"X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}


@pytest.fixture
def served(tmp_path):
    rig = Rig(tmp_path)
    http_server = make_server(rig.host, 0)
    serve_in_thread(http_server)
    rig.base = rig.host.url.rstrip("/")
    rig.file = rig.config / sd.DIAGNOSTICS_FILE
    yield rig
    http_server.shutdown()
    http_server.server_close()
    rig.host.stop()


def get(rig, path: str):
    with urllib.request.urlopen(rig.base + path, timeout=10) as response:
        return response.status, json.loads(response.read())


def post(rig, body: dict, headers: dict | None = None):
    request = urllib.request.Request(rig.base + "/api/v1/diagnostics/config", data=json.dumps(body).encode(), method="POST",
                                     headers=PAGE if headers is None else headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def config_of(rig) -> dict:
    status, body = get(rig, f"/api/v1/diagnostics/config?path={rig.board}")
    assert status == 200
    return {item["id"]: item for item in body["diagnostics"]}


def field(item: dict, name: str) -> dict:
    return next(row for row in item["fields"] if row["field"] == name)


def test_the_registry_lists_every_diagnostic_with_its_config_schema(served):
    status, body = get(served, "/api/v1/diagnostics")
    assert status == 200 and body["numpy"] is True and body["beat_s"] == 0.5 and body["file"] == str(served.file)
    by_id = {item["id"]: item for item in body["diagnostics"]}
    from robot_diagnostics.catalog import PRIMITIVES
    implemented = {name for name, primitive in PRIMITIVES.items() if primitive.implemented}
    assert set(by_id) == implemented and all(item["kind"] == "stock" for item in by_id.values())
    box = by_id["stock.box"]
    assert box["title"] == "box check" and box["config_type"] == "BoxConfig" and "BoxChartThresholds" in box["doc"]
    assert box["part_types"] == sorted(box["part_types"]) and "bldc_driver" in box["part_types"]
    rows = {row["field"]: row for row in box["config"]}
    assert rows["max.warn_high"] == {"field": "max.warn_high", "type": "float|null", "default": None, "unit": "Cel", "doc": "", "group": "max"}
    assert rows["headline"]["type"] == "str" and rows["headline"]["default"] == "max" and "headline" in rows["headline"]["doc"]
    rate = {row["field"]: row for row in by_id["stock.sample_rate"]["config"]}
    assert rate["tolerance"]["type"] == "float" and rate["tolerance"]["default"] == 0.1 and "FrequencyStatus" in rate["tolerance"]["doc"]
    assert {row["field"]: row["type"] for row in by_id["stock.fault_codes"]["config"]}["fault_codes"] == "tuple"


def test_an_entitys_diagnostics_with_their_values_and_where_each_came_from(served):
    served.ready()
    served.host.run_stock_diagnostics()
    item = config_of(served)["driver.board_temp"]
    assert item["diagnostic"] == "stock.box" and item["title"] == "board temperature" and item["kind"] == "stock"
    assert item["match"] == {"path": "", "model_id": "mjbots-moteus-*", "part": ""} and item["window_s"] == 2.0 and item["state"] == "motor"
    assert item["catalog"]["part"] == "bldc_driver" and item["catalog"]["provenance"] == "his code"
    assert (field(item, "max.warn_high")["value"], field(item, "max.warn_high")["source"]) == (60.0, "toml")
    assert (field(item, "max.warn_low")["value"], field(item, "max.warn_low")["source"]) == (None, "toml")  # its Limits came from the file
    assert (field(item, "mean.warn_high")["value"], field(item, "mean.warn_high")["source"]) == (None, "default")
    assert (field(item, "headline")["value"], field(item, "headline")["source"]) == ("max", "default")
    assert item["status"]["source"] == "robotdx.diagnostics" and item["status"]["effective_level"] == "ok"
    assert item["window"]["window_s"] == 2.0
    with pytest.raises(urllib.error.HTTPError) as missing:
        get(served, "/api/v1/diagnostics/config?path=host/nothing")
    assert missing.value.code == 404


def test_a_valid_value_is_written_atomically_and_read_back(served):
    status, answer = post(served, {"path": served.board, "id": "driver.board_temp", "field": "max.warn_high", "value": 30})
    assert status == 200 and answer["state"] == "accepted" and answer["ok"] is True and answer["saved"] is True
    assert answer["value"] == 30.0 and answer["source"] == "edited" and answer["file"] == str(served.file)
    written = tomllib.loads(served.file.read_text())
    table = next(t for t in written["diagnostic"] if t["id"] == "driver.board_temp")
    assert table["config"]["max"] == {"warn_high": 30.0, "error_high": 70.0} and table["edited"] == ["max.warn_high"]
    assert [t["id"] for t in written["diagnostic"]] == ["driver.board_temp", "driver.faults"]  # the whole set, the other binding kept
    assert not list(served.config.glob(".diagnostics.toml.*"))  # no temporary file left behind
    assert (field(config_of(served)["driver.board_temp"], "max.warn_high")["source"]) == "edited"
    # the next beat uses it: the fixture's 38 .. 44 Cel is now above the warn limit
    served.ready()
    assert served.mine()["board temperature"].level == "warn"
    status, answer = post(served, {"path": served.board, "id": "driver.faults", "field": "timeout_is_fault", "value": False})
    assert answer["state"] == "accepted" and answer["value"] is False


@pytest.mark.parametrize("body, reason", [
    ({"field": "max.warn_high", "value": 80}, "error_low <= warn_low <= warn_high <= error_high"),  # above its own error limit
    ({"field": "max.warn_high", "value": "hot"}, "not a number"),
    ({"field": "max.warn_high", "value": True}, "not a number"),
    ({"field": "headline", "value": 3}, "not text"),
    ({"field": "skip_prelude", "value": "yes"}, "not true or false"),
    ({"field": "nothing", "value": 1}, "has no field 'nothing'"),
])
def test_an_invalid_value_is_rejected_with_its_reason_and_nothing_is_written(served, body, reason):
    before = served.file.read_text()
    status, answer = post(served, {"path": served.board, "id": "driver.board_temp", **body})
    assert status == 200 and answer["state"] == "rejected" and answer["ok"] is False and reason in answer["message"]
    assert served.file.read_text() == before


def test_reset_puts_the_dataclass_default_back(served):
    post(served, {"path": served.board, "id": "driver.board_temp", "field": "headline", "value": "mean"})
    status, answer = post(served, {"path": served.board, "id": "driver.board_temp", "field": "headline", "reset": True})
    assert answer["state"] == "accepted" and answer["value"] == "max" and answer["source"] == "default" and answer["reset"] == "default"
    table = next(t for t in tomllib.loads(served.file.read_text())["diagnostic"] if t["id"] == "driver.board_temp")
    assert "headline" not in table["config"] and "edited" not in table


def test_the_post_is_the_pages_only_and_names_a_configured_diagnostic(served):
    body = {"path": served.board, "id": "driver.board_temp", "field": "max.warn_high", "value": 50}
    assert post(served, body, {"Content-Type": "application/json"})[0] == 403  # no page header
    assert post(served, body, {**PAGE, "Sec-Fetch-Site": "cross-site"})[1]["refused"] == "NotSameOrigin"
    assert post(served, body, {**PAGE, "Origin": "http://evil.example"})[1]["refused"] == "NotSameOrigin"
    assert post(served, {**body, "path": "host/nothing"})[0] == 404
    status, answer = post(served, {**body, "id": "driver.nope"})
    assert status == 200 and answer["state"] == "rejected" and answer["refused"] == "NoSuchDiagnostic"
    assert post(served, {"path": served.board, "id": "driver.board_temp", "field": "max.warn_high"})[0] == 400  # no value, no reset
    assert "max = { warn_high = 60.0, error_high = 70.0 }" in served.file.read_text()


def test_a_host_with_no_file_of_the_persons_keeps_the_change_for_this_run_and_says_not_saved(tmp_path):
    runner = sd.StockDiagnostics(None)
    answer = runner.set_field("driver.board_temp", "max.warn_high", 50)
    assert answer["state"] == "accepted" and answer["saved"] is False and "not written" in answer["note"]
    _spec, config = runner.build_config(next(b for b in runner.bindings if b.id == "driver.board_temp"))
    assert config.max.warn_high == 50.0
