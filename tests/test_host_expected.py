"""Expected but absent (record v0.1): a device a plugin's ``describe`` lists as ``expected``, or one this host has seen a
plugin diagnose, that is not on USB now gets its own WARN row saying in plain words what the kernel saw.

The live case (2026-10-22): fdcanusb 28B3A25D was never on USB today (no 0483:5740, no by-id link, no plug event in
``journalctl -k``): a charge-only cable, a bad port, or the adapter. The journal and the state file are faked here.
"""

from __future__ import annotations

import datetime as dt
import json

from test_host_support import FakeOs, SetClock, standard_skeleton, usb_device, write_fake_plugin

from io_diagnostics import activity
from io_diagnostics.host import EXPECTED_PATH, RECENT_PATH, SEEN_FILE, Host, make_host

PDT = -7 * 3600
BOOT = dt.datetime(2026, 10, 22, 7, 0, tzinfo=dt.timezone(dt.timedelta(seconds=PDT))).timestamp()
NOW = BOOT + 11 * 3600  # 18:00
ABSENT = "usb:0483:5740:28B3A25D"
PRESENT = usb_device("1-3", "0483", "5740", "272813EF", label="fdcanusb 272813EF")
INVENTORY = [{"hardware_id": ABSENT, "name": "fdcanusb 28B3A25D", "last_seen": "2026-10-21 18:24", "source": "rack_inventory"},
             {"hardware_id": "usb:0483:5740:272813EF", "name": "fdcanusb 272813EF", "source": "rack_inventory"},
             {"hardware_id": "moteus:7E3A12", "name": "CAN 15"}]  # not a USB id: the host does not judge it


def kernel(*lines: str) -> list[str]:
    return [f"2026-10-22T{line} rig kernel: usb {rest}" for line, rest in (entry.split(" ", 1) for entry in lines)]


PLUGGED_THEN_GONE = kernel("09:00:00-07:00 1-5: new full-speed USB device number 7 using xhci_hcd",
                           "09:00:00-07:00 1-5: New USB device found, idVendor=0483, idProduct=5740, bcdDevice= 1.00",
                           "09:00:00-07:00 1-5: Product: fdcanusb",
                           "09:00:01-07:00 1-5: SerialNumber: 28B3A25D",
                           "09:30:00-07:00 1-5: USB disconnect, device number 7")
OTHER_DEVICE_ONLY = kernel("08:00:00-07:00 1-3: new full-speed USB device number 3 using xhci_hcd",
                           "08:00:00-07:00 1-3: New USB device found, idVendor=0483, idProduct=5740, bcdDevice= 1.00",
                           "08:00:01-07:00 1-3: SerialNumber: 272813EF")


class Clock(SetClock):
    utc_offset_s = PDT


class KernelOs(FakeOs):
    def __init__(self, skeleton, lines, note=""):
        super().__init__(skeleton)
        self.lines = lines
        self.kernel_log = type("KernelLog", (), {"note": note})()

    def boots(self):
        return [activity.Boot(BOOT, list(self.lines))]


def host_with(tmp_path, lines, *, expected=INVENTORY, skeleton=(PRESENT,), note="", state_file=None) -> Host:
    write_fake_plugin(tmp_path / "plugins.d", "moteus", ["usb:0483:5740"], tmp_path / "runs.jsonl", sleep_s=0.0,
                      expected=expected)
    host = Host(plugins_dir=tmp_path / "plugins.d", clock=Clock(NOW), os_source=KernelOs(standard_skeleton(*skeleton), lines, note),
                state_file=state_file)
    host.refresh()
    return host


def absent_checks(host: Host) -> dict[str, dict]:
    out = {}
    for path, entity in host.view().merged.entities.items():
        if path.startswith(EXPECTED_PATH + "/"):
            [check] = host.node(path)["checks"]
            out[entity.hardware_id] = {**check, "owner": entity.owner, "name": entity.name, "values": dict(
                (pair["key"], pair["value"]) for pair in check["values"])}
    return out


def recent_checks(host: Host) -> dict[str, dict]:
    out = {}
    for path, entity in host.view().merged.entities.items():
        if path.startswith(RECENT_PATH + "/"):
            [check] = host.node(path)["checks"]
            out[entity.hardware_id] = {**check, "owner": entity.owner, "name": entity.name}
    return out


def test_never_enumerated_says_so_in_plain_words(tmp_path):
    host = host_with(tmp_path, OTHER_DEVICE_ONLY)
    checks = absent_checks(host)
    assert list(checks) == [ABSENT]  # the present adapter and the non-USB board get no row
    check = checks[ABSENT]
    assert (check["level"], check["code"], check["owner"], check["name"]) == ("warn", "ExpectedAbsent", "moteus", "fdcanusb 28B3A25D")
    assert check["message"] == ("expected (last seen 2026-10-21 18:24, rack_inventory) · not on USB · the kernel saw no plug"
                                " event since boot (2026-10-22 07:00): try a data cable or another port")
    assert check["values"]["kernel_evidence"] == "never" and "charge-only" in check["values"]["what_to_check"]
    group = next(node for node in host.state()["tree"]["children"] if node["path"] == EXPECTED_PATH)
    assert (group["name"], group["kind"], group["level"]) == ("Expected, not connected", "group", "warn")  # worst below


def test_plugged_in_then_gone_says_when(tmp_path):
    check = absent_checks(host_with(tmp_path, PLUGGED_THEN_GONE))[ABSENT]
    assert check["message"] == ("expected (last seen 2026-10-21 18:24, rack_inventory) · not on USB · plugged in at 09:00:01"
                                " on 1-5, gone at 09:30:00")
    assert check["values"]["kernel_evidence"] == "left" and "connectors" in check["values"]["what_to_check"]


def test_an_unreadable_journal_is_said_not_guessed(tmp_path):
    check = absent_checks(host_with(tmp_path, [], note="journalctl unavailable: PermissionError: denied"))[ABSENT]
    assert check["values"]["kernel_evidence"] == "unknown"
    assert check["message"].endswith("the kernel log could not be read (journalctl unavailable: PermissionError: denied):"
                                     " no evidence whether it ever enumerated")
    empty = absent_checks(host_with(tmp_path / "empty", []))[ABSENT]  # readable but no USB line at all since boot
    assert "could not be read (no kernel USB lines since boot)" in empty["message"]


def test_a_present_device_has_no_row(tmp_path):
    plugged = usb_device("1-5", "0483", "5740", "28B3A25D", label="fdcanusb 28B3A25D")
    assert absent_checks(host_with(tmp_path, PLUGGED_THEN_GONE, skeleton=(PRESENT, plugged))) == {}
    assert EXPECTED_PATH not in host_with(tmp_path / "none", [], expected=[]).view().merged.entities


def test_the_host_remembers_what_a_plugin_diagnosed_and_writes_it_atomically(tmp_path):
    state = tmp_path / "state" / SEEN_FILE
    stick = usb_device("1-7", "0781", "5583", "STICK1", label="USB stick")  # no plugin claims it: never remembered
    first = host_with(tmp_path / "first", OTHER_DEVICE_ONLY, expected=[], skeleton=(PRESENT, stick), state_file=state)
    saved = json.loads(state.read_text())
    assert list(saved["devices"]) == ["usb:0483:5740:272813EF"]
    assert saved["devices"]["usb:0483:5740:272813EF"] == {"name": "fdcanusb 272813EF", "first_seen": NOW, "last_seen": NOW}
    assert sorted(path.name for path in state.parent.iterdir()) == [SEEN_FILE]  # no temp file left behind
    assert absent_checks(first) == {}

    later = host_with(tmp_path / "later", [], expected=[], skeleton=(), state_file=state)  # the adapter is gone now
    assert absent_checks(later) == {}  # only seen, never expected: no warning (Zach, 2026-10-23, FDA81D29)
    check = recent_checks(later)["usb:0483:5740:272813EF"]
    assert check["owner"] == "io_diagnostics" and check["level"] == "ok" and check["code"] == ""
    assert check["message"].startswith("seen 2026-10-22 18:00 · not on USB now · ")


def test_a_broken_state_file_is_named_and_the_host_starts_empty(tmp_path):
    state = tmp_path / SEEN_FILE
    state.write_text("{not json")
    host = host_with(tmp_path, [], expected=[], state_file=state)
    [source] = [source for source in host.state()["host"]["sources"] if source["name"] == "seen devices"]
    assert source["state"] == "skipped" and "unreadable" in source["note"]
    assert json.loads(state.read_text())["devices"]  # rewritten whole with what it saw now


def test_only_a_live_host_remembers_and_its_file_is_under_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("IO_DIAGNOSTICS_STATE_DIR", str(tmp_path / "state"))
    assert make_host(plugins_dir=tmp_path / "none", once=True).seen.path == tmp_path / "state" / SEEN_FILE
    assert make_host(fixture="F1", once=True).seen.path is None


def test_kernel_evidence_reads_only_usb_ids():
    assert activity.usb_id("usb:0483:5740:28B3A25D") == ("0483", "5740", "28B3A25D")
    assert activity.usb_id("usb:046d:0892@7-1.1.1") is None and activity.usb_id("moteus:7E3A12") is None


def test_a_device_only_seen_gives_no_warning_and_no_fault_when_it_is_unplugged(tmp_path):
    """FDA81D29, 2026-10-23 14:26: plugged for 15 s, unplugged; seen.json remembered it, and the host confirmed
    ExpectedAbsent. A device only SEEN is not expected: an info row under "Recently seen" (collapsed), never a WARN."""

    state = tmp_path / "state" / SEEN_FILE
    seen = usb_device("1-1", "0483", "5740", "FDA81D29", label="fdcanusb FDA81D29")
    host_with(tmp_path / "first", [], expected=[], skeleton=(PRESENT, seen), state_file=state)
    unplugged = kernel("17:59:40-07:00 1-1: new full-speed USB device number 9 using xhci_hcd",
                       "17:59:40-07:00 1-1: New USB device found, idVendor=0483, idProduct=5740, bcdDevice= 1.00",
                       "17:59:40-07:00 1-1: SerialNumber: FDA81D29",
                       "17:59:55-07:00 1-1: USB disconnect, device number 9")
    host = host_with(tmp_path / "later", unplugged, expected=[], skeleton=(PRESENT,), state_file=state)
    for _ in range(4):  # a WARN would confirm on its 3rd report
        host.clock.at += 2
        host.refresh()
    assert absent_checks(host) == {} and host.faults.all() == []
    [row] = recent_checks(host).values()
    assert (row["level"], row["code"], row["name"]) == ("ok", "", "fdcanusb FDA81D29")
    group = next(node for node in host.state()["tree"]["children"] if node["path"] == RECENT_PATH)
    assert (group["name"], group["kind"], group["level"], group["collapsed"]) == ("Recently seen", "group", "ok", True)
    assert not [event for event in host.activity()["events"] if event["code"].startswith("Fault")]
    assert [event["code"] for event in host.activity(types=("usb",))["events"] if event["hardware_id"] == "usb:0483:5740:FDA81D29"] == ["Unplugged", "Plugged"]
    [path] = [path for path in host.view().merged.entities if path.startswith(RECENT_PATH + "/")]
    assert host._setup_target(path, host.view())[0] == "absent"  # its card is an absent row's: Forget drops it


def test_a_device_declared_expected_still_warns_and_confirms_a_fault_when_unplugged(tmp_path):
    host = host_with(tmp_path, PLUGGED_THEN_GONE)  # ABSENT is in the plugin's describe.expected
    for _ in range(4):
        host.clock.at += 2
        host.refresh()
    assert absent_checks(host)[ABSENT]["level"] == "warn"
    assert [fault.code for fault in host.faults.all() if fault.hardware_id == ABSENT] == ["ExpectedAbsent"]
    assert RECENT_PATH not in host.view().merged.entities
