"""DeviceDiagnostics: what each driver exposes, and the host-side call that gates it.

Scope (Zach, 2026-10-21): presence, identity/ids, firmware + driver version, update available,
read-only config, comms check, reset. Out of scope: motion and control.

Every value is a ``Reading``: the value plus where it came from and when, so a surface can say
"last read 18:13 by rack_inventory" instead of passing a stale number off as live.
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from io_facts import gate  # noqa: E402

PASSIVE = "passive"  # sysfs / by-id / files: safe while an experiment runs
TOUCHES = "touches"  # talks to the device: only through the gate, query-only
CHANGES = "changes"  # reset / config write: explicit, per device, confirmed, AND gated


@dataclass
class Reading:
    value: Any
    source: str  # "sysfs", "rack_inventory 18:13", "moteus_tool --info (2026-10-10 flash run)", ...
    at: str = ""  # ISO time the value was observed ("" = now)
    state: str = "ok"  # ok / warn / error / unknown / blocked / not run

    @classmethod
    def now(cls, value: Any, source: str, state: str = "ok") -> "Reading":
        return cls(value, source, dt.datetime.now().isoformat(timespec="seconds"), state)


@dataclass(frozen=True)
class UsbMatch:
    vid: str
    pid: str = ""  # "" = any product of this vendor

    def accepts(self, device) -> bool:
        return device.vid == self.vid and (not self.pid or device.pid == self.pid)


@dataclass(frozen=True)
class Action:
    """A utility the DRIVER registers (printer-driver style); the host only shows it and the driver runs it.

    ``preconditions`` are ``(check, text)`` pairs from a small vocabulary the host can evaluate
    passively: ``port_free``, ``bus_lock_free``, ``camera_free``, ``board_answers``, ``update_available``.
    ``confirm`` may use ``{name}``, ``{id}``, ``{serial}``, ``{latest}``, filled in by the host.
    """

    name: str  # "run_comms_check", "clear_faults", "reset", "update_firmware", "identify"
    title: str  # the button label
    description: str
    danger: str  # "query" (reads only) / "interrupts" (drops the device for a moment) / "changes" (writes state)
    scope: str  # "device" (the USB device) or "node" (one board on its bus)
    confirm: str
    preconditions: tuple[tuple[str, str], ...] = ()
    kind: str = CHANGES


@runtime_checkable
class DeviceDiagnostics(Protocol):
    """Implemented by ``<driver package>/diagnostics.py``. Five reads and one confirmed action."""

    name: str  # "moteus_servo"
    matches: tuple[UsbMatch, ...]  # which USB devices this driver diagnoses
    kinds: dict[str, str]  # {"identity": PASSIVE, "comms": TOUCHES, "reset": CHANGES, ...}

    def identity(self, device) -> dict[str, Reading]: ...  # serial, model, ids on a bus

    def versions(self, device) -> dict[str, Reading]: ...  # firmware, driver, latest, update_available

    def config(self, device) -> dict[str, Reading]: ...  # read-only view

    def comms(self, device) -> dict[str, Reading]: ...  # link speed, latency, loss, error counters

    def reset(self, device, *, confirm: bool) -> Reading: ...  # the only state change in scope

    def nodes(self, device) -> list[str]: ...  # device nodes the gate checks for holders

    # optional: ``actions: tuple[Action, ...]``; a button exists only if its driver registered it


@dataclass
class Report:
    driver: str
    device: Any
    sections: dict[str, dict[str, Reading]] = field(default_factory=dict)
    gate: gate.GateDecision | None = None


def diagnose(driver: DeviceDiagnostics, device, *, gate_options: dict | None = None,
             sections=("identity", "versions", "config", "comms")) -> Report:
    """Host-side call: passive sections always run; device-touching ones only through the gate.

    WHY the host owns the gate and not each driver: one implementation of "never while the port is
    held or the rack lock is taken", inherited by every manufacturer's driver for free.
    """

    options = gate_options or {}
    decision = gate.decide(driver.nodes(device), camera=getattr(driver, "camera", False),
                           uses_rack_lock=getattr(driver, "uses_rack_lock", False), **options)
    report = Report(driver.name, device, gate=decision)
    for section in sections:
        if driver.kinds.get(section) == PASSIVE:
            report.sections[section] = getattr(driver, section)(device)
            continue
        cached = getattr(driver, f"last_known_{section}", None)
        last = cached(device) if cached else {}
        if decision.state != "free":
            report.sections[section] = {**last, "_gate": Reading(decision.reason, "io_facts.gate", state="blocked")}
        else:
            # Prototype round: the gate is free but active checks stay off (the orchestrator owns the rack).
            report.sections[section] = {**last, "_gate": Reading("passive-only round", "io_facts.gate", state="not run")}
    return report


def request_reset(driver: DeviceDiagnostics, device, *, confirmed_by_user: bool, gate_options: dict | None = None) -> Reading:
    """The one state-changing path: refuses without an explicit confirm, then still obeys the gate."""

    if not confirmed_by_user:
        return Reading("refused: reset needs an explicit, per-device confirm", "contract", state="not run")
    decision = gate.decide(driver.nodes(device), camera=getattr(driver, "camera", False),
                           uses_rack_lock=getattr(driver, "uses_rack_lock", False), **(gate_options or {}))
    if decision.state != "free":
        return Reading(f"waits: {decision.reason}", "io_facts.gate", state="blocked")
    return Reading("not run: passive-only round", "io_facts.gate", state="not run")
