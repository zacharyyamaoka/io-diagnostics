"""The host: one clock, one OS source, the plugin jobs, the fault memory, and every API response (docs/api-v1.md).

Live and fixture mode differ ONLY in their ``Clock`` and ``OsSource``; plugins run through the same runner either
way (the fixture's driver side is a real plugin subprocess, tests/fixtures/plugins/v6_scenario.py), so a fixture
replay exercises the code the rack will run.
"""

from __future__ import annotations

import collections
import contextlib
import dataclasses
import datetime as dt
import itertools
import json
import os
import re
import shutil
import socket
import string
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from io_diagnostics import API, FIXTURE_PLUGINS_DIR, PLUGINS_DIR
from io_diagnostics import activity as activity_module
from io_diagnostics import composition as composition_module
from io_diagnostics import eventlog as eventlog_module
from io_diagnostics import entity_state
from io_diagnostics.arrangements import ARRANGEMENTS_FILE, Arrangements
from io_diagnostics.dashboards import DASHBOARDS_DIR, DashboardStore
from io_diagnostics.live import LiveFeed
from io_diagnostics.config_edit import ConfigEditor
from io_diagnostics import expected as expected_module
from io_diagnostics import history as history_module
from io_diagnostics import host_utilities, usb_reset
from io_diagnostics import logview as logview_module
from io_diagnostics import probe as probe_module
from io_diagnostics import roster as roster_module
from io_diagnostics import settings as settings_module
from io_diagnostics import stock_diagnostics as stock_diagnostics_module
from io_diagnostics import tree
from io_diagnostics.faults import Faults
from io_diagnostics import terminal as terminal_module
from io_diagnostics.plugins import load_plugins, match_devices, source_stamp
from io_facts import open_files
from io_diagnostics.runner import (RUNNER_SOURCE, RUNNING, Follower, Gates, Job, RunOutcome, Scheduler, Watcher, absolute,
                                   _outcome, _records, act_argv, describe, follow_period, follow_status, host_status, plugin_env, rebase,
                                   relative, run_act, run_check, run_plugin)

from contract.record import (OK, RANK, RECORD_VERSION, SCHEMA, STALE, STALE_FACTOR, STALE_FLOOR_S, UNKNOWN, WARN, Action,
                             DiagnosticStatus, Entity, Event, Frame, KeyValue, Measurement, State, effective_level, from_dict,
                             to_dict)

HOST_SOURCE = "io_diagnostics.host"
# WHY a code and not a driver name: any driver that says "my frames never reached the wire" this way gets the
# other-adapter comparison (moteus_servo's FramesNotSent: fdcanusb 28B3A25D, 2026-10-22)
FRAMES_NOT_SENT = "FramesNotSent"
DEFAULT_RIG = "Rig"
FIXTURE_FILE = FIXTURE_PLUGINS_DIR / "v6_frames.json"
FIXTURE_ALIASES = {"plug": "F1", "bus-busy": "F2", "after-restart": "F3", "f1": "F1", "f2": "F2", "f3": "F3",
                   "v6-f1": "F1", "v6-f2": "F2", "v6-f3": "F3"}
REFRESH_S = 2.0
# WHY faster while a plug is provisional: the kernel log settles it 3 s (plug) or 10 s (unplug) after the uevent, and
# the confirmation must replace the provisional row as soon as it is due, not up to 2 s later
FAST_REFRESH_S = 0.5
# A precondition state beside True (pass) / False (fail) / None (can't tell): held by this host's own running check,
# so it frees by itself in a few seconds. Never "blocked", never a warning (round 3 brief item 10).
WAITING = "waiting"
GATE_CACHE_S = 2.0
# WHY every precondition resolves (Zach, 2026-10-29: "I am confused by the can't tell: does that mean it can block or not
# block? The utilities menu should just have 2 states"): what the host cannot read now is either the person's to confirm on
# the Run page, or checked by the driver on the bus right before it sends anything (it refuses, nothing sent). Only a
# precondition that says what the action needs to exist blocks while unknown.
PERSON_CHECKS = frozenset({"joint_free", "joint_clear", "joints_clear", "target_in_view"})
BLOCK_WHEN_UNKNOWN = {"config_recorded": "no recorded configuration for this board"}
OS_GATES = frozenset({"port_free", "bus_lock_free", "camera_free"})
GATE_READING = "being read"  # a gate nobody has an answer to yet: a page asked before the first pass over /proc came back
PRECONDITION_VALUE = "precondition."  # a driver's own answer, a status value precondition.<check> = "pass: ..." | "fail: ..."
ID_SHARED_CODES = frozenset({"IdCollision", "PossibleIdCollision", "SourceIdShared"})
# docs/state-config-v1.md section 4: a page's heartbeat keeps a plugin's ``watch`` running; it stops this long after the last one
WATCH_GRACE_S = 10.0
WATCH_TICK_S = 0.5  # how often the host re-checks every watch's gates and heartbeat
# how often the host looks at each driver's source for an edit; a changed driver is asked ``describe`` again
DESCRIBE_CHECK_S = 10.0
MAX_PLUGIN_EVENTS = 200
WATCH_NOTIFY_S = 1.0  # a 10 Hz watch tells a page's event stream at most this often
STATE_HISTORY_GAP_S = 1.0  # the history keeps a state series at 1 Hz at most
FRAME_FPS_WINDOW_S = 3.0
RECENT = 6
FOLD_EVENTS = 8000  # the newest events the Activity log folds (the store may hold 50,000; a row needs only its neighbours)
RUNS_IN_STATE = 30  # the newest runs /api/v1/state carries (docs/runs-v1.md)
NODE_EVENTS = 20  # the newest events of an entity /api/v1/node carries
# The published view (Host.read_view). WHY a bounded share of one core, and not a period: under a load of 60 a build takes ten times as long, so it runs a
# tenth as often and the host stays answerable; on a quiet machine a change reaches a read in a quarter of a second.
VIEW_GAP_S = 0.25  # at least this long between two builds
VIEW_REST = 4.0  # ... and this many times what the last build took
VIEW_MAX_AGE_S = 2.0  # nothing changed: a build anyway, because what goes stale goes stale with time alone
VIEW_QUIET_S = 10.0  # nobody has read for this long: only that build, which keeps the event log (Host._keep_log's reason)
VIEW_WAIT_S = 2.0  # how long a stream waits for the view of the change that woke it
FIRST_VIEW_WAIT_S = 30.0  # a read that came before the host's first view
HISTORY_FILE = "history.json"
HISTORY_SAVE_S = 120.0  # how much of the history a host killed outright can lose; a stop saves it all. WHY not less: it is megabytes (Preview's Computer alone: 1.2 MB in 7 min)
DESCRIBES_FILE = "describes.json"
STOP_BOUND_S = 4.0  # a stop that takes longer is cut short: the runtime's SIGKILL comes at 5 s, and a restart should not take that long
DRIVERS_WAIT_S = 60.0  # the longest ``serve`` waits to print its line; describe is bounded by 30 s a plugin, in parallel


# ----- clocks -------------------------------------------------------------------------------------------------
class Clock:
    mode = "live"
    utc_offset_s: int | None = None  # display zone; None = this machine's local zone

    def now(self) -> float:
        return time.time()


class ReplayClock(Clock):
    """A fixture's clock: the frame's recorded time, running at 1x from when the host started (plus an offset)."""

    mode = "fixture"

    def __init__(self, start: float, utc_offset_s: int | None, offset_s: float = 0.0):
        self.start, self.utc_offset_s, self._t0 = start + offset_s, utc_offset_s, time.monotonic()

    def now(self) -> float:
        return self.start + (time.monotonic() - self._t0)


class FixedClock(Clock):
    """``once``: one instant, so every record of a run is judged at the same time."""

    def __init__(self, at: float, utc_offset_s: int | None = None, mode: str = "live"):
        self.at, self.utc_offset_s, self.mode = at, utc_offset_s, mode

    def now(self) -> float:
        return self.at


def local_offset_s() -> int:
    offset = dt.datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds()) if offset else 0


# ----- OS sources ---------------------------------------------------------------------------------------------
class LiveOs:
    """This machine, passively: sysfs / by-id / io_facts sources, the kernel log, /proc for holders and locks."""

    mode = "live"
    _files: open_files.OpenFiles | None = None  # the /proc pass every gate answers from; made when a gate is first asked
    _holders = None  # a test's own (port, camera) holder functions, each with a ``.function``, in place of a pass

    def __init__(self, skeleton_ttl_s: float = 2.0):
        from io_facts import udev

        self.hostname = socket.gethostname()
        self.kernel_log = activity_module.LiveKernelLog()
        self.udev_command = list(udev.MONITOR)  # the host listens to udev only for an OS source that has this
        self._skeleton_ttl_s = skeleton_ttl_s
        self._skeleton_at = -1e18
        self._skeleton: tuple[list[dict], list[dict]] = ([], [])
        self._gate_cache: dict[tuple[str, str], tuple[float, tuple[bool | None, str]]] = {}

    def invalidate(self) -> None:
        self._skeleton_at = -1e18
        self.kernel_log.invalidate()

    def skeleton(self) -> tuple[list[dict], list[dict]]:
        if time.monotonic() - self._skeleton_at > self._skeleton_ttl_s:
            self._skeleton = tree.load_skeleton()
            self._skeleton_at = time.monotonic()
        return self._skeleton

    def boots(self) -> list[activity_module.Boot]:
        return self.kernel_log.boots(time.monotonic())

    def close(self) -> None:
        self.kernel_log.close()
        if self._files is not None:
            self._files.close()

    def _open_files(self) -> open_files.OpenFiles:
        if self._files is None:
            self._files = open_files.OpenFiles(self._reread_gates)
        return self._files

    def use_scan(self, source) -> None:
        """Where a pass over /proc comes from (a test's fake). Otherwise: ``open_files.scan`` in a process of its own."""

        self._open_files().source = source

    def gate(self, device: dict | None, check: str) -> tuple[bool | None, str]:
        """Passive precondition checks: who holds the port / camera, who holds the rack bus lock. Never takes it.

        What a watch or a configuration write decides on, so it keeps its promise: an answer no older than ``GATE_CACHE_S``, waiting for a pass if it
        must. A page reads ``peek``, which never waits."""

        key = (str((device or {}).get("id")), check)
        self._asked(key, device, check)
        cached = self._gate_cache.get(key)
        if cached and time.monotonic() - cached[0] < GATE_CACHE_S:
            return cached[1]
        if check != "bus_lock_free":
            self._open_files().fresh(GATE_CACHE_S)
        result = self._gate(device or {}, check)
        self._gate_cache[key] = (time.monotonic(), result)
        return result

    def peek(self, device: dict | None, check: str) -> tuple[bool | None, str]:
        """The same answer for a page: the last one, at once, and the next pass reads it again. WHY it never waits (measured 2026-11-04): an adapter's
        /api/v1/node took 22 s, five actions each waiting for ``port_free``. A gate never read says so (``GATE_READING``), and the driver still checks
        on the bus before it sends anything."""

        key = (str((device or {}).get("id")), check)
        self._asked(key, device, check, strict=False)
        cached = self._gate_cache.get(key)
        if cached:
            return cached[1]
        if check != "bus_lock_free" and self._open_files().latest(open_files.ASKED_S) is None:
            return None, self._reading()
        result = self._gate(device or {}, check)
        self._gate_cache[key] = (time.monotonic(), result)
        return result

    def _asked(self, key: tuple[str, str], device: dict | None, check: str, *, strict: bool = True) -> None:
        files = self._open_files()
        files.wanted[key] = (device or {}, check, time.monotonic())
        files.ask(strict=strict)

    def _reread_gates(self) -> None:
        """After each pass: every gate someone asked about lately. What nobody asked about for a while is dropped from the list, not from the cache."""

        wanted = self._open_files().wanted
        for key, (device, check, asked) in list(wanted.items()):
            if time.monotonic() - asked > open_files.ASKED_S:
                wanted.pop(key, None)
            else:
                self._gate_cache[key] = (time.monotonic(), self._gate(device, check))

    def _reading(self) -> str:
        """Why a gate has no answer: a pass is on its way, or what stopped it. WHY the second: "being read" for ever would be a page lying."""

        return self._open_files().note or GATE_READING

    def _holder_functions(self) -> tuple | None:
        """``(holders of a port, holders of camera nodes)`` as the latest pass knows them; None before the first pass."""

        if self._holders is not None:
            return self._holders[0].function, self._holders[1].function
        found = self._open_files().latest()
        if found is None:
            return None
        return (lambda node: open_files.port_holders(found, node)), (lambda nodes=None: open_files.camera_holders(found, nodes))

    def _gate(self, device: dict, check: str) -> tuple[bool | None, str]:
        from io_facts import gate, lock

        if check in host_utilities.CHECKS:
            return usb_reset_gate(device, check, self._open_files().latest())
        attrs = device.get("attrs") or {}
        functions = device.get("functions") or []

        def nodes_of(bus: str, own_key: str) -> list[str]:
            # the device's own nodes (fallback skeleton) + its function nodes' by-id links or /dev names (lane B)
            found = list(attrs.get(own_key) or [])
            for function in functions:
                if function.get("bus") == bus:
                    function_attrs = function.get("attrs") or {}
                    found += list(function_attrs.get("by_id") or []) or [str(function.get("label", "")).split(" · ")[0]]
            return [node for node in found if node]

        if check == "bus_lock_free":
            holders = lock.lock_holders()
            if not holders:
                return True, "rack bus lock free (/proc/locks, never taken)"
            return bus_lock_state(holders)
        holders = self._holder_functions()
        if check == "port_free":
            nodes = list(attrs.get("by_id") or []) or nodes_of("tty", "ttys")
            if not nodes:
                return None, "no serial port node for this device"
            if holders is None:
                return None, self._reading()
            port_holders = _without_own_watchers(holders[0])
            held = [holder for node in nodes for holder in port_holders(node)]
            mine = [holder for holder in held if _holder_pid(holder) and RUNNING.owner(_holder_pid(holder))]
            if held and len(mine) == len(held):
                # WHY waiting, not blocked (2026-10-29, "blocked: held by 1652054 python3"): that was a routine diagnose, ours or
                # the other channel's host, holding the port for a few seconds; a person's run of ours waits its turn
                label, elapsed = RUNNING.owner(_holder_pid(mine[0]))
                return WAITING, f"this host's own check is running (a few s): {label}, {elapsed:.0f} s so far"
            others = [holder for holder in held if holder not in mine]
            if held and all(other_robotdx(_holder_pid(holder)) for holder in others):
                return WAITING, "; ".join(holder_words(holder) for holder in others) + " (a few s)"
            if held:
                return False, "held by " + "; ".join(holder_words(holder) for holder in others)
            decision = gate.decide(nodes, holders_fn=port_holders, uses_rack_lock=False)
        elif check == "camera_free":
            if holders is None:
                return None, self._reading()
            video = [node for node in nodes_of("v4l", "video_nodes") if node.startswith("/dev/video")]
            decision = gate.decide(video, camera=True, camera_holders_fn=_without_own_watchers(holders[1]), uses_rack_lock=False)
        else:
            return None, f"{check}: not a check the host evaluates"
        if decision.state == "free":
            return True, "no holder (/proc, live)" if check == "port_free" else "no other process holds the camera"
        if decision.state == "blocked":
            return False, decision.reason.replace("open by", "held by")
        return None, decision.reason


def usb_reset_gate(device: dict, check: str, scan: dict | None = None) -> tuple[bool | None, str]:
    """The host's own ``reset_port`` preconditions (docs/usb-reset.md). ``reset_privilege``: may this user reset the port by any
    of the three mechanisms. ``device_free``: nothing else has the device (or what hangs below it) open; this host's own watch /
    follow processes do not count, they yield. Read passively (sysfs, /proc), never opens the device. ``scan``: a pass over /proc
    to read that from (``open_files``), else a walk of its own."""

    target = usb_reset.target_of(device)
    if target is None:
        return None, "not a USB device with a port"
    if check == "reset_privilege":
        return usb_reset.privilege(target)
    found, unseen = usb_reset.holders(target.port, frozenset(RUNNING.passive_sessions()), scan)
    if found:
        return False, "; ".join(holder.text() for holder in found)
    return True, "no process has it open" + (f" (could not look at {unseen} other users' processes)" if unseen else "")


def _without_own_watchers(holders_fn):
    """``holders_fn`` minus the holders that are this host's own ``watch`` / ``follow`` processes.

    WHY: a watch holds the port for the State tab, so without this the host's own watch blocked its own ``port_free`` gate
    and, for ~10 s after the person left the tab, every config write to the same board (the gates are the host's, the
    watch is the host's: it yields, see ``Host.release_watches``). A holder string is ``pid COMMAND``."""

    def filtered(*args, **kwargs):
        kept = []
        for holder in holders_fn(*args, **kwargs):
            lead = str(holder).split(None, 1)[0] if str(holder).strip() else ""
            if lead.isdigit() and RUNNING.is_passive(int(lead)):
                continue
            kept.append(holder)
        return kept

    return filtered


def _holder_pid(holder) -> int | None:
    lead = str(holder).split(None, 1)[0] if str(holder).strip() else ""
    return int(lead) if lead.isdigit() else None


def _cmdline(pid: int, proc: Path = Path("/proc")) -> list[str]:
    try:
        return [part for part in (proc / str(pid) / "cmdline").read_bytes().decode(errors="replace").split("\0") if part]
    except OSError:
        return []


def _parent(pid: int, proc: Path = Path("/proc")) -> int | None:
    try:
        stat = (proc / str(pid) / "stat").read_text()
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[-1].split()
    return int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None


def other_robotdx(pid: int | None, proc: Path = Path("/proc")) -> str:
    """``"RobotDx (tray, port 8822)"`` when ``pid`` was started by another RobotDx host on this machine (the other release
    channel's), else "". Read from /proc: its own or an ancestor's command line runs ``io_diagnostics/cli.py``."""

    seen = 0
    while pid and pid > 1 and seen < 6:
        argv = _cmdline(pid, proc)
        script = next((index for index, part in enumerate(argv) if part.endswith("io_diagnostics/cli.py")), None)
        if script is not None:
            rest = argv[script + 1:]
            verb = rest[0] if rest else "serve"
            port = rest[rest.index("--port") + 1] if "--port" in rest[:-1] else ""
            return f"RobotDx ({verb}{f', port {port}' if port else ''})"
        pid, seen = _parent(pid, proc), seen + 1
    return ""


def holder_words(holder, proc: Path = Path("/proc")) -> str:
    """Who holds a port, in plain words: another RobotDx host's routine check, tview, moteus_tool, else the program."""

    pid = _holder_pid(holder)
    argv = _cmdline(pid, proc) if pid else []
    text = " ".join(argv)
    host = other_robotdx(pid, proc) if pid else ""
    if host:
        verb = next((word for word in ("diagnose", "probe", "watch", "follow", "act") if f" {word} " in f" {text} "), "")
        driver = next((part for part in argv if part.endswith(".diagnostics")), "")
        what = " ".join(filter(None, [driver, verb]))
        return f"{host} has it open ({f'{what}, ' if what else ''}pid {pid})"
    if "tview" in text:
        return f"tview, moteus's console (pid {pid}): close it to free the port"
    if "moteus_tool" in text:
        return f"moteus_tool (pid {pid})"
    return f"{holder}" if not argv else f"pid {pid} ({' '.join(argv[:3])[:80]})"


def resolves(check: str, state: bool | str | None) -> str:
    """What one precondition does to its action (docs/api-v1.md, "Actions: two states"): ``ready`` (met), ``blocks`` (the
    action cannot run now, the detail says why), ``waiting`` (this host's own check, a few seconds), ``confirm`` (the
    person confirms it on the Run page) or ``at_run`` (the driver checks it on the bus right before it sends anything)."""

    if check in PERSON_CHECKS and state is not False:
        return "confirm"
    if state is True:
        return "ready"
    if state is False:
        return "blocks"
    if state == WAITING:
        return "waiting"
    return "blocks" if check in BLOCK_WHEN_UNKNOWN else "at_run"


def bus_lock_state(holders) -> tuple[bool | str, str]:
    """The rack bus lock is held: by one of THIS host's own running checks (``WAITING``: it frees in a few seconds,
    the host's own jobs take turns), or by anyone else (``False``: blocked, naming the pid and its command line).
    Holders that only share it (probes: a camera's query, a CAN adapter's) leave it free (``True``).

    WHY not "blocked" for our own: the lock is the host's own diagnose doing its job; calling it an experiment, in a
    blocking colour, sent people looking for a process that was the dashboard itself (round 3 brief item 10).

    WHY shared holders do not block (Zach, 2026-10-29: "they are likely completely different ... should not be
    blocked"): probes take the lock shared and a rack experiment exclusive, so only a WRITE holder is an experiment."""

    shared = [holder for holder in holders if not holder.exclusive]
    holders = [holder for holder in holders if holder.exclusive]
    if not holders:
        return True, f"rack bus lock free for probes ({len(shared)} probe{'s' if len(shared) != 1 else ''} share it)"
    foreign = [holder for holder in holders if RUNNING.owner(holder.pid) is None]
    if foreign:
        return False, "blocked by " + ", ".join(f"pid {holder.pid} ({holder.command})" for holder in foreign)
    label, elapsed = max((RUNNING.owner(holder.pid) or ("", 0.0) for holder in holders), key=lambda found: found[1])
    return WAITING, f"this host's own check is running (a few s): {label}, {elapsed:.0f} s so far"


class MachineOs:
    """A FAKE machine built from a lane-B sysfs spec (``tests/data/sysfs-rack-fixture.json``: a frame grabber with no
    driver, a capture card trained x1 of x4, two EtherCAT NICs, ...): its skeleton through the real io_facts sources,
    no kernel log, no holder checks. WHY: hardware this PC does not have (PCIe grabbers, EtherCAT) must still be
    reviewable in the real dashboard, and through the same source code that will read it on the rack."""

    mode = "machine"

    def __init__(self, spec: Path):
        import tempfile

        from io_facts import skeleton

        self._directory = tempfile.TemporaryDirectory(prefix="io-diagnostics-machine-")  # removed at exit
        self.root = skeleton.fake_root(Path(spec), Path(self._directory.name))
        self.hostname = f"{Path(spec).stem} (fake machine)"
        self._skeleton = skeleton.skeleton

    def invalidate(self) -> None:
        pass

    def skeleton(self) -> tuple[list[dict], list[dict]]:
        devices = [dict(device) for device in self._skeleton(self.root)]
        return devices, [{"name": "io_facts.skeleton (fake machine)", "state": "used", "note": f"{len(devices)} entries"}]

    def boots(self) -> list[activity_module.Boot]:
        return []

    def gate(self, device: dict | None, check: str) -> tuple[bool | None, str]:
        return None, f"{check}: not evaluated on a fake machine"


def resolve_fixture(name: str) -> str:
    return FIXTURE_ALIASES.get(name.lower(), name)


def load_fixture(name: str, path: Path = FIXTURE_FILE) -> tuple[dict, dict]:
    """``(document, frame)`` for a fixture name (F1 / F2 / F3, or plug / bus-busy / after-restart)."""

    document = json.loads(path.read_text())
    frame_name = resolve_fixture(name)
    if frame_name not in document["frames"]:
        raise SystemExit(f"unknown fixture {name!r}: one of {', '.join(document['frames'])} (or {', '.join(FIXTURE_ALIASES)})")
    return document, document["frames"][frame_name]


class FixtureOs:
    """A recorded frame's OS side: its skeleton dicts, its kernel log, its recorded holder / lock states."""

    mode = "fixture"

    def __init__(self, frame: dict):
        self.frame = frame
        self.hostname = frame.get("hostname", "rack-pc")

    def invalidate(self) -> None:
        pass

    def skeleton(self) -> tuple[list[dict], list[dict]]:
        devices = [json.loads(json.dumps(device)) for device in self.frame["skeleton"]]
        return devices, [{"name": f"fixture {self.frame['name']} (recorded io_facts.usb)", "state": "used",
                          "note": f"{len(devices)} devices"}]

    def boots(self) -> list[activity_module.Boot]:
        return [activity_module.Boot(boot["start"], boot["lines"], boot.get("end")) for boot in self.frame["kernel_boots"]]

    def gate(self, device: dict | None, check: str) -> tuple[bool | None, str]:
        recorded = self.frame.get("gates", {}).get(str((device or {}).get("id")), {}).get(check)
        if recorded is None:
            return None, f"{check}: not recorded in this scenario"
        return recorded[0], recorded[1]


# ----- devices the host has seen (expected-but-absent) ------------------------------------------------------------
SEEN_FILE = "seen.json"
SEEN_WRITE_S = 300.0  # refresh a known device's last_seen on disk at most this often; a new device is written at once
EXPECTED_SOURCE = "io_diagnostics.expected"
# WHY one OK status on the Computer, never a row or a warning: a device only SEEN (a plugin diagnosed it once) is not
# expected. FDA81D29 was plugged for 15 s on 2026-10-23 and its unplug confirmed an ExpectedAbsent WARN from seen.json
# alone (Zach: a seen-only device must not warn when it goes absent). And a missing thing is a diagnostic on its parent,
# never a ghost row (Zach, 2026-10-24), so the memory is a list in the Computer's detail.
RECENTLY_SEEN_S = 86400.0  # a seen-only device is listed for a day after it was last seen, then not


def _write_json(path: Path, data: dict) -> None:
    """``data`` as ``path``, atomically: a crash mid-write leaves the old file, never half of one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w") as file:
            json.dump(data, file, default=str, separators=(",", ":"))
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _pid_alive(pid: int) -> bool:
    """A process that exists and is not a zombie (/proc, no signal sent)."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[-1].split()[:1] != ["Z"]


def default_state_dir() -> Path:
    """``$IO_DIAGNOSTICS_STATE_DIR``, else ``~/.local/state/io-diagnostics`` (the XDG state home)."""

    return Path(os.environ.get("IO_DIAGNOSTICS_STATE_DIR") or Path.home() / ".local" / "state" / "io-diagnostics")


class SeenDevices:
    """``hardware_id -> {name, first_seen, last_seen}`` of the USB devices a plugin diagnosed, in a small JSON file.

    WHY only devices a plugin claimed (and only repeatable ``usb:<vid>:<pid>:<serial>`` ids): remembering every USB
    stick ever plugged would leave a permanent "expected" warning per stick; a device a driver diagnoses is rack
    hardware someone expects back. WHY atomic (temp file + rename in the same directory): a crash mid-write must
    leave the old file, never half of one. ``path=None`` keeps it in memory only (tests, fixtures, fake machines)."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.devices: dict[str, dict] = {}
        # hardware_id -> when a person chose Forget: kept out of the absent rows (a plugin's describe may still list
        # it) until the device is seen again, when it is remembered as any device is
        self.forgotten: dict[str, float] = {}
        self.note = ""  # why the file could not be read at start (sticky: the old content was dropped)
        self.write_note = ""  # why the latest write failed; cleared by the next good one
        self._written = -1e18
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.devices = {str(key): dict(value) for key, value in (data.get("devices") or {}).items()
                                if isinstance(value, dict)}
                self.forgotten = {str(key): float(value) for key, value in (data.get("forgotten") or {}).items()
                                  if isinstance(value, (int, float))}
            except (OSError, ValueError, AttributeError) as error:
                self.note = f"{self.path}: unreadable ({type(error).__name__}: {error}); starting empty"

    def mark(self, seen: dict[str, str], now: float) -> None:
        """``seen``: hardware_id -> name, present now."""

        new = False
        for hardware_id, name in seen.items():
            entry = self.devices.get(hardware_id)
            if entry is None:
                entry, new = {"first_seen": now}, True
                self.devices[hardware_id] = entry
            if self.forgotten.pop(hardware_id, None) is not None:
                new = True
            entry.update(name=name, last_seen=now)
        if self.path and seen and (new or time.monotonic() - self._written > SEEN_WRITE_S):
            self._write()

    def forget(self, hardware_id: str, now: float) -> bool:
        """Drop a device from the memory and keep it out of the absent rows until it is seen again; True if it was
        remembered. Written at once (a person asked)."""

        known = self.devices.pop(hardware_id, None) is not None
        self.forgotten[hardware_id] = now
        if self.path:
            self._write()
        return known

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(handle, "w") as file:
                json.dump({"version": 1, "devices": self.devices, "forgotten": self.forgotten}, file, indent=1, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self._written = time.monotonic()
            self.write_note = ""
        except OSError as error:  # a read-only home must not stop the host; the reason shows in host.sources
            self.write_note = f"{self.path}: not written ({type(error).__name__}: {error})"


def _seen_text(last_seen, utc_offset_s: int | None) -> str:
    if isinstance(last_seen, (int, float)) and not isinstance(last_seen, bool):
        return (activity_module.iso(float(last_seen), utc_offset_s) or "")[:16].replace("T", " ")
    return str(last_seen or "")


# ----- the host -------------------------------------------------------------------------------------------------
@dataclass
class View:
    now: float
    layer: tree.OsLayer
    merged: tree.Merged
    rows: tree.Rows
    events: list[Event]
    faults_by_path: dict[str, int]
    setup: expected_module.SetupView | None = None
    checks: list[DiagnosticStatus] = dataclasses.field(default_factory=list)  # the checks' results, attached
    memory: dict[str, float] = dataclasses.field(default_factory=dict)  # path -> last heard: restored, no fresh report yet
    live: frozenset = frozenset()  # paths whose data is arriving now (a follower is running)
    expected_paths: frozenset = frozenset()  # device rows the expected setup (or its retired list) names
    composition: composition_module.CompositeView | None = None  # the composites against this tree (docs/composition-v1.md)
    rates: dict[str, dict] = dataclasses.field(default_factory=dict)  # path -> received vs expected records per second (history.rates)
    # every job as this view found it (its cache copied): what a read needs of a plugin, with no lock to wait for
    jobs: dict[tuple[str, str], Job] = dataclasses.field(default_factory=dict)
    memo: dict = dataclasses.field(default_factory=dict)  # what reads derive from this view, each thing once: the names, the folded log, /state's heavy parts
    # WHY in the view: the log's rows pair a run's events with whether it still runs, and a run that ended just after the build would otherwise read "the host stopped while it ran"
    active_runs: frozenset = frozenset()
    revision: int = -1  # Host.revision when this build began


def _frozen(job: Job) -> Job:
    """``job`` with a copy of its cache as it stands. WHY no ``series``: its deques take every 10 Hz point, and nothing a view answers reads them (a ``values``
    read does, under the host's lock). Never run or changed: it is a record of a moment."""

    cache = job.cache
    return dataclasses.replace(job, cache=dataclasses.replace(
        cache, entities=dict(cache.entities), order=list(cache.order), icons=dict(cache.icons), statuses=dict(cache.statuses), events=dict(cache.events),
        states=dict(cache.states), configs=dict(cache.configs), frames=dict(cache.frames), series={}))


class _Blank(dict):
    def __missing__(self, key):
        return "{" + key + "}"


@dataclass
class Watch:
    """One entity a page is looking at: the plugin's ``watch`` process for it, why it is not running, when it was last asked for."""

    job: Job
    rel: str  # the entity's path relative to its device ("" = the device)
    declared: dict  # describe's ``watch``: {"period_s", "gates"}
    frame_dir: Path
    last_beat: float  # time.monotonic() of the latest heartbeat
    follower: Watcher | None = None
    gate: dict | None = None  # the host's own reason it does not run: {"reason", "holders"}
    plugin_blocked: dict | None = None  # the plugin said PortBusy / BusLockHeld itself: {"reason", "holders"}
    started: float | None = None
    notified: float = 0.0
    stopped: bool = False
    paused: int = 0  # >0 while a run (a config write, a utility) uses the same board: the watch yields the port, then resumes


class _NoOwner:
    owner = ""


@dataclass
class _ActPlan:
    """What a real or dry act would run: the owning job, the argv, and the entity and action it is for."""

    job: Job
    argv: list[str]
    device_path: str
    entity: Entity
    registered: Action
    node: int | None
    new_id: int | None


def _diagnostics_match(entity) -> tuple[str, str]:
    """(model_id, composite kind) of an entity: what diagnostics.toml's model_id and [[part]] tables match on (stock_diagnostics.py).
    A composite carries its kind as identity "model" (composition.entity_of)."""

    identity = entity.identity or {}
    kind = str(identity.get("model") or "") if entity.kind == "composite" else ""
    return str(identity.get("model_id") or ""), kind


class Host:
    def __init__(self, *, plugins_dir: Path = PLUGINS_DIR, clock: Clock | None = None, os_source=None,
                 fixture: dict | None = None, plugin_env: dict[str, str] | None = None, enable: tuple[str, ...] = (),
                 state_file: Path | None = None, expected_file: Path | None = None, settings_file: Path | None = None,
                 history_window_s: float | None = None, follow_backoff_s: tuple[float, float] | None = None,
                 recheck_poll_s: float | None = None, recheck_settle_s: float | None = None,
                 recheck_backoff_s: float | None = None, runs_dir: Path | None = None, run_grace_s: float | None = None,
                 run_ring_bytes: int | None = None, run_budget_bytes: int | None = None, run_bus_wait_s: float | None = None,
                 run_bus_poll_s: float | None = None, run_bus_settle_s: float | None = None,
                 event_log_dir: Path | None = None, boot_id: str = "", boot_start: float | None = None,
                 event_log_cap_bytes: int | None = None, event_log_segment_bytes: int | None = None,
                 ledger_file: Path | None = None, history_file: Path | None = None, describes_file: Path | None = None, restore_later: bool = False,
                 watch_grace_s: float | None = None, watch_tick_s: float | None = None, frames_dir: Path | None = None,
                 describe_check_s: float | None = None, diagnostics_beat_s: float | None = None,
                 stock_diagnostics_defaults: bool = True):
        self.plugins_dir = Path(plugins_dir)
        self.describe_check_s = describe_check_s if describe_check_s is not None else DESCRIBE_CHECK_S
        # WHY settable: the defaults are the product (probe.py: holders' pids every 1 s, 0.5 s settle, 10 s first
        # backoff); a test of the backoff alone shortens the last
        self.recheck_poll_s = recheck_poll_s if recheck_poll_s is not None else probe_module.POLL_S
        self.recheck_settle_s = recheck_settle_s if recheck_settle_s is not None else probe_module.SETTLE_S
        self.recheck_backoff_s = recheck_backoff_s if recheck_backoff_s is not None else probe_module.BACKOFF_FIRST_S
        self.follow_backoff_s = follow_backoff_s  # None: runner.FOLLOW_BACKOFF_S (tests make it short)
        self.clock = clock or Clock()
        self.os = os_source or LiveOs()
        self.fixture = fixture  # {"name", "label", "story", "clock", "frames": [...]} or None
        self.plugin_env = dict(plugin_env or {})
        self.specs, self.plugin_problems = load_plugins(self.plugins_dir)
        # WHY a run-time switch: a plugins.d file ships disabled when its diagnose talks to hardware; the one live
        # run says --enable NAME instead of editing (and forgetting to revert) the declaration.
        known = {spec.name for spec in self.specs}
        self.plugin_problems += [f"--enable {name}: no plugin of that name in {self.plugins_dir}" for name in enable
                                 if name not in known]
        self.specs = [dataclasses.replace(spec, enabled=True) if spec.name in enable else spec for spec in self.specs]
        self.jobs: dict[tuple[str, str], Job] = {}
        # Monitoring Plugins checks (kind = "check"): one job each, not bound to a skeleton device; their latest result
        # is kept here and attached to its entity on every view (the entity can move or come and go)
        self.check_jobs: dict[str, Job] = {spec.name: Job(spec, {"id": f"check:{spec.name}"}, "")
                                           for spec in self.specs if spec.kind == "check" and spec.enabled}
        self.check_results: dict[str, DiagnosticStatus] = {}
        self.gates = Gates()  # one per host: `once`, the scheduler and act all take turns per `exclusive` group
        self.seen = SeenDevices(state_file)  # USB devices a plugin diagnosed, remembered across runs (None: memory)
        # the last-known entities of every (plugin, device), so a restarted host shows the same rows, hollow, until fresh
        # data replaces them (roster.py; None: memory only)
        self.roster = roster_module.Roster(ledger_file)
        # the person's rig manifest (docs/expected-setup.md); None = nothing expected and no edits (fixture / machine)
        self.setup = expected_module.ExpectedSetup(expected_file)
        self.setup.answers = self.roster.rigs  # WHY the same dict: the rig rows write what the ledger keeps
        # the composites (docs/composition-v1.md): the file's, none while it has problems; the latest view's rollups and where each
        # child is, which a composite plugin's environment is made from
        self._composites: list = []
        self._composition: composition_module.CompositeView | None = None
        # the app-wide settings (settings.toml; None: memory only) and the rolling history they size (history.py)
        self.settings = settings_module.Settings(settings_file, history_window_s)
        self.history = history_module.History(self.settings.history_window_s)
        self.layer = tree.OsLayer()
        self.sources: list[dict] = []
        self.os_records: list[DiagnosticStatus] = []
        self.usb = activity_module.UsbActivity()
        self.live = activity_module.LiveActivity(self.clock.now)  # udev's provisional plugs and binds, sysfs carriers
        self.pending: list[str] = []  # provisional event ids the kernel log has not settled yet
        self.followers: dict[tuple[str, str], Follower] = {}
        # the entity page's live data (docs/state-config-v1.md): the watches pages keep alive, their frame directories
        self.watch_grace_s = watch_grace_s if watch_grace_s is not None else WATCH_GRACE_S
        self.watch_tick_s = watch_tick_s if watch_tick_s is not None else WATCH_TICK_S
        self.watches: dict[tuple, Watch] = {}
        self._watch_lock = threading.RLock()
        self._watch_thread: threading.Thread | None = None
        self._frames_dir = Path(frames_dir) if frames_dir else None
        self._frames_owned = False
        self._frame_times: dict[tuple, collections.deque] = {}
        # the Configuration tab: edit sessions, each setting's edit state, every write's Activity event (config_edit.py), and the
        # person's pins / order / folded folders (arrangements.py: beside settings.toml; memory only without one)
        self.config_editor = ConfigEditor(self, self.watch_grace_s)
        self.arrangements = Arrangements(Path(settings_file).parent / ARRANGEMENTS_FILE if settings_file else None)
        # the person's dashboards as files (dashboards.py), beside settings.toml; none without one (a host built by hand)
        self.dashboards = DashboardStore(Path(settings_file).parent / DASHBOARDS_DIR) if settings_file else None
        # robotdx.diagnostics (stock_diagnostics.py): which stock diagnostic runs on which entity, from diagnostics.toml beside
        # settings.toml (else the default set), and the latest status of each, by (entity path, status name), that the beat made
        self.stock_diagnostics = stock_diagnostics_module.StockDiagnostics(
            Path(settings_file).parent / stock_diagnostics_module.DIAGNOSTICS_FILE if settings_file else None,
            diagnostics_beat_s if diagnostics_beat_s is not None else stock_diagnostics_module.BEAT_S, defaults=stock_diagnostics_defaults)
        self.stock_results: dict[tuple[str, str], DiagnosticStatus] = {}  # what the views post
        self.stock_quiet: dict[tuple[str, str], DiagnosticStatus] = {}  # not run, no row: only the drill-in lists them (stock_diagnostics.quiet)
        # every change a page could see (a refresh, a udev event, a plugin's records): /api/v1/events waits on it
        self.changes = threading.Condition()
        self.revision = 0
        self.change_reason = ""
        # every State point a plugin run or a watch took in: /api/v1/live's samples lane wakes on it (a watch's 10 Hz is not a ``changes`` tick)
        self.sample_changes = threading.Condition()
        self.sample_revision = 0
        self.feed = LiveFeed(self)  # GET /api/v1/live: one change-only diff of state() for every client (live.py)
        # The published view (read_view): a service's reads answer from it, and only io-view builds it. A host built by hand, or `once`, builds what it reads.
        self.published = threading.Condition()
        self._published: View | None = None
        self._published_revision = -1
        self._view_lock = threading.RLock()  # one build at a time
        self._serving = False
        self._readers = threading.local()  # which threads answer from the published view (reads_published)
        self._read_at = -1e18  # when one last did
        self._view_wanted = threading.Event()  # a stream is waiting for the view of a change (wait_view): the build does not wait for its timer
        self.drivers_started = threading.Event()  # the drivers are described and scheduled: what ``serve``'s "IO diagnostics on" line says
        self._wake = threading.Event()
        self.faults = Faults()
        # the host's events on disk (docs/activity-log-v1.md): ``event_log_dir`` None keeps the same history in memory
        # only (a host built by hand, a fixture replay, ``once``)
        boot_id, boot_start = self._boot_identity(boot_id, boot_start)
        # WHY later for a service: the server answers first (cli.py), and reading the log is seconds under load
        self._restore_later = restore_later
        self.eventlog = eventlog_module.EventLog(
            event_log_dir, boot_id=boot_id, boot_start=boot_start,
            cap_bytes=event_log_cap_bytes or eventlog_module.CAP_BYTES,
            segment_bytes=event_log_segment_bytes or eventlog_module.SEGMENT_BYTES, load=not restore_later)
        self.history_file = Path(history_file) if history_file else None
        self.describes_file = Path(describes_file) if describes_file else None
        self.raw_kernel = logview_module.RawKernel()
        self.describes: dict[str, dict | None] = {spec.name: None for spec in self.specs}
        self.url = ""
        self.lock = threading.RLock()
        # every real act (docs/runs-v1.md): a process on a PTY, its output ring and recording, its events. ``runs_dir``
        # None keeps them in memory only (a host built by hand, a fixture replay)
        self.runs = terminal_module.RunRegistry(
            runs_dir, self.clock.now, lambda stamp: activity_module.iso(stamp, self.utc_offset_s), self.notify,
            ring_bytes=run_ring_bytes or terminal_module.RING_BYTES,
            budget_bytes=run_budget_bytes or terminal_module.CAST_BUDGET_BYTES,
            grace_s=run_grace_s if run_grace_s is not None else terminal_module.GRACE_S,
            bus_wait_s=run_bus_wait_s if run_bus_wait_s is not None else terminal_module.BUS_WAIT_S,
            bus_poll_s=run_bus_poll_s if run_bus_poll_s is not None else terminal_module.BUS_POLL_S,
            bus_settle_s=run_bus_settle_s if run_bus_settle_s is not None else terminal_module.BUS_SETTLE_S)
        # consoles a run launched (tview, realsense-viewer) that are still open: device id -> {pid, program, ...}; the
        # device's row says so while the process lives (docs/actions-vocabulary-v0.1.md, open_console)
        self.consoles: dict[str, dict] = {}
        self.runs.on_end = self._run_ended
        self.started = time.monotonic()
        self._last_os_level: dict[tuple[str, str], tuple[str, str]] = {}
        self._described: set[str] = set()
        # a driver updated in place keeps being run from its path: its source stamp when describe was last asked, the plugins whose
        # matches came from describe (a reload may change them), why a re-describe failed (the old one keeps running) and the
        # PluginReloaded events (check_plugin_sources)
        self._source_stamps: dict[str, tuple[int, int] | None] = {}
        self._adopted: set[str] = set()
        self.describe_failed: dict[str, str] = {}
        self.plugin_events: list[Event] = []
        self._plugin_event_seq = 0
        self._scheduler: Scheduler | None = None
        self._stop = threading.Event()
        self._udev = None

    @property
    def utc_offset_s(self) -> int | None:
        return self.clock.utc_offset_s

    def _boot_identity(self, boot_id: str, boot_start: float | None) -> tuple[str, float]:
        """Which boot the event log's file is: the machine's (the kernel's boot id), or a replay's frame."""

        if self.fixture is not None:
            return boot_id or f"fixture-{self.fixture['name']}", boot_start if boot_start is not None else float(self.fixture["clock"])
        if not boot_id:
            try:
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            except OSError:
                boot_id = "unknown"
        return boot_id, boot_start if boot_start is not None else (activity_module.boot_start() or time.time())

    # --- plugins that declare their matches in describe ------------------------------------------------------------
    def adopt_described_matches(self) -> None:
        """Every enabled plugin's ``describe``, once, before the first match: a declaration without ``matches`` takes
        them from it (docs/build-plan-v1.md: describe returns matches), and its ``expected`` devices (record v0.1)
        feed the absent rows. describe must be passive; it runs bounded by the plugin's timeout, in parallel, never
        more than once per plugin."""

        pending = [spec for spec in self.specs if spec.enabled and spec.kind == "driver" and spec.name not in self._described
                   and self.describes.get(spec.name) is None]
        results: dict[str, dict] = {}

        def ask(spec) -> None:
            self._source_stamps[spec.name] = source_stamp(spec)  # before describe: an edit made while it runs is seen next
            results[spec.name] = describe(spec, timeout_s=min(spec.timeout_s, 30.0), extra_env=self._job_env())

        threads = [threading.Thread(target=ask, args=(spec,), daemon=True) for spec in pending]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for spec in pending:
            self._adopt(spec, results.get(spec.name) or {"error": "describe did not run"})
        self._save_describes()

    def _adopt(self, spec, answer: dict) -> None:
        """One plugin's describe answer, for the first time this process: its matches, when its declaration names none."""

        from io_diagnostics.plugins import PluginConfigError, parse_match

        self._described.add(spec.name)
        self.describes[spec.name] = answer
        if spec.matches:
            return  # its declaration's matches win; describe was asked for its expected devices
        matches = []
        for item in answer.get("matches") or []:
            try:
                matches.append(parse_match(item))
            except PluginConfigError as error:
                self.plugin_problems.append(f"{spec.name}: describe {error}")
        if answer.get("error") or not matches:
            self.plugin_problems.append(f"{spec.name}: no matches in its declaration or its describe"
                                        + (f" ({answer['error']})" if answer.get("error") else ""))
            self._adopted.add(spec.name)  # a later describe may have them
            return
        self._adopted.add(spec.name)
        self.specs = [dataclasses.replace(spec, matches=tuple(matches)) if other.name == spec.name else other for other in self.specs]

    # --- what a restarted service knows at once ------------------------------------------------------------------------
    def _restore_describes(self) -> None:
        """The drivers' last describe answers, for the ones whose source has not changed since. WHY (Zach, 2026-11-04: "A server restart is invisible"): a
        driver described by ``uv run`` under load is seconds, and until then a camera has no job, so its streams, its tabs and its remembered rows
        were gone from the page. ``_start_drivers`` asks again behind the first view; this only says what the same code said last time."""

        if self.describes_file is None:
            return
        try:
            saved = json.loads(self.describes_file.read_text())
        except (OSError, ValueError):
            return
        for spec in self.specs:
            entry = (saved.get("plugins") or {}).get(spec.name) if isinstance(saved, dict) else None
            stamp = source_stamp(spec)
            if spec.enabled and spec.kind == "driver" and isinstance(entry, dict) and isinstance(entry.get("answer"), dict) \
                    and not entry["answer"].get("error") and stamp is not None and list(stamp) == entry.get("stamp"):
                self._source_stamps[spec.name] = stamp
                self._adopt(spec, entry["answer"])

    def _save_describes(self) -> None:
        if self.describes_file is None:
            return
        plugins = {name: {"stamp": list(self._source_stamps[name]), "answer": answer} for name, answer in self.describes.items()
                   if answer and not answer.get("error") and self._source_stamps.get(name)}
        _write_json(self.describes_file, {"version": 1, "plugins": plugins})

    def _restore_history(self) -> None:
        if self.history_file is None:
            return
        try:
            self.history.restore(json.loads(self.history_file.read_text()), self.clock.now())
        except (OSError, ValueError, AttributeError, KeyError, TypeError):  # a torn or foreign file: a history that starts here, never a host that does not start
            return

    def save_history(self) -> None:
        if self.history_file is not None:
            _write_json(self.history_file, self.history.dump(self.clock.now()))

    def _history_loop(self) -> None:
        while not self._stop.wait(HISTORY_SAVE_S):
            try:
                self.save_history()
            except (OSError, ValueError) as error:  # a full disk must not stop the host; it shows in sources
                self.sources = [*self.sources, {"name": "history file", "state": "skipped", "note": f"{type(error).__name__}: {error}"}]

    # --- refresh: OS skeleton + kernel log -> layer, jobs, OS checks ----------------------------------------------
    def refresh(self) -> None:
        if not self._serving:  # WHY: a service's drivers are described behind the first view (_start_drivers)
            self.adopt_described_matches()
        self.setup.reload_if_changed()  # a hand edit of expected.toml shows within one refresh
        now = self.clock.now()
        devices, sources = self.os.skeleton()
        boots = self.os.boots()
        usb = activity_module.usb_activity(boots, now, self.utc_offset_s)
        layer = tree.build_os_layer(self.os.hostname, devices, extra_ports=frozenset(usb.recent_ports))
        composites = self.setup.manifest.active_composites()
        composition_module.add_to_layer(layer, composites)  # their rows (the file's) right under the Computer
        usb.events = [self._placed(event, layer) for event in usb.events]
        usb.events = self._downgrades(usb.events, layer)
        self.live.carriers([device for device in devices if device.get("bus") == "net"])
        pairs = match_devices(self.specs, layer.matchables())
        claimed = {layer.entities[path].hardware_id: layer.entities[path].name
                   for path in {layer.device_paths[str(device.get("id"))] for _spec, device in pairs}
                   if activity_module.usb_id(layer.entities[path].hardware_id)}
        self.seen.mark(claimed, now)
        absent = self._absent_rows(layer, boots, now)
        if self.seen.note or self.seen.write_note:
            note = " · ".join(text for text in (self.seen.note, self.seen.write_note) if text)
            sources = [*sources, {"name": "seen devices", "state": "skipped", "note": note}]
        if self.setup.note:
            sources = [*sources, {"name": "expected setup", "state": "skipped", "note": self.setup.note}]
        if self.roster.note or self.roster.write_note:
            sources = [*sources, {"name": "last-known tree", "state": "skipped",
                                  "note": " · ".join(text for text in (self.roster.note, self.roster.write_note) if text)}]
        with self.lock:
            wanted = {}
            self._composites = composites
            for spec, device in [*pairs, *self._composite_pairs(composites)]:
                key = (spec.name, str(device.get("id")))
                job = self.jobs.get(key)
                if job is None:
                    job = Job(spec, device, layer.device_paths[str(device.get("id"))])
                    self.roster.restore(job, layer.entities[job.device_path].hardware_id)
                job.device, job.device_path = device, layer.device_paths[str(device.get("id"))]
                devnum = (device.get("attrs") or {}).get("devnum")
                if job.connection is not None and devnum is not None and devnum != job.connection:
                    job.probed, job.blocked, job.next_due = False, None, 0.0  # unplugged and back between two refreshes
                job.connection = devnum if devnum is not None else job.connection
                wanted[key] = job
            gone = [key for key in self.jobs if key not in wanted]
            self.jobs = wanted
            self.layer, self.sources, self.usb = layer, sources, usb
            self.os_records = tree.os_statuses(layer, usb.history, now, {key[1] for key in wanted}) + absent
        for key in gone:  # its device left: what it last said has no data from now on
            self.history.end(("job", *key), now)
        self.history.sync("os", self.os_records, now)
        changed = []
        for status in self.os_records:  # WHY only on change: OS checks are recomputed every refresh, not re-evaluated
            key, signature = (status.path, status.name), (status.level, status.code)
            if self._last_os_level.get(key) != signature:
                self._last_os_level[key] = signature
                changed.append(status)
        self.faults.observe(changed, now)
        self._sync_followers()
        self.notify("refresh")

    def _composite_pairs(self, composites: list) -> list[tuple]:
        """``(plugin, device)`` for each composite that names a plugin which can serve it (enabled, a driver, a ``composite:`` match
        for its kind). A composite whose plugin cannot is judged by ``_composition_notes``; its rollup still works."""

        specs = {spec.name: spec for spec in self.specs}
        pairs = []
        for composite in composites:
            spec = specs.get(composite.plugin) if composite.plugin else None
            device = composition_module.device_dict(composite)
            if spec is not None and spec.accepts(device):
                pairs.append((spec, device))
        return pairs

    # --- activity: where an event lands, and what only sysfs knows -------------------------------------------------
    @staticmethod
    def _placed(event: Event, layer: tree.OsLayer) -> Event:
        """An activity event on its tree row: a USB port's, a NIC's (``device_id``), a PCI function's (AER ``device``)."""

        args = event.args if isinstance(event.args, dict) else {}
        if args.get("port") and str(args["port"]) in layer.port_paths:
            return dataclasses.replace(event, path=layer.port_paths[str(args["port"])])
        if args.get("device_id") and str(args["device_id"]) in layer.device_paths:
            return dataclasses.replace(event, path=layer.device_paths[str(args["device_id"])])
        if event.code == "PcieAerError" and args.get("device"):
            found = next((path for device_id, path in layer.device_paths.items() if device_id.endswith(str(args["device"]))), None)
            if found:
                return dataclasses.replace(event, path=found)
        return event

    @staticmethod
    def _downgrades(events: list[Event], layer: tree.OsLayer) -> list[Event]:
        """A ``LinkSpeed`` of the enumeration a USB 3 device (sysfs ``version`` >= 3) is on now, below 5 Gb/s, is a
        ``LinkDowngraded`` WARN: the D435 on 1-1 at 480 Mb/s (2026-10-23), a USB 2 cable or hub in the way."""

        latest = {}
        for number, event in enumerate(events):
            if event.code == "LinkSpeed":
                latest[str(event.args.get("port"))] = number
        out = list(events)
        for port, number in latest.items():
            device_id = layer.port_devices.get(port)
            attrs = (layer.devices.get(device_id) or {}).get("attrs") or {} if device_id else {}
            try:
                major = int(str(attrs.get("usb_version") or "0").strip().split(".")[0])
            except ValueError:
                major = 0
            event = out[number]
            speed = event.args.get("speed_mbps") or 0
            if major >= 3 and 0 < speed < activity_module.SUPERSPEED_MBPS:
                out[number] = dataclasses.replace(
                    event, code="LinkDowngraded", severity="warn",
                    message=f"{event.message}, but it is a USB {str(attrs['usb_version']).strip()} device: a USB 2 cable, hub"
                            " or port is in the way", args={**event.args, "usb_version": str(attrs["usb_version"]).strip()})
        return out

    # --- change notification (GET /api/v1/events) -------------------------------------------------------------------
    def notify(self, reason: str) -> None:
        with self.changes:
            self.revision += 1
            self.change_reason = reason
            self.changes.notify_all()

    def wake(self) -> None:
        """Refresh now (a udev event, a new kernel line), not at the next tick."""

        self._wake.set()

    def on_udev(self, block: dict) -> None:
        if self.live.udev(block):
            self.notify("udev")
        self._wake.set()

    # --- expected but absent ------------------------------------------------------------------------------------
    def expected_devices(self) -> dict[str, dict]:
        """``hardware_id -> {name, last_seen, source, owner, expected}``: the devices this host has seen a plugin
        diagnose (``expected`` False: only seen), plus every enabled plugin's describe ``expected`` (record v0.1; the
        plugin's entry wins over the memory), plus the expected setup's devices that have a hardware_id
        (docs/expected-setup.md; the person's file wins over both). A retired device, or one a person chose to Forget,
        is in neither. Only ``expected`` True ones warn when absent (``_absent_rows``)."""

        out = {hardware_id: {"name": str(entry.get("name") or hardware_id), "last_seen": entry.get("last_seen"),
                             "source": "seen by this host", "owner": "io_diagnostics", "expected": False}
               for hardware_id, entry in self.seen.devices.items()}
        for spec in self.specs:
            if not spec.enabled:
                continue
            for item in (self.describes.get(spec.name) or {}).get("expected") or []:
                hardware_id = item.get("hardware_id") if isinstance(item, dict) else None
                if not isinstance(hardware_id, str) or not hardware_id:
                    continue
                remembered = out.get(hardware_id, {})
                out[hardware_id] = {"name": str(item.get("name") or remembered.get("name") or hardware_id),
                                    "last_seen": item.get("last_seen") or remembered.get("last_seen"),
                                    "source": str(item.get("source") or spec.name), "owner": spec.name, "expected": True}
        for rig, device in self.setup.manifest.entries():
            if not device.hardware_id:
                continue  # a serial-only entry is judged on the merged tree (expected.evaluate)
            known = next((key for key in out if expected_module.same_id(key, device.hardware_id)), device.hardware_id)
            remembered = out.pop(known, {})
            out[device.hardware_id] = {"name": device.label(), "last_seen": remembered.get("last_seen"),
                                       "source": f"expected setup: {rig.name}", "owner": remembered.get("owner", "io_diagnostics"),
                                       "expected": True}
        forgotten = set(self.seen.forgotten)
        return {hardware_id: entry for hardware_id, entry in out.items()
                if hardware_id not in forgotten and self.setup.retired_entry(hardware_id, self._usb_serial(hardware_id)) is None}

    @staticmethod
    def _usb_serial(hardware_id: str) -> tuple[str, ...]:
        ids = activity_module.usb_id(hardware_id)
        return (ids[2],) if ids else ()

    def _absent_rows(self, layer: tree.OsLayer, boots: list, now: float) -> list[DiagnosticStatus]:
        """One WARN status on the Computer per expected USB device that is not on USB now, saying in plain words what the
        kernel saw (Zach, 2026-10-24: a missing thing is a diagnostic on its parent, not a ghost row). WHY warn, not
        error: absent is not broken. Devices that were only seen are one OK status, "Recently seen", for ``RECENTLY_SEEN_S``."""

        present = {ids for entity in layer.entities.values() if (ids := activity_module.usb_id(entity.hardware_id))}
        note = str(getattr(getattr(self.os, "kernel_log", None), "note", "") or "")
        unreadable = note if note.startswith("journalctl unavailable") else ""
        statuses, recent = [], []
        for hardware_id, expected in sorted(self.expected_devices().items()):
            ids = activity_module.usb_id(hardware_id)
            if ids is None or ids in present:  # v0.1: only USB ids (usb:<vid>:<pid>:<serial>) are checked here
                continue
            if not expected["expected"]:
                last = expected["last_seen"]
                if isinstance(last, (int, float)) and not isinstance(last, bool) and now - float(last) <= RECENTLY_SEEN_S:
                    recent.append((hardware_id, expected, activity_module.kernel_evidence(boots, hardware_id, now, self.utc_offset_s, unreadable)))
                continue
            evidence = activity_module.kernel_evidence(boots, hardware_id, now, self.utc_offset_s, unreadable)
            seen = _seen_text(expected["last_seen"], self.utc_offset_s)
            who = str(expected["source"]).removeprefix("expected setup: ")
            message = f"{expected['name']} expected ({who}), not on USB" + (f" · last seen {seen}" if seen else "") + f" · {evidence['text']}"
            if evidence["kind"] == "never":
                message += ": try a data cable or another port"
            values = (KeyValue("what_to_check", activity_module.WHAT_TO_CHECK[evidence["kind"]]),
                      KeyValue("kernel_evidence", evidence["kind"]), KeyValue("expected_by", expected["source"]),
                      KeyValue("last_seen", seen or "unknown"), KeyValue("hardware_id", hardware_id))
            statuses.append(DiagnosticStatus(level=WARN, name=f"{expected['name']}: presence", message=message,
                                             hardware_id=hardware_id, values=values, path=tree.HOST_PATH, code="ExpectedAbsent",
                                             stamp=now, received=now, source=EXPECTED_SOURCE))
        if recent:
            lines = [f"{seen['name']} (seen {_seen_text(seen['last_seen'], self.utc_offset_s)}) · {evidence['text']}" for _id, seen, evidence in recent]
            statuses.append(DiagnosticStatus(
                level=OK, name="Recently seen", path=tree.HOST_PATH, stamp=now, received=now, source=EXPECTED_SOURCE,
                message=f"{len(recent)} device{'s' if len(recent) != 1 else ''} seen in the last day, not on USB now, not in the expected setup: "
                        + ", ".join(seen["name"] for _id, seen, _evidence in recent),
                values=(KeyValue("recently_seen", "\n".join(lines)),)))
        return statuses

    # --- plugin runs -----------------------------------------------------------------------------------------------
    def _job_env(self, job: Job | None = None) -> dict[str, str]:
        """What every plugin call gets beside the runner's own variables; for a job's diagnose and act, also
        ``IO_DIAGNOSTICS_EXPECTED`` when the expected setup lists its device (``{"nodes": [...], "rig": "..."}``).
        WHY "" when not listed: the runner copies this process's environment, and a stray value must not reach a
        driver as if the person had said it (an empty value is "no expected setup": the driver uses its own source)."""

        env = dict(self.plugin_env)
        if job is None:
            return env
        with self.lock:
            device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
            entity = self.layer.entities.get(device_path)
            plugin_row = job.cache.entities.get("")
        hardware_id = entity.hardware_id if entity else str((job.device.get("attrs") or {}).get("hardware_id") or "")
        serials = [str(job.device.get("serial") or ""), str(((plugin_row.identity if plugin_row else {}) or {}).get("serial_number") or "")]
        env["IO_DIAGNOSTICS_EXPECTED"] = self.setup.plugin_env(hardware_id, tuple(serial for serial in serials if serial)).get(
            "IO_DIAGNOSTICS_EXPECTED", "")
        if str(job.device.get("bus")) == "composite":
            env.update(self._composite_env(job))
        return env

    def run_job(self, job: Job) -> None:
        """Run one job, waiting first for its ``exclusive`` group (queued, never failed)."""

        with self.gates.hold(job.spec.exclusive):
            self._run_job(job)

    def _run_job(self, job: Job) -> None:
        """Run one job; the caller holds its group (the scheduler takes it before submitting)."""

        if job.spec.kind == "check":
            return self._run_check(job)
        with self.lock:
            entity = self.layer.entities.get(job.device_path)
        timeout_s = self._probe_timeout(job)
        probing = timeout_s is not None and not job.probed  # the connection's first run is its probe, in place of a diagnose
        outcome = run_plugin(job.spec, job.device, job.device_path, self.clock.now, self._job_env(job),
                             hardware_id=entity.hardware_id if entity else "", verb="probe" if probing else "diagnose",
                             timeout_s=timeout_s if probing else None)
        self._absorb(job, outcome)

    def _absorb(self, job: Job, outcome: RunOutcome, follower: Follower | None = None) -> None:
        """One diagnose run, or one follow line, into the job's cache, the fault memory and the history."""

        now = self.clock.now()
        blocked = self._probed(job, outcome) if follower is None and outcome.verb == "probe" else None
        with self.lock:
            fresh_statuses, fresh_events = job.cache.apply(outcome, received=now)
            hardware_id = self.layer.entities.get(job.device_path).hardware_id if job.device_path in self.layer.entities else ""
            # WHY a follower's line is only a touch: it reports some paths, so "not reported" says nothing; only a whole
            # run that really looked (not blocked, not failed) marks what it did not report as not heard
            self.roster.observe(job, hardware_id, outcome if follower is None else None, now)
            previous = job.cache.host_status
            if follower is None:
                job.cache.host_status = self._probe_status(job, outcome, blocked, hardware_id, now) if outcome.verb == "probe" \
                    else host_status(job.spec, outcome, hardware_id, now)
                job.last = outcome
            else:
                status = follow_status(job.spec, follower, hardware_id, now)
                # WHY re-said at most once per period while nothing changes: it is the follower's heartbeat, not news
                same = previous is not None and (previous.level, previous.code) == (status.level, status.code)
                if same and previous.received is not None and now - previous.received < follower.period_s:
                    status = None
                if status is not None:
                    job.cache.host_status = status
            device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
        observed = [rebase(status, device_path) for status in fresh_statuses]
        self._observe_states(job, device_path, now)
        if job.cache.host_status is not previous or follower is None:
            observed.append(dataclasses.replace(job.cache.host_status, path=device_path))
        self.faults.observe(observed, now)
        self.history.observe(observed, now, source=("job", *job.key))
        if observed or fresh_events:
            self.notify("plugin")

    # --- the on-connect probe (docs/probe-on-connect.md) -------------------------------------------------------------
    def _probe_timeout(self, job: Job) -> float | None:
        """The job's plugin's declared probe time limit, or None: it declares none, so connect runs a diagnose."""

        if job.spec.kind != "driver":
            return None
        return probe_module.probe_timeout(self.describes.get(job.spec.name), job.spec.timeout_s)

    def _probed(self, job: Job, outcome: RunOutcome) -> probe_module.Blocked | None:
        """Note how a probe ended and schedule what follows; the blocked state, or None.

        - Blocked (``PortBusy`` / ``BusLockHeld``): probe again when the last holder exits, or on the backoff.
        - Otherwise (also crashed or timed out: the row says so, and a probe that failed is not hammered): this
          connection is probed; the periodic diagnose (or follow) starts ``period_s`` after it.

        WHY the schedule is the job's ``next_due``: probing again is just making the job due, and the scheduler takes the
        ``exclusive`` group's turn as it does for any due job."""

        previous = job.blocked
        found = probe_module.blocked_by(outcome.records)
        job.blocked = found
        if found is None:
            job.probed = True
            follow_s = follow_period(self.describes.get(job.spec.name))
            job.next_due = time.monotonic() + (follow_s if follow_s is not None else job.spec.period_s)
            return None
        found.attempts = previous.attempts + 1 if previous else 1
        found.retry_at = time.monotonic() + probe_module.backoff_s(found.attempts, job.spec.period_s, self.recheck_backoff_s)
        job.next_due = found.retry_at  # a holder's exit makes it sooner still
        if probe_module.holders_gone(found):  # it let go while the probe ran
            self._release(job, found)
        return found

    def _probe_status(self, job: Job, outcome: RunOutcome, blocked: probe_module.Blocked | None, hardware_id: str,
                      now: float) -> DiagnosticStatus:
        """The device row's plugin-run check after a probe: ``probed 15:12:31 · 3.1 s``, or the ProbeBlocked sentence
        (UNKNOWN with the holder's code: nothing was checked, and nothing says it is fine)."""

        status = host_status(job.spec, outcome, hardware_id, now)
        if blocked is not None:
            retry_s = blocked.retry_at - time.monotonic()
            values = (*status.values, KeyValue("blocked_by", ", ".join(f"{holder.program} (pid {holder.pid})"
                                                                         for holder in blocked.holders) or "unknown"))
            return dataclasses.replace(status, level=UNKNOWN, code=blocked.code, values=values,
                                       message=probe_module.blocked_sentence(blocked, retry_s))
        if outcome.outcome == "ok":
            at = activity_module.iso(outcome.started + outcome.duration_s, self.utc_offset_s) or ""
            return dataclasses.replace(status, message=f"probed {at[11:19]} · {outcome.duration_s:.1f} s")
        return status

    def _release(self, job: Job, blocked: probe_module.Blocked) -> None:
        """Every holder is gone: probe again after the settle."""

        blocked.released = time.monotonic()
        job.next_due = min(job.next_due, blocked.released + self.recheck_settle_s)

    def _recheck_loop(self) -> None:
        """About once a second, a ``/proc/<pid>/stat`` read per watched holder (no subprocess): the last one gone makes
        its job due."""

        while not self._stop.wait(self.recheck_poll_s):
            with self.lock:
                jobs = list(self.jobs.values())
            for job in jobs:
                blocked = job.blocked
                if blocked is not None and not job.running and blocked.released is None and probe_module.holders_gone(blocked):
                    self._release(job, blocked)

    def _sync_followers(self) -> None:
        """One follower per job whose plugin's describe declares ``follow`` (started by ``start``, never by ``once``);
        a job that went away (its device left) stops its follower."""

        if self._scheduler is None:
            return
        with self.lock:
            jobs = dict(self.jobs)
        for key in [key for key in self.followers if key not in jobs]:
            self.followers.pop(key).stop()
        for key, job in jobs.items():
            period = follow_period(self.describes.get(job.spec.name)) if job.spec.kind == "driver" else None
            if period is None or key in self.followers or self._awaiting_probe(job):
                continue
            entity = self.layer.entities.get(job.device_path)

            def absorb(outcome: RunOutcome, job: Job = job) -> None:
                self._absorb(job, outcome, self.followers.get(job.key))

            follower = Follower(job.spec, job.device, job.device_path, period, self.clock.now, lambda job=job: self._job_env(job),
                                absorb, hardware_id=entity.hardware_id if entity else "", backoff_s=self.follow_backoff_s)
            self.followers[key] = follower
            follower.start()

    def _awaiting_probe(self, job: Job) -> bool:
        """A followed job whose plugin probes on connect: it is followed only once its probe is done and one follow
        period has passed ("start follow ``period_s`` after the probe"); until then the scheduler runs the probe."""

        return self._probe_timeout(job) is not None and (not job.probed or time.monotonic() < job.next_due)

    def scheduled_jobs(self) -> list[Job]:
        """What the scheduler runs: every job but the followed ones, and the checks. A probed job of a following plugin
        is about to be followed, not diagnosed."""

        def followed(key, job) -> bool:
            return key in self.followers or (job.probed and job.spec.kind == "driver"
                                             and follow_period(self.describes.get(job.spec.name)) is not None)

        return [job for key, job in self.jobs.items() if not followed(key, job)] + list(self.check_jobs.values())

    def _run_check(self, job: Job) -> None:
        """Run one Monitoring Plugins check; its one status lands on the entity it attaches to (``attach``)."""

        status = run_check(job.spec, self.clock.now, self.plugin_env)
        now = self.clock.now()
        with self.lock:
            self.check_results[job.spec.name] = dataclasses.replace(status, received=now)
        attached = next((found for found in self.view().checks if found.name == job.spec.name), None)
        if attached is not None:
            self.faults.observe([attached], now)
            self.history.observe([attached], now, source=("check", job.spec.name))

    def _attach_checks(self, merged: tree.Merged) -> list[DiagnosticStatus]:
        """Each check's latest result on its entity: ``host``, a hardware_id, or ``rig:<name>``. WHY a row of its own
        under the host when the entity is not here: a check whose subject left must still say what it last found."""

        out = []
        for name, status in self.check_results.items():
            job = self.check_jobs.get(name)
            if job is None:
                continue
            attach, path, hardware_id = job.spec.attach, None, ""
            if attach == tree.HOST_PATH:
                path = tree.HOST_PATH
            elif attach.startswith("rig:"):
                rig = next((rig for rig in self.setup.manifest.rigs if rig.name == attach[4:].strip()), None)
                path = expected_module.rig_path(rig) if rig else None
            else:
                path = next((found for found, entity in merged.entities.items() if not entity.part_of and entity.hardware_id
                             and (entity.hardware_id == attach or expected_module.same_id(entity.hardware_id, attach))), None)
                hardware_id = attach
            values = status.values
            if path is None:
                path = f"{tree.HOST_PATH}/check-{tree.slug(name)}"
                values = (*values, KeyValue("attach", f"{attach} (not here now)"))
            out.append(dataclasses.replace(status, path=path, hardware_id=hardware_id or status.hardware_id, values=values))
        return out

    def run_all_once(self) -> None:
        """Refresh, then run every matched (plugin, device) once, in parallel except within an ``exclusive`` group,
        whose jobs run one after another; used by ``once`` and tests."""

        self.refresh()
        jobs = list(self.jobs.values()) + list(self.check_jobs.values())
        threads = [threading.Thread(target=self.run_job, args=(job,), daemon=True) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def describe_all(self) -> None:
        for spec in list(self.specs):
            if spec.enabled and spec.kind == "driver" and self.describes.get(spec.name) is None:
                self._source_stamps[spec.name] = source_stamp(spec)
                self.describes[spec.name] = describe(spec, extra_env=self._job_env())

    # --- a driver updated in place ---------------------------------------------------------------------------------------
    def check_plugin_sources(self) -> list[str]:
        """Ask ``describe`` again of every driver whose source changed since it was last asked, and apply what it now declares.
        Returns the names it re-described. Called about every ``describe_check_s`` seconds by the ``io-reload`` thread.

        WHY (Zach, 2026-10-24: Preview still showed the old moteus and RealSense describe after the new drivers landed): the bam_ws
        plugins are run live from their path, and ``describe`` was read once, at start, so an update in place changed what a
        diagnose printed but never the actions, watch, streams or probe the host offered, until the host restarted.

        What is re-applied (``_apply_describe``): matches (when they came from describe), actions, ``watch``, ``streams``, ``tabs``,
        ``follow``, ``probe`` and ``expected`` all read ``self.describes`` where they are used. Running follows and watches are
        restarted only when their own declaration changed. A describe that fails keeps the old one running and shows an UNKNOWN
        ``<plugin>: describe`` line on the Computer until the next good one."""

        redescribed = []
        for spec in list(self.specs):
            if not (spec.enabled and spec.kind == "driver") or self.describes.get(spec.name) is None or spec.name not in self._source_stamps:
                continue
            stamp = source_stamp(spec)
            if stamp is None or stamp == self._source_stamps[spec.name]:
                continue
            self._source_stamps[spec.name] = stamp  # first: a failing describe is asked again only after the next edit
            redescribed.append(spec.name)
            self._reload_describe(spec)
        return redescribed

    def _reload_describe(self, spec) -> None:
        old = self.describes.get(spec.name) or {}
        answer = describe(spec, timeout_s=min(spec.timeout_s, 30.0), extra_env=self._job_env())
        if answer.get("error"):
            with self.lock:
                self.describe_failed[spec.name] = str(answer["error"])
            self.notify("plugin")
            return
        with self.lock:
            self.describe_failed.pop(spec.name, None)
            self.describes[spec.name] = answer
        changed = sorted(key for key in set(old) | set(answer) if old.get(key) != answer.get(key))
        self._apply_describe(spec, old, answer)
        self._plugin_event(spec, "PluginReloaded", f"{spec.name}: its code changed; describe was read again"
                           + (f" (changed: {', '.join(changed)})" if changed else " (nothing it declares changed)"), changed=changed)

    def _apply_describe(self, spec, old: dict, new: dict) -> None:
        from io_diagnostics.plugins import PluginConfigError, parse_match

        if spec.name in self._adopted and new.get("matches"):
            try:
                matches = tuple(parse_match(item) for item in new["matches"])
            except PluginConfigError as error:
                self.plugin_problems.append(f"{spec.name}: describe {error}")
                matches = ()
            if matches and matches != spec.matches:
                self.specs = [dataclasses.replace(other, matches=matches) if other.name == spec.name else other for other in self.specs]
                self.wake()  # the next refresh matches devices again
        if follow_period(old) != follow_period(new):
            for key in [key for key in self.followers if key[0] == spec.name]:
                self.followers.pop(key).stop()
            self._sync_followers()
        if old.get("watch") != new.get("watch"):
            self._redeclare_watches(spec)
        if probe_module.probe_timeout(old, spec.timeout_s) != probe_module.probe_timeout(new, spec.timeout_s) and new.get("probe"):
            with self.lock:
                for job in self.jobs.values():
                    if job.spec.name == spec.name:  # a followed job is not probed again (scheduled_jobs): its follow keeps running
                        job.probed, job.blocked, job.next_due = False, None, 0.0

    def _redeclare_watches(self, spec) -> None:
        """A driver's ``watch`` declaration changed: each running watch of it takes the new period and gates (its process restarts
        when the declaration it started with differs); a plugin that no longer declares one has its watches stopped."""

        with self._watch_lock:
            current = [watch for watch in self.watches.values() if watch.job.spec.name == spec.name and not watch.stopped]
            for watch in current:
                declared = self._watch_declared(watch.job)
                if declared is None or "refused" in declared:
                    self._watch_stop(watch)
                elif declared != watch.declared:
                    watch.declared = declared
                    if watch.follower is not None:
                        watch.follower.stop()
                        watch.follower = None
        for watch in current:
            if not watch.stopped:
                self._watch_step(watch)

    def _plugin_event(self, spec, code: str, message: str, **args) -> None:
        with self.lock:
            self._plugin_event_seq += 1
            self.plugin_events.append(Event(code, self.clock.now(), "info", message, path=tree.HOST_PATH, args=args,
                                            seq=self._plugin_event_seq, source=spec.name))
            del self.plugin_events[:-MAX_PLUGIN_EVENTS]
        self.notify("plugin")

    def _reload_loop(self) -> None:
        while not self._stop.wait(self.describe_check_s):
            try:
                self.check_plugin_sources()
            except Exception as error:  # noqa: BLE001 - a bad read must not stop the watcher of the sources
                self.sources = [*self.sources, {"name": "plugin sources", "state": "skipped", "note": f"{type(error).__name__}: {error}"}]

    def _describe_notes(self, now: float) -> list[DiagnosticStatus]:
        """One UNKNOWN line per driver whose changed code could not be described again: nothing says the old declaration is
        wrong, so it keeps running, and nothing says the new code is fine either."""

        return [DiagnosticStatus(
            level=UNKNOWN, name=f"{name}: describe", code="DescribeFailed", path=tree.HOST_PATH, stamp=now, received=now, source=HOST_SOURCE,
            message=f"{name}'s code changed but describe failed ({why}); the previous declaration is still in use",
            values=(KeyValue("what_to_check", "1. Run the plugin's describe by hand and read its error; the host asks again at the next edit"),))
            for name, why in list(self.describe_failed.items())]

    # --- background service ------------------------------------------------------------------------------------
    def prepare_service(self) -> None:
        """Before a service's first request (cli.py serves before ``start`` has run): a read waits for the first view, it does not build one on nothing."""

        self._serving = True

    def start(self, *, udev: bool = True) -> None:
        """The order is what makes a restart invisible (Zach, 2026-11-04): what the last process knew first, from disk; the tree and a first view at once; and
        everything slow (the drivers' describe, their probes) behind it, on threads a read never waits for."""

        self._serving = True
        kernel_log = getattr(self.os, "kernel_log", None)
        if hasattr(kernel_log, "on_lines"):
            kernel_log.on_lines = self.wake  # a new kernel line refreshes at once
        self._scheduler = Scheduler(self.scheduled_jobs, self._run_job, gates=self.gates)
        if self._restore_later:
            self.eventlog.load()
        self._restore_history()
        self._restore_describes()
        self.refresh()  # after the scheduler exists: the first refresh starts the followers, so they never run a diagnose
        self.view()  # the first published view: from here on every read answers
        threading.Thread(target=self._view_loop, name="io-view", daemon=True).start()
        threading.Thread(target=self._start_drivers, name="io-start", daemon=True).start()
        threading.Thread(target=self._refresh_loop, name="io-refresh", daemon=True).start()
        threading.Thread(target=self._recheck_loop, name="io-recheck", daemon=True).start()
        threading.Thread(target=self._reload_loop, name="io-reload", daemon=True).start()
        threading.Thread(target=self._stock_diagnostics_loop, name="io-stock-diagnostics", daemon=True).start()
        threading.Thread(target=self._history_loop, name="io-history", daemon=True).start()
        # WHY the OS source decides: only a live machine has a udev to listen to (a fixture or a fake machine has none)
        command = getattr(self.os, "udev_command", None)
        if udev and command:
            self._udev = activity_module.UdevWatcher(self.os.invalidate, self.on_udev, command)
            self._udev.start()

    def _start_drivers(self) -> None:
        """What a service does behind its first view: describe every driver (seconds under load, each bounded by its timeout), and only then run them, because
        a job's first run depends on what its plugin declares: a probe or a diagnose, a follower or neither."""

        known = {spec.name: self.describes.get(spec.name) for spec in self.specs}
        self.adopt_described_matches()
        self.refresh()
        if self._stop.is_set():
            return
        self._scheduler.start()
        self.drivers_started.set()
        self.describe_all()
        for spec in list(self.specs):
            if known.get(spec.name) is not None and spec.enabled and spec.kind == "driver":
                self._recheck_describe(spec)  # asked of the same code at the last start: a file it reads may have changed since
        self._save_describes()

    def _recheck_describe(self, spec) -> None:
        old = self.describes.get(spec.name) or {}
        answer = describe(spec, timeout_s=min(spec.timeout_s, 30.0), extra_env=self._job_env())
        if answer.get("error") or answer == old or self._stop.is_set():
            return
        with self.lock:
            self.describes[spec.name] = answer
        self._apply_describe(spec, old, answer)
        self.notify("plugin")

    # --- the published view (Host.read_view) ----------------------------------------------------------------------------
    def _view_loop(self) -> None:
        """The one place a service builds its view: on each change, a quarter of a second apart at most, and a bounded share of a core whatever the load."""

        seen = self._published_revision
        while not self._stop.is_set():
            if time.monotonic() - self._read_at > VIEW_QUIET_S:
                self._view_wanted.wait(VIEW_MAX_AGE_S)  # WHY: nobody is looking, so a change can wait; a service with no window spends no more than it has to
            else:
                with self.changes:
                    self.changes.wait_for(lambda: self.revision != seen or self._stop.is_set(), timeout=VIEW_MAX_AGE_S)
            self._view_wanted.clear()
            if self._stop.is_set():
                return
            began = time.monotonic()
            try:
                seen = self.view().revision
            except Exception as error:  # noqa: BLE001 - a bad build must not end the view: the last one stands, and the reason shows in sources
                self.sources = [*self.sources, {"name": "view", "state": "skipped", "note": f"{type(error).__name__}: {error}"}]
            took = time.monotonic() - began
            self._stop.wait(VIEW_GAP_S)
            # WHY a stream may cut the rest short: a plug shows within a second (docs/activity-log-v1.md), and streams only ask on a change
            self._view_wanted.wait(max(0.0, VIEW_REST * took - VIEW_GAP_S))

    def reads_published(self) -> None:
        """This thread answers people (a request, the live feed, the tray, the beat): from here on its reads never build."""

        self._readers.published = True

    def read_view(self) -> View:
        """What a read answers from: on a thread that ``reads_published``, the view last published, at once. WHY (measured 2026-11-04): every GET built the whole
        view for itself, behind the host's lock, and a page makes several a second; "never blocked behind a recompute or a slow beat". A published view is a
        moment old (``VIEW_GAP_S`` behind a change) and a write answers only once its effect is published (``after_write``). Any other caller, a host built by
        hand or a script asking in-process, builds what it reads, exact to the moment."""

        if not self._serving or not getattr(self._readers, "published", False):
            return self.view()
        if time.monotonic() - self._read_at > VIEW_QUIET_S:
            self._view_wanted.set()  # WHY: the first reader after a quiet spell ends it, or the builder sleeps on past what it came to see
        self._read_at = time.monotonic()
        view = self._published
        if view is None:  # the host is still reading what it knows from disk
            with self.published:
                self.published.wait_for(lambda: self._published is not None or self._stop.is_set(), timeout=FIRST_VIEW_WAIT_S)
            view = self._published
        return view if view is not None else self.view()

    def wait_view(self, revision: int, timeout_s: float = VIEW_WAIT_S) -> None:
        """For a stream, on its own thread: until the view of change ``revision`` is published. WHY: a stream woken by a change would otherwise read the view
        from before it, and say nothing until the next one."""

        if self._serving:
            self._read_at = time.monotonic()  # WHY: an open stream is someone looking, though it reads nothing until a change comes
            self._view_wanted.set()
            with self.published:
                self.published.wait_for(lambda: self._published_revision >= revision or self._stop.is_set(), timeout=timeout_s)

    def after_write(self) -> None:
        """A person's write: what they did is in the next read. A POST can wait for a build; a GET never does."""

        if self._serving and self._published is not None:
            self.view()

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(FAST_REFRESH_S if self.pending else REFRESH_S)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.refresh()
                self.faults.tick(self.clock.now())
            except Exception as error:  # noqa: BLE001 - a bad read must not stop the refresher; it shows in sources
                self.sources = [*self.sources, {"name": "refresh", "state": "skipped", "note": f"{type(error).__name__}: {error}"}]

    # --- robotdx.diagnostics: the stock diagnostics, on a slow beat over the State history ----------------------------------
    def _stock_diagnostics_loop(self) -> None:
        # WHY a thread of its own on a slow beat (5 s): the library evaluates windows of history, never the 10 Hz watch path
        self.reads_published()  # WHY: a beat reads the view; it is no reason to build one (Zach's brief, 2026-11-04: "a slow beat")
        while not self._stop.wait(self.stock_diagnostics.beat_s):
            try:
                self.run_stock_diagnostics()
            except Exception as error:  # noqa: BLE001 - a failing beat must not stop the host; it shows in sources
                self.sources = [*self.sources, {"name": "stock diagnostics", "state": "skipped", "note": f"{type(error).__name__}: {error}"}]

    def _state_history(self, path: str, now: float, span_s: float) -> dict[str, list[dict]]:
        """The entity's State groups over the last ``span_s``: ``{group name: History.series}``."""

        return {key[1]: self.history.series(key, now - span_s, now) for key in self.history.keys(path, health=False)
                if key[0] == path and self.history.is_state(key)}

    def _stock_inputs(self, path: str, entity, view: View, groups: dict, kinds: set[str], span_s: float) -> stock_diagnostics_module.Inputs:
        """What the entity at ``path`` publishes beside its State history (stock_diagnostics.Inputs), only the parts ``kinds`` read. A
        composite's events, statuses and operating time are its leaves' (and its child composites'). Nothing is decoded here."""

        composition = view.composition
        composite = composition.by_path.get(path) if composition else None
        leaves: list[str] = []
        members: list[tuple[str, str, str | None, str]] = []
        if composite is not None:
            def below(composite_id: str) -> list[str]:
                out = []
                for child in composition.resolved.get(composite_id, []):
                    if child.path and child.present:
                        out.append(child.path)
                    if child.kind == "composite":
                        out += below(child.ref)
                return out

            leaves = list(dict.fromkeys(below(composite.id)))
            for child in composition.resolved.get(composite.id, []):
                if not child.present or not child.path:
                    continue
                summary = view.rows.summaries.get(child.path)
                level = composition.levels.get(child.ref) if child.kind == "composite" else (summary.level if summary else None)
                members.append((child.path, child.name, level, summary.message if summary else ""))
        subtree = {path, *leaves}
        subtree |= {other for other in view.merged.entities for top in tuple(subtree) if other.startswith(top + "/")}
        target = self._watch_target(path)
        arrivals, frames, configs, image = {}, {}, [], None
        if target is not None:
            job, rel = target
            with self.lock:
                arrivals = {group: list(ring) for (where, group), ring in job.cache.arrivals.items() if where == rel}
                frames = {stream: list(ring) for (where, stream), ring in job.cache.frame_log.items() if where == rel}
                configs = [config for (where, _key), config in job.cache.configs.items() if where == rel]
            folder = (self._frames_base() / re.sub(r"[^A-Za-z0-9._-]+", "-", f"{job.key[0]}-{job.key[1]}-{rel or 'device'}")).resolve()

            def image(frame, folder=folder):
                found = (folder / frame.file).resolve() if frame.file else None
                return found if found is not None and found.parent == folder else None
        statuses, events, operating = [], [], []
        if kinds & {"reply_counts", "evidence"}:
            statuses = [status for status in view.merged.statuses if status.path in subtree and status.source != stock_diagnostics_module.SOURCE]
        if kinds & {"faults", "link_events", "evidence"}:
            # WHY without the stock diagnostics' own faults: a failing loop rate confirmed as a fault would count toward the MTTF that
            # reads it, and an MTTF that fails would count itself on the next beat
            own = {(fault.path, fault.code) for fault in self.faults.all()
                   if (fault.snapshot or {}).get("source") == stock_diagnostics_module.SOURCE}
            events = [event for event in view.events if event.path in subtree
                      and not (event.code.startswith("Fault") and (event.path, (event.args or {}).get("fault")) in own)]
        if "faults" in kinds:
            # WHY the health history: operating time is when the entity (a composite: its leaves) was reporting a level, never stale
            spans = []
            for where in subtree:
                for key in self.history.keys(where, health=True):
                    spans += [(piece["from"], piece["to"]) for piece in self.history.timeline(key, view.now - span_s, view.now)
                              if piece["level"] not in (None, STALE)]
            operating = spans
        return stock_diagnostics_module.Inputs(
            has_state=bool(groups), composite=entity.kind == "composite", plugin=composite is None or bool(composite.plugin),
            state_arrivals=arrivals, frames=frames, image=image, statuses=statuses, configs=configs, events=events, members=members,
            leaves=leaves, operating=operating)

    def run_stock_diagnostics(self) -> list[DiagnosticStatus]:
        """One beat: every binding of diagnostics.toml on every entity it matches, over that entity's State history; each result a
        status on the entity (source ``robotdx.diagnostics``), into the history, the fault memory and every view."""

        runner = self.stock_diagnostics
        runner.reload_if_changed()
        if not runner.bindings and not self.stock_results and not self.stock_quiet:
            return []  # nothing configured (a replay without the person's file): no view, no tick
        view = self.read_view()
        now = view.now
        found: dict[tuple[str, str], DiagnosticStatus] = {}
        for path, entity in view.merged.entities.items():
            model_id, composite_kind = _diagnostics_match(entity)
            bindings = runner.bindings_for(path, model_id, composite_kind=composite_kind)
            if not bindings:
                continue
            # WHY two windows and a beat: stream.windows steps from the slice's first sample, so the newest whole window always fits
            span = 2 * max(binding.window_s for binding in bindings) + runner.beat_s
            groups = self._state_history(path, now, span)
            kinds = {stock_diagnostics_module.source_of(binding).kind for binding in bindings}
            inputs = self._stock_inputs(path, entity, view, groups, kinds, span)
            for binding in bindings:
                status = runner.evaluate(binding, path, entity.name or path, groups, now, entity.hardware_id, inputs=inputs)
                found[(status.path, status.name)] = status
        def seen(results) -> set:
            return {(status.path, status.name, status.level, status.code, status.message,
                     tuple((measurement.key, measurement.value) for measurement in status.measurements)) for status in results.values()}

        # WHY a board with no State history posts no row (Zach, 2026-10-28, calm UI): an unwatched moteus keeps no State, so every
        # board read a grey 'unknown' forever. Its reason stays in stock_quiet for the drill-in API; it is never shown as OK.
        quiet = {key: status for key, status in found.items() if stock_diagnostics_module.quiet(status)}
        posted = {key: status for key, status in found.items() if key not in quiet}
        with self.lock:
            changed = seen(found) != seen({**self.stock_results, **self.stock_quiet})
            self.stock_results, self.stock_quiet = posted, quiet
        statuses = list(posted.values())
        self.history.sync(stock_diagnostics_module.SOURCE, statuses, now)
        self.faults.observe(statuses, now)
        if changed:  # WHY only on a change: a page's tick is for what it would draw differently, not for every beat
            self.notify("stock diagnostics")
        return list(found.values())

    def _keep_log(self) -> None:
        """Write what the host now knows to the event log. WHY here, not only in a view: a service nobody is looking at
        (a tray with no window) must still keep its events, and a view is where they are all gathered."""

        if self.eventlog.directory is not None:
            self.view()

    def stop(self) -> None:
        try:
            self._keep_log()
            self.save_history()
        except Exception:  # noqa: BLE001 - a failing flush never keeps the host from stopping
            pass
        self._stop.set()
        self._wake.set()
        self._view_wanted.set()
        with self.changes:
            self.changes.notify_all()
        with self.published:
            self.published.notify_all()
        if self._scheduler:
            self._scheduler.stop()
        if self._udev:
            self._udev.stop()
        for follower in list(self.followers.values()):
            follower.stop()
        self.followers.clear()
        for watch in list(self.watches.values()):
            self._watch_stop(watch)
        if self._frames_owned and self._frames_dir is not None:
            shutil.rmtree(self._frames_dir, ignore_errors=True)
        self.runs.shutdown()
        if hasattr(self.os, "close"):
            self.os.close()
        self.notify("stop")

    # --- views -------------------------------------------------------------------------------------------------
    def view(self) -> View:
        """Build the view now, publish it and return it. What a write acts on; a read calls ``read_view``."""

        with self._view_lock:
            revision = self.revision
            view = self._build_view()
            view.revision = revision
            with self.published:
                self._published, self._published_revision = view, revision
                self.published.notify_all()
        return view

    def _build_view(self) -> View:
        with self.lock:
            now = self.clock.now()
            frozen = {key: _frozen(job) for key, job in self.jobs.items()}
            jobs = list(frozen.values())
            layer = self.layer
            merged = tree.merge(layer, jobs, self.os_records)
            # WHY judged on the merged tree, every view: a rig counts boards and matches cameras by the serial their
            # DRIVER reports (the RealSense SDK serial), which only the plugins' records carry
            setup = expected_module.evaluate(self.setup, merged, layer, now, RUNNER_SOURCE)
            self.roster.keep_rigs()
            notes = (self._host_notes(merged, now) + self._links_summary(merged, now) + self._console_notes(merged, now)
                     + roster_module.stopped_answering(merged, now) + self._describe_notes(now) + self._composition_notes(now))
            # WHY here: the rig rows and the host's own lines are judged per view, never stored; their changes are
            # what the history keeps
            self.history.sync("view", setup.statuses + notes, now)
            checks = self._attach_checks(merged)
            # the stock diagnostics' latest statuses, on the entities that are still here (stock_diagnostics.py)
            stock = [status for status in self.stock_results.values()
                     if status.path in merged.entities and status.code != stock_diagnostics_module.NO_STATE]
            if setup.entities or setup.statuses or notes or checks or stock:
                layer = self._layer_with(setup.entities)
                merged = tree.merge(layer, jobs, self.os_records + setup.statuses + notes + checks + stock)
            # WHY a first summary: a composite's rollup is its children's levels, which only the summarized tree knows. The rollups
            # join the statuses, and the tree is summarized again with them (a composite's own level is the worse of its plugin's
            # and the rollup's); they are judged per view and never stored, so a composite cannot go stale
            composed = None
            if self._composites:
                composed = composition_module.evaluate(self._composites, merged, layer, tree.build_rows(merged, now), now)
                merged.statuses.extend(composed.statuses)
            self.history.sync("composition", composed.statuses if composed else [], now)
            self._composition = composed
            rows = tree.build_rows(merged, now)
            memory = self.roster.pending([(roster_module.memory_key(job.spec.name, self._device_hardware_id(job)),
                                           self.layer.device_paths.get(str(job.device.get("id")), job.device_path)) for job in jobs])
            streaming = self._live_paths(jobs)
            claimed = frozenset(setup.by_path) | frozenset(setup.retired)
            active_runs = frozenset(run.id for run in self.runs.active())  # before the events: a run that ends now has its ending in them
            live = [self._placed(event, self.layer) for event in self.live.events()]
            events, self.pending = activity_module.merge(self.usb.events, self.usb.consumed, live)
            events += self.faults.events
            events += self.runs.events()  # RunStarted / RunEnded (docs/runs-v1.md section 5)
            events += self.config_editor.events  # every configuration write, accepted or not (config_edit.py)
            events += self.plugin_events  # PluginReloaded: a driver's code changed and describe was read again
            events += activity_module.level_events(self.history.transitions(now))
            for job in self.jobs.values():
                device_path = self.layer.device_paths.get(str(job.device.get("id")))
                if device_path:
                    # WHY the plugin as source when it names none: the driver's own Found / Probed / ProbeBlocked go to the
                    # log as they are (docs/probe-on-connect.md); the host adds no event about a device itself
                    events += [dataclasses.replace(event, path=absolute(event.path, device_path), source=event.source or job.spec.name)
                               for event in job.cache.events.values()]
        events = [event for event in events if event.stamp is None or event.stamp <= now]
        # WHY the log between what the host derives and what a page reads: the derived events live in RAM (the fault
        # memory, the level history, the runs) or in a journal that ends with the boot, so the log keeps each one when
        # it is final and gives back the ones this process no longer produces (docs/activity-log-v1.md decision 4)
        self.eventlog.record(events, now)
        events = [event for event in self.eventlog.overlay(events, now) if event.stamp is None or event.stamp <= now]
        return View(now, layer, merged, rows, events, self.faults.open_by_path(), setup, checks, memory, streaming, claimed, composed,
                    self.history.rates(now), frozen, active_runs=active_runs)

    def _device_hardware_id(self, job: Job) -> str:
        entity = self.layer.entities.get(self.layer.device_paths.get(str(job.device.get("id")), job.device_path))
        return entity.hardware_id if entity else ""

    def _live_paths(self, jobs: list[Job]) -> frozenset:
        """The device row and everything its plugin reported, for a job whose follower is running and heard from lately:
        data is arriving now (the presence dot pulses)."""

        out = set()
        for key, follower in self.followers.items():
            process = follower.process
            if process is None or process.poll() is not None or follower.last_line is None:
                continue
            if time.monotonic() - follower.last_line > max(3 * follower.period_s, STALE_FLOOR_S):
                continue
            job = self.jobs.get(key)
            if job is None:
                continue
            base = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
            out.add(base)
            out.update(absolute(rel, base) for rel in job.cache.entities if rel)
        return frozenset(out)
 
 

    def _layer_with(self, extra: list[tuple[Entity, str]]) -> tree.OsLayer:
        """The OS layer plus the expected setup's rows: rig rows first under the host (the at-a-glance answer), the
        serial-only absent rows under "Expected, not connected"."""

        entities, icons = {}, dict(self.layer.icons)
        for path, entity in self.layer.entities.items():
            entities[path] = entity
            if path == tree.HOST_PATH:
                for row, icon in extra:
                    if row.kind == expected_module.RIG_KIND:
                        entities[row.path], icons[row.path] = row, icon
        for row, icon in extra:
            if row.kind == expected_module.RIG_KIND:
                continue
            entities[row.path], icons[row.path] = row, icon
        return dataclasses.replace(self.layer, entities=entities, icons=icons)

    def _host_notes(self, merged: tree.Merged, now: float) -> list[DiagnosticStatus]:
        """A device whose frames never reached the wire while another adapter of the same plugin on this PC has boards
        answering: name that one, so one swap tells adapter from bus (28B3A25D vs 272813EF, 2026-10-22)."""

        own = expected_module.latest_statuses(merged.statuses)
        devices: dict[str, list[str]] = {}  # plugin -> its device rows, in job order
        for job in self.jobs.values():
            path = self.layer.device_paths.get(str(job.device.get("id")))
            if path in merged.entities:
                devices.setdefault(job.spec.name, []).append(path)
        notes = []
        for path, statuses in own.items():
            dead = next((status for status in statuses if status.code == FRAMES_NOT_SENT and effective_level(status, now) != STALE), None)
            if dead is None:
                continue
            device = tree.nearest_device(path, merged, self.layer)
            plugin = next((name for name, paths in devices.items() if device in paths), None)
            healthy = [other for other in devices.get(plugin, []) if other != device
                       and expected_module.answering_nodes(other, merged, own, now, RUNNER_SOURCE)]
            if not device or not healthy:
                continue
            other = merged.entities[healthy[0]].name
            notes.append(DiagnosticStatus(
                level=WARN, name=f"{merged.entities[device].name}: another adapter", code="OtherAdapterWorks", path=path,
                message=f"{other} works on this PC: plug this bus into it to tell adapter from bus",
                hardware_id=merged.entities[device].hardware_id, stamp=now, received=now, source=HOST_SOURCE,
                values=(KeyValue("what_to_check", f"1. Plug this bus into {other} and run Check communication there: if its"
                                                  " boards answer, this adapter is dead; if they do not, the bus or a board"
                                                  " holds it"),
                        KeyValue("working_adapter", other))))
        return notes

    def _run_ended(self, run) -> None:
        self._on_run_end(run)
        self.after_write()  # WHY: as a write's: what a person started has ended, and the next read says so

    def _on_run_end(self, run) -> None:
        """A finished ``open_console`` run that says it launched a process (``ActionCompleted`` ``args.pid``): remember it."""

        if run.spec.action == usb_reset.NAME and run.spec.plugin == host_utilities.OWNER:
            self._after_reset(run)
            return
        if run.spec.action != "open_console" or run.state != "done":
            return
        for record in (run.result or {}).get("records") or []:
            pid = (record.get("args") or {}).get("pid") if isinstance(record, dict) else None
            if record.get("code") == "ActionCompleted" and isinstance(pid, int) and pid > 1:
                found = re.search(r"\(([a-z][a-z0-9 -]*)\)\s*$", run.spec.title)
                with self.lock:
                    self.consoles[run.spec.device_id] = {"pid": pid, "program": found[1] if found else "console",
                                                         "device_path": run.spec.device_path, "since": self.clock.now()}
                self.wake()
                return

    def _after_reset(self, run) -> None:
        """A port was reset (or tried): every driver job whose device is in it is probed again, as for a new connection
        (docs/probe-on-connect.md), whether the kernel gave it a new device number or reset it in place; and the OS side is read
        again at once, so the plug / unplug / reset the kernel logged shows in Activity within a second, not at the next poll.
        WHY even when the reset failed (exit 1) or was stopped: the device may have dropped without coming back, and the next
        probe is what says so. Not for a refusal (exit 2: a gate said no, nothing happened to the device)."""

        if run.exit_code is None or run.exit_code == usb_reset.REFUSED:
            return
        port = usb_reset.port_of({"id": run.spec.device_id})
        with self.lock:
            ids = set(host_utilities.subtree_device_ids(port, self.layer)) if port else {run.spec.device_id}
            for job in self.jobs.values():
                if str(job.device.get("id")) in ids:
                    job.probed, job.blocked, job.next_due = False, None, 0.0
        self.os.invalidate()
        self.wake()

    def _console_notes(self, merged: tree.Merged, now: float) -> list[DiagnosticStatus]:
        """While a console RobotDx launched is open, the device's row says so, instead of leaving a bare "port held"
        (Zach, 2026-10-24). The plugin's own probe / diagnose keep reporting PortBusy; the host's probe-again on holder
        exit (probe.py) brings the boards back when the console closes."""

        notes = []
        for device_id, info in list(self.consoles.items()):
            if not _pid_alive(info["pid"]):
                self.consoles.pop(device_id, None)
                continue
            path = self.layer.device_paths.get(device_id, info["device_path"])
            entity = merged.entities.get(path)
            if entity is None:
                continue
            notes.append(DiagnosticStatus(
                level=UNKNOWN, name=f"{entity.name}: console", code="ConsoleOpen", path=path, hardware_id=entity.hardware_id,
                message=f"{info['program']} open (launched from RobotDx, pid {info['pid']})", stamp=now, received=now,
                source=HOST_SOURCE, values=(KeyValue("what_to_check", f"1. Close {info['program']} when you are done: RobotDx "
                                                     "checks the boards again by itself once it is closed"),
                                            KeyValue("pid", str(info["pid"])))))
        return notes

    def _links_summary(self, merged: tree.Merged, now: float) -> list[DiagnosticStatus]:
        """The Computer's one "USB/PCIe links" line (Zach, 2026-10-23: the computer diagnostics belong to its entity):
        how many USB ports and PCIe links the OS skeleton measures and which are unhappy (a flapping port, a PCIe link
        narrower than the card or with AER errors, a USB 3 device linked at USB 2 speed). Only while a plugin
        diagnoses the host (the computer plugin), so a host without one stays a pure grouping row.

        WHY OK while every unhappy link already reports on its own row: the line is a roll-up, and a problem counts
        once, where it lives (one dead bus is one cause). It is WARN ``UsbLinkDowngraded`` only for what no row says:
        a USB 3 device (bcdUSB >= 3) linked below 5 Gb/s with no driver's ``Usb2Link`` on its row."""

        if not any(self.layer.device_paths.get(str(job.device.get("id"))) == tree.HOST_PATH for job in self.jobs.values()):
            return []
        own = expected_module.latest_statuses(merged.statuses)
        port_paths = list(self.layer.port_paths.values())
        port_checks = [(path, status) for path in port_paths for status in own.get(path, []) if status.name.endswith(": link")]
        pcie_checks = [(path, status) for path, statuses in own.items() for status in statuses if status.name.endswith(": PCIe link")]
        unhappy: list[tuple[str, str | None]] = []  # (text, the row that reports it; None = no row says it)
        for path, status in port_checks + pcie_checks:
            if status.level != OK:
                unhappy.append((f"{merged.entities[path].name}: {status.message}", path))
        driver_usb2 = set()
        for path, statuses in own.items():
            for status in statuses:
                if status.code == "Usb2Link" and effective_level(status, now) != STALE:
                    driver_usb2.add(tree.nearest_device(path, merged, self.layer))
                    unhappy.append((f"{merged.entities[path].name}: {status.message}", path))
        for device_id, device in self.layer.devices.items():
            attrs, path = device.get("attrs") or {}, self.layer.device_paths[device_id]
            speed = attrs.get("speed_mbps")
            try:
                major = int(str(attrs.get("usb_version") or "0").strip().split(".")[0])
            except ValueError:
                major = 0
            if device.get("bus") != "usb" or major < 3 or not isinstance(speed, (int, float)) or not 0 < speed < 5000 or path in driver_usb2:
                continue
            unhappy.append((f"{merged.entities[path].name} on {attrs.get('port') or '?'}: a USB {str(attrs['usb_version']).strip()}"
                            f" device linked at {tree.speed_text(speed)}", None))
        unreported = [text for text, row in unhappy if row is None]
        message = f"{len(port_paths)} USB port{'s' if len(port_paths) != 1 else ''} · {len(pcie_checks)} PCIe link{'s' if len(pcie_checks) != 1 else ''}"
        if not unhappy:
            message += " · all OK"
        else:
            message += f" · {len(unhappy)} unhappy: " + "; ".join(text for text, _row in unhappy)
            if not unreported:
                message += " (each counted on its own row)"
        values = [KeyValue("unhappy", "\n".join(text for text, _row in unhappy) or "none")]
        if unreported:
            values.append(KeyValue("what_to_check", "1. Plug it straight into a USB 3 port on the computer (not through a USB 2 hub)\n"
                                                    "2. Try a USB 3 data cable: a USB 2 cable links at 480 Mb/s"))
        name = merged.entities[tree.HOST_PATH].name
        # WHY caused_by only when every unhappy link is a row's own report: the line then says what those rows say
        # (record v0.1); one it alone reports makes it a cause of its own
        caused_by = tuple(dict.fromkeys(row for _text, row in unhappy)) if unhappy and not unreported else ()
        return [DiagnosticStatus(level=WARN if unreported else OK, name=f"{name}: USB/PCIe links", message=message,
                                 code="UsbLinkDowngraded" if unreported else "", path=tree.HOST_PATH, stamp=now, received=now,
                                 source=HOST_SOURCE, values=tuple(values), caused_by=caused_by,
                                 measurements=(Measurement("usb ports", len(port_paths), "1"),
                                               Measurement("pcie links", len(pcie_checks), "1"),
                                               Measurement("unhappy links", len(unhappy), "1")))]

    def clock_json(self, now: float) -> dict:
        when = activity_module.iso(now, self.utc_offset_s)
        offset = self.utc_offset_s if self.utc_offset_s is not None else local_offset_s()
        return {"now": now, "iso": when, "time": when[11:19] if when else "", "mode": self.clock.mode, "utc_offset_s": offset,
                "rank": sorted(RANK, key=RANK.__getitem__), "stale_factor": STALE_FACTOR, "stale_floor_s": STALE_FLOOR_S}

    def _names(self, view: View) -> dict[str, str]:
        names = view.memo.get("names")
        if names is None:
            names = view.memo["names"] = {path: entity.name for path, entity in view.merged.entities.items()}
        return names

    def summary_json(self, view: View) -> dict:
        root = view.rows.summaries[tree.HOST_PATH]
        attention = []
        for path in view.rows.causes:
            entity, summary = view.merged.entities[path], view.rows.summaries[path]
            device = tree.nearest_device(path, view.merged)
            attention.append({"path": path, "name": entity.name, "kind": entity.kind, "icon": view.merged.icons.get(path, "device"),
                              "level": summary.own, "message": summary.message,
                              "device": {"path": device, "name": view.merged.entities[device].name} if device else None})
        # WHY the rollup: with the computer plugin the host self-reports, and its row's level is then ITS report; the
        # chip's question stays "the worst independent cause anywhere" (equal to root.level for a pure grouping host)
        return {"level": root.rollup.level, "counts": dict(root.rollup.counts), "attention": attention,
                "entities": len(view.merged.entities), "devices": len(view.layer.physical)}

    def events_json(self, view: View, limit: int, types: tuple[str, ...] | None = None) -> list[dict]:
        names = self._names(view)
        events = view.events if not types else [event for event in view.events if activity_module.event_type(event.code) in types]
        return [activity_module.event_json(event, self.utc_offset_s, names) for event in events[:limit]]

    def state(self) -> dict:
        from io_diagnostics.tray import chip_model

        view = self.read_view()
        made = view.memo.get("state")
        if made is None:
            summary = self.summary_json(view)
            recent = self.events_json(view, RECENT)
            made = view.memo["state"] = {
                "summary": summary, "recent": recent, "chip": chip_model(summary, recent, self.url), "expected_setup": self.setup_summary(view),
                "composition": self.composition_json(view),
                "tree": tree.node_json(tree.HOST_PATH, view.rows, view.layer, view.faults_by_path, nested=True,
                                       memory=view.memory, live=view.live, expected_paths=view.expected_paths,
                                       rates=view.rates)}
        # WHY the clock is now and not the view's: the page's clock and every age it works out run from it
        return {"api": API, "schema": SCHEMA, "record_version": RECORD_VERSION, "clock": self.clock_json(self.clock.now() if self._serving else view.now),
                "fixture": self.fixture, "host": {"name": self.os.hostname, "sources": list(self.sources)}, **made,
                "runs": self.runs.json(RUNS_IN_STATE)}

    def setup_summary(self, view: View) -> dict:
        manifest = self.setup.manifest
        return {"file": str(self.setup.path) if self.setup.path else None, "editable": bool(self.setup.path) and not self.setup.note,
                "note": self.setup.note,
                # `members`: the rows of the rig's devices that are here (the page nests them under the rig row, tree structure p6)
                "rigs": [{"name": rig.name, "path": expected_module.rig_path(rig), "devices": len(rig.devices),
                          "members": [found.path for found in (view.setup.found if view.setup else []) if found.rig is rig and found.path]}
                         for rig in manifest.rigs],
                "retired": [{"name": entry.name, "hardware_id": entry.hardware_id, "serial": entry.serial, "date": entry.date,
                             "reason": entry.reason, "path": next((path for path, retired in (view.setup.retired if view.setup else {}).items()
                                                                   if retired is entry), None)} for entry in manifest.retired]}

    # --- one row's detail ------------------------------------------------------------------------------------------
    def node(self, path: str) -> dict | None:
        view = self.read_view()
        if path not in view.merged.entities:
            return None
        merged, rows = view.merged, view.rows
        ancestors, current = [], merged.parent.get(path)
        while current is not None:
            ancestors.insert(0, {"path": current, "name": merged.entities[current].name, "edge": merged.edge.get(current)})
            current = merged.parent.get(current)
        job_rel = self._watch_target(path, view)
        extra = self._page_json(path, view, job_rel)
        names = self._names(view)
        below = path + "/"
        # WHY lazily, and the names once: the 20 it sends were picked after every event of the log had been serialized, each with the whole name table built
        # again for it: 26,867 x 93 entities, the host page's 3.8 s (2026-11-04)
        events = itertools.islice((event for event in view.events if event.path == path or event.path.startswith(below)), NODE_EVENTS)
        return {"node": tree.node_json(path, rows, view.layer, view.faults_by_path, nested=False, memory=view.memory,
                                       live=view.live, expected_paths=view.expected_paths, rates=view.rates), "ancestors": ancestors,
                "checks": [self.check_json(status, view.now) for status in rows.own.get(path, [])],
                "actions": self.actions_json(path, view), **extra,
                "faults": [self.fault_json(fault, view) for fault in self.faults.all() if fault.path == path],
                "events": [activity_module.event_json(event, self.utc_offset_s, names) for event in events],
                "entity": to_dict(merged.entities[path]), "setup": self.setup_json(path, view),
                "composite": (self.composite_json(view.composition.by_path[path], view.composition)
                              if view.composition is not None and path in view.composition.by_path else None)}

    def _page_json(self, path: str, view: View, target: tuple[Job, str] | None) -> dict:
        """What the entity page adds to a node (docs/state-config-v1.md): the connection badge, when the host last heard from
        it, its Configuration, whether it can be watched, its declared image streams and the driver's extra tabs."""

        out = {"badge": self._badge(path, view), "config": [], "watch": self.watch_json(None, ""), "streams": [], "tabs": [],
               "heard": {"received": None, "age_s": None}, "editable": False, "editing": self.config_editor.status(path)}
        if target is None:
            return out
        job, rel = target
        describe_answer = self.describes.get(job.spec.name)
        cache = job.cache  # WHY no lock: this is the view's own copy
        heard = cache.heard(rel)
        out["config"] = entity_state.config_rows(cache, rel, view.now)
        entity = cache.entities.get(rel)
        out["editable"] = bool(entity and any(action.name == "set_config" for action in entity.actions))
        for row in out["config"]:
            row["edit"] = self.config_editor.edit_of(job, rel, row["key"])
        out["heard"] = {"received": heard, "age_s": round(view.now - heard, 3) if heard is not None else None}
        out["watch"] = self.watch_json(job, rel)
        out["streams"] = entity_state.stream_rows(describe_answer, cache, rel, view.now, lambda r, name: self._fps(job, r, name))
        out["tabs"] = entity_state.declared_tabs(describe_answer, rel)
        return out

    # --- the expected setup: what a row offers, and the edits (host-side file writes; never a device) -----------------
    def _setup_target(self, path: str, view: View) -> tuple[str, dict] | None:
        """``(role, ids)`` for a row the Expected setup card is about: ``rig`` (a rig row), ``device`` (a physical device
        row itself) or ``board`` (a node a plugin reported under one); None for anything else. There is no ``absent``
        row any more (a missing device is a status on the Computer, Zach 2026-10-24)."""

        merged, layer = view.merged, view.layer
        entity = merged.entities.get(path)
        if entity is None:
            return None
        if view.setup and path in view.setup.rigs:
            return "rig", {"rig": view.setup.rigs[path]}
        if layer.device_of_path.get(path) in layer.physical:
            return "device", {"hardware_id": entity.hardware_id, "serials": expected_module.device_serials(entity, layer, path),
                              "name": entity.name}
        board = self._board_of(path, view)
        return ("board", board) if board else None

    def _board_of(self, path: str, view: View) -> dict | None:
        """A node row a plugin reported (a CAN board): its job, its path relative to the device, its ids."""

        merged, layer = view.merged, view.layer
        entity = merged.entities.get(path)
        if entity is None or entity.kind != expected_module.NODE_KIND:
            return None
        device = tree.nearest_device(path, merged, layer)
        job = next((job for job in self.jobs.values()
                    if layer.device_paths.get(str(job.device.get("id")), job.device_path) == device), None) if device else None
        if job is None:
            return None
        return {"job": job, "device": device, "rel": expected_module.relative(path, device), "name": entity.name,
                "hardware_id": layer.entities[device].hardware_id, "node_id": entity.hardware_id,
                "device_name": merged.entities[device].name, "model_id": str(entity.identity.get("model_id", "")),
                "serials": expected_module.device_serials(merged.entities[device], layer, device)}

    def _device_entry(self, path: str, view: View) -> expected_module.ExpectedDevice:
        """What "Expect what's here" records for a device row: its ids, the word the rig row counts it as, and the
        nodes that answer below it now (relative paths, with their model and repeatable ids)."""

        merged, layer = view.merged, view.layer
        entity = merged.entities[path]
        own = expected_module.latest_statuses(merged.statuses)
        usb_serial = self._usb_serial(entity.hardware_id)
        serial = str((entity.identity or {}).get("serial_number") or "")
        nodes = [expected_module.ExpectedNode(expected_module.relative(node, path),
                                              str(merged.entities[node].identity.get("model_id", "")),
                                              merged.entities[node].hardware_id)
                 for node in expected_module.answering_nodes(path, merged, own, view.now, RUNNER_SOURCE)]
        return expected_module.ExpectedDevice(
            name=entity.name, hardware_id=entity.hardware_id, kind=expected_module.kind_of_row(merged, path),
            # WHY a driver's serial too when it differs from the USB one: a camera is named by the RealSense serial
            serial=serial if serial and serial not in usb_serial else "", model_id=str(entity.identity.get("model_id", "")),
            nodes=nodes)

    @staticmethod
    def _nodes_text(nodes: list) -> str:
        return ", ".join(node.path for node in nodes) or "no nodes"

    def _plan(self, op: str, path: str, view: View, *, rig: str | None = None, reason: str = "") -> dict:
        """``{"manifest", "text", "forget"}`` for one edit, or ``{"refused", "error"}``. The confirm text the page shows
        and the edit that runs come from this one plan."""

        target = self._setup_target(path, view)
        if target is None:
            return {"refused": "NotADevice", "error": f"{path!r} is not a device row, a rig row or an absent row"}
        role, ids = target
        if role == "board" and op == "forget":
            # WHY needs no manifest: a removed row is the host's memory, not the rig file (Zach, 2026-10-24: "remove from bus")
            return {"board": ids, "text": f"Remove {ids['name']} from the bus? RobotDx forgets the row; if {ids['name']} answers "
                                          "again, it comes back."}
        try:
            manifest = self.setup.editable()
        except expected_module.ManifestError as error:
            return {"refused": "ManifestUnavailable", "error": str(error)}
        if op == "expect":
            if role == "rig":
                return self._plan_expect_rig(ids["rig"], manifest, view)
            if role == "board":
                return self._plan_toggle_node(ids, manifest)
            if role != "device":
                return {"refused": "NotHere", "error": f"{ids['name']} is not connected: Expect what's here needs it plugged in"}
            entry = self._device_entry(path, view)
            current = next(((index, device) for index, found in enumerate(manifest.rigs) for device in found.devices
                            if (device.hardware_id and expected_module.same_id(device.hardware_id, entry.hardware_id))
                            or (device.serial and device.serial in ids["serials"])), None)
            back = expected_module.unretire(manifest, entry.hardware_id, ids["serials"])
            if current is not None:
                target_rig = manifest.rigs[current[0]]
                old = current[1]
                gone = sorted({node.path for node in old.nodes} - {node.path for node in entry.nodes})
                old.nodes, old.name = expected_module.carry_over(old.nodes, entry.nodes), entry.name
                old.kind, old.model_id = old.kind or entry.kind, entry.model_id or old.model_id
                if entry.serial and not old.serial:
                    old.serial = entry.serial
                if not old.hardware_id:
                    old.hardware_id = entry.hardware_id
                text =f"Expect {entry.name} in {target_rig.name} with {self._nodes_text(entry.nodes)}"
            else:
                names = [found.name for found in manifest.rigs]
                if rig is None and len(names) > 1:
                    return {"refused": "RigRequired", "error": f"choose a rig: {', '.join(names)}", "rigs": names}
                name = rig or (names[0] if names else DEFAULT_RIG)
                target_rig = next((found for found in manifest.rigs if found.name == name), None)
                if target_rig is None:
                    target_rig = expected_module.Rig(name)
                    manifest.rigs.append(target_rig)
                target_rig.devices.append(entry)
                gone = []
                text = f"Add {entry.name} to {target_rig.name}, expecting {self._nodes_text(entry.nodes)}"
            if gone:
                text += f" ({', '.join(gone)} no longer expected)"
            if back:
                text += " · takes it back into service (it was retired)"
            return {"manifest": manifest, "text": text + "?", "unforget": entry.hardware_id}
        if op == "forget":
            if role != "device":
                return {"refused": "NotADevice", "error": "Don't expect is for a device row; Remove from bus is for a board"}
            rigs = expected_module.remove_device(manifest, ids["hardware_id"], ids["serials"])
            if not rigs:
                return {"refused": "NotExpected", "error": f"{ids['name']} is not in the expected setup"}
            return {"manifest": manifest,
                    "text": f"Stop expecting {ids['name']}? It leaves the expected setup ({', '.join(rigs)}); it stays on the bus "
                            "as an ordinary device."}
        if op == "retire":
            if role != "device":
                return {"refused": "NotADevice", "error": "Retire… is for a device row"}
            reason = " ".join(str(reason or "").split())
            rigs = expected_module.remove_device(manifest, ids["hardware_id"], ids["serials"])
            expected_module.unretire(manifest, ids["hardware_id"], ids["serials"])
            date = expected_module.today(view.now, self.utc_offset_s)
            serial = next((serial for serial in ids["serials"] if serial not in self._usb_serial(ids["hardware_id"])), "")
            manifest.retired.append(expected_module.Retired(name=ids["name"], hardware_id=ids["hardware_id"], serial=serial,
                                                            date=date, reason=reason))
            where = f"It leaves {', '.join(rigs)}. " if rigs else ""
            plan = {"manifest": manifest,
                    "text": f"Retire {ids['name']}? {where}Plugged in again, its row says “retired {date}: "
                            f"{reason or '<reason>'}” (a warning)."}
            if not reason:
                plan.update(refused="ReasonRequired", error="say why it is retired (the reason shows when it is plugged in again)")
            return plan
        return {"refused": "UnknownEdit", "error": f"no edit {op!r}: expect, forget or retire"}

    def _plan_toggle_node(self, ids: dict, manifest: expected_module.Manifest) -> dict:
        """Expect / Don't expect one board: add its node to, or take it out of, its adapter's entry in the rig file."""

        found = next(((rig, device) for rig in manifest.rigs for device in rig.devices
                      if (device.hardware_id and expected_module.same_id(device.hardware_id, ids["hardware_id"]))
                      or (device.serial and device.serial in ids["serials"])), None)
        if found is None:
            return {"refused": "NotExpected", "error": f"{ids['device_name']} is not in the expected setup: use Expect what's here on it first"}
        rig, device = found
        existing = next((node for node in device.nodes if node.path == ids["rel"]), None)
        if existing is not None:
            device.nodes.remove(existing)
            return {"manifest": manifest, "text": f"Stop expecting {ids['name']} on {ids['device_name']}? {rig.name} no longer counts it, "
                                                   "and its bus stops saying it is silent."}
        device.nodes.append(expected_module.ExpectedNode(ids["rel"], ids["model_id"], ids["node_id"]))
        return {"manifest": manifest, "text": f"Expect {ids['name']} on {ids['device_name']} in {rig.name}? Its bus then says when it is silent."}

    def _plan_expect_rig(self, rig: expected_module.Rig, manifest: expected_module.Manifest, view: View) -> dict:
        target = next((found for found in manifest.rigs if found.name == rig.name), None)
        if target is None:
            return {"refused": "NoSuchRig", "error": f"no rig {rig.name!r} in the file"}
        changes = []
        for device in target.devices:
            path = expected_module.locate(device, view.merged, view.layer)
            if path is None:
                continue
            entry = self._device_entry(path, view)
            before, after = {node.path for node in device.nodes}, {node.path for node in entry.nodes}
            bits = [f"also {', '.join(sorted(after - before))}"] * bool(after - before) + \
                   [f"{', '.join(sorted(before - after))} no longer expected"] * bool(before - after)
            if bits:
                changes.append(f"{device.label()}: {'; '.join(bits)}")
            device.nodes = expected_module.carry_over(device.nodes, entry.nodes)
        text = (f"Expect what is here now in {target.name}: {' · '.join(changes)}?" if changes
                else f"Expect what is here now in {target.name}? Nothing changes: every present device's nodes are as expected.")
        return {"manifest": manifest, "text": text}

    def setup_json(self, path: str, view: View) -> dict | None:
        """The Expected setup card of one row: where it stands, what it offers, and each edit's confirm text."""

        target = self._setup_target(path, view)
        if target is None:
            return None
        role, ids = target
        merged = view.merged
        out = {"role": role, "file": str(self.setup.path) if self.setup.path else None, "note": self.setup.note,
               "editable": bool(self.setup.path) and not self.setup.note,
               "rigs": [rig.name for rig in self.setup.manifest.rigs], "rig": None, "expected": False, "retired": None,
               "nodes_expected": [], "node_notes": {}, "nodes_here": [],
               "can": {"expect": False, "forget": False, "retire": False},
               "confirm": {}, "needs_rig": False}
        if role == "rig":
            out.update(rig=ids["rig"].name, expected=True, can={"expect": True, "forget": False, "retire": False})
        else:
            found = self.setup.entry_for(ids["hardware_id"], ids["serials"]) if role != "board" else None
            if found:
                out.update(rig=found[0].name, expected=True, nodes_expected=[node.path for node in found[1].nodes],
                           node_notes={node.path: node.note for node in found[1].nodes if node.note})
            retired = self.setup.retired_entry(ids["hardware_id"], ids["serials"])
            if retired:
                out["retired"] = {"date": retired.date, "reason": retired.reason}
            if role == "device":
                own = expected_module.latest_statuses(merged.statuses)
                out["nodes_here"] = [expected_module.relative(node, path) for node in
                                     expected_module.answering_nodes(path, merged, own, view.now, RUNNER_SOURCE)]
                out["can"] = {"expect": True, "forget": bool(found), "retire": True}
                out["needs_rig"] = not found and len(out["rigs"]) > 1
            else:  # a board
                device_found = self.setup.entry_for(ids["hardware_id"], ids["serials"])
                expected = bool(device_found) and any(node.path == ids["rel"] for node in device_found[1].nodes)
                out.update(rig=device_found[0].name if device_found else None, expected=expected,
                           nodes_expected=[node.path for node in device_found[1].nodes] if device_found else [])
                # WHY expect only when its adapter is in the file: a board is expected as part of an adapter's entry
                out["can"] = {"expect": bool(device_found), "forget": True, "retire": False}
        for op, allowed in out["can"].items():
            if allowed:
                plan = self._plan(op, path, view, rig=out["rigs"][0] if out["needs_rig"] else None, reason="")
                out["confirm"][op] = plan.get("text") or plan.get("error", "")
        if not out["editable"]:
            # Remove from bus is the host's memory, not the rig file: it needs no file
            out["can"] = {op: bool(allowed and role == "board" and op == "forget") for op, allowed in out["can"].items()}
        return out

    def setup_edit(self, op: str, path: str, *, rig: str | None = None, reason: str = "") -> dict:
        """Run one edit of the expected setup for the row at ``path``: ``expect`` (Expect what's here), ``forget`` or
        ``retire`` (with ``reason``). A host-side file write: it never touches a device. Returns
        ``{"ok", "op", "path", "refused", "message", "file"}``."""

        view = self.view()
        plan = self._plan(op, path, view, rig=rig, reason=reason)
        out = {"ok": False, "op": op, "path": path, "refused": plan.get("refused"), "message": plan.get("error", ""),
               "file": str(self.setup.path) if self.setup.path else None}
        if plan.get("refused"):
            return {**out, **({"rigs": plan["rigs"]} if "rigs" in plan else {})}
        result = self._apply(plan, view.now)
        if result.get("ok"):
            self.refresh()  # WHY now: the absent rows are judged at refresh; a forgotten row must not linger 2 s
        return {**out, **result}

    def _apply(self, plan: dict, now: float) -> dict:
        if plan.get("board"):
            board = plan["board"]
            with self.lock:
                self.roster.forget(board["job"], self._device_hardware_id(board["job"]), board["rel"], now)
            self.notify("forget")
            return {"ok": True, "message": f"done: {plan['text'].split('?', 1)[0]}"}
        try:
            self.setup.save(plan["manifest"])
        except (OSError, expected_module.ManifestError) as error:
            return {"refused": "NotWritten", "message": f"{type(error).__name__}: {error}"}
        if plan.get("forget"):
            self.seen.forget(plan["forget"], now)
        if plan.get("unforget"):
            self.seen.forgotten.pop(plan["unforget"], None)
        return {"ok": True, "message": f"done: {plan['text'].split('?', 1)[0]}"}  # the confirm's question, as a fact

    def setup_edit_ids(self, op: str, *, hardware_id: str = "", serial: str = "", name: str = "", rig: str | None = None,
                       reason: str = "", nodes: tuple[str, ...] = (), kind: str = "") -> dict:
        """The same three edits by id, for the CLI (``--hardware-id`` / ``--serial``): no row needed, nothing runs."""

        out = {"ok": False, "op": op, "path": None, "refused": None, "message": "",
               "file": str(self.setup.path) if self.setup.path else None}
        if not hardware_id and not serial:
            return {**out, "refused": "NoId", "message": "name the device: --hardware-id or --serial"}
        try:
            manifest = self.setup.editable()
        except expected_module.ManifestError as error:
            return {**out, "refused": "ManifestUnavailable", "message": str(error)}
        serials = tuple(value for value in (serial, *self._usb_serial(hardware_id)) if value)
        label = name or hardware_id or serial
        if op == "expect":
            expected_module.remove_device(manifest, hardware_id, serials)
            back = expected_module.unretire(manifest, hardware_id, serials)
            names = [found.name for found in manifest.rigs]
            if rig is None and len(names) > 1:
                return {**out, "refused": "RigRequired", "message": f"choose a rig with --rig: {', '.join(names)}"}
            rig_name = rig or (names[0] if names else DEFAULT_RIG)
            target = next((found for found in manifest.rigs if found.name == rig_name), None)
            if target is None:
                target = expected_module.Rig(rig_name)
                manifest.rigs.append(target)
            target.devices.append(expected_module.ExpectedDevice(name=label, hardware_id=hardware_id, serial=serial, kind=kind,
                                                                 nodes=[expected_module.ExpectedNode(node.strip("/")) for node in nodes]))
            text = f"Add {label} to {rig_name}, expecting {', '.join(nodes) or 'no nodes'}" + (" (back into service)" if back else "")
            plan = {"manifest": manifest, "text": text, "unforget": hardware_id}
        elif op == "forget":
            rigs = expected_module.remove_device(manifest, hardware_id, serials)
            plan = {"manifest": manifest, "text": f"Forget {label}" + (f" (left {', '.join(rigs)})" if rigs else ""),
                    "forget": hardware_id}
        elif op == "retire":
            reason = " ".join(str(reason or "").split())
            if not reason:
                return {**out, "refused": "ReasonRequired", "message": "say why it is retired: --reason TEXT"}
            existing = next((entry for entry in manifest.retired if (hardware_id and expected_module.same_id(entry.hardware_id, hardware_id))
                             or (serial and entry.serial == serial)), None) or next(
                (device for _rig, device in manifest.entries() if (hardware_id and expected_module.same_id(device.hardware_id, hardware_id))
                 or (serial and device.serial == serial)), None)
            rigs = expected_module.remove_device(manifest, hardware_id, serials)
            expected_module.unretire(manifest, hardware_id, serials)
            date = expected_module.today(self.clock.now(), self.utc_offset_s)
            manifest.retired.append(expected_module.Retired(name=name or (existing.name if existing else label), hardware_id=hardware_id,
                                                            serial=serial, date=date, reason=reason))
            plan = {"manifest": manifest, "text": f"Retire {label} on {date}: {reason}" + (f" (left {', '.join(rigs)})" if rigs else "")}
        else:
            return {**out, "refused": "UnknownEdit", "message": f"no edit {op!r}: expect, forget or retire"}
        return {**out, **self._apply(plan, self.clock.now())}

    def check_json(self, status: DiagnosticStatus, now: float) -> dict:
        level = effective_level(status, now)
        seen = status.received if status.received is not None else status.stamp
        return {"name": status.name, "level": status.level, "effective_level": level, "stale": level == STALE,
                "message": status.message, "code": status.code, "mode": status.mode, "source": status.source,
                "hardware_id": status.hardware_id, "stamp": status.stamp, "received": status.received,
                "received_iso": activity_module.iso(status.received, self.utc_offset_s),
                "age_s": round(now - seen, 3) if seen is not None else None,
                "expected_period_s": status.expected_period_s,
                "stale_after_s": max(STALE_FACTOR * status.expected_period_s, STALE_FLOOR_S) if status.expected_period_s else None,
                "uptime_s": status.uptime_s, "caused_by": list(status.caused_by),
                "values": [{"key": key, "value": value} for key, value in status.values],
                "measurements": [{"key": measurement.key, "value": measurement.value, "unit": measurement.unit,
                                  "text": measurement.render(), "level": measurement.level, "stamp": measurement.stamp,
                                  "warn_low": measurement.warn_low, "warn_high": measurement.warn_high,
                                  "error_low": measurement.error_low, "error_high": measurement.error_high}
                                 for measurement in status.measurements]}

    def fault_json(self, fault, view: View) -> dict:
        out = {key: getattr(fault, key) for key in ("path", "code", "severity", "status", "active", "occurrence_count",
                                                     "first_occurred", "last_occurred", "last_passed", "message", "hardware_id")}
        for key in ("first_occurred", "last_occurred", "last_passed"):
            out[f"{key}_iso"] = activity_module.iso(getattr(fault, key), self.utc_offset_s)
        out["name"] = view.merged.entities[fault.path].name if fault.path in view.merged.entities else ""
        return out

    # --- actions: shown with their gate state, never executed ---------------------------------------------------
    def _firmware(self, path: str, view: View) -> DiagnosticStatus | None:
        return next((status for status in view.rows.own.get(path, [])
                     if status.code == "UpdateAvailable" or status.name == "firmware" or status.name.endswith(": firmware")), None)

    def evaluate(self, check: str, path: str, view: View) -> tuple[bool | None, str]:
        entity = view.merged.entities[path]
        if check == "board_answers":
            own = view.rows.own.get(path, [])
            if not own:
                return None, f"no report from {entity.name}"
            fresh = [status for status in own if effective_level(status, view.now) != STALE and status.source != RUNNER_SOURCE]
            if not fresh:
                return None, f"{entity.name}: its last report is stale"
            silent = next((status for status in fresh if status.code in tree.NOT_ANSWERING_CODES), None)
            if silent:
                return False, f"{entity.name}: {silent.message}"
            newest = tree.last_seen(fresh)
            when = activity_module.iso(newest, self.utc_offset_s) or ""
            return True, f"{entity.name} reported at {when[11:19]}"
        if check == "update_available":
            firmware = self._firmware(path, view)
            if firmware is None:
                return None, "no firmware check reported"
            values = dict(firmware.values)
            versions = f"installed {values.get('installed_version', '?')}, latest {values.get('latest_version', '?')}"
            if firmware.code == "UpdateAvailable":
                return True, versions
            if firmware.level == OK:
                return False, versions
            return None, firmware.message or versions
        if check in host_utilities.CHECKS:
            return self._evaluate_utility(check, path, view)
        if check == "board_idle":
            return self._board_idle(path, view)
        if check == "source_id_unique":
            return self._source_id_unique(path, view)
        if check not in OS_GATES:
            # the driver answers a precondition only it can read (its own files): a value precondition.<check> on one of
            # the entity's statuses, "pass: <why>" or "fail: <why>" (docs/api-v1.md, "Actions: two states")
            said = self._driver_says(check, path, view)
            if said is not None:
                return said
        device_path = tree.nearest_device(path, view.merged, view.layer)
        if device_path is None:
            return None, f"{check}: no physical device above {entity.name}"
        # WHY peek where the OS source has one: this is a page's read. A fixture's gates are recorded answers, and a run asks again on the bus
        return getattr(self.os, "peek", self.os.gate)(view.layer.matchable(view.layer.device_of_path[device_path]), check)

    def _driver_says(self, check: str, path: str, view: View) -> tuple[bool, str] | None:
        key = f"{PRECONDITION_VALUE}{check}"
        for status in view.rows.own.get(path, []):
            text = str(dict(status.values).get(key, "")).strip()
            verdict, _, why = text.partition(":")
            if verdict in ("pass", "fail"):
                return verdict == "pass", why.strip() or text
        return None

    def _board_idle(self, path: str, view: View) -> tuple[bool | None, str]:
        """The board's latest State (a watch's, or a probe's ``state`` record): mode 0 and no fault is idle. A State that
        has gone stale says nothing; the driver reads the board on the bus before it moves it, and sends nothing if not."""

        entity = view.merged.entities[path]
        target = self._watch_target(path, view)
        if target is None:
            return None, f"no State from {entity.name}"
        job, rel = target
        groups = entity_state.state_groups(job.cache, rel, view.now)  # WHY no lock: the view's own copy
        found = {}
        for group in groups:
            if group["stale"]:
                continue
            for row in group["rows"]:
                if row["key"] in ("mode", "fault") and row["key"] not in found and isinstance(row["value"], (int, float)):
                    found[row["key"]] = row
        if "mode" not in found:
            return None, f"no fresh State from {entity.name}: its mode is read on the bus when it runs"
        mode, fault = found["mode"], found.get("fault")
        words = lambda row: f"{row['value']:g}" + (f" ({row['meaning']})" if row.get("meaning") else "")  # noqa: E731
        if fault is not None and fault["value"]:
            return False, f"{entity.name} reports fault {words(fault)}: Clear faults first"
        if mode["value"] != 0:
            return False, f"{entity.name} is in mode {words(mode)}: something is driving it (it must be stopped, mode 0)"
        return True, f"{entity.name} reports mode 0 (stopped), no fault"

    def _source_id_unique(self, path: str, view: View) -> tuple[bool | None, str]:
        """One board on this CAN id: the driver's own id check (two UUIDs answering one id is IdCollision). Without one,
        the driver reads the UUIDs on the bus before it changes the id, and refuses a shared id with nothing sent."""

        entity = view.merged.entities[path]
        own = view.rows.own.get(path, [])
        shared = next((status for status in own if status.code in ID_SHARED_CODES and effective_level(status, view.now) != STALE), None)
        if shared is not None:
            return False, f"{entity.name}: {shared.message}"
        answers, detail = self.evaluate("board_answers", path, view)
        if answers:
            return True, f"one board answers {entity.name}"
        return (False, detail) if answers is False else (None, detail)

    def _evaluate_utility(self, check: str, path: str, view: View) -> tuple[bool | None, str]:
        """The preconditions of the host's own utilities (``host_utilities.CHECKS``) for a USB device or port row."""

        found = host_utilities.usb_target(path, view.layer)
        if found is None:
            return None, f"{check}: not a USB device or port"
        device, port = found
        if check == "device_present":
            if device is None:
                return False, f"nothing is plugged in at {port} now"
            return True, str(device.get("label") or device.get("id"))
        if check == "no_run_active":
            busy = self._active_run_near(path, view.layer, found)
            return (False, busy) if busy else (True, "no run is active on it")
        if device is None:
            return None, f"nothing is plugged in at {port} now"
        # WHY not os.gate: a recording or a fake machine has no real port, and says so (a blocked button), unless a test points
        # the module at a fake sysfs tree (IO_DIAGNOSTICS_USB_SYSFS), which then answers like a live one
        if getattr(self.os, "mode", "live") != "live" and not usb_reset.sysfs_overridden():
            return (False if check == "reset_privilege" else None), "this host replays a recording or a fake machine: there is no real port to reset"
        peek = getattr(self.os, "peek", None)
        return peek(device, check) if peek is not None and not usb_reset.sysfs_overridden() else usb_reset_gate(device, check)

    def _active_run_near(self, path: str, layer, found: tuple[dict | None, str]) -> str:
        """Why a reset would collide with a RobotDx run: an active run on the device, on anything below it (a hub), or on a
        hub above it. "" when none. WHY by path overlap: the run's own ``device_path`` is where it acts, and a reset takes
        down everything beneath the port."""

        device, _port = found
        here = layer.device_paths.get(str(device.get("id"))) if device else None
        for run in self.runs.active():
            for mine in (path, here):
                theirs = run.spec.device_path
                if mine and theirs and (mine == theirs or mine.startswith(theirs + "/") or theirs.startswith(mine + "/")):
                    return f"{run.spec.label} is running on it (started {run.to_json()['started_iso'] or 'just now'})"
        return ""

    def _fill(self, path: str, view: View) -> _Blank:
        """The values a confirm text's ``{name}``, ``{id}``, ``{serial}``, ``{latest}`` placeholders take.

        WHY ``{name}`` is the nearest device's name: the drivers' convention is ``{name}`` = the physical device that
        owns the action (a CAN board's adapter, a RealSense itself) and ``{id}`` = the node id, so a board's
        "Calibrate CAN {id} on {name}?" reads "Calibrate CAN 1 on fdcanusb 272813EF?", not "... on CAN 1?"."""

        entity = view.merged.entities[path]
        device_path = tree.nearest_device(path, view.merged)
        device = view.merged.entities[device_path] if device_path else None
        port = ""
        found = host_utilities.usb_target(path, view.layer)
        if found is not None:  # a USB device or port row: {port} is the port it sits at; a port row's {name} is the device in it
            port = found[1]
            if found[0] is not None and entity.kind == "port":
                device = view.merged.entities.get(view.layer.device_paths.get(str(found[0].get("id")), "")) or device
        serial = entity.identity.get("serial_number") or (device.identity.get("serial_number", "") if device else "")
        firmware = self._firmware(path, view)
        return _Blank(name=device.name if device else entity.name, id=path.rsplit("/", 1)[-1], serial=serial, port=port,
                      latest=dict(firmware.values).get("latest_version", "?") if firmware else "?")

    def actions_json(self, path: str, view: View) -> list[dict]:
        entity = view.merged.entities[path]
        fill = self._fill(path, view)
        out = []
        for action in self._entity_actions(path, entity, view):
            results = []
            for check, text in action.preconditions:
                state, detail = self.evaluate(check, path, view)
                results.append({"check": check, "text": string.Formatter().vformat(text, (), fill),
                                "state": {True: "pass", False: "fail", None: "unknown", WAITING: WAITING}[state],
                                "detail": detail, "resolves": resolves(check, state)})
                if results[-1]["resolves"] == "blocks" and state is None:
                    results[-1]["detail"] = BLOCK_WHEN_UNKNOWN[check]
                elif results[-1]["resolves"] == "at_run" and detail.endswith("not a check the host evaluates"):
                    results[-1]["detail"] = "checked on the bus when it runs; nothing is sent if it fails"
                elif results[-1]["resolves"] == "confirm" and state is None:
                    results[-1]["detail"] = "you confirm it on the Run page"
            # WHY device_free is not a block: something else having the device open is the person's call (the second
            # confirmation names it), not a reason the button is dead (docs/usb-reset.md)
            second = next((result for result in results if result["check"] == "device_free" and result["state"] == "fail"), None)
            if second is not None:
                second["resolves"] = "confirm"
            failing = [result for result in results if result["resolves"] == "blocks"]
            waiting = [result for result in results if result["state"] == WAITING]
            up_to_date = next((result for result in failing if result["check"] == "update_available"), None)
            if up_to_date:  # WHY first: with nothing to install, a free port would not make the button useful (V6)
                state, reason = "nothing_to_do", f"up to date: {up_to_date['detail']}"
            elif failing:
                first = next(result for result in failing if result["check"] != "update_available")
                detail = first["detail"]
                state, reason = "blocked", detail if detail.startswith("blocked") else f"blocked: {detail}"
            elif waiting:  # WHY its own state: runnable in a few seconds, by itself; the page greys it, never warns
                state, reason = WAITING, waiting[0]["detail"]
            else:  # never "unknown" (Zach, 2026-10-29): what is not read now is confirmed by the person or checked at run
                state, reason = "ready", ""
            confirmations = [result["text"] for result in results if result["resolves"] == "confirm" and result is not second]
            # WHY here and not only in the dry run: a greyed utility has no Run page, so how to allow it rides on the action
            fix = self._reset_fix(path, view) if action.name == usb_reset.NAME and any(
                result["check"] == "reset_privilege" and result["resolves"] == "blocks" for result in results) else None
            out.append({"name": action.name, "title": action.title, "description": action.description, "danger": action.danger,
                        "scope": action.scope, "kind": action.kind,
                        "confirm": string.Formatter().vformat(action.confirm, (), fill),
                        "preconditions": results, "state": state, "reason": reason, "executable": False,
                        "owner": host_utilities.OWNER if action in host_utilities.OFFERED else entity.owner,
                        "second_confirm": second["detail"] if second else "", "confirmations": confirmations,
                        **({"fix": fix} if fix else {}), "last_run": self._last_run(path, action.name)})
        if view.composition is not None and path in view.composition.by_path:
            out += self.batch_actions_json(path, view)
        return out

    def _reset_fix(self, path: str, view: View) -> str | None:
        """How to let this user reset the USB port (the udev rule for this device), for a reset_port its privilege blocks."""

        found = host_utilities.usb_target(path, view.layer)
        target = usb_reset.target_of(found[0]) if found and found[0] is not None else None
        return usb_reset.fix_text(target) if target is not None else None

    def _entity_actions(self, path: str, entity, view: View) -> list[Action]:
        """The entity's registered actions, plus the host's own utilities on the rows they belong to (a USB device or port
        row), unless the entity's driver registered that name itself or opted its devices out."""

        actions = list(entity.actions)
        for utility in host_utilities.OFFERED:
            if not any(action.name == utility.name for action in actions) and self._offers(utility.name, path, view.layer, view):
                actions.append(utility)
        return actions

    def _offers(self, name: str, path: str, layer, view: View | None = None) -> bool:
        """Whether the host offers its utility ``name`` on this row: a USB device or port, and no driver whose device sits at
        or below it opted out in ``describe`` (``"opt_out": ["reset_port"]``: a device that must not be reset mid-run; a hub
        is not reset under such a device either)."""

        found = host_utilities.usb_target(path, layer)
        if found is None:
            return False
        ids = set(host_utilities.subtree_device_ids(found[1], layer))
        jobs = [job for job in (view.jobs if view is not None else self.jobs).values() if str(job.device.get("id")) in ids]  # never mutated: replaced whole
        return not any(name in ((self.describes.get(job.spec.name) or {}).get("opt_out") or []) for job in jobs)

    # --- actions: run one, for a person ------------------------------------------------------------------------------
    def _owning_job(self, path: str, entity, view: View | None = None) -> Job | None:
        """The job whose device row is ``path`` or above it; with several plugins on one device, the entity's owner. ``view``: as that view knew them, with no lock."""

        jobs, layer = (view.jobs, view.layer) if view is not None else (self.jobs, self.layer)
        candidates = []
        for job in list(jobs.values()):
            device_path = layer.device_paths.get(str(job.device.get("id")), job.device_path)
            # WHY the host's own row only: every path is below the host, and the computer plugin owns nothing below it
            below = device_path != tree.HOST_PATH and path.startswith(device_path + "/")
            if path == device_path or below:
                candidates.append((len(device_path), job))
        if not candidates:
            return None
        deepest = max(length for length, _ in candidates)
        candidates = [job for length, job in candidates if length == deepest]
        return next((job for job in candidates if job.spec.name == entity.owner), candidates[0] if len(candidates) == 1 else None)

    def _registered(self, job: Job, entity, name: str) -> tuple[Action | None, str]:
        """The action as the entity registered it (after a diagnose), else as the plugin's ``describe`` lists it
        (a fresh ``io-diagnostics act`` process has run no diagnose: describe is passive, diagnose may not be)."""

        found = next((action for action in entity.actions if action.name == name), None)
        if found is not None:
            return found, "entity"
        if self.describes.get(job.spec.name) is None:
            self._source_stamps[job.spec.name] = source_stamp(job.spec)
            self.describes[job.spec.name] = describe(job.spec, extra_env=self._job_env())
        for item in (self.describes.get(job.spec.name) or {}).get("actions") or []:
            if isinstance(item, dict) and item.get("name") == name:
                try:
                    return from_dict({"schema": SCHEMA, **item, "record": "action"}), "describe"
                except (TypeError, ValueError, KeyError):
                    return None, "describe"
        return None, ""

    def act(self, path: str, action: str, *, node: int | None = None, confirm: str | None = None, dry_run: bool = False,
            human: bool | None = None, new_id: int | None = None, extra: list[str] | None = None) -> dict:
        """Run the plugin's ``act`` for one action registered on the entity at ``path``, and wait for it.

        The rule (docs/api-v1.md "Actions: io-diagnostics act"): only a person runs an action; agents can't click.
        - ``human`` is whether a person asked: the CLI passes stdin-is-a-TTY, and so does the default. Not a person
          -> everything is refused except ``dry_run``, which the plugin answers without touching the device.
        - Every action whose danger is not ``query`` needs ``confirm`` (the token the confirm dialog shows). The
          host passes it through; the plugin checks it against the device (moteus: its time-window token, RealSense:
          the camera's serial), because only the owner knows what it confirms.
        - ``new_id`` passes through as ``--new-id`` (set_id's new bus id); the plugin checks it.
        - It runs inside the plugin's ``exclusive`` group, so it never collides with its own diagnose.
        - A dry run asks the plugin on pipes. A real run is a RUN (docs/runs-v1.md): a process on a PTY with a
          recording, here waited for; ``start_act`` is the same run without the wait."""

        if action.startswith(composition_module.BATCH_PREFIX):
            if dry_run:
                return self.batch_dry_run(path, action)
            return {**self._batch_out(path, action, dry_run=False), "refused": "BatchFromPage",
                    "message": "a batch of runs starts from the page, after its dry run (the CLI runs one board's action at a time)"}
        out, plan = self._plan_act(path, action, node=node, confirm=confirm, dry_run=dry_run, human=human, new_id=new_id, extra=extra)
        if plan is None:
            return out
        if dry_run:
            group = plan.job.spec.exclusive
            busy = next((run for run in self.runs.active() if group and run.spec.group == group), None)
            if busy is not None:
                # WHY refuse and not wait: a run can last minutes at a prompt, and the page's dry run would hang for it
                return {**out, "refused": "Busy", "message": f"{busy.spec.label} is running on the same bus ({group}): "
                        "wait for it, or stop it, before asking for another dry run"}
            with self.gates.hold(group, person=True):
                result = run_act(plan.job.spec, plan.job.device, plan.device_path, plan.argv, self.clock.now,
                                 self._act_env(plan.job))
            return {**out, **result}
        run = self._launch_run(plan, out, interactive=False)
        run.wait()
        return {**out, **(run.result or {}), "run_id": run.id}

    def start_act(self, path: str, action: str, *, node: int | None = None, confirm: str | None = None,
                  new_id: int | None = None, interactive: bool = True, extra: list[str] | None = None):
        """A real run, started and not waited for: ``(run, out)``. ``out`` is ``act``'s answer so far (the plan, or why
        it was refused, with ``run`` None). ``interactive``: it was started from the page, so it takes input and Stop."""

        out, plan = self._plan_act(path, action, node=node, confirm=confirm, dry_run=False, human=True, new_id=new_id, extra=extra)
        if plan is None:
            return None, out
        return self._launch_run(plan, out, interactive=interactive), {**out, "run_id": None}

    def _plan_act(self, path: str, action: str, *, node: int | None, confirm: str | None, dry_run: bool,
                  human: bool | None, new_id: int | None, extra: list[str] | None = None):
        """``(out, plan)``: the answer so far and what would run, or ``(refusal, None)``."""

        if human is None:
            try:
                human = sys.stdin is not None and sys.stdin.isatty()
            except (ValueError, OSError):  # a closed stdin is not a person
                human = False
        view = self.view()
        out = {"path": path, "action": action, "node": node, "new_id": new_id, "dry_run": dry_run, "plugin": None,
               "device_id": None, "title": "", "danger": None, "registered_by": "", "argv": None, "refused": None,
               "outcome": "refused", "returncode": None, "duration_s": 0.0, "records": [], "action_result": None, "rejected": [],
               "stderr_tail": "", "message": ""}

        def refuse(code: str, message: str) -> tuple[dict, None]:
            return {**out, "refused": code, "message": message}, None

        entity = view.merged.entities.get(path)
        if entity is None:
            return refuse("NoSuchEntity", f"no entity at {path!r}")
        job = self._owning_job(path, entity)
        registered, source = self._registered(job, entity, action) if job is not None else (None, "")
        if registered is None and action in host_utilities.NAMES:
            builtin = self._plan_utility(view, entity, path, action, out, confirm=confirm, dry_run=dry_run, human=human, extra=extra)
            if builtin is not None:
                return builtin
        if job is None:
            return refuse("NoPlugin", f"no enabled plugin diagnoses {entity.name} ({path})")
        out.update(plugin=job.spec.name, device_id=str(job.device.get("id", "")), registered_by=source)
        if registered is None:
            return refuse("ActionNotRegistered", f"{job.spec.name} registers no action {action!r} for {entity.name}")
        argv = act_argv(job.spec, job.device, action, node=node, confirm=confirm, dry_run=dry_run, new_id=new_id, extra=extra)
        out.update(title=registered.title, danger=registered.danger, argv=argv)
        if not human and not dry_run:
            return refuse("NotInteractive", "not a person (stdin is not a TTY): only --dry-run runs; agents can't click")
        if registered.danger != "query" and not dry_run and not confirm:
            text = string.Formatter().vformat(registered.confirm, (), self._fill(path, view))
            return refuse("ConfirmRequired", f"{registered.danger}: pass --confirm <token> after reading: {text}")
        device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
        return out, _ActPlan(job, argv, device_path, entity, registered, node, new_id)

    def _plan_utility(self, view: View, entity, path: str, action: str, out: dict, *, confirm: str | None, dry_run: bool,
                      human: bool, extra: list[str] | None):
        """The host's own utility (``host_utilities``) on a USB device or port row: ``(out, plan)``, or None when this row does
        not get it (the caller then refuses as it would for any unknown action). It runs ``io_diagnostics.usb_reset`` through
        the same dry run -> token -> run path as a driver's action; its gates are, in order: the driver did not opt out, a
        device is there, no RobotDx run is active on it, a real run only for a person, with a confirmation, and (here and again
        in the verb) the privilege and the holders."""

        def refuse(code: str, message: str):
            return {**out, "refused": code, "message": message}, None

        if not self._offers(action, path, view.layer):
            return None
        device, port = host_utilities.usb_target(path, view.layer)
        out.update(plugin=host_utilities.OWNER, registered_by="builtin", title=host_utilities.RESET_PORT.title,
                   danger=host_utilities.RESET_PORT.danger)
        if device is None:
            return refuse("NoDevice", f"nothing is plugged in at {port} now")
        out["device_id"] = str(device.get("id", ""))
        busy = self._active_run_near(path, view.layer, (device, port))
        if busy:
            return refuse("RunActive", f"{busy}: wait for it, or stop it, before resetting the port")
        argv = act_argv(host_utilities.SPEC, device, action, confirm=confirm, dry_run=dry_run, extra=extra)
        out["argv"] = argv
        if not human and not dry_run:
            return refuse("NotInteractive", "not a person (stdin is not a TTY): only --dry-run runs; agents can't click")
        if not dry_run and not confirm:
            text = string.Formatter().vformat(host_utilities.RESET_PORT.confirm, (), self._fill(path, view))
            return refuse("ConfirmRequired", f"{host_utilities.RESET_PORT.danger}: pass --confirm <token> after reading: {text}")
        if not dry_run and getattr(self.os, "mode", "live") != "live" and not usb_reset.sysfs_overridden():
            return refuse("NotLive", "this host replays a recording or a fake machine: there is no real port to reset")
        device_path = self.layer.device_paths.get(str(device.get("id")), "")
        shown = view.merged.entities.get(device_path)  # WHY its row's name: the confirm, the terminal and the records say "fdcanusb 272813EF"
        device = {**device, "label": shown.name} if shown is not None and shown.name else device
        return out, _ActPlan(Job(host_utilities.SPEC, device, device_path), argv, device_path, entity, host_utilities.RESET_PORT, None, None)

    def _describe_for(self, job: Job) -> dict | None:
        if self.describes.get(job.spec.name) is None:
            self._source_stamps[job.spec.name] = source_stamp(job.spec)
            self.describes[job.spec.name] = describe(job.spec, extra_env=self._job_env())
        return self.describes[job.spec.name]

    def _launch_run(self, plan: "_ActPlan", out: dict, *, interactive: bool):
        run = self._create_run(plan, out, interactive=interactive)
        run.launch()
        return run

    def _create_run(self, plan: "_ActPlan", out: dict, *, interactive: bool, label: str | None = None):
        """The run, registered and ``queued``, not started: a batch creates all of its runs at once and starts them in order."""

        job, spec = plan.job, plan.job.spec
        started = self.clock.now()
        device_id = str(job.device.get("id", ""))
        stop_argv = None
        if plan.registered.danger == "moves" and (self._describe_for(job) or {}).get("stop"):
            # the plugin's own stop verb (docs/runs-v1.md section 6): run by the host after a moves run that did not finish
            stop_argv = [*spec.command, "stop", "--device", device_id, *(["--node", str(plan.node)] if plan.node is not None else [])]
        label = label or f"{plan.entity.name} · {terminal_module.plain_title(plan.registered.title)}"
        builtin = spec is host_utilities.SPEC
        run_spec = terminal_module.RunSpec(
            plugin=spec.name, device_id=device_id, device_path=plan.device_path, path=out["path"], action=out["action"],
            title=plan.registered.title, label=label, danger=plan.registered.danger, argv=plan.argv,
            env=plugin_env(spec, job.device, plan.device_path, started, self._act_env(job)), cwd=spec.cwd,
            timeout_s=spec.act_timeout_s, node=plan.node, new_id=plan.new_id, hardware_id=plan.entity.hardware_id,
            stop_argv=stop_argv, group=spec.exclusive,
            hold=(lambda: self._utility_hold(job)) if builtin else (lambda: self._run_hold(job, spec.exclusive)))
        return self.runs.create(run_spec, interactive=interactive)

    def _act_env(self, job: Job) -> dict[str, str]:
        """``_job_env`` for an act. The host's own utility also learns whether this host is a replay, and which sessions are
        this host's own watch / follow processes (they hold a device for a page and are not "someone else" to a reset)."""

        env = self._job_env(job)
        if job.spec is host_utilities.SPEC:
            env[usb_reset.OS_MODE_ENV] = str(getattr(self.os, "mode", "live"))
            env[usb_reset.OWN_SESSIONS_ENV] = ",".join(str(session) for session in RUNNING.passive_sessions())
        return env

    @contextlib.contextmanager
    def _utility_hold(self, job: Job):
        """What the host's own reset holds while it runs: the ``exclusive`` group of every plugin whose device is in the port
        (a person's press goes first, one group at a time in name order so two resets cannot deadlock), and no watch of those
        devices, so the reset finds nothing of the host's own holding the device."""

        found = host_utilities.usb_target(job.device_path, self.layer) if job.device_path in self.layer.entities else None
        ids = set(host_utilities.subtree_device_ids(found[1], self.layer)) if found else {str(job.device.get("id"))}
        with self.lock:
            jobs = [other for other in self.jobs.values() if str(other.device.get("id")) in ids]
        groups = sorted({other.spec.exclusive for other in jobs if other.spec.exclusive})
        with contextlib.ExitStack() as stack:
            for group in groups:
                stack.enter_context(self.gates.hold(group, person=True))
            paused = [watch for other in jobs for watch in self.pause_watches(other)]
            try:
                yield
            finally:
                self.resume_watches(paused)

    @contextlib.contextmanager
    def _run_hold(self, job: Job, group: str):
        """What a run holds while it talks to its device: its ``exclusive`` group (a person's press goes first), and no
        watch of the same board. WHY the watches stop here and not when the run is asked for: a press queues behind a
        routine diagnose that may take tens of seconds, and the State tab should keep moving meanwhile."""

        with self.gates.hold(group, person=True):
            paused = self.pause_watches(job)
            try:
                yield
            finally:
                self.resume_watches(paused)

    # --- the entity page: State, Configuration, image streams, watch (docs/state-config-v1.md) -------------------------------
    def _observe_states(self, job: Job, device_path: str, now: float) -> None:
        """The state records the latest apply() found: their measurements into the history (1 Hz at most)."""

        for state in job.cache.fresh_states:
            self.history.measure(absolute(state.path, device_path), state.name, state.measurements,
                                 state.received if state.received is not None else now, now, min_gap_s=STATE_HISTORY_GAP_S)
        if job.cache.fresh_states:
            with self.sample_changes:
                self.sample_revision += 1
                self.sample_changes.notify_all()

    def _frames_base(self) -> Path:
        with self._watch_lock:
            if self._frames_dir is None:
                import tempfile

                self._frames_dir, self._frames_owned = Path(tempfile.mkdtemp(prefix="io-diagnostics-frames-")), True
            self._frames_dir.mkdir(parents=True, exist_ok=True)
            return self._frames_dir

    def _watch_target(self, path: str, view: View | None = None) -> tuple[Job, str] | None:
        """``(job, path relative to its device)`` of an entity a plugin described (or its device row), else None. ``view``: the job as that view knew it, with
        no lock to wait for; without one, the job itself."""

        job = self._owning_job(path, _NoOwner, view)
        if job is None:
            return None
        if view is not None:
            device_path, known = view.layer.device_paths.get(str(job.device.get("id")), job.device_path), job.cache.entities
        else:
            with self.lock:
                device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
                known = set(job.cache.entities)
        rel = relative(path, device_path)
        return (job, rel) if rel == "" or rel in known else None

    def _watch_declared(self, job: Job) -> dict | None:
        """The plugin's describe ``watch``: ``{"period_s", "gates"}``, ``{"refused": why}`` or None (it declares none)."""

        declared = (self.describes.get(job.spec.name) or {}).get("watch")
        if not declared:
            return None
        declared = {} if declared is True else declared
        if not isinstance(declared, dict):
            return {"refused": "watch must be true or an object"}
        if declared.get("danger", "query") != "query":
            return {"refused": f"watch is query-only; this plugin declares danger {declared.get('danger')!r}"}
        period = declared.get("period_s", 1.0)
        period = float(period) if isinstance(period, (int, float)) and not isinstance(period, bool) and period > 0 else 1.0
        return {"period_s": period, "gates": [str(check) for check in declared.get("gates") or []]}

    def _watch_gate(self, watch: Watch) -> dict | None:
        return self._gate_for(watch.job, watch.declared.get("gates") or [])

    def _gate_for(self, job: Job, checks: list[str]) -> dict | None:
        """Why this plugin may not talk to its device now, in plain words, or None: a run of its exclusive group is active, or
        one of the ``checks`` says a person's tool holds the port, the camera or the rack bus lock. WAITING (the host's own
        check holds the lock for a few seconds) and "can't tell" never block. Shared by the watch and a configuration write."""

        group = job.spec.exclusive
        if group:
            run = next((run for run in self.runs.active() if run.spec.group == group), None)
            if run is not None:
                return {"reason": f"{run.spec.label} is running", "holders": []}
            if self.gates.person_waiting(group):
                return {"reason": "a utility is about to run", "holders": []}
        with self.lock:
            device_id = str(job.device.get("id"))
            device = self.layer.matchable(device_id) if device_id in self.layer.devices else dict(job.device)
        for check in checks:
            state, detail = self.os.gate(device, check)
            if state is False:
                return entity_state.holder_reason(check, detail)
        return None

    def watch_heartbeat(self, path: str) -> dict:
        """The page's heartbeat for one entity (``POST /api/v1/watch``): start its plugin's ``watch`` if it is not running,
        and keep it for ``watch_grace_s`` more. Answers the watch's state in plain words."""

        composite = self._composite_of_path(path)
        if composite is not None and composite.plugin:
            self._cascade_heartbeat(composite)
        target = self._watch_target(path)
        declared = self._watch_declared(target[0]) if target else None
        if target is None or declared is None or "refused" in declared:
            reason = (declared or {}).get("refused") or "no plugin of this entity declares watch"
            return {"path": path, "watch": {"supported": False, "state": "unsupported", "reason": reason, "holders": []}}
        job, rel = target
        key = (job.key, rel)
        with self._watch_lock:
            watch = self.watches.get(key)
            if watch is None:
                folder = self._frames_base() / re.sub(r"[^A-Za-z0-9._-]+", "-", f"{job.key[0]}-{job.key[1]}-{rel or 'device'}")
                folder.mkdir(parents=True, exist_ok=True)
                watch = self.watches[key] = Watch(job, rel, declared, folder, time.monotonic())
            watch.last_beat = time.monotonic()
            watch.declared = declared
            self._watch_thread_start()
        self._watch_step(watch)
        return {"path": path, "watch": self.watch_json(job, rel)}

    def _watch_thread_start(self) -> None:
        with self._watch_lock:
            if self._stop.is_set() or (self._watch_thread is not None and self._watch_thread.is_alive()):
                return
            self._watch_thread = threading.Thread(target=self._watch_loop, name="io-watch", daemon=True)
            self._watch_thread.start()

    def _watch_loop(self) -> None:
        while not self._stop.wait(self.watch_tick_s):
            for watch in list(self.watches.values()):
                self._watch_step(watch)

    def _watch_step(self, watch: Watch) -> None:
        """One look at one watch: past its grace it stops; a gate that says no stops (or keeps down) its process, and
        the gate clearing starts it again."""

        with self._watch_lock:
            if watch.stopped:
                return
            if time.monotonic() - watch.last_beat > self.watch_grace_s:
                self._watch_stop(watch)
                return
            if str(watch.job.device.get("bus")) == "composite":
                self._refresh_composite_values(watch.job)  # its plugin reads its children's latest State from this file
            if watch.paused:
                watch.gate = {"reason": "paused while a write to this board runs", "holders": []}
                return
            watch.gate = self._watch_gate(watch)
            if watch.gate is not None:
                if watch.follower is not None:
                    watch.follower.stop()
                    watch.follower = None
                return
            if watch.follower is None:
                job = watch.job
                with self.lock:
                    device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
                    entity = self.layer.entities.get(device_path)
                watch.plugin_blocked, watch.started = None, self.clock.now()
                frame_env = {"IO_DIAGNOSTICS_FRAME_DIR": str(watch.frame_dir)}
                watch.follower = Watcher(job.spec, job.device, device_path, watch.declared["period_s"], self.clock.now,
                                         lambda job=job, env=frame_env: {**self._job_env(job), **env, **self._watch_env()},
                                         lambda outcome, watch=watch: self._absorb_watch(watch, outcome),
                                         hardware_id=entity.hardware_id if entity else "", backoff_s=self.follow_backoff_s,
                                         path=watch.rel)
                watch.follower.start()

    def _watch_env(self) -> dict[str, str]:
        """What the app's settings tell a watch, read each time one starts: the depth colour map (Zach, 2026-10-24). A driver that
        does not draw depth ignores them; the RealSense driver honours them under record revision 0.2 (docs/state-config-v1.md)."""

        depth = self.settings.depth()
        return {"IO_DIAGNOSTICS_DEPTH_COLORMAP": depth["depth_colormap"],
                "IO_DIAGNOSTICS_DEPTH_EQUALIZE": "1" if depth["depth_equalize"] else "0",
                "IO_DIAGNOSTICS_DEPTH_MIN_M": f"{depth['depth_min_m']:g}",
                "IO_DIAGNOSTICS_DEPTH_MAX_M": f"{depth['depth_max_m']:g}"}

    def restart_watches(self) -> None:
        """Stop every running watch process and start each again at once when its page still beats: the next frame uses the
        settings now held. WHY a restart and not a message: a watch is a child process that read its environment when it started."""

        with self._watch_lock:
            running = [watch for watch in self.watches.values() if not watch.stopped]
            for watch in running:
                if watch.follower is not None:
                    watch.follower.stop()
                    watch.follower = None
        for watch in running:
            self._watch_step(watch)

    def pause_watches(self, job: Job) -> list[Watch]:
        """Stop every watch of this job's board and wait for its process to exit (``Follower.stop`` kills the group and
        waits), so a run that talks to the same device finds the port free. Returns what to give back to ``resume_watches``.

        WHY the host yields and does not make the run wait: the watch is the host's own, held only for a page that is
        looking; before this a write to a board was refused for ~10 s after the person left the State tab (the watch kept
        the port until its grace ran out), and a write made while the tab was open could never go through."""

        with self._watch_lock:
            paused = [watch for watch in self.watches.values() if watch.job.key == job.key and not watch.stopped]
            for watch in paused:
                watch.paused += 1
                if watch.follower is not None:
                    watch.follower.stop()
                    watch.follower = None
        return paused

    def resume_watches(self, paused: list[Watch]) -> None:
        """Give the board back to the watches ``pause_watches`` stopped: each starts again at once when its page still
        beats (the State tab is open), else it ends at its grace like any other."""

        with self._watch_lock:
            for watch in paused:
                watch.paused = max(0, watch.paused - 1)
        for watch in paused:
            self._watch_step(watch)

    def _watch_stop(self, watch: Watch) -> None:
        with self._watch_lock:
            watch.stopped = True
            if watch.follower is not None:
                watch.follower.stop()
                watch.follower = None
            self.watches.pop((watch.job.key, watch.rel), None)
        self.notify("watch")

    def _absorb_watch(self, watch: Watch, outcome: RunOutcome) -> None:
        """One line of a watch: its records into the job's cache like a follow line, but never into the device row's own
        plugin-run check; State into the history and the pages' event stream, at most once per ``WATCH_NOTIFY_S``."""

        if watch.stopped or not outcome.records:
            return
        job, now = watch.job, self.clock.now()
        with self.lock:
            fresh_statuses, fresh_events = job.cache.apply(outcome, received=now)
            frames = list(job.cache.fresh_frames)
            device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
            self._observe_states(job, device_path, now)
        blocked = probe_module.blocked_by(outcome.records)
        if blocked is not None:
            sentence = probe_module.blocked_sentence(blocked, 0.0)
            watch.plugin_blocked = {"reason": sentence, "holders": [{"pid": holder.pid, "program": holder.program}
                                                                    for holder in blocked.holders]}
        elif any(isinstance(record, (State, Frame)) for record in outcome.records):
            watch.plugin_blocked = None
        for frame in frames:
            self._frame_times.setdefault((job.key, frame.path, frame.stream), collections.deque(maxlen=30)).append(time.monotonic())
        observed = [rebase(status, device_path) for status in fresh_statuses]
        if observed:
            self.faults.observe(observed, now)
            self.history.observe(observed, now, source=("job", *job.key))
        if observed or fresh_events or time.monotonic() - watch.notified >= WATCH_NOTIFY_S:
            watch.notified = time.monotonic()
            self.notify("watch")

    def _fps(self, job: Job, rel: str, stream: str) -> float | None:
        times = self._frame_times.get((job.key, rel, stream))
        if not times or time.monotonic() - times[-1] > FRAME_FPS_WINDOW_S:
            return None
        recent = [at for at in times if times[-1] - at <= FRAME_FPS_WINDOW_S]
        return round((len(recent) - 1) / (recent[-1] - recent[0]), 1) if len(recent) > 1 and recent[-1] > recent[0] else None

    def watch_json(self, job: Job | None, rel: str) -> dict:
        """The watch of one entity, in plain words: ``supported`` (its plugin declares one), ``state`` (idle · starting ·
        running · paused · restarting · unsupported) and, when paused, ``reason`` and ``holders``."""

        declared = self._watch_declared(job) if job else None
        if declared is None or "refused" in declared:
            return {"supported": False, "state": "unsupported", "reason": (declared or {}).get("refused", ""), "holders": []}
        out = {"supported": True, "state": "idle", "reason": "", "holders": [], "pid": None, "since": None,
               "grace_s": self.watch_grace_s, "period_s": declared["period_s"], "gates": declared["gates"]}
        watch = self.watches.get((job.key, rel))
        if watch is None:
            return out
        out["beat_age_s"] = round(time.monotonic() - watch.last_beat, 2)
        if watch.gate is not None:
            return {**out, "state": "paused", **watch.gate}
        if watch.plugin_blocked is not None:
            return {**out, "state": "paused", **watch.plugin_blocked}
        follower = watch.follower
        if follower is None:
            return {**out, "state": "starting"}
        state = follower.state()
        mapped = {"running": "running", "restarting": "restarting"}.get(state["state"], "starting")
        return {**out, "state": mapped, "pid": state["pid"], "since": state["since"], "restarts": state["restarts"],
                "reason": (state["exit"] or "") if mapped == "restarting" else ""}

    def values_json(self, path: str, since: int = 0) -> dict | None:
        """The entity page's live numbers and images, cheap (no tree build): State groups with the points newer than
        ``since``, the declared image streams, when the host last heard from the entity, the watch. None: no such entity."""

        target = self._watch_target(path)
        now = self.clock.now()
        if target is None:
            return None
        job, rel = target
        with self.lock:
            cache = job.cache
            groups = entity_state.state_groups(cache, rel, now, since)
            streams = entity_state.stream_rows(self.describes.get(job.spec.name), cache, rel, now, lambda r, name: self._fps(job, r, name))
            heard = cache.heard(rel)
            cursor = cache.serial
        return {"path": path, "now": now, "cursor": cursor, "groups": groups, "streams": streams,
                "heard": {"received": heard, "age_s": round(now - heard, 3) if heard is not None else None},
                "watch": self.watch_json(job, rel)}

    def series_paths(self) -> list[str]:
        """The entities (absolute paths) whose plugin holds State points: where ``values_json`` can answer with data."""

        with self.lock:
            paths = set()
            for job in self.jobs.values():
                device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
                paths.update(f"{device_path}/{rel}" if rel else device_path for rel, _group, _key in list(job.cache.series))
        return sorted(paths)

    def watched_paths(self) -> set[str]:
        """The entities (absolute paths) a plugin ``watch`` is running or starting for now: the ones ``/api/v1/live`` sends samples for at the
        watch's rate instead of on the 1 Hz tick."""

        with self._watch_lock:
            watches = [watch for watch in self.watches.values() if not watch.stopped]
        with self.lock:
            return {f"{device_path}/{watch.rel}" if watch.rel else device_path
                    for watch in watches
                    for device_path in [self.layer.device_paths.get(str(watch.job.device.get("id")), watch.job.device_path)]}

    def frame_file(self, path: str, stream: str) -> tuple[bytes, dict] | None:
        """The latest image of a declared stream: ``(bytes, meta)`` or None. The file is the plugin's own, in the frame
        directory the host named; only a plain file name inside it is read."""

        target = self._watch_target(path)
        if target is None:
            return None
        job, rel = target
        with self.lock:
            frame = job.cache.frames.get((rel, stream))
        folder = (self._frames_base() / re.sub(r"[^A-Za-z0-9._-]+", "-", f"{job.key[0]}-{job.key[1]}-{rel or 'device'}")).resolve()
        if frame is None:
            return None
        found = (folder / frame.file).resolve()
        if found.parent != folder or not found.is_file():
            return None
        try:
            data = found.read_bytes()
        except OSError:
            return None
        return data, {"format": frame.format, "seq": frame.seq, "stamp": frame.stamp, "width": frame.width, "height": frame.height}

    def frame_seq(self, path: str, stream: str) -> int | None:
        """The latest frame's sequence number, for an MJPEG stream deciding whether to send again (None: no frame)."""

        target = self._watch_target(path)
        if target is None:
            return None
        job, rel = target
        with self.lock:
            frame = job.cache.frames.get((rel, stream))
        return None if frame is None else (frame.seq if frame.seq is not None else int((frame.received or 0) * 1000))

    def _display_name(self, path: str, entity) -> str:
        """What a log line calls a board: ``CAN 1 · fdcanusb FDA81D29`` (its own name, then the device it hangs on)."""

        name = (entity.name if entity else "") or path
        target = self._watch_target(path)
        device = target[0].cache.entities.get("") if target and target[1] else None
        return f"{name} · {device.name}" if device is not None and device.name else name

    def _apply_run_records(self, job: Job, records: list) -> None:
        """The ``config`` records a configuration write printed (what the board now holds) into the job's cache."""

        lines = "\n".join(json.dumps(record) for record in records if isinstance(record, dict) and record.get("record") == "config")
        if not lines:
            return
        with self.lock:
            device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
            entity = self.layer.entities.get(device_path)
            parsed, problems = _records(job.spec, lines, device_path, entity.hardware_id if entity else "")
            outcome = _outcome(job.spec, job.device, device_path, self.clock.now(), 0.0, "ok", None, parsed, problems, "", "")
            job.cache.apply(outcome, received=self.clock.now())
        self.notify("config")

    def _badge(self, path: str, view: View) -> dict | None:
        """The connection badge of the page header (realsense-viewer's "2.1"): a USB device and what hangs below it carry its link's
        ``speed_mbps`` and bcdUSB ``version`` and the PAGE names it (``web/usb.js``, the one USB-label mapping: "USB 2.1", "USB 3.2
        Gen 1", the raw speed in the tooltip); a board adds ``CAN 1 · fdcanusb FDA81D29``."""

        merged, layer = view.merged, view.layer
        device_path = tree.nearest_device(path, merged, layer)
        if device_path is None or path == tree.HOST_PATH:
            return None
        device = layer.devices.get(layer.device_of_path.get(device_path, ""), {})
        attrs = device.get("attrs") or {}
        usb = {"speed_mbps": attrs.get("speed_mbps"), "version": attrs.get("usb_version")} if device.get("bus") == "usb" and attrs.get("speed_mbps") else None
        found = re.search(r"(?:^|/)can/(\d+)$", path)
        if found and path != device_path:
            return {"kind": "can", "text": f"CAN {found.group(1)} · {merged.entities[device_path].name}", "usb": usb}
        return {"kind": "usb", "text": "", "usb": usb} if usb else None

    def _last_run(self, path: str, action: str) -> dict | None:
        run = next((run for run in self.runs.list() if run.spec.path == path and run.spec.action == action), None)
        if run is None:
            return None
        out = run.to_json()
        return {key: out[key] for key in ("id", "state", "active", "started", "started_iso", "ended", "ended_iso", "exit_code", "elapsed_s")}

    # --- composites (docs/composition-v1.md) --------------------------------------------------------------------------
    def _composition_notes(self, now: float) -> list[DiagnosticStatus]:
        """What is wrong with the composition file, as statuses (never a crash): the Computer's one ``composition`` UNKNOWN naming
        each problem (no composite loads while any exists), and a WARN on a composite whose plugin cannot serve it."""

        notes = []
        manifest = self.setup.manifest
        if manifest.composite_problems:
            problems = manifest.composite_problems
            numbered = "\n".join(f"{number}. {problem}" for number, problem in enumerate(problems, start=1))
            notes.append(DiagnosticStatus(
                level=UNKNOWN, name="composition", code="CompositionInvalid", path=tree.HOST_PATH, stamp=now, received=now,
                source=composition_module.SOURCE,
                message=f"{len(problems)} problem{'s' if len(problems) != 1 else ''} in the composite tables of "
                        f"{self.setup.path}: no composite is shown until they are fixed · {problems[0]}",
                values=(KeyValue("what_to_check", numbered), KeyValue("file", str(self.setup.path)))))
        specs = {spec.name: spec for spec in self.specs}
        for composite in self._composites:
            if not composite.plugin:
                continue
            spec, why = specs.get(composite.plugin), ""
            if spec is None:
                why = f"plugin {composite.plugin!r} is not in {self.plugins_dir}"
            elif not spec.enabled or spec.kind != "driver":
                why = f"plugin {composite.plugin!r} is disabled" if not spec.enabled else f"plugin {composite.plugin!r} is not a driver"
            elif not spec.accepts(composition_module.device_dict(composite)):
                patterns = ", ".join(match.text() for match in spec.matches) or "none"
                why = f"plugin {composite.plugin!r} does not handle kind {composite.kind!r} (its matches: {patterns})"
            if why:
                notes.append(DiagnosticStatus(
                    level=WARN, name=f"{composite.label()}: plugin", code="PluginNotUsable", path=composite.path, stamp=now, received=now,
                    source=composition_module.SOURCE, message=why,
                    values=(KeyValue("what_to_check", f"1. Declare a plugin named {composite.plugin!r} in {self.plugins_dir} with "
                                                      f"matches = [\"composite:{composite.kind}\"], or take plugin out of the composite"),)))
        return notes

    def _composition_view(self) -> composition_module.CompositeView:
        """The latest view's composites; one view is built if none has been yet (a plugin can be asked before a page is)."""

        if self._composition is None and self._composites:
            self.view()
        return self._composition or composition_module.CompositeView()

    def _child_json(self, child, cv: composition_module.CompositeView) -> dict:
        info = cv.info.get(child.path) or {} if child.path else {}
        level = cv.levels.get(child.ref) if child.kind == "composite" else info.get("level")
        return {"ref": child.ref, "id": child.ref, "kind": child.kind, "path": child.path, "name": child.name, "present": child.present,
                "level": level, "message": info.get("message", "")}

    def composite_json(self, composite, cv: composition_module.CompositeView) -> dict:
        """One composite, as ``GET /api/v1/state`` ``composition`` and ``GET /api/v1/node`` ``composite`` say it."""

        parent = next((other.path for other in cv.composites if composite.id in other.children), None)
        resolved = cv.resolved.get(composite.id, [])
        return {"id": composite.id, "path": composite.path, "kind": composite.kind, "title": composite.label(), "parent": parent,
                "plugin": composite.plugin or None, "batch": composite.batch, "source": composite.source or None,
                "expects": composite.expects, "level": cv.levels.get(composite.id),
                "children": [self._child_json(child, cv) for child in resolved],
                "members": composition_module.members(composite.id, cv.resolved),
                "leaves": len(composition_module.leaves(composite.id, cv.resolved))}

    def composition_json(self, view: View) -> dict:
        manifest = self.setup.manifest
        cv = view.composition
        return {"file": str(self.setup.path) if self.setup.path else None, "problems": list(manifest.composite_problems),
                "composites": [self.composite_json(composite, cv) for composite in composition_module.preorder(cv.composites)] if cv else []}

    def _state_of(self, path: str, now: float) -> dict:
        """A child's latest State as ``{group: {key: {"value", "unit", "stamp"}}}`` (what a composite plugin reads)."""

        target = self._watch_target(path)
        if target is None:
            return {}
        job, rel = target
        with self.lock:
            groups = entity_state.state_groups(job.cache, rel, now, job.cache.serial)
        return {group["name"]: {row["key"]: {"value": row["value"], "unit": row["unit"], "stamp": group["received"]}
                               for row in group["rows"]} for group in groups}

    def _composite_values(self, composite, cv: composition_module.CompositeView) -> Path:
        """Write the children's latest levels and State where the composite's plugin reads them (atomically, a file renamed over:
        the frames directory's precedent), and return its path."""

        now = self.clock.now()
        entries: list[dict] = []

        def walk(composite_id: str) -> None:
            for child in cv.resolved.get(composite_id, []):
                state = self._state_of(child.path, now) if child.kind == "entity" and child.present else {}
                entries.append({**self._child_json(child, cv), "state": state})
                if child.kind == "composite":
                    walk(child.ref)

        walk(composite.id)
        folder = self._frames_base() / ("composite-" + re.sub(r"[^A-Za-z0-9._-]+", "-", composite.id))
        folder.mkdir(parents=True, exist_ok=True)
        final, temporary = folder / "values.json", folder / ".values.json.tmp"
        temporary.write_text(json.dumps({"stamp": now, "children": entries}, default=str))
        os.replace(temporary, final)
        return final

    def _composite_tree(self, composite_id: str, cv: composition_module.CompositeView) -> list[dict]:
        known = composition_module.by_id(cv.composites)
        out = []
        for child in cv.resolved.get(composite_id, []):
            entry = {"ref": child.ref, "id": child.ref, "kind": child.kind, "path": child.path, "name": child.name, "present": child.present}
            if child.kind == "composite":
                entry["composite_kind"] = known[child.ref].kind
                entry["children"] = self._composite_tree(child.ref, cv)
            out.append(entry)
        return out

    def _composite_env(self, job: Job) -> dict[str, str]:
        """``IO_DIAGNOSTICS_COMPOSITE`` (the composite as the file says it, resolved now) and ``IO_DIAGNOSTICS_COMPOSITE_VALUES``
        (the file of its children's latest levels and State), for a composite plugin's call (docs/composition-v1.md section 4)."""

        self.view()  # WHY a fresh one: a plugin run is rare (a diagnose per period, a watch start), and it must see where its children are NOW
        cv = self._composition_view()
        composite = next((item for item in cv.composites if composition_module.device_id(item) == str(job.device.get("id"))), None)
        if composite is None:
            return {}
        body = {"id": composite.id, "kind": composite.kind, "title": composite.label(), "path": composite.path,
                "expects": composite.expects, "children": self._composite_tree(composite.id, cv)}
        return {"IO_DIAGNOSTICS_COMPOSITE": json.dumps(body), "IO_DIAGNOSTICS_COMPOSITE_VALUES": str(self._composite_values(composite, cv))}

    def _composite_of_path(self, path: str) -> object | None:
        cv = self._composition
        return cv.by_path.get(path) if cv else None

    def _refresh_composite_values(self, job: Job) -> None:
        cv = self._composition
        composite = next((item for item in cv.composites if composition_module.device_id(item) == str(job.device.get("id"))), None) if cv else None
        if composite is not None:
            try:
                self._composite_values(composite, cv)
            except OSError:  # the frames directory is gone (the host is stopping): the plugin keeps its last file
                pass

    def _cascade_heartbeat(self, composite) -> None:
        """A page looking at a composite keeps its children's watches (and theirs) running: their State is what the composite's
        plugin reads. WHY here and not in the page: the page knows one entity; the file knows the members."""

        cv = self._composition
        for child in (cv.resolved.get(composite.id, []) if cv else []):
            if child.present and child.path:
                self.watch_heartbeat(child.path)

    # --- composites: batch actions (docs/composition-v1.md section 6) ---------------------------------------------------
    def _leaf_actions(self, view: View):
        def actions_of(path: str) -> dict:
            entity = view.merged.entities.get(path)
            return {action.name: action for action in entity.actions} if entity is not None else {}

        return actions_of

    def _leaf_group(self, view: View):
        def group_of(path: str) -> tuple[str, str]:
            entity = view.merged.entities.get(path)
            job = self._owning_job(path, entity) if entity is not None else None
            return (job.spec.name, job.spec.exclusive) if job is not None else ("", "")

        return group_of

    def batch_plans(self, path: str, view: View) -> list:
        composite = view.composition.by_path.get(path) if view.composition else None
        if composite is None:
            return []
        return composition_module.offered(composite, view.composition.resolved, self._leaf_actions(view), self._leaf_group(view))

    def batch_actions_json(self, path: str, view: View) -> list[dict]:
        """The composite's ``all:<action>`` entries, in the shape of every action, state and reason from its leaves' own gates."""

        composite = view.composition.by_path.get(path) if view.composition else None
        out = []
        for plan in self.batch_plans(path, view):
            self._label_batch(plan, view)
            states = [(item, next((a for a in self.actions_json(item.child.path, view) if a["name"] == plan.name), None)) for item in plan.items]
            blocked = next(((item, a) for item, a in states if a and a["state"] == "blocked"), None)
            waiting = next(((item, a) for item, a in states if a and a["state"] == WAITING), None)
            if blocked:
                state, reason = "blocked", f"{blocked[0].child.name}: {blocked[1]['reason']}"
            elif waiting:
                state, reason = WAITING, f"{waiting[0].child.name}: {waiting[1]['reason']}"
            elif states and all(a and a["state"] == "nothing_to_do" for _item, a in states):
                state, reason = "nothing_to_do", states[0][1]["reason"]
            else:
                state, reason = "ready", ""
            names = ", ".join(item.who for item in plan.items)
            # the boards' own preconditions, one row per check: its sentences for the first three boards, the worst state, the first detail
            # that is not a pass (the page's tick for a moves batch quotes them)
            severity = {"fail": 3, "unknown": 2, WAITING: 1, "pass": 0}
            by_check: dict[str, dict] = {}
            for _item, found in states:
                for pre in (found or {}).get("preconditions", []):
                    row = by_check.setdefault(pre["check"], {"check": pre["check"], "texts": [], "state": "pass", "detail": "",
                                                             "resolves": pre.get("resolves", "ready")})
                    row["texts"].append(pre["text"])
                    if severity.get(pre["state"], 0) > severity[row["state"]]:
                        row["state"], row["detail"], row["resolves"] = pre["state"], pre["detail"], pre.get("resolves", row["resolves"])
            preconditions = [{"check": row["check"], "text": "; ".join(row["texts"][:3]) + ("; …" if len(row["texts"]) > 3 else ""),
                              "state": row["state"], "detail": row["detail"], "resolves": row["resolves"]} for row in by_check.values()]
            confirmations = [row["text"] for row in preconditions if row["resolves"] == "confirm"]
            groups = sorted({item.group for item in plan.items if item.group})
            queue = (f"; runs of {', '.join(groups)} go one at a time" if groups else "")
            skipped = self._skipped_text(plan)
            out.append({
                "name": composition_module.BATCH_PREFIX + plan.name, "title": plan.title,
                "description": f"{plan.items[0].action.title.rstrip('…')} on each of the {len(plan.items)} under {composite.label()}. "
                               f"One dry run lists the runs, one confirm starts them{queue}"
                               + (f". Not included: {skipped}" if skipped else ""),
                "danger": plan.danger, "scope": "composite", "kind": plan.items[0].action.kind,
                "confirm": f"{plan.title.rstrip('…')} under {composite.label()}? {len(plan.items)} runs: {names}.",
                "preconditions": preconditions, "state": state, "reason": reason, "executable": False, "owner": "io_diagnostics",
                "confirmations": confirmations, "last_run": None, "batch": {"action": plan.name, "n": len(plan.items), "groups": groups,
                                            "skipped": [{"path": child.path, "name": child.name, "why": why} for child, why in plan.skipped]}})
        return out

    @staticmethod
    def _skipped_text(plan) -> str:
        """The members a batch leaves out, in a line: their names while there are three or fewer, else how many and why."""

        if not plan.skipped:
            return ""
        if len(plan.skipped) <= 3:
            return ", ".join(f"{child.name} ({why})" for child, why in plan.skipped)
        reasons = sorted({why for _child, why in plan.skipped})
        return f"{len(plan.skipped)} members ({'; '.join(reasons)})"

    def _batch_out(self, path: str, action: str, *, dry_run: bool) -> dict:
        return {"path": path, "action": action, "node": None, "new_id": None, "dry_run": dry_run, "plugin": None, "device_id": None,
                "title": "", "danger": None, "registered_by": "composite", "argv": None, "refused": None, "outcome": "refused",
                "returncode": None, "duration_s": 0.0, "records": [], "action_result": None, "rejected": [], "stderr_tail": "",
                "message": "", "batch": None}

    def _batch_plan(self, path: str, action: str, out: dict):
        """``(plan, view, None)`` or ``(None, view, refusal)``."""

        view = self.view()
        composite = view.composition.by_path.get(path) if view.composition else None
        if composite is None:
            return None, view, {**out, "refused": "NoSuchComposite", "message": f"no composite at {path!r}"}
        name = action.removeprefix(composition_module.BATCH_PREFIX)
        plan = composition_module.plan_batch(composite, name, view.composition.resolved, self._leaf_actions(view), self._leaf_group(view))
        if plan.refused:
            return None, view, {**out, "refused": plan.refused, "message": plan.message}
        self._label_batch(plan, view)
        return plan, view, None

    def _label_batch(self, plan, view: View) -> None:
        """Name each run's board the way a person tells two apart (``CAN 1 · fdcanusb 272813EF``), not only ``CAN 1``: two adapters
        both have a CAN 1, and so do the tabs of their runs."""

        for item in plan.items:
            item.who = self._display_name(item.child.path, view.merged.entities.get(item.child.path))
            item.label = f"{item.who} · {terminal_module.plain_title(item.action.title)}"

    @staticmethod
    def _batch_summary(plan) -> dict:
        return {"action": plan.name, "title": plan.title, "n": len(plan.items), "danger": plan.danger,
                "skipped": [{"path": child.path, "name": child.name, "why": why} for child, why in plan.skipped],
                "runs": [{"order": item.order, "path": item.child.path, "label": item.label, "node": item.node, "plugin": item.plugin,
                          "group": item.group, "starts": item.starts, "after": item.after, "danger": item.action.danger}
                         for item in plan.items]}

    def batch_dry_run(self, path: str, action: str) -> dict:
        """One dry run for ``all:<action>`` on a composite: every leaf's own dry run (so each plugin's preconditions and token are
        honoured), one answer. ``plugin_tokens`` (leaf path -> that leaf's plugin token) is for the server's gate, never for a page."""

        out = self._batch_out(path, action, dry_run=True)
        plan, _view, refusal = self._batch_plan(path, action, out)
        if refusal is not None:
            return refusal
        summary = self._batch_summary(plan)
        tokens: dict[str, str | None] = {}
        for item, run in zip(plan.items, summary["runs"]):
            answer = self.act(item.child.path, plan.name, node=item.node, dry_run=True, human=False)
            if answer.get("refused") is not None or answer.get("outcome") != "ok":
                why = answer.get("message") or answer.get("refused") or answer.get("outcome")
                return {**out, "refused": answer.get("refused") or "DryRunFailed", "message": f"{item.child.name}: {why}",
                        "title": plan.title, "batch": {**summary, "failed": item.child.path}}
            result = answer.get("action_result") or {}
            tokens[item.child.path] = result.get("token") or result.get("confirm_token") or None
            run.update(argv=answer.get("argv"), confirm=result.get("confirm") or "", message=result.get("message") or "")
        names = ", ".join(item.who for item in plan.items)
        confirm = f"{plan.title.rstrip('…')}? {len(plan.items)} runs: {names}."
        return {**out, "outcome": "ok", "title": plan.title, "danger": plan.danger, "batch": summary, "plugin_tokens": tokens,
                "action_result": {"action": composition_module.BATCH_PREFIX + plan.name, "title": plan.title, "dry_run": True,
                                  "danger": plan.danger, "command_kind": "batch", "confirm_required": plan.danger != "query",
                                  "confirm": confirm, "token": "batch", "message": f"{len(plan.items)} runs, one confirm",
                                  "steps": [{"purpose": run["label"], "runs": "subprocess", "argv": run["argv"]} for run in summary["runs"]]}}

    def start_batch(self, path: str, action: str, plugin_tokens: dict, *, interactive: bool = True):
        """The confirmed batch: N runs created at once (all ``queued``), started in the plan's order, runs of one ``exclusive`` group
        one after another. Returns ``(runs, out)``; ``runs`` is empty when it is refused (``out["refused"]``)."""

        out = self._batch_out(path, action, dry_run=False)
        plan, _view, refusal = self._batch_plan(path, action, out)
        if refusal is not None:
            return [], refusal
        prepared = []
        for item in plan.items:
            answer, run_plan = self._plan_act(item.child.path, plan.name, node=item.node, confirm=plugin_tokens.get(item.child.path),
                                              dry_run=False, human=True, new_id=None)
            if run_plan is None:
                return [], {**out, "refused": answer.get("refused"), "message": f"{item.child.name}: {answer.get('message')}",
                            "batch": self._batch_summary(plan)}
            prepared.append((item, answer, run_plan))
        runs = [(item, self._create_run(run_plan, answer, interactive=interactive, label=item.label)) for item, answer, run_plan in prepared]
        by_group: dict[str, list] = {}
        for item, run in runs:
            by_group.setdefault(item.group, []).append(run)

        def one_at_a_time(group_runs: list) -> None:
            for run in group_runs:
                run.launch()
                run.wait()

        for group, group_runs in by_group.items():
            if group:
                threading.Thread(target=one_at_a_time, args=(group_runs,), name=f"batch-{group}", daemon=True).start()
            else:
                for run in group_runs:
                    run.launch()
        return [run for _item, run in runs], {**out, "outcome": "ok", "title": plan.title, "danger": plan.danger,
                                              "batch": self._batch_summary(plan)}

    # --- runs (docs/runs-v1.md) ---------------------------------------------------------------------------------
    def runs_json(self) -> dict:
        everything = self.runs.json()
        return {"runs": everything["items"], "active": everything["active"], "total": everything["total"],
                "limits": self.runs.limits()}

    def run_json(self, run_id: str) -> dict | None:
        run = self.runs.get(run_id)
        return run.to_json(detail=True) if run else None

    # --- activity, records, plugins -----------------------------------------------------------------------------
    def activity(self, limit: int = 200, types: tuple[str, ...] | None = None, *, fold: bool = False, channel: str = "activity",
                 path: str = "", scope: str = "children", severity: str = "debug") -> dict:
        """``types``: only events of these types (``activity.TYPES``); raises ValueError for one it does not know.

        ``fold`` is the Activity log's model (``logview``): the events as rows (a fault folded onto its cause, a run as
        one row, repeats counted), by ``channel`` (``activity`` coded events, ``raw`` the entity's kernel lines, ``all``),
        for the entity ``path`` and its ``scope`` (``children`` / ``self`` / ``host``), at or above ``severity``."""

        unknown = sorted(set(types or ()) - set(activity_module.TYPES))
        if unknown:
            raise ValueError(f"unknown event type {', '.join(map(repr, unknown))}: one of {', '.join(activity_module.TYPES)}")
        view = self.read_view()
        if fold:
            return self.log_json(view, limit, types, channel=channel, path=path, scope=scope, severity=severity)
        return {"clock": self.clock_json(view.now), "events": self.events_json(view, limit, types),
                "faults": [self.fault_json(fault, view) for fault in self.faults.all()],
                "link_health": activity_module.link_health(view.rows, view.layer, self.utc_offset_s)}

    def log_json(self, view: View, limit: int, types, *, channel: str, path: str, scope: str, severity: str) -> dict:
        for value, allowed, what in ((channel, logview_module.CHANNELS, "channel"), (scope, logview_module.SCOPES, "scope"),
                                     (severity, logview_module.FLOORS, "severity")):
            if value not in allowed:
                raise ValueError(f"unknown {what} {value!r}: one of {', '.join(allowed)}")
        if path and path not in view.merged.entities:
            raise ValueError(f"no entity {path!r}")
        entity = view.merged.entities.get(path)
        hardware_id = entity.hardware_id if entity else ""
        names = self._names(view)
        out: list[dict] = []
        total = 0
        if channel in ("activity", "all"):
            # WHY once per view: a Log tab, the Activity view and each open /events stream all read the same rows
            folded = view.memo.get("fold")
            if folded is None:
                folded = view.memo["fold"] = logview_module.fold(view.events[:FOLD_EVENTS], active_runs=view.active_runs)
            chosen = logview_module.select(folded, entity=path, entity_hardware_id=hardware_id, scope=scope, floor=severity, types=types)
            total += len(chosen)
            out += [logview_module.row_json(row, self.utc_offset_s, names) for row in chosen[:limit]]
        if channel in ("raw", "all"):
            raw, count = self._raw_rows(view, path, hardware_id, scope, severity, limit, names)
            total += count
            out += raw
        out.sort(key=lambda row: ((row["last_stamp"] if row["last_stamp"] is not None else row["stamp"]) or 0.0, row["seq"] or 0), reverse=True)
        return {"clock": self.clock_json(view.now), "events": out[:limit],
                "log": {"channel": channel, "path": path, "name": names.get(path, ""), "scope": scope, "severity": severity,
                        "matching": total, "shown": min(len(out), limit), "persisted": self.eventlog.directory is not None,
                        "note": self.eventlog.note}}

    def _raw_rows(self, view: View, path: str, hardware_id: str, scope: str, floor: str, limit: int,
                  names: dict[str, str]) -> tuple[list[dict], int]:
        """The Raw channel: this boot's kernel lines about the entity (a USB device's lines are its port's), newest first."""

        layer = view.layer
        out, count = [], 0
        for line in reversed(self.raw_kernel.lines(self.os.boots())):
            if not logview_module.severity_at_least(line.severity, floor):
                continue
            device_id = layer.port_devices.get(line.port) if line.port else None
            where = (layer.device_paths.get(device_id) if device_id else None) or (layer.port_paths.get(line.port) if line.port else None) or tree.HOST_PATH
            if not logview_module.in_scope(where, "", path, hardware_id, scope):
                continue
            count += 1
            if len(out) < limit:
                out.append(logview_module.raw_json(line, where, self.utc_offset_s, names))
        return out, count

    def records(self) -> dict:
        view = self.read_view()
        latest = {}
        for status in view.merged.statuses:
            latest[(status.path, status.name)] = status
        return {"clock": self.clock_json(view.now), "entities": [to_dict(entity) for entity in view.merged.entities.values()],
                "statuses": [to_dict(status) for status in latest.values()],
                "events": [to_dict(event) for event in view.events], "faults": self.faults.records()}

    def _check_run(self, name: str) -> dict | None:
        """A check's last run in a plugin run's shape (``RunOutcome.summary``)."""

        status = self.check_results.get(name)
        if status is None:
            return None
        values, measured = dict(status.values), {m.key: m.value for m in status.measurements}
        outcome = {"CheckTimeout": "timeout", "CheckNotStarted": "not_started"}.get(status.code, "ok")
        exit_code = values.get("exit_code", "none")
        return {"started": status.stamp, "duration_s": measured.get("check duration"), "outcome": outcome,
                "returncode": int(exit_code) if exit_code.lstrip("-").isdigit() else None, "accepted": 1, "rejected": 0,
                "problems": [], "rejected_lines": [], "message": status.message}

    def plugins(self) -> dict:
        with self.lock:
            jobs = list(self.jobs.values())
        attached = {status.name: status.path for status in (self.read_view().checks if self.check_jobs else [])}
        out = []
        for spec in self.specs:
            out.append({"name": spec.name, "file": str(spec.file), "command": list(spec.command),
                        "diagnose_args": list(spec.diagnose_args), "cwd": str(spec.cwd),
                        "timeout_s": spec.timeout_s, "act_timeout_s": spec.act_timeout_s, "period_s": spec.period_s,
                        "enabled": spec.enabled, "replay": spec.replay, "exclusive": spec.exclusive,
                        "kind": spec.kind, "attach": spec.attach or None,
                        "matches": [match.text() for match in spec.matches], "description": spec.description,
                        "describe": self.describes.get(spec.name),
                        "jobs": [{"device_id": str(job.device.get("id")), "device_path": job.device_path,
                                  "last_run": job.last.summary() if job.last else None,
                                  "next_due_s": (None if job.key in self.followers else
                                                 round(max(job.next_due - time.monotonic(), 0.0), 1) if self._scheduler else None),
                                  "follow": self.followers[job.key].state() if job.key in self.followers else None}
                                 for job in jobs if job.spec.name == spec.name]
                        + [{"device_id": job.device["id"], "device_path": attached.get(spec.name),
                            "last_run": self._check_run(spec.name),
                            "next_due_s": round(max(job.next_due - time.monotonic(), 0.0), 1) if self._scheduler else None,
                            "follow": None}
                           for job in self.check_jobs.values() if job.spec.name == spec.name]})
        return {"directory": str(self.plugins_dir), "problems": list(self.plugin_problems), "plugins": out}

    def health(self) -> dict:
        return {"ok": True, "api": API, "mode": self.clock.mode, "uptime_s": round(time.monotonic() - self.started, 1)}

    # --- history and settings (docs/api-v1.md "History", "Settings") ------------------------------------------------
    def _window(self, window) -> float:
        """A request's window: the app's, or the one asked for (within 1 min .. the history's retention)."""

        if window in (None, ""):
            return self.settings.history_window_s
        seconds = settings_module.parse_duration(window)
        if not history_module.MIN_WINDOW_S <= seconds <= self.history.retention_s:
            raise settings_module.SettingsError(f"window is {history_module.MIN_WINDOW_S:g} s .. "
                                                f"{self.history.retention_s:g} s (what the history keeps), got {seconds:g} s")
        return seconds

    def history_json(self, path: str, status: str | None = None, window=None) -> dict | None:
        """Per-status level spans and measurement series of one entity over the window; None: no such entity."""

        view = self.read_view()
        keys = [key for key in self.history.keys(path, health=False) if status in (None, "", key[1])]
        if path not in view.merged.entities and not keys:
            return None
        seconds = self._window(window)
        start, end = view.now - seconds, view.now
        statuses = []
        for key in keys:
            period = self.history.period(key)
            statuses.append({"name": key[1], "state": self.history.is_state(key), "expected_period_s": period,
                             "stale_after_s": history_module.stale_after(period),
                             "levels": self.history.timeline(key, start, end),
                             "measurements": self.history.series(key, start, end)})
        entity = view.merged.entities.get(path)
        return {"path": path, "name": entity.name if entity else path, "window_s": seconds, "from": start, "to": end,
                "statuses": statuses}

    def bar_json(self, path: str, bins: int = 60, window=None) -> dict | None:
        """The health bar of one entity and of each child row: per bin, the worst level any of the row's statuses held."""

        view = self.read_view()
        if path not in view.merged.entities:
            return None
        seconds = self._window(window)
        bins = max(1, min(int(bins), history_module.MAX_BINS))
        start, end = view.now - seconds, view.now
        rows = [self._bar_row(view, row, depth, start, end, bins, self.history.timeline)
                for depth, row in [(0, path), *((1, child) for child in view.merged.children.get(path, []))]]
        return {"path": path, "window_s": seconds, "from": start, "to": end, "bins": bins, "bin_s": seconds / bins,
                "rows": rows}

    def _bar_row(self, view: View, row: str, depth: int, start: float, end: float, bins: int, timeline_of) -> dict:
        """One row of a health bar: the worst level any of the row's statuses held per bin. The one place a row's bar is made, for
        ``/history/bar`` (an entity and its children) and ``/history/tree`` (every row)."""

        # WHY the row's own statuses when it has any: the bar says what THIS black box reported (its row's level);
        # a pure grouping row, like its level, shows what everything below reported
        keys = self.history.keys(row)
        basis = "own" if keys else "below"
        if not keys:
            keys = self.history.keys(row, below=True)
        if not keys:
            basis = "none"
        timelines = [timeline_of(key, start, end) for key in keys]
        return {"path": row, "name": view.merged.entities[row].name, "depth": depth, "basis": basis,
                "statuses": len(keys), "bar": history_module.bar(timelines, start, end, bins)}

    def tree_bar_json(self, bins: int = 60, window=None) -> dict:
        """The health bar of EVERY row of the tree, in tree order (a parent before its children), over one window: what ``/history/bar`` answers
        for each row, in one answer. ``depth`` is the row's depth in the tree (the host row is 0), as in ``/state``.

        WHY one answer: the page's tree rewinds to before it opened, and a request per row is what this replaces. A group's bar reads every
        status below it, so a timeline is made once per status and shared by every row that reads it."""

        view = self.read_view()
        seconds = self._window(window)
        bins = max(1, min(int(bins), history_module.MAX_BINS))
        start, end = view.now - seconds, view.now
        made: dict[tuple[str, str], list[dict]] = {}

        def timeline_of(key: tuple[str, str], low: float, high: float) -> list[dict]:
            if key not in made:
                made[key] = self.history.timeline(key, low, high)
            return made[key]

        rows: list[dict] = []
        stack = [tree.HOST_PATH]
        while stack:
            row = stack.pop()
            rows.append(self._bar_row(view, row, view.rows.depth.get(row, 0), start, end, bins, timeline_of))
            stack.extend(reversed(view.merged.children.get(row, [])))
        return {"path": tree.HOST_PATH, "window_s": seconds, "from": start, "to": end, "bins": bins, "bin_s": seconds / bins,
                "rows": rows}

    def checks_json(self) -> dict:
        """The latest checks (diagnostic statuses) of every entity that has any, keyed by entity path, each in the shape ``/node`` gives its
        ``checks`` (``check_json``). WHY: a Diagnostics table across a rack is one request, not one ``/node`` per entity."""

        view = self.read_view()
        return {"clock": self.clock_json(view.now),
                "checks": {path: [self.check_json(status, view.now) for status in own]
                           for path, own in view.rows.own.items() if own and path in view.merged.entities}}

    # --- the stock diagnostics' drill-in (docs/api-v1.md "Stock diagnostics") -----------------------------------------------------
    def diagnostics_json(self) -> dict:
        """``GET /api/v1/diagnostics``: every diagnostic in the registry (stock and custom), each with its config dataclass as a schema."""

        runner = self.stock_diagnostics
        runner.reload_if_changed()
        return {"clock": self.clock_json(self.clock.now()), **runner.about(), "diagnostics": stock_diagnostics_module.registry_json()}

    def diagnostics_config_json(self, path: str) -> dict | None:
        """``GET /api/v1/diagnostics/config?path=``: the diagnostics configured on one entity, each field's effective value and where it
        came from (default / toml / edited), and the latest status of each. None: no such entity."""

        view = self.read_view()
        entity = view.merged.entities.get(path)
        if entity is None:
            return None
        runner = self.stock_diagnostics
        runner.reload_if_changed()
        model_id, composite_kind = _diagnostics_match(entity)
        out = []
        for binding in runner.bindings_for(path, model_id, composite_kind=composite_kind):
            status = next((found for (where, _name), found in {**self.stock_results, **self.stock_quiet}.items()
                           if where == path and stock_diagnostics_module.binding_of(found) == binding.id), None)
            out.append({**runner.binding_json(binding),
                        "status": self.check_json(status, view.now) if status is not None else None,
                        "window": stock_diagnostics_module.window_of(status) if status is not None else None})
        return {"clock": self.clock_json(view.now), "path": path, "model_id": model_id,
                "parts": list(runner.parts_of(path, model_id, composite_kind)), **runner.about(), "diagnostics": out}

    def set_diagnostic_config(self, body: dict) -> tuple[dict, int]:
        """``POST /api/v1/diagnostics/config`` ``{path, id, field, value}`` (or ``reset: true``): one value of a diagnostic configured on
        the entity, validated against its dataclass field and the Limits ordering, then written to diagnostics.toml atomically."""

        path, binding_id, field_name = body.get("path"), body.get("id"), body.get("field")
        reset = body.get("reset") is True
        if not all(isinstance(item, str) and item for item in (path, binding_id, field_name)) or ("value" not in body and not reset):
            return {"refused": "BadRequest", "error": "path, id and field are strings; value is the new value (or reset: true)"}, 400
        view = self.view()
        entity = view.merged.entities.get(path)
        if entity is None:
            return {"refused": "NoSuchEntity", "error": f"no entity {path!r}"}, 404
        runner = self.stock_diagnostics
        runner.reload_if_changed()
        model_id, composite_kind = _diagnostics_match(entity)
        if binding_id not in {binding.id for binding in runner.bindings_for(path, model_id, composite_kind=composite_kind)}:
            return {"state": "rejected", "ok": False, "refused": "NoSuchDiagnostic", "path": path, "id": binding_id, "field": field_name,
                    "message": f"{binding_id!r} is not configured on {entity.name or path}"}, 200
        answer = runner.set_field(binding_id, field_name, body.get("value"), reset=reset)
        # WHY 200 for a rejection: the host answered the question ("not a number", "warn_high above error_high"), as the config writes do
        return {**answer, "ok": answer["state"] == "accepted", "path": path}, 200

    def settings_json(self) -> dict:
        return self.settings.json()

    def set_settings(self, body: dict) -> dict:
        """The page's View options: ``{"history_window_s": 600}`` (seconds, or "10m"), its Visibility choices:
        ``{"visibility": {"<row id>": "hide" | "show" | "ack" | null}}``, the chart view c4's pinned board:
        ``{"dashboard": [tiles]}``, and the depth colour map (``depth_colormap``, ``depth_equalize``, ``depth_min_m``,
        ``depth_max_m``; a change restarts the running watches). Raises SettingsError."""

        unknown = sorted(set(body) - {"history_window_s", "visibility", "dashboard", *settings_module.DEPTH_KEYS})
        if unknown:
            raise settings_module.SettingsError(f"no setting {', '.join(map(repr, unknown))}: only history_window_s, visibility, dashboard and "
                                                + ", ".join(settings_module.DEPTH_KEYS))
        if "dashboard" in body:
            settings_module.check_dashboard(body["dashboard"])  # before anything is applied: a refused body changes nothing
        if "history_window_s" in body:
            settings_module.check_window(settings_module.parse_duration(body["history_window_s"]))
        depth = {key: body[key] for key in settings_module.DEPTH_KEYS if key in body}
        if depth and self.settings.set_depth(depth):
            self.restart_watches()
        if "visibility" in body:
            self.settings.set_visibility(body["visibility"])
        if "history_window_s" in body:
            self.settings.set_history_window(body["history_window_s"])
            self.history.window_s = self.settings.history_window_s
        if "dashboard" in body:
            self.settings.set_dashboard(body["dashboard"])
        return self.settings_json()


# ----- construction ---------------------------------------------------------------------------------------------
def fixture_info(document: dict, frame: dict) -> dict:
    return {"name": frame["name"], "label": frame["label"], "story": frame["story"], "clock": frame["clock"],
            "frames": [{"name": other["name"], "label": other["label"]} for other in document["frames"].values()]}


def make_host(*, fixture: str | None = None, plugins_dir: Path | None = None, once: bool = False, at_s: float = 0.0,
              offset_s: float = 0.0, enable: tuple[str, ...] = (), machine: Path | None = None,
              expected: Path | None = None, history_window_s: float | None = None, restore_later: bool = False) -> Host:
    """Live: ``plugins.d`` on this machine's clock. Fixture: the recorded frame's OS side, its clock, and the fixture
    plugin directory (``tests/fixtures/plugins``), whose plugin learns the frame from ``IO_DIAGNOSTICS_FIXTURE``.
    Machine: a fake machine's skeleton (``MachineOs``) on this clock. ``enable`` turns on plugins their declaration
    disables, for this host only. ``expected``: the expected-setup file; live mode defaults to
    ``~/.config/io-diagnostics/expected.toml``, a fixture or a fake machine reads one only when it is named.
    ``history_window_s``: this run's history window (``serve --history-window``); every mode reads and writes the
    person's ``settings.toml`` (an app-wide preference, not a record of this machine). ``restore_later``: the service's ``start`` reads
    the event log, not this constructor, so the server can answer first (cli.py)."""

    settings = {"settings_file": expected_module.default_config_dir() / settings_module.SETTINGS_FILE,
                "history_window_s": history_window_s, "restore_later": restore_later}
    # WHY runs are recorded live, and in a replay only when the state dir is named: a replayed frame's runs must never
    # land in the person's own run history (docs/runs-v1.md section 4)
    replay_runs = default_state_dir() / "runs" if os.environ.get("IO_DIAGNOSTICS_STATE_DIR") else None
    # the same rule for the event log (docs/activity-log-v1.md): live it is on disk for a service, never for a one-shot ``once``
    replay_log = default_state_dir() / "log" if os.environ.get("IO_DIAGNOSTICS_STATE_DIR") else None
    replay_memory = {"history_file": default_state_dir() / HISTORY_FILE, "describes_file": default_state_dir() / DESCRIBES_FILE} \
        if os.environ.get("IO_DIAGNOSTICS_STATE_DIR") and not once else {}
    if machine is not None:
        if fixture is not None:
            raise SystemExit("--machine and --fixture are two different OS sides: pick one")
        clock = FixedClock(time.time()) if once else Clock()
        return Host(plugins_dir=plugins_dir or PLUGINS_DIR, clock=clock, os_source=MachineOs(machine), enable=enable,
                    expected_file=expected, runs_dir=replay_runs, event_log_dir=replay_log, **replay_memory, **settings)
    if fixture is None:
        # WHY live only: the remembered devices are this machine's; a fixture or a fake machine must never add to them
        clock = FixedClock(time.time()) if once else Clock()
        return Host(plugins_dir=plugins_dir or PLUGINS_DIR, clock=clock, os_source=LiveOs(), enable=enable,
                    state_file=default_state_dir() / SEEN_FILE, runs_dir=default_state_dir() / "runs",
                    event_log_dir=None if once else default_state_dir() / "log",
                    ledger_file=default_state_dir() / roster_module.LEDGER_FILE,
                    history_file=None if once else default_state_dir() / HISTORY_FILE,
                    describes_file=None if once else default_state_dir() / DESCRIBES_FILE,
                    expected_file=expected or expected_module.default_config_dir() / expected_module.EXPECTED_FILE,
                    **settings)
    document, frame = load_fixture(fixture)
    offset = frame.get("utc_offset_s")
    clock = FixedClock(frame["clock"] + at_s, offset, "fixture") if once else ReplayClock(frame["clock"], offset, offset_s)
    # WHY no default manifest here: a recorded frame is replayed as it was; the person's rig file would add rows
    # the recording never had (a test or a screenshot names its own file with --expected). The same for the stock diagnostics'
    # default set: a replay runs only what <config dir>/diagnostics.toml names (stock_diagnostics.py)
    return Host(plugins_dir=plugins_dir or FIXTURE_PLUGINS_DIR, clock=clock, os_source=FixtureOs(frame),
                fixture=fixture_info(document, frame), enable=enable, expected_file=expected, stock_diagnostics_defaults=False,
                plugin_env={"IO_DIAGNOSTICS_FIXTURE": frame["name"], "IO_DIAGNOSTICS_FIXTURE_FILE": str(FIXTURE_FILE)},
                runs_dir=replay_runs, event_log_dir=replay_log, **replay_memory, **settings)


__all__ = ["Clock", "ReplayClock", "FixedClock", "LiveOs", "FixtureOs", "MachineOs", "Host", "View", "make_host",
           "load_fixture", "resolve_fixture"]
