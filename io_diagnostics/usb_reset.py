"""Reset USB port: a host-side built-in utility, offered on every USB device row and port row whatever driver owns it.

Zach, 2026-10-22 (Daily Note, the converged design): "we can also incoperate like standard OS things in here as well like to
manually reset a usb port etc...". Today RobotDx only *detects* a USB reset (the kernel log's ``UsbReset``).

Run by the host exactly like a driver's ``act``, so it is a RUN (docs/runs-v1.md: a live terminal, RunStarted / RunEnded, the
plug / unplug events that follow in Activity):

    python -m io_diagnostics.usb_reset act --device usb:1-3 --action reset_port [--confirm T] [--confirm-holders] [--dry-run]
    python -m io_diagnostics.usb_reset describe

Stdlib only. WHY these mechanisms, in this order (docs/usb-reset.md has the measurements):

1. ``usbreset`` (usbutils): one ioctl (USBDEVFS_RESET) on ONE device node, by bus/device number; the stock tool, nothing to
   install here, touches nothing else on the hub. Needs write access to ``/dev/bus/usb/BBB/DDD``.
2. ``authorized``: write 0 then 1 to ``/sys/bus/usb/devices/<port>/authorized`` (a logical disconnect and reconnect, the kernel
   re-enumerates and rebinds drivers). Needs root: sysfs attributes cannot be given to a group by a udev ``MODE``.
3. ``uhubctl``: really cuts the port's power, but only on a hub with per-port power switching, and it is not installed by
   default. The last resort.

The first one that this user is allowed to use runs. None allowed: the run is refused and the answer carries the exact udev
rule to install (``fix``), never a silent ``sudo``.

Every path is read from the environment so a test points the whole module at a fake sysfs / dev / proc tree and a fake tool:
IO_DIAGNOSTICS_USB_SYSFS, IO_DIAGNOSTICS_DEV_ROOT, IO_DIAGNOSTICS_PROC, IO_DIAGNOSTICS_USBRESET, IO_DIAGNOSTICS_UHUBCTL.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

NAME = "reset_port"
TITLE = "Reset USB port"  # contract.record.STANDARD_ACTIONS["reset_port"]
SOURCE = "io_diagnostics.usb_reset"

SYSFS_ENV = "IO_DIAGNOSTICS_USB_SYSFS"
DEV_ENV = "IO_DIAGNOSTICS_DEV_ROOT"
PROC_ENV = "IO_DIAGNOSTICS_PROC"
USBRESET_ENV = "IO_DIAGNOSTICS_USBRESET"
UHUBCTL_ENV = "IO_DIAGNOSTICS_UHUBCTL"
WAIT_ENV = "IO_DIAGNOSTICS_RESET_WAIT_S"  # how long to wait for the device to come back
OWN_SESSIONS_ENV = "IO_DIAGNOSTICS_OWN_SESSIONS"  # session ids of the host's own watch / follow processes: not holders
OS_MODE_ENV = "IO_DIAGNOSTICS_OS_MODE"  # live | fixture | machine: a replay never resets anything real

REFUSED = 2  # the verb's exit code when a gate said no and nothing happened to the device
DEFAULT_WAIT_S = 15.0
AUTHORIZED_GAP_S = 0.5  # between the two writes: the kernel finishes the disconnect first
POLL_S = 0.1
UDEV_FILE = "/etc/udev/rules.d/70-robotdx-usb-reset.rules"
UDEV_GROUP = "plugdev"
# sysfs interface subdirectories that name a /dev node the device exposes (tty/ttyACM0, video4linux/video0, ...)
NODE_DIRS = ("tty", "video4linux", "hidraw", "usbmisc", "sound", "block")
PORT = re.compile(r"^\d+-\d+(\.\d+)*$")

# the words a person reads (docs/usb-reset.md): one place, so the dry run, the refusal and the page say the same thing
CONFIRM = ("Reset the USB port that {name} ({port}) is plugged into? It disconnects and reconnects, so whatever has it open "
           "(tview, a camera stream, an experiment) loses it, and it is gone for a few seconds. A hub takes everything "
           "plugged into it down too.")


# ----- where things are ------------------------------------------------------------------------------------------------
def sysfs_root() -> Path:
    return Path(os.environ.get(SYSFS_ENV) or "/sys/bus/usb/devices")


def dev_root() -> Path:
    return Path(os.environ.get(DEV_ENV) or "/dev")


def proc_root() -> Path:
    return Path(os.environ.get(PROC_ENV) or "/proc")


def sysfs_overridden() -> bool:
    return bool(os.environ.get(SYSFS_ENV))


def _tool(env_name: str, program: str) -> str | None:
    named = os.environ.get(env_name)
    if named:
        return named if os.access(named, os.X_OK) else None
    return shutil.which(program)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def _int(text: str) -> int | None:
    try:
        return int(text.strip(), 10)
    except ValueError:
        return None


# ----- the target ------------------------------------------------------------------------------------------------------
@dataclass
class Target:
    port: str  # "1-3", "1-3.2"
    name: str
    vid: str
    pid: str
    busnum: int | None
    devnum: int | None
    present: bool  # sysfs has the port now

    @property
    def node(self) -> Path | None:
        """``/dev/bus/usb/BBB/DDD``: the device node usbreset opens."""

        if self.busnum is None or self.devnum is None:
            return None
        return dev_root() / "bus" / "usb" / f"{self.busnum:03d}" / f"{self.devnum:03d}"

    @property
    def bus_dev(self) -> str:
        return f"{self.busnum:03d}/{self.devnum:03d}" if self.busnum is not None and self.devnum is not None else ""


def port_of(device: dict) -> str:
    """The USB port name a skeleton device sits at (``attrs.port``, else the id ``usb:1-3``)."""

    attrs = device.get("attrs") or {}
    port = str(attrs.get("port") or "")
    if not port:
        found = re.match(r"^usb:(\d+-\d+(?:\.\d+)*)$", str(device.get("id", "")))
        port = found[1] if found else ""
    return port if PORT.match(port) else ""


def target_of(device: dict) -> Target | None:
    """The device as sysfs shows it NOW (its device number changes at every enumeration), else as the skeleton recorded it."""

    port = port_of(device)
    if not port:
        return None
    directory = sysfs_root() / port
    attrs = device.get("attrs") or {}
    present = directory.is_dir()
    vid = _read(directory / "idVendor") or str(attrs.get("vid") or "")
    pid = _read(directory / "idProduct") or str(attrs.get("pid") or "")
    name = str(device.get("label") or device.get("product") or _read(directory / "product") or f"USB device {vid}:{pid}")
    busnum = _int(_read(directory / "busnum")) if present else None
    devnum = _int(_read(directory / "devnum")) if present else None
    if busnum is None:
        busnum = _int(str(attrs.get("busnum") or "")) or _int(port.split("-", 1)[0])
    if devnum is None and not present:
        devnum = _int(str(attrs.get("devnum") or ""))
    return Target(port, name, vid.lower(), pid.lower(), busnum, devnum, present)


def subtree_ports(port: str) -> list[str]:
    """The port itself and every device below it in sysfs (``1-3``, ``1-3.1``, ``1-3.2.4``): a hub takes them down with it."""

    root = sysfs_root()
    try:
        names = sorted(entry.name for entry in root.iterdir())
    except OSError:
        return [port]
    below = [name for name in names if PORT.match(name) and name.startswith(port + ".")]
    return [port, *below]


# ----- who has it open -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Holder:
    pid: int
    program: str
    node: str  # the device node it has open

    def text(self) -> str:
        return f"{self.program} (pid {self.pid}) has {self.node}"


def device_nodes(port: str) -> list[str]:
    """Every /dev node the port's device (and what hangs below it) exposes: its usbfs node, and the tty / video / hid nodes
    of its interfaces. From sysfs, so a fake tree is enough to test it."""

    root, nodes = sysfs_root(), []
    for each in subtree_ports(port):
        directory = root / each
        busnum, devnum = _int(_read(directory / "busnum")), _int(_read(directory / "devnum"))
        if busnum is not None and devnum is not None:
            nodes.append(str(dev_root() / "bus" / "usb" / f"{busnum:03d}" / f"{devnum:03d}"))
        try:
            interfaces = sorted(entry for entry in directory.iterdir() if entry.is_dir() and entry.name.startswith(each + ":"))
        except OSError:
            continue
        for interface in interfaces:
            for kind in NODE_DIRS:
                try:
                    found = sorted(entry.name for entry in (interface / kind).iterdir())
                except OSError:
                    continue
                for name in found:
                    if kind == "sound" and not name.startswith("card"):
                        continue
                    nodes.append(str(dev_root() / (name if kind != "sound" else f"snd/controlC{name.removeprefix('card')}")))
    return list(dict.fromkeys(nodes))


def _session_of(pid: int) -> int | None:
    """The session id of a process (field 6 of /proc/<pid>/stat, after the ``)`` that ends its name)."""

    stat = _read(proc_root() / str(pid) / "stat")
    after = stat.rpartition(")")[2].split()
    return _int(after[3]) if len(after) > 3 else None


def _program(pid: int) -> str:
    words = _read(proc_root() / str(pid) / "cmdline").split("\0")
    words = [word for word in words if word]
    comm = _read(proc_root() / str(pid) / "comm") or str(pid)
    # an interpreter is not what a person knows it by: python3 /home/x/tview -> tview
    if words and re.match(r"^(python|node|bash|sh|uv)[\d.]*$", Path(words[0]).name):
        script = next((word for word in words[1:] if not word.startswith("-")), "")
        return Path(script).name or comm
    return Path(words[0]).name if words else comm


def own_sessions() -> frozenset[int]:
    return frozenset(session for session in (_int(part) for part in os.environ.get(OWN_SESSIONS_ENV, "").split(",")) if session)


def holders(port: str, ignore_sessions: frozenset[int] = frozenset(), scan: dict | None = None) -> tuple[list[Holder], int]:
    """``(holders, processes that could not be looked at)``: every other process with one of the port's nodes open.

    WHY /proc and not ``lsof``: it is the same data without a subprocess, and a fake proc tree tests it. A process of another
    user cannot be looked into (EACCES); the count is returned so the answer can say it did not see everything.

    ``scan``: a pass over /proc already made (``io_facts.open_files``), which the page's gates share; without one, a walk of its own,
    as the run itself does just before it resets."""

    nodes = set(device_nodes(port))
    if not nodes:
        return [], 0
    me, found, unseen = os.getpid(), [], 0
    if scan is not None:
        held = {}
        for node in sorted(nodes):
            for pid, _comm in scan["dev"].get(node, []):
                held.setdefault(pid, node)
        for pid, node in sorted(held.items()):
            if pid != me and not (ignore_sessions and _session_of(pid) in ignore_sessions):
                found.append(Holder(pid, _program(pid), node))
        return found, int(scan.get("unseen") or 0)
    try:
        pids = sorted(int(entry.name) for entry in proc_root().iterdir() if entry.name.isdigit())
    except OSError:
        return [], 0
    for pid in pids:
        if pid == me:
            continue
        try:
            targets = [os.readlink(link) for link in (proc_root() / str(pid) / "fd").iterdir()]
        except PermissionError:
            unseen += 1
            continue
        except OSError:
            continue
        matched = sorted(node for node in {target for target in targets} if node in nodes)
        if not matched:
            continue
        if ignore_sessions and _session_of(pid) in ignore_sessions:
            continue
        found.append(Holder(pid, _program(pid), matched[0]))
    return found, unseen


# ----- how to reset it ---------------------------------------------------------------------------------------------------
@dataclass
class Method:
    name: str  # usbreset | authorized | uhubctl
    usable: bool
    why_not: str  # plain words, "" when usable
    command: list[str] | str  # what runs, exact
    kind: str  # argv | sysfs
    summary: str
    short: str = ""  # the reason in as few words as it can be said (the precondition row), "" = why_not as it is

    def to_json(self) -> dict:
        return {"name": self.name, "usable": self.usable, "why_not": self.why_not, "command": self.command, "kind": self.kind}


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def methods(target: Target) -> list[Method]:
    """The three mechanisms in order of preference, each with whether this user may use it now."""

    out = []
    tool = _tool(USBRESET_ENV, "usbreset")
    node = target.node
    if tool is None:
        out.append(Method("usbreset", False, "usbreset is not installed (sudo apt install usbutils)", ["usbreset", target.bus_dev or "BBB/DDD"],
                          "argv", "reset this one device with an ioctl"))
    elif node is None or not target.present:
        out.append(Method("usbreset", False, "the device has no node right now", [tool, "BBB/DDD"], "argv", "reset this one device with an ioctl"))
    elif not _writable(node):
        out.append(Method("usbreset", False, f"you cannot write to {node}", [tool, target.bus_dev], "argv", "reset this one device with an ioctl",
                          f"cannot write {node}"))
    else:
        out.append(Method("usbreset", True, "", [tool, target.bus_dev], "argv", "reset this one device with an ioctl"))

    authorized = sysfs_root() / target.port / "authorized"
    command = f"echo 0 > {authorized} && echo 1 > {authorized}"
    if not authorized.exists():
        out.append(Method("authorized", False, f"{authorized} does not exist", command, "sysfs", "disconnect and reconnect it in the kernel"))
    elif not _writable(authorized):
        out.append(Method("authorized", False, f"you cannot write to {authorized} (root only)", command, "sysfs",
                          "disconnect and reconnect it in the kernel", "root only"))
    else:
        out.append(Method("authorized", True, "", command, "sysfs", "disconnect and reconnect it in the kernel"))

    hub, number = hub_and_port(target.port)
    uhubctl = _tool(UHUBCTL_ENV, "uhubctl")
    cycle = [uhubctl or "uhubctl", "-l", hub, "-p", number, "-a", "cycle"]
    if uhubctl is None:
        out.append(Method("uhubctl", False, "uhubctl is not installed", cycle, "argv", "cut the port's power and restore it", "not installed"))
    elif os.geteuid() != 0 and not _hub_node_writable(hub, target):
        out.append(Method("uhubctl", False, f"you cannot write to hub {hub}'s node", cycle, "argv", "cut the port's power and restore it"))
    else:
        out.append(Method("uhubctl", True, "", cycle, "argv", "cut the port's power and restore it"))
    return out


def hub_and_port(port: str) -> tuple[str, str]:
    """uhubctl's location and port number: ``1-3`` is port 3 of root hub ``1``, ``1-3.2`` port 2 of hub ``1-3``."""

    if "." in port:
        hub, _, number = port.rpartition(".")
        return hub, number
    bus, _, number = port.partition("-")
    return bus, number


def _hub_node_writable(hub: str, target: Target) -> bool:
    directory = sysfs_root() / hub
    busnum, devnum = _int(_read(directory / "busnum")), _int(_read(directory / "devnum"))
    if busnum is None or devnum is None:
        return False
    return _writable(dev_root() / "bus" / "usb" / f"{busnum:03d}" / f"{devnum:03d}")


def chosen(options: list[Method]) -> Method | None:
    return next((method for method in options if method.usable), None)


def udev_rule(target: Target) -> str:
    """The one rule that lets this user's group open this device's node, so ``usbreset`` works without root. Narrow on
    purpose: one vendor:product, never every USB device on the machine."""

    return (f'SUBSYSTEM=="usb", ENV{{DEVTYPE}}=="usb_device", ATTR{{idVendor}}=="{target.vid}", ATTR{{idProduct}}=="{target.pid}", '
            f'GROUP="{UDEV_GROUP}", MODE="0664"')


def fix_text(target: Target) -> str:
    """What to do when no mechanism is allowed, in plain words (shown on the page and printed in the run)."""

    return (f"RobotDx may not reset {target.name} yet: your user cannot write to its USB node. Fix it once, as root: "
            f"save this line in {UDEV_FILE}, then run "
            f"sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=usb --action=change\n"
            f"{udev_rule(target)}")


def privilege(target: Target) -> tuple[bool, str]:
    """``(may reset, one line)``: the host's precondition ``reset_privilege``."""

    if not target.present:
        return False, f"nothing is plugged in at {target.port} now"
    options = methods(target)
    use = chosen(options)
    if use is not None:
        return True, f"{use.name}: {use.summary}"
    return False, "no way to reset it as your user: " + "; ".join(f"{m.name}: {m.short or m.why_not}" for m in options)


def token(target: Target, found: list[Holder]) -> str:
    """The confirm token: bound to this device AND the holders the person saw, so a holder that appeared since the dry run
    makes the old token useless (the second confirmation is for those holders, not for whatever came later)."""

    seed = "|".join([NAME, target.port, f"{target.vid}:{target.pid}", target.bus_dev,
                     ",".join(str(holder.pid) for holder in sorted(found, key=lambda h: h.pid))])
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


# ----- records ---------------------------------------------------------------------------------------------------------------
def _records():
    """Where records go: the run's record fd when the host gave one (docs/runs-v1.md section 3), else stdout."""

    fd = os.environ.get("IO_DIAGNOSTICS_RECORD_FD")
    return os.fdopen(int(fd), "w") if fd else sys.stdout


def _emit(out, record: dict) -> None:
    out.write(json.dumps(record, ensure_ascii=False) + "\n")
    out.flush()


def _event(code: str, severity: str, message: str, **args) -> dict:
    return {"record": "event", "code": code, "severity": severity, "message": message, "source": SOURCE, "args": args}


def _say(text: str) -> None:
    print(text, flush=True)


def describe_json() -> dict:
    return {"name": "io_diagnostics_usb_reset", "schema": "diagnostics-record/0", "host_builtin": True,
            "actions": [{"name": NAME, "title": TITLE, "danger": "interrupts", "scope": "device"}]}


def plan_json(target: Target, found: list[Holder], unseen: int, options: list[Method], dry_run: bool) -> dict:
    """The dry run's ``action-result``: the exact command, each precondition as it stands, the holders, the fix."""

    use = chosen(options)
    allowed = use is not None
    rule = None if allowed else {"file": UDEV_FILE, "rule": udev_rule(target),
                                 "then": "sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=usb --action=change"}
    preconditions = [
        {"check": "device_present", "text": f"A device is plugged in at {target.port}", "state": "pass" if target.present else "fail",
         "detail": f"{target.name} · {target.vid}:{target.pid} · device {target.bus_dev}" if target.present else "nothing there now"},
        {"check": "reset_privilege", "text": "RobotDx may reset it as this user", "state": "pass" if allowed else "fail",
         "detail": f"{use.name}: {use.summary}" if use else "; ".join(f"{m.name}: {m.short or m.why_not}" for m in options)},
        {"check": "device_free", "text": "Nothing else has it open", "state": "fail" if found else "pass",
         "detail": ("; ".join(holder.text() for holder in found) + " · you confirm again before it runs") if found
                   else "no process has it open" + (f" (could not look at {unseen} other users' processes)" if unseen else "")}]
    second = None
    if found:
        names = ", ".join(f"{holder.program} (pid {holder.pid})" for holder in found)
        second = f"{names} will lose {target.name}"
    return {"record": "action-result", "action": NAME, "title": TITLE, "dry_run": dry_run, "danger": "interrupts",
            "command_kind": use.kind if use else "none",
            "command": use.command if use else "(none allowed yet: see 'To allow it')",
            "method": use.name if use else None, "methods": [method.to_json() for method in options],
            "steps": ([{"purpose": use.summary, "runs": "subprocess" if use.kind == "argv" else "sysfs",
                        "argv": use.command if isinstance(use.command, list) else [use.command]},
                       {"purpose": f"wait up to {wait_s():g} s for it to come back", "runs": "in-process"}] if use else []),
            "preconditions": preconditions, "holders": [{"pid": h.pid, "program": h.program, "node": h.node} for h in found],
            "second_confirm": second, "fix": fix_text(target) if not allowed else None, "udev": rule,
            "confirm": CONFIRM.format(name=target.name, port=target.port), "token": token(target, found),
            "stamp": time.time(), "source": SOURCE,
            "message": (f"would reset {target.name} at {target.port} with {use.name}" if use
                        else f"cannot reset {target.name}: no mechanism is allowed")}


def wait_s() -> float:
    try:
        return float(os.environ.get(WAIT_ENV) or DEFAULT_WAIT_S)
    except ValueError:
        return DEFAULT_WAIT_S


# ----- the reset itself ---------------------------------------------------------------------------------------------------------
def _write_attr(path: Path, value: str) -> None:
    path.write_text(value)


def _run_tool(command: list[str]) -> int:
    """The vendor tool inherits the terminal (stdin / stdout / stderr as this process got them): its output is live."""

    _say("$ " + " ".join(command))
    return subprocess.run(command, check=False).returncode


def _cycle_authorized(target: Target) -> None:
    authorized = sysfs_root() / target.port / "authorized"
    _say(f"$ echo 0 > {authorized}")
    _write_attr(authorized, "0")
    try:
        time.sleep(AUTHORIZED_GAP_S)
    finally:
        # WHY in a finally: a Stop between the two writes must never leave the device disconnected
        _say(f"$ echo 1 > {authorized}")
        _write_attr(authorized, "1")


def _wait_back(target: Target, before: int | None, started: float) -> tuple[bool, int | None, float]:
    """``(back, device number now, seconds waited)``: back = the port is in sysfs again with a device number."""

    deadline = time.monotonic() + wait_s()
    while True:
        now = _int(_read(sysfs_root() / target.port / "devnum")) if (sysfs_root() / target.port).is_dir() else None
        if now is not None and (time.monotonic() - started >= 0.2 or now != before):
            return True, now, time.monotonic() - started
        if time.monotonic() >= deadline:
            return False, None, time.monotonic() - started
        time.sleep(POLL_S)


def reset(target: Target, use: Method) -> tuple[bool, int | None, float, str]:
    """Run the chosen mechanism and wait for the device. ``(ok, device number after, seconds, message)``."""

    before, started = target.devnum, time.monotonic()
    if use.kind == "sysfs":
        _cycle_authorized(target)
    else:
        code = _run_tool(list(use.command))  # type: ignore[arg-type]
        if code != 0:
            return False, None, time.monotonic() - started, f"{use.name} exited {code}"
    back, after, waited = _wait_back(target, before, started)
    if not back:
        return False, None, waited, f"{target.name} did not come back within {wait_s():g} s"
    return True, after, waited, ""


# ----- the verb --------------------------------------------------------------------------------------------------------------
def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv[:-1] else None


def _device_from_env(device_id: str) -> dict:
    try:
        device = json.loads(os.environ.get("IO_DIAGNOSTICS_DEVICE") or "{}")
    except json.JSONDecodeError:
        device = {}
    return device if isinstance(device, dict) and device else {"id": device_id, "attrs": {}}


def act(argv: list[str]) -> int:
    """Exit 0 reset (or a dry run) · 2 refused, nothing happened · 1 the reset ran and failed."""

    device_id = _arg(argv, "--device") or ""
    out = _records()
    dry = "--dry-run" in argv
    if (_arg(argv, "--action") or NAME) != NAME:
        _emit(out, _event("ActionRefused", "warn", f"unknown action {_arg(argv, '--action')!r}", reason="UnknownAction"))
        return REFUSED
    target = target_of(_device_from_env(device_id))
    if target is None:
        _emit(out, _event("ActionRefused", "warn", f"{device_id!r} is not a USB device with a port", reason="NotUsb"))
        _say(f"refused: {device_id!r} is not a USB device with a port")
        return REFUSED
    replay = os.environ.get(OS_MODE_ENV, "live") != "live" and not sysfs_overridden()
    if replay and not dry:
        _emit(out, _event("ActionRefused", "warn", "this host replays a recording: nothing here can be reset", reason="NotLive"))
        _say("refused: this host replays a recording: nothing here can be reset")
        return REFUSED
    found, unseen = holders(target.port, own_sessions()) if not replay else ([], 0)
    options = methods(target) if not replay else [Method("usbreset", False, "a recorded replay has no device to open", ["usbreset", "BBB/DDD"],
                                                            "argv", "reset this one device with an ioctl")]
    use = chosen(options)
    plan = plan_json(target, found, unseen, options, dry)
    if dry:
        _emit(out, plan)
        return 0

    # the gates of a real run, in this order; each refusal says why and what to do, and nothing has happened yet
    if not target.present:
        return _refuse(out, "NoDevice", f"nothing is plugged in at {target.port} now", target)
    if _arg(argv, "--confirm") != plan["token"]:
        return _refuse(out, "ConfirmMismatch", "the confirmation is not for this device as it is now (a holder came or went since "
                       "the dry run): ask for the dry run again", target)
    if use is None:
        _say(fix_text(target))
        return _refuse(out, "NoPrivilege", plan["message"], target, fix=fix_text(target), udev=plan["udev"])
    if found and "--confirm-holders" not in argv:
        _say("These have it open and will lose it:")
        for holder in found:
            _say(f"  {holder.text()}")
        return _refuse(out, "HoldersPresent", plan["second_confirm"] + ": confirm again to reset it anyway", target,
                       holders=plan["holders"])

    _say(f"Resetting {target.name} at {target.port} ({target.bus_dev}) with {use.name}")
    for holder in found:
        _say(f"  {holder.text()}: it loses the device")
    try:
        ok, after, waited, message = reset(target, use)
    except OSError as error:
        _emit(out, _event("ActionFailed", "error", f"{use.name} could not run: {error}", method=use.name, port=target.port))
        _say(f"failed: {error}")
        return 1
    except KeyboardInterrupt:  # a Stop from the page: a record and exit 130, not a traceback (the authorized switch is already back on)
        _say("\ninterrupted: the reset was stopped")
        _emit(out, _event("ActionInterrupted", "warn", f"the reset of {target.name} at {target.port} was interrupted", method=use.name,
                          port=target.port))
        return 130
    if not ok:
        _emit(out, _event("ActionFailed", "error", message, method=use.name, port=target.port))
        _say(f"failed: {message}")
        return 1
    how = (f"back after {waited:.1f} s as device {after}" if after != target.devnum
           else f"reset in place after {waited:.1f} s (device number {after} unchanged)")
    _say(f"{target.name}: {how}")
    said = f"{target.name} at {target.port} reset with {use.name}: {how}"
    _emit(out, _event("ActionCompleted", "info", said, method=use.name, port=target.port, devnum_before=target.devnum,
                      devnum_after=after, waited_s=round(waited, 2), holders=plan["holders"]))
    # the run's result line is this record's message (terminal.Run._after_exit), not "ran in 0.3 s · 2 lines"
    _emit(out, {"record": "action-result", "action": NAME, "title": TITLE, "dry_run": False, "danger": "interrupts",
                "command_kind": use.kind, "command": use.command, "method": use.name, "message": said, "stamp": time.time(),
                "source": SOURCE})
    return 0


def _refuse(out, reason: str, message: str, target: Target, **args) -> int:
    _say(f"refused ({reason}): {message}")
    _emit(out, _event("ActionRefused", "warn", message, reason=reason, port=target.port, **args))
    return REFUSED


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    verb = next((word for word in argv if word in ("describe", "act")), "")
    if verb == "describe":
        print(json.dumps(describe_json()))
        return 0
    if verb == "act":
        return act(argv)
    print("usage: python -m io_diagnostics.usb_reset describe | act --device usb:<port> --action reset_port [--dry-run]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
