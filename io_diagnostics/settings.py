"""App-wide settings the page can change, remembered in ``~/.config/io-diagnostics/settings.toml``.

Three settings: the history window (docs/api-v1.md "Settings"), the rolling window every health bar and history
answer covers, the Visibility choices (Zach, 2026-10-24: a row the person hid or showed, kept with the app's
settings so both channels and the tray agree), the page's dashboard (the tiles a person pinned from sparklines: the chart view c4) and
the depth colour map (Zach, 2026-10-24: "in the settings of this app please give the option to set the color map for the depth map").
Where the history window comes from, first that applies: ``serve --history-window`` (this run only, never written),
the file, the default (15 min). ``POST /api/v1/settings`` from the page sets it for this run AND writes the file.
WHY the flag is not written: like ``--enable``, a run-time switch must not silently become the person's preference.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

from io_diagnostics.history import DEFAULT_WINDOW_S, MAX_WINDOW_S, MIN_WINDOW_S

SETTINGS_FILE = "settings.toml"
HEADER = ("# IO diagnostics · app settings. The page's View options rewrite this file whole (comments are not kept).\n"
          "# history_window_s: the rolling window of every health bar and history, in seconds (60 .. 86400).\n"
          "# dashboard: the tiles pinned from sparklines, as JSON text (the page owns its shape).\n"
          "# [visibility]: a row's id (its hardware id, else its path) = \"hide\" | \"show\" | \"ack\" (hidden, and its error acknowledged).\n"
          "# depth_colormap: realsense-viewer's colour scheme names (Jet, Classic, White to Black, ..., Hue, or Turbo); depth_equalize: histogram\n"
          "# equalization; depth_min_m / depth_max_m: the colours' reach in metres, used when equalization is off.\n")
VISIBILITY_CHOICES = ("hide", "show", "ack")
# The depth colour map: the names of realsense-viewer's "Color Scheme" (the SDK's rs.colorizer option color_scheme, values
# 0..9 in this order; verified against librealsense src/proc/colorizer.cpp, 2026-10-24), then "Turbo", which the SDK lacks
# and the RealSense driver keeps from before this setting. WHY the default is Jet with equalization on, 0 .. 6 m: those are
# the colorizer's own defaults and realsense-viewer sets none of its own (it only narrows a D405 to 0.05 .. 4 m).
DEPTH_COLORMAPS = ("Jet", "Classic", "White to Black", "Black to White", "Bio", "Cold", "Warm", "Quantized", "Pattern", "Hue", "Turbo")
DEFAULT_DEPTH_COLORMAP = "Jet"
DEFAULT_DEPTH_EQUALIZE = True
DEFAULT_DEPTH_MIN_M, DEFAULT_DEPTH_MAX_M = 0.0, 6.0
DEPTH_RANGE_LIMIT_M = 16.0  # the colorizer's min / max distance option range
DEPTH_KEYS = ("depth_colormap", "depth_equalize", "depth_min_m", "depth_max_m")
MAX_VISIBILITY = 500
MAX_KEY = 200
MAX_TILES = 60
MAX_DASHBOARD_BYTES = 32768
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr)?\s*$", re.I)
_UNIT_S = {"": 1.0, "s": 1.0, "sec": 1.0, "m": 60.0, "min": 60.0, "h": 3600.0, "hr": 3600.0}


class SettingsError(ValueError):
    """A value the host refuses (the reason is the page's message)."""


def parse_duration(text) -> float:
    """``900``, ``"900"``, ``"90s"``, ``"15m"``, ``"1h"`` -> seconds."""

    if isinstance(text, bool):
        raise SettingsError(f"{text!r} is not a duration")
    if isinstance(text, (int, float)):
        return float(text)
    found = _DURATION.match(str(text))
    if not found:
        raise SettingsError(f"{text!r} is not a duration: seconds, or a number with s, m or h (15m, 1h)")
    return float(found[1]) * _UNIT_S[(found[2] or "").lower()]


def check_window(seconds: float) -> float:
    if not MIN_WINDOW_S <= seconds <= MAX_WINDOW_S:
        raise SettingsError(f"the history window is {MIN_WINDOW_S:g} s .. {MAX_WINDOW_S:g} s (1 min .. 24 h), got {seconds:g} s")
    return float(seconds)


def check_dashboard(value) -> list:
    """The page's dashboard: a list of tiles (objects, each with a ``spec`` object), at most ``MAX_TILES`` and
    ``MAX_DASHBOARD_BYTES`` of JSON. The host stores it and never reads inside a tile: the page owns the shape."""

    if not isinstance(value, list):
        raise SettingsError("the dashboard is a list of tiles")
    if len(value) > MAX_TILES:
        raise SettingsError(f"the dashboard holds at most {MAX_TILES} tiles, got {len(value)}")
    for tile in value:
        if not isinstance(tile, dict) or not isinstance(tile.get("spec"), dict):
            raise SettingsError("every dashboard tile is an object with a spec object")
    if len(json.dumps(value, separators=(",", ":"))) > MAX_DASHBOARD_BYTES:
        raise SettingsError(f"the dashboard is over {MAX_DASHBOARD_BYTES} bytes of JSON")
    return value


def _check_depth(key: str, value):
    """One depth colour map value, checked and normalised (a colour map name to its listed spelling)."""

    if key == "depth_colormap":
        wanted = str(value).strip().lower().replace("_", " ")
        for name in DEPTH_COLORMAPS:
            if name.lower() == wanted:
                return name
        raise SettingsError(f"depth_colormap {value!r} is not one of {', '.join(DEPTH_COLORMAPS)}")
    if key == "depth_equalize":
        if not isinstance(value, bool):
            raise SettingsError(f"depth_equalize is true or false, got {value!r}")
        return value
    if key in ("depth_min_m", "depth_max_m"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= DEPTH_RANGE_LIMIT_M:
            raise SettingsError(f"{key} is metres from 0 to {DEPTH_RANGE_LIMIT_M:g}, got {value!r}")
        return float(value)
    raise SettingsError(f"no depth setting {key!r}")


class Settings:
    """``path=None``: memory only (tests, a host built by hand). ``history_window_s`` given: this run's override."""

    def __init__(self, path: Path | None, history_window_s: float | None = None):
        self.path = Path(path) if path else None
        self.history_window_s = DEFAULT_WINDOW_S
        self.visibility: dict[str, str] = {}
        self.dashboard: list = []
        self.depth_colormap = DEFAULT_DEPTH_COLORMAP
        self.depth_equalize = DEFAULT_DEPTH_EQUALIZE
        self.depth_min_m, self.depth_max_m = DEFAULT_DEPTH_MIN_M, DEFAULT_DEPTH_MAX_M
        self.source = "default"  # default | file | flag | page
        self.note = ""  # why the file could not be read or written
        if self.path and self.path.exists():
            try:
                data = tomllib.loads(self.path.read_text())
                if "history_window_s" in data:
                    self.history_window_s = check_window(parse_duration(data["history_window_s"]))
                    self.source = "file"
                if "dashboard" in data:
                    self.dashboard = check_dashboard(json.loads(data["dashboard"]))
                if isinstance(data.get("visibility"), dict):
                    self.visibility = {str(key): value for key, value in data["visibility"].items()
                                       if value in VISIBILITY_CHOICES and 0 < len(str(key)) <= MAX_KEY}
                self._read_depth(data)
            except (OSError, tomllib.TOMLDecodeError, SettingsError, json.JSONDecodeError, TypeError) as error:
                self.note = f"{self.path}: unreadable ({type(error).__name__}: {error}); using the default"
        if history_window_s is not None:
            self.history_window_s, self.source = check_window(history_window_s), "flag"

    def _read_depth(self, data: dict) -> None:
        """The depth colour map keys of the file; a key that is not valid keeps its default (the others are still read)."""

        for key in DEPTH_KEYS:
            if key not in data:
                continue
            try:
                setattr(self, key, _check_depth(key, data[key]))
            except SettingsError as error:
                self.note = f"{self.path}: {error}; using the default"
        if not self.depth_min_m < self.depth_max_m:
            self.depth_min_m, self.depth_max_m = DEFAULT_DEPTH_MIN_M, DEFAULT_DEPTH_MAX_M

    def depth(self) -> dict:
        """The depth colour map choices, as the host passes them to a watch (``Host._watch_env``) and the page shows them."""

        return {key: getattr(self, key) for key in DEPTH_KEYS}

    def set_depth(self, changes: dict) -> bool:
        """The page's depth colour map choices: any of ``depth_colormap`` (a name of ``DEPTH_COLORMAPS``, any case),
        ``depth_equalize`` (bool), ``depth_min_m`` / ``depth_max_m`` (metres, min below max). Applied now and written. Returns True
        when something changed (a running watch must restart to use it). Raises SettingsError; nothing changes then."""

        merged = self.depth()
        for key, value in changes.items():
            merged[key] = _check_depth(key, value)
        if not merged["depth_min_m"] < merged["depth_max_m"]:
            raise SettingsError(f"the depth range needs a minimum below its maximum, got {merged['depth_min_m']:g} m .. {merged['depth_max_m']:g} m")
        changed = merged != self.depth()
        for key, value in merged.items():
            setattr(self, key, value)
        if changed:
            self._write()
        return changed

    def set_history_window(self, seconds) -> None:
        """The page's change: applied now and written (atomically). Raises SettingsError for a value out of range."""

        self.history_window_s, self.source = check_window(parse_duration(seconds)), "page"
        self._write()

    def set_dashboard(self, tiles) -> None:
        """The page's dashboard: applied now and written. Raises SettingsError for a value the host refuses."""

        self.dashboard = check_dashboard(tiles)
        self._write()

    def set_visibility(self, changes) -> None:
        """The page's Visibility choices: ``{row id: "hide" | "show" | "ack" | None}`` (None forgets the choice). Raises
        SettingsError for anything else."""

        if not isinstance(changes, dict):
            raise SettingsError("visibility is an object: {row id: hide | show | ack | null}")
        merged = dict(self.visibility)
        for key, value in changes.items():
            if not isinstance(key, str) or not 0 < len(key) <= MAX_KEY:
                raise SettingsError(f"a visibility key is a row id of 1..{MAX_KEY} characters")
            if value is None:
                merged.pop(key, None)
            elif value in VISIBILITY_CHOICES:
                merged[key] = value
            else:
                raise SettingsError(f"visibility {key!r}: {value!r} is not one of {', '.join(VISIBILITY_CHOICES)}")
        if len(merged) > MAX_VISIBILITY:
            raise SettingsError(f"at most {MAX_VISIBILITY} visibility choices are kept")
        self.visibility = merged
        self._write()

    def _write(self) -> None:
        if self.path is None:
            return
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(handle, "w") as file:
                file.write(HEADER + f"history_window_s = {self.history_window_s:g}\n")
                # WHY json.dumps as the TOML string, and before [visibility]: its escapes (\", \\, \n, \uXXXX with ensure_ascii)
                # are all valid TOML ones, and a top-level key must come before the first table
                file.write(f"dashboard = {json.dumps(json.dumps(self.dashboard, separators=(',', ':')))}\n")
                file.write(f"depth_colormap = {json.dumps(self.depth_colormap)}\ndepth_equalize = {json.dumps(self.depth_equalize)}\n"
                           f"depth_min_m = {self.depth_min_m:g}\ndepth_max_m = {self.depth_max_m:g}\n")
                if self.visibility:
                    file.write("\n[visibility]\n")
                    for key, value in sorted(self.visibility.items()):
                        file.write(f"{json.dumps(key, ensure_ascii=False)} = {json.dumps(value)}\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.note = ""
        except OSError as error:  # a read-only home keeps the value for this run; the reason shows in the answer
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            self.note = f"{self.path}: not written ({type(error).__name__}: {error})"

    def json(self) -> dict:
        return {"history_window_s": self.history_window_s, "visibility": dict(self.visibility), "dashboard": self.dashboard, **self.depth(),
                "source": self.source,
                "file": str(self.path) if self.path else None, "note": self.note,
                "limits": {"history_window_s": {"min": MIN_WINDOW_S, "max": MAX_WINDOW_S, "default": DEFAULT_WINDOW_S},
                           "depth_colormap": {"choices": list(DEPTH_COLORMAPS), "default": DEFAULT_DEPTH_COLORMAP},
                           "depth_equalize": {"default": DEFAULT_DEPTH_EQUALIZE},
                           "depth_range_m": {"min": 0.0, "max": DEPTH_RANGE_LIMIT_M, "default": [DEFAULT_DEPTH_MIN_M, DEFAULT_DEPTH_MAX_M]}}}


__all__ = ["Settings", "SettingsError", "SETTINGS_FILE", "DEPTH_COLORMAPS", "DEPTH_KEYS", "parse_duration", "check_window", "check_dashboard"]
