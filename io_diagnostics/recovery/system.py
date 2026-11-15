"""Everything the ladder does to the machine goes through ``Seams``, and every question it asks /proc goes through here, so a
test points the whole ladder at a fake usbfs, a fake /proc and fake tools and nothing can touch real hardware.

The tripwire (tests/test_host_support.py sets IO_DIAGNOSTICS_TRIPWIRE=1): with it set, a write outside a temp dir or an open
of a real /dev node raises before anything happens, and the subprocess tripwire refuses the real reset tools.
"""

from __future__ import annotations

import errno
import fcntl
import os
import re
import select
import shutil
import subprocess
import tempfile
import termios
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from io_diagnostics.usb_reset import proc_root

TRIPWIRE_ENV = "IO_DIAGNOSTICS_TRIPWIRE"
FDCANUSB_RESET_ENV = "IO_DIAGNOSTICS_FDCANUSB_RESET"
RACK_BUS_LOCK_ENV = "IO_DIAGNOSTICS_RACK_BUS_LOCK"
HUB_FACTS_ENV = "IO_DIAGNOSTICS_HUB_POWER_FACTS"
KERNEL_LOG_ENV = "IO_DIAGNOSTICS_KERNEL_LOG_FILE"  # tests: read kernel lines from this file instead of journalctl
LSUSB_ENV = "IO_DIAGNOSTICS_LSUSB"
DEFAULT_RACK_BUS_LOCK = "/home/bam/.cache/rack-commissioning/bus.lock"
DEFAULT_HUB_FACTS = "/var/lib/robotdx/hub-power-switching.txt"  # written once by tools/install_device_reset_access.sh (as root)
VIDIOC_QUERYCAP = 0x80685600  # _IOR('V', 0, struct v4l2_capability), 104 bytes


class TripwireError(PermissionError):
    pass


def _tripwire(path: str | os.PathLike) -> None:
    if not os.environ.get(TRIPWIRE_ENV):
        return
    resolved = Path(path).resolve()
    # a pseudo-terminal is no hardware: a test's fake fdcanusb console is one
    if not (resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()) or resolved.is_relative_to("/dev/pts")):
        raise TripwireError(errno.EPERM, f"hardware tripwire: refused a real {path} in a test")


def write_attr(path: Path, value: str) -> None:
    _tripwire(path)
    path.write_text(value)


def run(argv: list[str], timeout_s: float, env: dict | None = None) -> tuple[int | None, str]:
    """``(exit code, its output)``; None when it ran over ``timeout_s`` and was killed."""

    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, env=env, check=False)
    except subprocess.TimeoutExpired as expired:
        out = expired.stdout or ""
        return None, (out.decode() if isinstance(out, bytes) else out)
    except OSError as error:
        return 127, f"{argv[0]} could not run: {error}"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def kernel_lines(since: float, port: str) -> list[str]:
    """The kernel's lines about this port since ``since`` (unplug, reset, new device number): the evidence a rung left."""

    if not port:
        return []
    pattern = re.compile(rf"\busb {re.escape(port)}(?:[:.\s]|$)|\b{re.escape(port)}:\d")
    named = os.environ.get(KERNEL_LOG_ENV)
    if named:
        try:
            lines = Path(named).read_text().splitlines()
        except OSError:
            return []
    else:
        code, out = run(["journalctl", "-k", "--no-pager", "-o", "short-iso-precise", "--since", f"@{since:.3f}"], 5.0)
        lines = out.splitlines() if code == 0 else []
    return [line for line in lines if pattern.search(line)][-40:]


def port_from_kernel(serial: str, days: int = 7) -> str:
    """The USB port a serial was last enumerated at, from the kernel's own lines (``usb 7-1: SerialNumber: 0607623A``).

    WHY (2026-11-14 17:44): after a reset the hung adapter never came back ("device not accepting address"), so sysfs had no trace
    of it; only its port is left to power-cycle, and the kernel log is where that port is still written down."""

    if not serial:
        return ""
    pattern = re.compile(rf"\busb (\d+-\d+(?:\.\d+)*): SerialNumber: {re.escape(serial)}\s*$")
    named = os.environ.get(KERNEL_LOG_ENV)
    if named:
        try:
            lines = Path(named).read_text().splitlines()
        except OSError:
            return ""
    else:
        code, out = run(["journalctl", "-k", "--no-pager", "-o", "cat", "--since", f"-{days}d"], 8.0)
        lines = out.splitlines() if code == 0 else []
    found = [match[1] for match in (pattern.search(line) for line in lines) if match]
    return found[-1] if found else ""


def querycap(node: str, timeout_s: float) -> tuple[bool, str]:
    """VIDIOC_QUERYCAP on a fresh open of a V4L2 node: shared with any running stream (it takes nothing), and it answers within
    ``timeout_s`` or the camera's control plane is wedged. In a thread, so a hung ioctl cannot hang the ladder."""

    _tripwire(node)
    result: list = []

    def ask() -> None:
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        except OSError as error:
            result.append((False, f"could not open {node}: {error.strerror}"))
            return
        try:
            buffer = bytearray(104)
            fcntl.ioctl(fd, VIDIOC_QUERYCAP, buffer)
            card = bytes(buffer[16:48]).split(b"\0", 1)[0].decode(errors="replace")
            result.append((True, f"{node} answers ({card})"))
        except OSError as error:
            result.append((False, f"{node} did not answer VIDIOC_QUERYCAP: {error.strerror}"))
        finally:
            os.close(fd)

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    worker.join(timeout_s)
    return result[0] if result else (False, f"{node} did not answer within {timeout_s:g} s")


def tty_ask(node: str, timeout_s: float, line: bytes = b"can status\n") -> tuple[bool | None, str]:
    """Ask a CAN adapter's console one line on a fresh open: ``(True, its reply)``, ``(False, why)`` when it is silent, or
    ``(None, why)`` when it could not be asked (busy). Non-blocking end to end, so a wedged adapter costs ``timeout_s``.

    WHY the flock: the moteus_servo driver flocks the tty node itself while it talks to the adapter; taking it LOCK_NB means
    this probe never interleaves a line into anyone else's session."""

    _tripwire(node)
    try:
        fd = os.open(node, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as error:
        return False, f"could not open {node}: {error.strerror}"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None, f"{node} is locked by another process: not asked"
        try:
            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~(termios.ICANON | termios.ECHO)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIFLUSH)
        except termios.error:
            pass  # a pty in a test, or a node that is not a tty: still ask it
        deadline = time.monotonic() + timeout_s
        sent, pending = 0, line
        buffer = b""
        while time.monotonic() < deadline:
            wait = max(0.0, deadline - time.monotonic())
            readable, writable, _ = select.select([fd], [fd] if pending else [], [], wait)
            if writable and pending:
                try:
                    sent = os.write(fd, pending)
                    pending = pending[sent:]
                except BlockingIOError:
                    pass
            if readable:
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                except OSError as error:
                    return False, f"{node} failed while reading: {error.strerror}"
                if not chunk:
                    return False, f"{node} hung up"
                buffer += chunk
                for raw in buffer.split(b"\n")[:-1]:
                    text = raw.decode("latin1", "replace").strip()
                    if text and not text.startswith("rcv"):
                        return True, f"{node} answered: {text[:80]}"
        return False, (f"{node} took no input within {timeout_s:g} s" if pending
                       else f"{node} did not answer `{line.decode().strip()}` within {timeout_s:g} s")
    finally:
        os.close(fd)


def lsusb_power(busnum: int, devnum: int) -> str:
    """A hub's port power switching from its descriptor, read by the stock lsusb (needs to open the hub: root, or a writable
    node): individual | ganged | none | unknown."""

    tool = os.environ.get(LSUSB_ENV) or shutil.which("lsusb") or "lsusb"
    code, out = run([tool, "-v", "-s", f"{busnum}:{devnum}"], 5.0)
    return parse_power_switching(out) if code == 0 else "unknown"


def parse_power_switching(text: str) -> str:
    if "Per-port power switching" in text:
        return "individual"
    if "Ganged power switching" in text:
        return "ganged"
    if "No power switching" in text:
        return "none"
    return "unknown"


@dataclass
class Seams:
    write_attr: Callable[[Path, str], None] = write_attr
    run: Callable[..., tuple[int | None, str]] = run  # (argv, timeout_s, env=None)
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic
    kernel_lines: Callable[[float, str], list[str]] = kernel_lines
    querycap: Callable[[str, float], tuple[bool, str]] = querycap
    tty_ask: Callable[[str, float], tuple[bool | None, str]] = tty_ask
    lsusb_power: Callable[[int, int], str] = lsusb_power
    port_from_kernel: Callable[[str], str] = port_from_kernel
    env: dict = field(default_factory=dict)  # extra environment for plugin and tool subprocesses


# ----- processes: who is the owner, who else holds it --------------------------------------------------------------
def _stat(pid: int) -> list[str]:
    try:
        text = (proc_root() / str(pid) / "stat").read_text()
    except OSError:
        return []
    return text.rpartition(")")[2].split()


def parent_of(pid: int) -> int | None:
    fields = _stat(pid)
    try:
        return int(fields[1]) if len(fields) > 1 else None
    except ValueError:
        return None


def alive(pid: int) -> bool:
    return (proc_root() / str(pid)).is_dir()


def ancestors(pid: int) -> list[int]:
    chain, seen = [], set()
    current = parent_of(pid)
    while current and current not in seen and current > 1:
        chain.append(current)
        seen.add(current)
        current = parent_of(current)
    return chain


def descendants(pid: int) -> set[int]:
    """Every process below ``pid`` (an owner's helpers: `uv run` -> python, a reader thread's child), from /proc ppids."""

    children: dict[int, list[int]] = {}
    try:
        pids = [int(entry.name) for entry in proc_root().iterdir() if entry.name.isdigit()]
    except OSError:
        return set()
    for each in pids:
        parent = parent_of(each)
        if parent is not None:
            children.setdefault(parent, []).append(each)
    out, todo = set(), [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in out:
                out.add(child)
                todo.append(child)
    return out


# the rack bus lock's readers live in io_diagnostics.rack_lock (shared with reset_port); kept importable here by their old names
from io_diagnostics.rack_lock import lock_holders, lock_is_held  # noqa: E402,F401


def fill(text: str, values: dict) -> str:
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def tool_paths() -> dict:
    """The placeholders a profile's command and lock paths use, from the environment first (tests), else this machine."""

    fdcanusb_reset = os.environ.get(FDCANUSB_RESET_ENV)
    if not fdcanusb_reset:
        installed = Path.home() / ".local" / "bin" / "fdcanusb-reset"
        fdcanusb_reset = str(installed) if installed.exists() else (shutil.which("fdcanusb-reset") or str(installed))
    from io_diagnostics import rack_lock

    return {"fdcanusb_reset": fdcanusb_reset, "rack_bus_lock": str(rack_lock.lock_path())}
