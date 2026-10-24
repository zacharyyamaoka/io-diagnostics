"""Editing a board's configuration from the page, made safe (docs/state-config-v1.md section 9).

The moteus model: ``conf set`` changes the RUNNING board (RAM) and ``conf write`` makes it survive a restart. So an edit is
live the moment it is applied, a person can see whether the board ACCEPTED it (the driver reads it back), and the changes
that are live but not stored are collected ("3 changes live, not saved") until Save or Revert.

What the host guarantees, whatever the page does:

- an **edit session** (``open`` -> token): one confirm naming the board, page-only like an act, alive only while the page keeps
  sending heartbeats (``beat``) and ended by ``close`` or ``grace_s`` of silence;
- every write needs that token, goes through the same gates as a watch (a person's tool holding the port, the rack bus
  lock, an active run of the bus) and is a RUN (a recording, RunStarted / RunEnded) of the plugin's own ``set_config`` /
  ``persist_config`` / ``reset_config`` action; the plugin's confirm token is fetched by a dry run of that same write;
- a **safety key** (``Config.safety``) needs ``confirm_safety`` (the page's second confirm), a board that reported
  ``mode`` 0 (stopped) in its State a moment ago, and no active run on its bus; otherwise it is refused and nothing runs;
- every write, accepted or not (and every refusal), is an Activity event: who, which key, old -> new, what the board kept.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from contract.record import STALE_FACTOR, STALE_FLOOR_S, Event
from io_diagnostics.entity_state import same_value

CONFIG_SOURCE = "io_diagnostics.config_edit"
MODE_KEY = "mode"  # the State measurement a board reports its mode in; 0 = stopped
STOPPED = 0
DEFAULT_GATES = ("port_free", "bus_lock_free")
MAX_EVENTS = 500
RESET_TARGETS = ("stored", "expected", "default")


@dataclass
class Session:
    token: str
    path: str
    board: str
    opened: float  # host clock
    beat: float  # time.monotonic()


def _coerce(row, value: Any) -> tuple[Any, str]:
    """``(value as the row's type, "")`` or ``(None, why not)``. A range is the BOARD's to enforce (it may clamp), so it is not checked here."""

    kind = row.type
    try:
        if kind == "int":
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
            return int(value), ""
        if kind == "float":
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if number != number or number in (float("inf"), float("-inf")):
                raise ValueError
            return number, ""
        if kind == "bool":
            if isinstance(value, bool):
                return value, ""
            if str(value).strip().lower() in ("true", "1", "yes", "on"):
                return True, ""
            if str(value).strip().lower() in ("false", "0", "no", "off"):
                return False, ""
            raise ValueError
        if kind == "enum":
            for choice in row.choices:
                if same_value(choice, value) or str(choice) == str(value):
                    return choice, ""
            return None, f"{row.key} is one of {', '.join(map(str, row.choices))}"
    except (TypeError, ValueError):
        return None, f"{value!r} is not a {kind} (what {row.key} holds)"
    return value, ""


def _arg(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


class ConfigEditor:
    """Edit sessions, the per-setting edit state, and the Activity events of every write. One per ``Host``."""

    def __init__(self, host, grace_s: float):
        self.host, self.grace_s = host, grace_s
        self.sessions: dict[str, Session] = {}  # entity path -> its session
        self.edits: dict[tuple, dict] = {}  # (job key, rel path, key) -> {"state", "asked", "kept", "message", "old", "at", "run_id"}
        self.events: list[Event] = []
        self._seq = 0
        self._lock = threading.RLock()

    # --- events ----------------------------------------------------------------------------------------------------------
    def _log(self, code: str, severity: str, message: str, path: str, hardware_id: str = "", **args) -> None:
        with self._lock:
            self._seq += 1
            self.events.append(Event(code, self.host.clock.now(), severity, message, path=path, hardware_id=hardware_id,
                                     args={"by": "page", **args}, seq=self._seq, source=CONFIG_SOURCE))
            del self.events[:-MAX_EVENTS]
        self.host.notify("config")

    # --- the session -----------------------------------------------------------------------------------------------------
    def _alive(self, session: Session) -> bool:
        return time.monotonic() - session.beat <= self.grace_s

    def sweep(self) -> None:
        """Sessions whose page went away are over (and say so in the log)."""

        with self._lock:
            for path, session in list(self.sessions.items()):
                if not self._alive(session):
                    del self.sessions[path]
                    self._log("ConfigEditEnded", "info", f"{session.board}: configuration editing ended (the page went away)", path,
                              session=session.token[:6])

    def status(self, path: str) -> dict:
        self.sweep()
        session = self.sessions.get(path)
        return {"active": session is not None, "grace_s": self.grace_s, "board": session.board if session else ""}

    def _target(self, path: str):
        target = self.host._watch_target(path)
        if target is None:
            return None
        job, rel = target
        entity = job.cache.entities.get(rel)
        editable = bool(entity and any(action.name == "set_config" for action in entity.actions))
        return job, rel, entity, editable

    def open(self, path: str, confirm: bool) -> dict:
        target = self._target(path)
        if target is None:
            return {"ok": False, "refused": "NoSuchEntity", "message": f"no entity {path!r} a plugin describes"}
        job, rel, entity, editable = target
        if not editable:
            return {"ok": False, "refused": "NotEditable", "message": f"{job.spec.name} registers no set_config for this entity"}
        if confirm is not True:
            return {"ok": False, "refused": "ConfirmRequired", "message": "editing needs the person's confirm naming the board"}
        board = self.host._display_name(path, entity)
        with self._lock:
            self.sweep()
            old = self.sessions.get(path)
            session = Session(secrets.token_urlsafe(18), path, board, self.host.clock.now(), time.monotonic())
            self.sessions[path] = session
        self._log("ConfigEditStarted", "info", f"{board}: configuration editing started (changes apply live; Save keeps them)", path,
                  session=session.token[:6], replaced=bool(old))
        return {"ok": True, "token": session.token, "grace_s": self.grace_s, "board": board, "path": path}

    def beat(self, path: str, token: str) -> dict:
        session = self._session(path, token)
        if isinstance(session, dict):
            return session
        session.beat = time.monotonic()
        return {"ok": True, "grace_s": self.grace_s, "board": session.board}

    def close(self, path: str, token: str) -> dict:
        session = self._session(path, token)
        if isinstance(session, dict):
            return session
        with self._lock:
            self.sessions.pop(path, None)
        self._log("ConfigEditEnded", "info", f"{session.board}: configuration editing ended", path, session=session.token[:6])
        return {"ok": True}

    def _session(self, path: str, token: str):
        self.sweep()
        session = self.sessions.get(path)
        if session is None or not secrets.compare_digest(session.token, str(token or "")):
            return {"ok": False, "refused": "NoEditSession", "message": "no edit session for this entity (it ended, or was never confirmed): press Edit again"}
        session.beat = time.monotonic()
        return session

    # --- the state of each setting -------------------------------------------------------------------------------------
    def edit_of(self, job, rel: str, key: str) -> dict | None:
        found = self.edits.get((job.key, rel, key))
        return dict(found) if found else None

    def _set_edit(self, job, rel: str, key: str, **fields) -> None:
        with self._lock:
            current = self.edits.get((job.key, rel, key), {})
            self.edits[(job.key, rel, key)] = {**current, **fields}

    # --- the writes ---------------------------------------------------------------------------------------------------
    def _context(self, path: str, token: str):
        session = self._session(path, token)
        if isinstance(session, dict):
            return session
        target = self._target(path)
        if target is None or not target[3]:
            return {"ok": False, "refused": "NotEditable", "message": "this entity can no longer be edited"}
        return session, *target

    def _row(self, job, rel: str, key: str):
        return job.cache.configs.get((rel, key))

    def _gate(self, job, rel: str) -> str | None:
        """Why nothing may be written to this board now, or None: the watch's gates, and a run of the bus."""

        declared = (self.host.describes.get(job.spec.name) or {}).get("config_write") or {}
        gates = [str(check) for check in declared.get("gates") or DEFAULT_GATES]
        found = self.host._gate_for(job, gates)
        return found["reason"] if found else None

    def _safety(self, job, rel: str, row) -> str | None:
        """A safety key's extra conditions: the board is stopped (it said mode 0 in its State lately) and nothing runs on its bus."""

        group = job.spec.exclusive
        if group:
            run = next((run for run in self.host.runs.active() if run.spec.group == group), None)
            if run is not None:
                return f"{run.spec.label} is running on this bus: wait for it, or stop it, first"
        now = self.host.clock.now()
        for (path, _name), state in job.cache.states.items():
            if path != rel:
                continue
            for measurement in state.measurements:
                if measurement.key != MODE_KEY:
                    continue
                age = now - state.received if state.received is not None else None
                limit = max(STALE_FACTOR * state.expected_period_s, STALE_FLOOR_S) if state.expected_period_s else STALE_FLOOR_S
                if age is None or age > limit:
                    return f"{row.key} is a safety setting and the board's mode is not current (last heard {age:.0f} s ago): it is written only to a board known to be stopped"
                if measurement.value != STOPPED:
                    return (f"{row.key} is a safety setting: the board must be stopped (mode {STOPPED}) and it reports mode "
                            f"{measurement.value}{f' ({measurement.meaning})' if measurement.meaning else ''}")
                return None
        return f"{row.key} is a safety setting and the board reports no mode: it is written only to a board known to be stopped"

    def set(self, path: str, token: str, key: str, value: Any, confirm_safety: bool = False, *, reset: str | None = None) -> dict:
        """One setting, live. ``{"ok", "state": accepted | rejected | refused, "key", "old", "asked", "value", "message", "run_id"}``."""

        context = self._context(path, token)
        if isinstance(context, dict):
            return context
        session, job, rel, entity, _editable = context
        row = self._row(job, rel, key)
        base = {"key": key, "asked": value, "path": path, "reset": reset}
        if row is None or not row.writable:
            return self._refuse(job, rel, entity, path, base, "NotWritable", f"{key} is not a setting this board lets you change")
        coerced, why = _coerce(row, value)
        if why:
            return self._refuse(job, rel, entity, path, base, "BadValue", why, old=row.value)
        base["asked"] = coerced
        old = row.value
        if row.safety:
            if confirm_safety is not True:
                return self._refuse(job, rel, entity, path, base, "SafetyConfirmRequired", f"{key} is a safety setting: it needs the second confirm", old=old)
            problem = self._safety(job, rel, row)
            if problem:
                return self._refuse(job, rel, entity, path, base, "SafetyRefused", problem, old=old)
        blocked = self._gate(job, rel)
        if blocked:
            return self._refuse(job, rel, entity, path, base, "Blocked", f"not written: {blocked}", old=old)
        action = "reset_config" if reset else "set_config"
        extra = ["--key", key, "--value", _arg(coerced), *(["--to", reset] if reset else [])]
        self._set_edit(job, rel, key, state="pending", asked=coerced, kept=None, message="sent to the board", old=old,
                       at=self.host.clock.now(), run_id=None, reset=reset)
        ran = self._run(job, rel, path, action, extra)
        after = self._row(job, rel, key)
        kept = after.value if after is not None else None
        if ran["ok"] and after is not None and same_value(kept, coerced):
            state, message = "accepted", ""
        elif ran["ok"] and after is not None:
            state, message = "rejected", f"the board kept {kept} (asked {coerced})"
        else:
            state, message = "rejected", ran["message"] or "the board refused it"
        self._set_edit(job, rel, key, state=state, kept=kept, message=message, run_id=ran["run_id"])
        verb = "reset to " + reset if reset else "set"
        self._log("ConfigSet" if state == "accepted" else "ConfigRejected", "info" if state == "accepted" else "warn",
                  f"{self.host._display_name(path, entity)}: {key} {verb} {old} → {coerced}: " + ("accepted by the board" if state == "accepted" else message),
                  path, entity.hardware_id if entity else "", key=key, old=old, asked=coerced, kept=kept, accepted=state == "accepted",
                  run_id=ran["run_id"], reset=reset, session=session.token[:6])
        return {"ok": state == "accepted", "state": state, "key": key, "old": old, "asked": coerced, "value": kept, "message": message,
                "run_id": ran["run_id"], "stored": after.stored if after is not None else None}

    def _refuse(self, job, rel, entity, path, base, code: str, message: str, old=None) -> dict:
        key = base["key"]
        self._set_edit(job, rel, key, state="rejected", asked=base["asked"], kept=old, message=message, old=old, at=self.host.clock.now(),
                       run_id=None, reset=base.get("reset"))
        self._log("ConfigRejected", "warn", f"{self.host._display_name(path, entity)}: {key} → {base['asked']} refused before anything was sent: {message}",
                  path, entity.hardware_id if entity else "", key=key, old=old, asked=base["asked"], accepted=False, refused=code)
        return {"ok": False, "state": "refused", "refused": code, "key": key, "old": old, "asked": base["asked"], "value": old, "message": message,
                "run_id": None}

    def persist(self, path: str, token: str) -> dict:
        """Make every live setting survive a restart (moteus ``conf write``)."""

        context = self._context(path, token)
        if isinstance(context, dict):
            return context
        session, job, rel, entity, _editable = context
        blocked = self._gate(job, rel)
        if blocked:
            self._log("ConfigRejected", "warn", f"{self.host._display_name(path, entity)}: save refused: {blocked}", path, accepted=False, refused="Blocked")
            return {"ok": False, "state": "refused", "refused": "Blocked", "message": f"not saved: {blocked}"}
        unsaved = [row.key for (p, _k), row in job.cache.configs.items() if p == rel and row.stored is not None and not same_value(row.value, row.stored)]
        ran = self._run(job, rel, path, "persist_config", [])
        still = [row.key for (p, _k), row in job.cache.configs.items() if p == rel and row.stored is not None and not same_value(row.value, row.stored)]
        ok = ran["ok"] and not still
        if ok:
            with self._lock:
                for key in [key for key in self.edits if key[0] == job.key and key[1] == rel]:
                    self.edits.pop(key, None)
        self._log("ConfigPersisted" if ok else "ConfigRejected", "info" if ok else "warn",
                  f"{self.host._display_name(path, entity)}: " + (f"{len(unsaved)} setting{'s' if len(unsaved) != 1 else ''} saved to the board" if ok
                                                                 else f"save did not complete: {ran['message'] or 'the board still differs: ' + ', '.join(still)}"),
                  path, entity.hardware_id if entity else "", keys=unsaved, accepted=ok, run_id=ran["run_id"], session=session.token[:6])
        return {"ok": ok, "state": "saved" if ok else "rejected", "saved": unsaved if ok else [], "message": "" if ok else ran["message"] or "the board still differs",
                "run_id": ran["run_id"]}

    def reset(self, path: str, token: str, to: str, key: str | None = None, confirm_safety: bool = False) -> dict:
        """Put one setting (or, with no key, every setting) back live: to the stored value, the deployment record's, or the default."""

        if to not in RESET_TARGETS:
            return {"ok": False, "refused": "BadRequest", "message": f"to is one of {', '.join(RESET_TARGETS)}"}
        context = self._context(path, token)
        if isinstance(context, dict):
            return context
        session, job, rel, entity, _editable = context
        if key is not None:
            row = self._row(job, rel, key)
            if row is None:
                return {"ok": False, "refused": "NotWritable", "message": f"no setting {key!r}"}
            target = {"stored": row.stored, "expected": row.expected, "default": row.default}[to]
            if target is None:
                return {"ok": False, "refused": "NoTarget", "message": f"{key} has no {'deployment record value' if to == 'expected' else to + ' value'} to go back to"}
            return self.set(path, token, key, target, confirm_safety, reset=to)
        if to != "stored":
            return {"ok": False, "refused": "BadRequest", "message": "every setting can only go back to the stored values"}
        changed = [row for (p, _k), row in job.cache.configs.items() if p == rel and row.writable and row.stored is not None and not same_value(row.value, row.stored)]
        if any(row.safety for row in changed):
            problems = [self._safety(job, rel, row) for row in changed if row.safety]
            if confirm_safety is not True:
                return {"ok": False, "refused": "SafetyConfirmRequired", "message": "some of these are safety settings: they need the second confirm"}
            if any(problems):
                return {"ok": False, "refused": "SafetyRefused", "message": next(problem for problem in problems if problem)}
        blocked = self._gate(job, rel)
        if blocked:
            return {"ok": False, "state": "refused", "refused": "Blocked", "message": f"not reverted: {blocked}"}
        ran = self._run(job, rel, path, "reset_config", ["--to", "stored"])
        with self._lock:
            for row in changed:
                self.edits.pop((job.key, rel, row.key), None)
        self._log("ConfigReset" if ran["ok"] else "ConfigRejected", "info" if ran["ok"] else "warn",
                  f"{self.host._display_name(path, entity)}: " + (f"{len(changed)} live change{'s' if len(changed) != 1 else ''} reverted to the stored values" if ran["ok"]
                                                                  else f"revert failed: {ran['message']}"),
                  path, entity.hardware_id if entity else "", keys=[row.key for row in changed], accepted=ran["ok"], run_id=ran["run_id"],
                  session=session.token[:6])
        return {"ok": ran["ok"], "state": "reverted" if ran["ok"] else "rejected", "reverted": [row.key for row in changed], "message": ran["message"],
                "run_id": ran["run_id"]}

    # --- one write as a run -------------------------------------------------------------------------------------------
    def _run(self, job, rel: str, path: str, action: str, extra: list[str]) -> dict:
        """A dry run (for the plugin's own confirm token), then the real run, waited for; the ``config`` records it prints go
        into the job's cache (the read-back). ``{"ok", "message", "run_id"}``."""

        host = self.host
        node = int(rel.rsplit("/", 1)[-1]) if rel.rsplit("/", 1)[-1].isdigit() and (self._scope(job, rel, action) == "node") else None
        dry = host.act(path, action, node=node, dry_run=True, human=False, extra=extra)
        if dry.get("refused") or dry.get("outcome") != "ok":
            return {"ok": False, "message": dry.get("message") or "the plugin would not describe the write", "run_id": None}
        answer = dry.get("action_result") or {}
        run, out = host.start_act(path, action, node=node, confirm=answer.get("token") or answer.get("confirm_token"), interactive=False, extra=extra)
        if run is None:
            return {"ok": False, "message": out.get("message") or "the write was refused", "run_id": None}
        run.wait()
        result = run.result or {}
        host._apply_run_records(job, result.get("records") or [])
        ok = result.get("outcome") == "ok" and run.exit_code in (0, None)
        message = result.get("message") or ""
        for record in reversed(result.get("records") or []):
            if isinstance(record, dict) and record.get("code") == "ConfigRejected" and record.get("message"):
                ok, message = False, str(record["message"])
                break
        return {"ok": ok, "message": "" if ok else message or f"the plugin exited {run.exit_code}", "run_id": run.id}

    def _scope(self, job, rel: str, action: str) -> str:
        entity = job.cache.entities.get(rel)
        found = next((item for item in (entity.actions if entity else ()) if item.name == action), None)
        return found.scope if found else "device"
