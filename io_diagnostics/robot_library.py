"""Bringing a robot description in from the page: the upload, the kept descriptions, and a device assigned to a part by a click.

The write side of robot_description.py (docs/robot-description.md "From the page"). Zach, 2026-11-04: "I can just upload the URDF, etc. and
then it could show the information live"; "at some higher rung ... we just plug it in, and like now we can see which motors are where".

    <config dir>/robot-description.toml                 the active description, the one file the host reads
    <config dir>/robot-descriptions/<name>/             each upload, unpacked, with its own robot-description.toml; also where
                                                        the active file is kept when another one takes its place

The upload is a zip or a folder as multipart (POST /api/v1/robot-description/upload). It unpacks into a scratch folder first, so a refusal
leaves nothing behind: every path is checked before a byte is written (zip-slip, symlinks), and the size cap counts what is really read,
never what the archive claims. A cad2robot/1 bundle brings its own mappings from robot.json.

A page edit keeps the person's file: it changes only the [[part]] table it must (comments, order and every other table stay as written),
the file is re-parsed before it replaces the old one, and the old one stays beside it as robot-description.toml.bak.

Stdlib only. Nothing here touches a device.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import IO

from io_diagnostics import robot_description as rd

LIBRARY_DIR = "robot-descriptions"
# WHY these numbers: the rack's bundle is 72 MB of STL; a big arm with textured .dae files is a few hundred. The caps are there so a
# wrong drop cannot fill the disk, not to judge a robot.
MAX_UPLOAD = 1024 * 1024 * 1024
MAX_UNPACKED = 3 * 1024 * 1024 * 1024
MAX_FILES = 20_000
CHUNK = 1024 * 1024
SKIPPED_PARTS = ("__MACOSX",)
SKIPPED_NAMES = (".DS_Store", "Thumbs.db")
_write = threading.Lock()


class Refused(Exception):
    """A write the page must explain: ``code`` is the answer's ``refused``, ``status`` its HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def library_dir(directory: Path) -> Path:
    return Path(directory) / LIBRARY_DIR


def active_file(directory: Path) -> Path:
    return Path(directory) / rd.DESCRIPTION_FILE


# ------------------------------------------------------------------------------------------- unpacking ----
def member_path(name: str) -> PurePosixPath:
    """The path of an archive member or an uploaded file, or ``Refused`` for one that could leave the folder (zip-slip)."""

    # WHY a backslash is a separator and not a refusal: PowerShell 5.1's Compress-Archive writes them, and the Windows Cad2Robot box is
    # where a bundle comes from
    clean = name.replace("\\", "/")
    if not clean or "\x00" in clean or clean.startswith("/") or re.match(r"^[A-Za-z]:", clean):
        raise Refused("UnsafePath", f"unsafe path in the upload: {name!r}")
    path = PurePosixPath(clean)
    if ".." in path.parts or path.is_absolute():
        raise Refused("UnsafePath", f"unsafe path in the upload: {name!r}")
    return path


def _skipped(path: PurePosixPath) -> bool:
    return path.name in SKIPPED_NAMES or any(part in SKIPPED_PARTS for part in path.parts)


class _Budget:
    """What the upload may still write: every byte really read counts, whatever an archive's headers claim."""

    def __init__(self):
        self.files = 0
        self.bytes = 0

    def file(self) -> None:
        self.files += 1
        if self.files > MAX_FILES:
            raise Refused("TooLarge", f"more than {MAX_FILES} files", 413)

    def spend(self, count: int) -> None:
        self.bytes += count
        if self.bytes > MAX_UNPACKED:
            raise Refused("TooLarge", f"more than {MAX_UNPACKED} bytes unpacked", 413)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16)


def unpack_zip(stream: IO[bytes], target: Path) -> None:
    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as error:
        raise Refused("BadUpload", f"not a zip file: {error}") from error
    with archive:
        members = archive.infolist()
        # every path is checked before anything is written: a refused zip leaves no file of its own behind
        for info in members:
            member_path(info.filename.rstrip("/\\") or ".")
            if _is_symlink(info):
                raise Refused("UnsafePath", f"unsafe path in the upload: {info.filename!r} is a symlink")
        budget = _Budget()
        for info in members:
            path = member_path(info.filename.rstrip("/\\") or ".")
            if info.filename.endswith(("/", "\\")) or _skipped(path):
                continue
            budget.file()
            destination = target / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as source, open(destination, "wb") as sink:
                    while chunk := source.read(CHUNK):
                        budget.spend(len(chunk))
                        sink.write(chunk)
            except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as error:
                raise Refused("BadUpload", f"could not read {info.filename!r} from the zip: {error}") from error


def unpack_multipart(body: bytes, content_type: str, target: Path) -> None:
    """A folder from the page: one form part per file, its ``filename`` the path inside the folder."""

    message = email.message_from_bytes(b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\n\r\n" + body, policy=email.policy.HTTP)
    if not message.is_multipart():
        raise Refused("BadUpload", "a multipart body with one part per file is required")
    files = []
    for part in message.iter_parts():
        name = part.get_param("filename", header="content-disposition") or ""
        if isinstance(name, tuple):
            name = email.utils.collapse_rfc2231_value(name)
        if name:
            files.append((member_path(name), part))
    if not files:
        raise Refused("BadUpload", "the multipart body has no file")
    budget = _Budget()
    for path, part in files:
        if _skipped(path):
            continue
        budget.file()
        data = part.get_payload(decode=True) or b""
        budget.spend(len(data))
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


# ------------------------------------------------------------------------------------- what was brought ----
def _manifest(root: Path) -> tuple[Path, dict] | None:
    """A cad2robot/1 bundle's robot.json under ``root``, if there is one."""

    for path in sorted(root.rglob("robot.json"), key=lambda item: len(item.parts)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("schema", "")).startswith("cad2robot/"):
            return path, data
    return None


def find_urdf(root: Path, manifest: tuple[Path, dict] | None) -> tuple[Path, list[str]]:
    """The upload's URDF: the one a bundle's robot.json names, else the shallowest. ``Refused`` says what is there instead."""

    warnings: list[str] = []
    if manifest:
        named = manifest[0].parent / str(manifest[1].get("urdf") or "")
        if named.is_file():
            return named, warnings
    found = sorted(root.rglob("*.urdf"), key=lambda path: (len(path.parts), path.as_posix()))
    if not found:
        if any(root.rglob("*.xacro")):
            raise Refused("NoRobot", "no .urdf in the upload, only .xacro: expand it first (xacro robot.urdf.xacro > robot.urdf)")
        raise Refused("NoRobot", "no .urdf in the upload")
    if len(found) > 1:
        warnings.append(f"{len(found)} URDFs in the upload; using {found[0].relative_to(root).as_posix()}")
    return found[0], warnings


def read_robot(urdf: Path) -> tuple[str, list[str], list[str]]:
    try:
        robot, links = rd.urdf_links(urdf)
        return robot, links, rd.urdf_mesh_refs(urdf)
    except ET.ParseError as error:
        raise Refused("NoRobot", f"{urdf.name} is not XML: {error}") from error
    except rd.DescriptionError as error:
        raise Refused("NoRobot", f"{urdf.name}: the root element is not <robot>") from error


def missing_meshes(references: list[str], urdf: Path, root: Path, packages: dict[str, str]) -> list[str]:
    missing = []
    for reference in references:
        if reference.startswith("package://"):
            name, _, rest = reference[len("package://"):].partition("/")
            path = root / packages[name] / rest if name in packages else None
        else:
            path = urdf.parent / reference
        if path is None or not path.is_file():
            missing.append(reference)
    return missing


def occurrence_pattern(occurrence: str) -> str:
    """A Fusion occurrence path as Cad2Robot names its mesh files, as a pattern (``A:1+(B) C:2`` -> ``A_1___B_C_2__*``)."""

    return re.sub(r"[^A-Za-z0-9._-]+", "_", occurrence.replace("+", "__")) + "__*"


def bundle_parts(manifest: tuple[Path, dict], urdf: Path, meshes: list[str]) -> tuple[list[dict], list[str]]:
    """The mappings a cad2robot/1 robot.json carries: a joint's ``can_id`` to its child link and motor, a camera's ``serial`` to its link."""

    data = manifest[1]
    children = {joint.get("name"): joint.find("child").get("link") for joint in ET.parse(urdf).getroot().findall("joint") if joint.find("child") is not None}
    parts, warnings = [], []
    for joint in data.get("joints") or []:
        name, can_id = joint.get("name"), joint.get("can_id")
        if not isinstance(can_id, int) or isinstance(can_id, bool):
            warnings.append(f"{name}: robot.json has no can_id")
            continue
        if name not in children:
            warnings.append(f"{name}: robot.json names a joint the URDF does not have")
            continue
        pattern = occurrence_pattern(str(joint.get("occurrence") or ""))
        parts.append({"name": f"CAN {can_id} · {name}", "links": [children[name]],
                      "meshes": [pattern] if any(rd.mesh_matches(pattern, file) for file in meshes) else [],
                      "match": {"can_id": can_id}, "note": f"robot.json joints[{name}] can_id {can_id}"})
    for camera in data.get("cameras") or []:
        serial, link = camera.get("serial"), camera.get("link")
        if serial and link:
            parts.append({"name": f"{camera.get('name') or 'camera'} {serial}", "links": [link], "meshes": [], "match": {"serial": str(serial)},
                          "note": f"robot.json cameras[{camera.get('name')}] serial"})
    return parts, warnings


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _list(values: list[str]) -> str:
    return "[" + ", ".join(_quote(value) for value in values) + "]"


def _key_line(key: str, value) -> str:
    return f"{key} = {value}" if isinstance(value, int) and not isinstance(value, bool) else f"{key} = {_quote(str(value))}"


def part_table(part: dict) -> list[str]:
    lines = ["[[part]]"]
    if part.get("name"):
        lines.append(f"name = {_quote(part['name'])}")
    if part.get("links"):
        lines.append(f"links = {_list(part['links'])}")
    if part.get("meshes"):
        lines.append(f"meshes = {_list(part['meshes'])}")
    lines.extend(_key_line(key, value) for key, value in part["match"].items())
    if part.get("note"):
        lines.append(f"note = {_quote(part['note'])}")
    return lines


def _description_text(name: str, urdf: Path, root: Path, parts: list[dict], origin: str) -> str:
    lines = [f"# RobotDx · {origin}. The format: docs/robot-description.md", "version = 1", f"name = {_quote(name)}", f"urdf = {_quote(str(urdf))}",
             f"root = {_quote(str(root))}", ""]
    for part in parts:
        lines.extend([*part_table(part), ""])
    return "\n".join(lines)


def _replace(path: Path, text: str) -> None:
    """Write a whole file atomically (temp file + rename in the same directory): expected.toml's way."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------------- the kept descriptions ----
def _keep_active(directory: Path) -> str | None:
    """The active file, kept under its own name before another takes its place; its name, or None when there is no active file.
    A relative ``urdf`` becomes absolute, or the kept copy would stop resolving (it is relative to the file it is in)."""

    file = active_file(directory)
    if not file.is_file():
        return None
    description = rd.load_file(file)
    name = rd.description_name(description)
    text = file.read_text(encoding="utf-8")
    if description.urdf is not None:
        text = re.sub(r"(?m)^urdf\s*=.*$", lambda _: f"urdf = {_quote(str(description.urdf))}", text, count=1)
    _replace(library_dir(directory) / name / rd.DESCRIPTION_FILE, text)
    return name


def listing(directory: Path) -> list[dict]:
    """Every description this PC has: each kept folder, and the active file (which may never have been kept), by name."""

    directory = Path(directory)
    active = rd.load_file(active_file(directory))
    active_name = rd.description_name(active) if active else None
    found: dict[str, rd.Description] = {}
    folder = library_dir(directory)
    if folder.is_dir():
        for file in folder.glob(f"*/{rd.DESCRIPTION_FILE}"):
            found[file.parent.name] = rd.load_file(file)
    if active:
        found[active_name] = active
    return [{"name": name, "robot": found[name].robot or None, "parts": len(found[name].parts), "problem": found[name].problem, "active": name == active_name}
            for name in sorted(found)]


def _known(directory: Path, name) -> dict:
    for item in listing(directory):
        if item["name"] == name:
            return item
    raise Refused("NoSuchDescription", f"no kept description {name!r}", 404)


def activate(directory: Path, name) -> dict:
    with _write:
        item = _known(directory, name)
        if item["active"]:
            return {"name": name}
        previous = _keep_active(directory)
        shutil.copyfile(library_dir(directory) / name / rd.DESCRIPTION_FILE, active_file(directory))
        return {"name": name, "previous": previous}


def remove(directory: Path, name) -> dict:
    """Removes the description and its kept folder (only ever that folder: a URDF elsewhere that it pointed at stays). The active one
    goes back to the lowest rung: no description, every other view as it was."""

    with _write:
        item = _known(directory, name)
        if item["active"]:
            active_file(directory).unlink()
        folder = library_dir(directory) / name
        if folder.is_dir():
            shutil.rmtree(folder)
        return {"name": name}


def _free_name(directory: Path, wanted: str) -> str:
    taken = {item["name"] for item in listing(directory)}
    if wanted not in taken and not (library_dir(directory) / wanted).exists():
        return wanted
    number = 2
    while f"{wanted}-{number}" in taken or (library_dir(directory) / f"{wanted}-{number}").exists():
        number += 1
    return f"{wanted}-{number}"


def _install(directory: Path, unpack: Callable[[Path], None], hint: str) -> dict:
    kept = library_dir(directory)
    with _write:
        kept.mkdir(parents=True, exist_ok=True)
        incoming = Path(tempfile.mkdtemp(prefix=".incoming-", dir=kept))
        try:
            unpack(incoming)
            manifest = _manifest(incoming)
            urdf, warnings = find_urdf(incoming, manifest)
            robot, links, references = read_robot(urdf)
            files = [rd.mesh_file(reference) for reference in references]
            parts: list[dict] = []
            if manifest:
                parts, more = bundle_parts(manifest, urdf, files)
                warnings = [*warnings, *more]
            packages = rd.package_dirs(references, incoming)
            missing = missing_meshes(references, urdf, incoming, packages)
            name = _free_name(directory, rd.slug(hint or robot or urdf.stem))
            folder = kept / name
            final_urdf = folder / urdf.relative_to(incoming)
            text = _description_text(name, final_urdf, folder, parts, f"brought in from the page as {name}")
            (incoming / rd.DESCRIPTION_FILE).write_text(text, encoding="utf-8")
            os.replace(incoming, folder)
        except BaseException:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
        previous = _keep_active(directory)
        shutil.copyfile(folder / rd.DESCRIPTION_FILE, active_file(directory))
    return {"name": name, "robot": robot or None, "links": len(links), "meshes": len(references), "missing_meshes": missing, "mapped": len(parts),
            "schema": str(manifest[1].get("schema")) if manifest else None, "warnings": warnings, "previous": previous}


def upload(directory: Path, stream: IO[bytes], content_type: str, hint: str = "") -> dict:
    """POST /api/v1/robot-description/upload: unpack, find the URDF, keep the active description, make this one active."""

    kind = content_type.split(";")[0].strip().lower()
    if kind == "multipart/form-data":
        return _install(directory, lambda target: unpack_multipart(stream.read(), content_type, target), hint)
    if kind in ("application/zip", "application/x-zip-compressed", "application/octet-stream"):
        return _install(directory, lambda target: unpack_zip(stream, target), hint)
    raise Refused("BadUpload", f"a zip (application/zip) or a folder (multipart/form-data) is required, not {kind or 'nothing'}")


# ------------------------------------------------------------------------------- a device on a part, by a click ----
_TABLE = re.compile(r"^\s*\[\[?\s*([A-Za-z0-9_.-]+)\s*\]\]?\s*(#.*)?$")


def _tables(lines: list[str]) -> list[tuple[str, int, int]]:
    """Every table of the file as ``(name, first line, line after its last)``; a table's own comments above its header are its first
    lines, so the comments before a table are never a part of the one above."""

    heads = [(index, _TABLE.match(line)[1]) for index, line in enumerate(lines) if _TABLE.match(line)]
    out = []
    for position, (index, name) in enumerate(heads):
        first = index
        while first > 0 and lines[first - 1].lstrip().startswith("#"):
            first -= 1
        following = heads[position + 1][0] if position + 1 < len(heads) else len(lines)
        last = following
        if position + 1 < len(heads):
            while last > index + 1 and lines[last - 1].lstrip().startswith("#"):
                last -= 1
        out.append((name, first, last))
    return out


def _without_keys(block: list[str], keys: tuple[str, ...]) -> list[str]:
    """The table's lines without these keys (a value may run over several lines)."""

    pattern = re.compile(rf"^\s*({'|'.join(keys)})\s*=")
    out, skipping = [], False
    for line in block:
        if skipping:
            skipping = "]" not in line.split("#")[0]
            continue
        if pattern.match(line):
            value = line.split("=", 1)[1].split("#")[0]
            skipping = value.count("[") > value.count("]")
            continue
        out.append(line)
    return out


def _edit_part(text: str, index: int, links: list[str], meshes: list[str], placed: bool = False) -> str:
    """The file with the ``index``-th [[part]] holding these links and meshes, or gone when it holds nothing (``placed``: it has a point
    of its own, ``at``, and stays)."""

    lines = text.split("\n")
    tables = [table for table in _tables(lines) if table[0] == "part"]
    _, first, last = tables[index]
    if not (links or meshes or placed):
        while last < len(lines) and not lines[last].strip():         # the blank line under it goes with it
            last += 1
        return "\n".join(lines[:first] + lines[last:])
    block = _without_keys(lines[first:last], ("link", "links", "meshes"))
    head = next(position for position, line in enumerate(block) if _TABLE.match(line))
    written = next((position for position, line in enumerate(block) if re.match(r"^\s*name\s*=", line)), head)
    insert = ([f"links = {_list(links)}"] if links else []) + ([f"meshes = {_list(meshes)}"] if meshes else [])
    block[written + 1:written + 1] = insert
    return "\n".join(lines[:first] + block + lines[last:])


def _append_part(text: str, part: dict) -> str:
    """A new [[part]] after the last one, before the tables that follow, else at the end of the file."""

    lines = text.split("\n")
    table = part_table(part)
    parts = [item for item in _tables(lines) if item[0] == "part"]
    if parts:
        at = parts[-1][2]
        while at > 0 and not lines[at - 1].strip():
            at -= 1
        return "\n".join(lines[:at] + ["", *table] + lines[at:])
    following = _tables(lines)
    following = [item for item in following if item[0] != "view"]
    if following:
        at = following[0][1]
        return "\n".join(lines[:at] + [*table, ""] + lines[at:])
    return "\n".join([*lines[:len(lines) - (1 if lines and not lines[-1] else 0)], "", *table, ""])


def match_of(row: dict, rows: list[dict]) -> dict:
    """The keys that find this row again after a replug, expected.toml's way: the nearest device at or above it with a repeatable USB
    id (or a serial of its own, for a port-tied id), then the node below it. WHY not the entity path: it changes with the USB port."""

    by_path = {item["path"]: item for item in rows}
    at: dict | None = row
    while at is not None:
        hardware_id = at.get("hardware_id") or ""
        serial = str((at.get("identity") or {}).get("serial_number") or "")
        found = {}
        if hardware_id.startswith("usb:") and "@" not in hardware_id:
            found = {"hardware_id": hardware_id}
        elif hardware_id.startswith("usb:") and serial:
            found = {"serial": serial}
        if found:
            below = row["path"][len(at["path"]):].strip("/")
            return {**found, "node": below} if below else found
        at = by_path.get(at.get("parent") or "")
    return {"path": row["path"]}


def assign(directory: Path, tree: dict, body: dict) -> dict:
    """POST /api/v1/robot-description/assign: put these links / mesh patterns on the device at ``path``. What the device already holds
    comes off again (the click that undoes), what another device holds moves here, and a part left with nothing goes."""

    path, links, meshes, name = body.get("path"), body.get("links", []), body.get("meshes", []), body.get("name", "")
    valid = isinstance(path, str) and isinstance(name, str) and all(isinstance(value, list) and all(isinstance(item, str) and item for item in value) for value in (links, meshes))
    if not valid or not (links or meshes):
        raise Refused("BadRequest", "path is a string; links and meshes are lists of strings, at least one of them not empty")
    with _write:
        file = active_file(directory)
        description = rd.load_file(file)
        if description is None or description.problem:
            raise Refused("NoDescription", "no robot description to assign to" + (f": {description.problem}" if description else ""), 409)
        rows = rd._rows(tree)
        row = next((item for item in rows if item["path"] == path), None)
        if row is None:
            raise Refused("NoSuchEntity", f"no entity {path!r}", 404)
        unknown = [link for link in links if link not in description.links]
        unknown += [pattern for pattern in meshes if not any(rd.mesh_matches(pattern, mesh) for mesh in description.meshes)]
        if unknown:
            raise Refused("NoSuchLink", f"not in the URDF: {', '.join(unknown)}")
        match = match_of(row, rows)
        text = file.read_text(encoding="utf-8")
        own = next((index for index, part in enumerate(description.parts) if rd.resolve(part.match, rows) == path or part.match == match), None)
        held = own is not None and all(link in description.parts[own].links for link in links) and all(pattern in description.parts[own].meshes for pattern in meshes)

        def without(part: rd.Part) -> tuple[list[str], list[str]]:
            return [link for link in part.links if link not in links], [pattern for pattern in part.meshes if pattern not in meshes]

        if held:
            text = _edit_part(text, own, *without(description.parts[own]), placed=description.parts[own].at is not None)
        else:
            # what another device holds comes off it first; the tables are edited last to first, so an index stays good
            for index in range(len(description.parts) - 1, -1, -1):
                part = description.parts[index]
                if index != own and (set(links) & set(part.links) or set(meshes) & set(part.meshes)):
                    left = without(part)
                    text = _edit_part(text, index, *left, placed=part.at is not None)
                    if own is not None and own > index and not any(left) and part.at is None:
                        own -= 1
            if own is None:
                text = _append_part(text, {"name": name or row.get("name") or path, "links": links, "meshes": meshes, "match": match, "note": "assigned on the page"})
            else:
                part = rd.parse(text, file).parts[own]
                text = _edit_part(text, own, [*part.links, *[link for link in links if link not in part.links]],
                                  [*part.meshes, *[pattern for pattern in meshes if pattern not in part.meshes]], placed=part.at is not None)
        rd.parse(text, file)                                              # never replace the file with one that does not read
        previous = file.read_text(encoding="utf-8")
        _replace(file.with_name(file.name + ".bak"), previous)
        _replace(file, text)
    return {"assigned": not held, "path": path}
