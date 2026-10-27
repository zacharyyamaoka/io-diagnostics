"""A person's dashboards as files: ``<config dir>/dashboards/<name>.base`` (docs/api-v1.md "Dashboards as files").

The host treats a ``.base`` as OPAQUE text: the page (data-ui) owns the format, so nothing here parses it. What the host does
enforce is the file store's own rules: a name that cannot leave the folder, a size cap, UTF-8 only, an etag so two tabs cannot
silently overwrite each other, an atomic write, and a delete that moves the file to ``.trash/`` instead of removing it.

WHY beside settings.toml (``$IO_DIAGNOSTICS_CONFIG_DIR``, else the XDG default): a dashboard is the person's own file, like their
settings and arrangements, so both channels, the tray and a text editor all see the same ones.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from io_diagnostics.expected import default_config_dir

DASHBOARDS_DIR = "dashboards"
TRASH_DIR = ".trash"
EXTENSION = ".base"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$")
MAX_BYTES = 1024 * 1024


class DashboardError(Exception):
    """A request the store refuses: ``code`` is the contract's ``refused`` word, ``status`` its HTTP code."""

    def __init__(self, code: str, status: int, reason: str, **extra):
        super().__init__(reason)
        self.code, self.status, self.extra = code, status, extra


def default_dashboards_dir() -> Path:
    """``<config dir>/dashboards``: the config dir is the one the settings use (``$IO_DIAGNOSTICS_CONFIG_DIR``, else
    ``$XDG_CONFIG_HOME/io-diagnostics``, else ``~/.config/io-diagnostics``)."""

    return default_config_dir() / DASHBOARDS_DIR


def etag_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_name(file: str) -> str:
    """The stem of ``<name>.base`` when ``file`` is a safe dashboard file name, else a ``BadName`` refusal.

    WHY the stem is checked, not the file name: the contract's name rule is on ``<name>``; ``.base`` is the fixed extension.
    The first character is a letter or digit, so ``.``, ``..`` and a hidden file (``.trash``) can never match, and no separator
    is in the set, so a decoded ``%2e%2e%2fx`` is refused whole."""

    stem = file[:-len(EXTENSION)] if file.endswith(EXTENSION) else None
    if stem is None or not NAME.match(stem):
        raise DashboardError("BadName", 400, f"{file!r} is not a dashboard file name: <name>.base, <name> of 1..80 characters "
                                              "(letters, digits, space, _ . -; first a letter or digit)")
    return stem


def _matches(if_match: str | None, etag: str | None) -> bool:
    """RFC 9110 ``If-Match``: ``*`` matches any existing file, else the quoted (or bare) etags it lists."""

    if if_match is None:
        return True
    if etag is None:
        return False
    wanted = {part.strip().removeprefix("W/").strip('"') for part in if_match.split(",")}
    return "*" in wanted or etag in wanted


class DashboardStore:
    def __init__(self, directory: Path, now=time.time):
        self.dir = Path(directory)
        self.now = now
        self._lock = threading.Lock()  # the etag check and the replace are one step across the server's threads

    def _path(self, file: str) -> Path:
        check_name(file)
        return self.dir / file

    def _read(self, path: Path) -> bytes | None:
        # WHY not follow a symlink: the folder is the person's, but a link in it must not read or write outside it
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes()

    def _entry(self, file: str, data: bytes, stat: os.stat_result) -> dict:
        return {"name": file[:-len(EXTENSION)], "file": file, "size": len(data), "mtime": stat.st_mtime, "etag": etag_of(data)}

    def list(self) -> dict:
        """``{"files": [...]}``, newest first; a file that is not a dashboard (a hand-placed name that fails the rule, a link, a
        file over the cap) is left out. The folder is not created by a read."""

        files = []
        if self.dir.is_dir():
            for path in self.dir.iterdir():
                try:
                    check_name(path.name)
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
                        continue
                    files.append(self._entry(path.name, path.read_bytes(), path.stat()))
                except (DashboardError, OSError):
                    continue
        files.sort(key=lambda entry: (-entry["mtime"], entry["name"]))
        return {"files": files}

    def read(self, file: str) -> tuple[bytes, dict]:
        path = self._path(file)
        data = self._read(path)
        if data is None:
            raise DashboardError("NoSuchDashboard", 404, f"no dashboard {file!r}")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise DashboardError("NotUtf8", 422, f"{file!r} on disk is not UTF-8 text") from None
        return data, self._entry(file, data, path.stat())

    def write(self, file: str, data: bytes, if_match: str | None = None) -> tuple[dict, bool]:
        """Atomically replace (or create) ``file``; ``(entry, created)``. ``if_match`` is the request's ``If-Match`` header."""

        path = self._path(file)
        if len(data) > MAX_BYTES:
            raise DashboardError("TooLarge", 413, f"a dashboard is at most {MAX_BYTES} bytes, got {len(data)}")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DashboardError("NotUtf8", 400, f"the body is not UTF-8 text ({error.reason} at byte {error.start})") from None
        with self._lock:
            current = self._read(path)
            current_etag = None if current is None else etag_of(current)
            if not _matches(if_match, current_etag):
                raise DashboardError("EtagMismatch", 412, f"If-Match {if_match} does not match the file on disk"
                                     if current is not None else f"If-Match was sent but there is no dashboard {file!r}",
                                     etag=current_etag)
            self.dir.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(dir=self.dir, prefix=".write-", suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(temp, 0o644)  # mkstemp's 0600 would make a person's own file unreadable to their other tools
                os.replace(temp, path)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
            return self._entry(file, data, path.stat()), current is None

    def delete(self, file: str, if_match: str | None = None) -> dict:
        """Move ``file`` to ``.trash/<name>.<UTC stamp>.base``: nothing is ever removed, and a second delete of the same name
        lands beside the first."""

        path = self._path(file)
        with self._lock:
            current = self._read(path)
            if current is None:
                raise DashboardError("NoSuchDashboard", 404, f"no dashboard {file!r}")
            if not _matches(if_match, etag_of(current)):
                raise DashboardError("EtagMismatch", 412, f"If-Match {if_match} does not match the file on disk", etag=etag_of(current))
            trash = self.dir / TRASH_DIR
            trash.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(self.now()))
            stem, number = file[:-len(EXTENSION)], 0
            while True:
                target = trash / f"{stem}.{stamp}{f'-{number}' if number else ''}{EXTENSION}"
                if not target.exists():
                    break
                number += 1
            os.replace(path, target)
            return {"name": stem, "file": file, "trashed": f"{TRASH_DIR}/{target.name}", "etag": etag_of(current)}
