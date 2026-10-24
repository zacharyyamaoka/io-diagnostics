"""Composites (docs/composition-v1.md), the pure half: the file (parse, every problem named, dump), how a child is found, the worst-child
rollup through three levels with caused_by, which plugin may serve which kind, and batch planning. No host, no subprocess: the tree is
built from the OS layer plus statuses, exactly as ``tree.merge`` does for a host.
"""

from __future__ import annotations

import tomllib

import pytest

from contract.record import (ERROR, OK, UNKNOWN, WARN, Action, DiagnosticStatus, Entity, Measurement, SourceRef, State, from_dict, to_dict,
                             validate)
from io_diagnostics import composite as composite_module
from io_diagnostics import composition, expected, tree
from io_diagnostics.composite import Composite
from io_diagnostics.plugins import Match, parse_match
from test_host_support import host_dict, standard_skeleton, usb_device

NOW = 1000.0
ADAPTER = "usb:0483:5740:AB12"


def tables(text: str):
    return tomllib.loads(text).get("composite")


def parse(text: str):
    return composite_module.parse(tables(text))


# ----- the file ------------------------------------------------------------------------------------------------------
GOOD = f'''
[[composite]]
id = "lab"
kind = "rack"
title = "Lab rack"
children = ["lab/robot", "host/usb1/camera"]

[[composite]]
id = "lab/robot"
kind = "robot"
children = ["lab/robot/arm"]

[[composite]]
id = "lab/robot/arm"
kind = "arm"
plugin = "state_arm"
expects = {{ dof = 3, links_m = [0.3, 0.25, 0.1], limits = {{ lo = -1.5, hi = 1.5 }} }}
batch = "having"
source = "cad2robot One_Dof_Test_Rack"
children = ["{ADAPTER}#can/1", "{ADAPTER}#can/2", "moteus:abc-3"]
'''


def test_a_valid_file_parses_to_composites_at_any_depth():
    composites, problems = parse(GOOD)
    assert problems == []
    assert [c.id for c in composites] == ["lab", "lab/robot", "lab/robot/arm"]
    arm = composites[2]
    assert (arm.kind, arm.plugin, arm.batch, arm.source) == ("arm", "state_arm", "having", "cad2robot One_Dof_Test_Rack")
    assert arm.expects == {"dof": 3, "links_m": [0.3, 0.25, 0.1], "limits": {"lo": -1.5, "hi": 1.5}}
    assert composites[0].label() == "Lab rack" and arm.label() == "arm"
    assert arm.path == "host/composite-lab-robot-arm"
    assert [composite_module.classify(ref) for ref in composites[0].children + arm.children] == [
        "composite", "path", "node", "node", "hardware"]


def test_several_roots_and_the_manifest_round_trips_with_the_rigs_kept():
    text = GOOD + '\n[[composite]]\nid = "other"\nkind = "robot"\nchildren = ["host/usb2/x"]\n'
    manifest = expected.parse('version = 1\n[[rig]]\nname = "r"\n' + text)
    assert manifest.composite_problems == [] and len(manifest.rigs) == 1
    assert [c.id for c in manifest.active_composites()] == ["lab", "lab/robot", "lab/robot/arm", "other"]
    again = expected.parse(expected.dump(manifest))
    assert again.composites == manifest.composites and again.rigs == manifest.rigs


@pytest.mark.parametrize("text, fragment", [
    ('[[composite]]\nid = "a"\nkind = "arm"\nchildren = []\n', "children is required"),
    ('[[composite]]\nid = "a"\nchildren = ["host/x"]\n', "kind is required"),
    ('[[composite]]\nkind = "arm"\nchildren = ["host/x"]\n', "id is required"),
    ('[[composite]]\nid = "a b"\nkind = "arm"\nchildren = ["host/x"]\n', "id must be words"),
    ('[[composite]]\nid = "host/a"\nkind = "arm"\nchildren = ["host/x"]\n', "id must be words"),
    ('[[composite]]\nid = "a:b"\nkind = "arm"\nchildren = ["host/x"]\n', "id must be words"),
    ('[[composite]]\nid = "a"\nkind = "arm"\nchildren = [3]\n', "each child is a non-empty string"),
    ('[[composite]]\nid = "a"\nkind = "arm"\nchildren = ["host/x"]\nbatch = "some"\n', "batch must be one of"),
    ('[[composite]]\nid = "a"\nkind = "arm"\nchildren = ["host/x"]\nplugin = "a b"\n', "plugin must be a plugin's name"),
    ('[[composite]]\nid = "a"\nkind = "arm"\nchildren = ["host/x"]\nexpects = 3\n', "expects must be a table"),
    ('[[composite]]\nid = "a"\nkind = "arm"\nchildren = ["host/x"]\nexpects = { x = inf }\n', "not finite"),
    ('[[composite]]\nid = "a"\nkind = "arm"\nchildren = ["host/x"]\ntitle = 3\n', "title must be a string"),
])
def test_a_table_that_is_wrong_in_itself_is_a_named_problem_not_a_crash(text, fragment):
    composites, problems = parse(text)
    assert any(fragment in problem for problem in problems), problems
    assert composites == []


def test_composites_that_disagree_with_each_other_are_named():
    def ids(*specs):
        return "".join(f'[[composite]]\nid = "{i}"\nkind = "k"\nchildren = {kids!r}\n'.replace("'", '"') for i, kids in specs)

    _, problems = parse(ids(("a", ["host/x"]), ("a", ["host/y"])))
    assert any("defined twice" in problem for problem in problems)
    _, problems = parse(ids(("a-b", ["host/x"]), ("a/b", ["host/y"])))
    assert any("would both be host/composite-a-b" in problem for problem in problems)
    _, problems = parse(ids(("a", ["nobody"])))  # a composite-looking id nobody defines: an unknown child
    assert any("child 'nobody' is not a composite in this file" in problem for problem in problems)
    _, problems = parse(ids(("a", ["a"])))
    assert any("contains itself" in problem for problem in problems)
    _, problems = parse(ids(("a", ["b"]), ("b", ["a"])))
    loop = next(problem for problem in problems if "contain each other: " in problem)
    assert loop.count(" -> ") == 2 and loop.split(": ")[1].split(" -> ")[0] == loop.split(" -> ")[-1] and {"a", "b"} <= set(loop.split(" -> ")), loop
    _, problems = parse(ids(("a", ["c"]), ("b", ["c"]), ("c", ["host/x"])))
    assert any("'c' is a child of both 'a' and 'b'" in problem for problem in problems)
    _, problems = parse(ids(("a", ["host/x"]), ("b", ["host/x"])))
    assert any("'host/x' is a child of both 'a' and 'b'" in problem for problem in problems)
    _, problems = parse(ids(("a", ["host/x", "host/x"])))
    assert any("lists 'host/x' twice" in problem for problem in problems)


def test_a_composite_is_a_child_by_its_id_never_by_its_path():
    _, problems = parse('[[composite]]\nid = "a"\nkind = "k"\nchildren = ["host/composite-b"]\n[[composite]]\nid = "b"\nkind = "k"\nchildren = ["host/x"]\n')
    assert any("child 'host/composite-b' is a composite's path: name it by its id" in problem for problem in problems), problems


def test_a_three_composite_loop_is_named_by_its_members():
    text = ''.join(f'[[composite]]\nid = "{i}"\nkind = "k"\nchildren = ["{kid}"]\n' for i, kid in (("a", "b"), ("b", "c"), ("c", "a")))
    _, problems = parse(text)
    loops = [problem for problem in problems if "contain each other" in problem]
    assert len(loops) == 1 and all(word in loops[0] for word in ("a", "b", "c")), problems


def test_problems_in_the_composites_never_take_the_rigs_away_and_stop_a_rewrite():
    manifest = expected.parse('version = 1\n[[rig]]\nname = "r"\n[[composite]]\nid = "a"\nkind = "k"\nchildren = ["b"]\n')
    assert [rig.name for rig in manifest.rigs] == ["r"] and manifest.composite_problems
    assert manifest.active_composites() == []  # no half-loaded hierarchy
    setup = expected.ExpectedSetup(None)
    setup.manifest = manifest
    setup.path = None
    with pytest.raises(expected.ManifestError):
        setup.editable()


def test_a_file_that_is_not_toml_still_fails_whole_as_before():
    with pytest.raises(expected.ManifestError):
        expected.parse("[[composite\nid =")


# ----- who may serve a composite -------------------------------------------------------------------------------------
def composite_device(kind: str) -> dict:
    return composition.device_dict(Composite("x", kind, ["host/y"]))


def test_a_composite_match_sees_only_a_composite_of_that_kind_and_no_device_glob_sees_one():
    assert parse_match("composite:arm").accepts(composite_device("arm"))
    assert parse_match("composite:*").accepts(composite_device("robot"))
    assert not parse_match("composite:arm").accepts(composite_device("robot"))
    usb = usb_device("9-1", "0483", "5740", "AB12")
    assert not parse_match("composite:*").accepts(usb) and not parse_match("composite:*").accepts(host_dict())
    for pattern in ("usb:*", "usb:0483:5740", "host", "by-id:*", "tty:*", "pci:*", "netdev:*", "v4l:*"):
        assert not parse_match(pattern).accepts(composite_device("arm")), pattern
    assert Match("composite", "arm").text() == "composite:arm"


# ----- finding the children, rolling their levels up ----------------------------------------------------------------
def world(levels: dict[str, str | None], *, missing: list[str] = (), codes: dict | None = None):
    """A host over one adapter with boards can/1..can/N reporting the given levels (None = no record at all), plus a camera.
    Returns (merged, layer, rows)."""

    devices = standard_skeleton(usb_device("9-1", "0483", "5740", "AB12", label="fdcanusb"),
                                usb_device("9-2", "8086", "0b5b", "CAM1", label="camera"))
    layer = tree.build_os_layer("rig", devices)
    adapter = layer.device_paths["usb:9-1"]
    statuses = [DiagnosticStatus(level=OK, name="fdcanusb: presence", path=adapter, stamp=NOW, received=NOW)]
    extra = [Entity(f"{adapter}/can", "CAN bus", "bus", via=adapter)]
    for key, level in levels.items():
        path = f"{adapter}/can/{key}"
        extra.append(Entity(path, f"CAN {key}", "node", via=f"{adapter}/can", hardware_id=f"moteus:{key}"))
        if level is not None:
            statuses.append(DiagnosticStatus(level=level, name=f"CAN {key}: servo", path=path, stamp=NOW, received=NOW,
                                             code="" if level == OK else (codes or {}).get(key, "Broken"), message=f"board {key} is {level}"))
    layer.entities = {**layer.entities, **{entity.path: entity for entity in extra}}
    return layer, adapter, statuses


def composed(composites: list[Composite], layer, statuses):
    merged = tree.merge(layer, [], statuses)
    pre = tree.build_rows(merged, NOW)
    view = composition.evaluate(composites, merged, layer, pre, NOW)
    merged.statuses.extend(view.statuses)
    return merged, view, tree.build_rows(merged, NOW)


def three_levels(adapter_id=ADAPTER):
    return [Composite("rack", "rack", ["rack/robot"], title="Rack"),
            Composite("rack/robot", "robot", ["rack/robot/arm", "usb:8086:0b5b:CAM1"], title="Robot"),
            Composite("rack/robot/arm", "arm", [f"{adapter_id}#can/1", f"{adapter_id}#can/2", f"{adapter_id}#can/3"], title="Arm")]


def add_composites(layer, composites):
    composition.add_to_layer(layer, composites)


def test_children_are_found_by_adapter_and_node_by_hardware_id_and_by_path():
    layer, adapter, statuses = world({"1": OK, "2": OK})
    composites = [Composite("a", "arm", [f"{ADAPTER}#can/1", f"{adapter}/can/2", "usb:0483:5740:AB12", f"{ADAPTER}#can/9", "moteus:9"])]
    add_composites(layer, composites)
    merged = tree.merge(layer, [], statuses)
    found = composition.resolve(composites, merged, layer)["a"]
    assert [(c.present, c.path) for c in found] == [(True, f"{adapter}/can/1"), (True, f"{adapter}/can/2"), (True, adapter),
                                                      (False, None), (False, None)]
    assert [c.name for c in found[3:]] == ["CAN 9", "moteus:9"]  # an absent board is named in the sentence, not hidden


def test_rollup_through_three_levels_is_the_worst_child_and_counts_the_cause_once():
    layer, adapter, statuses = world({"1": OK, "2": WARN, "3": ERROR})
    composites = three_levels()
    add_composites(layer, composites)
    merged, view, rows = composed(composites, layer, statuses)
    by_name = {s.name: s for s in view.statuses}
    assert (by_name["Arm: children"].level, by_name["Arm: children"].code) == (ERROR, "ChildError")
    assert by_name["Arm: children"].caused_by == (f"{adapter}/can/2", f"{adapter}/can/3")
    assert by_name["Robot: children"].level == ERROR and by_name["Robot: children"].caused_by == (composites[2].path,)
    assert by_name["Rack: children"].level == ERROR and by_name["Rack: children"].caused_by == (composites[1].path,)
    assert [view.levels[c.id] for c in composites] == [ERROR, ERROR, ERROR]
    # the chip: CAN 3 (error) and CAN 2 (warn) are the causes; the three composites are their consequence and are not counted
    assert rows.causes == [f"{adapter}/can/3", f"{adapter}/can/2"]
    assert set(rows.knock_on_of) >= {c.path for c in composites}
    assert rows.summaries[composites[0].path].level == ERROR  # the top still shows the level, as an icon
    assert "all" not in by_name["Arm: children"].message and "CAN 3 error" in by_name["Arm: children"].message


def test_the_rollup_is_the_worst_not_the_best_and_all_ok_is_ok():
    layer, adapter, statuses = world({"1": OK, "2": OK, "3": WARN})
    composites = three_levels()
    add_composites(layer, composites)
    _, view, _ = composed(composites, layer, statuses)
    assert view.levels["rack/robot/arm"] == WARN and view.levels["rack"] == WARN
    layer, adapter, statuses = world({"1": OK, "2": OK, "3": OK})
    add_composites(layer, composites)
    _, view, rows = composed(composites, layer, statuses)
    assert [s.level for s in view.statuses] == [OK, OK, OK] and rows.causes == []
    assert next(s for s in view.statuses if s.name == "Arm: children").message == "all 3 ok"


def test_a_stale_child_is_unknown_not_stale_and_a_silent_one_is_not_counted():
    layer, adapter, statuses = world({"1": OK, "2": None, "3": OK})
    stale = DiagnosticStatus(level=OK, name="CAN 3: servo", path=f"{adapter}/can/3", stamp=NOW - 100, received=NOW - 100, expected_period_s=1.0)
    statuses = [s for s in statuses if s.path != f"{adapter}/can/3"] + [stale]
    composites = three_levels()
    add_composites(layer, composites)
    _, view, _ = composed(composites, layer, statuses)
    arm = next(s for s in view.statuses if s.name == "Arm: children")
    assert arm.level == UNKNOWN and arm.code == "ChildUnknown" and arm.level in ("unknown", "ok", "warn", "error")
    assert "CAN 2" not in arm.message  # a board with no record at all says nothing


def test_a_child_that_is_not_here_is_a_warn_and_has_no_row_to_blame():
    layer, adapter, statuses = world({"1": OK, "2": ERROR})
    composites = [Composite("a", "arm", [f"{ADAPTER}#can/1", f"{ADAPTER}#can/2", f"{ADAPTER}#can/15"], title="A")]
    add_composites(layer, composites)
    _, view, rows = composed(composites, layer, statuses)
    status = view.statuses[0]
    assert status.level == ERROR and "CAN 15" in status.message and "not here" in status.message
    assert status.caused_by == ()  # the composite is its own cause too: nothing reports CAN 15
    composites = [Composite("a", "arm", [f"{ADAPTER}#can/1", f"{ADAPTER}#can/15"], title="A")]
    layer, adapter, statuses = world({"1": OK, "2": OK})
    add_composites(layer, composites)
    _, view, rows = composed(composites, layer, statuses)
    assert (view.statuses[0].level, view.statuses[0].code) == (WARN, "ChildMissing")
    assert rows.causes == [composites[0].path]


def test_a_robot_may_list_its_computer_and_it_counts_by_its_own_checks_not_everything_below_it():
    layer, adapter, statuses = world({"1": OK, "2": ERROR})
    composites = [Composite("robot", "robot", ["host", f"{ADAPTER}#can/1"], title="Robot")]
    add_composites(layer, composites)
    # CAN 2 (not in the robot) is in error, and the Computer's rollup says so; the Computer itself reports nothing: not counted
    _, view, _ = composed(composites, layer, statuses)
    assert (view.statuses[0].level, view.statuses[0].message) == (OK, "all 1 ok")
    # a computer plugin's own check is the Computer's own level: warn, and the robot is its consequence
    statuses = statuses + [DiagnosticStatus(level=WARN, name="CPU", path=tree.HOST_PATH, code="CpuHot", stamp=NOW, received=NOW, message="hot")]
    _, view, rows = composed(composites, layer, statuses)
    assert (view.statuses[0].level, view.statuses[0].code, view.statuses[0].caused_by) == (WARN, "ChildWarn", (tree.HOST_PATH,))
    assert tree.HOST_PATH in rows.causes and composites[0].path not in rows.causes


def test_a_composite_with_nothing_to_say_is_unknown_not_green():
    layer, adapter, statuses = world({"1": None})
    composites = [Composite("a", "arm", [f"{ADAPTER}#can/1"], title="A")]
    add_composites(layer, composites)
    _, view, _ = composed(composites, layer, statuses)
    assert (view.statuses[0].level, view.statuses[0].code) == (UNKNOWN, "ChildrenSilent")


def test_a_plugins_own_verdict_makes_the_composite_its_own_cause_beside_its_children():
    layer, adapter, statuses = world({"1": OK, "2": ERROR})
    composites = [Composite("a", "arm", [f"{ADAPTER}#can/1", f"{ADAPTER}#can/2"], title="A")]
    add_composites(layer, composites)
    statuses.append(DiagnosticStatus(level=ERROR, name="A: tracking error", path=composites[0].path, code="TrackingError", stamp=NOW,
                                     received=NOW, message="lagging"))
    _, view, rows = composed(composites, layer, statuses)
    assert view.levels["a"] == ERROR
    assert composites[0].path in rows.causes  # the plugin's verdict has no caused_by: it counts


def test_the_rollup_records_are_valid_records_and_never_stale():
    layer, adapter, statuses = world({"1": ERROR, "2": OK})
    composites = [Composite("a", "arm", [f"{ADAPTER}#can/1", f"{ADAPTER}#can/2"], title="A")]
    add_composites(layer, composites)
    _, view, _ = composed(composites, layer, statuses)
    for status in view.statuses:
        assert validate(status) == [] and status.expected_period_s is None


# ----- a child's value, linked ---------------------------------------------------------------------------------------
def test_a_measurement_may_say_which_entity_and_key_it_comes_from_and_it_survives_the_wire():
    measurement = Measurement("joint 3 angle", 0.25, "rev", source=SourceRef("host/usb1/can/13", "position"))
    assert to_dict(State(path="", name="arm", measurements=(measurement,)))["measurements"][0]["source"] == {
        "path": "host/usb1/can/13", "key": "position"}
    state = State(path="", name="arm", measurements=(measurement, Measurement("x", 1.0, "m")))
    again = from_dict(to_dict(state))
    assert again == state and again.measurements[0].source == SourceRef("host/usb1/can/13", "position") and again.measurements[1].source is None
    assert validate(state) == []
    assert any("source must be" in problem for problem in validate(State(path="", name="s", measurements=(Measurement("a", 1, source=SourceRef("")),))))


# ----- batch planning ------------------------------------------------------------------------------------------------
def action(name: str, danger="moves", scope="node") -> Action:
    return Action(name=name, title=name, description="", danger=danger, scope=scope, confirm="", kind="changes")


def planning(actions: dict, groups: dict | None = None, *, batch="all", missing=False, nested=True):
    layer, adapter, statuses = world({"1": OK, "2": OK, "3": OK, "4": OK})
    refs = [f"{ADAPTER}#can/{n}" for n in (1, 2, 3, 4)] + ([f"{ADAPTER}#can/15"] if missing else [])
    composites = [Composite("top", "rack", ["mid", refs[3]] if nested else refs, batch=batch, title="Top")]
    if nested:
        composites.append(Composite("mid", "arm", refs[:3] + refs[4:], title="Mid"))
    add_composites(layer, composites)
    merged = tree.merge(layer, [], statuses)
    resolved = composition.resolve(composites, merged, layer)
    by_hardware = {f"{adapter}/can/{n}": f"can{n}" for n in (1, 2, 3, 4)}

    def actions_of(path):
        return {a.name: a for a in actions.get(by_hardware.get(path), [])}

    def group_of(path):
        return ("plug", (groups or {}).get(by_hardware.get(path), ""))

    return composites[0], resolved, actions_of, group_of, adapter


def test_a_batch_covers_every_leaf_recursively_in_file_order_and_queues_by_group():
    everything = {f"can{n}": [action("calibrate"), action("clear_faults", "changes")] for n in (1, 2, 3, 4)}
    composite, resolved, actions_of, group_of, adapter = planning(everything, {"can1": "bus", "can2": "bus", "can3": "bus", "can4": "other"})
    plan = composition.plan_batch(composite, "calibrate", resolved, actions_of, group_of)
    assert plan.refused == "" and len(plan.items) == 4 and plan.title == "Calibrate all 4…" and plan.danger == "moves"
    assert [item.child.path.rsplit("/", 1)[-1] for item in plan.items] == ["1", "2", "3", "4"]
    assert [item.node for item in plan.items] == [1, 2, 3, 4]
    assert [item.starts for item in plan.items] == ["now", "after 1 earlier run of bus", "after 2 earlier runs of bus", "now"]
    assert composition.plan_batch(composite, "clear_faults", resolved, actions_of, group_of).danger == "changes"
    assert [p.name for p in composition.offered(composite, resolved, actions_of, group_of)] == ["calibrate", "clear_faults"]


def test_a_batch_is_refused_when_one_leaf_lacks_the_action_and_the_refusal_names_it():
    actions = {f"can{n}": [action("calibrate")] for n in (1, 2, 4)}
    composite, resolved, actions_of, group_of, adapter = planning(actions)
    plan = composition.plan_batch(composite, "calibrate", resolved, actions_of, group_of)
    assert (plan.refused, plan.items) == ("ChildLacksAction", [])
    assert "CAN 3" in plan.message
    assert composition.offered(composite, resolved, actions_of, group_of) == []  # and it is not offered at all


def test_having_takes_the_leaves_that_have_it_and_lists_the_others():
    actions = {f"can{n}": [action("calibrate")] for n in (1, 2, 4)}
    composite, resolved, actions_of, group_of, adapter = planning(actions, batch="having")
    plan = composition.plan_batch(composite, "calibrate", resolved, actions_of, group_of)
    assert plan.refused == "" and len(plan.items) == 3 and [(c.name, why) for c, why in plan.skipped] == [("CAN 3", "offers no calibrate")]
    assert plan.title == "Calibrate all 3…"


def test_a_leaf_that_is_not_here_refuses_all_and_is_skipped_by_having():
    everything = {f"can{n}": [action("calibrate")] for n in (1, 2, 3, 4)}
    composite, resolved, actions_of, group_of, adapter = planning(everything, missing=True)
    plan = composition.plan_batch(composite, "calibrate", resolved, actions_of, group_of)
    assert plan.refused == "ChildMissing" and "CAN 15" in plan.message
    composite, resolved, actions_of, group_of, adapter = planning(everything, batch="having", missing=True)
    plan = composition.plan_batch(composite, "calibrate", resolved, actions_of, group_of)
    assert len(plan.items) == 4 and plan.skipped[0][1] == "not here"


def test_only_actions_that_need_nothing_typed_are_batchable_and_one_board_is_not_a_batch():
    everything = {f"can{n}": [action("set_id", "changes"), action("open_console", "query", "device"), action("set_config", "changes")]
                  for n in (1, 2, 3, 4)}
    composite, resolved, actions_of, group_of, adapter = planning(everything)
    for name in ("set_id", "open_console", "set_config", "not_an_action"):
        assert composition.plan_batch(composite, name, resolved, actions_of, group_of).refused == "NotBatchable"
    only_one = {"can1": [action("calibrate")]}
    composite, resolved, actions_of, group_of, adapter = planning(only_one, batch="having")
    assert composition.plan_batch(composite, "calibrate", resolved, actions_of, group_of).refused == "TooFew"


def test_the_batch_titles_say_the_count():
    assert composition.batch_title("calibrate", 6) == "Calibrate all 6…"
    assert composition.batch_title("check_comms", 3) == "Check communication of all 3"
    assert composition.batch_title("stop_all", 2) == "Stop all 2"
