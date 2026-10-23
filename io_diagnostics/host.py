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
from io_diagnostics import tree
from io_diagnostics.faults import Faults
from io_diagnostics.plugins import load_plugins, match_devices
from io_diagnostics.runner import (RUNNER_SOURCE, Gates, Job, Scheduler, absolute, act_argv, describe, host_status,
                                   run_act, run_plugin)

from contract.record import (OK, RANK, RECORD_VERSION, SCHEMA, STALE, STALE_FACTOR, STALE_FLOOR_S, WARN, Action,
                             DiagnosticStatus, Entity, Event, KeyValue, effective_level, from_dict, to_dict)

FIXTURE_FILE = FIXTURE_PLUGINS_DIR / "v6_frames.json"
FIXTURE_ALIASES = {"plug": "F1", "bus-busy": "F2", "after-restart": "F3", "f1": "F1", "f2": "F2", "f3": "F3",
                   "v6-f1": "F1", "v6-f2": "F2", "v6-f3": "F3"}
REFRESH_S = 2.0
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
        self.hostname = socket.gethostname()
        self.kernel_log = activity_module.LiveKernelLog()
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
            return False, "rack bus lock held by " + ", ".join(f"pid {holder.pid} {holder.command}" for holder in holders)
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
        self.note = ""  # why the file could not be read at start (sticky: the old content was dropped)
        self.write_note = ""  # why the latest write failed; cleared by the next good one
        self._written = -1e18
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.devices = {str(key): dict(value) for key, value in (data.get("devices") or {}).items()
                                if isinstance(value, dict)}
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
            entry.update(name=name, last_seen=now)
        if self.path and seen and (new or time.monotonic() - self._written > SEEN_WRITE_S):
            self._write()

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(handle, "w") as file:
                json.dump({"version": 1, "devices": self.devices}, file, indent=1, sort_keys=True)
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


class _Blank(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class Host:
    def __init__(self, *, plugins_dir: Path = PLUGINS_DIR, clock: Clock | None = None, os_source=None,
                 fixture: dict | None = None, plugin_env: dict[str, str] | None = None, enable: tuple[str, ...] = (),
                 state_file: Path | None = None):
        self.plugins_dir = Path(plugins_dir)
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
        self.gates = Gates()  # one per host: `once`, the scheduler and act all take turns per `exclusive` group
        self.seen = SeenDevices(state_file)  # USB devices a plugin diagnosed, remembered across runs (None: memory)
        self.layer = tree.OsLayer()
        self.sources: list[dict] = []
        self.os_records: list[DiagnosticStatus] = []
        self.usb = activity_module.UsbActivity()
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

        pending = [spec for spec in self.specs if spec.enabled and spec.name not in self._described
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
        now = self.clock.now()
        devices, sources = self.os.skeleton()
        boots = self.os.boots()
        usb = activity_module.usb_activity(boots, now, self.utc_offset_s)
        layer = tree.build_os_layer(self.os.hostname, devices, extra_ports=frozenset(usb.recent_ports))
        usb.events = [dataclasses.replace(event, path=layer.port_paths.get(str(event.args.get("port")), event.path))
                      if event.args.get("port") else event for event in usb.events]
        pairs = match_devices(self.specs, [layer.matchable(device_id) for device_id in layer.devices])
        claimed = {layer.entities[path].hardware_id: layer.entities[path].name
                   for path in {layer.device_paths[str(device.get("id"))] for _spec, device in pairs}
                   if activity_module.usb_id(layer.entities[path].hardware_id)}
        self.seen.mark(claimed, now)
        absent = self._absent_rows(layer, boots, now)
        if self.seen.note or self.seen.write_note:
            note = " · ".join(text for text in (self.seen.note, self.seen.write_note) if text)
            sources = [*sources, {"name": "seen devices", "state": "skipped", "note": note}]
        with self.lock:
            wanted = {}
            for spec, device in pairs:
                key = (spec.name, str(device.get("id")))
                job = self.jobs.get(key) or Job(spec, device, layer.device_paths[str(device.get("id"))])
                job.device, job.device_path = device, layer.device_paths[str(device.get("id"))]
                wanted[key] = job
            self.jobs = wanted
            self.layer, self.sources, self.usb = layer, sources, usb
            self.os_records = tree.os_statuses(layer, usb.history, now, {key[1] for key in wanted}) + absent
        changed = []
        for status in self.os_records:  # WHY only on change: OS checks are recomputed every refresh, not re-evaluated
            key, signature = (status.path, status.name), (status.level, status.code)
            if self._last_os_level.get(key) != signature:
                self._last_os_level[key] = signature
                changed.append(status)
        self.faults.observe(changed, now)

    # --- expected but absent ------------------------------------------------------------------------------------
    def expected_devices(self) -> dict[str, dict]:
        """``hardware_id -> {name, last_seen, source, owner}``: the devices this host has seen a plugin diagnose, plus
        every enabled plugin's describe ``expected`` (record v0.1; the plugin's entry wins over the memory)."""

        out = {hardware_id: {"name": str(entry.get("name") or hardware_id), "last_seen": entry.get("last_seen"),
                             "source": "seen by this host", "owner": "io_diagnostics"}
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
                                    "source": str(item.get("source") or spec.name), "owner": spec.name}
        return out

    def _absent_rows(self, layer: tree.OsLayer, boots: list, now: float) -> list[DiagnosticStatus]:
        """One row (entity + WARN status) per expected USB device that is not on USB now, under ``host/expected``,
        saying in plain words what the kernel saw. WHY warn, not error: absent is not broken."""

        present = {ids for entity in layer.entities.values() if (ids := activity_module.usb_id(entity.hardware_id))}
        note = str(getattr(getattr(self.os, "kernel_log", None), "note", "") or "")
        unreadable = note if note.startswith("journalctl unavailable") else ""
        statuses = []
        for hardware_id, expected in sorted(self.expected_devices().items()):
            ids = activity_module.usb_id(hardware_id)
            if ids is None or ids in present:  # v0.1: only USB ids (usb:<vid>:<pid>:<serial>) are checked here
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

    # --- plugin runs -----------------------------------------------------------------------------------------------
    def _job_env(self) -> dict[str, str]:
        return dict(self.plugin_env)

    def run_job(self, job: Job) -> None:
        """Run one job, waiting first for its ``exclusive`` group (queued, never failed)."""

        with self.gates.hold(job.spec.exclusive):
            self._run_job(job)

    def _run_job(self, job: Job) -> None:
        """Run one job; the caller holds its group (the scheduler takes it before submitting)."""

        with self.lock:
            entity = self.layer.entities.get(job.device_path)
        outcome = run_plugin(job.spec, job.device, job.device_path, self.clock.now, self._job_env(),
                             hardware_id=entity.hardware_id if entity else "")
        now = self.clock.now()
        with self.lock:
            fresh_statuses, _fresh_events = job.cache.apply(outcome, received=now)
            hardware_id = self.layer.entities.get(job.device_path).hardware_id if job.device_path in self.layer.entities else ""
            job.cache.host_status = host_status(job.spec, outcome, hardware_id, now)
            job.last = outcome
            device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
        observed = [dataclasses.replace(status, path=absolute(status.path, device_path)) for status in fresh_statuses]
        observed.append(dataclasses.replace(job.cache.host_status, path=device_path))
        self.faults.observe(observed, now)

    def run_all_once(self) -> None:
        """Refresh, then run every matched (plugin, device) once, in parallel except within an ``exclusive`` group,
        whose jobs run one after another; used by ``once`` and tests."""

        self.refresh()
        threads = [threading.Thread(target=self.run_job, args=(job,), daemon=True) for job in list(self.jobs.values())]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def describe_all(self) -> None:
        for spec in list(self.specs):
            if spec.enabled and self.describes.get(spec.name) is None:
                self.describes[spec.name] = describe(spec, extra_env=self._job_env())

    # --- background service ------------------------------------------------------------------------------------
    def start(self, *, udev: bool = True) -> None:
        self.refresh()
        self._scheduler = Scheduler(lambda: list(self.jobs.values()), self._run_job, gates=self.gates)
        self._scheduler.start()
        threading.Thread(target=self._refresh_loop, name="io-refresh", daemon=True).start()
        threading.Thread(target=self.describe_all, name="io-describe", daemon=True).start()
        if udev and self.os.mode == "live":
            self._udev = activity_module.UdevWatcher(self.os.invalidate)
            self._udev.start()

    def _refresh_loop(self) -> None:
        while not self._stop.wait(REFRESH_S):
            try:
                self.refresh()
                self.faults.tick(self.clock.now())
            except Exception as error:  # noqa: BLE001 - a bad read must not stop the refresher; it shows in sources
                self.sources = [*self.sources, {"name": "refresh", "state": "skipped", "note": f"{type(error).__name__}: {error}"}]

    def stop(self) -> None:
        self._stop.set()
        if self._scheduler:
            self._scheduler.stop()
        if self._udev:
            self._udev.stop()

    # --- views -------------------------------------------------------------------------------------------------
    def view(self) -> View:
        with self.lock:
            now = self.clock.now()
            merged = tree.merge(self.layer, list(self.jobs.values()), self.os_records)
            rows = tree.build_rows(merged, now)
            events = list(self.usb.events) + self.faults.events
            for job in self.jobs.values():
                device_path = self.layer.device_paths.get(str(job.device.get("id")))
                if device_path:
                    events += [dataclasses.replace(event, path=absolute(event.path, device_path)) for event in job.cache.events.values()]
        events = [event for event in events if event.stamp is None or event.stamp <= now]
        events.sort(key=lambda event: (event.stamp or 0.0, event.seq or 0), reverse=True)
        return View(now, self.layer, merged, rows, events, self.faults.open_by_path())

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
        return {"level": root.level, "counts": dict(root.rollup.counts), "attention": attention,
                "entities": len(view.merged.entities), "devices": len(view.layer.physical)}

    def events_json(self, view: View, limit: int) -> list[dict]:
        names = self._names(view)
        return [activity_module.event_json(event, self.utc_offset_s, names) for event in view.events[:limit]]

    def state(self) -> dict:
        from io_diagnostics.tray import chip_model

        view = self.view()
        summary = self.summary_json(view)
        recent = self.events_json(view, RECENT)
        return {"api": API, "schema": SCHEMA, "record_version": RECORD_VERSION, "clock": self.clock_json(view.now), "fixture": self.fixture,
                "host": {"name": self.os.hostname, "sources": list(self.sources)}, "summary": summary,
                "chip": chip_model(summary, recent, self.url),
                "tree": tree.node_json(tree.HOST_PATH, view.rows, view.layer, view.faults_by_path, nested=True),
                "recent": recent}

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
                "entity": to_dict(merged.entities[path])}

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
                "uptime_s": status.uptime_s, "values": [{"key": key, "value": value} for key, value in status.values],
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
        """The values a confirm text's ``{name}``, ``{id}``, ``{serial}``, ``{latest}`` placeholders take."""

        entity = view.merged.entities[path]
        device_path = tree.nearest_device(path, view.merged)
        serial = entity.identity.get("serial_number") or (view.merged.entities[device_path].identity.get("serial_number", "")
                                                           if device_path else "")
        firmware = self._firmware(path, view)
        return _Blank(name=entity.name, id=path.rsplit("/", 1)[-1], serial=serial,
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
                                "state": {True: "pass", False: "fail", None: "unknown"}[state], "detail": detail})
            failing = [result for result in results if result["state"] == "fail"]
            unknown = [result for result in results if result["state"] == "unknown"]
            up_to_date = next((result for result in failing if result["check"] == "update_available"), None)
            if up_to_date:  # WHY first: with nothing to install, a free port would not make the button useful (V6)
                state, reason = "nothing_to_do", f"up to date: {up_to_date['detail']}"
            elif failing:
                first = next(result for result in failing if result["check"] != "update_available")
                state, reason = "blocked", f"blocked: {first['detail']}"
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
                if path == device_path or path.startswith(device_path + "/"):
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
            human: bool | None = None) -> dict:
        """Run the plugin's ``act`` for one action registered on the entity at ``path``.

        The rule (docs/api-v1.md "Actions: io-diagnostics act"): only a person runs an action; agents can't click.
        - ``human`` is whether a person asked: the CLI passes stdin-is-a-TTY, and so does the default. Not a person
          -> everything is refused except ``dry_run``, which the plugin answers without touching the device.
        - Every action whose danger is not ``query`` needs ``confirm`` (the token the confirm dialog shows). The
          host passes it through; the plugin checks it against the device (moteus: its time-window token, RealSense:
          the camera's serial), because only the owner knows what it confirms.
        - It runs inside the plugin's ``exclusive`` group, so it never collides with its own diagnose."""

        if human is None:
            try:
                human = sys.stdin is not None and sys.stdin.isatty()
            except (ValueError, OSError):  # a closed stdin is not a person
                human = False
        view = self.view()
        out = {"path": path, "action": action, "node": node, "dry_run": dry_run, "plugin": None, "device_id": None,
               "title": "", "danger": None, "registered_by": "", "argv": None, "refused": None, "outcome": "refused",
               "returncode": None, "duration_s": 0.0, "records": [], "action_result": None, "rejected": [],
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
        argv = act_argv(job.spec, job.device, action, node=node, confirm=confirm, dry_run=dry_run)
        out.update(title=registered.title, danger=registered.danger, argv=argv)
        if not human and not dry_run:
            return refuse("NotInteractive", "not a person (stdin is not a TTY): only --dry-run runs; agents can't click")
        if registered.danger != "query" and not dry_run and not confirm:
            text = string.Formatter().vformat(registered.confirm, (), self._fill(path, view))
            return refuse("ConfirmRequired", f"{registered.danger}: pass --confirm <token> after reading: {text}")
        device_path = self.layer.device_paths.get(str(job.device.get("id")), job.device_path)
        with self.gates.hold(job.spec.exclusive):
            result = run_act(job.spec, job.device, device_path, argv, self.clock.now, self._job_env())
        return {**out, **result}

    # --- activity, records, plugins -----------------------------------------------------------------------------
    def activity(self, limit: int = 200) -> dict:
        view = self.view()
        return {"clock": self.clock_json(view.now), "events": self.events_json(view, limit),
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

    def plugins(self) -> dict:
        with self.lock:
            jobs = list(self.jobs.values())
        out = []
        for spec in self.specs:
            out.append({"name": spec.name, "file": str(spec.file), "command": list(spec.command),
                        "diagnose_args": list(spec.diagnose_args), "cwd": str(spec.cwd),
                        "timeout_s": spec.timeout_s, "act_timeout_s": spec.act_timeout_s, "period_s": spec.period_s,
                        "enabled": spec.enabled, "replay": spec.replay, "exclusive": spec.exclusive,
                        "matches": [match.text() for match in spec.matches], "description": spec.description,
                        "describe": self.describes.get(spec.name),
                        "jobs": [{"device_id": str(job.device.get("id")), "device_path": job.device_path,
                                  "last_run": job.last.summary() if job.last else None,
                                  "next_due_s": round(max(job.next_due - time.monotonic(), 0.0), 1) if self._scheduler else None}
                                 for job in jobs if job.spec.name == spec.name]})
        return {"directory": str(self.plugins_dir), "problems": list(self.plugin_problems), "plugins": out}

    def health(self) -> dict:
        return {"ok": True, "api": API, "mode": self.clock.mode, "uptime_s": round(time.monotonic() - self.started, 1)}


# ----- construction ---------------------------------------------------------------------------------------------
def fixture_info(document: dict, frame: dict) -> dict:
    return {"name": frame["name"], "label": frame["label"], "story": frame["story"], "clock": frame["clock"],
            "frames": [{"name": other["name"], "label": other["label"]} for other in document["frames"].values()]}


def make_host(*, fixture: str | None = None, plugins_dir: Path | None = None, once: bool = False, at_s: float = 0.0,
              offset_s: float = 0.0, enable: tuple[str, ...] = (), machine: Path | None = None) -> Host:
    """Live: ``plugins.d`` on this machine's clock. Fixture: the recorded frame's OS side, its clock, and the fixture
    plugin directory (``tests/fixtures/plugins``), whose plugin learns the frame from ``IO_DIAGNOSTICS_FIXTURE``.
    Machine: a fake machine's skeleton (``MachineOs``) on this clock. ``enable`` turns on plugins their declaration
    disables, for this host only."""

    if machine is not None:
        if fixture is not None:
            raise SystemExit("--machine and --fixture are two different OS sides: pick one")
        clock = FixedClock(time.time()) if once else Clock()
        return Host(plugins_dir=plugins_dir or PLUGINS_DIR, clock=clock, os_source=MachineOs(machine), enable=enable)
    if fixture is None:
        # WHY live only: the remembered devices are this machine's; a fixture or a fake machine must never add to them
        clock = FixedClock(time.time()) if once else Clock()
        return Host(plugins_dir=plugins_dir or PLUGINS_DIR, clock=clock, os_source=LiveOs(), enable=enable,
                    state_file=default_state_dir() / SEEN_FILE)
    document, frame = load_fixture(fixture)
    offset = frame.get("utc_offset_s")
    clock = FixedClock(frame["clock"] + at_s, offset, "fixture") if once else ReplayClock(frame["clock"], offset, offset_s)
    return Host(plugins_dir=plugins_dir or FIXTURE_PLUGINS_DIR, clock=clock, os_source=FixtureOs(frame),
                fixture=fixture_info(document, frame), enable=enable,
                plugin_env={"IO_DIAGNOSTICS_FIXTURE": frame["name"], "IO_DIAGNOSTICS_FIXTURE_FILE": str(FIXTURE_FILE)})


__all__ = ["Clock", "ReplayClock", "FixedClock", "LiveOs", "FixtureOs", "MachineOs", "Host", "View", "make_host",
           "load_fixture", "resolve_fixture"]
