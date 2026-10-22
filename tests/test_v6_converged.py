"""V6 converged: the two rules the tree owns (staleness, roll-up) and the 18:25 frame they produce."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

V6 = Path(__file__).resolve().parents[1] / "prototypes" / "v6-converged"
sys.path.insert(0, str(V6))

from tree import Entity, level_at, rollup  # noqa: E402

T0 = dt.datetime(2026, 10, 21, 18, 25, 0)


def record(path: str, level: str = "ok", *, period: float | None = 1.0, age: float = 0.0, children=(), message="") -> Entity:
    return Entity(path, path.rsplit("/", 1)[-1], "chip", "node", level=level, message=message or level, label=path.rsplit("/", 1)[-1],
                  expected_period_s=period, last_seen=T0 - dt.timedelta(seconds=age) if period else None, children=list(children))


def test_stale_only_after_three_expected_periods():
    assert level_at(record("a", period=1.0, age=2.9), T0) == "ok"
    assert level_at(record("a", period=1.0, age=3.0), T0) == "ok"  # exactly 3x is still fresh
    assert level_at(record("a", period=1.0, age=3.1), T0) == "stale"
    assert level_at(record("a", "error", period=180.0, age=541), T0) == "stale"  # a stale ERROR is no longer a live claim


def test_event_driven_records_never_go_stale():
    assert level_at(record("usb", period=None), T0) == "ok"
    assert level_at(record("usb", "warn", period=None), T0) == "warn"


def test_parent_rolls_up_worst_child_and_names_where_it_came_from():
    fault = record("host/bus/15/driver", "error", period=None, message="fault 33")
    node = record("host/bus/15", children=[fault])
    quiet = record("host/bus/1", period=1.0, age=10)  # stale
    bus = record("host/bus", children=[record("host/bus/14"), quiet, node])
    rolled = rollup(record("host", period=None, children=[bus]), T0)
    assert rolled.level == "error" and rolled.origin is fault
    assert rolled.message == "driver: fault 33"
    assert rolled.counts["stale"] == 1 and rolled.counts["error"] == 1


def test_rank_is_ok_warn_stale_error_and_stale_is_not_promoted():
    stale_child = record("p/a", period=1.0, age=10)
    warn_child = record("p/b", "warn")
    assert rollup(record("p", children=[warn_child, stale_child]), T0).level == "stale"
    assert rollup(record("p", children=[warn_child]), T0).level == "warn"
    assert rollup(record("p", children=[record("p/c")]), T0).level == "ok"
    own_error = record("p", "error", children=[stale_child])
    assert rollup(own_error, T0).origin is own_error


def test_staleness_ticks_with_the_clock_in_the_rollup():
    child = record("p/a", period=1.0, age=0.0)
    parent = record("p", children=[child])
    assert rollup(parent, T0).level == "ok"
    assert rollup(parent, T0 + dt.timedelta(seconds=3.5)).level == "stale"


@pytest.mark.skipif(not Path("/archive/datasets/bam_rig/rack_inventory").is_dir(), reason="rack_inventory not on this machine")
def test_busy_frame_boards_go_stale_at_1830_41_from_the_real_poller():
    import frames
    from tree import walk

    def boards(now):
        root = frames.build("F2", now)
        return {entity.name: level_at(entity, now) for entity in walk(root) if entity.kind == "node"}

    before = boards(dt.datetime(2026, 10, 21, 18, 30, 40))
    after = boards(dt.datetime(2026, 10, 21, 18, 30, 42))
    assert before == {"CAN 1": "ok", "CAN 14": "ok", "CAN 15": "ok"}
    assert after == {"CAN 1": "stale", "CAN 14": "stale", "CAN 15": "stale"}
    holder, since = frames.holder_at("272813EF", dt.datetime(2026, 10, 21, 18, 25))
    assert holder == "tview pid 640623" and since.strftime("%H:%M:%S") == "18:24:46"


def test_chip_model_is_the_appindicator_shape():
    import activity

    tree = {"kind": "host", "path": "host", "children": [
        {"kind": "device", "path": "host/a", "name": "fdcanusb 272813EF", "level": "error", "message": "CAN 15 driver: fault 33", "children": []},
        {"kind": "device", "path": "host/b", "name": "D405 ·4411", "level": "ok", "message": "OK", "children": []}]}
    chip = activity.chip(tree, [{"time": "18:11:42", "text": "fdcanusb 28B3A25D plugged on 1-5"}], "http://127.0.0.1:8812/")
    assert chip["label"] == "IO 1 ⚠" and chip["icon"] == "attention"
    assert all(set(item) <= {"text", "enabled", "children", "action", "sep"} for item in chip["menu"])  # contract/indicator.py items
