"""The plugin registry: ``plugins.d/*.toml`` -> ``PluginSpec``, and which OS skeleton device each plugin diagnoses.

A plugin is an EXECUTABLE that speaks JSON lines (docs/build-plan-v1.md, "Plugin boundary"; format and examples:
plugins.d/README.md). WHY a declaration file instead of entry points: the driver's dependencies (moteus,
pyrealsense2) stay in the driver's own uv environment, so the host only needs an argv, a working directory and the
devices to hand it. The host never imports a driver.

    name = "moteus_servo"
    command = ["uv", "run", "--project", "/home/bam/bam_ws/...", "python", "-m", "moteus_servo.diagnostics"]
    cwd = "/home/bam/bam_ws"           # optional; default: this file's directory
    timeout_s = 20                     # one run's wall-clock limit
    period_s = 30                      # how often to run it per matched device
    matches = ["usb:0483:5740", "by-id:*fdcanusb*"]
    exclusive = "rack-bus"             # optional: never two runs of this group at once (queued, not failed)

A skeleton device is the frozen lane-B dict ``{"bus", "id", "path", "label", "vendor", "product", "serial",
"parent", "attrs"}``; matching reads only those keys, defensively, so any OS source can feed it.
"""

from __future__ import annotations

import fnmatch
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from io_diagnostics import ROOT

DEFAULT_TIMEOUT_S = 20.0
DEFAULT_PERIOD_S = 30.0
# WHY far longer than a diagnose: an act may flash firmware (rs-fw-update is given 300 s) or calibrate (~30-120 s);
# a diagnose timeout would kill it half-way. Used only by act, never by diagnose.
DEFAULT_ACT_TIMEOUT_S = 900.0
MATCH_KINDS = ("usb", "pci", "pci-class", "by-id", "tty", "v4l", "netdev")
KNOWN_KEYS = {"name", "command", "cwd", "timeout_s", "period_s", "timeout", "period", "matches", "enabled", "replay", "env",
              "description", "diagnose_args", "exclusive", "act_timeout_s", "act_timeout"}
# WHY aliases: docs/build-plan-v1.md names the keys "timeout, period"; the lane spec and this README say timeout_s /
# period_s. Both mean seconds; the _s spelling wins when a file has both.
KEY_ALIASES = {"timeout_s": "timeout", "period_s": "period", "act_timeout_s": "act_timeout"}
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class PluginConfigError(ValueError):
    """A ``plugins.d`` file the host refuses to load (the reason is shown in /api/v1/plugins)."""


def _hex(text: Any) -> str:
    return str(text or "").strip().lower().removeprefix("0x")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def usb_ids(device: dict) -> tuple[str, str]:
    """``(vid, pid)`` of a USB skeleton device, read from whichever attribute names its source used."""

    attrs = device.get("attrs") or {}
    for vid_key, pid_key in (("vid", "pid"), ("idVendor", "idProduct"), ("vendor_id", "product_id")):
        if attrs.get(vid_key):
            return _hex(attrs.get(vid_key)), _hex(attrs.get(pid_key))
    usb_id = attrs.get("usb_id") or ""
    if ":" in usb_id:
        vid, _, pid = usb_id.partition(":")
        return _hex(vid), _hex(pid)
    found = re.match(r"^usb:([0-9a-fA-F]{4}):([0-9a-fA-F]{4})", str(device.get("id", "")))
    return (found[1].lower(), found[2].lower()) if found else ("", "")


@dataclass(frozen=True)
class Match:
    """One ``matches`` entry: ``"<kind>:<pattern>"`` (or ``{kind = "pattern"}`` in TOML)."""

    kind: str
    pattern: str

    def text(self) -> str:
        return f"{self.kind}:{self.pattern}"

    def accepts(self, device: dict) -> bool:
        attrs = device.get("attrs") or {}
        bus = device.get("bus")
        if self.kind == "usb":
            if bus != "usb":
                return False
            want_vid, _, want_pid = self.pattern.lower().partition(":")
            vid, pid = usb_ids(device)
            return bool(vid) and fnmatch.fnmatchcase(vid, want_vid) and fnmatch.fnmatchcase(pid, want_pid or "*")
        if self.kind == "pci":
            if bus != "pci":
                return False
            want_vendor, _, want_device = self.pattern.lower().partition(":")
            vendor = _hex(attrs.get("vendor_id") or attrs.get("vendor"))
            product = _hex(attrs.get("device_id") or attrs.get("device"))
            return bool(vendor) and fnmatch.fnmatchcase(vendor, want_vendor) and fnmatch.fnmatchcase(product, want_device or "*")
        if self.kind == "pci-class":
            if bus != "pci":
                return False
            # lane B writes the 24-bit code as hex (attrs.class_code "0c0330") and its name (attrs.class); match either
            code = _hex(attrs.get("class_code") or attrs.get("pci_class"))
            if re.fullmatch(r"(0x)?[0-9a-fA-F]+", self.pattern) and code:
                return code.startswith(_hex(self.pattern))
            return any(fnmatch.fnmatch(str(text).lower(), self.pattern.lower()) for text in (attrs.get("class"), attrs.get("kind")) if text)
        # A by-id link, tty or video node usually belongs to a FUNCTION node (lane B hangs /dev/ttyACM0 under its USB
        # device), so these kinds also look at the device's function nodes: "by-id:*fdcanusb*" selects the fdcanusb.
        scopes = [device, *(device.get("functions") or [])]
        if self.kind == "by-id":
            links = [link for scope in scopes for link in _as_list((scope.get("attrs") or {}).get("by_id"))]
            return any(fnmatch.fnmatchcase(link, self.pattern) or fnmatch.fnmatchcase(Path(link).name, self.pattern) for link in links)
        if self.kind in ("tty", "v4l"):
            own_bus = "tty" if self.kind == "tty" else "v4l"
            nodes = []
            for scope in scopes:
                scope_attrs = scope.get("attrs") or {}
                nodes += _as_list(scope_attrs.get("ttys" if self.kind == "tty" else "video_nodes"))
                if scope.get("bus") == own_bus:
                    nodes += [str(scope.get("id", "")), str(scope.get("label", "")).split(" · ")[0], *_as_list(scope_attrs.get("devnode"))]
            return any(fnmatch.fnmatchcase(node, self.pattern) or fnmatch.fnmatchcase(Path(node).name, self.pattern) for node in nodes if node)
        if self.kind == "netdev":
            if bus != "net":
                return False
            names = [str(device.get("id", "")), str(device.get("label", "")), *_as_list(attrs.get("interface")), *_as_list(attrs.get("name"))]
            return any(fnmatch.fnmatchcase(name, self.pattern) for name in names if name)
        return False


def _match_from_bus_dict(item: dict) -> tuple[str, str]:
    """``{"bus": "usb", "vid": "8086", "pid": "0b*"}`` (the shape a plugin's ``describe`` may use) -> kind, pattern."""

    bus = str(item["bus"]).lower()
    if bus == "usb":
        return "usb", f"{item.get('vid', '*')}:{item.get('pid', '*')}"
    if bus == "pci":
        if item.get("class") or item.get("class_code"):
            return "pci-class", str(item.get("class_code") or item.get("class"))
        return "pci", f"{item.get('vendor', item.get('vendor_id', '*'))}:{item.get('device', item.get('device_id', '*'))}"
    if bus == "net":
        return "netdev", str(item.get("interface") or item.get("name") or "*")
    if bus in ("tty", "v4l"):
        return bus, str(item.get("devnode") or item.get("name") or "*")
    raise PluginConfigError(f"match {item!r}: bus {bus!r} must be usb, pci, net, tty or v4l")


def parse_match(item: Any) -> Match:
    if isinstance(item, dict) and "bus" in item:
        kind, pattern = _match_from_bus_dict(item)
    elif isinstance(item, dict) and len(item) == 1:
        (kind, pattern), = item.items()
    elif isinstance(item, str) and ":" in item:
        kind, _, pattern = item.partition(":")
    else:
        raise PluginConfigError(f"match {item!r} must be '<kind>:<pattern>' with kind one of {MATCH_KINDS}")
    kind = str(kind).strip().lower()
    if kind not in MATCH_KINDS:
        raise PluginConfigError(f"match kind {kind!r} must be one of {MATCH_KINDS}")
    if not str(pattern).strip():
        raise PluginConfigError(f"match {item!r} has an empty pattern")
    return Match(kind, str(pattern).strip())


@dataclass(frozen=True)
class PluginSpec:
    name: str
    command: tuple[str, ...]
    cwd: Path
    file: Path
    timeout_s: float = DEFAULT_TIMEOUT_S
    period_s: float = DEFAULT_PERIOD_S
    act_timeout_s: float = DEFAULT_ACT_TIMEOUT_S  # act only (firmware updates, calibration); diagnose uses timeout_s
    matches: tuple[Match, ...] = ()
    enabled: bool = True
    # WHY replay is declared, not inferred: a replay plugin re-delivers a RECORDING, so a "received" time in its
    # lines is the recorded receive time and the host keeps it; for a live plugin the host's own clock always
    # wins (staleness is judged on the observer's clock, docs/diagnostics-record-v0.md rule 2).
    replay: bool = False
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""
    # WHY a separate list: options of the diagnose VERB (a driver's ``--passive``) must follow
    # ``diagnose --device <id>``; ``command`` holds only what goes before the verb.
    diagnose_args: tuple[str, ...] = ()
    # WHY a declared group: plugins that share one resource the OS cannot arbitrate for them (the rack bus lock is
    # flock LOCK_NB, so the loser FAILS) must take turns. The host runs at most one job (diagnose or act) per group
    # at a time, across plugins and across one plugin's devices; "" = no group, runs in parallel.
    exclusive: str = ""

    def accepts(self, device: dict) -> bool:
        return self.enabled and any(match.accepts(device) for match in self.matches)


def _expand(text: str, here: Path) -> str:
    # {python}: the host's own interpreter, so a stdlib-only plugin (the fixture) runs in uv's 3.14 and the tray's
    # system 3.12 alike; {root}: the io-diagnostics checkout; {here}: the declaring file's directory.
    return text.replace("{python}", sys.executable).replace("{root}", str(ROOT)).replace("{here}", str(here))


def load_plugin(path: Path) -> tuple[PluginSpec, list[str]]:
    """One declaration file -> its spec and non-fatal notes (unknown keys). Raises PluginConfigError."""

    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PluginConfigError(f"{path.name}: {type(error).__name__}: {error}") from error
    notes = [f"{path.name}: unknown key {key!r} ignored" for key in sorted(set(data) - KNOWN_KEYS)]
    here = path.resolve().parent
    name = data.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise PluginConfigError(f"{path.name}: name must be a word ([A-Za-z0-9_.-]), got {name!r}")
    command = data.get("command")
    if isinstance(command, str):
        raise PluginConfigError(f"{path.name}: command must be an argv list (no shell), got a string")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise PluginConfigError(f"{path.name}: command must be a non-empty list of strings")
    cwd = Path(_expand(str(data.get("cwd", here)), here))
    cwd = cwd if cwd.is_absolute() else (here / cwd).resolve()
    argv = [_expand(part, here) for part in command]
    if "/" in argv[0] and not Path(argv[0]).is_absolute():
        argv[0] = str((cwd / argv[0]).resolve())
    numbers = {}
    for key, default in (("timeout_s", DEFAULT_TIMEOUT_S), ("period_s", DEFAULT_PERIOD_S),
                         ("act_timeout_s", DEFAULT_ACT_TIMEOUT_S)):
        value = data.get(key, data.get(KEY_ALIASES[key], default))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise PluginConfigError(f"{path.name}: {key} must be a positive number, got {value!r}")
        numbers[key] = float(value)
    raw_matches = data.get("matches", [])
    if not isinstance(raw_matches, list):
        raise PluginConfigError(f"{path.name}: matches must be a list")
    try:
        matches = tuple(parse_match(item) for item in raw_matches)
    except PluginConfigError as error:
        raise PluginConfigError(f"{path.name}: {error}") from error
    env = data.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(value, str) for value in env.values()):
        raise PluginConfigError(f"{path.name}: env must be a table of strings")
    for flag in ("enabled", "replay"):
        if flag in data and not isinstance(data[flag], bool):
            raise PluginConfigError(f"{path.name}: {flag} must be true or false")
    diagnose_args = data.get("diagnose_args", [])
    if not isinstance(diagnose_args, list) or not all(isinstance(part, str) and part for part in diagnose_args):
        raise PluginConfigError(f"{path.name}: diagnose_args must be a list of strings")
    exclusive = data.get("exclusive", "")
    if not isinstance(exclusive, str) or (exclusive and not _NAME.match(exclusive)):
        raise PluginConfigError(f"{path.name}: exclusive must be a group name ([A-Za-z0-9_.-]), got {exclusive!r}")
    spec = PluginSpec(name=name, command=tuple(argv), cwd=cwd, file=path.resolve(), matches=matches,
                      enabled=data.get("enabled", True), replay=data.get("replay", False),
                      env={key: _expand(value, here) for key, value in env.items()},
                      description=str(data.get("description", "")),
                      diagnose_args=tuple(_expand(part, here) for part in diagnose_args), exclusive=exclusive, **numbers)
    return spec, notes


def load_plugins(directory: Path) -> tuple[list[PluginSpec], list[str]]:
    """Every ``*.toml`` in ``directory`` (not recursive), sorted by file name. A broken file is skipped with a
    problem line; two files with one name keep the first."""

    specs: list[PluginSpec] = []
    problems: list[str] = []
    if not directory.is_dir():
        return specs, [f"{directory}: no such directory (no plugins loaded)"]
    for path in sorted(directory.glob("*.toml")):
        try:
            spec, notes = load_plugin(path)
        except PluginConfigError as error:
            problems.append(str(error))
            continue
        problems += notes
        if any(existing.name == spec.name for existing in specs):
            problems.append(f"{path.name}: a plugin named {spec.name!r} is already loaded; skipped")
            continue
        specs.append(spec)
    return specs, problems


STRUCTURE_KINDS = ("host", "bus", "group", "port")


def match_devices(specs: list[PluginSpec], devices: list[dict]) -> list[tuple[PluginSpec, dict]]:
    """Every (plugin, device) pair to diagnose. A device may have several plugins. Host structure (the host, buses,
    groups, ports) never matches; a function node never matches a plugin that already matched the device exposing
    it (the fdcanusb, not also its /dev/ttyACM0)."""

    pairs = []
    for device in devices:
        if (device.get("attrs") or {}).get("kind", "device") in STRUCTURE_KINDS:
            continue
        for spec in specs:
            if spec.accepts(device):
                pairs.append((spec, device))
    chosen = {(spec.name, str(device.get("id"))) for spec, device in pairs}
    return [(spec, device) for spec, device in pairs if (spec.name, str(device.get("parent"))) not in chosen]
