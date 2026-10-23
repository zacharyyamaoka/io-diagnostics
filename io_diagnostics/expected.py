"""The expected setup: a rig manifest of what this PC should see, and the adapters retired from service.

``~/.config/io-diagnostics/expected.toml`` (``$IO_DIAGNOSTICS_CONFIG_DIR`` overrides the directory; format:
docs/expected-setup.md). WHY outside the checkout: it is the user's own data, like the seen-devices memory; a test
points the directory at a temporary one.

What the host does with it (docs/api-v1.md "Expected setup"):

- one composite **rig row** per rig, ``host/rig-<name>``, kind ``rig``, whose name self-reports its counts at a glance
  ("1-DOF test rack · 2/2 adapters · 6/7 boards · 2/2 cameras"); WARN when anything expected is missing, naming what;
  an unexpected extra node is an info note on it, never a problem;
- each expected device's node paths go to its plugin on diagnose as ``IO_DIAGNOSTICS_EXPECTED``
  (``{"nodes": ["can/1", ...], "rig": "<name>"}``), so the driver's own plain-words reasons cover an expected board
  that does not answer;
- an expected device that is not here gets a row under "Expected, not connected" (with the kernel's evidence when it
  has a USB id);
- a retired device that is plugged in again gets a WARN on its own row: "retired <date>: <reason>".

Edits (Expect what's here / Forget / Retire...) rewrite the file whole, atomically; they never touch a device.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from io_diagnostics import ROOT, activity, tree

from contract.record import ERROR, OK, STALE, UNKNOWN, WARN, DiagnosticStatus, Entity, KeyValue, effective_level

EXPECTED_FILE = "expected.toml"
MANIFEST_VERSION = 1
SOURCE = "io_diagnostics.expected"
RIG_KIND = "rig"
RIG_PREFIX = f"{tree.HOST_PATH}/rig-"
EXPECTED_PATH = f"{tree.HOST_PATH}/expected"  # the "Expected, not connected" group (host.py adds the USB rows)
NODE_KIND = "node"
NODE_WORD = "board"  # what a rig row counts a device's nodes as ("6/7 boards")
# WHY these codes: a node that says it does not answer is missing; one whose check could not run is "not checked"
# (unknown), never found and never missing.
NOT_ANSWERING_CODES = ("NoReply", "NoAck", "BoardNotAnswering")
HEADER = (f"# IO diagnostics · expected setup: the rigs this PC should see, and the devices retired from service.\n"
          f"# Format: {ROOT}/docs/expected-setup.md\n"
          "# The dashboard's Expect what's here / Forget / Retire… buttons and `io-diagnostics expect | forget | retire`\n"
          "# rewrite this file whole: comments other than these lines are not kept.\n")


def default_config_dir() -> Path:
    """``$IO_DIAGNOSTICS_CONFIG_DIR``, else ``$XDG_CONFIG_HOME/io-diagnostics``, else ``~/.config/io-diagnostics``."""

    if os.environ.get("IO_DIAGNOSTICS_CONFIG_DIR"):
        return Path(os.environ["IO_DIAGNOSTICS_CONFIG_DIR"])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "io-diagnostics"


# ----- the manifest ----------------------------------------------------------------------------------------------
@dataclass
class ExpectedNode:
    path: str  # relative to its device, as the plugin writes it ("can/15")
    model_id: str = ""
    hardware_id: str = ""  # the node's own repeatable id, when "Expect what's here" saw one ("moteus:<uuid>")
    note: str = ""  # the person's label for it ("tag 5 · direct"), shown with it, never judged
    # what its config should say (moteus conf motor_position.rotor_to_output_ratio, the measurement the driver sends
    # as "rotor_to_output_ratio"); a fresh reading that differs is one WARN on the node
    rotor_to_output_ratio: float | None = None


@dataclass
class ExpectedDevice:
    name: str
    hardware_id: str = ""  # usb:<vid>:<pid>:<serial> (or any OS row's hardware_id)
    serial: str = ""  # a serial the device's row reports: the USB serial, or a driver's (RealSense SDK serial)
    kind: str = ""  # the word the rig row counts it as: adapter, camera, device ...
    model_id: str = ""
    nodes: list[ExpectedNode] = field(default_factory=list)

    def label(self) -> str:
        return self.name or self.hardware_id or self.serial


@dataclass
class Rig:
    name: str
    devices: list[ExpectedDevice] = field(default_factory=list)


@dataclass
class Retired:
    name: str
    hardware_id: str = ""
    serial: str = ""
    date: str = ""  # YYYY-MM-DD
    reason: str = ""


@dataclass
class Manifest:
    rigs: list[Rig] = field(default_factory=list)
    retired: list[Retired] = field(default_factory=list)

    def entries(self):
        for rig in self.rigs:
            for device in rig.devices:
                yield rig, device


class ManifestError(ValueError):
    pass


def _text(table: dict, key: str, where: str, required: bool = False) -> str:
    value = table.get(key, "")
    if not isinstance(value, str):
        raise ManifestError(f"{where}: {key} must be a string")
    if required and not value.strip():
        raise ManifestError(f"{where}: {key} is required")
    return value.strip()


def parse(text: str) -> Manifest:
    """TOML text -> Manifest; ``ManifestError`` names the first thing wrong."""

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(f"not TOML: {error}") from error
    version = data.get("version", MANIFEST_VERSION)
    if version != MANIFEST_VERSION:
        raise ManifestError(f"version {version!r}: this host reads version {MANIFEST_VERSION}")
    manifest = Manifest()
    rigs = data.get("rig", [])
    if not isinstance(rigs, list):
        raise ManifestError("rig must be an array of tables ([[rig]])")
    for number, table in enumerate(rigs, start=1):
        if not isinstance(table, dict):
            raise ManifestError(f"rig {number}: not a table")
        rig = Rig(_text(table, "name", f"rig {number}", required=True))
        devices = table.get("device", [])
        if not isinstance(devices, list):
            raise ManifestError(f"rig {rig.name!r}: device must be an array of tables ([[rig.device]])")
        for index, item in enumerate(devices, start=1):
            where = f"rig {rig.name!r} device {index}"
            if not isinstance(item, dict):
                raise ManifestError(f"{where}: not a table")
            device = ExpectedDevice(name=_text(item, "name", where), hardware_id=_text(item, "hardware_id", where),
                                    serial=_text(item, "serial", where), kind=_text(item, "kind", where),
                                    model_id=_text(item, "model_id", where))
            if not device.hardware_id and not device.serial:
                raise ManifestError(f"{where}: needs a hardware_id or a serial")
            nodes = item.get("nodes", [])
            if not isinstance(nodes, list):
                raise ManifestError(f"{where}: nodes must be a list")
            for node in nodes:
                if isinstance(node, str) and node.strip():
                    device.nodes.append(ExpectedNode(node.strip().strip("/")))
                elif isinstance(node, dict):
                    ratio = node.get("rotor_to_output_ratio")
                    if ratio is not None and (isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not ratio > 0):
                        raise ManifestError(f"{where} node: rotor_to_output_ratio must be a positive number")
                    device.nodes.append(ExpectedNode(_text(node, "path", f"{where} node", required=True).strip("/"),
                                                     _text(node, "model_id", f"{where} node"),
                                                     _text(node, "hardware_id", f"{where} node"),
                                                     _text(node, "note", f"{where} node"),
                                                     float(ratio) if ratio is not None else None))
                else:
                    raise ManifestError(f"{where}: a node is a path string or a table with a path")
            device.name = device.name or device.hardware_id or device.serial
            rig.devices.append(device)
        manifest.rigs.append(rig)
    retired = data.get("retired", [])
    if not isinstance(retired, list):
        raise ManifestError("retired must be an array of tables ([[retired]])")
    for index, item in enumerate(retired, start=1):
        where = f"retired {index}"
        if not isinstance(item, dict):
            raise ManifestError(f"{where}: not a table")
        entry = Retired(name=_text(item, "name", where), hardware_id=_text(item, "hardware_id", where),
                        serial=_text(item, "serial", where), date=_text(item, "date", where),
                        reason=_text(item, "reason", where))
        if not entry.hardware_id and not entry.serial:
            raise ManifestError(f"{where}: needs a hardware_id or a serial")
        entry.name = entry.name or entry.hardware_id or entry.serial
        manifest.retired.append(entry)
    return manifest


def _string(value: str) -> str:
    # WHY json.dumps: a TOML basic string takes the same escapes (\" \\ \n \uXXXX), so any authored text round-trips
    return json.dumps(value)


def dump(manifest: Manifest) -> str:
    """Manifest -> TOML text that ``parse`` reads back to the same Manifest."""

    lines = [HEADER, f"version = {MANIFEST_VERSION}"]
    for rig in manifest.rigs:
        lines += ["", "[[rig]]", f"name = {_string(rig.name)}"]
        for device in rig.devices:
            lines += ["", "[[rig.device]]", f"name = {_string(device.name)}"]
            for key in ("kind", "hardware_id", "serial", "model_id"):
                if getattr(device, key):
                    lines.append(f"{key} = {_string(getattr(device, key))}")
            if device.nodes:
                lines.append("nodes = [")
                for node in device.nodes:
                    if node.model_id or node.hardware_id or node.note or node.rotor_to_output_ratio is not None:
                        bits = [f"path = {_string(node.path)}"] + [f"{key} = {_string(getattr(node, key))}"
                                                                    for key in ("model_id", "hardware_id", "note") if getattr(node, key)]
                        if node.rotor_to_output_ratio is not None:
                            bits.append(f"rotor_to_output_ratio = {node.rotor_to_output_ratio!r}")
                        lines.append(f"  {{ {', '.join(bits)} }},")
                    else:
                        lines.append(f"  {_string(node.path)},")
                lines.append("]")
            else:
                lines.append("nodes = []")
    for entry in manifest.retired:
        lines += ["", "[[retired]]", f"name = {_string(entry.name)}"]
        for key in ("hardware_id", "serial", "date", "reason"):
            if getattr(entry, key):
                lines.append(f"{key} = {_string(getattr(entry, key))}")
    return "\n".join(lines) + "\n"


def same_id(first: str, second: str) -> bool:
    """Two hardware ids name one device: equal, or the same USB vid / pid / serial (vid and pid in any case)."""

    if not first or not second:
        return False
    if first == second:
        return True
    ids = activity.usb_id(first)
    return ids is not None and ids == activity.usb_id(second)


class ExpectedSetup:
    """The manifest file, re-read when it changes on disk, and the host's edits to it.

    ``path=None``: no manifest (a fixture or a fake machine without ``--expected``): nothing expected, edits refused."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.manifest = Manifest()
        self.note = ""  # why the file could not be read (edits are refused until it reads again)
        self._stamp: tuple | None = None
        self.reload()

    def _file_stamp(self) -> tuple | None:
        try:
            stat = self.path.stat()
        except (OSError, AttributeError):
            return None
        return (stat.st_mtime_ns, stat.st_size, stat.st_ino)

    def reload(self) -> None:
        self._stamp = self._file_stamp()
        if self.path is None or self._stamp is None:
            self.manifest, self.note = Manifest(), ""
            return
        try:
            self.manifest, self.note = parse(self.path.read_text()), ""
        except (OSError, ManifestError) as error:
            # WHY keep nothing rather than the last good copy: the file is what the person edits; a host acting on a
            # copy they can no longer see would be wrong in a way the page cannot show
            self.manifest, self.note = Manifest(), f"{self.path}: {error}"

    def reload_if_changed(self) -> None:
        if self._file_stamp() != self._stamp:
            self.reload()

    def save(self, manifest: Manifest) -> None:
        """Write the whole file atomically (temp file + rename in the same directory), then adopt it."""

        if self.path is None:
            raise ManifestError("no expected-setup file on this host (a fixture or a fake machine without --expected)")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w") as file:
                file.write(dump(manifest))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise
        self.reload()

    def editable(self) -> Manifest:
        """A copy to edit; refuses while the file does not read (an edit would overwrite what the person wrote)."""

        if self.path is None:
            raise ManifestError("no expected-setup file on this host (a fixture or a fake machine without --expected)")
        if self.note:
            raise ManifestError(f"the expected-setup file does not read, so it is not rewritten: {self.note}")
        return dataclasses.replace(self.manifest, rigs=[dataclasses.replace(rig, devices=[
            dataclasses.replace(device, nodes=[dataclasses.replace(node) for node in device.nodes]) for device in rig.devices])
            for rig in self.manifest.rigs], retired=[dataclasses.replace(entry) for entry in self.manifest.retired])

    def retired_entry(self, hardware_id: str = "", serials: tuple[str, ...] = ()) -> Retired | None:
        for entry in self.manifest.retired:
            if (entry.hardware_id and same_id(entry.hardware_id, hardware_id)) or (entry.serial and entry.serial in serials):
                return entry
        return None

    def entry_for(self, hardware_id: str = "", serials: tuple[str, ...] = ()) -> tuple[Rig, ExpectedDevice] | None:
        for rig, device in self.manifest.entries():
            if device.hardware_id and same_id(device.hardware_id, hardware_id):
                return rig, device
        for rig, device in self.manifest.entries():
            if device.serial and device.serial in serials:
                return rig, device
        return None

    def plugin_env(self, hardware_id: str, serials: tuple[str, ...]) -> dict[str, str]:
        """``{"IO_DIAGNOSTICS_EXPECTED": '{"nodes": [...], "rig": "..."}'}`` for an expected device, else ``{}``
        (the driver then falls back to its own source, e.g. moteus's rack inventory)."""

        found = self.entry_for(hardware_id, serials)
        if found is None:
            return {}
        rig, device = found
        return {"IO_DIAGNOSTICS_EXPECTED": json.dumps({"nodes": [node.path for node in device.nodes], "rig": rig.name})}


# ----- what the merged tree shows about the manifest ------------------------------------------------------------
@dataclass
class Found:
    """One expected device against the tree now."""

    rig: Rig
    device: ExpectedDevice
    path: str | None  # its row, None = not here
    nodes: dict[str, str] = field(default_factory=dict)  # node path (relative) -> found | missing | unknown
    moved_to: str | None = None  # the device path whose nodes answer for this absent one
    pending: bool = False  # known only by a driver's serial, and a driver has not reported yet: not checked


@dataclass
class SetupView:
    entities: list[tuple[Entity, str]] = field(default_factory=list)  # (entity, icon), rig rows first
    statuses: list[DiagnosticStatus] = field(default_factory=list)
    rigs: dict[str, Rig] = field(default_factory=dict)  # rig row path -> rig
    found: list[Found] = field(default_factory=list)
    by_path: dict[str, Found] = field(default_factory=dict)  # present device row -> its entry
    retired: dict[str, Retired] = field(default_factory=dict)  # present device row -> its retirement
    absent: dict[str, Found] = field(default_factory=dict)  # serial-only absent row -> its entry


def rig_path(rig: Rig) -> str:
    return f"{RIG_PREFIX}{tree.slug(rig.name.lower()) or 'rig'}"


def latest_statuses(statuses: list[DiagnosticStatus]) -> dict[str, list[DiagnosticStatus]]:
    latest: dict[tuple[str, str], DiagnosticStatus] = {}
    for status in statuses:
        latest[(status.path, status.name)] = status
    out: dict[str, list[DiagnosticStatus]] = {}
    for (path, _name), status in latest.items():
        out.setdefault(path, []).append(status)
    return out


def node_state(own: list[DiagnosticStatus], now: float, host_source: str) -> str:
    """``found`` (it answers), ``missing`` (it says it does not) or ``unknown`` (nothing fresh says either)."""

    fresh = [status for status in own if status.source != host_source and effective_level(status, now) != STALE]
    if any(status.code in NOT_ANSWERING_CODES for status in fresh):
        return "missing"
    if any(effective_level(status, now) in (OK, WARN, ERROR) and status.code not in tree.NOT_ANSWERING_CODES
           for status in fresh):
        return "found"
    return "unknown"


def _plugin_ran(own: list[DiagnosticStatus], now: float, host_source: str) -> bool:
    """The device's plugin finished a run recently (the host's own "plugin run" check is fresh and not a failure)."""

    return any(status.source == host_source and effective_level(status, now) in (OK, WARN) for status in own)


def device_serials(entity: Entity, layer: tree.OsLayer, path: str) -> tuple[str, ...]:
    """Every serial a device row reports: the USB descriptor's (the skeleton) and the driver's identity serial."""

    serials = [str((entity.identity or {}).get("serial_number") or "")]
    device_id = layer.device_of_path.get(path)
    if device_id is not None:
        serials.append(str(layer.devices[device_id].get("serial") or ""))
    ids = activity.usb_id(entity.hardware_id)
    if ids:
        serials.append(ids[2])
    return tuple(serial for serial in dict.fromkeys(serials) if serial)


def physical_rows(merged: tree.Merged, layer: tree.OsLayer) -> list[str]:
    return [layer.device_paths[device_id] for device_id in layer.devices
            if device_id in layer.physical and layer.device_paths[device_id] in merged.entities]


def locate(device: ExpectedDevice, merged: tree.Merged, layer: tree.OsLayer) -> str | None:
    rows = physical_rows(merged, layer)
    if device.hardware_id:
        found = next((path for path in rows if same_id(merged.entities[path].hardware_id, device.hardware_id)), None)
        if found:
            return found
    if device.serial:
        return next((path for path in rows if device.serial in device_serials(merged.entities[path], layer, path)), None)
    return None


def node_rows(device_path: str, merged: tree.Merged) -> list[str]:
    """Every entity of kind ``node`` below a device row, in tree order (relative paths are what a manifest stores)."""

    out, stack = [], list(reversed(merged.children.get(device_path, [])))
    while stack:
        path = stack.pop()
        if merged.entities[path].kind == NODE_KIND:
            out.append(path)
        stack.extend(reversed(merged.children.get(path, [])))
    return out


def relative(path: str, device_path: str) -> str:
    return path[len(device_path) + 1:] if path.startswith(device_path + "/") else path


def answering_nodes(device_path: str, merged: tree.Merged, own: dict[str, list[DiagnosticStatus]], now: float,
                    host_source: str) -> list[str]:
    return [path for path in node_rows(device_path, merged) if node_state(own.get(path, []), now, host_source) == "found"]


def kind_of_row(merged: tree.Merged, path: str) -> str:
    icon = merged.icons.get(path, "")
    return icon if icon in ("adapter", "camera") else "device"


def _plural(count: int, word: str) -> str:
    return word if count == 1 else (word[:-1] + "ies" if word.endswith("y") and word[-2:-1] not in "aeiou" else word + "s")


def _numbered(lines: list[str]) -> str:
    return "\n".join(f"{number}. {line}" for number, line in enumerate(lines, start=1))


def evaluate(setup: ExpectedSetup, merged: tree.Merged, layer: tree.OsLayer, now: float, host_source: str) -> SetupView:
    """The rig rows, the retired-device warnings and the serial-only absent rows, judged on the merged tree."""

    view = SetupView()
    manifest = setup.manifest
    own = latest_statuses(merged.statuses)
    claimed: set[str] = set()
    # WHY: a device known only by its driver's serial (a RealSense SDK serial) cannot be told absent while a
    # driver that may report it has not run yet (the host's first seconds)
    unreported = any(not _plugin_ran(own.get(layer.device_paths[device_id], []), now, host_source)
                     for device_id in merged.plugin_devices if device_id in layer.device_paths)
    for rig in manifest.rigs:
        for device in rig.devices:
            found = Found(rig, device, locate(device, merged, layer))
            found.pending = found.path is None and not device.hardware_id and unreported
            if found.path:
                claimed.add(found.path)
                view.by_path.setdefault(found.path, found)
                ran = _plugin_ran(own.get(found.path, []), now, host_source)
                for node in device.nodes:
                    node_path = f"{found.path}/{node.path}"
                    if node_path in merged.entities:
                        found.nodes[node.path] = node_state(own.get(node_path, []), now, host_source)
                    else:
                        # WHY missing only after a good run: its driver looked and did not report it; before any run
                        # (or after a crash) nobody has looked yet
                        found.nodes[node.path] = "missing" if ran else "unknown"
            else:
                found.nodes = {node.path: "missing" for node in device.nodes}
            view.found.append(found)
    # an absent device whose boards answer on another adapter (it was replaced): say where they went
    for found in view.found:
        if found.path or not found.device.nodes:
            continue
        found.moved_to = _moved_to(found, merged, layer, own, now, host_source)
        if found.moved_to:
            target = found.moved_to
            found.nodes = {node.path: node_state(own.get(f"{target}/{node.path}", []), now, host_source)
                           if f"{target}/{node.path}" in merged.entities else "missing" for node in found.device.nodes}
    for found in view.found:
        if found.path:
            view.statuses += _ratio_checks(found, merged, own, now, host_source)
    for rig in manifest.rigs:
        path = rig_path(rig)
        view.rigs[path] = rig
        members = [found for found in view.found if found.rig is rig]
        entity, status = _rig_row(rig, path, members, merged, own, now, host_source, setup)
        view.entities.append((entity, "group"))
        view.statuses.append(status)
    for path in physical_rows(merged, layer):
        entity = merged.entities[path]
        retired = setup.retired_entry(entity.hardware_id, device_serials(entity, layer, path))
        if retired is None:
            continue
        view.retired[path] = retired
        when = f"retired {retired.date}" if retired.date else "retired"
        view.statuses.append(DiagnosticStatus(
            level=WARN, name=f"{entity.name}: retired", code="RetiredDevice", path=path, hardware_id=entity.hardware_id,
            message=f"{when}: {retired.reason}" if retired.reason else when, stamp=now, received=now, source=SOURCE,
            values=(KeyValue("what_to_check", _numbered([
                f"{entity.name} was taken out of service{f' on {retired.date}' if retired.date else ''}"
                + (f": {retired.reason}" if retired.reason else ""),
                "Unplug it and put it aside, or use Expect what's here to take it back into service"])),
                KeyValue("retired_on", retired.date or "unknown"), KeyValue("reason", retired.reason or ""))))
    for found in view.found:
        if found.path or found.pending or found.device.hardware_id or not found.device.serial:
            continue  # a USB id's absence is judged by the host with the kernel's evidence (host.py)
        path = f"{EXPECTED_PATH}/{tree.slug('serial-' + found.device.serial)}"
        view.absent[path] = found
        view.entities.append((Entity(path, found.device.label(), "device", via=EXPECTED_PATH, owner="io_diagnostics",
                                     identity={"serial_number": found.device.serial}), "usb"))
        view.statuses.append(DiagnosticStatus(
            level=WARN, name=f"{found.device.label()}: presence", code="ExpectedAbsent", path=path,
            message=f"expected ({found.rig.name}) · no connected device reports serial {found.device.serial}",
            stamp=now, received=now, source=SOURCE,
            values=(KeyValue("what_to_check", _numbered([
                "Is it plugged in? Its cable and both connectors", "Another USB port, not through a hub",
                "Its driver's row: a device is matched by this serial only after its driver has run"])),
                KeyValue("expected_by", f"expected setup: {found.rig.name}"))))
    return view


RATIO_KEY = "rotor_to_output_ratio"
RATIO_TOLERANCE = 1e-3  # relative: 0.1 vs 0.1667 is a wrong gearbox, 0.1 vs 0.10000001 is float noise


def gearbox_text(ratio: float) -> str:
    """0.1 -> "10:1 gearbox", 1 -> "direct drive"."""

    if abs(ratio - 1.0) <= RATIO_TOLERANCE:
        return "direct drive"
    return f"{1 / ratio:.3g}:1 gearbox" if ratio < 1 else f"1:{ratio:.3g} step-up"


def _ratio_checks(found: Found, merged: tree.Merged, own: dict, now: float, host_source: str) -> list[DiagnosticStatus]:
    """One WARN per expected node whose fresh config reading of rotor_to_output_ratio differs from the file's."""

    out = []
    for node in found.device.nodes:
        path = f"{found.path}/{node.path}"
        if node.rotor_to_output_ratio is None or path not in merged.entities:
            continue
        readings = [measurement.value for status in own.get(path, []) if status.source != host_source
                    and effective_level(status, now) != STALE for measurement in status.measurements
                    if measurement.key == RATIO_KEY and isinstance(measurement.value, (int, float))
                    and not isinstance(measurement.value, bool)]
        if not readings:
            continue
        actual, expected = float(readings[-1]), node.rotor_to_output_ratio
        if abs(actual - expected) <= RATIO_TOLERANCE * max(abs(expected), 1e-9):
            continue
        name = merged.entities[path].name
        out.append(DiagnosticStatus(
            level=WARN, name=f"{name}: expected ratio", code="RatioMismatch", path=path,
            hardware_id=merged.entities[path].hardware_id, stamp=now, received=now, source=SOURCE,
            message=f"config ratio {actual:.4g}, expected {expected:g} ({gearbox_text(expected)})",
            values=tuple(value for value in (
                KeyValue("what_to_check", _numbered([
                    f"Which gearbox is on {name}" + (f" ({node.note})" if node.note else "") + f": the file says "
                    f"{gearbox_text(expected)}",
                    f"If it is, set its config to {expected:g} (Update config… with a corrected recorded config); if "
                    f"not, correct rotor_to_output_ratio for {name} in {found.rig.name}'s expected setup"])),
                KeyValue("expected_ratio", f"{expected:g}"), KeyValue("config_ratio", f"{actual:.6g}"),
                KeyValue("note", node.note) if node.note else None) if value is not None)))
    return out


def _moved_to(found: Found, merged: tree.Merged, layer: tree.OsLayer, own: dict, now: float, host_source: str) -> str | None:
    wanted_ids = {node.hardware_id for node in found.device.nodes if node.hardware_id}
    candidates = []
    for path in physical_rows(merged, layer):
        entity = merged.entities[path]
        if found.device.model_id and entity.identity.get("model_id") and entity.identity.get("model_id") != found.device.model_id:
            continue
        ids = activity.usb_id(found.device.hardware_id)
        if ids and activity.usb_id(entity.hardware_id) and activity.usb_id(entity.hardware_id)[:2] != ids[:2]:
            continue
        answering = answering_nodes(path, merged, own, now, host_source)
        if wanted_ids and wanted_ids & {merged.entities[node].hardware_id for node in answering}:
            return path
        here = {relative(node, path) for node in answering}
        if not wanted_ids and all(node.path in here for node in found.device.nodes):
            candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def _rig_row(rig: Rig, path: str, members: list[Found], merged: tree.Merged, own: dict, now: float, host_source: str,
             setup: ExpectedSetup) -> tuple[Entity, DiagnosticStatus]:
    kinds: dict[str, list[int]] = {}  # kind word -> [here, expected]
    boards = [0, 0]
    missing, unknown, extras, moved, mismatched, checks = [], [], [], [], [], []
    # WHY: the rig's verdict is caused by its members' own reports (record v0.1 caused_by), so it names, per problem,
    # the row that reports it; None = no row says it (the rig is then its own cause)
    missing_rows: list[str | None] = []
    unknown_rows: list[str | None] = []
    for found in members:
        word = found.device.kind or (kind_of_row(merged, found.path) if found.path else "device")
        count = kinds.setdefault(word, [0, 0])
        count[1] += 1
        if found.device.nodes and "_boards" not in kinds:
            kinds["_boards"] = boards  # the boards count right after the first kind that has nodes
        device_name = merged.entities[found.path].name if found.path else found.device.label()
        if found.path:
            count[0] += 1
        elif found.pending:
            unknown.append(f"{found.device.label()} (its driver has not reported yet)")
            unknown_rows.append(None)
            boards[1] += len(found.device.nodes)
            continue
        else:
            missing.append(f"{found.device.label()} (not here)")
            missing_rows.append(_absent_row(found.device, merged))
            if found.moved_to:
                target = merged.entities[found.moved_to].name
                moved.append(f"{found.device.label()}'s {_plural(len(found.device.nodes), NODE_WORD)} answer on {target} now")
                checks.append(f"{found.device.label()}'s {_plural(len(found.device.nodes), NODE_WORD)} answer on {target}: "
                              f"if {target} replaced it, use Expect what's here on {target}, then Retire… on "
                              f"{found.device.label()}")
            else:
                checks.append(f"{found.device.label()}: not on this PC: see its row under Expected, not connected "
                              "(Forget or Retire… it if it is gone for good)")
        base = found.path or found.moved_to
        for node in found.device.nodes:
            boards[1] += 1
            state = found.nodes.get(node.path, "missing")
            label = merged.entities[f"{base}/{node.path}"].name if base and f"{base}/{node.path}" in merged.entities else node.path
            if state == "found":
                boards[0] += 1
                actual = merged.entities[f"{base}/{node.path}"].identity.get("model_id", "")
                if node.model_id and actual and actual != node.model_id:
                    mismatched.append(f"{label} on {device_name} is {actual}, expected {node.model_id}")
            elif state == "unknown" and base:
                unknown.append(f"{label} on {device_name}")
                unknown_rows.append(f"{base}/{node.path}" if f"{base}/{node.path}" in merged.entities else None)
            elif found.path:
                missing.append(f"{label} on {device_name}")
                missing_rows.append(f"{base}/{node.path}" if f"{base}/{node.path}" in merged.entities else None)
                checks.append(f"{label} on {device_name} does not answer: see its row for what to check (or Expect "
                              f"what's here on {device_name} if it was taken off on purpose)")
        if found.path:
            expected_paths = {node.path for node in found.device.nodes}
            for node in answering_nodes(found.path, merged, own, now, host_source):
                if relative(node, found.path) not in expected_paths:
                    extras.append(f"{merged.entities[node].name} on {device_name}")
    counts = []
    for word, (here, total) in kinds.items():
        counts.append(f"{here}/{total} {_plural(total, NODE_WORD if word == '_boards' else word)}")
    name = f"{rig.name} · {' · '.join(counts)}" if counts else f"{rig.name} · nothing expected yet"
    values = [KeyValue(_plural(2, NODE_WORD if word == "_boards" else word), f"{here}/{total}") for word, (here, total) in kinds.items()]
    if missing:
        level, code, message = WARN, "ExpectedMissing", f"missing: {' · '.join(missing)}"
        rows = missing_rows
    elif unknown:
        level, code = UNKNOWN, "ExpectedNotChecked"
        message = f"not checked: {' · '.join(unknown)} (no fresh answer either way)"
        rows = unknown_rows
    else:
        level, code, message = OK, "", "everything expected is here" if members else "nothing expected yet: use Expect what's here on a device"
        rows = []
    caused_by = tuple(dict.fromkeys(rows)) if rows and None not in rows else ()
    notes = moved + [f"also here, not expected: {', '.join(extras)}"] * bool(extras) + mismatched
    if notes:
        message += " · " + " · ".join(notes)
    if missing:
        values.append(KeyValue("missing", ", ".join(missing)))
    if checks:
        values.append(KeyValue("what_to_check", _numbered(checks)))
    if moved:
        values.append(KeyValue("moved", "; ".join(moved)))
    if extras:
        values.append(KeyValue("not_expected", ", ".join(extras)))
    if mismatched:
        values.append(KeyValue("model_mismatch", "; ".join(mismatched)))
    values.append(KeyValue("file", str(setup.path) if setup.path else "none"))
    entity = Entity(path, name, RIG_KIND, via=tree.HOST_PATH, owner="io_diagnostics")
    status = DiagnosticStatus(level=level, name=f"{rig.name}: expected setup", code=code, message=message, path=path,
                              values=tuple(values), stamp=now, received=now, source=SOURCE, caused_by=caused_by)
    return entity, status


def _absent_row(device: ExpectedDevice, merged: tree.Merged) -> str | None:
    """The "Expected, not connected" row that reports an absent device: the host's kernel-evidence row for a USB id,
    else the serial-only row ``evaluate`` adds; None when no row will say it."""

    if device.hardware_id:
        return next((path for path, entity in merged.entities.items()
                     if path.startswith(EXPECTED_PATH + "/") and same_id(entity.hardware_id, device.hardware_id)), None)
    return f"{EXPECTED_PATH}/{tree.slug('serial-' + device.serial)}" if device.serial else None


# ----- the panel's edits: Expect what's here / Forget / Retire... --------------------------------------------------
def today(now: float, utc_offset_s: int | None) -> str:
    return (activity.iso(now, utc_offset_s) or time.strftime("%Y-%m-%d"))[:10]


def carry_over(old: list[ExpectedNode], new: list[ExpectedNode]) -> list[ExpectedNode]:
    """The new snapshot's nodes, keeping what the person wrote on a node that stays (its note, its expected ratio)."""

    by_path = {node.path: node for node in old}
    for node in new:
        if node.path in by_path:
            node.note = node.note or by_path[node.path].note
            if node.rotor_to_output_ratio is None:
                node.rotor_to_output_ratio = by_path[node.path].rotor_to_output_ratio
            node.model_id = node.model_id or by_path[node.path].model_id
    return new


def remove_device(manifest: Manifest, hardware_id: str, serials: tuple[str, ...]) -> list[str]:
    """Drop every entry naming this device from every rig; returns the rigs it was in."""

    rigs = []
    for rig in manifest.rigs:
        kept = [device for device in rig.devices
                if not ((device.hardware_id and same_id(device.hardware_id, hardware_id)) or (device.serial and device.serial in serials))]
        if len(kept) != len(rig.devices):
            rigs.append(rig.name)
        rig.devices = kept
    return rigs


def unretire(manifest: Manifest, hardware_id: str, serials: tuple[str, ...]) -> bool:
    kept = [entry for entry in manifest.retired
            if not ((entry.hardware_id and same_id(entry.hardware_id, hardware_id)) or (entry.serial and entry.serial in serials))]
    changed = len(kept) != len(manifest.retired)
    manifest.retired = kept
    return changed


__all__ = ["EXPECTED_FILE", "SOURCE", "RIG_KIND", "ExpectedNode", "ExpectedDevice", "Rig", "Retired", "Manifest",
           "ManifestError", "ExpectedSetup", "SetupView", "Found", "parse", "dump", "evaluate", "default_config_dir",
           "rig_path", "same_id", "node_rows", "answering_nodes", "relative", "kind_of_row", "device_serials", "locate",
           "remove_device", "unretire", "today", "latest_statuses", "node_state"]
