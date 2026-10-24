"""Write the ``[[composite]]`` tables of a Cad2Robot bundle (docs/composition-v1.md section 8). A STUB: one robot per bundle.

A ``cad2robot/1.x`` bundle's ``robot.json`` lists its ``joints`` (a ``name``, a ``can_id``, sometimes the ``bus`` / board they sit on) in
kinematic order. The joint is a board on a CAN bus, so the composition file's child for it is ``<adapter hardware_id>#can/<can_id>``.
Which adapter: ``--bus NAME=HARDWARE_ID`` for the joint's own ``bus`` word, else ``--adapter HARDWARE_ID`` for all of them, else the rig in
the expected setup (``--expected FILE``) whose device lists ``can/<can_id>`` among its nodes: one adapter that does is the answer, two or none
is not guessed, the joint is written as a comment for the person to place. WHY the expected setup: it already says which boards are on
which adapter, so a bundle and the rack's own file agree for free.

    /usr/bin/python3 /home/bam/io-diagnostics/tools/cad2robot_composites.py /path/to/robot.json --expected ~/.config/io-diagnostics/expected.toml

prints the tables (a paste, or ``--write FILE`` to put them in a composition file: the composites that carry the same ``source`` are
replaced, everything else is kept; the file is rewritten whole, as the panel's edits do). ``--group arm=revolute_1,revolute_2`` makes a
sub-composite of kind ``arm`` of those joints: the bundle itself carries no arm / leg grouping, so the person says it. It never touches a
device and never reads a Cad2Robot file other than ``robot.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from io_diagnostics import composite as composite_module  # noqa: E402
from io_diagnostics import expected as expected_module  # noqa: E402
from io_diagnostics import tree  # noqa: E402


class BundleError(ValueError):
    pass


def load_bundle(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise BundleError(f"{path}: not a readable JSON file ({error})") from error
    if not isinstance(data, dict) or not str(data.get("schema", "")).startswith("cad2robot/"):
        raise BundleError(f"{path}: schema {data.get('schema') if isinstance(data, dict) else None!r} is not a cad2robot/1.x bundle")
    joints = data.get("joints")
    if not isinstance(joints, list) or not joints:
        raise BundleError(f"{path}: no joints")
    return data


def adapters_for(can_id: int, manifest: expected_module.Manifest | None) -> list[str]:
    """The hardware ids of every rig device whose nodes list ``can/<can_id>``."""

    found = []
    for _rig, device in (manifest.entries() if manifest else []):
        if device.hardware_id and any(node.path == f"can/{can_id}" for node in device.nodes):
            found.append(device.hardware_id)
    return list(dict.fromkeys(found))


def place(joint: dict, bus_map: dict[str, str], adapter: str, manifest: expected_module.Manifest | None) -> tuple[str | None, str]:
    """``(child reference, why not)`` for one joint."""

    can_id = joint.get("can_id")
    name = joint.get("name", "?")
    if isinstance(can_id, bool) or not isinstance(can_id, int):
        return None, f"{name}: no can_id (not a powered joint)"
    chosen = bus_map.get(str(joint.get("bus"))) if joint.get("bus") else None
    if chosen is None and adapter:
        chosen = adapter
    if chosen is None:
        candidates = adapters_for(can_id, manifest)
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            why = "no rig in the expected setup lists it" if not candidates else f"{len(candidates)} adapters list it ({', '.join(candidates)})"
            return None, f"{name}: can/{can_id} is on an adapter I cannot tell: {why}; pass --adapter or --bus"
    return f"{chosen}#can/{can_id}", ""


def slug(text: str) -> str:
    return tree.slug(str(text).strip().lower().replace(" ", "-")).strip("-.") or "robot"


def build(bundle: dict, *, bus_map: dict[str, str], adapter: str, manifest: expected_module.Manifest | None, groups: dict[str, list[str]],
          robot_id: str = "", title: str = "") -> tuple[list[composite_module.Composite], list[str]]:
    """``(composites, notes)``: the robot composite (and a sub-composite per ``--group``), and a note per joint that could not be placed."""

    design = str(bundle.get("design") or "robot")
    source = f"cad2robot {bundle.get('schema')} {design}"
    robot_id = robot_id or slug(design)
    refs: dict[str, str] = {}
    notes: list[str] = []
    for joint in bundle["joints"]:
        ref, why = place(joint, bus_map, adapter, manifest)
        if ref is None:
            notes.append(why)
        else:
            refs[str(joint.get("name"))] = ref
    unknown = sorted({name for names in groups.values() for name in names} - {str(joint.get("name")) for joint in bundle["joints"]})
    if unknown:
        raise BundleError(f"--group names joints the bundle does not have: {', '.join(unknown)}")
    composites, children, grouped = [], [], set()
    for group, names in groups.items():
        placed = [refs[name] for name in names if name in refs]
        if not placed:
            notes.append(f"group {group}: none of its joints could be placed")
            continue
        grouped.update(names)
        composites.append(composite_module.Composite(id=f"{robot_id}/{slug(group)}", kind=slug(group), title=group.replace("-", " ").title(),
                                                     children=placed, source=source))
        children.append(composites[-1].id)
    for joint in bundle["joints"]:
        name = str(joint.get("name"))
        if name in refs and name not in grouped:
            children.append(refs[name])
    if not children:
        raise BundleError("no joint could be placed on an adapter: " + "; ".join(notes))
    robot = composite_module.Composite(id=robot_id, kind="robot", title=title or design.replace("_", " "), children=children, source=source)
    return [robot, *composites], notes


def write_into(path: Path, composites: list[composite_module.Composite]) -> str:
    """Put the tables in the composition file: those with the same ``source`` replaced, the rest kept, the file rewritten whole."""

    setup = expected_module.ExpectedSetup(path)
    if setup.note:
        raise BundleError(f"{path} does not read ({setup.note}); not rewritten")
    manifest = setup.editable() if path.exists() else expected_module.Manifest()
    sources = {item.source for item in composites if item.source}
    kept = [item for item in manifest.composites if item.source not in sources]
    replaced = len(manifest.composites) - len(kept)
    manifest.composites = [*kept, *composites]
    problems = composite_module.check(manifest.composites)
    if problems:
        raise BundleError(f"the result would not load, so {path} is not written: " + "; ".join(problems))
    setup.path = path
    setup.save(manifest)
    return f"{path}: {len(composites)} composite(s) written, {replaced} of the same source replaced, {len(kept)} others kept"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="the [[composite]] tables of a Cad2Robot bundle (docs/composition-v1.md section 8)")
    parser.add_argument("robot_json", type=Path)
    parser.add_argument("--expected", type=Path, help="an expected setup whose rigs say which adapter each can/<id> is on")
    parser.add_argument("--adapter", default="", help="the adapter's hardware_id for every joint (usb:0483:5740:<serial>)")
    parser.add_argument("--bus", action="append", default=[], metavar="NAME=HARDWARE_ID", help="the adapter for a joint whose bundle 'bus' is NAME")
    parser.add_argument("--group", action="append", default=[], metavar="NAME=JOINT,JOINT", help="a sub-composite (kind NAME) of those joints")
    parser.add_argument("--id", default="", help="the robot composite's id (default: the design's name)")
    parser.add_argument("--title", default="")
    parser.add_argument("--write", type=Path, help="put the tables in this composition file instead of printing them")
    args = parser.parse_args(argv)
    try:
        bundle = load_bundle(args.robot_json)
        manifest = expected_module.parse(args.expected.read_text()) if args.expected else None
        bus_map = dict(item.split("=", 1) for item in args.bus)
        groups = {name: [joint for joint in joints.split(",") if joint] for name, joints in (item.split("=", 1) for item in args.group)}
        composites, notes = build(bundle, bus_map=bus_map, adapter=args.adapter, manifest=manifest, groups=groups, robot_id=args.id,
                                  title=args.title)
        if args.write:
            print(write_into(args.write, composites))
        else:
            print(f"# generated from {args.robot_json.name}: {composites[0].source}")
            print("\n".join(composite_module.dump(composites)).lstrip("\n"))
        for note in notes:
            print(f"# not placed: {note}", file=sys.stderr if args.write else sys.stdout)
    except (BundleError, expected_module.ManifestError, ValueError, OSError) as error:
        print(f"cad2robot_composites: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
