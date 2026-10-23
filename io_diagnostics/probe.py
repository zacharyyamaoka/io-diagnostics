"""The on-connect probe (docs/probe-on-connect.md): when it runs again, and who is in the way.

A driver declares ``probe`` in ``describe``; the host runs ``<cmd> probe --device <id>`` once per connection, in place of
that connection's first diagnose. The DRIVER says what it found, in its own ``Found`` / ``Probed`` / ``ProbeBlocked``
events; the host derives nothing about devices. What is the host's, and lives here, is the schedule of a probe that ended
blocked (a ``PortBusy`` / ``BusLockHeld`` status carrying the holders as ``"pid program"``):

- the holders' pids are watched with a ``/proc/<pid>/stat`` read about once a second (no subprocess), and the probe runs
  again ``SETTLE_S`` after the last one exits;
- in case a holder cannot be watched it also runs again on a backoff of 10, 20, 40 s ..., never beyond the plugin's period.

Pure functions and small dataclasses; ``Host`` owns the clock, the thread and the queue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contract.record import DiagnosticStatus, Event

BLOCKED_CODES = ("PortBusy", "BusLockHeld")
PROBE_BLOCKED = "ProbeBlocked"  # the driver's event code for a probe that could not talk to the device
# keys a plugin may list its holders under, as "pid program, pid program" (moteus_servo: port_holders)
HOLDER_KEYS = ("port_holders", "holders", "lock_holders")
# WHY a list: python3 as the command says nothing; the script in its argv does (tview is "python3 tview")
GENERIC_COMMANDS = re.compile(r"^(python[\d.]*|node|bash|sh|dash|perl|ruby|java)$")
BACKOFF_FIRST_S = 10.0  # then 20, 40 ... capped at the plugin's period
SETTLE_S = 0.5  # after the last holder exits: let the kernel close the port before asking again
POLL_S = 1.0  # how often the holders' pids are looked at


def probe_timeout(answer: dict[str, Any] | None, default_s: float) -> float | None:
    """A plugin's ``describe`` ``probe`` -> its time limit in seconds (``true`` = the plugin's own ``timeout_s``);
    None = it declares no probe (the host runs diagnose on connect, as before)."""

    declared = (answer or {}).get("probe")
    if declared is None or declared is False:
        return None
    if isinstance(declared, dict):
        value = declared.get("timeout_s")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
    return default_s


# ----- holders --------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Holder:
    pid: int
    command: str  # as the plugin said it ("python3", or already the program's name)
    program: str  # what a person calls it: "tview"
    started: str | None  # /proc/<pid>/stat starttime when first seen: a reused pid is not this holder


@dataclass
class Blocked:
    """A device whose latest probe could not read its port or bus: who holds it, and when it is probed again."""

    code: str  # PortBusy | BusLockHeld
    holders: tuple[Holder, ...]
    message: str  # the driver's ProbeBlocked sentence, or ""
    attempts: int = 1  # consecutive blocked probes (the backoff's exponent)
    released: float | None = None  # time.monotonic() when every watched holder was seen gone
    retry_at: float = 0.0  # time.monotonic() of the backoff retry


def parse_holders(text: str) -> list[tuple[int, str]]:
    """``"78183 python3, 90 tview"`` -> ``[(78183, "python3"), (90, "tview")]``; entries that do not start with a pid are dropped."""

    out = []
    for item in text.split(", "):
        match = re.match(r"^\s*(\d+)(?:\s+(.*?))?\s*$", item)
        if match:
            out.append((int(match.group(1)), match.group(2) or "?"))
    return out


def _after(message: str) -> str:
    match = re.search(r"\bheld by (.*)$", message)
    return match.group(1) if match else ""


def program_name(pid: int, command: str, proc: Path = Path("/proc")) -> str:
    """The program a person knows the holder by. The plugin's command when it already is one; for an interpreter
    (``python3``) the script or module in its argv (``/home/bam/.local/bin/tview`` -> ``tview``), read from
    ``/proc/<pid>/cmdline`` while the process lives."""

    if command and command != "?" and not GENERIC_COMMANDS.match(command):
        return command
    try:
        argv = (proc / str(pid) / "cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return command or "?"
    args = [word for word in argv[1:] if word]
    if argv and argv[0] and not GENERIC_COMMANDS.match(Path(argv[0]).name):
        return Path(argv[0]).name  # not an interpreter: its own name is the program
    index = 0
    while index < len(args):
        word = args[index]
        if word == "-m" and index + 1 < len(args):
            return args[index + 1]
        if word.startswith("-"):
            index += 1
            continue
        return Path(word).name.removesuffix(".py") or command or "?"
    return command or "?"


def start_time(pid: int, proc: Path = Path("/proc")) -> str | None:
    """The process's start time in clock ticks since boot (``/proc/<pid>/stat`` field 22), or None when it is gone or a
    zombie (a zombie holds no file: its port is free)."""

    try:
        stat = (proc / str(pid) / "stat").read_text()
    except OSError:
        return None
    fields = stat[stat.rfind(")") + 2:].split()  # after "pid (comm)": state is fields[0], starttime fields[19]
    if len(fields) < 20 or fields[0] in ("Z", "X"):
        return None
    return fields[19]


def blocked_by(records: list, proc: Path = Path("/proc")) -> Blocked | None:
    """The blocked state a probe's records report, or None. ``records`` are the run's records.

    The holders come from the plugin's ``port_holders`` value, else from the sentence it wrote after "held by"; the
    message is the driver's own ``ProbeBlocked`` event."""

    statuses = [record for record in records if isinstance(record, DiagnosticStatus) and record.code in BLOCKED_CODES]
    if not statuses:
        return None
    pairs: list[tuple[int, str]] = []
    for status in statuses:
        listed = next((value for key, value in status.values if key in HOLDER_KEYS and value != "none"), "")
        pairs += parse_holders(listed or _after(status.message))
    holders = tuple(Holder(pid, command, program_name(pid, command, proc), start_time(pid, proc))
                    for pid, command in dict(pairs).items())
    code = "PortBusy" if any(status.code == "PortBusy" for status in statuses) else statuses[0].code
    said = next((record.message for record in records if isinstance(record, Event) and record.code == PROBE_BLOCKED), "")
    return Blocked(code=code, holders=holders, message=said)


def holder_alive(holder: Holder, proc: Path = Path("/proc")) -> bool:
    started = start_time(holder.pid, proc)
    return started is not None and (holder.started is None or started == holder.started)


def holders_gone(blocked: Blocked, proc: Path = Path("/proc")) -> bool:
    """Every watched holder has exited. A block with no pid to watch is never "gone": only its backoff retries it."""

    return bool(blocked.holders) and not any(holder_alive(holder, proc) for holder in blocked.holders)


def backoff_s(attempts: int, period_s: float, first_s: float = BACKOFF_FIRST_S) -> float:
    """10 s, 20 s, 40 s ... after the 1st, 2nd, 3rd consecutive blocked probe, never beyond the plugin's own period."""

    delay = first_s * 2 ** max(attempts - 1, 0)
    return min(delay, period_s) if period_s > 0 else delay


def blocked_sentence(blocked: Blocked, retry_s: float) -> str:
    """The plugin-run check's message while blocked. The driver's own ``ProbeBlocked`` sentence when it sent one; else
    "tview has the port (pid 78183) · RobotDx probes again when it closes"."""

    if blocked.message:
        return blocked.message
    what = "the rack bus lock" if blocked.code == "BusLockHeld" else "the port"
    if not blocked.holders:
        return f"another process holds {what} · RobotDx probes again in {max(1, round(retry_s))} s"
    if len(blocked.holders) == 1:  # the program leads, the pid is the detail a person can act on
        holder = blocked.holders[0]
        return f"{holder.program} has {what} (pid {holder.pid}) · RobotDx probes again when it closes"
    names = [f"{holder.program} (pid {holder.pid})" for holder in blocked.holders]
    return f"{', '.join(names[:-1])} and {names[-1]} have {what} · RobotDx probes again when they close"
