"""The host runs robotdx.diagnostics: which stock diagnostic runs on which entity, on a slow beat over that entity's State history,
each Result an ordinary record-v0.2 status on the entity (docs/api-v1.md "Stock diagnostics").

- **Which, where, with what config:** ``<config dir>/diagnostics.toml`` (``[[diagnostic]]`` tables: ``id``, ``diagnostic``, a ``path``
  and / or ``model_id`` and / or ``part`` match, ``[diagnostic.config]`` values), else the host's default set (``diagnostics_default.d/``, one
  file per part type, generated from the catalog by ``default_diagnostics.py``). The file is read with the registry's ``load_toml`` (a table
  with ``function`` registers a custom diagnostic) and re-read when it changes. The keys:

      [[part]]          which entities are a part type: type (catalog.PARTS), model_id / path / composite (lists of patterns;
                        composite = a composite's kind). An entity may be several types (a RealSense is camera_rgb and camera_rgbd).
      [[diagnostic]]    one binding: id (the catalog's, "driver.board_temp"), diagnostic (the registry's function, "stock.box"),
                        title, and at least one of path / model_id (fnmatch) / part; state (the State group, "" = the first with the
                        columns), window_s (s of State history per evaluation)
      [diagnostic.config]   the config dataclass's values; a field left out keeps the dataclass default
      [diagnostic.columns]  (optional) a Trajectory column = the State key it is read from, beside COLUMNS
- **Whose limits:** a catalog binding whose thresholds are an agent's or proposed (robotdx/diagnostics/defaults.py) reports a crossing as a
  warning that names the limit as proposed, never an error, until a person sets a limit of their own (catalog.gating / advisory).
  A fixture replay runs only the person's file, never the default set (a recording is replayed as it was).
- **When:** every ``BEAT_S`` (5 s) on a thread of its own, never on the 10 Hz watch path. Each binding reads the last ``window_s`` of
  the entity's State history (history.py keeps it at 1 Hz at most) through ``stream.monitor`` and keeps the newest window's Result.
- **What feeds it** (``source_of``, the table ``SOURCES``): each binding reads one typed input, out of what RobotDx entities actually publish
  (``Inputs``, gathered by the host each beat): the State history as a Trajectory; every State record's arrival as Stamps; frame records
  as StampPairs; the newest image of the colour stream as Frames (PNG or JPEG, decoded only here, on the beat, never on the watch path); the fault
  memory's FaultConfirmed events and the history's operating time as an EventLog; link events as Events; a status's reply counts as
  Events; config records with an expected value as Pairs; a composite's members as Results; the statuses' stamps as EvidenceStamps.
  What a composite borrows: a servo or encoder over one board reads that board's State; a servo whose State has no commanded position
  reads the newest recorded run of its joint (``RackArchive``) and its holds; a deployment reads the newest rack acceptance (gate matrix)
  and the Computer plugin's CPU temperature (rack.host). Round 4: a camera's recorded captures and calibrate runs, a servo's calibration
  log and follow-up, a gearbox's calibration round against its model card, and (while the arm's State has no commanded pose) the newest
  UR5e sim cell report, every such result prefixed "UR5e sim cell (<situation>): ".
- **What it cannot run:** a binding whose input no source on the entity carries is not run with code ``NotMeasured`` (no instrument on
  the entity can produce it) or ``NotWired`` (an existing producer has it or could publish it; nothing maps it yet), value ``dx.gap``, and
  a message "no <input> source on <entity>: <what would provide it>", unknown and never OK. It is a row where the entity reports State, and none
  where it reports no State at all (as ``NoState``: a board nobody watches posts no grey row forever); the drill-in API always lists it,
  with the binding's ``source``. ``NotStreaming``: the source is there but nothing arrived in the window (frames come while a page
  watches): no row either. ``NoDecoder``: a frame in a format nothing here decodes (a JPEG without Pillow).
- **What it emits:** ``record.to_record`` of the Result (source ``robotdx.diagnostics``, ``dx.*`` values, ``expected_period_s`` = the
  beat, so a stopped beat goes STALE), plus ``dx.binding`` and ``dx.window``. A Result that could not be computed is not_run (level
  unknown), never OK; a diagnostic that raises is not_run with the exception text; numpy missing is not_run with that reason.
- **Drill-in:** ``config_json`` (each binding's fields, effective values and where each came from: default / toml / edited) and
  ``set_field`` (validate against the dataclass field's type and the Limits ordering, then rewrite the file atomically).

WHY the State history, not the watch's 10 Hz stream (Zach, 2026-10-28): RobotDx is the system monitor over one robot's whole history;
the history is that stream, kept, and a window of it is exactly what the library's RobotDx consumer evaluates.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import inspect
import json
import math
import os
import re
import tempfile
import threading
import tomllib
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from contract.record import from_dict
from io_diagnostics.composite import toml_value
# WHY the not-run codes live in not_run.py: tree.py rolls levels up without them and host.py marks their rows, and neither may import this module
from io_diagnostics.not_run import (DIAGNOSTIC_RAISED, NO_DECODER, NO_STATE, NOT_ENOUGH_HISTORY, NOT_MEASURED, NOT_STREAMING, NOT_WIRED,
                                    NUMPY_MISSING_CODE, is_not_run)
from robotdx.diagnostics import catalog as dx_catalog
from robotdx.diagnostics import core as dx_core
from robotdx.diagnostics import record as dx_record
from robotdx.diagnostics import registry as dx_registry
from robotdx.diagnostics._numpy import MISSING as NUMPY_MISSING

DIAGNOSTICS_FILE = "diagnostics.toml"
# WHY a directory, one file per part type: 70-odd bindings read best grouped by what they are about (servo, camera_rgbd, ...); they are
# generated from the catalog by tools/build_default_diagnostics.py, and tests/test_default_diagnostics.py fails when they drift
DEFAULT_DIR = Path(__file__).with_name("diagnostics_default.d")
SOURCE = dx_record.SOURCE  # "robotdx.diagnostics": the status's source, which the page keys its drill-in on
BEAT_S = 5.0
DEFAULT_WINDOW_S = 60.0
# WHY two codes, not one NoSource (Codex audit 2026-11-04 r3, gap 2): "no source" hid two different next steps. NotMeasured needs an
# instrument (a sensor, a log, a tag: hardware or a person); NotWired is software, the data exists or an existing producer could publish it
NO_SOURCE_CODES = (NOT_WIRED, NOT_MEASURED)
GAP_KEY = "dx.gap"  # not_wired | not_measured on a status of either code
QUIET =(NO_STATE, NOT_STREAMING)  # not run until the entity streams: listed by the drill-in, never a grey row
QUIET_KEY = "dx.quiet"  # a value on a NotWired / NotMeasured status of an entity that reports no State: listed by the drill-in, no row (``quiet``)
MIN_WINDOW_S, MAX_WINDOW_S = 2.0, 86400.0
LIMIT_PARTS = ("warn_low", "warn_high", "error_low", "error_high")
HEADER = ("# robotdx.diagnostics on this host (io_diagnostics/stock_diagnostics.py; the default set and every key explained:\n"
          "# io_diagnostics/diagnostics_default.d/). The entity page's drill-in rewrites this file whole (comments are not kept);\n"
          "# `edited` lists the fields it changed.\n")
# The State keys a moteus board sends (moteus_servo.diagnostics state_measurements) -> the Trajectory's PEP 0010 columns. A unit the
# column does not use is converted (rev -> rad); a binding's [diagnostic.columns] adds or overrides an entry.
COLUMNS = {"q_rad": "position", "qd_rad_s": "velocity", "tau_nm": "torque", "i_q_a": "q_current", "v_bus_v": "voltage",
           "temp_c": "temperature", "mode": "mode", "fault": "fault"}
TO_SI = {"rev": 2 * math.pi, "rev/s": 2 * math.pi, "deg": math.pi / 180, "deg/s": math.pi / 180}
CODE_COLUMNS = ("mode", "fault")  # a code holds until the next report: a gap is carried forward, never a NaN cast to int


class DiagnosticsError(ValueError):
    """A diagnostics.toml the host cannot use (the reason is shown in /api/v1/diagnostics)."""


@dataclass(frozen=True)
class Binding:
    id: str  # this binding's own name (the catalog's, "driver.board_temp"); unique in the file
    diagnostic: str  # the registry's function, "stock.box"
    title: str  # the status name on the entity
    path: str = ""  # fnmatch over the entity path
    model_id: str = ""  # fnmatch over identity.model_id
    part: str = ""  # a part type (catalog.PARTS) the entity is, by the file's [[part]] tables
    state: str = ""  # the State group it reads; "" = the first with a mapped column
    window_s: float = DEFAULT_WINDOW_S
    config: Mapping[str, Any] = field(default_factory=dict)  # the file's values (plain data)
    columns: Mapping[str, str] = field(default_factory=dict)  # Trajectory column -> State key, beside COLUMNS
    edited: tuple[str, ...] = ()  # fields the page changed ("max.warn_high" for one part of a Limits)

    def matches(self, path: str, model_id: str, parts: tuple[str, ...] = ()) -> bool:
        if self.path and not fnmatch.fnmatchcase(path, self.path):
            return False
        if self.model_id and not fnmatch.fnmatchcase(model_id or "", self.model_id):
            return False
        if self.part and self.part not in parts:
            return False
        return bool(self.path or self.model_id or self.part)


@dataclass(frozen=True)
class PartRule:
    """``[[part]]``: which entities are a part type, so a binding can say ``part = "camera_rgbd"`` instead of listing models.
    An entity is the type when any pattern matches: its identity's model_id, its path, or (a composite) its kind."""

    type: str
    model_id: tuple[str, ...] = ()
    path: tuple[str, ...] = ()
    composite: tuple[str, ...] = ()

    def matches(self, path: str, model_id: str, composite_kind: str) -> bool:
        return (any(fnmatch.fnmatchcase(model_id, pattern) for pattern in self.model_id if model_id)
                or any(fnmatch.fnmatchcase(path, pattern) for pattern in self.path)
                or (bool(composite_kind) and composite_kind in self.composite))


def parse_parts(document: Mapping[str, Any]) -> list[PartRule]:
    """The ``[[part]]`` tables of a parsed diagnostics.toml. A broken one raises with its type."""

    from robotdx.diagnostics.catalog import PARTS

    out = []
    for number, table in enumerate(document.get("part") or [], 1):
        where = f"[[part]] {table.get('type') if isinstance(table, dict) else number}"
        if not isinstance(table, dict):
            raise DiagnosticsError(f"{where} is not a table")
        unknown = set(table) - {"type", "model_id", "path", "composite"}
        if unknown:
            raise DiagnosticsError(f"{where}: unknown key(s) {sorted(unknown)}")
        if table.get("type") not in PARTS:
            raise DiagnosticsError(f"{where}: type is one of the catalog's part types ({', '.join(PARTS)})")
        lists = {}
        for name in ("model_id", "path", "composite"):
            value = table.get(name, [])
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                raise DiagnosticsError(f"{where}: {name} is a list of patterns")
            lists[name] = tuple(value)
        out.append(PartRule(type=table["type"], **lists))
    return out


_BINDING_KEYS = {"id", "diagnostic", "title", "path", "model_id", "part", "state", "window_s", "config", "columns", "edited"}


def parse(document: Mapping[str, Any]) -> tuple[list[Binding], list[dict]]:
    """``(bindings, custom spec tables)`` of a parsed diagnostics.toml. A broken binding raises with its id."""

    bindings, specs, seen = [], [], set()
    for number, table in enumerate(document.get("diagnostic") or [], 1):
        if not isinstance(table, dict):
            raise DiagnosticsError(f"[[diagnostic]] {number} is not a table")
        if "function" in table:  # a custom diagnostic: the registry's load_toml registers it
            specs.append(table)
            continue
        where = f"[[diagnostic]] {table.get('id') or number}"
        unknown = set(table) - _BINDING_KEYS
        if unknown:
            raise DiagnosticsError(f"{where}: unknown key(s) {sorted(unknown)}")
        if not isinstance(table.get("id"), str) or not table["id"]:
            raise DiagnosticsError(f"{where}: id is required (a name of its own, like driver.board_temp)")
        if table["id"] in seen:
            raise DiagnosticsError(f"{where}: id {table['id']!r} is used twice")
        seen.add(table["id"])
        if not isinstance(table.get("diagnostic"), str) or not table["diagnostic"]:
            raise DiagnosticsError(f"{where}: diagnostic is required (a registry name, like stock.box)")
        if not table.get("path") and not table.get("model_id") and not table.get("part"):
            raise DiagnosticsError(f"{where}: says no path, model_id or part, so it matches no entity")
        window = table.get("window_s", DEFAULT_WINDOW_S)
        if isinstance(window, bool) or not isinstance(window, (int, float)) or not MIN_WINDOW_S <= window <= MAX_WINDOW_S:
            raise DiagnosticsError(f"{where}: window_s is a number of seconds, {MIN_WINDOW_S:g} .. {MAX_WINDOW_S:g}")
        for name in ("config", "columns"):
            if not isinstance(table.get(name, {}), dict):
                raise DiagnosticsError(f"{where}: [diagnostic.{name}] must be a table")
        edited = table.get("edited", [])
        if not isinstance(edited, list) or not all(isinstance(item, str) for item in edited):
            raise DiagnosticsError(f"{where}: edited is a list of field names")
        bindings.append(Binding(id=table["id"], diagnostic=table["diagnostic"], title=str(table.get("title") or table["id"]),
                                path=str(table.get("path") or ""), model_id=str(table.get("model_id") or ""), part=str(table.get("part") or ""),
                                state=str(table.get("state") or ""), window_s=float(window), config=dict(table.get("config") or {}),
                                columns={str(k): str(v) for k, v in (table.get("columns") or {}).items()}, edited=tuple(edited)))
    return bindings, specs


def dump(bindings: list[Binding], specs: list[dict], parts: list[PartRule] = ()) -> str:
    """The file's text; ``parse(tomllib.loads(dump(b, s, p)))`` gives the same bindings and specs back (and ``parse_parts`` the parts)."""

    lines = [HEADER.rstrip("\n")]
    for rule in parts:
        lines += ["", "[[part]]", f"type = {toml_value(rule.type)}"]
        lines += [f"{name} = {toml_value(list(getattr(rule, name)))}" for name in ("model_id", "path", "composite") if getattr(rule, name)]
    for table in specs:
        lines += ["", "[[diagnostic]]"] + [f"{_key(key)} = {toml_value(_plain(value))}" for key, value in table.items()]
    for binding in bindings:
        lines += ["", "[[diagnostic]]", f"id = {toml_value(binding.id)}", f"diagnostic = {toml_value(binding.diagnostic)}",
                  f"title = {toml_value(binding.title)}"]
        for name in ("path", "model_id", "part", "state"):
            if getattr(binding, name):
                lines.append(f"{name} = {toml_value(getattr(binding, name))}")
        lines.append(f"window_s = {toml_value(binding.window_s)}")
        if binding.edited:
            lines.append(f"edited = {toml_value(list(binding.edited))}")
        for name in ("config", "columns"):
            values = getattr(binding, name)
            if values:
                lines += ["", f"[diagnostic.{name}]"] + [f"{_key(key)} = {toml_value(_plain(value))}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


def _key(key: str) -> str:
    return key if re.match(r"^[A-Za-z0-9_-]+$", key) else json.dumps(key)


def _plain(value):
    """TOML has no null: a Limits table leaves its open sides out."""

    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


# ----- the dataclass as a form -----------------------------------------------------------------------------------------
def field_docs(config_type: type) -> dict[str, str]:
    """Each field's doc: the comment beside it in the dataclass's source (the library documents its fields that way)."""

    try:
        source = inspect.getsource(config_type)
    except (OSError, TypeError):
        return {}
    out = {}
    for line in source.splitlines():
        found = re.match(r"^\s+([a-z_][a-z0-9_]*)\s*:[^#]*#\s*(.+)$", line)
        if found:
            out[found[1]] = found[2].strip()
    return out


def _type_name(hint) -> str:
    if hint is dx_core.Limits:
        return "limits"
    if hint in (bool, int, float, str):
        return hint.__name__
    origin = typing.get_origin(hint)
    if origin is tuple:
        return "tuple"
    return str(hint)


def schema(config_type: type, unit: str = "") -> list[dict]:
    """The config dataclass as rows a page draws: field, type, default, unit, doc. A Limits field is four rows of type float|null,
    named ``<field>.<part>`` (what a page edits and POST /api/v1/diagnostics/config names)."""

    hints = typing.get_type_hints(config_type)
    docs = field_docs(config_type)
    defaults = config_type()
    value_unit = getattr(defaults, "unit", "") if isinstance(getattr(defaults, "unit", ""), str) else ""
    out = []
    for spec in dataclasses.fields(config_type):
        kind, default = _type_name(hints.get(spec.name)), getattr(defaults, spec.name)
        if kind == "limits":
            for part in LIMIT_PARTS:
                out.append({"field": f"{spec.name}.{part}", "type": "float|null", "default": getattr(default, part),
                            "unit": value_unit or unit, "doc": docs.get(spec.name, ""), "group": spec.name})
        else:
            out.append({"field": spec.name, "type": kind, "default": dx_core._canonical(default),
                        "unit": "", "doc": docs.get(spec.name, ""), "group": ""})
    return out


def _coerce(kind: str, value, default):
    """``value`` as the field's type, or raise ValueError with the reason a person reads."""

    if kind == "float|null":
        if value is None:
            return None
        kind = "float"
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{value!r} is not a number")
        return float(value)
    if kind == "int":
        if isinstance(value, bool) or not (isinstance(value, int) or (isinstance(value, float) and value.is_integer())):
            raise ValueError(f"{value!r} is not a whole number")
        return int(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{value!r} is not true or false")
        return value
    if kind == "str":
        if not isinstance(value, str):
            raise ValueError(f"{value!r} is not text")
        return value
    if kind == "tuple":
        if not isinstance(value, list) or len(value) != len(default):
            raise ValueError(f"{value!r} is not a list of {len(default)} values")
        return [_coerce(type(item).__name__ if not isinstance(item, bool) else "bool", got, item) for got, item in zip(value, default)]
    raise ValueError(f"a {kind} field is not editable here")


# ----- State history -> Trajectory ----------------------------------------------------------------------------------------
def trajectory(series: list[dict], *, columns: Mapping[str, str], run: str, device: str):
    """A ``data.Trajectory`` from one State group's history (``History.series``: ``[{key, unit, points: [[t, v], ...]}]``), on the
    union of its arrival times. ``q_rad`` is all NaN when the entity reports no position (the Trajectory needs one); a code column's
    gap holds the code before it; a column that is not one of the Trajectory's own (``gyro_rad_s``, ``width_mm``) goes in its ``extra``.
    Returns ``(trajectory, first arrival)``, or ``(None, None)`` when nothing maps."""

    from robotdx.diagnostics._numpy import np
    from robotdx.diagnostics.data import COLUMNS as FIELDS
    from robotdx.diagnostics.data import Trajectory

    by_key = {item["key"]: item for item in series}
    wanted = {column: by_key[key] for column, key in columns.items() if key in by_key and by_key[key]["points"]}
    if not wanted:
        return None, None
    times = sorted({point[0] for item in wanted.values() for point in item["points"]})
    at = {stamp: index for index, stamp in enumerate(times)}
    arrays, extra = {}, {}
    for column, item in wanted.items():
        values = np.full(len(times), np.nan)
        scale = TO_SI.get(item.get("unit") or "", 1.0) if column.endswith(("_rad", "_rad_s")) else 1.0
        for stamp, value in item["points"]:
            if isinstance(value, bool):
                value = float(value)
            if isinstance(value, (int, float)):
                values[at[stamp]] = float(value) * scale
        if column in CODE_COLUMNS:
            finite = np.isfinite(values)
            if not finite.any():
                continue
            index = np.where(finite, np.arange(len(values)), 0)
            np.maximum.accumulate(index, out=index)
            values = values[index]
            values[~np.isfinite(values)] = values[np.argmax(finite)]
        (arrays if column in FIELDS else extra)[column] = values
    first = times[0]
    t = np.asarray(times, float) - first
    arrays.setdefault("q_rad", np.full(len(times), np.nan))
    return Trajectory(t=t, run=run, device=device, situation="real", source="real", extra=extra, **arrays), first


# ----- what a State history can feed ------------------------------------------------------------------------------------------
def _target(column: str) -> str:
    from robotdx.diagnostics.data import target_of

    return target_of(column) or f"{column} target"


# The stock primitives a State history can feed (they take runs), and the columns each reads with its config
_STATE_NEEDS = {
    "stock.box": lambda c: (c.channel,),
    "stock.fault_codes": lambda c: ("fault",),
    "stock.tracking_error": lambda c: (c.channel, _target(c.channel)),
    "stock.step_response": lambda c: (c.channel, _target(c.channel)),
    "stock.stop_error": lambda c: ("q_rad", "q_target_rad", "qd_target_rad_s"),
    "stock.coverage": lambda c: (c.x, c.y),
    "stock.feedback_share": lambda c: ("tau_nm", "tau_ff_nm"),
    "stock.hysteresis_loop": lambda c: ("q_rad", "tau_nm"),
}


# WHY only these parts borrow a board's State (round 3, gap 2): a moteus board's State IS its joint's (position, torque, mode, fault) and
# its own encoder's reading; an IMU, FT sensor or gripper composite over a board is not that board, and borrowing would judge the wrong
# instrument (imu.rate on a board's State stamps). Only a servo has recorded runs of its own (the rack records joints).
LEAF_STATE_PARTS = ("servo", "encoder")
RUN_PARTS = ("servo",)


def _part_of(binding: Binding) -> str:
    entry = catalog_entry(binding.id)  # None for the person's own binding (dx_catalog.entry raises)
    return binding.part or (entry.part if entry is not None else "")


def state_fed(name: str) -> bool:
    """Whether a State history can ever feed diagnostic ``name``: a stock primitive over runs, or any custom diagnostic."""

    return name in _STATE_NEEDS or kind_of(name) == "custom"


def state_needs(name: str, config) -> tuple[str, ...]:
    """The Trajectory columns diagnostic ``name`` reads from the State with ``config``; () for one the State does not feed."""

    needs = _STATE_NEEDS.get(name)
    return tuple(needs(config)) if needs is not None else ()


def state_inputs_missing(name: str, config, run) -> str:
    """"" when ``run`` (a Trajectory from the State history) carries what diagnostic ``name`` reads, else what it lacks. A stock primitive
    that takes something other than runs (stamps, events, images, poses, a model's rows) is never fed by State: the whole input is missing.
    A custom diagnostic is always run."""

    needs = _STATE_NEEDS.get(name)
    if needs is None and kind_of(name) == "custom":
        return ""  # WHY: a custom diagnostic says nothing about its inputs; it gets the window and answers for itself, as before
    if needs is None:
        spec = dx_registry.REGISTRY.get(name)
        what = spec.summary if spec is not None and spec.summary else "its own input"
        return f"the input of {name} ({what})"
    from robotdx.diagnostics._numpy import np  # WHY after: "State cannot feed it" needs no numpy; only the column check does

    absent = [column for column in needs(config)
              if run is None or not run.has(column) or not np.isfinite(run.channel(column)).any()]
    return ", ".join(absent)


# ----- the typed inputs: what each binding is fed from (Codex audit 2026-10-29 r2, gap 1) -------------------------------------
@dataclass(frozen=True)
class Inputs:
    """What the host has on one entity beside its State history, gathered once per beat (host.py ``_stock_inputs``). Nothing here is
    decoded: ``image`` only says where a frame's file is, and the one binding that reads images opens it when it runs."""

    has_state: bool = False  # the entity has State history: a NotWired / NotMeasured row is shown only then (a board with no State posts no row)
    composite: bool = False  # a composite: its events, statuses and operating time are its leaves'
    plugin: bool = True  # someone reports State for it (a device's driver; a composite only with a composite plugin)
    state_arrivals: Mapping[str, Sequence[float]] = field(default_factory=dict)  # State group -> every record's arrival, not the 1 Hz history
    frames: Mapping[str, Sequence[Any]] = field(default_factory=dict)  # stream -> its frame records (contract Frame), oldest first
    image: Callable[[Any], Path | None] | None = None  # a frame record -> its image file in the frame directory, or None
    statuses: Sequence[Any] = ()  # the entity's and its leaves' statuses (not robotdx.diagnostics' own)
    configs: Sequence[Any] = ()  # the entity's config records (contract Config)
    events: Sequence[Any] = ()  # the activity log's events about the entity or its leaves (contract Event)
    members: Sequence[tuple[str, str, str | None, str]] = ()  # a composite's own children: (path, name, level, message)
    leaves: Sequence[str] = ()  # the entity paths below a composite (a device: none)
    operating: Sequence[tuple[float, float]] = ()  # (start, end) host clock: when the entity or its leaves were reporting
    # what a composite borrows (round 3, gap 2): nothing here is read unless a binding of the right part asks for it
    leaf_state: Mapping[str, list[dict]] = field(default_factory=dict)  # a composite over ONE device: that device's State history
    can_ids: Sequence[int] = ()  # the CAN ids of the boards below it (".../can/<id>"), which name its recorded runs and acceptance stations
    archive: "RackArchive | None" = None  # the rack's recordings on disk (runs, acceptance), None where the host has none
    computer: Sequence[dict] = ()  # the Computer plugin's CPU temperature as a State series (cpu_temperature), for rack.host
    realsense_serials: Sequence[str] = ()  # a camera's SDK serials (its statuses' realsense_serial values): names its captures
    action_runs: Sequence[Mapping[str, Any]] = ()  # the host's runs of an action on this entity (terminal.Run.to_json), newest last
    leaf_identity: Mapping[str, Any] = field(default_factory=dict)  # the one board under a composite: its identity (serial_number)
    leaf_configs: Sequence[Any] = ()  # ... and its config records (contract Config), e.g. motor.Kv


NONE = "none"


NOT_WIRED_GAP, NOT_MEASURED_GAP = "not_wired", "not_measured"
GAP_CODE = {NOT_WIRED_GAP: NOT_WIRED, NOT_MEASURED_GAP: NOT_MEASURED}


@dataclass(frozen=True)
class Source:
    """What one binding is fed from. ``kind``: state | state_stamps | frame_stamps | frames | reply_counts | config | faults | link_events |
    members | evidence | holds | acceptance | none. ``input``: what the diagnostic takes, in plain words. ``feeds``: where RobotDx has it, or
    (none, and a State input no entity reports) what would provide it. ``gap``: what its absence on an entity is: ``not_measured`` (no
    instrument on the entity can produce it) or ``not_wired`` (an existing producer has it or could publish it, and nothing maps it yet)."""

    kind: str
    input: str
    feeds: str
    gap: str = NOT_WIRED_GAP

    def json(self) -> dict:
        return {"kind": self.kind, "input": self.input, "from": "" if self.kind == NONE else self.feeds,
                "missing": self.feeds if self.kind == NONE else "", "gap": self.gap}


def _none(input_: str, why: str, gap: str) -> Source:
    return Source(NONE, input_, why, gap)


# WHY one row per catalog binding, not per primitive: one primitive means different things per part (stock.share is a hold, a lost reply,
# a grasp), so what feeds it is the binding's. Every live catalog binding is here (tests/test_stock_inputs.py lists them).
_DRIVER_STATE = "the driver's State (its watch, and each diagnose)"
_NO_DRIVER = "no {what} on RobotDx; a driver of one that reports {keys} in its State would provide it"
_RUN = ("a driver or runner that reports its commanded position{velocity} live, or the rack's recorded runs of this joint (bam_rack_twin "
        "npz in the rack archive), of which there is none")
_TAG = ("no fiducial tag is mounted in view and nothing detects one; a fixed tag and a detector that reports {what} would provide them")
_DEPTH_CAPTURE = ("the newest raw-depth capture of this camera in the rack archive (bam_ws intel_rgbd.depth_capture: "
                  "depth_<RealSense serial>_<UTC>.npz, N metric depth frames, intrinsics, depth scale)")
_FLAT = ("plane RMS is defined on a flat target at a measured distance filling the central 40 % (Intel's depth-quality method), and the "
         "newest capture of this camera is not tagged as one; a capture of a flat target with its distance (depth_capture --flat-target-mm) "
         "would provide it")
_SIM_CELL = "the newest UR5e sim cell (traj_integration_tests ur_compare: its traj_compare report's commanded and simulated trajectory)"
W, M = NOT_WIRED_GAP, NOT_MEASURED_GAP
SOURCES: dict[str, Source] = {
    # servo (a joint): its board's State, the rack's recorded runs of it, the fault memory
    "servo.tracking_rms": Source("state", "the position and its target", _RUN.format(velocity=" (q_target_rad)"), W),
    "servo.tracking_peak": Source("state", "the position and its target", _RUN.format(velocity=" (q_target_rad)"), W),
    "servo.stop_error": Source("state", "the position and its target at each stop",
                               _RUN.format(velocity=" and velocity (q_target_rad, qd_target_rad_s)"), W),
    "servo.hold_effort": Source("holds", "hold events", "the holds of the rack's newest recorded run of this joint (a hold converged when it "
                                "ends within 1 mrad of its commanded position)", W),
    "servo.torque_truth_kt": Source("kv_check", "reference Kv", "moteus_tool's calibration log of this very board (moteus-cal-<serial>-*.log, its "
                                    "measured kv) against the board's motor.Kv config", W),
    "servo.live_reid": Source("followup", "model error rows and a repeatability floor", "the newest calibration follow-up of this joint "
                              "(bam_rack_twin.calibration_followup/1: its declared pick's rows and floor) in the rack archive", W),
    "servo.mttf": Source("faults", "confirmed faults and operating time", "the fault memory's FaultConfirmed events on its members, and the "
                         "time they reported", W),
    # bldc_driver (a moteus board)
    "driver.faults": Source("state", "the mode and fault codes", _DRIVER_STATE, W),
    "driver.board_temp": Source("state", "the board temperature", _DRIVER_STATE, W),
    "driver.bus_voltage": Source("state", "the bus voltage", _DRIVER_STATE, W),
    "driver.loop_rate": Source("state_stamps", "State record stamps", "every State record the driver sends while its page watches it", W),
    "driver.link_loss": Source("reply_counts", "reply counts", "the driver's per-board status: queries and replies (moteus's diagnose)", W),
    "driver.config": Source("config", "expected configuration", "the driver's config records with an expected value (a deployment record)", W),
    "gearbox.wear": Source("wear", "re-identified friction against the commissioning card", "the newest calibration round of this joint "
                           "(bam_rack_twin.calibration_round/1) against its model card (bam-rack-twin/model-card-1, the measured terms) in "
                           "the rack archive", W),
    # encoder: a moteus reads its own (onboard or aux) encoder, so the one board under the encoder is its State
    # WHY NotMeasured (round 4): homing finds an offset against a hard stop or an index, and none is set up; nothing is missing in software
    "encoder.homing": _none("repeated homing offsets", "homing finds its offset against a hard stop or an index, and the rack's hard stops "
                            "are not set (its homing configs hold placeholders) and no homing run is recorded anywhere (the rack archive "
                            "holds no homing offsets); set hard stops and a homing routine that records each offset found would provide them", M),
    "encoder.standstill_noise": Source("state", "the position at rest", "the State of the one board under this encoder (its position), or "
                                       "an encoder driver that reports its position", W),
    "encoder.validity": Source("state", "the encoder's validity codes", "the State of the one board under this encoder (its fault code), or "
                               "an encoder driver that reports a fault code", W),
    # camera_rgb / camera_rgbd: the driver's State, frame records and images
    "camera.frame_rate": Source("captures", "per-frame stamps", "the newest recorded capture of this camera in the rack archive "
                                "(bam_rack_twin.hardware.record's cam_<RealSense serial>.npz: every frame's hardware clock, hw_ms)", W),
    "camera.latency": Source("frame_stamps", "frame stamps (the driver's and the host's)", "the driver's frame records: their stamp and when the "
                             "host received them", W),
    "camera.extrinsic_drift": _none("fixed tag detections", _TAG.format(what="the tag's pixel centre"), M),
    "camera.blur": Source("frames", "grey frames", "the newest image of the colour stream in the frame directory (JPEG or PNG)", W),
    "camera.tag_detect": _none("tag detections (found or not, per frame)", _TAG.format(what="each frame's result"), M),
    # WHY a raw-depth capture (round 5, Codex audit 2026-11-04 r5, gap 2): the watch's depth tile is a colourised preview; bam_ws
    # intel_rgbd.depth_capture writes N metric depth frames with intrinsics. Fill rate and temporal noise hold on any still scene; plane RMS
    # needs a flat target at a known distance, so it is NotMeasured (the physical gap) on a capture not tagged as one (_feed_depth)
    "depth.plane_rms": Source("depth_capture", "depth values of a flat target", _DEPTH_CAPTURE, W),
    "depth.fill_rate": Source("depth_capture", "raw depth frames", _DEPTH_CAPTURE, W),
    "depth.temporal_noise": Source("depth_capture", "raw depth frames", _DEPTH_CAPTURE, W),
    "depth.health_check": Source("action", "the on-chip calibration's health score", "intel_rgbd's calibrate run kept by this host "
                                 "(run_on_chip_calibration: the health before and after, and whether it was written)", W),
    # camera_hsi
    "hsi.snr": _none("repeated cubes of a uniform target", "no hyperspectral camera on RobotDx; a driver of one that records reference cubes "
                     "would provide them", M),
    "hsi.references": _none("white and dark reference captures", "no hyperspectral camera on RobotDx; a driver of one that reports when it "
                            "took its references would provide them", M),
    "hsi.spectral_angle": _none("measured and reference spectra", "no hyperspectral camera on RobotDx; a driver of one that reports known "
                                "materials' spectra beside a library would provide them", M),
    "hsi.frame_rate": _none("per-frame stamps", "no hyperspectral camera on RobotDx; a driver of one that reports each cube's stamp would "
                            "provide them", M),
    # imu
    "imu.static_bias": Source("state", "the gyro rate at rest", _NO_DRIVER.format(what="IMU", keys="gyro_rad_s"), M),
    "imu.gravity": _none("the accel magnitude at rest, paired with g", "no IMU on RobotDx; a driver of one that reports accel_m_s2 at rest "
                         "would provide it", M),
    "imu.rate": Source("state_stamps", "State record stamps", _NO_DRIVER.format(what="IMU", keys="its samples"), M),
    # ft_sensor
    "ft.known_mass": _none("known-mass sweeps (load on and off)", "no force-torque sensor and no weighed mass on RobotDx; a record of a "
                           "weighed mass's sweeps would provide them", M),
    "ft.zero_drift": Source("state", "the unloaded force", _NO_DRIVER.format(what="force-torque sensor", keys="force_n"), M),
    "ft.noise": Source("state", "the unloaded force", _NO_DRIVER.format(what="force-torque sensor", keys="force_n"), M),
    "ft.saturation": Source("state", "the sensor's status codes", _NO_DRIVER.format(what="force-torque sensor", keys="a fault code"), M),
    # gripper
    "gripper.cycle": Source("state", "the width and its target", _NO_DRIVER.format(what="gripper", keys="width_mm and width_target_mm"), M),
    "gripper.detect": _none("object-inside readings with ground truth", "no gripper on RobotDx, and no ground truth; a labelled pick "
                            "log would provide them", M),
    "gripper.grasp_success": _none("grasp outcomes", "no gripper or pick log on RobotDx; one that reports each grasp held or not would "
                                   "provide them", M),
    "gripper.compute_time": Source("state", "the grasp compute time", _NO_DRIVER.format(what="gripper or grasp planner", keys="compute_s"), M),
    "gripper.force": Source("state", "the force and its target", _NO_DRIVER.format(what="gripper", keys="force_n and force_target_n"), M),
    # arm: the arm plugin's State (joint angles, end effector x / y), its members
    # WHY the sim cell (round 4, Zach's brief: "the arm's commanded trajectory: from the UR5e sim cell"): no RobotDx arm reports a commanded
    # pose; the cell's report carries both, and every result says it is the sim cell's, so it is never read as the arm's own measurement
    "arm.tool_error_at_stop": Source("sim_cell", "the tool pose and its target at each stop", _SIM_CELL, W),
    "arm.path_tracking": Source("sim_cell", "the tool pose and its target along the path", _SIM_CELL, W),
    "arm.overshoot": Source("sim_cell", "the position and its target", _SIM_CELL, W),
    "arm.completion_time": Source("sim_cell", "planned and actual end of each trajectory", _SIM_CELL, W),
    "arm.hold_effort": Source("sim_cell", "the torque and its feed-forward", _SIM_CELL, W),
    "arm.calibration_drift": _none("tag detections on the tool", _TAG.format(what="the tool tag's pose"), M),
    "arm.joints": Source("members", "the joints' levels", "the composite's members as the tree shows them", W),
    # arm_gripper: the pick
    "pick.sppm": _none("pick outcomes", "no pick log on RobotDx; one that reports each pick and whether it held would provide them", M),
    "pick.vs_human": _none("a human's SPPM on the same stream", "a reference a person measures; recorded as a record it would provide it", M),
    "pick.rate": _none("pick events", "no pick log on RobotDx; one that reports each pick would provide them", M),
    "pick.mtti": _none("intervention events", "no operator or teleop log on RobotDx; one that reports each human intervention would provide them", M),
    "pick.grasp_pose_error": _none("the grasp pose and the TCP at closing", "no grasp planner or tool pose on RobotDx; one that reports both "
                                   "at closing would provide them", M),
    # deployment: the composite's members, their faults, links and evidence; the rack acceptance; the Computer it runs on
    "rack.gate_matrix": Source("acceptance", "gate results (T1-T8)", "the newest rack acceptance (bam_rack_twin.acceptance/1) in the rack "
                               "archive, for the stations under this composite", W),
    "rack.rollup": Source("members", "the stations' levels", "the composite's members as the tree shows them", W),
    "rack.evidence_age": Source("evidence", "statuses with their stamps", "every status of the composite's leaves, and their replug / restart events", W),
    "rack.uptime": Source("faults", "confirmed system faults and operating time", "the fault memory's FaultConfirmed events anywhere in the "
                          "composite, and the time its leaves reported", W),
    "rack.throughput": _none("material processed events", "no scale or belt counter on RobotDx; one that reports each batch would provide "
                             "them", M),
    "rack.host": Source("state", "the host's CPU temperature", "the Computer plugin's CPU temperature status (Temperature CPU) on this host, "
                        "or a composite plugin that reports cpu_temp_c", W),
    "rack.links": Source("link_events", "link events per edge", "the activity log's USB and network link events on the composite's leaves", W),
}
del W, M
# a binding that is not in the catalog (the person's own, a custom diagnostic): what its primitive is fed from on any entity
_BY_PRIMITIVE = {
    "stock.sample_rate": Source("state_stamps", "State record stamps", "every State record the driver sends while its page watches it"),
    "stock.stamp_delay": Source("frame_stamps", "frame stamps (the driver's and the host's)", "the driver's frame records"),
    "stock.image_sharpness": Source("frames", "grey frames", "the newest image of the colour stream in the frame directory (JPEG or PNG)"),
    "stock.rollup": Source("members", "the members' levels", "the composite's members as the tree shows them"),
    "stock.staleness": Source("evidence", "statuses with their stamps", "every status of the entity and its leaves"),
    "stock.event_interval": Source("faults", "confirmed faults and operating time", "the fault memory's FaultConfirmed events"),
}


def source_of(binding: Binding) -> Source:
    """What feeds ``binding``: its catalog row, else its primitive's (State for the ones over runs and every custom diagnostic)."""

    if binding.id in SOURCES and dx_catalog.entry(binding.id) is not None and dx_catalog.entry(binding.id).primitive == binding.diagnostic:
        return SOURCES[binding.id]
    if binding.diagnostic in _BY_PRIMITIVE:
        return _BY_PRIMITIVE[binding.diagnostic]
    if state_fed(binding.diagnostic):
        return Source("state", "State", _DRIVER_STATE)
    spec = dx_registry.REGISTRY.get(binding.diagnostic)
    return _none(f"the input of {binding.diagnostic}" + (f" ({spec.summary})" if spec is not None and spec.summary else ""),
                 "no RobotDx entity publishes it; it runs where that input is recorded (sim2real)", NOT_WIRED_GAP)


def quiet(status) -> bool:
    """Whether a stock status posts no row (it stays in the drill-in): not run until the entity streams, or NotWired / NotMeasured on an entity that
    reports no State at all."""

    return status.code in QUIET or (status.code in NO_SOURCE_CODES and any(key == QUIET_KEY for key, _value in status.values))


class _Missing(Exception):
    """The binding cannot run on this beat: ``code`` and the sentence a person reads."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _no_source(source: Source, entity: str, why: str = "") -> _Missing:
    what = source.input.removeprefix("the ")  # "no position and its target source", never "no the ..."
    code = GAP_CODE[source.gap]
    if source.kind == NONE:
        return _Missing(code, f"no {what} source on {entity}: {source.feeds}")
    feeds = source.feeds if source.feeds.endswith(("would provide it", "would provide them", "of which there is none")) \
        else f"{source.feeds} would provide it"
    return _Missing(code, f"no {what} source on {entity}: {why}; {feeds}")


def _in(start: float, end: float, stamp) -> bool:
    return stamp is not None and start <= stamp <= end


# ----- images: decoded on the beat (PNG with the stdlib's zlib and numpy, JPEG with Pillow), never on the watch path --------------------
class Undecodable(ValueError):
    """A frame this host cannot turn into pixels; the message says why."""


def _luma(pixels):
    """ITU-R BT.601 luma of an (H, W) or (H, W, 3) float array, as OpenCV's cvtColor (one formula for every format)."""

    if pixels.ndim == 2:
        return pixels
    return 0.299 * pixels[..., 0] + 0.587 * pixels[..., 1] + 0.114 * pixels[..., 2]


def _decode_jpeg(data: bytes):
    """A JPEG as a grey float array, decoded by Pillow.

    WHY Pillow, an optional extra, and not the driver's decoded frame or a hand-written decoder (Codex audit 2026-11-04 r3, gap 2): the
    configured intel_rgbd driver writes <stream>.jpg (entity_page.save_jpeg) and publishes no decoded pixels; neither the stdlib nor numpy
    decodes JPEG, and a baseline + progressive + chroma-subsampled decoder is exactly the stock part not to rewrite. The core stays stdlib +
    the ``diagnostics`` extra (numpy, Pillow; the Stable channel's /usr/bin/python3 has both); without Pillow a JPEG is NoDecoder."""

    try:
        import io

        from PIL import Image, UnidentifiedImageError
    except ImportError as error:
        raise Undecodable("the frame is JPEG and Pillow is not installed here (the diagnostics extra: uv run --extra diagnostics); "
                          "with it, or a driver that writes PNG frames, it would be decoded") from error
    from robotdx.diagnostics._numpy import np

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            grey = image.mode in ("L", "I;16", "I", "F")
            pixels = np.asarray(image.convert("L" if grey else "RGB"), float)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
        raise Undecodable(f"the JPEG does not decode ({type(error).__name__}: {error})") from error
    return _luma(pixels)


def decode_image(data: bytes, format: str = "png"):
    """A frame's file as a grey ``(H, W)`` float array (ITU-R BT.601 luma, as OpenCV's cvtColor). PNG: 8- or 16-bit grey, grey + alpha, RGB
    or RGBA, not interlaced, every filter (the stdlib's zlib and numpy). JPEG: Pillow (``_decode_jpeg``); without it, ``Undecodable``."""

    from robotdx.diagnostics._numpy import np

    if format in ("jpeg", "jpg") or data[:2] == b"\xff\xd8":
        return _decode_jpeg(data)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise Undecodable(f"the frame is not a PNG (format {format!r})")
    import struct
    import zlib

    offset, header, idat = 8, None, []
    while offset + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        offset += 12 + length
    if header is None or not idat:
        raise Undecodable("the PNG has no IHDR or no image data")
    width, height, depth, colour, _compression, _filter, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour)
    if channels is None or depth not in (8, 16) or interlace:
        raise Undecodable(f"a PNG of colour type {colour}, {depth}-bit{', interlaced' if interlace else ''}: only 8- or 16-bit grey / RGB "
                          "(+ alpha), not interlaced, is decoded here")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as error:
        raise Undecodable(f"the PNG's image data does not inflate ({error})") from error
    step, stride = channels * depth // 8, width * channels * depth // 8
    if len(raw) < height * (stride + 1):
        raise Undecodable("the PNG's image data is shorter than its size says")
    rows = np.frombuffer(raw, np.uint8, count=height * (stride + 1)).reshape(height, stride + 1)
    out = np.zeros((height, stride), np.uint8)
    previous = np.zeros(stride, np.uint8)
    for y in range(height):
        kind, line = rows[y, 0], rows[y, 1:]
        if kind == 0:
            current = line.copy()
        elif kind == 1:  # Sub: each byte plus the one a pixel to its left; a running sum per byte of the pixel, mod 256
            current = np.cumsum(line.reshape(-1, step), axis=0, dtype=np.uint8).reshape(-1)
        elif kind == 2:  # Up
            current = line + previous
        elif kind in (3, 4):  # Average, Paeth: each byte depends on the one just decoded, so byte by byte
            current = np.zeros(stride, np.uint8)
            for i in range(stride):
                left = int(current[i - step]) if i >= step else 0
                up = int(previous[i])
                if kind == 3:
                    current[i] = (int(line[i]) + (left + up) // 2) & 0xFF
                else:
                    corner = int(previous[i - step]) if i >= step else 0
                    estimate = left + up - corner
                    pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - corner)
                    best = left if pa <= pb and pa <= pc else up if pb <= pc else corner
                    current[i] = (int(line[i]) + best) & 0xFF
        else:
            raise Undecodable(f"the PNG row {y} has filter {kind}, which PNG does not define")
        out[y], previous = current, current
    pixels = out.reshape(height, width, channels * depth // 8)
    if depth == 16:
        pixels = pixels.reshape(height, width, channels, 2)
        pixels = (pixels[..., 0].astype(float) * 256 + pixels[..., 1]) / 257.0  # big-endian, onto the 8-bit scale
    else:
        pixels = pixels.astype(float)
    if channels in (1, 2):
        return pixels[..., 0]
    return _luma(pixels[..., :3])


# ----- the rack's recordings on disk: recorded runs and the acceptance (round 3, gap 2) ---------------------------------------------
ACCEPTANCE_SCHEMA = "bam_rack_twin.acceptance/1"
DEPTH_CAPTURE_SCHEMA = "intel_rgbd.depth_capture/1"  # bam_ws intel_rgbd.depth_capture.SCHEMA
_CAN_IN_NAME = re.compile(r"(?<![0-9A-Za-z])can(\d+)(?![0-9])")  # s02_can11_waypoints.npz -> 11; never "can1" inside "can11"
_CAN_IN_PATH = re.compile(r"/can/(\d+)$")  # a board's entity path: <adapter>/can/<id>


def can_id_of(path: str) -> int | None:
    found = _CAN_IN_PATH.search(path)
    return int(found[1]) if found else None


class RackArchive:
    """The rack's recordings, read-only: ``<root>/rack_<date>/<study>/runs/<name>.npz`` (+ ``.json`` sidecar, bam_rack_twin.hardware) and
    ``<root>/rack_<date>/analysis/acceptance*.json`` (bam_rack_twin.acceptance/1). The listing is re-read at most every ``refresh_s``; a
    loaded run or acceptance is kept until its file changes.

    WHY recorded runs feed a live monitor (Zach's brief, round 3: "wire every not_wired case ... from the rack's recorded runs"): the
    moteus board reports no commanded position, while every rack run records q_target / qd_target beside q; the newest run of a joint is
    the best evidence RobotDx has of how it tracks, and its window says when it was recorded, so it is never mistaken for now."""

    def __init__(self, root: Path | str | None, refresh_s: float = 60.0, *, calibrations: Path | str | None = None,
                 sim_cells: Path | str | None = None):
        # WHY three roots: the rack archive (bam_rack_twin's runs and analysis), moteus_tool's calibration logs
        # (~/.cache/moteus-firmware/calibrations) and the UR5e sim cell's reports (traj_integration_tests/out) are written by three producers
        self.root = Path(root) if root is not None else Path("/nonexistent-rack-archive")
        self.calibrations = Path(calibrations) if calibrations is not None else None
        self.sim_cells = Path(sim_cells) if sim_cells is not None else None
        self.refresh_s = refresh_s
        self._listed_at: float | None = None
        self._runs: dict[int, list[tuple[float, Path]]] = {}
        self._acceptance: list[Path] = []
        self._loaded: dict[Path, tuple[int, Any]] = {}
        self._lock = threading.Lock()

    def _list(self) -> None:
        import time

        now = time.monotonic()
        if self._listed_at is not None and now - self._listed_at < self.refresh_s:
            return
        runs: dict[int, list[tuple[float, Path]]] = {}
        for pattern in ("rack_*/*/runs/*.npz", "rack_*/runs/*.npz"):
            for file in self.root.glob(pattern):
                found = _CAN_IN_NAME.search(file.name)
                if not found:
                    continue
                try:
                    runs.setdefault(int(found[1]), []).append((file.stat().st_mtime, file))
                except OSError:
                    continue
        self._runs = {can_id: sorted(items, reverse=True) for can_id, items in runs.items()}
        self._acceptance = sorted(self.root.glob("rack_*/analysis/acceptance*.json"))
        self._analysis = sorted(self.root.glob("rack_*/analysis/*.json"))
        self._cards = sorted(self.root.glob("rack_*/analysis/model_cards/can*.json"))
        self._captures = sorted(self.root.glob("rack_*/**/cam_*.npz"))
        self._depth = sorted(self.root.glob("rack_*/**/depth_*.npz"))
        self._cal_logs = sorted(self.calibrations.glob("*/moteus-cal-*.log")) if self.calibrations is not None else []
        self._cells = sorted(self.sim_cells.glob("ur_compare_*/report.html")) if self.sim_cells is not None else []
        self._listed_at = now

    def _cached(self, file: Path, load):
        mtime = file.stat().st_mtime_ns
        held = self._loaded.get(file)
        if held is None or held[0] != mtime:
            held = self._loaded[file] = (mtime, load(file))
        return held[1]

    def newest_run(self, can_id: int):
        """The newest recorded run of the joint on CAN ``can_id`` as a Trajectory (robotdx's rack_npz adapter), or None. A run whose
        sidecar names another CAN id is skipped (the file name is only the index)."""

        from robotdx.diagnostics.adapters import rack_npz

        with self._lock:
            self._list()
            for _mtime, file in self._runs.get(can_id, []):
                try:
                    run = self._cached(file, rack_npz.load)
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    continue
                named = run.labels.get("can_id")
                if named is not None and int(named) != can_id:
                    continue
                return run
        return None

    def newest_acceptance(self) -> tuple[Path, dict] | None:
        """``(file, document)`` of the acceptance with the latest ``generated_at`` (never the newest mtime: a re-score of an old run is
        written later), or None."""

        def load(file: Path):
            try:
                document = json.loads(file.read_text())
            except (OSError, ValueError):
                return None
            ok = isinstance(document, dict) and document.get("schema") == ACCEPTANCE_SCHEMA and isinstance(document.get("stations"), dict)
            return document if ok else None

        with self._lock:
            self._list()
            found = []
            for file in self._acceptance:
                try:
                    document = self._cached(file, load)
                except OSError:
                    continue
                if document is not None:
                    found.append((str(document.get("generated_at") or ""), str(file), file, document))
        if not found:
            return None
        _stamp, _name, file, document = max(found)
        return file, document


    def _json_files(self, files, keep) -> list[tuple[Path, dict]]:
        out = []
        for file in files:
            try:
                document = self._cached(file, _read_json)
            except OSError:
                continue
            if isinstance(document, dict) and keep(document):
                out.append((file, document))
        return out

    def newest_followup(self, can_id: int) -> tuple[Path, dict] | None:
        """The newest bam_rack_twin.calibration_followup/1 of station ``can<id>`` (by scored_at), or None."""

        with self._lock:
            self._list()
            found = self._json_files(self._analysis, lambda d: d.get("schema") == "bam_rack_twin.calibration_followup/1"
                                     and d.get("station") == f"can{can_id}" and isinstance(d.get("variants"), dict))
        return max(found, key=lambda item: (str(item[1].get("scored_at") or ""), str(item[0])), default=None)

    def model_card(self, can_id: int) -> tuple[Path, dict] | None:
        """The newest model card of CAN ``can_id`` (bam-rack-twin/model-card-1, by file time), or None."""

        with self._lock:
            self._list()
            found = self._json_files([f for f in self._cards if f.name == f"can{can_id}.json"],
                                     lambda d: str(d.get("schema", "")).startswith("bam-rack-twin/model-card"))
        return max(found, key=lambda item: item[0].stat().st_mtime, default=None)

    def newest_calibration_round(self, can_id: int) -> tuple[Path, dict] | None:
        with self._lock:
            self._list()
            found = self._json_files(self._analysis, lambda d: d.get("schema") == "bam_rack_twin.calibration_round/1"
                                     and d.get("station") == f"can{can_id}" and isinstance(d.get("point"), dict))
        return max(found, key=lambda item: (str(item[1].get("identified_at") or ""), str(item[0])), default=None)

    def newest_capture(self, serial: str) -> Path | None:
        """The newest recorded capture of the RealSense with SDK serial ``serial`` (cam_<serial>.npz, by file time), or None."""

        with self._lock:
            self._list()
            found = [file for file in self._captures if file.name == f"cam_{serial}.npz"]
        try:
            return max(found, key=lambda file: (file.stat().st_mtime, str(file)), default=None)
        except OSError:
            return None

    def newest_depth_capture(self, serial: str):
        """The newest intel_rgbd.depth_capture/1 of RealSense ``serial`` (by its recorded_at) as ``(file, meta, depth_m)``, or None."""

        def load(file: Path):
            from robotdx.diagnostics._numpy import np

            try:
                with np.load(file) as archive:
                    meta = json.loads(str(archive["meta"]))
                    return (meta, np.asarray(archive["depth_m"], float)) if meta.get("schema") == DEPTH_CAPTURE_SCHEMA else None
            except (OSError, ValueError, KeyError):
                return None

        with self._lock:
            self._list()
            found = []
            for file in self._depth:
                if not file.name.startswith(f"depth_{serial}_"):
                    continue
                try:
                    loaded = self._cached(file, load)
                except OSError:
                    continue
                if loaded is not None and loaded[0].get("serial") == serial:
                    found.append((str(loaded[0].get("recorded_at") or ""), str(file), file, *loaded))
        if not found:
            return None
        _stamp, _name, file, meta, depth = max(found, key=lambda item: item[:2])
        return file, meta, depth

    def moteus_calibration(self, serial: str) -> tuple[Path, dict] | None:
        """The newest moteus_tool calibration log of the board with serial ``serial`` (its device_info, by its timestamp), or None."""

        with self._lock:
            self._list()
            found = self._json_files(self._cal_logs, lambda d: (d.get("device_info") or {}).get("serial_number") == serial
                                     and isinstance(d.get("kv"), (int, float)))
        return max(found, key=lambda item: (str(item[1].get("timestamp") or ""), str(item[0])), default=None)

    def newest_sim_cell(self) -> tuple[Path, dict] | None:
        """The newest UR5e sim cell report (ur_compare_*/report.html, by file time) and its traj_compare payload, or None."""

        with self._lock:
            self._list()
            cells = sorted(self._cells, key=lambda file: file.stat().st_mtime if file.exists() else 0.0, reverse=True)
            for file in cells:
                try:
                    payload = self._cached(file, _traj_compare_payload)
                except OSError:
                    continue
                if payload is not None:
                    return file, payload
        return None


def _read_json(file: Path):
    try:
        return json.loads(file.read_text())
    except (OSError, ValueError):
        return None


_PAYLOAD = re.compile(r'<script type="application/json" id="traj-compare-data">(.*?)</script>', re.S)


def _traj_compare_payload(file: Path):
    """The data a traj_compare report embeds (traj_compare.report: one JSON script, ``comparisons`` with their situations).
    WHY the report, not a second export: it is the sim cell's own output, and its payload holds the commanded and simulated joints, torques
    and tool poses, exactly what the arm bindings read."""

    found = _PAYLOAD.search(file.read_text(errors="replace"))
    if not found:
        return None
    try:
        payload = json.loads(found[1])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) and isinstance(payload.get("comparisons"), list) else None


# The Computer plugin's CPU temperature (plugins/computer: a "Temperature CPU (<chip>)" status, one measurement per sensor). WHY these
# keys first: Tctl (k10temp, AMD) and "Package id 0" (coretemp, Intel) are the die's control temperature, what its throttling acts on
CPU_STATUS_PREFIX = "Temperature CPU"
CPU_KEYS = ("Tctl", "Tdie", "Package id 0", "CPU")


def cpu_temperature(series: list[dict]) -> list[dict]:
    """The CPU status's measurement series as one State series ``cpu_temp_c`` (rack.host's channel), or [] when it has none."""

    with_points = [item for item in series if item.get("points")]
    if not with_points:
        return []
    chosen = next((item for key in CPU_KEYS for item in with_points if item["key"] == key), with_points[0])
    return [{**chosen, "key": "cpu_temp_c", "unit": chosen.get("unit") or "Cel"}]


HOLD_MIN_S = 0.5  # a hold is the target still for at least this long (the rack's waypoint dwells are 1-2 s)
HOLD_CONVERGED_RAD = 1e-3  # a hold converged when it ends within 1 mrad of its target (catalog servo.hold_effort)


def hold_events(run) -> list:
    """Every hold of a recorded run as a share Event: ok = it ended within 1 mrad of its commanded position. A hold is a stretch where the
    commanded velocity is zero and the commanded position does not move, for at least HOLD_MIN_S (tracking.stops' dwell, with the tape's
    last one included: a run ends on a hold that must converge too)."""

    from robotdx.diagnostics._numpy import np
    from robotdx.diagnostics.stock.events import Event as DxEvent

    keep = run.rows(skip_prelude=True)
    t, q, target = run.t[keep], run.channel("q_rad")[keep], run.channel("q_target_rad")[keep]
    velocity = run.channel("qd_target_rad_s")[keep] if run.has("qd_target_rad_s") else np.zeros_like(t)
    still = np.isfinite(target) & (np.abs(np.nan_to_num(velocity, nan=1.0)) < 1e-9)
    out, start, count = [], 0, len(t)
    while start < count:
        if not still[start]:
            start += 1
            continue
        end = start
        while end + 1 < count and still[end + 1] and abs(target[end + 1] - target[start]) < 1e-9:
            end += 1
        error = abs(float(q[end] - target[end]))
        if t[end] - t[start] >= HOLD_MIN_S and np.isfinite(error):
            out.append(DxEvent(t=float(t[end]), ok=error <= HOLD_CONVERGED_RAD, code="hold", evidence=run.run,
                               labels={"error_mrad": round(1000 * error, 3)}))
        start = end + 1
    return out


def _one_can_id(source, entity, inputs: Inputs) -> int:
    ids = sorted(set(inputs.can_ids))
    if len(ids) != 1:
        why = "it has no CAN board below it" if not ids else f"it has {len(ids)} boards below it (CAN {', '.join(map(str, ids))}), not one joint"
        raise _no_source(source, entity, why)
    return ids[0]


def _recorded_run(source, entity, inputs: Inputs):
    """The newest recorded run of the one joint below ``entity``, or ``_Missing`` saying why there is none."""

    can_id = _one_can_id(source, entity, inputs)
    if inputs.archive is None:
        raise _no_source(source, entity, "this host has no rack archive (IO_DIAGNOSTICS_RACK_ARCHIVE)")
    run = inputs.archive.newest_run(can_id)
    if run is None:
        raise _no_source(source, entity, f"the rack archive {inputs.archive.root} holds no recorded run of CAN {can_id}")
    return run, can_id


def _run_window(run, binding: Binding, samples) -> dict:
    wall = run.labels.get("wall_start")
    start = float(wall) if wall is not None else None
    return {"from": start, "to": start + float(run.t[-1] - run.t[0]) if start is not None else None, "window_s": float(run.t[-1] - run.t[0]),
            "state": binding.state, "samples": samples, "source": "recorded_run", "run": run.run}


# ----- one feed per source kind: (data, samples) or _Missing ------------------------------------------------------------------------
def _feed_state_stamps(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics._numpy import np
    from robotdx.diagnostics.stock.health import Stamps

    names = [binding.state] if binding.state else list(inputs.state_arrivals)
    arrivals = next((inputs.state_arrivals[name] for name in names if inputs.state_arrivals.get(name)), None)
    if arrivals is None:
        if inputs.composite and not inputs.plugin:
            raise _no_source(source, entity, "it reports no State of its own (a composite without a plugin)")
        if not inputs.has_state:
            raise _Missing(NO_STATE, f"no State on {entity} yet: its records' stamps are counted while the driver reports State")
        raise _no_source(source, entity, f"it reports no State group {binding.state!r}" if binding.state else "it reports no State records")
    stamps = [stamp for stamp in arrivals if _in(start, now, stamp)]
    if len(stamps) < 3:
        raise _Missing(NOT_STREAMING, f"{len(stamps)} State record(s) from {entity} in the last {binding.window_s:g} s: its rate is measured "
                                      "while its page watches it (the watch streams State)")
    return [Stamps(t=np.asarray(sorted(stamps), float) / config.stamps_scale, source_id=f"{entity} State")], len(stamps)


def _frame_records(inputs: Inputs, start, now) -> dict[str, list]:
    return {stream: [frame for frame in records if _in(start, now, frame.received)] for stream, records in inputs.frames.items()}


def _no_frames(source, entity, inputs: Inputs, window_s) -> _Missing:
    if inputs.composite and not inputs.frames:
        return _no_source(source, entity, "a composite has no image stream of its own")
    return _Missing(NOT_STREAMING, f"no frame from {entity} in the last {window_s:g} s: frames arrive while its page watches it")


def _feed_frame_stamps(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics._numpy import np
    from robotdx.diagnostics.stock.health import StampPairs

    streams = {stream: [frame for frame in records if frame.stamp is not None] for stream, records in _frame_records(inputs, start, now).items()}
    pairs = [StampPairs(source=np.asarray([frame.stamp for frame in records], float), received=np.asarray([frame.received for frame in records], float),
                        source_id=f"{entity} {stream}") for stream, records in streams.items() if records]
    if not pairs:
        raise _no_frames(source, entity, inputs, binding.window_s)
    return pairs, sum(len(records) for records in streams.values())


IMAGE_STREAMS = ("color", "colour", "rgb", "ir", "infrared")  # the streams whose pictures are the scene (a depth tile is a colour map)


def _feed_frames(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.camera import Frames

    streams = _frame_records(inputs, start, now)
    order = [name for name in IMAGE_STREAMS if streams.get(name)] + [name for name in streams if streams[name] and "depth" not in name]
    if not order:
        raise _no_frames(source, entity, inputs, binding.window_s)
    stream = order[0]
    newest = streams[stream][-1]
    file = inputs.image(newest) if inputs.image is not None else None
    if file is None or not Path(file).is_file():
        raise _Missing(NOT_STREAMING, f"the newest {stream} frame of {entity} is not in the frame directory any more")
    try:
        data = Path(file).read_bytes()
    except OSError as error:
        raise _Missing(NOT_STREAMING, f"the newest {stream} frame of {entity} could not be read ({error})") from error
    try:
        image = decode_image(data, newest.format or "jpeg")
    except Undecodable as error:
        raise _Missing(NO_DECODER, f"{entity}'s {stream} frame cannot be decoded here: {error}") from error
    return Frames(images=[image], t=[newest.stamp], source_id=f"{entity} {stream}"), 1


def _feed_reply_counts(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.events import Event as DxEvent

    def counts(status):
        values = {measurement.key: measurement.value for measurement in status.measurements}
        queries, replies = values.get("queries"), values.get("replies")
        ok = all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (queries, replies))
        return (int(queries), int(replies)) if ok and queries >= replies >= 0 and queries > 0 else None

    found = sorted((status for status in inputs.statuses if status.path == path and counts(status)),
                   key=lambda status: status.stamp if status.stamp is not None else 0.0)
    if not found:
        raise _no_source(source, entity, "none of its statuses reports queries and replies")
    newest = found[-1]
    queries, replies = counts(newest)
    stamp = newest.stamp if newest.stamp is not None else now
    # WHY one event per query: a share counts events; a lost reply is the true one (better = lower)
    lost = [DxEvent(t=stamp, ok=True, code="lost", evidence=newest.name) for _ in range(queries - replies)]
    return lost + [DxEvent(t=stamp, ok=False, code="reply") for _ in range(replies)], queries


def _feed_config(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.truthfulness import Pair

    pairs = [Pair(reported=item.value, reference=item.expected, key=item.key, evidence=item.expected_source or "")
             for item in inputs.configs if item.expected is not None]
    if not pairs:
        raise _no_source(source, entity, f"none of its {len(inputs.configs)} settings has an expected value (the driver gives them one from a "
                                         "deployment record)")
    return pairs, len(pairs)


FAULT_CODES = ("fault", "system_fault")  # what RobotDx's fault memory confirms: a fault; on a composite, a fault of the system too


def _feed_faults(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.events import Event as DxEvent
    from robotdx.diagnostics.stock.events import EventLog

    codes = tuple(getattr(config, "codes", ()) or ())
    known = ("fault", "system_fault") if inputs.composite else ("fault",)
    if codes and not set(codes) & set(known):
        raise _no_source(source, entity, f"RobotDx's fault memory confirms faults ({', '.join(known)} here), and nothing reports "
                                         f"{', '.join(codes)}")
    events = []
    for event in inputs.events:
        # WHY when the host received it: the operating time is the history's, on the host's clock; the event's own stamp is the
        # driver's, so a fault confirmed by a board's first report would land just before the time it was seen operating
        at = event.received if event.received is not None else event.stamp
        if event.code != "FaultConfirmed" or not _in(start, now, at):
            continue
        for code in (known if codes else known[:1]):
            events.append(DxEvent(t=at, ok=False, code=code, evidence=event.path))
    operating = [(max(a, start), min(b, now)) for a, b in inputs.operating if min(b, now) > max(a, start)]
    return EventLog(events=events, operating=operating), len(events)


LINK_CODES = ("Unplugged", "ReEnumerated", "Flapped", "LinkDowngraded", "NetCarrierDown", "PcieAerError")


def _feed_link_events(binding, config, source, path, entity, inputs: Inputs, start, now):
    from io_diagnostics import activity
    from robotdx.diagnostics.stock.events import Event as DxEvent

    edges = list(inputs.leaves) or [path]
    # WHY one event per edge that is not a problem: a share of nothing is not run, while a count of link problems with none is 0
    events = [DxEvent(t=now, ok=False, labels={"edge": edge}, code="edge") for edge in edges]
    events += [DxEvent(t=event.stamp, ok=True, labels={"edge": event.path}, code=event.code, evidence=event.path)
               for event in inputs.events if _in(start, now, event.stamp)
               and (event.code in LINK_CODES or activity.event_type(event.code) == "usb_error")]
    return events, len(events)


VERDICT_OF = {"ok": "pass", "warn": "warn", "error": "fail"}  # a member's level as a Result's verdict; unknown / stale / none: not run


def _feed_members(binding, config, source, path, entity, inputs: Inputs, start, now):
    if not inputs.composite:
        raise _no_source(source, entity, "it is not a composite, so it has no members")
    out = []
    for member, name, level, message in inputs.members:
        verdict = VERDICT_OF.get(level or "", "not_run")
        out.append(dx_core.Result(diagnostic="member", title=name or member, verdict=verdict, value=None, unit="1", subject=member,
                                  code="" if verdict == "pass" else f"Member{(level or 'none').capitalize()}", message=message))
    return out, len(out)


RESET_CODES = ("Plugged", "ReEnumerated", "Restarted")  # evidence before a replug or a restart no longer counts


def _feed_evidence(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.events import EvidenceStamps

    stamps = [(status.name, status.stamp) for status in inputs.statuses if status.stamp is not None and status.source != SOURCE]
    resets = [event.stamp for event in inputs.events if event.code in RESET_CODES and event.stamp is not None and event.stamp <= now]
    return EvidenceStamps(now=now, stamps=stamps, reset_at=max(resets) if resets else None), len(stamps)


def _feed_holds(binding, config, source, path, entity, inputs: Inputs, start, now):
    run, can_id = _recorded_run(source, entity, inputs)
    if not (run.has("q_target_rad") and run.has("q_rad")):
        raise _no_source(source, entity, f"CAN {can_id}'s newest recorded run has no commanded position")
    events = hold_events(run)
    return events, len(events), _run_window(run, binding, len(events))


GATE_VERDICT = {"pass": "pass", "fail": "fail", "partial": "warn"}  # the acceptance's status as a cell's verdict; not_run and others: not run


def _feed_acceptance(binding, config, source, path, entity, inputs: Inputs, start, now):
    ids = sorted(set(inputs.can_ids))
    if not ids:
        raise _no_source(source, entity, "it has no CAN board below it, so no acceptance station is its own")
    if inputs.archive is None:
        raise _no_source(source, entity, "this host has no rack archive (IO_DIAGNOSTICS_RACK_ARCHIVE)")
    found = inputs.archive.newest_acceptance()
    if found is None:
        raise _no_source(source, entity, f"the rack archive {inputs.archive.root} holds no {ACCEPTANCE_SCHEMA} file")
    file, document = found
    stations = {str(key): value for key, value in document["stations"].items()}
    mine = [can_id for can_id in ids if str(can_id) in stations]
    if not mine:
        raise _no_source(source, entity, f"the newest acceptance ({file.name}) has stations {', '.join(sorted(stations, key=int))}, and none is "
                                         f"below it (CAN {', '.join(map(str, ids))})")
    cells = []
    for can_id in mine:
        for gate in stations[str(can_id)].get("gates") or []:
            verdict = GATE_VERDICT.get(gate.get("status"), "not_run")
            value = gate.get("value")
            value = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None  # T11 writes "18/25"
            cells.append((f"CAN {can_id}", str(gate.get("id")), dx_core.Result(
                diagnostic="gate", title=str(gate.get("name") or gate.get("id")), verdict=verdict, value=value, unit="1",
                subject=f"CAN {can_id}", code="" if verdict == "pass" else f"Gate{str(gate.get('status')).title().replace('_', '')}",
                message=str(gate.get("detail") or ""))))
    window = {"from": None, "to": None, "window_s": None, "state": "", "samples": len(cells), "source": "acceptance", "file": str(file),
              "generated_at": document.get("generated_at"), "stations": [f"CAN {can_id}" for can_id in mine]}
    return cells, len(cells), window


def _feed_captures(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.adapters import rack_npz

    if inputs.archive is None:
        raise _no_source(source, entity, "this host has no rack archive (IO_DIAGNOSTICS_RACK_ARCHIVE)")
    if not inputs.realsense_serials:
        raise _no_source(source, entity, "its driver names no RealSense serial (realsense_serial) to find its captures by")
    for serial in inputs.realsense_serials:
        file = inputs.archive.newest_capture(serial)
        if file is None:
            continue
        stamps = rack_npz.camera_stamps(file)
        window = {"from": None, "to": None, "window_s": None, "state": "", "samples": int(len(stamps.t)), "source": "capture",
                  "file": str(file), "serial": serial}
        return [stamps], len(stamps.t), window
    raise _no_source(source, entity, f"the rack archive {inputs.archive.root} holds no capture of RealSense {', '.join(inputs.realsense_serials)}")


def _feed_depth(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.camera import DepthFrames

    if inputs.archive is None:
        raise _no_source(source, entity, "this host has no rack archive (IO_DIAGNOSTICS_RACK_ARCHIVE)")
    if not inputs.realsense_serials:
        raise _no_source(source, entity, "its driver names no RealSense serial (realsense_serial) to find its depth captures by")
    found = next((item for item in (inputs.archive.newest_depth_capture(serial) for serial in inputs.realsense_serials) if item), None)
    if found is None:
        raise _no_source(source, entity, f"the rack archive {inputs.archive.root} holds no depth_capture of RealSense "
                                         f"{', '.join(inputs.realsense_serials)}")
    file, meta, depth_m = found
    flat = meta.get("flat_target_mm")
    if config.headline == "plane_rms" and not (isinstance(flat, (int, float)) and flat > 0):
        raise _Missing(NOT_MEASURED, f"no flat target depth source on {entity}: its newest depth capture ({file.name}) is not tagged; {_FLAT}")
    intrinsics = meta.get("intrinsics") or {}
    data = DepthFrames(depth=1000.0 * depth_m, depth_unit_mm=1.0, fx=intrinsics.get("fx"), fy=intrinsics.get("fy"), cx=intrinsics.get("ppx"),
                       cy=intrinsics.get("ppy"), ground_truth_mm=float(flat) if isinstance(flat, (int, float)) else None, source_id=str(file))
    window = {"from": None, "to": None, "window_s": None, "state": "", "samples": int(depth_m.shape[0]), "source": "depth_capture",
              "file": str(file), "frames": int(depth_m.shape[0]), "recorded_at": meta.get("recorded_at"), "flat_target_mm": flat}
    return data, int(depth_m.shape[0]), window


def _feed_action(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.truthfulness import Pair

    for run in sorted(inputs.action_runs, key=lambda item: item.get("ended") or 0.0, reverse=True):
        for record in ((run.get("result") or {}).get("records") or []):
            args = record.get("args") or {}
            if record.get("code") != "ActionCompleted" or not all(isinstance(args.get(k), (int, float)) for k in ("health_before", "health_after")):
                continue
            # WHY before when not written: the camera keeps the stored calibration unless the new one was better (intel_rgbd)
            health = float(args["health_after"] if args.get("wrote") else args["health_before"])
            window = {"from": run.get("started"), "to": run.get("ended"), "window_s": None, "state": "", "samples": 1, "source": "action",
                      "run": run.get("id"), "wrote": bool(args.get("wrote"))}
            return [Pair(reported=health, reference=0.0, key="health", evidence=str(run.get("id")))], 1, window
    raise _no_source(source, entity, "no calibrate run of this camera is kept on this host (its Calibrate action has not run here)")


def _feed_kv(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.truthfulness import KtReadings

    serial = str(inputs.leaf_identity.get("serial_number") or "")
    kv = next((item.value for item in inputs.leaf_configs if item.key == "motor.Kv" and isinstance(item.value, (int, float))), None)
    if kv is None:
        raise _no_source(source, entity, "its board reports no motor.Kv config (the driver reads it with conf enumerate)")
    if not serial or inputs.archive is None:
        raise _no_source(source, entity, "its board names no serial number" if not serial else "this host has no calibration logs")
    found = inputs.archive.moteus_calibration(serial)
    if found is None:
        raise _no_source(source, entity, f"no moteus_tool calibration log of board {serial} in {inputs.archive.calibrations}")
    file, log = found
    window = {"from": None, "to": None, "window_s": None, "state": "", "samples": 1, "source": "calibration_log", "file": str(file),
              "timestamp": log.get("timestamp")}
    return KtReadings(kv_board_rpm_per_v=float(kv), kv_reference_rpm_per_v=float(log["kv"]), reference=str(file)), 1, window


def _feed_followup(binding, config, source, path, entity, inputs: Inputs, start, now):
    from robotdx.diagnostics.stock.fidelity import FidelityInput, ModelRow

    can_id = _one_can_id(source, entity, inputs)
    if inputs.archive is None:
        raise _no_source(source, entity, "this host has no rack archive (IO_DIAGNOSTICS_RACK_ARCHIVE)")
    found = inputs.archive.newest_followup(can_id)
    if found is None:
        raise _no_source(source, entity, f"the rack archive {inputs.archive.root} holds no calibration follow-up of CAN {can_id}")
    file, document = found
    variant = str(document.get("declared_pick") or "")
    rows = (document["variants"].get(variant) or {}).get("rows") or []
    floor = (document.get("floor") or {}).get("floor_mrad")
    model = [ModelRow(model="twin", run_id=str(row.get("run")), cls=str(row.get("class")), error=float(row["q_rms_mrad"]))
             for row in rows if isinstance(row.get("q_rms_mrad"), (int, float))]
    if not model or not isinstance(floor, (int, float)):
        raise _no_source(source, entity, f"{file.name} has no rows of its declared pick {variant!r} or no floor")
    window = {"from": None, "to": None, "window_s": None, "state": "", "samples": len(model), "source": "followup", "file": str(file),
              "variant": variant, "scored_at": document.get("scored_at")}
    return FidelityInput(floor=float(floor), rows=model, floor_kind="repeats", unit="mrad"), len(model), window


# the model card's terms a calibration round re-identifies, by the round's point keys
WEAR_TERMS = {"coulomb": "coulomb_nm", "viscous": "viscous_nm_s_rad"}


def _feed_wear(binding, config, source, path, entity, inputs: Inputs, start, now):
    import datetime as dt

    from robotdx.diagnostics.stock.truthfulness import Pair

    can_id = _one_can_id(source, entity, inputs)
    if inputs.archive is None:
        raise _no_source(source, entity, "this host has no rack archive (IO_DIAGNOSTICS_RACK_ARCHIVE)")
    card, round_ = inputs.archive.model_card(can_id), inputs.archive.newest_calibration_round(can_id)
    if card is None or round_ is None:
        raise _no_source(source, entity, f"the rack archive holds no {'model card' if card is None else 'calibration round'} of CAN {can_id}")
    (card_file, card_doc), (round_file, round_doc) = card, round_
    identified = str(round_doc.get("identified_at") or "")
    try:
        later = dt.datetime.fromisoformat(identified.replace("Z", "+00:00")).timestamp() > card_file.stat().st_mtime
    except ValueError:
        later = False
    if not later:
        raise _no_source(source, entity, f"CAN {can_id}'s newest calibration round ({identified or 'undated'}) is not after its model card")
    # WHY only measured terms: a card's refused or datasheet term was never this motor's own measurement, so it is no reference for wear
    pairs = [Pair(reported=float(round_doc["point"][key]), reference=float(term["value"]), key=term["term"], evidence=str(round_file))
             for term in card_doc.get("terms") or [] for name, key in WEAR_TERMS.items()
             if term.get("term") == name and term.get("status") == "measured" and isinstance(term.get("value"), (int, float))
             and isinstance(round_doc["point"].get(key), (int, float))]
    if not pairs:
        raise _no_source(source, entity, f"CAN {can_id}'s model card has no measured friction term to compare against")
    window = {"from": None, "to": None, "window_s": None, "state": "", "samples": len(pairs), "source": "model_card", "card": str(card_file),
              "round": str(round_file), "identified_at": identified}
    return pairs, len(pairs), window


SETTLE_RAD = 2e-3  # completion: every joint within 2 mrad of its final target from then on


def _sim_situations(payload: dict):
    """``[(comparison, target situation, judged situation)]``. WHY the first non-target situation: in ur_compare it is Sim A, the cell's
    nominal controller (feed-forward knows the payload); Sim B is its degraded A/B variant."""

    out = []
    for comparison in payload.get("comparisons") or []:
        situations = comparison.get("situations") or []
        target = next((item for item in situations if item.get("role") == "target"), None)
        judged = next((item for item in situations if item.get("role") != "target" and (item.get("plot") or {}).get("q")), None)
        if target is not None and judged is not None and (target.get("plot") or {}).get("q"):
            out.append((comparison, target, judged))
    return out


def _floats(values):
    """A payload array as floats: traj_compare writes a non-finite sample as null."""

    from robotdx.diagnostics._numpy import np

    def clean(item):
        return [clean(value) for value in item] if isinstance(item, (list, tuple)) else (np.nan if item is None else float(item))

    return np.asarray(clean(values), float)


def _sim_cell_data(binding: Binding, situations, file: Path):
    from robotdx.diagnostics._numpy import np
    from robotdx.diagnostics.data import Trajectory
    from robotdx.diagnostics.stock.iso9283 import PathCycle
    from robotdx.diagnostics.stock.truthfulness import Pair

    def joints(comparison, target, judged):
        t = _floats(comparison["plot_t"])
        q_target, q = _floats(target["plot"]["q"]), _floats(judged["plot"]["q"])
        tau_ff = _floats(target["plot"]["tau"]) if target["plot"].get("tau") else np.full_like(q_target, np.nan)
        tau = _floats(judged["plot"]["tau"]) if judged["plot"].get("tau") else np.full_like(q, np.nan)
        names = comparison.get("joint_names") or [f"joint {i}" for i in range(len(q))]
        for index in range(min(len(q), len(q_target))):
            # WHY the target's torque as the feed-forward: ur_compare's target torque is the plant's inverse dynamics, what Sim A feeds forward
            yield Trajectory(t=t, q_rad=q[index], q_target_rad=q_target[index], qd_target_rad_s=np.gradient(q_target[index], t),
                             tau_nm=tau[index], tau_ff_nm=tau_ff[index], run=f"{file}#{comparison.get('name')}/{names[index]}",
                             device=str(names[index]), situation=str(judged.get("name")), source="sim")

    def cartesian(comparison, judged):
        three = judged.get("three") or {}
        if not three.get("p") or not three.get("tp"):
            return None
        return _floats(three["t"]), _floats(three["p"]), _floats(three["tp"])

    if binding.id in ("arm.overshoot", "arm.hold_effort"):
        return [run for comparison, target, judged in situations for run in joints(comparison, target, judged)]
    if binding.id == "arm.tool_error_at_stop":
        runs = []
        for comparison, _target, judged in situations:
            found = cartesian(comparison, judged)
            if found is None:
                continue
            t, p, tp = found
            speed = np.linalg.norm(np.gradient(tp, t, axis=0), axis=1)
            # WHY a distance as the channel: the stop error is |p - p_target| (catalog: Cartesian, mm); its target is 0
            runs.append(Trajectory(t=t, q_rad=1000.0 * np.linalg.norm(p - tp, axis=1), q_target_rad=np.zeros_like(t),
                                   qd_target_rad_s=np.where(speed < 1e-9, 0.0, speed), run=f"{file}#{comparison.get('name')}/tool",
                                   device="tool", situation=str(judged.get("name")), source="sim", frame="base"))
        return runs
    if binding.id == "arm.path_tracking":
        # WHY one comparison: ISO 9283 path accuracy compares repeats of ONE path point by point; two tapes are two paths
        for comparison, _target, judged in situations:
            found = cartesian(comparison, judged)
            if found is not None:
                return [PathCycle(xyz_mm=1000.0 * found[1], target_xyz_mm=1000.0 * found[2], frame="base", run=f"{file}#{comparison.get('name')}")]
        return []
    if binding.id == "arm.completion_time":
        pairs = []
        for comparison, target, judged in situations:
            t = _floats(comparison["plot_t"])
            q_target, q = _floats(target["plot"]["q"]), _floats(judged["plot"]["q"])
            moving = np.where(np.max(np.abs(np.gradient(q_target, t, axis=1)), axis=0) > 1e-6)[0]
            if not len(moving) or moving[-1] >= len(t) - 1:
                continue  # never moves, or still moving when the tape ends (a conveyor track ends at belt speed): no planned end
            planned = int(moving[-1])
            off = np.max(np.abs(q - q_target[:, -1:]), axis=0) > SETTLE_RAD
            late = np.where(off)[0]
            settled = int(late[-1]) + 1 if len(late) else 0
            if settled >= len(t):
                continue  # never settles on the tape: no actual end to compare
            pairs.append(Pair(reported=float(t[max(settled, planned)] - t[0]), reference=float(t[planned] - t[0]),
                              key=str(comparison.get("name")), evidence=f"{file}#{comparison.get('name')}"))
        return pairs
    return None


def _feed_sim_cell(binding, config, source, path, entity, inputs: Inputs, start, now):
    if inputs.archive is None or inputs.archive.sim_cells is None:
        raise _no_source(source, entity, "this host has no UR5e sim cell reports (IO_DIAGNOSTICS_SIM_CELLS)")
    found = inputs.archive.newest_sim_cell()
    if found is None:
        raise _no_source(source, entity, f"{inputs.archive.sim_cells} holds no UR5e sim cell report (ur_compare_*/report.html)")
    file, payload = found
    situations = _sim_situations(payload)
    data = _sim_cell_data(binding, situations, file) if situations else None
    if data is None:
        raise _no_source(source, entity, f"the sim cell report {file} carries no {source.input.removeprefix('the ')}")
    # WHY an empty list goes on: the report is there and read; a tape with no stop or no planned end is the primitive's own not_run
    # (NoStops, NoMoves, no pair), which says what the tape lacks, never a missing source
    window = {"from": None, "to": None, "window_s": None, "state": "", "samples": len(data), "source": "sim_cell", "file": str(file),
              "situation": str(situations[0][2].get("name")), "comparisons": [str(c.get("name")) for c, _t, _j in situations]}
    return data, len(data), window


# WHY a feed may return a third item, its own window: a recorded run's or an acceptance's time is its own, not the last window_s of now
_FEEDS = {"state_stamps": _feed_state_stamps, "frame_stamps": _feed_frame_stamps, "frames": _feed_frames, "reply_counts": _feed_reply_counts,
          "holds": _feed_holds, "acceptance": _feed_acceptance, "captures": _feed_captures, "depth_capture": _feed_depth, "action": _feed_action, "kv_check": _feed_kv,
          "followup": _feed_followup, "wear": _feed_wear, "sim_cell": _feed_sim_cell, "config": _feed_config, "faults": _feed_faults, "link_events": _feed_link_events, "members": _feed_members, "evidence": _feed_evidence}


# ----- the runner --------------------------------------------------------------------------------------------------------
class StockDiagnostics:
    """The bindings (from the person's file, else the default set) and their evaluation. ``path`` None: no file of the person's (a host
    built by hand): a change is kept for this run only. ``defaults`` False: no default set, only what the person's file names (a fixture
    replay: a recorded frame is replayed as it was, without rows the recording never had)."""

    def __init__(self, path: Path | None, beat_s: float = BEAT_S, *, defaults: bool = True):
        self.path = Path(path) if path is not None else None
        self.beat_s = beat_s
        self.defaults = defaults
        self.bindings: list[Binding] = []
        self.specs: list[dict] = []
        self.parts: list[PartRule] = []
        self.source = ""  # the file the bindings came from
        self.problem = ""  # why the person's file is not used (its bindings are then none, never a guess)
        self._stamp: tuple | None = None
        self._lock = threading.RLock()
        self.reload_if_changed()

    # --- the file ------------------------------------------------------------------------------------------------------
    def _files(self) -> tuple[Path | None, list[Path]]:
        """(what ``source`` names, the files read in order): the person's file, else every part file of the default set."""

        if self.path is not None and self.path.is_file():
            return self.path, [self.path]
        if self.defaults:
            return DEFAULT_DIR, sorted(DEFAULT_DIR.glob("*.toml"))
        return None, []

    def reload_if_changed(self) -> None:
        source, files = self._files()
        if source is None:
            with self._lock:
                self.bindings, self.specs, self.parts, self.source, self.problem, self._stamp = [], [], [], "", "", None
            return
        stamp = []
        for file in files:
            try:
                stat = file.stat()
                stamp.append((str(file), stat.st_mtime_ns, stat.st_size))
            except OSError:
                stamp.append((str(file), None, None))
        stamp = tuple(stamp)
        with self._lock:
            if stamp == self._stamp:
                return
            self._stamp = stamp
            bindings, specs, parts, seen = [], [], [], set()
            try:
                for file in files:
                    document = tomllib.loads(file.read_text())
                    found, custom = parse(document)
                    twice = seen & {binding.id for binding in found}
                    if twice:
                        raise DiagnosticsError(f"id(s) {sorted(twice)} are used twice in the set")
                    seen |= {binding.id for binding in found}
                    bindings += found
                    specs += custom
                    parts += parse_parts(document)
                    if custom:
                        # WHY the registry's own loader: a custom diagnostic is declared like a stock one (registry.load_toml)
                        dx_registry.load_toml(file)
            except (OSError, tomllib.TOMLDecodeError, DiagnosticsError, ValueError, ImportError, AttributeError, TypeError) as error:
                self.bindings, self.specs, self.parts, self.source = [], [], [], str(source)
                self.problem = f"{file}: {type(error).__name__}: {error}"
                return
            self.bindings, self.specs, self.parts, self.source, self.problem = bindings, specs, parts, str(source), ""

    def _write(self, bindings: list[Binding]) -> None:
        if self.path is None:
            raise OSError("this host keeps no diagnostics.toml (no config directory)")
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(handle, "w") as file:
                file.write(dump(bindings, self.specs, self.parts))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            raise

    # --- which bindings an entity has ------------------------------------------------------------------------------------
    def parts_of(self, path: str, model_id: str = "", composite_kind: str = "") -> tuple[str, ...]:
        """The part types an entity is, by the [[part]] tables (an entity may be several: a RealSense is camera_rgb and camera_rgbd)."""

        with self._lock:
            return tuple(dict.fromkeys(rule.type for rule in self.parts if rule.matches(path, model_id or "", composite_kind or "")))

    def bindings_for(self, path: str, model_id: str, parts: tuple[str, ...] | None = None, composite_kind: str = "") -> list[Binding]:
        """The bindings that run on the entity at ``path``; ``parts`` None = resolve them from ``model_id`` / ``composite_kind``."""

        parts = self.parts_of(path, model_id, composite_kind) if parts is None else parts
        with self._lock:
            return [binding for binding in self.bindings if binding.matches(path, model_id, parts)]

    def build_config(self, binding: Binding):
        spec = dx_registry.REGISTRY.get(binding.diagnostic)
        if spec is None:
            raise LookupError(f"no diagnostic {binding.diagnostic!r} in the registry (GET /api/v1/diagnostics lists them)")
        return spec, dx_registry.config_from_dict(spec.config_type, binding.config)

    # --- one evaluation ------------------------------------------------------------------------------------------------
    def evaluate(self, binding: Binding, path: str, entity_name: str, groups: dict[str, list[dict]], now: float, hardware_id: str = "",
                 inputs: Inputs | None = None):
        """The newest window's status of ``binding`` on the entity at ``path``. ``groups``: its State history, ``{group name: series}``
        over at least the last ``2 x window_s + beat_s``; ``inputs``: everything else the entity publishes (``Inputs``; None = nothing but
        its State). Never raises: whatever goes wrong is a not_run status that says what."""

        inputs = inputs if inputs is not None else Inputs()
        # WHY a borrowed board's State counts (round 3): a servo or encoder over a watched board reports through it, so its gaps are rows
        has_state = inputs.has_state or bool(groups) or (bool(inputs.leaf_state) and _part_of(binding) in LEAF_STATE_PARTS)
        spec = dx_registry.REGISTRY.get(binding.diagnostic)
        window = None
        try:
            spec, config = self.build_config(binding)
            source = source_of(binding)
            if source.kind == "state":
                result, window = self._from_state(binding, spec, config, source, path, entity_name, groups, inputs)
            elif source.kind == "sim_cell" and binding.diagnostic in _STATE_NEEDS and (groups or inputs.leaf_state) and \
                    (live := self._from_state(binding, spec, config, source, path, entity_name, groups, inputs))[0].code not in (*NO_SOURCE_CODES, NO_STATE):
                # WHY the arm's own State first: an arm whose runner reports its commanded position or feed-forward is judged on itself;
                # the sim cell stands in only while it does not
                result, window = live
            elif source.kind == NONE:  # WHY first and without numpy: nothing RobotDx publishes carries it, so it is the lasting reason
                missing = _no_source(source, entity_name)
                result = self._not_run(binding, spec, missing.code, missing.message)
            else:
                start = now - binding.window_s
                try:
                    fed = _FEEDS[source.kind](binding, config, source, path, entity_name, inputs, start, now)
                except _Missing as missing:
                    result = self._not_run(binding, spec, missing.code, missing.message)
                else:
                    data, samples = fed[:2]
                    result = self._gated(binding, config, dx_registry.evaluate(binding.diagnostic, config, data, subject=path, stamp=now))
                    window = fed[2] if len(fed) > 2 else {"from": start, "to": now, "window_s": binding.window_s, "state": binding.state,
                                                          "samples": samples, "source": source.kind}
                    if window.get("source") == "sim_cell":  # WHY: a sim's number on an arm's row must say whose it is
                        result = dataclasses.replace(result, message=f"UR5e sim cell ({window['situation']}): {result.message}")
        except ModuleNotFoundError as error:
            result = self._not_run(binding, spec, NUMPY_MISSING_CODE, NUMPY_MISSING if error.name == "numpy" else f"{error}")
        except Exception as error:  # noqa: BLE001 - a diagnostic that raises is a not_run with its reason, never a crashed host
            result = self._not_run(binding, spec, DIAGNOSTIC_RAISED, f"{binding.diagnostic} raised {type(error).__name__}: {error}")
        entry = catalog_entry(binding.id)
        if entry is not None:  # the entry's own view: its data-ui hint, role and family (rack.uptime draws as a level_history)
            result = dataclasses.replace(result, hint=entry.the_hint, role=entry.role, family=entry.the_family,
                                         view_extra=entry.view_extra or result.view_extra)
        # WHY "<entity>: <title>": the page's row model folds "who: what" names into the entity's folder, beside its driver's own checks
        result = dataclasses.replace(result, title=f"{entity_name}: {binding.title}" if entity_name and entity_name != path else binding.title,
                                     subject=path)
        line = dx_record.to_record(result, hardware_id=hardware_id, expected_period_s=self.beat_s)
        line["values"] = [*line["values"], {"key": "dx.binding", "value": binding.id}]
        if window is not None:
            line["values"].append({"key": "dx.window", "value": json.dumps(window, separators=(",", ":"))})
        if result.code in NO_SOURCE_CODES:
            line["values"].append({"key": GAP_KEY, "value": next(gap for gap, code in GAP_CODE.items() if code == result.code)})
        if result.code in NO_SOURCE_CODES and not has_state:
            # WHY no row (Zach, 2026-10-28, calm UI): an entity that reports no State at all (an unwatched board, a composite without a
            # plugin) would carry a grey row per binding forever; the drill-in still lists it with this reason
            line["values"].append({"key": QUIET_KEY, "value": "no State on this entity"})
        status = from_dict(line)
        return dataclasses.replace(status, stamp=status.stamp if status.stamp is not None else now, received=now)

    def _gated(self, binding: Binding, config, result: dx_core.Result) -> dx_core.Result:
        if catalog_entry(binding.id) is not None and not dx_catalog.gating(binding.id, config):
            return dx_catalog.advisory(result, binding.id)  # an agent's or proposed limit shows, never gates
        return result

    def _from_state(self, binding: Binding, spec, config, source: Source, path: str, entity_name: str, groups, inputs: Inputs):
        """``(result, window)`` of a binding fed from the State history: the entity's own, else (round 3, gap 2) what it borrows, in order:
        the one board under a servo or encoder (``LEAF_STATE_PARTS``), the Computer's CPU temperature (rack.host), and for a servo the
        newest recorded run of its joint. Each borrowed window says which (``source``: state | leaf_state | computer | recorded_run)."""

        needs = state_needs(binding.diagnostic, config)
        # WHY the column's own name as a State key too: PEP 0010's names (q_target_rad, gyro_rad_s) are what a driver reports them as
        columns = {**COLUMNS, **{column: column for column in needs if column not in COLUMNS}, **binding.columns}
        part = _part_of(binding)
        candidates = [("state", self._pick(binding, columns, groups))]
        if part in LEAF_STATE_PARTS and inputs.leaf_state:
            candidates.append(("leaf_state", self._pick(binding, columns, inputs.leaf_state, named=False)))
        if inputs.computer and "cpu_temp_c" in needs:
            candidates.append(("computer", inputs.computer))
        absent, lacking = "", None
        for kind, series in candidates:
            if series is None:
                continue
            run, first = trajectory(series, columns=columns, run=f"history:{path}#{binding.state or kind}", device=entity_name)
            absent = state_inputs_missing(binding.diagnostic, config, run)
            if not absent:
                return self._monitor(binding, spec, config, path, run, first, kind)
            lacking = lacking or absent
        if part in RUN_PARTS:
            try:
                run, can_id = _recorded_run(source, entity_name, inputs)
            except _Missing as missing:
                if lacking is None and not groups and not inputs.leaf_state:
                    pass  # no State at all: the reasons below say so
                else:
                    return self._not_run(binding, spec, missing.code, missing.message), None
            else:
                absent = state_inputs_missing(binding.diagnostic, config, run)
                if absent:
                    missing = _no_source(source, entity_name, f"CAN {can_id}'s newest recorded run ({Path(run.run).name}) lacks {absent}")
                    return self._not_run(binding, spec, missing.code, missing.message), None
                result = self._gated(binding, config, dx_registry.evaluate(binding.diagnostic, config, [run], subject=path, stamp=None))
                result = dataclasses.replace(result, stamp=None)  # WHY: stamped when evaluated (now), the window carries the run's own time
                samples = next((m.value for m in result.measurements if m.key in ("n", "rows")), int(len(run.t)))
                return result, _run_window(run, binding, samples)
        if lacking is not None:
            missing = _no_source(source, entity_name, f"its State does not report {lacking}")
            return self._not_run(binding, spec, missing.code, missing.message), None
        wanted = ", ".join(needs) or "State"
        # WHY a named group not there yet is NoState: a driver reports a group when it has one (a board's watch group arrives with
        # the watch); only a group that is there and lacks the keys says the source is missing
        if groups and not (binding.state and binding.state not in groups):
            missing = _no_source(source, entity_name, f"its State does not report {wanted}")
            return self._not_run(binding, spec, missing.code, missing.message), None
        if inputs.composite and not inputs.plugin:
            missing = _no_source(source, entity_name, "it reports no State of its own (a composite without a plugin)")
            return self._not_run(binding, spec, missing.code, missing.message), None
        which = f"State group {binding.state!r}" if binding.state else "State"
        return self._not_run(binding, spec, NO_STATE, f"no {which} history on {entity_name} yet: it is kept while the driver reports State"), None

    @staticmethod
    def _pick(binding: Binding, columns, groups, named: bool = True):
        """The first State group (``binding.state`` when ``named``) that carries any of ``columns``' keys, or None."""

        names = [binding.state] if binding.state and named else list(groups)
        return next((groups[name] for name in names if name in groups and any(item["key"] in columns.values() for item in groups[name])), None)

    def _monitor(self, binding: Binding, spec, config, path: str, run, first, kind: str):
        from robotdx.diagnostics import stream as dx_stream

        pairs = dx_stream.monitor(binding.diagnostic, config, run, subject=path, window_s=binding.window_s, step_s=self.beat_s,
                                  wall_start=first)
        if not pairs:
            held, samples = float(run.t[-1] - run.t[0]), int(len(run.t))
            return self._not_run(binding, spec, NOT_ENOUGH_HISTORY,
                                 f"the State history holds {held:.0f} s ({samples} samples); this diagnostic looks at a {binding.window_s:g} s window"
                                 " with at least 2 samples"), None
        result = self._gated(binding, config, pairs[-1][0])  # the newest window: stamped with its end, on the host's clock
        window_end = result.stamp
        return result, {"from": window_end - binding.window_s, "to": window_end, "window_s": binding.window_s, "state": binding.state,
                        "samples": next((m.value for m in result.measurements if m.key in ("n", "rows")), None), "source": kind}

    @staticmethod
    def _not_run(binding: Binding, spec, code: str, message: str) -> dx_core.Result:
        out = dx_core.result(dict(binding.config), value=None, unit=spec.unit if spec else "", metric="value", limits=dx_core.Limits(),
                             title=binding.title, message=message, code=code)
        if spec is None:
            return dataclasses.replace(out, diagnostic=binding.diagnostic)
        return dataclasses.replace(out, diagnostic=spec.name, family=spec.family, role=spec.role, hint=spec.hint, emits=spec.emits,
                                   view_extra=spec.view_extra)

    # --- the drill-in --------------------------------------------------------------------------------------------------
    def fields_json(self, binding: Binding) -> list[dict]:
        spec = dx_registry.REGISTRY.get(binding.diagnostic)
        if spec is None:
            return []
        try:
            _spec, config = self.build_config(binding)
        except (ValueError, TypeError, LookupError):
            config = spec.config_type()
        rows = []
        for row in schema(spec.config_type, spec.unit):
            name, _, part = row["field"].partition(".")
            value = getattr(config, name)
            value = getattr(value, part) if part else dx_core._canonical(value)
            source = ("edited" if row["field"] in binding.edited or name in binding.edited
                      else "toml" if name in binding.config else "default")
            editable = row["type"] in ("float|null", "float", "int", "bool", "str", "tuple")
            rows.append({**row, "value": value, "source": source, "editable": editable})
        return rows

    # --- the API's shapes ---------------------------------------------------------------------------------------------
    def about(self) -> dict:
        """Where the bindings come from and where a change goes: ``file`` (read), ``writes`` (the person's file, or None), ``problem``."""

        from robotdx.diagnostics._numpy import available

        with self._lock:
            return {"file": self.source, "writes": str(self.path) if self.path is not None else None, "problem": self.problem,
                    "defaults": self.defaults, "beat_s": self.beat_s, "numpy": available()}

    def binding_json(self, binding: Binding) -> dict:
        spec = dx_registry.REGISTRY.get(binding.diagnostic)
        entry = catalog_entry(binding.id)
        return {"id": binding.id, "diagnostic": binding.diagnostic, "title": binding.title,
                "summary": spec.summary if spec else "", "doc": config_doc(spec.config_type) if spec else "",
                "kind": kind_of(binding.diagnostic), "match": {"path": binding.path, "model_id": binding.model_id, "part": binding.part},
                "state": binding.state, "window_s": binding.window_s, "edited": list(binding.edited),
                "catalog": ({"part": entry.part, "checks": entry.checks, "provenance": entry.prov, "quote": entry.quote, "where": entry.where,
                             "hint": entry.the_hint, "role": entry.role, "gate": entry.gate,
                             "limits_provenance": dx_catalog.default(entry.id).limits, "limits_source": dx_catalog.default(entry.id).source,
                             "gating": self._gating(binding)}
                            if entry else None),
                "source": source_of(binding).json(), "fields": self.fields_json(binding)}

    def _gating(self, binding: Binding) -> bool:
        try:
            return dx_catalog.gating(binding.id, self.build_config(binding)[1])
        except (ValueError, TypeError, LookupError):
            return dx_catalog.gating(binding.id)

    def set_field(self, binding_id: str, field_name: str, value=None, *, reset: bool = False) -> dict:
        """Validate one field against the dataclass (its type, the Limits ordering, the config's own construction) and write the whole
        file atomically. ``{"state": "accepted", "value": <read back from the file>}`` or ``{"state": "rejected", "message"}``."""

        with self._lock:
            binding = next((b for b in self.bindings if b.id == binding_id), None)
            if binding is None:
                return {"state": "rejected", "refused": "NoSuchDiagnostic", "message": f"no diagnostic {binding_id!r} is configured"}
            spec = dx_registry.REGISTRY.get(binding.diagnostic)
            if spec is None:
                return {"state": "rejected", "refused": "NoSuchDiagnostic", "message": f"{binding.diagnostic} is not in the registry"}
            rows = {row["field"]: row for row in schema(spec.config_type, spec.unit)}
            if field_name not in rows:
                return {"state": "rejected", "refused": "NoSuchField",
                        "message": f"{spec.config_type.__name__} has no field {field_name!r} (fields: {', '.join(rows)})"}
            row, (name, _, part) = rows[field_name], field_name.partition(".")
            values = {key: (dict(item) if isinstance(item, dict) else item) for key, item in binding.config.items()}
            try:
                if reset:
                    new = row["default"]
                else:
                    new = _coerce(row["type"], value, row["default"])
                if part:
                    current = values.get(name)
                    limits = dict(current) if isinstance(current, dict) else dataclasses.asdict(getattr(spec.config_type(), name))
                    limits[part] = new
                    dx_core.Limits(**{key: item for key, item in limits.items() if key in LIMIT_PARTS})  # the ordering, error_low <= ... <= error_high
                    values[name] = {key: limits.get(key) for key in LIMIT_PARTS}
                elif reset:
                    values.pop(name, None)
                else:
                    values[name] = new
                dx_registry.config_from_dict(spec.config_type, values)  # the config as a whole must still build
            except (ValueError, TypeError) as error:
                return {"state": "rejected", "refused": "BadValue", "message": str(error), "field": field_name, "id": binding_id}
            edited = [item for item in binding.edited if item != field_name]
            if not reset:
                edited.append(field_name)
            updated = dataclasses.replace(binding, config=values, edited=tuple(edited))
            bindings = [updated if b.id == binding_id else b for b in self.bindings]
            saved, note = True, ""
            try:
                self._write(bindings)
            except OSError as error:
                # WHY accepted, not rejected: the value is valid and runs from the next beat, like a setting kept for this run when the
                # home is read-only (settings.py); the answer says it is not saved, and why
                saved, note = False, f"{self.path or 'diagnostics.toml'}: not written ({type(error).__name__}: {error})"
            if saved:
                self._stamp = None
                self.reload_if_changed()  # the read-back is the file's, as the host now reads it
            else:
                self.bindings = bindings
            back = next((b for b in self.bindings if b.id == binding_id), None)
            read = next((r for r in self.fields_json(back) if r["field"] == field_name), None) if back else None
            return {"state": "accepted", "id": binding_id, "field": field_name, "value": read["value"] if read else None,
                    "source": read["source"] if read else None, "saved": saved, "note": note,
                    "file": str(self.path) if saved else None, "reset": "default" if reset else None}


def config_doc(config_type: type) -> str:
    """The config dataclass's own docstring; "" for the signature a dataclass writes when it has none."""

    doc = inspect.getdoc(config_type) or ""
    return "" if doc.startswith(f"{config_type.__name__}(") else doc


def kind_of(name: str) -> str:
    from robotdx.diagnostics.stock import STOCK

    spec = dx_registry.REGISTRY.get(name)
    return "stock" if spec is not None and any(spec is stock for stock in STOCK) else "custom"


def registry_json() -> list[dict]:
    """``GET /api/v1/diagnostics``: every registered diagnostic, stock and custom, with its config dataclass as a schema."""

    out = []
    for name, spec in sorted(dx_registry.REGISTRY.items()):
        out.append({"id": name, "title": spec.title, "summary": spec.summary, "kind": kind_of(name), "family": spec.family, "role": spec.role,
                    "hint": spec.hint, "emits": spec.emits, "unit": spec.unit, "consumers": list(spec.consumers), "view_extra": spec.view_extra,
                    "part_types": catalog_parts(name), "config_type": spec.config_type.__name__, "doc": config_doc(spec.config_type),
                    "config": schema(spec.config_type, spec.unit)})
    return out


def catalog_parts(name: str) -> list[str]:
    """The catalog's part types that use the primitive ``name`` (robotdx/diagnostics/catalog.py)."""

    from robotdx.diagnostics.catalog import ENTRIES

    return sorted({entry.part for entry in ENTRIES if entry.primitive == name})


def catalog_entry(binding_id: str):
    from robotdx.diagnostics.catalog import ENTRIES

    return next((entry for entry in ENTRIES if entry.id == binding_id), None)


def window_of(status) -> dict | None:
    for key, value in status.values:
        if key == "dx.window":
            try:
                return json.loads(value)
            except ValueError:
                return None
    return None


def binding_of(status) -> str:
    return next((value for key, value in status.values if key == "dx.binding"), "")


__all__ = ["StockDiagnostics", "Binding", "PartRule", "parse_parts", "state_inputs_missing", "NOT_WIRED", "NOT_MEASURED", "NO_SOURCE_CODES",
           "GAP_KEY", "RackArchive", "cpu_temperature", "NO_STATE", "NOT_STREAMING", "NO_DECODER", "QUIET", "is_not_run",
           "Inputs", "Source", "SOURCES", "source_of", "quiet", "decode_image", "DEFAULT_DIR", "DiagnosticsError", "parse", "dump", "schema", "field_docs", "trajectory", "SOURCE",
           "DIAGNOSTICS_FILE", "BEAT_S", "registry_json", "config_doc", "kind_of", "catalog_parts", "catalog_entry", "window_of", "binding_of"]
