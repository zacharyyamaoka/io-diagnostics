"""How a person arranges the entity page's rows: pinned rows, manual order, folded folders (docs/state-config-v1.md section 10).

One arrangement per entity KIND (servo, camera, adapter, host...) and section (diagnostics, state, config), so every CAN board
shares one. Kept in ``arrangements.json`` beside the app's ``settings.toml`` (``~/.config/io-diagnostics/``): the same place the
View options live, written atomically by the page's own POST. ``path=None`` keeps them in memory only (a fixture, a test).

    {"servo": {"config": {"pins": ["servo.max_current_A"], "order": {"": ["id", "servo"], "servo": ["max_current_A"]},
                          "folded": ["aux1"], "sort": "manual"}}}

WHY not settings.toml itself: its writer rewrites the one setting it has, whole, and is the View options' alone; an
arrangement is nested lists the page edits as often as it drags a row.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

ARRANGEMENTS_FILE = "arrangements.json"
SECTIONS = ("diagnostics", "state", "config")
SORTS = ("name", "manual", "severity")
MAX_LIST = 2000  # entries in one list: a bound, not a feature
MAX_KIND = 64


class ArrangementError(ValueError):
    """A value the host refuses (the reason is the page's message)."""


def _strings(value, what: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST or not all(isinstance(item, str) and 0 < len(item) <= 300 for item in value):
        raise ArrangementError(f"{what} is a list of at most {MAX_LIST} names")
    return list(dict.fromkeys(value))


def check(value) -> dict:
    """One section's arrangement: ``pins`` (names, in pin order), ``order`` (folder path -> its children's names), ``folded``
    (folder paths), ``sort`` (name | manual | severity). Anything else is refused."""

    if not isinstance(value, dict):
        raise ArrangementError("an arrangement is an object")
    unknown = sorted(set(value) - {"pins", "order", "folded", "sort"})
    if unknown:
        raise ArrangementError(f"no arrangement field {', '.join(map(repr, unknown))}: pins, order, folded, sort")
    out: dict = {}
    if "pins" in value:
        out["pins"] = _strings(value["pins"], "pins")
    if "folded" in value:
        out["folded"] = _strings(value["folded"], "folded")
    if "order" in value:
        order = value["order"]
        if not isinstance(order, dict) or len(order) > MAX_LIST or not all(isinstance(folder, str) for folder in order):
            raise ArrangementError("order maps a folder path (\"\" is the top) to a list of names")
        out["order"] = {folder: _strings(names, f"order[{folder!r}]") for folder, names in order.items()}
    if "sort" in value:
        if value["sort"] not in SORTS:
            raise ArrangementError(f"sort is one of {', '.join(SORTS)}")
        out["sort"] = value["sort"]
    return out


class Arrangements:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.data: dict = {}
        self.note = ""
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                if not isinstance(loaded, dict):
                    raise ArrangementError("the file is not an object")
                self.data = {kind: {section: check(value) for section, value in sections.items() if section in SECTIONS}
                             for kind, sections in loaded.items() if isinstance(sections, dict)}
            except (OSError, json.JSONDecodeError, ArrangementError, AttributeError) as error:
                self.data, self.note = {}, f"{self.path}: unreadable ({type(error).__name__}: {error}); starting empty"

    def json(self) -> dict:
        with self._lock:
            return {"arrangements": json.loads(json.dumps(self.data)), "file": str(self.path) if self.path else None, "note": self.note,
                    "sections": list(SECTIONS), "sorts": list(SORTS)}

    def set(self, kind: str, section: str, value) -> dict:
        """Replace one section of one kind's arrangement (written now, atomically). Raises ArrangementError."""

        if not isinstance(kind, str) or not 0 < len(kind) <= MAX_KIND:
            raise ArrangementError("kind is the entity's kind (servo, camera, ...)")
        if section not in SECTIONS:
            raise ArrangementError(f"section is one of {', '.join(SECTIONS)}")
        checked = check(value)
        with self._lock:
            self.data.setdefault(kind, {})[section] = checked
            self._write()
        return self.json()

    def _write(self) -> None:
        if self.path is None:
            return
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(handle, "w") as file:
                json.dump(self.data, file, indent=1, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.note = ""
        except OSError as error:  # a read-only home keeps the arrangement for this run; the reason shows in the answer
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            self.note = f"{self.path}: not written ({type(error).__name__}: {error})"


__all__ = ["Arrangements", "ArrangementError", "ARRANGEMENTS_FILE", "SECTIONS", "SORTS", "check"]
