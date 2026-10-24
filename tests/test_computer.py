"""The computer plugin (plugins/computer) and the host's support for it: a plugin that matches THE host.

Readings are fakes (FakeProbe) except one test that runs the real plugin, read-only, in its own env under an audit
hook to prove it opens no /dev node. Nothing here touches the rack.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_host_support import ROOT, SetClock, FakeOs, host_dict, make_host, standard_skeleton, usb_bus, usb_device, write_plugin

from contract.record import ERROR, OK, UNKNOWN, WARN, validate
from io_diagnostics.host import Host
from io_diagnostics.plugins import load_plugins, match_devices, parse_match
from io_diagnostics.tree import HOST_PATH, PortHistory, os_statuses

PLUGIN_DIR = ROOT / "plugins" / "computer"
sys.path.insert(0, str(PLUGIN_DIR))
import computer_diagnostics as computer  # noqa: E402


# ----- a fake computer ---------------------------------------------------------------------------------------------
class FakeProbe:
    """Every reading the plugin takes, as plain data a test overrides."""

    def __init__(self, **override):
        self.cores = [10.0, 20.0, 30.0, 40.0]
        self.load = (1.0, 0.8, 0.5)
        self.memory = {"total": 64 * computer.GIB, "available": 40 * computer.GIB, "percent": 37.5, "used": 20 * computer.GIB,
                       "buffers": 1 * computer.GIB, "cached": 10 * computer.GIB}
        self.swap = {"total": 8 * computer.GIB, "used": 1 * computer.GIB, "percent": 12.5}
        self.partitions = [{"device": "/dev/nvme0n1p2", "mountpoint": "/", "fstype": "ext4"},
                           {"device": "/dev/loop3", "mountpoint": "/snap/core22/1", "fstype": "squashfs"},
                           {"device": "tmpfs", "mountpoint": "/run", "fstype": "tmpfs"},
                           {"device": "archive/datasets", "mountpoint": "/archive/datasets", "fstype": "zfs"},
                           {"device": "archive/datasets", "mountpoint": "/mnt/archive-datasets", "fstype": "zfs"}]
        self.usage = {"/": (1000, 500), "/archive/datasets": (4000, 3000)}  # mountpoint -> (total, free)
        self.temps = {"k10temp": [{"label": "Tctl", "current": 60.0, "high": None, "critical": None}],
                      "nvme": [{"label": "Composite", "current": 40.0, "high": 81.85, "critical": 84.85},
                               {"label": "Sensor 1", "current": 41.0, "high": 65261.85, "critical": 65261.85}]}
        self.gpu = {"gpus": [{"index": "0", "name": "NVIDIA GeForce RTX 4080 SUPER", "uuid": "GPU-1", "bus_id": "00000000:01:00.0",
                              "utilization": 30.0, "memory_used_mib": 2000.0, "memory_total_mib": 16376.0, "temperature": 45.0,
                              "max_operating": 90.0, "slowdown": 96.0, "shutdown": 101.0}]}
        self.clock_reading = {"source": "systemd-timesyncd", "synced": True, "ntp_enabled": True, "offset_s": 0.0015,
                              "server": "ntp.ubuntu.com"}
        self.boot = 1000.0
        self.counters = [{"enp6s0": (0, 0), "lo": (0, 0)}, {"enp6s0": (12_500_000, 2_500_000), "lo": (9, 9)}]
        self.stats = {"enp6s0": (True, 1000.0), "lo": (True, 0.0), "wlp5s0": (False, 0.0), "veth1": (True, 10000.0)}
        self.buses = [{"bus": 1, "speed_mbps": 480.0, "claimed_bps": 4_800_000.0, "endpoints": 2},
                      {"bus": 2, "speed_mbps": 10000.0, "claimed_bps": 0.0, "endpoints": 0}]
        self.clock_now, self.mono = 1000.0 + 90061, 0.0
        self.__dict__.update(override)

    def time(self):
        return self.clock_now

    def monotonic(self):
        self.mono += 1.0  # read at the window's start and end: a 1 s sample
        return self.mono

    def cpu_percent(self, interval):
        return self.cores

    def cpu_counts(self):
        return len(self.cores), len(self.cores) // 2

    def loadavg(self):
        return self.load

    def virtual_memory(self):
        return self.memory

    def swap_memory(self):
        return self.swap

    def disk_partitions(self):
        return self.partitions

    def disk_usage(self, mountpoint):
        total, free = self.usage[mountpoint]
        return {"total": total, "used": total - free, "free": free}

    def temperatures(self):
        return self.temps

    def gpus(self):
        return self.gpu

    def clock(self):
        return self.clock_reading

    def boot_time(self):
        return self.boot

    def net_counters(self):
        return self.counters.pop(0) if len(self.counters) > 1 else self.counters[0]

    def net_stats(self):
        return self.stats

    def usb_buses(self):
        return self.buses


def run(**override) -> dict[str, object]:
    statuses = computer.diagnose(FakeProbe(**override), computer.Options(), host="rig")
    for status in statuses:
        rooted = dataclasses.replace(status, path=HOST_PATH)  # the host validates after rooting "" at the device row
        assert validate(rooted) == [], (status.name, validate(rooted))
        assert status.path == "" and status.expected_period_s == computer.PERIOD_S and status.source == "computer"
    return {status.name.removeprefix("rig: "): status for status in statuses}


def measure(status, key):
    return next(measurement for measurement in status.measurements if measurement.key == key)


# ----- OK / WARN / ERROR per status --------------------------------------------------------------------------------
def test_a_healthy_computer_is_quiet_and_every_number_is_a_measurement():
    found = run()
    assert set(found) == {"CPU Information", "RAM Information", "HD Usage", "Temperature CPU (k10temp)", "Temperature NVMe (nvme)",
                          "GPU 0 NVIDIA GeForce RTX 4080 SUPER", "NTP offset", "Uptime", "Network Usage", "USB Bandwidth"}
    assert {status.level for status in found.values()} == {OK}
    cpu = found["CPU Information"]
    assert cpu.message.startswith("CPU Average 25.0 percent") and measure(cpu, "CPU 3 Load").value == 40.0
    assert measure(cpu, "CPU Load Average").warn_high == 90 and measure(cpu, "load 15 min").value == 0.5
    ram = found["RAM Information"]
    assert {"RAM used", "RAM total", "buffers", "cached", "swap used", "swap total"} <= {m.key for m in ram.measurements}
    assert measure(ram, "RAM used").value == 24.0 and measure(ram, "RAM used").unit == "GiBy"
    assert found["Uptime"].uptime_s == 90061 and found["Uptime"].message.startswith("up 1 d 01:01")
    net = found["Network Usage"]  # 12.5 MB in 1 s = 100 Mbit/s; lo, a down Wi-Fi and a veth are not listed
    assert measure(net, "enp6s0 rx").value == 100.0 and measure(net, "enp6s0 tx").value == 20.0
    assert measure(net, "enp6s0 rx").warn_high == 900.0 and measure(net, "enp6s0 link speed").unit == "Mbit/s"
    assert not any(m.key.startswith(("lo ", "veth")) for m in net.measurements) and dict(net.values)["down"] == "wlp5s0"
    usb = found["USB Bandwidth"]
    assert measure(usb, "usb1 claimed").value == 4.8 and measure(usb, "usb1 claimed %").warn_high == 80.0
    assert measure(usb, "usb2 claimed %").warn_high == 90.0 and measure(usb, "usb2 capacity").value == 10000.0


def test_cpu_warns_on_the_average_not_one_pegged_core():
    assert run(cores=[100.0, 5.0, 5.0, 5.0])["CPU Information"].level == OK
    hot = run(cores=[95.0, 92.0, 96.0, 91.0])["CPU Information"]
    assert hot.level == WARN and hot.code == "CpuAverageHigh" and "exceeds 90 percent" in hot.message


def test_ram_warns_above_ros_90_percent():
    memory = {"total": 100 * computer.GIB, "available": 5 * computer.GIB, "percent": 95.0, "used": 95 * computer.GIB,
              "buffers": 0, "cached": 0}
    found = run(memory=memory)["RAM Information"]
    assert found.level == WARN and found.code == "RamAverageHigh" and measure(found, "RAM Load Average").level == WARN


def test_disks_skip_snaps_tmpfs_and_second_mounts_and_use_ros_free_percent_limits():
    found = run()["HD Usage"]
    assert dict(found.values)["mounts"] == "/ (ext4), /archive/datasets (zfs)"
    assert measure(found, "/ available").warn_low == 5 and measure(found, "/ available").error_low == 1
    low = run(usage={"/": (1000, 30), "/archive/datasets": (4000, 3000)})["HD Usage"]
    assert low.level == WARN and low.code == "LowDiskSpace" and low.message.startswith("Low Disk Space: / 3.0 % free")
    full = run(usage={"/": (1000, 5), "/archive/datasets": (4000, 3000)})["HD Usage"]
    assert full.level == ERROR and full.code == "VeryLowDiskSpace"


def test_temperatures_use_the_sensors_own_limits_and_defaults_only_without_them():
    found = run()
    nvme = found["Temperature NVMe (nvme)"]
    assert (measure(nvme, "Composite").warn_high, measure(nvme, "Composite").error_high) == (81.85, 84.85)
    assert (measure(nvme, "Sensor 1").warn_high, measure(nvme, "Sensor 1").error_high) == (85.0, 95.0)  # 65261 °C is a sentinel
    cpu_hot = run(temps={"k10temp": [{"label": "Tctl", "current": 92.0, "high": None, "critical": None}]})["Temperature CPU (k10temp)"]
    assert cpu_hot.level == WARN and cpu_hot.code == "HighTemperature" and cpu_hot.message.startswith("CPU Tctl 92 °C: High Temperature")
    assert "default" in cpu_hot.message
    nvme_hot = run(temps={"nvme": [{"label": "Composite", "current": 83.0, "high": 81.85, "critical": 84.85}]})["Temperature NVMe (nvme)"]
    assert nvme_hot.level == WARN and "its own limit" in nvme_hot.message
    critical = run(temps={"nvme": [{"label": "Composite", "current": 86.0, "high": 81.85, "critical": 84.85}]})["Temperature NVMe (nvme)"]
    assert critical.level == ERROR and critical.code == "CriticalTemperature"
    mixed = run(temps={"k10temp": [{"label": "Tctl", "current": None, "high": None, "critical": None},
                                   {"label": "Tccd1", "current": 90.0, "high": None, "critical": None}]})["Temperature CPU (k10temp)"]
    assert mixed.level == WARN and "Tccd1" in mixed.message  # a sensor with no reading never hides a hot one


def test_gpu_uses_its_own_limits_from_nvidia_smi():
    def gpu(temperature):
        return {"gpus": [{**FakeProbe().gpu["gpus"][0], "temperature": temperature}]}

    name = "GPU 0 NVIDIA GeForce RTX 4080 SUPER"
    ok = run()[name]
    assert ok.message == "30 % · 2000 of 16376 MiB · 45 °C" and (measure(ok, "temperature").warn_high, measure(ok, "temperature").error_high) == (90.0, 96.0)
    assert run(gpu=gpu(92.0))[name].level == WARN and run(gpu=gpu(97.0))[name].level == ERROR
    driver_gone = run(gpu={"error": "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver."})["GPU"]
    assert driver_gone.level == WARN and driver_gone.code == "GpuDriverNotAnswering"


def test_clock_follows_the_daemon_and_ros_5_s_error_offset():
    assert run()["NTP offset"].message.startswith("synchronized · offset +1.5 ms")
    unsynced = run(clock_reading={"source": "timedatectl", "synced": False, "ntp_enabled": False, "offset_s": None, "server": None})
    assert unsynced["NTP offset"].level == WARN and unsynced["NTP offset"].code == "ClockNotSynchronized"
    far = run(clock_reading={"source": "chrony", "synced": True, "ntp_enabled": True, "offset_s": -6.0, "server": "pool"})
    assert far["NTP offset"].level == ERROR and far["NTP offset"].code == "ClockOffsetTooLarge"


def test_network_and_usb_warn_against_their_own_capacity():
    saturated = run(counters=[{"enp6s0": (0, 0)}, {"enp6s0": (125_000_000, 0)}])["Network Usage"]  # 1000 Mbit/s on a 1 GbE
    assert saturated.level == WARN and saturated.code == "HighNetworkUsage"
    busy = run(buses=[{"bus": 3, "speed_mbps": 480.0, "claimed_bps": 400e6, "endpoints": 9}])["USB Bandwidth"]
    assert busy.level == WARN and busy.code == "UsbBandwidthHigh" and "usb3 83 % of 480 Mbit/s" in busy.message


def test_missing_sensors_read_not_available_never_an_error():
    found = run(temps={}, gpu={"missing": "no nvidia-smi on PATH"}, clock_reading=None, partitions=[], buses=[],
                stats={"lo": (True, 0.0)})
    assert {status.level for status in found.values()} == {OK}
    for name in ("Temperatures", "GPU", "NTP offset", "HD Usage", "USB Bandwidth", "Network Usage"):
        assert "not available" in found[name].message, name


def test_a_reading_that_throws_is_unknown_for_that_check_only():
    class Broken(FakeProbe):
        def loadavg(self):
            raise OSError("no /proc/loadavg")

    statuses = {status.name: status for status in computer.diagnose(Broken(), computer.Options(), host="rig")}
    assert statuses["rig: CPU Information"].level == UNKNOWN and statuses["rig: CPU Information"].code == "ReadFailed"
    assert statuses["rig: RAM Information"].level == OK


# ----- parsers of the tools' text ----------------------------------------------------------------------------------
def test_tool_output_parsers():
    assert computer.parse_offset_s(" +1.496ms") == pytest.approx(0.001496) and computer.parse_offset_s("-250us") == pytest.approx(-0.00025)
    gpus = computer.parse_nvidia_query("0, NVIDIA GeForce RTX 4080 SUPER, GPU-c6b9, 00000000:01:00.0, 34, 2234, 16376, 43\n")
    assert gpus[0]["temperature"] == 43.0 and gpus[0]["bus_id"] == "00000000:01:00.0"
    assert computer.parse_nvidia_query("0, X, U, 00000000:02:00.0, [N/A], 1, 2, [N/A]")[0]["utilization"] is None
    limits = computer.parse_nvidia_thresholds("GPU 00000000:01:00.0\n    Temperature\n        GPU Current Temp : 43 C\n"
                                              "        GPU Shutdown Temp                 : 101 C\n"
                                              "        GPU Slowdown Temp                 : 96 C\n"
                                              "        GPU Max Operating Temp            : 90 C\n")
    assert limits == {"00000000:01:00.0": {"shutdown": 101.0, "slowdown": 96.0, "max_operating": 90.0}}
    chrony = computer.parse_chrony_tracking("A29FC87B,ntp1.example,3,1700000000.1,-0.000012,0.0,0.0,1.0,0.0,0.0,0.01,0.001,64.0,Normal\n")
    assert chrony["synced"] and chrony["offset_s"] == pytest.approx(-0.000012) and chrony["server"] == "ntp1.example"
    assert computer.parse_interval_s("125us") == pytest.approx(125e-6) and computer.parse_interval_s("8ms") == pytest.approx(0.008)


def test_usb_claimed_bandwidth_sums_periodic_endpoints_from_sysfs(tmp_path):
    devices = tmp_path / "bus" / "usb" / "devices"

    def endpoint(interface: str, name: str, kind: str, packet: str, interval: str) -> None:
        folder = devices / interface / name
        folder.mkdir(parents=True)
        for file, text in (("type", kind), ("wMaxPacketSize", packet), ("interval", interval)):
            (folder / file).write_text(text + "\n")

    (devices / "usb1").mkdir(parents=True)
    (devices / "usb1" / "speed").write_text("480\n")
    endpoint("1-0:1.0", "ep_81", "Interrupt", "0004", "256ms")  # the root hub's own: not on the wire
    endpoint("1-3:1.0", "ep_81", "Interrupt", "0040", "1ms")  # 64 B / 1 ms = 0.512 Mbit/s
    endpoint("1-3:1.0", "ep_02", "Bulk", "0200", "0ms")  # bulk claims nothing
    endpoint("1-4:1.1", "ep_83", "Isoc", "1400", "125us")  # 0x1400: 1024 B x 3 transactions / 125 us = 196.608 Mbit/s
    buses = computer.read_usb_buses(tmp_path)
    assert buses == [{"bus": 1, "claimed_bps": pytest.approx(512_000 + 196_608_000), "endpoints": 2, "speed_mbps": 480.0}]


# ----- the plugin opens no device node -----------------------------------------------------------------------------
def test_with_fake_readings_the_plugin_opens_nothing_under_dev():
    opened = []

    def hook(event, args):
        if event == "open" and args and isinstance(args[0], str):
            opened.append(args[0])

    sys.addaudithook(hook)  # audit hooks cannot be removed; this one only records into its own list
    computer.diagnose(FakeProbe(), computer.Options(), host="rig")
    assert not [path for path in opened if path.startswith("/dev/")]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed: the plugin's own env cannot run")
def test_the_real_plugin_in_its_env_opens_no_device_node():
    """The one real run in the suite: read-only system stats (psutil, sysfs, nvidia-smi / timedatectl subprocesses)."""

    completed = subprocess.run(["uv", "run", "--quiet", "--project", str(PLUGIN_DIR), "python",
                                str(ROOT / "tests" / "fixtures" / "computer_audit.py")],
                               capture_output=True, text=True, timeout=120,
                               env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"})
    audit = json.loads(completed.stderr.strip().splitlines()[-1])
    assert completed.returncode == 0 and audit["dev_opens"] == [], (completed.returncode, audit, completed.stderr[-400:])
    lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert lines and all(line["record"] == "status" and line.get("path", "") == "" for line in lines)  # "" = the host row
    assert {line["name"].split(": ", 1)[1] for line in lines} >= {"CPU Information", "RAM Information", "HD Usage", "Uptime"}


def test_describe_and_the_declaration_match_the_host():
    assert computer.describe()["matches"] == ["host"] and computer.describe()["actions"] == []
    specs, problems = load_plugins(ROOT / "plugins.d")
    spec = next(spec for spec in specs if spec.name == "computer")
    assert spec.enabled and spec.period_s == computer.PERIOD_S and [match.text() for match in spec.matches] == ["host:*"]
    assert spec.cwd == PLUGIN_DIR and not [problem for problem in problems if problem.startswith("computer.toml")]


# ----- host support: a plugin that matches THE host ----------------------------------------------------------------
def test_host_match_sees_only_the_host_and_device_globs_never_see_it():
    host = {**host_dict(), "functions": []}
    device = usb_device("9-1", "1234", "abcd", "SER")
    assert parse_match("host").accepts(host) and parse_match("host:rig").accepts(host) and not parse_match("host:other").accepts(host)
    assert not parse_match("host").accepts(device)
    assert not parse_match("usb:*:*").accepts(host) and not parse_match("by-id:*").accepts(host)
    assert parse_match({"bus": "host"}).text() == "host:*"


def test_the_computer_plugin_attaches_to_the_host_row(tmp_path):
    host = make_host(tmp_path, standard_skeleton(), [("computer", "hot_computer", ["host"])])
    host.run_all_once()
    assert [job.device_path for job in host.jobs.values()] == [HOST_PATH]
    state = host.state()
    assert state["tree"]["path"] == HOST_PATH and state["tree"]["own"]["level"] == ERROR
    assert state["tree"]["message"] == "CPU Tctl 99 °C: Critical Temperature"
    detail = host.node(HOST_PATH)
    names = {check["name"] for check in detail["checks"]}
    assert {"rig: Temperature CPU (k10temp)", "rig: RAM Information", "computer: plugin run", "rig: USB/PCIe links"} <= names
    assert state["summary"]["attention"][0]["path"] == HOST_PATH and state["summary"]["devices"] == 0


def test_a_failed_computer_never_swallows_the_problems_below_it(tmp_path):
    """summarize collapses problems under a failed ancestor; the Computer's own ERROR must stay one cause beside them."""

    skeleton = standard_skeleton(usb_device("9-1", "1234", "abcd", "SER"))
    host = make_host(tmp_path, skeleton, [("computer", "hot_computer", ["host"]), ("spider", "spider", ["usb:1234:abcd"])])
    host.run_all_once()
    state = host.state()
    causes = [item["path"] for item in state["summary"]["attention"]]
    assert HOST_PATH in causes and any(path.endswith("/leg3") for path in causes), causes
    assert state["summary"]["level"] == ERROR and state["summary"]["counts"] == {"error": 2, "warn": 1}
    leg = next(path for path in causes if path.endswith("/leg3"))
    assert host.node(leg)["node"]["knock_on_of"] is None


def test_without_a_computer_plugin_the_host_stays_a_grouping_row(tmp_path):
    host = make_host(tmp_path, standard_skeleton(usb_device("9-1", "1234", "abcd", "SER")), [("spider", "spider", ["usb:1234:abcd"])])
    host.run_all_once()
    tree = host.state()["tree"]
    assert tree["own"]["level"] is None and tree["level"] == ERROR and host.node(HOST_PATH)["checks"] == []


def test_a_host_job_owns_the_host_row_only(tmp_path):
    host = make_host(tmp_path, standard_skeleton(usb_device("9-1", "1234", "abcd", "SER")), [("computer", "hot_computer", ["host"])])
    host.run_all_once()
    device_path = host.layer.device_paths["usb:9-1"]
    assert host._owning_job(HOST_PATH, host.view().merged.entities[HOST_PATH]).spec.name == "computer"
    assert host._owning_job(device_path, host.view().merged.entities[device_path]) is None


def _links(host: Host) -> object:
    return next(status for status in host.view().merged.statuses if status.name.endswith(": USB/PCIe links"))


def test_links_summary_names_unhappy_links_and_counts_each_once(tmp_path):
    camera = usb_device("9-2", "8086", "0b5b", "CAM", label="RealSense D405")
    camera["attrs"].update(speed_mbps=480, usb_version=" 3.20")
    pci = {"bus": "pci", "id": "pci:0000:01:00.0", "path": "pci-0000-01-00.0", "label": "Frame grabber", "vendor": None,
           "product": None, "serial": None, "parent": "host",
           "attrs": {"kind": "device", "link": {"width": 1, "max_width": 4, "speed": "8 GT/s", "max_speed": "8 GT/s"},
                     "link_narrower_than_max": True, "driver": "x"}}
    skeleton = [host_dict(), usb_bus(9), usb_device("9-1", "1234", "abcd", "SER"), camera, pci]
    host = make_host(tmp_path, skeleton, [("computer", "hot_computer", ["host"])])
    host.run_all_once()
    links = _links(host)
    assert links.level == WARN and links.code == "UsbLinkDowngraded"  # nobody else says the D405 runs at USB 2
    assert links.message.startswith("2 USB ports · 1 PCIe link · 2 unhappy: Frame grabber: trained narrower")
    assert "RealSense D405 on 9-2: a USB 3.20 device linked at USB 2 high-speed · 480 Mb/s" in links.message
    assert dict((m.key, m.value) for m in links.measurements) == {"usb ports": 2, "pcie links": 1, "unhappy links": 2}
    # a flapping port reports on its own row: the Computer's line names it but stays OK
    host2 = make_host(tmp_path / "b", [host_dict(), usb_bus(9), usb_device("9-1", "1234", "abcd", "SER")],
                      [("computer", "hot_computer", ["host"])])
    host2.refresh()
    host2.usb.history["9-1"] = PortHistory(enumerations=4)
    host2.os_records = os_statuses(host2.layer, host2.usb.history, 1000.0, {"usb:9-1"})
    flapping = _links(host2)
    assert flapping.level == OK and "Port 9-1: 4 enumerations in the last hour (flapping)" in flapping.message
    assert flapping.message.endswith("(each counted on its own row)")


def test_no_links_line_without_a_plugin_on_the_host(tmp_path):
    host = make_host(tmp_path, standard_skeleton(usb_device("9-1", "1234", "abcd", "SER")), [])
    host.refresh()
    assert not [status for status in host.view().merged.statuses if status.name.endswith(": USB/PCIe links")]


def test_a_skeleton_without_a_host_node_still_has_a_matchable_host(tmp_path):
    directory = tmp_path / "plugins.d"
    write_plugin(directory, "computer", "hot_computer", ["host"])
    host = Host(plugins_dir=directory, clock=SetClock(1000.0), os_source=FakeOs([usb_bus(9)], hostname="bare"))
    host.run_all_once()
    assert host.state()["tree"]["own"]["level"] == ERROR
    specs, _ = load_plugins(directory)
    assert [device["id"] for _spec, device in match_devices(specs, host.layer.matchables())] == ["host"]
