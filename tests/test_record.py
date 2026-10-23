"""Diagnostics record v0 (contract/record.py): stock round trips, the composition rule, fault debounce, limits.

Spec: docs/diagnostics-record-v0.md. The scenarios are the rack's own (2026-10-21): fdcanusb 28B3A25D's dead
bus, CAN 15's fault 33, a flapping USB port; plus Zach's spider (2026-10-22) for the black-box rule.
"""

from __future__ import annotations

import dataclasses
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import diagnostics  # noqa: E402
from contract.record import (  # noqa: E402
    CONFIRMED, ERROR, HEALED, LEVELS, OK, PENDING, SCHEMA, STALE, UNKNOWN, WARN, Action, Debounce, DiagnosticStatus,
    Entity, Event, Fault, FaultManager, KeyValue, Measurement, effective_level, from_diagnostic_array,
    from_diagnostic_status, from_dict, restarted, summarize, to_diagnostic_array, to_diagnostic_status, to_dict,
    validate,
)

NOW = 1_792_631_510.0  # 2026-10-21 18:11:50 -07:00, the rack's plug frame


# ----- stock round trips -----------------------------------------------------------------------------------
REAL_WORLD = [
    # ros2_control packs a whole statistic into one string (research lane ROS, 2.3)
    {"level": 0, "name": "controller_manager: Controllers Activity", "message": "All controllers are active",
     "hardware_id": "ros2_control",
     "values": [{"key": "joint_state_broadcaster.execution_time", "value": "Avg: 12.30 [10.10 - 20.40] us, StdDev: 1.20"}]},
    # Greenwave sets STALE itself and relies on magic keys
    {"level": 3, "name": "/camera/image: frequency", "message": "STALE", "hardware_id": "",
     "values": [{"key": "frame_rate_node", "value": "0.0"}, {"key": "expected_frequency", "value": "30.0"},
                {"key": "tolerance", "value": "5.0"}]},
    # the aggregator's rewritten name (ros/diagnostics #520)
    {"level": 2, "name": "/Arm/Hardware/Control/ arm1 arm_communicator: errors", "message": "Error",
     "hardware_id": "/arm1/arm_communicator", "values": []},
    # an empty first message (#666)
    {"level": 1, "name": "", "message": "", "hardware_id": "", "values": []},
    # unicode, a duplicate key, and a key that looks like ours but is just a value
    {"level": 0, "name": "D405 ·4411: stream", "message": "30 fps ✓", "hardware_id": "usb:8086:0b5b:219323070276",
     "values": [{"key": "rate", "value": "30"}, {"key": "rate", "value": "29.97"},
                {"key": "@record", "value": '{"path": "x"}'}]},
]


def test_stock_to_v0_to_stock_is_the_identity_for_real_messages():
    for message in REAL_WORLD:
        assert to_diagnostic_status(from_diagnostic_status(message)) == message


def test_stock_round_trip_identity_fuzz():
    rng = random.Random(20261022)
    alphabet = "abcXYZ019 :/_-.@{}[]\"'·✓é\n\t"

    def text():
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))

    for _ in range(2000):
        message = {"level": rng.randint(0, 3), "name": text(), "message": text(), "hardware_id": text(),
                   "values": [{"key": text(), "value": text()} for _ in range(rng.randint(0, 5))]}
        assert to_diagnostic_status(from_diagnostic_status(message)) == message


def test_ros2_bytes_level_and_attribute_messages_come_in():
    assert from_diagnostic_status({"level": b"\x02", "name": "n"}).level == ERROR  # rosidl byte -> bytes
    ros_like = SimpleNamespace(level=b"\x01", name="fdcanusb: comms", message="slow", hardware_id="hw",
                               values=[SimpleNamespace(key="rate", value="9")])
    status = from_diagnostic_status(ros_like)
    assert (status.level, status.values) == (WARN, (KeyValue("rate", "9"),))


def test_level_projection_is_a_bijection_and_unknown_is_stale_on_the_wire():
    for level in LEVELS:
        stock = to_diagnostic_status(DiagnosticStatus(level, "n"))
        assert from_diagnostic_status(stock).level == level
    assert to_diagnostic_status(DiagnosticStatus(name="never evaluated"))["level"] == 3  # never green
    assert DiagnosticStatus().level == UNKNOWN  # the zero value


def full_status() -> DiagnosticStatus:
    return DiagnosticStatus(
        level=WARN, name="CAN 15: bus voltage", message="bus voltage low: 33.1 V", hardware_id="moteus:7E3A12",
        values=[("fault", "0"), ("mode", "11")], path="host/usb-1-3/fdcanusb-272813EF/can/15", code="BusVoltageLow",
        mode="operational", stamp=NOW, received=NOW + 0.011, expected_period_s=1.0, uptime_s=812.0,
        source="moteus_servo",
        measurements=(Measurement("bus_voltage", 33.1, "V", warn_low=34.0, error_low=30.0),
                      Measurement("board_temp", 41, "Cel", warn_high=60.0, error_high=70.0)))


def test_v0_to_stock_to_v0_keeps_everything_representable_and_strips_the_rest():
    full = full_status()
    stock = to_diagnostic_status(full)
    assert stock["level"] == 1
    assert stock["values"][-2:] == [{"key": "bus_voltage", "value": "33.1 V"}, {"key": "board_temp", "value": "41 Cel"}]
    back = from_diagnostic_status(stock)
    # the stock five survive; typed measurements survive as the strings a stock viewer shows
    assert (back.level, back.name, back.message, back.hardware_id) == (full.level, full.name, full.message, full.hardware_id)
    assert back.values == full.values + (KeyValue("bus_voltage", "33.1 V"), KeyValue("board_temp", "41 Cel"))
    # everything else was stripped, as Zach asked of the stock view
    assert back == DiagnosticStatus(full.level, full.name, full.message, full.hardware_id, back.values)
    # the v0 transport (JSON) carries every field exactly
    assert from_dict(json.loads(json.dumps(to_dict(full)))) == full


def test_array_round_trip_puts_the_header_stamp_on_each_record():
    array = to_diagnostic_array([full_status(), DiagnosticStatus(OK, "host: usb")], stamp=NOW + 0.5)
    records = from_diagnostic_array(array, received=NOW + 0.6)
    assert [record.stamp for record in records] == [NOW + 0.5, NOW + 0.5]
    assert to_diagnostic_array(records, stamp=NOW + 0.5) == array


def test_every_shape_survives_json_and_other_schemas_are_refused():
    action = Action("clear_faults", "Clear faults", "Send stop (mode 0).", "changes", "node", "Send stop to CAN {id}.",
                    (("port_free", "No other process has the adapter open"),))
    shapes = [
        full_status(),
        Entity("host/usb-1-3/fdcanusb-272813EF", "fdcanusb 272813EF", "device", via="host/usb-1-3",
               hardware_id="usb:0483:5740:272813EF", identity={"manufacturer": "mjbots", "model": "fdcanusb"},
               owner="moteus_servo", actions=(action,)),
        Event("Plugged", NOW, message="fdcanusb 28B3A25D plugged on 1-5", path="host/usb-1-5",
              hardware_id="usb:0483:5740:28B3A25D", args={"port": "1-5", "flaps": 4}, seq=7, source="io_facts.usb"),
        Fault("host/usb-1-3/fdcanusb-272813EF/can/15", "MoteusFault.33", ERROR, CONFIRMED, True, 1, NOW, NOW, None,
              "fault 33 on bridge enable", "moteus:7E3A12", {"level": "error"}),
        action,
    ]
    for shape in shapes:
        assert from_dict(json.loads(json.dumps(to_dict(shape)))) == shape
    try:
        from_dict({**to_dict(full_status()), "schema": "diagnostics-record/1"})
    except ValueError:
        pass
    else:
        raise AssertionError("a different schema must be refused, not misread")
    assert from_dict({**to_dict(full_status()), "added_in_a_later_minor": 1}) == full_status()  # open world
    assert to_dict(full_status())["schema"] == SCHEMA


# ----- staleness ----------------------------------------------------------------------------------------------
def test_staleness_is_judged_on_the_observers_clock_with_a_floor():
    def aged(age, period=1.0, **times):
        times = times or {"received": NOW - age}
        return DiagnosticStatus(OK, "n", expected_period_s=period, **times)

    assert effective_level(aged(5.0), NOW) == OK  # max(3 x 1 s, 5 s): exactly 5 s is still fresh
    assert effective_level(aged(5.01), NOW) == STALE
    assert effective_level(aged(30.0, period=10.0), NOW) == OK
    assert effective_level(aged(30.1, period=10.0), NOW) == STALE
    assert effective_level(aged(1e6, period=None), NOW) == OK  # event-driven: never stale
    assert effective_level(DiagnosticStatus(OK, "n", expected_period_s=1.0), NOW) == STALE  # no time at all
    # the observer's receive time wins over a drifting producer clock
    assert effective_level(aged(0, stamp=NOW - 3600, received=NOW - 1), NOW) == OK


# ----- the composition rule -------------------------------------------------------------------------------------
def status(path, level=OK, *, age=0.0, period=1.0, code="", message="", name="health"):
    if level != OK and not code:
        code = {WARN: "Degraded", ERROR: "Failed", UNKNOWN: "NotEvaluated"}[level]
    return DiagnosticStatus(level, name, message or f"{path.rsplit('/', 1)[-1]} {level}", path=path, code=code,
                            stamp=NOW - age, received=NOW - age, expected_period_s=period)


def folder(*paths, via=None):
    return [Entity(path, path.rsplit("/", 1)[-1], via=via) for path in paths]


def test_error_outranks_stale_and_stale_outranks_warn():
    entities = folder("rig") + folder("rig/a", "rig/b", via="rig")
    rig = summarize(entities, [status("rig/a", ERROR), status("rig/b", OK, age=60)], NOW)["rig"]
    assert (rig.own, rig.level, rig.rollup.origin) == (None, ERROR, "rig/a")
    assert rig.rollup.counts == {ERROR: 1, STALE: 1}
    assert summarize(entities, [status("rig/a", WARN), status("rig/b", OK, age=60)], NOW)["rig"].level == STALE
    assert summarize(entities, [status("rig/a", OK), status("rig/b", UNKNOWN)], NOW)["rig"].level == UNKNOWN  # never green


def spider_world():
    entities = [Entity("cell", "cell"), Entity("cell/spider", "spider", "robot", via="cell")]
    entities += [Entity(f"cell/spider/leg{n}", f"leg {n}", "part", part_of="cell/spider") for n in range(1, 9)]
    statuses = [status("cell/spider", WARN, code="LegFailed", message="1 of 8 legs failed; walking on 7")]
    statuses += [status(f"cell/spider/leg{n}", ERROR if n == 3 else OK, code="MotorFault" if n == 3 else "")
                 for n in range(1, 9)]
    return entities, statuses


def test_the_spider_self_reports_warn_and_the_rollup_only_hints_at_the_failed_leg():
    # Zach, 2026-10-22: "say a robot with 8 legs, 1 leg says failure, the spider can still report warning."
    entities, statuses = spider_world()
    rows = summarize(entities, statuses, NOW)
    spider = rows["cell/spider"]
    assert (spider.own, spider.level, spider.message) == (WARN, WARN, "1 of 8 legs failed; walking on 7")
    assert (spider.rollup.level, spider.rollup.origin) == (ERROR, "cell/spider/leg3")  # the hint, never the headline
    assert rows["cell/spider/leg3"].level == ERROR
    # a pure grouping node has no judgement of its own: it shows worst-below, so the leg is not hidden above
    assert (rows["cell"].own, rows["cell"].level) == (None, ERROR)


def dead_bus_world(node_levels):
    adapter = "host/usb-1-5/fdcanusb-28B3A25D"
    bus = f"{adapter}/can"
    entities = folder("host") + folder("host/usb-1-5", via="host")
    entities += [Entity(adapter, "fdcanusb 28B3A25D", "device", via="host/usb-1-5", hardware_id="usb:0483:5740:28B3A25D"),
                 Entity(bus, "28B3A25D CAN bus", "bus", via=adapter)]
    entities += [Entity(f"{bus}/{n}", f"CAN {n}", "node", via=bus) for n in node_levels]
    return adapter, bus, entities


def test_a_dead_bus_is_one_cause_not_n_plus_one():
    adapter, bus, entities = dead_bus_world([1, 2, 3, 4])
    statuses = [status(adapter, OK, name="usb"), status(bus, ERROR, code="NoAck", message="error-passive: nobody ACKs"),
                status(f"{bus}/1", OK, age=600), status(f"{bus}/2", OK, age=600),
                status(f"{bus}/3", ERROR, code="NoReply"), status(f"{bus}/4", UNKNOWN)]
    rows = summarize(entities, statuses, NOW)
    host = rows["host"]
    assert (host.level, host.rollup.origin, host.rollup.counts) == (ERROR, bus, {ERROR: 1})
    assert sorted(host.rollup.knock_on) == [(f"{bus}/{n}", bus) for n in (1, 2, 3, 4)]
    assert host.message == "28B3A25D CAN bus: error-passive: nobody ACKs"
    # the adapter's own self-report stands (its USB side is fine); the dead bus below is its hint
    assert (rows[adapter].level, rows[adapter].rollup.level, rows[adapter].rollup.origin) == (OK, ERROR, bus)
    assert rows[f"{bus}/1"].level == STALE  # each knock-on still shows its own state on its own row


def test_a_fresh_error_under_a_silent_parent_still_counts():
    adapter, bus, entities = dead_bus_world([1, 2])
    statuses = [status(adapter, OK, name="usb"), status(bus, OK, age=600), status(f"{bus}/1", OK, age=600),
                status(f"{bus}/2", ERROR, code="MoteusFault.33", message="fault 33 on bridge enable")]
    host = summarize(entities, statuses, NOW)["host"]
    assert (host.level, host.rollup.origin) == (ERROR, f"{bus}/2")
    assert host.rollup.counts == {STALE: 1, ERROR: 1}
    assert host.rollup.knock_on == ((f"{bus}/1", bus),)


def test_stock_records_attach_by_hardware_id_and_cycles_are_refused():
    adapter, bus, entities = dead_bus_world([])
    plain = from_diagnostic_status({"level": 2, "name": "fdcanusb: link", "message": "babble",
                                    "hardware_id": "usb:0483:5740:28B3A25D", "values": []})
    assert summarize(entities, [plain], NOW)[adapter].own == ERROR  # repeatable identity joins the two observers
    try:
        summarize([Entity("a", via="b"), Entity("b", via="a")], [], NOW)
    except ValueError:
        pass
    else:
        raise AssertionError("a parent cycle must be refused")


# ----- faults ---------------------------------------------------------------------------------------------------
def port_report(level, at, code="UsbFlapping", *, period=1.0):
    return DiagnosticStatus(level, "port 1-5: enumeration", f"{level} at {at - NOW:.0f}", path="host/usb-1-5",
                            code=code if level in (WARN, ERROR) else "", stamp=at, expected_period_s=period,
                            values=[("flaps", "4")])


def test_debounce_confirms_a_warn_on_the_third_report_and_heals_after_three_passes():
    manager = FaultManager()
    manager.observe(port_report(WARN, NOW))
    manager.observe(port_report(WARN, NOW + 1))
    fault = manager.fault("host/usb-1-5", "UsbFlapping")
    assert (fault.status, fault.active, fault.occurrence_count) == (PENDING, True, 0)
    assert manager.observe(port_report(UNKNOWN, NOW + 2)) == []  # not evaluated: moves nothing
    assert fault.status == PENDING
    events = manager.observe(port_report(WARN, NOW + 3))
    assert [event.code for event in events] == ["FaultConfirmed"]
    assert (fault.status, fault.occurrence_count, fault.first_occurred) == (CONFIRMED, 1, NOW)
    assert fault.snapshot["values"] == [{"key": "flaps", "value": "4"}]  # the freeze frame
    manager.observe(port_report(WARN, NOW + 4))
    assert (fault.occurrence_count, fault.last_occurred) == (1, NOW + 4)  # occurrences count edges, not reports
    manager.observe(port_report(OK, NOW + 5))
    manager.observe(port_report(OK, NOW + 6))
    assert (fault.status, fault.active, fault.last_passed) == (CONFIRMED, False, NOW + 6)  # latched, idle
    assert [event.code for event in manager.observe(port_report(OK, NOW + 7))] == ["FaultHealed"]
    assert fault.status == HEALED
    manager.observe(port_report(WARN, NOW + 8))
    assert (fault.status, fault.first_occurred) == (PENDING, NOW + 8)  # a new occurrence starts
    manager.observe(port_report(WARN, NOW + 9))
    manager.observe(port_report(WARN, NOW + 10))
    assert (fault.status, fault.occurrence_count) == (CONFIRMED, 2)


def test_a_pass_before_confirmation_forgets_and_the_count_starts_over():
    manager = FaultManager()
    manager.observe(port_report(WARN, NOW))
    manager.observe(port_report(WARN, NOW + 1))
    manager.observe(port_report(OK, NOW + 2))
    assert manager.fault("host/usb-1-5", "UsbFlapping") is None
    manager.observe(port_report(WARN, NOW + 3))
    manager.observe(port_report(WARN, NOW + 4))
    assert manager.fault("host/usb-1-5", "UsbFlapping").status == PENDING  # "3 in a row" means in a row
    manager.observe(port_report(WARN, NOW + 5))
    assert manager.fault("host/usb-1-5", "UsbFlapping").status == CONFIRMED


def test_an_error_confirms_at_once_and_clearing_starts_a_new_occurrence():
    manager = FaultManager()
    can15 = "host/usb-1-3/fdcanusb-272813EF/can/15"
    trip = DiagnosticStatus(ERROR, "CAN 15: fault", "fault 33 on bridge enable", path=can15, code="MoteusFault.33",
                            stamp=NOW, expected_period_s=180.0)
    assert [event.code for event in manager.observe(trip)] == ["FaultConfirmed"]
    cleared = manager.clear(can15, "MoteusFault.33", NOW + 60)
    assert cleared.code == "FaultCleared" and manager.fault(can15, "MoteusFault.33").status == "cleared"
    manager.observe(DiagnosticStatus(ERROR, "CAN 15: fault", "fault 33 again", path=can15, code="MoteusFault.33",
                                     stamp=NOW + 120, expected_period_s=180.0))
    fault = manager.fault(can15, "MoteusFault.33")
    assert (fault.status, fault.occurrence_count, fault.first_occurred) == (CONFIRMED, 2, NOW + 120)


def test_time_debounce_serves_edge_reporters_and_silence_never_confirms():
    manager = FaultManager(rules={"UsbFlapping": Debounce(confirm_s=2.0)})
    manager.observe(port_report(WARN, NOW, period=None))  # an edge reporter says it once
    assert manager.tick(NOW + 1) == []
    assert [event.code for event in manager.tick(NOW + 2.5)] == ["FaultConfirmed"]
    # count rules on an edge reporter: its counts are 1 (ros2_medkit's documented trap)
    edge = FaultManager()
    edge.observe(port_report(WARN, NOW, period=None))
    assert edge.fault("host/usb-1-5", "UsbFlapping").status == CONFIRMED
    # a periodic stream that stops reporting is silent, and silence is not evidence
    quiet = FaultManager(rules={"UsbFlapping": Debounce(confirm_s=2.0)})
    quiet.observe(port_report(WARN, NOW, period=1.0))
    assert quiet.tick(NOW + 10) == [] and quiet.fault("host/usb-1-5", "UsbFlapping").status == PENDING


def test_a_check_failing_for_a_new_reason_passes_the_old_one():
    manager = FaultManager()
    can15 = "host/usb-1-3/fdcanusb-272813EF/can/15"

    def report(code, at):
        return DiagnosticStatus(ERROR, "CAN 15: fault", code, path=can15, code=code, stamp=at, expected_period_s=1.0)

    manager.observe(report("MoteusFault.33", NOW))
    for second in (1, 2, 3):
        manager.observe(report("MoteusFault.39", NOW + second))
    assert manager.fault(can15, "MoteusFault.33").status == HEALED
    assert manager.fault(can15, "MoteusFault.39").status == CONFIRMED


# ----- typed values ----------------------------------------------------------------------------------------------
def test_limits_turn_a_typed_value_into_a_level():
    def voltage(value):
        return Measurement("bus_voltage", value, "V", warn_low=34.0, error_low=30.0).level

    def temperature(value):
        return Measurement("board_temp", value, "Cel", warn_high=60.0, error_high=70.0).level

    assert [voltage(v) for v in (37.2, 34.0, 33.9, 30.0, 29.9)] == [OK, OK, WARN, WARN, ERROR]
    assert [temperature(v) for v in (41, 60.0, 65.0, 70.0, 70.1)] == [OK, OK, WARN, WARN, ERROR]
    assert voltage(None) == UNKNOWN and voltage(float("nan")) == UNKNOWN  # unreadable is not fine
    assert Measurement("update_available", True).level == OK
    assert Measurement("rate", 29.97, "Hz").render() == "29.97 Hz" and Measurement("ids", 3, "1").render() == "3"


# ----- conformance, restarts, one Action ---------------------------------------------------------------------------
def test_validate_names_each_broken_rule():
    assert validate(full_status()) == []
    assert any("code is required" in problem for problem in validate(DiagnosticStatus(WARN, "n", path="p")))
    assert any("STALE is derived" in problem for problem in validate(DiagnosticStatus(STALE, "n", path="p")))
    clash = DiagnosticStatus(OK, "n", path="p", values=[("rate", "1")], measurements=[Measurement("rate", 1.0)])
    assert any("appears 2 times" in problem for problem in validate(clash))
    unordered = DiagnosticStatus(OK, "n", path="p", measurements=[Measurement("t", 1.0, warn_high=70.0, error_high=60.0)])
    assert any("limits must satisfy" in problem for problem in validate(unordered))
    assert any("one parent edge" in problem for problem in validate(Entity("x", via="a", part_of="b")))
    assert any("a part carries no hardware_id" in problem
               for problem in validate(Entity("leg", part_of="spider", hardware_id="serial")))
    assert any("severity" in problem for problem in validate(Event("Plugged", NOW, severity="notice")))
    assert any("danger" in problem for problem in validate(Action("reset", "Reset", "", "risky", "node", "")))


def test_uptime_going_backwards_is_a_restart():
    assert restarted(DiagnosticStatus(OK, "n", uptime_s=812.0), DiagnosticStatus(OK, "n", uptime_s=3.0))
    assert not restarted(DiagnosticStatus(OK, "n", uptime_s=812.0), DiagnosticStatus(OK, "n", uptime_s=813.0))


def test_action_has_one_definition():
    assert diagnostics.Action is Action  # V6 drivers import it from contract.diagnostics; the standard owns it


# ----- v0.1 (additive): model_id, moves, the standard action names -------------------------------------------------
VOCABULARY = Path(__file__).resolve().parents[1] / "docs" / "actions-vocabulary-v0.1.md"


def test_v0_1_is_signalled_by_a_constant_never_by_the_schema_string():
    from contract.record import RECORD_VERSION

    assert (SCHEMA, RECORD_VERSION) == ("diagnostics-record/0", "0.1")
    line = to_dict(Entity("host/usb-1-3", identity={"model_id": "mjbots-fdcanusb"}))
    assert line["schema"] == SCHEMA and "record_version" not in line  # a v0 reader still reads every v0.1 line


def test_v0_1_adds_model_id_and_moves():
    from contract.record import DANGERS, IDENTITY_KEYS

    assert IDENTITY_KEYS[-1] == "model_id" and DANGERS[-1] == "moves"
    board = Entity("host/usb-1-3/can/15", "CAN 15", "node", via="host/usb-1-3/can", hardware_id="moteus:7E3A12",
                   identity={"manufacturer": "mjbots", "model": "moteus c1", "model_id": "mjbots-moteus-c1"},
                   actions=[Action("calibrate", "Run calibration…", "spins the rotor ~30 s", "moves", "node",
                                   "Calibrate {name}? The rotor spins.")])
    assert validate(board) == []
    assert from_dict(json.loads(json.dumps(to_dict(board)))) == board
    assert any("identity key 'model_number'" in problem for problem in validate(Entity("x", identity={"model_number": "1"})))


def test_the_standard_action_table_is_the_frozen_vocabulary():
    import re

    from contract.record import STANDARD_ACTIONS

    rows = re.findall(r"^\| `([a-z_]+)` \| ([^|]+?) \|", VOCABULARY.read_text(), re.M)
    assert rows and STANDARD_ACTIONS == {name: label for name, label in rows}


def test_a_standard_name_must_carry_its_label_and_any_danger():
    assert validate(Action("reboot", "Reboot…", "", "interrupts", "node", "")) == []
    assert validate(Action("calibrate", "Run calibration…", "", "changes", "device", "")) == []  # danger is per driver
    [problem] = validate(Action("reboot", "Reset…", "", "interrupts", "node", ""))
    assert "title must be its label 'Reboot…'" in problem
    assert validate(Action("identify", "Blink the LED", "", "query", "node", "")) == []  # non-standard: any title


def test_stock_round_trip_is_still_the_identity_beside_v0_1_records():
    # model_id lives on Entity and moves on Action: neither can reach the stock five
    for message in REAL_WORLD:
        assert to_diagnostic_status(from_diagnostic_status(message, path="host/usb-1-3/can/15", source="moteus_servo")) == message
    status = DiagnosticStatus(ERROR, "CAN 15: calibration", "rotor did not spin", "moteus:7E3A12", path="host/usb-1-3/can/15",
                              code="CalibrationFailed", source="moteus_servo")
    stock = to_diagnostic_status(status)
    assert set(stock) == {"level", "name", "message", "hardware_id", "values"}
    assert to_diagnostic_status(from_diagnostic_status(stock)) == stock


# ----- v0.1 addendum: caused_by (the three probes of reports/diagnostics-rollup-proposals-2026-10-23.html) --------
RACK_ADAPTER = "host/usb1/1-3"
CAN15 = f"{RACK_ADAPTER}/can/15"
RIG = "host/rig-1-dof-test-rack"


def rack_world():
    """The rack as the probes built it: fdcanusb -> can (part_of) -> can/15 (via), the rig row beside them (via host)."""

    return folder("host") + [Entity(RACK_ADAPTER, "fdcanusb 0607623A", "device", via="host", hardware_id="usb:0483:5740:0607623A"),
                             Entity(f"{RACK_ADAPTER}/can", "CAN bus", "bus", part_of=RACK_ADAPTER),
                             Entity(CAN15, "CAN 15", "node", via=f"{RACK_ADAPTER}/can"),
                             Entity(RIG, "1-DOF test rack", "rig", via="host")]


def test_probe_1_the_via_direction_stands_so_the_computer_writes_beside_the_tree():
    # a failed ancestor still explains everything below it (Nagios UNREACHABLE): written ON the host, a full disk
    # swallows CAN 15's fault 33 ...
    board = status(CAN15, ERROR, code="MoteusFault.33", message="fault 33 on bridge enable")
    on_host = summarize(rack_world(), [status("host", ERROR, code="DiskFull", name="disk /"), board], NOW)["host"]
    assert (dict(on_host.rollup.counts), on_host.rollup.knock_on) == ({ERROR: 1}, ((CAN15, "host"),))
    # ... and written on a leaf beside the tree (io_diagnostics.tree HOST_SELF does this for the Computer), both count
    entities = rack_world() + [Entity("host/computer", "Computer", via="host")]
    beside = summarize(entities, [status("host/computer", ERROR, code="DiskFull", name="disk /"), board], NOW)["host"]
    assert (dict(beside.rollup.counts), beside.rollup.knock_on) == ({ERROR: 2}, ())


def test_probe_2_a_rig_naming_the_missing_board_is_one_cause_and_the_chip_names_the_board():
    board = status(CAN15, ERROR, code="BoardNotAnswering", message="CAN 15 does not answer")
    rig = DiagnosticStatus(WARN, "1-DOF test rack: expected setup", "missing: CAN 15 on fdcanusb 0607623A", path=RIG,
                           code="ExpectedMissing", stamp=NOW, received=NOW)
    before = summarize(rack_world(), [rig, board], NOW)["host"]
    assert dict(before.rollup.counts) == {WARN: 1, ERROR: 1}  # without caused_by: one missing board, two causes
    rows = summarize(rack_world(), [dataclasses.replace(rig, caused_by=(CAN15,)), board], NOW)
    host = rows["host"]
    assert (dict(host.rollup.counts), host.rollup.origin, host.rollup.knock_on) == ({ERROR: 1}, CAN15, ((RIG, CAN15),))
    # the rig's row keeps its own word (the black-box rule): only the count and the attention list change
    assert (rows[RIG].own, rows[RIG].level, rows[RIG].message) == (WARN, WARN, "missing: CAN 15 on fdcanusb 0607623A")
    # inside its own row the rig is all there is, so its rollup still names itself
    assert rows[RIG].rollup.origin == RIG


def test_probe_3_a_spider_at_error_names_its_failed_leg_instead_of_hiding_it():
    entities, statuses = spider_world()
    leg3 = "cell/spider/leg3"
    before = summarize(entities, [status("cell/spider", ERROR, code="LegFailed", message="leg 3 failed: walking on 7"),
                                  *statuses[1:]], NOW)["cell"]
    assert (before.rollup.origin, before.rollup.knock_on) == ("cell/spider", ((leg3, "cell/spider"),))  # without: hidden
    for level in (WARN, ERROR):
        spider = dataclasses.replace(status("cell/spider", level, code="LegFailed", message="leg 3 failed: walking on 7"),
                                     caused_by=(leg3,))
        rows = summarize(entities, [spider, *statuses[1:]], NOW)
        cell = rows["cell"]
        assert (dict(cell.rollup.counts), cell.rollup.origin, cell.rollup.knock_on) == ({ERROR: 1}, leg3, (("cell/spider", leg3),)), level
        assert (rows["cell/spider"].level, rows["cell/spider"].message) == (level, "leg 3 failed: walking on 7")
        assert rows["cell/spider"].rollup.origin == leg3 and rows[leg3].level == ERROR


def test_caused_by_counts_only_when_every_named_member_reports_a_problem():
    entities, statuses = spider_world()
    legs = statuses[1:]
    gait = status("cell/spider", ERROR, code="LegFailed", message="legs failed")
    # naming an OK leg, a path nobody describes, or itself: the spider's verdict stands as its own cause (and, as
    # before, its ERROR explains the leg below it)
    for named in (("cell/spider/leg5",), ("cell/spider/leg3", "cell/spider/leg5"), ("cell/spider/leg9",), ("cell/spider",)):
        cell = summarize(entities, [dataclasses.replace(gait, caused_by=named), *legs], NOW)["cell"]
        assert (cell.rollup.origin, dict(cell.rollup.counts)) == ("cell/spider", {ERROR: 1}), named
    # a second, unnamed problem record on the spider keeps it a cause of its own
    extra = status("cell/spider", WARN, code="BatteryLow", name="battery")
    cell = summarize(entities, [dataclasses.replace(gait, caused_by=("cell/spider/leg3",)), extra, *legs], NOW)["cell"]
    assert cell.rollup.origin == "cell/spider" and dict(cell.rollup.counts) == {ERROR: 1}
    # a stale composite is its own cause: silence is the wrapper's own failure, whatever it last said
    stale = dataclasses.replace(status("cell/spider", WARN, code="LegFailed", age=60), caused_by=("cell/spider/leg3",))
    cell = summarize(entities, [stale, *legs], NOW)["cell"]
    assert dict(cell.rollup.counts) == {STALE: 1, ERROR: 1}
    # an unknown member is still a problem the composite can be the consequence of
    unknown_leg = [status(f"cell/spider/leg{n}", UNKNOWN if n == 3 else OK) for n in range(1, 9)]
    cell = summarize(entities, [dataclasses.replace(gait, caused_by=("cell/spider/leg3",)), *unknown_leg], NOW)["cell"]
    assert (dict(cell.rollup.counts), cell.rollup.origin) == ({UNKNOWN: 1}, "cell/spider/leg3")


def test_a_dead_adapter_still_explains_the_boards_a_rig_names():
    adapter, bus, entities = dead_bus_world([1, 2])
    entities += [Entity(RIG, "rig", "rig", via="host")]
    rig = DiagnosticStatus(WARN, "rig: expected setup", "missing: CAN 1, CAN 2", path=RIG, code="ExpectedMissing",
                           stamp=NOW, received=NOW, caused_by=(f"{bus}/1", f"{bus}/2"))
    statuses = [status(adapter, OK, name="usb"), status(bus, ERROR, code="NoAck"), status(f"{bus}/1", OK, age=600),
                status(f"{bus}/2", ERROR, code="NoReply"), rig]
    host = summarize(entities, statuses, NOW)["host"]
    assert (dict(host.rollup.counts), host.rollup.origin) == ({ERROR: 1}, bus)
    assert sorted(host.rollup.knock_on) == sorted([(f"{bus}/1", bus), (f"{bus}/2", bus), (RIG, bus)])


def test_members_naming_each_other_never_explain_a_problem_away():
    entities = folder("host") + folder("host/a", "host/b", via="host")
    ring = [dataclasses.replace(status("host/a", WARN), caused_by=("host/b",)),
            dataclasses.replace(status("host/b", ERROR), caused_by=("host/a",))]
    host = summarize(entities, ring, NOW)["host"]
    assert sum(host.rollup.counts.values()) == 1 and host.rollup.level not in (None, OK)
    assert (host.rollup.origin, host.rollup.knock_on) == ("host/a", (("host/b", "host/a"),))  # broken at the first in tree order


def test_caused_by_rides_in_one_stock_value_and_the_stock_trip_stays_the_identity():
    from contract.record import CAUSED_BY_KEY

    rig = DiagnosticStatus(WARN, "rack: expected setup", "missing: CAN 15", path=RIG, code="ExpectedMissing",
                           values=[("missing", "CAN 15")], measurements=(Measurement("boards", 6, "1"),),
                           caused_by=(CAN15, "host/expected/usb-0483-5740-28B3A25D"))
    stock = to_diagnostic_status(rig)
    assert stock["values"] == [{"key": "missing", "value": "CAN 15"}, {"key": "boards", "value": "6"},
                               {"key": CAUSED_BY_KEY, "value": f"{CAN15}, host/expected/usb-0483-5740-28B3A25D"}]
    assert to_diagnostic_status(dataclasses.replace(rig, caused_by=()))["values"][-1]["key"] != CAUSED_BY_KEY
    # a stock message that happens to carry a caused_by value (anywhere) comes back exactly: it stays a value
    for message in REAL_WORLD + [stock, {**stock, "values": [stock["values"][-1], *stock["values"][:-1]]}]:
        assert to_diagnostic_status(from_diagnostic_status(message)) == message
    assert from_diagnostic_status(stock).caused_by == ()
    # the v0 transport carries it exactly; one path given as a string is one member
    assert from_dict(json.loads(json.dumps(to_dict(rig)))) == rig
    assert to_dict(rig)["caused_by"] == [CAN15, "host/expected/usb-0483-5740-28B3A25D"]
    assert DiagnosticStatus(WARN, "x", caused_by=CAN15).caused_by == (CAN15,)
    assert from_dict({**to_dict(rig), "caused_by": CAN15}).caused_by == (CAN15,)
    assert "caused_by" not in to_dict(dataclasses.replace(rig, caused_by=()))


def test_validate_names_a_broken_caused_by():
    rig = DiagnosticStatus(WARN, "rack: expected setup", "missing", path=RIG, code="ExpectedMissing", caused_by=(CAN15,))
    assert validate(rig) == []
    assert any("names its own entity" in problem for problem in validate(dataclasses.replace(rig, caused_by=(RIG,))))
    assert any("entity paths" in problem for problem in validate(dataclasses.replace(rig, caused_by=("",))))
    clash = dataclasses.replace(rig, values=(KeyValue("caused_by", "CAN 15"),))
    assert any("where caused_by rides" in problem for problem in validate(clash))
