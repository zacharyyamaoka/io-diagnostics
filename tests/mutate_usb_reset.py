"""Mutation checks for the Reset USB port gates (docs/usb-reset.md "Proof"): each mutation breaks one gate in a COPY of the tree
and a test must fail; the checkout itself is never edited.

    uv run --project /home/bam/io-diagnostics --group dev python /home/bam/io-diagnostics/tests/mutate_usb_reset.py

Prints CAUGHT / MISSED per mutation (the first failing test) and exits 1 if any was missed or its text is no longer in the source.
"""
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY = os.path.join(tempfile.mkdtemp(prefix='usb-reset-mutation-'), 'tree')
PY = sys.executable
PYTESTS = ['tests/test_usb_reset.py', 'tests/test_host_reset_action.py']  # the second: Reset…, the same gates (docs/device-reset.md)

USB = 'io_diagnostics/usb_reset.py'
HOST = 'io_diagnostics/host.py'
MUTATIONS = [
    # (name, file, old, new)
    ('privilege: the first mechanism is chosen whether or not this user may use it', USB,
     'return next((method for method in options if method.usable), None)', 'return options[0]'),
    ('privilege: a node you cannot write to counts as usable', USB, 'elif not _writable(node):', 'elif False:'),
    ('privilege: usbreset is addressed by vendor:product, not bus/device number', USB,
     'out.append(Method("usbreset", True, "", [tool, target.bus_dev], "argv",',
     'out.append(Method("usbreset", True, "", [tool, f"{target.vid}:{target.pid}"], "argv",'),
    ('holders: a real run goes ahead without the second confirmation', USB,
     'if found and "--confirm-holders" not in argv:', 'if False:'),
    ('holders: the confirmation token ignores who holds the device', USB,
     '",".join(str(holder.pid) for holder in sorted(found, key=lambda h: h.pid))])', '""])'),
    ('holders: the token is not checked at all', USB, 'if _arg(argv, "--confirm") != plan["token"]:', 'if False:'),
    ('holders: the host\'s own watch processes count as holders', USB,
     'if ignore_sessions and _session_of(pid) in ignore_sessions:', 'if False:'),
    ('holders: the device\'s tty / video nodes are not looked at', USB, 'for kind in NODE_DIRS:', 'for kind in ():'),
    ('holders: what hangs below a hub is not looked at', USB, 'return [port, *below]', 'return [port]'),
    ('replay: the verb resets for a recording', USB, 'if replay and not dry:', 'if False:'),
    ('replay: the host starts a real run on a recording', HOST,
     'if not dry_run and getattr(self.os, "mode", "live") != "live" and not usb_reset.sysfs_overridden():', 'if False:'),
    ('authorized: a Stop between the two writes leaves the device off', USB,
     '    finally:\n        # WHY in a finally: a Stop between', '    except ZeroDivisionError:\n        # WHY in a finally: a Stop between'),
    ('run active: never checked', HOST, 'if mine and theirs and (mine == theirs or', 'if False and (mine == theirs or'),
    ('run active: a run on a device does not block its port row', HOST,
     '(mine == theirs or mine.startswith(theirs + "/") or theirs.startswith(mine + "/")):',
     '(mine == theirs or mine.startswith(theirs + "/")):'),
    ('run active: refused at the dry run only in name', HOST, '        if busy:\n            return refuse("RunActive"',
     '        if False:\n            return refuse("RunActive"'),
    ('opt-out: a driver\'s opt-out is ignored', HOST,
     'return not any(name in ((self.describes.get(job.spec.name) or {}).get("opt_out") or []) for job in jobs)', 'return True'),
    ('opt-out: a hub is still reset under a device that opted out', 'io_diagnostics/host_utilities.py',
     'if found == port or found.startswith(port + "."):', 'if found == port:'),
    # WHY retargeted (2026-11-14): 'and result is not second]' only de-duplicates the Run page's confirmations since 2026-10-29,
    # and no test could see it; the gate is the line that makes a holder a confirmation instead of a block
    ('holders: something else having the device open blocks the button', HOST, 'second["resolves"] = "confirm"', 'second["resolves"] = "blocks"'),
    ('person: confirmation is not required of a person', HOST, '        if not dry_run and not confirm:\n            text = string.Formatter().vformat(host_utilities',
     '        if False:\n            text = string.Formatter().vformat(host_utilities'),
    ('person: an agent may run it', HOST, 'out["argv"] = argv\n        if not human and not dry_run:', 'out["argv"] = argv\n        if False:'),
    ('server: the second-confirmation flag reaches any driver\'s action', 'io_diagnostics/server.py',
     'body.get("confirm_holders") is True and action in host_utilities.NAMES', 'body.get("confirm_holders") is True'),
    ('after: a refusal (nothing happened) probes the device again', HOST,
     'if run.exit_code is None or run.exit_code == usb_reset.REFUSED:', 'if run.exit_code is None:'),
    ('reset: offered on a port row too', 'io_diagnostics/host_utilities.py',
     'if entity is not None and entity.kind == "port":\n            return None', 'if False:\n            return None'),
    ('reset: a board\'s run loses its --node', HOST, 'argv = act_argv(spec, device, action, node=node,', 'argv = act_argv(spec, device, action, node=None,'),
    ('reset: a replay host leaves the button live', HOST, 'return (False, "this host replays a recording or a fake machine: there is no real device',
     'return (True, "this host replays a recording or a fake machine: there is no real device'),
    ('after: a reset run is not followed by a probe', HOST, 'if run.spec.action in host_utilities.NAMES and run.spec.plugin',
     'if run.spec.action == usb_reset.NAME and run.spec.plugin'),
    ('after: the device is not probed again once the reset ended', HOST,
     '                    job.probed, job.blocked, job.next_due = False, None, 0.0\n        self.os.invalidate()',
     '                    pass\n        self.os.invalidate()'),
]


def run():
    env = dict(os.environ)
    env.pop('VIRTUAL_ENV', None)
    return subprocess.run([PY, '-m', 'pytest', '-q', '-x', '-p', 'no:cacheprovider', *PYTESTS], cwd=COPY, capture_output=True, text=True,
                          env=env, timeout=900)


def main():
    os.makedirs(COPY)
    for name in ('io_diagnostics', 'contract', 'io_facts', 'tests', 'docs', 'plugins', 'plugins.d', 'pyproject.toml'):
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
