"""Composites on a host (docs/composition-v1.md): the file read into rows under the Computer, the worst-child rollup counted once, a
composite plugin run like a driver (its environment, its records on the composite, a child's value linked), the problems of the file as
a status on the Computer, and batch actions through the runs registry (one dry run, one confirm, N runs queued by their group).

Every process here is the state fixture (tests/fixtures/plugins-state): joints that stream State and only ever dry-run, and an "arm" composite
plugin; nothing touches hardware. Two fdcanusb adapters (AAAA1111, BBBB2222) report can/1..can/3 each; can/1 of each is in fault (the
fixture's CAN 1: driver check), so every arm has one real cause below it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_host_support import FakeOs, standard_skeleton, usb_device

from io_diagnostics.host import Clock, Host
from io_diagnostics.server import make_server, serve_in_thread

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plugins-state"
A, B, CAM = "AAAA1111", "BBBB2222", "CAM00001"
PAGE = {"X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
LAB = "host/composite-lab"
ARM1, ARM2 = "host/composite-lab-robot-1-arm", "host/composite-lab-robot-2-arm"


def wait_for(check, *, timeout=10.0, what="the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def joints(serial: str, ids=(1, 2, 3)) -> str:
    return "\n".join(f'  "usb:0483:5740:{serial}#can/{n}",' for n in ids)


def lab(extra: str = "", *, arm_extra: str = "") -> str:
    return f'''
[[composite]]
id = "lab"
kind = "rack"
title = "Lab"
children = ["lab/robot-1", "lab/robot-2"]

[[composite]]
id = "lab/robot-1"
kind = "robot"
title = "Robot 1"
children = ["lab/robot-1/arm"]

[[composite]]
id = "lab/robot-2"
kind = "robot"
title = "Robot 2"
children = ["lab/robot-2/arm"]

[[composite]]
id = "lab/robot-1/arm"
kind = "arm"
title = "Arm 1"
plugin = "state_arm"
expects = {{ tracking_error_rev = 0.5, links_m = [0.3, 0.25, 0.1] }}
{arm_extra}children = [
{joints(A)}
]

[[composite]]
id = "lab/robot-2/arm"
kind = "arm"
title = "Arm 2"
plugin = "state_arm"
children = [
{joints(B)}
]
{extra}'''


class CompRig:
    """Two fdcanusb adapters of three joints each, an arm plugin, a composition file; ``run_all_once`` twice (the arm needs its joints' State)."""

    def __init__(self, tmp_path: Path, composites: str | None = None, *, exclusive="rack-bus", act_s="0", camera=False, arm_plugin=True,
                 rigs="", grace_s=1.0, arm_matches=("composite:arm",)):
        self.tmp = tmp_path
        self.state_log, self.arm_log = tmp_path / "state.log", tmp_path / "arm.log"
        directory = tmp_path / "plugins.d"
        directory.mkdir()
        env = {"STATE_LOG": str(self.state_log), "STATE_PERIOD_S": "0.05", "STATE_NODES": "3", "STATE_ACT_S": act_s}
        group = f'exclusive = "{exclusive}"\n' if exclusive else ""
        (directory / "state_joint.toml").write_text(
            f'name = "state_joint"\ncommand = ["{{python}}", "{FIXTURES}/state_plugin.py", "--role", "joint"]\ntimeout_s = 10\nperiod_s = 30\n'
            f'matches = ["usb:0483:5740"]\n{group}env = {{ {", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items())} }}\n')
        if camera:
            (directory / "state_camera.toml").write_text(
                f'name = "state_camera"\ncommand = ["{{python}}", "{FIXTURES}/state_plugin.py", "--role", "camera"]\ntimeout_s = 10\n'
                f'period_s = 30\nmatches = ["usb:8086:0b5b"]\nenv = {{ STATE_LOG = {json.dumps(str(self.state_log))} }}\n')
        if arm_plugin:
            (directory / "state_arm.toml").write_text(
                f'name = "state_arm"\ncommand = ["{{python}}", "{FIXTURES}/arm_plugin.py"]\ntimeout_s = 10\nperiod_s = 30\n'
                f'matches = {json.dumps(list(arm_matches))}\nenv = {{ ARM_LOG = {json.dumps(str(self.arm_log))}, ARM_PERIOD_S = "0.05" }}\n')
        self.expected = tmp_path / "expected.toml"
        self.expected.write_text("version = 1\n" + rigs + (lab() if composites is None else composites))
        devices = [usb_device("9-1", "0483", "5740", A, label="fdcanusb"), usb_device("9-2", "0483", "5740", B, label="fdcanusb")]
        if camera:
            devices.append(usb_device("9-3", "8086", "0b5b", CAM, label="camera"))
        self.host = Host(plugins_dir=directory, clock=Clock(), os_source=FakeOs(standard_skeleton(*devices)), expected_file=self.expected,
                         watch_grace_s=grace_s, watch_tick_s=0.1, follow_backoff_s=(0.2, 0.4), frames_dir=tmp_path / "frames",
                         ledger_file=tmp_path / "ledger.json")
        self.host.run_all_once()
        self.host.run_all_once()

    def adapter(self, serial: str) -> str:
        return next(path for path, entity in self.host.view().merged.entities.items() if entity.hardware_id == f"usb:0483:5740:{serial}")

    def joint(self, serial: str, number: int) -> str:
        return f"{self.adapter(serial)}/can/{number}"

    def log(self, path: Path, verb: str) -> list[dict]:
        if not path.exists():
            return []
        return [row for row in map(json.loads, path.read_text().splitlines()) if row["verb"] == verb]

    def checks(self, path: str) -> dict:
        return {check["name"]: check for check in self.host.node(path)["checks"]}

    def stop(self) -> None:
        self.host.stop()


@pytest.fixture
def rig(tmp_path):
    made = CompRig(tmp_path)
    yield made
    made.stop()


def walk(node: dict):
    yield node
    for child in node["children"]:
        yield from walk(child)


# ----- the tree and the rollup ---------------------------------------------------------------------------------------
def test_composites_are_rows_under_the_computer_nested_to_any_depth(rig):
    state = rig.host.state()
    nodes = {node["path"]: node for node in walk(state["tree"])}
    assert [child["path"] for child in nodes["host"]["children"]][:1] == [LAB]  # right under the Computer, before the OS groups
    assert [child["path"] for child in nodes[LAB]["children"]] == ["host/composite-lab-robot-1", "host/composite-lab-robot-2"]
    assert [child["path"] for child in nodes["host/composite-lab-robot-1"]["children"]] == [ARM1]
    arm = nodes[ARM1]
    assert (arm["kind"], arm["name"], arm["id_text"], arm["parent"], arm["edge"]) == ("composite", "Arm 1", "arm", "host/composite-lab-robot-1", "via")
    assert nodes[LAB]["parent"] == "host" and nodes[LAB]["id_text"] == "rack"
    composition = state["composition"]
    assert composition["problems"] == [] and [c["id"] for c in composition["composites"]] == [
        "lab", "lab/robot-1", "lab/robot-1/arm", "lab/robot-2", "lab/robot-2/arm"]
    arm_json = next(c for c in composition["composites"] if c["id"] == "lab/robot-1/arm")
    assert arm_json["members"] == [rig.joint(A, 1), rig.joint(A, 2), rig.joint(A, 3)] and arm_json["leaves"] == 3 and arm_json["plugin"] == "state_arm"
    assert next(c for c in composition["composites"] if c["id"] == "lab")["leaves"] == 6


def test_three_levels_roll_the_worst_child_up_and_the_chip_counts_each_cause_once(rig):
    view = rig.host.view()
    top = rig.checks(LAB)["Lab: children"]
    assert (top["level"], top["code"], top["caused_by"]) == ("error", "ChildError", ["host/composite-lab-robot-1", "host/composite-lab-robot-2"])
    robot = rig.checks("host/composite-lab-robot-1")["Robot 1: children"]
    assert robot["caused_by"] == [ARM1]
    arm = rig.checks(ARM1)["Arm 1: children"]
    assert arm["caused_by"] == [rig.joint(A, 1)] and "CAN 1 error" in arm["message"]
    assert view.composition.levels["lab"] == "error"
    # one real cause per arm: the two CAN 1 faults; the arms, robots and the rack are their consequence
    assert [path for path in view.rows.causes if "composite" in path] == []
    assert sorted(path for path in view.rows.causes if path.endswith("/can/1")) == sorted([rig.joint(A, 1), rig.joint(B, 1)])
    assert view.rows.summaries[LAB].level == "error"  # the row still has its level (the page draws it as an icon)
    assert rig.host.node(LAB)["node"]["knock_on_of"] is not None


def test_a_child_that_recovers_clears_the_rollup(tmp_path):
    made = CompRig(tmp_path, f'''
[[composite]]
id = "pair"
kind = "arm"
title = "Pair"
children = [
{joints(B, (2, 3))}
]
[[composite]]
id = "withfault"
kind = "arm"
title = "With a fault"
children = [
{joints(A, (1, 2))}
]
''')
    try:
        assert made.checks("host/composite-pair")["Pair: children"]["level"] == "ok"
        assert made.checks("host/composite-pair")["Pair: children"]["message"] == "all 2 ok"
        assert made.checks("host/composite-withfault")["With a fault: children"]["level"] == "error"
    finally:
        made.stop()


def test_a_child_that_is_not_here_is_one_warn_naming_it(tmp_path):
    made = CompRig(tmp_path, f'''
[[composite]]
id = "gap"
kind = "arm"
title = "Gap"
children = [
{joints(A, (2, 3, 15))}
]
''')
    try:
        check = made.checks("host/composite-gap")["Gap: children"]
        assert (check["level"], check["code"]) == ("warn", "ChildMissing") and "CAN 15" in check["message"] and check["caused_by"] == []
    finally:
        made.stop()


def test_a_hand_edit_of_the_file_shows_within_one_refresh(rig):
    assert "host/composite-extra" not in rig.host.view().merged.entities
    rig.expected.write_text(rig.expected.read_text() + f'\n[[composite]]\nid = "extra"\nkind = "arm"\ntitle = "Extra"\nchildren = ["usb:0483:5740:{A}"]\n')
    rig.host.refresh()
    assert rig.host.view().merged.entities["host/composite-extra"].name == "Extra"
    assert rig.checks("host/composite-extra")["Extra: children"]["level"] in ("ok", "error")  # the adapter row: its worst below


# ----- the file's problems, as a status ------------------------------------------------------------------------------
BAD = '''
[[composite]]
id = "a"
kind = "arm"
children = ["b"]
[[composite]]
id = "b"
kind = "arm"
children = ["a"]
'''


def test_a_broken_file_is_one_unknown_on_the_computer_and_no_composite_loads_but_the_rigs_do(tmp_path):
    rigs = f'[[rig]]\nname = "Test rack"\n[[rig.device]]\nname = "fdcanusb A"\nkind = "adapter"\nhardware_id = "usb:0483:5740:{A}"\nnodes = ["can/1"]\n'
    made = CompRig(tmp_path, BAD, rigs=rigs, arm_plugin=False)
    try:
        view = made.host.view()
        status = made.checks("host")["composition"]
        assert (status["level"], status["code"]) == ("unknown", "CompositionInvalid")
        assert "contain each other" in status["message"] and "1." in dict((v["key"], v["value"]) for v in status["values"])["what_to_check"]
        assert not any("composite" in path for path in view.merged.entities) and made.host.state()["composition"]["problems"]
        assert "host/rig-test-rack" in view.merged.entities  # a typo in a composite never takes a rig away
        with pytest.raises(Exception, match="composite tables"):
            made.host.setup.editable()
        before = made.expected.read_text()
        out = made.host.setup_edit_ids("expect", hardware_id=f"usb:0483:5740:{B}", name="B", kind="adapter", rig="Test rack")
        assert out.get("refused") and made.expected.read_text() == before  # the person's file is not rewritten
    finally:
        made.stop()


def test_a_panel_edit_rewrites_the_file_with_the_composites_kept(rig):
    before = rig.host.setup.manifest.composites
    out = rig.host.setup_edit_ids("expect", hardware_id=f"usb:0483:5740:{A}", name="fdcanusb A", kind="adapter", rig="Rack")
    assert not out.get("refused"), out
    rig.host.setup.reload()
    assert rig.host.setup.manifest.composites == before and rig.host.setup.manifest.rigs[0].name == "Rack"
    assert rig.expected.read_text().count("[[composite]]") == 5


def test_a_plugin_that_cannot_serve_a_composite_is_a_warn_and_the_rollup_still_works(tmp_path):
    text = f'''
[[composite]]
id = "robot"
kind = "robot"
title = "Robot"
plugin = "state_arm"
children = [
{joints(B, (2, 3))}
]
[[composite]]
id = "ghost"
kind = "arm"
title = "Ghost"
plugin = "nobody"
children = [
  "host/nothing-here",
]
'''
    made = CompRig(tmp_path, text)
    try:
        robot = made.checks("host/composite-robot")
        assert robot["Robot: plugin"]["level"] == "warn" and robot["Robot: plugin"]["code"] == "PluginNotUsable"
        assert "does not handle kind 'robot'" in robot["Robot: plugin"]["message"] and "composite:arm" in robot["Robot: plugin"]["message"]
        assert robot["Robot: children"]["level"] == "ok"  # the rollup needs no plugin
        assert "is not in" in made.checks("host/composite-ghost")["Ghost: plugin"]["message"]
        assert made.log(made.arm_log, "diagnose") == []  # a plugin that does not match the kind is never run for it
    finally:
        made.stop()


# ----- a composite plugin: given its children, its records on the composite --------------------------------------------
def test_the_plugin_is_run_with_the_composites_id_and_its_childrens_ids_and_paths(rig):
    runs = [row for row in rig.log(rig.arm_log, "diagnose") if row["composite"].get("id") == "lab/robot-1/arm"]
    assert runs and runs[-1]["argv"][-2:] == ["--device", "composite:lab/robot-1/arm"]
    last = runs[-1]
    composite = last["composite"]
    assert (composite["kind"], composite["title"], composite["path"]) == ("arm", "Arm 1", ARM1)
    assert composite["expects"] == {"tracking_error_rev": 0.5, "links_m": [0.3, 0.25, 0.1]}
    assert [(c["ref"], c["id"], c["kind"], c["path"], c["name"], c["present"]) for c in composite["children"]] == [
        (f"usb:0483:5740:{A}#can/{n}", f"usb:0483:5740:{A}#can/{n}", "entity", rig.joint(A, n), f"CAN {n}", True) for n in (1, 2, 3)]
    assert last["device_path"] == ARM1 and json.loads(last["device"])["id"] == "composite:lab/robot-1/arm"
    assert last["record_version"] == "0.2"
    values = json.loads(Path(last["values"]).read_text())
    assert [entry["path"] for entry in values["children"]] == [rig.joint(A, n) for n in (1, 2, 3)]
    assert values["children"][0]["level"] == "error" and values["children"][1]["level"] == "ok"
    assert values["children"][1]["state"]["motor"]["position"]["unit"] == "rev"


def test_a_nested_composite_lists_its_subtree_to_its_plugin(tmp_path):
    text = lab().replace('title = "Robot 1"\nchildren = ["lab/robot-1/arm"]', 'title = "Robot 1"\nplugin = "state_arm"\nchildren = ["lab/robot-1/arm"]')
    made = CompRig(tmp_path, text, arm_matches=("composite:arm", "composite:robot"))
    try:
        row = next(row for row in made.log(made.arm_log, "diagnose") if row["composite"].get("id") == "lab/robot-1")
        child = row["composite"]["children"][0]
        assert (child["kind"], child["composite_kind"], child["path"]) == ("composite", "arm", ARM1)
        assert [grand["name"] for grand in child["children"]] == ["CAN 1", "CAN 2", "CAN 3"]
        values = json.loads(Path(row["values"]).read_text())
        assert [entry["path"] for entry in values["children"]] == [ARM1, *[made.joint(A, n) for n in (1, 2, 3)]]  # flat, depth first
    finally:
        made.stop()


def test_the_plugins_records_land_on_the_composite(rig):
    node = rig.host.node(ARM1)
    assert node["node"]["name"] == "Arm 1" and node["entity"]["owner"] == "state_arm"
    checks = rig.checks(ARM1)
    assert checks["Arm 1: all joints answer"]["level"] == "ok" and checks["Arm 1: all joints answer"]["message"] == "all 3 answer"
    assert checks["Arm 1: tracking error"]["level"] == "ok"
    assert "state_arm: plugin run" in checks and checks["Arm 1: children"]["source"] == "io_diagnostics.composition"
    assert [(row["key"], row["value"]) for row in node["config"]] == [("arm.dof", 3), ("arm.tracking_error_rev", 0.5)]
    assert [action["name"] for action in node["actions"] if not action["name"].startswith("all:")] == ["home_arm"]
    assert node["composite"]["id"] == "lab/robot-1/arm" and [c["name"] for c in node["composite"]["children"]] == ["CAN 1", "CAN 2", "CAN 3"]
    # the arm's own verdict and its children's rollup sit side by side; the arm is one row with the worst of them
    assert node["node"]["level"] == "error"
    # a composite without a plugin has none of this and still has its rollup
    lab_node = rig.host.node(LAB)
    assert lab_node["config"] == [] and lab_node["watch"]["supported"] is False and lab_node["composite"]["plugin"] is None


def test_a_composites_own_utility_is_its_plugins_and_runs_with_the_composites_id_and_children(rig):
    result = rig.host.act(ARM1, "home_arm", dry_run=True)
    assert result["refused"] is None and result["outcome"] == "ok" and (result["plugin"], result["danger"]) == ("state_arm", "moves")
    assert result["device_id"] == "composite:lab/robot-1/arm" and result["action_result"]["token"] == "tok-home_arm"
    assert result["argv"][result["argv"].index("--device") + 1] == "composite:lab/robot-1/arm"
    acts = rig.log(rig.arm_log, "act")
    assert acts and acts[-1]["composite"]["id"] == "lab/robot-1/arm" and len(acts[-1]["composite"]["children"]) == 3  # told the same as a diagnose
    # a utility of a composite without a plugin is nobody's: refused, not guessed
    assert rig.host.act(LAB, "home_arm", dry_run=True)["refused"] == "NoPlugin"


def test_two_composites_of_one_plugin_are_remembered_apart(rig):
    # the last-known tree keys a plugin's entities by (plugin, hardware_id): a composite's hardware_id is what the file says it is
    keys = sorted(key for key in rig.host.roster.devices if key.startswith("state_arm|"))
    assert keys == ["state_arm|composite:lab/robot-1/arm", "state_arm|composite:lab/robot-2/arm"]
    assert rig.host.node(ARM1)["node"]["hardware_id"] == "composite:lab/robot-1/arm"


def test_a_state_value_may_say_it_is_a_childs_and_the_host_passes_it_through(rig):
    values = rig.host.values_json(ARM1)
    group = next(group for group in values["groups"] if group["name"] == "arm")
    rows = {row["key"]: row for row in group["rows"]}
    for number in (1, 2, 3):
        assert rows[f"joint {number} angle"]["source"] == {"path": rig.joint(A, number), "key": "position"}
        assert rows[f"joint {number} angle"]["unit"] == "rev"
    assert rows["end effector x"]["source"] is None and rows["end effector x"]["unit"] == "m"
    assert rows["tracking error"]["warn_high"] == 0.5
    # the planar chain of three links at the joints' angles: the plugin's own maths, passed through unread
    import math

    angles = [rows[f"joint {n} angle"]["value"] for n in (1, 2, 3)]
    total, x, y = 0.0, 0.0, 0.0
    for length, angle in zip((0.3, 0.25, 0.1), angles):
        total += angle * 2 * math.pi
        x, y = x + length * math.cos(total), y + length * math.sin(total)
    assert rows["end effector x"]["value"] == pytest.approx(x, abs=1e-3) and rows["end effector y"]["value"] == pytest.approx(y, abs=1e-3)


def test_the_plugins_own_failing_verdict_counts_as_a_cause_of_its_own(tmp_path):
    made = CompRig(tmp_path, lab().replace("tracking_error_rev = 0.5", "tracking_error_rev = -1"))
    try:
        # a limit of -1 rev can never be met: the plugin says error, the host only shows it, and (no caused_by) it counts
        check = made.checks(ARM1)["Arm 1: tracking error"]
        assert (check["level"], check["code"], check["caused_by"]) == ("error", "TrackingError", [])
        assert ARM1 in made.host.view().rows.causes
    finally:
        made.stop()


def test_a_watch_of_a_composite_keeps_its_childrens_watches_alive_and_the_values_file_moves(rig):
    rig.host.watch_heartbeat(ARM1)
    watched = wait_for(lambda: {row["argv"][-1] for row in rig.log(rig.state_log, "watch")} >= {"can/1", "can/2", "can/3"}
                       and rig.log(rig.arm_log, "watch"), what="the arm's watch and its three joints' watches")
    assert watched
    wait_for(lambda: [g for g in (rig.host.values_json(ARM1) or {"groups": []})["groups"] if g["name"] == "arm"], what="arm State")
    stamps = []
    for _ in range(2):
        time.sleep(0.4)
        rig.host.watch_heartbeat(ARM1)
        row = next(r for g in rig.host.values_json(ARM1)["groups"] if g["name"] == "arm" for r in g["rows"] if r["key"] == "joint 2 angle")
        stamps.append(row["value"])
    assert len(set(stamps)) > 1, stamps  # it moves: the arm is reading its joints' live State from the file the host keeps writing
    assert rig.host.watch_json(*rig.host._watch_target(ARM1))["state"] in ("running", "starting")


# ----- batch actions -------------------------------------------------------------------------------------------------
def batch_titles(rig, path):
    return {a["name"]: a for a in rig.host.node(path)["actions"] if a["name"].startswith("all:")}


def test_a_composite_offers_an_action_all_its_leaves_offer_with_the_count(rig):
    lab_actions = batch_titles(rig, LAB)
    assert sorted(lab_actions) == ["all:calibrate", "all:clear_faults"]
    calibrate = lab_actions["all:calibrate"]
    assert (calibrate["title"], calibrate["danger"], calibrate["scope"], calibrate["batch"]["n"]) == ("Calibrate all 6…", "moves", "composite", 6)
    assert calibrate["batch"]["groups"] == ["rack-bus"] and calibrate["state"] in ("ready", "unknown")
    assert [(pre["check"], pre["state"]) for pre in calibrate["preconditions"]] == [("joint_free", "unknown")]
    assert "CAN 1's joint is free to spin" in calibrate["preconditions"][0]["text"] and calibrate["preconditions"][0]["text"].endswith("; …")  # the boards' own
    assert batch_titles(rig, ARM1)["all:calibrate"]["title"] == "Calibrate all 3…"
    assert batch_titles(rig, "host/composite-lab-robot-1")["all:calibrate"]["batch"]["n"] == 3  # a robot over one arm: the same three
    own = [a["name"] for a in rig.host.node(ARM1)["actions"] if not a["name"].startswith("all:")]
    assert own == ["home_arm"]  # the plugin's own utility is the plugin's: RobotDx added nothing custom


def test_one_dry_run_lists_the_n_runs_queued_by_their_group_and_holds_each_boards_own_token(rig):
    result = rig.host.act(LAB, "all:calibrate", dry_run=True)
    assert result["refused"] is None and result["outcome"] == "ok" and result["dry_run"] is True
    batch = result["batch"]
    assert (batch["action"], batch["n"], batch["danger"]) == ("calibrate", 6, "moves") and batch["skipped"] == []
    assert [run["path"] for run in batch["runs"]] == [rig.joint(A, 1), rig.joint(A, 2), rig.joint(A, 3), rig.joint(B, 1), rig.joint(B, 2), rig.joint(B, 3)]
    assert [run["starts"] for run in batch["runs"]] == ["now"] + [f"after {k} earlier run{'s' if k != 1 else ''} of rack-bus" for k in range(1, 6)]
    assert [run["label"] for run in batch["runs"]][:2] == ["CAN 1 · fdcanusb fixture · Run calibration", "CAN 2 · fdcanusb fixture · Run calibration"]
    assert [run["after"] for run in batch["runs"]] == [0, 1, 2, 3, 4, 5]
    assert all(run["argv"][-1] == "--dry-run" and "--confirm" not in run["argv"] for run in batch["runs"])
    assert sorted(result["plugin_tokens"]) == sorted(run["path"] for run in batch["runs"])
    assert result["plugin_tokens"] == {run["path"]: f"tok-calibrate-{run['node']}" for run in batch["runs"]}  # each board's own
    assert result["action_result"]["confirm"].startswith("Calibrate all 6") and len(result["action_result"]["steps"]) == 6
    assert rig.host.runs.json()["total"] == 0  # a dry run starts no run


def cell(batch: str = "all") -> str:
    having = f'batch = "{batch}"\n' if batch != "all" else ""
    return f'''
[[composite]]
id = "arm"
kind = "arm"
title = "Arm"
children = [
{joints(B)}
]
[[composite]]
id = "cell"
kind = "robot"
title = "Cell"
{having}children = ["arm", "usb:8086:0b5b:{CAM}"]
'''


CELL = "host/composite-cell"


def test_a_batch_is_refused_while_one_board_lacks_the_action_and_the_plan_says_which(tmp_path):
    made = CompRig(tmp_path, cell(), camera=True, arm_plugin=False)
    try:
        assert batch_titles(made, CELL) == {}  # not offered: the camera calibrates nothing
        assert sorted(batch_titles(made, "host/composite-arm")) == ["all:calibrate", "all:clear_faults"]
        refused = made.host.act(CELL, "all:calibrate", dry_run=True)
        assert refused["refused"] == "ChildLacksAction" and "D405" in refused["message"] and refused["batch"] is None
        assert "plugin_tokens" not in refused and made.host.runs.json()["total"] == 0
        assert made.host.act("host/composite-nothing", "all:calibrate", dry_run=True)["refused"] == "NoSuchComposite"
        assert made.host.act(CELL, "all:set_id", dry_run=True)["refused"] == "NotBatchable"
    finally:
        made.stop()


def test_having_skips_the_boards_without_it_and_says_so(tmp_path):
    made = CompRig(tmp_path, cell("having"), camera=True, arm_plugin=False)
    try:
        actions = batch_titles(made, CELL)
        assert sorted(actions) == ["all:calibrate", "all:clear_faults"]  # check_firmware / identify: only the camera has them, one is not a batch
        assert actions["all:calibrate"]["title"] == "Calibrate all 3…"
        assert [(item["name"], item["why"]) for item in actions["all:calibrate"]["batch"]["skipped"]] == [("RealSense D405 fixture", "offers no calibrate")]
        result = made.host.act(CELL, "all:calibrate", dry_run=True)
        assert result["batch"]["n"] == 3 and result["batch"]["skipped"][0]["why"] == "offers no calibrate"
        assert "RealSense D405 fixture" in actions["all:calibrate"]["description"]
    finally:
        made.stop()


@pytest.fixture
def served(tmp_path):
    made = CompRig(tmp_path, act_s="0.6")
    server = make_server(made.host, 0)
    serve_in_thread(server)
    yield made, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    made.stop()


def fetch(url, *, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def post(base, body, headers=PAGE):
    status, _h, payload = fetch(f"{base}/api/v1/act", data=json.dumps(body).encode(), headers=headers)
    return status, json.loads(payload)


def test_one_confirm_starts_n_runs_one_at_a_time_per_group_and_each_run_is_an_ordinary_run(served):
    made, base = served
    status, dry = post(base, {"path": LAB, "action": "all:calibrate", "dry_run": True})
    assert status == 200 and dry["token"] and dry["result"]["batch"]["n"] == 6 and "plugin_tokens" not in dry["result"]
    status, answer = post(base, {"path": LAB, "action": "all:calibrate", "token": dry["token"], "detach": True})
    assert status == 202 and len(answer["runs"]) == 6 and answer["batch"]["n"] == 6
    ids = [run["id"] for run in answer["runs"]]
    assert [run["path"] for run in answer["runs"]] == [r["path"] for r in dry["result"]["batch"]["runs"]]
    assert [run["label"] for run in answer["runs"]] == [r["label"] for r in dry["result"]["batch"]["runs"]]  # the tabs carry the plan's names
    assert all(run["interactive"] and run["danger"] == "moves" and run["action"] == "calibrate" for run in answer["runs"])
    # never more than the bus allows: one running, the other five queued, then they go one after another in the plan's order
    seen_states = []

    def snapshot():
        runs = {run["id"]: run for run in json.loads(fetch(f"{base}/api/v1/runs")[2])["runs"]}
        states = [runs[i]["state"] for i in ids]
        seen_states.append(states)
        assert states.count("running") + states.count("waiting") <= 1, states
        return all(state == "done" for state in states)

    wait_for(snapshot, timeout=30, what="six runs, one at a time")
    assert any(states.count("queued") >= 4 for states in seen_states), seen_states
    order = sorted(ids, key=lambda i: next(r for r in json.loads(fetch(f"{base}/api/v1/runs")[2])["runs"] if r["id"] == i)["started"])
    assert order == ids  # started in the plan's order
    assert fetch(f"{base}/api/v1/runs/{ids[0]}")[0] == 200
    # every run went past its plugin's own check of its board's token (the fixture answers ConfirmationRequired to a wrong one)
    codes = [[r.get("code") for r in json.loads(fetch(f"{base}/api/v1/runs/{i}")[2])["result"]["records"]] for i in ids]
    assert codes == [["ActionRefused"]] * 6, codes
    # the plugin was given a token per board, and every real run carried its own
    acts = [row for row in map(json.loads, made.state_log.read_text().splitlines()) if row["verb"] == "act" and "--dry-run" not in row["argv"]]
    assert len(acts) == 6 and all(row["argv"][row["argv"].index("--confirm") + 1] == f"tok-calibrate-{row['argv'][row['argv'].index('--node') + 1]}"
                                  for row in acts)


def test_a_batch_starts_a_groups_runs_one_at_a_time_in_the_plans_order(tmp_path):
    """While a person's own run holds the bus, a batch is created whole (six runs, all queued) but only the FIRST of the group is launched; the
    next is launched when it ends. So the order is the plan's and not whatever six waiting threads wake in."""

    import threading

    made = CompRig(tmp_path, act_s="0.2")
    try:
        dry = made.host.act(LAB, "all:calibrate", dry_run=True)
        held, release = threading.Event(), threading.Event()

        def hold_the_bus():
            with made.host.gates.hold("rack-bus", person=True):
                held.set()
                release.wait(20)

        holder = threading.Thread(target=hold_the_bus, daemon=True)
        holder.start()
        assert held.wait(5)
        runs, out = made.host.start_batch(LAB, "all:calibrate", dry["plugin_tokens"], interactive=False)
        assert out["outcome"] == "ok" and len(runs) == 6
        time.sleep(0.4)
        waiting = [thread.name for thread in threading.enumerate() if thread.name.startswith("run-")]
        assert len(waiting) == 1 and [run.state for run in runs] == ["queued"] * 6, (waiting, [run.state for run in runs])
        release.set()
        holder.join(5)
        for run in runs:
            assert run.wait(20)
        ended = [run.to_json()["ended"] for run in runs]
        assert ended == sorted(ended) and len(set(ended)) == 6, ended  # one after another, in the plan's order
    finally:
        made.stop()


def test_a_boards_with_no_group_all_start_together(tmp_path):
    made = CompRig(tmp_path, exclusive="", act_s="0.8")
    server = make_server(made.host, 0)
    serve_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        _s, dry = post(base, {"path": LAB, "action": "all:calibrate", "dry_run": True})
        assert [run["starts"] for run in dry["result"]["batch"]["runs"]] == ["now"] * 6
        _s, answer = post(base, {"path": LAB, "action": "all:calibrate", "token": dry["token"], "detach": True})
        ids = [run["id"] for run in answer["runs"]]
        wait_for(lambda: sum(1 for r in json.loads(fetch(f"{base}/api/v1/runs")[2])["runs"] if r["id"] in ids and r["state"] == "running") >= 4,
                 what="the unqueued runs to run side by side")
    finally:
        server.shutdown()
        server.server_close()
        made.stop()


def test_the_batch_is_the_pages_alone_and_its_token_is_for_that_batch_only(served):
    made, base = served
    body = {"path": LAB, "action": "all:calibrate", "dry_run": True}
    assert post(base, body, {"Content-Type": "application/json"})[0] == 403  # not from the page
    _s, dry = post(base, body)
    status, answer = post(base, {"path": LAB, "action": "all:calibrate", "token": "nope", "detach": True})
    assert status == 403 and answer["refused"] == "NoDryRun"
    status, answer = post(base, {"path": ARM1, "action": "all:calibrate", "token": dry["token"], "detach": True})
    assert status == 403 and answer["refused"] == "TokenMismatch"  # the rack's token does not run the arm (and a wrong token is spent)
    status, answer = post(base, {"path": LAB, "action": "all:calibrate", "token": dry["token"], "detach": True})
    assert status == 403 and answer["refused"] == "NoDryRun"
    assert made.host.runs.json()["total"] == 0  # nothing ever started
    assert not [row for row in map(json.loads, made.state_log.read_text().splitlines()) if row["verb"] == "act" and "--dry-run" not in row["argv"]]
    # a batch is not a CLI action: no real run through Host.act
    assert made.host.act(LAB, "all:calibrate", confirm="x", human=True)["refused"] == "BatchFromPage"


def test_a_batch_whose_dry_run_a_board_refuses_is_refused_whole(served):
    made, base = served
    status, first = post(base, {"path": LAB, "action": "all:clear_faults", "dry_run": True})
    assert status == 200 and first["result"]["batch"]["n"] == 6 and first["result"]["danger"] == "changes"
    # a run is active on the bus: every dry run of the group is refused at once (docs/runs-v1.md), so is the batch
    _s, dry = post(base, {"path": LAB, "action": "all:calibrate", "dry_run": True})
    post(base, {"path": LAB, "action": "all:calibrate", "token": dry["token"], "detach": True})
    wait_for(lambda: made.host.runs.active(), what="a run")
    status, busy = post(base, {"path": LAB, "action": "all:clear_faults", "dry_run": True})
    assert busy["result"]["refused"] == "Busy" and busy["token"] is None
