#!/usr/bin/env python3
"""Vendor a data-ui build into io_diagnostics/web/vendor/data-ui/ (the RobotDx data-ui page, web/dx/, loads it).

    /usr/bin/python3 /home/bam/io-diagnostics/tools/vendor_data_ui.py /path/to/data-ui-tree
    /usr/bin/python3 /home/bam/io-diagnostics/tools/vendor_data_ui.py /path/to/data-ui-tree --build
    /usr/bin/python3 /home/bam/io-diagnostics/tools/vendor_data_ui.py /path/to/data-ui-tree --views all

Re-runnable: it empties the vendor folder first, copies the files the page loads from ``<tree>/dist`` byte for byte,
checks each one against ``dist/manifest.json`` (data-ui's own sha256 list: "what a host vendors, re-copies, never
edits"), and writes ``VERSION`` naming the tree's commit and, for a merge commit, each parent with the branch that
points at it (the RobotDx lane builds lane/live with lane/app-rail merged in). ``--build`` runs ``npm run build`` in
the tree first (it never installs anything).

WHY a subset of the views: the stdlib server serves ``web/`` as static files from the release snapshot, so every
vendored byte ships in every Stable build. The page needs the core, ``<data-dashboard>``, its Arrange bundle, the
row-model worker and the views a RobotDx dashboard draws; the map (1.3 MB plus a ``.mjs`` worker the server would
serve as ``application/octet-stream``), graph, timeline, scene3d, calendar, kanban and compare views are left out
until a dashboard needs one (``--views all`` takes every one). A ``.base`` that names a missing view type gets
data-ui's own "could not load the view" message, never a broken page. Source maps stay out (no page loads them).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "io_diagnostics" / "web" / "vendor" / "data-ui"
CORE_FILES = ("data-ui.js", "data-ui.css", "dashboard.js", "dashboard-arrange.js", "rowmodel-worker.js", "resize.js",
              "LICENSES.md", "manifest.json")
DEFAULT_VIEWS = ("table", "list", "cards", "kpi", "live", "playback", "record", "chart", "media")


def git(tree: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(tree), *args], check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def branch_at(tree: Path, sha: str) -> str:
    """The local branches whose tip is ``sha`` (lane/live, lane/app-rail), else ``git name-rev``'s name."""

    names = [line.strip() for line in git(tree, "branch", "--points-at", sha, "--format=%(refname:short)").splitlines()
             if line.strip()]
    return ", ".join(names) or git(tree, "name-rev", "--name-only", "--no-undefined", sha) or "(no branch)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tree", type=Path, help="a data-ui checkout or worktree")
    parser.add_argument("--build", action="store_true", help="run npm run build in the tree first")
    parser.add_argument("--views", default=",".join(DEFAULT_VIEWS),
                        help=f"view bundles to copy, comma separated, or 'all' (default: {','.join(DEFAULT_VIEWS)})")
    args = parser.parse_args(argv)
    tree = args.tree.resolve()
    if args.build:
        subprocess.run(["npm", "run", "build"], cwd=tree, check=True)
    dist = tree / "dist"
    manifest_file = dist / "manifest.json"
    if not manifest_file.is_file():
        print(f"no {manifest_file}: build the tree first (npm run build, or --build)", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_file.read_text())
    listed = manifest.get("files", {})
    folders = sorted({view["folder"] for view in manifest.get("views", [])})
    wanted = folders if args.views == "all" else [name.strip() for name in args.views.split(",") if name.strip()]
    unknown = [name for name in wanted if name not in folders]
    if unknown:
        print(f"no view bundle {unknown} in this build (it has {folders})", file=sys.stderr)
        return 2

    files = list(CORE_FILES)
    for folder in wanted:
        files.append(f"views/{folder}.js")
        # a `split: true` view's lazy chunks sit beside it as views/<folder>-<name>-<hash>.js (data-ui build.mjs)
        files += sorted(name for name in listed if name.startswith(f"views/{folder}-") and name.endswith(".js"))
    missing = [name for name in files if not (dist / name).is_file()]
    if missing:
        print(f"the build lacks {missing}", file=sys.stderr)
        return 2
    bad = [name for name in files if name != "manifest.json" and name in listed and listed[name]["sha256"] != sha256(dist / name)]
    if bad:
        print(f"these files differ from dist/manifest.json (a half-finished build?): {bad}", file=sys.stderr)
        return 2

    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    for name in files:
        target = VENDOR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dist / name, target)

    head = git(tree, "rev-parse", "HEAD")
    parents = git(tree, "log", "-1", "--format=%P", "HEAD").split()
    dirty = bool(git(tree, "status", "--porcelain", "--untracked-files=no"))
    lines = [
        f"data-ui {manifest.get('version', '?')}, vendored {time.strftime('%Y-%m-%d %H:%M %Z')} by tools/vendor_data_ui.py",
        f"tree      {tree}",
        f"commit    {head or '(not a git tree)'}{'  (the tree had uncommitted changes)' if dirty else ''}",
    ]
    if len(parents) > 1:
        lines.append("a merge of:")
        lines += [f"  {sha}  {branch_at(tree, sha)}  {git(tree, 'log', '-1', '--format=%s', sha)[:90]}" for sha in parents]
    lines += [f"views     {', '.join(wanted)}", "", "files (sha256, from the build's own dist/manifest.json):"]
    lines += [f"  {sha256(VENDOR / name)}  {name}" for name in files]
    (VENDOR / "VERSION").write_text("\n".join(lines) + "\n")
    total = sum((VENDOR / name).stat().st_size for name in files)
    print(f"vendored {len(files)} files ({total / 1024:.0f} KB) from {head[:7] or tree} into {VENDOR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
