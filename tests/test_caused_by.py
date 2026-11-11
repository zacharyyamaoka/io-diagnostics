"""caused_by through the host (record v0.1 addendum, 2026-10-23): a composite's verdict names the members whose own
reports caused it, so the chip counts one fact once and names the member, while the composite's row keeps its word.

The contract rule and the report's three probes are in tests/test_record.py; here: a plugin's composite naming its
leg relative to its device, the rig row naming an absent adapter's row, the Computer's links line naming the rows it
repeats, and a check naming the host itself (whose records summarize sees on ``tree.HOST_SELF``). Fakes only.
"""

from __future__ import annotations

from test_host_setup import A, PRESENT, RACK, Rack
from test_host_support import host_dict, make_host, standard_skeleton, usb_bus, usb_device

from contract.record import ERROR, OK, WARN, DiagnosticStatus, Entity
from io_diagnostics import tree
from io_diagnostics.tree import HOST_PATH, PortHistory, os_statuses


def test_a_plugin_composite_names_its_leg_relative_to_its_device(tmp_path):
    host = make_host(tmp_path, standard_skeleton(usb_device("9-1", "1234", "abcd", "SER")), [("spider", "spider_named", ["usb:1234:abcd"])])
    host.run_all_once()
    spider = host.layer.device_paths["usb:9-1"]
    gait = next(check for check in host.node(spider)["checks"] if check["name"] == "spider: gait")
    assert gait["caused_by"] == [f"{spider}/leg3"]  # rebased onto the device's tree path, like its own path
    state = host.state()
    assert state["summary"]["counts"] == {"error": 1}
    assert [item["path"] for item in state["summary"]["attention"]] == [f"{spider}/leg3"]
    spider_row = host.node(spider)["node"]
    assert (spider_row["level"], spider_row["message"], spider_row["knock_on_of"]) == (ERROR, "leg 3 failed: walking on 7", f"{spider}/leg3")
    assert host.node(f"{spider}/leg3")["node"]["knock_on_of"] is None  # no longer swallowed by the spider's ERROR


def test_a_rig_missing_an_adapter_is_its_own_cause(tmp_path):
    rack = Rack(tmp_path, devices=tuple(device for device in PRESENT if device["serial"] != A))
    rig = rack.rig_row()
    check = rack.check(rig["path"], "expected setup")
    # WHY the rig: a missing device is a status on its rig, not a row (Zach, 2026-10-24) and not on the Computer, where it
    # read as the PC's own problem (Zach, 2026-11-11)
    assert (check["code"], check["caused_by"]) == ("ExpectedMissing", [])
    assert rack.check(rig["path"], f"fdcanusb {A}: presence")["code"] == "ExpectedAbsent"
    attention = [item["path"] for item in rack.state()["summary"]["attention"]]
    assert rig["path"] in attention and HOST_PATH not in attention and rig["knock_on_of"] is None


def test_a_rig_whose_problem_no_row_reports_stays_its_own_cause(tmp_path):
    # CAN 15 is expected but its driver never mentions it: nobody else says it is missing, so the rig names nothing
    rack = Rack(tmp_path, rack={**RACK, A: {"name": f"fdcanusb {A}", "model_id": "mjbots-fdcanusb", "answer": [1, 14], "silent": []}})
    rig = rack.rig_row()
    check = rack.check(rig["path"])
    assert (check["code"], check["caused_by"], rig["knock_on_of"]) == ("ExpectedMissing", [], None)
    assert rig["path"] in [item["path"] for item in rack.state()["summary"]["attention"]]


def test_the_links_line_names_the_rows_whose_problems_it_repeats(tmp_path):
    host = make_host(tmp_path, [host_dict(), usb_bus(9), usb_device("9-1", "1234", "abcd", "SER")], [("computer", "hot_computer", ["host"])])
    host.refresh()
    host.usb.history["9-1"] = PortHistory(enumerations=4)
    host.os_records = os_statuses(host.layer, host.usb.history, 1000.0, {"usb:9-1"})
    links = next(status for status in host.view().merged.statuses if status.name.endswith(": USB/PCIe links"))
    assert links.level == OK and links.caused_by == (host.layer.port_paths["9-1"],)


def test_a_check_naming_the_host_names_the_computers_own_records():
    """The host's records are summarized on a leaf beside its children; a caused_by of "host" must follow them there."""

    entities = {HOST_PATH: Entity(HOST_PATH, "rig", "host"), "host/rig-r": Entity("host/rig-r", "r", "rig", via=HOST_PATH)}
    statuses = [DiagnosticStatus(ERROR, "rig: disk /", "1 % free", path=HOST_PATH, code="DiskFull", stamp=1.0, received=1.0),
                DiagnosticStatus(WARN, "r: recording", "recording unavailable: disk", path="host/rig-r", code="NoDisk",
                                 stamp=1.0, received=1.0, caused_by=(HOST_PATH,))]
    merged = tree.Merged(entities, statuses, {}, {HOST_PATH: ["host/rig-r"], "host/rig-r": []}, {}, {HOST_PATH: None, "host/rig-r": HOST_PATH}, set())
    root = tree._summaries(merged, 1.0)[HOST_PATH]
    assert (root.own, dict(root.rollup.counts), root.rollup.origin) == (ERROR, {ERROR: 1}, HOST_PATH)
    assert root.rollup.knock_on == (("host/rig-r", HOST_PATH),)
