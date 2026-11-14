"""``python -m io_diagnostics.recovery``: reset a device's way back, by itself or for a person (docs/device-reset.md).

    check [--json]                                  every device here, every rung: can it run here, and why not
    profiles [--json]                               the device x rung table, from the profiles
    plan --device KEY [--owner-pid N]               what an unattended run would do now (a dry run; nothing happens)
    recover --device KEY [--owner-pid N] [--detected WORDS] [--tried reopen,native] [--probe-command CMD] [--step]
            [--page-command CMD] [--plugins DIR] [--json]
    healthy --device KEY                            the owner says it answers again (starts the quiet window)
    hold --device KEY --minutes M --reason WHY      nothing resets it until then (a firmware flash, an experiment)
    ledger [--device KEY] [--limit N]               the attempts, newest last
    udev-rules                                      the installer's rules file, generated from the profiles
    describe | act --device usb:<port> --action reset [--node N] [--dry-run] [--confirm T] [--confirm-holders]
                                                    the host's plugin boundary: a person's Reset… run

KEY: usb:VID:PID:SERIAL (best), usb:<port>, serial:<serial>, can:<adapter serial>/<CAN id>.
Exit: 0 it answers · 2 refused, nothing was reset · 3 a rung ran, its owner judges · 4 a human is needed · 1 failed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import sys
import time
from pathlib import Path

from io_diagnostics import usb_reset
from io_diagnostics.recovery import availability, device as devices
from io_diagnostics.recovery.incident import Store, ledger, off_switch
from io_diagnostics.recovery.ladder import PERSON, UNATTENDED, Ladder, Request
from io_diagnostics.recovery.profiles import ALL, profile_for
from io_diagnostics.recovery.system import DEFAULT_HUB_FACTS, HUB_FACTS_ENV, Seams, tool_paths

ACTION = "reset"
TITLE = "Reset…"  # contract.record.STANDARD_ACTIONS["reset"]


def _records_out():
    fd = os.environ.get("IO_DIAGNOSTICS_RECORD_FD")
    return os.fdopen(int(fd), "w") if fd else sys.stdout


def _emit(out, record: dict) -> None:
    out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    out.flush()


def describe_json() -> dict:
    return {"name": "io_diagnostics_recovery", "schema": "diagnostics-record/0", "host_builtin": True,
            "actions": [{"name": ACTION, "title": TITLE, "danger": "interrupts", "scope": "device"}]}


# ----- check: what can run here ------------------------------------------------------------------------------------------
def machine_facts() -> dict:
    tools = tool_paths()
    rules = Path("/etc/udev/rules.d/70-robotdx-device-reset.rules")
    groups = []
    try:
        import grp

        groups = sorted(grp.getgrgid(gid).gr_name for gid in os.getgroups())
    except (ImportError, KeyError):
        pass
    kernel = platform.release()
    major_minor = tuple(int(part) for part in kernel.split("-")[0].split(".")[:2] if part.isdigit())
    return {"kernel": kernel, "port_disable_interface": major_minor >= (6, 0), "user": os.environ.get("USER", ""),
            "in_plugdev": "plugdev" in groups, "root": os.geteuid() == 0,
            "usbreset": usb_reset._tool(usb_reset.USBRESET_ENV, "usbreset"), "uhubctl": usb_reset._tool(usb_reset.UHUBCTL_ENV, "uhubctl"),
            "fdcanusb_reset": tools["fdcanusb_reset"] if os.access(tools["fdcanusb_reset"], os.X_OK) else None,
            "installed_rules": str(rules) if rules.exists() else None,
            "hub_facts": os.environ.get(HUB_FACTS_ENV) or (DEFAULT_HUB_FACTS if Path(DEFAULT_HUB_FACTS).exists() else None),
            "unattended": "off" if off_switch().exists() else "on"}


def check(seams: Seams, plugins_dir: Path | None) -> dict:
    rows = []
    for port in devices._ports():
        found = devices._device_at(port, f"usb:{port}")
        if (devices.sysfs_root() / port / "bDeviceClass").exists() and \
                (devices.sysfs_root() / port / "bDeviceClass").read_text().strip() == "09":
            continue  # a hub is reset only by what is plugged into it
        if found.serial and found.vid:
            found = devices.Device(**{**found.__dict__, "key": f"usb:{found.vid}:{found.pid}:{found.serial}"})
        profile = profile_for(found.keys())
        if profile is None:
            continue
        rungs = []
        for rung in profile.rungs:
            here = availability.rung_availability(rung, found, profile, seams, plugins_dir)
            rungs.append({"kind": rung.kind, "title": rung.title, "unattended": rung.unattended, **here.to_json()})
        rows.append({"device": found.key, "name": found.name, "port": port, "profile": profile.name, "rungs": rungs})
    return {"machine": machine_facts(), "devices": rows}


def _print_check(report: dict) -> None:
    facts = report["machine"]
    print(f"kernel {facts['kernel']} (port disable switch: {'yes' if facts['port_disable_interface'] else 'no, needs 6.0+'}) · "
          f"plugdev: {'yes' if facts['in_plugdev'] else 'no'} · usbreset: {facts['usbreset'] or 'missing'} · "
          f"fdcanusb-reset: {facts['fdcanusb_reset'] or 'missing'} · uhubctl: {facts['uhubctl'] or 'not installed (not needed)'}")
    print(f"rules: {facts['installed_rules'] or 'not installed'} · hub facts: {facts['hub_facts'] or 'not read yet'} · "
          f"unattended recovery: {facts['unattended']}")
    for row in report["devices"]:
        print(f"\n{row['name']}  [{row['port']}, {row['profile']}]  {row['device']}")
        for rung in row["rungs"]:
            mark = "yes" if rung["usable"] else "no "
            person = "" if rung["unattended"] else " (person only)"
            print(f"  {mark} {rung['title']}{person}: {rung['why']}" + (f"  -> needs {rung['needs']}" if rung["needs"] else ""))


# ----- the udev rules the installer copies ---------------------------------------------------------------------------------
RULES_HEADER = """# RobotDx device reset access (docs/device-reset.md "Permissions"): lets the plugdev group run every USB rung of a reset
# ladder WITHOUT root at runtime, for these device classes only. Generated by `python -m io_diagnostics.recovery udev-rules`
# from io_diagnostics/recovery/profiles/; installed by tools/install_device_reset_access.sh (run it once with sudo).
#
# Per device class, three grants, all scoped to the device (never every USB device: a keyboard's node stays root's):
#   1. its usbfs node /dev/bus/usb/BBB/DDD to plugdev 0664, for USBDEVFS_RESET (usbreset, fdcanusb-reset)
#   2. its sysfs `authorized` switch to plugdev g+w, for the kernel deauthorize / reauthorize rung
#   3. the `disable` switch of the hub port it is plugged into (and that port's USB 3 peer) to plugdev g+w, for the port
#      power cycle; on a hub without per-port power switching it is only a logical disconnect, and the ladder skips it
# WHY RUN chgrp/chmod for 2 and 3: a sysfs attribute is not a device node, so GROUP= / MODE= cannot reach it (the pattern
# uhubctl's README uses for */disable). $$f is a literal $f for the shell; $sys$devpath is udev's.
#
# Undo: sudo rm /etc/udev/rules.d/70-robotdx-device-reset.rules && sudo udevadm control --reload
"""


def udev_rules() -> str:
    lines = [RULES_HEADER]
    for profile in ALL:
        if not profile.udev_match:
            continue
        match = ", ".join(f'ATTR{{{name}}}=="{value}"' for name, value in profile.udev_match)
        node = ', GROUP="plugdev", MODE="0664"' if profile.node_rule else ""
        why = "" if profile.node_rule else f"\n# its node: left alone, {profile.node_rule_why}"
        script = ("chgrp plugdev $sys$devpath/authorized && chmod g+w $sys$devpath/authorized; "
                  "for f in $sys$devpath/port/disable $sys$devpath/port/peer/disable; do "
                  "[ -e $$f ] && chgrp plugdev $$f && chmod g+w $$f; done; true")
        lines.append(f"# {profile.title}{why}\n"
                     f'ACTION=="add|change", SUBSYSTEM=="usb", ENV{{DEVTYPE}}=="usb_device", {match}{node}, '
                     f"RUN+=\"/bin/sh -c '{script}'\"\n")
    return "\n".join(lines)


# ----- the verbs ---------------------------------------------------------------------------------------------------------
def _request(args, mode: str) -> Request:
    return Request(key=args.device or "", mode=mode, owner_pid=getattr(args, "owner_pid", None),
                   detected=getattr(args, "detected", "") or "",
                   tried_by_owner=tuple(word for word in (getattr(args, "tried", "") or "").split(",") if word),
                   probe_command=shlex.split(getattr(args, "probe_command", "") or ""), step=getattr(args, "step", False),
                   page_command=shlex.split(getattr(args, "page_command", "") or ""),
                   plugins_dir=Path(args.plugins) if getattr(args, "plugins", None) else None)


def _finish(result, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"code": result.code, "outcome": result.outcome, "message": result.message, "plan": result.plan,
                          "attempts": result.attempts}, default=str))
    elif result.plan is not None and result.outcome == "planned":
        _print_plan(result.plan)
    else:
        print(f"{result.outcome}: {result.message}")
    return result.code


def _print_plan(plan: dict) -> None:
    print(f"{plan['name']} [{plan['port'] or '?'}] · {plan['profile_title']} · probe: {plan['probe']} · mode: {plan['mode']}")
    for step in plan["steps"]:
        mark = "run " if step["runs"] else "skip"
        print(f"  {mark} {step['title']}" + (f" — {step['skip_why']}" if not step["runs"] and step["skip_why"] else ""))
        print(f"       why: {step['why']}")
        print(f"       expect: {step['expect']}  (timeout {step['timeout_s']:g} s, settle {step['settle_s']:g} s)")
    if plan["others"]:
        print("  held by: " + ", ".join(f"{h['program']} (pid {h['pid']}) {h['node']}" for h in plan["others"]))
    if plan["fix"]:
        print(f"  to allow more rungs: {plan['fix']}")


def act(argv: list[str]) -> int:
    """The host's run of the Reset… button (person mode): stdlib, the plugin boundary, records on IO_DIAGNOSTICS_RECORD_FD."""

    parser = argparse.ArgumentParser(prog="io_diagnostics.recovery act")
    parser.add_argument("--device", default="")
    parser.add_argument("--action", default=ACTION)
    parser.add_argument("--node", type=int)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--confirm-holders", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--plugins")
    args, _rest = parser.parse_known_args(argv)
    out = _records_out()
    if args.action != ACTION:
        _emit(out, {"record": "event", "code": "ActionRefused", "severity": "warn", "message": f"unknown action {args.action!r}",
                    "source": "io_diagnostics.recovery", "args": {"reason": "UnknownAction"}})
        return 2
    try:
        skeleton = json.loads(os.environ.get("IO_DIAGNOSTICS_DEVICE") or "{}") or None
    except json.JSONDecodeError:
        skeleton = None
    key = ""
    try:
        if args.node is not None:
            adapter = devices.from_skeleton(skeleton) if skeleton else devices.resolve(args.device)
            key = f"can:{adapter.serial}/{args.node}"
        elif not skeleton:
            key = args.device
    except devices.DeviceError as error:
        _emit(out, {"record": "event", "code": "ActionRefused", "severity": "warn", "message": str(error),
                    "source": "io_diagnostics.recovery", "args": {"reason": "NoDevice"}})
        print(f"refused: {error}")
        return 2
    request = Request(key=key, skeleton=skeleton if not key or args.node is not None else None, mode=PERSON, dry_run=args.dry_run,
                      confirm=args.confirm, confirm_holders=args.confirm_holders,
                      plugins_dir=Path(args.plugins) if args.plugins else None)
    try:
        result = Ladder(request, say=lambda text: print(text, flush=True)).run()
    except KeyboardInterrupt:
        print("\ninterrupted: the reset was stopped", flush=True)
        _emit(out, {"record": "event", "code": "ActionInterrupted", "severity": "warn", "message": "reset interrupted",
                    "source": "io_diagnostics.recovery", "args": {}})
        return 130
    for record in result.records:
        if record.get("record") == "action-result":
            record = {**record, "stamp": time.time(), "source": "io_diagnostics.recovery"}
        _emit(out, record)
    if not request.dry_run:
        _emit(out, {"record": "action-result", "action": ACTION, "title": TITLE, "dry_run": False, "message": result.message,
                    "outcome": result.outcome, "stamp": time.time(), "source": "io_diagnostics.recovery"})
    return {0: 0, 2: 2}.get(result.code, 1) if result.code != 4 else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    verb = argv[0] if argv else ""
    if verb == "describe":
        print(json.dumps(describe_json()))
        return 0
    if verb == "act":
        return act(argv[1:])
    parser = argparse.ArgumentParser(prog="python -m io_diagnostics.recovery", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="verb", required=True)
    for name in ("check", "profiles", "udev-rules", "ledger"):
        each = sub.add_parser(name)
        each.add_argument("--json", action="store_true")
        each.add_argument("--plugins")
        if name == "ledger":
            each.add_argument("--device", default="")
            each.add_argument("--limit", type=int, default=20)
    for name in ("plan", "recover", "healthy", "hold"):
        each = sub.add_parser(name)
        each.add_argument("--device", required=True)
        each.add_argument("--json", action="store_true")
        each.add_argument("--plugins")
        each.add_argument("--owner-pid", type=int)
        if name == "recover":
            each.add_argument("--detected", default="")
            each.add_argument("--tried", default="")
            each.add_argument("--probe-command", default="")
            each.add_argument("--page-command", default="")
            each.add_argument("--step", action="store_true")
        if name == "hold":
            each.add_argument("--minutes", type=float, required=True)
            each.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    seams = Seams()
    plugins_dir = Path(args.plugins) if getattr(args, "plugins", None) else None
    if args.verb == "check":
        report = check(seams, plugins_dir)
        print(json.dumps(report, indent=1)) if args.json else _print_check(report)
        return 0
    if args.verb == "profiles":
        print(json.dumps([profile.to_json() for profile in ALL], indent=1)) if args.json else [
            print(f"{p.name}: " + " -> ".join(r.kind for r in p.rungs)) for p in ALL]
        return 0
    if args.verb == "udev-rules":
        print(udev_rules(), end="")
        return 0
    if args.verb == "ledger":
        for line in ledger(args.limit, args.device):
            print(json.dumps(line, default=str) if args.json else
                  f"{line.get('utc', 0):.0f} {line.get('device')} {line.get('rung', '-')}: {line.get('outcome')} · {line.get('words', '')}")
        return 0
    if args.verb == "plan":
        request = _request(args, UNATTENDED)
        request.dry_run = True
        return _finish(Ladder(request, seams).run(), args.json)
    if args.verb == "recover":
        return _finish(Ladder(_request(args, UNATTENDED), seams).run(), args.json)
    found = devices.resolve(args.device)
    store = Store(found.state_key)
    profile = profile_for(found.keys())
    if args.verb == "healthy":
        incident = store.healthy(profile.quiet_s if profile else 600.0)
        print("no open incident" if incident is None else
              f"incident {incident.id}: {'closed' if incident.closed else 'quiet window started'}")
        return 0
    if args.verb == "hold":
        store.set_hold(time.time() + args.minutes * 60, os.environ.get("USER", "someone"), args.reason)
        print(f"held {found.name} for {args.minutes:g} min: {args.reason}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
