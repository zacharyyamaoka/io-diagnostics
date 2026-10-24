"""Runs: every real ``act`` as a process on a pseudo-terminal, with live output, input, Stop and a recording
(docs/runs-v1.md is the contract; this file is its host half).

One ``Run`` = one plugin ``act`` command. It gets a PTY for stdin / stdout / stderr (so tools print progressively,
redraw with ``\\r`` and can wait for a typed answer) and, beside it, a pipe named by ``IO_DIAGNOSTICS_RECORD_FD`` where a
plugin writes its JSON-line records. The terminal's bytes go to a ring buffer (the stream endpoint replays and follows
it) and, as they happen, to an asciinema v2 ``.cast`` under the state dir. A ``RunRegistry`` keeps the runs, loads the
finished ones from disk and prunes the oldest past a size budget.

WHY a PTY and not pipes: ``communicate()`` returned everything at the end (Zach, 2026-10-24: calibrating CAN 1, "it would
be cool if I could see the terminal output ... or enter user input"); a tool only prompts, or draws a progress bar, on a
terminal. WHY a record fd: records are the protocol and the terminal is for a person; mixed on one stream a prompt or a
progress bar would corrupt the records.
"""

from __future__ import annotations

import base64
import codecs
import collections
import contextlib
import dataclasses
import errno
import fcntl
import json
import os
import pty
import re
import secrets
import select
import signal
import struct
import subprocess
import termios
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager

from contract.record import Event
from io_diagnostics.runner import RUNNING, STDERR_TAIL, _kill_group, parse_action_lines

SOURCE = "io_diagnostics.runs"
RECORD_FD_ENV = "IO_DIAGNOSTICS_RECORD_FD"
RUN_ID_ENV = "IO_DIAGNOSTICS_RUN_ID"
RING_BYTES = 2 * 1024 * 1024
CAST_BUDGET_BYTES = 64 * 1024 * 1024
INPUT_MAX = 4096
GRACE_S = 3.0  # SIGINT, then SIGTERM after this, then SIGKILL after this again
STOP_TIMEOUT_S = 30.0  # the plugin's own stop verb
DEFAULT_COLS, DEFAULT_ROWS = 120, 30
STREAM_EVENT_BYTES = 64 * 1024
KEEPALIVE_S = 15.0
MAX_EVENTS = 500
ACTIVE = ("queued", "running")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]")


class RunError(Exception):
    """A request about a run the host refuses; ``code`` is the API's ``refused`` word, ``status`` its HTTP status."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.status = code, status


def plain_text(raw: bytes | str) -> str:
    """Terminal bytes as readable lines: escape sequences removed, a carriage return keeps only what was drawn last."""

    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    lines = []
    for line in _ANSI.sub("", text).split("\n"):
        lines.append(line.rstrip("\r").split("\r")[-1])
    return "\n".join(lines)


def redact(argv: list[str]) -> list[str]:
    """The argv as a run shows it: the value after ``--confirm`` is the dry run's token, never kept."""

    out, hide = [], False
    for word in argv:
        out.append("<token>" if hide else word)
        hide = word == "--confirm"
    return out


@dataclass
class RunSpec:
    """What a run starts: built by ``Host`` from the plugin's declaration and the entity it acts on."""

    plugin: str
    device_id: str
    device_path: str
    path: str  # the entity the action ran on
    action: str
    title: str
    label: str
    danger: str
    argv: list[str]
    env: dict[str, str]
    cwd: str | None
    timeout_s: float
    node: int | None = None
    new_id: int | None = None
    hardware_id: str = ""
    stop_argv: list[str] | None = None  # the plugin's stop verb (moves-class runs); None: it declares none
    group: str = ""  # the plugin's ``exclusive`` group the run holds for its whole life
    hold: Callable[[], ContextManager] = lambda: _no_hold()  # the plugin's ``exclusive`` group


class _no_hold:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Run:
    def __init__(self, registry: "RunRegistry", run_id: str, spec: RunSpec, *, interactive: bool):
        self.registry = registry
        self.id = run_id
        self.spec = spec
        self.interactive_start = interactive
        self.state = "queued"
        self.started = registry.now()
        self.ended: float | None = None
        self._t0 = time.monotonic()
        self._t_end: float | None = None
        self.exit_code: int | None = None
        self.stopping = False
        self.stop_requested = False
        self.stage: str | None = None
        self.stop_info: dict | None = None
        self.result: dict | None = None
        self.tail = ""
        self.cols, self.rows = DEFAULT_COLS, DEFAULT_ROWS
        self.cond = threading.Condition()
        self.chunks: collections.deque[tuple[int, float, bytes]] = collections.deque()  # (offset, seconds, bytes)
        self.first_offset = 0
        self.end_offset = 0
        self.dropped = 0
        self._held = 0
        self.process: subprocess.Popen | None = None
        self.master: int | None = None
        self.exited = threading.Event()
        self.finished = threading.Event()
        self.timed_out = False
        self.from_disk = False
        self._buffer_loaded = True
        self._cast = None
        self._cast_lock = threading.Lock()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._record_buffer = bytearray()

    # --- files ----------------------------------------------------------------------------------------------------
    @property
    def cast_path(self) -> Path | None:
        return self.registry.directory / f"{self.id}.cast" if self.registry.directory else None

    @property
    def meta_path(self) -> Path | None:
        return self.registry.directory / f"{self.id}.json" if self.registry.directory else None

    def _open_cast(self) -> None:
        path = self.cast_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._cast = open(path, "w", encoding="utf-8")
            header = {"version": 2, "width": self.cols, "height": self.rows, "timestamp": int(time.time()),
                      "env": {"TERM": "xterm-256color"}, "title": self.spec.label}
            self._cast.write(json.dumps(header, ensure_ascii=False) + "\n")
            self._cast.flush()
        except OSError:
            self._cast = None  # a read-only state dir: the run still works, only unrecorded

    def _close_cast(self) -> None:
        with self._cast_lock:
            if self._cast is not None:
                tail = self._decoder.decode(b"", final=True)
                if tail:
                    self._cast.write(json.dumps([round(self.elapsed(), 6), "o", tail], ensure_ascii=False) + "\n")
                self._cast.close()
                self._cast = None

    def persist(self) -> None:
        path = self.meta_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.to_json(detail=True), ensure_ascii=False, default=str))
            os.replace(temporary, path)
        except OSError:
            pass

    # --- output ---------------------------------------------------------------------------------------------------
    def elapsed(self) -> float:
        return (self._t_end if self._t_end is not None else time.monotonic()) - self._t0

    def append(self, data: bytes) -> None:
        if not data:
            return
        t = time.monotonic() - self._t0
        with self.cond:
            self.chunks.append((self.end_offset, t, data))
            self.end_offset += len(data)
            self._held += len(data)
            while self._held > self.registry.ring_bytes and len(self.chunks) > 1:
                old = self.chunks.popleft()
                self._held -= len(old[2])
                self.dropped += len(old[2])
            self.first_offset = self.chunks[0][0]
            self.cond.notify_all()
        with self._cast_lock:
            if self._cast is not None:
                text = self._decoder.decode(data)
                if text:
                    try:
                        self._cast.write(json.dumps([round(t, 6), "o", text], ensure_ascii=False) + "\n")
                        self._cast.flush()
                    except OSError:
                        pass

    def note(self, text: str) -> None:
        """A dim line from the host itself, in the terminal and so in the recording."""

        self.append(f"\x1b[2m{text}\x1b[0m\r\n".encode())

    def _load_buffer(self) -> None:
        """A finished run from a previous host process: its ring is rebuilt from its recording."""

        if self._buffer_loaded:
            return
        self._buffer_loaded = True
        path = self.cast_path
        if path is None or not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines[1:]:
            try:
                t, kind, text = json.loads(line)
            except (ValueError, TypeError):
                continue
            if kind != "o":
                continue
            data = text.encode()
            self.chunks.append((self.end_offset, float(t), data))
            self.end_offset += len(data)
            self._held += len(data)
            while self._held > self.registry.ring_bytes and len(self.chunks) > 1:
                old = self.chunks.popleft()
                self._held -= len(old[2])
                self.dropped += len(old[2])
        if self.chunks:
            self.first_offset = self.chunks[0][0]

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    def read_from(self, offset: int, timeout: float = KEEPALIVE_S) -> tuple[list[tuple[int, float, bytes]], bool]:
        """``(chunks at or after offset, ended)``, waiting up to ``timeout`` for something new. Each chunk is
        ``(offset, seconds since start, bytes)`` and at most ``STREAM_EVENT_BYTES`` (replays are merged into that)."""

        with self.cond:
            self._load_buffer()
            self.cond.wait_for(lambda: self.end_offset > offset or not self.active, timeout)
            out: list[tuple[int, float, bytes]] = []
            offset = max(offset, self.first_offset)
            for start, t, data in self.chunks:
                end = start + len(data)
                if end <= offset:
                    continue
                piece = data[max(0, offset - start):]
                at = max(start, offset)
                if out and out[-1][0] + len(out[-1][2]) == at and len(out[-1][2]) + len(piece) <= STREAM_EVENT_BYTES:
                    first = out[-1]
                    out[-1] = (first[0], first[1], first[2] + piece)
                else:
                    out.append((at, t, piece))
                offset = end
            return out, not self.active and not out

    def output_bytes(self) -> bytes:
        with self.cond:
            self._load_buffer()
            return b"".join(data for _o, _t, data in self.chunks)

    # --- views ----------------------------------------------------------------------------------------------------
    def to_json(self, detail: bool = False) -> dict:
        iso = self.registry.iso
        with self.cond:
            out = {"id": self.id, "path": self.spec.path, "device_id": self.spec.device_id,
                   "device_path": self.spec.device_path, "plugin": self.spec.plugin, "action": self.spec.action,
                   "title": self.spec.title, "label": self.spec.label, "danger": self.spec.danger, "node": self.spec.node,
                   "new_id": self.spec.new_id, "argv": redact(self.spec.argv), "started": self.started,
                   "started_iso": iso(self.started), "ended": self.ended, "ended_iso": iso(self.ended),
                   "elapsed_s": round(self.elapsed(), 3), "state": self.state, "active": self.active,
                   "stopping": self.stopping, "exit_code": self.exit_code,
                   "interactive": bool(self.interactive_start and self.active and not self.from_disk),
                   "cols": self.cols, "rows": self.rows, "output_bytes": self.end_offset, "dropped_bytes": self.dropped,
                   "stop": self.stop_info}
            if detail and not self.active:
                out["result"] = self.result
                out["tail"] = self.tail
            return out

    # --- the three writes -----------------------------------------------------------------------------------------
    def send_input(self, text: str) -> int:
        self.check_writable()
        data = text.encode()
        if len(data) > INPUT_MAX:
            raise RunError("BadRequest", f"input is at most {INPUT_MAX} bytes", 400)
        with self.cond:
            master = self.master
            if self.state != "running" or master is None:
                raise RunError("NotRunning", "the run is not running", 409)
            try:
                return os.write(master, data)
            except (BlockingIOError, InterruptedError):
                return 0
            except OSError as error:
                raise RunError("NotRunning", f"the terminal is closed ({error.strerror})", 409) from error

    def resize(self, cols: int, rows: int) -> None:
        self.check_writable()
        with self.cond:
            if self.state != "running" or self.master is None:
                raise RunError("NotRunning", "the run is not running", 409)
            _winsize(self.master, rows, cols)
            self.cols, self.rows = cols, rows

    def interrupt(self) -> str:
        """SIGINT now; SIGTERM and SIGKILL follow after ``grace_s`` each while it is still alive. ``stage`` says how far."""

        self.check_writable()
        with self.cond:
            if self.state == "queued":  # cancel the wait: nothing ever ran
                self.stop_requested = True
                self.stage = "cancelled"
                self._end_queued()
                return "cancelled"
            if self.state != "running" or self.process is None:
                raise RunError("NotRunning", "the run is not running", 409)
            if self.stop_requested:
                return self.stage or "SIGINT"
            self.stop_requested = True
            self.stage = "SIGINT"
        self._signal(signal.SIGINT)
        threading.Thread(target=self._escalate, name=f"run-stop-{self.id}", daemon=True).start()
        return "SIGINT"

    def check_writable(self) -> None:
        """The refusals every write shares, in the contract's order: not started from the page, then already ended."""

        if not self.interactive_start or self.from_disk:
            raise RunError("NotInteractive", "this run was not started from the page in this process: it takes no input",
                           403)
        if not self.active:
            raise RunError("NotRunning", "the run has ended", 409)

    def _signal(self, number: int) -> None:
        process = self.process
        if process is None or self.exited.is_set():
            return
        try:
            os.killpg(process.pid, number)
        except (ProcessLookupError, PermissionError):
            pass

    def _escalate(self) -> None:
        for stage, number in (("SIGTERM", signal.SIGTERM), ("SIGKILL", signal.SIGKILL)):
            if self.exited.wait(self.registry.grace_s):
                return
            self.stage = stage
            self._signal(number)

    def _end_queued(self) -> None:
        """Stop pressed while waiting for the exclusive group (``cond`` held)."""

        self.state = "stopped"
        self.ended = self.registry.now()
        self._t_end = time.monotonic()
        self.stop_info = {"requested": True, "signals": [], "plugin_stop": {"ran": False, "reason": "it never started"}}
        self.result = {"outcome": "stopped", "returncode": None, "duration_s": 0.0, "records": [], "action_result": None,
                       "rejected": [], "stderr_tail": "", "message": "stopped before it started"}
        self.cond.notify_all()
        self.finished.set()
        threading.Thread(target=self._after_end, daemon=True).start()

    def _after_end(self) -> None:
        self.persist()
        self.registry.started(self)  # one RunStarted, one RunEnded for every run, even one stopped in the queue
        self.registry.ended(self)

    def wait(self, timeout: float | None = None) -> bool:
        return self.finished.wait(timeout)

    # --- the lifecycle --------------------------------------------------------------------------------------------
    def launch(self) -> None:
        threading.Thread(target=self._execute, name=f"run-{self.id}", daemon=True).start()

    def _execute(self) -> None:
        try:
            with self.spec.hold():
                with self.cond:
                    if self.state != "queued":
                        return
                self._run()
        except Exception as error:  # noqa: BLE001 - a run never takes the host down
            self._conclude("failed", None, f"{type(error).__name__}: {error}", [], [], "not_started")

    def _run(self) -> None:
        spec = self.spec
        master, slave = pty.openpty()
        record_read, record_write = os.pipe()
        try:
            _winsize(master, self.rows, self.cols)
            env = dict(spec.env)
            env.update({RECORD_FD_ENV: str(record_write), RUN_ID_ENV: self.id, "TERM": "xterm-256color",
                        "COLUMNS": str(self.cols), "LINES": str(self.rows)})
            self._open_cast()
            # WHY login_tty in the child: setsid + the PTY as its controlling terminal + stdio on it, so a typed
            # Ctrl-C is a real SIGINT to the plugin's foreground process group
            process = subprocess.Popen(spec.argv, cwd=spec.cwd, env=env, pass_fds=(record_write,),
                                       preexec_fn=lambda: os.login_tty(slave))
        except OSError as error:
            for fd in (master, record_read):
                with contextlib.suppress(OSError):
                    os.close(fd)
            self.note(f"could not start: {type(error).__name__}: {error}")
            self._conclude("failed", None, f"could not start: {type(error).__name__}: {error}", [], [], "not_started")
            return
        finally:
            os.close(slave)
            os.close(record_write)
        os.set_blocking(master, False)
        with self.cond:
            self.process, self.master, self.state = process, master, "running"
        self.persist()
        self.registry.started(self)
        self.note("$ " + " ".join(redact(spec.argv)))
        reader = threading.Thread(target=self._read_records, args=(record_read,), daemon=True)
        reader.start()
        pump = threading.Thread(target=self._pump, args=(master,), daemon=True)
        pump.start()
        with RUNNING.run(process, f"{spec.plugin} {spec.action} {spec.device_id}"):
            try:
                process.wait(timeout=spec.timeout_s)
            except subprocess.TimeoutExpired:
                self.timed_out = True
                self.note(f"no answer within {spec.timeout_s:g} s (act_timeout_s): killing it")
                _kill_group(process)
                process.wait()
        self.exited.set()
        pump.join(5)
        reader.join(2)
        with self.cond:
            self.master = None
        for fd in (master, record_read):
            with contextlib.suppress(OSError):
                os.close(fd)
        self._after_exit(process.returncode)

    def _read_records(self, fd: int) -> None:
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return
            self._record_buffer.extend(data)

    def _pump(self, master: int) -> None:
        """Terminal bytes -> ring + recording, as they arrive; after the process is gone, what is left, then done."""

        while True:
            ready, _w, _x = select.select([master], [], [], 0.05)
            if ready:
                try:
                    data = os.read(master, 65536)
                except BlockingIOError:
                    continue
                except OSError:  # EIO: the last holder of the slave side closed it
                    return
                if not data:
                    return
                self.append(data)
                continue
            if self.exited.is_set():
                return

    def _after_exit(self, returncode: int | None) -> None:
        spec = self.spec
        duration = self.elapsed()
        if self.timed_out:
            state, outcome = "timed_out", "timeout"
        elif returncode == 0:
            state, outcome = "done", "ok"
        elif self.stop_requested:
            state, outcome = "stopped", "stopped"
        else:
            state, outcome = "failed", "crashed"
        terminal = plain_text(self.output_bytes())
        tail = terminal.strip()[-STDERR_TAIL:]
        records_text = self._record_buffer.decode("utf-8", "replace")
        records, rejected = parse_action_lines(records_text)
        if not records_text.strip():
            # an old plugin ignores the record fd and prints its records on stdout, which is the terminal now
            records, rejected = [r for r in _terminal_records(terminal)], []
        if spec.danger == "moves" and state != "done":
            self._plugin_stop(state)
        if state == "done":
            result = next((r for r in records if r.get("record") == "action-result"), None)
            message = (str(result["message"]) if result and result.get("message")
                       else f"ran in {duration:.2g} s · {len(records)} lines")
        elif state == "timed_out":
            message = f"no answer within {spec.timeout_s:g} s (act_timeout_s): killed"
        elif state == "stopped":
            message = f"stopped after {duration:.1f} s" + (" (exit %s)" % returncode if returncode is not None else "")
        else:
            last = tail.splitlines()[-1] if tail else ""
            how = (f"killed by signal {-returncode}" if returncode is not None and returncode < 0 else f"exited {returncode}")
            message = f"{how} after {duration:.1f} s" + (f": {last}" if last else "")
        self._conclude(state, returncode, message, records, rejected, outcome, tail=tail, duration=duration)

    def _plugin_stop(self, state: str) -> None:
        """The plugin's own stop for a moves-class run that did not finish cleanly, inside the exclusive hold."""

        spec = self.spec
        with self.cond:
            self.stopping = True
        self.stop_info = {"requested": self.stop_requested, "signals": self._signals_sent(),
                          "plugin_stop": {"ran": False}}
        where = f"CAN {spec.node}" if spec.node is not None else "the device"
        if not spec.stop_argv:
            reason = f"{spec.plugin} declares no stop verb: {where} may still be driven"
            self.stop_info["plugin_stop"] = {"ran": False, "reason": reason}
            self.note(f"! {reason}")
            return
        self.note(f"stopping {where} ({spec.plugin}'s own stop, no confirm needed) ...")
        try:
            done = subprocess.run(spec.stop_argv, cwd=spec.cwd, env=_plain_env(spec.env), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                                  timeout=STOP_TIMEOUT_S, start_new_session=True)
            records, rejected = parse_action_lines(done.stdout or "")
            message = next((str(r.get("message")) for r in reversed(records) if r.get("message")), "")
            outcome = "ok" if done.returncode == 0 else "crashed"
            if outcome != "ok" and not message:
                message = f"exited {done.returncode}: {(done.stderr or '').strip()[-200:]}"
        except subprocess.TimeoutExpired:
            records, rejected, outcome, message = [], [], "timeout", f"no answer within {STOP_TIMEOUT_S:g} s"
        except OSError as error:
            records, rejected, outcome, message = [], [], "not_started", f"could not start: {error}"
        self.stop_info["plugin_stop"] = {"ran": True, "outcome": outcome, "message": message, "records": records}
        self.note(f"stop: {message or outcome}")

    def _signals_sent(self) -> list[str]:
        order = ["SIGINT", "SIGTERM", "SIGKILL"]
        return order[: order.index(self.stage) + 1] if self.stage in order else []

    def _conclude(self, state: str, returncode: int | None, message: str, records: list, rejected: list, outcome: str, *,
                  tail: str = "", duration: float | None = None) -> None:
        duration = self.elapsed() if duration is None else duration
        if state != "failed" or returncode is not None:
            self.note(f"[exit {returncode} · {duration:.1f} s · {state}]")
        else:
            self.note(f"[{message}]")
        action_result = next((r for r in records if r.get("record") == "action-result"), None)
        if self.stop_requested and self.stop_info is None:
            self.stop_info = {"requested": True, "signals": self._signals_sent(),
                              "plugin_stop": {"ran": False, "reason": "not a moves-class action"}}
        elif self.stop_info is not None:
            self.stop_info["signals"] = self._signals_sent()
        self._close_cast()
        with self.cond:
            self.state = state
            self.stopping = False
            self.exit_code = returncode
            self.ended = self.registry.now()
            self._t_end = time.monotonic()
            self.tail = tail
            self.result = {"outcome": outcome, "returncode": returncode, "duration_s": round(duration, 3), "records": records,
                           "action_result": action_result, "rejected": rejected, "stderr_tail": tail, "message": message}
            self.cond.notify_all()
        self.persist()
        self.registry.ended(self)
        self.finished.set()


def _plain_env(env: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in env.items() if key not in (RECORD_FD_ENV, RUN_ID_ENV)}


def _terminal_records(terminal: str):
    for line in terminal.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "record" in data:
            yield data


class RunRegistry:
    """The runs of this host process, newest last, plus the finished ones found on disk (``directory`` None: memory only)."""

    def __init__(self, directory: Path | None, now: Callable[[], float], iso: Callable[[float | None], str | None],
                 notify: Callable[[str], None] = lambda _reason: None, *, ring_bytes: int = RING_BYTES,
                 budget_bytes: int = CAST_BUDGET_BYTES, grace_s: float = GRACE_S):
        self.directory = Path(directory) if directory else None
        self.now, self.iso, self.notify = now, iso, notify
        self.ring_bytes, self.budget_bytes, self.grace_s = ring_bytes, budget_bytes, grace_s
        self.runs: dict[str, Run] = {}
        self._events: list[Event] = []
        self._seq = 0
        self._lock = threading.RLock()
        self._load()

    # --- creating and finding -------------------------------------------------------------------------------------
    def create(self, spec: RunSpec, *, interactive: bool) -> Run:
        with self._lock:
            while True:
                run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)
                if run_id not in self.runs and not (self.directory and (self.directory / f"{run_id}.json").exists()):
                    break
            run = Run(self, run_id, spec, interactive=interactive)
            self.runs[run_id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self.runs.get(run_id)

    def list(self) -> list[Run]:
        with self._lock:
            return sorted(self.runs.values(), key=lambda run: run.id, reverse=True)

    def active(self) -> list[Run]:
        return [run for run in self.list() if run.active]

    def json(self, limit: int | None = None) -> dict:
        runs = self.list()
        items = [run.to_json() for run in (runs[:limit] if limit else runs)]
        return {"active": sum(1 for run in runs if run.active), "total": len(runs), "items": items}

    def limits(self) -> dict:
        return {"ring_bytes": self.ring_bytes, "input_max": INPUT_MAX, "cast_budget_bytes": self.budget_bytes,
                "grace_s": self.grace_s}

    # --- events ---------------------------------------------------------------------------------------------------
    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def _add_event(self, event: Event) -> None:
        with self._lock:
            self._seq += 1  # per source, +1 per event (contract Event.seq): also orders two events of one instant
            self._events.append(dataclasses.replace(event, seq=self._seq))
            del self._events[:-MAX_EVENTS]

    def started(self, run: Run) -> None:
        spec = run.spec
        self._add_event(Event(code="RunStarted", stamp=self.now(), severity="info", path=spec.path,
                              hardware_id=spec.hardware_id, message=f"{plain_title(spec.title)} started on {spec.label.split(' · ')[0]}",
                              args={"id": f"run:{run.id}:started", "run_id": run.id, "action": spec.action, "node": spec.node,
                                    "plugin": spec.plugin, "danger": spec.danger}, source=SOURCE))
        self.notify("run")

    def ended(self, run: Run) -> None:
        spec, result = run.spec, run.result or {}
        severity = {"done": "info", "stopped": "warn", "timed_out": "warn"}.get(run.state, "error")
        words = {"done": "done", "stopped": "stopped", "timed_out": "timed out", "failed": "failed"}.get(run.state, run.state)
        plugin_stop = (run.stop_info or {}).get("plugin_stop") or {}
        extra = ""
        if plugin_stop.get("ran"):
            extra = f"; the plugin's stop sent ({plugin_stop.get('outcome')})"
        elif spec.danger == "moves" and run.state != "done" and plugin_stop.get("reason"):
            extra = "; no stop was sent: " + str(plugin_stop["reason"])
            severity = "warn" if severity == "info" else severity
        exit_text = f" (exit {run.exit_code})" if run.exit_code is not None else ""
        self._add_event(Event(code="RunEnded", stamp=self.now(), severity=severity, path=spec.path, hardware_id=spec.hardware_id,
                              message=f"{plain_title(spec.title)} on {spec.label.split(' · ')[0]} {words} after "
                                      f"{result.get('duration_s', 0):.0f} s{exit_text}{extra}",
                              args={"id": f"run:{run.id}:ended", "run_id": run.id, "state": run.state,
                                    "exit_code": run.exit_code, "duration_s": result.get("duration_s"),
                                    "stop": run.stop_info}, source=SOURCE))
        self.prune()
        self.notify("run")

    # --- disk -----------------------------------------------------------------------------------------------------
    def _load(self) -> None:
        if self.directory is None or not self.directory.is_dir():
            return
        for meta in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(meta.read_text())
            except (OSError, ValueError):
                continue
            run = self._from_json(data)
            if run is not None:
                self.runs[run.id] = run
        self.prune()

    def _from_json(self, data: dict) -> Run | None:
        try:
            spec = RunSpec(plugin=data["plugin"], device_id=data["device_id"], device_path=data["device_path"], path=data["path"],
                           action=data["action"], title=data["title"], label=data["label"], danger=data["danger"],
                           argv=list(data["argv"]), env={}, cwd=None, timeout_s=0.0, node=data.get("node"),
                           new_id=data.get("new_id"))
            run = Run(self, data["id"], spec, interactive=False)
        except (KeyError, TypeError):
            return None
        run.from_disk = True
        run._buffer_loaded = False
        run.started, run.ended = data.get("started") or 0.0, data.get("ended")
        run.exit_code, run.cols, run.rows = data.get("exit_code"), data.get("cols", DEFAULT_COLS), data.get("rows", DEFAULT_ROWS)
        run.stop_info, run.result, run.tail = data.get("stop"), data.get("result"), data.get("tail", "")
        run.state = data.get("state", "failed")
        if run.state in ACTIVE:  # the host died while it ran
            run.state = "failed"
            run.result = {"outcome": "crashed", "returncode": None, "duration_s": data.get("elapsed_s", 0.0), "records": [],
                          "action_result": None, "rejected": [], "stderr_tail": "",
                          "message": "the host stopped while it ran"}
        run._t0 = 0.0
        run._t_end = float(data.get("elapsed_s") or 0.0)
        run.finished.set()
        run.exited.set()
        run.end_offset = 0
        run.dropped = 0
        return run

    def prune(self) -> None:
        """Delete the oldest ENDED runs' files while the total is over the budget; an active run is never pruned."""

        if self.directory is None or not self.directory.is_dir():
            return
        with self._lock:
            sizes = {}
            for run in self.runs.values():
                total = 0
                for path in (run.cast_path, run.meta_path):
                    try:
                        total += path.stat().st_size if path and path.exists() else 0
                    except OSError:
                        pass
                sizes[run.id] = total
            total = sum(sizes.values())
            for run in sorted((run for run in self.runs.values() if not run.active), key=lambda run: run.id):
                if total <= self.budget_bytes:
                    break
                for path in (run.cast_path, run.meta_path):
                    with contextlib.suppress(OSError):
                        if path:
                            path.unlink(missing_ok=True)
                total -= sizes[run.id]
                del self.runs[run.id]

    def shutdown(self) -> None:
        """The host is stopping: ask every active run to stop, wait a moment for them."""

        for run in self.active():
            if run.state == "running":
                run.stop_requested = True
                run.stage = "SIGINT"
                run._signal(signal.SIGINT)
                threading.Thread(target=run._escalate, daemon=True).start()
        for run in self.active():
            run.wait(self.grace_s * 2 + 1)


def plain_title(title: str) -> str:
    return str(title or "").removesuffix("…").strip()


__all__ = ["Run", "RunRegistry", "RunSpec", "RunError", "RECORD_FD_ENV", "RUN_ID_ENV", "plain_text", "plain_title", "redact",
           "INPUT_MAX", "RING_BYTES", "CAST_BUDGET_BYTES", "GRACE_S", "ACTIVE"]
