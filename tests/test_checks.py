"""Monitoring Plugins (Nagios) checks, the CLI seam of round 3: a plugins.d entry with ``kind = "check"`` runs any
command; its exit code is the level, its first stdout line the message, its perfdata measurements with units and
limits (https://www.monitoring-plugins.org/doc/guidelines.html §2, §2.4, §2.5, §2.6). Fake commands only (``sh -c``
lines and a python one-liner), plus the shipped example, which only reads ``df -P /``.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import pytest
from test_host_setup import SEED, Rack
from test_host_support import ROOT, FakeOs, SetClock, standard_skeleton, usb_device

from contract.record import ERROR, OK, UNKNOWN, WARN, KeyValue, Measurement, validate
from io_diagnostics.plugins import PluginConfigError, load_plugin, load_plugins
from io_diagnostics.host import Host
from io_diagnostics.runner import CHECK_SOURCE, _range, parse_check_output, parse_perfdata, run_check


def declare(directory: Path, name: str, script: str, *, attach: str = "host", timeout_s: float = 5, extra: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text(f'name = "{name}"\nkind = "check"\ncommand = {json.dumps(["/bin/sh", "-c", script])}\nperiod_s = 30\n'
                    f"timeout_s = {timeout_s}\nattach = {json.dumps(attach)}\n" + extra)  # JSON strings are TOML strings
    return path


def spec_of(tmp_path, script: str, **options):
    spec, notes = load_plugin(declare(tmp_path / "plugins.d", "probe", script, **options))
    assert notes == []
    return spec


# ----- §2.5 ranges and §2.6 perfdata ----------------------------------------------------------------------------------
def test_ranges_follow_the_guidelines_table():
    # Table 3 "Generate an alert if x...": outside the range (inclusive), or inside it with "@"
    assert _range("10") == (0.0, 10.0, False)
    assert _range("10:") == (10.0, None, False)
    assert _range("~:10") == (None, 10.0, False)
    assert _range("10:20") == (10.0, 20.0, False)
    assert _range("@10:20") == (10.0, 20.0, True)
    for bad in ("20:10", "abc", "", "1:x"):
        assert _range(bad) is None, bad


def test_perfdata_becomes_measurements_with_units_and_limits():
    measurements, values, ignored = parse_perfdata(
        "'/ used'=92%;95;99;0;100 time=0.0042s;1;2 load1=1.5;~:4;~:8 'it''s'=3 temp=U junk 'x'=7c 'x'=8 bytes=10KB")
    by_key = {measurement.key: measurement for measurement in measurements}
    assert by_key["/ used"] == Measurement("/ used", 92, "%", warn_low=0.0, warn_high=95.0, error_low=0.0, error_high=99.0)
    assert by_key["time"] == Measurement("time", 0.0042, "s", warn_low=0.0, warn_high=1.0, error_low=0.0, error_high=2.0)
    assert by_key["load1"] == Measurement("load1", 1.5, "1", warn_high=4.0, error_high=8.0)
    assert by_key["it's"].value == 3  # '' is a quote inside a quoted label
    assert by_key["temp"].value is None and by_key["temp"].level == UNKNOWN  # "U": could not be determined
    assert (by_key["x"].value, by_key["x"].unit) == (7, "{counter}") and by_key["bytes"].unit == "kBy"
    assert KeyValue("/ used min..max", "0 .. 100") in values
    assert ignored == ["junk", "'x'=8"]  # not label=value, and a label given twice
    # the record's own limit rule is the guidelines' (endpoints inside): 95 % is OK, 96 % is a warning
    assert Measurement("u", 95, "%", warn_high=95.0).level == OK and Measurement("u", 96, "%", warn_high=95.0).level == WARN


def test_an_inside_range_or_crossed_thresholds_stay_text():
    measurements, values, _ = parse_perfdata("a=5;@10:20;30 b=5;10;5")
    a, b = measurements
    assert (a.warn_low, a.warn_high, a.error_low, a.error_high) == (None, None, 0.0, 30.0)
    assert KeyValue("a warn", "@10:20") in values
    assert (b.warn_high, b.error_high) == (None, None) and KeyValue("b thresholds", "10;5") in values


# ----- §2 and §2.4: output and return codes ---------------------------------------------------------------------------
def test_exit_codes_are_the_level_and_the_first_line_the_message():
    for code, level, reason in ((0, OK, ""), (1, WARN, "CheckWarning"), (2, ERROR, "CheckCritical"), (3, UNKNOWN, "CheckUnknown")):
        level_out, code_out, message, _values, _measurements = parse_check_output("DISK STATE - fine | a=1\n", code)
        assert (level_out, code_out, message) == (level, reason, "DISK STATE - fine")
    level, code, message, values, _ = parse_check_output("PROC OK\n", 255)
    assert (level, code) == (UNKNOWN, "CheckUnknown") and message.startswith("exit code 255 is not 0-3")
    level, _code, message, _values, _ = parse_check_output("", 0)
    assert (level, message) == (OK, "the check printed nothing")


def test_long_output_and_perfdata_on_later_lines():
    stdout = "DISK OK - free space: / 3326 MB (56%); | /=2643MB;5948;5958;0;5968\n/ 15272 MB (77%);\n/boot 68 MB (69%);\n" \
             "/home 69357 MB (27%);\n/var/log 819 MB (84%); | /boot=68MB;88;93;0;98\n/home=69357MB;253404;253409;0;253414\n"
    _level, _code, message, values, measurements = parse_check_output(stdout, 0)  # the guidelines' own example shape
    assert message == "DISK OK - free space: / 3326 MB (56%);"
    assert dict(values)["long_output"] == "/ 15272 MB (77%);\n/boot 68 MB (69%);\n/home 69357 MB (27%);\n/var/log 819 MB (84%);"
    assert [(m.key, m.value, m.unit) for m in measurements] == [("/", 2643, "MBy"), ("/boot", 68, "MBy"), ("/home", 69357, "MBy")]


def test_a_check_run_is_one_valid_status(tmp_path):
    status = run_check(spec_of(tmp_path, "echo 'LOAD WARNING - 9 | load1=9;4;8'; exit 1"), time.time)
    assert (status.level, status.code, status.message, status.source) == (WARN, "CheckWarning", "LOAD WARNING - 9", CHECK_SOURCE)
    assert status.expected_period_s == 30 and dict(status.values)["exit_code"] == "1"
    assert [m.key for m in status.measurements] == ["load1", "check duration"]
    assert validate(dataclasses.replace(status, path="host")) == []


def test_a_hung_check_is_killed_with_its_children_and_reads_unknown(tmp_path):
    started = time.monotonic()
    status = run_check(spec_of(tmp_path, "sleep 30 & sleep 30", timeout_s=0.5), time.time)
    assert time.monotonic() - started < 10
    assert (status.level, status.code) == (UNKNOWN, "CheckTimeout")
    missing = run_check(dataclasses.replace(spec_of(tmp_path, "true"), command=("/nonexistent/check_nothing",)), time.time)
    assert (missing.level, missing.code) == (UNKNOWN, "CheckNotStarted")


# ----- declarations ----------------------------------------------------------------------------------------------
def test_a_check_declares_where_it_attaches_and_nothing_a_driver_has(tmp_path):
    spec = spec_of(tmp_path, "exit 0", attach="usb:0483:5740:272813EF")
    assert (spec.kind, spec.attach, spec.matches, spec.accepts({"bus": "usb", "attrs": {"vid": "0483"}})) == (
        "check", "usb:0483:5740:272813EF", (), False)
    with pytest.raises(PluginConfigError, match="needs attach"):
        load_plugin(declare(tmp_path / "a", "x", "exit 0", attach=" "))
    with pytest.raises(PluginConfigError, match="takes no matches"):
        load_plugin(declare(tmp_path / "b", "x", "exit 0", extra='matches = ["usb:1234:*"]\n'))
    driver = tmp_path / "c" / "d.toml"
    driver.parent.mkdir()
    driver.write_text('name = "d"\ncommand = ["true"]\nmatches = ["usb:1234:*"]\nattach = "host"\n')
    with pytest.raises(PluginConfigError, match="attach is for"):
        load_plugin(driver)
    bad = tmp_path / "e" / "k.toml"
    bad.parent.mkdir()
    bad.write_text('name = "k"\nkind = "probe"\ncommand = ["true"]\n')
    with pytest.raises(PluginConfigError, match="kind must be one of"):
        load_plugin(bad)


def test_the_shipped_example_is_disabled_and_runs_as_declared():
    specs, problems = load_plugins(ROOT / "plugins.d")
    spec = next(spec for spec in specs if spec.name == "check_root_disk")
    assert (spec.enabled, spec.kind, spec.attach) == (False, "check", "host") and problems == []
    status = run_check(spec, time.time)  # reads df -P / only
    assert status.code in ("", "CheckWarning", "CheckCritical") and status.message.startswith("DISK ")
    used = next(m for m in status.measurements if m.key == "/ used")
    assert (used.unit, used.warn_high, used.error_high) == ("%", 95.0, 99.0) and 0 <= used.value <= 100


# ----- the host: each check lands on its entity ------------------------------------------------------------------
def host_with(tmp_path, checks: list[tuple[str, str, str]], devices=()) -> Host:
    directory = tmp_path / "plugins.d"
    for name, script, attach in checks:
        declare(directory, name, script, attach=attach)
    directory.mkdir(parents=True, exist_ok=True)
    host = Host(plugins_dir=directory, clock=SetClock(1000.0), os_source=FakeOs(standard_skeleton(*devices)))
    host.run_all_once()
    return host


def test_checks_attach_to_the_host_a_device_and_a_missing_entity(tmp_path):
    device = usb_device("9-1", "1234", "abcd", "SER")
    host = host_with(tmp_path, [("disk", "echo 'DISK CRITICAL - 99% used | used=99%;95;98'; exit 2", "host"),
                                ("widget", "echo 'WIDGET OK'", "usb:1234:abcd:SER"),
                                ("gone", "echo 'GONE WARNING - stale'; exit 1", "usb:dead:beef:NOPE")], devices=[device])
    on_host = {check["name"]: check for check in host.node("host")["checks"]}
    assert (on_host["disk"]["level"], on_host["disk"]["code"], on_host["disk"]["source"]) == (ERROR, "CheckCritical", CHECK_SOURCE)
    assert host.state()["tree"]["own"]["level"] == ERROR  # the Computer's own report now
    widget_row = host.layer.device_paths["usb:9-1"]
    assert [check["name"] for check in host.node(widget_row)["checks"] if check["source"] == CHECK_SOURCE] == ["widget"]
    gone = host.node("host/check-gone")
    assert gone["node"]["parent"] == "host" and dict((v["key"], v["value"]) for v in gone["checks"][0]["values"])["attach"] == \
        "usb:dead:beef:NOPE (not here now)"
    # counted once each, kept in the history and the fault memory, listed as plugins with their attached row
    assert host.state()["summary"]["counts"] == {"error": 1, "warn": 1}
    assert [status["name"] for status in host.history_json("host")["statuses"]] == ["disk"]
    assert any(fault.code == "CheckCritical" for fault in host.faults.all())
    listed = {plugin["name"]: plugin for plugin in host.plugins()["plugins"]}
    assert listed["disk"]["kind"] == "check" and listed["disk"]["jobs"][0]["device_path"] == "host"
    assert listed["disk"]["jobs"][0]["last_run"]["returncode"] == 2 and listed["disk"]["describe"] is None


def test_a_check_attaches_to_a_rig_row(tmp_path):
    rack = Rack(tmp_path, manifest=SEED)
    declare(tmp_path / "plugins.d", "rack_air", "echo 'AIR OK - 21 C | air=21;30;35'", attach="rig:1-DOF test rack")
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=SetClock(rack.host.clock.at), os_source=rack.host.os,
                expected_file=rack.file)
    host.run_all_once()
    rig = next(node for node in host.state()["tree"]["children"] if node["kind"] == "rig")
    assert "rack_air" in [check["name"] for check in host.node(rig["path"])["checks"]]


def test_a_disabled_check_never_runs_unless_enabled(tmp_path):
    directory = tmp_path / "plugins.d"
    marker = tmp_path / "ran"
    declare(directory, "quiet", f"touch {marker}; echo 'QUIET OK'", extra="enabled = false\n")
    Host(plugins_dir=directory, clock=SetClock(1000.0), os_source=FakeOs(standard_skeleton())).run_all_once()
    assert not marker.exists()
    host = Host(plugins_dir=directory, clock=SetClock(1000.0), os_source=FakeOs(standard_skeleton()), enable=("quiet",))
    host.run_all_once()
    assert marker.exists() and host.describes["quiet"] is None  # a check is never asked to describe
