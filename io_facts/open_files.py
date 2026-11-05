"""Who has which /dev node open: ONE pass over /proc, in a process of its own, that every gate answers from.

WHY (measured on the rack PC, 2026-11-04: 1,173 processes, 22,160 open descriptors, a load of 60): the host answered each gate with its own walk.
``find_port_holders`` is 0.95 s of CPU (a ``realpath`` per descriptor), ``camera_holders`` 1.04 s, ``usb_reset.holders`` 0.2 s; ``port_free`` walked twice
for one answer, once per action, and the 2 s cache had expired before the next action asked, so one adapter's ``/api/v1/node`` took 22 s. A pass reads
every descriptor once and answers them all.

WHY a process of its own: a walk is tens of thousands of syscalls, and each one drops and takes back the GIL, so inside the host it both waited behind
every busy thread and slowed every one of them. Here it costs another core, for a tenth of a second, and the host only reads a JSON answer.

``scan`` is stdlib and imports nothing of this repo, because it runs under ``python -I -S``. ``OpenFiles`` is the host's side: the newest pass, and a
thread that keeps it fresh only while someone is asking. A page reads ``latest`` and never waits; a watch or a write asks ``fresh`` and waits for a
pass no older than it can accept.

The semantics are the two functions it replaces on this path, which stay the drivers' own: ``config_probe.find_port_holders`` (every process with the
port open, resolving symlinks; pyserial opens an fdcanusb without a lock, so a second reader silently steals bytes) and
``bam_rack_twin.hardware.record.camera_holders`` (a viewer by its executable's own name, or any process with a node open). ``/proc/<pid>/fd/<n>`` is
the kernel's own canonical path, so ``readlink`` here is what ``realpath`` found there, at a fifth of the cost.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

# bam_rack_twin.hardware.record.VIEWER_NAMES (tests/test_instant_pages.py compares them): a viewer counts even before it opens a node
VIEWERS = ("realsense-viewer",)
# the nodes no gate is ever about, and nearly every process has open
QUIET = frozenset({"/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom", "/dev/ptmx", "/dev/tty", "/dev/fuse", "/dev/kvm"})
QUIET_PREFIXES = ("/dev/pts/", "/dev/shm/", "/dev/dri/", "/dev/nvidia", "/dev/net/", "/dev/vhost")
SCAN_TIMEOUT_S = 20.0
PAGE_PERIOD_S = 5.0  # how often a page's gates are read again while it looks
STRICT_PERIOD_S = 1.5  # while a watch or a write depends on them: inside the host's own 2 s promise (GATE_CACHE_S)
STRICT_ASKED_S = 4.0  # ... which it does for this long after it last asked
ASKED_S = 20.0  # nobody has asked for this long: no more passes, and the thread ends


def scan(proc: str | os.PathLike = "/proc") -> dict:
    """``{"dev": {node: [[pid, comm], ...]}, "viewers": [[pid, name], ...], "unseen": processes that could not be looked at}``."""

    root, own = os.fspath(proc), os.getpid()
    dev: dict[str, list[list]] = {}
    viewers: list[list] = []
    unseen = 0
    for name in os.listdir(root):
        if not name.isdigit() or int(name) == own:
            continue
        folder = f"{root}/{name}"
        pid = int(name)
        try:
            with open(f"{folder}/cmdline", "rb") as file:
                # WHY the executable's own name and not the whole command line: a shell that merely mentions realsense-viewer is not a viewer
                program = os.path.basename(file.read(4096).split(b"\0")[0].decode(errors="replace"))
            if program in VIEWERS:
                viewers.append([pid, program])
            descriptors = os.listdir(f"{folder}/fd")
        except PermissionError:
            unseen += 1
            continue
        except OSError:  # it left between the listing and the look
            continue
        targets = set()
        for descriptor in descriptors:
            try:
                target = os.readlink(f"{folder}/fd/{descriptor}")
            except OSError:
                continue
            if target.startswith("/dev/") and target not in QUIET and not target.startswith(QUIET_PREFIXES):
                targets.add(target)
        if targets:
            try:
                with open(f"{folder}/comm") as file:
                    comm = file.read().strip()
            except OSError:
                comm = "?"
            for target in targets:
                dev.setdefault(target, []).append([pid, comm])
    return {"dev": dev, "viewers": viewers, "unseen": unseen}


def port_holders(found: dict, port: str) -> list[str]:
    """``find_port_holders(port)`` from a pass: ``pid COMMAND`` of every process holding it, symlinks resolved."""

    return [f"{pid} {comm}" for pid, comm in found["dev"].get(os.path.realpath(port), [])]


def camera_holders(found: dict, nodes=None) -> list[str]:
    """``camera_holders(nodes)`` from a pass: every viewer, and every process holding one of ``nodes`` (no ``nodes``: any video node)."""

    wanted = {os.path.realpath(node) for node in nodes} if nodes else {node for node in found["dev"] if node.startswith("/dev/video")}
    out = {pid: f"{pid} {name}" for pid, name in found["viewers"]}
    for node in wanted:
        for pid, comm in found["dev"].get(node, []):
            out.setdefault(pid, f"{pid} {comm}")
    return list(out.values())


def scan_in_a_process() -> dict:
    """One pass, in its own interpreter. ``-I -S``: no site, no environment, so nothing of a venv can slow or change it."""

    done = subprocess.run([sys.executable, "-I", "-S", __file__], stdin=subprocess.DEVNULL, capture_output=True, timeout=SCAN_TIMEOUT_S, check=True)
    return json.loads(done.stdout)


class OpenFiles:
    """The newest pass, and the thread that keeps it fresh while someone asks. ``on_pass`` runs on that thread after each one."""

    def __init__(self, on_pass=None, source=scan_in_a_process):
        self.source, self.on_pass = source, on_pass
        self.wanted: dict = {}  # what the owner should read again after each pass
        self.note = ""
        self._found: dict | None = None
        self._at = -1e18
        self._lock = threading.Lock()  # one pass at a time: a second asker waits for that pass, it does not run another
        self._state = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._asked = -1e18
        self._strict = -1e18

    def latest(self, max_age_s: float | None = None) -> dict | None:
        """The newest pass, at once; None when there is none (or none younger than ``max_age_s``)."""

        return self._found if max_age_s is None or time.monotonic() - self._at <= max_age_s else None

    def age(self) -> float | None:
        return time.monotonic() - self._at if self._found is not None else None

    def ask(self, *, strict: bool = False) -> None:
        """Someone wants this kept fresh. Starts the thread when none runs; never waits."""

        now = time.monotonic()
        self._asked = now
        if strict:
            self._strict = now
        with self._state:
            if self._closed or (self._thread is not None and self._thread.is_alive()):
                return
            self._wake.set()  # WHY at once: the first asker is a page waiting on it
            self._thread = threading.Thread(target=self._loop, name="io-open-files", daemon=True)
            self._thread.start()

    def fresh(self, max_age_s: float) -> dict | None:
        """A pass no older than ``max_age_s``: this one, or the one under way, or a new one. For what must not act on an old answer."""

        self.ask(strict=True)
        with self._lock:
            if time.monotonic() - self._at > max_age_s:
                self._read()
            return self._found

    def _read(self) -> None:
        try:
            found = self.source()
        except (OSError, ValueError, subprocess.SubprocessError) as error:  # the last pass stands; the gates say what they can
            self.note = f"open files not read: {type(error).__name__}: {error}"
            return
        self._found, self._at, self.note = found, time.monotonic(), ""

    def _loop(self) -> None:
        while not self._closed:
            now = time.monotonic()
            if now - self._asked > ASKED_S:
                with self._state:  # a last look under the lock: an ask that came just now finds this thread still running otherwise
                    if time.monotonic() - self._asked > ASKED_S:
                        self._thread = None
                        return
            period = STRICT_PERIOD_S if now - self._strict < STRICT_ASKED_S else PAGE_PERIOD_S
            if self._wake.wait(max(0.0, self._at + period - now)):
                self._wake.clear()
            if self._closed:
                return
            with self._lock:
                self._read()
            if self.on_pass is not None:
                try:
                    self.on_pass()
                except Exception as error:  # noqa: BLE001 - a bad answer must not end the passes; it shows in the note
                    self.note = f"gates not read: {type(error).__name__}: {error}"

    def close(self) -> None:
        self._closed = True
        self._wake.set()


if __name__ == "__main__":
    json.dump(scan(), sys.stdout)
