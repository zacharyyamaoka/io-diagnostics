"""Run plugin subprocesses, parse their JSON lines into ``contract.record`` types, cache by entity path, schedule.

One run = ``<command> diagnose --device <id>`` for one (plugin, device) pair, bounded by the plugin's
``timeout_s``. Whatever happens, the host stays up and the device row says what happened:

- a line that is not JSON, not a known record, from another schema, or a ``Fault`` (faults are the host's
  ``FaultManager``'s, never a plugin's) is REJECTED and counted;
- a record that parses but breaks the contract (``contract.record.validate``) is KEPT and its problems listed;
- a plugin that cannot start, exits non-zero, or hangs past its timeout (its whole process group is killed)
  becomes an ERROR check on its device row, authored by the host (``source = "io_diagnostics.runner"``).

A plugin whose ``describe`` declares ``follow`` is instead kept running: ONE ``<command> follow --device <id>`` per
device (``Follower``), its JSON lines applied as they arrive, restarted with a backoff when it exits.

Paths a plugin writes are RELATIVE to its device: ``""`` is the device itself, ``"can/15"`` is below it; a path
that already starts with the device's tree path (passed in ``IO_DIAGNOSTICS_DEVICE_PATH``) is accepted too. The
cache stores them relative, so a device that moves to another USB port keeps its children (``tree`` rebases them).
"""

from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import dataclasses
import datetime as dt
import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from io_diagnostics.plugins import PluginSpec
from io_diagnostics.probe import Blocked

from contract.record import (ERROR, OK, RECORD_VERSION, SCHEMA, UNKNOWN, WARN, Config, DiagnosticStatus, Entity, Event, Frame, KeyValue,
                             Measurement, State, from_dict, validate)

RUNNER_SOURCE = "io_diagnostics.runner"
# WHY: a driver emits the record kinds and describe fields newer than a host (state, config, frame, watch, tabs, streams) only when this
# says the host understands them, so a driver that lands first cannot break an older host such as Stable's release. Set in every plugin
# invocation: describe, diagnose, probe, watch, follow, act and stop.
RECORD_VERSION_ENV = "IO_DIAGNOSTICS_RECORD_VERSION"
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
        if kind not in ("entity", "status", "event", "state", "config", "frame"):
            parsed.rejected.append((number, f"record {kind!r} is not entity, status, event, state, config or frame"))
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
        elif isinstance(record, DiagnosticStatus):
            moved.append(dataclasses.replace(record, path=move(record.path), caused_by=tuple(map(move, record.caused_by))))
        else:  # event / state / config / frame: one path
            moved.append(dataclasses.replace(record, path=move(record.path)))
    return moved, alias


def _relativize(record, device_path: str):
    if isinstance(record, Entity):
        return dataclasses.replace(record, path=relative(record.path, device_path),
                                   via=relative(record.via, device_path), part_of=relative(record.part_of, device_path))
    if isinstance(record, DiagnosticStatus):
        # WHY caused_by too: a composite names its members the way it writes paths, relative to its device
        return dataclasses.replace(record, path=relative(record.path, device_path),
                                   caused_by=tuple(relative(member, device_path) for member in record.caused_by))
    return dataclasses.replace(record, path=relative(record.path, device_path))


def _absolutize(record, device_path: str):
    if isinstance(record, Entity):
        return dataclasses.replace(record, path=absolute(record.path, device_path),
                                   via=absolute(record.via, device_path), part_of=absolute(record.part_of, device_path))
    if isinstance(record, DiagnosticStatus):
        return rebase(record, device_path)
    return dataclasses.replace(record, path=absolute(record.path, device_path))


def rebase(status: DiagnosticStatus, device_path: str) -> DiagnosticStatus:
    """A cached status (paths relative to its device) -> tree paths: its own path and its ``caused_by`` members."""

    return dataclasses.replace(status, path=absolute(status.path, device_path),
                               caused_by=tuple(absolute(member, device_path) for member in status.caused_by))


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
    verb: str = "diagnose"  # diagnose | probe

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


class Running:
    """This host's running plugin and check processes, by session (each starts its own: ``start_new_session``).

    WHY by session and not by pid: the rack bus lock is taken by whatever the plugin's command started (``uv run`` ->
    python), never by the pid the host holds; every descendant keeps the session. So "is the lock holder one of OUR
    checks?" is ``os.getsid(holder) in sessions``, read passively, and a lock held by this host's own diagnose reads
    as its own check running, never as "blocked" (round 3 brief item 10)."""

    def __init__(self):
        self._sessions: dict[int, tuple[str, float]] = {}  # session id -> (what runs, monotonic start)
        self._passive: set[int] = set()  # the sessions among them that are the host's own follow / watch processes
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def run(self, process: subprocess.Popen, label: str, *, passive: bool = False):
        with self._lock:
            self._sessions[process.pid] = (label, time.monotonic())
            if passive:
                self._passive.add(process.pid)
        try:
            yield
        finally:
            with self._lock:
                self._sessions.pop(process.pid, None)
                self._passive.discard(process.pid)

    def is_passive(self, pid: int) -> bool:
        """``pid`` belongs to one of this host's own ``follow`` / ``watch`` processes: they hold the port or the camera
        for the page's sake, and give it up the moment the host needs it (``Host.release_watches``)."""

        try:
            session = os.getsid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            return False
        with self._lock:
            return session in self._passive or pid in self._passive

    def passive_sessions(self) -> list[int]:
        """The session ids of this host's own ``follow`` / ``watch`` processes (what ``is_passive`` tests a pid against)."""

        with self._lock:
            return sorted(self._passive)

    def owner(self, pid: int) -> tuple[str, float] | None:
        """``(what runs, seconds so far)`` when ``pid`` belongs to one of this host's running processes, else None."""

        try:
            session = os.getsid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            return None
        with self._lock:
            found = self._sessions.get(session) or self._sessions.get(pid)
        return (found[0], time.monotonic() - found[1]) if found else None


RUNNING = Running()


def _communicate(process: subprocess.Popen, timeout_s: float, label: str) -> tuple[str, str, bool]:
    """``(stdout, stderr, timed_out)``; past ``timeout_s`` the whole process group is killed. Registered in RUNNING."""

    with RUNNING.run(process, label):
        try:
            stdout, stderr = process.communicate(timeout=timeout_s)
            return stdout, stderr, False
        except subprocess.TimeoutExpired:
            _kill_group(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:  # a child kept the pipe open after the kill: give up on its output
                stdout, stderr = "", ""
            return stdout, stderr, True


def plugin_env(spec: PluginSpec, device: dict, device_path: str, now: float, extra: dict[str, str] | None = None) -> dict[str, str]:
    """What a plugin gets beside ``--device <id>``: its device's tree path and skeleton dict, and the host clock."""

    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)  # WHY: a uv-run host would otherwise leak its venv into a driver's own uv run
    env.update(spec.env)
    env.update({"IO_DIAGNOSTICS_DEVICE_PATH": device_path,
                "IO_DIAGNOSTICS_DEVICE": json.dumps(device, default=str),
                "IO_DIAGNOSTICS_NOW": repr(now),
                "IO_DIAGNOSTICS_SCHEMA": SCHEMA, RECORD_VERSION_ENV: RECORD_VERSION})
    env.update(extra or {})
    return env


def run_plugin(spec: PluginSpec, device: dict, device_path: str, now: Callable[[], float],
               extra_env: dict[str, str] | None = None, hardware_id: str = "", verb: str = "diagnose",
               timeout_s: float | None = None) -> RunOutcome:
    """Run ``diagnose`` (or the on-connect ``probe``, docs/probe-on-connect.md; same records, its own time limit) once
    for one device. Never raises for anything the plugin does. ``hardware_id`` is the OS row's repeatable id
    (default: the skeleton dict's), used to re-root a plugin that builds its own paths."""

    return dataclasses.replace(_run_verb(spec, device, device_path, now, extra_env, hardware_id, verb,
                                         timeout_s or spec.timeout_s), verb=verb)


def _run_verb(spec: PluginSpec, device: dict, device_path: str, now: Callable[[], float],
              extra_env: dict[str, str] | None, hardware_id: str, verb: str, timeout_s: float) -> RunOutcome:

    hardware_id = hardware_id or str((device.get("attrs") or {}).get("hardware_id") or "")

    started = now()
    clock = time.monotonic()
    argv = [*spec.command, verb, "--device", str(device.get("id", "")), *spec.diagnose_args]
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
    stdout, stderr, timed_out = _communicate(process, timeout_s, f"{spec.name} {verb} {device.get('id', '')}")
    returncode = process.returncode
    duration = time.monotonic() - clock
    parsed, problems = _records(spec, stdout or "", device_path, hardware_id)
    tail = (stderr or "").strip()[-STDERR_TAIL:]
    last_line = tail.splitlines()[-1] if tail else ""
    if timed_out:
        return _outcome(spec, device, device_path, started, duration, "timeout", returncode, parsed, problems, tail,
                        f"no answer within {timeout_s:g} s: killed; its last records age toward STALE")
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


def _records(spec: PluginSpec, text: str, device_path: str, hardware_id: str) -> tuple[Parsed, list[str]]:
    """Parse, re-root and relativize a plugin's lines (diagnose or follow); ``(parsed, contract problems)``."""

    parsed = parse_lines(text, replay=spec.replay)
    parsed.records, _alias = reroot(parsed.records, device_path, hardware_id)
    records, problems = [], []
    for record in parsed.records:
        relative_record = _relativize(record, device_path)
        problems += validate(_absolutize(relative_record, device_path))
        records.append(relative_record)
    parsed.records = records
    return parsed, problems


def act_argv(spec: PluginSpec, device: dict, action: str, *, node: int | None = None, confirm: str | None = None,
             dry_run: bool = False, new_id: int | None = None, extra: list[str] | None = None) -> list[str]:
    """``<command> act --device <id> --action <name> [--node N] [--new-id N] [--confirm T] [--dry-run]``
    (docs/actions-vocabulary-v0.1.md; ``--new-id`` is set_id's new bus id)."""

    argv = [*spec.command, "act", "--device", str(device.get("id", "")), "--action", action]
    if node is not None:
        argv += ["--node", str(node)]
    if new_id is not None:
        argv += ["--new-id", str(new_id)]
    if confirm:
        argv += ["--confirm", confirm]
    if dry_run:
        argv.append("--dry-run")
    return [*argv, *(extra or [])]  # v0.2: a configuration write's --key / --value / --to (config_edit.py)


def parse_action_lines(text: str) -> tuple[list[dict], list[str]]:
    """An act's record lines (``run_act``'s stdout, a run's record fd): every JSON object kept as the plugin wrote it;
    ``(records, why each other non-blank line was rejected)``."""

    records, rejected = [], []
    for number, raw in enumerate(text.splitlines(), start=1):
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
    return records, rejected


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
    action = argv[argv.index("--action") + 1] if "--action" in argv[:-1] else "act"
    stdout, stderr, timed_out = _communicate(process, spec.act_timeout_s, f"{spec.name} {action} {device.get('id', '')}")
    duration = time.monotonic() - clock
    records, rejected = parse_action_lines(stdout or "")
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


# ----- follow: one long-running process per device (docs/build-plan-v1.md "Plugin boundary", follow) -------------------
FOLLOW_BACKOFF_S = (1.0, 60.0)  # first restart delay, doubling up to the cap; back to the first after a healthy run
FOLLOW_HEALTHY_S = 60.0  # a follower that ran this long before exiting restarts at the first delay again
FOLLOW_MAX_LINE = 256 * 1024  # bytes: a longer line is rejected (and the rest of it skipped), never buffered whole
FOLLOW_SILENCE_FACTOR = 10.0  # no line for max(factor x its period, timeout_s): the follower is killed and restarted


def follow_period(answer: dict | None) -> float | None:
    """A plugin's ``describe`` ``follow`` -> its period in seconds (``true`` = 1 s); None = it does not follow."""

    declared = (answer or {}).get("follow")
    if declared is True:
        return 1.0
    if isinstance(declared, dict):
        period = declared.get("period_s", 1.0)
        return float(period) if isinstance(period, (int, float)) and not isinstance(period, bool) and period > 0 else 1.0
    return None


class Follower:
    """``<command> follow --device <id> [diagnose_args]`` kept alive for one job: every JSON line it prints is parsed
    like a diagnose line and handed to ``on_outcome`` at once (a one-line ``RunOutcome``), so a 1 Hz plugin fills the
    history at 1 Hz. When it exits it is restarted after a backoff; ``state()`` is what the device row's
    ``<plugin>: plugin run`` check says. Bounded: one line at most ``FOLLOW_MAX_LINE``, a stderr tail, counters.

    WHY a verb of its own and not a 1 s diagnose: each diagnose is a fresh ``uv run`` (~0.7 s of start-up), so the
    Computer page could never be live (Zach, 2026-10-23: "something that feels a bit like system monitor")."""

    def __init__(self, spec: PluginSpec, device: dict, device_path: str, period_s: float, now: Callable[[], float],
                 env: Callable[[], dict[str, str]], on_outcome: Callable[[RunOutcome], None], hardware_id: str = "",
                 backoff_s: tuple[float, float] | None = None):
        self.spec, self.device, self.device_path, self.period_s = spec, device, device_path, period_s
        self.now, self.env, self.on_outcome, self.hardware_id = now, env, on_outcome, hardware_id
        self.backoff_s = backoff_s or FOLLOW_BACKOFF_S
        self.process: subprocess.Popen | None = None
        self.started: float | None = None  # host clock, this process's start
        self.first_started: float | None = None
        self.restarts = 0
        self.lines = 0
        self.records = 0
        self.rejected = 0
        self.last_line: float | None = None  # monotonic
        self.exit: str = ""  # how the last process ended
        self.retry_at: float | None = None  # host clock: the next start, while down
        self.stderr = collections.deque(maxlen=20)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"io-follow-{spec.name}", daemon=True)

    verb = "follow"

    @property
    def argv(self) -> list[str]:
        return [*self.spec.command, self.verb, "--device", str(self.device.get("id", "")), *self.extra_args(), *self.spec.diagnose_args]

    def extra_args(self) -> list[str]:
        return []

    @property
    def silence_s(self) -> float:
        return max(FOLLOW_SILENCE_FACTOR * self.period_s, self.spec.timeout_s)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self.process
        if process is not None and process.poll() is None:
            _kill_group(process)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)

    def _loop(self) -> None:
        delay = self.backoff_s[0]
        while not self._stop.is_set():
            began = time.monotonic()
            self._run_once()
            if self._stop.is_set():
                return
            delay = self.backoff_s[0] if time.monotonic() - began >= FOLLOW_HEALTHY_S else delay
            self.retry_at = self.now() + delay
            self._report()
            if self._stop.wait(delay):
                return
            self.retry_at = None
            delay = min(delay * 2, self.backoff_s[1])
            self.restarts += 1

    def _run_once(self) -> None:
        env = plugin_env(self.spec, self.device, self.device_path, self.now(), self.env())
        try:
            process = subprocess.Popen(self.argv, cwd=self.spec.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env, start_new_session=True)
        except OSError as error:
            self.exit = f"could not start: {type(error).__name__}: {error}"
            self.process = None
            return
        self.process, self.started, self.exit = process, self.now(), ""
        self.first_started = self.first_started or self.started
        self.last_line = time.monotonic()
        threading.Thread(target=self._drain, args=(process,), name=f"io-follow-err-{self.spec.name}", daemon=True).start()
        watchdog = threading.Thread(target=self._watch, args=(process,), name=f"io-follow-dog-{self.spec.name}", daemon=True)
        watchdog.start()
        self._report()
        with RUNNING.run(process, f"{self.spec.name} {self.verb} {self.device.get('id', '')}", passive=True):
            while True:
                line = process.stdout.readline(FOLLOW_MAX_LINE + 1)
                if not line:
                    break
                if len(line) > FOLLOW_MAX_LINE and not line.endswith(b"\n"):
                    while (rest := process.stdout.readline(FOLLOW_MAX_LINE)) and not rest.endswith(b"\n"):
                        pass  # skip the rest of an over-long line without keeping it
                    self.lines += 1
                    self.rejected += 1
                    continue
                self._line(line.decode("utf-8", errors="replace"))
            process.wait()
        code = process.returncode
        if not self._stop.is_set():
            how = f"killed by signal {-code}" if code is not None and code < 0 else f"exited {code}"
            ran = time.monotonic() - (self.last_line or time.monotonic())
            tail = self.stderr[-1] if self.stderr else ""
            self.exit = f"{self.verb} {how}" + (f" ({ran:.0f} s after its last line)" if ran >= 1 else "") + (f": {tail}" if tail else "")

    def _drain(self, process: subprocess.Popen) -> None:
        for raw in iter(lambda: process.stderr.readline(4096), b""):
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                self.stderr.append(text[:300])

    def _watch(self, process: subprocess.Popen) -> None:
        while process.poll() is None and not self._stop.is_set():
            if time.monotonic() - (self.last_line or time.monotonic()) > self.silence_s:
                self.stderr.append(f"no line for {self.silence_s:g} s: killed")
                _kill_group(process)
                return
            time.sleep(min(1.0, self.period_s))

    def _line(self, text: str) -> None:
        self.last_line = time.monotonic()
        if not text.strip():
            return
        self.lines += 1
        parsed, problems = _records(self.spec, text, self.device_path, self.hardware_id)
        self.records += len(parsed.records)
        self.rejected += len(parsed.rejected)
        outcome = "malformed" if parsed.rejected or problems else "ok"
        message = (f"line rejected: {parsed.rejected[0][1]}" if parsed.rejected else problems[0]) if outcome == "malformed" else ""
        self.on_outcome(_outcome(self.spec, self.device, self.device_path, self.now(), 0.0, outcome, None, parsed, problems,
                                 "", message))

    def _report(self) -> None:
        """An empty outcome, so the host re-says the run state (started, down, restarting) without a line."""

        self.on_outcome(_outcome(self.spec, self.device, self.device_path, self.now(), 0.0, "ok", None, Parsed(), [], "", ""))

    def state(self) -> dict:
        running = self.process is not None and self.process.poll() is None and self.exit == ""
        return {"state": "running" if running else ("restarting" if self.retry_at is not None else "starting"),
                "pid": self.process.pid if running else None, "since": self.started if running else None,
                "first_started": self.first_started, "restarts": self.restarts, "lines": self.lines, "records": self.records,
                "rejected": self.rejected, "period_s": self.period_s, "retry_at": self.retry_at, "exit": self.exit or None,
                "stderr": self.stderr[-1] if self.stderr else None}


class Watcher(Follower):
    """``<command> watch --device <id> --path <relative path>``: a Follower of one ENTITY, run only while a page looks at it
    (docs/state-config-v1.md section 4). Same process rules as ``follow`` (one line at a time, a backoff, a silence
    kill, a bounded stderr); its lines are applied by ``Host._absorb_watch``, and it never touches the device row's
    ``<plugin>: plugin run`` check."""

    verb = "watch"

    def __init__(self, *args, path: str = "", **options):
        self.path = path
        super().__init__(*args, **options)

    def extra_args(self) -> list[str]:
        return ["--path", self.path]


class Presencer(Watcher):
    """``<command> presence --device <id>``: a Follower of one DEVICE's bus, run only while a page shows the Connected view
    (docs/api-v1.md "Presence"). Same process rules as ``watch``; its passes are read by ``Host._absorb_presence``."""

    verb = "presence"
    # WHY a short silence (not a follower's max(10 x period, timeout_s), 100 s for moteus): every pass prints its PresencePass line,
    # and a pass holds the rack bus lock, so a quiet presence is a hung one holding it (seen on the rack 2026-11-09). Killed, the
    # kernel drops its locks; the backoff starts a fresh one.
    silence_factor = 5.0
    silence_floor_s = 10.0

    @property
    def silence_s(self) -> float:
        return max(self.silence_factor * self.period_s, self.silence_floor_s)

    def extra_args(self) -> list[str]:
        return []


def follow_status(spec: PluginSpec, follower: Follower, hardware_id: str, now: float) -> DiagnosticStatus:
    """The device row's ``<plugin>: plugin run`` check for a follower: OK while it runs, ERROR while it is down."""

    state = follower.state()
    values = [KeyValue("command", " ".join(follower.argv)), KeyValue("outcome", state["state"]),
              KeyValue("restarts", str(state["restarts"]))]
    if state["stderr"]:
        values.append(KeyValue("stderr", state["stderr"]))
    measurements = (Measurement("lines", state["lines"], "1"), Measurement("records", state["records"], "1"),
                    Measurement("rejected lines", state["rejected"], "1"), Measurement("restarts", state["restarts"], "1"))
    if state["state"] == "running":
        since = dt.datetime.fromtimestamp(state["since"]).strftime("%H:%M:%S") if state["since"] else "?"
        level, code = (WARN, "PluginOutputMalformed") if state["rejected"] else (OK, "")
        message = (f"following since {since} (pid {state['pid']}) · every {follower.period_s:g} s · {state['records']} records"
                   + (f" · restarted {state['restarts']}×" if state["restarts"] else "")
                   + (f" · {state['rejected']} lines rejected" if state["rejected"] else ""))
    else:
        level = ERROR
        code = "PluginNotStarted" if (state["exit"] or "").startswith("could not start") else "PluginCrashed"
        wait = f" · restarting in {max(state['retry_at'] - now, 0):.0f} s" if state["retry_at"] is not None else " · starting"
        message = f"{state['exit'] or 'follow not running'}{wait}"
    # WHY the follow period as its promise: a follower that stops talking reads STALE on this row within seconds
    return DiagnosticStatus(level=level, name=f"{spec.name}: plugin run", message=message, hardware_id=hardware_id,
                            values=tuple(values), path="", code=code, stamp=now, received=now,
                            expected_period_s=max(follower.period_s, 1.0) if level == OK else None, source=RUNNER_SOURCE,
                            measurements=measurements)


SERIES_MAX_POINTS = 1200  # per measurement: the sparkline's recent past (about two minutes at 10 Hz)
SERIES_MAX_AGE_S = 120.0
HOST_SOURCES = "io_diagnostics."  # a record whose source starts with this is the host's own: never lifted into State


@dataclass
class JobCache:
    """What one (plugin, device) pair has told the host, latest per key, paths relative to the device.

    WHY merge rather than replace per run: entities are "sent once and on change" and a check that could not run
    this time (port busy) simply is not resent; its last record must stay and age toward STALE on the host's clock.

    v0.2 (docs/state-config-v1.md): ``states`` / ``configs`` / ``frames`` hold the latest ``state`` / ``config`` /
    ``frame`` record per key, and ``series`` the recent values of every State measurement (a state record's and a
    status's), each point numbered by ``serial`` so a page asks only for what is new.
    """

    entities: dict[str, Entity] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)  # entity paths in first-seen order (tree child order)
    icons: dict[str, str] = field(default_factory=dict)
    statuses: dict[tuple[str, str], DiagnosticStatus] = field(default_factory=dict)
    events: dict[tuple, Event] = field(default_factory=dict)
    host_status: DiagnosticStatus | None = None
    states: dict[tuple[str, str], State] = field(default_factory=dict)  # (path, group name)
    configs: dict[tuple[str, str], Config] = field(default_factory=dict)  # (path, key)
    frames: dict[tuple[str, str], Frame] = field(default_factory=dict)  # (path, stream)
    series: dict[tuple[str, str, str], collections.deque] = field(default_factory=dict)  # (path, group, key) -> (received, value, serial)
    serial: int = 0
    fresh_states: list[State] = field(default_factory=list)  # what the latest apply() found new
    fresh_frames: list[Frame] = field(default_factory=list)
    # WHY two rings beside the latest-per-key maps: the stock diagnostics' rates and delays count every State and frame record
    # (stock_diagnostics.py Inputs), not the 1 Hz history; metadata only, appended here, never an image read on the watch path
    arrivals: dict[tuple[str, str], collections.deque] = field(default_factory=dict)  # (path, group) -> each State record's received
    frame_log: dict[tuple[str, str], collections.deque] = field(default_factory=dict)  # (path, stream) -> its recent Frame records

    @staticmethod
    def _ring(rings: dict, key, item, at: float) -> None:
        ring = rings.setdefault(key, collections.deque(maxlen=SERIES_MAX_POINTS))
        ring.append(item)
        while len(ring) > 1 and at - (ring[0].received if isinstance(ring[0], Frame) else ring[0]) > SERIES_MAX_AGE_S:
            ring.popleft()

    def _point(self, path: str, group: str, measurement: Measurement, at: float) -> None:
        points = self.series.setdefault((path, group, measurement.key), collections.deque())
        self.serial += 1
        value = measurement.value
        points.append((at, value if isinstance(value, (bool, int, float)) or value is None else None, self.serial))
        while len(points) > SERIES_MAX_POINTS or (len(points) > 1 and at - points[0][0] > SERIES_MAX_AGE_S):
            points.popleft()

    def heard(self, path: str) -> float | None:
        """When the host last received a record of the plugin's about exactly this entity (host clock), or None."""

        times = [status.received for (status_path, _name), status in self.statuses.items()
                 if status_path == path and status.received is not None and not status.source.startswith(HOST_SOURCES)]
        times += [state.received for (state_path, _name), state in self.states.items() if state_path == path and state.received is not None]
        return max(times) if times else None

    def apply(self, outcome: RunOutcome, received: float) -> tuple[list[DiagnosticStatus], list[Event]]:
        """Merge one run; returns the statuses that are NEW evaluations and the events not seen before.

        WHY "new": the same evaluation delivered twice (a replay re-sending its recording) is one report, so the
        fault debounce counts reports, not deliveries."""

        fresh_statuses, fresh_events = [], []
        self.fresh_states, self.fresh_frames = [], []
        for index, record in enumerate(outcome.records):
            at = outcome.received.get(index, received)
            if isinstance(record, Entity):
                if record.path not in self.entities:
                    self.order.append(record.path)
                self.entities[record.path] = record
                if index in outcome.icons:
                    self.icons[record.path] = outcome.icons[index]
            elif isinstance(record, DiagnosticStatus):
                stamped = dataclasses.replace(record, received=at)
                key = (stamped.path, stamped.name)
                previous = self.statuses.get(key)
                if previous is None or (previous.stamp, previous.received) != (stamped.stamp, stamped.received):
                    fresh_statuses.append(stamped)
                    if not stamped.source.startswith(HOST_SOURCES):
                        for measurement in stamped.measurements:
                            self._point(stamped.path, stamped.name, measurement, at)
                self.statuses[key] = stamped
            elif isinstance(record, State):
                stamped = dataclasses.replace(record, received=at)
                self.states[(stamped.path, stamped.name)] = stamped
                self.fresh_states.append(stamped)
                self._ring(self.arrivals, (stamped.path, stamped.name), at, at)
                for measurement in stamped.measurements:
                    self._point(stamped.path, stamped.name, measurement, at)
            elif isinstance(record, Config):
                self.configs[(record.path, record.key)] = dataclasses.replace(record, stamp=record.stamp if record.stamp is not None else at)
            elif isinstance(record, Frame):
                stamped = dataclasses.replace(record, received=at)
                self.frames[(stamped.path, stamped.stream)] = stamped
                self.fresh_frames.append(stamped)
                self._ring(self.frame_log, (stamped.path, stamped.stream), stamped, at)
            elif isinstance(record, Event):
                if record.stamp is None:
                    record = dataclasses.replace(record, stamp=received)
                if record.received is None:
                    # WHY here: this is the moment the host first heard it, and the log keeps that (OpenTelemetry's ObservedTimestamp)
                    record = dataclasses.replace(record, received=received)
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
    probed: bool = False  # this connection's probe has run and was not blocked (docs/probe-on-connect.md)
    blocked: Blocked | None = None  # the latest probe found its port or bus held: who, and when it is probed again
    connection: Any = None  # the device's kernel device number when the probe last ran: a new one is a re-enumeration
    bringing_up: bool = False  # the connection's first look is in flight (its probe, or a plugin's first read): the adapter row pulses (tree.py ``activity``)

    @property
    def key(self) -> tuple[str, str]:
        return (self.spec.name, str(self.device.get("id", "")))


def describe(spec: PluginSpec, timeout_s: float | None = None, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    """``<command> describe`` -> its first JSON object, or ``{"error": ...}``. Informational only."""

    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.update(spec.env)
    env.update(extra_env or {})
    env[RECORD_VERSION_ENV] = RECORD_VERSION
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


# ----- Monitoring Plugins (Nagios) checks: any command, its exit code is the level ------------------------------
# The Monitoring Plugins Development Guidelines (https://www.monitoring-plugins.org/doc/guidelines.html), followed as
# written: §2.4 "Plugin Return Codes" (0 OK, 1 Warning, 2 Critical, 3 Unknown; codes outside 0-3 fall to the
# guidelines' "default catch-all" UNKNOWN); §2 "Plugin Output for Nagios" (the first stdout line is "SERVICE STATUS:
# Information text"; later lines are long output; perfdata follows a "|" on the first line and on later lines);
# §2.6 "Performance data" ('label'=value[UOM];[warn];[crit];[min];[max], space separated, quoted labels with ''
# for a quote, value "U" = could not be determined); §2.5 "Threshold and ranges" ([@]start:end, inclusive).
CHECK_SOURCE = "io_diagnostics.check"
CHECK_LEVEL = {0: (OK, ""), 1: (WARN, "CheckWarning"), 2: (ERROR, "CheckCritical"), 3: (UNKNOWN, "CheckUnknown")}
# §2.6's UOMs -> the record's UCUM units. WHY by name, never converted: the guidelines do not say whether KB is 1000
# or 1024 bytes; the number is shown as the check printed it. "" (a number of things) is dimensionless "1".
CHECK_UNITS = {"": "1", "s": "s", "ms": "ms", "us": "us", "%": "%", "B": "By", "KB": "kBy", "MB": "MBy", "GB": "GBy",
               "TB": "TBy", "c": "{counter}"}
_PERF_ITEM = re.compile(r"""\s*('(?:[^']|'')+'|[^'=\s]+)=(\S*)""")
_PERF_VALUE = re.compile(r"^(U|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)([A-Za-z%]*)$")
_NUMBER = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def _range(text: str) -> tuple[float | None, float | None, bool] | None:
    """§2.5 ``[@]start:end`` -> (low, high, inside); None = not a range. ``10`` = 0..10, ``10:`` = 10..inf,
    ``~:10`` = -inf..10; an alert is raised outside [low, high] (inclusive), or inside it with ``@``."""

    inside = text.startswith("@")
    body = text[1:] if inside else text
    start, colon, end = body.partition(":")
    if not colon:
        start, end = "0", start  # "start and ':' is not required if start=0"
        if not end:
            return None
    if start == "~":
        low = None  # "to specify negative infinity, use '~'"
    elif _NUMBER.match(start or "0"):
        low = float(start or "0")
    else:
        return None
    if end == "":
        high = None  # "start:" = end is infinity
    elif _NUMBER.match(end):
        high = float(end)
    else:
        return None
    if low is not None and high is not None and low > high:  # "start <= end"
        return None
    return low, high, inside


def parse_perfdata(text: str) -> tuple[list[Measurement], list[KeyValue], list[str]]:
    """§2.6 perfdata -> measurements (value, UCUM unit, warn/crit as limits), min/max as values, and what was ignored."""

    measurements, values, ignored, seen = [], [], [], set()
    position = 0
    text = text.strip()
    while position < len(text):
        found = _PERF_ITEM.match(text, position)
        if not found:
            rest = text[position:].split(None, 1)
            ignored.append(rest[0])
            position = len(text) - len(rest[1]) if len(rest) > 1 else len(text)
            continue
        position = found.end()
        label, fields = found[1], found[2].split(";")
        if label.startswith("'"):
            label = label[1:-1].replace("''", "'")
        value = _PERF_VALUE.match(fields[0])
        if not value or label in seen:
            ignored.append(found[0].strip())
            continue
        seen.add(label)
        number = None if value[1] == "U" else float(value[1])
        if number is not None and number.is_integer() and "." not in value[1] and "e" not in value[1].lower():
            number = int(number)
        uom = value[2]
        limits = {}
        for field_text, low_key, high_key in ((fields[1] if len(fields) > 1 else "", "warn_low", "warn_high"),
                                              (fields[2] if len(fields) > 2 else "", "error_low", "error_high")):
            if not field_text:
                continue
            parsed = _range(field_text)
            if parsed is None or parsed[2]:  # WHY "@" (alert inside) has no low/high limit form: kept as text
                values.append(KeyValue(f"{label} {'warn' if low_key == 'warn_low' else 'crit'}", field_text))
                continue
            limits[low_key], limits[high_key] = parsed[0], parsed[1]
        ordered = [limits.get(key) for key in ("error_low", "warn_low", "warn_high", "error_high")]
        present = [limit for limit in ordered if limit is not None]
        if present != sorted(present):  # a warn range wider than the crit range is not limits the record can hold
            values.append(KeyValue(f"{label} thresholds", ";".join(fields[1:3])))
            limits = {}
        measurements.append(Measurement(label, number, CHECK_UNITS.get(uom, uom), **limits))
        bounds = fields[3:5]
        if any(bounds):
            values.append(KeyValue(f"{label} min..max", " .. ".join(part or "?" for part in (bounds + [""])[:2])))
    return measurements, values, ignored


def parse_check_output(stdout: str, returncode: int | None) -> tuple[str, str, str, list[KeyValue], list[Measurement]]:
    """``(level, code, message, values, measurements)`` of one Monitoring Plugins run (§2, §2.4, §2.6)."""

    lines = (stdout or "").splitlines()
    first, _bar, perf = (lines[0] if lines else "").partition("|")
    long_lines, more_perf, in_perf = [], [], False
    for line in lines[1:]:
        if in_perf:
            more_perf.append(line)
            continue
        text, bar, rest = line.partition("|")
        if text.strip():
            long_lines.append(text.rstrip())
        if bar:
            in_perf = True
            more_perf.append(rest)
    level, code = CHECK_LEVEL.get(returncode, (UNKNOWN, "CheckUnknown"))
    message = first.strip() or "the check printed nothing"
    values = [KeyValue("exit_code", str(returncode))]
    if returncode not in CHECK_LEVEL:
        message = f"exit code {returncode} is not 0-3 (UNKNOWN by the guidelines' catch-all) · {message}"
    measurements, perf_values, ignored = parse_perfdata(" ".join([perf, *more_perf]))
    if long_lines:
        values.append(KeyValue("long_output", "\n".join(long_lines)))
    values += perf_values
    if ignored:
        values.append(KeyValue("perfdata_ignored", " ".join(ignored)))
    return level, code, message, values, measurements


def run_check(spec: PluginSpec, now: Callable[[], float], extra_env: dict[str, str] | None = None) -> DiagnosticStatus:
    """Run one check's command once, bounded by ``timeout_s`` (its process group killed past it). Its result is ONE
    status (path ``""``: the host rebases it onto the attached entity). Never raises for anything the command does."""

    started, clock = now(), time.monotonic()
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.update(spec.env)
    env.update(extra_env or {})
    stdout, returncode, timed_out, failed = "", None, False, ""
    try:
        process = subprocess.Popen(list(spec.command), cwd=spec.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, errors="replace", env=env, start_new_session=True)
    except OSError as error:
        failed = f"could not start: {type(error).__name__}: {error}"
    else:
        stdout, _stderr, timed_out = _communicate(process, spec.timeout_s, f"check {spec.name}")
        returncode = process.returncode
    duration = time.monotonic() - clock
    if failed or timed_out:
        # WHY UNKNOWN, not CRITICAL: the check did not finish, so nothing says the service is broken ("can't tell",
        # which never reads green and ranks above WARN here)
        level, code = UNKNOWN, "CheckNotStarted" if failed else "CheckTimeout"
        message = failed or f"no answer within {spec.timeout_s:g} s: killed"
        values, measurements = [KeyValue("exit_code", "none")], []
    else:
        level, code, message, values, measurements = parse_check_output(stdout, returncode)
    values = [KeyValue("command", " ".join(spec.command)), *values]
    if not any(measurement.key == "check duration" for measurement in measurements):
        measurements.append(Measurement("check duration", round(duration, 3), "s", warn_high=spec.timeout_s * 0.8))
    return DiagnosticStatus(level=level, name=spec.name, message=message, path="", code=code, values=tuple(values),
                            measurements=tuple(measurements), stamp=started, expected_period_s=spec.period_s,
                            source=CHECK_SOURCE)


class Gates:
    """One lock per ``exclusive`` group: at most one run (diagnose or act) of a group at a time, in this process.

    WHY in the host and not left to the plugins: both rack drivers take the rack bus lock with flock LOCK_NB, so
    two sibling jobs started together fail each other with ``BusLockHeld`` ("an experiment runs") although the
    holder was the host's own job (found live 2026-10-22 17:41, moteus vs RealSense and camera 6-1 vs 6-2). The
    plugin's lock still guards against OTHER processes; this one makes the host's own jobs take turns."""

    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._wanted: dict[str, int] = {}  # group -> people (a real act, a dry run) waiting for or holding it

    def _lock(self, group: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(group, threading.Lock())

    def person_waiting(self, group: str) -> bool:
        with self._guard:
            return self._wanted.get(group, 0) > 0

    @contextlib.contextmanager
    def hold(self, group: str, *, person: bool = False):
        """Wait for the group, run, release. ``""`` = no group: never waits.

        ``person``: a real act or a dry run a person asked for. WHY it goes first: while one is waiting or running,
        no scheduled diagnose or probe of the group starts (``try_take`` is False, a blocking routine ``hold`` waits),
        so a person's press is never starved by routine checks (docs/runs-v1.md section 6); a check already running
        finishes first (it is bounded by its own timeout)."""

        if not group:
            yield
            return
        lock = self._lock(group)
        if person:
            with self._guard:
                self._wanted[group] = self._wanted.get(group, 0) + 1
            try:
                with lock:
                    yield
            finally:
                with self._guard:
                    self._wanted[group] -= 1
            return
        while True:
            if self.person_waiting(group):
                time.sleep(0.05)
                continue
            if lock.acquire(timeout=0.2):
                if self.person_waiting(group):  # one arrived while this one was taking it: it goes first
                    lock.release()
                    continue
                break
        try:
            yield
        finally:
            lock.release()

    def try_take(self, group: str) -> bool:
        if not group:
            return True
        if self.person_waiting(group):
            return False
        lock = self._lock(group)
        if not lock.acquire(blocking=False):
            return False
        if self.person_waiting(group):
            lock.release()
            return False
        return True

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
