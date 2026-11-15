"""Mutation checks for the reset ladder's refusal paths and safety rules (docs/device-reset.md "Proof"): each mutation breaks one
gate or rule in a COPY of the tree and tests/test_recovery.py must fail; the checkout itself is never edited.

    uv run --project /home/bam/io-diagnostics --group dev python /home/bam/io-diagnostics/tests/mutate_recovery.py

Prints CAUGHT / MISSED per mutation (the first failing test) and exits 1 if any was missed or its text is no longer in the source.
"""
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY = os.path.join(tempfile.mkdtemp(prefix='recovery-mutation-'), 'tree')
PY = sys.executable
PYTESTS = ['tests/test_recovery.py']

LADDER = 'io_diagnostics/recovery/ladder.py'
MECH = 'io_diagnostics/recovery/mechanisms.py'
AVAIL = 'io_diagnostics/recovery/availability.py'
SYSTEM = 'io_diagnostics/recovery/system.py'
INCIDENT = 'io_diagnostics/recovery/incident.py'
SUPPORT = 'tests/test_host_support.py'
RACK = 'io_diagnostics/rack_lock.py'
USB = 'io_diagnostics/usb_reset.py'
MUTATIONS = [
    # (name, file, old, new)
    ('off switch: unattended recovery ignores recovery.off', LADDER,
     'if request.mode == UNATTENDED and off_switch().exists() and not request.dry_run:', 'if False:'),
    ('hold: a person\'s hold is ignored', LADDER, 'if hold is not None and not request.dry_run:', 'if False:'),
    ('owner: a gone owner is accepted', LADDER, 'if not alive(request.owner_pid):', 'if False:'),
    ('owner: any process may declare any pid the owner', LADDER,
     'if request.owner_pid != request.caller_pid and request.owner_pid not in ancestors(request.caller_pid):', 'if False:'),
    ('replay: a recording host resets', LADDER, 'if replay and not request.dry_run:', 'if False:'),
    ('ladder lock: two ladders run on one device', LADDER,
     '            if not mine:\n                return self._refuse("LadderRunning"', '            if False:\n                return self._refuse("LadderRunning"'),
    ('holders: someone else having it open does not block an unattended run', LADDER,
     '        elif others:\n            return self._blocked(', '        elif False:\n            return self._blocked('),
    ('holders: the owner\'s helpers count as someone else', LADDER,
     'mine |= {self.request.owner_pid, *descendants(self.request.owner_pid)}', 'mine |= {self.request.owner_pid}'),
    ('blocked: a holder that blocks forever is never paged', LADDER,
     'elif self.seams.now() - incident.blocked_since >= BLOCKED_PAGE_S and incident.paged is None:', 'elif False:'),
    ('person: the token is not checked', LADDER, 'if request.confirm != plan["token"]:', 'if False:'),
    ('person: the second confirmation is not needed', LADDER, 'if others and not request.confirm_holders:', 'if False:'),
    ('person: the token ignores who holds it', LADDER,
     '",".join(str(holder.pid) for holder in sorted(others, key=lambda h: h.pid))])', '""])'),
    ('cannot tell: an unattended run resets what it cannot judge', LADDER,
     'if healthy is None and request.mode == UNATTENDED:\n            return self._refuse("CannotTell"',
     'if False:\n            return self._refuse("CannotTell"'),
    ('healthy: a healthy device is reset anyway', LADDER,
     'if healthy and request.mode == UNATTENDED:\n            incident = store.healthy', 'if False:\n            incident = store.healthy'),
    ('person only: a board reboot runs unattended', LADDER,
     'if request.mode == UNATTENDED and not rung.unattended:', 'if False:'),
    ('once per incident: a rung runs again in the same incident', LADDER,
     'if rung.kind in incident.tried:\n                return "AlreadyTried"', 'if False:\n                return "AlreadyTried"'),
    ('same reset: usbreset runs again after fdcanusb-reset did the same ioctl', LADDER,
     'if same is not None:\n                return "SameReset"', 'if False:\n                return "SameReset"'),
    ('owner tried: the owner\'s own reset is run again', LADDER,
     'if rung.kind in request.tried_by_owner:\n            return "DoneByOwner"', 'if False:\n            return "DoneByOwner"'),
    ('availability: a rung that cannot run here is run anyway', LADDER,
     'if not here.usable:\n            return "NotAvailable"', 'if False:\n            return "NotAvailable"'),
    ('crash: a rung is marked tried only after it ran', LADDER,
     '        store.save(incident)  # WHY before it runs', '        pass  # WHY before it runs'),
    ('not judged: a heavier reset is stacked when the probe could not ask', LADDER,
     'if outcome.state == mechanisms.DONE and (request.step or healthy is None):',
     'if outcome.state == mechanisms.DONE and request.step:'),
    ('pending: a rung still pending in the kernel does not stop the ladder', LADDER,
     'if outcome.details.get("pending"):', 'if False:'),
    ('page: a human is paged again every call', LADDER, 'if incident.paged is not None:\n            message', 'if False:\n            message'),
    ('budget: a device that keeps dying is reset forever', LADDER,
     'if request.mode == UNATTENDED and store.opened_in_last_day() > profile.max_incidents_per_day:', 'if False:'),
    ('quiet window: an incident closes at the first good answer', INCIDENT,
     'if self.now() - found.healthy_since >= quiet_s:', 'if True:'),
    ('incident: a failure inside the quiet window opens a new incident', INCIDENT,
     'found = self.current()\n        if found is not None:\n            found.healthy_since = None',
     'found = None\n        if found is not None:\n            found.healthy_since = None'),
    ('authorized: an error between the writes leaves it deauthorized', MECH,
     '        finally:\n            # WHY in a finally: an error or a Stop', '        except ZeroDivisionError:\n            # WHY in a finally: an error or a Stop'),
    ('power cycle: the USB 3 peer is left off', MECH,
     'switches = [here / "disable"] + ([here / "peer" / "disable"] if (here / "peer" / "disable").exists() else [])\n    command',
     'switches = [here / "disable"]\n    command'),
    ('power cycle: an error while off leaves the port off', MECH,
     '        finally:\n            # WHY every switch back on', '        except ZeroDivisionError:\n            # WHY every switch back on'),
    ('power cycle: a hub without per-port power switching is power cycled', AVAIL,
     'if mode in ("ganged", "none"):', 'if False:'),
    ('power cycle: an unknown hub is power cycled', AVAIL, 'if mode != "individual":', 'if False:'),
    ('privilege: a node you cannot write counts as usable for fdcanusb-reset', AVAIL,
     'if node is not None and target.present and not _writable(node):', 'if False:'),
    ('driver: a refusing driver counts as a reset done', MECH,
     'if refused is not None or not token:', 'if False:'),
    ('tty: a console someone else has locked is written to', SYSTEM,
     'except OSError:\n            return None, f"{node} is locked by another process: not asked"',
     'except OSError:\n            pass'),
    ('tty: a line from the bus (rcv) counts as the adapter\'s answer', SYSTEM,
     'if text and not text.startswith("rcv"):', 'if text:'),
    ('tripwire: an in-process write to the real /sys is allowed in a test', SYSTEM,
     '    if not os.environ.get(TRIPWIRE_ENV):\n        return', '    return'),
    ('nothing to run: a plan with no rung still carries a token', LADDER,
     'token = hashlib.sha256(seed.encode()).hexdigest()[:16] if runnable else ""', 'token = hashlib.sha256(seed.encode()).hexdigest()[:16]'),
    ('nothing to run: a real run with no rung goes ahead', LADDER,
     'if not plan["runs"]:\n                return self._refuse("NothingToRun"', 'if False:\n                return self._refuse("NothingToRun"'),
    ('board: a board\'s reset ignores who has its adapter open', LADDER,
     '        if not device.port:\n            return [], []\n        # a CAN board', '        if not device.port or device.is_can:\n            return [], []\n        # a CAN board'),
    ('rack lock: the ladder resets the arm\'s adapter without the rack bus lock', LADDER,
     'if not profile.owner_locks:\n            return self._climb(', 'if True:\n            return self._climb('),
    ('rack lock: a lock held by another process does not refuse', RACK,
     'others = [pid for pid in holders if pid not in family]', 'others = []'),
    ('rack lock: a lock it cannot take is taken as held', RACK,
     '            yield False, f"the rack bus lock ({path}) is held (its holder could not be named)"\n            return',
     '            pass'),
    ('rack lock: a redirected lock counts on a real machine', RACK,
     'return Path(named) if named else Path(os.environ["IO_DIAGNOSTICS_USB_SYSFS"]).parent / "rack-bus.lock"\n    return REAL_BUS_LOCK',
     'return Path(named) if named else Path(os.environ["IO_DIAGNOSTICS_USB_SYSFS"]).parent / "rack-bus.lock"\n    return Path(os.environ.get(FAKE_LOCK_ENV) or REAL_BUS_LOCK)'),
    ('rack lock: reset_port resets the arm\'s adapter without the lock', USB,
     'if rack_lock.is_rack_adapter(target.vid, target.pid, _read(sysfs_root() / target.port / "product")):', 'if False:'),
    ('vanished: an adapter gone from USB is not found at the port the kernel last saw it on', LADDER,
     'or self.seams.port_from_kernel(found.serial)', 'or ""'),
    ('cost: the host\'s row check reads the kernel log', LADDER,
     '"" if request.dry_run else self.seams.port_from_kernel(found.serial))', 'self.seams.port_from_kernel(found.serial))'),
    ('dry run: a dry run takes the ladder lock and leaves its file', LADDER,
     '        if self.request.dry_run:\n            return self._run(device, profile, store)  # a dry run changes nothing',
     '        if False:\n            return self._run(device, profile, store)  # a dry run changes nothing'),
    ('tripwire: the real fdcanusb-reset may run in a test', SUPPORT,
     'if words and Path(words[0]).name == "fdcanusb-reset" and not (', 'if False and not ('),
]


def run():
    env = dict(os.environ)
    env.pop('VIRTUAL_ENV', None)
    return subprocess.run([PY, '-m', 'pytest', '-q', '-x', '-p', 'no:cacheprovider', *PYTESTS], cwd=COPY, capture_output=True, text=True,
                          env=env, timeout=900)


def main():
    os.makedirs(COPY)
    for name in ('io_diagnostics', 'contract', 'io_facts', 'tests', 'docs', 'plugins', 'plugins.d', 'tools', 'pyproject.toml'):
        src = os.path.join(SRC, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(COPY, name), ignore=shutil.ignore_patterns('__pycache__', 'node_modules', '.venv', 'reports'))
        elif os.path.exists(src):
            shutil.copy(src, COPY)
    baseline = run()
    print('baseline: exit', baseline.returncode, (baseline.stdout.strip().splitlines() or [''])[-1])
    if baseline.returncode != 0:
        print(baseline.stdout[-1500:], baseline.stderr[-800:])
        sys.exit(1)
    caught = missed = 0
    for name, rel, old, new in MUTATIONS:
        path = os.path.join(COPY, rel)
        original = open(path).read()
        if old not in original:
            print(f'SKIP    {name}: the text to mutate is not in {rel}')
            missed += 1
            continue
        open(path, 'w').write(original.replace(old, new, 1))
        try:
            result = run()
        finally:
            open(path, 'w').write(original)
        tail = [line for line in (result.stdout + result.stderr).splitlines() if line.startswith(('FAILED', 'ERROR')) or 'failed' in line][:1]
        if result.returncode != 0:
            caught += 1
            print(f'CAUGHT  {name}  ·  {tail[0][:110] if tail else ""}')
        else:
            missed += 1
            print(f'MISSED  {name}')
    print(f'\n{caught} of {caught + missed} mutations caught')
    shutil.rmtree(os.path.dirname(COPY), ignore_errors=True)
    sys.exit(0 if not missed else 1)


if __name__ == '__main__':
    main()
