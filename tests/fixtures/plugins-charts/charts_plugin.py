"""Fixture plugin for the chart proposals: the Computer's statuses with moving series, a pure function of the wall clock.

    <python> charts_plugin.py describe
    <python> charts_plugin.py diagnose --device host
    <python> charts_plugin.py follow --device host      # a tick every 0.5 s, forever

Same status names and measurement keys as plugins/computer (CPU Information, RAM Information, Network Usage, USB
Bandwidth, Temperature k10temp) so d4 groups them like the real Computer. Every value is a sum of sines, so two
screenshots a minute apart look alike and a test can predict the shape; the temperature rises past its warn limit for
about 25 s every 150 s, and the CPU average past 90 % for about 12 s every 300 s (a real WARN, with a real message), so
a threshold line and a warning have something to show. Stdlib plus the record contract.
"""

from __future__ import annotations

import json
import math
import os
import signal
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.record import (ERROR, OK, RECORD_VERSION, SCHEMA, WARN, DiagnosticStatus, Measurement,  # noqa: E402
                             to_dict)

PERIOD_S = 0.5
CORES = 8


def bump(t: float, every: float, width: float, phase: float = 0.0) -> float:
    """0..1: a smooth pulse of ``width`` seconds once per ``every`` seconds."""

    x = ((t - phase) % every) / width
    return math.sin(math.pi * x) ** 2 if 0.0 <= x <= 1.0 else 0.0


def cpu_core(t: float, core: int) -> float:
    base = 22 + 14 * math.sin(t / 17 + core) + 8 * math.sin(t / 3.1 + core * 2.3) + 4 * math.sin(t * 1.7 + core)
    return max(1.0, min(100.0, base + 75 * bump(t, 300, 12, 40) * (0.7 + 0.3 * (core % 3) / 2)))


def values(t: float) -> dict:
    cores = [cpu_core(t, core) for core in range(CORES)]
    average = sum(cores) / CORES
    ram = 41 + 6 * math.sin(t / 55) + 1.5 * math.sin(t / 7)
    swap = 9 + 2.5 * math.sin(t / 90)
    rx = max(0.0, 180 + 140 * math.sin(t / 11) + 60 * math.sin(t / 2.3) + 400 * bump(t, 120, 8, 20))
    tx = max(0.0, 40 + 25 * math.sin(t / 13 + 1) + 10 * math.sin(t / 1.9))
    usb = 31 + 9 * math.sin(t / 23) + 3 * math.sin(t / 2.9)
    temp = 70 + 5 * math.sin(t / 40) + 1.2 * math.sin(t / 3) + 18 * bump(t, 150, 25, 60)
    return {"cores": cores, "cpu": average, "ram": ram, "swap": swap, "rx": rx, "tx": tx, "usb": usb, "temp": temp,
            "load1": average / 12.5}


def host_name() -> str:
    try:
        device = json.loads(os.environ.get("IO_DIAGNOSTICS_DEVICE") or "{}")
    except json.JSONDecodeError:
        device = {}
    return str(device.get("label") or "").strip() or socket.gethostname()


def statuses(t: float, host: str, period: float = PERIOD_S) -> list[DiagnosticStatus]:
    v = values(t)

    def status(check: str, level: str, message: str, measurements, code: str = "") -> DiagnosticStatus:
        return DiagnosticStatus(level=level, name=f"{host}: {check}", message=message, hardware_id=host, values=(), path="",
                                code=code if level != OK else "", stamp=t, expected_period_s=period, source="charts_fixture",
                                measurements=tuple(measurements))

    cpu_level = WARN if v["cpu"] > 90 else OK
    temp_level = ERROR if v["temp"] > 95 else WARN if v["temp"] > 85 else OK
    return [
        status("CPU Information", cpu_level, f"{v['cpu']:.1f} % average" + (" (above 90 %)" if cpu_level != OK else ""),
               [Measurement("CPU Load Average", round(v["cpu"], 2), "%", warn_high=90.0),
                Measurement("CPU max core load", round(max(v["cores"]), 2), "%"),
                *(Measurement(f"CPU {n} Load", round(load, 2), "%") for n, load in enumerate(v["cores"])),
                Measurement("load 1 min", round(v["load1"], 2), "1")], "HighLoad"),
        status("RAM Information", OK, f"{v['ram']:.1f} % used",
               [Measurement("RAM Load Average", round(v["ram"], 2), "%", warn_high=90.0),
                Measurement("RAM used", round(v["ram"] * 0.62, 2), "GiBy"), Measurement("RAM total", 62.0, "GiBy"),
                Measurement("swap used %", round(v["swap"], 2), "%")]),
        status("Network Usage", OK, f"eth0 down {v['rx']:.0f} / up {v['tx']:.0f} Mbit/s",
               [Measurement("eth0 rx", round(v["rx"], 2), "Mbit/s", warn_high=900.0),
                Measurement("eth0 tx", round(v["tx"], 2), "Mbit/s", warn_high=900.0)]),
        status("USB Bandwidth", OK, f"busiest bus {v['usb']:.0f} % claimed",
               [Measurement("usb1 claimed %", round(v["usb"], 2), "%", warn_high=80.0),
                Measurement("usb1 claimed", round(v["usb"] * 4.8, 2), "Mbit/s")]),
        status("Temperature k10temp", temp_level, f"Tctl {v['temp']:.0f} °C" + (" (above 85 °C)" if temp_level != OK else ""),
               [Measurement("Tctl", round(v["temp"], 2), "Cel", warn_high=85.0, error_high=95.0)], "HighTemperature"),
    ]


def describe() -> dict:
    return {"name": "charts_fixture", "version": "0.1.0", "schema": SCHEMA, "record_version": RECORD_VERSION, "matches": ["host"],
            "actions": [], "follow": {"period_s": PERIOD_S}, "description": "moving Computer series for the chart proposals"}


def emit(batch) -> None:
    for item in batch:
        sys.stdout.write(json.dumps(to_dict(item)) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    verb = next((word for word in argv if word in ("describe", "diagnose", "follow")), "")
    if verb == "describe":
        print(json.dumps(describe()))
    elif verb == "diagnose":
        emit(statuses(time.time(), host_name(), 5.0))
    elif verb == "follow":
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        host = host_name()
        next_at = time.time()
        try:
            while True:
                emit(statuses(time.time(), host))
                next_at = max(next_at + PERIOD_S, time.time())
                time.sleep(max(next_at - time.time(), 0.0))
        except BrokenPipeError:
            pass
    else:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
