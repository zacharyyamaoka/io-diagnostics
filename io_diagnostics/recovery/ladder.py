"""The reset ladder: detect, then each rung in its profile's order until the device answers, then a human.

Two modes, one engine (docs/device-reset.md):

- **unattended**: a deployed robot recovers on its own. The device's owner (arm-hold for the arm's fdcanusb) calls it when
  its own reopen failed, naming itself with ``owner_pid``; or a supervisor calls it for a device nobody holds. Each rung runs
  at most once per incident, person-only rungs never run, nothing is reset while any process other than the owner has it
  open or holds its owner lock, and the last rung pages a human.
- **person**: the Reset button (``act --action reset``): one confirmation for the whole plan, every rung that applies
  (person-only ones too), a second confirmation when something has it open, stopping as soon as it answers.

Exit codes: 0 it answers (healthy, or cured by a rung) · 2 refused, nothing was reset · 3 a rung ran and it is back on USB,
but only its owner can tell whether it answers (step mode, or a probe that could not ask) · 4 a human is needed (paged) ·
1 the ladder itself failed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from io_diagnostics import usb_reset
from io_diagnostics.recovery import availability, device as devices, mechanisms, probes
from io_diagnostics.recovery.incident import Store, off_switch
from io_diagnostics.recovery.profile import RecoveryProfile
from io_diagnostics.recovery.profiles import BY_NAME, profile_for
from io_diagnostics.recovery.rung import NATIVE, PAGE, REOPEN, USB_KINDS, Rung
from io_diagnostics.recovery.system import (Seams, alive, ancestors, descendants, fill, lock_holders, lock_is_held, tool_paths)

UNATTENDED, PERSON = "unattended", "person"
HEALTHY, REFUSED, NOT_JUDGED, NEEDS_HUMAN, BROKEN = 0, 2, 3, 4, 1
BLOCKED_PAGE_S = 120.0  # a holder or an owner lock that keeps every rung from running this long is paged, not waited out
SETTLE_POLL_S = 0.2
PAGE_ENV = "IO_DIAGNOSTICS_PAGE_COMMAND"  # a deployment's pager: argv words, {device} {text} filled in (space separated)


@dataclass
class Request:
    key: str = ""  # usb:VID:PID:SERIAL | usb:<port> | serial:S | can:S/N
    skeleton: dict | None = None  # the host's device dict (person mode from the page)
    mode: str = UNATTENDED
    owner_pid: int | None = None
    detected: str = ""  # the owner's words for what it saw ("no answer for 5 exchanges", "frames stale 3 s"); "" = probe
    tried_by_owner: tuple[str, ...] = ()  # rung kinds the owner already ran this outage (arm-hold's own reopen and reset)
    probe_command: list[str] = field(default_factory=list)
    step: bool = False  # run one rung and return, the owner reopens and calls again
    dry_run: bool = False
    confirm: str = ""
    confirm_holders: bool = False
    page_command: list[str] = field(default_factory=list)
    plugins_dir: Path | None = None
    caller_pid: int = field(default_factory=os.getpid)


@dataclass
class Result:
    code: int
    outcome: str  # healthy | cured | not_judged | refused | paged | already_paged | not_cured | planned | error
    message: str
    records: list[dict] = field(default_factory=list)  # diagnostics-record events and the action-result, for the host's run
    plan: dict | None = None
    attempts: list[dict] = field(default_factory=list)  # the ledger lines this call wrote


def _event(code: str, severity: str, message: str, **args) -> dict:
    return {"record": "event", "code": code, "severity": severity, "message": message, "source": "io_diagnostics.recovery",
            "args": args}


class Ladder:
    def __init__(self, request: Request, seams: Seams | None = None, say=print):
        self.request = request
        self.seams = seams or Seams()
        self.say = say
        self.result_records: list[dict] = []
        self.attempts: list[dict] = []

    # ----- what device, which profile -----------------------------------------------------------------------------------
    def _device(self) -> tuple[devices.Device, RecoveryProfile, Store]:
        request = self.request
        if request.key:
            found = devices.resolve(request.key)
        elif request.skeleton:
            found = devices.from_skeleton(request.skeleton)
        else:
            raise devices.DeviceError("name a device (--device)")
        store = Store(found.state_key, self.seams.now)
        incident = store.current()
        if not found.present and incident and incident.last_port and not found.port:
            found = dataclasses.replace(found, port=incident.last_port)
        profile = profile_for(found.keys()) or (BY_NAME.get(incident.profile) if incident and incident.profile else None)
        if profile is None:
            profile = BY_NAME["usb_device"]
        return found, profile, store

    # ----- who else has it ---------------------------------------------------------------------------------------------
    def _family(self) -> set[int]:
        """The owner and its helpers, and this ladder and the processes that started it: never "someone else"."""

        mine = {self.request.caller_pid, os.getpid(), *ancestors(self.request.caller_pid)}
        if self.request.owner_pid:
            mine |= {self.request.owner_pid, *descendants(self.request.owner_pid)}
        return mine

    def _holders(self, device: devices.Device) -> tuple[list, list]:
        """``(everyone with it open, the ones who are not the owner)``, over the device and anything below it."""

        if not device.port:
            return [], []
        # a CAN board's are its adapter's: its restart goes out through the adapter's port, so whoever has that open loses it
        found, _unseen = usb_reset.holders(device.port, usb_reset.own_sessions())
        family = self._family()
        return found, [holder for holder in found if holder.pid not in family]

    def _owner_locks(self, profile: RecoveryProfile) -> list[str]:
        """Owner locks held by a process outside the family, in words: the rack bus lock held by an experiment."""

        out, family = [], self._family()
        for template in profile.owner_locks:
            path = fill(template, tool_paths())
            if not os.path.exists(path):
                continue
            pids = [pid for pid in lock_holders(path) if pid not in family]
            if pids:
                out.append(f"{path} is held by " + ", ".join(f"{usb_reset._program(pid)} (pid {pid})" for pid in pids))
            elif not lock_holders(path) and lock_is_held(path) and not self.request.owner_pid:
                out.append(f"{path} is held (its holder could not be named)")
        return out

    # ----- the gates of every call ------------------------------------------------------------------------------------
    def _refuse(self, reason: str, message: str, store: Store | None = None, **args) -> Result:
        self.say(f"refused ({reason}): {message}")
        record = _event("RecoveryRefused", "warn", message, reason=reason, **args)
        if store is not None:
            self.attempts.append(store.record({"outcome": "refused", "reason": reason, "words": message, "mode": self.request.mode,
                                               "caller": self._caller(), **({"args": args} if args else {})}))
        return Result(REFUSED, "refused", message, [*self.result_records, record], attempts=self.attempts)

    def _caller(self) -> dict:
        return {"pid": self.request.caller_pid, "owner_pid": self.request.owner_pid, "program": usb_reset._program(self.request.caller_pid)}

    def _gates(self, device: devices.Device, store: Store) -> Result | None:
        request = self.request
        if request.mode == UNATTENDED and off_switch().exists() and not request.dry_run:
            return self._refuse("RecoveryOff", f"unattended recovery is switched off ({off_switch()} exists)", store)
        hold = store.hold()
        if hold is not None and not request.dry_run:
            return self._refuse("HoldDeclared", f"{hold.get('by') or 'someone'} holds {device.name} until "
                                f"{_clock(hold.get('until'))}: {hold.get('reason') or 'no reason given'}", store)
        if request.owner_pid:
            if not alive(request.owner_pid):
                return self._refuse("OwnerGone", f"the declared owner (pid {request.owner_pid}) is not running", store)
            # WHY: only the owner itself (or a process it started) may say it is the owner, so no third party can name the
            # owner's pid to slip past the holder check
            if request.owner_pid != request.caller_pid and request.owner_pid not in ancestors(request.caller_pid):
                return self._refuse("OwnerNotCaller", f"pid {request.owner_pid} is not this process or one that started it: "
                                    "only the owner may declare itself the owner", store)
        replay = os.environ.get(usb_reset.OS_MODE_ENV, "live") != "live" and not usb_reset.sysfs_overridden()
        if replay and not request.dry_run:
            return self._refuse("NotLive", "this host replays a recording: nothing here can be reset", store)
        return None

    # ----- the plan (a dry run, and the head of every real run) ----------------------------------------------------------
    def _rungs(self, profile: RecoveryProfile) -> list[Rung]:
        rungs = [rung for rung in profile.rungs if rung.kind != PAGE]
        if self.request.mode == PERSON:
            # WHY: a person who presses Reset has already reopened (restarted their app); they want a reset
            rungs = [rung for rung in rungs if rung.kind != REOPEN] or rungs
        return rungs

    def _skip(self, rung: Rung, device: devices.Device, profile: RecoveryProfile, incident) -> tuple[str, str]:
        """``("", "")`` when it may run now, else ``(code, plain words)``."""

        request = self.request
        if request.mode == UNATTENDED and not rung.unattended:
            return "PersonOnly", f"only a person runs it: {rung.attended_why}"
        if rung.kind in request.tried_by_owner:
            return "DoneByOwner", f"done by its owner (pid {request.owner_pid}) before it asked"
        if incident is not None and request.mode == UNATTENDED:
            if rung.kind in incident.tried:
                return "AlreadyTried", f"already tried this incident ({incident.tried_by.get(rung.kind, 'robotdx')}, " \
                                       f"{_clock(incident.tried[rung.kind])})"
            same = next((other for other in profile.rungs if other.same_as == rung.kind and other.kind in incident.tried), None)
            if same is not None:
                return "SameReset", f"the same reset as {same.title}, already tried this incident"
        if rung.kind in (NATIVE, *USB_KINDS[:2]) and not device.present and not device.is_can:
            return "NotOnUsb", "it is not on USB now"
        here = availability.rung_availability(rung, device, profile, self.seams, request.plugins_dir)
        if not here.usable:
            return "NotAvailable", here.why + (f" (needs {here.needs})" if here.needs else "")
        return "", ""

    def plan(self, device: devices.Device, profile: RecoveryProfile, store: Store, found: list, others: list) -> dict:
        incident = store.current()
        steps = []
        for rung in profile.rungs:
            here = availability.rung_availability(rung, device, profile, self.seams, self.request.plugins_dir)
            code, why = ("", "") if rung.kind == PAGE else self._skip(rung, device, profile, incident)
            if self.request.mode == PERSON and rung.kind == REOPEN:
                code, why = "PersonSkips", "a person pressing Reset has already reopened it"
            if self.request.mode == PERSON and rung.kind == PAGE:
                code, why = "PersonHere", "a person is here: the run ends by saying what to do instead"
            steps.append({**rung.to_json(), "available": here.to_json(), "runs": not code, "skip": code, "skip_why": why})
        runnable = [step["kind"] for step in steps if step["runs"] and step["kind"] != PAGE]
        target = device.target() if device.port and not device.is_can else None
        seed = "|".join(["reset", device.key, device.port, target.bus_dev if target else "", ",".join(runnable),
                         ",".join(str(holder.pid) for holder in sorted(others, key=lambda h: h.pid))])
        # WHY no token when nothing can run: a Run that could only end in "not cured" must not be offered at all
        token = hashlib.sha256(seed.encode()).hexdigest()[:16] if runnable else ""
        needs = sorted({step["available"]["needs"] for step in steps if not step["available"]["usable"] and step["available"]["needs"]})
        second = None
        if others:
            second = ", ".join(f"{holder.program} (pid {holder.pid})" for holder in others) + f" will lose {device.name}"
        return {"record": "action-result", "action": "reset", "title": "Reset…", "dry_run": self.request.dry_run,
                "danger": "moves" if any(step["danger"] == "moves" and step["runs"] for step in steps) else "interrupts",
                "device": device.key, "name": device.name, "port": device.port, "profile": profile.name,
                "profile_title": profile.title, "probe": profile.probe, "mode": self.request.mode, "steps": steps,
                "runs": runnable, "holders": [{"pid": h.pid, "program": h.program, "node": h.node} for h in found],
                "others": [{"pid": h.pid, "program": h.program, "node": h.node} for h in others],
                "second_confirm": second, "token": token, "needs": needs,
                "fix": (f"Run the one-time installer as root: {availability.INSTALLER}" if "the one-time installer" in needs else None),
                "incident": incident.to_json() if incident else None,
                "confirm": (f"Reset {device.name}? RobotDx tries, in order, until it answers: "
                            + "; ".join(step["title"] for step in steps if step["runs"] and step["kind"] != PAGE)
                            + ". Whatever has it open loses it.") if runnable else
                           f"Nothing RobotDx may try on {device.name} here: "
                           + "; ".join(f"{step['title']}: {step['skip_why']}" for step in steps
                                       if not step["runs"] and step["kind"] not in (PAGE, REOPEN)) + ".",
                "message": (f"would try {', '.join(runnable)} on {device.name}" if runnable
                            else f"no rung can run on {device.name} here: " + "; ".join(
                                f"{step['kind']}: {step['skip_why']}" for step in steps if not step["runs"] and step["kind"] != PAGE))}

    # ----- the run -------------------------------------------------------------------------------------------------------
    def run(self) -> Result:
        try:
            device, profile, store = self._device()
        except devices.DeviceError as error:
            return self._refuse("NoDevice", str(error))
        gate = self._gates(device, store)
        if gate is not None:
            return gate
        with store.ladder_lock() as mine:
            if not mine and not self.request.dry_run:
                return self._refuse("LadderRunning", f"another reset ladder is running on {device.name}", store)
            return self._run(device, profile, store)

    def _run(self, device: devices.Device, profile: RecoveryProfile, store: Store) -> Result:
        request = self.request
        found, others = self._holders(device)
        plan = self.plan(device, profile, store, found, others)
        if request.dry_run:
            return Result(HEALTHY, "planned", plan["message"], [plan], plan)

        # gates that depend on who has it now
        locks = self._owner_locks(profile)
        if request.mode == PERSON:
            if not plan["runs"]:
                return self._refuse("NothingToRun", plan["confirm"], store)
            if request.confirm != plan["token"]:
                return self._refuse("ConfirmMismatch", "the confirmation is not for this device and plan as they are now (a "
                                    "holder came or went, or a rung became available): ask for the dry run again", store)
            if others and not request.confirm_holders:
                return self._refuse("HoldersPresent", plan["second_confirm"] + ": confirm again to reset it anyway", store,
                                    holders=plan["others"])
            if locks:
                return self._refuse("OwnerLockHeld", "; ".join(locks) + ": that process owns the device's bus; stop it first",
                                    store)
        else:
            blocked = (f"{', '.join(h.text() for h in others)}: not the declared owner" if others else "") or "; ".join(locks)
            if blocked:
                return self._blocked(device, profile, store, blocked, "HeldByOther" if others else "OwnerLockHeld")

        # detect
        before = devices.snapshot(device, found)
        if request.detected:
            healthy, words = False, f"its owner says: {request.detected}"
        else:
            healthy, words = probes.probe(profile, device, self.seams, holders=found, probe_command=request.probe_command,
                                          plugins_dir=request.plugins_dir, skeleton=request.skeleton)
        if healthy and request.mode == UNATTENDED:
            incident = store.healthy(profile.quiet_s)
            note = f" (incident {incident.id} {'closed' if incident.closed else 'quiet since ' + _clock(incident.healthy_since)})" \
                if incident else ""
            self.say(f"{device.name} answers: {words}{note}")
            return Result(HEALTHY, "healthy", words + note, [*self.result_records, _event("RecoveryHealthy", "info", words)],
                          plan)
        if healthy is None and request.mode == UNATTENDED:
            return self._refuse("CannotTell", f"{words}; its owner must say what it saw (--detected)", store)

        incident = store.open(words, device.port)
        incident.profile = profile.name
        incident.blocked_since = None
        for kind in request.tried_by_owner:
            incident.tried.setdefault(kind, self.seams.now())
            incident.tried_by.setdefault(kind, f"owner pid {request.owner_pid}")
        store.save(incident)
        self.say(f"{device.name} at {device.port or '?'}: {words}")
        self.result_records.append(_event("RecoveryStarted", "warn", f"{device.name}: {words}", incident=incident.id,
                                          profile=profile.name, mode=request.mode))
        if request.mode == UNATTENDED and store.opened_in_last_day() > profile.max_incidents_per_day:
            return self._page(device, profile, store, incident,
                              f"it failed {store.opened_in_last_day()} times in 24 h (more than {profile.max_incidents_per_day}): "
                              "it needs a look, not another reset")

        for rung in self._rungs(profile):
            code, why = self._skip(rung, device, profile, incident)
            if code:
                self.say(f"  skip {rung.title}: {why}")
                self.attempts.append(store.record({"incident": incident.id, "rung": rung.kind, "outcome": "skipped",
                                                   "reason": code, "words": why, "mode": request.mode, "caller": self._caller()}))
                continue
            result = self._rung(rung, device, profile, store, incident, before)
            if result is not None:
                return result
            before = self.attempts[-1]["after"]
        if request.mode == PERSON:
            text = fill(profile.page_text, self._page_values(device, incident, profile))
            self.say(f"not cured: {text}")
            record = _event("RecoveryNotCured", "error", text, incident=incident.id)
            return Result(NEEDS_HUMAN, "not_cured", text, [*self.result_records, record], plan, self.attempts)
        return self._page(device, profile, store, incident, "every rung RobotDx may run here has been tried")

    def _rung(self, rung: Rung, device: devices.Device, profile: RecoveryProfile, store: Store, incident, before: dict):
        request = self.request
        started = self.seams.now()
        incident.tried[rung.kind] = started
        incident.tried_by[rung.kind] = "person" if request.mode == PERSON else (
            f"owner pid {request.owner_pid}" if request.owner_pid else "robotdx")
        store.save(incident)  # WHY before it runs: a ladder killed mid-rung must not run the same rung again this incident
        self.say(f"  {rung.title}: {rung.why}")
        if rung.kind == REOPEN:
            outcome = mechanisms.Outcome(mechanisms.DONE, "fresh look, nothing reset")
        else:
            outcome = mechanisms.run_rung(rung, device, self.seams, plugins_dir=request.plugins_dir, skeleton=request.skeleton)
        self.say(f"    {outcome.state}: {outcome.words}")
        back = None
        if outcome.state == mechanisms.DONE and rung.settle_s > 0:
            back = self._settle(device, profile, rung.settle_s)
        now = devices.resolve(device.key, device.port) if not device.is_can else devices.resolve(device.key)
        if not now.port:
            now = dataclasses.replace(now, port=device.port)
        found, _others = self._holders(now)
        healthy, words = (None, "not probed: the rung did not run") if outcome.state != mechanisms.DONE else probes.probe(
            profile, now, self.seams, holders=found, probe_command=request.probe_command, plugins_dir=request.plugins_dir,
            skeleton=request.skeleton)
        after = devices.snapshot(now, found)
        after["kernel"] = self.seams.kernel_lines(started - 1.0, device.port)
        verdict = ("cured" if healthy else "back_not_judged" if healthy is None and outcome.state == mechanisms.DONE
                   else "not_cured" if outcome.state == mechanisms.DONE else outcome.state)
        entry = store.record({"incident": incident.id, "rung": rung.kind, "title": rung.title, "outcome": verdict,
                              "mechanism": outcome.state, "words": outcome.words, "command": outcome.command,
                              "exit_code": outcome.exit_code, "output": outcome.output[-2000:], "seconds": round(self.seams.now() - started, 2),
                              "back_after_s": back, "probe": {"kind": profile.probe, "ok": healthy, "words": words},
                              "before": before, "after": after, "mode": request.mode, "caller": self._caller(),
                              "expect": rung.expect, **({"details": outcome.details} if outcome.details else {})})
        self.attempts.append(entry)
        self.result_records.append(_event("RecoveryRung", "info" if healthy else "warn", f"{rung.title}: {outcome.words}; {words}",
                                          incident=incident.id, rung=rung.kind, outcome=verdict))
        self.say(f"    {'answers' if healthy else 'still not answering' if healthy is False else 'back; not asked'}: {words}")
        if healthy:
            incident.cured_by = rung.kind
            store.save(incident)
            if request.mode == UNATTENDED:
                store.healthy(profile.quiet_s)
            message = f"{device.name} answers again after {rung.title.lower()}"
            return Result(HEALTHY, "cured", message, [*self.result_records, _event("RecoveryCured", "info", message,
                                                                                   incident=incident.id, rung=rung.kind)],
                          attempts=self.attempts)
        if outcome.details.get("pending"):
            return self._page(device, profile, store, incident, f"{rung.title} is still pending in the kernel: RobotDx stopped "
                              "there rather than stack another reset on it")
        # WHY stop when the probe could not ask: the owner has it open, so only the owner can tell whether that reset cured it;
        # stacking the next, heavier reset on a device that may already be fine is what the ladder must never do
        if outcome.state == mechanisms.DONE and (request.step or healthy is None):
            message = f"{rung.title} ran; {words}: reopen it and call again if it still does not answer"
            return Result(NOT_JUDGED, "not_judged", message, [*self.result_records], attempts=self.attempts)
        return None

    def _settle(self, device: devices.Device, profile: RecoveryProfile, settle_s: float) -> float | None:
        """Seconds until it is back on USB (configured, drivers bound, its nodes there), or None within ``settle_s``."""

        if device.is_can:
            self.seams.sleep(min(settle_s, 2.0))
            return None
        start = self.seams.monotonic()
        self.seams.sleep(SETTLE_POLL_S)
        while True:
            now = devices.resolve(device.key, device.port)
            if not now.port:
                now = dataclasses.replace(now, port=device.port)
            if devices.present_now(now, profile.expects_nodes)[0]:
                return round(self.seams.monotonic() - start, 2)
            if self.seams.monotonic() - start >= settle_s:
                return None
            self.seams.sleep(SETTLE_POLL_S)

    # ----- blocked, and the page ----------------------------------------------------------------------------------------
    def _blocked(self, device, profile, store: Store, why: str, reason: str) -> Result:
        # the owner says it is dead: the outage has begun even if nothing may be reset yet, so the blocked time counts
        incident = store.current() or (store.open(self.request.detected, device.port) if self.request.detected else None)
        if incident is not None:
            if incident.blocked_since is None:
                incident.blocked_since = self.seams.now()
                store.save(incident)
            elif self.seams.now() - incident.blocked_since >= BLOCKED_PAGE_S and incident.paged is None:
                return self._page(device, profile, store, incident, f"RobotDx may not reset it: {why}")
        return self._refuse(reason, why, store)

    def _page_values(self, device: devices.Device, incident, profile: RecoveryProfile | None = None) -> dict:
        kinds = list(incident.tried if incident else {})
        rungs = [profile.rung(kind) if profile else None for kind in kinds]
        words = [(rung.title[0].lower() + rung.title[1:]) if rung else kind for kind, rung in zip(kinds, rungs)]
        return {"name": device.name, "port": device.port or "?", "serial": device.serial or "?", "node": device.node,
                "tried": "; ".join(words) if words else "nothing (no rung could run here)"}

    def _page(self, device: devices.Device, profile: RecoveryProfile, store: Store, incident, because: str) -> Result:
        # WHY the reason rides in the text: a person reading a page on a phone must see "tview has it open" without the ledger
        text = f"{fill(profile.page_text, self._page_values(device, incident, profile))} Why: {because}."
        if incident.paged is not None:
            message = f"a human was already paged at {_clock(incident.paged)}: {text}"
            self.say(message)
            return Result(NEEDS_HUMAN, "already_paged", message, list(self.result_records), attempts=self.attempts)
        alarm = store.alarm({"incident": incident.id, "name": device.name, "port": device.port, "profile": profile.name,
                             "text": text, "because": because, "tried": dict(incident.tried)})
        command = self.request.page_command or (os.environ.get(PAGE_ENV, "").split() if os.environ.get(PAGE_ENV) else [])
        sent = ""
        if command:
            argv = [fill(word, {"device": device.key, "text": text}) for word in command]
            code, output = self.seams.run(argv, 10.0, None)
            sent = f"page command exited {code}" if code is not None else "page command ran over 10 s"
        incident.paged = self.seams.now()
        store.save(incident)
        self.attempts.append(store.record({"incident": incident.id, "rung": PAGE, "outcome": "paged", "words": text,
                                           "because": because, "page_command": sent, "mode": self.request.mode,
                                           "caller": self._caller(), "alarm_utc": alarm["utc"]}))
        self.say(f"NEEDS A HUMAN: {text} ({because})")
        record = _event("RecoveryNeedsHuman", "error", text, incident=incident.id, because=because)
        return Result(NEEDS_HUMAN, "paged", text, [*self.result_records, record], attempts=self.attempts)


def _clock(stamp) -> str:
    import datetime as dt

    try:
        return dt.datetime.fromtimestamp(float(stamp)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "?"


def runnable(request: Request, seams: Seams | None = None) -> tuple[bool, str]:
    """``(a person's Reset… has a rung it may run here, plain words)``: the host's ``ladder_runnable`` precondition. Cheap and
    read-only on purpose (the host asks it whenever it draws the row): sysfs modes and the plugin declarations, no /proc walk,
    no lock, nothing opened. The dry run itself still re-checks everything."""

    seams = seams or Seams()
    ladder = Ladder(dataclasses.replace(request, mode=PERSON, dry_run=True), seams, say=lambda _text: None)
    try:
        device, profile, _store = ladder._device()
    except devices.DeviceError as error:
        return False, str(error)
    titles, why = [], []
    for rung in ladder._rungs(profile):
        if rung.kind in (NATIVE, *USB_KINDS[:2]) and not device.present and not device.is_can:
            why.append(f"{rung.title}: it is not on USB now")
            continue
        here = availability.rung_availability(rung, device, profile, seams, request.plugins_dir)
        (titles if here.usable else why).append(rung.title if here.usable else f"{rung.title}: {here.why}")
    if titles:
        return True, f"{len(titles)} to try: " + "; ".join(titles)
    return False, "nothing RobotDx may try here: " + "; ".join(why)
