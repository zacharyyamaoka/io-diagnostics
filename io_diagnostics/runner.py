"""Run plugin subprocesses, parse their JSON lines into ``contract.record`` types, cache by entity path, schedule.

One run = ``<command> diagnose --device <id>`` for one (plugin, device) pair, bounded by the plugin's
``timeout_s``. Whatever happens, the host stays up and the device row says what happened:

- a line that is not JSON, not a known record, from another schema, or a ``Fault`` (faults are the host's
  ``FaultManager``'s, never a plugin's) is REJECTED and counted;
- a record that parses but breaks the contract (``contract.record.validate``) is KEPT and its problems listed;
- a plugin that cannot start, exits non-zero, or hangs past its timeout (its whole process group is killed)
  becomes an ERROR check on its device row, authored by the host (``source = "io_diagnostics.runner"``).

Paths a plugin writes are RELATIVE to its device: ``""`` is the device itself, ``"can/15"`` is below it; a path
that already starts with the device's tree path (passed in ``IO_DIAGNOSTICS_DEVICE_PATH``) is accepted too. The
cache stores them relative, so a device that moves to another USB port keeps its children (``tree`` rebases them).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from io_diagnostics.plugins import PluginSpec

from contract.record import (ERROR, OK, SCHEMA, WARN, DiagnosticStatus, Entity, Event, KeyValue, Measurement,
                             from_dict, validate)

RUNNER_SOURCE = "io_diagnostics.runner"
STDERR_TAIL = 800
MAX_EVENTS_PER_JOB = 500
OUTCOME_LEVEL = {"ok": (OK, ""), "malformed": (WARN, "PluginOutputMalformed"), "crashed": (ERROR, "PluginCrashed"),
                 "timeout": (ERROR, "PluginTimeout"), "not_started": (ERROR, "PluginNotStarted")}
# WHY only 0 counts as "completed": levels travel IN the records, and an uncaught Python exception also exits 1, so
# accepting the Monitoring Plugins codes (1 WARNING ...) would pass off a traceback after a partial print as a run.


@dataclass
class Parsed:
    """One plugin run's stdout, parsed. Paths are still as the plugin wrote them."""

    records: list = field(default_factory=list)  # Entity / DiagnosticStatus / Event
    received: dict[int, float] = field(default_factory=dict)  # record index -> recorded receive time (replay only)
    icons: dict[int, str] = field(default_factory=dict)  # record index -> optional icon hint on an Entity line
    rejected: list[tuple[int, str]] = field(default_factory=list)  # (line number, why)
    lines: int = 0


def parse_lines(text: str, *, replay: bool = False) -> Parsed:
    parsed = Parsed()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parsed.lines += 1
        try:
            data = json.loads(line)
        except json.JSONDecodeError as error:
            parsed.rejected.append((number, f"not JSON ({error.msg})"))
            continue
        if not isinstance(data, dict):
            parsed.rejected.append((number, f"a JSON {type(data).__name__}, not a record object"))
            continue
        kind = data.get("record")
        if kind == "fault":
            parsed.rejected.append((number, "a Fault: faults are kept by the host's FaultManager, never sent by a plugin"))
            continue
        if kind == "action":
            parsed.rejected.append((number, "a bare Action: actions travel inside their Entity's 'actions'"))
            continue
        if kind not in ("entity", "status", "event"):
            parsed.rejected.append((number, f"record {kind!r} is not entity, status or event"))
            continue
        if data.get("schema", SCHEMA) != SCHEMA:
            parsed.rejected.append((number, f"schema {data.get('schema')!r} is not {SCHEMA!r}"))
            continue
        recorded_received = data.pop("received", None)
        icon = data.pop("icon", None)
        try:
            record = from_dict(data)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            parsed.rejected.append((number, f"{type(error).__name__}: {error}"))
            continue
        index = len(parsed.records)
        parsed.records.append(record)
        if replay and isinstance(recorded_received, (int, float)) and not isinstance(recorded_received, bool):
            parsed.received[index] = float(recorded_received)
        if isinstance(icon, str) and isinstance(record, Entity):
            parsed.icons[index] = icon
    return parsed


def relative(path: str | None, device_path: str) -> str | None:
    """A plugin path -> relative to its device (``""`` = the device). ``None`` stays ``None`` (no edge)."""

    if path is None:
        return None
    text = str(path).strip()
    if text == device_path:
        return ""
    if device_path and text.startswith(device_path + "/"):
        return text[len(device_path) + 1:]
    text = text.removeprefix("./").strip("/")
    return "" if text == "." else text


def absolute(path: str | None, device_path: str) -> str | None:
    if path is None:
        return None
    return device_path if path == "" else f"{device_path}/{path}"


def reroot(records: list, device_path: str, hardware_id: str) -> tuple[list, str | None]:
    """A plugin that builds its own paths (``host/usb-6-2/d405-<serial>``) is re-rooted by IDENTITY: the entity that
    carries the device's ``hardware_id`` IS the device row, and everything under its path moves with it.

    WHY identity, not a guess at path shapes: v0's attach rule is the repeatable ``hardware_id`` (record v0 §4,
    "the payoff of repeatable identity"); every observer computes ``usb:<vid>:<pid>:<serial>`` the same way."""

    if not hardware_id:
        return records, None
    alias = next((record.path for record in records if isinstance(record, Entity) and record.hardware_id == hardware_id
                  and not record.part_of and record.path not in (None, "", ".", device_path)
                  and not record.path.startswith(device_path + "/")), None)
    if alias is None:
        return records, None

    def move(path: str | None) -> str | None:
        if path is None:
            return None
        if path == alias:
            return device_path
        return device_path + path[len(alias):] if path.startswith(alias + "/") else path

    moved = []
    for record in records:
        if isinstance(record, Entity):
            moved.append(dataclasses.replace(record, path=move(record.path), via=move(record.via), part_of=move(record.part_of)))
        else:
            moved.append(dataclasses.replace(record, path=move(record.path)))
    return moved, alias


def _relativize(record, device_path: str):
    if isinstance(record, Entity):
        return dataclasses.replace(record, path=relative(record.path, device_path),
                                   via=relative(record.via, device_path), part_of=relative(record.part_of, device_path))
    return dataclasses.replace(record, path=relative(record.path, device_path))


def _absolutize(record, device_path: str):
    if isinstance(record, Entity):
        return dataclasses.replace(record, path=absolute(record.path, device_path),
                                   via=absolute(record.via, device_path), part_of=absolute(record.part_of, device_path))
    return dataclasses.replace(record, path=absolute(record.path, device_path))


@dataclass
class RunOutcome:
    plugin: str
    device_id: str
    device_path: str
    started: float  # host clock
    duration_s: float
    outcome: str  # ok | malformed | crashed | timeout | not_started
    returncode: int | None
    records: list  # paths RELATIVE to the device
    received: dict[int, float]
    icons: dict[int, str]
    rejected: list[tuple[int, str]]
    problems: list[str]  # contract.record.validate notes on kept records
    stderr_tail: str
    message: str

    def summary(self) -> dict:
        return {"started": self.started, "duration_s": round(self.duration_s, 3), "outcome": self.outcome,
                "returncode": self.returncode, "accepted": len(self.records), "rejected": len(self.rejected),
                "problems": self.problems[:10], "rejected_lines": [f"line {number}: {why}" for number, why in self.rejected[:10]],
                "message": self.message}


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def plugin_env(spec: PluginSpec, device: dict, device_path: str, now: float, extra: dict[str, str] | None = None) -> dict[str, str]:
    """What a plugin gets beside ``--device <id>``: its device's tree path and skeleton dict, and the host clock."""

    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)  # WHY: a uv-run host would otherwise leak its venv into a driver's own uv run
    env.update(spec.env)
    env.update({"IO_DIAGNOSTICS_DEVICE_PATH": device_path,
                "IO_DIAGNOSTICS_DEVICE": json.dumps(device, default=str),
                "IO_DIAGNOSTICS_NOW": repr(now),
                "IO_DIAGNOSTICS_SCHEMA": SCHEMA})
    env.update(extra or {})
    return env


def run_plugin(spec: PluginSpec, device: dict, device_path: str, now: Callable[[], float],
               extra_env: dict[str, str] | None = None, hardware_id: str = "") -> RunOutcome:
    """Run ``diagnose`` once for one device. Never raises for anything the plugin does. ``hardware_id`` is the OS
    row's repeatable id (default: the skeleton dict's), used to re-root a plugin that builds its own paths."""

    hardware_id = hardware_id or str((device.get("attrs") or {}).get("hardware_id") or "")

    started = now()
    clock = time.monotonic()
    argv = [*spec.command, "diagnose", "--device", str(device.get("id", "")), *spec.diagnose_args]
    returncode = None
    stdout = stderr = ""
    try:
        process = subprocess.Popen(argv, cwd=spec.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, errors="replace",
                                   env=plugin_env(spec, device, device_path, started, extra_env),
                                   start_new_session=True)  # WHY: so a timeout kills the plugin's children too
    except OSError as error:
        return _outcome(spec, device, device_path, started, time.monotonic() - clock, "not_started", None, Parsed(), [],
                        "", f"could not start: {type(error).__name__}: {error}")
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=spec.timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # a child kept the pipe open after the kill: give up on its output
            stdout, stderr = "", ""
    returncode = process.returncode
    duration = time.monotonic() - clock
    parsed = parse_lines(stdout or "", replay=spec.replay)
    parsed.records, _alias = reroot(parsed.records, device_path, hardware_id)
    records, problems = [], []
    for record in parsed.records:
        relative_record = _relativize(record, device_path)
        problems += validate(_absolutize(relative_record, device_path))
        records.append(relative_record)
    parsed.records = records
    tail = (stderr or "").strip()[-STDERR_TAIL:]
    last_line = tail.splitlines()[-1] if tail else ""
    if timed_out:
        return _outcome(spec, device, device_path, started, duration, "timeout", returncode, parsed, problems, tail,
                        f"no answer within {spec.timeout_s:g} s: killed; its last records age toward STALE")
    if returncode != 0:  # the records it printed before failing are still kept; the row says it failed
        how = f"killed by signal {-returncode}" if returncode is not None and returncode < 0 else f"exited {returncode}"
        return _outcome(spec, device, device_path, started, duration, "crashed", returncode, parsed, problems, tail,
                        f"{how} after {duration:.1f} s" + (f": {last_line}" if last_line else ""))
    if parsed.rejected or problems:
        bits = []
        if parsed.rejected:
            number, why = parsed.rejected[0]
            bits.append(f"{len(parsed.rejected)} of {parsed.lines} lines rejected (line {number}: {why})")
        if problems:
            bits.append(f"{len(problems)} contract problem{'s' if len(problems) != 1 else ''} ({problems[0]})")
        return _outcome(spec, device, device_path, started, duration, "malformed", returncode, parsed, problems, tail,
                        " · ".join(bits) + f" · {len(parsed.records)} records used")
    return _outcome(spec, device, device_path, started, duration, "ok", returncode, parsed, problems, tail,
                    f"ran in {duration:.2g} s · {len(parsed.records)} records")


def act_argv(spec: PluginSpec, device: dict, action: str, *, node: int | None = None, confirm: str | None = None,
             dry_run: bool = False) -> list[str]:
    """``<command> act --device <id> --action <name> [--node N] [--confirm T] [--dry-run]`` (docs/actions-vocabulary-v0.1.md)."""

    argv = [*spec.command, "act", "--device", str(device.get("id", "")), "--action", action]
    if node is not None:
        argv += ["--node", str(node)]
    if confirm:
        argv += ["--confirm", confirm]
    if dry_run:
        argv.append("--dry-run")
    return argv


def run_act(spec: PluginSpec, device: dict, device_path: str, argv: list[str], now: Callable[[], float],
            extra_env: dict[str, str] | None = None) -> dict:
    """Run one ``act`` argv, bounded by the plugin's ``act_timeout_s`` (never its diagnose timeout: a firmware flash
    or a calibration outlasts any diagnose). Never raises for anything the plugin does.

    The answer's lines are kept as the plugin wrote them (``records``: every JSON object, e.g. an Event
    ``ConfirmationRequired`` or a dry run's ``action-result``); the host does not reinterpret an action's result.
    The first ``action-result`` line (docs/actions-vocabulary-v0.1.md: action, title, dry_run, danger, path,
    hardware_id, command_kind argv|sdk, command, preconditions, confirm_required / confirm_token / confirm_given,
    source, stamp, message) is also returned as ``action_result``, and its ``message`` becomes the result's."""

    started = now()
    clock = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=spec.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, errors="replace",
                                   env=plugin_env(spec, device, device_path, started, extra_env), start_new_session=True)
    except OSError as error:
        return {"outcome": "not_started", "returncode": None, "duration_s": 0.0, "records": [], "rejected": [],
                "stderr_tail": "", "message": f"could not start: {type(error).__name__}: {error}"}
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=spec.act_timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    duration = time.monotonic() - clock
    records, rejected = [], []
    for number, raw in enumerate((stdout or "").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            rejected.append(f"line {number}: not JSON ({error.msg})")
            continue
        if isinstance(data, dict):
            records.append(data)
        else:
            rejected.append(f"line {number}: a JSON {type(data).__name__}, not an object")
    tail = (stderr or "").strip()[-STDERR_TAIL:]
    if timed_out:
        outcome, message = "timeout", f"no answer within {spec.act_timeout_s:g} s (act_timeout_s): killed"
    elif process.returncode != 0:
        last_line = tail.splitlines()[-1] if tail else ""
        outcome, message = "crashed", f"exited {process.returncode} after {duration:.1f} s" + (f": {last_line}" if last_line else "")
    else:
        outcome, message = "ok", f"ran in {duration:.2g} s · {len(records)} lines"
    result = next((record for record in records if record.get("record") == "action-result"), None)
    if result is not None and outcome == "ok" and result.get("message"):
        message = str(result["message"])
    return {"outcome": outcome, "returncode": process.returncode, "duration_s": round(duration, 3), "records": records,
            "action_result": result, "rejected": rejected, "stderr_tail": tail, "message": message}


def _outcome(spec, device, device_path, started, duration, outcome, returncode, parsed: Parsed, problems, tail, message) -> RunOutcome:
    return RunOutcome(plugin=spec.name, device_id=str(device.get("id", "")), device_path=device_path, started=started,
                      duration_s=duration, outcome=outcome, returncode=returncode, records=parsed.records,
                      received=parsed.received, icons=parsed.icons, rejected=parsed.rejected, problems=problems,
                      stderr_tail=tail, message=message)


def host_status(spec: PluginSpec, outcome: RunOutcome, hardware_id: str, now: float) -> DiagnosticStatus:
    """The host's own check on the device row: did the plugin run? (relative path ``""`` = the device)."""

    level, code = OUTCOME_LEVEL[outcome.outcome]
    values = [KeyValue("command", " ".join(spec.command)), KeyValue("outcome", outcome.outcome),
              KeyValue("returncode", str(outcome.returncode))]
    values += [KeyValue(f"rejected {index + 1}", f"line {number}: {why}") for index, (number, why) in enumerate(outcome.rejected[:3])]
    values += [KeyValue(f"problem {index + 1}", problem) for index, problem in enumerate(outcome.problems[:3])]
    if outcome.stderr_tail:
        values.append(KeyValue("stderr", outcome.stderr_tail.splitlines()[-1][:300]))
    measurements = (Measurement("duration", round(outcome.duration_s, 3), "s", warn_high=spec.timeout_s * 0.8),
                    Measurement("records", len(outcome.records), "1"),
                    Measurement("rejected lines", len(outcome.rejected), "1"))
    return DiagnosticStatus(level=level, name=f"{spec.name}: plugin run", message=outcome.message, hardware_id=hardware_id,
                            values=tuple(values), path="", code=code, stamp=now, received=now,
                            expected_period_s=spec.period_s, source=RUNNER_SOURCE, measurements=measurements)


@dataclass
class JobCache:
    """What one (plugin, device) pair has told the host, latest per key, paths relative to the device.

    WHY merge rather than replace per run: entities are "sent once and on change" and a check that could not run
    this time (port busy) simply is not resent; its last record must stay and age toward STALE on the host's clock.
    """

    entities: dict[str, Entity] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)  # entity paths in first-seen order (tree child order)
    icons: dict[str, str] = field(default_factory=dict)
    statuses: dict[tuple[str, str], DiagnosticStatus] = field(default_factory=dict)
    events: dict[tuple, Event] = field(default_factory=dict)
    host_status: DiagnosticStatus | None = None

    def apply(self, outcome: RunOutcome, received: float) -> tuple[list[DiagnosticStatus], list[Event]]:
        """Merge one run; returns the statuses that are NEW evaluations and the events not seen before.

        WHY "new": the same evaluation delivered twice (a replay re-sending its recording) is one report, so the
        fault debounce counts reports, not deliveries."""

        fresh_statuses, fresh_events = [], []
        for index, record in enumerate(outcome.records):
            if isinstance(record, Entity):
                if record.path not in self.entities:
                    self.order.append(record.path)
                self.entities[record.path] = record
                if index in outcome.icons:
                    self.icons[record.path] = outcome.icons[index]
            elif isinstance(record, DiagnosticStatus):
                stamped = dataclasses.replace(record, received=outcome.received.get(index, received))
                key = (stamped.path, stamped.name)
                previous = self.statuses.get(key)
                if previous is None or (previous.stamp, previous.received) != (stamped.stamp, stamped.received):
                    fresh_statuses.append(stamped)
                self.statuses[key] = stamped
            elif isinstance(record, Event):
                if record.stamp is None:
                    record = dataclasses.replace(record, stamp=received)
                key = (record.source or outcome.plugin, record.seq) if record.seq is not None else (
                    record.code, record.stamp, record.path, record.message)
                if key not in self.events:
                    self.events[key] = record
                    fresh_events.append(record)
        if len(self.events) > MAX_EVENTS_PER_JOB:
            for key in sorted(self.events, key=lambda key: self.events[key].stamp or 0)[:len(self.events) - MAX_EVENTS_PER_JOB]:
                self.events.pop(key)
        return fresh_statuses, fresh_events


@dataclass
class Job:
    spec: PluginSpec
    device: dict
    device_path: str
    cache: JobCache = field(default_factory=JobCache)
    last: RunOutcome | None = None
    next_due: float = 0.0  # time.monotonic()
    running: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.spec.name, str(self.device.get("id", "")))


def describe(spec: PluginSpec, timeout_s: float | None = None, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    """``<command> describe`` -> its first JSON object, or ``{"error": ...}``. Informational only."""

    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.update(spec.env)
    env.update(extra_env or {})
    try:
        completed = subprocess.run([*spec.command, "describe"], cwd=spec.cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                   text=True, errors="replace", timeout=timeout_s or spec.timeout_s, env=env,
                                   start_new_session=True)
    except subprocess.TimeoutExpired:
        return {"error": f"describe did not answer within {timeout_s or spec.timeout_s:g} s"}
    except OSError as error:
        return {"error": f"could not start: {type(error).__name__}: {error}"}
    for line in completed.stdout.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            if data.get("schema") not in (None, SCHEMA):
                data["error"] = f"schema {data.get('schema')!r} is not {SCHEMA!r}"
            return data
    return {"error": f"describe printed no JSON object (exit {completed.returncode})"}


class Gates:
    """One lock per ``exclusive`` group: at most one run (diagnose or act) of a group at a time, in this process.

    WHY in the host and not left to the plugins: both rack drivers take the rack bus lock with flock LOCK_NB, so
    two sibling jobs started together fail each other with ``BusLockHeld`` ("an experiment runs") although the
    holder was the host's own job (found live 2026-10-22 17:41, moteus vs RealSense and camera 6-1 vs 6-2). The
    plugin's lock still guards against OTHER processes; this one makes the host's own jobs take turns."""

    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, group: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(group, threading.Lock())

    @contextlib.contextmanager
    def hold(self, group: str):
        """Wait for the group, run, release. ``""`` = no group: never waits."""

        if not group:
            yield
            return
        with self._lock(group):
            yield

    def try_take(self, group: str) -> bool:
        return True if not group else self._lock(group).acquire(blocking=False)

    def release(self, group: str) -> None:
        if group:
            self._lock(group).release()

    def busy(self) -> list[str]:
        with self._guard:
            return sorted(group for group, lock in self._locks.items() if lock.locked())


class Scheduler:
    """Runs due jobs on a small thread pool, each at most once at a time, every ``period_s`` (real time), and never
    two jobs of one ``exclusive`` group at once: a due job whose group is busy waits for a later tick (queued, not
    failed), oldest due first.

    WHY real (monotonic) time for scheduling and the host clock only for stamps: a replay clock can be offset
    from wall time, and a run's cadence must not depend on it. WHY the group is taken before submitting, not
    inside the worker: a worker blocked on a busy group would hold one of the few pool threads idle.
    """

    def __init__(self, jobs: Callable[[], list[Job]], run: Callable[[Job], None], workers: int = 4, tick_s: float = 0.25,
                 gates: Gates | None = None):
        self._jobs, self._run, self._tick_s = jobs, run, tick_s
        self.gates = gates or Gates()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="io-plugin")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="io-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            due = sorted((job for job in self._jobs() if not job.running and job.next_due <= now), key=lambda job: job.next_due)
            for job in due:
                if not self.gates.try_take(job.spec.exclusive):
                    continue  # its group is running: stays due, so it goes first once the group is free
                job.running = True
                job.next_due = now + job.spec.period_s
                self._pool.submit(self._guarded, job)
            self._stop.wait(self._tick_s)

    def _guarded(self, job: Job) -> None:
        try:
            self._run(job)
        finally:
            job.running = False
            self.gates.release(job.spec.exclusive)
