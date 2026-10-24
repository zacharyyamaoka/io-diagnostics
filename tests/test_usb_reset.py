"""Reset USB port: the host's own utility on every USB device and port row (docs/usb-reset.md; Zach, 2026-10-22: "we can also
incoperate like standard OS things in here as well like to manually reset a usb port etc...").

Nothing here touches a real USB device. The whole module is pointed at a FAKE tree (a sysfs of ports, a /dev, a /proc with
fake open files) and a FAKE ``usbreset`` that only appends to a log (and, like the kernel would, bumps the device number and
writes the kernel lines a reset leaves); tests/test_host_support.py's tripwire refuses the real tools and a real reset
without the fake tree named in the environment.

- the dry run shows the exact command, the three mechanisms and which this user may use;
- refused without privilege, with the plain-words fix and the exact udev rule (and the tool never ran);
- refused while a RobotDx run is active on the device (a hub too, by path), allowed again when it ends;
- the holders are listed; a real run refuses until the person confirms again (and a holder that came since the dry run voids
  the first confirmation); the host's own watch processes are not holders;
- a successful reset: RunStarted / RunEnded in Activity, the kernel's own plug / unplug lines after it, and the on-connect
  probe runs again (new device number or reset in place);
- a driver opts a device out, or registers the name itself; a replay never resets anything real.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from test_host_support import ROOT, FakeOs, standard_skeleton, usb_device

from contract.record import STANDARD_ACTIONS, validate
from io_diagnostics import activity, host_utilities, usb_reset
from io_diagnostics.host import Clock, Host, usb_reset_gate

RACK = ROOT / "tests" / "fixtures" / "plugins" / "rack_plugin.py"
TERMINAL = ROOT / "tests" / "fixtures" / "plugins-terminal" / "terminal_plugin.py"
PORT, VID, PID, BUS, DEVNUM = "9-3", "5678", "0001", 9, 6
NAME = "fdcanusb 0607623A"
ADAPTER = usb_device(PORT, VID, PID, "0607623A", label=NAME)
HUB = usb_device("9-2", "05e3", "0610", "", kind="hub", label="Hub")
BEHIND_HUB = usb_device("9-2.4", "1234", "abcd", "ZZ", label="Camera behind the hub", parent="usb:9-2")

FAKE_TOOL = r'''#!{python}
import datetime, os, sys
root = os.environ["FAKE_USB_ROOT"]
with open(os.path.join(root, "calls.log"), "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
mode = os.environ.get("FAKE_RESET_MODE", "replug")
port, bus = os.environ.get("FAKE_PORT", "9-3"), 9
now = datetime.datetime.now().astimezone().isoformat(timespec="microseconds")
def kernel(text):
    with open(os.path.join(root, "kernel.log"), "a") as out:
        out.write(f"{now} rig kernel: usb {port}: {text}\n")
devnum_file = os.path.join(root, "sys", port, "devnum")
old = int(open(devnum_file).read())
print(f"fake usbreset: resetting {sys.argv[1:]}", flush=True)
if mode == "fail":
    sys.exit(3)
if mode == "replug":
    kernel(f"USB disconnect, device number {old}")
    open(devnum_file, "w").write(str(old + 1))
    kernel(f"new full-speed USB device number {old + 1} using xhci_hcd")
    kernel("New USB device found, idVendor=5678, idProduct=0001, bcdDevice= 1.00")
    kernel("Product: fdcanusb 0607623A")
else:
    kernel(f"reset full-speed USB device number {old} using xhci_hcd")
'''


class Tree:
    """A fake /sys/bus/usb/devices, /dev and /proc, a fake usbreset on a bin directory, and the kernel log it writes to."""

    def __init__(self, root: Path, monkeypatch, *, node_writable: bool = True, authorized_writable: bool = False):
        self.root = root
        self.sysfs, self.dev, self.proc, self.bin = root / "sys", root / "dev", root / "proc", root / "bin"
        for directory in (self.sysfs, self.dev / "bus" / "usb" / f"{BUS:03d}", self.proc, self.bin):
            directory.mkdir(parents=True, exist_ok=True)
        self.add_port(PORT, DEVNUM, VID, PID, "fdcanusb", interfaces={"tty": "ttyACM0"})
        self.node(PORT).chmod(0o664 if node_writable else 0o444)
        (self.sysfs / PORT / "authorized").chmod(0o644 if authorized_writable else 0o444)
        self.tool = self.bin / "usbreset"
        self.tool.write_text(FAKE_TOOL.replace("{python}", sys.executable))
        self.tool.chmod(0o755)
        (root / "calls.log").write_text("")
        (root / "kernel.log").write_text("")
        monkeypatch.setenv("IO_DIAGNOSTICS_USB_SYSFS", str(self.sysfs))
        monkeypatch.setenv("IO_DIAGNOSTICS_DEV_ROOT", str(self.dev))
        monkeypatch.setenv("IO_DIAGNOSTICS_PROC", str(self.proc))
        monkeypatch.setenv("IO_DIAGNOSTICS_USBRESET", str(self.tool))
        monkeypatch.setenv("IO_DIAGNOSTICS_UHUBCTL", str(root / "no-uhubctl"))  # not installed
        monkeypatch.setenv("IO_DIAGNOSTICS_RESET_WAIT_S", "5")
        monkeypatch.setenv("FAKE_USB_ROOT", str(root))
        monkeypatch.setenv("FAKE_PORT", PORT)

    def add_port(self, port: str, devnum: int, vid: str, pid: str, product: str, interfaces: dict | None = None) -> None:
        directory = self.sysfs / port
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in (("busnum", str(BUS)), ("devnum", str(devnum)), ("idVendor", vid), ("idProduct", pid),
                            ("product", product), ("authorized", "1")):
            (directory / name).write_text(value + "\n")
        (self.dev / "bus" / "usb" / f"{BUS:03d}" / f"{devnum:03d}").write_text("")
        for kind, node in (interfaces or {}).items():
            (directory / f"{port}:1.0" / kind / node).mkdir(parents=True, exist_ok=True)
            (self.dev / node).write_text("")

    def node(self, port: str = PORT) -> Path:
        return self.dev / "bus" / "usb" / f"{BUS:03d}" / f"{int((self.sysfs / port / 'devnum').read_text()):03d}"

    def open_file(self, pid: int, program: str, node: str, *, session: int | None = None) -> None:
        """A fake process ``pid`` that has ``/dev/<node>`` open: <proc>/<pid>/{fd/3 -> node, cmdline, comm, stat}."""

        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True, exist_ok=True)
        link = directory / "fd" / "3"
        link.unlink(missing_ok=True)
        os.symlink(self.dev / node, link)
        (directory / "cmdline").write_text(f"python3\0/home/bam/bin/{program}\0")
        (directory / "comm").write_text("python3\n")
        (directory / "stat").write_text(f"{pid} (python3) S 1 {pid} {session or pid} 0 0\n")

    def close_file(self, pid: int) -> None:
        import shutil

        shutil.rmtree(self.proc / str(pid))

    def calls(self) -> list[str]:
        return [line for line in (self.root / "calls.log").read_text().splitlines() if line]

    def kernel_lines(self) -> list[str]:
        return (self.root / "kernel.log").read_text().splitlines()


@pytest.fixture
def tree(tmp_path, monkeypatch):
    return Tree(tmp_path / "usb", monkeypatch)


class ResetOs(FakeOs):
    """A live-like OS over a skeleton, with the kernel log the fake tool writes to; the reset gates read the fake tree."""

    def __init__(self, tree: Tree, skeleton: list[dict], *, mode: str = "live"):
        super().__init__(skeleton)
        self.tree, self.mode = tree, mode
        self.start = time.time() - 3600

    def boots(self):
        return [activity.Boot(self.start, self.tree.kernel_lines())]


def declare(directory: Path, name: str, command: str, matches: str, extra: str = "") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.toml").write_text(f'name = "{name}"\ncommand = {command}\nmatches = ["{matches}"]\ntimeout_s = 20\n'
                                            f"period_s = 30\n{extra}")


class Rig:
    """One host over the fake tree, the rack stand-in plugin matched to the adapter, and runs kept in memory."""

    def __init__(self, tmp_path: Path, tree: Tree, devices=(ADAPTER,), *, plugin: bool = True, os_mode: str = "live",
                 extra_plugins=None, start: bool = False):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp, self.tree = tmp_path, tree
        self.log = tmp_path / "rack.jsonl"
        self.state = tmp_path / "rack-state.json"
        self.state.write_text(json.dumps({"name": NAME, "boards": {"12": {"answers": True, "model": "moteus-c1"}}}))
        plugins = tmp_path / "plugins.d"
        plugins.mkdir()
        if plugin:
            declare(plugins, "rack", f'["{sys.executable}", "{RACK}"]', f"usb:{VID}:{PID}",
                    f'env = {{ RACK_STATE = "{self.state}", RACK_LOG = "{self.log}", RACK_PROBE = "1", RACK_FOLLOW = "0" }}\n')
        if extra_plugins:
            extra_plugins(plugins)
        self.os = ResetOs(tree, standard_skeleton(*devices), mode=os_mode)
        self.host = Host(plugins_dir=plugins, clock=Clock(), os_source=self.os)
        if start:
            self.host.start(udev=False)
        else:
            self.host.refresh()

    def path(self, port: str = PORT) -> str:
        return self.host.layer.device_paths[f"usb:{port}"]

    def port_path(self, port: str = PORT) -> str:
        return self.host.layer.port_paths[port]

    def action(self, path: str, name: str = "reset_port") -> dict | None:
        return next((action for action in self.host.node(path)["actions"] if action["name"] == name), None)

    def dry(self, path: str | None = None) -> dict:
        return self.host.act(path or self.path(), "reset_port", dry_run=True, human=False)

    def reset(self, path: str | None = None, *, holders_ok: bool = False, token: str | None = None):
        """A person's press: the dry run's token, then the run, waited for. ``(run, answer)``."""

        path = path or self.path()
        token = token or self.dry(path)["action_result"]["token"]
        run, answer = self.host.start_act(path, "reset_port", confirm=token, interactive=True,
                                          extra=["--confirm-holders"] if holders_ok else None)
        if run is not None:
            run.wait(30)
        return run, answer

    def probes(self) -> int:
        if not self.log.exists():
            return 0
        return sum(1 for line in self.log.read_text().splitlines() if json.loads(line)["verb"] == "probe")

    def activity(self, *types: str) -> list[dict]:
        return self.host.activity(200, types or None)["events"]

    def stop(self) -> None:
        self.host.stop()


@pytest.fixture
def rig(tmp_path, tree):
    made: list[Rig] = []

    def make(**options) -> Rig:
        made.append(Rig(tmp_path / f"rig{len(made)}", tree, **options))
        return made[-1]

    yield make
    for each in made:
        each.stop()


def wait_for(condition, timeout: float = 15.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")


# ----- the vocabulary -------------------------------------------------------------------------------------------------------
def test_the_name_title_and_danger_are_the_standard_vocabulary():
    assert STANDARD_ACTIONS["reset_port"] == "Reset USB port" == usb_reset.TITLE
    assert host_utilities.RESET_PORT.danger == "interrupts" and validate(host_utilities.RESET_PORT) == []
    assert host_utilities.RESET_PORT.name == "reset_port"


# ----- where it is offered ----------------------------------------------------------------------------------------------------
def test_it_is_offered_on_a_usb_device_row_and_its_port_row_and_nowhere_else(rig):
    rig_ = rig()
    device, port = rig_.path(), rig_.port_path()
    for path in (device, port):
        action = rig_.action(path)
        assert action and action["title"] == "Reset USB port" and action["danger"] == "interrupts" and action["scope"] == "device"
        assert action["owner"] == "io_diagnostics"
        assert NAME in action["confirm"] and PORT in action["confirm"]
        assert all(word in action["confirm"] for word in ("tview", "camera stream", "experiment"))
    bus = next(path for path, entity in rig_.host.layer.entities.items() if entity.kind == "bus")
    assert rig_.action(bus) is None and rig_.action("host") is None
    assert [a["name"] for a in rig_.host.node(bus)["actions"]] == []


def test_a_driver_opts_a_device_out_in_describe_and_a_hub_is_not_reset_under_it(rig, tmp_path):
    keeper = tmp_path / "keeper.py"
    keeper.write_text('import json\nprint(json.dumps({"name": "keeper", "schema": "diagnostics-record/0", "opt_out": ["reset_port"],'
                      ' "actions": []}))\n')
    open_ = rig(devices=(HUB, BEHIND_HUB, ADAPTER))
    assert all(open_.action(open_.path(port)) for port in ("9-2", "9-2.4", PORT)), "offered while nobody opts out"
    held = rig(devices=(HUB, BEHIND_HUB, ADAPTER),
               extra_plugins=lambda directory: declare(directory, "keeper", f'["{sys.executable}", "{keeper}"]', "usb:1234:abcd"))
    for port in ("9-2.4", "9-2"):  # the device that opted out, and the hub that would take it down with it
        for path in (held.path(port), held.port_path(port)):
            assert held.action(path) is None, path
            assert held.host.act(path, "reset_port", dry_run=True, human=False)["refused"] in ("ActionNotRegistered", "NoPlugin"), path
    assert held.action(held.path()) is not None, "a device nobody opted out stays offered"


def test_a_driver_that_registers_reset_port_itself_replaces_the_built_in(rig):
    import dataclasses

    rig_ = rig()
    path = rig_.path()
    entity = rig_.host.layer.entities[path]
    own = dataclasses.replace(host_utilities.RESET_PORT, confirm="The driver's own words")
    registered = dataclasses.replace(entity, actions=(own,))
    actions = rig_.host._entity_actions(path, registered, rig_.host.view())
    assert [action.confirm for action in actions if action.name == "reset_port"] == ["The driver's own words"]


# ----- the dry run ------------------------------------------------------------------------------------------------------------
def test_the_dry_run_shows_the_exact_command_the_mechanisms_and_the_preconditions(rig, tree):
    rig_ = rig()
    dry = rig_.dry()
    assert (dry["refused"], dry["outcome"], dry["plugin"], dry["registered_by"]) == (None, "ok", "io_diagnostics", "builtin")
    assert dry["argv"][-6:] == ["act", "--device", f"usb:{PORT}", "--action", "reset_port", "--dry-run"]
    answer = dry["action_result"]
    assert answer["dry_run"] is True and answer["method"] == "usbreset" and answer["command_kind"] == "argv"
    assert answer["command"] == [str(tree.tool), "009/006"], "the exact argv: usbreset by bus/device number, never by vid:pid"
    assert [(m["name"], m["usable"]) for m in answer["methods"]] == [("usbreset", True), ("authorized", False), ("uhubctl", False)]
    assert "root only" in answer["methods"][1]["why_not"] and "not installed" in answer["methods"][2]["why_not"]
    assert {p["check"]: p["state"] for p in answer["preconditions"]} == {"device_present": "pass", "reset_privilege": "pass",
                                                                         "device_free": "pass"}
    assert answer["holders"] == [] and answer["second_confirm"] is None and answer["fix"] is None and answer["token"]
    assert tree.calls() == [], "a dry run runs nothing"


def test_the_dry_run_from_the_port_row_resets_the_device_in_it(rig, tree):
    rig_ = rig()
    dry = rig_.dry(rig_.port_path())
    assert dry["refused"] is None and dry["device_id"] == f"usb:{PORT}" and dry["action_result"]["command"][-1] == "009/006"
    assert tree.calls() == []


def test_an_empty_port_has_the_button_blocked_and_a_dry_run_refuses_it(rig, tree):
    rig_ = rig(devices=())
    now = dt.datetime.now().astimezone().isoformat(timespec="microseconds")
    with (tree.root / "kernel.log").open("a") as log:  # a device came and went at 9-4: the kernel log gives its port a row
        log.write(f"{now} rig kernel: usb 9-4: new full-speed USB device number 3 using xhci_hcd\n"
                  f"{now} rig kernel: usb 9-4: USB disconnect, device number 3\n")
    rig_.host.refresh()
    port = rig_.port_path("9-4")
    action = rig_.action(port)
    assert action and action["state"] == "blocked" and "nothing is plugged in at 9-4" in action["reason"]
    dry = rig_.dry(port)
    assert dry["refused"] == "NoDevice" and "nothing is plugged in at 9-4" in dry["message"]


# ----- privilege ---------------------------------------------------------------------------------------------------------------
def test_refused_without_privilege_with_the_fix_in_plain_words_and_the_tool_never_ran(rig, tree):
    tree.node().chmod(0o444)  # you cannot write to /dev/bus/usb/009/006, and authorized is root only, and no uhubctl
    rig_ = rig()
    action = rig_.action(rig_.path())
    assert action["state"] == "blocked" and "no way to reset it as your user" in action["reason"]
    privilege = next(p for p in action["preconditions"] if p["check"] == "reset_privilege")
    assert privilege["state"] == "fail" and "usbreset: cannot write" in privilege["detail"] and "authorized: root only" in privilege["detail"]
    answer = rig_.dry()["action_result"]
    rule = ('SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="5678", ATTR{idProduct}=="0001", '
            'GROUP="plugdev", MODE="0664"')
    assert answer["udev"] == {"file": usb_reset.UDEV_FILE, "rule": rule,
                              "then": "sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=usb --action=change"}
    assert "may not reset fdcanusb 0607623A yet" in answer["fix"] and rule in answer["fix"] and usb_reset.UDEV_FILE in answer["fix"]
    assert answer["command"].startswith("(none allowed yet") and answer["method"] is None
    run, _ = rig_.reset()
    assert run.state == "failed" and run.exit_code == 2
    refusal = next(r for r in run.result["records"] if r["code"] == "ActionRefused")
    assert refusal["args"]["reason"] == "NoPrivilege" and rule in refusal["args"]["fix"]
    assert rule in run.output_bytes().decode(), "the terminal line says what to do, too"
    assert tree.calls() == [], "nothing ran"


def test_the_authorized_switch_is_the_second_choice_and_uhubctl_the_last(rig, tree, tmp_path, monkeypatch):
    tree.node().chmod(0o444)
    (tree.sysfs / PORT / "authorized").chmod(0o644)  # usbreset cannot, authorized can
    rig_ = rig()
    answer = rig_.dry()["action_result"]
    assert answer["method"] == "authorized" and answer["command_kind"] == "sysfs"
    assert answer["command"] == f"echo 0 > {tree.sysfs / PORT / 'authorized'} && echo 1 > {tree.sysfs / PORT / 'authorized'}"
    run, _ = rig_.reset()
    assert run.state == "done", run.result
    assert (tree.sysfs / PORT / "authorized").read_text() == "1", "the switch is left on"
    out = run.output_bytes().decode()
    assert out.index("echo 0 >") < out.index("echo 1 >"), "off, then on"
    assert tree.calls() == []
    # neither of the first two: uhubctl, with the hub's location and the port number
    tool = tmp_path / "uhubctl"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("IO_DIAGNOSTICS_UHUBCTL", str(tool))
    (tree.sysfs / PORT / "authorized").chmod(0o444)
    (tree.sysfs / "9").mkdir(exist_ok=True)  # the root hub 9: busnum 9 devnum 1 with a node you may write
    (tree.sysfs / "9" / "busnum").write_text("9\n")
    (tree.sysfs / "9" / "devnum").write_text("1\n")
    (tree.dev / "bus" / "usb" / "009" / "001").write_text("")
    third = rig().dry()["action_result"]
    assert third["method"] == "uhubctl" and third["command"] == [str(tool), "-l", "9", "-p", "3", "-a", "cycle"]
    assert usb_reset.hub_and_port("1-3.2") == ("1-3", "2") and usb_reset.hub_and_port("2-4") == ("2", "4")


# ----- a RobotDx run is active --------------------------------------------------------------------------------------------------
def test_refused_while_a_run_is_active_on_the_device_and_allowed_again_when_it_ends(tmp_path, tree, monkeypatch):
    monkeypatch.setenv("TERMINAL_LINES", "30")
    rig_ = Rig(tmp_path / "busy", tree, plugin=False)
    try:
        declare(rig_.tmp / "plugins.d", "terminal", f'["{sys.executable}", "{TERMINAL}"]', f"usb:{VID}:{PID}",
                'env = { TERMINAL_LOG = "' + str(rig_.tmp / "term.jsonl") + '" }\n')
        busy = Host(plugins_dir=rig_.tmp / "plugins.d", clock=Clock(), os_source=rig_.os)
        busy.run_all_once()
        path = busy.layer.device_paths[f"usb:{PORT}"]
        run, out = busy.start_act(path, "check_comms", interactive=True)
        assert run is not None and run.active
        action = next(a for a in busy.node(path)["actions"] if a["name"] == "reset_port")
        precondition = next(p for p in action["preconditions"] if p["check"] == "no_run_active")
        assert action["state"] == "blocked" and precondition["state"] == "fail" and "is running on it" in precondition["detail"]
        for where in (path, busy.layer.port_paths[PORT]):  # the port row overlaps by path too
            dry = busy.act(where, "reset_port", dry_run=True, human=False)
            assert dry["refused"] == "RunActive" and "Check communication" in dry["message"], dry
        real = busy.start_act(path, "reset_port", confirm="whatever", interactive=True)
        assert real[0] is None and real[1]["refused"] == "RunActive" and tree.calls() == []
        run.interrupt()
        run.wait(30)
        wait_for(lambda: not busy.runs.active(), what="the run to end")
        assert next(a for a in busy.node(path)["actions"] if a["name"] == "reset_port")["state"] == "ready"
        assert busy.act(path, "reset_port", dry_run=True, human=False)["refused"] is None
        busy.stop()
    finally:
        rig_.stop()


def test_a_run_on_a_hub_blocks_a_reset_of_a_device_behind_it_and_the_other_way_round(rig, monkeypatch):
    rig_ = rig(devices=(HUB, BEHIND_HUB), plugin=False)
    layer = rig_.host.layer
    hub, behind = layer.device_paths["usb:9-2"], layer.device_paths["usb:9-2.4"]
    below, above = (layer.devices["usb:9-2.4"], "9-2.4"), (layer.devices["usb:9-2"], "9-2")

    class Active:  # a stand-in for a running run: only the path it acts on matters
        def __init__(self, device_path):
            self.spec = type("Spec", (), {"device_path": device_path, "label": "Hub · Check communication"})

        def to_json(self):
            return {"started_iso": "now"}

    with monkeypatch.context() as patched:
        patched.setattr(rig_.host.runs, "active", lambda: [Active(hub)])
        assert "Hub · Check communication" in rig_.host._active_run_near(behind, layer, below), "a run on the hub"
        patched.setattr(rig_.host.runs, "active", lambda: [Active(behind)])
        assert rig_.host._active_run_near(hub, layer, above), "a run behind the hub"
        patched.setattr(rig_.host.runs, "active", lambda: [Active(hub + "-other")])
        assert rig_.host._active_run_near(hub, layer, above) == "", "a path that only shares a prefix does not overlap"


# ----- who has it open -----------------------------------------------------------------------------------------------------------
def test_the_holders_are_listed_and_a_real_run_refuses_until_the_person_confirms_again(rig, tree):
    tree.open_file(4242, "tview", "ttyACM0")
    tree.open_file(4243, "realsense-viewer", "ttyACM0")
    rig_ = rig()
    answer = rig_.dry()["action_result"]
    assert [(h["pid"], h["program"], h["node"]) for h in answer["holders"]] == [
        (4242, "tview", str(tree.dev / "ttyACM0")), (4243, "realsense-viewer", str(tree.dev / "ttyACM0"))]
    assert answer["second_confirm"] == "tview (pid 4242), realsense-viewer (pid 4243) will lose fdcanusb 0607623A"
    free = next(p for p in answer["preconditions"] if p["check"] == "device_free")
    assert free["state"] == "fail" and "tview (pid 4242) has" in free["detail"] and "confirm again" in free["detail"]
    listed = rig_.action(rig_.path())
    assert listed["state"] == "ready", "holders are the person's call, not a dead button"
    assert listed["second_confirm"].startswith("tview (pid 4242) has") and rig_.action(rig_.port_path())["second_confirm"]

    run, _ = rig_.reset()
    refusal = next(r for r in run.result["records"] if r["code"] == "ActionRefused")
    assert (run.state, run.exit_code, refusal["args"]["reason"]) == ("failed", 2, "HoldersPresent")
    assert [h["pid"] for h in refusal["args"]["holders"]] == [4242, 4243]
    assert "tview (pid 4242) has" in run.output_bytes().decode() and tree.calls() == [], "refused: nothing ran"

    run, _ = rig_.reset(holders_ok=True)  # the second confirmation
    assert run.state == "done" and len(tree.calls()) == 1
    assert "tview (pid 4242) has" in run.output_bytes().decode() and "it loses the device" in run.output_bytes().decode()


def test_a_holder_that_came_after_the_dry_run_voids_the_first_confirmation(rig, tree):
    rig_ = rig()
    token = rig_.dry()["action_result"]["token"]
    tree.open_file(5000, "tview", "ttyACM0")
    run, _ = rig_.reset(token=token, holders_ok=True)
    refusal = next(r for r in run.result["records"] if r["code"] == "ActionRefused")
    assert refusal["args"]["reason"] == "ConfirmMismatch" and tree.calls() == []


def test_the_hosts_own_watch_and_follow_processes_are_not_holders(rig, tree, monkeypatch):
    from io_diagnostics import runner

    tree.open_file(6000, "moteus_servo.diagnostics", "ttyACM0", session=6000)
    rig_ = rig()
    assert [h["pid"] for h in rig_.dry()["action_result"]["holders"]] == [6000], "a stranger's process is a holder"
    monkeypatch.setattr(runner.RUNNING, "passive_sessions", lambda: [6000])  # it is one of this host's own watch sessions
    assert rig_.dry()["action_result"]["holders"] == []
    assert usb_reset_gate(rig_.host.layer.matchable(f"usb:{PORT}"), "device_free")[0] is True


def test_a_holder_of_anything_the_device_exposes_counts_and_a_hub_counts_what_is_behind_it(tree):
    tree.add_port("9-2", 2, "05e3", "0610", "Hub")
    tree.add_port("9-2.4", 9, "1234", "abcd", "Camera", interfaces={"video4linux": "video0"})
    assert usb_reset.subtree_ports("9-2") == ["9-2", "9-2.4"] and usb_reset.subtree_ports("9-3") == ["9-3"]
    assert str(tree.dev / "video0") in usb_reset.device_nodes("9-2") and str(tree.dev / "video0") not in usb_reset.device_nodes("9-3")
    tree.open_file(7000, "ffplay", "video0")
    found, unseen = usb_reset.holders("9-2")
    assert [(h.pid, h.program) for h in found] == [(7000, "ffplay")] and unseen == 0
    assert usb_reset.holders("9-3")[0] == []


# ----- a reset, and what follows ------------------------------------------------------------------------------------------------------
def test_a_successful_reset_logs_the_run_the_kernels_plug_lines_and_probes_the_device_again(rig, tree):
    rig_ = rig(start=True)
    wait_for(lambda: rig_.probes() == 1, what="the connection's own probe")
    run, _ = rig_.reset()
    assert run.state == "done" and run.exit_code == 0, run.result
    assert tree.calls() == ["009/006"], "usbreset BBB/DDD, once"
    out = run.output_bytes().decode()
    assert "fake usbreset: resetting ['009/006']" in out and "back after" in out and "as device 7" in out
    completed = next(r for r in run.result["records"] if r["code"] == "ActionCompleted")
    assert completed["args"]["devnum_before"] == 6 and completed["args"]["devnum_after"] == 7 and completed["args"]["method"] == "usbreset"
    assert run.result["message"].startswith(f"{NAME} at 9-3 reset with usbreset: back after "), "the run's own result line"
    # the run is in Activity: RunStarted and RunEnded, folded into one Run row
    assert {"RunStarted", "RunEnded"} <= {event["code"] for event in rig_.activity("run")}
    assert [event["code"] for event in rig_.host.activity(50, ("run",), fold=True)["events"]] == ["Run"]
    # the kernel's own unplug and plug follow it (the fake tool wrote them to the log the host reads)
    seen = wait_for(lambda: [event for event in rig_.activity("usb") if event["code"] in ("ReEnumerated", "Plugged", "Unplugged")],
                    timeout=25, what="the unplug / plug in Activity")
    assert any("9-3" in event["message"] for event in seen), [event["message"] for event in seen]
    # and the on-connect probe ran again
    wait_for(lambda: rig_.probes() == 2, what="the second probe")


def test_a_reset_in_place_probes_again_too_though_the_device_number_did_not_change(rig, tree, monkeypatch):
    monkeypatch.setenv("FAKE_RESET_MODE", "inplace")
    rig_ = rig(start=True)
    wait_for(lambda: rig_.probes() == 1, what="the connection's own probe")
    run, _ = rig_.reset()
    out = run.output_bytes().decode()
    assert run.state == "done" and "reset in place" in out and "unchanged" in out
    assert (tree.sysfs / PORT / "devnum").read_text().strip() == "6"
    wait_for(lambda: rig_.probes() == 2, what="the probe after an in-place reset")
    assert any("reset full-speed USB device number 6" in line for line in tree.kernel_lines())
    raw = wait_for(lambda: [row for row in rig_.host.activity(200, None, channel="raw")["events"] if "reset full-speed" in row["message"]],
                   what="the kernel's reset line in the Raw channel")
    assert "9-3" in raw[0]["message"]


def test_a_refusal_changes_nothing_so_nothing_is_probed_again(rig, tree):
    tree.open_file(4242, "tview", "ttyACM0")
    rig_ = rig(start=True)
    wait_for(lambda: rig_.probes() == 1, what="the connection's own probe")
    run, _ = rig_.reset()  # the holder check refuses: nothing happened to the device
    assert run.state == "failed" and run.exit_code == usb_reset.REFUSED
    time.sleep(1.5)
    assert rig_.probes() == 1 and rig_.host.jobs[("rack", f"usb:{PORT}")].probed


def test_a_tool_that_fails_or_a_device_that_does_not_come_back_is_a_failed_run_with_the_reason(rig, tree, monkeypatch):
    monkeypatch.setenv("FAKE_RESET_MODE", "fail")
    rig_ = rig()
    run, _ = rig_.reset()
    assert run.state == "failed" and run.exit_code == 1
    failed = next(r for r in run.result["records"] if r["code"] == "ActionFailed")
    assert "usbreset exited 3" in failed["message"]
    # it never comes back: the port vanishes from sysfs while the tool "runs"
    monkeypatch.setenv("FAKE_RESET_MODE", "replug")
    monkeypatch.setenv("IO_DIAGNOSTICS_RESET_WAIT_S", "0.6")
    vanish = tree.bin / "usbreset"
    vanish.write_text(f"#!{sys.executable}\nimport shutil,sys\nshutil.rmtree('{tree.sysfs / PORT}')\n")
    run, _ = rig_.reset()
    assert run.state == "failed" and "did not come back within 0.6 s" in next(r for r in run.result["records"] if r["code"] == "ActionFailed")["message"]


def test_a_stop_between_the_two_authorized_writes_never_leaves_the_device_off(tree, monkeypatch):
    (tree.sysfs / PORT / "authorized").chmod(0o644)
    target = usb_reset.target_of(ADAPTER)
    writes = []

    def write(path, value):
        writes.append(value)

    monkeypatch.setattr(usb_reset, "_write_attr", write)
    monkeypatch.setattr(usb_reset.time, "sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        usb_reset._cycle_authorized(target)
    assert writes == ["0", "1"]


def test_a_stop_during_the_reset_is_a_record_and_exit_130_not_a_traceback(tree, monkeypatch, capsys):
    monkeypatch.setenv("IO_DIAGNOSTICS_DEVICE", json.dumps(ADAPTER))
    token = usb_reset.plan_json(usb_reset.target_of(ADAPTER), [], 0, usb_reset.methods(usb_reset.target_of(ADAPTER)), False)["token"]
    monkeypatch.setattr(usb_reset, "reset", lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert usb_reset.act(["act", "--device", f"usb:{PORT}", "--action", "reset_port", "--confirm", token]) == 130
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "ActionInterrupted"


# ----- the other gates ------------------------------------------------------------------------------------------------------------------
def test_not_a_person_and_no_confirmation_never_run_it(rig, tree):
    rig_ = rig()
    assert rig_.host.act(rig_.path(), "reset_port", confirm="x", human=False)["refused"] == "NotInteractive"
    assert rig_.host.act(rig_.path(), "reset_port", human=True)["refused"] == "ConfirmRequired"
    assert rig_.host.start_act(rig_.path(), "reset_port", confirm="wrong-token", interactive=True)[0].wait(20) is True
    assert tree.calls() == [], "a token that is not the dry run's token resets nothing"


def test_a_replay_or_a_fake_machine_has_no_real_port_to_reset(tmp_path, tree, monkeypatch, capsys):
    rig_ = Rig(tmp_path / "replay", tree, os_mode="fixture")
    try:
        monkeypatch.delenv("IO_DIAGNOSTICS_USB_SYSFS")  # no fake tree: this would be the real /sys
        listed = rig_.action(rig_.path())
        assert listed["state"] == "blocked" and "replays a recording" in listed["reason"], "the button is dead on a recording"
        dry = rig_.dry()
        assert dry["refused"] is None and dry["action_result"]["methods"][0]["usable"] is False
        token = dry["action_result"]["token"]
        assert rig_.host.start_act(rig_.path(), "reset_port", confirm=token, interactive=True)[1]["refused"] == "NotLive"
        # the verb itself refuses too, whatever the host does
        monkeypatch.setenv("IO_DIAGNOSTICS_OS_MODE", "machine")
        assert usb_reset.act(["act", "--device", f"usb:{PORT}", "--action", "reset_port", "--confirm", token]) == 2
        record = next(json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{"))
        assert record["args"]["reason"] == "NotLive", "refused as a replay, not for some other reason"
        assert tree.calls() == []
    finally:
        rig_.stop()


def test_the_tripwire_refuses_a_real_tool_and_a_real_reset_with_no_fake_tree(monkeypatch):
    from test_host_support import tripwire_refuses

    assert tripwire_refuses(["/usr/bin/usbreset", "001/002"]) and tripwire_refuses(["uhubctl", "-l", "1"])
    cycle = [sys.executable, "-m", "io_diagnostics.usb_reset", "act", "--device", "usb:1-3", "--action", "reset_port"]
    assert tripwire_refuses(cycle, {}) and not tripwire_refuses([*cycle, "--dry-run"], {})
    assert not tripwire_refuses(cycle, {"IO_DIAGNOSTICS_USB_SYSFS": "/tmp/fake"})


def test_the_verb_by_itself_prints_the_plan_as_a_record_and_describe_names_the_action(tree, capsys, monkeypatch):
    monkeypatch.setenv("IO_DIAGNOSTICS_DEVICE", json.dumps(ADAPTER))
    assert usb_reset.main(["act", "--device", f"usb:{PORT}", "--action", "reset_port", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert plan["record"] == "action-result" and plan["method"] == "usbreset" and plan["token"]
    assert usb_reset.main(["describe"]) == 0
    assert json.loads(capsys.readouterr().out)["actions"][0]["name"] == "reset_port"
    assert usb_reset.main(["act", "--device", "usb:nope", "--action", "reset_port", "--dry-run"]) in (0, 2)


def test_the_cli_takes_the_second_confirmation_flag(monkeypatch):
    from io_diagnostics import cli

    args = cli._parser().parse_args(["act", "--path", "host/x", "--action", "reset_port", "--confirm", "T", "--confirm-holders"])
    assert args.confirm_holders is True and args.action == "reset_port"


def test_a_mode_bit_check_is_what_privilege_means(tree):
    assert usb_reset.privilege(usb_reset.target_of(ADAPTER))[0] is True
    tree.node().chmod(0o444)
    allowed, why = usb_reset.privilege(usb_reset.target_of(ADAPTER))
    assert allowed is False and "usbreset: cannot write" in why and stat.S_IMODE(tree.node().stat().st_mode) == 0o444
    assert usb_reset.privilege(usb_reset.target_of(usb_device("9-9", "1", "2", "")))[0] is False


# ----- the page's POST /api/v1/act ---------------------------------------------------------------------------------------------
PAGE = {"Content-Type": "application/json", "X-IO-Diagnostics": "ui", "Sec-Fetch-Site": "same-origin"}


def post(base: str, body: dict) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(base + "api/v1/act", data=json.dumps(body).encode(), method="POST", headers=PAGE)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_page_flow_dry_run_then_tick_then_run_over_http(rig, tree):
    from io_diagnostics.server import make_server, serve_in_thread

    tree.open_file(4242, "tview", "ttyACM0")
    rig_ = rig()
    http = make_server(rig_.host, 0)
    serve_in_thread(http)
    try:
        body = {"path": rig_.path(), "action": "reset_port", "node": None}
        status, dry = post(rig_.host.url, {**body, "dry_run": True})
        answer = dry["result"]["action_result"]
        assert status == 200 and dry["token"] and answer["second_confirm"] == "tview (pid 4242) will lose fdcanusb 0607623A"
        # the run without the box ticked: the run starts, and the verb refuses it
        status, ran = post(rig_.host.url, {**body, "token": dry["token"], "detach": True})
        assert status == 202
        run = rig_.host.runs.get(ran["run"]["id"])
        run.wait(30)
        assert run.state == "failed" and next(r for r in run.result["records"] if r["code"] == "ActionRefused")["args"]["reason"] == "HoldersPresent"
        assert tree.calls() == []
        # a fresh dry run (a token is single use), the box ticked
        status, dry = post(rig_.host.url, {**body, "dry_run": True})
        status, ran = post(rig_.host.url, {**body, "token": dry["token"], "detach": True, "confirm_holders": True})
        run = rig_.host.runs.get(ran["run"]["id"])
        run.wait(30)
        assert status == 202 and run.state == "done" and tree.calls() == ["009/006"]
        assert "--confirm-holders" in run.to_json()["argv"]
    finally:
        http.shutdown()
        http.server_close()


def test_confirm_holders_from_a_request_reaches_only_the_host_s_own_utility(rig, tmp_path, monkeypatch):
    from test_server_act import TOKEN_PLUGIN

    from io_diagnostics.server import make_server, serve_in_thread

    script = tmp_path / "token_plugin.py"
    script.write_text(TOKEN_PLUGIN)
    rig_ = rig(extra_plugins=lambda directory: declare(directory, "tokens", f'["{sys.executable}", "{script}"]', f"usb:{VID}:{PID}",
                                                       f'env = {{ TOKEN_LOG = "{tmp_path / "acts.jsonl"}" }}\n'))
    rig_.host.run_all_once()
    seen = []
    real = rig_.host.start_act
    monkeypatch.setattr(rig_.host, "start_act", lambda *a, **k: (seen.append((a[1], k.get("extra"))), real(*a, **k))[1])
    http = make_server(rig_.host, 0)
    serve_in_thread(http)
    try:
        for action in ("reset_port", "check_comms"):  # the second is a driver's own action, with a token of its own
            status, dry = post(rig_.host.url, {"path": rig_.path(), "action": action, "node": None, "dry_run": True})
            assert dry["token"], (action, dry)
            post(rig_.host.url, {"path": rig_.path(), "action": action, "node": None, "token": dry["token"], "detach": True,
                                 "confirm_holders": True})
        assert seen == [("reset_port", ["--confirm-holders"]), ("check_comms", None)], "no driver action ever gets the flag"
    finally:
        http.shutdown()
        http.server_close()
