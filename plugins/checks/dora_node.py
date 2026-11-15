#!/usr/bin/env python3
"""A Monitoring Plugins check for one driver's dora-rs node: is it live, and how healthy?

    dora_node.py --dataflow obsbot --node obsbot_node [--unit obsbot-dataflow.service] -- STATUS_COMMAND...

The driver-template contract (robotparts docs/drivers.md, "Status"): every dora-wrapped driver ships a
``status --json`` command that prints ONE JSON object with ``age_s`` (seconds since its gateway last
heard the node, on this host's clock) and exits 0 healthy, 3 no gateway / no status, 5 stale. This check
turns that into RobotDx's check API (plugins.d/README.md "Checks"): exit 0 OK, 1 WARNING, 2 CRITICAL,
3 UNKNOWN, the first stdout line the message, perfdata after ``|``.

- live and the node's own ``health.level`` ok (or no level: the belt reports a mode instead) -> OK
- the node's ``health.level`` warn (a camera with no picture, say) -> WARNING, the node's message
- the node's ``health.level`` error, a non-zero ``fault`` -> CRITICAL
- status stale (5), no gateway (3), or the dataflow not Running in ``dora list`` -> CRITICAL
- the supervisor unit not active -> WARNING appended (the node may be up, but nothing restarts the
  dataflow after a reboot)

Read-only: ``dora list``, ``systemctl --user is-active``, and the driver's status command, which reads
its gateway's cached status (no dora round trip, no command to the device). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3
WORDS = {OK: "OK", WARNING: "WARNING", CRITICAL: "CRITICAL", UNKNOWN: "UNKNOWN"}
DEFAULT_DORA = "/home/bam/hyper_rgb/netbelt/.venv/bin/dora"


def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return done.returncode, done.stdout


def dataflow_states(dora: str, timeout: float = 8.0) -> dict[str, list[str]] | None:
    """{name: [status, ...]} from ``dora list`` (several rows can share a name), or None."""

    code, out = _run([dora, "list"], timeout)
    if code != 0:
        return None
    states: dict[str, list[str]] = {}
    for line in out.splitlines()[1:]:
        words = line.split()
        if len(words) >= 3:
            states.setdefault(words[1], []).append(words[2])
    return states


def _dig(data: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def verdict(status_exit: int, status: dict[str, Any] | None, *, dataflow: str, node: str,
            running: bool | None, unit_active: bool | None, summary_keys: list[str]) -> tuple[int, str, list[str]]:
    """(exit code, message, perfdata items): pure, for the tests."""

    where = f"{node} in dataflow {dataflow}"
    perf: list[str] = []
    age = None if status is None else status.get("age_s")
    if isinstance(age, (int, float)):
        perf.append(f"age_s={age:g}s;2;5;0")
    facts = []
    for key in summary_keys:
        value = _dig(status, key) if status else None
        if value is not None:
            facts.append(f"{key.rsplit('.', 1)[-1]} {value}")
    detail = f" ({', '.join(facts)})" if facts else ""

    if running is False:
        code, message = CRITICAL, f"no dora node: dataflow {dataflow} is not Running"
    elif status_exit == 3 or status is None:
        code, message = CRITICAL, f"no dora node: {where} has no gateway or no status yet"
    elif status_exit == 5:
        code, message = CRITICAL, f"dora node stale: {where} last heard {age} s ago"
    elif status_exit != 0:
        code, message = UNKNOWN, f"status command exited {status_exit} for {where}"
    else:
        level = _dig(status, "health.level") or status.get("level")
        fault = status.get("fault")
        node_message = _dig(status, "health.message") or status.get("message")
        if level == "error" or (isinstance(fault, int) and fault != 0):
            code = CRITICAL
            message = f"dora node live, device unhealthy: {node_message or f'fault {fault}'}"
        elif level == "warn":
            code, message = WARNING, f"dora node live: {node_message}"
        else:
            code = OK
            message = f"dora node live: {where}, age {age} s" + (f"; {node_message}" if node_message else "")
    message += detail
    if unit_active is False:
        code = max(code, WARNING) if code != UNKNOWN else code
        message += "; its supervisor unit is not active (nothing restarts it after a reboot)"
    return code, message, perf


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("DORA UNKNOWN - usage: dora_node.py --dataflow NAME --node ID [--unit U] -- STATUS_COMMAND...")
        return UNKNOWN
    split = argv.index("--")
    parser = argparse.ArgumentParser(prog="dora_node.py")
    parser.add_argument("--dataflow", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--unit", default=None, help="the systemd user unit that supervises the dataflow")
    parser.add_argument("--summary", action="append", default=[],
                        help="status keys (dotted) to show in the message, e.g. mode, health.fps")
    parser.add_argument("--dora", default=os.environ.get("DORA_CLI", DEFAULT_DORA))
    parser.add_argument("--timeout", type=float, default=8.0)
    arguments = parser.parse_args(argv[:split])
    command = argv[split + 1:]

    states = dataflow_states(arguments.dora)
    running = None if states is None else "Running" in states.get(arguments.dataflow, [])
    status_exit, out = _run(command, arguments.timeout)
    try:
        status = json.loads(out) if out.strip() else None
        if not isinstance(status, dict):
            status = None
    except ValueError:
        status = None
    unit_active = None
    if arguments.unit:
        code, out_unit = _run(["systemctl", "--user", "is-active", arguments.unit], 5.0)
        unit_active = out_unit.strip() == "active"
    code, message, perf = verdict(status_exit, status, dataflow=arguments.dataflow, node=arguments.node,
                                  running=running, unit_active=unit_active,
                                  summary_keys=arguments.summary)
    print(f"DORA {WORDS[code]} - {message}" + (f" | {' '.join(perf)}" if perf else ""))
    if states is not None:
        print(f"dora list: {arguments.dataflow} {', '.join(states.get(arguments.dataflow, [])) or 'absent'}")
    print(f"status command: {' '.join(command)} (exit {status_exit})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
