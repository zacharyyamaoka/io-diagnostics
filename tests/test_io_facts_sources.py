"""Lane B discovery sources: PCI, network, serial and video nodes beside USB, merged into one OS skeleton.

A fake machine (tests/data/sysfs-rack-fixture.json, built by ``skeleton.fake_root``) carries what the rack PC
lacks today: a PCIe frame grabber, a capture card with a V4L2 node, NICs driven by an IgH and a pysoem master,
a usb-serial adapter and a real COM port. One live smoke reads the rack PC itself, passively.
"""

from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from io_facts import netdev, pci, skeleton, tty, v4l  # noqa: E402

FIXTURE = ROOT / "tests" / "data" / "sysfs-rack-fixture.json"
SOURCES = {"usb": skeleton.usb_devices, "pci": pci.devices, "net": netdev.devices, "tty": tty.devices, "v4l": v4l.devices}
RACK_PC = "bam-GPU"  # WHY a hostname gate: the live smoke asserts this rack's own cameras and CAN adapter
C920E = "pci0000:00/0000:00:08.3/0000:10:00.0/usb7/7-1/7-1.1"


@pytest.fixture(scope="module")
def machine(tmp_path_factory) -> Path:
    return skeleton.fake_root(FIXTURE, tmp_path_factory.mktemp("machine"))


def by_id(nodes: list[dict]) -> dict[str, dict]:
    return {item["id"]: item for item in nodes}


@pytest.fixture
def no_device_opens(monkeypatch):
    """Any open() of a /dev path fails the test: sources may list and readlink device nodes, never open one."""

    opened: list[str] = []

    def guard(real):
        def checked(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)) and "/dev/" in os.fsdecode(file):
                opened.append(os.fsdecode(file))
                raise AssertionError(f"a passive source opened a device node: {os.fsdecode(file)}")
            return real(file, *args, **kwargs)

        return checked

    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(io, "open", guard(io.open))
    monkeypatch.setattr(os, "open", guard(os.open))
    return opened


# ----- the frozen shape --------------------------------------------------------------------------
def test_every_source_returns_the_frozen_dict_shape(machine):
    for bus, read in SOURCES.items():
        nodes = read(machine)
        assert nodes, bus
        assert len({item["id"] for item in nodes}) == len(nodes), f"{bus}: ids not unique"
        for item in nodes:
            assert tuple(item) == skeleton.NODE_KEYS
            assert item["bus"] == bus and item["id"].startswith(f"{bus}:")
            assert item["path"] and "/" not in item["path"] and item["label"]
            assert item["parent"] is None or isinstance(item["parent"], str)
            assert all(item[key] is None or isinstance(item[key], str) for key in ("vendor", "product", "serial"))
            assert isinstance(item["attrs"], dict)
            assert importlib.util.find_spec(item["attrs"]["source"].split(" ")[0])  # provenance names a real module
            json.dumps(item)


def test_a_missing_sysfs_is_an_empty_source_not_an_error(tmp_path):
    assert all(read(tmp_path) == [] for read in SOURCES.values())


# ----- PCI ---------------------------------------------------------------------------------------
def test_pcie_frame_grabber_is_flagged_with_its_parent_bridge_link_and_error_counters(machine):
    grabber = by_id(pci.devices(machine))["pci:0000:01:00.0"]
    attrs = grabber["attrs"]
    assert attrs["likely_capture_card"] and attrs["kind"] == "capture"
    assert attrs["capture_card_reasons"] == ["vendor 11ec = Coreco / Teledyne DALSA (Xtium, X64), a frame grabber / capture card maker"]
    assert grabber["label"] == "Likely frame grabber · Coreco Inc" and grabber["vendor"] == "Coreco Inc" and grabber["product"] is None
    assert grabber["parent"] == "pci:0000:00:01.1" and attrs["parent_bridge"] == "0000:00:01.1" and attrs["root_bus"] == "0000:00"
    assert attrs["driver"] is None  # no grabber SDK module loaded: a fact to show, not a reason to hide the card
    assert attrs["link"] == {"speed": "5.0 GT/s PCIe", "width": 4, "max_speed": "5.0 GT/s PCIe", "max_width": 4}
    assert not attrs["link_narrower_than_max"]
    assert attrs["aer"] == {"correctable": 3, "nonfatal": 0, "fatal": 0}
    assert attrs["hardware_id"] == "pci:11ec:1000:11ec:1000@0000:01:00.0"


def test_capture_card_with_a_v4l_node_and_a_link_trained_narrower_than_it_can(machine):
    functions = by_id(pci.devices(machine))
    card = functions["pci:0000:08:00.0"]
    assert card["product"] == "Pro Capture Endpoint" and card["label"] == "Pro Capture Endpoint"
    assert card["attrs"]["video_nodes"] == ["video20"] and card["attrs"]["kind"] == "capture"
    assert "class 0400: multimedia video controller" in card["attrs"]["capture_card_reasons"]
    assert card["attrs"]["link_narrower_than_max"]  # x1 of x4
    assert card["attrs"]["upstream"] == ["0000:00:02.1", "0000:03:00.0", "0000:04:03.0"]
    bridge = functions["pci:0000:04:03.0"]  # its downstream port: plumbing, never the capture card itself
    assert bridge["attrs"]["kind"] == "bridge" and bridge["attrs"]["video_nodes"] == [] and not bridge["attrs"]["likely_capture_card"]


def test_audio_coprocessor_controllers_and_nics_are_not_capture_cards(machine):
    functions = by_id(pci.devices(machine))
    for bdf in ("0000:0f:00.5", "0000:0d:00.0", "0000:06:00.0", "0000:00:18.3"):
        assert not functions[f"pci:{bdf}"]["attrs"]["likely_capture_card"], bdf
    assert functions["pci:0000:0f:00.5"]["attrs"]["kind"] == "multimedia"  # AMD ACP, class 0480


def test_pci_names_classes_and_owned_buses_come_from_pci_ids_and_sysfs(machine):
    functions = by_id(pci.devices(machine))
    xhci = functions["pci:0000:0d:00.0"]["attrs"]
    assert (xhci["class"], xhci["subclass"], xhci["prog_if"], xhci["kind"]) == ("Serial bus controller", "USB controller", "XHCI", "usb_controller")
    assert xhci["usb_buses"] == ["usb:usb1"] and functions["pci:0000:0f:00.4"]["attrs"]["usb_buses"] == ["usb:usb5", "usb:usb6"]
    nic = functions["pci:0000:06:00.0"]
    assert (nic["vendor"], nic["product"]) == ("Intel Corporation", "I210 Gigabit Network Connection")
    assert nic["attrs"]["subsystem"] == "Ethernet Server Adapter I210-T1" and nic["attrs"]["net"] == ["enp6s0"]
    assert nic["attrs"]["driver"] == "igb" and nic["parent"] == "pci:0000:04:01.0"


def test_capture_card_rules_by_vendor_class_and_driver():
    assert not pci.capture_card_reasons("102b", 0x030000, "mgag200", [])  # Matrox server VGA: a display chip
    assert pci.capture_card_reasons("102b", 0x048000, None, [])  # Matrox Imaging grabber
    assert pci.capture_card_reasons("abcd", 0x118000, "cxpframegrabber", []) == ["driver cxpframegrabber is a grabber driver"]
    assert not pci.capture_card_reasons("1022", 0x048000, "snd_pci_acp6x", [])  # class 0480 alone is not enough
    assert not pci.capture_card_reasons("1805", 0x060400, "pcieport", ["video0"])  # bridges never


# ----- network -----------------------------------------------------------------------------------
def test_only_physical_nics_each_on_its_pci_or_usb_device(machine):
    nics = by_id(netdev.devices(machine))
    assert set(nics) == {"net:enp6s0", "net:enp7s0", "net:enx00e04c680001"}  # lo, docker0, veth skipped
    assert nics["net:enp6s0"]["parent"] == "pci:0000:06:00.0"
    dongle = nics["net:enx00e04c680001"]
    assert dongle["parent"] == "usb:7-2" and dongle["attrs"]["interface"] == "7-2:1.0"
    attrs = dongle["attrs"]
    assert (attrs["operstate"], attrs["carrier"], attrs["speed_mbps"], attrs["driver"]) == ("down", False, None, "r8152")
    assert attrs["hardware_id"] == "net:00:e0:4c:68:00:01" and attrs["ethercat"] is None and dongle["label"] == "enx00e04c680001"


def test_igh_master_marks_the_nic_its_config_names_and_nothing_is_scanned(machine):
    nic = by_id(netdev.devices(machine))["net:enp6s0"]
    ethercat = nic["attrs"]["ethercat"]
    assert nic["label"] == "enp6s0 · EtherCAT segment (not scanned)"
    assert ethercat["segment"] and ethercat["scanned"] is False and ethercat["label"] == netdev.SEGMENT_LABEL
    [master] = ethercat["masters"]
    assert master["kind"] == "igh" and "MAC 00:1b:21:aa:bb:01 is the master's configured device" in master["evidence"]
    assert nic["attrs"]["speed_mbps"] == 100 and nic["attrs"]["statistics"]["rx_crc_errors"] == 0


def test_pysoem_raw_socket_marks_its_nic_and_names_the_process(machine):
    nic = by_id(netdev.devices(machine))["net:enp7s0"]
    [master] = nic["attrs"]["ethercat"]["masters"]
    assert (master["kind"], master["pid"], master["process"]) == ("process", 4242, "pysoem_master.py")
    assert "its EtherType 0x88A4 socket is bound to enp7s0 (ifindex 3)" in master["evidence"]


def test_a_master_named_process_without_a_nic_is_kept_and_a_command_line_mention_is_not_a_master(machine):
    masters = netdev.ethercat_masters(machine)
    by_pid = {master["pid"]: master for master in masters}
    assert by_pid[5151]["process"] == "EcMaster" and by_pid[5151]["nics"] == []  # running, NIC unknown
    assert 6000 not in by_pid  # `python3 -m pytest -k ethercat` only mentions the word
    assert [(master["kind"], master["pid"], master["nics"]) for master in masters] == [
        ("igh", None, ["enp6s0"]), ("process", 4242, ["enp7s0"]), ("process", 5151, [])]


def test_process_names_are_comm_executable_and_the_interpreted_script():
    assert netdev._names("python3", ["python3", "-u", "/x/pysoem_master.py", "eth0"]) == ["python3", "python3", "pysoem_master.py"]
    assert netdev._names("python3", ["python3", "-m", "pytest", "-k", "ethercat"]) == ["python3", "python3", "pytest"]
    assert netdev._names("simple_test", ["/opt/soem/simple_test", "enp7s0"]) == ["simple_test", "simple_test"]


# ----- serial ------------------------------------------------------------------------------------
def test_ttyacm_hangs_off_its_usb_device_with_by_id_and_custom_udev_links(machine):
    acm = by_id(tty.devices(machine))["tty:1-3:1.0"]
    attrs = acm["attrs"]
    assert acm["parent"] == "usb:1-3" and acm["label"] == "/dev/ttyACM0" and acm["path"] == "ttyACM0"
    assert attrs["by_id"] == ["/dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00"]
    assert attrs["by_path"] == ["/dev/serial/by-path/pci-0000:0d:00.0-usb-0:3:1.0"] and attrs["other_links"] == ["/dev/fdcanusb"]
    assert (attrs["driver"], attrs["interface"], attrs["major_minor"]) == ("cdc_acm", "1-3:1.0", "166:0")


def test_usb_serial_tty_belongs_to_the_adapter_and_by_id_falls_back_to_dev(machine):
    ftdi = by_id(tty.devices(machine))["tty:1-4:1.0"]  # no udev record for it: by-id comes from /dev/serial
    assert ftdi["parent"] == "usb:1-4" and ftdi["attrs"]["driver"] == "ftdi_sio"
    assert ftdi["attrs"]["by_id"] == ["/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10K1234-if00-port0"]
    assert ftdi["attrs"]["interface_name"] == "FT232R USB UART"


def test_placeholder_and_virtual_ttys_are_skipped_and_a_real_com_port_is_kept(machine):
    ttys = by_id(tty.devices(machine))
    assert set(ttys) == {"tty:1-3:1.0", "tty:1-4:1.0", "tty:ttyS1"}  # ttyS0 (type 0), console, ptmx skipped
    com = ttys["tty:ttyS1"]
    assert com["parent"] is None and com["attrs"]["uart"] == {"type": 4, "port": "0x2F8", "irq": 3, "uartclk": 1843200}


# ----- video4linux -------------------------------------------------------------------------------
def test_video_nodes_attach_to_the_camera_never_to_a_hub_above_it(machine):
    """V6's known bug: path-prefix matching gave both hubs above the C920e its video nodes."""

    nodes = v4l.devices(machine)
    webcam = [item for item in nodes if item["attrs"]["name"] in ("video12", "video13")]
    assert [item["parent"] for item in webcam] == ["usb:7-1.1.1", "usb:7-1.1.1"]
    hubs = {item["id"] for item in skeleton.usb_devices(machine) if item["attrs"].get("is_hub")}
    assert hubs == {"usb:7-1", "usb:7-1.1"} and not any(item["parent"] in hubs for item in nodes)


def test_d405_nodes_carry_udevs_capabilities_and_stable_interface_ids(machine):
    nodes = by_id(v4l.devices(machine))
    depth, metadata = nodes["v4l:6-1:1.0#0"], nodes["v4l:6-1:1.0#1"]
    assert depth["parent"] == metadata["parent"] == "usb:6-1"
    assert (depth["label"], depth["attrs"]["capabilities"]) == ("/dev/video0 · capture", ["capture"])
    assert (metadata["label"], metadata["attrs"]["capabilities"]) == ("/dev/video1", [])
    assert depth["product"] == "Intel(R) RealSense(TM) Depth Ca"  # the kernel's 31-character card name, as is
    assert depth["attrs"]["interface_name"] == "Intel(R) RealSense(TM) Depth Camera 405  Depth"
    assert depth["attrs"]["by_path"] == ["/dev/v4l/by-path/pci-0000:0f:00.4-usb-0:1:1.0-video-index0"]


def test_capture_card_video_node_hangs_off_the_pci_function(machine):
    node = by_id(v4l.devices(machine))["v4l:0000:08:00.0#0"]
    assert node["parent"] == "pci:0000:08:00.0" and node["attrs"]["device_bus"] == "pci" and node["label"] == "/dev/video20 · capture"


# ----- the merged skeleton -----------------------------------------------------------------------
def test_skeleton_is_one_tree_under_the_host_parents_first_and_deterministic(machine):
    nodes = skeleton.skeleton(machine)
    host = nodes[0]
    assert (host["id"], host["bus"], host["label"], host["parent"]) == ("host", "host", "rack-fixture", None)
    assert host["attrs"]["notes"] == [] and host["attrs"]["os"] == "Ubuntu 24.04.5 LTS"
    seen: set[str] = set()
    for item in nodes:
        assert item["id"] not in seen
        assert item is host or item["parent"] in seen
        assert tuple(item) == skeleton.NODE_KEYS and item["attrs"]["source"].startswith("io_facts.")
        seen.add(item["id"])
    assert skeleton.skeleton(machine) == nodes
    top = [item["id"] for item in nodes if item["parent"] == "host"]
    assert top == ["usb:usb1", "usb:usb5", "usb:usb6", "usb:usb7", "pci:root-0000:00", "tty:on-board"]


def test_skeleton_folds_pci_bridges_but_keeps_the_chain_and_can_keep_them(machine):
    folded = by_id(skeleton.skeleton(machine))
    assert not [item for item in folded.values() if item["attrs"].get("kind") == "bridge"]
    assert folded["pci:0000:01:00.0"]["parent"] == "pci:root-0000:00"
    assert folded["pci:0000:08:00.0"]["attrs"]["upstream"] == ["0000:00:02.1", "0000:03:00.0", "0000:04:03.0"]
    assert folded["net:enp6s0"]["parent"] == "pci:0000:06:00.0" and folded["v4l:0000:08:00.0#0"]["parent"] == "pci:0000:08:00.0"
    kept = by_id(skeleton.skeleton(machine, keep_bridges=True))
    assert kept["pci:0000:01:00.0"]["parent"] == "pci:0000:00:01.1"
    assert kept["pci:0000:06:00.0"]["parent"] == "pci:0000:04:01.0" and kept["pci:0000:00:01.1"]["parent"] == "pci:root-0000:00"


def test_usb_buses_name_their_controller_and_devices_their_port(machine):
    nodes = by_id(skeleton.skeleton(machine))
    bus6 = nodes["usb:usb6"]
    assert bus6["label"] == "USB bus 6 · 10 Gb/s" and bus6["attrs"]["controller"] == "pci:0000:0f:00.4"
    assert bus6["attrs"]["device_count"] == 1 and nodes["usb:usb5"]["attrs"]["device_count"] == 0
    d405 = nodes["usb:6-1"]
    assert d405["parent"] == "usb:usb6" and d405["attrs"]["kind"] == "camera" and d405["attrs"]["speed_mbps"] == 5000
    assert d405["attrs"]["hardware_id"] == "usb:8086:0b5b:219323070276" and d405["serial"] == "219323070276"
    port = d405["attrs"]["hub_port"]
    assert (port["name"], port["peer"], port["connect_type"]) == ("usb6-port1", "usb5-port1", "hotplug")
    assert port["location_firmware"] == {"panel": "back", "vertical_position": "center", "horizontal_position": "left"}
    assert nodes["usb:1-4"]["attrs"]["hub_port"]["over_current_count"] == 1
    hub = nodes["usb:7-1"]
    assert (hub["vendor"], hub["attrs"]["vendor_source"], hub["attrs"]["kind"], hub["attrs"]["ports"]) == ("Terminus Technology Inc.", "usb.ids", "hub", 4)
    assert nodes["usb:7-1.1.1"]["parent"] == "usb:7-1.1" and nodes["usb:7-1.1.1"]["vendor"] == "Logitech, Inc."
    assert nodes["tty:1-3:1.0"]["parent"] == "usb:1-3" and nodes["tty:ttyS1"]["parent"] == "tty:on-board"


def test_ethercat_masters_live_on_the_host_even_without_a_nic(machine):
    host = skeleton.skeleton(machine)[0]
    masters = {master["process"] or master["kind"]: master["nics"] for master in host["attrs"]["ethercat_masters"]}
    assert masters == {"igh": ["enp6s0"], "pysoem_master.py": ["enp7s0"], "EcMaster": []}


def test_a_failing_source_is_a_note_and_its_children_move_to_an_on_board_group(machine, monkeypatch):
    def broken(root):
        raise RuntimeError("sysfs went away")

    monkeypatch.setattr(pci, "devices", broken)
    nodes = by_id(skeleton.skeleton(machine))
    assert nodes["host"]["attrs"]["notes"] == ["pci: skipped (RuntimeError: sysfs went away)"]
    assert nodes["net:enp6s0"]["parent"] == "net:on-board" and nodes["net:enp6s0"]["attrs"]["owner_not_in_tree"] == "pci:0000:06:00.0"
    assert nodes["usb:6-1"]["parent"] == "usb:usb6"  # the other sources are untouched


def test_sources_never_open_a_device_node(machine, no_device_opens):
    for read in SOURCES.values():
        read(machine)
    skeleton.skeleton(machine)
    assert no_device_opens == []


def test_a_low_speed_usb_device_does_not_break_the_usb_source(tmp_path):
    # WHY: io_facts.usb once parsed sysfs speed with int(), so one 1.5 Mb/s keyboard blanked the whole USB source
    spec = json.loads(FIXTURE.read_text())
    spec["devices"][f"{C920E}/7-1.1.3"] = {"idVendor": "413c", "idProduct": "2113", "bDeviceClass": "00", "speed": "1.5",
                                          "version": " 1.10", "busnum": "7", "devnum": "6", "product": "Keyboard"}
    nodes = by_id(skeleton.usb_devices(skeleton.fake_root(spec, tmp_path)))
    assert nodes["usb:7-1.1.3"]["attrs"]["speed_mbps"] == 1.5


# ----- live, passive -----------------------------------------------------------------------------
@pytest.mark.skipif(socket.gethostname() != RACK_PC, reason="reads the rack PC's own sysfs")
def test_live_skeleton_finds_both_d405s_and_the_fdcanusb_without_opening_anything(no_device_opens):
    nodes = skeleton.skeleton()
    assert nodes[0]["attrs"]["notes"] == [], nodes[0]["attrs"]["notes"]
    d405s = [item for item in nodes if item["bus"] == "usb" and item["attrs"].get("usb_id") == "8086:0b5b"]
    adapters = [item for item in nodes if item["bus"] == "usb" and item["attrs"].get("usb_id") == "0483:5740"]
    assert len(d405s) == 2 and adapters
    for camera in d405s:
        assert [item for item in nodes if item["bus"] == "v4l" and item["parent"] == camera["id"]], camera["id"]
    for adapter in adapters:
        [port] = [item for item in nodes if item["bus"] == "tty" and item["parent"] == adapter["id"]]
        assert f"/dev/serial/by-id/usb-mjbots_fdcanusb_{adapter['serial']}-if00" in port["attrs"]["by_id"]
    hubs = {item["id"] for item in nodes if item["attrs"].get("is_hub")}
    assert not [item for item in nodes if item["bus"] == "v4l" and item["parent"] in hubs]
    assert no_device_opens == []
