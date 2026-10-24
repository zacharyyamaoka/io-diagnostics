"""The composition file: ``[[composite]]`` tables of the expected setup (docs/composition-v1.md section 1).

A composite is a name for a set of entities: an arm, a robot, a rack, a facility. RobotDx never reads what the name means; it only
knows which entities (or other composites) the composite contains. This module is the FILE: the dataclass, ``parse`` (with every
problem named, never a crash) and ``dump``. WHY it is apart from ``composition.py`` (resolution, rollup, batch): the expected-setup
manifest (``expected.py``) reads and writes these tables, and the evaluation needs the manifest's id matching, so the two cannot
import each other.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from io_diagnostics import tree

COMPOSITE_KIND = "composite"
PATH_PREFIX = f"{tree.HOST_PATH}/composite-"
BATCH_MODES = ("all", "having")
# WHY these shapes: an id is words joined by "/" (a convention, never a path in the tree); no ":" or "#" so it can never be mistaken for a
# hardware id or a node reference, and nothing starting with "host" so it can never be mistaken for an entity path.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
_KIND = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_PLUGIN = re.compile(r"^[A-Za-z0-9_.-]+$")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class Composite:
    id: str
    kind: str
    children: list[str]
    title: str = ""
    plugin: str = ""
    expects: dict = field(default_factory=dict)  # handed to the plugin verbatim; RobotDx never reads it
    batch: str = "all"  # "all" | "having": which leaves a batch action covers (docs/composition-v1.md section 6)
    source: str = ""  # where the table came from (a generator writes it); shown, never judged

    def label(self) -> str:
        return self.title or self.id.rsplit("/", 1)[-1]

    @property
    def path(self) -> str:
        return composite_path(self.id)


def composite_path(composite_id: str) -> str:
    return PATH_PREFIX + tree.slug(composite_id.replace("/", "-"))


def classify(ref: str) -> str:
    """What a child reference names, by its shape: ``node`` (``<hardware_id>#<node path>``), ``hardware`` (any other reference
    with a ``:``), ``path`` (an entity path, rooted at ``host``) or ``composite`` (another composite's id)."""

    if "#" in ref:
        return "node"
    if ":" in ref:
        return "hardware"
    if ref == tree.HOST_PATH or ref.startswith(tree.HOST_PATH + "/"):
        return "path"
    return "composite"


class CompositeError(ValueError):
    pass


def _text(table: dict, key: str, where: str, *, required: bool = False) -> str:
    value = table.get(key, "")
    if not isinstance(value, str):
        raise CompositeError(f"{where}: {key} must be a string")
    if required and not value.strip():
        raise CompositeError(f"{where}: {key} is required")
    return value.strip()


def _plain(value, where: str) -> object:
    """A TOML value that survives JSON and TOML both (the ``expects`` table is JSON to the plugin and TOML in the file)."""

    if isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CompositeError(f"{where}: expects holds a number that is not finite")
        return value
    if isinstance(value, list):
        return [_plain(item, where) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item, where) for key, item in value.items()}
    raise CompositeError(f"{where}: expects holds a {type(value).__name__}, which a plugin cannot be given as JSON")


def _one(table: dict, where: str) -> Composite:
    composite_id = _text(table, "id", where, required=True)
    where = f"composite {composite_id!r}"
    if not _ID.match(composite_id) or composite_id == tree.HOST_PATH or composite_id.startswith(tree.HOST_PATH + "/"):
        raise CompositeError(f"{where}: id must be words of [A-Za-z0-9._-] joined by '/', and not start with 'host/'")
    kind = _text(table, "kind", where, required=True)
    if not _KIND.match(kind):
        raise CompositeError(f"{where}: kind must be a word ([A-Za-z][A-Za-z0-9_-]*), got {kind!r}")
    plugin = _text(table, "plugin", where)
    if plugin and not _PLUGIN.match(plugin):
        raise CompositeError(f"{where}: plugin must be a plugin's name ([A-Za-z0-9_.-]), got {plugin!r}")
    batch = _text(table, "batch", where) or "all"
    if batch not in BATCH_MODES:
        raise CompositeError(f"{where}: batch must be one of {BATCH_MODES}, got {batch!r}")
    expects = table.get("expects", {})
    if not isinstance(expects, dict):
        raise CompositeError(f"{where}: expects must be a table")
    children = table.get("children")
    if not isinstance(children, list) or not children:
        raise CompositeError(f"{where}: children is required: a list of entities (or composite ids), at least one")
    if not all(isinstance(child, str) and child.strip() for child in children):
        raise CompositeError(f"{where}: each child is a non-empty string (a hardware id, 'hardware_id#node/path', an entity path or a composite id)")
    return Composite(id=composite_id, kind=kind, children=[child.strip() for child in children], title=_text(table, "title", where),
                     plugin=plugin, expects=_plain(expects, where), batch=batch, source=_text(table, "source", where))


def parse(tables) -> tuple[list[Composite], list[str]]:
    """The file's ``composite`` tables -> ``(composites, problems)``. Never raises: every problem is a sentence naming the table.
    A table that is wrong in itself is left out of the list; the rest are checked against each other (``check``)."""

    if tables in (None, []):
        return [], []
    if not isinstance(tables, list):
        return [], ["composite must be an array of tables ([[composite]])"]
    composites, problems = [], []
    for number, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            problems.append(f"composite {number}: not a table")
            continue
        try:
            composites.append(_one(table, f"composite {number}"))
        except CompositeError as error:
            problems.append(str(error))
    return composites, problems + check(composites)


def check(composites: list[Composite]) -> list[str]:
    """What is wrong between composites: duplicate ids, a composite id nobody defines, two parents, a leaf in two places, a cycle."""

    problems: list[str] = []
    by_id: dict[str, Composite] = {}
    paths: dict[str, str] = {}
    for composite in composites:
        if composite.id in by_id:
            problems.append(f"composite {composite.id!r}: defined twice")
            continue
        if composite.path in paths:
            problems.append(f"composite {composite.id!r} and {paths[composite.path]!r} would both be {composite.path}: rename one")
            continue
        by_id[composite.id], paths[composite.path] = composite, composite.id
    parent_of: dict[str, str] = {}
    leaf_of: dict[str, str] = {}
    for composite in by_id.values():
        seen: set[str] = set()
        for ref in composite.children:
            if ref in seen:
                problems.append(f"composite {composite.id!r}: lists {ref!r} twice")
                continue
            seen.add(ref)
            if classify(ref) == "path" and ref.startswith(PATH_PREFIX):
                # WHY: a composite is a child by its id; by its path it would be a leaf with actions of its own, and two composites could name each other
                problems.append(f"composite {composite.id!r}: child {ref!r} is a composite's path: name it by its id")
            elif classify(ref) == "composite":
                if ref == composite.id:
                    problems.append(f"composite {composite.id!r}: contains itself")
                elif ref not in by_id:
                    problems.append(f"composite {composite.id!r}: child {ref!r} is not a composite in this file (a hardware id has a ':', an entity path starts with 'host/')")
                elif ref in parent_of:
                    problems.append(f"composite {ref!r} is a child of both {parent_of[ref]!r} and {composite.id!r}: a composite has one parent")
                else:
                    parent_of[ref] = composite.id
            elif ref in leaf_of:
                problems.append(f"{ref!r} is a child of both {leaf_of[ref]!r} and {composite.id!r}: a leaf belongs to one composite")
            else:
                leaf_of[ref] = composite.id
    # a cycle: walk up from each composite; a repeat before the top is a loop (named by the first composite met again)
    reported: set[str] = set()
    for start in by_id:
        trail, current = [], start
        while current in parent_of and current not in trail:
            trail.append(current)
            current = parent_of[current]
        if current in trail and current not in reported:
            loop = trail[trail.index(current):][::-1]
            reported.update(loop)
            problems.append("composites contain each other: " + " -> ".join([*loop, loop[0]]))
    return problems


# ----- writing the file back (the panel's edits rewrite the manifest whole) ------------------------------------------
def _toml_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key)


def toml_value(value) -> str:
    # WHY json.dumps for text: a TOML basic string takes the same escapes (\" \\ \n \uXXXX), so any authored text round-trips
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_key(key)} = {toml_value(item)}" for key, item in value.items()) + " }" if value else "{}"
    raise CompositeError(f"cannot write a {type(value).__name__} to TOML")


def dump(composites: list[Composite]) -> list[str]:
    """The lines of the ``[[composite]]`` tables (a blank line before each); ``parse`` reads them back to the same composites."""

    lines: list[str] = []
    for composite in composites:
        lines += ["", "[[composite]]", f"id = {toml_value(composite.id)}", f"kind = {toml_value(composite.kind)}"]
        if composite.title:
            lines.append(f"title = {toml_value(composite.title)}")
        lines.append("children = [")
        lines += [f"  {toml_value(ref)}," for ref in composite.children]
        lines.append("]")
        if composite.plugin:
            lines.append(f"plugin = {toml_value(composite.plugin)}")
        if composite.expects:
            lines.append(f"expects = {toml_value(composite.expects)}")
        if composite.batch != "all":
            lines.append(f"batch = {toml_value(composite.batch)}")
        if composite.source:
            lines.append(f"source = {toml_value(composite.source)}")
    return lines


__all__ = ["COMPOSITE_KIND", "PATH_PREFIX", "BATCH_MODES", "Composite", "CompositeError", "composite_path", "classify", "parse", "check",
           "dump", "toml_value"]
