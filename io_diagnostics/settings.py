"""App-wide settings the page can change, remembered in ``~/.config/io-diagnostics/settings.toml``.

One setting so far: the history window (docs/api-v1.md "Settings"), the rolling window every health bar and history
answer covers. Where a value comes from, first that applies: ``serve --history-window`` (this run only, never written),
the file, the default (15 min). ``POST /api/v1/settings`` from the page sets it for this run AND writes the file.
WHY the flag is not written: like ``--enable``, a run-time switch must not silently become the person's preference.
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from pathlib import Path

from io_diagnostics.history import DEFAULT_WINDOW_S, MAX_WINDOW_S, MIN_WINDOW_S

SETTINGS_FILE = "settings.toml"
HEADER = ("# IO diagnostics · app settings. The page's View options rewrite this file whole (comments are not kept).\n"
          "# history_window_s: the rolling window of every health bar and history, in seconds (60 .. 86400).\n")
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


class Settings:
    """``path=None``: memory only (tests, a host built by hand). ``history_window_s`` given: this run's override."""

    def __init__(self, path: Path | None, history_window_s: float | None = None):
        self.path = Path(path) if path else None
        self.history_window_s = DEFAULT_WINDOW_S
        self.source = "default"  # default | file | flag | page
        self.note = ""  # why the file could not be read or written
        if self.path and self.path.exists():
            try:
                data = tomllib.loads(self.path.read_text())
                if "history_window_s" in data:
                    self.history_window_s = check_window(parse_duration(data["history_window_s"]))
                    self.source = "file"
            except (OSError, tomllib.TOMLDecodeError, SettingsError) as error:
                self.note = f"{self.path}: unreadable ({type(error).__name__}: {error}); using the default"
        if history_window_s is not None:
            self.history_window_s, self.source = check_window(history_window_s), "flag"

    def set_history_window(self, seconds) -> None:
        """The page's change: applied now and written (atomically). Raises SettingsError for a value out of range."""

        self.history_window_s, self.source = check_window(parse_duration(seconds)), "page"
        if self.path is None:
            return
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(handle, "w") as file:
                file.write(HEADER + f"history_window_s = {self.history_window_s:g}\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.note = ""
        except OSError as error:  # a read-only home keeps the value for this run; the reason shows in the answer
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            self.note = f"{self.path}: not written ({type(error).__name__}: {error})"

    def json(self) -> dict:
        return {"history_window_s": self.history_window_s, "source": self.source,
                "file": str(self.path) if self.path else None, "note": self.note,
                "limits": {"history_window_s": {"min": MIN_WINDOW_S, "max": MAX_WINDOW_S, "default": DEFAULT_WINDOW_S}}}


__all__ = ["Settings", "SettingsError", "SETTINGS_FILE", "parse_duration", "check_window"]
