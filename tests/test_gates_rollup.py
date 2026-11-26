"""RobotDx's roll-up holds a black-box agent's gates: hundreds of specs, one calm answer (feedback fb-f903b0fe).

Zach, 2026-11-24, designing the Gates section of a black-box agent's site: "I think We had mabye in like robot
diagoinstics very nice ways to show this in a roll up type of way? I think thats important beacuse the gates are
almost like the Specs.. basically And you kinda wnat to be passing all of the Specs... and there can be alot alot of
em.." (Daily Note - Nov 24 2026, "Three proposals for each tab of a black-box agent's site"; blackbox_agent's
proposals-gates built on it).

WHY this file exists: the roll-up he liked is ``contract.record.summarize``, written for a device tree. These tests
pin that the same rule, unchanged, rolls up a spec tree (spec -> gates -> nested sections -> tests) at the size he
named (950 tests, the "glance field" count), so a refactor toward hardware-only shapes cannot quietly lose it:

- one calm answer at the top: all pass reads OK; otherwise the worst cause, with a count per level;
- a gate with no verdict of its own (a pure grouping) shows its worst child; a nested section rolls up the same way;
- a test that did not run is UNKNOWN, never green, and a passing gate beside a failing one stays OK;
- one broken gate (its own setup failed) is ONE cause: its tests that could not run are knock-on, listed, not counted;
- the rule is stdlib-only, so any viewer (blackbox_agent's, data-ui's) can import it without RobotDx's host.
"""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract.record import ERROR, OK, UNKNOWN, WARN, DiagnosticStatus, Entity, summarize  # noqa: E402

RECORD = Path(__file__).resolve().parents[1] / "contract" / "record.py"
NOW = 1_800_000_000.0
GATES = {"tracking": 300, "stopping": 250, "limits": 200, "thermal": 150, "docs": 50}  # 950 tests in all
SECTIONS = ("fast", "slow")  # each gate's tests sit in two nested sections: a gate matrix with nested gates


def spec_tree():
    """The spec as entities: spec -> gate -> section -> test. Gates and sections carry no record of their own."""

    entities = [Entity(path="spec", name="Spec", kind="spec")]
    tests = []
    for gate, count in GATES.items():
        entities.append(Entity(path=f"spec/{gate}", name=gate, kind="gate", part_of="spec"))
        for section in SECTIONS:
            entities.append(Entity(path=f"spec/{gate}/{section}", name=f"{gate} {section}", kind="section",
                                   part_of=f"spec/{gate}"))
        for number in range(count):
            section = SECTIONS[number % 2]
            path = f"spec/{gate}/{section}/t{number:03d}"
            entities.append(Entity(path=path, name=f"{gate} t{number:03d}", kind="test", part_of=f"spec/{gate}/{section}"))
            tests.append(path)
    return entities, tests


def result(path: str, level: str = OK, message: str = "passes", code: str = "") -> DiagnosticStatus:
    """A test run's verdict as a record. Event-driven (no expected period): a result never goes stale."""

    return DiagnosticStatus(level=level, name="result", message=message, path=path, stamp=NOW,
                            code=code or ("" if level == OK else "SpecFails"))


def test_the_spec_tree_is_the_size_zach_named():
    entities, tests = spec_tree()
    assert len(tests) == 950


def test_all_pass_reads_one_calm_ok_at_every_level():
    entities, tests = spec_tree()
    started = time.perf_counter()
    rows = summarize(entities, [result(path) for path in tests], NOW)
    assert time.perf_counter() - started < 2.0, "950 specs must roll up interactively"
    for path in ["spec", *(f"spec/{gate}" for gate in GATES), *(f"spec/{gate}/{s}" for gate in GATES for s in SECTIONS)]:
        assert rows[path].own is None, f"{path} is a pure grouping"
        assert rows[path].level == OK, path
        assert dict(rows[path].rollup.counts) == {}, path


def test_a_few_reds_among_many_lead_with_the_worst_and_count_per_level():
    entities, tests = spec_tree()
    failing = {
        "spec/stopping/slow/t011": result("spec/stopping/slow/t011", ERROR, "stop error 4.1 mm > 2 mm limit"),
        "spec/tracking/fast/t040": result("spec/tracking/fast/t040", WARN, "tracking error 0.9 mm, margin 0.1 mm"),
        "spec/tracking/slow/t041": result("spec/tracking/slow/t041", ERROR, "tracking error 2.3 mm > 1 mm limit"),
    }
    rows = summarize(entities, [failing.get(path, result(path)) for path in tests], NOW)

    top = rows["spec"]
    assert top.level == ERROR
    assert dict(top.rollup.counts) == {ERROR: 2, WARN: 1}, "947 passing specs fold away; only the reds are counted"
    assert top.rollup.origin in {"spec/stopping/slow/t011", "spec/tracking/slow/t041"}
    assert "limit" in top.message, "the top says what exactly is failing, not just that something is red"

    assert rows["spec/tracking"].level == ERROR and dict(rows["spec/tracking"].rollup.counts) == {ERROR: 1, WARN: 1}
    assert rows["spec/tracking/fast"].level == WARN, "a nested section takes its own worst child, not its gate's"
    assert rows["spec/tracking/slow"].rollup.origin == "spec/tracking/slow/t041"
    assert rows["spec/stopping"].rollup.message == "stopping t011: stop error 4.1 mm > 2 mm limit"
    for gate in ("limits", "thermal", "docs"):
        assert rows[f"spec/{gate}"].level == OK, f"a passing gate beside failing ones stays green ({gate})"


def test_a_test_that_did_not_run_is_never_green():
    entities, tests = spec_tree()
    missing = "spec/docs/fast/t000"
    rows = summarize(entities, [result(path, UNKNOWN, "no test yet", "NoTest") if path == missing else result(path)
                                for path in tests], NOW)
    assert rows["spec/docs"].level == UNKNOWN
    assert rows["spec"].level == UNKNOWN
    assert dict(rows["spec"].rollup.counts) == {UNKNOWN: 1}


def test_one_broken_gate_is_one_cause_not_one_per_test():
    entities, tests = spec_tree()
    broken = "spec/thermal"
    statuses = [result(broken, ERROR, "thermal fixture failed to start", "FixtureFailed")]
    statuses += [result(path, UNKNOWN, "did not run: fixture down", "NotRun") if path.startswith(broken + "/")
                 else result(path) for path in tests]
    rows = summarize(entities, statuses, NOW)

    top = rows["spec"]
    assert top.level == ERROR
    assert dict(top.rollup.counts) == {ERROR: 1}, "150 tests that could not run are knock-on of one cause"
    assert top.rollup.origin == broken
    assert len(top.rollup.knock_on) == GATES["thermal"]
    assert {cause for _member, cause in top.rollup.knock_on} == {broken}
    assert rows[broken].own == ERROR, "a gate that judges itself keeps its own verdict"


def test_the_rule_is_stdlib_only_so_any_viewer_can_reuse_it():
    tree = ast.parse(RECORD.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported - set(sys.stdlib_module_names)
