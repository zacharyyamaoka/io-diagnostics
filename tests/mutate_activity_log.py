"""Mutation checks for the Activity log's key rules (docs/activity-log-v1.md "Proof"): each mutation breaks one rule in a COPY of
the tree and a test must fail; the checkout itself is never edited.

    /home/bam/io-diagnostics/.venv/bin/python /home/bam/io-diagnostics/tests/mutate_activity_log.py

Prints CAUGHT / MISSED per mutation (the first failing test) and exits 1 if any was missed or its text is no longer in the source.
"""
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY = os.path.join(tempfile.mkdtemp(prefix='activity-mutation-'), 'tree')
PY = sys.executable
PYTESTS = ['tests/test_activity_log.py', 'tests/test_activity_log_host.py', 'tests/test_record.py']

MUTATIONS = [
    # (name, file, old, new, command)
    ('coalescing: the gap is ignored', 'io_diagnostics/logview.py', 'COALESCE_GAP_S = 60.0', 'COALESCE_GAP_S = 1e12', 'py'),
    ('coalescing: the message is not part of "identical"', 'io_diagnostics/logview.py', 'key = (event.code, event.source, event.path, event.message, event.severity)', 'key = (event.code, event.source, event.path, event.severity)', 'py'),
    ('coalescing: a plug merges like any other repeat', 'io_diagnostics/logview.py', 'NO_COALESCE = {"Booted", "ShutDown", *PLUG_CODES,', 'NO_COALESCE = {"Booted", "ShutDown",', 'py'),
    ('folding: a cause may be any time before', 'io_diagnostics/logview.py', 'CAUSE_WINDOW_S = 10.0', 'CAUSE_WINDOW_S = 1e9', 'py'),
    ('folding: an info event can be a cause', 'io_diagnostics/logview.py', 'if event.path != fault.path or SEVERITY_ORDER.get(event.severity, 1) < SEVERITY_ORDER["warn"]:', 'if event.path != fault.path:', 'py'),
    ('folding: a cause may be on another entity', 'io_diagnostics/logview.py', 'if event.path != fault.path or SEVERITY_ORDER.get(event.severity, 1) < SEVERITY_ORDER["warn"]:', 'if SEVERITY_ORDER.get(event.severity, 1) < SEVERITY_ORDER["warn"]:', 'py'),
    ('folding: a fault with no cause is never "found at start"', 'io_diagnostics/logview.py', 'elif _args(fault).get("at_start") is not None:', 'elif False:', 'py'),
    ('folding: every host start shares one found-at-start row', 'io_diagnostics/logview.py', 'started_groups.setdefault(_args(fault)["at_start"], []).append(fault)', 'started_groups.setdefault("one", []).append(fault)', 'py'),
    ('folding: healing does not update the badge', 'io_diagnostics/logview.py', 'return ("healed" if event.code == "FaultHealed" else "cleared"), event.stamp', 'return "confirmed", None', 'py'),
    ('transitions: silence is never explained by a plug', 'io_diagnostics/logview.py', 'elif to in SILENCE and not _explained(event, plugs):', 'elif to in SILENCE:', 'py'),
    ('transitions: silence is always explained', 'io_diagnostics/logview.py', 'elif to in SILENCE and not _explained(event, plugs):', 'elif to in SILENCE and False:', 'py'),
    ('transitions: a warn transition keeps its own row', 'io_diagnostics/logview.py', 'if target is not None:\n                target.members.append(event)\n                continue', 'if False:\n                continue', 'py'),
    ('transitions: a plain warn transition is info, not debug', 'io_diagnostics/logview.py', '            shown = dataclasses.replace(event, severity="debug")\n        elif to in SILENCE', '            shown = dataclasses.replace(event, severity="info")\n        elif to in SILENCE', 'py'),
    ('runs: an unfinished run is "running" even when the host has no such run', 'io_diagnostics/logview.py', 'elif active is False:', 'elif False:', 'py'),
    ('scope: the path up to the host is never added', 'io_diagnostics/logview.py', 'return scope == "host" and bool(path) and _at(entity, path)', 'return False', 'py'),
    ('scope: "children" matches a name prefix, not the path boundary', 'io_diagnostics/logview.py', 'return path == ancestor or path.startswith(ancestor + "/")', 'return path.startswith(ancestor)', 'py'),
    ('floor: debug counts as info', 'io_diagnostics/logview.py', 'SEVERITY_ORDER = {"debug": 0, "info": 1,', 'SEVERITY_ORDER = {"debug": 1, "info": 1,', 'py'),
    ('raw: a kernel line has no port', 'io_diagnostics/logview.py', 'port = _KERNEL_PORT.match(text)', 'port = None', 'py'),
    ('persistence: nothing is pruned', 'io_diagnostics/eventlog.py', 'if total <= self.cap_bytes or path == current:\n                break', 'break', 'py'),
    ('persistence: a segment never closes', 'io_diagnostics/eventlog.py', 'if mine and mine[-1].stat().st_size < self.segment_bytes:', 'if mine:', 'py'),
    ('persistence: a kernel event is written before it settles', 'io_diagnostics/eventlog.py', 'return event.stamp is not None and now - event.stamp >= SETTLE_S', 'return True', 'py'),
    ('persistence: `received` is the time of the latest sighting', 'io_diagnostics/eventlog.py', 'first = held.received if held is not None and held.received is not None else self._first_seen.setdefault(key, now)', 'first = now', 'py'),
    ('persistence: a changed event is not written again', 'io_diagnostics/eventlog.py', 'if held is not None and dataclasses.replace(held, seq=event.seq) == event:', 'if held is not None:', 'py'),
    ('persistence: the log is not read at start', 'io_diagnostics/eventlog.py', '        self._load()\n\n    # --- reading', '        pass\n\n    # --- reading', 'py'),
    ('persistence: another host\'s lines are not read first', 'io_diagnostics/eventlog.py', '        for path in self.files():\n            if path.name.startswith(self.prefix):\n                self._read_new(path)', '        pass', 'py'),
    ('persistence: the host does not write what it sees', 'io_diagnostics/host.py', '        self.eventlog.record(events, now)\n', '', 'py'),
    ('faults: a start-up confirmation is not marked', 'io_diagnostics/faults.py', 'starting = self._first is not None and now - self._first <= self.start_window_s', 'starting = False', 'py'),
    ('record: a warn is level 40 on the stock side', 'contract/record.py', 'ROS_LOG_LEVEL = {"debug": 10, "info": 20, WARN: 30,', 'ROS_LOG_LEVEL = {"debug": 10, "info": 20, WARN: 40,', 'py'),
    ('record: the stock stamp is cut to whole milliseconds', 'contract/record.py', 'microseconds = round((stamp - seconds) * 1e6)', 'microseconds = round((stamp - seconds) * 1e3) * 1000', 'py'),
    ('record: the code is not carried in `function`', 'contract/record.py', '"file": event.source, "function": event.code, "line": event.seq or 0}', '"file": event.source, "function": "", "line": event.seq or 0}', 'py'),
    ('record: a stock level between two bands rounds up', 'contract/record.py', 'for name, below in (("debug", 20), ("info", 30), (WARN, 40), (ERROR, 50)):', 'for name, below in (("debug", 10), ("info", 20), (WARN, 30), (ERROR, 40)):', 'py'),
    # the page's logic (node, no browser)
    ('search: the same key is ANDed', 'io_diagnostics/web/activity/core.js', "if (!groups.has(clause.key)) groups.set(clause.key, false);\n      if (clause.test(row)) groups.set(clause.key, true);", "if (!groups.has(clause.key + clause.value)) groups.set(clause.key + clause.value, false);\n      if (clause.test(row)) groups.set(clause.key + clause.value, true);", 'js'),
    ('search: a negation is ignored', 'io_diagnostics/web/activity/core.js', 'if (clause.neg) { if (clause.test(row)) return false; continue; }', 'if (clause.neg) continue;', 'js'),
    ('search: level: means exactly that level', 'io_diagnostics/web/activity/core.js', 'test: (row) => sevRank(row.severity) >= RANK[floor] }', 'test: (row) => sevRank(row.severity) === RANK[floor] }', 'js'),
    ('filters: the search can lower the severity floor', 'io_diagnostics/web/activity/core.js', 'if (sevRank(row.severity) < floor) { hidden.floor += 1; continue; }', 'if (sevRank(row.severity) < floor && !search.clauses.some((c) => c.key === "level")) { hidden.floor += 1; continue; }', 'js'),
    ('volume: a repeat counts once', 'io_diagnostics/web/activity/core.js', 'Math.max(row.count || 1, 1); // a row', '1; // a row', 'js'),
    ('output: a \\r does not start the line over', 'io_diagnostics/web/activity/core.js', "        current = '';\n      }\n      if (c === '\\x1b') mode = 'esc';", "      }\n      if (c === '\\x1b') mode = 'esc';", 'js'),
    ('output: escape sequences are shown', 'io_diagnostics/web/activity/core.js', "if (c === '\\x1b') mode = 'esc';", "if (false) mode = 'esc';", 'js'),
]


def run(command):
    env = dict(os.environ)
    env.pop('VIRTUAL_ENV', None)
    if command == 'py':
        return subprocess.run([PY, '-m', 'pytest', '-q', '-x', '-p', 'no:cacheprovider', *PYTESTS], cwd=COPY, capture_output=True, text=True, env=env, timeout=600)
    return subprocess.run(['/usr/bin/node', 'tests/test_activity_view.js'], cwd=COPY, capture_output=True, text=True, env={**env, 'ACTIVITY_CORE_ONLY': '1'}, timeout=600)


def main():
    os.makedirs(COPY)
    for name in ('io_diagnostics', 'contract', 'io_facts', 'tests', 'docs', 'plugins', 'plugins.d', 'pyproject.toml'):
        src = os.path.join(SRC, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(COPY, name), ignore=shutil.ignore_patterns('__pycache__', 'node_modules', '.venv', 'reports'))
        elif os.path.exists(src):
            shutil.copy(src, COPY)
    baseline = {'py': run('py'), 'js': run('js')}
    for kind, result in baseline.items():
        print(f'baseline {kind}: exit {result.returncode}', (result.stdout.strip().splitlines() or [''])[-1])
        if result.returncode != 0:
            print(result.stdout[-1500:], result.stderr[-800:])
            sys.exit(1)
    caught = missed = 0
    for name, rel, old, new, kind in MUTATIONS:
        path = os.path.join(COPY, rel)
        original = open(path).read()
        if old not in original:
            print(f'SKIP  {name}: the text to mutate is not in {rel}')
            missed += 1
            continue
        open(path, 'w').write(original.replace(old, new, 1))
        try:
            result = run(kind)
        finally:
            open(path, 'w').write(original)
        tail = [line for line in (result.stdout + result.stderr).splitlines() if line.startswith(('FAILED', 'FAIL ')) or 'passed' in line or 'failed' in line][:1]
        if result.returncode != 0:
            caught += 1
            print(f'CAUGHT  {name}  ·  {tail[0][:110] if tail else ""}')
        else:
            missed += 1
            print(f'MISSED  {name}')
    print(f'\n{caught} of {caught + missed} mutations caught')
    sys.exit(0 if not missed else 1)


if __name__ == '__main__':
    main()
