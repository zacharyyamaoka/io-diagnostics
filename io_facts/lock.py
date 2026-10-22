"""Who holds the rack bus lock, read from /proc/locks without ever taking the lock.

WHY passive: the rack's ``board.hold_bus_lock`` takes ``LOCK_EX | LOCK_NB`` and REFUSES to start
if anyone holds it. A registry that "checks" the lock by acquiring it, even for a millisecond,
can make an experiment refuse at that instant. Reading /proc/locks shows the holder's pid and
never contends. (Only the gated ACTIVE path takes the lock, for the length of one bounded query,
the same way ``rack_inventory`` already does.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BUS_LOCK = Path("/home/bam/.cache/rack-commissioning/bus.lock")


@dataclass(frozen=True)
class LockHolder:
    pid: int
    command: str
    kind: str  # FLOCK or POSIX


def _command_of(pid: int) -> str:
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return " ".join(part.decode(errors="replace") for part in argv[:3] if part) or Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return "?"


def lock_holders(lock_path: Path = BUS_LOCK, proc_locks: Path = Path("/proc/locks")) -> list[LockHolder]:
    """Every process holding a lock on ``lock_path`` (empty list = free). Never calls flock."""

    try:
        info = lock_path.stat()
    except FileNotFoundError:
        return []
    # /proc/locks prints the device as hex major:minor, then the decimal inode.
    wanted = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
    holders = []
    for line in proc_locks.read_text().splitlines():
        fields = line.split()
        if "->" in fields:  # a blocked waiter, not a holder
            continue
        if len(fields) >= 6 and fields[5] == wanted:
            pid = int(fields[4])
            holders.append(LockHolder(pid=pid, command=_command_of(pid), kind=fields[1]))
    return holders
