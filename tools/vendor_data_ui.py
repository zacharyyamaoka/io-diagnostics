#!/usr/bin/env python3
"""Vendor a data-ui build into io_diagnostics/web/vendor/data-ui/ (the RobotDx data-ui page, web/dx/, loads it).

    /usr/bin/python3 /home/bam/io-diagnostics/tools/vendor_data_ui.py /path/to/data-ui-tree
    /usr/bin/python3 /home/bam/io-diagnostics/tools/vendor_data_ui.py /path/to/data-ui-tree --build
    /usr/bin/python3 /home/bam/io-diagnostics/tools/vendor_data_ui.py /path/to/data-ui-tree --views all
    /usr/bin/python3 /home/bam/io-diagnostics/tools/vendor_data_ui.py /home/bam/data-ui --ref integrate/packet

Re-runnable: it empties the vendor folder first, copies the files the page loads from ``<tree>/dist`` byte for byte,
checks each one against ``dist/manifest.json`` (data-ui's own sha256 list: "what a host vendors, re-copies, never
edits"), and writes ``VERSION`` naming the tree's commit and, for a merge commit, each parent with the branch that
points at it (the RobotDx lane builds lane/live with lane/app-rail merged in). ``--build`` runs ``npm run build`` in
the tree first (it never installs anything).

``--ref REF`` builds a commit of the data-ui repository ``tree`` without checking it out anywhere: the commit's files are
exported (``git archive``) into ``--work`` (default: a new temporary folder, removed afterwards), its ``node_modules`` is
a link to a worktree of the same repository whose ``package-lock.json`` is byte for byte the commit's (or
``--node-modules``; nothing is installed), ``npm run build`` runs there, and ``VERSION`` names the ref and its commit.
WHY an export and not a worktree or a checkout: the repository's own checkouts and worktrees belong to other sessions,
and a build writes ``dist/``; an export writes nothing into the repository.

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
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
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


def node_modules_for(repo: Path, lock: bytes) -> Path | None:
    """A ``node_modules`` installed for this ``package-lock.json``: the first worktree of ``repo`` whose lock is the same
    bytes and that has one (``git worktree list``; the main checkout is the first listed)."""

    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if not line.startswith("worktree "):
            continue
        tree = Path(line[len("worktree "):])
        modules, theirs = tree / "node_modules", tree / "package-lock.json"
        if modules.is_dir() and theirs.is_file() and theirs.read_bytes() == lock:
            return modules
    return None


def export_ref(repo: Path, sha: str, work: Path, modules: Path | None) -> Path:
    """The files of commit ``sha`` in ``work`` (``git archive``), with ``node_modules`` linked; returns ``work``."""

    archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", sha], check=True, capture_output=True).stdout
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise SystemExit(f"--work {work} is not empty: give an empty or a new folder")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(work, filter="data")
    lock = (work / "package-lock.json").read_bytes() if (work / "package-lock.json").is_file() else b""
    modules = modules or node_modules_for(repo, lock)
    if modules is None:
        raise SystemExit(f"no worktree of {repo} has a node_modules for {sha[:7]}'s package-lock.json: install one there first "
                         "(npm ci in that worktree) or pass --node-modules")
    (work / "node_modules").symlink_to(modules.resolve(), target_is_directory=True)
    return work


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tree", type=Path, help="a data-ui checkout or worktree (with --ref: any checkout of the data-ui repository)")
    parser.add_argument("--build", action="store_true", help="run npm run build in the tree first")
    parser.add_argument("--ref", help="build this branch, tag or commit of the repository instead of the tree's own files")
    parser.add_argument("--work", type=Path, help="with --ref: the (empty or new) folder to export and build in; kept afterwards")
    parser.add_argument("--node-modules", type=Path, help="with --ref: the node_modules to link (default: a worktree's with the same lock)")
    parser.add_argument("--views", default=",".join(DEFAULT_VIEWS),
                        help=f"view bundles to copy, comma separated, or 'all' (default: {','.join(DEFAULT_VIEWS)})")
    args = parser.parse_args(argv)
    repo = tree = args.tree.resolve()
    ref = sha = None
    temp = None
    if args.ref:
        sha = git(repo, "rev-parse", "--verify", f"{args.ref}^{{commit}}")
        if not sha:
            print(f"{args.ref} is not a commit of {repo}", file=sys.stderr)
            return 2
        ref = args.ref
        if args.work is None:
            temp = tempfile.TemporaryDirectory(prefix="data-ui-ref-")
        tree = export_ref(repo, sha, (args.work.resolve() if args.work else Path(temp.name) / "tree"), args.node_modules)
    try:
        return vendor(tree, repo, ref, sha, build=args.build or bool(ref), views=args.views)
    finally:
        if temp is not None:
            temp.cleanup()


def vendor(tree: Path, repo: Path, ref: str | None, sha: str | None, *, build: bool, views: str) -> int:
    """Copy ``tree``'s build into the vendor folder; ``ref``/``sha`` name a commit exported from ``repo`` (else ``tree``'s HEAD)."""

    if build:
        subprocess.run(["npm", "run", "build"], cwd=tree, check=True)
    dist = tree / "dist"
    manifest_file = dist / "manifest.json"
    if not manifest_file.is_file():
        print(f"no {manifest_file}: build the tree first (npm run build, or --build)", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_file.read_text())
    listed = manifest.get("files", {})
    folders = sorted({view["folder"] for view in manifest.get("views", [])})
    wanted = folders if views == "all" else [name.strip() for name in views.split(",") if name.strip()]
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

    if ref:
        # an exported commit: the repository names it, and the export is exactly the commit (never dirty)
        head, source, dirty = sha, repo, False
        lines_head = [f"repo      {repo}", f"ref       {ref}  (exported with git archive and built by this tool)"]
    else:
        head, source = git(tree, "rev-parse", "HEAD"), tree
        dirty = bool(git(tree, "status", "--porcelain", "--untracked-files=no"))
        lines_head = [f"tree      {tree}"]
    parents = git(source, "log", "-1", "--format=%P", head).split() if head else []
    lines = [
        f"data-ui {manifest.get('version', '?')}, vendored {time.strftime('%Y-%m-%d %H:%M %Z')} by tools/vendor_data_ui.py",
        *lines_head,
        f"commit    {head or '(not a git tree)'}{'  (the tree had uncommitted changes)' if dirty else ''}",
    ]
    if len(parents) > 1:
        lines.append("a merge of:")
        lines += [f"  {parent}  {branch_at(source, parent)}  {git(source, 'log', '-1', '--format=%s', parent)[:90]}" for parent in parents]
    lines += [f"views     {', '.join(wanted)}", "", "files (sha256, from the build's own dist/manifest.json):"]
    lines += [f"  {sha256(VENDOR / name)}  {name}" for name in files]
    (VENDOR / "VERSION").write_text("\n".join(lines) + "\n")
    total = sum((VENDOR / name).stat().st_size for name in files)
    print(f"vendored {len(files)} files ({total / 1024:.0f} KB) from {head[:7] or tree} into {VENDOR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
