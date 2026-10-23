"""Integration: the host with its real plugin declarations, the real driver plugins in passive mode, and the live OS
skeleton of this machine.

Safety pins first (run anywhere): plugins.d ships both hardware-touching drivers DISABLED, `--enable` is the only
way to run them, and the live-passive declarations can only ever run `diagnose ... --passive`. Then two live runs,
skipped off the rack PC: the passive skeleton + the fixture plugin (`once --json`), and the real moteus_servo /
intel_rgbd plugins through the host with `--passive` (sysfs, by-id, /proc only: no CAN adapter or camera is
opened; both drivers' own tests pin what --passive reads).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from test_host_support import ROOT, FakeOs, SetClock, node_by_name, standard_skeleton, usb_device, write_plugin

from io_diagnostics.host import Host
from io_diagnostics.plugins import load_plugins, match_devices

CLI = ROOT / "io_diagnostics" / "cli.py"
PLUGINS_D = ROOT / "plugins.d"
LIVE_PASSIVE = ROOT / "tests" / "fixtures" / "plugins-live-passive"
FIXTURE_PLUGINS = ROOT / "tests" / "fixtures" / "plugins"
RACK_PC = "bam-GPU"
DRIVERS = {"moteus_servo": "moteus_servo.diagnostics", "intel_rgbd": "intel_rgbd.diagnostics"}


def walk(node: dict):
    yield node
    for child in node["children"]:
        yield from walk(child)


# ----- the declarations ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_plugins_d_runs_the_real_driver_module_in_its_own_project_enabled_in_the_rack_bus_group(name):
    declared = tomllib.loads((PLUGINS_D / f"{name}.toml").read_text())
    command = declared["command"]
    # WHY enabled (2026-10-22): query-only bus traffic is approved; writes run only through `act`, for a person
    assert declared["enabled"] is True, f"{name}: the rack drivers ship on, so plugging in the rack shows it live"
    assert declared["exclusive"] == "rack-bus", f"{name}: both take the rack bus lock, so they must take turns"
    assert "diagnose_args" not in declared, f"{name}: plugins.d runs the driver's own default (query-only) diagnose"
    # one spelling in the shipped files; act gets its own, longer limit (firmware flash, calibration)
    assert {"timeout_s", "period_s", "act_timeout_s"} <= set(declared) and not {"timeout", "period", "act_timeout"} & set(declared)
    assert declared["act_timeout_s"] >= 900 > declared["timeout_s"]
    assert command[:2] == ["uv", "run"] and command[command.index("-m") + 1] == DRIVERS[name]
    project = Path(command[command.index("--project") + 1])
    assert project == Path(declared["cwd"]) and project.is_absolute()
    if project.is_dir():  # the driver checkout is on this machine: the module the command names exists
        assert (project / "src" / name / "diagnostics.py").is_file()


def test_a_host_on_plugins_d_matches_the_rack_adapter_without_being_asked():
    """Matching only (a pure function): no test runs plugins.d. intel_rgbd takes its matches from its describe."""

    specs, problems = load_plugins(PLUGINS_D)
    # the computer plugin (2026-10-23) matches only the host, never a device
    assert {spec.name for spec in specs} == set(DRIVERS) | {"computer"} and problems == []
    fdcanusb = usb_device("1-3", "0483", "5740", "272813EF", label="fdcanusb")
    d405 = usb_device("6-1", "8086", "0b5b", "219323070276", label="D405")
    assert [(spec.name, device["id"]) for spec, device in match_devices(specs, [fdcanusb, d405])] == [("moteus_servo", "usb:1-3")]


@pytest.mark.parametrize("argv", [
    ["/bin/echo", "-m", "moteus_servo.diagnostics", "diagnose", "--device", "usb:1-3"],
    ["uv", "run", "python", "-m", "intel_rgbd.diagnostics", "act", "--device", "x", "--action", "reboot"],
    [sys.executable, str(CLI), "once", "--json"],  # no --plugins / --fixture: it would load plugins.d
    [sys.executable, str(CLI), "act", "--path", "host", "--action", "reboot", "--dry-run"],
])
def test_the_suite_tripwire_refuses_hardware_before_anything_runs(argv):
    """tests/test_host_support.py patches subprocess.Popen for the whole session (plugins.d ships the drivers on).
    Every argv here is harmless if the tripwire broke: echo, a missing module, or a host that exits on its own."""

    import test_host_support

    before = len(test_host_support.TRIPPED)
    with pytest.raises(PermissionError, match="hardware tripwire"):
        subprocess.run(argv, capture_output=True, timeout=10)
    assert len(test_host_support.TRIPPED) == before + 1


def test_the_suite_tripwire_lets_the_guarded_and_the_directed_through():
    from test_host_support import tripwire_refuses

    assert tripwire_refuses([sys.executable, str(LIVE_PASSIVE / "passive_guard.py"), "uv", "run", "python", "-m",
                             "moteus_servo.diagnostics", "diagnose", "--device", "usb:1-3", "--passive"]) == ""
    assert tripwire_refuses([sys.executable, str(CLI), "once", "--json", "--fixture", "F1"]) == ""
    assert tripwire_refuses([sys.executable, str(CLI), "act", "--plugins", "/tmp/x", "--path", "p", "--action", "a"]) == ""


@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_the_live_passive_declarations_can_only_run_passive_and_match_plugins_d(name):
    passive = tomllib.loads((LIVE_PASSIVE / f"{name}.toml").read_text())
    shipped = tomllib.loads((PLUGINS_D / f"{name}.toml").read_text())
    assert passive["diagnose_args"] == ["--passive"]
    assert passive["command"][:2] == ["{python}", "{here}/passive_guard.py"]  # independent of the host's argv code
    assert passive["command"][2:] == shipped["command"] and passive["cwd"] == shipped["cwd"]
    assert passive.get("matches") == shipped.get("matches")


@pytest.mark.parametrize("after_verb, allowed", [
    (["describe"], True),
    (["diagnose", "--device", "usb:1-3", "--passive"], True),
    (["diagnose", "--device", "usb:1-3"], False),  # the host dropped diagnose_args: the driver must not run
    (["act", "--device", "usb:1-3", "--action", "reboot", "--passive"], False),
    ([], False),
])
def test_the_passive_guard_refuses_before_the_driver_starts(after_verb, allowed):
    driver = ["/bin/echo", "DRIVER-RAN", "-m", "moteus_servo.diagnostics"]
    completed = subprocess.run([sys.executable, str(LIVE_PASSIVE / "passive_guard.py"), *driver, *after_verb],
                               capture_output=True, text=True, timeout=10)
    assert ("DRIVER-RAN" in completed.stdout) is allowed, completed
    assert completed.returncode == (0 if allowed else 2)


# ----- host features the integration needed ----------------------------------------------------------------------
def test_diagnose_args_follow_diagnose_device(tmp_path):
    directory = tmp_path / "plugins.d"
    directory.mkdir()
    (directory / "echo.toml").write_text(
        f'name = "echo"\ncommand = ["{{python}}", "{ROOT}/tests/fixtures/plugins/cases/case_plugin.py", "--case", "echo_argv"]\n'
        'diagnose_args = ["--passive", "--range", "1", "3"]\nmatches = ["usb:1234:*"]\n')
    host = Host(plugins_dir=directory, clock=SetClock(1000.0), os_source=FakeOs(standard_skeleton(usb_device("9-1", "1234", "abcd", "S"))))
    host.run_all_once()
    detail = host.node(host.layer.device_paths["usb:9-1"])
    check = next(check for check in detail["checks"] if check["name"] == "argv: diagnose")
    assert check["message"].endswith("diagnose --device usb:9-1 --passive --range 1 3")
    assert host.plugins()["plugins"][0]["diagnose_args"] == ["--passive", "--range", "1", "3"]


def test_diagnose_args_must_be_strings(tmp_path):
    (tmp_path / "bad.toml").write_text('name = "bad"\ncommand = ["true"]\ndiagnose_args = "--passive"\n')
    specs, problems = load_plugins(tmp_path)
    assert specs == [] and "diagnose_args must be a list of strings" in problems[0]


def test_enable_runs_a_disabled_plugin_for_this_host_only(tmp_path):
    skeleton = standard_skeleton(usb_device("9-1", "1234", "abcd", "S", label="widget"))
    path = write_plugin(tmp_path / "plugins.d", "spider", "spider", ["usb:1234:*"])
    path.write_text(path.read_text() + "enabled = false\n")

    off = Host(plugins_dir=path.parent, clock=SetClock(1000.0), os_source=FakeOs(skeleton))
    off.run_all_once()
    assert off.jobs == {} and node_by_name(off.state()["tree"], "widget")["children"] == []
    on = Host(plugins_dir=path.parent, clock=SetClock(1000.0), os_source=FakeOs(skeleton), enable=("spider", "nope"))
    on.run_all_once()
    assert list(on.jobs) == [("spider", "usb:9-1")]
    assert node_by_name(on.state()["tree"], "leg 3")["level"] == "error"
    assert on.plugins()["problems"] == [f"--enable nope: no plugin of that name in {path.parent}"]


# ----- beyond USB: lane B's fake rack machine (frame grabber, capture card, EtherCAT NICs) through the host --------
def test_the_host_tree_shows_pcie_capture_cards_and_ethercat_from_lane_b_sources(tmp_path):
    from io_facts import skeleton

    root = skeleton.fake_root(ROOT / "tests" / "data" / "sysfs-rack-fixture.json", tmp_path / "machine")
    host = Host(plugins_dir=tmp_path / "none", clock=SetClock(1000.0), os_source=FakeOs(skeleton.skeleton(root)))
    host.refresh()
    tree = host.state()["tree"]
    rows = {node["name"]: node for node in walk(tree)}
    grabber = next(node for name, node in rows.items() if name.startswith("Likely frame grabber"))
    assert grabber["level"] == "warn"  # no kernel driver bound, and 3 correctable AER errors on its link
    checks = {check["name"].split(": ", 1)[1]: check for check in host.node(grabber["path"])["checks"]}
    assert (checks["kernel driver"]["code"], checks["PCIe link"]["code"]) == ("NoKernelDriver", "PcieCorrectableErrors")
    magewell = rows["Pro Capture Endpoint"]
    assert magewell["level"] == "warn" and magewell["message"].startswith("trained narrower than the card can: x1 of x4")
    assert any(child["name"].startswith("/dev/video") for child in magewell["children"])  # its video node under the card
    ethercat = [name for name in rows if name.endswith("EtherCAT segment (not scanned)")]
    assert sorted(ethercat) == ["enp6s0 · EtherCAT segment (not scanned)", "enp7s0 · EtherCAT segment (not scanned)"]
    camera = rows["Logi Webcam C920e"]
    assert sorted(child["name"].split(" ")[0] for child in camera["children"]) == ["/dev/video12", "/dev/video13"]
    for hub in ("USB2.0 HUB", "USB2.1 Hub"):  # the hubs above the camera own none of its nodes (V6's bug)
        assert not any(child["name"].startswith("/dev/video") for child in rows[hub]["children"])
    assert "/dev/ttyS0" not in rows and "/dev/ttyS1" in rows  # the 8250 placeholder is skipped, the real port kept


def test_once_machine_reviews_hardware_this_pc_lacks_through_the_real_sources():
    out = once("--machine", str(ROOT / "tests" / "data" / "sysfs-rack-fixture.json"), "--plugins", "/nonexistent")
    state = out["state"]
    assert state["host"]["name"] == "sysfs-rack-fixture (fake machine)"
    causes = {item["name"]: item["message"] for item in state["summary"]["attention"]}
    assert set(causes) == {"Likely frame grabber · Coreco Inc", "Pro Capture Endpoint"}, causes
    assert "no kernel driver bound" in causes["Likely frame grabber · Coreco Inc"]
    assert state["chip"]["label"] == "IO 2 ⚠"


def test_a_healthy_pcie_link_is_quiet_and_says_its_width():
    from io_diagnostics.tree import pci_statuses
    from contract.record import Entity

    gpu = {"link": {"speed": "16.0 GT/s PCIe", "width": 16, "max_speed": "16.0 GT/s PCIe", "max_width": 16},
           "aer": {"correctable": 0, "nonfatal": 0, "fatal": 0}, "driver": "nvidia", "link_narrower_than_max": False}
    [link] = pci_statuses(Entity(path="host/pci/gpu", name="GPU", kind="device"), gpu, "host/pci/gpu", 1000.0)
    assert (link.level, link.code, link.message) == ("ok", "", "x16 of x16 · 16.0 GT/s PCIe (max 16.0 GT/s PCIe)")
    idle = {**gpu, "link": {**gpu["link"], "speed": "2.5 GT/s PCIe"}}  # ASPM at idle: slower, same width, healthy
    assert pci_statuses(Entity(path="p", name="GPU", kind="device"), idle, "p", 1000.0)[0].level == "ok"
    broken = {**gpu, "aer": {"correctable": 0, "nonfatal": 1, "fatal": 0}}
    assert pci_statuses(Entity(path="p", name="GPU", kind="device"), broken, "p", 1000.0)[0].code == "PcieUncorrectableErrors"


# ----- a manufacturer's plugin: one executable + one toml (README.md shows these files verbatim) ------------------
EXAMPLE = ROOT / "examples" / "acme_gripper"


def test_the_readme_example_plugin_hangs_under_its_device_through_the_host():
    skeleton = standard_skeleton(usb_device("9-4", "1234", "5678", "G1-0042", label="USB gripper"))
    host = Host(plugins_dir=EXAMPLE, clock=SetClock(1000.0), os_source=FakeOs(skeleton))
    host.run_all_once()
    [run] = host.plugins()["plugins"][0]["jobs"]
    assert run["last_run"]["outcome"] == "ok" and run["last_run"]["problems"] == [], run
    device = node_by_name(host.state()["tree"], "Acme gripper G1-0042")
    assert device["identity"]["model"] == "Gripper G1" and device["level"] == "ok"
    assert (device["hint"] or {}).get("level") == "warn"  # the jaw's warning is a hint on the device, not its level
    jaw = node_by_name(device, "jaw")
    assert (jaw["level"], jaw["edge"]) == ("warn", "part_of")


def test_the_readme_shows_the_example_plugin_verbatim():
    readme = (ROOT / "README.md").read_text()
    for file in ("acme_gripper.py", "acme_gripper.toml"):
        assert (EXAMPLE / file).read_text().strip() in readme, f"README.md is out of date with examples/acme_gripper/{file}"


# ----- own the dashboard, interop by adapter: the real host -> lane C's bridge source -> stock CDR ----------------
def test_the_bridge_reads_the_real_hosts_records_and_they_survive_stock_cdr():
    """Lane C proved the bridge against a stub of GET /api/v1/records; this runs it against the real host."""

    pytest.importorskip("rosbags")
    from io_diagnostics.host import make_host as make_real_host
    from io_diagnostics.interop import stock
    from io_diagnostics.interop.foxglove_bridge import host_api_source
    from io_diagnostics.server import make_server, serve_in_thread

    host = make_real_host(fixture="F1", once=True)
    host.run_all_once()
    server = make_server(host, 0)
    serve_in_thread(server)
    try:
        now = host.clock.now()
        statuses = host_api_source(host.url)(now)
        array = stock.stock_array(statuses, now)
    finally:
        server.shutdown()
        server.server_close()
    assert len(statuses) == len(host.records()["statuses"]) > 20
    by_name = {status["name"]: status for status in array["status"]}
    driver = next(status for name, status in by_name.items() if name.endswith("CAN 15 driver: fault on enable"))
    assert driver["level"] in (2, b"\x02") and "fault 33" in driver["message"]
    assert set(driver) == {"level", "name", "message", "hardware_id", "values"}  # our added fields are stripped
    decoded = stock.from_cdr(stock.to_cdr(array))
    assert [status["name"] for status in decoded["status"]] == [status["name"] for status in array["status"]]


# ----- live, passive (the rack PC) -------------------------------------------------------------------------------
def once(*arguments: str, timeout: float = 180) -> dict:
    completed = subprocess.run([sys.executable, str(CLI), "once", "--json", *arguments], capture_output=True, text=True,
                               timeout=timeout, cwd="/")
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


live = pytest.mark.skipif(socket.gethostname() != RACK_PC, reason="reads the rack PC's own sysfs")


@live
def test_live_skeleton_covers_every_bus_and_the_fixture_plugin_attaches_to_the_real_devices():
    out = once("--plugins", str(FIXTURE_PLUGINS))
    state = out["state"]
    owners = {node["owner"] for node in walk(state["tree"])}
    for source in ("io_facts.usb", "io_facts.pci", "io_facts.netdev", "io_facts.tty", "io_facts.v4l"):
        assert source in owners, f"{source} missing from the live tree (beyond USB)"
    assert state["host"]["sources"][0]["name"] == "io_facts.skeleton" and state["host"]["sources"][0]["state"] == "used"
    [plugin] = out["plugins"]["plugins"]
    assert plugin["name"] == "v6_scenario" and plugin["jobs"], "the fixture plugin matched no live fdcanusb or D405"
    assert all(job["last_run"]["outcome"] == "ok" for job in plugin["jobs"])
    assert any(entity.get("owner") == "v6_scenario" for entity in out["records"]["entities"])


needs_drivers = pytest.mark.skipif(
    shutil.which("uv") is None or not all(Path(tomllib.loads((LIVE_PASSIVE / f"{name}.toml").read_text())["cwd"]).is_dir()
                                          for name in DRIVERS),
    reason="needs uv and both driver checkouts")


@live
@needs_drivers
def test_live_the_real_drivers_run_through_the_host_passively_and_hang_under_their_devices():
    out = once("--plugins", str(LIVE_PASSIVE), timeout=300)
    assert out["plugins"]["problems"] == []
    runs = {plugin["name"]: plugin["jobs"] for plugin in out["plugins"]["plugins"]}
    assert set(runs) == set(DRIVERS) and all(runs.values()), runs
    for name, jobs in runs.items():
        for job in jobs:
            assert job["last_run"]["outcome"] == "ok", (name, job)
            assert job["last_run"]["accepted"] > 1, (name, job)  # not just a DeviceNotFound event
    tree = out["state"]["tree"]
    by_path = {node["path"]: node for node in walk(tree)}
    for job in runs["moteus_servo"]:  # the adapter IS the OS row; its CAN bus hangs under it, boards under the bus
        bus = by_path[job["device_path"] + "/can"]
        assert (bus["owner"], bus["kind"], bus["level"]) == ("moteus_servo", "bus", "unknown")
    for job in runs["intel_rgbd"]:
        camera = next(node for node in walk(tree) if node["path"] == job["device_path"])
        assert camera["owner"] == "intel_rgbd" and camera["level"] == "unknown"  # PassiveOnly: the SDK was not asked
        streams = {child["name"] for child in camera["children"] if child["kind"] == "stream"}
        assert streams == {"colour stream", "depth stream"}
    passive_codes = {status.get("code") for status in out["records"]["statuses"]
                     if status.get("source") in DRIVERS and status.get("level") == "unknown"}
    assert passive_codes == {"PassiveOnly"}, passive_codes
