"""IO diagnostics plugin: the Computer's own health, as statuses on THE host entity (Zach, 2026-10-23: "in our entity
chart we kinda have the Computer as an entity and those diagnostics can be associated with it there").

    uv run --project /home/bam/io-diagnostics/plugins/computer python -m computer_diagnostics describe
    uv run --project /home/bam/io-diagnostics/plugins/computer python -m computer_diagnostics diagnose --device host

Boundary: docs/build-plan-v1.md "Plugin boundary" (plugins.d/computer.toml, ``matches = ["host"]``). Passive and
read-only: psutil (``/proc``, ``/sys``), sysfs text files for USB endpoints, and three read-only tools as subprocesses
(``nvidia-smi``, ``timedatectl``, ``chronyc``). The plugin process opens no ``/dev`` node (tests pin it), so it can
never hold a port the rack needs.

Statuses mirror ROS's stock ``diagnostic_common_diagnostics`` monitors (ros/diagnostics, ros2 branch; local copy
/home/bam/public_ws/src/diagnostics/diagnostic_common_diagnostics), so a record reads like ROS's: their names, their
measured values and their default thresholds where sensible; every departure says why beside it.

| status | ROS source | level |
|---|---|---|
| ``CPU Information`` | cpu_monitor.py ``CpuTask`` | WARN above 90 % (``warning_percentage``), judged on the average |
| ``RAM Information`` | ram_monitor.py ``RamTask`` | WARN above 90 % (``warning_percentage``) |
| ``HD Usage`` | hd_monitor.py ``HDMonitor`` | WARN at 5 % free (``free_percent_low``), ERROR at 1 % (``free_percent_crit``) |
| ``Temperature <chip>`` | sensors_monitor.py | WARN above the sensor's own ``high``, ERROR above its ``critical`` |
| ``GPU <n> <name>`` | (none in ROS) | the GPU's own limits from nvidia-smi |
| ``NTP offset`` | ntp_monitor.py | WARN when the clock daemon says unsynchronized, ERROR beyond 5 s (``error_offset``) |
| ``Uptime`` | (none) | always OK |
| ``Network Usage`` | (none in ROS 2) | WARN above 90 % of the NIC's own link speed |
| ``USB Bandwidth`` | (none) | WARN above the USB spec's periodic budget (80 % at 480 Mb/s, else 90 %) |

Every number is also a ``Measurement`` (unit + limits) so a history view can draw it; per-core loads, per-NIC rates
and per-bus USB bandwidth are separate measurements (Zach's reference: the ROS 2 System Webview).

Quiet when OK; a reading the machine does not have (no NVIDIA GPU, no hwmon sensor, no clock tool) is an OK
"not available", never an error. A reading that throws is UNKNOWN ``ReadFailed`` with the reason.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
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
# Only when a sensor reports no high / critical of its own (k10temp on a Ryzen reports none). WHY 85 / 95: a Ryzen 7000
# boosts up to its 95 °C Tjmax by design; sustained above 85 °C is worth a look, past 95 °C it is over its own limit.
TEMP_WARN_DEFAULT = 85.0
TEMP_ERROR_DEFAULT = 95.0
TEMP_SANE_MAX = 200.0  # a hwmon limit above this is a sentinel (nvme "Sensor 1" reports 65261.85 °C), not a limit
# ROS ntp_monitor.py: error_offset = 5000000 us. Its WARN offset (500 us) assumes a LAN NTP server; systemd-timesyncd
# over the internet sits at a few ms (bam-GPU: +1.5 ms, 132 ms delay), so WARN is the clock daemon's own verdict
# (NTPSynchronized / chrony leap status) instead; --ntp-warn-offset-s sets a number when wanted.
NTP_ERROR_OFFSET_S = 5.0
# Network: a NIC above this share of its own link speed is saturated (our default; ROS 2 has no network monitor).
NET_WARN_FRACTION = 0.9
# USB 2.0 spec §5.7.4 / §5.8.4: periodic transfers may claim at most 80 % of a high-speed microframe, 90 % of a
# full-speed frame; USB 3.2 likewise caps periodic at 90 %.
USB_PERIODIC_BUDGET = {480: 80.0}
USB_PERIODIC_BUDGET_DEFAULT = 90.0
# Mounts that are not a disk that can fill: snaps, RAM and kernel filesystems, network shares (statvfs can hang).
SKIP_FSTYPES = {"squashfs", "tmpfs", "devtmpfs", "ramfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2", "devpts",
                "autofs", "nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs", "fuse.portal", "fuse.gvfsd-fuse", "iso9660"}
VIRTUAL_NICS = ("lo", "veth", "docker", "br-", "virbr", "vnet")
# hwmon chip -> what a person calls it (the spec's "CPU package, NVMe, GPU")
FRIENDLY_CHIPS = {"k10temp": "CPU", "coretemp": "CPU", "zenpower": "CPU", "cpu_thermal": "CPU", "nvme": "NVMe",
                  "amdgpu": "GPU", "radeon": "GPU", "nouveau": "GPU", "acpitz": "ACPI zone"}
CHIP_ORDER = {"CPU": 0, "GPU": 1, "NVMe": 2}
READING_ORDER = {UNKNOWN: -1, OK: 0, WARN: 1, ERROR: 2}
GIB = 2 ** 30
MIB = 2 ** 20
TOOL_TIMEOUT_S = 5.0


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


class LiveProbe:
    """This computer. Each method is one reading; an exception becomes UNKNOWN ``ReadFailed`` on its status."""

    def __init__(self, sys_root: Path = Path("/sys")):
        self.sys_root = sys_root
        self._psutil = None

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

    def temperatures(self) -> dict[str, list[dict]] | None:
        if not hasattr(self.psutil, "sensors_temperatures"):
            return None
        return {chip: [{"label": item.label, "current": item.current, "high": item.high, "critical": item.critical}
                       for item in items]
                for chip, items in self.psutil.sensors_temperatures().items()}

    def gpus(self) -> dict:
        """``{"gpus": [...]}``, or ``{"missing": why}`` (no NVIDIA tool: not available), or ``{"error": why}``."""

        query = _run(["nvidia-smi", "--query-gpu=index,name,uuid,pci.bus_id,utilization.gpu,memory.used,memory.total,"
                      "temperature.gpu", "--format=csv,noheader,nounits"])
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
        return {"gpus": gpus}

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


def _duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return f"{days} d {hours:02d}:{minutes:02d}" if days else f"{hours:02d}:{minutes:02d}"


class Checks:
    """One diagnose: every status, each built from one reading of ``probe``."""

    def __init__(self, probe, host: str, options: Options):
        self.probe, self.host, self.options = probe, host, options
        self.now = probe.time()

    def status(self, check: str, level: str, message: str, *, code: str = "", values=(), measurements=(), **extra) -> DiagnosticStatus:
        return DiagnosticStatus(level=level, name=f"{self.host}: {check}", message=message, hardware_id=self.host,
                                values=tuple(values), path="", code=code if level != OK else "", stamp=self.now,
                                expected_period_s=self.options.period_s, source=SOURCE, measurements=tuple(measurements),
                                **extra)

    def guarded(self, check: str, build: Callable[[], list[DiagnosticStatus]]) -> list[DiagnosticStatus]:
        try:
            return build()
        except Exception as error:  # noqa: BLE001 - a reading that throws is that check's UNKNOWN, never the run's crash
            return [self.status(check, UNKNOWN, f"could not read: {type(error).__name__}: {error}", code="ReadFailed")]

    # --- CPU and network share one sampling window ----------------------------------------------------------------
    def sample(self) -> None:
        self.net_before = self._try(self.probe.net_counters)
        started = self.probe.monotonic()
        self.cores = self._try(lambda: self.probe.cpu_percent(self.options.sample_s))
        self.net_after = self._try(self.probe.net_counters)
        self.elapsed_s = max(self.probe.monotonic() - started, 1e-3)

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
            return [self.status("CPU Information", OK, "not available (no per-core readings)")]
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
        values = [KeyValue("cores", f"{logical or len(cores)} logical · {physical or '?'} physical"),
                  KeyValue("sample", f"{self.options.sample_s:g} s")]
        tail = f"worst core {worst:.0f} % · load {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}"
        # WHY the average and not ROS's "any core above 90 %": one pegged core is a single-threaded job on a 24-thread
        # machine, not a starved computer; the worst core stays a measurement
        if average > limit:
            return [self.status("CPU Information", WARN, f"CPU Average exceeds {limit:g} percent ({_pct(average)} %) · {tail}",
                                code="CpuAverageHigh", values=values, measurements=measurements)]
        return [self.status("CPU Information", OK, f"CPU Average {_pct(average)} percent · {tail}", values=values,
                            measurements=measurements)]

    def ram(self) -> list[DiagnosticStatus]:
        memory, swap = self.probe.virtual_memory(), self.probe.swap_memory()
        percent = float(memory["percent"])
        limit = self.options.ram_warn_percent

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
                                code="RamAverageHigh", measurements=measurements)]
        return [self.status("RAM Information", OK, f"RAM Average {_pct(percent)} percent · {tail}", measurements=measurements)]

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
            return [self.status("HD Usage", OK, "not available (no disk mounts)")]
        low, crit = self.options.free_percent_low, self.options.free_percent_crit
        measurements = []
        for mountpoint, _fstype, usage, free_percent in mounts:
            measurements += [Measurement(f"{mountpoint} available", _round(free_percent, 2), "%", warn_low=low, error_low=crit),
                             Measurement(f"{mountpoint} free", round(usage["free"] / GIB, 2), "GiBy"),
                             Measurement(f"{mountpoint} total", round(usage["total"] / GIB, 2), "GiBy")]
        values = [KeyValue("mounts", ", ".join(f"{mountpoint} ({fstype})" for mountpoint, fstype, _usage, _free in mounts))]
        if skipped:
            values.append(KeyValue("not read", "; ".join(skipped)))
        fullest = min(mounts, key=lambda mount: mount[3])
        mountpoint, _fstype, usage, free_percent = fullest
        text = f"{mountpoint} {free_percent:.1f} % free ({usage['free'] / GIB:.0f} of {usage['total'] / GIB:.0f} GiB)"
        others = f" · {len(mounts) - 1} other mount{'s' if len(mounts) != 2 else ''}" if len(mounts) > 1 else ""
        # ROS hd_monitor's words: DICT_USAGE {OK: 'OK', WARN: 'Low Disk Space', ERROR: 'Very Low Disk Space'}
        if free_percent < crit:
            return [self.status("HD Usage", ERROR, f"Very Low Disk Space: {text} (error below {crit:g} %){others}",
                                code="VeryLowDiskSpace", values=values, measurements=measurements)]
        if free_percent < low:
            return [self.status("HD Usage", WARN, f"Low Disk Space: {text} (warn below {low:g} %){others}",
                                code="LowDiskSpace", values=values, measurements=measurements)]
        return [self.status("HD Usage", OK, f"{len(mounts)} mount{'s' if len(mounts) != 1 else ''} · fullest {text}",
                            values=values, measurements=measurements)]

    def temperatures(self) -> list[DiagnosticStatus]:
        chips = self.probe.temperatures()
        if not chips:
            return [self.status("Temperatures", OK, "not available (no hwmon temperature sensors)")]

        def limit(value) -> float | None:
            return float(value) if isinstance(value, (int, float)) and 0 < value <= TEMP_SANE_MAX else None

        out = []
        # CPU, GPU, NVMe first: among equal levels the host's row headlines the first check (summarize)
        for chip, sensors in sorted(chips.items(), key=lambda item: CHIP_ORDER.get(FRIENDLY_CHIPS.get(item[0], ""), len(CHIP_ORDER))):
            friendly = FRIENDLY_CHIPS.get(chip)
            check = f"Temperature {friendly} ({chip})" if friendly else f"Temperature {chip}"
            who = friendly or chip
            measurements, readings, keys = [], [], set()
            for index, sensor in enumerate(sensors, start=1):
                key = str(sensor.get("label") or "").strip() or f"temp {index}"
                while key in keys:
                    key = f"{key} #{index}"
                keys.add(key)
                high, critical = limit(sensor.get("high")), limit(sensor.get("critical"))
                if high is not None and critical is not None and high > critical:
                    high = None  # a chip that reports high above critical: keep the stricter one (validate's ordering)
                own = high is not None or critical is not None
                warn, error = (high, critical) if own else (self.options.temp_warn, self.options.temp_error)
                current = sensor.get("current")
                measurement = Measurement(key, _round(current, 2), "Cel", warn_high=warn, error_high=error)
                measurements.append(measurement)
                readings.append((measurement, own))
            # a sensor with no reading never outranks a hot one (RANK puts UNKNOWN above WARN)
            worst_measurement, own = max(readings, key=lambda reading: (READING_ORDER[reading[0].level],
                                                                        reading[0].value if reading[0].value is not None else -1e9))
            level = worst_measurement.level
            value = worst_measurement.value
            source = "its own limit" if own else "default: the sensor reports none"
            values = [KeyValue("chip", chip), KeyValue("limits", "hardware high / critical" if all(own for _m, own in readings)
                                                        else "defaults where the sensor reports none"
                                                        f" (warn {self.options.temp_warn:g} °C, error {self.options.temp_error:g} °C)")]
            if level == ERROR:
                out.append(self.status(check, ERROR, f"{who} {worst_measurement.key} {value:.0f} °C: Critical Temperature (above"
                                                     f" {worst_measurement.error_high:g} °C, {source})", code="CriticalTemperature",
                                       values=values, measurements=measurements))
            elif level == WARN:
                out.append(self.status(check, WARN, f"{who} {worst_measurement.key} {value:.0f} °C: High Temperature (above"
                                                    f" {worst_measurement.warn_high:g} °C, {source})", code="HighTemperature",
                                       values=values, measurements=measurements))
            elif level == UNKNOWN:
                out.append(self.status(check, OK, f"{who}: not available (no reading)", values=values, measurements=measurements))
            else:
                out.append(self.status(check, OK, f"{who} {worst_measurement.key} {value:.0f} °C", values=values,
                                       measurements=measurements))
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
            return [self.status("GPU", OK, "not available (nvidia-smi lists no GPU)")]
        out = []
        for gpu in found["gpus"]:
            check = f"GPU {gpu['index']} {gpu['name']}"
            # the GPU's own limits (black box): above its max operating temperature it is out of spec (WARN), at its
            # slowdown temperature it throttles hard (ERROR); without a max operating figure, slowdown / shutdown
            warn, error = ((gpu.get("max_operating"), gpu.get("slowdown")) if gpu.get("max_operating") is not None
                           else (gpu.get("slowdown"), gpu.get("shutdown")))
            if warn is not None and error is not None and warn > error:
                warn = None
            used, total = gpu.get("memory_used_mib"), gpu.get("memory_total_mib")
            used_percent = used / total * 100.0 if used is not None and total else None
            temperature = Measurement("temperature", gpu.get("temperature"), "Cel", warn_high=warn, error_high=error)
            measurements = [Measurement("utilization", gpu.get("utilization"), "%"),
                            Measurement("memory used", used, "MiBy"), Measurement("memory total", total, "MiBy"),
                            Measurement("memory used %", _round(used_percent, 2), "%"), temperature]
            limits = ", ".join(f"{label} {gpu[key]:g} °C" for key, label in (("max_operating", "max operating"),
                                                                              ("slowdown", "slowdown"), ("shutdown", "shutdown"))
                               if gpu.get(key) is not None)
            values = [KeyValue("uuid", gpu["uuid"]), KeyValue("pci", gpu["bus_id"]),
                      KeyValue("limits", f"nvidia-smi: {limits}" if limits else "nvidia-smi reports none")]
            reading = " · ".join(text for text in (
                f"{gpu['utilization']:.0f} %" if gpu.get("utilization") is not None else "",
                f"{used:.0f} of {total:.0f} MiB" if used is not None and total is not None else "",
                f"{gpu['temperature']:.0f} °C" if gpu.get("temperature") is not None else "") if text)
            if temperature.level == ERROR:
                out.append(self.status(check, ERROR, f"GPU {gpu['index']} {gpu['temperature']:.0f} °C: Critical Temperature"
                                                     f" (above its slowdown {error:g} °C)", code="CriticalTemperature",
                                       values=values, measurements=measurements))
            elif temperature.level == WARN:
                out.append(self.status(check, WARN, f"GPU {gpu['index']} {gpu['temperature']:.0f} °C: High Temperature"
                                                    f" (above its max operating {warn:g} °C)", code="HighTemperature",
                                       values=values, measurements=measurements))
            else:
                out.append(self.status(check, OK, reading or "no reading", values=values, measurements=measurements))
        return out

    def clock(self) -> list[DiagnosticStatus]:
        found = self.probe.clock()
        if found is None:
            return [self.status("NTP offset", OK, "not available (no timedatectl or chronyc)")]
        offset = found.get("offset_s")
        error_limit, warn_limit = self.options.ntp_error_offset_s, self.options.ntp_warn_offset_s
        measurement = Measurement("offset", _round(offset, 6), "s", error_low=-error_limit, error_high=error_limit,
                                  warn_low=-warn_limit if warn_limit is not None else None, warn_high=warn_limit)
        values = [KeyValue("source", found["source"]), KeyValue("server", found.get("server") or "unknown"),
                  KeyValue("synchronized", {True: "yes", False: "no", None: "unknown"}[found.get("synced")])]
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
            return [self.status("Network Usage", OK, "not available (no interface up)" + (f" · down: {', '.join(down)}" if down else ""))]
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
        if saturated:
            return [self.status("Network Usage", WARN, f"High Network Usage: {'; '.join(saturated)} (warn above"
                                                       f" {self.options.net_warn_fraction * 100:g} % of the link)",
                                code="HighNetworkUsage", values=values, measurements=measurements)]
        return [self.status("Network Usage", OK, " · ".join(texts) or "no counters", values=values, measurements=measurements)]

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
                                     " bulk reserves nothing; USB 3 bursts are not in sysfs, so USB 3 figures are a lower bound")]
        if busy:
            return [self.status("USB Bandwidth", WARN, f"USB periodic bandwidth over budget: {'; '.join(busy)}",
                                code="UsbBandwidthHigh", values=values, measurements=measurements)]
        text = (f"{len(buses)} buses · busiest usb{worst[0]['bus']} {worst[1].value:.1f} % of {worst[2]:g} Mbit/s claimed"
                if worst else f"{len(buses)} buses")
        return [self.status("USB Bandwidth", OK, text, values=values, measurements=measurements)]

    def all(self) -> list[DiagnosticStatus]:
        self.sample()
        out = []
        for check, build in (("CPU Information", self.cpu), ("RAM Information", self.ram), ("HD Usage", self.disks),
                             ("Temperatures", self.temperatures), ("GPU", self.gpus), ("NTP offset", self.clock),
                             ("Uptime", self.uptime), ("Network Usage", self.network), ("USB Bandwidth", self.usb)):
            out += self.guarded(check, build)
        return out


# ----- the plugin boundary -----------------------------------------------------------------------------------------
def describe() -> dict:
    return {"name": NAME, "version": VERSION, "schema": SCHEMA, "record_version": RECORD_VERSION, "matches": ["host"],
            "actions": [], "description": "the Computer: CPU, memory, disks, temperatures, GPU, clock, uptime, network, USB bandwidth"}


def host_name() -> str:
    try:
        device = json.loads(os.environ.get("IO_DIAGNOSTICS_DEVICE") or "{}")
    except json.JSONDecodeError:
        device = {}
    return str(device.get("label") or "").strip() or socket.gethostname()


def diagnose(probe=None, options: Options | None = None, host: str | None = None) -> list[DiagnosticStatus]:
    return Checks(probe or LiveProbe(), host or host_name(), options or Options()).all()


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="computer_diagnostics", description=__doc__.split("\n\n")[0])
    top.add_argument("--sample-s", type=float, default=SAMPLE_S, help="CPU / network sampling window (s)")
    top.add_argument("--cpu-warn-percent", type=float, default=CPU_WARN_PERCENT)
    top.add_argument("--ram-warn-percent", type=float, default=RAM_WARN_PERCENT)
    top.add_argument("--free-percent-low", type=float, default=FREE_PERCENT_LOW)
    top.add_argument("--free-percent-crit", type=float, default=FREE_PERCENT_CRIT)
    top.add_argument("--temp-warn", type=float, default=TEMP_WARN_DEFAULT, help="°C, only for a sensor that reports no limit")
    top.add_argument("--temp-error", type=float, default=TEMP_ERROR_DEFAULT, help="°C, only for a sensor that reports no limit")
    top.add_argument("--ntp-error-offset-s", type=float, default=NTP_ERROR_OFFSET_S)
    top.add_argument("--ntp-warn-offset-s", type=float, default=None)
    top.add_argument("--net-warn-fraction", type=float, default=NET_WARN_FRACTION)
    top.add_argument("--period-s", type=float, default=PERIOD_S, help="must equal plugins.d/computer.toml period_s")
    verbs = top.add_subparsers(dest="verb", required=True)
    verbs.add_parser("describe")
    run = verbs.add_parser("diagnose")
    run.add_argument("--device", default="host")
    act = verbs.add_parser("act")
    act.add_argument("--device", default="host")
    act.add_argument("--action", required=True)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv, namespace=Options())
    if args.verb == "describe":
        print(json.dumps(describe()))
        return 0
    if args.verb == "act":
        print(json.dumps({"record": "action-result", "action": args.action, "message": "the computer plugin registers no actions"}))
        return 2
    for status in diagnose(options=args):
        print(json.dumps(to_dict(status)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
