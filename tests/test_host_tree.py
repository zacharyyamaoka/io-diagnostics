"""The tree through the HOST: lane B's skeleton + plugin children with two edges, and contract.record's rules
(black boxes self-report, STALE on the host receive clock, knock-on collapse, fault debounce) end to end.

Plugins here are real subprocesses (tests/fixtures/plugins/cases/case_plugin.py); the OS side is a fake source.
"""

from __future__ import annotations

from test_host_support import make_host, node_by_name, standard_skeleton, tty, usb_bus, usb_device

from contract.record import ERROR, OK, STALE, WARN
from io_diagnostics import tree
from io_diagnostics.tree import build_os_layer

SPIDER = usb_device("9-1", "1234", "abcd", "SPIDER1", label="walker")


# ----- the OS layer -----------------------------------------------------------------------------------------------
def test_os_layer_honours_lane_b_edges_and_adds_only_usb_ports():
    hub = usb_device("9-2", "05e3", "0610", kind="hub", label="hub")
    behind_hub = usb_device("9-2.4", "0483", "5740", "S1", label="fdcanusb")
    serial = tty("usb:9-2.4", "ttyACM0", "/dev/serial/by-id/usb-mjbots_fdcanusb_S1-if00")
    layer = build_os_layer("rig", standard_skeleton(hub, behind_hub, serial, SPIDER), extra_ports={"9-3", "9-2.1"})
    paths = list(layer.entities)
    assert paths[0] == "host" and "host/usb9" in paths  # lane B's host dict is the root, its USB bus a child
    assert layer.device_paths["usb:9-2.4"] == "host/usb9/port-9-2/usb-9-2/port-9-2.4/usb-9-2.4"
    assert layer.entities["host/usb9/port-9-2/usb-9-2/port-9-2.4/usb-9-2.4/ttyACM0"].via == layer.device_paths["usb:9-2.4"]
    assert layer.functions["usb:9-2.4"][0]["id"] == "tty:usb:9-2.4"
    assert layer.physical == {"usb:9-2", "usb:9-2.4", "usb:9-1"}  # a USB device behind a hub is hardware, a tty is not
    assert layer.port_paths["9-3"] == "host/usb9/port-9-3" and layer.port_paths["9-2.1"].startswith("host/usb9/port-9-2/usb-9-2/")
    assert layer.icons[layer.device_paths["usb:9-2"]] == "hub" and layer.icons[layer.device_paths["usb:9-2.4"]] == "adapter"
    assert layer.entities["host"].identity == {"sw_version": "Ubuntu 24.04 · kernel 6.8.0"}


def test_a_device_without_a_parent_lands_in_its_bus_group():
    loose = {"bus": "pci", "id": "pci:0000:01:00.0", "path": "pci-0000:01:00.0", "label": "GPU", "parent": None,
             "attrs": {"kind": "display", "class": "Display controller", "driver": "nvidia"}}
    layer = build_os_layer("rig", [loose])
    assert layer.device_paths["pci:0000:01:00.0"] == "host/pci/pci-0000-01-00.0"
    assert layer.entities["host/pci"].kind == "group" and layer.entities["host/pci"].name == "PCI"


# ----- merge: two parent edges --------------------------------------------------------------------------------------
def test_plugin_children_hang_by_their_two_edges(tmp_path):
    host = make_host(tmp_path, standard_skeleton(SPIDER), [("spider", "spider", ["usb:1234:abcd"])])
    host.run_all_once()
    root = host.state()["tree"]
    spider = node_by_name(root, "spider")  # the plugin renamed the OS row ("walker")
    assert spider["path"] == "host/usb9/port-9-1/usb-9-1" and spider["edge"] == "via" and spider["parent"] == "host/usb9/port-9-1"
    leg = node_by_name(root, "leg 3")
    assert (leg["edge"], leg["parent"], leg["icon"]) == ("part_of", spider["path"], "motor")
    assert spider["identity"]["model"] == "8-legged walker" and spider["identity"]["manufacturer"] == "maker"
    assert spider["hardware_id"] == "usb:1234:abcd:SPIDER1"  # the OS's repeatable id survives the plugin's rename


def test_a_plugin_cannot_reparent_os_structure_and_orphans_rehang():
    from pathlib import Path

    from contract.record import DiagnosticStatus, Entity
    from io_diagnostics.plugins import PluginSpec
    from io_diagnostics.runner import Job

    layer = build_os_layer("rig", standard_skeleton(SPIDER, tty("usb:9-1", "ttyACM0", "/dev/serial/by-id/x")))
    job = Job(PluginSpec("p", ("true",), cwd=Path("."), file=Path("p.toml")), SPIDER, layer.device_paths["usb:9-1"])
    job.cache.order = ["leg9", "deep/below", "ttyACM0"]
    job.cache.entities = {"leg9": Entity("leg9", "leg 9", "part", part_of="body"),  # "body" never arrives
                          "deep/below": Entity("deep/below", "below", "node"),  # no edge at all
                          "ttyACM0": Entity("ttyACM0", "hijacked", "node", via="leg9")}  # an OS function node
    job.cache.statuses = {("ghost", "ghost: check"): DiagnosticStatus(level=OK, name="ghost: check", path="ghost")}
    merged = tree.merge(layer, [job], [])
    device = layer.device_paths["usb:9-1"]
    assert merged.parent[f"{device}/leg9"] == device and merged.edge[f"{device}/leg9"] == "via"
    assert merged.parent[f"{device}/deep/below"] == device
    assert merged.parent[f"{device}/ghost"] == device and merged.entities[f"{device}/ghost"].name == "ghost"
    assert merged.parent[f"{device}/ttyACM0"] == device and merged.entities[f"{device}/ttyACM0"].name == "/dev/ttyACM0"


# ----- black boxes self-report (Zach, 2026-10-22) -----------------------------------------------------------------
def test_the_spider_self_reports_warn_through_the_host_and_only_hints_at_its_failed_leg(tmp_path):
    host = make_host(tmp_path, standard_skeleton(SPIDER), [("spider", "spider", ["usb:1234:abcd"])])
    host.run_all_once()
    state = host.state()
    spider = node_by_name(state["tree"], "spider")
    assert (spider["own"]["level"], spider["level"]) == (WARN, WARN)
    assert spider["message"] == "1 of 8 legs failed; walking on 7"  # its own judgement, never overridden
    assert spider["hint"]["level"] == ERROR and spider["hint"]["origin"].endswith("/leg3")
    port = node_by_name(state["tree"], "Port 9-1")
    assert (port["own"]["level"], port["level"], port["hint"]["level"]) == (OK, OK, ERROR)  # a black box about its link
    bus = node_by_name(state["tree"], "USB bus 9")
    assert (bus["own"]["level"], bus["level"]) == (None, ERROR)  # a pure grouping node shows worst below
    assert state["tree"]["level"] == ERROR
    assert state["summary"]["counts"] == {WARN: 1, ERROR: 1}  # a WARN parent does not explain an ERROR child
    assert state["chip"]["label"] == "IO 2 ⚠"


# ----- STALE is derived on the host's receive clock ------------------------------------------------------------------
def test_stale_is_judged_on_the_host_receive_clock_never_on_a_plugin_claim(tmp_path):
    host = make_host(tmp_path, standard_skeleton(SPIDER), [("beat", "periodic", ["usb:1234:abcd"])], at=1000.0)
    host.run_all_once()  # the plugin claims received=0.0; a live plugin's claim is ignored
    path = node_by_name(host.state()["tree"], "walker")["path"]

    def heartbeat():
        return next(check for check in host.node(path)["checks"] if check["name"] == "thing: heartbeat")

    assert heartbeat()["received"] == 1000.0 and heartbeat()["effective_level"] == OK
    host.clock.at = 1005.9  # max(3 x 2 s, 5 s) = 6 s
    assert heartbeat()["effective_level"] == OK
    host.clock.at = 1006.1
    assert heartbeat()["effective_level"] == STALE and heartbeat()["level"] == OK  # reported level kept beside it
    row = node_by_name(host.state()["tree"], "walker")
    assert row["stale"] and row["level"] == STALE and row["stale_after_s"] == 6.0


def test_a_replay_plugin_keeps_its_recorded_receive_time(tmp_path):
    host = make_host(tmp_path, standard_skeleton(SPIDER), [("beat", "periodic", ["usb:1234:abcd"], {"replay": True})])
    host.run_all_once()
    path = node_by_name(host.state()["tree"], "walker")["path"]
    check = next(check for check in host.node(path)["checks"] if check["name"] == "thing: heartbeat")
    assert check["received"] == 0.0 and check["effective_level"] == STALE


# ----- knock-on collapse: one dead bus is one cause ------------------------------------------------------------------
def test_a_dead_bus_is_one_cause_through_the_host(tmp_path):
    adapter = usb_device("9-1", "0483", "5740", "DEAD", label="fdcanusb")
    host = make_host(tmp_path, standard_skeleton(adapter), [("moteus", "deadbus", ["usb:0483:5740"])], at=1000.0)
    host.run_all_once()  # first poll: all answer
    assert host.state()["summary"]["counts"] == {}
    host.clock.at = 1020.0
    host.run_all_once()  # later: bus error-passive, CAN 1 not re-sent (goes stale), CAN 2 says NoReply
    state = host.state()
    bus = node_by_name(state["tree"], "DEAD CAN bus")
    can1, can2 = node_by_name(state["tree"], "CAN 1"), node_by_name(state["tree"], "CAN 2")
    assert bus["level"] == ERROR and bus["knock_on_of"] is None
    assert can1["level"] == STALE and can1["knock_on_of"] == bus["path"]
    assert can2["level"] == WARN and can2["knock_on_of"] == bus["path"]  # an ERROR ancestor explains anything below
    assert state["summary"]["counts"] == {ERROR: 1} and [item["path"] for item in state["summary"]["attention"]] == [bus["path"]]
    adapter_row = node_by_name(state["tree"], "fdcanusb DEAD")
    assert (adapter_row["level"], adapter_row["hint"]["level"]) == (OK, ERROR)  # the adapter itself is fine
    assert {item["path"] for item in adapter_row["rollup"]["knock_on"]} == {can1["path"], can2["path"]}
    confirmed = [event for event in host.activity()["events"] if event["code"] == "FaultConfirmed"]
    assert [event["path"] for event in confirmed] == [bus["path"]]  # ERROR confirms at once (DEFAULT_DEBOUNCE)


# ----- faults count evaluations, not deliveries -----------------------------------------------------------------------
def test_a_replay_resending_one_recorded_warn_is_one_report(tmp_path):
    host = make_host(tmp_path, standard_skeleton(SPIDER), [("rec", "warn_replay", ["usb:1234:abcd"], {"replay": True})])
    for _run in range(4):
        host.run_all_once()
    fault = next(fault for fault in host.activity()["faults"] if fault["code"] == "Recorded")
    assert fault["status"] == "pending"  # a periodic WARN needs 3 EVALUATIONS; one evaluation delivered 4x is one


def test_extra_buses_from_lane_b_stay_in_their_order():
    layer = build_os_layer("rig", [*standard_skeleton(SPIDER), usb_bus(10)])
    children = [path for path, entity in layer.entities.items() if entity.via == "host"]
    assert children == ["host/usb9", "host/usb10"]
