"""Composites against the tree now: where each child is, the worst-child rollup, and the batch actions (docs/composition-v1.md).

``composite.py`` is the file; this is what the host does with it on every view. RobotDx never reads what a composite IS: the only
things here are *containment* (which entities, which composites), the *level* of each child and the *actions* each leaf offers.
Pure functions over the merged tree, so the rules are tested without a host.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Callable

from contract.record import (DANGERS, ERROR, OK, RANK, STALE, UNKNOWN, WARN, STANDARD_ACTIONS, Action, DiagnosticStatus, Entity,
                             KeyValue)
from io_diagnostics import composite as composite_module
from io_diagnostics import expected as expected_module
from io_diagnostics import tree
from io_diagnostics.composite import Composite

SOURCE = "io_diagnostics.composition"
STATUS_SUFFIX = "children"  # the rollup's check: "<title>: children"
PLUGIN_DEVICE_PREFIX = "composite:"
BATCH_PREFIX = "all:"
# WHY a list and not "every standard action": a batch is one confirm for N runs, so it covers what needs nothing typed per run.
# set_id needs a new id per board, the configuration writes are the Configuration tab's edit session, and open_console opens a
# window on the person's screen per board.
BATCHABLE = ("check_comms", "check_firmware", "update_firmware", "calibrate", "update_config", "clear_faults", "reboot", "stop_all",
             "identify")
MIN_BATCH = 2  # "<action> all 1" is the one board's own button
BATCH_TITLES = {"check_comms": "Check communication of all {n}", "check_firmware": "Check firmware of all {n}",
                "update_firmware": "Update firmware of all {n}…", "calibrate": "Calibrate all {n}…",
                "update_config": "Update configuration of all {n}…", "clear_faults": "Clear faults on all {n}",
                "reboot": "Reboot all {n}…", "stop_all": "Stop all {n}", "identify": "Identify all {n}"}
_CAN = re.compile(r"(?:^|/)can/(\d+)$")


def device_id(composite: Composite) -> str:
    """The id a composite plugin is run with (``--device composite:<id>``) and the job is keyed by."""

    return PLUGIN_DEVICE_PREFIX + composite.id


def device_dict(composite: Composite) -> dict:
    """The skeleton-shaped dict a composite plugin's job carries (``IO_DIAGNOSTICS_DEVICE``)."""

    return {"bus": "composite", "id": device_id(composite), "path": composite.id, "label": composite.label(), "vendor": None,
            "product": None, "serial": None, "parent": None,
            "attrs": {"kind": composite.kind, "composite": composite.id, "composite_kind": composite.kind}}


def by_id(composites: list[Composite]) -> dict[str, Composite]:
    return {composite.id: composite for composite in composites}


def parent_ids(composites: list[Composite]) -> dict[str, str | None]:
    """composite id -> the id of the composite that contains it, None for a root."""

    parents: dict[str, str | None] = {composite.id: None for composite in composites}
    known = by_id(composites)
    for composite in composites:
        for ref in composite.children:
            if composite_module.classify(ref) == "composite" and ref in known:
                parents[ref] = composite.id
    return parents


def preorder(composites: list[Composite]) -> list[Composite]:
    """Roots in file order, each followed by its descendants (children in their listed order): the tree's display order."""

    known, out, seen = by_id(composites), [], set()

    def visit(composite: Composite) -> None:
        if composite.id in seen:
            return
        seen.add(composite.id)
        out.append(composite)
        for ref in composite.children:
            if composite_module.classify(ref) == "composite" and ref in known:
                visit(known[ref])

    parents = parent_ids(composites)
    for composite in composites:
        if parents[composite.id] is None:
            visit(composite)
    return out


def postorder(composites: list[Composite]) -> list[Composite]:
    known, out, seen = by_id(composites), [], set()

    def visit(composite: Composite) -> None:
        if composite.id in seen:
            return
        seen.add(composite.id)
        for ref in composite.children:
            if composite_module.classify(ref) == "composite" and ref in known:
                visit(known[ref])
        out.append(composite)

    for composite in preorder(composites):
        visit(composite)
    return out


# ----- the tree: composite entities --------------------------------------------------------------------------------
def entity_of(composite: Composite, parent: Composite | None) -> Entity:
    # WHY a hardware_id: the last-known tree (roster.py) remembers a plugin's entities per (plugin, hardware_id), and two arms of one plugin
    # must not share a key; the id is repeatable (what the file says it is), like a device's
    return Entity(composite.path, composite.label(), composite_module.COMPOSITE_KIND,
                  via=parent.path if parent is not None else tree.HOST_PATH, owner=composite.plugin or "io_diagnostics",
                  hardware_id=f"composite:{composite.id}", identity={"model": composite.kind})


def add_to_layer(layer: tree.OsLayer, composites: list[Composite]) -> None:
    """The composites' rows, right under the Computer (before the OS groups), and the id -> path map of the ones with a plugin.

    WHY in the OS layer and not per view: a row's name, kind and place are the file's, they change only when the file does; the
    per-view part is its status (``evaluate``). WHY ``via`` for a nested composite and not ``part_of``: every page structure
    folds a ``part_of`` child into its parent's row as a quiet part, and a sub-composite is a row."""

    if not composites:
        return
    known = by_id(composites)
    parents = parent_ids(composites)
    ordered = preorder(composites)
    rows = {}
    for path, entity in layer.entities.items():
        rows[path] = entity
        if path == tree.HOST_PATH:
            for composite in ordered:
                parent = known.get(parents[composite.id]) if parents[composite.id] else None
                rows[composite.path] = entity_of(composite, parent)
    layer.entities = rows
    for composite in ordered:
        layer.icons[composite.path] = "group"
        if composite.plugin:
            layer.device_paths[device_id(composite)] = composite.path
            layer.device_of_path[composite.path] = device_id(composite)


# ----- where each child is now --------------------------------------------------------------------------------------
@dataclass
class Child:
    ref: str  # as the file wrote it
    kind: str  # "entity" | "composite"
    path: str | None  # its entity path now; None = not here
    name: str
    present: bool


def _absent_name(ref: str) -> str:
    """A child that is not here still gets a name in the sentence: ``CAN 15`` from ``<adapter>#can/15``, else the last word."""

    node = ref.split("#", 1)[1] if "#" in ref else ref
    found = _CAN.search(node)
    return f"CAN {found.group(1)}" if found else node.rstrip("/").rsplit("/", 1)[-1] or ref


def _holder(hardware_id: str, merged: tree.Merged, layer: tree.OsLayer | None) -> str | None:
    """The row whose ``hardware_id`` is this one: a physical device row first, else any entity that is not a part of another."""

    rows = expected_module.physical_rows(merged, layer) if layer is not None else []
    for path in rows:
        if expected_module.same_id(merged.entities[path].hardware_id, hardware_id):
            return path
    for path, entity in merged.entities.items():
        if entity.kind != composite_module.COMPOSITE_KIND and not entity.part_of and entity.hardware_id \
                and expected_module.same_id(entity.hardware_id, hardware_id):
            return path
    return None


def resolve_child(ref: str, known: dict[str, Composite], merged: tree.Merged, layer: tree.OsLayer | None) -> Child:
    kind = composite_module.classify(ref)
    if kind == "composite":
        composite = known[ref]
        return Child(ref, "composite", composite.path, composite.label(), True)
    path: str | None = None
    if kind == "path":
        path = ref if ref in merged.entities else None
    elif kind == "hardware":
        path = _holder(ref, merged, layer)
    else:  # node: <hardware_id>#<node path under that device>
        hardware_id, _, node = ref.partition("#")
        device = _holder(hardware_id, merged, layer)
        wanted = f"{device}/{node.strip('/')}" if device and node.strip("/") else None
        path = wanted if wanted in merged.entities else None
    if path is None:
        return Child(ref, "entity", None, _absent_name(ref), False)
    return Child(ref, "entity", path, merged.entities[path].name or path, True)


def resolve(composites: list[Composite], merged: tree.Merged, layer: tree.OsLayer | None) -> dict[str, list[Child]]:
    known = by_id(composites)
    return {composite.id: [resolve_child(ref, known, merged, layer) for ref in composite.children] for composite in composites}


def leaves(composite_id: str, resolved: dict[str, list[Child]]) -> list[Child]:
    """The entity children below a composite, depth first, a child composite expanded in place."""

    out: list[Child] = []
    for child in resolved.get(composite_id, []):
        if child.kind == "composite":
            out += leaves(child.ref, resolved)  # a composite child's ref is its id
        else:
            out.append(child)
    return out


def members(composite_id: str, resolved: dict[str, list[Child]]) -> list[str]:
    """The paths of the composite's own entity children that are here (the page nests their rows under it)."""

    return [child.path for child in resolved.get(composite_id, []) if child.kind == "entity" and child.present]


# ----- the rollup ---------------------------------------------------------------------------------------------------
LEVEL_CODE = {ERROR: "ChildError", WARN: "ChildWarn", UNKNOWN: "ChildUnknown"}


def _worse(first: str | None, second: str | None) -> str | None:
    if first is None:
        return second
    if second is None:
        return first
    return first if RANK[first] >= RANK[second] else second


def _as_authored(level: str | None) -> str | None:
    """A level a producer may write: derived STALE is "can't tell" for a rollup that has to be written as a status."""

    return UNKNOWN if level == STALE else level


@dataclass
class CompositeView:
    """The composites against one merged tree."""

    composites: list[Composite] = field(default_factory=list)
    resolved: dict[str, list[Child]] = field(default_factory=dict)
    statuses: list[DiagnosticStatus] = field(default_factory=list)
    levels: dict[str, str | None] = field(default_factory=dict)  # composite id -> its final level (own records and the rollup)
    by_path: dict[str, Composite] = field(default_factory=dict)
    info: dict[str, dict] = field(default_factory=dict)  # a child's path -> {"level", "message"} as its row shows it now


def _summary_level(rows: tree.Rows, path: str) -> str | None:
    summary = rows.summaries.get(path)
    if summary is None:
        return None
    # WHY the Computer counts by its own checks only: everything is below it, so its rollup is the worst fault anywhere, and a robot that
    # lists its computer ("arm + computer + camera") must not read as bad as the worst fault in the building
    return summary.own if path == tree.HOST_PATH else summary.level


def _blame(rows: tree.Rows, child: Child) -> str | None:
    """The row a problem of this child should be counted on: itself when it reports one, else the cause below it. A composite child
    always reports its own (its rollup, or its plugin's), by the time its parent is judged."""

    if child.kind == "composite":
        return child.path
    summary = rows.summaries.get(child.path)
    if summary is None:
        return None
    if summary.own not in (None, OK):
        return child.path
    return summary.rollup.origin or None


def rollup_status(composite: Composite, children: list[Child], levels: dict[str, str | None], rows: tree.Rows,
                  now: float) -> DiagnosticStatus:
    """``<title>: children``: the worst child's level, the children that report a problem named in ``caused_by``."""

    counted: list[tuple[Child, str]] = []
    absent: list[Child] = []
    for child in children:
        if not child.present:
            absent.append(child)
            continue
        level = levels.get(child.ref) if child.kind == "composite" else _summary_level(rows, child.path)
        if level is not None:
            counted.append((child, _as_authored(level)))
    worst = None
    for _child, level in counted:
        worst = _worse(worst, level)
    if absent:
        worst = _worse(worst, WARN)
    bad = [(child, level) for child, level in counted if level != OK]
    name = f"{composite.label()}: {STATUS_SUFFIX}"
    values = [KeyValue("children", "\n".join([*(f"{child.name}: {level}" for child, level in counted),
                                              *(f"{child.name}: not here" for child in absent)]) or "none"),
              KeyValue("defined_in", "expected setup (composite " + composite.id + ")")]
    if composite.source:
        values.append(KeyValue("source", composite.source))
    if not counted and not absent:
        return DiagnosticStatus(level=UNKNOWN, name=name, code="ChildrenSilent", path=composite.path, stamp=now, received=now,
                                source=SOURCE, message="none of its children reports anything yet", values=tuple(values))
    if worst in (None, OK):
        return DiagnosticStatus(level=OK, name=name, path=composite.path, stamp=now, received=now, source=SOURCE,
                                message=f"all {len(counted)} ok", values=tuple(values))
    named = [f"{child.name} {level}" for child, level in sorted(bad, key=lambda pair: -RANK[pair[1]])][:3]
    more = len(bad) - len(named)
    parts = [", ".join(named) + (f" · {more} more not ok" if more > 0 else "")] if bad else []
    if absent:
        parts.append("not here: " + ", ".join(child.name for child in absent))
    # WHY only the members that themselves report a problem (the record contract, caused_by): naming one that is OK or has no
    # record would make the whole verdict stand as a cause of its own. A member nobody reports (absent) has no row to name.
    members = [blamed for child, level in bad if (blamed := _blame(rows, child)) is not None and blamed != composite.path]
    caused = tuple(dict.fromkeys(members)) if members and not absent and len(members) == len(bad) else ()
    code = "ChildMissing" if worst == WARN and absent and not bad else LEVEL_CODE.get(worst, "ChildUnknown")
    return DiagnosticStatus(level=worst, name=name, code=code, path=composite.path, stamp=now, received=now, source=SOURCE,
                            message=" · ".join(parts), values=tuple(values), caused_by=caused)


def evaluate(composites: list[Composite], merged: tree.Merged, layer: tree.OsLayer | None, rows: tree.Rows,
             now: float) -> CompositeView:
    """The rollup status of every composite, children first. ``rows`` is the tree summarized WITHOUT these statuses: a leaf's level
    is its own, a composite's own (plugin) level is its row's, and its final level is that worse-of the rollup."""

    view = CompositeView(composites=composites, by_path={composite.path: composite for composite in composites})
    if not composites:
        return view
    view.resolved = resolve(composites, merged, layer)
    for composite in postorder(composites):
        status = rollup_status(composite, view.resolved[composite.id], view.levels, rows, now)
        view.statuses.append(status)
        own = rows.summaries.get(composite.path)
        plugin_level = _as_authored(own.own) if own is not None and own.own is not None else None
        view.levels[composite.id] = _worse(plugin_level, status.level)
        view.info[composite.path] = {"level": view.levels[composite.id], "message": status.message}
        for child in view.resolved[composite.id]:
            summary = rows.summaries.get(child.path) if child.present and child.kind == "entity" else None
            if summary is not None:
                view.info[child.path] = {"level": summary.level, "message": summary.message}
    return view


# ----- batch actions ------------------------------------------------------------------------------------------------
def node_of(path: str, action: Action) -> int | None:
    """The CAN id a node-scoped action names: the entity's own last path segment (``.../can/15`` -> 15), as the Utilities panel does."""

    if action.scope != "node":
        return None
    last = path.rsplit("/", 1)[-1]
    return int(last) if last.isdigit() else None


def batch_title(name: str, n: int) -> str:
    if name in BATCH_TITLES:
        return BATCH_TITLES[name].format(n=n)
    plain = STANDARD_ACTIONS.get(name, name).rstrip("…")
    return f"{plain} · all {n}" + ("…" if STANDARD_ACTIONS.get(name, "").endswith("…") else "")


@dataclass
class BatchItem:
    order: int
    child: Child
    action: Action
    node: int | None
    group: str = ""
    plugin: str = ""
    starts: str = "now"
    after: int = 0  # how many earlier runs of its group it waits for (0 = it starts at once)
    who: str = ""  # the board as a person tells two apart ("CAN 1 · fdcanusb 272813EF"); the host fills it
    label: str = ""  # who + the action: the run's tab title


@dataclass
class BatchPlan:
    name: str  # the leaf action's name ("calibrate")
    composite: Composite
    items: list[BatchItem] = field(default_factory=list)
    skipped: list[tuple[Child, str]] = field(default_factory=list)  # (leaf, why it is not in the batch)
    refused: str = ""  # a code, when there is no plan
    message: str = ""

    @property
    def title(self) -> str:
        return batch_title(self.name, len(self.items))

    @property
    def danger(self) -> str:
        return max((item.action.danger for item in self.items), key=lambda danger: DANGERS.index(danger) if danger in DANGERS else 0,
                   default="query")


def plan_batch(composite: Composite, name: str, resolved: dict[str, list[Child]],
               actions_of: Callable[[str], dict[str, Action]], group_of: Callable[[str], tuple[str, str]]) -> BatchPlan:
    """The N runs of ``<name> all N`` under a composite, or why there are none.

    ``actions_of(path)`` -> the leaf's registered actions by name; ``group_of(path)`` -> ``(its plugin, its exclusive group)``.
    ``batch = "all"``: a leaf that lacks the action, or is not here, refuses the plan (naming it). ``"having"``: the leaves that offer it,
    the others listed in ``skipped``. Order = the leaves' order in the file; runs of one group go one at a time in that order."""

    plan = BatchPlan(name, composite)
    if name not in BATCHABLE:
        plan.refused, plan.message = "NotBatchable", f"{name!r} is not an action a batch runs (it needs something typed per board, or opens a window)"
        return plan
    members = leaves(composite.id, resolved)
    for child in members:
        if not child.present:
            if composite.batch == "all":
                plan.items, plan.refused = [], "ChildMissing"
                plan.message = f"{child.name} is not here, so not every member can {name}"
                return plan
            plan.skipped.append((child, "not here"))
            continue
        action = actions_of(child.path).get(name)
        if action is None:
            if composite.batch == "all":
                plan.items, plan.refused = [], "ChildLacksAction"
                plan.message = f"{child.name} offers no {name}, so not every member can"
                return plan
            plan.skipped.append((child, f"offers no {name}"))
            continue
        plugin, group = group_of(child.path)
        plan.items.append(BatchItem(len(plan.items) + 1, child, action, node_of(child.path, action), group, plugin))
    if len(plan.items) < MIN_BATCH:
        plan.refused = "TooFew"
        plan.message = f"{len(plan.items)} member{'s' if len(plan.items) != 1 else ''} can {name}: use its own button"
        return plan
    earlier: dict[str, int] = {}
    for item in plan.items:
        if item.group:
            before = earlier.get(item.group, 0)
            item.after = before
            item.starts = "now" if before == 0 else f"after {before} earlier run{'s' if before != 1 else ''} of {item.group}"
            earlier[item.group] = before + 1
    return plan


def offered(composite: Composite, resolved: dict[str, list[Child]], actions_of: Callable[[str], dict[str, Action]],
            group_of: Callable[[str], tuple[str, str]]) -> list[BatchPlan]:
    """The batch plans a composite offers: one per batchable action name its leaves offer, in the vocabulary's order."""

    members = [child for child in leaves(composite.id, resolved) if child.present]
    names = [name for name in BATCHABLE if any(name in actions_of(child.path) for child in members)]
    plans = [plan_batch(composite, name, resolved, actions_of, group_of) for name in names]
    return [plan for plan in plans if not plan.refused]


def matches_kind(patterns, kind: str) -> bool:
    return any(fnmatch.fnmatchcase(kind, pattern) for pattern in patterns)


__all__ = ["SOURCE", "BATCH_PREFIX", "BATCHABLE", "Child", "CompositeView", "BatchPlan", "BatchItem", "device_id", "device_dict",
           "by_id", "parent_ids", "preorder", "postorder", "add_to_layer", "resolve", "leaves", "members", "evaluate", "rollup_status", "plan_batch", "offered",
           "node_of", "batch_title"]
