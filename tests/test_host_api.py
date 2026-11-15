"""The server answers exactly what docs/api-v1.md promises: every JSON example in the doc is parsed and the live
server's objects must carry exactly its keys (so a field added without documenting it, or documented without
existing, fails here). Served from a fixture host (V6's F1 / F2 frames through the real plugin runner)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import pytest
from test_host_support import ROOT, node_by_name

from io_diagnostics.host import make_host
from io_diagnostics.server import make_server, serve_in_thread
from io_diagnostics.tray import chip_model

DOC = (ROOT / "docs" / "api-v1.md").read_text()


def doc_examples() -> dict[str, list]:
    """``{section heading: [parsed json blocks]}`` for every ```json block in the doc."""

    sections: dict[str, list] = {}
    heading = ""
    for part in re.split(r"(?m)^(#{2,3} .*)$", DOC):
        if re.match(r"^#{2,3} ", part):
            heading = part.strip("# ").strip()
            continue
        for block in re.findall(r"```json\n(.*?)\n```", part, re.S):
            sections.setdefault(heading, []).append(json.loads(block))
    return sections


EXAMPLES = doc_examples()


def example(prefix: str) -> dict:
    return next(blocks[0] for heading, blocks in EXAMPLES.items() if heading.startswith(prefix))


@pytest.fixture(scope="module")
def server():
    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    http = make_server(host, 0)
    serve_in_thread(http)
    yield host
    http.shutdown()
    http.server_close()


def get(host, path: str):
    with urllib.request.urlopen(host.url.rstrip("/") + path, timeout=10) as response:
        body = response.read()
        return response.status, response.headers.get("Content-Type", ""), body


def get_json(host, path: str) -> dict:
    status, kind, body = get(host, path)
    assert status == 200 and kind.startswith("application/json")
    return json.loads(body)


def same_keys(actual: dict, documented: dict, where: str) -> None:
    assert set(actual) == set(documented), f"{where}: undocumented {set(actual) - set(documented)}, missing {set(documented) - set(actual)}"


def test_every_documented_endpoint_answers(server):
    endpoints = re.findall(r"^\| `GET (/api/v1/[a-z]+)", DOC, re.M)
    assert endpoints == ["/api/v1/state", "/api/v1/node", "/api/v1/activity", "/api/v1/events", "/api/v1/live", "/api/v1/records", "/api/v1/plugins",
                         "/api/v1/health", "/api/v1/history", "/api/v1/history", "/api/v1/history", "/api/v1/checks", "/api/v1/settings", "/api/v1/runs", "/api/v1/values",
                         "/api/v1/frames", "/api/v1/arrangements", "/api/v1/dashboards",
                         "/api/v1/diagnostics", "/api/v1/diagnostics"]  # history and history/bar (2026-10-23); values, frames, arrangements, live and dashboards (2026-10-24); history/tree and checks (2026-10-27); diagnostics and diagnostics/config (2026-10-28)
    for endpoint in endpoints:
        query = "?path=host" if endpoint in ("/api/v1/node", "/api/v1/history") else ""
        if endpoint in ("/api/v1/events", "/api/v1/live"):  # a stream: its status and first message, then hang up
            with urllib.request.urlopen(server.url.rstrip("/") + endpoint, timeout=10) as response:
                assert response.status == 200 and response.headers["Content-Type"].startswith("text/event-stream")
                assert response.readline().startswith(b"retry: ")
            continue
        if endpoint in ("/api/v1/values", "/api/v1/frames"):  # an entity a plugin describes; no stream yet, so no frame
            device = "host/usb1/port-1-3/usb-1-3"
            if endpoint.endswith("values"):
                assert get(server, f"{endpoint}?path={device}")[0] == 200
            else:
                with pytest.raises(urllib.error.HTTPError) as refused:
                    get(server, f"{endpoint}?path={device}&stream=color")
                assert refused.value.code == 404
            continue
        assert get(server, endpoint + query)[0] == 200


def test_state_matches_the_doc_example(server):
    state, documented = get_json(server, "/api/v1/state"), example("`GET /api/v1/state`")
    same_keys(state, documented, "state")
    for key in ("clock", "fixture", "host", "summary", "chip"):
        same_keys(state[key], documented[key], f"state.{key}")
    same_keys(state["summary"]["attention"][0], documented["summary"]["attention"][0], "summary.attention[]")
    assert state["api"] == "io-diagnostics/1" and state["schema"] == "diagnostics-record/0"
    assert state["clock"]["rank"] == ["ok", "warn", "unknown", "stale", "error"]
    assert state["fixture"]["name"] == "F1" and state["clock"]["time"] == "18:11:50"


def test_every_tree_node_has_exactly_the_documented_fields(server):
    state, documented = get_json(server, "/api/v1/state"), example("The node")
    table = DOC.split("### The node", 1)[1].split("## `GET /api/v1/node", 1)[0]
    in_table = {name for row in re.findall(r"^\| ([^|]+) \|", table, re.M) for name in re.findall(r"`([a-z_]+)`", row)}
    stack, seen = [state["tree"]], 0
    while stack:
        node = stack.pop()
        seen += 1
        # `rerun` is on the nodes a recorder could show only (docs/api-v1.md "The rerun field"): optional, and its own shape is checked below
        same_keys({key: value for key, value in node.items() if key != "rerun"}, documented, node["path"])
        if "rerun" in node:
            assert set(node["rerun"]) in ({"url", "label", "live"}, {"absent"}, {"absent", "start_cmd"}), (node["path"], node["rerun"])
        same_keys(node["rollup"], documented["rollup"], f"{node['path']}.rollup")
        same_keys(node["own"], documented["own"], f"{node['path']}.own")
        if node["hint"]:
            same_keys(node["hint"], documented["hint"], f"{node['path']}.hint")
        assert node["level"] in (None, "ok", "warn", "unknown", "stale", "error")
        assert node["icon"] in re.findall(r"`([a-z]+)`", DOC.split("| `icon` |", 1)[1].split("\n", 1)[0])
        stack.extend(node["children"])
    assert set(documented) - {"children"} <= in_table  # every field is explained in the table
    assert seen == state["summary"]["entities"]


def test_node_detail_matches_the_doc_example(server):
    can15 = node_by_name(get_json(server, "/api/v1/state")["tree"], "CAN 15")
    detail, documented = get_json(server, f"/api/v1/node?path={can15['path']}"), example("`GET /api/v1/node")
    same_keys(detail, documented, "node")
    same_keys(detail["ancestors"][0], documented["ancestors"][0], "ancestors[]")
    for check in detail["checks"]:
        same_keys(check, documented["checks"][0], f"checks[{check['name']}]")
        for measurement in check["measurements"]:
            same_keys(measurement, documented["checks"][0]["measurements"][0], "measurements[]")
    assert detail["actions"], "CAN 15 registers clear_faults / reset / update_firmware"
    for action in detail["actions"]:
        same_keys(action, documented["actions"][0], f"actions[{action['name']}]")
        assert action["executable"] is False and action["state"] in ("ready", "blocked", "nothing_to_do", "unknown")
        for precondition in action["preconditions"]:
            same_keys(precondition, documented["actions"][0]["preconditions"][0], "preconditions[]")
    assert detail["entity"]["record"] == "entity" and detail["node"]["children"] == [f"{can15['path']}/driver", f"{can15['path']}/motor"]


def test_activity_records_plugins_and_health_match_the_doc(server):
    activity, documented = get_json(server, "/api/v1/activity"), example("`GET /api/v1/activity")
    same_keys(activity, documented, "activity")
    for key in ("events", "faults", "link_health"):
        assert activity[key], key
        for item in activity[key]:
            same_keys(item, documented[key][0], f"activity.{key}[]")
    codes = {event["code"] for event in activity["events"]}
    assert {"Booted", "Plugged", "ReEnumerated", "FaultConfirmed", "BusErrorPassive"} <= codes
    records, documented = get_json(server, "/api/v1/records"), example("`GET /api/v1/records`")
    same_keys(records, documented, "records")
    assert {record["record"] for record in records["statuses"]} == {"status"}
    assert all(record["schema"] == "diagnostics-record/0" for record in records["entities"])
    plugins, documented = get_json(server, "/api/v1/plugins"), example("`GET /api/v1/plugins`")
    same_keys(plugins, documented, "plugins")
    plugin = plugins["plugins"][0]
    same_keys(plugin, documented["plugins"][0], "plugins[]")
    same_keys(plugin["jobs"][0], documented["plugins"][0]["jobs"][0], "jobs[]")
    same_keys(plugin["jobs"][0]["last_run"], documented["plugins"][0]["jobs"][0]["last_run"], "last_run")
    health = get_json(server, "/api/v1/health")
    assert set(health) == {"ok", "api", "mode", "uptime_s"} and health["mode"] == "fixture"


def test_errors_and_the_page(server):
    with pytest.raises(urllib.error.HTTPError) as missing:
        get(server, "/api/v1/node?path=host/nope")
    assert missing.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as unknown:
        get(server, "/api/v1/nothing")
    assert unknown.value.code == 404
    request = urllib.request.Request(server.url + "api/v1/state", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=10)
    assert refused.value.code == 405 and "never execute" in json.loads(refused.value.read())["error"]
    status, kind, body = get(server, "/")
    assert status == 200 and kind.startswith("text/html") and b"app.js" in body
    with pytest.raises(urllib.error.HTTPError) as outside:
        get(server, "/../../pyproject.toml")  # resolves to the real checkout file: must not be served
    assert outside.value.code == 404


def test_actions_carry_their_gate_state_and_never_execute():
    host = make_host(fixture="F2", once=True)  # tview holds 272813EF since 18:24:46
    host.run_all_once()
    can15 = node_by_name(host.state()["tree"], "CAN 15")
    actions = {action["name"]: action for action in host.node(can15["path"])["actions"]}
    assert actions["clear_faults"]["state"] == "blocked" and "tview" in actions["clear_faults"]["reason"]
    assert actions["update_firmware"]["state"] == "nothing_to_do" and actions["update_firmware"]["reason"].startswith("blocked") is False
    port_free = next(item for item in actions["reset_board"]["preconditions"] if item["check"] == "port_free")
    assert port_free["state"] == "fail" and "640623" in port_free["detail"]
    assert "{" not in actions["reset_board"]["confirm"]  # {id} / {serial} filled by the host


def test_chip_model_is_the_appindicator_shape():
    summary = {"devices": 10, "attention": [
        {"path": "a/can", "name": "28B3A25D CAN bus", "level": "error", "message": "no ACK", "device": {"path": "a", "name": "fdcanusb"}},
        {"path": "b", "name": "D405 ·6036", "level": "warn", "message": "moved 28 px", "device": {"path": "b", "name": "D405 ·6036"}}]}
    chip = chip_model(summary, [{"time": "18:11:42", "message": "fdcanusb 28B3A25D plugged on 1-5"}], "http://127.0.0.1:8812/")
    assert chip["label"] == "IO 2 ⚠" and chip["icon"] == "attention"
    assert all(set(item) <= {"text", "enabled", "children", "action", "sep"} for item in chip["menu"])  # contract/indicator.py
    texts = [item.get("text", "") for item in chip["menu"]]
    assert "✖  fdcanusb › 28B3A25D CAN bus  —  no ACK" in texts and "▲  D405 ·6036  —  moved 28 px" in texts
    assert {"text": "Open dashboard", "action": "open:http://127.0.0.1:8812/"} in chip["menu"]
    fault = {"time": "18:11:50", "code": "FaultConfirmed", "kind": "fault confirmed", "name": "D405 ·6036",
             "message": "CameraMoved confirmed on host/usb6/port-6-1/usb-6-1: moved 28 px", "args": {"fault": "CameraMoved"}}
    long = {"time": "18:12:00", "code": "Plugged", "message": "x" * 300}
    rows = [item.get("text", "") for item in chip_model(summary, [fault, long], "")["menu"]]
    assert "   18:11   D405 ·6036: fault confirmed (CameraMoved)" in rows
    assert any(row.endswith("…") and len(row) == 110 for row in rows)  # explicit ellipsis, never a silent clip
    quiet = chip_model({"devices": 3, "attention": []}, [], "")
    assert quiet["label"] == "IO ✓" and quiet["icon"] == "ok" and not any("action" in item and item["action"].startswith("open:") for item in quiet["menu"])
