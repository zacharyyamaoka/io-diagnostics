"""The page's one write, ``POST /api/v1/act`` (docs/api-v1.md "Actions from the page"), over a real server and a real
plugin subprocess that never touches hardware.

The rule: every click is a dry run first; a real run happens only with that dry run's token, from the page itself
(``Sec-Fetch-Site: same-origin`` and ``X-IO-Diagnostics: ui``), within the token's window, once. Every refusal is
checked against the plugin's own log: nothing but dry runs reached it. The real drivers never run here (the
hardware tripwire in test_host_support.py refuses them); the fixture host's own act is checked by argv only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from test_host_support import FakeOs, SetClock, read_log, standard_skeleton, usb_device

from io_diagnostics.host import Host, make_host
from io_diagnostics.server import NO_ACTIONS, ActGate, make_server, serve_in_thread

WIDGET = usb_device("9-1", "1234", "abcd", "W1", label="widget")
PAGE = {"Content-Type": "application/json", "X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin"}

# Like the moteus driver: the dry run names a token; the run is refused without it (ConfirmationRequired) and runs
# with it. It logs every act's argv and prints what a driver prints. Never touches hardware.
TOKEN_PLUGIN = r'''
import json, os, sys

args = sys.argv[1:]
verb = next(word for word in args if word in ("describe", "diagnose", "act"))
ACTIONS = [
    {"name": "check_comms", "title": "Check communication", "description": "ask who answers", "danger": "query",
     "scope": "device", "confirm": "Ask every id on {name}?", "kind": "touches"},
    {"name": "calibrate", "title": "Run calibration…", "description": "spins the rotor", "danger": "moves",
     "scope": "node", "confirm": "Calibrate {name}? The rotor spins.", "kind": "changes",
     "preconditions": [["joint_free", "the joint is free to spin"]]},
    {"name": "set_id", "title": "Change CAN id…", "description": "a new bus id", "danger": "changes",
     "scope": "node", "confirm": "Change the CAN id of CAN {id}?", "kind": "changes"},
]
if verb == "describe":
    print(json.dumps({"name": "tokens", "schema": "diagnostics-record/0", "actions": ACTIONS}))
    raise SystemExit(0)
if verb == "diagnose":
    print(json.dumps({"schema": "diagnostics-record/0", "record": "entity", "path": "", "name": "widget W1", "kind": "device",
                      "identity": {"serial_number": "W1"}, "actions": ACTIONS}))
    print(json.dumps({"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": "widget: ran", "path": ""}))
    raise SystemExit(0)
action = args[args.index("--action") + 1]
with open(os.environ["TOKEN_LOG"], "a") as log:
    log.write(json.dumps({"verb": "act", "argv": args}) + "\n")
token = "tok-" + action
if "--dry-run" in args:
    print(json.dumps({"record": "action-result", "action": action, "dry_run": True, "command": ["widget-tool", "--" + action],
                      "steps": [{"purpose": "do it", "runs": "subprocess", "argv": ["widget-tool", "--" + action]}],
                      "preconditions": [{"check": "joint_free", "text": "the joint is free to spin", "state": "the person confirms it"}],
                      "confirm": "Do " + action + "?", "token": token}))
elif "--confirm" not in args or args[args.index("--confirm") + 1] != token:
    print(json.dumps({"record": "event", "code": "ConfirmationRequired", "severity": "info", "message": "pass the token"}))
else:
    print(json.dumps({"record": "event", "code": "ActionCompleted", "severity": "info", "message": action + " ran"}))
'''


class Clock:
    def __init__(self):
        self.at = 100.0

    def __call__(self):
        return self.at


@pytest.fixture()
def rig(tmp_path):
    log = tmp_path / "acts.jsonl"
    script = tmp_path / "token_plugin.py"
    script.write_text(TOKEN_PLUGIN)
    (tmp_path / "plugins.d").mkdir()
    (tmp_path / "plugins.d" / "tokens.toml").write_text(
        f'name = "tokens"\ncommand = ["{{python}}", "{script}"]\ntimeout_s = 20\nperiod_s = 30\nmatches = ["usb:1234:*"]\n'
        f'env = {{ TOKEN_LOG = "{log}" }}\n')
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(), os_source=FakeOs(standard_skeleton(WIDGET)))
    host.run_all_once()
    clock = Clock()
    http = make_server(host, 0, gate=ActGate(window_s=300.0, now=clock))
    serve_in_thread(http)
    yield {"url": host.url, "path": host.layer.device_paths["usb:9-1"], "log": log, "clock": clock}
    http.shutdown()


def post(rig, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(rig["url"] + "api/v1/act", data=json.dumps(body).encode(), method="POST",
                                     headers=PAGE if headers is None else headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def runs(log) -> list[list[str]]:
    """The argv of every act that was NOT a dry run."""

    return [entry["argv"] for entry in read_log(log) if "--dry-run" not in entry["argv"]]


def dry(rig, action: str = "calibrate", node: int | None = 3) -> dict:
    status, answer = post(rig, {"path": rig["path"], "action": action, "node": node, "dry_run": True})
    assert status == 200 and answer["dry_run"] is True and answer["token"], answer
    return answer


def test_a_dry_run_shows_what_would_run_and_issues_a_token(rig):
    answer = dry(rig)
    result = answer["result"]
    assert result["argv"][-8:] == ["act", "--device", "usb:9-1", "--action", "calibrate", "--node", "3", "--dry-run"]
    assert result["action_result"]["command"] == ["widget-tool", "--calibrate"] and result["danger"] == "moves"
    assert answer["window_s"] == 300.0 and runs(rig["log"]) == []


def test_a_run_needs_the_page_headers(rig):
    token = dry(rig)["token"]
    body = {"path": rig["path"], "action": "calibrate", "node": 3, "token": token}
    for headers, code in [({"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}, "NotFromPage"),
                          ({"Content-Type": "application/json", "X-IO-Diagnostics": "ui"}, "NotSameOrigin"),
                          ({**PAGE, "Sec-Fetch-Site": "cross-site"}, "NotSameOrigin"),
                          ({**PAGE, "Origin": "http://evil.example"}, "NotSameOrigin"),
                          ({**PAGE, "Content-Type": "text/plain"}, "NotJson")]:
        status, answer = post(rig, body, headers)
        assert (status, answer["refused"]) == (403, code), (headers, answer)
        assert answer["error"]  # a plain reason
    assert runs(rig["log"]) == []
    status, answer = post(rig, body)  # the token was never spent by a refused request
    assert status == 200 and answer["result"]["outcome"] == "ok", answer


def test_a_run_needs_its_own_fresh_unused_token(rig):
    base = {"path": rig["path"], "action": "calibrate", "node": 3}
    assert post(rig, base)[1]["refused"] == "NoDryRun"
    assert post(rig, {**base, "token": "made-up"})[1]["refused"] == "NoDryRun"
    other = dry(rig, "check_comms", None)["token"]
    assert post(rig, {**base, "token": other})[1]["refused"] == "TokenMismatch"  # another action's token
    wrong_node = dry(rig)["token"]
    assert post(rig, {**base, "node": 4, "token": wrong_node})[1]["refused"] == "TokenMismatch"
    stale = dry(rig)["token"]
    rig["clock"].at += 301  # a dry run older than the window
    status, answer = post(rig, {**base, "token": stale})
    assert (status, answer["refused"]) == (403, "TokenExpired") and "dry run" in answer["error"]
    assert runs(rig["log"]) == []
    fresh = dry(rig)["token"]
    status, answer = post(rig, {**base, "token": fresh})
    assert status == 200 and answer["result"]["records"][-1]["code"] == "ActionCompleted"
    # the plugin got ITS token (from the dry run), never the page's
    assert runs(rig["log"]) == [["act", "--device", "usb:9-1", "--action", "calibrate", "--node", "3", "--confirm", "tok-calibrate"]]
    assert post(rig, {**base, "token": fresh})[1]["refused"] == "NoDryRun"  # single use: a replay is refused
    assert len(runs(rig["log"])) == 1


def test_set_id_carries_its_new_id_and_the_token_binds_it(rig):
    base = {"path": rig["path"], "action": "set_id", "node": 3}
    status, answer = post(rig, {**base, "new_id": 7, "dry_run": True})
    assert status == 200 and answer["token"] and answer["result"]["new_id"] == 7, answer
    assert answer["result"]["argv"][-10:] == ["act", "--device", "usb:9-1", "--action", "set_id", "--node", "3", "--new-id", "7",
                                              "--dry-run"]
    for other in (9, None):  # a token issued for CAN 3 -> CAN 7 runs neither CAN 3 -> CAN 9 nor a set_id without an id
        token = post(rig, {**base, "new_id": 7, "dry_run": True})[1]["token"]  # a mismatch spends the token
        status, refused = post(rig, {**base, "new_id": other, "token": token})
        assert (status, refused.get("refused")) == (403, "TokenMismatch") and "new id 7" in refused["error"], refused
    assert runs(rig["log"]) == []
    status, answer = post(rig, {**base, "new_id": 7, "dry_run": True})
    status, ran = post(rig, {**base, "new_id": 7, "token": answer["token"]})
    assert status == 200 and ran["result"]["records"][-1]["code"] == "ActionCompleted", ran
    assert runs(rig["log"]) == [["act", "--device", "usb:9-1", "--action", "set_id", "--node", "3", "--new-id", "7",
                                 "--confirm", "tok-set_id"]]
    status, bad = post(rig, {**base, "new_id": "7", "dry_run": True})
    assert (status, bad["refused"]) == (400, "BadRequest") and "new_id" in bad["error"]


def test_a_query_runs_with_one_more_click(rig):
    token = dry(rig, "check_comms", None)["token"]
    status, answer = post(rig, {"path": rig["path"], "action": "check_comms", "node": None, "token": token})
    assert status == 200 and answer["result"]["outcome"] == "ok" and answer["result"]["records"][-1]["message"] == "check_comms ran"


def test_bad_requests_and_every_other_write_are_refused(rig):
    status, answer = post(rig, {"path": rig["path"], "action": "calibrate", "node": "3", "dry_run": True})
    assert (status, answer["refused"]) == (400, "BadRequest")
    status, answer = post(rig, {"path": "host/nope", "action": "calibrate", "node": None, "dry_run": True})
    assert status == 200 and answer["result"]["refused"] == "NoSuchEntity" and answer["token"] is None  # no token
    for method, url in [("POST", "api/v1/state"), ("PUT", "api/v1/act"), ("DELETE", "api/v1/act")]:
        request = urllib.request.Request(rig["url"] + url, data=b"{}", method=method, headers=PAGE)
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=10)
        assert refused.value.code == 405 and json.loads(refused.value.read())["error"] == NO_ACTIONS
    with pytest.raises(urllib.error.HTTPError) as get:
        urllib.request.urlopen(rig["url"] + "api/v1/act", timeout=10)
    assert get.value.code == 405
    assert runs(rig["log"]) == []


def test_the_fixture_host_dry_runs_the_rack_vocabulary_without_running_anything():
    """F1's replayed moteus side answers a dry run with the argv a live driver would run, and the server never hands
    a person's run to anything but the fixture plugin (which never touches hardware)."""

    host = make_host(fixture="F1", once=True)
    host.run_all_once()
    assert [plugin["name"] for plugin in host.plugins()["plugins"]] == ["v6_scenario"]
    http = make_server(host, 0)
    serve_in_thread(http)
    try:
        state = json.loads(urllib.request.urlopen(host.url + "api/v1/state", timeout=10).read())
        can15 = next(path for path in _paths(state["tree"]) if path.endswith("/can/15"))
        rig = {"url": host.url}
        status, answer = post(rig, {"path": can15, "action": "calibrate", "node": 15, "dry_run": True})
        result = answer["result"]
        assert status == 200 and result["outcome"] == "ok" and answer["token"], answer
        assert result["argv"][-8:] == ["act", "--device", "usb:1-3", "--action", "calibrate", "--node", "15", "--dry-run"]
        assert result["action_result"]["command"][:4] == ["moteus_tool", "--target", "15", "--calibrate"]
        assert result["danger"] == "moves" and result["action_result"]["token"]
    finally:
        http.shutdown()


def _paths(node):
    yield node["path"]
    for child in node["children"]:
        yield from _paths(child)
