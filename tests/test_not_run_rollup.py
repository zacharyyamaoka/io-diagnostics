"""A stock check that did NOT RUN says nothing about its entity (io_diagnostics/not_run.py). Its row stays in the drill-in as a grey
unknown (never OK, with its own reason and ``not_run: true``), and it sets no level: not the entity's, not a parent's, not the summary's
counts, not ``summary.attention``. A real unknown (a driver's BrsNotTested) still rolls up, and a real warn or error still wins.

Over the state fixture's joint with two boards (a real subprocess that never touches hardware): CAN 2 answers and its firmware is the
latest, so its own checks are all OK; CAN 1 carries the fixture's driver fault. The not-run rows are put where the beat puts them
(``host.stock_results``) with the codes ``stock_diagnostics`` emits."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from contract.record import DiagnosticStatus
from io_diagnostics import stock_diagnostics as sd
from io_diagnostics import tree
from io_diagnostics.host import Clock, Host

from test_host_support import FakeOs, standard_skeleton, usb_device
from test_stock_diagnostics import JOINT, PLUGIN

NO_SOURCE_MESSAGE = "no mode and fault codes source on CAN 2: its State does not report fault; a driver's State would provide it"
SHORT_HISTORY = "the State history holds 2 s (3 samples); this diagnostic looks at a 60 s window with at least 2 samples"


@pytest.fixture
def host(tmp_path):
    directory = tmp_path / "plugins.d"
    directory.mkdir()
    (directory / "state_joint.toml").write_text(
        f'name = "state_joint"\ncommand = ["{{python}}", "{PLUGIN}", "--role", "joint"]\ntimeout_s = 10\nperiod_s = 30\n'
        f'matches = ["usb:{JOINT[0]}:{JOINT[1]}"]\nenv = {{ STATE_PERIOD_S = "0.05", STATE_NODES = "2" }}\n')
    config = tmp_path / "config"
    config.mkdir()
    made = Host(plugins_dir=directory, clock=Clock(), os_source=FakeOs(standard_skeleton(usb_device("9-1", *JOINT, "SER1", label="fdcanusb")), {}),
                watch_grace_s=30.0, frames_dir=tmp_path / "frames", settings_file=config / "settings.toml", diagnostics_beat_s=0.5)
    made.run_all_once()
    made.adapter = made.layer.device_paths["usb:9-1"]
    made.board1, made.board2 = made.adapter + "/can/1", made.adapter + "/can/2"
    yield made
    made.stop()


def stock(host, path: str, title: str, code: str, message: str = "", *, level: str = "unknown", source: str = sd.SOURCE) -> None:
    """What the beat posts for a check: a status of the stock source (``expected_period_s`` the beat), into ``host.stock_results``."""

    now = host.clock.now()
    name = f"{host.view().merged.entities[path].name}: {title}"
    status = DiagnosticStatus(level=level, name=name, message=message or code, code=code, path=path, source=source, stamp=now, received=now,
                              expected_period_s=0.5)
    with host.lock:
        host.stock_results[(path, name)] = status


def row(host, path: str) -> dict:
    stack = [host.state()["tree"]]
    while stack:
        node = stack.pop()
        if node["path"] == path:
            return node
        stack += node["children"]
    raise KeyError(path)


def attention_paths(host) -> list[str]:
    return [item["path"] for item in host.state()["summary"]["attention"]]


def checks(host, path: str) -> dict:
    return {check["name"].split(": ", 1)[-1]: check for check in host.node(path)["checks"]}


def add_not_run(host, path: str) -> None:
    stock(host, path, "mode and fault codes", "NotWired", NO_SOURCE_MESSAGE)
    stock(host, path, "board temperature", "NotEnoughHistory", SHORT_HISTORY)


def test_a_board_whose_real_checks_are_ok_stays_ok_beside_checks_that_did_not_run(host):
    assert row(host, host.board2)["level"] == "ok"
    before = host.state()["summary"]
    add_not_run(host, host.board2)
    after = host.state()
    node = row(host, host.board2)
    assert node["level"] == "ok" and node["own"]["level"] == "ok" and node["hint"] is None
    assert host.board2 not in attention_paths(host)
    assert after["summary"]["counts"] == before["counts"]
    assert after["summary"]["level"] == before["level"]
    # the adapter above it: a rollup that did not move
    assert row(host, host.adapter)["rollup"]["counts"].get("unknown", 0) == 0
    assert row(host, host.adapter)["level"] != "unknown"


def test_the_rows_that_did_not_run_stay_in_the_drill_in_as_unknown_never_ok(host):
    add_not_run(host, host.board2)
    shown = {title: check for title, check in checks(host, host.board2).items() if title in ("mode and fault codes", "board temperature")}
    assert set(shown) == {"mode and fault codes", "board temperature"}
    for check in shown.values():
        assert check["effective_level"] == "unknown" and check["level"] == "unknown" and check["not_run"] is True
    assert shown["mode and fault codes"]["code"] == "NotWired" and "its State does not report fault" in shown["mode and fault codes"]["message"]
    assert shown["board temperature"]["code"] == "NotEnoughHistory" and "60 s window" in shown["board temperature"]["message"]
    # every other check says it ran
    assert all(check["not_run"] is False for title, check in checks(host, host.board2).items() if title not in shown)
    on_the_wire = host.checks_json()["checks"][host.board2]
    assert sum(1 for check in on_the_wire if check["not_run"]) == 2
    assert row(host, host.board2)["checks"] == len(host.node(host.board2)["checks"])  # still listed among the entity's checks


def test_a_real_unknown_check_still_rolls_up_as_unknown(host):
    add_not_run(host, host.board2)
    stock(host, host.board2, "configuration", "BrsNotTested", "the board's configuration could not be read", source="moteus_servo")
    assert row(host, host.board2)["level"] == "unknown", row(host, host.board2)
    assert host.board2 in attention_paths(host)
    assert host.state()["summary"]["counts"].get("unknown") == 1
    assert checks(host, host.board2)["configuration"]["not_run"] is False


def test_a_real_warn_or_error_beats_the_rows_that_did_not_run(host):
    add_not_run(host, host.board2)
    stock(host, host.board2, "board temperature high", "OverTemperature", "board 71 Cel", level="warn")
    assert row(host, host.board2)["level"] == "warn" and host.board2 in attention_paths(host)
    stock(host, host.board2, "bus voltage", "Brownout", "9 V", level="error")
    assert row(host, host.board2)["level"] == "error"
    # CAN 1 carries the fixture's driver fault: it stays an error with the not-run rows beside it
    add_not_run(host, host.board1)
    assert row(host, host.board1)["level"] == "error" and host.board1 in attention_paths(host)


def test_an_entity_with_only_rows_that_did_not_run_is_not_ok(host):
    """A board nothing else reports on: judged on the real tree with only its not-run rows left, and with nothing at all. The same level
    either way (no data: no level, grey), never OK, and the rows still listed."""

    now = host.clock.now()
    merged = host.view().merged
    rows_of = {path: [status for status in merged.statuses if status.path == path] for path in (host.board2,)}
    assert rows_of[host.board2], "the premise: CAN 2 has checks of its own"
    nothing = dataclasses.replace(merged, statuses=[status for status in merged.statuses if status.path != host.board2])
    add_not_run(host, host.board2)
    only = dataclasses.replace(merged, statuses=[*nothing.statuses, *(status for key, status in host.stock_results.items() if key[0] == host.board2)])
    bare, held = tree.node_json(host.board2, tree.build_rows(nothing, now), host.layer, {}, nested=False), \
        tree.node_json(host.board2, tree.build_rows(only, now), host.layer, {}, nested=False)
    assert held["level"] == bare["level"] and held["level"] != "ok", (bare["level"], held["level"])
    assert held["own"]["level"] is None and held["checks"] == 2 and bare["checks"] == 0
    assert tree.build_rows(only, now).causes == tree.build_rows(nothing, now).causes


def test_a_check_that_did_not_run_and_then_went_stale_still_says_nothing(host):
    add_not_run(host, host.board2)
    real = host.clock.now
    host.clock.now = lambda: real() + 600  # the beat stopped: a 0.5 s promise, ten minutes on
    shown = [check for check in host.node(host.board2)["checks"] if check["code"] in ("NotWired", "NotEnoughHistory")]
    assert len(shown) == 2 and all(check["effective_level"] == "stale" and check["not_run"] for check in shown)
    assert host.board2 not in attention_paths(host)


def test_the_health_bar_of_a_board_does_not_paint_what_did_not_run(host):
    add_not_run(host, host.board2)
    host.history.sync(sd.SOURCE, [status for (path, _name), status in host.stock_results.items() if path == host.board2], host.clock.now())
    bar = next(item for item in host.tree_bar_json()["rows"] if item["path"] == host.board2)
    assert bar["basis"] == "own" and bar["statuses"] == len(host.node(host.board2)["checks"]) - 2
    assert "unknown" not in str(bar["bar"])


def test_every_code_the_stock_runner_can_not_run_with_is_in_the_one_predicate():
    from io_diagnostics import not_run

    tree = ast.parse(Path(sd.__file__).read_text())
    constants = {name: getattr(sd, name) for name in dir(sd) if isinstance(getattr(sd, name, None), str)}
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
        if callee == "_not_run" and len(node.args) >= 3:
            code = node.args[2]
        elif callee == "_Missing" and node.args:
            code = node.args[0]
        else:
            continue
        if isinstance(code, ast.Constant):
            seen.add(code.value)
        elif isinstance(code, ast.Name) and code.id in constants:
            seen.add(constants[code.id])
        # a code passed on from a _Missing (``missing.code``) is covered by the _Missing calls above
    seen |= set(sd.NO_SOURCE_CODES)  # emitted through GAP_CODE, which this scan of literal names cannot see
    assert {"NotWired", "NotMeasured", "NoState", "NotStreaming", "NoDecoder", "NotEnoughHistory", "NumpyMissing", "DiagnosticRaised"} <= seen
    assert seen <= set(not_run.NOT_RUN_CODES), seen - set(not_run.NOT_RUN_CODES)
    assert not_run.SOURCE == sd.SOURCE


def test_the_predicate_is_the_stock_sources_unknowns_and_nothing_else():
    from io_diagnostics.not_run import is_not_run

    base = dict(name="x", path="p", message="m")
    assert is_not_run(DiagnosticStatus(level="unknown", code="NotWired", source=sd.SOURCE, **base))
    assert is_not_run(DiagnosticStatus(level="unknown", code="NoValue", source=sd.SOURCE, **base))  # the library's own not_run verdict
    assert not is_not_run(DiagnosticStatus(level="unknown", code="BrsNotTested", source="moteus_servo", **base))
    assert not is_not_run(DiagnosticStatus(level="unknown", code="NotWired", source="some_driver", **base))
    assert not is_not_run(DiagnosticStatus(level="warn", code="OverTemperature", source=sd.SOURCE, **base))
    assert not is_not_run(DiagnosticStatus(level="ok", source=sd.SOURCE, **base))
