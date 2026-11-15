"""The rack bus lock rule, the same one Sim to Real enforces (bam_ws a3a12ac7d, bam_rack_twin.hardware.board.guard_real_open):
nothing opens or resets the arm's fdcanusb unless (a) no OTHER process holds its port AND (b) this process, or one above it,
holds the REAL rack bus lock, or runs in a systemd unit that a RUNNING rackq job registered in its lease.

WHY (2026-11-14 ~17:41): a lane's repro script opened /dev/ttyACM0 while arm-hold held it, the bus died at 17:42:58, and a USB
reset could not bring the adapter back (kernel "device descriptor read/64, error -71"): its MCU hung hard and needed a hand power
cycle. RobotDx's own resets were outside that guard; the W1 dispatcher asked for the same rule here. Not imported from bam_ws:
RobotDx is stdlib only and runs under the tray's system python, so the rule is restated, and this docstring names its source.

The REAL lock path, never a redirected one: "a process that redirected its lock holds nothing the rack respects" (their guard). On
a FAKE machine (IO_DIAGNOSTICS_USB_SYSFS names a fake sysfs) the lock is a fake too (IO_DIAGNOSTICS_RACK_BUS_LOCK, else beside the
fake tree), so no test can ever take the rack's real lock.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path

REAL_BUS_LOCK = Path("/home/bam/.cache/rack-commissioning/bus.lock")
FAKE_LOCK_ENV = "IO_DIAGNOSTICS_RACK_BUS_LOCK"
RACKQ_ROOT_ENV = "RACKQ_ROOT"
DEFAULT_RACKQ_ROOT = Path("/home/bam/.local/state/rack/queue")
FDCANUSB_IDS = ("0483", "5740")


def _proc() -> Path:
    return Path(os.environ.get("IO_DIAGNOSTICS_PROC") or "/proc")


def _fake_machine() -> bool:
    return bool(os.environ.get("IO_DIAGNOSTICS_USB_SYSFS"))


def lock_path() -> Path:
    """The rack bus lock this machine's rack respects: the real one, unless the whole machine is a fake (a test, a demo)."""

    if _fake_machine():
        named = os.environ.get(FAKE_LOCK_ENV)
        return Path(named) if named else Path(os.environ["IO_DIAGNOSTICS_USB_SYSFS"]).parent / "rack-bus.lock"
    return REAL_BUS_LOCK


def is_rack_adapter(vid: str, pid: str, product: str = "") -> bool:
    """The arm's CAN adapter class (mjbots fdcanusb): resetting or opening one is a rack action."""

    return (vid.lower(), pid.lower()) == FDCANUSB_IDS or product.strip() == "fdcanusb"


def _parent(pid: int) -> int | None:
    try:
        fields = (_proc() / str(pid) / "stat").read_text().rpartition(")")[2].split()
        return int(fields[1])
    except (OSError, ValueError, IndexError):
        return None


def ancestors(pid: int) -> list[int]:
    chain, seen, current = [], set(), _parent(pid)
    while current and current > 1 and current not in seen:
        chain.append(current)
        seen.add(current)
        current = _parent(current)
    return chain


def lock_holders(path: str | os.PathLike) -> list[int]:
    """The pids /proc/locks names for a lock file (flock or POSIX), matched by its device and inode; waiters are not holders."""

    try:
        info = os.stat(path)
    except OSError:
        return []
    want = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
    try:
        lines = (_proc() / "locks").read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        words = line.split()
        if "->" in words:
            continue
        found = next((word for word in words if word.count(":") == 2), "")
        if found.lower() == want:
            with contextlib.suppress(ValueError, IndexError):
                out.append(int(words[words.index(found) - 1]))
    return sorted(set(out))


def lock_is_held(path: str | os.PathLike) -> bool:
    """Does anyone hold it now (any mode)? A LOCK_EX | LOCK_NB that fails says so, and is released at once."""

    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def own_units(pid: int | None = None) -> set[str]:
    """The systemd units a process runs in (``arm-hold`` for ``arm-hold.service``), from its cgroup path."""

    try:
        text = (_proc() / str(pid or os.getpid()) / "cgroup").read_text()
    except OSError:
        return set()
    return {part.removesuffix(".service") for line in text.splitlines() for part in line.rsplit(":", 1)[-1].split("/")
            if part.endswith((".service", ".scope"))}


def leased_units() -> set[str]:
    """The units named in the lease of every RUNNING rackq job (``running/<id>.json`` ``lease.units``)."""

    root = Path(os.environ.get(RACKQ_ROOT_ENV) or DEFAULT_RACKQ_ROOT)
    units: set[str] = set()
    for job in sorted((root / "running").glob("*.json")):
        try:
            lease = json.loads(job.read_text()).get("lease") or {}
        except (OSError, ValueError, AttributeError):
            continue
        units.update(str(unit).removesuffix(".service") for unit in lease.get("units") or [])
    return units


def _name(pid: int) -> str:
    try:
        words = [word for word in (_proc() / str(pid) / "cmdline").read_text().split("\0") if word]
    except OSError:
        words = []
    if words and Path(words[0]).name.startswith(("python", "uv", "bash", "sh")) and len(words) > 1:
        script = next((word for word in words[1:] if not word.startswith("-")), words[0])
        return f"{Path(script).name} (pid {pid})"
    return f"{Path(words[0]).name if words else 'pid'} (pid {pid})" if words else f"pid {pid}"


@contextlib.contextmanager
def hold(family: set[int], *, path: Path | None = None):
    """Hold the rack bus lock for a rack action, or say why not: yields ``(ok, words)``.

    ``family``: the processes that count as this action (it, the processes that started it, a declared owner and its helpers).
    Someone OUTSIDE it holding the lock refuses. Someone inside it holding it (arm-hold calling the ladder), or a rackq lease on a
    unit this process runs in, already covers the action. Else the lock is taken here, LOCK_EX | LOCK_NB, for the whole action."""

    path = path or lock_path()
    if os.environ.get("IO_DIAGNOSTICS_TRIPWIRE") and Path(path) == REAL_BUS_LOCK:
        raise PermissionError("hardware tripwire: a test reached for the rack's real bus lock")
    holders = lock_holders(path) if path.exists() else []
    others = [pid for pid in holders if pid not in family]
    if others:
        yield False, f"the rack bus lock ({path}) is held by " + ", ".join(_name(pid) for pid in others)
        return
    if holders:
        yield True, f"the rack bus lock is held by this action's own {_name(holders[0])}"
        return
    units = own_units() | {unit for pid in ancestors(os.getpid()) for unit in own_units(pid)}
    leased = units & leased_units()
    if leased:
        yield True, f"a running rackq job's lease covers the unit {sorted(leased)[0]}"
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    except OSError as error:
        yield False, f"could not open the rack bus lock {path}: {error.strerror}"
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False, f"the rack bus lock ({path}) is held (its holder could not be named)"
            return
        yield True, f"took the rack bus lock ({path})"
    finally:
        os.close(fd)
