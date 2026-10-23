"""The host: one clock, one OS source, the plugin jobs, the fault memory, and every API response (docs/api-v1.md).

Live and fixture mode differ ONLY in their ``Clock`` and ``OsSource``; plugins run through the same runner either
way (the fixture's driver side is a real plugin subprocess, tests/fixtures/plugins/v6_scenario.py), so a fixture
replay exercises the code the rack will run.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
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
from io_diagnostics import expected as expected_module
from io_diagnostics import history as history_module
from io_diagnostics import probe as probe_module
from io_diagnostics import settings as settings_module
from io_diagnostics import tree
from io_diagnostics.faults import Faults
from io_diagnostics.plugins import load_plugins, match_devices
from io_diagnostics.runner import (RUNNER_SOURCE, RUNNING, Follower, Gates, Job, RunOutcome, Scheduler, absolute, act_argv,
                                   describe, follow_period, follow_status, host_status, rebase, run_act, run_check,
                                   run_plugin)

from contract.record import (OK, RANK, RECORD_VERSION, SCHEMA, STALE, STALE_FACTOR, STALE_FLOOR_S, UNKNOWN, WARN, Action,
                             DiagnosticStatus, Entity, Event, KeyValue, Measurement, effective_level, from_dict, to_dict)

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
RECENT = 6


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

    def __init__(self, skeleton_ttl_s: float = 2.0):
        from io_facts import udev

        self.hostname = socket.gethostname()
        self.kernel_log = activity_module.LiveKernelLog()
        self.udev_command = list(udev.MONITOR)  # the host listens to udev only for an OS source that has this
        self._skeleton_ttl_s = skeleton_ttl_s
        self._skeleton_at = -1e18
        self._skeleton: tuple[list[dict], list[dict]] = ([], [])
        self._holders = None  # (port_holders Imported, camera_holders Imported), imported once, lazily
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

    def gate(self, device: dict | None, check: str) -> tuple[bool | None, str]:
        """Passive precondition checks: who holds the port / camera, who holds the rack bus lock. Never takes it."""

        key = (str((device or {}).get("id")), check)
        cached = self._gate_cache.get(key)
        if cached and time.monotonic() - cached[0] < GATE_CACHE_S:
            return cached[1]
        result = self._gate(device or {}, check)
        self._gate_cache[key] = (time.monotonic(), result)
        return result

    def _gate(self, device: dict, check: str) -> tuple[bool | None, str]:
        from io_facts import gate, lock, reuse

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
        if self._holders is None:
            self._holders = (reuse.port_holders_function(), reuse.camera_holders_function())
        port_holders, camera_holders = self._holders
        if check == "port_free":
            nodes = list(attrs.get("by_id") or []) or nodes_of("tty", "ttys")
            if not nodes:
                return None, "no serial port node for this device"
            if port_holders.function is None:
                return None, f"port holder check {port_holders.source}"
            decision = gate.decide(nodes, holders_fn=port_holders.function, uses_rack_lock=False)
        elif check == "camera_free":
            if camera_holders.function is None:
                return None, f"camera holder check {camera_holders.source}"
            video = [node for node in nodes_of("v4l", "video_nodes") if node.startswith("/dev/video")]
            decision = gate.decide(video, camera=True, camera_holders_fn=camera_holders.function, uses_rack_lock=False)
        else:
            return None, f"{check}: not a check the host evaluates"
        if decision.state == "free":
            return True, "no holder (find_port_holders, live)" if check == "port_free" else "no other process holds the camera"
        if decision.state == "blocked":
            return False, decision.reason.replace("open by", "held by")
        return None, decision.reason


def bus_lock_state(holders) -> tuple[bool | str, str]:
    """The rack bus lock is held: by one of THIS host's own running checks (``WAITING``: it frees in a few seconds,
    the host's own jobs take turns), or by anyone else (``False``: blocked, naming the pid and its command line).

    WHY not "blocked" for our own: the lock is the host's own diagnose doing its job; calling it an experiment, in a
    blocking colour, sent people looking for a process that was the dashboard itself (round 3 brief item 10)."""

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
EXPECTED_PATH = f"{tree.HOST_PATH}/expected"
EXPECTED_SOURCE = "io_diagnostics.expected"
# WHY a group of its own, collapsed and never above OK: a device only SEEN (a plugin diagnosed it once) is not expected.
# FDA81D29 was plugged for 15 s on 2026-10-23 and its unplug confirmed an ExpectedAbsent WARN from seen.json alone
# (Zach: a seen-only device must not warn when it goes absent). Its unplug is an activity event; this row is a memory.
RECENT_PATH = tree.RECENT_PATH
RECENTLY_SEEN_S = 86400.0  # a seen-only device gets its info row for a day after it was last seen, then none


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


class _Blank(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class Host:
    def __init__(self, *, plugins_dir: Path = PLUGINS_DIR, clock: Clock | None = None, os_source=None,
                 fixture: dict | None = None, plugin_env: dict[str, str] | None = None, enable: tuple[str, ...] = (),
                 state_file: Path | None = None, expected_file: Path | None = None, settings_file: Path | None = None,
                 history_window_s: float | None = None, follow_backoff_s: tuple[float, float] | None = None,
                 recheck_poll_s: float | None = None, recheck_settle_s: float | None = None,
                 recheck_backoff_s: float | None = None):
        self.plugins_dir = Path(plugins_dir)
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
        # the person's rig manifest (docs/expected-setup.md); None = nothing expected and no edits (fixture / machine)
        self.setup = expected_module.ExpectedSetup(expected_file)
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
        # every change a page could see (a refresh, a udev event, a plugin's records): /api/v1/events waits on it
        self.changes = threading.Condition()
        self.revision = 0
        self.change_reason = ""
        self._wake = threading.Event()
        self.faults = Faults()
        self.describes: dict[str, dict | None] = {spec.name: None for spec in self.specs}
        self.url = ""
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self._last_os_level: dict[tuple[str, str], tuple[str, str]] = {}
        self._described: set[str] = set()
        self._scheduler: Scheduler | None = None
        self._stop = threading.Event()
        self._udev = None

    @property
    def utc_offset_s(self) -> int | None:
        return self.clock.utc_offset_s

    # --- plugins that declare their matches in describe ------------------------------------------------------------
    def adopt_described_matches(self) -> None:
        """Every enabled plugin's ``describe``, once, before the first match: a declaration without ``matches`` takes
        them from it (docs/build-plan-v1.md: describe returns matches), and its ``expected`` devices (record v0.1)
        feed the absent rows. describe must be passive; it runs bounded by the plugin's timeout, in parallel, never
        more than once per plugin."""

        from io_diagnostics.plugins import PluginConfigError, parse_match

        pending = [spec for spec in self.specs if spec.enabled and spec.kind == "driver" and spec.name not in self._described
                   and self.describes.get(spec.name) is None]
        results: dict[str, dict] = {}

        def ask(spec) -> None:
            results[spec.name] = describe(spec, timeout_s=min(spec.timeout_s, 30.0), extra_env=self._job_env())

        threads = [threading.Thread(target=ask, args=(spec,), daemon=True) for spec in pending]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for spec in pending:
            self._described.add(spec.name)
            answer = results.get(spec.name) or {"error": "describe did not run"}
            self.describes[spec.name] = answer
            if spec.matches:
                continue  # its declaration's matches win; describe was asked for its expected devices
            matches = []
            for item in answer.get("matches") or []:
                try:
                    matches.append(parse_match(item))
                except PluginConfigError as error:
                    self.plugin_problems.append(f"{spec.name}: describe {error}")
            if answer.get("error") or not matches:
                self.plugin_problems.append(f"{spec.name}: no matches in its declaration or its describe"
                                            + (f" ({answer['error']})" if answer.get("error") else ""))
                continue
            self.specs = [dataclasses.replace(spec, matches=tuple(matches)) if other is spec else other for other in self.specs]

    # --- refresh: OS skeleton + kernel log -> layer, jobs, OS checks ----------------------------------------------
    def refresh(self) -> None:
        self.adopt_described_matches()
        self.setup.reload_if_changed()  # a hand edit of expected.toml shows within one refresh
        now = self.clock.now()
        devices, sources = self.os.skeleton()
        boots = self.os.boots()
        usb = activity_module.usb_activity(boots, now, self.utc_offset_s)
        layer = tree.build_os_layer(self.os.hostname, devices, extra_ports=frozenset(usb.recent_ports))
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
        with self.lock:
            wanted = {}
            for spec, device in pairs:
                key = (spec.name, str(device.get("id")))
                job = self.jobs.get(key) or Job(spec, device, layer.device_paths[str(device.get("id"))])
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
        """One row (entity + WARN status) per expected USB device that is not on USB now, under ``host/expected``,
        saying in plain words what the kernel saw. WHY warn, not error: absent is not broken. A device that was only
        seen gets an OK row under the collapsed ``host/recently-seen`` instead, for ``RECENTLY_SEEN_S``."""

        present = {ids for entity in layer.entities.values() if (ids := activity_module.usb_id(entity.hardware_id))}
        note = str(getattr(getattr(self.os, "kernel_log", None), "note", "") or "")
        unreadable = note if note.startswith("journalctl unavailable") else ""
        statuses = []
        for hardware_id, expected in sorted(self.expected_devices().items()):
            ids = activity_module.usb_id(hardware_id)
            if ids is None or ids in present:  # v0.1: only USB ids (usb:<vid>:<pid>:<serial>) are checked here
                continue
            if not expected["expected"]:
                statuses += self._recent_row(layer, hardware_id, expected, boots, now, unreadable)
                continue
            if EXPECTED_PATH not in layer.entities:
                layer.entities[EXPECTED_PATH] = Entity(EXPECTED_PATH, "Expected, not connected", "group",
                                                       via=tree.HOST_PATH, owner="io_diagnostics")
                layer.icons[EXPECTED_PATH] = "group"
            path = f"{EXPECTED_PATH}/{tree.slug(hardware_id)}"
            layer.entities[path] = Entity(path, expected["name"], "device", via=EXPECTED_PATH, hardware_id=hardware_id,
                                          owner=expected["owner"])
            layer.icons[path] = "usb"
            evidence = activity_module.kernel_evidence(boots, hardware_id, now, self.utc_offset_s, unreadable)
            seen = _seen_text(expected["last_seen"], self.utc_offset_s)
            said = f"expected (last seen {seen}, {expected['source']})" if seen else f"expected ({expected['source']})"
            message = f"{said} · not on USB · {evidence['text']}"
            if evidence["kind"] == "never":
                message += ": try a data cable or another port"
            values = (KeyValue("what_to_check", activity_module.WHAT_TO_CHECK[evidence["kind"]]),
                      KeyValue("kernel_evidence", evidence["kind"]), KeyValue("expected_by", expected["source"]),
                      KeyValue("last_seen", seen or "unknown"))
            statuses.append(DiagnosticStatus(level=WARN, name=f"{expected['name']}: presence", message=message,
                                             hardware_id=hardware_id, values=values, path=path, code="ExpectedAbsent",
                                             stamp=now, received=now, source=EXPECTED_SOURCE))
        return statuses

    def _recent_row(self, layer: tree.OsLayer, hardware_id: str, seen: dict, boots: list, now: float,
                    unreadable: str) -> list[DiagnosticStatus]:
        last = seen["last_seen"]
        if not isinstance(last, (int, float)) or isinstance(last, bool) or now - float(last) > RECENTLY_SEEN_S:
            return []
        if RECENT_PATH not in layer.entities:
            layer.entities[RECENT_PATH] = Entity(RECENT_PATH, "Recently seen", "group", via=tree.HOST_PATH, owner="io_diagnostics")
            layer.icons[RECENT_PATH] = "group"
        path = f"{RECENT_PATH}/{tree.slug(hardware_id)}"
        layer.entities[path] = Entity(path, seen["name"], "device", via=RECENT_PATH, hardware_id=hardware_id, owner=seen["owner"])
        layer.icons[path] = "usb"
        evidence = activity_module.kernel_evidence(boots, hardware_id, now, self.utc_offset_s, unreadable)
        when = _seen_text(last, self.utc_offset_s)
        return [DiagnosticStatus(level=OK, name=f"{seen['name']}: presence", hardware_id=hardware_id, path=path,
                                 message=f"seen {when} · not on USB now · {evidence['text']} (not in the expected setup)",
                                 values=(KeyValue("kernel_evidence", evidence["kind"]), KeyValue("last_seen", when),
                                         KeyValue("expected_by", "nothing: only seen by this host")),
                                 stamp=now, received=now, source=EXPECTED_SOURCE)]

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
                self.describes[spec.name] = describe(spec, extra_env=self._job_env())

    # --- background service ------------------------------------------------------------------------------------
    def start(self, *, udev: bool = True) -> None:
        kernel_log = getattr(self.os, "kernel_log", None)
        if hasattr(kernel_log, "on_lines"):
            kernel_log.on_lines = self.wake  # a new kernel line refreshes at once
        self._scheduler = Scheduler(self.scheduled_jobs, self._run_job, gates=self.gates)
        self.refresh()  # after the scheduler exists: the first refresh starts the followers, so they never run a diagnose
        self._scheduler.start()
        threading.Thread(target=self._refresh_loop, name="io-refresh", daemon=True).start()
        threading.Thread(target=self._recheck_loop, name="io-recheck", daemon=True).start()
        threading.Thread(target=self.describe_all, name="io-describe", daemon=True).start()
        # WHY the OS source decides: only a live machine has a udev to listen to (a fixture or a fake machine has none)
        command = getattr(self.os, "udev_command", None)
        if udev and command:
            self._udev = activity_module.UdevWatcher(self.os.invalidate, self.on_udev, command)
            self._udev.start()

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

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._scheduler:
            self._scheduler.stop()
        if self._udev:
            self._udev.stop()
        for follower in list(self.followers.values()):
            follower.stop()
        self.followers.clear()
        if hasattr(self.os, "close"):
            self.os.close()
        self.notify("stop")

    # --- views -------------------------------------------------------------------------------------------------
    def view(self) -> View:
        with self.lock:
            now = self.clock.now()
            jobs = list(self.jobs.values())
            layer = self.layer
            merged = tree.merge(layer, jobs, self.os_records)
            # WHY judged on the merged tree, every view: a rig counts boards and matches cameras by the serial their
            # DRIVER reports (the RealSense SDK serial), which only the plugins' records carry
            setup = expected_module.evaluate(self.setup, merged, layer, now, RUNNER_SOURCE)
            notes = self._host_notes(merged, now) + self._links_summary(merged, now)
            # WHY here: the rig rows and the host's own lines are judged per view, never stored; their changes are
            # what the history keeps
            self.history.sync("view", setup.statuses + notes, now)
            checks = self._attach_checks(merged)
            if setup.entities or setup.statuses or notes or checks:
                layer = self._layer_with(setup.entities)
                merged = tree.merge(layer, jobs, self.os_records + setup.statuses + notes + checks)
            rows = tree.build_rows(merged, now)
            live = [self._placed(event, self.layer) for event in self.live.events()]
            events, self.pending = activity_module.merge(self.usb.events, self.usb.consumed, live)
            events += self.faults.events
            events += activity_module.level_events(self.history.transitions(now))
            for job in self.jobs.values():
                device_path = self.layer.device_paths.get(str(job.device.get("id")))
                if device_path:
                    # WHY the plugin as source when it names none: the driver's own Found / Probed / ProbeBlocked go to the
                    # log as they are (docs/probe-on-connect.md); the host adds no event about a device itself
                    events += [dataclasses.replace(event, path=absolute(event.path, device_path), source=event.source or job.spec.name)
                               for event in job.cache.events.values()]
        events = [event for event in events if event.stamp is None or event.stamp <= now]
        events.sort(key=lambda event: (event.stamp or 0.0, event.seq or 0), reverse=True)
        return View(now, layer, merged, rows, events, self.faults.open_by_path(), setup, checks)

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
            if row.via == EXPECTED_PATH and EXPECTED_PATH not in entities:
                entities[EXPECTED_PATH] = Entity(EXPECTED_PATH, "Expected, not connected", "group", via=tree.HOST_PATH,
                                                 owner="io_diagnostics")
                icons[EXPECTED_PATH] = "group"
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
        return {path: entity.name for path, entity in view.merged.entities.items()}

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

        view = self.view()
        summary = self.summary_json(view)
        recent = self.events_json(view, RECENT)
        return {"api": API, "schema": SCHEMA, "record_version": RECORD_VERSION, "clock": self.clock_json(view.now), "fixture": self.fixture,
                "host": {"name": self.os.hostname, "sources": list(self.sources)}, "summary": summary,
                "chip": chip_model(summary, recent, self.url),
                "tree": tree.node_json(tree.HOST_PATH, view.rows, view.layer, view.faults_by_path, nested=True),
                "recent": recent, "expected_setup": self.setup_summary(view)}

    def setup_summary(self, view: View) -> dict:
        manifest = self.setup.manifest
        return {"file": str(self.setup.path) if self.setup.path else None, "editable": bool(self.setup.path) and not self.setup.note,
                "note": self.setup.note,
                "rigs": [{"name": rig.name, "path": expected_module.rig_path(rig), "devices": len(rig.devices)} for rig in manifest.rigs],
                "retired": [{"name": entry.name, "hardware_id": entry.hardware_id, "serial": entry.serial, "date": entry.date,
                             "reason": entry.reason, "path": next((path for path, retired in (view.setup.retired if view.setup else {}).items()
                                                                   if retired is entry), None)} for entry in manifest.retired]}

    # --- one row's detail ------------------------------------------------------------------------------------------
    def node(self, path: str) -> dict | None:
        view = self.view()
        if path not in view.merged.entities:
            return None
        merged, rows = view.merged, view.rows
        ancestors, current = [], merged.parent.get(path)
        while current is not None:
            ancestors.insert(0, {"path": current, "name": merged.entities[current].name, "edge": merged.edge.get(current)})
            current = merged.parent.get(current)
        return {"node": tree.node_json(path, rows, view.layer, view.faults_by_path, nested=False), "ancestors": ancestors,
                "checks": [self.check_json(status, view.now) for status in rows.own.get(path, [])],
                "actions": self.actions_json(path, view),
                "faults": [self.fault_json(fault, view) for fault in self.faults.all() if fault.path == path],
                "events": [activity_module.event_json(event, self.utc_offset_s, self._names(view)) for event in view.events
                           if event.path == path or event.path.startswith(path + "/")][:20],
                "entity": to_dict(merged.entities[path]), "setup": self.setup_json(path, view)}

    # --- the expected setup: what a row offers, and the edits (host-side file writes; never a device) -----------------
    def _setup_target(self, path: str, view: View) -> tuple[str, dict] | None:
        """``(role, ids)`` for a row the Expected setup card is about: ``rig`` (a rig row), ``absent`` (a row under
        Expected, not connected) or ``device`` (a physical device row itself); None for anything else."""

        merged, layer = view.merged, view.layer
        entity = merged.entities.get(path)
        if entity is None:
            return None
        if view.setup and path in view.setup.rigs:
            return "rig", {"rig": view.setup.rigs[path]}
        if path.startswith((EXPECTED_PATH + "/", RECENT_PATH + "/")):  # a recently seen row is absent too: Forget drops it
            serials = tuple(serial for serial in ((entity.identity or {}).get("serial_number", ""),) + self._usb_serial(entity.hardware_id) if serial)
            return "absent", {"hardware_id": entity.hardware_id, "serials": serials, "name": entity.name}
        if layer.device_of_path.get(path) in layer.physical:
            return "device", {"hardware_id": entity.hardware_id, "serials": expected_module.device_serials(entity, layer, path),
                              "name": entity.name}
        return None

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
        try:
            manifest = self.setup.editable()
        except expected_module.ManifestError as error:
            return {"refused": "ManifestUnavailable", "error": str(error)}
        if op == "expect":
            if role == "rig":
                return self._plan_expect_rig(ids["rig"], manifest, view)
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
            if role != "absent":
                return {"refused": "NotAbsent", "error": "Forget is for a device that is not connected (a row under Expected, not connected)"}
            rigs = expected_module.remove_device(manifest, ids["hardware_id"], ids["serials"])
            where = f"the expected setup ({', '.join(rigs)}) and " if rigs else ""
            return {"manifest": manifest, "forget": ids["hardware_id"],
                    "text": f"Forget {ids['name']}? It leaves {where}the devices this host remembers; plugged in again, it is "
                            "simply a device again."}
        if op == "retire":
            if role not in ("device", "absent"):
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
            found = self.setup.entry_for(ids["hardware_id"], ids["serials"])
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
                out["can"] = {"expect": True, "forget": False, "retire": True}
                out["needs_rig"] = not found and len(out["rigs"]) > 1
            else:
                out["can"] = {"expect": False, "forget": True, "retire": True}
        for op, allowed in out["can"].items():
            if allowed:
                plan = self._plan(op, path, view, rig=out["rigs"][0] if out["needs_rig"] else None, reason="")
                out["confirm"][op] = plan.get("text") or plan.get("error", "")
        if not out["editable"]:
            out["can"] = {op: False for op in out["can"]}
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
        device_path = tree.nearest_device(path, view.merged, view.layer)
        if device_path is None:
            return None, f"{check}: no physical device above {entity.name}"
        return self.os.gate(view.layer.matchable(view.layer.device_of_path[device_path]), check)

    def _fill(self, path: str, view: View) -> _Blank:
        """The values a confirm text's ``{name}``, ``{id}``, ``{serial}``, ``{latest}`` placeholders take.

        WHY ``{name}`` is the nearest device's name: the drivers' convention is ``{name}`` = the physical device that
        owns the action (a CAN board's adapter, a RealSense itself) and ``{id}`` = the node id, so a board's
        "Calibrate CAN {id} on {name}?" reads "Calibrate CAN 1 on fdcanusb 272813EF?", not "... on CAN 1?"."""

        entity = view.merged.entities[path]
        device_path = tree.nearest_device(path, view.merged)
        device = view.merged.entities[device_path] if device_path else None
        serial = entity.identity.get("serial_number") or (device.identity.get("serial_number", "") if device else "")
        firmware = self._firmware(path, view)
        return _Blank(name=device.name if device else entity.name, id=path.rsplit("/", 1)[-1], serial=serial,
                      latest=dict(firmware.values).get("latest_version", "?") if firmware else "?")

    def actions_json(self, path: str, view: View) -> list[dict]:
        entity = view.merged.entities[path]
        fill = self._fill(path, view)
        out = []
        for action in entity.actions:
            results = []
            for check, text in action.preconditions:
                state, detail = self.evaluate(check, path, view)
                results.append({"check": check, "text": string.Formatter().vformat(text, (), fill),
                                "state": {True: "pass", False: "fail", None: "unknown", WAITING: WAITING}[state],
                                "detail": detail})
            failing = [result for result in results if result["state"] == "fail"]
            waiting = [result for result in results if result["state"] == WAITING]
            unknown = [result for result in results if result["state"] == "unknown"]
            up_to_date = next((result for result in failing if result["check"] == "update_available"), None)
            if up_to_date:  # WHY first: with nothing to install, a free port would not make the button useful (V6)
                state, reason = "nothing_to_do", f"up to date: {up_to_date['detail']}"
            elif failing:
                first = next(result for result in failing if result["check"] != "update_available")
                detail = first["detail"]
                state, reason = "blocked", detail if detail.startswith("blocked") else f"blocked: {detail}"
            elif waiting:  # WHY its own state: runnable in a few seconds, by itself; the page greys it, never warns
                state, reason = WAITING, waiting[0]["detail"]
            elif unknown:
                state, reason = "unknown", f"can't tell: {unknown[0]['detail']}"
            else:
                state, reason = "ready", ""
            out.append({"name": action.name, "title": action.title, "description": action.description, "danger": action.danger,
                        "scope": action.scope, "kind": action.kind,
                        "confirm": string.Formatter().vformat(action.confirm, (), fill),
                        "preconditions": results, "state": state, "reason": reason, "executable": False,
                        "owner": entity.owner})
        return out

    # --- actions: run one, for a person ------------------------------------------------------------------------------
    def _owning_job(self, path: str, entity) -> Job | None:
        """The job whose device row is ``path`` or above it; with several plugins on one device, the entity's owner."""

        with self.lock:
            candidates = []
            for job in self.jobs.values():
                device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
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
            self.describes[job.spec.name] = describe(job.spec, extra_env=self._job_env())
        for item in (self.describes.get(job.spec.name) or {}).get("actions") or []:
            if isinstance(item, dict) and item.get("name") == name:
                try:
                    return from_dict({"schema": SCHEMA, **item, "record": "action"}), "describe"
                except (TypeError, ValueError, KeyError):
                    return None, "describe"
        return None, ""

    def act(self, path: str, action: str, *, node: int | None = None, confirm: str | None = None, dry_run: bool = False,
            human: bool | None = None, new_id: int | None = None) -> dict:
        """Run the plugin's ``act`` for one action registered on the entity at ``path``.

        The rule (docs/api-v1.md "Actions: io-diagnostics act"): only a person runs an action; agents can't click.
        - ``human`` is whether a person asked: the CLI passes stdin-is-a-TTY, and so does the default. Not a person
          -> everything is refused except ``dry_run``, which the plugin answers without touching the device.
        - Every action whose danger is not ``query`` needs ``confirm`` (the token the confirm dialog shows). The
          host passes it through; the plugin checks it against the device (moteus: its time-window token, RealSense:
          the camera's serial), because only the owner knows what it confirms.
        - ``new_id`` passes through as ``--new-id`` (set_id's new bus id); the plugin checks it.
        - It runs inside the plugin's ``exclusive`` group, so it never collides with its own diagnose."""

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

        def refuse(code: str, message: str) -> dict:
            return {**out, "refused": code, "message": message}

        entity = view.merged.entities.get(path)
        if entity is None:
            return refuse("NoSuchEntity", f"no entity at {path!r}")
        job = self._owning_job(path, entity)
        if job is None:
            return refuse("NoPlugin", f"no enabled plugin diagnoses {entity.name} ({path})")
        registered, source = self._registered(job, entity, action)
        out.update(plugin=job.spec.name, device_id=str(job.device.get("id", "")), registered_by=source)
        if registered is None:
            return refuse("ActionNotRegistered", f"{job.spec.name} registers no action {action!r} for {entity.name}")
        argv = act_argv(job.spec, job.device, action, node=node, confirm=confirm, dry_run=dry_run, new_id=new_id)
        out.update(title=registered.title, danger=registered.danger, argv=argv)
        if not human and not dry_run:
            return refuse("NotInteractive", "not a person (stdin is not a TTY): only --dry-run runs; agents can't click")
        if registered.danger != "query" and not dry_run and not confirm:
            text = string.Formatter().vformat(registered.confirm, (), self._fill(path, view))
            return refuse("ConfirmRequired", f"{registered.danger}: pass --confirm <token> after reading: {text}")
        device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
        with self.gates.hold(job.spec.exclusive):
            result = run_act(job.spec, job.device, device_path, argv, self.clock.now, self._job_env(job))
        return {**out, **result}

    # --- activity, records, plugins -----------------------------------------------------------------------------
    def activity(self, limit: int = 200, types: tuple[str, ...] | None = None) -> dict:
        """``types``: only events of these types (``activity.TYPES``); raises ValueError for one it does not know."""

        unknown = sorted(set(types or ()) - set(activity_module.TYPES))
        if unknown:
            raise ValueError(f"unknown event type {', '.join(map(repr, unknown))}: one of {', '.join(activity_module.TYPES)}")
        view = self.view()
        return {"clock": self.clock_json(view.now), "events": self.events_json(view, limit, types),
                "faults": [self.fault_json(fault, view) for fault in self.faults.all()],
                "link_health": activity_module.link_health(view.rows, view.layer, self.utc_offset_s)}

    def records(self) -> dict:
        view = self.view()
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
        attached = {status.name: status.path for status in (self.view().checks if self.check_jobs else [])}
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

        view = self.view()
        keys = [key for key in self.history.keys(path) if status in (None, "", key[1])]
        if path not in view.merged.entities and not keys:
            return None
        seconds = self._window(window)
        start, end = view.now - seconds, view.now
        statuses = []
        for key in keys:
            period = self.history.period(key)
            statuses.append({"name": key[1], "expected_period_s": period, "stale_after_s": history_module.stale_after(period),
                             "levels": self.history.timeline(key, start, end),
                             "measurements": self.history.series(key, start, end)})
        entity = view.merged.entities.get(path)
        return {"path": path, "name": entity.name if entity else path, "window_s": seconds, "from": start, "to": end,
                "statuses": statuses}

    def bar_json(self, path: str, bins: int = 60, window=None) -> dict | None:
        """The health bar of one entity and of each child row: per bin, the worst level any of the row's statuses held."""

        view = self.view()
        if path not in view.merged.entities:
            return None
        seconds = self._window(window)
        bins = max(1, min(int(bins), history_module.MAX_BINS))
        start, end = view.now - seconds, view.now
        rows = []
        for depth, row in [(0, path), *((1, child) for child in view.merged.children.get(path, []))]:
            # WHY the row's own statuses when it has any: the bar says what THIS black box reported (its row's level);
            # a pure grouping row, like its level, shows what everything below reported
            keys = self.history.keys(row)
            basis = "own" if keys else "below"
            if not keys:
                keys = self.history.keys(row, below=True)
            if not keys:
                basis = "none"
            timelines = [self.history.timeline(key, start, end) for key in keys]
            rows.append({"path": row, "name": view.merged.entities[row].name, "depth": depth, "basis": basis,
                         "statuses": len(keys), "bar": history_module.bar(timelines, start, end, bins)})
        return {"path": path, "window_s": seconds, "from": start, "to": end, "bins": bins, "bin_s": seconds / bins,
                "rows": rows}

    def settings_json(self) -> dict:
        return self.settings.json()

    def set_settings(self, body: dict) -> dict:
        """The page's View options: ``{"history_window_s": 600}`` (seconds, or "10m"). Raises SettingsError."""

        unknown = sorted(set(body) - {"history_window_s"})
        if unknown:
            raise settings_module.SettingsError(f"no setting {', '.join(map(repr, unknown))}: only history_window_s")
        if "history_window_s" in body:
            self.settings.set_history_window(body["history_window_s"])
            self.history.window_s = self.settings.history_window_s
        return self.settings_json()


# ----- construction ---------------------------------------------------------------------------------------------
def fixture_info(document: dict, frame: dict) -> dict:
    return {"name": frame["name"], "label": frame["label"], "story": frame["story"], "clock": frame["clock"],
            "frames": [{"name": other["name"], "label": other["label"]} for other in document["frames"].values()]}


def make_host(*, fixture: str | None = None, plugins_dir: Path | None = None, once: bool = False, at_s: float = 0.0,
              offset_s: float = 0.0, enable: tuple[str, ...] = (), machine: Path | None = None,
              expected: Path | None = None, history_window_s: float | None = None) -> Host:
    """Live: ``plugins.d`` on this machine's clock. Fixture: the recorded frame's OS side, its clock, and the fixture
    plugin directory (``tests/fixtures/plugins``), whose plugin learns the frame from ``IO_DIAGNOSTICS_FIXTURE``.
    Machine: a fake machine's skeleton (``MachineOs``) on this clock. ``enable`` turns on plugins their declaration
    disables, for this host only. ``expected``: the expected-setup file; live mode defaults to
    ``~/.config/io-diagnostics/expected.toml``, a fixture or a fake machine reads one only when it is named.
    ``history_window_s``: this run's history window (``serve --history-window``); every mode reads and writes the
    person's ``settings.toml`` (an app-wide preference, not a record of this machine)."""

    settings = {"settings_file": expected_module.default_config_dir() / settings_module.SETTINGS_FILE,
                "history_window_s": history_window_s}
    if machine is not None:
        if fixture is not None:
            raise SystemExit("--machine and --fixture are two different OS sides: pick one")
        clock = FixedClock(time.time()) if once else Clock()
        return Host(plugins_dir=plugins_dir or PLUGINS_DIR, clock=clock, os_source=MachineOs(machine), enable=enable,
                    expected_file=expected, **settings)
    if fixture is None:
        # WHY live only: the remembered devices are this machine's; a fixture or a fake machine must never add to them
        clock = FixedClock(time.time()) if once else Clock()
        return Host(plugins_dir=plugins_dir or PLUGINS_DIR, clock=clock, os_source=LiveOs(), enable=enable,
                    state_file=default_state_dir() / SEEN_FILE,
                    expected_file=expected or expected_module.default_config_dir() / expected_module.EXPECTED_FILE,
                    **settings)
    document, frame = load_fixture(fixture)
    offset = frame.get("utc_offset_s")
    clock = FixedClock(frame["clock"] + at_s, offset, "fixture") if once else ReplayClock(frame["clock"], offset, offset_s)
    # WHY no default manifest here: a recorded frame is replayed as it was; the person's rig file would add rows
    # the recording never had (a test or a screenshot names its own file with --expected)
    return Host(plugins_dir=plugins_dir or FIXTURE_PLUGINS_DIR, clock=clock, os_source=FixtureOs(frame),
                fixture=fixture_info(document, frame), enable=enable, expected_file=expected,
                plugin_env={"IO_DIAGNOSTICS_FIXTURE": frame["name"], "IO_DIAGNOSTICS_FIXTURE_FILE": str(FIXTURE_FILE)},
                **settings)


__all__ = ["Clock", "ReplayClock", "FixedClock", "LiveOs", "FixtureOs", "MachineOs", "Host", "View", "make_host",
           "load_fixture", "resolve_fixture"]
