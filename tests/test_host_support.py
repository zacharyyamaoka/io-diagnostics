"""Shared helpers for tests/test_host_*.py: a fake OS source, a settable clock, lane-B-shaped skeleton dicts and
plugin declarations that run tests/fixtures/plugins/cases/case_plugin.py as a REAL subprocess."""

from __future__ import annotations

import atexit
import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from io_diagnostics.host import Clock, Host  # noqa: E402

CASES = ROOT / "tests" / "fixtures" / "plugins" / "cases" / "case_plugin.py"


# ----- the hardware tripwire -------------------------------------------------------------------------------------
# WHY (plugins.d ships the real drivers ENABLED since 2026-10-22): a test that forgets its plugin directory would run
# the real moteus_servo / intel_rgbd diagnose against the rack. Every subprocess this test process starts goes through
# here; it refuses (EPERM, before anything runs) a real driver module unless passive_guard.py stands in front of it
# (which allows only describe / diagnose --passive), and an io-diagnostics once/serve/tray/act that names neither
# --plugins nor --fixture (it would load plugins.d). pytest imports this module at collection, so it covers the suite.
REAL_DRIVER_MODULES = ("moteus_servo.diagnostics", "intel_rgbd.diagnostics")
HOST_VERBS = ("once", "serve", "tray", "act", "expect", "forget", "retire")
TRIPPED: list[list[str]] = []


def tripwire_refuses(argv, env=None) -> str:
    """Why this argv must not run in a test, or "" (allowed)."""

    words = [os.fspath(word) for word in ([argv] if isinstance(argv, (str, bytes, os.PathLike)) else list(argv))]
    words = [word.decode() if isinstance(word, bytes) else word for word in words]
    guarded = len(words) > 1 and words[1].endswith("passive_guard.py")
    if any(word in REAL_DRIVER_MODULES for word in words) and not guarded:
        return "a real driver module without passive_guard.py in front of it"
    # the host's own reset_port utility (docs/usb-reset.md): the real usbreset / uhubctl only ever as a fake under the temp dir,
    # and a real (not dry) reset only with a fake sysfs tree named in the environment, so no test can reset a real port
    if words and Path(words[0]).name in ("usbreset", "uhubctl") and not (
            Path(words[0]).is_absolute() and Path(words[0]).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())):
        return "a real USB reset tool (only a fake under the temp dir may run)"
    if "io_diagnostics.usb_reset" in words and "act" in words and "--dry-run" not in words \
            and not (env if env is not None else os.environ).get("IO_DIAGNOSTICS_USB_SYSFS"):
        return "a real USB port reset with no fake sysfs tree in IO_DIAGNOSTICS_USB_SYSFS"
    # the reset ladder (docs/device-reset.md): Sim to Real's fdcanusb-reset only as a fake under the temp dir, and a recover / act
    # that could reset (not a dry run, not a plan) only with a fake sysfs tree named, so no test can reset a real device
    if words and Path(words[0]).name == "fdcanusb-reset" and not (
            Path(words[0]).is_absolute() and Path(words[0]).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())):
        return "the real fdcanusb-reset (only a fake under the temp dir may run)"
    if "io_diagnostics.recovery" in words and {"recover", "act"} & set(words) and "--dry-run" not in words \
            and not (env if env is not None else os.environ).get("IO_DIAGNOSTICS_USB_SYSFS"):
        return "a real reset ladder with no fake sysfs tree in IO_DIAGNOSTICS_USB_SYSFS"
    host = next((index for index, word in enumerate(words) if word.endswith("io_diagnostics/cli.py") or word == "io-diagnostics"), None)
    if host is not None and any(word in HOST_VERBS for word in words[host + 1:host + 2]) \
            and not {"--plugins", "--fixture"} & set(words):
        return "an io-diagnostics host with neither --plugins nor --fixture (it would load plugins.d)"
    return ""


class _TripwirePopen(subprocess.Popen):
    def __init__(self, args, *rest, **options):
        why = tripwire_refuses(args, options.get("env"))
        if why:
            TRIPPED.append([str(word) for word in ([args] if isinstance(args, str) else args)])
            print(f"hardware tripwire: refused {why}: {args!r}", file=sys.stderr)
            raise PermissionError(errno.EPERM, f"hardware tripwire (tests/test_host_support.py): {why}")
        super().__init__(args, *rest, **options)


if not issubclass(subprocess.Popen, _TripwirePopen):
    subprocess.Popen = _TripwirePopen  # subprocess.run and the host's runner both look Popen up on the module

# WHY: the reset ladder's own writes and device opens (io_diagnostics/recovery/system.py) refuse anything outside the temp dir
# while this is set, so an in-process test that forgets its fake tree fails instead of writing to the real /sys. Inherited.
os.environ["IO_DIAGNOSTICS_TRIPWIRE"] = "1"

# WHY: a live-mode host remembers the USB devices it saw in ~/.local/state/io-diagnostics/seen.json; a test's fake
# devices must never land in Zach's real file (they would show as "expected" forever). Inherited by subprocesses.
_STATE_DIR = tempfile.mkdtemp(prefix="io-diagnostics-test-state-")
atexit.register(shutil.rmtree, _STATE_DIR, ignore_errors=True)
os.environ["IO_DIAGNOSTICS_STATE_DIR"] = _STATE_DIR
# WHY: likewise a live-mode host reads the person's rig manifest (~/.config/io-diagnostics/expected.toml); a test must
# neither see Zach's rig nor ever write to it
_CONFIG_DIR = tempfile.mkdtemp(prefix="io-diagnostics-test-config-")
atexit.register(shutil.rmtree, _CONFIG_DIR, ignore_errors=True)
os.environ["IO_DIAGNOSTICS_CONFIG_DIR"] = _CONFIG_DIR


class SetClock(Clock):
    """A host clock the test moves by hand."""

    def __init__(self, at: float = 1000.0):
        self.at = at

    def now(self) -> float:
        return self.at


class FakeOs:
    """An OsSource over hand-written skeleton dicts; no kernel log, recorded gate answers."""

    mode = "live"

    def __init__(self, skeleton: list[dict], gates: dict | None = None, hostname: str = "rig"):
        self.devices, self.gates, self.hostname = skeleton, gates or {}, hostname

    def invalidate(self) -> None:
        pass

    def skeleton(self):
        return [dict(device) for device in self.devices], [{"name": "fake", "state": "used", "note": f"{len(self.devices)}"}]

    def boots(self):
        return []

    def gate(self, device, check):
        return tuple(self.gates.get(check, (None, f"{check}: not recorded")))


def host_dict() -> dict:
    return {"bus": "host", "id": "host", "path": "host", "label": "rig", "vendor": None, "product": None, "serial": None,
            "parent": None, "attrs": {"kind": "host", "os": "Ubuntu 24.04", "kernel": "6.8.0"}}


def usb_bus(number: int) -> dict:
    return {"bus": "usb", "id": f"usb:usb{number}", "path": f"usb{number}", "label": f"USB bus {number}", "vendor": None,
            "product": None, "serial": None, "parent": "host", "attrs": {"kind": "bus"}}


def usb_device(port: str, vid: str, pid: str, serial: str = "", *, kind: str = "device", label: str = "thing",
               parent: str | None = None) -> dict:
    bus = port.split("-", 1)[0]
    return {"bus": "usb", "id": f"usb:{port}", "path": f"usb-{port}", "label": label, "vendor": "maker", "product": label,
            "serial": serial or None, "parent": parent or (f"usb:{port.rsplit('.', 1)[0]}" if "." in port else f"usb:usb{bus}"),
            "attrs": {"kind": kind, "port": port, "vid": vid, "pid": pid, "usb_id": f"{vid}:{pid}", "speed_mbps": 12,
                      "hardware_id": f"usb:{vid}:{pid}:{serial}" if serial else f"usb:{vid}:{pid}@{port}"}}


def tty(parent: str, name: str, by_id: str) -> dict:
    return {"bus": "tty", "id": f"tty:{parent}", "path": name, "label": f"/dev/{name}", "vendor": None, "product": None,
            "serial": None, "parent": parent, "attrs": {"kind": "serial", "by_id": [by_id]}}


def write_plugin(directory: Path, name: str, case: str, matches: list[str], *, timeout_s: float = 10, period_s: float = 2,
                 replay: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text(f'name = "{name}"\ncommand = ["{{python}}", "{CASES}", "--case", "{case}"]\n'
                    f"timeout_s = {timeout_s}\nperiod_s = {period_s}\nreplay = {'true' if replay else 'false'}\n"
                    f"matches = {matches!r}\n".replace("'", '"'))
    return path


# A stdlib plugin for the gate and action tests: logs every run (verb, device, argv, start and end on the wall clock,
# shared by every process) as one JSON line to $FAKE_LOG, sleeps $FAKE_SLEEP in diagnose, and registers three
# actions on its device row (query / interrupts / moves). Never touches hardware.
FAKE_PLUGIN = r'''
import json, os, sys, time

NAME = os.environ["FAKE_NAME"]
ACTIONS = [
    {"name": "check_comms", "title": "Check communication", "description": "ask who answers", "danger": "query",
     "scope": "device", "confirm": "", "kind": "touches"},
    {"name": "reboot", "title": "Reboot…", "description": "restart it", "danger": "interrupts", "scope": "node",
     "confirm": "Reboot {name} ({serial})?", "kind": "changes"},
    {"name": "calibrate", "title": "Run calibration…", "description": "spins the rotor", "danger": "moves",
     "scope": "node", "confirm": "Calibrate {name}? The rotor spins.", "kind": "changes"},
]
args = sys.argv[1:]
verb = next(word for word in args if word in ("describe", "diagnose", "act"))
device = args[args.index("--device") + 1] if "--device" in args else ""
start = time.time()
if verb == "diagnose":
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0.4")))
elif verb == "act":
    time.sleep(float(os.environ.get("FAKE_ACT_SLEEP", "0")))
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"plugin": NAME, "verb": verb, "device": device, "argv": args, "start": start, "end": time.time()}) + "\n")
if verb == "describe":
    print(json.dumps({"name": NAME, "schema": "diagnostics-record/0", "record_version": "0.1", "actions": ACTIONS,
                      "expected": json.loads(os.environ.get("FAKE_EXPECTED", "[]"))}))
elif verb == "diagnose":
    print(json.dumps({"schema": "diagnostics-record/0", "record": "entity", "path": "", "name": NAME + " device",
                      "kind": "device", "identity": {"serial_number": "SER-" + NAME, "model_id": "acme-widget-1"},
                      "actions": ACTIONS}))
    print(json.dumps({"schema": "diagnostics-record/0", "record": "status", "level": "ok", "name": NAME + ": ran",
                      "path": ""}))
else:
    print(json.dumps({"record": "action-result", "action": args[args.index("--action") + 1], "dry_run": "--dry-run" in args,
                      "command": ["would", "run", "this"]}))
'''


def write_fake_plugin(directory: Path, name: str, matches: list[str], log: Path, *, exclusive: str = "", sleep_s: float = 0.4,
                      period_s: float = 30, timeout_s: float = 20, act_sleep_s: float = 0.0,
                      expected: list[dict] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "fake_plugin.py"
    script.write_text(FAKE_PLUGIN)
    path = directory / f"{name}.toml"
    path.write_text(f'name = "{name}"\ncommand = ["{{python}}", "{script}"]\ntimeout_s = {timeout_s}\nperiod_s = {period_s}\n'
                    f'matches = {matches!r}\n'.replace("'", '"')
                    + (f'exclusive = "{exclusive}"\n' if exclusive else "")
                    + f'env = {{ FAKE_NAME = "{name}", FAKE_LOG = "{log}", FAKE_SLEEP = "{sleep_s}", FAKE_ACT_SLEEP = "{act_sleep_s}",'
                      f" FAKE_EXPECTED = '{json.dumps(expected or [])}' }}\n")  # a TOML literal string: JSON keeps its quotes
    return path


def read_log(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def make_host(tmp_path: Path, skeleton: list[dict], plugins: list[tuple], *, at: float = 1000.0, gates=None) -> Host:
    """``plugins``: (name, case, matches[, options]) tuples, written as declarations under tmp_path."""

    directory = tmp_path / "plugins.d"
    directory.mkdir(parents=True, exist_ok=True)
    for name, case, matches, *options in plugins:
        write_plugin(directory, name, case, matches, **(options[0] if options else {}))
    return Host(plugins_dir=directory, clock=SetClock(at), os_source=FakeOs(skeleton, gates))


def standard_skeleton(*devices: dict) -> list[dict]:
    return [host_dict(), usb_bus(9), *devices]


def node_by_name(tree: dict, name: str) -> dict:
    stack = [tree]
    while stack:
        node = stack.pop()
        if node["name"] == name:
            return node
        stack.extend(node["children"])
    raise KeyError(name)


def test_support_builds_a_lane_b_shaped_device():
    device = usb_device("9-1", "1234", "abcd", "SER")
    assert set(device) == {"bus", "id", "path", "label", "vendor", "product", "serial", "parent", "attrs"}
    assert device["parent"] == "usb:usb9" and device["attrs"]["hardware_id"] == "usb:1234:abcd:SER"
