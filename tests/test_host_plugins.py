"""Plugin registry + runner: declarations, matching, JSON-line parsing, and plugins that misbehave.

Every run here is a REAL subprocess (tests/fixtures/plugins/cases/case_plugin.py). None touches hardware.
"""

from __future__ import annotations

import sys
import time

import pytest
from test_host_support import (CASES, make_host, node_by_name, standard_skeleton, tty, usb_device, write_plugin)

from contract.record import ERROR, OK, WARN, DiagnosticStatus, Entity
from io_diagnostics.plugins import Match, load_plugins, match_devices, parse_match
from io_diagnostics.runner import parse_lines, relative, run_plugin


# ----- declarations -----------------------------------------------------------------------------------------------
def test_good_declaration_loads_with_placeholders_and_defaults(tmp_path):
    (tmp_path / "moteus.toml").write_text(
        'name = "moteus_servo"\ncommand = ["{python}", "{here}/driver.py"]\nmatches = ["usb:0483:5740", {by-id = "*fdcanusb*"}]\n'
        'env = { WHERE = "{root}" }\nflavour = "unknown key"\n')
    (specs, problems) = load_plugins(tmp_path)
    spec = specs[0]
    assert spec.command == (sys.executable, f"{tmp_path.resolve()}/driver.py")
    assert spec.cwd == tmp_path.resolve() and (spec.timeout_s, spec.period_s, spec.enabled, spec.replay) == (20.0, 30.0, True, False)
    assert [match.text() for match in spec.matches] == ["usb:0483:5740", "by-id:*fdcanusb*"]
    assert spec.env["WHERE"].endswith("io-diagnostics")
    assert problems == ["moteus.toml: unknown key 'flavour' ignored"]


@pytest.mark.parametrize("body, why", [
    ('name = "x"\ncommand = "python driver.py"\n', "argv list"),
    ('command = ["true"]\n', "name must be a word"),
    ('name = "x"\ncommand = ["true"]\nmatches = ["serial:abc"]\n', "match kind"),
    ('name = "x"\ncommand = ["true"]\ntimeout_s = -1\n', "timeout_s must be a positive number"),
    ('name = "x"\ncommand = []\n', "non-empty list"),
    ('name = "x"\ncommand = ["true"\n', "TOMLDecodeError"),
])
def test_a_broken_declaration_is_skipped_with_its_reason(tmp_path, body, why):
    (tmp_path / "broken.toml").write_text(body)
    (tmp_path / "fine.toml").write_text('name = "fine"\ncommand = ["true"]\nmatches = ["usb:1234:*"]\n')
    specs, problems = load_plugins(tmp_path)
    assert [spec.name for spec in specs] == ["fine"]
    assert any(why in problem and problem.startswith("broken.toml") for problem in problems), problems


def test_two_plugins_with_one_name_keep_the_first(tmp_path):
    for file in ("a.toml", "b.toml"):
        (tmp_path / file).write_text('name = "same"\ncommand = ["true"]\nmatches = ["usb:1234:*"]\n')
    specs, problems = load_plugins(tmp_path)
    assert [spec.file.name for spec in specs] == ["a.toml"] and "already loaded" in problems[0]


def test_a_missing_directory_loads_nothing_and_says_so(tmp_path):
    specs, problems = load_plugins(tmp_path / "nope")
    assert specs == [] and "no such directory" in problems[0]


# ----- matching ---------------------------------------------------------------------------------------------------
def test_matches_read_lane_b_dicts_and_their_function_nodes():
    fdcanusb = {**usb_device("1-3", "0483", "5740", "272813EF", label="fdcanusb"),
                "functions": [tty("usb:1-3", "ttyACM0", "/dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00")]}
    assert parse_match("usb:0483:5740").accepts(fdcanusb)
    assert parse_match("usb:0483:*").accepts(fdcanusb) and parse_match("usb:0483").accepts(fdcanusb)
    assert not parse_match("usb:8086:0b5b").accepts(fdcanusb)
    assert parse_match("by-id:*fdcanusb*").accepts(fdcanusb)  # the link lives on the tty function node
    assert parse_match("tty:/dev/ttyACM*").accepts(fdcanusb)
    nic = {"bus": "pci", "id": "pci:0000:06:00.0", "attrs": {"kind": "network", "class": "Network controller",
                                                             "class_code": "020000", "vendor_id": "10ec", "device_id": "8168"}}
    assert parse_match("pci-class:0200").accepts(nic) and parse_match("pci-class:*network*").accepts(nic)
    assert parse_match("pci:10ec:8168").accepts(nic) and not parse_match("pci-class:0c03").accepts(nic)
    assert Match("netdev", "enp*").accepts({"bus": "net", "id": "net:enp6s0", "label": "enp6s0", "attrs": {}})


def test_structure_never_matches_and_a_function_node_yields_to_its_device(tmp_path):
    (tmp_path / "p.toml").write_text('name = "p"\ncommand = ["true"]\nmatches = ["by-id:*fdcanusb*", "usb:*:*"]\n')
    specs, _ = load_plugins(tmp_path)
    device = usb_device("1-3", "0483", "5740", "S", label="fdcanusb")
    serial = tty("usb:1-3", "ttyACM0", "/dev/serial/by-id/usb-mjbots_fdcanusb_S-if00")
    bus = {"bus": "usb", "id": "usb:usb1", "parent": "host", "attrs": {"kind": "bus", "vid": "1d6b", "pid": "0002"}}
    pairs = match_devices(specs, [{**device, "functions": [serial]}, {**serial, "functions": []}, bus])
    assert [device["id"] for _spec, device in pairs] == ["usb:1-3"]


# ----- parsing --------------------------------------------------------------------------------------------------
def test_parse_lines_keeps_records_and_rejects_everything_else_by_line():
    text = "\n".join([
        '{"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": "a", "path": "", "received": 5}',
        "not json", "[1, 2]", '{"record": "fault", "path": "", "code": "X"}', '{"record": "action", "name": "x"}',
        '{"schema": "other/1", "record": "status", "name": "b"}', '{"record": "gadget"}',
        '{"record": "status", "name": "c", "measurements": [{"nope": 1}]}',
        '{"record": "entity", "path": "can", "kind": "bus", "icon": "bus"}', ""])
    parsed = parse_lines(text)
    assert [type(record).__name__ for record in parsed.records] == ["DiagnosticStatus", "Entity"]
    assert [number for number, _why in parsed.rejected] == [2, 3, 4, 5, 6, 7, 8]
    assert "FaultManager" in parsed.rejected[2][1] and parsed.lines == 9
    assert parsed.received == {} and parsed.icons == {1: "bus"}  # a live plugin's "received" is never kept
    assert parse_lines(text, replay=True).received == {0: 5.0}  # a replay re-delivers its recorded receive time


def test_plugin_paths_are_relative_to_the_device():
    device = "host/usb1/port-1-3/usb-1-3"
    assert relative("", device) == "" and relative(".", device) == "" and relative("./can/15", device) == "can/15"
    assert relative(device, device) == "" and relative(f"{device}/can", device) == "can"
    assert relative("/can/1/", device) == "can/1" and relative(None, device) is None


# ----- runs -------------------------------------------------------------------------------------------------------
def spec_for(tmp_path, case, **options):
    write_plugin(tmp_path, f"case_{case}", case, ["usb:1234:*"], **options)
    specs, problems = load_plugins(tmp_path)
    assert not problems
    return next(spec for spec in specs if spec.name == f"case_{case}")


DEVICE = usb_device("9-1", "1234", "abcd", "SER")


def test_a_good_plugin_run_parses_every_line(tmp_path):
    outcome = run_plugin(spec_for(tmp_path, "spider"), DEVICE, "host/usb9/port-9-1/usb-9-1", time.time)
    assert outcome.outcome == "ok" and outcome.returncode == 0 and not outcome.rejected and not outcome.problems
    assert sum(isinstance(record, Entity) for record in outcome.records) == 9
    assert sum(isinstance(record, DiagnosticStatus) for record in outcome.records) == 9
    assert outcome.icons and set(outcome.icons.values()) == {"motor"}


def test_a_malformed_plugin_is_flagged_on_its_device_row_and_its_good_lines_are_used(tmp_path):
    host = make_host(tmp_path, standard_skeleton(DEVICE), [("bad", "malformed", ["usb:1234:abcd"])])
    host.run_all_once()
    device = node_by_name(host.state()["tree"], "thing")
    detail = host.node(device["path"])
    run = next(check for check in detail["checks"] if check["name"] == "bad: plugin run")
    assert (run["level"], run["code"]) == (WARN, "PluginOutputMalformed")
    assert "6 of 8 lines rejected" in run["message"] and "1 contract problem" in run["message"]
    names = {check["name"] for check in detail["checks"]}
    assert {"good: check", "no code: check"} <= names  # kept; the second one is the contract problem
    assert device["level"] == WARN  # its own worst: the malformed-output check, never a crash


def test_a_crashing_plugin_becomes_an_error_on_its_device_row(tmp_path):
    host = make_host(tmp_path, standard_skeleton(DEVICE), [("crashy", "crash", ["usb:1234:abcd"])])
    host.run_all_once()
    device = node_by_name(host.state()["tree"], "thing")
    assert device["level"] == ERROR and "boom: the driver crashed" in device["message"]
    run = next(check for check in host.node(device["path"])["checks"] if check["name"] == "crashy: plugin run")
    assert run["code"] == "PluginCrashed" and "exited 1" in run["message"]
    assert any(check["name"] == "before: crash" for check in host.node(device["path"])["checks"])
    assert host.plugins()["plugins"][0]["jobs"][0]["last_run"]["outcome"] == "crashed"


def test_a_hanging_plugin_is_killed_with_its_children_and_shown(tmp_path):
    host = make_host(tmp_path, standard_skeleton(DEVICE), [("sleepy", "hang", ["usb:1234:abcd"], {"timeout_s": 1})])
    started = time.monotonic()
    host.run_all_once()
    assert time.monotonic() - started < 6  # the child holding stdout died with the process group
    device = node_by_name(host.state()["tree"], "thing")
    assert device["level"] == ERROR and "no answer within 1 s" in device["message"]
    run = next(check for check in host.node(device["path"])["checks"] if check["name"] == "sleepy: plugin run")
    assert run["code"] == "PluginTimeout" and run["expected_period_s"] == 2
    assert any(check["name"] == "before: hang" for check in host.node(device["path"])["checks"])


def test_a_plugin_that_cannot_start_is_an_error_not_a_crash_of_the_host(tmp_path):
    directory = tmp_path / "plugins.d"
    directory.mkdir()
    (directory / "ghost.toml").write_text('name = "ghost"\ncommand = ["/nonexistent/ghost-driver"]\nmatches = ["usb:1234:*"]\n')
    from io_diagnostics.host import Host
    from test_host_support import FakeOs, SetClock

    host = Host(plugins_dir=directory, clock=SetClock(), os_source=FakeOs(standard_skeleton(DEVICE)))
    host.run_all_once()
    device = node_by_name(host.state()["tree"], "thing")
    assert device["level"] == ERROR and "could not start" in device["message"]


def test_a_plugin_declared_the_build_plan_way_is_matched_by_describe_and_rerooted_by_identity(tmp_path):
    """intel_rgbd's real shape: `timeout` / `period` keys, matches only in describe ({bus, vid, pid}), and records
    rooted at its own path. The host must run it and fold it onto the OS row, not add a duplicate row."""

    from io_diagnostics.host import Host
    from test_host_support import FakeOs, SetClock

    directory = tmp_path / "plugins.d"
    directory.mkdir()
    (directory / "cam.toml").write_text(f'name = "cam"\ncommand = ["{{python}}", "{CASES}", "--case", "foreign"]\n'
                                        "timeout = 7\nperiod = 60\n")
    host = Host(plugins_dir=directory, clock=SetClock(), os_source=FakeOs(standard_skeleton(DEVICE)))
    assert (host.specs[0].timeout_s, host.specs[0].period_s, host.specs[0].matches) == (7.0, 60.0, ())
    assert host.plugin_problems == []
    host.run_all_once()
    assert [match.text() for match in host.specs[0].matches] == ["usb:1234:ab*"]
    assert host.plugins()["plugins"][0]["describe"]["name"] == "case_foreign"
    root = host.state()["tree"]
    camera = node_by_name(root, "camera SER")  # the OS row "thing", renamed by the plugin: one row, not two
    assert camera["path"] == "host/usb9/port-9-1/usb-9-1" and camera["identity"]["serial_number"] == "SDK-777"
    stream = node_by_name(root, "depth stream")
    assert (stream["parent"], stream["edge"]) == (camera["path"], "part_of")
    assert {check["name"] for check in host.node(camera["path"])["checks"]} >= {"camera SER: link", "cam: plugin run"}
    assert all(not node["path"].startswith(camera["path"] + "/host") for node in [root, *walk(root)])


def walk(node):
    for child in node["children"]:
        yield child
        yield from walk(child)


def test_the_case_plugin_is_where_the_tests_expect_it():
    assert CASES.is_file() and OK == "ok"
