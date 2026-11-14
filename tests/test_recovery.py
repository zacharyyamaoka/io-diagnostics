"""Reset… and automatic recovery (docs/device-reset.md; Zach, 2026-11-14: "when you deploy a robot you cannot be there to unplug
stuff manually").

Nothing here touches a real device. The ladder is pointed at a FAKE tree (a sysfs of ports with their hub port switches, a
/dev, a /proc with open files, ppids and locks) and FAKE seams: its tools, sysfs writes, clock and probes are a model of a rig
whose device is cured by one chosen rung. tests/test_host_support.py's tripwire refuses the real fdcanusb-reset / usbreset /
uhubctl, a recovery run with no fake sysfs, and any in-process write or device open outside the temp dir.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_host_support import ROOT, TRIPPED, tripwire_refuses  # noqa: F401  (the import arms the tripwire)

from io_diagnostics.recovery import __main__ as cli
from io_diagnostics.recovery import availability, device as devices, system
from io_diagnostics.recovery.incident import Store, state_dir
from io_diagnostics.recovery.ladder import (HEALTHY, NEEDS_HUMAN, NOT_JUDGED, PERSON, REFUSED, UNATTENDED, Ladder, Request)
from io_diagnostics.recovery.profile import PROBE_TTY
from io_diagnostics.recovery.profiles import ALL, BY_NAME
from io_diagnostics.recovery.rung import KINDS, NATIVE, PAGE, REOPEN
from io_diagnostics.recovery.system import Seams

BUS = 9
FDCANUSB = "usb:0483:5740:0607623A"
ME = os.getpid()


# ----- the fake rig ---------------------------------------------------------------------------------------------------
class Rig:
    """A fake machine: sysfs, /dev, /proc, the hub's port switches, and a device that a chosen rung cures.

    ``cure_by``: the rung kind that makes the device answer again (None: nothing does). The fake tools and sysfs writes
    record every call in ``self.calls`` and bump the device number the way the kernel does on a re-enumeration."""

    def __init__(self, root: Path, monkeypatch, *, cure_by: str | None = "native", power: str = "unknown",
                 node_writable: bool = True, sysfs_writable: bool = True):
        self.root, self.monkeypatch = root, monkeypatch
        self.sysfs, self.dev, self.proc, self.bin = root / "sys", root / "dev", root / "proc", root / "bin"
        for directory in (self.sysfs, self.dev / "bus" / "usb" / f"{BUS:03d}", self.dev / "serial" / "by-id", self.proc, self.bin):
            directory.mkdir(parents=True, exist_ok=True)
        self.cure_by, self.answering, self.calls, self.t = cure_by, False, [], 1000.0
        self.fdcanusb_reset_code = 0
        self.vanish_on: str | None = None
        self.hang_on: str | None = None
        self.recreate_nodes = True
        self.kernel: list[str] = []
        self.add_hub()
        self.add_device("9-3", 6, "0483", "5740", "0607623A", "fdcanusb", "mjbots", nodes={"tty": "ttyACM0"}, driver="cdc_acm",
                        node_writable=node_writable, sysfs_writable=sysfs_writable)
        (self.dev / "serial" / "by-id" / "usb-mjbots_fdcanusb_0607623A-if00").write_text("")
        self.facts = root / "hub-power.txt"
        self.facts.write_text(f"usb{BUS} 1d6b:0002 {power}\n")
        self.process(ME, "pytest", parent=1)
        self.lock = root / "bus.lock"
        self.lock.write_text("")
        (self.proc / "locks").write_text("")
        self.fake_tool("fdcanusb-reset")
        self.fake_tool("usbreset")
        for name, value in {"IO_DIAGNOSTICS_USB_SYSFS": self.sysfs, "IO_DIAGNOSTICS_DEV_ROOT": self.dev,
                            "IO_DIAGNOSTICS_PROC": self.proc, "IO_DIAGNOSTICS_USBRESET": self.bin / "usbreset",
                            "IO_DIAGNOSTICS_UHUBCTL": root / "no-uhubctl", "IO_DIAGNOSTICS_FDCANUSB_RESET": self.bin / "fdcanusb-reset",
                            "IO_DIAGNOSTICS_RACK_BUS_LOCK": self.lock, "IO_DIAGNOSTICS_HUB_POWER_FACTS": self.facts,
                            "IO_DIAGNOSTICS_STATE_DIR": root / "state", "IO_DIAGNOSTICS_CONFIG_DIR": root / "config",
                            "IO_DIAGNOSTICS_KERNEL_LOG_FILE": root / "kernel.log"}.items():
            monkeypatch.setenv(name, str(value))
        monkeypatch.delenv("IO_DIAGNOSTICS_OS_MODE", raising=False)
        monkeypatch.delenv("IO_DIAGNOSTICS_PAGE_COMMAND", raising=False)

    # --- the tree ---
    def add_hub(self) -> None:
        hub = self.sysfs / f"usb{BUS}"
        hub.mkdir(parents=True, exist_ok=True)
        for name, value in (("idVendor", "1d6b"), ("idProduct", "0002"), ("busnum", str(BUS)), ("devnum", "1"),
                            ("bDeviceClass", "09")):
            (hub / name).write_text(value + "\n")
        (self.dev / "bus" / "usb" / f"{BUS:03d}" / "001").write_text("")

    def add_device(self, port, devnum, vid, pid, serial, product, manufacturer="", *, nodes=None, driver="usbfs",
                   node_writable=True, sysfs_writable=True, configured=True) -> None:
        directory = self.sysfs / port
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in (("busnum", str(BUS)), ("devnum", str(devnum)), ("idVendor", vid), ("idProduct", pid),
                            ("serial", serial), ("product", product), ("manufacturer", manufacturer), ("authorized", "1"),
                            ("bConfigurationValue", "1" if configured else ""), ("speed", "12"), ("bDeviceClass", "02")):
            (directory / name).write_text(value + "\n")
        (directory / "authorized").chmod(0o644 if sysfs_writable else 0o444)
        interface = directory / f"{port}:1.0"
        interface.mkdir(exist_ok=True)
        drivers = self.root / "drivers" / driver
        drivers.mkdir(parents=True, exist_ok=True)
        if not (interface / "driver").exists():
            os.symlink(drivers, interface / "driver")
        for kind, node in (nodes or {}).items():
            (interface / kind / node).mkdir(parents=True, exist_ok=True)
            (self.dev / node).write_text("")
        node = self.dev / "bus" / "usb" / f"{BUS:03d}" / f"{devnum:03d}"
        node.write_text("")
        node.chmod(0o664 if node_writable else 0o444)
        # the hub side: <hub>-0:1.0/usb<hub>-port<N>/disable, and a USB 3 peer port with its own switch
        number = port.split("-", 1)[1].split(".")[0]
        here = self.sysfs / f"{BUS}-0:1.0" / f"usb{BUS}-port{number}"
        peer = self.sysfs / f"{BUS + 1}-0:1.0" / f"usb{BUS + 1}-port{number}"
        for switch in (here, peer):
            switch.mkdir(parents=True, exist_ok=True)
            (switch / "disable").write_text("0\n")
            (switch / "disable").chmod(0o644 if sysfs_writable else 0o444)
            (switch / "state").write_text("configured\n")
        if not (here / "peer").exists():
            os.symlink(peer, here / "peer")

    def devnum(self, port="9-3") -> int:
        return int((self.sysfs / port / "devnum").read_text())

    def renumber(self, port="9-3") -> None:
        old = self.devnum(port)
        (self.dev / "bus" / "usb" / f"{BUS:03d}" / f"{old:03d}").rename(self.dev / "bus" / "usb" / f"{BUS:03d}" / f"{old + 1:03d}")
        (self.sysfs / port / "devnum").write_text(f"{old + 1}\n")
        self.kernel_line(f"usb {port}: USB disconnect, device number {old}")
        self.kernel_line(f"usb {port}: new full-speed USB device number {old + 1} using xhci_hcd")
        if self.recreate_nodes:
            # as udev does on a re-enumeration: the tty node is made anew, so a handle still open on the old one reads
            # "/dev/ttyACM0 (deleted)" in /proc/<pid>/fd and is no longer a holder of the device
            for link in self.proc.glob("*/fd/*"):
                if os.readlink(link) == str(self.dev / "ttyACM0"):
                    link.unlink()
                    os.symlink(f"{self.dev / 'ttyACM0'} (deleted)", link)

    def kernel_line(self, text: str) -> None:
        with (self.root / "kernel.log").open("a") as out:
            out.write(f"2026-11-14T16:00:00 rig kernel: {text}\n")

    def vanish(self, port="9-3") -> None:
        import shutil

        shutil.rmtree(self.sysfs / port)

    def process(self, pid: int, program: str, *, parent: int = 1, holds: str = "") -> None:
        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True, exist_ok=True)
        (directory / "cmdline").write_text(f"python3\0/home/bam/bin/{program}\0")
        (directory / "comm").write_text(f"{program}\n")
        (directory / "stat").write_text(f"{pid} ({program}) S {parent} {pid} {pid} 0 0\n")
        if holds:
            os.symlink(self.dev / holds, directory / "fd" / "3")

    def hold_lock(self, pid: int) -> None:
        info = os.stat(self.lock)
        (self.proc / "locks").write_text(
            f"1: FLOCK  ADVISORY  WRITE {pid} {os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino} 0 EOF\n")

    def fake_tool(self, name: str) -> None:
        tool = self.bin / name
        tool.write_text("#!/bin/sh\nexit 0\n")  # only ever called through the fake seams.run, never spawned
        tool.chmod(0o755)

    # --- the seams ---
    def _cure(self, kind: str) -> None:
        if self.vanish_on == kind:
            self.vanish()
            return
        if kind in ("authorized", "power_cycle", "native") and (self.sysfs / "9-3").is_dir():
            self.renumber()
        if self.cure_by == kind:
            self.answering = True

    def run(self, argv, timeout_s, env=None):
        name = Path(argv[0]).name
        self.calls.append(" ".join([name, *[str(word) for word in argv[1:]]]))
        if name == "fdcanusb-reset":
            if self.hang_on == "native":
                return None, ""
            if self.fdcanusb_reset_code == 0:
                self._cure("native")
                return 0, "reset sent to 0607623A; back after 1.6 s"
            return self.fdcanusb_reset_code, {2: "no permission on /dev/bus/usb/009/006", 3: "need exactly one fdcanusb",
                                              4: "did not come back within 10 s: needs a power cycle"}[self.fdcanusb_reset_code]
        if name == "usbreset":
            self._cure("usbreset")
            return 0, "Resetting fdcanusb ... ok"
        if name == "page":
            return 0, "paged"
        return 127, f"unknown fake tool {name}"

    def write_attr(self, path: Path, value: str) -> None:
        self.calls.append(f"write {path.relative_to(self.sysfs)} {value}")
        if self.hang_on and path.name == ("authorized" if self.hang_on == "authorized" else "disable") and value in ("0", "1") \
                and not getattr(self, "_hung", False):
            self._hung = True
            time.sleep(1.0)  # longer than the test's rung timeout: the kernel "still pending"
        system.write_attr(path, value)  # a real write, into the fake tree (the tripwire allows the temp dir)
        if path.name == "authorized" and value == "1":
            self._cure("authorized")
        if path.name == "disable" and value == "0" and path.parent.name == f"usb{BUS}-port3":
            self._cure("power_cycle")

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def now(self) -> float:
        return self.t

    def tty_ask(self, node, timeout_s):
        self.calls.append(f"ask {Path(node).name}")
        return (True, f"{node} answered: lec=0 dlec=0 OK") if self.answering else (False, f"{node} did not answer within 1 s")

    def seams(self, **override) -> Seams:
        fakes = dict(write_attr=self.write_attr, run=self.run, sleep=self.sleep, now=self.now, monotonic=self.now,
                     kernel_lines=lambda since, port: system.kernel_lines(since, port), tty_ask=self.tty_ask,
                     querycap=lambda node, timeout: (self.answering, "querycap"), lsusb_power=lambda bus, dev: "unknown")
        return Seams(**{**fakes, **override})

    def resets(self) -> list[str]:
        return [call for call in self.calls if not call.startswith("ask ")]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path / "rig", monkeypatch)


def owner_run(rig: Rig, **options) -> object:
    request = Request(key=options.pop("key", FDCANUSB), mode=options.pop("mode", UNATTENDED), owner_pid=options.pop("owner_pid", ME),
                      detected=options.pop("detected", "no reply to 5 exchanges"), **options)
    lines = []
    result = Ladder(request, rig.seams(), say=lines.append).run()
    result.lines = lines
    return result


def ledger_lines(rig: Rig) -> list[dict]:
    path = state_dir() / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ----- the profiles ---------------------------------------------------------------------------------------------------
def test_every_profile_follows_the_ladder_order_and_ends_in_a_page():
    for profile in ALL:
        kinds = [rung.kind for rung in profile.rungs]
        assert kinds == sorted(kinds, key=KINDS.index), profile.name
        assert kinds[-1] == PAGE and kinds[0] == REOPEN, profile.name
        for rung in profile.rungs:
            assert rung.why.strip() and rung.expect.strip() and rung.timeout_s > 0, (profile.name, rung.kind)


def test_each_device_class_has_its_own_reset_first():
    assert BY_NAME["fdcanusb"].rung(NATIVE).command[0] == "{fdcanusb_reset}"  # Sim to Real's helper, reused, not copied
    assert BY_NAME["fdcanusb"].rung(NATIVE).same_as == "usbreset"
    assert (BY_NAME["realsense"].rung(NATIVE).plugin, BY_NAME["realsense"].rung(NATIVE).action) == ("intel_rgbd", "reboot")
    assert BY_NAME["obsbot_ptz"].rung(NATIVE) is None  # the camera exposes none: reopen, then the USB rungs
    board = BY_NAME["moteus_board"].rung(NATIVE)
    assert (board.plugin, board.action, board.unattended, board.danger) == ("moteus_servo", "reboot", False, "moves")
    assert "d reset" in board.title and "homed map" in board.attended_why
    assert [rung.kind for rung in BY_NAME["moteus_board"].rungs] == ["reopen", "native", "page"]  # not a USB device


def test_the_committed_udev_rules_are_the_generated_ones_and_never_cover_every_device():
    committed = (ROOT / "tools" / "udev" / "70-robotdx-device-reset.rules").read_text()
    assert committed == cli.udev_rules(), "run: python -m io_diagnostics.recovery udev-rules > tools/udev/70-robotdx-device-reset.rules"
    for line in committed.splitlines():
        if line.startswith("ACTION"):
            assert "ATTR{idVendor}" in line or "ATTR{product}" in line, line  # one class per line, never every USB device
    assert 'ATTR{idVendor}=="8086"' in committed and 'ATTR{idVendor}=="8086", ATTR{idProduct}=="0b*", GROUP' not in committed


def test_the_committed_udev_rules_parse(tmp_path):
    udevadm = Path("/usr/bin/udevadm")
    if not udevadm.exists():
        pytest.skip("no udevadm here")
    done = subprocess.run([str(udevadm), "verify", str(ROOT / "tools" / "udev" / "70-robotdx-device-reset.rules")],
                          capture_output=True, text=True)
    if "Unknown command verb" in done.stderr:
        pytest.skip("udevadm verify needs systemd 254+")
    assert done.returncode == 0, done.stdout + done.stderr


# ----- which rungs can run here, and why not ------------------------------------------------------------------------------
def test_check_names_each_rung_that_cannot_run_and_what_would_allow_it(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "rig", monkeypatch, node_writable=False, sysfs_writable=False, power="unknown")
    found = devices.resolve(FDCANUSB)
    profile = BY_NAME["fdcanusb"]
    seams = rig.seams()
    usbreset = availability.rung_availability(profile.rung("usbreset"), found, profile, seams)
    assert not usbreset.usable and "cannot write" in usbreset.why and usbreset.needs == "the one-time installer"
    authorized = availability.rung_availability(profile.rung("authorized"), found, profile, seams)
    assert not authorized.usable and "root only" in authorized.why and authorized.needs == "the one-time installer"
    power = availability.rung_availability(profile.rung("power_cycle"), found, profile, seams)
    assert not power.usable and "unknown whether hub usb9" in power.why
    native = availability.rung_availability(profile.rung("native"), found, profile, seams)
    assert not native.usable and "cannot write" in native.why  # fdcanusb-reset needs the same node


@pytest.mark.parametrize("mode, usable, words", [("individual", True, "switches port power per port"),
                                                 ("ganged", False, "only a logical disconnect"),
                                                 ("none", False, "only a logical disconnect"),
                                                 ("unknown", False, "unknown whether")])
def test_a_port_power_cycle_runs_only_on_a_hub_with_per_port_power_switching(tmp_path, monkeypatch, mode, usable, words):
    rig = Rig(tmp_path / "rig", monkeypatch, power=mode)
    profile = BY_NAME["fdcanusb"]
    here = availability.rung_availability(profile.rung("power_cycle"), devices.resolve(FDCANUSB), profile, rig.seams())
    assert here.usable is usable and words in here.why


def test_a_device_with_no_rule_of_its_own_is_not_told_the_installer_covers_it(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "rig", monkeypatch, node_writable=False)
    rig.add_device("9-4", 7, "046d", "c092", "MOUSE1", "Gaming Mouse", node_writable=False)
    found = devices.resolve("usb:046d:c092:MOUSE1")
    profile = BY_NAME["usb_device"]
    here = availability.rung_availability(profile.rung("usbreset"), found, profile, rig.seams())
    assert not here.usable and "of its own" in here.needs


# ----- the unattended ladder ----------------------------------------------------------------------------------------------
def test_the_owner_s_wedged_adapter_is_cured_by_its_own_reset_and_every_step_is_evidenced(rig):
    before = rig.devnum()
    result = owner_run(rig, tried_by_owner=("reopen",))
    assert (result.code, result.outcome) == (HEALTHY, "cured"), result.lines
    assert rig.resets() == ["fdcanusb-reset --serial 0607623A --yes --wait-s 10"]
    rung = next(line for line in ledger_lines(rig) if line.get("rung") == "native")
    assert rung["outcome"] == "cured" and rung["probe"]["ok"] is True and "answered" in rung["probe"]["words"]
    assert rung["before"]["devnum"] == str(before) and rung["after"]["devnum"] == str(before + 1)
    assert any("USB disconnect" in line for line in rung["after"]["kernel"])  # the kernel's own lines about the port
    assert rung["command"][0].endswith("fdcanusb-reset") and rung["expect"]
    skipped = next(line for line in ledger_lines(rig) if line.get("rung") == "reopen")
    assert skipped["reason"] == "DoneByOwner"


def test_an_owner_held_tty_is_not_asked_so_the_owner_judges_and_nothing_heavier_follows(rig):
    rig.process(5001, "arm_hold_reader", parent=ME, holds="ttyACM0")  # the owner's helper has the port open: not someone else
    rig.cure_by, rig.recreate_nodes = "native", False  # and still has it open after the reset (it reopened at once)
    result = owner_run(rig, tried_by_owner=("reopen",))
    # the probe may not write into the owner's open session: back on USB, the owner reopens and judges
    assert (result.code, result.outcome) == (NOT_JUDGED, "not_judged")
    assert not [call for call in rig.calls if call.startswith("ask ")]
    assert [call.split()[0] for call in rig.resets()] == ["fdcanusb-reset"]  # no second reset stacked on it
    rung = next(line for line in ledger_lines(rig) if line.get("rung") == "native")
    assert rung["outcome"] == "back_not_judged" and rung["before"]["holders"][0]["program"] == "arm_hold_reader"


def test_the_ladder_escalates_rung_by_rung_and_skips_a_reset_it_already_did(rig):
    rig.cure_by = "authorized"
    result = owner_run(rig, tried_by_owner=("reopen",))
    assert result.outcome == "cured"
    assert [call.split()[0] for call in rig.resets()] == ["fdcanusb-reset", "write", "write"]
    outcomes = [(line.get("rung"), line["outcome"], line.get("reason", "")) for line in ledger_lines(rig)]
    assert ("native", "not_cured", "") in outcomes
    assert ("usbreset", "skipped", "SameReset") in outcomes  # fdcanusb-reset IS a USBDEVFS_RESET
    assert outcomes[-1] == ("authorized", "cured", "")


def test_a_power_cycle_switches_the_port_and_its_usb3_peer_and_reaches_a_device_that_vanished(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "rig", monkeypatch, power="individual", cure_by="power_cycle")
    rig.vanish_on = "authorized"  # deauthorize and it never comes back: only its port is left
    result = owner_run(rig, tried_by_owner=("reopen", "native"))
    writes = [call for call in rig.calls if call.startswith("write") and "disable" in call]
    assert writes[:2] == ["write 9-0:1.0/usb9-port3/disable 1", "write 9-0:1.0/usb9-port3/peer/disable 1"]
    assert writes[2:] == ["write 9-0:1.0/usb9-port3/disable 0", "write 9-0:1.0/usb9-port3/peer/disable 0"]
    assert result.outcome in ("paged", "cured")  # the fake does not bring a vanished device back; the switches are the point
    assert (rig.sysfs / "9-0:1.0" / "usb9-port3" / "disable").read_text().strip() == "0"


def test_when_every_rung_fails_a_human_is_paged_once_and_no_rung_runs_twice(rig):
    rig.cure_by = None
    first = owner_run(rig, tried_by_owner=("reopen",), page_command=["/bin/page", "{device}", "{text}"])
    assert (first.code, first.outcome) == (NEEDS_HUMAN, "paged")
    assert "Replug fdcanusb 0607623A" in first.message
    alarms = (state_dir() / "alarms.jsonl").read_text().splitlines()
    assert len(alarms) == 1 and json.loads(alarms[0])["schema"] == "recovery-alarm/1"
    assert rig.calls[-1].startswith(f"page {FDCANUSB} Replug fdcanusb")
    count = len(rig.resets())
    again = owner_run(rig, tried_by_owner=("reopen",), page_command=["/bin/page", "{device}", "{text}"])
    assert (again.code, again.outcome) == (NEEDS_HUMAN, "already_paged")
    assert len(rig.resets()) == count  # once per incident: nothing re-ran, nobody was paged again
    assert len((state_dir() / "alarms.jsonl").read_text().splitlines()) == 1


def test_an_incident_closes_only_after_the_quiet_window_and_a_failure_inside_it_continues_it(rig):
    rig.cure_by = "native"
    assert owner_run(rig, tried_by_owner=("reopen",)).outcome == "cured"
    rig.answering, rig.cure_by = False, "authorized"
    rig.t += 60  # fails again inside the quiet window: the same incident, the native reset stays used
    result = owner_run(rig, tried_by_owner=("reopen",))
    assert result.outcome == "cured" and [c.split()[0] for c in rig.resets()].count("fdcanusb-reset") == 1
    rig.t += 10
    healthy = Ladder(Request(key=FDCANUSB, mode=UNATTENDED), rig.seams(), say=lambda _: None).run()
    assert healthy.outcome == "healthy"
    rig.t += BY_NAME["fdcanusb"].quiet_s + 1
    Ladder(Request(key=FDCANUSB, mode=UNATTENDED), rig.seams(), say=lambda _: None).run()
    assert Store(devices.resolve(FDCANUSB).state_key, rig.now).current() is None  # closed
    rig.answering, rig.cure_by = False, "native"
    assert owner_run(rig, tried_by_owner=("reopen",)).outcome == "cured"  # a fresh incident: its own reset is allowed again
    assert [c.split()[0] for c in rig.resets()].count("fdcanusb-reset") == 2


def test_a_device_that_keeps_dying_is_paged_not_reset_again(rig):
    for _ in range(BY_NAME["fdcanusb"].max_incidents_per_day):
        rig.answering, rig.cure_by = False, "native"
        assert owner_run(rig, tried_by_owner=("reopen",)).outcome == "cured"
        rig.t += BY_NAME["fdcanusb"].quiet_s + 1
        Ladder(Request(key=FDCANUSB, mode=UNATTENDED), rig.seams(), say=lambda _: None).run()
        rig.t += BY_NAME["fdcanusb"].quiet_s + 1
        Ladder(Request(key=FDCANUSB, mode=UNATTENDED), rig.seams(), say=lambda _: None).run()
    rig.answering = False
    count = len(rig.resets())
    result = owner_run(rig, tried_by_owner=("reopen",))
    assert result.outcome == "paged" and "needs a look" in result.attempts[-1]["because"]
    assert len(rig.resets()) == count


def test_a_rung_marked_tried_before_it_runs_is_not_repeated_after_a_crash(rig):
    rig.cure_by = None

    def crash(argv, timeout_s, env=None):
        raise KeyboardInterrupt  # the ladder is killed in the middle of its own reset

    with pytest.raises(KeyboardInterrupt):
        Ladder(Request(key=FDCANUSB, owner_pid=ME, detected="dead", tried_by_owner=("reopen",)), rig.seams(run=crash),
               say=lambda _: None).run()
    incident = Store(devices.resolve(FDCANUSB).state_key, rig.now).current()
    assert "native" in incident.tried


def test_a_kernel_write_still_pending_stops_the_ladder_and_pages(rig, monkeypatch):
    rig.cure_by, rig.hang_on = None, "authorized"
    import dataclasses
    from io_diagnostics.recovery.profiles import fdcanusb

    quick = dataclasses.replace(fdcanusb.PROFILE, rungs=tuple(dataclasses.replace(r, timeout_s=0.2) if r.kind == "authorized" else r
                                                              for r in fdcanusb.PROFILE.rungs))
    monkeypatch.setitem(BY_NAME, "fdcanusb", quick)
    monkeypatch.setattr("io_diagnostics.recovery.ladder.profile_for", lambda keys: quick)
    result = owner_run(rig, tried_by_owner=("reopen", "native"))
    assert result.outcome == "paged" and "still pending in the kernel" in result.attempts[-1]["because"]
    assert not [call for call in rig.calls if "disable" in call]  # nothing stacked on it
    time.sleep(1.2)  # let the hung fake write finish so the finally restores authorized
    assert (rig.sysfs / "9-3" / "authorized").read_text().strip() == "1"


def test_the_authorized_switch_is_put_back_even_when_the_wait_between_fails(rig):
    from io_diagnostics.recovery import mechanisms

    def broken_sleep(_seconds):
        raise RuntimeError("stopped between the two writes")

    outcome = mechanisms.run_rung(BY_NAME["fdcanusb"].rung("authorized"), devices.resolve(FDCANUSB), rig.seams(sleep=broken_sleep))
    assert outcome.state == "failed"
    assert (rig.sysfs / "9-3" / "authorized").read_text().strip() == "1"


def test_a_port_switched_off_is_switched_back_on_with_its_peer_even_when_the_wait_fails(tmp_path, monkeypatch):
    from io_diagnostics.recovery import mechanisms

    rig = Rig(tmp_path / "rig", monkeypatch, power="individual")

    def broken_sleep(_seconds):
        raise RuntimeError("stopped while the port was off")

    outcome = mechanisms.run_rung(BY_NAME["fdcanusb"].rung("power_cycle"), devices.resolve(FDCANUSB), rig.seams(sleep=broken_sleep))
    assert outcome.state == "failed"
    for switch in ("9-0:1.0/usb9-port3/disable", "10-0:1.0/usb10-port3/disable"):
        assert (rig.sysfs / switch).read_text().strip() == "0", switch


# ----- the refusals: nothing is reset ---------------------------------------------------------------------------------------
def _refused(result, reason: str, rig: Rig) -> None:
    assert result.code == REFUSED, (result.outcome, result.message)
    assert result.records[-1]["args"]["reason"] == reason, result.records[-1]
    assert rig.resets() == [], rig.calls


def test_refused_when_unattended_recovery_is_switched_off(rig):
    (rig.root / "config").mkdir(parents=True, exist_ok=True)
    (rig.root / "config" / "recovery.off").write_text("")
    _refused(owner_run(rig), "RecoveryOff", rig)


def test_refused_while_a_person_holds_the_device(rig):
    Store(devices.resolve(FDCANUSB).state_key, rig.now).set_hold(rig.t + 600, "zach", "flashing firmware")
    _refused(owner_run(rig), "HoldDeclared", rig)


def test_refused_when_the_declared_owner_is_not_the_caller(rig):
    rig.process(4343, "someone_else", parent=1)
    _refused(owner_run(rig, owner_pid=4343), "OwnerNotCaller", rig)


def test_refused_when_the_declared_owner_is_gone(rig):
    _refused(owner_run(rig, owner_pid=4999), "OwnerGone", rig)


def test_refused_while_someone_other_than_the_owner_has_it_open(rig):
    rig.process(4242, "tview", parent=1, holds="ttyACM0")
    result = owner_run(rig)
    _refused(result, "HeldByOther", rig)
    assert "tview (pid 4242)" in result.message


def test_a_holder_that_blocks_every_rung_for_two_minutes_is_paged(rig):
    rig.process(4242, "tview", parent=1, holds="ttyACM0")
    Store(devices.resolve(FDCANUSB).state_key, rig.now).open("dead", "9-3")
    owner_run(rig)
    rig.t += 121
    result = owner_run(rig)
    assert result.outcome == "paged" and "tview" in result.attempts[-1]["because"] and rig.resets() == []


def test_refused_while_another_process_holds_the_device_s_owner_lock(rig):
    rig.process(5555, "experiment", parent=1)
    rig.hold_lock(5555)
    result = owner_run(rig)
    _refused(result, "OwnerLockHeld", rig)
    assert "experiment (pid 5555)" in result.message


def test_the_owner_s_own_owner_lock_does_not_block_it(rig):
    rig.hold_lock(ME)
    assert owner_run(rig, tried_by_owner=("reopen",)).outcome == "cured"


def test_refused_while_another_ladder_runs_on_the_device(rig):
    store = Store(devices.resolve(FDCANUSB).state_key, rig.now)
    with store.ladder_lock() as mine:
        assert mine
        _refused(owner_run(rig), "LadderRunning", rig)


def test_refused_on_a_host_that_replays_a_recording(rig, monkeypatch):
    monkeypatch.setenv("IO_DIAGNOSTICS_OS_MODE", "fixture")
    monkeypatch.setattr("io_diagnostics.usb_reset.sysfs_overridden", lambda: False)
    _refused(owner_run(rig), "NotLive", rig)


def test_unattended_refuses_when_it_cannot_tell_and_the_owner_said_nothing(rig):
    rig.process(5001, "arm_hold", parent=ME, holds="ttyACM0")
    _refused(owner_run(rig, detected=""), "CannotTell", rig)


def test_a_supervisor_with_no_owner_asks_the_device_itself_and_leaves_a_healthy_one_alone(rig):
    rig.answering = True
    result = Ladder(Request(key=FDCANUSB, mode=UNATTENDED), rig.seams(), say=lambda _: None).run()
    assert result.outcome == "healthy" and rig.resets() == [] and "ask ttyACM0" in rig.calls


def test_a_board_reboot_never_runs_unattended(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "rig", monkeypatch)
    plugins = fake_plugins(tmp_path, answering=[])
    request = Request(key="can:0607623A/12", mode=UNATTENDED, plugins_dir=plugins, detected="CAN 12 silent")
    result = Ladder(request, rig.seams(run=system.run), say=lambda _: None).run()
    assert result.outcome == "paged"
    skipped = next(line for line in ledger_lines(rig) if line.get("rung") == "native")
    assert skipped["reason"] == "PersonOnly" and "homed map" in skipped["words"]
    log = (tmp_path / "plugin.log").read_text() if (tmp_path / "plugin.log").exists() else ""
    assert "reboot" not in log


# ----- the person's Reset… --------------------------------------------------------------------------------------------------
def person(rig: Rig, **options):
    request = Request(key=options.pop("key", FDCANUSB), mode=PERSON, **options)
    return Ladder(request, rig.seams(), say=lambda _: None).run()


def test_a_person_s_dry_run_lists_every_rung_with_why_and_expect_and_resets_nothing(rig):
    plan = person(rig, dry_run=True).plan
    assert [step["kind"] for step in plan["steps"]] == ["reopen", "native", "usbreset", "authorized", "power_cycle", "page"]
    assert plan["runs"] == ["native", "usbreset", "authorized"]  # reopen is the person's; the hub's power switching is unknown
    assert all(step["why"] and step["expect"] for step in plan["steps"])
    assert plan["fix"] and "install_device_reset_access.sh" in plan["fix"]
    assert rig.resets() == []


def test_a_person_needs_the_dry_run_s_token(rig):
    _refused(person(rig, confirm="not-the-token"), "ConfirmMismatch", rig)


def test_a_person_confirms_again_when_something_has_it_open(rig):
    rig.process(4242, "tview", parent=1, holds="ttyACM0")
    token = person(rig, dry_run=True).plan["token"]
    _refused(person(rig, confirm=token), "HoldersPresent", rig)
    rig.cure_by = "native"
    assert person(rig, confirm=token, confirm_holders=True).outcome == "cured"


def test_a_holder_that_came_after_the_dry_run_voids_the_token(rig):
    token = person(rig, dry_run=True).plan["token"]
    rig.process(4242, "tview", parent=1, holds="ttyACM0")
    _refused(person(rig, confirm=token, confirm_holders=True), "ConfirmMismatch", rig)


def test_a_person_s_reset_runs_the_rungs_in_order_until_it_answers(rig):
    rig.cure_by = "usbreset"
    rig.fdcanusb_reset_code = 4  # its own reset did not bring it back
    token = person(rig, dry_run=True).plan["token"]
    result = person(rig, confirm=token)
    assert result.outcome == "cured"
    assert [call.split()[0] for call in rig.resets()] == ["fdcanusb-reset", "usbreset"]  # a person re-runs a reset (no SameReset)


# ----- a driver's own reset, through its plugin -----------------------------------------------------------------------------
FAKE_PLUGIN = r'''
import json, os, sys
log = os.environ["FAKE_PLUGIN_LOG"]
args = sys.argv[1:]
with open(log, "a") as out:
    out.write(" ".join(args) + "\n")
def emit(**record):
    print(json.dumps(record), flush=True)
if args[0] == "describe":
    emit(name="fake_driver", schema="diagnostics-record/0", matches=["usb:*"], actions=[])
    sys.exit(0)
action = args[args.index("--action") + 1]
if action == "check_comms":
    emit(record="event", code="Probed", severity="info", message="probed", args={"answering": json.loads(os.environ.get("FAKE_ANSWERING", "[]"))})
    sys.exit(0)
if "--dry-run" in args:
    if os.environ.get("FAKE_BUSY"):
        emit(record="event", code="ActionRefused", severity="warn", message="realsense-viewer (pid 77) has the camera", args={"reason": "BusyHeldBy"})
        sys.exit(3)
    emit(record="action-result", action=action, dry_run=True, confirm_token="SERIAL123", message="would reboot")
    sys.exit(0)
if "--confirm" in args and args[args.index("--confirm") + 1] == "SERIAL123":
    emit(record="event", code="ActionCompleted", severity="info", message="hardware_reset done; back after 3.1 s", args={})
    sys.exit(0)
emit(record="event", code="ActionRefused", severity="warn", message="wrong token", args={"reason": "ConfirmRequired"})
sys.exit(3)
'''


def fake_plugins(tmp_path: Path, *, answering: list[int], busy: bool = False, names=("intel_rgbd", "moteus_servo")) -> Path:
    directory = tmp_path / "plugins"
    directory.mkdir(exist_ok=True)
    script = tmp_path / "fake_plugin.py"
    script.write_text(FAKE_PLUGIN)
    env = {"FAKE_PLUGIN_LOG": str(tmp_path / "plugin.log"), "FAKE_ANSWERING": json.dumps(answering)}
    if busy:
        env["FAKE_BUSY"] = "1"
    for name in names:
        (directory / f"{name}.toml").write_text(
            f'name = "{name}"\ncommand = ["{sys.executable}", "{script}"]\nmatches = ["usb:*"]\nenabled = true\n'
            f"[env]\n" + "".join(f'{key} = "{value}"\n' for key, value in env.items()))
    return directory


def test_a_camera_s_own_hardware_reset_runs_through_its_driver_with_the_driver_s_token(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "rig", monkeypatch)
    rig.add_device("9-5", 8, "8086", "0b07", "CAM1", "Intel RealSense D435", nodes={"video4linux": "video0"}, driver="uvcvideo")
    plugins = fake_plugins(tmp_path, answering=[])
    request = Request(key="usb:8086:0b07:CAM1", owner_pid=ME, detected="frames stale 3 s", tried_by_owner=("reopen",),
                      plugins_dir=plugins, step=True)
    result = Ladder(request, rig.seams(run=system.run), say=lambda _: None).run()
    calls = (tmp_path / "plugin.log").read_text().splitlines()
    assert "--dry-run" in calls[0] and "--action reboot" in calls[0]
    assert "--confirm SERIAL123" in calls[1]
    assert result.outcome in ("cured", "not_judged")
    native = next(line for line in ledger_lines(rig) if line.get("rung") == "native")
    assert "hardware_reset done" in native["words"]


def test_a_driver_that_refuses_its_own_reset_moves_the_ladder_on(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "rig", monkeypatch)
    rig.add_device("9-5", 8, "8086", "0b07", "CAM1", "Intel RealSense D435", nodes={"video4linux": "video0"}, driver="uvcvideo")
    plugins = fake_plugins(tmp_path, answering=[], busy=True)
    rig.cure_by = "usbreset"
    request = Request(key="usb:8086:0b07:CAM1", owner_pid=ME, detected="frames stale", tried_by_owner=("reopen",),
                      plugins_dir=plugins)

    def both(argv, timeout_s, env=None):
        return system.run(argv, timeout_s, env) if "fake_plugin.py" in " ".join(map(str, argv)) else rig.run(argv, timeout_s, env)

    result = Ladder(request, rig.seams(run=both), say=lambda _: None).run()
    native = next(line for line in ledger_lines(rig) if line.get("rung") == "native")
    assert native["outcome"] == "refused" and "BusyHeldBy" in json.dumps(native)
    assert result.outcome in ("cured", "paged")
    assert any(call.startswith("usbreset") for call in rig.calls)


# ----- the probes, for real (a pty, not a device) ------------------------------------------------------------------------
def _fake_console(master: int, reply: bytes | None, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            data = os.read(master, 1024)
        except OSError:
            return
        if b"can status" in data and reply is not None:
            os.write(master, b"rcv 8001 00\r\n" + reply)


@pytest.mark.parametrize("reply, expected", [(b"lec=0 dlec=0 err=0 warn=0 busoff=0\r\nOK\r\n", True), (None, False)])
def test_tty_ask_gets_a_reply_from_an_answering_console_and_times_out_on_a_wedged_one(reply, expected):
    master, slave = pty.openpty()
    stop = threading.Event()
    worker = threading.Thread(target=_fake_console, args=(master, reply, stop), daemon=True)
    worker.start()
    try:
        started = time.monotonic()
        answered, words = system.tty_ask(os.ttyname(slave), 0.5)
        assert answered is expected, words
        assert time.monotonic() - started < 2.0  # a wedged adapter costs the timeout, never a hang
        if expected:
            assert "lec=0" in words  # the rcv line is not mistaken for the answer
    finally:
        stop.set()
        os.close(slave)
        os.close(master)


def test_tty_ask_never_writes_into_a_console_someone_else_has_locked():
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    holder = os.open(path, os.O_RDWR | os.O_NOCTTY)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        answered, words = system.tty_ask(path, 0.3)
        assert answered is None and "locked" in words
    finally:
        os.close(holder)
        os.close(slave)
        os.close(master)


def test_the_in_process_tripwire_refuses_a_real_sysfs_write():
    with pytest.raises(system.TripwireError):
        system.write_attr(Path("/sys/bus/usb/devices/usb1/authorized_default"), "1")


def test_the_subprocess_tripwire_refuses_the_real_tools_and_a_real_ladder(monkeypatch):
    monkeypatch.delenv("IO_DIAGNOSTICS_USB_SYSFS", raising=False)
    assert tripwire_refuses(["/home/bam/.local/bin/fdcanusb-reset", "--serial", "0607623A", "--yes"])
    assert tripwire_refuses([sys.executable, "-m", "io_diagnostics.recovery", "recover", "--device", FDCANUSB])
    assert tripwire_refuses([sys.executable, "-m", "io_diagnostics.recovery", "act", "--device", "usb:7-1", "--action", "reset"])
    assert not tripwire_refuses([sys.executable, "-m", "io_diagnostics.recovery", "act", "--device", "usb:7-1", "--action", "reset",
                                 "--dry-run"])


# ----- the CLI end to end, in a subprocess, on the fake tree --------------------------------------------------------------
def test_the_cli_plans_checks_and_resets_a_device_on_the_fake_tree(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "rig", monkeypatch)
    # a plain USB device that is on USB but not configured: the usb_device ladder's present probe fails until a reset
    rig.add_device("9-6", 9, "1234", "5678", "GRIP1", "Gripper", configured=False)
    tool = rig.bin / "usbreset"
    tool.write_text(f"#!{sys.executable}\nimport sys\nopen('{rig.sysfs}/9-6/bConfigurationValue', 'w').write('1\\n')\n"
                    f"print('fake usbreset', sys.argv[1:])\n")
    tool.chmod(0o755)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    run = lambda *args: subprocess.run([sys.executable, "-m", "io_diagnostics.recovery", *args], capture_output=True, text=True,
                                       env=env, cwd=ROOT, timeout=60)
    check = json.loads(run("check", "--json").stdout)
    gripper = next(row for row in check["devices"] if row["device"] == "usb:1234:5678:GRIP1")
    assert [rung["usable"] for rung in gripper["rungs"]] == [True, True, True, False, True]
    dry = run("act", "--device", "usb:9-6", "--action", "reset", "--dry-run")
    plan = json.loads(dry.stdout.strip().splitlines()[-1])
    assert plan["runs"] == ["usbreset", "authorized"] and plan["token"]
    done = run("act", "--device", "usb:9-6", "--action", "reset", "--confirm", plan["token"])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "answers again after usb port reset" in done.stdout
    assert (rig.sysfs / "9-6" / "bConfigurationValue").read_text().strip() == "1"
    assert '"outcome": "cured"' in done.stdout


# ----- the one-time installer ---------------------------------------------------------------------------------------------
def test_the_installer_installs_the_committed_rules_and_writes_the_facts_the_ladder_reads():
    script = (ROOT / "tools" / "install_device_reset_access.sh").read_text()
    assert 'rules_src="$here/udev/70-robotdx-device-reset.rules"' in script
    assert f"facts=\"$facts_dir/{Path(system.DEFAULT_HUB_FACTS).name}\"" in script
    assert f"facts_dir={Path(system.DEFAULT_HUB_FACTS).parent}" in script
    assert "udevadm verify" in script and "sudo -u \"$user\"" in script  # verified before install; the check runs as the user
    assert "apt" not in script and "python3 -m io_diagnostics.recovery check" in script  # no package, no repo code as root


def test_the_installer_refuses_without_root_and_touches_nothing():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    done = subprocess.run(["bash", str(ROOT / "tools" / "install_device_reset_access.sh")], capture_output=True, text=True)
    assert done.returncode == 2 and "sudo" in done.stderr
