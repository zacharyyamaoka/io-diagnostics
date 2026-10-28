"""The host runs robotdx.diagnostics: which stock diagnostic runs on which entity, on a slow beat over that entity's State history,
each Result an ordinary record-v0.2 status on the entity (docs/api-v1.md "Stock diagnostics").

- **Which, where, with what config:** ``<config dir>/diagnostics.toml`` (``[[diagnostic]]`` tables: ``id``, ``diagnostic``, a ``path``
  and / or ``model_id`` match, ``[diagnostic.config]`` values), else the host's default set (``diagnostics_default.toml``). The file is
  read with the registry's ``load_toml`` (a table with ``function`` registers a custom diagnostic) and re-read when it changes.
  A fixture replay runs only the person's file, never the default set (a recording is replayed as it was).
- **When:** every ``BEAT_S`` (5 s) on a thread of its own, never on the 10 Hz watch path. Each binding reads the last ``window_s`` of
  the entity's State history (history.py keeps it at 1 Hz at most) through ``stream.monitor`` and keeps the newest window's Result.
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
from typing import Any, Mapping

from contract.record import from_dict
from io_diagnostics.composite import toml_value
from robotdx.diagnostics import core as dx_core
from robotdx.diagnostics import record as dx_record
from robotdx.diagnostics import registry as dx_registry
from robotdx.diagnostics._numpy import MISSING as NUMPY_MISSING

DIAGNOSTICS_FILE = "diagnostics.toml"
DEFAULT_FILE = Path(__file__).with_name("diagnostics_default.toml")
SOURCE = dx_record.SOURCE  # "robotdx.diagnostics": the status's source, which the page keys its drill-in on
BEAT_S = 5.0
DEFAULT_WINDOW_S = 60.0
MIN_WINDOW_S, MAX_WINDOW_S = 2.0, 86400.0
LIMIT_PARTS = ("warn_low", "warn_high", "error_low", "error_high")
HEADER = ("# robotdx.diagnostics on this host (io_diagnostics/stock_diagnostics.py; the default set and every key explained:\n"
          "# io_diagnostics/diagnostics_default.toml). The entity page's drill-in rewrites this file whole (comments are not kept);\n"
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
    state: str = ""  # the State group it reads; "" = the first with a mapped column
    window_s: float = DEFAULT_WINDOW_S
    config: Mapping[str, Any] = field(default_factory=dict)  # the file's values (plain data)
    columns: Mapping[str, str] = field(default_factory=dict)  # Trajectory column -> State key, beside COLUMNS
    edited: tuple[str, ...] = ()  # fields the page changed ("max.warn_high" for one part of a Limits)

    def matches(self, path: str, model_id: str) -> bool:
        if self.path and not fnmatch.fnmatchcase(path, self.path):
            return False
        if self.model_id and not fnmatch.fnmatchcase(model_id or "", self.model_id):
            return False
        return bool(self.path or self.model_id)


_BINDING_KEYS = {"id", "diagnostic", "title", "path", "model_id", "state", "window_s", "config", "columns", "edited"}


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
        if not table.get("path") and not table.get("model_id"):
            raise DiagnosticsError(f"{where}: says neither path nor model_id, so it matches no entity")
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
                                path=str(table.get("path") or ""), model_id=str(table.get("model_id") or ""),
                                state=str(table.get("state") or ""), window_s=float(window), config=dict(table.get("config") or {}),
                                columns={str(k): str(v) for k, v in (table.get("columns") or {}).items()}, edited=tuple(edited)))
    return bindings, specs


def dump(bindings: list[Binding], specs: list[dict]) -> str:
    """The file's text; ``parse(tomllib.loads(dump(b, s)))`` gives the same bindings and specs back."""

    lines = [HEADER.rstrip("\n")]
    for table in specs:
        lines += ["", "[[diagnostic]]"] + [f"{_key(key)} = {toml_value(_plain(value))}" for key, value in table.items()]
    for binding in bindings:
        lines += ["", "[[diagnostic]]", f"id = {toml_value(binding.id)}", f"diagnostic = {toml_value(binding.diagnostic)}",
                  f"title = {toml_value(binding.title)}"]
        for name in ("path", "model_id", "state"):
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
    gap holds the code before it. Returns ``(trajectory, first arrival)``, or ``(None, None)`` when nothing maps."""

    from robotdx.diagnostics._numpy import np
    from robotdx.diagnostics.data import Trajectory

    by_key = {item["key"]: item for item in series}
    wanted = {column: by_key[key] for column, key in columns.items() if key in by_key and by_key[key]["points"]}
    if not wanted:
        return None, None
    times = sorted({point[0] for item in wanted.values() for point in item["points"]})
    at = {stamp: index for index, stamp in enumerate(times)}
    arrays = {}
    for column, item in wanted.items():
        values = np.full(len(times), np.nan)
        scale = TO_SI.get(item.get("unit") or "", 1.0) if column in ("q_rad", "qd_rad_s") else 1.0
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
        arrays[column] = values
    first = times[0]
    t = np.asarray(times, float) - first
    arrays.setdefault("q_rad", np.full(len(times), np.nan))
    return Trajectory(t=t, run=run, device=device, situation="real", source="real", **arrays), first


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
        self.source = ""  # the file the bindings came from
        self.problem = ""  # why the person's file is not used (its bindings are then none, never a guess)
        self._stamp: tuple | None = None
        self._lock = threading.RLock()
        self.reload_if_changed()

    # --- the file ------------------------------------------------------------------------------------------------------
    def _file(self) -> Path | None:
        if self.path is not None and self.path.is_file():
            return self.path
        return DEFAULT_FILE if self.defaults else None

    def reload_if_changed(self) -> None:
        file = self._file()
        if file is None:
            with self._lock:
                self.bindings, self.specs, self.source, self.problem, self._stamp = [], [], "", "", None
            return
        try:
            stat = file.stat()
            stamp = (str(file), stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = (str(file), None, None)
        with self._lock:
            if stamp == self._stamp:
                return
            self._stamp = stamp
            try:
                text = file.read_text()
                bindings, specs = parse(tomllib.loads(text))
                if specs:
                    # WHY the registry's own loader: a custom diagnostic is declared like a stock one (registry.load_toml)
                    dx_registry.load_toml(file)
            except (OSError, tomllib.TOMLDecodeError, DiagnosticsError, ValueError, ImportError, AttributeError, TypeError) as error:
                self.bindings, self.specs, self.source = [], [], str(file)
                self.problem = f"{file}: {type(error).__name__}: {error}"
                return
            self.bindings, self.specs, self.source, self.problem = bindings, specs, str(file), ""

    def _write(self, bindings: list[Binding]) -> None:
        if self.path is None:
            raise OSError("this host keeps no diagnostics.toml (no config directory)")
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(handle, "w") as file:
                file.write(dump(bindings, self.specs))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            raise

    # --- which bindings an entity has ------------------------------------------------------------------------------------
    def bindings_for(self, path: str, model_id: str) -> list[Binding]:
        with self._lock:
            return [binding for binding in self.bindings if binding.matches(path, model_id)]

    def build_config(self, binding: Binding):
        spec = dx_registry.REGISTRY.get(binding.diagnostic)
        if spec is None:
            raise LookupError(f"no diagnostic {binding.diagnostic!r} in the registry (GET /api/v1/diagnostics lists them)")
        return spec, dx_registry.config_from_dict(spec.config_type, binding.config)

    # --- one evaluation ------------------------------------------------------------------------------------------------
    def evaluate(self, binding: Binding, path: str, entity_name: str, groups: dict[str, list[dict]], now: float, hardware_id: str = ""):
        """The newest window's status of ``binding`` on the entity at ``path``. ``groups``: its State history, ``{group name: series}``
        over at least the last ``2 x window_s + beat_s``. Never raises: whatever goes wrong is a not_run status that says what."""

        spec = dx_registry.REGISTRY.get(binding.diagnostic)
        window = None
        try:
            spec, config = self.build_config(binding)
            columns = {**COLUMNS, **binding.columns}
            names = [binding.state] if binding.state else list(groups)
            series = next((groups[name] for name in names if name in groups and any(item["key"] in columns.values() for item in groups[name])), None)
            if series is None:
                which = f"State group {binding.state!r}" if binding.state else "State"
                result = self._not_run(binding, spec, "NoState", f"no {which} history on {entity_name} yet: it is kept while the driver reports State")
            else:
                from robotdx.diagnostics import stream as dx_stream

                run, first = trajectory(series, columns=columns, run=f"history:{path}#{binding.state or 'state'}", device=entity_name)
                pairs = [] if run is None else dx_stream.monitor(binding.diagnostic, config, run, subject=path, window_s=binding.window_s,
                                                                 step_s=self.beat_s, wall_start=first)
                if not pairs:
                    held = 0.0 if run is None else float(run.t[-1] - run.t[0])
                    samples = 0 if run is None else int(len(run.t))
                    result = self._not_run(binding, spec, "NotEnoughHistory",
                                           f"the State history holds {held:.0f} s ({samples} samples); this diagnostic looks at a {binding.window_s:g} s window"
                                           " with at least 2 samples")
                else:
                    result = pairs[-1][0]  # the newest window: stamped with its end, on the host's clock
                    window_end = result.stamp
                    window = {"from": window_end - binding.window_s, "to": window_end, "window_s": binding.window_s,
                              "state": binding.state, "samples": next((m.value for m in result.measurements if m.key in ("n", "rows")), None)}
        except ModuleNotFoundError as error:
            result = self._not_run(binding, spec, "NumpyMissing", NUMPY_MISSING if error.name == "numpy" else f"{error}")
        except Exception as error:  # noqa: BLE001 - a diagnostic that raises is a not_run with its reason, never a crashed host
            result = self._not_run(binding, spec, "DiagnosticRaised", f"{binding.diagnostic} raised {type(error).__name__}: {error}")
        result = dataclasses.replace(result, title=binding.title, subject=path)
        line = dx_record.to_record(result, hardware_id=hardware_id, expected_period_s=self.beat_s)
        line["values"] = [*line["values"], {"key": "dx.binding", "value": binding.id}]
        if window is not None:
            line["values"].append({"key": "dx.window", "value": json.dumps(window, separators=(",", ":"))})
        status = from_dict(line)
        return dataclasses.replace(status, stamp=status.stamp if status.stamp is not None else now, received=now)

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
            try:
                self._write(bindings)
            except OSError as error:
                return {"state": "rejected", "refused": "NotWritten", "message": f"{self.path}: not written ({type(error).__name__}: {error})"}
            self._stamp = None
            self.reload_if_changed()
            back = next((b for b in self.bindings if b.id == binding_id), None)
            read = next((r for r in self.fields_json(back) if r["field"] == field_name), None) if back else None
            return {"state": "accepted", "id": binding_id, "field": field_name, "value": read["value"] if read else None,
                    "source": read["source"] if read else None, "file": str(self.path), "reset": "default" if reset else None}


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


__all__ = ["StockDiagnostics", "Binding", "DiagnosticsError", "parse", "dump", "schema", "field_docs", "trajectory", "SOURCE",
           "DIAGNOSTICS_FILE", "DEFAULT_FILE", "BEAT_S", "catalog_parts", "catalog_entry", "window_of", "binding_of"]
