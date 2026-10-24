"""The Cad2Robot -> composition generator (tools/cad2robot_composites.py, docs/composition-v1.md section 8): a bundle's joints become the child
references of one robot composite, the adapter taken from a flag, the bundle's bus word or the rig in the expected setup, never guessed; what it
writes loads, and rolls up on a host."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2robot_composites as generator  # noqa: E402
from io_diagnostics import composite as composite_module  # noqa: E402
from io_diagnostics import expected as expected_module  # noqa: E402
from test_composition_host import A, B, CompRig  # noqa: E402

BUNDLE = ROOT / "tests" / "fixtures" / "cad2robot" / "robot.json"
RIG = f'''version = 1

[[rig]]
name = "Cell"

[[rig.device]]
name = "fdcanusb A"
kind = "adapter"
hardware_id = "usb:0483:5740:{A}"
nodes = ["can/1", "can/2", "can/3"]

[[rig.device]]
name = "fdcanusb B"
kind = "adapter"
hardware_id = "usb:0483:5740:{B}"
nodes = ["can/21"]
'''


def manifest(text: str = RIG) -> expected_module.Manifest:
    return expected_module.parse(text)


def build(**options):
    options = {"bus_map": {}, "adapter": "", "manifest": manifest(), "groups": {}, **options}
    return generator.build(generator.load_bundle(BUNDLE), **options)


def test_each_powered_joint_becomes_a_board_on_the_adapter_the_rig_says_in_the_joints_order():
    composites, notes = build()
    assert len(composites) == 1
    robot = composites[0]
    assert (robot.id, robot.kind, robot.title, robot.source) == ("two_arm_cell", "robot", "Two Arm Cell", "cad2robot cad2robot/1.1 Two_Arm_Cell")
    assert robot.children == [f"usb:0483:5740:{A}#can/1", f"usb:0483:5740:{A}#can/2", f"usb:0483:5740:{A}#can/3", f"usb:0483:5740:{B}#can/21"]
    assert notes == ["turntable: no can_id (not a powered joint)"]


def test_what_it_writes_parses_back_and_loads():
    composites, _ = build()
    text = "\n".join(composite_module.dump(composites))
    again, problems = composite_module.parse(tomllib.loads(text).get("composite"))
    assert problems == [] and again == composites


def test_a_joint_it_cannot_place_is_a_note_and_never_a_guess():
    # can/1 on both adapters: two rigs list it, so the bundle cannot say which
    both = RIG.replace('nodes = ["can/21"]', 'nodes = ["can/1", "can/21"]')
    composites, notes = build(manifest=manifest(both))
    assert f"usb:0483:5740:{A}#can/1" not in composites[0].children and f"usb:0483:5740:{B}#can/1" not in composites[0].children
    assert any(note.startswith("base_yaw: can/1 is on an adapter I cannot tell: 2 adapters list it") for note in notes), notes
    with pytest.raises(generator.BundleError, match="no joint could be placed.*no rig in the expected setup lists it"):
        build(manifest=None)  # nothing placed at all: refused, not an empty composite


def test_an_adapter_flag_or_the_bundles_own_bus_word_decides():
    composites, _ = build(manifest=None, adapter="usb:0483:5740:ZZZZ0000")
    assert set(ref.split("#")[0] for ref in composites[0].children) == {"usb:0483:5740:ZZZZ0000"}
    composites, _ = build(manifest=None, adapter="usb:0483:5740:ZZZZ0000", bus_map={"bus_b": f"usb:0483:5740:{B}"})
    assert composites[0].children[-1] == f"usb:0483:5740:{B}#can/21" and composites[0].children[0] == "usb:0483:5740:ZZZZ0000#can/1"


def test_a_group_is_a_sub_composite_the_robot_contains_and_a_wrong_name_is_refused():
    composites, notes = build(groups={"arm": ["base_yaw", "shoulder", "elbow"]})
    robot, arm = composites
    assert (arm.id, arm.kind, arm.title) == ("two_arm_cell/arm", "arm", "Arm") and len(arm.children) == 3
    assert robot.children == ["two_arm_cell/arm", f"usb:0483:5740:{B}#can/21"]
    assert composite_module.check(composites) == []
    with pytest.raises(generator.BundleError, match="names joints the bundle does not have: nobody"):
        build(groups={"arm": ["nobody"]})


def test_the_bundle_must_be_a_cad2robot_bundle(tmp_path):
    bad = tmp_path / "robot.json"
    bad.write_text(json.dumps({"schema": "something/1", "joints": [{"name": "a", "can_id": 1}]}))
    with pytest.raises(generator.BundleError, match="is not a cad2robot/1.x bundle"):
        generator.load_bundle(bad)
    bad.write_text(json.dumps({"schema": "cad2robot/1.1", "joints": []}))
    with pytest.raises(generator.BundleError, match="no joints"):
        generator.load_bundle(bad)


def test_write_replaces_its_own_tables_and_keeps_everything_else(tmp_path):
    target = tmp_path / "expected.toml"
    target.write_text(RIG + '\n[[composite]]\nid = "mine"\nkind = "rack"\nchildren = ["host/somewhere"]\n')
    composites, _ = build()
    first = generator.write_into(target, composites)
    assert "1 composite(s) written, 0 of the same source replaced, 1 others kept" in first
    second = generator.write_into(target, composites)  # again: replaced, not duplicated
    assert "1 of the same source replaced" in second
    loaded = expected_module.parse(target.read_text())
    assert [c.id for c in loaded.composites] == ["mine", "two_arm_cell"] and [rig.name for rig in loaded.rigs] == ["Cell"]
    assert loaded.composite_problems == []
    # a clash with a hand-written id is refused whole and the file is not touched
    target.write_text(RIG + f'\n[[composite]]\nid = "mine"\nkind = "rack"\nchildren = ["usb:0483:5740:{A}#can/1"]\n')
    before = target.read_text()
    with pytest.raises(generator.BundleError, match="would not load"):
        generator.write_into(target, composites)
    assert target.read_text() == before
    # a new file is written too
    fresh = tmp_path / "new" / "expected.toml"
    generator.write_into(fresh, composites)
    assert expected_module.parse(fresh.read_text()).composites[0].id == "two_arm_cell"


def test_the_command_prints_the_tables_and_the_notes_and_exits_2_on_a_bad_bundle(tmp_path):
    expected_file = tmp_path / "expected.toml"
    expected_file.write_text(RIG)
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "cad2robot_composites.py"), str(BUNDLE), "--expected", str(expected_file),
                           "--group", "arm=base_yaw,shoulder,elbow"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("# generated from robot.json: cad2robot cad2robot/1.1 Two_Arm_Cell") and "# not placed: turntable" in done.stdout
    parsed, problems = composite_module.parse(tomllib.loads(done.stdout.split("# not placed")[0]).get("composite"))
    assert problems == [] and [c.id for c in parsed] == ["two_arm_cell", "two_arm_cell/arm"]
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "cad2robot_composites.py"), str(bad)], capture_output=True, text=True)
    assert done.returncode == 2 and "is not a cad2robot/1.x bundle" in done.stderr


def test_a_generated_composition_loads_on_a_host_and_rolls_its_boards_up(tmp_path):
    """The two adapters of the host test (AAAA1111, BBBB2222) with the bundle's joints on them: CAN 1 of A is in fault."""

    composites, _ = generator.build(generator.load_bundle(BUNDLE), bus_map={"bus_b": f"usb:0483:5740:{B}"}, adapter=f"usb:0483:5740:{A}",
                                    manifest=None, groups={"arm": ["base_yaw", "shoulder", "elbow"]})
    # wrist is can/21, which the fixture adapter B does not have: that child is absent, and says so
    text = "\n".join(composite_module.dump(composites))
    made = CompRig(tmp_path, text, arm_plugin=False)
    try:
        robot = made.checks("host/composite-two_arm_cell")["Two Arm Cell: children"]
        arm = made.checks("host/composite-two_arm_cell-arm")["Arm: children"]
        assert arm["level"] == "error" and arm["caused_by"] == [made.joint(A, 1)]
        assert robot["level"] == "error" and "not here" in robot["message"] and "CAN 21" in robot["message"]
    finally:
        made.stop()
