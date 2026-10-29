"""The robot description: an optional URDF whose links are mapped to the devices this PC detects (the page's 3D view).

``<config dir>/robot-description.toml`` (the directory settings.toml and expected.toml live in; format:
docs/robot-description.md). Without the file nothing changes: ``GET /api/v1/robot-description`` says ``present: false`` and the
3D view shows how to add one. With it the page draws the URDF and colours each mapped link by its device's own level.

    version = 1
    urdf = "/abs/path/robot.urdf"         # meshes resolve relative to it; a relative path is relative to this file
    [[part]]
    link = "one_dof_arm"                  # or links = ["a", "b"]
    hardware_id = "usb:0483:5740:0607623A"
    node = "can/12"

A part is matched the way expected.toml matches a device (docs/expected-setup.md): a device by its ``hardware_id`` (vid / pid
in any case) and / or a ``serial`` its row reports (the USB serial, or a driver's identity serial, a RealSense's SDK serial),
then ``node``, the path below that device as the driver writes it. ``path`` (an fnmatch pattern over the entity path),
``model_id`` (the identity's robotparts id) and ``can_id`` (the row is ``.../can/<id>``) narrow any of them, or stand alone.
The first row in tree order that matches is the part's entity; none is ``null`` (the page draws it as a ghost: not detected).
``[[unmapped_device]]`` tables list the devices the author could not place in the model, with the reason (shown, never drawn).

Stdlib only: ``tomllib`` and ``xml.etree``. Read-only: nothing here writes a file or touches a device.
"""

from __future__ import annotations

import fnmatch
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from io_diagnostics import ROOT, activity
from io_diagnostics import expected as expected_module

DESCRIPTION_FILE = "robot-description.toml"
DESCRIPTION_VERSION = 1
DOCS = "docs/robot-description.md"
API = "/api/v1/robot-description"
FILES = f"{API}/files/"
MATCH_TEXT = ("path", "hardware_id", "serial", "node", "model_id")
MATCH_KEYS = (*MATCH_TEXT, "can_id")


class DescriptionError(ValueError):
    pass


@dataclass
class Part:
    links: list[str]
    match: dict
    name: str = ""
    note: str = ""


@dataclass
class Unmapped:
    name: str
    match: dict
    reason: str = ""


@dataclass
class Description:
    file: Path
    urdf: Path | None = None
    robot: str = ""
    links: list[str] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    unmapped: list[Unmapped] = field(default_factory=list)
    problem: str | None = None


def _text(table: dict, key: str, where: str) -> str:
    value = table.get(key, "")
    if not isinstance(value, str):
        raise DescriptionError(f"{where}: {key} must be a string")
    return value.strip()


def _match(table: dict, where: str, required: bool = True) -> dict:
    match = {key: _text(table, key, where) for key in MATCH_TEXT if _text(table, key, where)}
    if "node" in match:
        match["node"] = match["node"].strip("/")
    if "can_id" in table:
        can_id = table["can_id"]
        if isinstance(can_id, bool) or not isinstance(can_id, int):
            raise DescriptionError(f"{where}: can_id must be an integer")
        match["can_id"] = can_id
    if required and not match:
        raise DescriptionError(f"{where}: needs at least one of {', '.join(MATCH_KEYS)}")
    return match


def parse(text: str, file: Path) -> Description:
    """TOML text -> Description; ``DescriptionError`` names the first thing wrong. The URDF is read by ``load``."""

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise DescriptionError(f"not TOML: {error}") from error
    version = data.get("version", DESCRIPTION_VERSION)
    if version != DESCRIPTION_VERSION:
        raise DescriptionError(f"version {version!r}: this host reads version {DESCRIPTION_VERSION}")
    urdf = _text(data, "urdf", "top")
    if not urdf:
        raise DescriptionError("urdf is required: the path of the robot's URDF file")
    path = Path(urdf).expanduser()
    description = Description(file=file, urdf=path if path.is_absolute() else file.parent / path)
    parts = data.get("part", [])
    if not isinstance(parts, list):
        raise DescriptionError("part must be an array of tables ([[part]])")
    for number, table in enumerate(parts, start=1):
        where = f"part {number}"
        if not isinstance(table, dict):
            raise DescriptionError(f"{where}: not a table")
        links = table.get("links", [])
        if "link" in table:
            links = [table["link"], *links] if isinstance(links, list) else links
        if not isinstance(links, list) or not all(isinstance(link, str) and link.strip() for link in links) or not links:
            raise DescriptionError(f"{where}: link (a URDF link name) or links (a list of them) is required")
        description.parts.append(Part(links=[link.strip() for link in links], match=_match(table, where),
                                      name=_text(table, "name", where), note=_text(table, "note", where)))
    unmapped = data.get("unmapped_device", [])
    if not isinstance(unmapped, list):
        raise DescriptionError("unmapped_device must be an array of tables ([[unmapped_device]])")
    for number, table in enumerate(unmapped, start=1):
        where = f"unmapped_device {number}"
        if not isinstance(table, dict):
            raise DescriptionError(f"{where}: not a table")
        description.unmapped.append(Unmapped(name=_text(table, "name", where), match=_match(table, where, required=False),
                                             reason=_text(table, "reason", where)))
    return description


def urdf_links(path: Path) -> tuple[str, list[str]]:
    """The robot's name and its link names, in file order (xml.etree; nothing else of the URDF is read here)."""

    root = ET.parse(path).getroot()
    if root.tag != "robot":
        raise DescriptionError(f"{path}: the root element is <{root.tag}>, not <robot>")
    return root.get("name", ""), [link.get("name", "") for link in root.findall("link") if link.get("name")]


def config_dir(settings_path: Path | None) -> Path:
    """Where the host keeps settings.toml (every mode reads it there), else the default config directory."""

    return Path(settings_path).parent if settings_path else expected_module.default_config_dir()


def _stamp(path: Path | None) -> tuple | None:
    try:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except (OSError, AttributeError):
        return None


_cache: dict[Path, tuple[tuple, Description | None]] = {}


def load_cached(directory: Path) -> Description | None:
    """``load``, read again only when the description file or its URDF changed on disk. WHY: the page asks for a URDF's
    meshes one request each (267 for the rack), and each request resolves its path through the description."""

    file = Path(directory) / DESCRIPTION_FILE
    held = _cache.get(file)
    if held is not None:
        stamp, description = held
        if stamp == (_stamp(file), _stamp(description.urdf if description else None)):
            return description
    description = load(directory)
    _cache[file] = ((_stamp(file), _stamp(description.urdf if description else None)), description)
    return description


# what a served file is sent as; anything else is application/octet-stream
MEDIA = {".urdf": "application/xml; charset=utf-8", ".xml": "application/xml; charset=utf-8", ".stl": "model/stl",
         ".dae": "model/vnd.collada+xml", ".obj": "text/plain; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg"}


def load(directory: Path) -> Description | None:
    """The description in ``directory``, or None when there is no file. A file that does not read, or names a URDF that does
    not, is a Description with ``problem`` set (the page says it, and draws nothing)."""

    file = Path(directory) / DESCRIPTION_FILE
    if not file.is_file():
        return None
    try:
        description = parse(file.read_text(encoding="utf-8"), file)
    except (OSError, UnicodeDecodeError, DescriptionError) as error:
        return Description(file=file, problem=f"{file}: {error}")
    if not description.urdf.is_file():
        description.problem = f"{file}: urdf {description.urdf} is not a file"
        return description
    try:
        description.robot, description.links = urdf_links(description.urdf)
    except (OSError, ET.ParseError, DescriptionError) as error:
        description.problem = f"{description.urdf}: {error}"
    return description


# ------------------------------------------------------------------------------------- matching ----
def _rows(tree: dict) -> list[dict]:
    out, stack = [], [tree]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(reversed(node.get("children") or []))
    return out


def row_serials(row: dict) -> tuple[str, ...]:
    """Every serial a row reports, as expected.device_serials reads them: the driver's identity serial and the USB serial."""

    serials = [str((row.get("identity") or {}).get("serial_number") or "")]
    ids = activity.usb_id(row.get("hardware_id") or "")
    if ids:
        serials.append(ids[2])
    return tuple(serial for serial in dict.fromkeys(serials) if serial)


def resolve(match: dict, rows: list[dict]) -> str | None:
    """The entity path of the first row (tree order) that matches, or None."""

    if not match:
        return None
    candidates = rows
    hardware_id, serial, node = match.get("hardware_id"), match.get("serial"), match.get("node")
    if hardware_id or serial:
        bases = [row for row in rows if (not hardware_id or expected_module.same_id(row.get("hardware_id") or "", hardware_id))
                 and (not serial or serial in row_serials(row))]
        if node:
            wanted = {f"{base['path']}/{node}" for base in bases}
            candidates = [row for row in rows if row["path"] in wanted]
        else:
            candidates = bases
    elif node:
        candidates = [row for row in rows if row["path"].endswith(f"/{node}")]
    for row in candidates:
        if "path" in match and not fnmatch.fnmatchcase(row["path"], match["path"]):
            continue
        if "model_id" in match and (row.get("identity") or {}).get("model_id") != match["model_id"]:
            continue
        if "can_id" in match and not row["path"].endswith(f"/can/{match['can_id']}"):
            continue
        return row["path"]
    return None


def file_url(description: Description, path: Path) -> str:
    return FILES + quote(path.relative_to(description.urdf.parent).as_posix())


def describe(description: Description | None, tree: dict | None, where: Path | None = None) -> dict:
    """``GET /api/v1/robot-description`` (docs/api-v1.md "Robot description"). ``where``: the file the host reads, named in the
    answer when there is none (the page's empty state says where to put it)."""

    if description is None:
        return {"present": False, "problem": None, "file": str(where) if where else None, "file_name": DESCRIPTION_FILE,
                "docs": str(ROOT / DOCS), "urdf": None, "robot": None, "links": [], "parts": [], "unmapped_devices": []}
    rows = _rows(tree) if tree else []
    known = set(description.links)
    usable = description.problem is None
    parts = []
    for part in description.parts:
        missing = [link for link in part.links if link not in known] if usable else []
        parts.append({"name": part.name, "links": part.links, "match": part.match, "note": part.note,
                      "path": resolve(part.match, rows),
                      "problem": f"not in the URDF: {', '.join(missing)}" if missing else None})
    return {"present": True, "problem": description.problem, "file": str(description.file), "file_name": DESCRIPTION_FILE,
            "docs": str(ROOT / DOCS), "urdf": file_url(description, description.urdf) if usable else None, "robot": description.robot or None,
            "links": description.links, "parts": parts,
            "unmapped_devices": [{"name": item.name, "match": item.match, "reason": item.reason, "path": resolve(item.match, rows)}
                                 for item in description.unmapped]}


def served_file(description: Description | None, relative: str) -> Path | None:
    """``GET /api/v1/robot-description/files/<relative>``: the file under the URDF's directory, or None (no description, a
    problem, a ``..``, an absolute path, a symlink that leads out of the directory, or no such file)."""

    if description is None or description.problem or description.urdf is None:
        return None
    name = unquote(relative)
    if not name or name.startswith("/") or "\x00" in name:
        return None
    base = description.urdf.parent.resolve()
    target = (base / name).resolve()
    if base not in target.parents or not target.is_file():
        return None
    return target
