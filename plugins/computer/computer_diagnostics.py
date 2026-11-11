"""IO diagnostics plugin: the Computer's own health, as statuses on THE host entity (Zach, 2026-10-23: "in our entity
chart we kinda have the Computer as an entity and those diagnostics can be associated with it there").

    uv run --project /home/bam/io-diagnostics/plugins/computer python -m computer_diagnostics describe
    uv run --project /home/bam/io-diagnostics/plugins/computer python -m computer_diagnostics diagnose --device host
    uv run --project /home/bam/io-diagnostics/plugins/computer python -m computer_diagnostics follow --device host

Boundary: docs/build-plan-v1.md "Plugin boundary" (plugins.d/computer.toml, ``matches = ["host"]``). Passive and
read-only: psutil (``/proc``, ``/sys``), sysfs text files (hwmon temperatures, fans and PWM duty; USB endpoints; the board's
DMI name), NVIDIA's own library NVML for the GPU (nvidia-ml-py's ``pynvml``, in-process), and read-only tools as
subprocesses (``timedatectl``, ``chronyc``; ``nvidia-smi`` only when the NVML binding is missing). It writes nothing,
controls no fan and needs no root. Below /dev it opens only the NVIDIA GPU's shared control nodes (``/dev/nvidiactl``,
``/dev/nvidia0``, ``/dev/nvidia-uvm``, ``/dev/nvidia-caps``: what nvidia-smi and every CUDA program open; strace-pinned in
tests/test_computer_nvml.py), so it can never hold a port, camera or CAN node the rack needs.

Statuses mirror ROS's stock ``diagnostic_common_diagnostics`` monitors (ros/diagnostics, ros2 branch; local copy
/home/bam/public_ws/src/diagnostics/diagnostic_common_diagnostics), so a record reads like ROS's: their names, their
measured values and their default thresholds where sensible; every departure says why beside it.

Each check is named ``<device>: <check>`` (Zach, 2026-11-10: "grouped by device on the computer. RAM, CPU, GPU, etc"): the
entity page folds a "<who>: <what>" name into a folder per who (web/detail/d6-rows.js ``folderPath``), so the Computer's list
reads CPU · RAM · Disks · GPU 0 · Board · Network, and the machine's own checks (clock, uptime, USB buses, processes) keep the
host's name. WHY the name and not part entities: an entity page lists only that entity's own records, so a status moved onto
a ``part_of`` entity leaves the Computer's list (tests/test_computer_parts.py). Every status stays on the Computer (path "").

| status | ROS source | level |
|---|---|---|
| ``CPU: CPU Information`` | cpu_monitor.py ``CpuTask`` | WARN above 90 % (``warning_percentage``), judged on the average |
| ``RAM: RAM Information`` | ram_monitor.py ``RamTask`` | WARN above 90 % (``warning_percentage``) |
| ``Disks: HD Usage`` | hd_monitor.py ``HDMonitor`` | WARN at 5 % free (``free_percent_low``), ERROR at 1 % (``free_percent_crit``) |
| ``<device>: Temperature <chip>`` | sensors_monitor.py | the sensor's own limits; a CPU without them: its published Tjmax; else none |
| ``<device>: Fans <chip>`` | (none in ROS) | each fan's RPM and duty; WARN only on the chip's own fan alarm |
| ``GPU <n>: <name>`` | (none in ROS) | the GPU's own limits from NVML; WARN when it says it slows itself for heat |
| ``NTP offset`` | ntp_monitor.py | WARN when the clock daemon says unsynchronized, ERROR beyond 5 s (``error_offset``) |
| ``Uptime`` | (none) | always OK |
| ``Network: Network Usage`` | (none in ROS 2) | WARN above 90 % of the NIC's own link speed |
| ``USB Bandwidth`` | (none) | WARN above the USB spec's periodic budget (80 % at 480 Mb/s, else 90 %) |

Every status with a limit says whose limit it is: the value ``threshold_source`` in plain words and, where a page states the
number, ``threshold_url`` (a datasheet, a spec, the ROS monitor's source line); the entity page shows both when the row is
opened (Zach, 2026-11-10: "go to the source for that threshold value ... link to like a datasheet"). A sensor nobody
publishes a limit for has none: it never warns on a number borrowed from another part (gigabyte_wmi's VRM read "Critical
Temperature" from the CPU's 95 °C until 2026-11-10).

Every number is also a ``Measurement`` (unit + limits) so a history view can draw it; per-core loads, per-NIC rates
and per-bus USB bandwidth are separate measurements (Zach's reference: the ROS 2 System Webview).

``follow`` (docs/build-plan-v1.md "Plugin boundary") is the same statuses once a second, forever, plus ``Processes``
(the top 10 by CPU and by memory, like GNOME System Monitor's Processes tab), so the host's history of the Computer
fills at 1 Hz (Zach, 2026-10-23: "something that feels a bit like system monitor with the live data"). It samples
CPU and network over the second between two ticks instead of sleeping in one, keeps ONE NVML session open (or, without
the binding, ONE ``nvidia-smi -lms 1000`` loop) instead of starting a reader every second, and re-reads the clock every 10 s
and the process table every 3 s; each status's ``expected_period_s`` is its own cadence.

Quiet when OK; a reading the machine does not have (no NVIDIA GPU, no hwmon sensor, no fan sensor, no clock tool) is an
OK "not available", never an error. A reading that throws is UNKNOWN ``ReadFailed`` with the reason.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]  # the io-diagnostics checkout: the record contract lives there
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.record import (ERROR, OK, RECORD_VERSION, SCHEMA, UNKNOWN, WARN, DiagnosticStatus,  # noqa: E402
                             KeyValue, Measurement, to_dict)

NAME = "computer"
VERSION = "0.1.0"
SOURCE = "computer"
# Must equal plugins.d/computer.toml period_s: every status promises it as expected_period_s.
PERIOD_S = 10.0
# The CPU and network sampling window: psutil.cpu_percent needs two readings, and each diagnose is a fresh process.
SAMPLE_S = 0.5
# ROS cpu_monitor.py / ram_monitor.py: warning_percentage = 90.
CPU_WARN_PERCENT = 90.0
RAM_WARN_PERCENT = 90.0
# ROS hd_monitor.py: FREE_PERCENT_LOW = 5 (WARN), FREE_PERCENT_CRIT = 1 (ERROR).
FREE_PERCENT_LOW = 5.0
FREE_PERCENT_CRIT = 1.0
# Only for a CPU sensor that reports no high / critical of its own (k10temp on a Ryzen reports none); the error yields to the
# CPU's published Tjmax when CPU_SPECS knows the model. WHY 85 / 95: a Ryzen 7000 boosts up to its 95 °C Tjmax by design;
# sustained above 85 °C is worth a look, past 95 °C it is over its own limit.
# WHY only a CPU (2026-11-10): these used to apply to EVERY sensor without a limit, so gigabyte_wmi's VRM reading (temp5, 93-97 °C
# under the 7900X's load on a board with no VRM heatsink) read "Critical Temperature" against the CPU's 95 °C. A sensor nobody
# publishes a limit for now has none.
TEMP_WARN_DEFAULT = 85.0
TEMP_ERROR_DEFAULT = 95.0
TEMP_SANE_MAX = 200.0  # a hwmon limit above this is a sentinel (nvme "Sensor 1" reports 65261.85 °C = 0xFFFF K, "no threshold set")
# ROS ntp_monitor.py: error_offset = 5000000 us. Its WARN offset (500 us) assumes a LAN NTP server; systemd-timesyncd
# over the internet sits at a few ms (bam-GPU: +1.5 ms, 132 ms delay), so WARN is the clock daemon's own verdict
# (NTPSynchronized / chrony leap status) instead; --ntp-warn-offset-s sets a number when wanted.
NTP_ERROR_OFFSET_S = 5.0
# Network: a NIC above this share of its own link speed is saturated (our default; ROS 2 has no network monitor).
NET_WARN_FRACTION = 0.9
# USB 2.0 spec §5.6.4 (isochronous) and §5.7.4 (interrupt): "no more than 90% of any frame be allocated for periodic
# (isochronous and interrupt) transfers for full-speed endpoints. High-speed endpoints can allocate at most 80% of a
# microframe" (re-read in usb_20.pdf on 2026-11-10; this comment used to say §5.8.4, which is bulk). Other speeds keep 90 %.
USB_PERIODIC_BUDGET = {480: 80.0}
USB_PERIODIC_BUDGET_DEFAULT = 90.0
# Mounts that are not a disk that can fill: snaps, RAM and kernel filesystems, network shares (statvfs can hang).
SKIP_FSTYPES = {"squashfs", "tmpfs", "devtmpfs", "ramfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2", "devpts",
                "autofs", "nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs", "fuse.portal", "fuse.gvfsd-fuse", "iso9660"}
VIRTUAL_NICS = ("lo", "veth", "docker", "br-", "virbr", "vnet")
# hwmon chip -> what a person calls it in the check's name (the spec's "CPU package, NVMe, GPU")
FRIENDLY_CHIPS = {"k10temp": "CPU", "coretemp": "CPU", "zenpower": "CPU", "cpu_thermal": "CPU", "nvme": "NVMe",
                  "amdgpu": "GPU", "radeon": "GPU", "nouveau": "GPU", "acpitz": "ACPI zone"}
READING_ORDER = {UNKNOWN: -1, OK: 0, WARN: 1, ERROR: 2}

# ----- the device a check is about: its name's "who", one folder per device on the entity page -------------------------------
CPU_CHIPS = ("k10temp", "coretemp", "zenpower", "cpu_thermal")
DISK_CHIPS = ("nvme", "drivetemp")
GPU_CHIPS = ("amdgpu", "radeon", "nouveau", "i915", "xe")
RAM_CHIPS = ("spd5118", "jc42", "ee1004")  # DDR5 / DDR4 module sensors
NIC_CHIPS = ("iwlwifi", "r8169", "igb", "igc", "ixgbe", "e1000e", "atlantic", "bnxt", "mlx5", "tg3")
BOARD_CHIPS = ("gigabyte_wmi", "acpitz", "asus", "it8", "nct6", "nct7", "w83", "f71", "sch5", "dell_smm", "thinkpad", "hp_wmi")
DEVICE_ORDER = {"CPU": 0, "GPU": 1, "RAM": 2, "Disks": 3, "Board": 4, "Network": 5}


def device_of(chip: dict) -> str:
    """The device an hwmon chip measures, as a person names it. WHY a chip nobody mapped keeps its own name: a folder named
    after an unknown chip is honest; one guessed onto "Board" would not be."""

    name, where = chip["name"], chip.get("device") or ""
    if name in CPU_CHIPS:
        return "CPU"
    if name in DISK_CHIPS:
        return "Disks"
    if name in GPU_CHIPS:
        return f"GPU ({name})"  # WHY not "GPU <n>": that is NVML's numbering, which an amdgpu chip is not part of
    if name in RAM_CHIPS:
        return "RAM"
    if "/ieee80211/" in where or "/net/" in where or re.search(r"_phy\d+$", name) or name.startswith(NIC_CHIPS):
        return "Network"
    if name.startswith(BOARD_CHIPS):
        return "Board"
    return name


def device_rank(device: str) -> int:
    return 1 if device.startswith("GPU") else DEVICE_ORDER.get(device, len(DEVICE_ORDER))


# ----- where each limit comes from (Zach, 2026-11-10: "go to the source for that threshold value ... link to like a datasheet") ---
# Every status with a limit carries the values threshold_source (whose number, in plain words) and, when a page states it,
# threshold_url; the entity page shows both in the opened row, the URL as a link (web/detail/d6.js). Each URL below was opened,
# and the number it is cited for read on it, on 2026-11-10.
ROS_MONITORS = "https://github.com/ros/diagnostics/blob/ros2/diagnostic_common_diagnostics/diagnostic_common_diagnostics/"
CPU_LIMIT_URL = ROS_MONITORS + "cpu_monitor.py#L54"  # CpuTask.__init__(self, warning_percentage=90, window=1)
RAM_LIMIT_URL = ROS_MONITORS + "ram_monitor.py#L86"  # node.declare_parameter('warning_percentage', 90)
HD_LIMIT_URL = ROS_MONITORS + "hd_monitor.py#L52"  # FREE_PERCENT_LOW = 5, FREE_PERCENT_CRIT = 1
NTP_LIMIT_URL = ROS_MONITORS + "ntp_monitor.py#L49"  # error_offset=5000000 (µs)
USB_SPEC_URL = "https://www.usb.org/document-library/usb-20-specification"  # usb_20.pdf §5.6.4, §5.7.4
NVML_HEADER_URL = "https://github.com/NVIDIA/go-nvml/blob/main/gen/nvml/nvml.h"  # nvmlTemperatureThresholds_t: what each GPU limit is
GIGABYTE_WMI_URL = "https://github.com/torvalds/linux/blob/master/drivers/platform/x86/gigabyte-wmi.c"  # temp*_input only: no limit, no label
# A part's published limit, by the model name the machine reports. WHY only parts checked by hand: a number on the screen must be
# the vendor's, never a guess; an unknown part keeps its sensor's own limit or RobotDx's default, and says which.
CPU_SPECS = {  # /proc/cpuinfo "model name" starts with the key, then a space ("... 12-Core Processor"); "7900X3D" is another part
    "AMD Ryzen 9 7900X": {"tjmax": 95.0, "page": "AMD's product page",  # "Max. Operating Temperature (Tjmax) 95°C"
                          "url": "https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-9-7900x.html"},
}
GPU_SPECS = {  # NVML's name; NVIDIA's spec table, RTX 4080 SUPER column: "Maximum GPU Temperature (in C) 90", "Total Graphics Power (W) 320"
    "NVIDIA GeForce RTX 4080 SUPER": {"max_c": 90.0, "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4080-family/"},
}
DRIVE_SPECS = {  # the drive's model then its capacity ("Samsung SSD 980 PRO 1TB"); data sheet Rev.2.1: "Operating 0°C to 70°C"
    "Samsung SSD 980 PRO": {"operating": "0–70 °C", "url": "https://download.semiconductor.samsung.com/resources/data-sheet/"
                                                           "Samsung-NVMe-SSD-980-PRO-Data-Sheet_Rev.2.1_10129504060695.pdf"},
}
# gigabyte_wmi's channels carry no label (the driver exposes temp*_input only). WHY a map per board, and only for a board checked
# by hand: Gigabyte publishes none, and the order differs between boards (t-8ch/linux-gigabyte-wmi-driver issue #1: a Z390 I
# AORUS PRO WIFI has temp4 unconnected; a B550 AORUS ELITE V2 reads temp2 "System 2" and temp6 "PCH").
BOARD_SENSOR_NAMES = {
    ("Gigabyte Technology Co., Ltd.", "A620M S2H"): {
        "names": {"temp1": "System 1", "temp2": "Chipset", "temp3": "CPU", "temp4": "PCIEX16", "temp5": "VRM MOS", "temp6": "VSOC MOS"},
        "source": "probable, Gigabyte publishes no map: the channel order of Gigabyte's ITE sensor chip in lm-sensors' Gigabyte "
                  "configs (temp5 is the VRM on every Gigabyte board reported there and in the driver's issue #1), checked on this "
                  "board on 2026-11-10 over 10 minutes: temp3 reads the CPU's own Tctl within 0.5 °C, temp5 is the hottest "
                  "(88-93 °C) and follows the CPU's total load slowly as a voltage regulator does, temp1 and temp4 warm with the GPU, "
                  "temp2 holds still",
        "url": "https://github.com/lm-sensors/lm-sensors/blob/master/configs/Gigabyte/GA-AB350-GAMING3.conf"},
}
GIB = 2 ** 30
MIB = 2 ** 20
TOOL_TIMEOUT_S = 5.0
FOLLOW_PERIOD_S = 1.0
# WHY 3 s: GNOME System Monitor's own Processes refresh (its update-interval default, 3000 ms); one psutil scan of
# ~1000 processes costs ~35 ms of CPU, ~1 % of a core every 3 s but 3.5 % every second (measured 2026-10-23)
PROCESSES_PERIOD_S = 3.0
NTP_PERIOD_S = 10.0  # timedatectl / chronyc are subprocesses: not every second
TOP_N = 10
GPU_FIELDS = "index,name,uuid,pci.bus_id,utilization.gpu,memory.used,memory.total,temperature.gpu"
GPU_RESTART_S = 30.0  # a dead nvidia-smi loop, or an NVML session that stopped answering, is restarted at most this often
# NVML's clock-event reasons (nvml.h nvmlClocksEventReason*, the same bits as the older ...ThrottleReason*): bit -> plain words.
CLOCK_REASONS = ((0x1, "idle"), (0x2, "application clocks"), (0x4, "power cap"), (0x8, "hardware slowdown"), (0x10, "sync boost"),
                 (0x20, "heat (software slowdown)"), (0x40, "heat (hardware slowdown)"), (0x80, "power brake"), (0x100, "display clock"))
# WHY these four warn: each is the GPU saying it slowed itself for heat or a power brake (nvml.h: HW Slowdown is "temperature
# being too high" or an "External Power Brake Assertion"); a power cap, an idle GPU or an application clock is the GPU working
# as configured, and only reads in held_back_by.
SLOWDOWN_REASONS = 0x8 | 0x20 | 0x40 | 0x80
# NVML answers a reading a GPU does not have with one of these: that reading is None and the rest still read. Anything else (the
# GPU fell off the bus, the driver went away) fails the whole read, so a lost GPU is never an empty "OK".
NVML_ABSENT = ("NVML_ERROR_INVALID_ARGUMENT", "NVML_ERROR_NOT_SUPPORTED", "NVML_ERROR_NO_PERMISSION", "NVML_ERROR_NOT_FOUND",
               "NVML_ERROR_FUNCTION_NOT_FOUND", "NVML_ERROR_NO_DATA", "NVML_ERROR_ARGUMENT_VERSION_MISMATCH", "NVML_ERROR_DEPRECATED")


# ----- readings: this computer, read only ---------------------------------------------------------------------------
def _run(argv: list[str]) -> subprocess.CompletedProcess | None:
    """A read-only tool, bounded, in the C locale; None when it is not installed."""

    if shutil.which(argv[0]) is None:
        return None
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                          timeout=TOOL_TIMEOUT_S, env={**os.environ, "LC_ALL": "C"})


def parse_offset_s(text: str) -> float | None:
    """systemd's "+1.496ms" / "-250us" / "+1.2s" -> seconds."""

    found = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(us|µs|ms|s)\b", text or "")
    if not found:
        return None
    return float(found[1]) * {"us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0}[found[2]]


def parse_nvidia_query(text: str) -> list[dict]:
    """``nvidia-smi --query-gpu=index,name,uuid,pci.bus_id,utilization.gpu,memory.used,memory.total,temperature.gpu
    --format=csv,noheader,nounits`` -> one dict per GPU; "[N/A]" -> None."""

    def number(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    gpus = []
    for row in csv.reader((text or "").splitlines(), skipinitialspace=True):
        if len(row) < 8:
            continue
        index, name, uuid, bus_id, utilization, used, total, temperature = (cell.strip() for cell in row[:8])
        gpus.append({"index": index, "name": name, "uuid": uuid, "bus_id": bus_id.lower(), "utilization": number(utilization),
                     "memory_used_mib": number(used), "memory_total_mib": number(total), "temperature": number(temperature)})
    return gpus


def parse_nvidia_thresholds(text: str) -> dict[str, dict[str, float]]:
    """``nvidia-smi -q -d TEMPERATURE`` -> {pci bus id: {"shutdown", "slowdown", "max_operating"}} in °C."""

    keys = {"GPU Shutdown Temp": "shutdown", "GPU Slowdown Temp": "slowdown", "GPU Max Operating Temp": "max_operating"}
    out: dict[str, dict[str, float]] = {}
    current = None
    for line in (text or "").splitlines():
        block = re.match(r"^GPU\s+([0-9A-Fa-f]+:[0-9A-Fa-f]+:[0-9A-Fa-f]+\.[0-7])\s*$", line)
        if block:
            current = out.setdefault(block[1].lower(), {})
            continue
        found = re.match(r"^\s*(GPU Shutdown Temp|GPU Slowdown Temp|GPU Max Operating Temp)\s*:\s*(\d+(?:\.\d+)?)\s*C\b", line)
        if found and current is not None:
            current[keys[found[1]]] = float(found[2])
    return out


def parse_chrony_tracking(text: str) -> dict | None:
    """``chronyc -c tracking`` (CSV): field 5 is the system clock's offset in seconds, the last the leap status."""

    row = next(csv.reader((text or "").splitlines()), None)
    if not row or len(row) < 14:
        return None
    try:
        offset = float(row[4])
    except ValueError:
        offset = None
    return {"source": "chrony", "synced": row[-1].strip() != "Not synchronised", "ntp_enabled": True, "offset_s": offset,
            "server": row[1].strip() or None}


def parse_interval_s(text: str) -> float:
    found = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(us|ms|s)\s*$", text or "")
    if not found:
        return 0.0
    return float(found[1]) * {"us": 1e-6, "ms": 1e-3, "s": 1.0}[found[2]]


def read_usb_buses(sys_root: Path) -> list[dict]:
    """Per USB bus: its speed and the bandwidth its devices' periodic endpoints (interrupt + isochronous, active
    setting) claim, from sysfs text files. WHY periodic only: the host controller reserves bandwidth for periodic
    endpoints; bulk transfers reserve nothing. A SuperSpeed endpoint's burst is in its companion descriptor, which sysfs
    does not show, so a USB 3 figure is a lower bound."""

    devices = sys_root / "bus" / "usb" / "devices"
    if not devices.is_dir():
        return []

    def read(path: Path) -> str:
        try:
            return path.read_text(errors="replace").strip()
        except OSError:
            return ""

    buses: dict[int, dict] = {}
    for entry in sorted(devices.iterdir(), key=lambda path: path.name):
        root = re.fullmatch(r"usb(\d+)", entry.name)
        if root:
            bus = buses.setdefault(int(root[1]), {"bus": int(root[1]), "claimed_bps": 0.0, "endpoints": 0})
            try:
                bus["speed_mbps"] = float(read(entry / "speed"))
            except ValueError:
                bus["speed_mbps"] = None
            continue
        interface = re.fullmatch(r"(\d+)-([\d.]+):\d+\.\d+", entry.name)
        if not interface or interface[2] == "0":
            continue  # a root hub's own interface ("1-0:1.0"): its status endpoint is not on the wire
        bus = buses.setdefault(int(interface[1]), {"bus": int(interface[1]), "claimed_bps": 0.0, "endpoints": 0, "speed_mbps": None})
        for endpoint in entry.glob("ep_*"):
            if read(endpoint / "type") not in ("Interrupt", "Isoc"):
                continue
            interval = parse_interval_s(read(endpoint / "interval"))
            try:
                packet = int(read(endpoint / "wMaxPacketSize"), 16)
            except ValueError:
                continue
            if interval <= 0:
                continue
            size, transactions = packet & 0x7FF, ((packet >> 11) & 0x3) + 1  # USB 2.0 §9.6.6: high-bandwidth bits
            bus["claimed_bps"] += size * transactions * 8 / interval
            bus["endpoints"] += 1
    return [buses[number] for number in sorted(buses)]


def _text(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return None


def _number(text: str | None) -> int | None:
    try:
        return int(text)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _milli(text: str | None) -> float | None:
    value = _number(text)
    return None if value is None else value / 1000.0


def _index(path: Path) -> int:
    found = re.search(r"(\d+)", path.name)
    return int(found[1]) if found else 0


def read_hwmon(sys_root: Path) -> list[dict]:
    """Every hwmon chip (``/sys/class/hwmon/hwmon*``, read-only text files, hwmon order): its name, the device it sits on, each
    temperature channel with the limits the chip itself reports (°C), each fan (RPM, its own min / max / alarm) and each PWM output
    (duty %, control mode). The hwmon ABI (Documentation/ABI/testing/sysfs-class-hwmon) names every file read here.

    WHY not psutil.sensors_temperatures: it merges chips that share a name (two NVMe drives become one "nvme" list), names an
    unlabelled sensor by its position (gigabyte_wmi hides a channel it cannot read, so its 4th sensor can be temp5) and reads no
    fan duty; the channel (temp5) and the chip's device are what a label map, a datasheet and a person need."""

    root = sys_root / "class" / "hwmon"
    if not root.is_dir():
        return []
    chips = []
    for entry in sorted(root.glob("hwmon*"), key=_index):
        name = _text(entry / "name") or entry.name
        device = os.path.realpath(entry / "device") if (entry / "device").exists() else ""
        temps = []
        for found in sorted(entry.glob("temp*_input"), key=_index):
            channel = found.name.removesuffix("_input")
            temps.append({"channel": channel, "label": _text(entry / f"{channel}_label") or "", "current": _milli(_text(found)),
                          # WHY no tempN_min: nothing uses it, and on an NVMe drive each tempN_min / tempN_max read is an admin
                          # command to the drive (drivers/nvme/host/hwmon.c nvme_get_temp_thresh), which follow sends every second
                          **{limit: _milli(_text(entry / f"{channel}_{limit}")) for limit in ("max", "crit", "emergency")}})
        fans = []
        for found in sorted(entry.glob("fan*_input"), key=_index):
            channel = found.name.removesuffix("_input")
            alarm = _number(_text(entry / f"{channel}_alarm"))
            fans.append({"channel": channel, "label": _text(entry / f"{channel}_label") or "", "rpm": _number(_text(found)),
                         "min": _number(_text(entry / f"{channel}_min")), "max": _number(_text(entry / f"{channel}_max")),
                         "target": _number(_text(entry / f"{channel}_target")), "alarm": None if alarm is None else alarm != 0})
        pwms = []
        for found in sorted((path for path in entry.glob("pwm*") if re.fullmatch(r"pwm\d+", path.name)), key=_index):
            duty = _number(_text(found))
            # hwmon ABI: pwmN is 0-255, "255 is max or 100%"; pwmN_enable 0 = no control (full speed), 1 = manual, 2+ = automatic
            pwms.append({"channel": found.name, "duty_percent": None if duty is None else round(duty / 255 * 100, 1),
                         "enable": _number(_text(entry / f"{found.name}_enable"))})
        model = (_text(entry / "device" / "model") or "") if name in DISK_CHIPS else ""
        chips.append({"name": name, "hwmon": entry.name, "device": device, "model": model, "temps": temps, "fans": fans, "pwms": pwms})
    return chips


def load_nvml():
    """NVIDIA's own Python binding for NVML (nvidia-ml-py, module ``pynvml``), or None when this env does not have it."""

    try:
        import pynvml  # noqa: PLC0415 - lazy: the host's env has no NVML binding, and tests hand in a fake
    except ImportError:
        return None
    return pynvml


def read_nvml(nvml, static: dict | None = None) -> list[dict]:
    """Every NVIDIA GPU through NVML (an initialised ``pynvml``), read-only queries. A reading the GPU does not have is None
    (NVML_ABSENT); a GPU that fell off the bus raises ``nvml.NVMLError``. ``static``: a dict kept for ONE NVML session (follow),
    into which each GPU's constants (name, PCI id, thresholds, maximum clocks, fan range) are asked once; None asks everything.
    WHY: every NVML call is a driver ioctl (~7 ms of CPU a tick for all of them, measured 2026-11-10), and half never change.

    WHY NVML and not ``nvidia-smi --query-gpu`` (2026-11-10): nvidia-smi is built on NVML, and its thresholds are only in the
    ``-q`` text, which NVIDIA does not promise to keep parseable; NVML is the versioned API, answers in microseconds in-process
    (no process per read), and has what nvidia-smi's CSV lacks: the acoustic target, each fan, the clock-event reasons.
    The thresholds come from nvmlDeviceGetTemperatureThreshold. nvml.h calls it "no longer the preferred interface" on Ada and
    later (use nvmlDeviceGetFieldValues' NVML_FI_DEV_TEMPERATURE_*_TLIMIT), but this RTX 4080 SUPER answers those fields
    NOT_SUPPORTED (driver 580.178.04) while the old call answers 101 / 96 / 90; should a driver drop it, the GPU reads "no limit
    reported", never a made-up one."""

    absent = {getattr(nvml, name) for name in NVML_ABSENT if hasattr(nvml, name)}

    def optional(call_name: str, *args):
        call = getattr(nvml, call_name, None)
        if call is None:
            return None  # an older binding without this call
        try:
            return call(*args)
        except nvml.NVMLError as error:
            if getattr(error, "value", None) in absent:
                return None
            raise

    def text(value) -> str:
        return value.decode(errors="replace") if isinstance(value, bytes) else str(value)

    def watts(milliwatts) -> float | None:
        return None if milliwatts is None else milliwatts / 1000.0

    def threshold(handle, kind: str):
        constant = getattr(nvml, f"NVML_TEMPERATURE_THRESHOLD_{kind}", None)
        return None if constant is None else optional("nvmlDeviceGetTemperatureThreshold", handle, constant)

    def constants(index: int, handle) -> dict:
        span = optional("nvmlDeviceGetMinMaxFanSpeed", handle)
        return {"index": str(index), "name": text(nvml.nvmlDeviceGetName(handle)),
                "uuid": text(optional("nvmlDeviceGetUUID", handle) or ""), "bus_id": text(nvml.nvmlDeviceGetPciInfo(handle).busId).lower(),
                "max_operating": threshold(handle, "GPU_MAX"), "slowdown": threshold(handle, "SLOWDOWN"),
                "shutdown": threshold(handle, "SHUTDOWN"), "acoustic_target": threshold(handle, "ACOUSTIC_CURR"),
                "fan_count": optional("nvmlDeviceGetNumFans", handle) or 0,
                "fan_min_percent": span[0] if span else None, "fan_max_percent": span[1] if span else None,
                "clock_sm_max_mhz": optional("nvmlDeviceGetMaxClockInfo", handle, nvml.NVML_CLOCK_SM),
                "clock_mem_max_mhz": optional("nvmlDeviceGetMaxClockInfo", handle, nvml.NVML_CLOCK_MEM)}

    # the clock-event call's older name ("throttle reasons", same bits) for a binding older than r535
    reasons_call = ("nvmlDeviceGetCurrentClocksEventReasons" if getattr(nvml, "nvmlDeviceGetCurrentClocksEventReasons", None)
                    else "nvmlDeviceGetCurrentClocksThrottleReasons")
    gpus = []
    for index in range(nvml.nvmlDeviceGetCount()):
        handle = nvml.nvmlDeviceGetHandleByIndex(index)
        known = static.get(index) if static is not None else None
        if known is None:
            known = constants(index, handle)
            if static is not None:
                static[index] = known
        memory = optional("nvmlDeviceGetMemoryInfo", handle)
        rates = optional("nvmlDeviceGetUtilizationRates", handle)
        fans = [{"fan": fan, "percent": optional("nvmlDeviceGetFanSpeed_v2", handle, fan),
                 "target_percent": optional("nvmlDeviceGetTargetFanSpeed", handle, fan)} for fan in range(known["fan_count"])]
        gpus.append({
            **{key: value for key, value in known.items() if key != "fan_count"},
            "utilization": rates.gpu if rates is not None else None,
            "memory_used_mib": memory.used / MIB if memory is not None else None,
            "memory_total_mib": memory.total / MIB if memory is not None else None,
            "temperature": optional("nvmlDeviceGetTemperature", handle, nvml.NVML_TEMPERATURE_GPU),
            "fans": fans,
            "power_w": watts(optional("nvmlDeviceGetPowerUsage", handle)),
            "power_limit_w": watts(optional("nvmlDeviceGetEnforcedPowerLimit", handle)),
            "clock_sm_mhz": optional("nvmlDeviceGetClockInfo", handle, nvml.NVML_CLOCK_SM),
            "clock_mem_mhz": optional("nvmlDeviceGetClockInfo", handle, nvml.NVML_CLOCK_MEM),
            "reasons": optional(reasons_call, handle), "source": "nvml"})
    return gpus


class LiveProbe:
    """This computer. Each method is one reading; an exception becomes UNKNOWN ``ReadFailed`` on its status."""

    def __init__(self, sys_root: Path = Path("/sys"), *, follow: bool = False, proc_root: Path = Path("/proc"),
                 nvml_loader: Callable[[], Any] = load_nvml):
        self.sys_root, self.proc_root = sys_root, proc_root
        self._psutil = None
        self.follow = follow
        self.gpu_loop: GpuLoop | None = None
        self._thresholds: dict | None = None
        self._proc_ticks: dict[int, int] = {}  # pid -> its CPU ticks at the previous processes() call
        self._proc_at: float | None = None
        self._nvml_loader = nvml_loader
        self._nvml: Any = None  # the binding once looked for (False: this env has none)
        self._nvml_ready = False  # nvmlInit done and not shut down (follow keeps ONE session)
        self._nvml_retry_at: float | None = None  # monotonic: after a failure, NVML is tried again from then
        self._nvml_error = ""
        self._nvml_static: dict = {}  # GPU index -> its constants, for the open NVML session (read_nvml)
        self._board: dict | None = None
        self._cpu_model: str | None = None

    @property
    def psutil(self):
        if self._psutil is None:
            import psutil  # noqa: PLC0415 - lazy: tests hand in a fake probe and the host's env has no psutil

            self._psutil = psutil
        return self._psutil

    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def cpu_percent(self, interval: float) -> list[float]:
        return list(self.psutil.cpu_percent(interval=interval, percpu=True))

    def cpu_counts(self) -> tuple[int | None, int | None]:
        return self.psutil.cpu_count(logical=True), self.psutil.cpu_count(logical=False)

    def loadavg(self) -> tuple[float, float, float]:
        return tuple(self.psutil.getloadavg())

    def virtual_memory(self) -> dict:
        memory = self.psutil.virtual_memory()
        return {key: getattr(memory, key, None) for key in ("total", "available", "percent", "used", "buffers", "cached")}

    def swap_memory(self) -> dict:
        swap = self.psutil.swap_memory()
        return {"total": swap.total, "used": swap.used, "percent": swap.percent}

    def disk_partitions(self) -> list[dict]:
        return [{"device": part.device, "mountpoint": part.mountpoint, "fstype": part.fstype}
                for part in self.psutil.disk_partitions(all=False)]

    def disk_usage(self, mountpoint: str) -> dict:
        usage = self.psutil.disk_usage(mountpoint)
        return {"total": usage.total, "used": usage.used, "free": usage.free}

    def hwmon(self) -> list[dict]:
        return read_hwmon(self.sys_root)

    def board(self) -> dict:
        """The board's maker and name from DMI (``/sys/class/dmi/id``, world-readable): what a per-board sensor map is keyed by."""

        if self._board is None:
            found = self.sys_root / "class" / "dmi" / "id"
            self._board = {"vendor": _text(found / "board_vendor") or "", "name": _text(found / "board_name") or ""}
        return self._board

    def cpu_model(self) -> str:
        """``/proc/cpuinfo``'s first "model name" ("AMD Ryzen 9 7900X 12-Core Processor"): what CPU_SPECS is keyed by.

        WHY read once per process: neither the board nor the CPU changes while the plugin runs, and on x86 the kernel builds
        /proc/cpuinfo by sampling every core's clock, which follow would otherwise pay every second."""

        if self._cpu_model is None:
            self._cpu_model = ""
            for line in (_text(self.proc_root / "cpuinfo") or "").splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "model name":
                    self._cpu_model = value.strip()
                    break
        return self._cpu_model

    def gpus(self) -> dict:
        """``{"gpus": [...]}``, or ``{"missing": why}`` (no NVIDIA driver: not available), or ``{"error": why}``.

        NVML first (read_nvml); nvidia-smi only when this env has no NVML binding, or NVML finds no NVIDIA library (then
        nvidia-smi, if any, says the rest). One diagnose opens and shuts NVML down; follow keeps one session and, when it stops
        answering, starts it again at most every ``GPU_RESTART_S``."""

        if self._nvml is None:
            self._nvml = self._nvml_loader() or False
        nvml = self._nvml
        if nvml is False:
            return self._smi_gpus()
        try:
            if not self._nvml_ready:
                if self._nvml_retry_at is not None and self.monotonic() < self._nvml_retry_at:
                    return {"error": self._nvml_error}
                try:
                    nvml.nvmlInit()
                except nvml.NVMLError as error:
                    if getattr(error, "value", None) == getattr(nvml, "NVML_ERROR_LIBRARY_NOT_FOUND", 12):
                        return self._smi_gpus()  # no libnvidia-ml: no NVIDIA driver here (nvidia-smi, if any, says the rest)
                    self._nvml_error = f"NVML could not start: {error}"
                    self._nvml_retry_at = self.monotonic() + GPU_RESTART_S
                    return {"error": self._nvml_error}
                self._nvml_ready, self._nvml_retry_at, self._nvml_static = True, None, {}
            try:
                return {"gpus": read_nvml(nvml, self._nvml_static if self.follow else None)}
            except nvml.NVMLError as error:
                self._nvml_error = f"NVML stopped answering: {error}"
                self._nvml_retry_at = self.monotonic() + GPU_RESTART_S
                self._nvml_stop()
                return {"error": self._nvml_error}
        finally:
            if not self.follow:
                self._nvml_stop()

    def _nvml_stop(self) -> None:
        if self._nvml_ready:
            self._nvml_ready = False
            try:
                self._nvml.nvmlShutdown()
            except Exception:  # noqa: BLE001 - a session that already broke has nothing left to close
                pass

    def _smi_gpus(self) -> dict:
        return self._followed_gpus() if self.follow else self._smi_once()

    def _smi_once(self) -> dict:
        """nvidia-smi (no NVML binding in this env): one ``--query-gpu`` and its ``-q -d TEMPERATURE`` thresholds."""

        query = _run(["nvidia-smi", f"--query-gpu={GPU_FIELDS}", "--format=csv,noheader,nounits"])
        if query is None:
            return {"missing": "no nvidia-smi on PATH"}
        if query.returncode != 0:
            said = (query.stdout + query.stderr).strip().splitlines()
            return {"error": said[0] if said else f"nvidia-smi exited {query.returncode}"}
        gpus = parse_nvidia_query(query.stdout)
        limits = _run(["nvidia-smi", "-q", "-d", "TEMPERATURE"])
        thresholds = parse_nvidia_thresholds(limits.stdout) if limits is not None and limits.returncode == 0 else {}
        for gpu in gpus:
            gpu.update(thresholds.get(gpu["bus_id"], {}))
            gpu["source"] = "nvidia-smi"
        return {"gpus": gpus}

    def _followed_gpus(self) -> dict:
        """follow without NVML: the latest rows of ONE ``nvidia-smi -lms`` loop; its first tick (and a dead loop's restart, at
        most every ``GPU_RESTART_S``) reads once, as diagnose does."""

        if shutil.which("nvidia-smi") is None:
            return {"missing": "no nvidia-smi on PATH"}
        loop = self.gpu_loop
        if loop is not None and loop.alive() and loop.rows:
            gpus = [{**row, "source": "nvidia-smi"} for row in loop.rows.values()]
            for gpu in gpus:
                gpu.update((self._thresholds or {}).get(gpu["bus_id"], {}))
            return {"gpus": gpus}
        if loop is not None and loop.alive():
            pass  # started, no row yet: read once below
        elif loop is None or self.monotonic() - loop.started >= GPU_RESTART_S:
            if loop is not None:
                loop.stop()
            self.gpu_loop = GpuLoop(self.monotonic())
            self.gpu_loop.start()
        else:
            return {"error": f"nvidia-smi -lms exited {loop.process.returncode}; restarting in"
                             f" {GPU_RESTART_S - (self.monotonic() - loop.started):.0f} s"}
        found = self._smi_once()
        if self._thresholds is None and "gpus" in found:
            self._thresholds = {gpu["bus_id"]: {key: gpu[key] for key in ("max_operating", "slowdown", "shutdown") if key in gpu}
                                for gpu in found["gpus"]}
        return found

    def processes(self) -> list[dict]:
        """Every process: pid, name, CPU % of ONE core since the previous call, RSS in bytes.

        WHY ``/proc/<pid>/stat`` by hand here, the one reading psutil does not make: psutil's process_iter reads three
        or four files per process (~35 ms of CPU per scan of ~1000 processes); stat alone holds the name, the CPU
        ticks and the RSS (~13 ms), which keeps follow under 2 % of one core (measured 2026-10-23)."""

        clock, ticks, page = time.monotonic(), os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")
        elapsed = clock - self._proc_at if self._proc_at is not None else 0.0
        out, seen = [], {}
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                handle = os.open(f"/proc/{name}/stat", os.O_RDONLY)
                try:
                    data = os.read(handle, 4096)
                finally:
                    os.close(handle)
                right = data.rindex(b")")
                fields = data[right + 2:].split()
                used, rss = int(fields[11]) + int(fields[12]), int(fields[21]) * page  # utime + stime, rss pages
            except (OSError, ValueError, IndexError):
                continue  # it exited while we read
            pid = int(name)
            seen[pid] = used
            before = self._proc_ticks.get(pid)
            percent = max(used - before, 0) / ticks / elapsed * 100.0 if before is not None and elapsed > 0 else 0.0
            out.append({"pid": pid, "name": data[data.index(b"(") + 1:right].decode("utf-8", errors="replace") or "?",
                        "cpu_percent": percent, "rss": rss})
        self._proc_ticks, self._proc_at = seen, clock
        return out

    def close(self) -> None:
        if self.gpu_loop is not None:
            self.gpu_loop.stop()
        self._nvml_stop()

    def clock(self) -> dict | None:
        chrony = _run(["chronyc", "-c", "tracking"])
        if chrony is not None and chrony.returncode == 0:
            parsed = parse_chrony_tracking(chrony.stdout)
            if parsed:
                return parsed
        show = _run(["timedatectl", "show"])
        if show is None or show.returncode != 0:
            return None
        props = dict(line.split("=", 1) for line in show.stdout.splitlines() if "=" in line)
        out = {"source": "timedatectl", "synced": {"yes": True, "no": False}.get(props.get("NTPSynchronized", "")),
               "ntp_enabled": {"yes": True, "no": False}.get(props.get("NTP", "")), "offset_s": None, "server": None}
        status = _run(["timedatectl", "timesync-status"])  # systemd-timesyncd only; another daemon makes this fail
        if status is not None and status.returncode == 0:
            out["source"] = "systemd-timesyncd"
            for line in status.stdout.splitlines():
                key, _, value = line.strip().partition(":")
                if key == "Offset":
                    out["offset_s"] = parse_offset_s(value)
                elif key == "Server":
                    named = re.search(r"\(([^)]+)\)", value)
                    out["server"] = named[1] if named else value.strip()
        return out

    def boot_time(self) -> float:
        return self.psutil.boot_time()

    def net_counters(self) -> dict[str, tuple[int, int]]:
        return {nic: (counters.bytes_recv, counters.bytes_sent) for nic, counters in self.psutil.net_io_counters(pernic=True).items()}

    def net_stats(self) -> dict[str, tuple[bool, float]]:
        return {nic: (stats.isup, float(stats.speed or 0)) for nic, stats in self.psutil.net_if_stats().items()}

    def usb_buses(self) -> list[dict]:
        return read_usb_buses(self.sys_root)


class GpuLoop:
    """``nvidia-smi --query-gpu=... -lms 1000``: one long-running reader (read-only) keeping each GPU's latest row.
    WHY: starting nvidia-smi costs ~20 ms of CPU, 2 % of a core every second; its own loop costs almost nothing."""

    def __init__(self, started: float, interval_ms: int = int(FOLLOW_PERIOD_S * 1000)):
        self.started, self.interval_ms = started, interval_ms
        self.rows: dict[str, dict] = {}
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        import threading  # noqa: PLC0415 - follow only

        self.process = subprocess.Popen(["nvidia-smi", f"--query-gpu={GPU_FIELDS}", "--format=csv,noheader,nounits",
                                         f"--loop-ms={self.interval_ms}"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, errors="replace", env={**os.environ, "LC_ALL": "C"})
        threading.Thread(target=self._pump, name="gpu-loop", daemon=True).start()

    def _pump(self) -> None:
        for line in self.process.stdout:
            for gpu in parse_nvidia_query(line):
                self.rows[gpu["index"]] = gpu

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.alive():
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


# ----- the statuses ------------------------------------------------------------------------------------------------
class Options(argparse.Namespace):
    sample_s = SAMPLE_S
    cpu_warn_percent = CPU_WARN_PERCENT
    ram_warn_percent = RAM_WARN_PERCENT
    free_percent_low = FREE_PERCENT_LOW
    free_percent_crit = FREE_PERCENT_CRIT
    temp_warn = TEMP_WARN_DEFAULT
    temp_error = TEMP_ERROR_DEFAULT
    ntp_error_offset_s = NTP_ERROR_OFFSET_S
    ntp_warn_offset_s: float | None = None
    net_warn_fraction = NET_WARN_FRACTION
    period_s = PERIOD_S


def _pct(value: float) -> str:
    return f"{value:.1f}"


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def _fan_text(fans: list[dict]) -> str:
    """NVML's fans in a few words: "fans 0 % (stopped)", "fans 45 / 47 %", "fan 100 % (full speed)"."""

    percents = [fan["percent"] for fan in fans if fan.get("percent") is not None]
    if not percents:
        return ""
    said = f"{percents[0]:.0f}" if len(set(percents)) == 1 else " / ".join(f"{percent:.0f}" for percent in percents)
    tail = " (full speed)" if max(percents) >= 100 else " (stopped)" if max(percents) == 0 else ""
    return f"{'fan' if len(fans) == 1 else 'fans'} {said} %{tail}"


def _duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return f"{days} d {hours:02d}:{minutes:02d}" if days else f"{hours:02d}:{minutes:02d}"


class Checks:
    """One diagnose: every status, each built from one reading of ``probe``."""

    def __init__(self, probe, host: str, options: Options, window: tuple | None = None):
        self.probe, self.host, self.options = probe, host, options
        self.now = probe.time()
        self.window = window  # follow: (net counters, monotonic) of the previous tick; it opens this sample
        self._chips: list[dict] | None = None  # one hwmon read per diagnose or tick, shared by temperatures and fans

    def status(self, check: str, level: str, message: str, *, device: str | None = None, code: str = "", values=(),
               measurements=(), **extra) -> DiagnosticStatus:
        """One check on the Computer, named "<device>: <check>"; ``device`` None is the machine itself (the host's name)."""

        return DiagnosticStatus(level=level, name=f"{device or self.host}: {check}", message=message, hardware_id=self.host,
                                values=tuple(values), path="", code=code if level != OK else "", stamp=self.now,
                                expected_period_s=self.options.period_s, source=SOURCE, measurements=tuple(measurements),
                                **extra)

    def guarded(self, check: str, device: str | None, build: Callable[[], list[DiagnosticStatus]]) -> list[DiagnosticStatus]:
        try:
            return build()
        except Exception as error:  # noqa: BLE001 - a reading that throws is that check's UNKNOWN, never the run's crash
            return [self.status(check, UNKNOWN, f"could not read: {type(error).__name__}: {error}", device=device, code="ReadFailed")]

    def chips(self) -> list[dict]:
        if self._chips is None:
            self._chips = self.probe.hwmon()
        return self._chips

    @staticmethod
    def sourced(text: str, url: str | None = None) -> list[KeyValue]:
        """The values that say whose limit a status uses: threshold_source, and threshold_url when a page states the number."""

        return [KeyValue("threshold_source", text)] + ([KeyValue("threshold_url", url)] if url else [])

    # --- CPU and network share one sampling window ----------------------------------------------------------------
    def sample(self) -> None:
        if self.window is not None:  # follow: the second since the previous tick IS the window (no sleep)
            self.net_before, started = self.window
            self.cores = self._try(lambda: self.probe.cpu_percent(None))
        else:
            self.net_before = self._try(self.probe.net_counters)
            started = self.probe.monotonic()
            self.cores = self._try(lambda: self.probe.cpu_percent(self.options.sample_s))
        self.net_after = self._try(self.probe.net_counters)
        self.ended = self.probe.monotonic()
        self.elapsed_s = max(self.ended - started, 1e-3)

    @staticmethod
    def _try(read: Callable[[], Any]) -> Any:
        try:
            return read()
        except Exception as error:  # noqa: BLE001
            return error

    def cpu(self) -> list[DiagnosticStatus]:
        if isinstance(self.cores, Exception):
            raise self.cores
        cores = [float(value) for value in self.cores]
        if not cores:
            return [self.status("CPU Information", OK, "not available (no per-core readings)", device="CPU")]
        average = sum(cores) / len(cores)
        worst = max(cores)
        load = self.probe.loadavg()
        logical, physical = self.probe.cpu_counts()
        limit = self.options.cpu_warn_percent
        measurements = [Measurement("CPU Load Average", _round(average, 2), "%", warn_high=limit),
                        Measurement("CPU max core load", _round(worst, 2), "%"),
                        *(Measurement(f"CPU {index} Load", _round(value, 2), "%") for index, value in enumerate(cores)),
                        Measurement("load 1 min", _round(load[0], 2), "1"), Measurement("load 5 min", _round(load[1], 2), "1"),
                        Measurement("load 15 min", _round(load[2], 2), "1")]
        whose = "ROS cpu_monitor's warning_percentage" if limit == CPU_WARN_PERCENT else f"--cpu-warn-percent (ROS cpu_monitor's default is {CPU_WARN_PERCENT:g})"
        values = [KeyValue("cores", f"{logical or len(cores)} logical · {physical or '?'} physical"),
                  KeyValue("sample", f"{self.options.sample_s:g} s"),
                  *self.sourced(f"warn above {limit:g} % average load: {whose}, judged on the average of every core, not on one",
                                CPU_LIMIT_URL)]
        tail = f"worst core {worst:.0f} % · load {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}"
        # WHY the average and not ROS's "any core above 90 %": one pegged core is a single-threaded job on a 24-thread
        # machine, not a starved computer; the worst core stays a measurement
        if average > limit:
            return [self.status("CPU Information", WARN, f"CPU Average exceeds {limit:g} percent ({_pct(average)} %) · {tail}",
                                device="CPU", code="CpuAverageHigh", values=values, measurements=measurements)]
        return [self.status("CPU Information", OK, f"CPU Average {_pct(average)} percent · {tail}", device="CPU", values=values,
                            measurements=measurements)]

    def ram(self) -> list[DiagnosticStatus]:
        memory, swap = self.probe.virtual_memory(), self.probe.swap_memory()
        percent = float(memory["percent"])
        limit = self.options.ram_warn_percent
        whose = "ROS ram_monitor's warning_percentage" if limit == RAM_WARN_PERCENT else f"--ram-warn-percent (ROS ram_monitor's default is {RAM_WARN_PERCENT:g})"
        values = self.sourced(f"warn above {limit:g} % of the memory in use: {whose}", RAM_LIMIT_URL)

        def gib(value) -> float | None:
            return None if value is None else round(value / GIB, 2)

        measurements = [Measurement("RAM Load Average", _round(percent, 2), "%", warn_high=limit),
                        Measurement("RAM used", gib(memory["total"] - memory["available"]), "GiBy"),
                        Measurement("RAM total", gib(memory["total"]), "GiBy"),
                        Measurement("RAM available", gib(memory["available"]), "GiBy"),
                        Measurement("buffers", gib(memory.get("buffers")), "GiBy"),
                        Measurement("cached", gib(memory.get("cached")), "GiBy"),
                        Measurement("swap used", gib(swap["used"]), "GiBy"),
                        Measurement("swap total", gib(swap["total"]), "GiBy"),
                        Measurement("swap used %", _round(swap["percent"], 2), "%")]
        used = (memory["total"] - memory["available"]) / GIB
        tail = f"{used:.1f} of {memory['total'] / GIB:.1f} GiB · swap {swap['used'] / GIB:.1f} of {swap['total'] / GIB:.1f} GiB"
        if percent > limit:
            return [self.status("RAM Information", WARN, f"RAM Average exceeds {limit:g} percent ({_pct(percent)} %) · {tail}",
                                device="RAM", code="RamAverageHigh", values=values, measurements=measurements)]
        return [self.status("RAM Information", OK, f"RAM Average {_pct(percent)} percent · {tail}", device="RAM", values=values,
                            measurements=measurements)]

    def disks(self) -> list[DiagnosticStatus]:
        mounts, seen, skipped = [], set(), []
        for part in self.probe.disk_partitions():
            device, mountpoint, fstype = part["device"], part["mountpoint"], part["fstype"]
            if fstype in SKIP_FSTYPES or fstype.startswith("fuse.") or device.startswith("/dev/loop") or mountpoint.startswith("/snap/"):
                continue
            if device in seen:
                continue  # a bind mount or a second mountpoint of one filesystem: one row per filesystem
            seen.add(device)
            try:
                usage = self.probe.disk_usage(mountpoint)
            except OSError as error:
                skipped.append(f"{mountpoint}: {error.strerror or error}")
                continue
            if not usage["total"]:
                continue
            mounts.append((mountpoint, fstype, usage, usage["free"] / usage["total"] * 100.0))
        if not mounts:
            return [self.status("HD Usage", OK, "not available (no disk mounts)", device="Disks")]
        low, crit = self.options.free_percent_low, self.options.free_percent_crit
        measurements = []
        for mountpoint, _fstype, usage, free_percent in mounts:
            measurements += [Measurement(f"{mountpoint} available", _round(free_percent, 2), "%", warn_low=low, error_low=crit),
                             Measurement(f"{mountpoint} free", round(usage["free"] / GIB, 2), "GiBy"),
                             Measurement(f"{mountpoint} total", round(usage["total"] / GIB, 2), "GiBy")]
        values = [KeyValue("mounts", ", ".join(f"{mountpoint} ({fstype})" for mountpoint, fstype, _usage, _free in mounts))]
        if skipped:
            values.append(KeyValue("not read", "; ".join(skipped)))
        ros = (low, crit) == (FREE_PERCENT_LOW, FREE_PERCENT_CRIT)
        values += self.sourced(f"warn below {low:g} % free, error below {crit:g} % free, on every mount: "
                               + ("ROS hd_monitor's FREE_PERCENT_LOW and FREE_PERCENT_CRIT" if ros else
                                  f"--free-percent-low / --free-percent-crit (ROS hd_monitor's defaults are {FREE_PERCENT_LOW:g} and {FREE_PERCENT_CRIT:g})"),
                               HD_LIMIT_URL)
        fullest = min(mounts, key=lambda mount: mount[3])
        mountpoint, _fstype, usage, free_percent = fullest
        text = f"{mountpoint} {free_percent:.1f} % free ({usage['free'] / GIB:.0f} of {usage['total'] / GIB:.0f} GiB)"
        others = f" · {len(mounts) - 1} other mount{'s' if len(mounts) != 2 else ''}" if len(mounts) > 1 else ""
        # ROS hd_monitor's words: DICT_USAGE {OK: 'OK', WARN: 'Low Disk Space', ERROR: 'Very Low Disk Space'}
        if free_percent < crit:
            return [self.status("HD Usage", ERROR, f"Very Low Disk Space: {text} (error below {crit:g} %){others}", device="Disks",
                                code="VeryLowDiskSpace", values=values, measurements=measurements)]
        if free_percent < low:
            return [self.status("HD Usage", WARN, f"Low Disk Space: {text} (warn below {low:g} %){others}", device="Disks",
                                code="LowDiskSpace", values=values, measurements=measurements)]
        return [self.status("HD Usage", OK, f"{len(mounts)} mount{'s' if len(mounts) != 1 else ''} · fullest {text}", device="Disks",
                            values=values, measurements=measurements)]

    def temperatures(self) -> list[DiagnosticStatus]:
        """One check per hwmon chip, in its device's folder. Limits, in this order: the sensor's own (``tempN_max`` / ``_crit``,
        else ``_emergency``); for a CPU sensor without them, its model's published Tjmax (CPU_SPECS) under RobotDx's warning,
        else RobotDx's defaults, said as such; for any other sensor, none (OK however hot, "no limit known")."""

        chips = [chip for chip in self.chips() if chip["temps"]]
        if not chips:
            return [self.status("Temperatures", OK, "not available (no hwmon temperature sensors)")]
        board = self._read(self.probe.board, {}) if hasattr(self.probe, "board") else {}
        cpu_model = self._read(self.probe.cpu_model, "") if hasattr(self.probe, "cpu_model") else ""
        out, seen = [], {}
        # CPU, GPU, RAM, disks, board, network: among equal levels the host's row headlines the first check (summarize)
        for chip in sorted(chips, key=lambda item: device_rank(device_of(item))):
            out.append(self._chip_temperatures(chip, board, cpu_model, seen))
        return out

    @staticmethod
    def _read(read: Callable[[], Any], fallback):
        try:
            return read() or fallback
        except Exception:  # noqa: BLE001 - a missing DMI or cpuinfo only loses the part's name, never the readings
            return fallback

    def _sensor_limits(self, chip: dict, sensor: dict, cpu_spec: dict | None, cpu_model: str) -> dict:
        """{warn, error, kind, warn_why, error_why, warn_attr, error_attr} for one sensor; kind is own / spec / default / none."""

        def sane(value) -> float | None:
            return float(value) if isinstance(value, (int, float)) and 0 < value <= TEMP_SANE_MAX else None

        channel = sensor["channel"]
        high, critical, critical_attr = sane(sensor.get("max")), sane(sensor.get("crit")), f"{channel}_crit"
        if critical is None and sane(sensor.get("emergency")) is not None:
            critical, critical_attr = sane(sensor.get("emergency")), f"{channel}_emergency"
        if high is not None and critical is not None and high > critical:
            high = None  # a chip that reports high above critical: keep the stricter one (validate's ordering)
        if high is not None or critical is not None:
            return {"warn": high, "error": critical, "kind": "own", "warn_why": "its own limit", "error_why": "its own limit",
                    "warn_attr": f"{channel}_max", "error_attr": critical_attr}
        if chip["name"] not in CPU_CHIPS:
            return {"warn": None, "error": None, "kind": "none"}
        warn = self.options.temp_warn
        if cpu_spec is not None and self.options.temp_error == TEMP_ERROR_DEFAULT:  # the part's own number beats our default
            error = cpu_spec["tjmax"]
            return {"warn": warn if warn < error else None, "error": error, "kind": "spec", "error_why": f"the {cpu_spec['key']}'s Tjmax",
                    "warn_why": f"RobotDx's warning, {error - warn:g} °C under the {error:g} °C Tjmax"}
        return {"warn": warn, "error": self.options.temp_error, "kind": "default",
                "warn_why": "RobotDx default, the sensor reports none", "error_why": "RobotDx default, the sensor reports none"}

    def _chip_temperatures(self, chip: dict, board: dict, cpu_model: str, seen: dict) -> DiagnosticStatus:
        name, device = chip["name"], device_of(chip)
        friendly = FRIENDLY_CHIPS.get(name)
        check = f"Temperature {friendly} ({name})" if friendly else f"Temperature {name}"
        seen[check] = seen.get(check, 0) + 1
        if seen[check] > 1:
            check = f"{check} #{seen[check]}"  # a second drive of the same kind: its own check, never merged into the first
        who = friendly or name
        cpu_spec = next(({**spec, "key": key} for key, spec in CPU_SPECS.items() if cpu_model == key or cpu_model.startswith(key + " ")),
                        None) if name in CPU_CHIPS else None
        names = BOARD_SENSOR_NAMES.get((board.get("vendor"), board.get("name"))) if name == "gigabyte_wmi" else None
        measurements, rows, keys = [], [], set()
        for sensor in chip["temps"]:
            label = sensor["label"].strip()
            probable = (names or {}).get("names", {}).get(sensor["channel"], "") if not label else ""
            key = label or (f"{sensor['channel']} {probable}" if probable else sensor["channel"])
            while key in keys:
                key = f"{key} #{len(keys) + 1}"
            keys.add(key)
            limits = self._sensor_limits(chip, sensor, cpu_spec, cpu_model)
            measurement = Measurement(key, _round(sensor["current"], 2), "Cel", warn_high=limits["warn"], error_high=limits["error"])
            measurements.append(measurement)
            rows.append((measurement, sensor, limits, label or sensor["channel"], probable))
        # a sensor with no reading never outranks a hot one (RANK puts UNKNOWN above WARN)
        worst, sensor, limits, shown, probable = max(rows, key=lambda row: (READING_ORDER[row[0].level],
                                                                            row[0].value if row[0].value is not None else -1e9))
        values = [KeyValue("chip", name)]
        if chip.get("model"):
            values.append(KeyValue("model", chip["model"]))
        if device == "Board" and board.get("name"):
            values.append(KeyValue("board", f"{board.get('vendor', '')} {board['name']}".strip()))
        if names:
            values += [KeyValue("labels_source", names["source"]), KeyValue("labels_url", names["url"])]
        values += self._temperature_source(chip, rows, cpu_spec, cpu_model)
        limited = any(row[2]["kind"] != "none" for row in rows)
        level, value = worst.level, worst.value
        if level == ERROR:
            return self.status(check, ERROR, f"{who} {shown} {value:.0f} °C: Critical Temperature (above {worst.error_high:g} °C:"
                                             f" {limits['error_why']})", device=device, code="CriticalTemperature", values=values,
                               measurements=measurements)
        if level == WARN:
            return self.status(check, WARN, f"{who} {shown} {value:.0f} °C: High Temperature (above {worst.warn_high:g} °C:"
                                            f" {limits['warn_why']})", device=device, code="HighTemperature", values=values,
                               measurements=measurements)
        if level == UNKNOWN:
            return self.status(check, OK, f"{who}: not available (no reading)", device=device, values=values, measurements=measurements)
        guess = f" (probably the {probable})" if probable else ""
        return self.status(check, OK, f"{who} {shown} {value:.0f} °C{guess}{'' if limited else ' · no limit known'}", device=device,
                           values=values, measurements=measurements)

    def _temperature_source(self, chip: dict, rows: list, cpu_spec: dict | None, cpu_model: str) -> list[KeyValue]:
        """threshold_source / threshold_url for one chip: whose number each sensor's limit is, sensors that share one said once."""

        name = chip["name"]
        thing = "drive" if name in DISK_CHIPS else "sensor"
        parts, url = [], None
        own = [row for row in rows if row[2]["kind"] == "own"]
        groups: dict[tuple, list] = {}
        for row in own:
            groups.setdefault((row[2]["warn"], row[2]["error"]), []).append(row)
        for (warn, error), members in groups.items():
            single = len(members) == 1
            said = [f"warn above {warn:g} °C ({members[0][2]['warn_attr'] if single else 'tempN_max'})" if warn is not None else "",
                    f"error above {error:g} °C ({members[0][2]['error_attr'] if single else 'tempN_crit'})" if error is not None else ""]
            who = "" if len(members) == len(rows) else f"{', '.join(row[0].key for row in members)}: "
            parts.append(f"{who}the {thing}'s own limits, " + ", ".join(text for text in said if text))
        spec = [row for row in rows if row[2]["kind"] == "spec"]
        if spec:
            limits = spec[0][2]
            parts.append(f"error above {limits['error']:g} °C: the {cpu_spec['key']}'s maximum operating temperature (Tjmax,"
                         f" {cpu_spec['page']})" + (f" · warn above {limits['warn']:g} °C: RobotDx's own choice,"
                                                    f" {limits['error'] - limits['warn']:g} °C under it" if limits["warn"] is not None else ""))
            url = cpu_spec["url"]
        if any(row[2]["kind"] == "default" for row in rows):
            parts.append(f"warn above {self.options.temp_warn:g} °C, error above {self.options.temp_error:g} °C: RobotDx default, no"
                         f" published limit on file for {cpu_model or 'this CPU'} and the sensor reports none")
        unset = [row for row in rows if row[2]["kind"] == "none"]
        if unset:
            sentinel = [row for row in unset if isinstance(row[1].get("max"), (int, float)) and row[1]["max"] > TEMP_SANE_MAX]
            if len(unset) == len(rows):
                if name == "gigabyte_wmi":
                    parts.append("none: gigabyte_wmi reports no limits and Gigabyte publishes none for these board sensors, so they"
                                 " never warn")
                    url = GIGABYTE_WMI_URL
                else:
                    parts.append(f"none: {name} reports no limit and none is published for it, so it never warns")
            else:
                said = (f": the {thing} sets no threshold ({sentinel[0][1]['max']:.2f} °C means unset)" if sentinel
                        else f": the {thing} reports no limit")
                parts.append(f"{', '.join(row[0].key for row in unset)}{said}")
        drive = next((spec for key, spec in DRIVE_SPECS.items() if re.fullmatch(rf"{re.escape(key)} \d+(GB|TB)", chip.get("model") or "")), None)
        if drive is not None:
            parts.append(f"{chip['model'].rsplit(' ', 1)[0]}'s data sheet: {drive['operating']} in operation")
            url = url or drive["url"]
        return self.sourced(" · ".join(parts), url)

    def fans(self) -> list[DiagnosticStatus]:
        """Every hwmon fan, one check per chip in its device's folder: RPM, the duty its PWM output drives it at (100 % = full
        speed: Zach's "are the fans at maximum?") and, where the chip knows it, the share of its maximum RPM. A fan's level is
        the chip's own alarm (``fanN_alarm``, typically a fan under its ``fanN_min``), never a number of ours: a fan the board
        stopped on purpose (0 RPM at 0 % duty) is not a fault. The GPU's fans are NVML's, on the GPU's check."""

        chips = [chip for chip in self.chips() if chip["fans"]]
        if not chips:
            return [self.status("Fans", OK, "not available: no fan sensor in /sys/class/hwmon (the board's fan controller needs a"
                                            " Linux driver for that)", device="Board")]
        out = []
        for chip in chips:
            pwms = {pwm["channel"].removeprefix("pwm"): pwm for pwm in chip["pwms"]}
            measurements, texts, alarms = [], [], []
            for fan in chip["fans"]:
                number = fan["channel"].removeprefix("fan")
                key = f"{fan['label']} ({fan['channel']})" if fan["label"] else fan["channel"]
                pwm = pwms.get(number)
                duty = pwm["duty_percent"] if pwm else None
                measurements.append(Measurement(key, fan["rpm"], "rpm"))
                text = f"{key} {fan['rpm']} RPM" if fan["rpm"] is not None else f"{key} no reading"
                if duty is not None:
                    measurements.append(Measurement(f"{key} duty", duty, "%"))
                    text += f" at {duty:.0f} % duty"
                if (duty is not None and duty >= 100) or (pwm and pwm.get("enable") == 0):
                    text += " (full speed)"
                if fan.get("max") and fan["rpm"] is not None:
                    share = round(fan["rpm"] / fan["max"] * 100, 1)
                    measurements.append(Measurement(f"{key} of max", share, "%"))
                    text += f" ({share:.0f} % of its {fan['max']} RPM)"
                (alarms if fan.get("alarm") else texts).append(text)
            control = [f"{pwm['channel']} {'full speed (no control)' if pwm['enable'] == 0 else 'manual' if pwm['enable'] == 1 else 'automatic'}"
                       for pwm in chip["pwms"] if pwm.get("enable") is not None]
            values = [KeyValue("chip", chip["name"])] + ([KeyValue("control", " · ".join(control))] if control else [])
            watched = [f"{fan['channel']}_alarm" for fan in chip["fans"] if fan.get("alarm") is not None]
            if watched:
                values += self.sourced(f"the chip's own fan alarms ({', '.join(watched)}): a warning only when the chip raises one,"
                                       " typically a fan turning slower than its fanN_min")
            device, check = device_of(chip), f"Fans {chip['name']}"
            if alarms:
                out.append(self.status(check, WARN, "Fan alarm: " + " · ".join([*(f"{text} (the chip's alarm)" for text in alarms), *texts]),
                                       device=device, code="FanAlarm", values=values, measurements=measurements))
            else:
                out.append(self.status(check, OK, " · ".join(texts), device=device, values=values, measurements=measurements))
        return out

    def gpus(self) -> list[DiagnosticStatus]:
        found = self.probe.gpus()
        if "missing" in found:
            return [self.status("GPU", OK, f"not available ({found['missing']})")]
        if "error" in found:
            # WHY a warning: nvidia-smi present but no answer is the driver gone (a kernel update or a BIOS flash that
            # wiped the module-signing key, bam_ws memory bios-flash-wipes-mok-nvidia), and nothing on the GPU will run
            return [self.status("GPU", WARN, f"nvidia-smi does not answer: {found['error']}", code="GpuDriverNotAnswering",
                                values=[KeyValue("what_to_check", "1. Run nvidia-smi in a terminal\n2. After a kernel or BIOS"
                                                                  " update: is the nvidia module loaded (lsmod | grep nvidia)?")])]
        if not found["gpus"]:
            return [self.status("GPU", OK, "not available (the NVIDIA driver lists no GPU)")]
        return [self._gpu(gpu) for gpu in found["gpus"]]

    def _gpu(self, gpu: dict) -> DiagnosticStatus:
        """One NVIDIA GPU, in its own folder ("GPU 0"): its temperature against the limits IT reports, its fans against their
        maximum (Zach: "if the temperature is that then are the fans at maximum?"), power against its enforced limit, clocks,
        and what holds the clocks back. WARN also when the GPU itself says it slows down for heat or a power brake."""

        index, via = gpu["index"], "NVML" if gpu.get("source") == "nvml" else "nvidia-smi -q"
        device = f"GPU {index}"
        # the GPU's own limits (black box): above its max operating temperature it is out of spec (WARN), at its slowdown
        # temperature it slows itself in hardware (ERROR); without a max operating figure, slowdown / shutdown
        mine = gpu.get("max_operating") is not None
        warn, error = (gpu.get("max_operating"), gpu.get("slowdown")) if mine else (gpu.get("slowdown"), gpu.get("shutdown"))
        warn_word, error_word = ("max operating", "slowdown") if mine else ("slowdown", "shutdown")
        if warn is not None and error is not None and warn > error:
            warn = None
        used, total = gpu.get("memory_used_mib"), gpu.get("memory_total_mib")
        used_percent = used / total * 100.0 if used is not None and total else None
        temperature = Measurement("temperature", gpu.get("temperature"), "Cel", warn_high=warn, error_high=error)
        measurements = [Measurement("utilization", gpu.get("utilization"), "%"),
                        Measurement("memory used", _round(used, 1), "MiBy"), Measurement("memory total", _round(total, 1), "MiBy"),
                        Measurement("memory used %", _round(used_percent, 2), "%"), temperature]
        fans = gpu.get("fans") or []
        measurements += [Measurement(f"fan {number}", fan.get("percent"), "%") for number, fan in enumerate(fans, start=1)]
        if gpu.get("acoustic_target") is not None:
            measurements.append(Measurement("fan target temperature", gpu["acoustic_target"], "Cel"))
        for key, field, unit in (("power", "power_w", "W"), ("power limit", "power_limit_w", "W"),
                                 ("SM clock", "clock_sm_mhz", "MHz"), ("memory clock", "clock_mem_mhz", "MHz")):
            if gpu.get(field) is not None:
                measurements.append(Measurement(key, _round(gpu[field], 1), unit))
        reasons = gpu.get("reasons")
        held = [words for bit, words in CLOCK_REASONS if reasons is not None and reasons & bit]
        slowing = [words for bit, words in CLOCK_REASONS if reasons is not None and reasons & bit & SLOWDOWN_REASONS]
        values = [KeyValue("uuid", gpu.get("uuid") or ""), KeyValue("pci", gpu["bus_id"]),
                  KeyValue("reading", "NVML (nvidia-ml-py)" if via == "NVML" else "nvidia-smi (the NVML binding is not installed)")]
        if fans:
            values.append(KeyValue("fan_scale", "each fan's intended speed as a share of its maximum (NVML: \"a percentage of the"
                                                " product's maximum noise tolerance fan speed\"; it can exceed 100 %)"
                                   + (f"; the driver runs them between {gpu['fan_min_percent']} and {gpu['fan_max_percent']} %"
                                      if gpu.get("fan_min_percent") is not None and gpu.get("fan_max_percent") is not None else "")))
        if gpu.get("clock_sm_mhz") is not None and gpu.get("clock_mem_mhz") is not None:
            values.append(KeyValue("clocks", f"SM {gpu['clock_sm_mhz']:g} of {gpu.get('clock_sm_max_mhz') or '?'} MHz · memory"
                                             f" {gpu['clock_mem_mhz']:g} of {gpu.get('clock_mem_max_mhz') or '?'} MHz"))
        if reasons is not None:
            values.append(KeyValue("held_back_by", ", ".join(held) or "nothing"))
        values += self._gpu_source(gpu, via, warn, error, warn_word, error_word)
        reading = " · ".join(text for text in (
            f"{gpu['utilization']:.0f} %" if gpu.get("utilization") is not None else "",
            f"{used:.0f} of {total:.0f} MiB" if used is not None and total is not None else "",
            f"{gpu['temperature']:.0f} °C" if gpu.get("temperature") is not None else "",
            _fan_text(fans),
            (f"{gpu['power_w']:.0f} of {gpu['power_limit_w']:.0f} W" if gpu.get("power_limit_w") is not None else f"{gpu['power_w']:.0f} W")
            if gpu.get("power_w") is not None else "") if text)
        if temperature.level == ERROR:
            return self.status(gpu["name"], ERROR, f"GPU {index} {gpu['temperature']:.0f} °C: Critical Temperature (above its"
                                                   f" {error_word} {error:g} °C)", device=device, code="CriticalTemperature",
                               values=values, measurements=measurements)
        if temperature.level == WARN:
            return self.status(gpu["name"], WARN, f"GPU {index} {gpu['temperature']:.0f} °C: High Temperature (above its"
                                                  f" {warn_word} {warn:g} °C)", device=device, code="HighTemperature",
                               values=values, measurements=measurements)
        if slowing:
            return self.status(gpu["name"], WARN, f"GPU {index} is slowing itself: {', '.join(slowing)} · {reading}", device=device,
                               code="GpuSlowingDown", values=values, measurements=measurements)
        return self.status(gpu["name"], OK, reading or "no reading", device=device, values=values, measurements=measurements)

    @staticmethod
    def _gpu_source(gpu: dict, via: str, warn, error, warn_word: str, error_word: str) -> list[KeyValue]:
        spec = GPU_SPECS.get(gpu["name"])
        if warn is None and error is None:
            return Checks.sourced(f"none: the GPU reports no temperature limit ({via})", spec["url"] if spec else NVML_HEADER_URL)
        said = [f"warn above its {warn_word} temperature {warn:g} °C" if warn is not None else "",
                f"error above its {error_word} temperature {error:g} °C" if error is not None else ""]
        text = f"the GPU reports them ({via}): " + ", ".join(part for part in said if part)
        if gpu.get("shutdown") is not None and error_word != "shutdown":
            text += f"; it shuts itself down at {gpu['shutdown']:g} °C"
        if gpu.get("acoustic_target") is not None:
            text += f"; its fans aim to hold {gpu['acoustic_target']:g} °C"
        if spec:
            text += f" · NVIDIA's spec page lists {spec['max_c']:g} °C as the {gpu['name']}'s maximum GPU temperature"
        return Checks.sourced(text, spec["url"] if spec else NVML_HEADER_URL)

    def clock(self) -> list[DiagnosticStatus]:
        found = self.probe.clock()
        if found is None:
            return [self.status("NTP offset", OK, "not available (no timedatectl or chronyc)")]
        offset = found.get("offset_s")
        error_limit, warn_limit = self.options.ntp_error_offset_s, self.options.ntp_warn_offset_s
        measurement = Measurement("offset", _round(offset, 6), "s", error_low=-error_limit, error_high=error_limit,
                                  warn_low=-warn_limit if warn_limit is not None else None, warn_high=warn_limit)
        values = [KeyValue("source", found["source"]), KeyValue("server", found.get("server") or "unknown"),
                  KeyValue("synchronized", {True: "yes", False: "no", None: "unknown"}[found.get("synced")]),
                  *self.sourced(f"error beyond ±{error_limit:g} s: "
                                + ("ROS ntp_monitor's error_offset (5,000,000 µs)" if error_limit == NTP_ERROR_OFFSET_S else
                                   f"--ntp-error-offset-s (ROS ntp_monitor's default is {NTP_ERROR_OFFSET_S:g} s)")
                                + (f"; warn beyond ±{warn_limit:g} s (--ntp-warn-offset-s)" if warn_limit is not None else
                                   "; warn when the clock daemon itself says the clock is not synchronized (ROS's 500 µs warning"
                                   " assumes a LAN time server)"), NTP_LIMIT_URL)]
        offset_text = f"offset {offset * 1e3:+.1f} ms" if offset is not None else "offset not reported"
        where = ", ".join(text for text in (found.get("server"), found["source"]) if text)
        if measurement.level == ERROR:
            return [self.status("NTP offset", ERROR, f"NTP offset above error threshold: abs({offset:+.3f} s) > {error_limit:g} s ({where})",
                                code="ClockOffsetTooLarge", values=values, measurements=[measurement])]
        if found.get("synced") is False:
            service = {True: "the NTP service is on", False: "the NTP service is off", None: ""}[found.get("ntp_enabled")]
            return [self.status("NTP offset", WARN, f"clock not synchronized ({found['source']}{'; ' + service if service else ''})",
                                code="ClockNotSynchronized", values=values, measurements=[measurement])]
        if measurement.level == WARN:
            return [self.status("NTP offset", WARN, f"NTP offset above threshold: abs({offset * 1e3:+.1f} ms) > {warn_limit:g} s ({where})",
                                code="ClockOffsetHigh", values=values, measurements=[measurement])]
        state = "synchronized" if found.get("synced") else "synchronization unknown"
        return [self.status("NTP offset", OK, f"{state} · {offset_text} ({where})", values=values, measurements=[measurement])]

    def uptime(self) -> list[DiagnosticStatus]:
        booted = self.probe.boot_time()
        up = max(self.now - booted, 0.0)
        when = dt.datetime.fromtimestamp(booted).astimezone().strftime("%Y-%m-%d %H:%M")
        return [self.status("Uptime", OK, f"up {_duration(up)} (booted {when})", measurements=[Measurement("uptime", round(up), "s")],
                            uptime_s=round(up, 3))]

    def network(self) -> list[DiagnosticStatus]:
        for reading in (self.net_before, self.net_after):
            if isinstance(reading, Exception):
                raise reading
        stats = self.probe.net_stats()
        nics = [nic for nic, (isup, _speed) in stats.items() if isup and not nic.startswith(VIRTUAL_NICS)]
        down = [nic for nic, (isup, _speed) in stats.items() if not isup and not nic.startswith(VIRTUAL_NICS)]
        if not nics:
            return [self.status("Network Usage", OK, "not available (no interface up)" + (f" · down: {', '.join(down)}" if down else ""),
                                device="Network")]
        measurements, texts, saturated = [], [], []
        for nic in nics:
            before, after = self.net_before.get(nic), self.net_after.get(nic)
            if before is None or after is None:
                continue
            rx = max(after[0] - before[0], 0) * 8 / self.elapsed_s / 1e6
            tx = max(after[1] - before[1], 0) * 8 / self.elapsed_s / 1e6
            speed = stats[nic][1]
            warn = round(speed * self.options.net_warn_fraction, 3) if speed > 0 else None
            rx_m = Measurement(f"{nic} rx", round(rx, 3), "Mbit/s", warn_high=warn)
            tx_m = Measurement(f"{nic} tx", round(tx, 3), "Mbit/s", warn_high=warn)
            measurements += [rx_m, tx_m, Measurement(f"{nic} rx total", round(after[0] / MIB, 1), "MiBy"),
                             Measurement(f"{nic} tx total", round(after[1] / MIB, 1), "MiBy")]
            if speed > 0:
                measurements.append(Measurement(f"{nic} link speed", speed, "Mbit/s"))
            link = f" of {speed:g} Mbit/s" if speed > 0 else ""
            texts.append(f"{nic} rx {rx:.2f} · tx {tx:.2f} Mbit/s{link}")
            if WARN in (rx_m.level, tx_m.level):
                saturated.append(f"{nic} {max(rx, tx):.0f} of {speed:g} Mbit/s")
        values = [KeyValue("interfaces", ", ".join(nics))] + ([KeyValue("down", ", ".join(down))] if down else [])
        values += self.sourced(f"warn above {self.options.net_warn_fraction * 100:g} % of each interface's own link speed: the speed is"
                               " the card's (it reports what it linked at), the share is RobotDx's own choice; ROS 2 has no network"
                               " monitor to borrow one from")
        if saturated:
            return [self.status("Network Usage", WARN, f"High Network Usage: {'; '.join(saturated)} (warn above"
                                                       f" {self.options.net_warn_fraction * 100:g} % of the link)", device="Network",
                                code="HighNetworkUsage", values=values, measurements=measurements)]
        return [self.status("Network Usage", OK, " · ".join(texts) or "no counters", device="Network", values=values,
                            measurements=measurements)]

    def usb(self) -> list[DiagnosticStatus]:
        buses = self.probe.usb_buses()
        if not buses:
            return [self.status("USB Bandwidth", OK, "not available (no USB buses in sysfs)")]
        measurements, busy, worst = [], [], None
        for bus in buses:
            speed = bus.get("speed_mbps")
            claimed = bus["claimed_bps"] / 1e6
            measurements.append(Measurement(f"usb{bus['bus']} claimed", round(claimed, 3), "Mbit/s"))
            if not speed:
                continue
            budget = USB_PERIODIC_BUDGET.get(int(speed), USB_PERIODIC_BUDGET_DEFAULT)
            share = Measurement(f"usb{bus['bus']} claimed %", round(claimed / speed * 100.0, 2), "%", warn_high=budget)
            measurements += [Measurement(f"usb{bus['bus']} capacity", speed, "Mbit/s"), share]
            if worst is None or share.value > worst[1].value:
                worst = (bus, share, speed)
            if share.level == WARN:
                busy.append(f"usb{bus['bus']} {share.value:.0f} % of {speed:g} Mbit/s (budget {budget:g} %)")
        values = [KeyValue("method", "periodic (interrupt + isochronous) endpoints of each device's active setting, from sysfs;"
                                     " bulk reserves nothing; USB 3 bursts are not in sysfs, so USB 3 figures are a lower bound"),
                  *self.sourced("warn above the USB periodic budget: 80 % of a high-speed (480 Mb/s) bus's microframe, 90 % of any"
                                " other bus's frame (USB 2.0 spec §5.6.4 and §5.7.4)", USB_SPEC_URL)]
        if busy:
            return [self.status("USB Bandwidth", WARN, f"USB periodic bandwidth over budget: {'; '.join(busy)}",
                                code="UsbBandwidthHigh", values=values, measurements=measurements)]
        text = (f"{len(buses)} buses · busiest usb{worst[0]['bus']} {worst[1].value:.1f} % of {worst[2]:g} Mbit/s claimed"
                if worst else f"{len(buses)} buses")
        return [self.status("USB Bandwidth", OK, text, values=values, measurements=measurements)]

    def processes(self) -> list[DiagnosticStatus]:
        """follow only: the top ``TOP_N`` processes by CPU and by memory. Always OK: what runs is not a fault.

        CPU % is the share of the WHOLE machine (psutil's per-core figure divided by the logical CPU count), as GNOME
        System Monitor shows it by default ("Divide CPU usage by CPU count"). The two tables are values, tab-separated
        with a header line ``pid  name  cpu_percent  rss_bytes``, readable as text and parseable by a page."""

        procs = self.probe.processes()
        logical = (self.probe.cpu_counts()[0] or 1) if procs else 1
        rows = [{**proc, "share": float(proc["cpu_percent"]) / logical, "name": str(proc["name"]).replace("\t", " ")}
                for proc in procs]
        by_cpu = sorted(rows, key=lambda row: (-row["share"], row["pid"]))[:TOP_N]
        by_memory = sorted(rows, key=lambda row: (-row["rss"], row["pid"]))[:TOP_N]

        def table(chosen: list[dict]) -> str:
            return "\n".join(["pid\tname\tcpu_percent\trss_bytes",
                              *(f"{row['pid']}\t{row['name']}\t{row['share']:.1f}\t{row['rss']}" for row in chosen)])

        if not rows:
            return [self.status("Processes", OK, "not available (no processes listed)")]
        busiest, largest = by_cpu[0], by_memory[0]
        message = (f"{len(rows)} processes · busiest {busiest['name']} (pid {busiest['pid']}) {busiest['share']:.1f} % CPU"
                   f" · largest {largest['name']} (pid {largest['pid']}) {largest['rss'] / GIB:.1f} GiB")
        measurements = [Measurement("processes", len(rows), "1"), Measurement("busiest process CPU", round(busiest["share"], 2), "%"),
                        Measurement("largest process RSS", round(largest["rss"] / MIB, 1), "MiBy")]
        return [self.status("Processes", OK, message, values=[KeyValue("top_cpu", table(by_cpu)),
                                                             KeyValue("top_memory", table(by_memory))],
                            measurements=measurements)]

    def builders(self) -> tuple:
        """(check, device, build): ``check`` keys the follow cadence; ``device`` names a check that cannot be read (None = the
        machine itself). A build that names several devices (temperatures) names each of its statuses itself."""

        return (("CPU Information", "CPU", self.cpu), ("RAM Information", "RAM", self.ram), ("HD Usage", "Disks", self.disks),
                ("Temperatures", None, self.temperatures), ("Fans", "Board", self.fans), ("GPU", None, self.gpus),
                ("NTP offset", None, self.clock), ("Uptime", None, self.uptime), ("Network Usage", "Network", self.network),
                ("USB Bandwidth", None, self.usb))

    def all(self) -> list[DiagnosticStatus]:
        self.sample()
        out = []
        for check, device, build in self.builders():
            out += self.guarded(check, device, build)
        return out

    def tick(self, due: set[str]) -> list[DiagnosticStatus]:
        """follow: one tick, only the checks in ``due``, each stamped with its own cadence as its promise."""

        self.sample()
        out = []
        for check, device, build in (*self.builders(), ("Processes", None, self.processes)):
            if check in due:
                period = FOLLOW_CADENCE.get(check, self.options.period_s)
                out += [dataclasses.replace(status, expected_period_s=period) for status in self.guarded(check, device, build)]
        return out


# Each check's period in follow (seconds); a check not listed runs every tick
FOLLOW_CADENCE = {"Processes": PROCESSES_PERIOD_S, "NTP offset": NTP_PERIOD_S}


# ----- the plugin boundary -----------------------------------------------------------------------------------------
def describe() -> dict:
    return {"name": NAME, "version": VERSION, "schema": SCHEMA, "record_version": RECORD_VERSION, "matches": ["host"],
            "actions": [], "follow": {"period_s": FOLLOW_PERIOD_S},
            # the entity page's extra tab (docs/state-config-v1.md section 7): the top processes the follow reports, drawn as a
            # sortable table from the two tab-separated values of the "Processes" status (header line: pid name cpu_percent rss_bytes)
            "tabs": [{"id": "processes", "title": "Processes", "path": "", "kind": "table", "status": "Processes",
                      "values": ["top_cpu", "top_memory"],
                      "description": "the busiest processes by CPU and by memory, like System Monitor's Processes tab"}],
            "description": "the Computer, grouped by device: CPU, RAM, disks, GPU (NVML), board temperatures and fans, network;"
                           " clock, uptime, USB bandwidth (follow: the same once a second, plus the top processes)"}


def host_name() -> str:
    try:
        device = json.loads(os.environ.get("IO_DIAGNOSTICS_DEVICE") or "{}")
    except json.JSONDecodeError:
        device = {}
    return str(device.get("label") or "").strip() or socket.gethostname()


def diagnose(probe=None, options: Options | None = None, host: str | None = None) -> list[DiagnosticStatus]:
    return Checks(probe or LiveProbe(), host or host_name(), options or Options()).all()


def follow(options: Options, probe=None, host: str | None = None, *, ticks: int | None = None, out=None,
           sleep: Callable[[float], None] = time.sleep) -> int:
    """``follow``: a tick every ``options.period_s`` (default 1 s), each a batch of JSON lines, until killed (or
    ``ticks`` ticks, for tests). The first tick waits one period after priming the CPU and network counters."""

    out = out or sys.stdout
    probe = probe or LiveProbe(follow=True)
    host = host or host_name()
    period = options.period_s
    last: dict[str, float] = {}
    try:
        probe.cpu_percent(None)  # prime: psutil measures from this call to the next
        if hasattr(probe, "processes"):
            probe.processes()
        window = (Checks._try(probe.net_counters), probe.monotonic())
        next_at = window[1] + period
        count = 0
        while ticks is None or count < ticks:
            sleep(max(next_at - probe.monotonic(), 0.0))
            checks = Checks(probe, host, options, window=window)
            now = probe.monotonic()
            due = {check for check, *_rest in (*checks.builders(), ("Processes",))
                   if now - last.get(check, -1e18) >= FOLLOW_CADENCE.get(check, period) - period / 2}
            for status in checks.tick(due):
                out.write(json.dumps(to_dict(status)) + "\n")
            out.flush()
            for check in due:
                last[check] = now
            window = (checks.net_after, checks.ended)
            next_at = max(next_at + period, probe.monotonic())  # WHY max: a slow tick never makes a burst of catch-up ticks
            count += 1
    except BrokenPipeError:  # the host closed our stdout: it is done with us
        pass
    finally:
        if hasattr(probe, "close"):
            probe.close()
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="computer_diagnostics", description=__doc__.split("\n\n")[0])
    top.add_argument("--sample-s", type=float, default=SAMPLE_S, help="CPU / network sampling window (s)")
    top.add_argument("--cpu-warn-percent", type=float, default=CPU_WARN_PERCENT)
    top.add_argument("--ram-warn-percent", type=float, default=RAM_WARN_PERCENT)
    top.add_argument("--free-percent-low", type=float, default=FREE_PERCENT_LOW)
    top.add_argument("--free-percent-crit", type=float, default=FREE_PERCENT_CRIT)
    top.add_argument("--temp-warn", type=float, default=TEMP_WARN_DEFAULT, help="°C, only for a CPU sensor that reports no limit")
    top.add_argument("--temp-error", type=float, default=TEMP_ERROR_DEFAULT,
                     help="°C, only for a CPU sensor that reports no limit; a set value beats the CPU's published Tjmax")
    top.add_argument("--ntp-error-offset-s", type=float, default=NTP_ERROR_OFFSET_S)
    top.add_argument("--ntp-warn-offset-s", type=float, default=None)
    top.add_argument("--net-warn-fraction", type=float, default=NET_WARN_FRACTION)
    top.add_argument("--period-s", type=float, default=PERIOD_S, help="must equal plugins.d/computer.toml period_s")
    verbs = top.add_subparsers(dest="verb", required=True)
    verbs.add_parser("describe")
    run = verbs.add_parser("diagnose")
    run.add_argument("--device", default="host")
    followed = verbs.add_parser("follow", help="the same statuses every --interval-s, forever (plus Processes)")
    followed.add_argument("--device", default="host")
    followed.add_argument("--interval-s", type=float, default=FOLLOW_PERIOD_S)
    act = verbs.add_parser("act")
    act.add_argument("--device", default="host")
    act.add_argument("--action", required=True)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv, namespace=Options())
    if args.verb == "describe":
        print(json.dumps(describe()))
        return 0
    if args.verb == "follow":
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so the finally stops the nvidia-smi loop
        args.period_s = args.interval_s
        return follow(args)
    if args.verb == "act":
        print(json.dumps({"record": "action-result", "action": args.action, "message": "the computer plugin registers no actions"}))
        return 2
    for status in diagnose(options=args):
        print(json.dumps(to_dict(status)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
