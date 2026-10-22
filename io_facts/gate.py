"""The one gate every ACTIVE health check goes through (hard gate g2).

An active check runs only when (a) no process holds the port (the repo's ``find_port_holders``,
or ``camera_holders`` for a RealSense) AND (b) the rack bus lock is free. It is query-only and
has an outer wall-clock timeout. Anything else is a shown state ("blocked by pid 4187 ..."),
never an error.

Prototype round: active checks are DISABLED twice over (``allow_active`` and the
``IO_ALLOW_ACTIVE=1`` environment flag must both be set; neither is, anywhere in this repo),
because the orchestrator is running rack experiments on the adapters and cameras right now.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import lock as bus_lock
from . import reuse


@dataclass
class GateDecision:
    state: str  # "free", "blocked", "unknown"
    reason: str
    port_holders: list[str] = field(default_factory=list)
    lock_holders: list[bus_lock.LockHolder] = field(default_factory=list)
    decided_by: list[str] = field(default_factory=list)


def decide(
    nodes: list[str],
    *,
    camera: bool = False,
    holders_fn: Callable[[str], list[str]] | None = None,
    camera_holders_fn: Callable[..., list[str]] | None = None,
    lock_fn: Callable[[], list[bus_lock.LockHolder]] = bus_lock.lock_holders,
    uses_rack_lock: bool = True,
) -> GateDecision:
    """Would an active check on these device nodes be allowed right now? Reads only."""

    decided_by = ["io_facts.lock.lock_holders (/proc/locks, never flock)"] if uses_rack_lock else []
    if camera:
        imported = reuse.camera_holders_function() if camera_holders_fn is None else reuse.Imported(camera_holders_fn, "injected")
        if imported.function is None:
            return GateDecision("unknown", f"camera holder check {imported.source}", decided_by=decided_by)
        holders = imported.function([Path(node) for node in nodes]) if nodes else imported.function()
        decided_by.insert(0, imported.source)
    else:
        imported = reuse.port_holders_function() if holders_fn is None else reuse.Imported(holders_fn, "injected")
        if imported.function is None:
            return GateDecision("unknown", f"port holder check {imported.source}", decided_by=decided_by)
        holders = [holder for node in nodes for holder in imported.function(node)]
        decided_by.insert(0, imported.source)
    locks = lock_fn() if uses_rack_lock else []
    if holders:
        return GateDecision("blocked", f"open by {', '.join(holders)}", holders, locks, decided_by)
    if locks:
        names = ", ".join(f"pid {holder.pid} {holder.command}" for holder in locks)
        return GateDecision("blocked", f"rack bus lock held by {names}", holders, locks, decided_by)
    return GateDecision("free", "no holder, lock free", holders, locks, decided_by)


def run_gated(
    nodes: list[str],
    command: list[str],
    *,
    timeout_s: float = 4.0,
    allow_active: bool = False,
    lock_path: Path = bus_lock.BUS_LOCK,
    **decide_options,
) -> dict:
    """Run one query-only active check behind the gate. Returns a result with its state and source."""

    decision = decide(nodes, **decide_options)
    if decision.state != "free":
        return {"state": decision.state, "reason": decision.reason, "decided_by": decision.decided_by}
    if not (allow_active and os.environ.get("IO_ALLOW_ACTIVE") == "1"):
        return {"state": "not run", "reason": "passive-only round: the orchestrator owns the rack", "would_run": command}
    with open(lock_path, "w") as handle:
        try:
            # WHY NB and held only for this one bounded query: the same contract rack_inventory uses.
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "blocked", "reason": "bus lock taken between check and run"}
        recheck = decide(nodes, **{**decide_options, "lock_fn": lambda: []})
        if recheck.state != "free":
            return {"state": recheck.state, "reason": recheck.reason}
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return {"state": "timeout", "reason": f"no answer within {timeout_s:.0f} s (outer timeout)"}
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return {"state": "ran", "returncode": completed.returncode, "stdout": completed.stdout[-4000:]}
