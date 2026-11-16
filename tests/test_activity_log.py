"""The Activity log (docs/activity-log-v1.md): the Event as the log record, the stock ROS log projection, the folding
rules (coalescing, a fault folded onto its cause, "found at start", status transitions, a run as one row), and the
events kept on disk across a restart, with the cap.

The scenarios are the rack's own (2026-10-21, tests/fixtures/plugins F1): CAN 15's fault 33 and the bus error-passive
that each showed twice, four WARN faults that confirmed in one second at start, ProbeBlocked five times a minute.
"""

from __future__ import annotations

import dataclasses
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract.record import (  # noqa: E402
    EVENT_SEVERITIES, ROS_LOG_LEVEL, Event, from_dict, from_ros_log, to_dict, to_ros_log, validate,
)
from io_diagnostics import eventlog, logview  # noqa: E402
from io_diagnostics.activity import event_id  # noqa: E402
from io_diagnostics.faults import FOUND_AT_START_S, Faults  # noqa: E402

T0 = 1_792_631_000.0  # 2026-10-21 18:03:20 -07:00
CAN = "host/usb1/port-1-5/usb-1-5/can"


def ev(code, at=0.0, severity="info", path=CAN, message=None, source="moteus_servo", seq=None, **args):
    return Event(code, T0 + at, severity, message if message is not None else f"{code} on {path}", path=path, source=source,
                 seq=seq, args=dict(args))


def confirmed(fault, at, path=CAN, severity="error", occurrence=1, seq=None, **args):
    return ev("FaultConfirmed", at, severity, path, f"{fault} confirmed on {path}", "fault_manager", seq,
              fault=fault, occurrence=occurrence, **args)


def healed(fault, at, path=CAN, occurrence=1, seq=None):
    return ev("FaultHealed", at, "info", path, f"{fault} healed on {path}", "fault_manager", seq, fault=fault, occurrence=occurrence)


def transition(path, to, at, name="CAN 15: bus", before="ok", code=""):
    return ev("LevelChanged", at, "warn", path, f"{name}: {before} → {to}", "io_diagnostics.history",
              id=f"level:{path}|{name}|{T0 + at:.3f}|{to}", status=name, **{"from": before, "to": to, "status_code": code})


def rows_of(events, **options):
    return logview.fold(events, **options)


# ----- the Event as the log record (record v0.2) ---------------------------------------------------------------
def test_the_event_gains_the_log_fields_with_defaults_that_change_nothing():
    event = Event("Plugged", T0, message="plugged")
    assert (event.received, event.count, event.last_stamp) == (None, 1, None)
    assert to_dict(event) == {"schema": "diagnostics-record/0", "record": "event", "code": "Plugged", "stamp": T0, "message": "plugged"}
    # a v0.1 line (none of the new fields) reads exactly as before, and the new fields round-trip through JSON
    assert from_dict(json.loads(json.dumps(to_dict(event)))) == event
    full = dataclasses.replace(event, received=T0 + 1, count=5, last_stamp=T0 + 30, severity="fatal")
    assert from_dict(json.loads(json.dumps(to_dict(full)))) == full


def test_severities_are_the_five_ros_log_levels_and_validate_checks_the_new_fields():
    assert EVENT_SEVERITIES == ("debug", "info", "warn", "error", "fatal")
    assert [ROS_LOG_LEVEL[name] for name in EVENT_SEVERITIES] == [10, 20, 30, 40, 50]
    assert validate(Event("Plugged", T0, "debug")) == [] and validate(Event("Plugged", T0, "fatal")) == []
    assert any("severity" in problem for problem in validate(Event("Plugged", T0, "critical")))
    assert any("count" in problem for problem in validate(Event("Plugged", T0, count=0)))
    assert any("last_stamp" in problem for problem in validate(Event("Plugged", T0, count=2, last_stamp=T0 - 1)))
    assert validate(Event("Plugged", T0, count=2, last_stamp=T0 + 4)) == []


def test_to_ros_log_puts_each_field_where_a_stock_viewer_shows_it():
    stock = to_ros_log(Event("BoardFault", T0 + 0.25, "error", "CAN 15 fault 33", path=f"{CAN}/15", source="moteus_servo", seq=7,
                             hardware_id="usb:0483:5740:28B3A25D", args={"serial": "x"}, received=T0 + 9))
    assert stock == {"stamp": {"sec": int(T0), "nanosec": 250_000_000}, "level": 40, "name": f"{CAN.replace('/', '.')}.15",
                     "msg": "CAN 15 fault 33", "file": "moteus_servo", "function": "BoardFault", "line": 7}
    assert set(stock) == {"stamp", "level", "name", "msg", "file", "function", "line"}  # args, hardware_id, received: stripped
    assert [to_ros_log(Event("x", T0, severity))["level"] for severity in EVENT_SEVERITIES] == [10, 20, 30, 40, 50]
    assert to_ros_log(Event("Probed", T0, count=5, last_stamp=T0 + 4, message="probed"))["msg"] == "probed (x5)"
    with pytest.raises(ValueError):
        to_ros_log(Event("x", None))
    with pytest.raises(ValueError):
        to_ros_log(Event("x", T0, "critical"))


def test_a_stock_rosout_line_comes_in_as_a_raw_line_on_its_entity():
    line = {"stamp": {"sec": 1_792_631_000, "nanosec": 500_000}, "level": 30, "name": "arm.joint1", "msg": "limit close",
            "file": "arm_node.cpp", "function": "check", "line": 88}
    event = from_ros_log(line)
    assert (event.severity, event.path, event.message, event.source, event.seq) == ("warn", "arm/joint1", "limit close", "arm_node.cpp", 88)
    assert event.stamp == pytest.approx(1_792_631_000.0005)
    attached = from_ros_log({**line, "function": ""}, path="host/usb1/port-1-5/usb-1-5", received=T0)
    assert (attached.code, attached.path, attached.received) == ("", "host/usb1/port-1-5/usb-1-5", T0)
    # rclpy delivers the level byte as bytes; levels between the five fall to the band below
    assert from_ros_log({**line, "level": bytes([10])}).severity == "debug"
    assert [from_ros_log({**line, "level": level}).severity for level in (0, 19, 25, 35, 45, 50, 60)] == \
        ["debug", "debug", "info", "warn", "error", "fatal", "fatal"]


def stock_messages():
    rng = random.Random(7)
    for _ in range(300):
        yield {"stamp": {"sec": rng.randrange(1_700_000_000, 1_900_000_000), "nanosec": rng.randrange(0, 1_000_000) * 1000},
               "level": rng.choice([10, 20, 30, 40, 50]), "name": ".".join(rng.choice(["host", "usb1", "port-1-5", "can", "a_b"]) for _ in range(rng.randrange(0, 4))),
               "msg": rng.choice(["", "x", "limit close [Fault]", "two\nlines", "ünïcode ✓"]), "file": rng.choice(["", "arm_node.cpp", "/a/b.py"]),
               "function": rng.choice(["", "check", "BoardFault", "main"]), "line": rng.choice([0, 1, 88, 4_000_000])}


def test_stock_to_v0_to_stock_is_the_identity_for_ros_log_lines():
    # the same proof as to_diagnostic_status: whatever a stock stream says survives a trip through the record
    for message in stock_messages():
        assert to_ros_log(from_ros_log(message)) == message


def test_v0_to_stock_to_v0_keeps_what_a_stock_line_can_hold():
    original = Event("BoardFault", T0 + 0.5, "warn", "CAN 15 fault 33", path=f"{CAN}/15", source="moteus_servo", seq=7, hardware_id="usb:1",
                     args={"serial": "x"}, received=T0 + 3)
    back = from_ros_log(to_ros_log(original), hardware_id="usb:1", args={"serial": "x"}, received=T0 + 3)
    assert back == original
    # an event with no seq or source and no code (a raw line) survives too
    raw = Event("", T0, "info", "usb 1-5: new full-speed USB device number 8", path="host/usb1/port-1-5")
    assert from_ros_log(to_ros_log(raw)) == raw


# ----- the host's fault marks ------------------------------------------------------------------------------------
def test_a_confirmation_in_the_hosts_first_reports_is_marked_and_a_later_one_is_not():
    from contract.record import DiagnosticStatus

    faults = Faults()
    failing = lambda name, at: DiagnosticStatus("error", name, "bad", path=f"{CAN}/{name}", code="Bad", stamp=T0 + at, received=T0 + at)  # noqa: E731
    first = faults.observe([failing("a", 0)], T0)
    later = faults.observe([failing("b", 0)], T0 + FOUND_AT_START_S + 1)
    assert [event.code for event in first] == ["FaultConfirmed"] and first[0].args["at_start"] == round(T0, 3)
    assert [event.code for event in later] == ["FaultConfirmed"] and "at_start" not in later[0].args
    assert faults.events[0].args["at_start"] == round(T0, 3)  # the host's own list carries the mark, so the log does


# ----- coalescing --------------------------------------------------------------------------------------------------
def probe_blocked(at):
    return ev("ProbeBlocked", at, "warn", message="the bus is held by tview (pid 41)", source="moteus_servo.diagnostics",
              seq=int(at) + 1)


def test_identical_repeats_become_one_row_with_a_count():
    rows = rows_of([probe_blocked(at) for at in (0, 12, 25, 41, 58)])
    assert len(rows) == 1
    row = rows[0].event
    assert (row.count, row.stamp, row.last_stamp, row.severity) == (5, T0, T0 + 58, "warn")
    assert event_id(row) == event_id(probe_blocked(0))  # the row keeps the first one's id: a page's row does not change identity when a repeat arrives


def test_a_gap_over_a_minute_or_any_difference_keeps_rows_apart():
    rows = rows_of([probe_blocked(0), probe_blocked(30), probe_blocked(30 + logview.COALESCE_GAP_S + 1)])
    assert sorted(row.event.count for row in rows) == [1, 2]
    other_message = dataclasses.replace(probe_blocked(10), message="the bus is held by tview (pid 99)", seq=99)
    other_path = dataclasses.replace(probe_blocked(10), path=f"{CAN}/2", seq=98)
    other_severity = dataclasses.replace(probe_blocked(10), severity="error", seq=97)
    assert len(rows_of([probe_blocked(0), other_message, other_path, other_severity])) == 4


def test_state_changes_never_coalesce():
    plugs = [ev("Plugged", at, path="host/usb1/port-1-5", message="fdcanusb plugged on 1-5", source="io_facts.journal", id=f"p{at}") for at in (0, 20)]
    boots = [ev("Booted", at, path="host", message="PC booted", source="io_facts.journal", id=f"b{at}") for at in (0, 20)]
    assert len(rows_of(plugs + boots)) == 4


# ----- a fault folds onto its cause -------------------------------------------------------------------------------
def test_a_confirmed_fault_rides_on_the_event_that_caused_it():
    cause = ev("BusErrorPassive", 0, "warn", message="28B3A25D bus error-passive", seq=1)
    rows = rows_of([cause, confirmed("NoAck", 0, seq=6)])
    assert len(rows) == 1
    row = rows[0]
    assert row.event.code == "BusErrorPassive"
    assert [(badge["label"], badge["fault"], badge["state"]) for badge in row.badges] == [("fault confirmed", "NoAck", "confirmed")]
    assert [member.code for member in row.members] == ["FaultConfirmed"]


def test_the_cause_is_the_closest_event_before_it_on_the_same_entity_and_warn_or_worse():
    events = [ev("BoardFault", -3, "error", seq=1), ev("BoardFault", -1, "error", message="the later one", seq=2),
              ev("Probed", -1, "info", seq=3), ev("BoardFault", -1, "error", path=f"{CAN}/2", seq=4)]
    rows = rows_of(events + [confirmed("MoteusFault.33", 0, seq=9)])
    badged = [row for row in rows if row.badges]
    assert [row.event.message for row in badged] == ["the later one"]
    assert len(rows) == 4  # nothing else was folded


def test_an_info_event_is_never_the_cause_of_a_fault():
    rows = rows_of([ev("Probed", -1, "info", seq=3), confirmed("NoAck", 0, seq=9)])
    assert len(rows) == 2 and not any(row.badges for row in rows)


def test_a_confirmation_too_long_after_the_event_is_not_its_cause():
    rows = rows_of([ev("BoardFault", 0, "error", seq=1), confirmed("MoteusFault.33", logview.CAUSE_WINDOW_S + 5, seq=9)])
    assert len(rows) == 2 and not any(row.badges for row in rows)


def test_healing_updates_the_badge_and_stays_a_row_that_points_back():
    cause = ev("BoardFault", 0, "error", seq=1)
    rows = rows_of([cause, confirmed("MoteusFault.33", 0, seq=2), healed("MoteusFault.33", 90, seq=3)])
    cause_row = next(row for row in rows if row.event.code == "BoardFault")
    assert [(badge["state"], badge["ended"]) for badge in cause_row.badges] == [("healed", T0 + 90)]
    heal_row = next(row for row in rows if row.event.code == "FaultHealed")
    assert heal_row.event.args["confirmed_row"] == cause_row.id
    # a second occurrence after healing is its own badge, still "confirmed"
    again = rows_of([cause, confirmed("MoteusFault.33", 0, seq=2), healed("MoteusFault.33", 90, seq=3),
                     ev("BoardFault", 200, "error", seq=4), confirmed("MoteusFault.33", 200, occurrence=2, seq=5)])
    assert sorted(badge["state"] for row in again for badge in row.badges) == ["confirmed", "healed"]


def test_a_confirmation_with_no_cause_is_a_row_of_its_own():
    rows = rows_of([confirmed("NoAck", 5, seq=6)])
    assert [row.event.code for row in rows] == ["FaultConfirmed"]


# ----- found at start ----------------------------------------------------------------------------------------------
def at_start(fault, at, path, start=1000.0, **options):
    return confirmed(fault, at, path=path, severity="warn", seq=int(at * 10), at_start=start, **options)


def test_faults_confirmed_in_the_first_reports_are_one_found_at_start_row():
    events = [at_start("UpdateAvailable", 0, "host/usb6/port-6-1"), at_start("CameraMoved", 0.1, "host/usb6/port-6-1"),
              at_start("UpdateAvailable", 0.2, "host/usb6/port-6-2"), at_start("PortFlapping", 0.3, "host/usb1/port-1-7")]
    rows = rows_of(events)
    assert len(rows) == 1
    row = rows[0]
    assert row.event.code == "FoundAtStart" and row.event.path == "host" and row.event.severity == "warn"
    assert row.event.message.startswith("Found at start: 4 faults already confirmed (UpdateAvailable, CameraMoved, PortFlapping)")
    assert [fault["path"] for fault in row.event.args["faults"]] == ["host/usb6/port-6-1", "host/usb6/port-6-1", "host/usb6/port-6-2", "host/usb1/port-1-7"]
    assert len(row.members) == 4


def test_each_host_start_gets_its_own_group_and_a_cause_still_wins():
    first = [at_start("A", 0, "host/a", start=1000.0), at_start("B", 0.1, "host/b", start=1000.0)]
    second = [at_start("A", 600, "host/a", start=1600.0)]
    assert sorted(len(row.members) for row in rows_of(first + second)) == [1, 2]
    cause = ev("BusErrorPassive", 0, "warn", seq=1)
    rows = rows_of([cause, at_start("NoAck", 0, CAN)])
    assert [row.event.code for row in rows] == ["BusErrorPassive"] and rows[0].badges


# ----- status transitions -------------------------------------------------------------------------------------------
def test_a_warn_or_error_transition_rides_on_its_fault_row_and_has_none_of_its_own():
    cause = ev("BoardFault", 0, "error", seq=1)
    rows = rows_of([transition(f"{CAN}/15", "error", -0.5, code="MoteusFault.33"), cause, confirmed("MoteusFault.33", 0, path=CAN, seq=2)])
    # the fault is on CAN, the transition on CAN/15: different entities, so it stays a debug row (it cannot ride on a row of another entity)
    assert sorted(row.event.severity for row in rows) == ["debug", "error"]
    same = rows_of([transition(CAN, "error", -0.5, code="MoteusFault.33"), cause, confirmed("MoteusFault.33", 0, seq=2)])
    assert len(same) == 1 and same[0].event.code == "BoardFault"
    assert [member.code for member in same[0].members] == ["FaultConfirmed", "LevelChanged"]


def test_a_warn_transition_with_no_fault_is_debug():
    rows = rows_of([transition(CAN, "warn", 0, code="Slow")])
    assert [(row.event.code, row.event.severity) for row in rows] == [("LevelChanged", "debug")]


def test_silence_is_a_warn_row_only_when_no_plug_or_unplug_on_its_path_explains_it():
    for to in ("stale", "unknown"):
        assert [row.event.severity for row in rows_of([transition(CAN, to, 0, before="ok")])] == ["warn"]
    port = "host/usb1/port-1-5"
    unplug = ev("Unplugged", -20, path=port, message="fdcanusb unplugged from 1-5", source="io_facts.journal", id="u1")
    assert [row.event.severity for row in rows_of([unplug, transition(CAN, "stale", 0)]) if row.event.code == "LevelChanged"] == ["debug"]
    replug = ev("ReEnumerated", -20, "warn", path=port, message="dropped off USB", source="io_facts.journal", id="u2")
    assert [row.event.severity for row in rows_of([replug, transition(CAN, "stale", 0)]) if row.event.code == "LevelChanged"] == ["debug"]
    # not explained: another port, a plug too long ago, a plug AFTER, or the unplanned host-level one
    other = dataclasses.replace(unplug, path="host/usb1/port-1-9", args={"id": "u3"})
    old = dataclasses.replace(unplug, stamp=T0 - 500, args={"id": "u4"})
    after = dataclasses.replace(unplug, stamp=T0 + 60, args={"id": "u5"})
    host_level = dataclasses.replace(unplug, path="host", args={"id": "u6"})
    for event in (other, old, after, host_level):
        assert [row.event.severity for row in rows_of([event, transition(CAN, "stale", 0)]) if row.event.code == "LevelChanged"] == ["warn"], event


def test_every_other_transition_is_debug_and_a_flapping_check_is_one_row():
    rows = rows_of([transition(CAN, "ok", 0, before="warn")])
    assert [row.event.severity for row in rows] == ["debug"]
    flapping = rows_of([transition(CAN, "warn", at, code="") for at in (0, 5, 10, 15)])
    assert [(row.event.severity, row.event.count) for row in flapping] == [("debug", 4)]


# ----- a run is one row ----------------------------------------------------------------------------------------------
def run_events(run_id="20261024T153012-a3f9", state="done", ended=True):
    started = ev("RunStarted", 0, path=CAN, message="Run calibration started on CAN 1", source="io_diagnostics.runs", seq=1,
                 id=f"run:{run_id}:started", run_id=run_id, action="calibrate", node=1, plugin="moteus_servo", danger="moves")
    end = ev("RunEnded", 31, "info" if state == "done" else "error", path=CAN, message=f"Run calibration on CAN 1 {state} after 31 s",
             source="io_diagnostics.runs", seq=2, id=f"run:{run_id}:ended", run_id=run_id, state=state, exit_code=0, duration_s=31.2)
    return [started, end] if ended else [started]


def test_run_started_and_ended_are_one_row_with_the_end_as_its_last_stamp():
    rows = rows_of(run_events(state="failed"), active_runs=[])
    assert len(rows) == 1
    row = rows[0]
    assert (row.event.code, row.event.severity, row.event.stamp, row.event.last_stamp) == ("Run", "error", T0, T0 + 31)
    assert row.event.args["state"] == "failed" and row.event.args["run_id"] == "20261024T153012-a3f9"
    assert [member.code for member in row.members] == ["RunStarted", "RunEnded"]
    assert row.event.message.endswith("failed after 31 s")


def test_an_unfinished_run_is_running_only_while_the_host_says_so():
    started = run_events(ended=False)
    assert rows_of(started, active_runs=["20261024T153012-a3f9"])[0].event.args["state"] == "running"
    gone = rows_of(started, active_runs=[])[0]
    assert (gone.event.args["state"], gone.event.severity) == ("interrupted", "warn")  # the host died mid-run: not "running" forever
    assert rows_of(started)[0].event.args["state"] == "running"  # None = unknown


# ----- selecting ------------------------------------------------------------------------------------------------------
def test_scope_is_the_entity_and_its_children_and_optionally_the_path_up_to_the_host():
    device = "host/usb1/port-1-5/usb-1-5"
    events = [ev("A", 0, path="host", id="a"), ev("B", 1, path="host/usb1/port-1-5", id="b"), ev("C", 2, path=device, id="c"),
              ev("D", 3, path=CAN, id="d"), ev("E", 4, path=f"{CAN}/15", id="e"), ev("F", 5, path="host/usb1/port-1-9", id="f"),
              ev("G", 6, path="host/usb1/port-1-5/usb-1-50", id="g")]
    rows = rows_of(events)
    codes = lambda **options: sorted(row.event.code for row in logview.select(rows, **options))  # noqa: E731
    assert codes() == list("ABCDEFG")
    assert codes(entity=device) == ["C", "D", "E"]  # children: the entity and what is below; usb-1-50 is not below usb-1-5
    assert codes(entity=device, scope="self") == ["C"]
    assert codes(entity=device, scope="host") == ["A", "B", "C", "D", "E"]
    assert codes(entity=device, scope="children", entity_hardware_id="hw") == ["C", "D", "E"]
    named = rows_of([dataclasses.replace(ev("H", 7, path="host/elsewhere", id="h"), hardware_id="usb:0483:5740:X")])
    assert [row.event.code for row in logview.select(named, entity=device, entity_hardware_id="usb:0483:5740:X")] == ["H"]


def test_the_severity_floor_and_the_types_narrow_the_rows():
    rows = rows_of([ev("A", 0, "debug", id="a"), ev("B", 1, "info", id="b"), ev("C", 2, "warn", id="c"), ev("D", 3, "error", id="d"),
                    ev("E", 4, "fatal", id="e")])
    assert [row.event.code for row in logview.select(rows, floor="warn")] == ["E", "D", "C"]
    assert len(logview.select(rows, floor="debug")) == 5 and len(logview.select(rows, floor="fatal")) == 1
    assert [row.event.code for row in logview.select(rows, floor="info")] == ["E", "D", "C", "B"]  # debug is below info
    boot = rows_of([ev("Booted", 0, path="host", id="b"), ev("Plugged", 1, id="p")])
    assert [row.event.code for row in logview.select(boot, types=["boot"])] == ["Booted"]


# ----- the Raw channel -------------------------------------------------------------------------------------------------
def test_kernel_lines_are_parsed_once_and_belong_to_their_port():
    raw = logview.RawKernel()
    boot = type("Boot", (), {"start": T0, "lines": [
        "2026-10-21T18:11:30.123456-07:00 bam-GPU kernel: usb 1-7: device descriptor read/64, error -71",
        "2026-10-21T18:11:31-07:00 bam-GPU kernel: cdc_acm 1-5:1.0: ttyACM0: USB ACM device",
        "2026-10-21T18:11:32-07:00 bam-GPU kernel: pcieport 0000:00:01.1: AER: Corrected error received",
        "not a kernel line"]})()
    lines = raw.lines([boot])
    assert [(line.port, line.severity) for line in lines] == [("1-7", "warn"), ("1-5", "info"), ("", "info")]
    assert lines[0].text == "usb 1-7: device descriptor read/64, error -71" and lines[0].stamp < lines[1].stamp
    boot.lines.append("2026-10-21T18:11:40-07:00 bam-GPU kernel: usb 1-5: USB disconnect, device number 8")
    again = raw.lines([boot])
    assert len(again) == 4 and again[:3] == lines  # only the new line was parsed


# ----- the log on disk ---------------------------------------------------------------------------------------------------
def log_in(tmp_path, **options):
    options = {"boot_id": "12fdca7d-9d96-4929-824a-24902e740ddf", "boot_start": T0 - 3600, **options}
    return eventlog.EventLog(tmp_path / "log", **options)


def test_events_survive_a_restart_and_keep_when_the_host_first_saw_them(tmp_path):
    first = log_in(tmp_path)
    events = [ev("BoardFault", 0, "error", seq=1), confirmed("MoteusFault.33", 0, seq=2), ev("RunStarted", 5, seq=3, id="run:x:started")]
    assert first.record(events, now=T0 + 100) == 3
    assert first.record(events, now=T0 + 101) == 0  # nothing new: nothing written
    again = log_in(tmp_path)  # a new host process, the same boot
    assert [event.code for event in again.events()] == ["RunStarted", "FaultConfirmed", "BoardFault"]
    assert {event.received for event in again.events()} == {T0 + 100}
    # the restarted process produces the same facts again: still one copy each, and `received` is the first sighting
    assert again.record(events, now=T0 + 500) == 0
    overlaid = again.overlay([dataclasses.replace(events[0], received=None)], now=T0 + 500)
    assert len(overlaid) == 3 and next(event for event in overlaid if event.code == "BoardFault").received == T0 + 100
    assert [path.name for path in again.files()] == [first.files()[0].name] and len(first.files()) == 1
    # a reboot is another boot: another file, and the old one's events are still history
    rebooted = log_in(tmp_path, boot_id="03d10086-bd43-4fae-9166-c403b4878c6f", boot_start=T0 + 3600)
    assert len(rebooted.files()) == 1 and len(rebooted.events()) == 3
    rebooted.record([ev("Booted", 3600, path="host", id="boot:2")], now=T0 + 3601)
    assert len(rebooted.files()) == 2 and len(rebooted.events()) == 4


def test_a_changed_event_is_written_again_and_the_newest_line_wins(tmp_path):
    log = log_in(tmp_path)
    speed = ev("LinkSpeed", 0, "info", path="host/usb1/port-1-1", message="linked at USB full-speed", source="x", id="usb:1-1:3:add:link")
    log.record([speed], now=T0 + 100)
    slow = dataclasses.replace(speed, code="LinkDowngraded", severity="warn", message="linked at USB full-speed, but it is a USB 3 device")
    assert log.record([slow], now=T0 + 101) == 1
    reloaded = log_in(tmp_path)
    assert [(event.code, event.severity) for event in reloaded.events()] == [("LinkDowngraded", "warn")]
    assert len(log.files()[0].read_text().splitlines()) == 2


def test_only_settled_events_are_written(tmp_path):
    log = log_in(tmp_path)
    provisional = ev("Plugging", 95, path="host", source="io_facts.udev (udevadm monitor)", id="usb:1-5:9:add", provisional=True)
    young = ev("Plugged", 80, path="host/usb1/port-1-5", source=eventlog.activity_module.JOURNAL_SOURCE, id="usb:1-5:9:add")
    old = ev("Plugged", 10, path="host/usb1/port-1-5", source=eventlog.activity_module.JOURNAL_SOURCE, id="usb:1-5:7:add")
    host_made = ev("FaultConfirmed", 99, "error", seq=1, fault="X")
    assert not eventlog.settled(provisional, T0 + 100) and not eventlog.settled(young, T0 + 100)
    assert eventlog.settled(old, T0 + 100) and eventlog.settled(host_made, T0 + 100)
    assert log.record([provisional, young, old, host_made], now=T0 + 100) == 2
    assert {event_id(event) for event in log.events()} == {"usb:1-5:7:add", event_id(host_made)}
    # the young one is written once it has settled
    assert log.record([young], now=T0 + 80 + eventlog.SETTLE_S) == 1


def test_overlay_gives_back_what_this_process_no_longer_produces_and_the_current_one_wins(tmp_path):
    log = log_in(tmp_path)
    gone = ev("BoardFault", 0, "error", seq=1)
    log.record([gone, ev("Plugged", 2, id="p1", source="io_facts.journal")], now=T0 + 100)
    now_only = ev("RunStarted", 50, seq=3, id="run:y:started")
    merged = log.overlay([now_only, dataclasses.replace(ev("Plugged", 2, id="p1", source="io_facts.journal"), message="newer words")], now=T0 + 200)
    assert [(event.code, event.message) for event in merged] == [("RunStarted", "RunStarted on " + CAN), ("Plugged", "newer words"), ("BoardFault", "BoardFault on " + CAN)]
    assert all(event.received is not None for event in merged)


def test_the_log_is_capped_and_the_oldest_segments_go_first(tmp_path):
    log = log_in(tmp_path, cap_bytes=6000, segment_bytes=1500)
    for number in range(60):
        log.record([ev("BoardFault", number * 10, "error", seq=number, message="x" * 80)], now=T0 + number * 10 + 1)
    sizes = [path.stat().st_size for path in log.files()]
    assert sum(sizes) <= 6000 + 1500 and len(sizes) >= 3  # over the cap by at most the segment being written
    assert max(sizes) <= 1500 + 400  # a segment closes at its limit (one line past it at most)
    newest = log.events()[0]
    assert newest.seq == 59  # the newest is kept
    reloaded = log_in(tmp_path, cap_bytes=6000, segment_bytes=1500)
    assert 5 <= len(reloaded.events()) < 60 and reloaded.events()[0].seq == 59  # the oldest went with its segment, in memory too
    assert len(log.events()) == len(reloaded.events())
    names = [path.name for path in log.files()]
    assert names == sorted(names) and len({name.rsplit("-", 1)[0] for name in names}) == 1  # the segments of one boot


def test_a_torn_or_foreign_line_is_skipped_and_a_full_disk_never_stops_the_host(tmp_path):
    log = log_in(tmp_path)
    log.record([ev("BoardFault", 0, "error", seq=1)], now=T0)
    path = log.files()[0]
    with path.open("a") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"key": "k", "event": {"schema": "diagnostics-record/9", "record": "event", "code": "X"}}) + "\n")
        handle.write(json.dumps({"key": "ok", "event": to_dict(ev("Plugged", 3, id="p"))}) + "\n")
        handle.write('{"key": "torn", "ev')  # a writer still mid-line: no newline yet
    reloaded = log_in(tmp_path)
    assert sorted(event.code for event in reloaded.events()) == ["BoardFault", "Plugged"]
    blocked = eventlog.EventLog(path, boot_id="b", boot_start=T0)  # a FILE where the directory should be
    assert blocked.record([ev("BoardFault", 0, "error", seq=1)], now=T0) == 1  # kept in memory
    assert "could not be written" in blocked.note and len(blocked.events()) == 1


def test_two_hosts_on_one_boot_do_not_write_each_others_events_twice(tmp_path):
    stable, preview = log_in(tmp_path), log_in(tmp_path)  # Stable and Preview run side by side
    shared = [ev("BoardFault", 0, "error", seq=1), ev("Plugged", 1, id="p1", source="io_facts.journal")]
    assert stable.record(shared, now=T0 + 100) == 2
    assert preview.record(shared, now=T0 + 101) == 0  # it read what the other appended before deciding
    assert len(preview.events()) == 2 and {event.received for event in preview.events()} == {T0 + 100}
    assert preview.record([ev("RunStarted", 9, id="run:z:started", seq=4)], now=T0 + 102) == 1
    assert stable.record([], now=T0 + 103) == 0 and len(log_in(tmp_path).events()) == 3
    assert len(stable.files()[0].read_text().splitlines()) == 3


def test_a_memory_only_log_keeps_the_same_history_without_a_disk():
    log = eventlog.EventLog(None, boot_id="b", boot_start=T0)
    assert log.record([ev("BoardFault", 0, "error", seq=1)], now=T0) == 1
    assert log.record([ev("BoardFault", 0, "error", seq=1)], now=T0 + 1) == 0
    assert [event.code for event in log.events()] == ["BoardFault"] and log.files() == [] and log.note == ""


def test_segments_past_999_still_rotate_and_the_oldest_by_number_goes_first(tmp_path):
    # 2026-11-15: segment 1000 sorted before 997 as a string, so the host kept appending to -1000 (22 GB by 20:41, ~2 GB/h)
    # and the pruner took -1000 for the oldest file, the one being written, and deleted nothing.
    log = log_in(tmp_path, cap_bytes=6000, segment_bytes=1500)
    (tmp_path / "log").mkdir(parents=True, exist_ok=True)
    for number in (997, 998, 999):
        (tmp_path / "log" / f"{log.prefix}-{number:03d}.jsonl").write_text("{}\n" * 600)  # full: 1800 bytes each
    for number in range(80):
        log.record([ev("BoardFault", number * 10, "error", seq=number, message="x" * 80)], now=T0 + number * 10 + 1)
    names = [path.name for path in log.files()]
    numbers = [int(name.rsplit("-", 1)[1].removesuffix(".jsonl")) for name in names]
    assert max(numbers) >= 1001, names  # it moved past 1000
    assert numbers == sorted(numbers), names  # numeric order, not string order
    assert 997 not in numbers, names  # the oldest by number was pruned
    sizes = [path.stat().st_size for path in log.files()]
    assert max(sizes) <= 1800 + 400 and sum(sizes) <= 6000 + 1800 + 400, list(zip(names, sizes))


def test_a_segment_never_grows_past_the_hard_limit_whatever_the_names_say(tmp_path):
    # the backstop: even if a naming bug picks a full file again, a segment over HARD_SEGMENT_FACTOR x its limit is never
    # appended to
    log = log_in(tmp_path, cap_bytes=10**9, segment_bytes=1500)
    for number in range(200):
        log.record([ev("BoardFault", number * 10, "error", seq=number, message="x" * 80)], now=T0 + number * 10 + 1)
    assert max(path.stat().st_size for path in log.files()) <= 1500 + 400


def test_events_older_than_what_the_capped_log_keeps_are_not_written_again_and_again(tmp_path):
    # 2026-11-15: the pruner forgot the keys of the segments it deleted, the activity layer re-derives the same old journal
    # events every pass, so they were written again, filled a segment, pushed the next out, and so on: ~550 KB/s for ever
    log = log_in(tmp_path, cap_bytes=6000, segment_bytes=1500)
    kernel = eventlog.activity_module.KERNEL_SOURCE  # journal-derived: re-derived on every pass, the loop's fuel
    old = [ev("ReEnumerated", number, "info", seq=number, id=f"usb:5-1.3:{number}:add", message="x" * 80, source=kernel)
           for number in range(200)]
    first = sum(log.record(old[: number + 1], now=T0 + 10_000 + number) for number in range(200))  # one pass per new event
    assert first == 200
    again = sum(log.record(old, now=T0 + 20_000 + round_) for round_ in range(10))  # the same history, re-derived 10 times
    assert again == 0, f"re-wrote {again} lines of events the cap had already dropped"
    assert sum(path.stat().st_size for path in log.files()) <= 6000 + 1500 + 400
    newer = log.record([ev("BoardFault", 9_000, "error", seq=999, id="fresh")], now=T0 + 20_020)
    assert newer == 1  # a genuinely new event is still written
    reloaded = log_in(tmp_path, cap_bytes=6000, segment_bytes=1500)  # a restart: the floor comes back from what is on disk
    assert reloaded.record(old, now=T0 + 20_030) <= 40  # at most what the kept segments hold, never the whole history


def test_two_facts_sharing_a_key_in_one_pass_are_written_once_not_alternately_for_ever(tmp_path):
    # 2026-11-15: the OBSBOT re-enumerated 261 times; USB device numbers wrap, so "usb:5-1.3:25:add" named both its 08:24 and
    # its 08:26 enumeration. Every pass re-derived both, each differed from the one written before it, and the host wrote
    # them alternately for ever (282 copies of one key)
    log = log_in(tmp_path)
    kernel = eventlog.activity_module.KERNEL_SOURCE
    early = ev("LinkSpeed", 100, "info", seq=1, id="usb:5-1.3:25:add:link", source=kernel)
    late = ev("LinkSpeed", 232, "info", seq=2, id="usb:5-1.3:25:add:link", source=kernel)
    written = sum(log.record([early, late], now=T0 + 1_000 + round_) for round_ in range(20))
    assert written == 1, f"{written} lines for one key"
    assert log.events()[0].stamp == T0 + 232  # the newest of the two is the one kept
