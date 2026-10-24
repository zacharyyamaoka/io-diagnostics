"""The complete port-activity log and the live Computer (Zach, 2026-10-23: "I would really like the computer activity
log to be a complete view of all the port activity"; "something that feels a bit like system monitor").

- every kernel notice kind, typed events with stable ids, USB errors, driver binds, PCIe AER, level changes;
- a provisional plug from udev within a second, confirmed IN PLACE by the kernel log (same id, never twice), seen
  through a fake ``udevadm monitor`` stream and a growing fake kernel log, and pushed to an ``/api/v1/events`` client;
- the plugin boundary's ``follow`` verb: one long-running process per device, restarted with a backoff, bounded;
- the computer plugin's ``follow`` (fake readings).

Every stream is a fake (a stdlib subprocess or a list); nothing here opens a device or reads the real journal.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_computer import FakeProbe, computer
from test_host_support import ROOT, FakeOs, SetClock, host_dict, standard_skeleton, usb_bus, usb_device

from contract.record import ERROR, OK, DiagnosticStatus
from io_diagnostics import activity
from io_diagnostics.history import History
from io_diagnostics.host import Clock, Host, make_host
from io_diagnostics.runner import Follower, follow_period
from io_diagnostics.server import make_server, serve_in_thread
from io_facts import journal, kernel_log, udev

PDT = -7 * 3600
BOOT = dt.datetime(2026, 10, 23, 7, 0, tzinfo=dt.timezone(dt.timedelta(seconds=PDT))).timestamp()


def kernel(*entries: str, who: str = "usb") -> list[str]:
    """``"14:17:36 1-1: new high-speed ..."`` -> journalctl short-iso lines of 2026-10-23 (PDT)."""

    out = []
    for entry in entries:
        clock, rest = entry.split(" ", 1)
        prefix = "" if rest.startswith("@") else f"{who} "
        out.append(f"2026-10-23T{clock}-07:00 rig kernel: {prefix}{rest.removeprefix('@')}")
    return out


def plug(clock: str, port: str, devnum: int, vid: str, pid: str, product: str, serial: str = "", speed: str = "full-speed") -> list[str]:
    lines = [f"{clock} {port}: new {speed} USB device number {devnum} using xhci_hcd",
             f"{clock} {port}: New USB device found, idVendor={vid}, idProduct={pid}, bcdDevice= 1.00",
             f"{clock} {port}: Product: {product}"]
    if serial:
        lines.append(f"{clock} {port}: SerialNumber: {serial}")
    return kernel(*lines)


def unplug(clock: str, port: str, devnum: int) -> list[str]:
    return kernel(f"{clock} {port}: USB disconnect, device number {devnum}")


# ----- the kernel log: every notice kind, ids, speeds -------------------------------------------------------------
EVERY_KIND = (plug("09:00:00", "1-5", 7, "0483", "5740", "fdcanusb", "AAA")  # plugged
              + plug("09:01:00", "1-6", 8, "0483", "5740", "fdcanusb", "BBB")
              + unplug("09:01:20", "1-6", 8)  # unplugged (never back)
              + plug("09:02:00", "6-2", 3, "8086", "0b5b", "D405", "CAM", speed="SuperSpeed")
              + unplug("09:02:10", "6-2", 3)
              + plug("09:02:14", "6-2", 4, "8086", "0b5b", "D405", "CAM", speed="high-speed")  # dropped, back at USB 2
              + plug("09:03:00", "1-7", 9, "0483", "5740", "fdcanusb", "CCC")
              + unplug("09:03:01", "1-7", 9))  # flapped: never stayed, never came back


def test_every_notice_kind_has_an_id_its_raws_and_a_code():
    notices = journal.replay(journal.parse_kernel_log(EVERY_KIND))
    kinds: dict[str, journal.Notice] = {}
    for notice in notices:
        kinds.setdefault(notice.kind, notice)  # the first of each kind
    # every kind the coalescer can emit is in this log, and the host maps each one (Preview's KeyError 'flapped')
    assert set(kinds) == set(journal.NOTICE_KINDS) == set(activity.NOTICE_CODE)
    assert kinds["plugged"].id == "usb:1-5:7:add" and kinds["unplugged"].id == "usb:1-6:8:remove"
    assert kinds["dropped"].id == "usb:6-2:3:remove" and [journal.raw_id(raw) for raw in kinds["dropped"].raws] == [
        "usb:6-2:3:remove", "usb:6-2:4:add"]
    assert kinds["flapped"].text() == "fdcanusb CCC flapped on 1-7: 1 short enumeration, none stayed 3 s"
    assert [journal.raw_id(raw) for raw in kinds["flapped"].raws] == ["usb:1-7:9:add", "usb:1-7:9:remove"]


def test_kernel_speed_words():
    assert [journal.speed_mbps(word) for word in ("low-speed", "full-speed", "high-speed", "SuperSpeed", "SuperSpeed Plus Gen 2x1",
                                                  "SuperSpeed Plus Gen 2x2", "SuperSpeed Plus Gen 1x2")] == [
        1.5, 12.0, 480.0, 5000.0, 10000.0, 20000.0, 10000.0]
    [added] = [event for event in journal.parse_kernel_log(plug("10:00:00", "1-1", 12, "8086", "0b07", "D435", speed="high-speed"))]
    assert added.speed_mbps == 480.0 and added.devnum == 12


def test_wanted_lines_drop_noise():
    assert journal.wanted("2026-10-23T14:17:36-07:00 rig kernel: usb 1-1: new high-speed USB device number 12 using xhci_hcd")
    assert journal.wanted("2026-10-23T14:17:36-07:00 rig kernel: cdc_acm 1-1:1.0: ttyACM1: USB ACM device")
    assert journal.wanted("2026-10-23T14:17:36-07:00 rig kernel: pcieport 0000:00:01.1: AER: Corrected error received: 0000:01:00.0")
    assert not journal.wanted('2026-10-23T14:17:36-07:00 rig kernel: audit: type=1400 apparmor="DENIED"')
    assert not journal.wanted("2026-10-23T08:35:52-07:00 rig kernel: uvcvideo 6-1:1.3: Non-zero status (-71) in video completion handler.")


# ----- the kernel log: USB errors, driver binds, PCIe AER -------------------------------------------------------
NOTES = kernel("10:00:00 1-3: device descriptor read/64, error -71",
               "10:00:01 1-3: device descriptor read/64, error -71",
               "10:00:01 1-3: device not accepting address 5, error -71",
               "10:00:02 usb1-port3: unable to enumerate USB device",
               "10:00:30 usb2-port4: over-current condition",
               "10:00:40 1-1-port2: disabled by hub (EMI?), re-enabling...",
               "10:00:50 1-4: reset high-speed USB device number 6 using xhci_hcd",
               "10:01:00 usb3-port1: Cannot enable. Maybe the USB cable is bad?") + \
    plug("10:02:00", "1-1", 12, "8086", "0b07", "Intel(R) RealSense(TM) Depth Camera 435") + \
    kernel("10:02:01 1-1: Found UVC 1.50 device Intel(R) RealSense(TM) Depth Camera 435  (8086:0b07)",
           "10:02:01 1-1: Found UVC 1.50 device Intel(R) RealSense(TM) Depth Camera 435  (8086:0b07)") + \
    plug("10:03:00", "1-5", 13, "0483", "5740", "fdcanusb", "DDD") + \
    kernel("10:03:00 @cdc_acm 1-5:1.0: ttyACM1: USB ACM device",
           "10:04:00 @ftdi_sio 1-2:1.0: FTDI USB Serial Device converter detected",
           "10:04:00 1-2: FTDI USB Serial Device converter now attached to ttyUSB0",
           "10:05:00 @ftdi_sio ttyUSB0: FTDI USB Serial Device converter now disconnected from ttyUSB0",
           "10:06:00 @pcieport 0000:00:01.1: AER: Corrected error message received from 0000:01:00.0",
           "10:06:00 @pcieport 0000:00:01.1: PCIe Bus Error: severity=Corrected, type=Physical Layer, (Receiver ID)",
           "10:06:00 @pcieport 0000:00:01.1:   device [1022:1483] error status/mask=00000001/0000e000",
           "10:07:00 @nvme 0000:02:00.0: AER: aer_status: 0x00004000, aer_mask: 0x00000000",
           "10:07:00 @nvme 0000:02:00.0: PCIe Bus Error: severity=Uncorrected (Non-Fatal), type=Transaction Layer, (Requester ID)")


def test_usb_errors_driver_binds_and_aer_are_parsed_and_bursts_folded():
    notes = kernel_log.parse_port_notes(NOTES)
    by = [(note.code, note.port or note.device, note.driver, note.count, note.nodes, note.severity) for note in notes]
    assert by == [("UsbDescriptorReadError", "1-3", "", 2, [], "warn"),
                  ("UsbNotAcceptingAddress", "1-3", "", 1, [], "warn"),
                  ("UsbEnumerationFailed", "1-3", "", 1, [], "error"),
                  ("UsbOverCurrent", "2-4", "", 1, [], "error"),
                  ("UsbPortDisabled", "1-1.2", "", 1, [], "warn"),
                  ("UsbReset", "1-4", "", 1, [], "warn"),
                  ("UsbCannotEnable", "3-1", "", 1, [], "error"),
                  ("DriverBound", "1-1", "uvcvideo", 2, [], "info"),  # the D435 logs it twice: one notice
                  ("DriverBound", "1-5", "cdc_acm", 1, ["/dev/ttyACM1"], "info"),
                  ("DriverBound", "1-2", "ftdi_sio", 1, ["/dev/ttyUSB0"], "info"),
                  ("DriverUnbound", "1-2", "ftdi_sio", 1, ["/dev/ttyUSB0"], "info"),
                  ("PcieAerError", "0000:01:00.0", "pcieport", 2, [], "warn"),  # the status/mask line is not an AER line
                  ("PcieAerError", "0000:02:00.0", "nvme", 2, [], "error")]
    aer = notes[-2]
    assert aer.message.startswith("PCIe Bus Error: severity=Corrected, type=Physical Layer")
    assert kernel_log.port_of("usb1-port3") == "1-3" and kernel_log.port_of("1-1-port2") == "1-1.2"


def boot_events(lines: list[str], now_clock: str = "11:00:00") -> activity.UsbActivity:
    now = dt.datetime.fromisoformat(f"2026-10-23T{now_clock}-07:00").timestamp()
    return activity.usb_activity([activity.Boot(BOOT, lines)], now, PDT)


def test_activity_events_are_typed_with_stable_ids():
    out = boot_events(EVERY_KIND + NOTES)
    types = {}
    for event in out.events:
        types.setdefault(activity.event_type(event.code), set()).add(event.code)
    assert types["usb"] == {"Plugged", "Unplugged", "ReEnumerated", "Flapped"}
    assert types["link"] == {"LinkSpeed", "LinkDowngraded"}
    assert types["driver"] == {"DriverBound", "DriverUnbound"} and types["pcie"] == {"PcieAerError"}
    assert {"UsbDescriptorReadError", "UsbOverCurrent", "UsbPortDisabled"} <= types["usb_error"] and types["boot"] == {"Booted"}
    ids = [activity.event_id(event) for event in out.events]
    assert len(ids) == len(set(ids)) and ids == [activity.event_id(event) for event in boot_events(EVERY_KIND + NOTES).events]
    downgrade = next(event for event in out.events if event.code == "LinkDowngraded")
    assert downgrade.message == "D405 CAM on 6-2 linked at USB 2.0 · 480 Mb/s; it linked at USB 3.2 Gen 1 · 5 Gb/s earlier this boot"
    bound = next(event for event in out.events if event.code == "DriverBound" and event.args["driver"] == "cdc_acm")
    assert activity.event_id(bound) == "usb:1-5:13:add/cdc_acm:bind" and bound.message == "cdc_acm bound on 1-5 → /dev/ttyACM1"
    assert out.consumed >= {"usb:1-7:9:add", "usb:1-7:9:remove", "usb:6-2:4:add"}
    assert {activity.event_type(code) for code in ("NetCarrierUp", "LevelChanged", "FaultConfirmed", "NoAck")} == {
        "network", "level", "fault", "plugin"}


def test_a_host_survives_every_notice_kind_and_filters_by_type(tmp_path):
    """The Preview crash of 2026-10-23 (a notice kind the host did not map): refresh, view, activity and state all run
    over a log with every kind, error, bind and AER line."""

    class Kernel(FakeOs):
        def boots(self):
            return [activity.Boot(BOOT, EVERY_KIND + NOTES)]

    clock = SetClock(dt.datetime.fromisoformat("2026-10-23T11:00:00-07:00").timestamp())
    clock.utc_offset_s = PDT
    host = Host(plugins_dir=tmp_path / "none", clock=clock, os_source=Kernel(standard_skeleton()))
    host.refresh()
    state = host.state()
    assert state["recent"] and all({"type", "id", "provisional"} <= set(event) for event in state["recent"])
    every = host.activity(5000)["events"]
    assert {event["type"] for event in every} >= {"usb", "link", "usb_error", "driver", "pcie", "boot"}
    usb_only = host.activity(5000, ("usb", "link"))["events"]
    assert usb_only and {event["type"] for event in usb_only} == {"usb", "link"}
    with pytest.raises(ValueError, match="unknown event type 'bogus'"):
        host.activity(10, ("bogus",))


# A stand-in for journalctl: the one-shot read prints the log and a cursor; the follower (-f) prints one more line
# (a flap that never comes back) and then waits, as journalctl -f does.
FAKE_JOURNALCTL = r'''
import json, sys, time
lines = json.load(open(sys.argv[1]))
if "-f" not in sys.argv:
    print("\n".join(lines["read"]))
    print("-- cursor: s=fake;i=1")
    raise SystemExit
assert any(arg.startswith("--after-cursor=s=fake") for arg in sys.argv), sys.argv
for line in lines["follow"]:
    print(line, flush=True)
time.sleep(60)
'''


def test_real_startup_survives_every_notice_kind_through_the_kernel_log_follower(tmp_path, monkeypatch):
    """``Host.start`` as ``serve`` runs it (the scheduler, the refresh loop, the journalctl read and follower), over a
    log holding every notice kind, USB error, bind and AER line, then answered over HTTP. The Preview crash of
    2026-10-23 was this path meeting a kind it did not map."""

    monkeypatch.setattr(activity, "boot_start", lambda: BOOT)  # the recorded log's boot, not this PC's /proc/stat btime
    wall = time.time()
    late = [kernel_now("1-9: new full-speed USB device number 40 using xhci_hcd", wall - 30),
            kernel_now("1-9: New USB device found, idVendor=0483, idProduct=5740, bcdDevice= 1.00", wall - 30),
            kernel_now("1-9: SerialNumber: LATE", wall - 30), kernel_now("1-9: USB disconnect, device number 40", wall - 29)]
    (tmp_path / "log.json").write_text(json.dumps({"read": EVERY_KIND + NOTES, "follow": late}))
    (tmp_path / "journalctl.py").write_text(FAKE_JOURNALCTL)

    class FollowedOs(FakeOs):
        def __init__(self):
            super().__init__(standard_skeleton())
            self.kernel_log = activity.LiveKernelLog(journalctl=(sys.executable, str(tmp_path / "journalctl.py"),
                                                                 str(tmp_path / "log.json")))

        def boots(self):
            return self.kernel_log.boots(time.monotonic())

        def close(self):
            self.kernel_log.close()

    host = Host(plugins_dir=tmp_path / "none", clock=Clock(), os_source=FollowedOs())
    http = make_server(host, 0)
    serve_in_thread(http)
    host.start(udev=False)
    try:
        assert wait_for(lambda: any(event["code"] == "Flapped" and event["port"] == "1-9" for event in host.activity(500)["events"]))
        assert host.os.kernel_log.following()
        with urllib.request.urlopen(host.url + "api/v1/activity?limit=500", timeout=10) as response:
            served = json.loads(response.read())["events"]
        codes = {event["code"] for event in served}
        assert {activity.NOTICE_CODE[kind] for kind in journal.NOTICE_KINDS} <= codes
        assert {"LinkSpeed", "LinkDowngraded", "DriverBound", "DriverUnbound", "PcieAerError", "UsbOverCurrent"} <= codes
        with urllib.request.urlopen(host.url + "api/v1/state", timeout=10) as response:
            assert json.loads(response.read())["recent"]
    finally:
        host.stop()
        http.shutdown()
        http.server_close()
    assert not host.os.kernel_log.following()  # the follower is stopped with the host


def test_once_and_serve_with_a_fixture_start_and_answer():
    """``io-diagnostics once --fixture F1`` and ``serve --fixture F1``, as Preview starts them, as real processes."""

    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    cli = str(ROOT / "io_diagnostics" / "cli.py")
    once = subprocess.run([sys.executable, cli, "once", "--fixture", "F1", "--json"], capture_output=True, text=True, timeout=60, env=env)
    assert once.returncode == 0, once.stderr[-800:]
    events = json.loads(once.stdout)["activity"]["events"]
    assert events and all({"type", "id", "provisional"} <= set(event) for event in events)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen([sys.executable, cli, "serve", "--fixture", "F1", "--port", str(port)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, text=True, env=env)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/activity?types=usb", timeout=5) as response:
                    served = json.loads(response.read())
                break
            except (urllib.error.URLError, ConnectionError):
                assert server.poll() is None, server.stderr.read()[-800:]
                assert time.monotonic() < deadline, "serve never answered"
                time.sleep(0.2)
        assert served["events"] and {event["type"] for event in served["events"]} == {"usb"}
    finally:
        server.terminate()
        server.wait(timeout=10)


# ----- udev: the instant half --------------------------------------------------------------------------------------
def block(**props: str) -> dict:
    return dict(props)


D435_ADD = block(ACTION="add", SUBSYSTEM="usb", DEVTYPE="usb_device", DEVNUM="012", ID_VENDOR_ID="8086", ID_MODEL_ID="0b07",
                 DEVPATH="/devices/pci0000:00/0000:00:08.1/0000:0e:00.3/usb1/1-1",
                 ID_MODEL_ENC="Intel(R)\\x20RealSense(TM)\\x20Depth\\x20Camera\\x20435\\x20", ID_MODEL="Intel_R__RealSense_TM__435")


def test_udev_blocks_and_the_descriptor_product_string():
    text = ["UDEV  [1.0] add      /devices/.../1-1 (usb)\n", *(f"{key}={value}\n" for key, value in D435_ADD.items()), "\n",
            "UDEV  [1.1] add /devices/.../video0 (video4linux)\n", "SUBSYSTEM=video4linux\n", "DEVNAME=/dev/video0\n"]
    found = list(udev.blocks(iter(text)))
    assert [item["SUBSYSTEM"] for item in found] == ["usb", "video4linux"]
    assert udev.product(found[0]) == "Intel(R) RealSense(TM) Depth Camera 435"
    [event] = list(udev.parse_blocks(iter(text)))
    assert (event.port, event.devnum, event.product) == ("1-1", 12, "Intel(R) RealSense(TM) Depth Camera 435")


def test_live_activity_plugs_binds_and_carriers(tmp_path):
    live = activity.LiveActivity(lambda: 1000.0, sys_root=tmp_path)
    assert live.udev(D435_ADD)
    iface = "/devices/pci0000:00/0000:00:08.1/0000:0e:00.3/usb1/1-1/1-1:1.0"
    assert live.udev(block(ACTION="bind", SUBSYSTEM="usb", DEVTYPE="usb_interface", DRIVER="uvcvideo", DEVPATH=iface))
    for number in (0, 1):
        assert live.udev(block(ACTION="add", SUBSYSTEM="video4linux", DEVNAME=f"/dev/video{number}", ID_USB_DRIVER="uvcvideo",
                               DEVPATH=f"{iface}/video4linux/video{number}"))
    assert not live.udev(block(ACTION="add", SUBSYSTEM="tty", DEVNAME="/dev/ttyS0", DEVPATH="/devices/platform/serial8250/tty/ttyS0"))
    assert not live.udev(block(ACTION="add", SUBSYSTEM="usb", DEVTYPE="usb_device", DEVNUM="001", DEVPATH="/devices/x/usb1"))
    assert live.udev(block(ACTION="unbind", SUBSYSTEM="usb", DEVTYPE="usb_interface", DEVPATH=iface))  # no DRIVER: recalled
    events = {activity.event_id(event): event for event in live.events()}
    assert list(events) == ["usb:1-1:12:add", "usb:1-1:12:add/uvcvideo:bind", "usb:1-1:12:add/uvcvideo:unbind"]
    plugging = events["usb:1-1:12:add"]
    assert plugging.code == "Plugging" and plugging.args["provisional"] is True
    assert plugging.message == "plugging in: Intel(R) RealSense(TM) Depth Camera 435 on 1-1…"
    assert events["usb:1-1:12:add/uvcvideo:bind"].message == "uvcvideo bound on 1-1 → /dev/video0, /dev/video1"
    nic = {"bus": "net", "id": "net:enp6s0", "attrs": {"ifname": "enp6s0", "carrier": True, "carrier_changes": 4, "speed_mbps": 1000}}
    assert not live.carriers([nic])  # the first look is no change
    assert live.carriers([{**nic, "attrs": {**nic["attrs"], "carrier": False, "carrier_changes": 5}}])
    down = live.events()[-1]
    assert (down.code, down.severity, down.message, activity.event_id(down)) == ("NetCarrierDown", "warn",
                                                                                "enp6s0: link down (no carrier)", "net:enp6s0:5")


def test_merge_confirms_in_place_and_never_shows_one_event_twice(tmp_path):
    live = activity.LiveActivity(lambda: 1000.0, sys_root=tmp_path)
    live.udev(block(**{**D435_ADD, "DEVPATH": "/devices/x/usb1/1-5", "DEVNUM": "13", "ID_SERIAL_SHORT": "DDD"}))
    iface = "/devices/x/usb1/1-5/1-5:1.0"
    live.udev(block(ACTION="add", SUBSYSTEM="tty", DEVNAME="/dev/ttyACM1", ID_USB_DRIVER="cdc_acm", DEVPATH=f"{iface}/tty/ttyACM1"))
    pending_only, pending = activity.merge([], set(), live.events())
    assert pending == ["usb:1-5:13:add"] and [event.code for event in pending_only] == ["Plugging", "DriverBound"]
    log = boot_events(plug("10:03:00", "1-5", 13, "0483", "5740", "fdcanusb", "DDD")
                      + kernel("10:03:00 @cdc_acm 1-5:1.0: ttyACM1: USB ACM device"), now_clock="10:03:02")
    early, pending = activity.merge(log.events, log.consumed, live.events())  # not settled yet: still provisional
    assert pending == ["usb:1-5:13:add"] and "Plugging" in [event.code for event in early]
    log = boot_events(plug("10:03:00", "1-5", 13, "0483", "5740", "fdcanusb", "DDD")
                      + kernel("10:03:00 @cdc_acm 1-5:1.0: ttyACM1: USB ACM device"), now_clock="10:03:04")
    merged, pending = activity.merge(log.events, log.consumed, live.events())
    ids = [activity.event_id(event) for event in merged]
    assert pending == [] and len(ids) == len(set(ids)) and "Plugging" not in [event.code for event in merged]
    assert next(event for event in merged if activity.event_id(event) == "usb:1-5:13:add").code == "Plugged"
    [bound] = [event for event in merged if event.code == "DriverBound"]
    assert bound.source == activity.KERNEL_SOURCE and bound.args["nodes"] == ["/dev/ttyACM1"]


# ----- the real check: a fake udevadm stream and a growing fake kernel log, through the server ----------------------
FAKE_UDEVADM = r'''
import sys
with open(sys.argv[1]) as fifo:  # what the test writes is what "udevadm monitor" prints
    for line in fifo:
        sys.stdout.write(line)
        sys.stdout.flush()
'''


class GrowingKernelOs(FakeOs):
    """A live-like OS: a kernel log the test appends to, and a udevadm monitor that is a fake subprocess."""

    def __init__(self, skeleton, udev_command):
        super().__init__(skeleton)
        self.lines: list[str] = []
        self.udev_command = udev_command

    def boots(self):
        return [activity.Boot(BOOT, list(self.lines))]


def kernel_now(text: str, at: float) -> str:
    stamp = dt.datetime.fromtimestamp(at).astimezone().isoformat(timespec="microseconds")
    return f"{stamp} rig kernel: usb {text}"


def test_a_provisional_plug_shows_within_a_second_and_is_confirmed_in_place_through_the_event_stream(tmp_path):
    fifo = tmp_path / "udev.fifo"
    os.mkfifo(fifo)
    script = tmp_path / "fake_udevadm.py"
    script.write_text(FAKE_UDEVADM)
    host_os = GrowingKernelOs(standard_skeleton(), [sys.executable, "-u", str(script), str(fifo)])
    host = Host(plugins_dir=tmp_path / "none", clock=Clock(), os_source=host_os)
    writer = None
    http = make_server(host, 0)
    serve_in_thread(http)
    host.start()
    streamed: list[tuple[float, dict]] = []
    try:
        writer = open(fifo, "w")  # opens once the fake udevadm opened its end
        stream = urllib.request.urlopen(host.url + "api/v1/events?types=usb", timeout=20)
        assert stream.headers["Content-Type"].startswith("text/event-stream")

        def read() -> None:
            name = ""
            for raw in stream:
                line = raw.decode().rstrip("\n")
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: ") and name == "activity":
                    streamed.append((time.monotonic(), json.loads(line[6:])))

        threading.Thread(target=read, daemon=True).start()
        time.sleep(0.3)  # the stream's hello is out
        plugged_at, wall = time.monotonic(), time.time()
        writer.write("UDEV  [5.1] add /devices/x/usb1/1-1 (usb)\n" + "".join(
            f"{key}={value}\n" for key, value in {**D435_ADD, "ID_SERIAL_SHORT": "SER9"}.items()) + "\n")
        writer.flush()
        deadline = plugged_at + 1.0
        while time.monotonic() < deadline and not any(event["id"] == "usb:1-1:12:add" for _at, event in streamed):
            time.sleep(0.01)
        first = next(((at, event) for at, event in streamed if event["id"] == "usb:1-1:12:add"), None)
        assert first is not None, "no provisional event on /api/v1/events within 1 s"
        assert first[1]["provisional"] is True and first[1]["code"] == "Plugging" and first[0] - plugged_at < 1.0
        api = host.activity(50, ("usb",))["events"]
        assert [event["provisional"] for event in api if event["id"] == "usb:1-1:12:add"] == [True]
        # the kernel's own lines for that uevent, stamped now: the log settles it 3 s later, in place
        host_os.lines += [kernel_now("1-1: new high-speed USB device number 12 using xhci_hcd", wall),
                          kernel_now("1-1: New USB device found, idVendor=8086, idProduct=0b07, bcdDevice=50.f1", wall),
                          kernel_now("1-1: Product: Intel(R) RealSense(TM) Depth Camera 435", wall),
                          kernel_now("1-1: SerialNumber: SER9", wall)]
        host.wake()
        deadline = plugged_at + 6.0
        while time.monotonic() < deadline and not any(event["id"] == "usb:1-1:12:add" and not event["provisional"]
                                                      for _at, event in streamed):
            time.sleep(0.05)
        confirmed = [event for _at, event in streamed if event["id"] == "usb:1-1:12:add" and not event["provisional"]]
        assert confirmed and confirmed[0]["code"] == "Plugged"
        assert confirmed[0]["message"] == "Intel(R) RealSense(TM) Depth Camera 435 SER9 plugged on 1-1"
        api = host.activity(50, ("usb",))["events"]
        assert [(event["code"], event["provisional"]) for event in api if event["id"] == "usb:1-1:12:add"] == [("Plugged", False)]
        assert len([event for event in api if event["port"] == "1-1"]) == 1  # one row, never Plugging + Plugged
        assert not host.pending
    finally:
        host.stop()
        http.shutdown()
        http.server_close()
        if writer:
            writer.close()
    assert host._udev.process.poll() is not None  # the fake udevadm is stopped with the host


# ----- the follow verb ---------------------------------------------------------------------------------------------
FOLLOW_PLUGIN = r'''
import json, os, sys, time
args = sys.argv[1:]
verb = next(word for word in args if word in ("describe", "diagnose", "follow"))
if verb == "describe":
    print(json.dumps({"name": "ticker", "schema": "diagnostics-record/0", "follow": {"period_s": 0.1}}))
    raise SystemExit
if verb == "diagnose":
    with open(os.environ["FOLLOW_LOG"], "a") as log:
        log.write("diagnose\n")
    raise SystemExit
with open(os.environ["FOLLOW_LOG"], "a") as log:
    log.write(f"follow {os.getpid()}\n")
if os.environ.get("FOLLOW_LONG"):
    print("x" * 300000, flush=True)
for number in range(int(os.environ.get("FOLLOW_LINES", "1000000"))):
    print(json.dumps({"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": "ticker: tick",
                      "path": "", "message": f"tick {number}", "expected_period_s": 0.1,
                      "measurements": [{"key": "n", "value": number, "unit": "1"}]}), flush=True)
    time.sleep(0.1)
sys.exit(3)
'''


def follow_host(tmp_path, **env) -> tuple[Host, Path]:
    directory = tmp_path / "plugins.d"
    directory.mkdir()
    (directory / "ticker.py").write_text(FOLLOW_PLUGIN)
    log = tmp_path / "follow.log"
    extra = "".join(f', {key} = "{value}"' for key, value in env.items())
    (directory / "ticker.toml").write_text(f'name = "ticker"\ncommand = ["{{python}}", "{{here}}/ticker.py"]\nperiod_s = 0.2\n'
                                           f'timeout_s = 5\nmatches = ["host"]\nenv = {{ FOLLOW_LOG = "{log}"{extra} }}\n')
    host = Host(plugins_dir=directory, clock=Clock(), os_source=FakeOs(standard_skeleton()), follow_backoff_s=(0.2, 0.4))
    return host, log


def text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def wait_for(condition, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_follow_runs_one_process_per_device_fills_the_history_and_never_diagnoses(tmp_path):
    host, log = follow_host(tmp_path)
    host.start(udev=False)
    try:
        assert wait_for(lambda: len(host.history.series(("host", "ticker: tick"), 0, 1e12)[0]["points"]) >= 10
                        if host.history.keys("host") and host.history.series(("host", "ticker: tick"), 0, 1e12) else False)
        assert log.read_text().splitlines()[0].startswith("follow ") and "diagnose" not in log.read_text()
        [job] = host.plugins()["plugins"][0]["jobs"]
        assert job["follow"]["state"] == "running" and job["follow"]["restarts"] == 0 and job["next_due_s"] is None
        run = next(check for check in host.node("host")["checks"] if check["name"] == "ticker: plugin run")
        assert run["level"] == OK and run["message"].startswith("following since ") and "every 0.1 s" in run["message"]
        pid = job["follow"]["pid"]
    finally:
        host.stop()
    assert wait_for(lambda: not os.path.exists(f"/proc/{pid}") or open(f"/proc/{pid}/stat").read().split()[2] == "Z", 5)


def test_a_follower_that_exits_is_restarted_with_a_backoff_and_says_so(tmp_path):
    host, log = follow_host(tmp_path, FOLLOW_LINES="3")
    host.start(udev=False)
    try:
        assert wait_for(lambda: text(log).count("follow ") >= 3)
        [job] = host.plugins()["plugins"][0]["jobs"]
        assert job["follow"]["restarts"] >= 2 and "exited 3" in (job["follow"]["exit"] or "follow exited 3")
        statuses = [status for status in host.view().merged.statuses if status.name == "ticker: plugin run"]
        assert statuses
    finally:
        host.stop()
    starts = [line for line in log.read_text().splitlines() if line.startswith("follow ")]
    assert len(starts) <= 6  # 0.2 s, then 0.4 s caps: never a tight loop


def test_a_follower_line_too_long_is_rejected_unread(tmp_path):
    host, _log = follow_host(tmp_path, FOLLOW_LONG="1")
    host.start(udev=False)
    try:
        assert wait_for(lambda: (host.plugins()["plugins"][0]["jobs"] or [{}])[0].get("follow", {}) and
                        host.plugins()["plugins"][0]["jobs"][0]["follow"]["rejected"] >= 1)
        assert wait_for(lambda: host.plugins()["plugins"][0]["jobs"][0]["follow"]["records"] >= 2)
    finally:
        host.stop()


def test_follow_period_reads_describe():
    assert follow_period({"follow": {"period_s": 2}}) == 2.0 and follow_period({"follow": True}) == 1.0
    assert follow_period({}) is None and follow_period(None) is None and follow_period({"follow": False}) is None


# ----- level changes from the history store ----------------------------------------------------------------------
def test_level_changes_are_events_stale_included():
    history = History()
    ok = DiagnosticStatus(level=OK, name="board: comms", path="host/x", expected_period_s=1.0)
    history.observe([ok], 100.0)
    history.observe([ok], 101.0)
    history.observe([DiagnosticStatus(level=ERROR, name="board: comms", path="host/x", code="NoAck", message="no ACK",
                                      expected_period_s=1.0)], 102.0)
    history.observe([ok], 120.0)  # 18 s late: it went stale at 107
    transitions = history.transitions(130.0)
    assert [(at, before, after) for at, _path, _name, before, after, _code, _message in transitions] == [
        (102.0, "ok", "error"), (107.0, "error", "stale"), (120.0, "stale", "ok"), (125.0, "ok", "stale")]
    events = activity.level_events(transitions)
    assert events[0].message == "board: comms: ok → error (NoAck: no ACK)" and events[0].severity == "error"
    assert {activity.event_type(event.code) for event in events} == {"level"}
    assert len({activity.event_id(event) for event in events}) == 4


# ----- the computer plugin's follow --------------------------------------------------------------------------------
class FollowProbe(FakeProbe):
    def __init__(self, **override):
        super().__init__(**override)
        self.clock_mono = 0.0
        self.scans = 0

    def monotonic(self):
        return self.clock_mono

    def processes(self):
        self.scans += 1
        return [{"pid": 1, "name": "init", "cpu_percent": 0.0, "rss": 10 * computer.MIB},
                {"pid": 4242, "name": "python3", "cpu_percent": 200.0, "rss": 3 * computer.GIB},
                {"pid": 77, "name": "chrome\tx", "cpu_percent": 40.0, "rss": 2 * computer.GIB}]


def test_computer_follow_ticks_every_second_with_processes_every_three_and_clock_every_ten():
    probe = FollowProbe()

    def sleep(seconds):
        probe.clock_mono += seconds

    class Out:
        lines: list[dict] = []

        def write(self, text):
            self.lines += [json.loads(line) for line in text.splitlines() if line]

        def flush(self):
            pass

    out = Out()
    options = computer.Options()
    options.period_s = 1.0
    assert computer.follow(options, probe, host="rig", ticks=10, out=out, sleep=sleep) == 0
    names = [line["name"].removeprefix("rig: ") for line in out.lines]
    assert names.count("CPU Information") == 10 and names.count("Processes") == 4 and names.count("NTP offset") == 1
    periods = {line["name"].removeprefix("rig: "): line["expected_period_s"] for line in out.lines}
    assert periods["CPU Information"] == 1.0 and periods["Processes"] == 3.0 and periods["NTP offset"] == 10.0
    processes = next(line for line in out.lines if line["name"] == "rig: Processes")
    assert processes["level"] == "ok"
    assert processes["message"] == "3 processes · busiest python3 (pid 4242) 50.0 % CPU · largest python3 (pid 4242) 3.0 GiB"
    values = {pair["key"]: pair["value"] for pair in processes["values"]}
    assert values["top_cpu"].splitlines() == ["pid\tname\tcpu_percent\trss_bytes", f"4242\tpython3\t50.0\t{3 * computer.GIB}",
                                              f"77\tchrome x\t10.0\t{2 * computer.GIB}", f"1\tinit\t0.0\t{10 * computer.MIB}"]
    assert values["top_memory"].splitlines()[1].startswith("4242\t")
    assert computer.describe()["follow"] == {"period_s": computer.FOLLOW_PERIOD_S}


def test_the_real_computer_processes_reader_opens_only_proc():
    opened = []
    sys.addaudithook(lambda event, args: opened.append(os.fsdecode(args[0])) if event == "open" and args
                     and isinstance(args[0], (str, bytes, os.PathLike)) else None)
    probe = computer.LiveProbe()
    probe.processes()
    found = probe.processes()
    assert found and all(row["cpu_percent"] >= 0 for row in found)
    assert any(row["pid"] == os.getpid() for row in found)
    assert not [path for path in opened if path.startswith("/dev/")]


def test_a_usb3_device_on_a_usb2_link_is_a_downgrade_event(tmp_path):
    """The D435 on 1-1, 2026-10-23: sysfs says bcdUSB 3.20, the kernel linked it at high speed (480 Mb/s)."""

    d435 = usb_device("1-1", "8086", "0b07", label="Intel(R) RealSense(TM) Depth Camera 435")
    d435["attrs"].update(usb_version=" 3.20", speed_mbps=480)

    class Kernel(FakeOs):
        def boots(self):
            return [activity.Boot(BOOT, plug("10:00:00", "1-1", 12, "8086", "0b07", "Intel(R) RealSense(TM) Depth Camera 435",
                                              speed="high-speed"))]

    clock = SetClock(dt.datetime.fromisoformat("2026-10-23T10:01:00-07:00").timestamp())
    host = Host(plugins_dir=tmp_path / "none", clock=clock, os_source=Kernel([host_dict(), usb_bus(1), d435]))
    host.refresh()
    [link] = host.activity(50, ("link",))["events"]
    assert (link["code"], link["severity"], link["id"]) == ("LinkDowngraded", "warn", "usb:1-1:12:add:link")
    assert link["message"] == ("Intel(R) RealSense(TM) Depth Camera 435 8086:0b07@1-1 on 1-1 linked at USB 2.0 · 480 Mb/s,"
                               " but it is a USB 3.20 device: a USB 2 cable, hub or port is in the way")
    assert link["path"] == host.layer.port_paths["1-1"]
