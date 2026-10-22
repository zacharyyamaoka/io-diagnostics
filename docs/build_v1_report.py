"""Build the IO diagnostics v1 handoff report: one self-contained HTML file.

    uv run --project /home/bam/io-diagnostics --with pillow python /home/bam/io-diagnostics/docs/build_v1_report.py

    --recapture     re-run the passive host captures (this PC, the live-passive drivers, F1, the fake machine) and the
                    plugins' `describe` before building; otherwise the last captures are reused (and taken if missing)
    --record-hero   re-record the dashboard hero video first (docs/record_v1_hero.js, headless Chrome, fixture F2)

Inputs, all local:
  - reports/media/v1/          the five tree proposals at the same two frames (tests/test_tree_variants.js, 100 checks)
  - reports/media/integration/ the whole-dashboard proof shots (tools/shoot_dashboard.js, shots.json)
  - reports/media/interop/     Lichtblick + ROS 2 Humble evidence (lane C)
  - reports/media/v1-report/   hero.mp4 (docs/record_v1_hero.js) and capture/*.json (taken here, see capture())
  - the GNOME Shell sandbox tray render (tools/sandbox.sh), kept as reports/media/v1-report/tray-menu.png

Output: reports/media/io-diagnostics-v1-2026-10-22.html. Every image is inlined as a WebP data URI, the videos as
MP4 data URIs. WHY WebP, crops and a 900 px cap: the desktop preview tops out near 1.9 MB for a media page, so each
capture is cut to the region that carries the point (1:1 where text must stay readable) instead of shipping 1600 px
screens. WHY the captures go through the real host: every number in the page is what `io-diagnostics once --json`
printed, never a hand-copied claim. Passive only: no CAN adapter or camera is opened by anything this script runs.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops

REPO = Path("/home/bam/io-diagnostics")
MEDIA = REPO / "reports/media"
WORK = MEDIA / "v1-report"
CAPTURE_DIR = WORK / "capture"
OUT = MEDIA / "io-diagnostics-v1-2026-10-22.html"
TREES = MEDIA / "v1"
SHOTS = MEDIA / "integration"
INTEROP = MEDIA / "interop"
SCRATCH = Path("/tmp/claude-1000/-home-bam-bam-ws--claude-worktrees-traj-sim-calibration-b4e26f/"
               "56e62701-653a-41fa-b911-d154f3596ddd/scratchpad")
TRAY_SHOTS = {"tray-f1": SCRATCH / "io-sandbox.21n1lf/out/tray-menu.png"}
LIVE_PASSIVE = REPO / "tests/fixtures/plugins-live-passive"
MACHINE_SPEC = REPO / "tests/data/sysfs-rack-fixture.json"
ACME = REPO / "examples/acme_gripper"
UV = shutil.which("uv") or "/home/bam/.local/bin/uv"
SYSTEM_PYTHON = "/usr/bin/python3"
BUDGET_BYTES = 1_900_000
BAM_WS_BRANCH = "claude/traj-tracking-sim-to-real-a3da60"

media_bytes: dict[str, int] = {}


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


# ===================================================================================================== captures ===
# What the host printed, taken through the real CLI. Each is passive: no plugin runs in "live" (both plugins.d
# declarations ship enabled = false); "live-passive" runs the real drivers with diagnose --passive behind
# passive_guard.py, which refuses anything else before the driver starts.
HOST_CAPTURES = {
    "live": ([], "this PC, no driver plugin (both plugins.d declarations ship enabled = false)"),
    "live-passive": (["--plugins", str(LIVE_PASSIVE)],
                     "this PC, the real moteus_servo + intel_rgbd plugins, diagnose --passive behind passive_guard.py"),
    "f1": (["--fixture", "F1"], "recorded frame F1 (2026-10-21 18:11, plug)"),
    "machine": (["--machine", str(MACHINE_SPEC)], "lane B's fake rack PC through the real io_facts sources"),
}


def _clean_env(extra: dict | None = None) -> dict:
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env.update(extra or {})
    return env


def _run(argv: list[str], cwd: str | Path, timeout: float, env: dict | None = None) -> str:
    done = subprocess.run(argv, cwd=str(cwd), env=_clean_env(env), capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(argv[:6])} ... exited {done.returncode}: {done.stderr[-600:]}")
    return done.stdout


def _plugin_argv(toml_path: Path) -> tuple[list[str], str]:
    """The declared command, with the host's {python} / {here} expansions (plugins.d/README.md)."""
    spec = tomllib.loads(toml_path.read_text())
    here = str(toml_path.parent)
    argv = [str(part).replace("{python}", SYSTEM_PYTHON).replace("{here}", here).replace("{root}", str(REPO))
            for part in spec["command"]]
    return argv, str(spec.get("cwd", here)).replace("{here}", here).replace("{root}", str(REPO))


def capture() -> None:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"taken": datetime.now().astimezone().isoformat(timespec="seconds"), "host": os.uname().nodename,
            "commands": {}}
    for name, (extra, why) in HOST_CAPTURES.items():
        argv = [UV, "run", "--quiet", "--project", str(REPO), "io-diagnostics", "once", "--json", *extra]
        started = time.monotonic()
        state = json.loads(_run(argv, REPO, timeout=240))
        (CAPTURE_DIR / f"{name}.json").write_text(json.dumps(state, ensure_ascii=False))
        meta["commands"][name] = {"argv": argv, "why": why, "seconds": round(time.monotonic() - started, 1)}
        print(f"captured {name}: {state['state']['chip']['label']} · {state['state']['summary'].get('devices')} devices")
    # `describe` is the passive verb (the host asks it before matching); the real drivers go through the guard anyway.
    for name, toml_path in (("moteus_servo", LIVE_PASSIVE / "moteus_servo.toml"),
                            ("intel_rgbd", LIVE_PASSIVE / "intel_rgbd.toml"),
                            ("acme_gripper", ACME / "acme_gripper.toml")):
        argv, cwd = _plugin_argv(toml_path)
        lines = [json.loads(line) for line in _run([*argv, "describe"], cwd, timeout=120).splitlines() if line.strip()]
        (CAPTURE_DIR / f"describe-{name}.json").write_text(json.dumps(lines[0], indent=1, ensure_ascii=False))
        meta["commands"][f"describe-{name}"] = {"argv": [*argv, "describe"], "cwd": cwd}
    argv, cwd = _plugin_argv(ACME / "acme_gripper.toml")
    device = {"bus": "usb", "id": "usb:1-4", "path": "host/usb1/port-1-4/usb-1-4", "label": "Acme Gripper G1",
              "vendor": "Acme", "product": "Gripper G1", "serial": "G1-0042", "parent": "usb:1", "attrs": {}}
    out = _run([*argv, "diagnose", "--device", "usb:1-4"], cwd, timeout=30,
               env={"IO_DIAGNOSTICS_DEVICE": json.dumps(device), "IO_DIAGNOSTICS_DEVICE_PATH": device["path"]})
    (CAPTURE_DIR / "diagnose-acme_gripper.jsonl").write_text(out)
    (CAPTURE_DIR / "meta.json").write_text(json.dumps(meta, indent=1))


def load_captures(recapture: bool) -> dict:
    wanted = [*(f"{name}.json" for name in HOST_CAPTURES), "describe-moteus_servo.json", "describe-intel_rgbd.json",
              "describe-acme_gripper.json", "diagnose-acme_gripper.jsonl", "meta.json"]
    if recapture or not all((CAPTURE_DIR / name).exists() for name in wanted):
        capture()
    data = {name: json.loads((CAPTURE_DIR / f"{name}.json").read_text()) for name in HOST_CAPTURES}
    data["describe"] = {name: json.loads((CAPTURE_DIR / f"describe-{name}.json").read_text())
                        for name in ("moteus_servo", "intel_rgbd", "acme_gripper")}
    data["acme_records"] = [json.loads(line) for line in
                            (CAPTURE_DIR / "diagnose-acme_gripper.jsonl").read_text().splitlines() if line.strip()]
    data["meta"] = json.loads((CAPTURE_DIR / "meta.json").read_text())
    return data


# ======================================================================================================= media ===
def _flatten(image: Image.Image) -> Image.Image:
    if image.mode in ("P", "RGBA", "LA"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", image.size, (27, 28, 31))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def _trim_bottom(image: Image.Image, margin: int = 14) -> Image.Image:
    """Cut the empty pane below the last row. WHY ignore the right 8 px: the pane's divider runs the full height."""
    probe = image.crop((0, 0, max(1, image.width - 8), image.height))
    background = probe.getpixel((4, probe.height - 4))
    difference = ImageChops.difference(probe, Image.new("RGB", probe.size, background)).convert("L")
    box = difference.point(lambda value: 255 if value > 18 else 0).getbbox()
    if not box:
        return image
    return image.crop((0, 0, image.width, min(image.height, box[3] + margin)))


def webp(path: Path, *, crop=None, max_width: int = 900, quality: int = 56, trim: bool = False,
         label: str | None = None) -> dict:
    image = _flatten(Image.open(path))
    if crop:
        image = image.crop(crop)
    if trim:
        image = _trim_bottom(image)
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=quality, method=6)
    raw = buffer.getvalue()
    media_bytes[label or path.name] = len(raw)
    return {"src": "data:image/webp;base64," + base64.b64encode(raw).decode(), "w": image.width, "h": image.height}


def video_uri(path: Path, label: str) -> str | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    media_bytes[label] = len(raw)
    return "data:video/mp4;base64," + base64.b64encode(raw).decode()


def img_tag(image: dict, alt: str, cls: str = "zoomable") -> str:
    # WHY no loading="lazy": the bytes are already in the page, and lazy data: images stay blank in print and in
    # full-page captures.
    return (f'<img class="{cls}" src="{image["src"]}" width="{image["w"]}" height="{image["h"]}" alt="{esc(alt)}" '
            f'decoding="async">')


# ================================================================================================ tree helpers ===
MARK = {"error": "✖", "warn": "▲", "unknown": "?", "stale": "◷", "ok": " ", None: " "}
LEVEL_WORD = {"error": "ERROR", "warn": "WARN", "unknown": "UNKNOWN", "stale": "STALE", "ok": "OK", None: "—"}


def walk(node: dict):
    yield node
    for child in node.get("children", []):
        yield from walk(child)


def tree_lines(node: dict, indent: int = 0, out: list | None = None, limit: int | None = None) -> list[str]:
    """The same text as `io-diagnostics once` (cli.py _print_tree), for a subtree."""
    out = [] if out is None else out
    if limit is not None and len(out) >= limit:
        return out
    hint = f"  [below: {node['hint']['level']}]" if node.get("hint") else ""
    knock = "  (knock-on)" if node.get("knock_on_of") else ""
    message = f"  — {node['message']}" if node.get("message") else ""
    out.append(f"{'  ' * indent}{MARK.get(node.get('level'), '?')} {node['name']}  {node.get('id_text') or ''}"
               f"{message}{hint}{knock}".rstrip())
    for child in node.get("children", []):
        tree_lines(child, indent + 1, out, limit)
    return out


def colour_tree(lines: list[str]) -> str:
    """Escape, then tint the status marks and hints the way the CLI means them."""
    painted = []
    for line in lines:
        text = esc(line)
        text = re.sub(r"^(\s*)✖", r'\1<b class="t-err">✖</b>', text)
        text = re.sub(r"^(\s*)▲", r'\1<b class="t-warn">▲</b>', text)
        text = re.sub(r"^(\s*)\?", r'\1<b class="t-unk">?</b>', text)
        text = re.sub(r"(\[below: \w+\])", r'<span class="t-hint">\1</span>', text)
        text = text.replace("(knock-on)", '<span class="t-knock">(knock-on)</span>')
        painted.append(text)
    return "\n".join(painted)


def path_to(tree: dict, target: str) -> list[dict]:
    chain, node = [tree], tree
    while node["path"] != target:
        nxt = next((child for child in node["children"]
                    if target == child["path"] or target.startswith(child["path"] + "/")), None)
        if nxt is None:
            raise KeyError(target)
        chain.append(nxt)
        node = nxt
    return chain


def find(tree: dict, predicate) -> dict | None:
    return next((node for node in walk(tree) if predicate(node)), None)


def badge(level, extra: str = "") -> str:
    word = LEVEL_WORD.get(level, str(level).upper())
    cls = {"error": "err", "warn": "warn", "unknown": "unk", "stale": "stale", "ok": "ok"}.get(level, "none")
    return f'<span class="lvl {cls}">{esc(word)}{esc(extra)}</span>'


# ============================================================================================== the variants ===
VARIANT_FALLBACK = {
    "v5": ("Columns tree", "The tree is also the table: one nested name column, then quiet aligned PORT · ID · RATE · "
           "LAST SEEN · FIRMWARE columns, the status shape + message last, only when the row is not OK."),
}


def variant_meta() -> dict:
    meta = {}
    for number in range(1, 6):
        key = f"v{number}"
        source = (REPO / f"io_diagnostics/web/tree/{key}.js").read_text()
        name = re.search(r"name:\s*'([^']+)'", source)
        description = re.search(r"description:\s*'((?:[^'\\]|\\.)*)'", source)
        fallback = VARIANT_FALLBACK.get(key, ("", ""))
        meta[key] = {"name": name.group(1) if name else fallback[0],
                     "description": description.group(1).replace("\\'", "'") if description else fallback[1]}
    return meta


TREE_FRAMES = (("1811", "F1 · 18:11 · plug", "CAN 15 driver selected"),
               ("1825", "F2 · 18:25 · bus busy (tview holds 272813EF)", "CAN 15 driver selected"))


def tree_tiles() -> tuple[str, str]:
    meta = variant_meta()
    report = json.loads((TREES / "tree-variants-report.json").read_text())
    tiles = []
    for key in meta:
        frames = []
        for stamp, _, _ in TREE_FRAMES:
            width = 895 if key == "v5" else 470  # v5's columns tree is twice as wide as the others' pane
            tile = webp(TREES / f"tree-{key}-{stamp}.png", crop=(0, 108, width, 968), trim=True, quality=58,
                        label=f"tree-{key}-{stamp}")
            hover = webp(TREES / f"tree-{key}-{stamp}-hover.png", quality=60, label=f"hover-{key}-{stamp}")
            frames.append(f'<div class="frame" data-frame="{stamp}">{img_tag(tile, f"{key} tree at {stamp}")}'
                          f'<div class="hover"><span class="muted">on hover</span>{img_tag(hover, f"{key} hover")}</div></div>')
        default = ' <span class="tag acc">default</span>' if key == "v1" else ""
        # WHY the description goes under the pictures: the five trees must start at the same height to compare
        tiles.append(
            f'<div class="tile" style="flex-grow:{895 if key == "v5" else 470}" data-variant="{key}">'
            f'<div class="tile-head"><label class="pickme"><input type="radio" name="tree-pick" value="{key}"'
            f'{" checked" if key == "v1" else ""}> <b>{key} · {esc(meta[key]["name"])}</b></label>{default}</div>'
            f'{"".join(frames)}<p class="tile-desc">{esc(meta[key]["description"])}</p></div>')
    # the check matrix: every check of the node run, both frames
    checks: dict[str, dict[str, list]] = {}
    for result in report["results"]:
        checks.setdefault(result["check"], {}).setdefault(result["variant"], []).append(result)
    head = "".join(f"<th>{key}</th>" for key in meta)
    rows = []
    for check, by_variant in checks.items():
        cells = []
        for key in meta:
            results = by_variant.get(key, [])
            ok = results and all(result["ok"] for result in results)
            detail = " | ".join(f'{result["frame"]}: {result["detail"]}' for result in results)
            cells.append(f'<td class="{"pass" if ok else "fail"}" title="{esc(detail)}">{"✓" if ok else "✗"}</td>')
        rows.append(f"<tr><td><code>{esc(check)}</code></td>{''.join(cells)}</tr>")
    matrix = (f'<div class="table-wrap"><table class="matrix"><thead><tr><th>check (hover a cell for the measured '
              f'detail)</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
              f'<p class="meta">{report["passed"]} passed · {report["failed"]} failed · run {esc(report["run"])} · '
              f'viewport {report["viewport"]["width"]}×{report["viewport"]["height"]} · '
              f'<code>/usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js</code></p>')
    return "".join(tiles), matrix


# =============================================================================================== the sections ===
def section_black_box(data: dict) -> str:
    tree = data["f1"]["state"]["tree"]
    chain = path_to(tree, "host/usb1/port-1-3/usb-1-3/can/15/driver")
    rows = []
    for node in chain:
        own = node.get("own") or {}
        rollup = node.get("rollup") or {}
        if own.get("level") is None:
            rule = "a pure grouping row has no self-report, so it shows the worst below"
            own_cell = '<span class="muted">none (grouping row)</span>'
        elif node.get("hint"):
            rule = "its own report wins; the worse level below is only a quiet hint"
            own_cell = f'{badge(own["level"])} {esc(own.get("message"))}'
        else:
            rule = "it reports this itself" if own.get("level") != "ok" else "its own report"
            own_cell = f'{badge(own["level"])} {esc(own.get("message"))}'
        below = (f'{badge(rollup.get("level"))} <span class="muted">{esc(rollup.get("origin_name"))}</span>'
                 if rollup.get("level") and rollup.get("origin") != node["path"] else '<span class="muted">—</span>')
        shows = badge(node.get("level")) + (f' <span class="hintpill">▾ {esc(node["hint"]["level"])} below</span>'
                                             if node.get("hint") else "")
        rows.append(f'<tr><td style="padding-left:{10 + 14 * node["depth"]}px"><b>{esc(node["name"])}</b> '
                    f'<span class="muted">{esc(node["kind"])} · {esc(node.get("edge") or "root")}</span></td>'
                    f'<td>{own_cell}</td><td>{below}</td><td>{shows}</td><td class="muted">{esc(rule)}</td></tr>')
    table = ('<div class="table-wrap"><table class="bb"><thead><tr><th>row (host → … → CAN 15 driver)</th>'
             '<th>its own self-report</th><th>worst below (rollup)</th><th>the row shows</th><th>why</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    # knock-on: the live passive run, where the bus was not queried and its boards inherit nothing
    live = data["live-passive"]["state"]
    bus = find(live["tree"], lambda node: node.get("kind") == "bus" and node.get("owner") == "moteus_servo")
    knock_rows = []
    if bus:
        names = {node["path"]: node["name"] for node in walk(live["tree"])}
        for node in [bus, *bus["children"]]:
            knock = node.get("knock_on_of")
            counted = (f'knock-on of {esc(names.get(knock, knock))} <span class="muted">(not counted)</span>' if knock
                       else "<b>the one cause</b> (counted)")
            knock_rows.append(
                f'<tr><td class="nowrap"><b>{esc(node["name"])}</b></td><td>{badge((node.get("own") or {}).get("level"))} '
                f'{esc((node.get("own") or {}).get("message"))}</td><td>{counted}</td></tr>')
    attention = live["summary"].get("attention", [])
    counted = ", ".join(esc(item["name"]) for item in attention)
    knock_table = ('<div class="table-wrap"><table><thead><tr><th>row</th><th>own report</th><th>counted as</th></tr>'
                   f'</thead><tbody>{"".join(knock_rows)}</tbody></table></div>'
                   f'<p class="meta">Chip on that run: <b>{esc(live["chip"]["label"])}</b>, counting only '
                   f'{counted}. The three boards under the unqueried bus are knock-ons, so they collapse into the '
                   'bus instead of tripling the count.</p>')
    lines = tree_lines(path_to(tree, "host/usb1/port-1-3")[-1])
    return f"""
<p>Zach's rule (2026-10-22): <b>a black box reports for itself.</b> A row draws its own entity's self-report and the host
never overrides it. What is worse further down is an advisory hint. A row with nothing to report itself (the host, a USB
bus) shows the worst thing below it. And when a parent has failed, the failures under it collapse into that one cause.
Below is the real host's output for recorded frame F1, row by row along the path to CAN 15's fault, from
<code>io-diagnostics once --json --fixture F1</code>:</p>
{table}
<div class="two">
<figure><figcaption class="top">The same subtree as the CLI prints it (✖ is the row's own error; <span class="t-hint">[below: …]</span> is the hint)</figcaption>
<pre class="tree">{colour_tree(lines)}</pre></figure>
<figure><figcaption class="top">Knock-ons collapse into one cause: this PC right now, with the real moteus_servo plugin in <code>--passive</code> mode</figcaption>
{knock_table}</figure>
</div>"""


SOURCE_INFO = {
    "io_facts.usb": ("USB", "sysfs /sys/bus/usb + usb.ids, by-id links, kernel log (plug / re-enumerate history)"),
    "io_facts.pci": ("PCI / PCIe", "sysfs /sys/bus/pci: class, driver, link width/speed vs max, AER error counters"),
    "io_facts.netdev": ("Network", "/sys/class/net: link, speed, driver; an EtherCAT master is detected, the segment is not scanned"),
    "io_facts.tty": ("Serial ttys", "/sys/class/tty + /dev/serial/by-id, hung under the USB or platform device"),
    "io_facts.v4l": ("video4linux", "/sys/class/video4linux + /dev/v4l/by-id, hung under the camera"),
}


def section_beyond_usb(data: dict, shots: dict) -> str:
    records = data["live"]["records"]
    statuses_by_path: dict[str, list] = {}
    for status in records["statuses"]:
        statuses_by_path.setdefault(status.get("path"), []).append(status)
    rows = []
    for owner, (label, how) in SOURCE_INFO.items():
        entities = [entity for entity in records["entities"] if entity.get("owner") == owner]
        if owner == "io_facts.usb":
            entities = [entity for entity in entities if entity.get("kind") == "device"]
        examples = []
        for entity in entities:
            name = entity["name"]
            if owner == "io_facts.netdev":
                message = next((status["message"] for status in statuses_by_path.get(entity["path"], [])), "")
                name = f"{name} ({message})" if message else name
            if name not in examples:
                examples.append(name)
        shown = ", ".join(esc(example) for example in examples[:6]) + (" …" if len(examples) > 6 else "")
        rows.append(f"<tr><td><b>{esc(label)}</b><br><code>{esc(owner)}</code></td><td class='num'>{len(entities)}</td>"
                    f"<td>{shown}</td><td class='muted'>{esc(how)}</td></tr>")
    sources = ('<div class="table-wrap"><table><thead><tr><th>source</th><th>rows</th><th>on this PC right now</th>'
               f'<th>what it reads</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    links = []
    for status in records["statuses"]:
        if not status.get("name", "").endswith(": PCIe link"):
            continue
        aer = {m["key"].replace("aer ", ""): m["value"] for m in status.get("measurements", []) if m["key"].startswith("aer")}
        links.append(f"<tr><td>{esc(status['name'].removesuffix(': PCIe link'))}</td><td>{esc(status['message'])}</td>"
                     f"<td class='num'>{aer.get('correctable', '—')} / {aer.get('nonfatal', '—')} / {aer.get('fatal', '—')}</td>"
                     f"<td>{badge(status.get('level'))}</td></tr>")
    machine = data["machine"]["state"]
    pci_root = find(machine["tree"], lambda node: node["name"].startswith("PCI root bus"))
    machine_lines = tree_lines(pci_root) if pci_root else []
    summary = data["live"]["state"]["summary"]
    return f"""
<p>The OS skeleton is passive and always there: <b>{summary.get('devices')} devices</b> on this PC at capture time, chip
<b>{esc(data['live']['state']['chip']['label'])}</b>, with no driver plugin running. Every source is a file read
(sysfs, by-id links, the udev database, the kernel log); nothing is opened.</p>
{sources}
<h3>PCIe link health, this PC</h3>
<p class="meta">Width below the card's maximum → WARN <code>PcieLinkNarrow</code> (speed is ignored: a GPU drops speed at
idle). AER correctable → WARN, nonfatal/fatal → ERROR. A likely frame grabber with no driver bound → WARN
<code>NoKernelDriver</code>. On this PC every link is clean, so the chip stays quiet: no false positives.</p>
<div class="table-wrap"><table><thead><tr><th>function</th><th>link</th><th>AER corr / nonfatal / fatal</th><th>level</th></tr></thead>
<tbody>{''.join(links)}</tbody></table></div>
<h3>Hardware this PC does not have: the fake rack machine</h3>
<p>Lane B's sysfs spec (<code>tests/data/sysfs-rack-fixture.json</code>) served through the same io_facts sources and the real
dashboard: a PCIe frame grabber with no kernel driver, a capture card that trained x1 of x4, two I210 NICs carrying
EtherCAT segments (detected, not scanned), and a USB-serial adapter.</p>
<figure class="shot">{img_tag(shots['machine'], 'fake machine: frame grabber detail')}<figcaption>1:1: the grabber row says it has no
kernel driver bound and 3 correctable AER errors since boot; the capture card's own row warns it trained x1 of x4.</figcaption></figure>
<div class="two">
<figure>{img_tag(shots['ethercat'], 'search EtherCAT')}<figcaption>Search "EtherCAT": both segments, under their NICs.</figcaption></figure>
<figure><figcaption class="top">The fake machine's PCI subtree, as the host printed it</figcaption><pre class="tree small">{colour_tree(machine_lines)}</pre></figure>
</div>"""


def section_live(data: dict, shots: dict) -> str:
    state = data["live-passive"]["state"]
    plugins = data["live-passive"]["plugins"]
    runs = []
    for plugin in plugins.get("plugins", []):
        jobs = [f"<code>{esc(job.get('device_id'))}</code> {esc((job.get('last_run') or {}).get('outcome'))} "
                f"<span class='muted'>({esc((job.get('last_run') or {}).get('message'))})</span>"
                for job in plugin.get("jobs", [])]
        runs.append(f"<li><b>{esc(plugin['name'])}</b> <span class='muted'>diagnose_args "
                    f"{esc(' '.join(plugin.get('diagnose_args') or []))}</span>: {'; '.join(jobs)}</li>")
    usb1 = find(state["tree"], lambda node: node["name"].startswith("USB bus 1 "))
    usb6 = find(state["tree"], lambda node: node["name"].startswith("USB bus 6 "))
    lines = tree_lines(state["tree"], limit=1)[:1]
    for node in (usb1, usb6):
        if node:
            lines += tree_lines(node, indent=1)
    lines.append("  … (USB bus 7, PCI, network, tty: see the next section)")
    return f"""
<p>This PC, right now, through the real host with the real driver plugins in <code>--passive</code> mode (sysfs, by-id
links, <code>/proc</code> holders, the rack inventory and firmware cache files; no adapter or camera opened):</p>
<ul class="tight">{''.join(runs)}</ul>
<figure class="shot">{img_tag(shots['live'], 'live tree, real drivers passive')}<figcaption>The live dashboard, 1:1 (shot by
<code>tools/shoot_dashboard.js</code>): the D405 carries intel_rgbd's identity, USB link and stream rows, and its two
registered actions say who blocks them (realsense-viewer, pid 2300632); the fdcanusb carries moteus_servo's CAN bus.</figcaption></figure>
<figure><figcaption class="top">The same tree as text, captured for this report — chip <b>{esc(state['chip']['label'])}</b>, {state['summary'].get('devices')} devices</figcaption>
<pre class="tree small">{colour_tree(lines)}</pre></figure>
<p class="meta">Why "?": in passive mode the drivers say <code>unknown</code> with code <code>PassiveOnly</code> ("the bus was
not queried"), which the contract ranks above warn, so the chip reads {esc(state['chip']['label'])} with nothing wrong.
That is decision 2 below.</p>"""


def _describe_summary(describe: dict) -> str:
    matches = describe.get("matches", [])
    match_text = ", ".join(
        esc(match if isinstance(match, str) else f"{match.get('bus')}:{match.get('vid')}:{match.get('pid')} (dict form)")
        for match in matches)
    actions = []
    for action in describe.get("actions", []):
        pre = "; ".join(esc(item[0]) for item in action.get("preconditions", []))
        actions.append(f"<tr><td><b>{esc(action.get('title'))}</b><br><code>{esc(action.get('name'))}</code></td>"
                       f"<td><span class='danger {esc(action.get('danger'))}'>{esc(action.get('danger'))}</span></td>"
                       f"<td>{esc(action.get('scope'))}</td><td>{pre}</td><td class='muted'>{esc(action.get('description'))}</td></tr>")
    action_table = (f'<div class="table-wrap"><table><thead><tr><th>action</th><th>danger</th><th>scope</th>'
                    f'<th>preconditions</th><th>what it does</th></tr></thead><tbody>{"".join(actions)}</tbody></table></div>'
                    if actions else '<p class="muted">No actions registered.</p>')
    return (f'<p><b>{esc(describe.get("name"))}</b> {esc(describe.get("version"))} · schema <code>{esc(describe.get("schema"))}</code>'
            f' · matches {match_text}</p>{action_table}'
            f'<details><summary>raw <code>describe</code> output</summary><pre>{esc(json.dumps(describe, indent=1, ensure_ascii=False))}</pre></details>')


def section_contract(data: dict) -> str:
    plugin_py = (ACME / "acme_gripper.py").read_text()
    plugin_toml = (ACME / "acme_gripper.toml").read_text()
    records = "\n".join(json.dumps({key: value for key, value in record.items() if key != "schema"}, ensure_ascii=False)
                        for record in data["acme_records"])
    return f"""
<p><b>A manufacturer writes one executable and one TOML file.</b> The executable answers three verbs with JSON lines on
stdout; the host never imports it, so its dependencies, language and crashes stay its own (it runs in the driver's own
uv environment as a subprocess). The smallest real example, <code>examples/acme_gripper/</code>, run through the real host
by <code>tests/test_integration.py</code> (which also fails if the README drifts from these files):</p>
<figure><figcaption class="top"><code>acme_gripper.py</code> — stdlib only</figcaption><pre class="code">{esc(plugin_py)}</pre></figure>
<div class="two">
<figure><figcaption class="top"><code>acme_gripper.toml</code> — dropped into <code>plugins.d/</code></figcaption><pre class="code">{esc(plugin_toml)}</pre></figure>
<figure><figcaption class="top">what <code>diagnose --device usb:1-4</code> printed (schema key dropped)</figcaption><pre class="code small wrap">{esc(records)}</pre></figure>
</div>
<div class="table-wrap"><table><thead><tr><th>verb</th><th>prints</th><th>who calls it, v1</th></tr></thead><tbody>
<tr><td><code>describe</code></td><td>one object: name, version, schema <code>diagnostics-record/0</code>, matches, registered actions</td><td>the host, once (must be passive)</td></tr>
<tr><td><code>diagnose --device &lt;id&gt;</code></td><td>JSON lines tagged <code>entity</code> / <code>status</code> / <code>event</code> (never <code>fault</code>: the host's FaultManager keeps faults)</td><td>the host, every <code>period_s</code> per matched device, killed past <code>timeout_s</code></td></tr>
<tr><td><code>act --device &lt;id&gt; --action &lt;n&gt; --confirm &lt;token&gt;</code></td><td>a result record</td><td><b>nobody in v1</b>: buttons are shown with their gates, never executed</td></tr>
</tbody></table></div>
<p class="meta">Matches: <code>usb:VID:PID</code> · <code>pci:VENDOR:DEVICE</code> · <code>pci-class:…</code> · <code>by-id:&lt;glob&gt;</code> ·
<code>tty:&lt;glob&gt;</code> · <code>v4l:&lt;glob&gt;</code> · <code>netdev:&lt;glob&gt;</code>, in the TOML or returned by <code>describe</code>.
TOML keys: name, command, cwd, timeout_s, period_s, matches, enabled, env, diagnose_args, description.
What the host does for you: the device row (port, VID:PID, serial, link speed, plug history) already exists; your rows
hang under it; a check not re-sent within max(3 × expected_period_s, 5 s) turns STALE on the host's clock; a crash, a hang
or a malformed line becomes a check on the device row, never a dead dashboard.</p>
<h3>The two real driver plugins, as they describe themselves</h3>
<p>Captured for this report through <code>passive_guard.py</code> (which lets only <code>describe</code> and
<code>diagnose --passive</code> through). These are the bam_ws files on branch <code>{BAM_WS_BRANCH}</code>.</p>
<div class="plugin-card"><h4>moteus_servo <span class="muted">— fdcanusb adapters + moteus boards</span></h4>{_describe_summary(data['describe']['moteus_servo'])}</div>
<div class="plugin-card"><h4>intel_rgbd <span class="muted">— Intel RealSense cameras</span></h4>{_describe_summary(data['describe']['intel_rgbd'])}</div>
<p class="meta">Both are declared in <code>plugins.d/</code> with <code>enabled = false</code> (their default diagnose talks to the hardware,
behind a port-holder check and the rack bus lock); <code>--enable NAME</code> turns one on for a single run.</p>"""


def section_interop(lichtblick_video: str | None, shots: dict) -> str:
    verdict = json.loads((INTEROP / "ros2/verdict.json").read_text())
    checks = "".join(f"<tr><td>{'✓' if check['passed'] else '✗'} <b>{esc(check['check'])}</b></td><td>{esc(check['detail'])}</td></tr>"
                     for check in verdict["checks"])
    echo = (INTEROP / "ros2/ros2_topic_echo.txt").read_text().splitlines()
    rows = json.loads((INTEROP / "lichtblick/lichtblick-rows.json").read_text())
    video = (f'<video class="hero" autoplay muted loop playsinline controls src="{lichtblick_video}"></video>'
             if lichtblick_video else '<p class="muted">lichtblick-hero.mp4 missing</p>')
    return f"""
<p>Decision 1 of the build plan: own the dashboard, and stay backwards compatible through <b>one pure adapter</b>
(<code>contract.record.to_diagnostic_status</code>) that strips our added fields. A stock viewer then shows our records
with zero code of ours inside it.</p>
<figure>{video}<figcaption><b>Lichtblick</b> (open source, run locally in docker): its stock Diagnostics Summary + Detail panels
on our <code>/diagnostics</code> topic, served by our foxglove-sdk bridge. {len(rows)} stock rows, levels coloured, stale rows greyed (CAN 14).</figcaption></figure>
<div class="two">
<figure>{img_tag(shots['lb-summary'], 'Lichtblick summary')}<figcaption>Lichtblick's own Summary panel, 1:1: error red (CAN 15 fault 33),
warn yellow (firmware update), OK green; the CAN 14 row is greyed by Lichtblick from the stock STALE level our adapter sends.</figcaption></figure>
<figure>{img_tag(shots['lb-stale'], 'Lichtblick stale')}<figcaption>Its Detail panel on that row: our adapter's stale message ("no update
for 52 s") and our values (serial, firmware, station) as plain stock key/values.</figcaption></figure>
</div>
<h3>ROS 2 Humble (docker, no network): {sum(c['passed'] for c in verdict['checks'])}/{len(verdict['checks'])} checks</h3>
<div class="table-wrap"><table><tbody>{checks}</tbody></table></div>
<details><summary><code>ros2 topic echo /diagnostics</code> (first 40 lines)</summary><pre class="small">{esc(chr(10).join(echo[:40]))}</pre></details>
<p class="meta">Plus <code>tests/test_integration.py</code>: the real host serves F1 → lane C's <code>--from-host</code> source → stock array
with our fields stripped → CDR and back. Not proven: <code>diagnostic_aggregator</code> (not in the image).</p>"""


# ================================================================================================ the decisions ===
DECISIONS = [
    {"id": "chip-passive", "q": "Chip noise in passive mode",
     "context": "With the drivers in --passive mode, every driver row says unknown/PassiveOnly and the chip reads 'IO 3 ⚠' with nothing wrong. The contract ranks unknown above warn, so it is honest but noisy.",
     "options": [("skip", "The host leaves code PassiveOnly out of the chip count (rows keep their ?)"), ("leave", "Leave as is")],
     "rec": "skip", "default": "leave"},
    {"id": "usb-under-pci", "q": "USB buses under their xHCI controllers?",
     "context": "Today 'USB bus 1…8' are siblings of 'PCI root bus 0000:00'; the true wire is host → PCIe → xHCI function → USB bus. Lane B already records the controller in attrs.",
     "options": [("nest", "Nest them: host → PCIe → xHCI → USB bus (v1.1)"), ("leave", "Keep them as siblings")],
     "rec": "nest", "default": "leave"},
    {"id": "active-run", "q": "Your live ACTIVE run",
     "context": "The one run that talks to the hardware is yours (hard rule). realsense-viewer (pid 2300632) holds both D405s, so intel_rgbd would report BusyHeldBy. Still to prove on the rack: bounded replies on a real fdcanusb (lane D) and the SDK path (physical_port, asic_serial vs USB serial, lane E).",
     "options": [("today", "Today: rack powered, realsense-viewer and tview closed, run the once command below"), ("later", "Later")],
     "rec": "today", "default": "later"},
    {"id": "incident", "q": "The 08:46 safety incident",
     "context": "A mutation test ran moteus_servo's ACTIVE diagnose once (~3 s, query-only, behind its gate). passive_guard.py now refuses any diagnose without --passive and any act, independent of the host; re-verified with the same mutation (refused in 0.011 s).",
     "options": [("closed", "Closed with the guard; argv mutation tests only ever run behind it"), ("more", "I want more (say what)")],
     "rec": "closed", "default": "closed"},
]


def decision_html() -> str:
    cards = []
    for number, item in enumerate(DECISIONS, start=2):
        options = "".join(
            f'<label class="opt" data-text="{esc(text)}"><input type="radio" name="{item["id"]}" value="{value}"'
            f'{" checked" if value == item["default"] else ""}> {esc(text)}'
            f'{" <span class=tag-rec>recommended</span>" if value == item["rec"] else ""}'
            f'{" <span class=tag-def>default if silent</span>" if value == item["default"] else ""}</label>'
            for value, text in item["options"])
        cards.append(f'<div class="q" data-q="{item["id"]}"><div class="qn">{number}</div><div><b>{esc(item["q"])}</b>'
                     f'<p class="muted">{esc(item["context"])}</p>{options}</div></div>')
    return "".join(cards)


NOT_DONE = [
    ("Cosmetic", "<code>--machine</code> mode's header says 'Live · sysfs-rack-fixture (fake machine)' while the clock mode is still 'live'; "
                 "the table's Status column truncates 'UNKNOWN'; moteus passive names its bus just 'CAN bus' (no adapter serial)."),
    ("Lane B", "move the sysfs helpers from <code>io_facts/skeleton.py</code> to <code>io_facts/sysfs.py</code>."),
    ("Lane C", "<code>stock.py</code> imports the contract's private <code>_stale_message</code> (needs an additive contract change to make it public; "
               "the contract is frozen at c9784d0); foxglove-sdk accepts only the <code>foxglove.sdk.v1</code> subprotocol; "
               "<code>diagnostic_aggregator</code> is not proven (not in the image)."),
    ("Lane D", "status unresolved until the live run: bounded replies on a real fdcanusb (problem 3). Sibling moteus tools still carry moteus 1.0.0 traps "
               "(<code>can_bus_triage.scan_bus</code> batches 8 ids; <code>triage()</code> and <code>list_fdcanusb.scan()</code> open several Fdcanusb in one event loop); lane D's diff is in its handoff."),
    ("Lane F", "the per-minute timeline strips were dropped (no API endpoint for them)."),
    ("bam_ws", "two <code>bam_rack_twin</code> lint tests fail on already-committed code (<code>configcheck.py:148/151</code>, <code>inventory.py:36</code>); "
               "the intel_rgbd README/DATASHEET do not mention the plugin yet."),
    ("After the bam_ws merge", "switch the worktree paths to <code>/home/bam/bam_ws/src/core/mdp/agent/…</code> in <code>plugins.d/*.toml</code>, "
                               "<code>tests/fixtures/plugins-live-passive/*.toml</code>, the README commands and <code>tools/safe_pytest_intel_rgbd.py</code>'s docstring."),
]

TESTS = [
    ("io-diagnostics pytest", "141 passed", "re-run 2026-10-22 09:10 while building this report (10.5 s); was 119 + 1 xfail before integration. 22 new in tests/test_integration.py, two of them live on bam-GPU",
     "uv run --project /home/bam/io-diagnostics --group dev --group interop pytest /home/bam/io-diagnostics/tests -q"),
    ("moteus_servo plugin tests", "35 passed", "re-run 2026-10-22 09:12 for this report; fakes only", None),
    ("intel_rgbd package suite", "125 passed · 18 skipped", "re-run 2026-10-22 09:12 for this report, through the device-less pyrealsense2 shadow (tools/safe_pytest_intel_rgbd.py)", None),
    ("tree proposals (node)", "100 / 100", "tests/test_tree_variants.js: 10 checks × 5 variants × 2 frames, measured in headless Chrome", None),
    ("dashboard proof shots (node)", "24 / 24", "tools/shoot_dashboard.js: F1, F2, live with real drivers passive, fake machine", None),
    ("hero recording", None, "docs/record_v1_hero.js: the flow in the video, asserted while it was recorded", None),
    ("ROS 2 Humble", "5 / 5", "decode, encode, schema, transport, contract + ros2 topic echo", None),
    ("Lichtblick", "10 stock rows", "colours checked per row; its own containers and ports cleaned up", None),
    ("mutation checks", "red when broken", "dropping diagnose_args, removing the intel alias, removing pci_statuses, changing the README example each turned a guarded test red", None),
]


# ===================================================================================================== the page ===
CSS = r"""
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b6760;--line:#e3dfd6;--line-strong:#c9c3b7;--panel:#fff;--panel2:#f3f1ec;
--accent:#2a7a6f;--accent-soft:#e3f1ee;--ok:#2e8b57;--warn:#b7791f;--err:#c4422f;--unk:#6d6aa8;--stale:#8a8580;--code:#f1efe9;
--danger-soft:#fbeceb;--warn-soft:#fbf3e6;--shadow:0 1px 2px rgba(0,0,0,.05)}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#141413;--fg:#ecebe7;--muted:#a5a19a;--line:#2e2d2a;
--line-strong:#46443f;--panel:#1c1b19;--panel2:#24231f;--accent:#5fb3a6;--accent-soft:#1d302d;--ok:#57c785;--warn:#f0b04a;
--err:#ef6a55;--unk:#a4a1e0;--stale:#8f8a83;--code:#1f1e1b;--danger-soft:#2b1b18;--warn-soft:#251f15;--shadow:none}}
:root[data-theme="dark"]{--bg:#141413;--fg:#ecebe7;--muted:#a5a19a;--line:#2e2d2a;--line-strong:#46443f;--panel:#1c1b19;
--panel2:#24231f;--accent:#5fb3a6;--accent-soft:#1d302d;--ok:#57c785;--warn:#f0b04a;--err:#ef6a55;--unk:#a4a1e0;--stale:#8f8a83;
--code:#1f1e1b;--danger-soft:#2b1b18;--warn-soft:#251f15;--shadow:none}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
html,body{overflow-x:clip}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Ubuntu,"Noto Sans",sans-serif}
main{max-width:1120px;margin:0 auto;padding:28px 16px 80px}
a{color:var(--accent)}
h1{font-size:30px;line-height:1.2;margin:6px 0 8px;letter-spacing:-.01em}
h2{font-size:22px;margin:56px 0 8px;padding-top:14px;border-top:1px solid var(--line)}
h3{font-size:17px;margin:26px 0 6px}
h4{font-size:16px;margin:0 0 6px}
p{margin:8px 0}
.kicker{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.muted{color:var(--muted)}
.meta{font-size:13.5px;color:var(--muted)}
.verdict{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:16px 20px;margin:18px 0;box-shadow:var(--shadow)}
.verdict .big{font-size:19px;font-weight:700;line-height:1.35;margin:0 0 8px}
.where{display:inline-block;font-size:13px;background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:4px 6px 0 0}
.incident{border:1px solid var(--err);border-left:5px solid var(--err);background:var(--danger-soft);border-radius:10px;padding:14px 18px;margin:16px 0}
.incident b.h{color:var(--err)}
.incident ul{margin:6px 0 0;padding-left:20px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:14px 0}
.cell{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;box-shadow:var(--shadow);font-size:14.5px}
.cell h3{margin:0 0 6px;font-size:15px}
.cell.live{border-top:3px solid var(--accent)} .cell.fix{border-top:3px solid var(--unk)} .cell.block{border-top:3px solid var(--warn)}
.cell ul{margin:0;padding-left:18px} .cell li{margin:3px 0}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:14px;margin:12px 0 0}
nav.toc a{text-decoration:none}
figure{margin:16px 0;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;box-shadow:var(--shadow);min-width:0}
figure img,figure video{display:block;max-width:100%;height:auto;border-radius:6px;border:1px solid var(--line);background:#111}
figcaption{font-size:13.5px;color:var(--muted);margin-top:8px}
figcaption.top{margin:0 0 8px}
video.hero{width:100%;height:auto}
figure.shot img{margin:0 auto}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
.two>*{min-width:0}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;vertical-align:top;padding:6px 10px;border-bottom:1px solid var(--line)}
th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
code,.mono,pre{font-family:ui-monospace,"JetBrains Mono",Menlo,Consolas,monospace}
code{font-size:.86em;background:var(--code);padding:1px 5px;border-radius:4px;overflow-wrap:break-word}
td:first-child code,td.nowrap{white-space:nowrap}
pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px 12px;overflow-x:auto;font-size:12.5px;line-height:1.45;margin:0}
pre code{background:none;padding:0}
pre.small,.small{font-size:11.5px}
pre.tree{white-space:pre;max-height:520px;overflow:auto}
.t-err{color:var(--err)} .t-warn{color:var(--warn)} .t-unk{color:var(--unk)} .t-hint{color:var(--muted);font-style:italic} .t-knock{color:var(--unk)}
.lvl{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:5px;color:#fff;letter-spacing:.02em;white-space:nowrap}
.lvl.ok{background:transparent;color:var(--muted);border:1px solid var(--line-strong)}
.lvl.warn{background:var(--warn)} .lvl.err{background:var(--err)} .lvl.unk{background:var(--unk)} .lvl.stale{background:var(--stale)}
.lvl.none{background:transparent;color:var(--muted)}
.hintpill{font-size:12px;color:var(--muted);border:1px dashed var(--line-strong);border-radius:999px;padding:0 7px;white-space:nowrap}
.danger{font-size:12px;font-weight:600;padding:1px 7px;border-radius:5px;border:1px solid var(--line-strong)}
.danger.query{color:var(--accent);border-color:var(--accent)} .danger.changes{color:var(--warn);border-color:var(--warn)} .danger.interrupts{color:var(--err);border-color:var(--err)}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;border:1px solid var(--line-strong);color:var(--muted)}
.tag.acc{color:var(--accent);border-color:var(--accent)}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:10px 0}
summary{cursor:pointer;font-weight:600;font-size:14px}
details pre{margin-top:8px;max-height:420px}
ul.tight{margin:6px 0;padding-left:20px} ul.tight li{margin:2px 0}
.plugin-card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:12px 0;box-shadow:var(--shadow)}
/* the five trees, side by side */
.bleed{width:calc(100vw - 32px);max-width:2400px;position:relative;left:50%;transform:translateX(-50%)}
.treebar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}
.seg{display:inline-flex;border:1px solid var(--line-strong);border-radius:8px;overflow:hidden}
.seg button{font:inherit;font-size:13.5px;padding:5px 12px;border:0;background:var(--panel);color:var(--fg);cursor:pointer}
.seg button+button{border-left:1px solid var(--line-strong)}
.seg button[aria-pressed="true"]{background:var(--accent);color:#fff}
.strip{display:flex;gap:10px;align-items:flex-start;overflow-x:auto;padding-bottom:8px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px;min-width:0;flex-basis:0}
.tile.picked{outline:2px solid var(--accent);outline-offset:1px}
.tile-head{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px;min-height:1.6em}
.tile-head .pickme{cursor:pointer}
.tile-desc{font-size:12.5px;color:var(--muted);margin:8px 0 0}
.tile img{display:block;width:100%;height:auto;border-radius:6px;border:1px solid var(--line);background:#1b1c1f}
.tile .hover{margin-top:6px;font-size:11px}
.strip .frame{display:none} .strip[data-frame="1811"] .frame[data-frame="1811"],.strip[data-frame="1825"] .frame[data-frame="1825"]{display:block}
.strip.actual .tile{flex:0 0 auto!important}
.strip.actual .tile img{width:auto;max-width:none}
.strip.actual .tile-desc{max-width:470px}
table.matrix td.pass{color:var(--ok);text-align:center} table.matrix td.fail{color:var(--err);text-align:center;font-weight:700}
table.matrix th:not(:first-child){text-align:center}
/* dashboard tabs */
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.tabs button{font:inherit;font-size:13.5px;padding:5px 12px;border:1px solid var(--line-strong);border-radius:999px;background:var(--panel);color:var(--fg);cursor:pointer}
.tabs button[aria-selected="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.tabpanel[hidden]{display:none}
/* decisions */
.q{display:grid;grid-template-columns:34px 1fr;gap:10px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:10px 0}
.qn{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700}
.q p{font-size:14px;margin:4px 0 8px}
.opt{display:block;font-size:14.5px;margin:4px 0;cursor:pointer}
.tag-rec,.tag-def{font-size:11px;font-weight:600;padding:0 7px;border-radius:999px;margin-left:4px;white-space:nowrap}
.tag-rec{color:var(--accent);border:1px solid var(--accent)} .tag-def{color:var(--muted);border:1px solid var(--line-strong)}
.copybar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}
.btn{font:inherit;font-size:14px;padding:7px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
textarea{width:100%;min-height:64px;font:13px/1.45 ui-monospace,Menlo,monospace;background:var(--code);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px}
.cmd{margin:6px 0 12px}
.cmd .lbl{font-size:14px;margin:0 0 4px}
.cmd pre{white-space:pre;overflow-x:auto}
.foot{font-size:13px;color:var(--muted);margin-top:40px}
/* lightbox */
#lightbox{position:fixed;inset:0;background:rgba(0,0,0,.86);display:none;z-index:50;overflow:auto;padding:24px;cursor:zoom-out}
#lightbox.on{display:block}
#lightbox img{display:block;margin:0 auto;max-width:none;border-radius:6px}
img.zoomable{cursor:zoom-in}
@media (max-width:860px){.two,.grid3{grid-template-columns:1fr} h1{font-size:25px} .strip:not(.actual) .tile{flex:0 0 auto!important;width:min(470px,86vw)} .bleed{width:100%;left:0;transform:none}}
"""

JS = r"""
(() => {
  const store = {
    get(key) { try { return localStorage.getItem('io-v1-report:' + key); } catch (_) { return null; } },
    set(key, value) { try { localStorage.setItem('io-v1-report:' + key, value); } catch (_) {} },
  };
  // tree strip: frame + scale
  const strip = document.getElementById('strip');
  const press = (group, value) => document.querySelectorAll(`[data-group="${group}"] button`).forEach((b) =>
    b.setAttribute('aria-pressed', String(b.dataset.value === value)));
  const setFrame = (value) => { strip.dataset.frame = value; press('frame', value); };
  const setScale = (value) => { strip.classList.toggle('actual', value === 'actual'); press('scale', value); };
  document.querySelectorAll('[data-group] button').forEach((button) => button.addEventListener('click', () => {
    const group = button.closest('[data-group]').dataset.group;
    (group === 'frame' ? setFrame : setScale)(button.dataset.value);
  }));
  setFrame('1811'); setScale(window.innerWidth < 860 ? 'actual' : 'fit');
  // tabs
  document.querySelectorAll('.tabs').forEach((tabs) => tabs.addEventListener('click', (event) => {
    const button = event.target.closest('button'); if (!button) return;
    tabs.querySelectorAll('button').forEach((b) => b.setAttribute('aria-selected', String(b === button)));
    document.querySelectorAll(`[data-tabset="${tabs.dataset.tabs}"]`).forEach((panel) => { panel.hidden = panel.dataset.tab !== button.dataset.tab; });
  }));
  // lightbox (shows the shipped pixels 1:1)
  const box = document.getElementById('lightbox'); const big = box.querySelector('img');
  document.addEventListener('click', (event) => {
    const image = event.target.closest('img.zoomable');
    if (image) { big.src = image.src; big.width = image.naturalWidth; box.classList.add('on'); return; }
    if (event.target.closest('#lightbox')) box.classList.remove('on');
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') box.classList.remove('on'); });
  // the decision packet: remembered in this browser only, copied as text for the chat
  const radios = [...document.querySelectorAll('input[type=radio]')];
  radios.forEach((radio) => {
    const saved = store.get(radio.name);
    if (saved !== null) radio.checked = radio.value === saved;
    radio.addEventListener('change', () => { store.set(radio.name, radio.value); paint(); });
  });
  const notes = document.getElementById('notes');
  notes.value = store.get('notes') || '';
  notes.addEventListener('input', () => { store.set('notes', notes.value); paint(); });
  const out = document.getElementById('packet');
  function paint() {
    const pick = document.querySelector('input[name=tree-pick]:checked')?.value || 'v1';
    const pickName = document.querySelector(`.tile[data-variant="${pick}"] .pickme b`)?.textContent || pick;
    document.querySelectorAll('.tile').forEach((tile) => tile.classList.toggle('picked', tile.dataset.variant === pick));
    const lines = ['IO diagnostics v1 review'];
    document.querySelectorAll('.q').forEach((q) => {
      const number = q.querySelector('.qn').textContent;
      const title = q.querySelector('b').textContent;
      let answer;
      if (q.dataset.q === 'tree') answer = pickName;
      else {
        const chosen = q.querySelector('input:checked');
        answer = chosen ? chosen.closest('label').dataset.text : (q.dataset.default || '-');
      }
      lines.push(`${number}. ${title}: ${answer}`);
    });
    if (notes.value.trim()) lines.push(`Notes: ${notes.value.trim()}`);
    out.value = lines.join('\n');
  }
  // WHY a nudge: some browsers hold back muted autoplay for data: videos until asked
  document.querySelectorAll('video[autoplay]').forEach((video) => { video.muted = true; video.play().catch(() => {}); });
  document.getElementById('copy').addEventListener('click', async () => {
    paint();
    try { await navigator.clipboard.writeText(out.value); document.getElementById('copied').textContent = 'copied'; }
    catch (_) { out.select(); document.getElementById('copied').textContent = 'selected: press Ctrl+C'; }
  });
  paint();
})();
"""


def command_block(label: str, command: str) -> str:
    return f'<div class="cmd"><p class="lbl">{label}</p><pre>{esc(command)}</pre></div>'


def build(data: dict) -> str:
    live_state = data["live"]["state"]
    passive_state = data["live-passive"]["state"]
    f1_state = data["f1"]["state"]
    taken = data["meta"]["taken"]
    hero_meta = json.loads((WORK / "hero.json").read_text()) if (WORK / "hero.json").exists() else {"steps": []}
    hero_pass = sum(step["ok"] for step in hero_meta["steps"])
    TESTS[5] = ("hero recording", f"{hero_pass} / {len(hero_meta['steps'])}", TESTS[5][2], None)

    hero = video_uri(WORK / "hero.mp4", "hero.mp4")
    lichtblick = video_uri(INTEROP / "lichtblick/lichtblick-hero.mp4", "lichtblick-hero.mp4")
    tiles, matrix = tree_tiles()
    shots = {
        "overview": webp(SHOTS / "f1-tree.png", quality=50, label="f1-tree overview"),
        "detail": webp(SHOTS / "f1-tree.png", crop=(470, 110, 1370, 870), quality=52, label="f1-tree detail 1:1"),
        "chip": webp(SHOTS / "f1-chip.png", crop=(1100, 28, 1600, 735), quality=56, label="f1-chip popover"),
        "table": webp(SHOTS / "f1-table.png", crop=(0, 104, 1180, 980), quality=52, label="f1-table"),
        "activity": webp(SHOTS / "f1-activity.png", crop=(0, 104, 820, 980), quality=52, label="f1-activity log"),
        "health": webp(SHOTS / "f1-activity.png", crop=(820, 104, 1600, 980), quality=52, label="f1-activity link health"),
        "action": webp(SHOTS / "f2-action-dialog.png", crop=(500, 262, 1100, 730), quality=58, label="f2-action dialog"),
        "live": webp(SHOTS / "live-tree.png", crop=(0, 30, 900, 968), quality=52, label="live-tree"),
        "machine": webp(SHOTS / "machine-beyond-usb.png", crop=(0, 30, 900, 968), quality=52, label="machine-beyond-usb"),
        "ethercat": webp(SHOTS / "machine-ethercat.png", crop=(0, 30, 600, 300), quality=60, label="machine-ethercat"),
        "lb-summary": webp(INTEROP / "lichtblick/lichtblick-summary-detail.png", crop=(322, 44, 986, 410), quality=60, label="lichtblick summary"),
        "lb-stale": webp(INTEROP / "lichtblick/lichtblick-stale-detail.png", crop=(986, 44, 1600, 300), quality=60, label="lichtblick stale"),
    }
    # WHY a copy under WORK: the GNOME Shell sandbox render lives in a session scratchpad that will not survive; the
    # copy keeps the tray tab in later rebuilds.
    tray_path = WORK / "tray-menu.png"
    if not tray_path.exists() and TRAY_SHOTS["tray-f1"].exists():
        shutil.copyfile(TRAY_SHOTS["tray-f1"], tray_path)
    if tray_path.exists():
        shots["tray"] = webp(tray_path, crop=(487, 0, 1600, 660), quality=56, label="tray menu")

    tabs = [("overview", "Whole window", "overview",
             "The real host serving recorded frame F1 (18:11), proposal v1, CAN 15 driver selected from the chip. Click to see the shipped pixels."),
            ("detail", "Detail panel 1:1", "detail",
             "Its own self-report first (shows ERROR), the worst below, identity, the fault check, the reading, the host's fault memory and the events for this row and below."),
            ("chip", "Top-bar chip", "chip", "The chip's popover: what needs you, then Recent. Each cause opens its row."),
            ("table", "Table", "table", "The same tree as a table: status, message, the worst below, last seen against the expected period, checks, faults."),
            ("activity", "Activity log", "activity", "Plugged / unplugged / re-enumerated / fault confirmed / board fault cleared, with time and port."),
            ("health", "Link health", "health", "The host's fault memory and 'the computer diagnoses its own wires': enumerations per hour and link speed per edge."),
            ("action", "Gated action (F2)", "action",
             "Recorded frame F2: tview held 272813EF. The driver registered 'Run comms check'; the confirm lists its preconditions, says it is blocked by the port holder, and the run button is disabled (v1 never executes)."),
            ]
    if "tray" in shots:
        tabs.append(("tray", "Tray (GNOME Shell)", "tray",
                     "The real AppIndicator chip in an isolated GNOME Shell sandbox (Xvfb, never on your desktop): 'IO 5 ⚠', Recent, causes, Open dashboard."))
    tab_buttons = "".join(f'<button data-tab="{key}" aria-selected="{str(index == 0).lower()}">{esc(title)}</button>'
                          for index, (key, title, _, _) in enumerate(tabs))
    tab_panels = "".join(
        f'<figure class="tabpanel" data-tabset="dash" data-tab="{key}"{"" if index == 0 else " hidden"}>'
        f'{img_tag(shots[shot], title)}<figcaption>{esc(caption)}</figcaption></figure>'
        for index, (key, title, shot, caption) in enumerate(tabs))

    hero_html = (f'<video class="hero" autoplay muted loop playsinline controls src="{hero}">'
                 f'<img src="v1-report/hero.gif" alt="hero (GIF fallback)"></video>' if hero
                 else '<p class="muted">hero.mp4 not recorded yet: /usr/bin/node /home/bam/io-diagnostics/docs/record_v1_hero.js</p>')

    tests_rows = "".join(
        f"<tr><td><b>{esc(name)}</b></td><td class='num'>{esc(result)}</td><td class='muted'>{esc(note)}</td></tr>"
        for name, result, note, _ in TESTS)
    not_done = "".join(f"<li><b>{esc(who)}:</b> {text}</li>" for who, text in NOT_DONE)
    devices_live = live_state["summary"].get("devices")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IO diagnostics v1</title>
<style>{CSS}</style></head>
<body><main>
<div class="kicker">BAM Robotics · IO diagnostics · v1 handoff · 2026-10-22</div>
<h1>IO diagnostics v1</h1>

<div class="verdict">
<p class="big">v1 runs end to end on this PC, passively: the real host builds one tree from the OS ({devices_live} devices across
USB, PCI, network, serial and video), and the real moteus_servo and intel_rgbd plugins hang their rows under it without
opening an adapter or a camera.</p>
<p>The dashboard, the chip, the table, the activity log, the gated action dialog and the GNOME tray all render from the
real host, and stock viewers (Lichtblick, ROS 2 Humble) show the same records. <b>Not proved yet:</b> the drivers'
<i>active</i> checks (CAN queries, the RealSense <code>camera_info</code> read). Those are your run to make, with the rack
powered and realsense-viewer closed (decision 4).</p>
<span class="where">io-diagnostics: main, <b>uncommitted</b></span><span class="where">bam_ws: branch <code>{BAM_WS_BRANCH}</code>, 4 untracked plugin files, <b>not merged</b></span><span class="where">no git writes anywhere</span>
</div>

<div class="incident"><b class="h">Safety incident, read first.</b> At about 08:46 PDT a mutation test removed
<code>--passive</code> from the runner. The live-passive test then ran moteus_servo's <b>ACTIVE</b> diagnose once, for about 3 s,
against fdcanusb 272813EF.
<ul>
<li>It went through the plugin's own gate: no port holder, the rack bus lock taken (LOCK_NB) and released. The checks are
query-only: id scan, reply-loss queries, <code>conf get</code>, <code>can status</code>. No board changes mode.</li>
<li>The timing suggests it really talked to the bus. Neither outcome can be proved.</li>
<li>The fix: <code>tests/fixtures/plugins-live-passive/passive_guard.py</code> now sits in front of both live-passive declarations.
It refuses any <code>diagnose</code> without <code>--passive</code> and any <code>act</code>, and it works independently of the host.
Re-running the same mutation behind it, both drivers were refused in 0.011 s and never started.</li>
<li>The rule from now on: mutation tests that touch how argv is built only run behind that guard.</li>
</ul></div>

<div class="grid3">
<div class="cell live"><h3>Live on this PC (passive)</h3><ul>
<li>OS skeleton: {devices_live} devices; chip <b>{esc(live_state['chip']['label'])}</b> with no plugin</li>
<li>PCIe link health on every function: all clean (no false positives)</li>
<li>moteus_servo: fdcanusb 272813EF → CAN bus → CAN 1 / 14 / 15 from the rack inventory</li>
<li>intel_rgbd: both D405s with identity, USB link and their colour/depth stream rows</li>
<li>chip <b>{esc(passive_state['chip']['label'])}</b> with the drivers passive (decision 2)</li></ul></div>
<div class="cell fix"><h3>Replayed from recordings</h3><ul>
<li>F1 18:11 (plug): 28B3A25D's bus error-passive, CAN 15 fault 33, Port 1-7 flapping, D405 moved 28 px</li>
<li>F2 18:25 (bus busy): tview held 272813EF, so every check and button waits</li>
<li>F3 19:02 (after a restart); recordings older than 3× their period show as STALE</li>
<li>A fake rack PC: PCIe frame grabber, capture card x1 of x4, two EtherCAT NICs</li></ul></div>
<div class="cell block"><h3>Blocked: waits on you + the rack</h3><ul>
<li>The ACTIVE driver checks (hard rule: your run)</li>
<li>realsense-viewer (pid 2300632) holds both D405s, so intel_rgbd would say BusyHeldBy</li>
<li>On the rack: bounded replies on a real fdcanusb (lane D), and the SDK path: physical_port, asic_serial vs USB serial (lane E)</li>
<li>Lane D stays "unresolved" until that run</li></ul></div>
</div>

<nav class="toc"><a href="#hero">Hero</a><a href="#trees">5 tree drawings</a><a href="#dashboard">Dashboard</a><a href="#live">Live</a>
<a href="#beyond">Beyond USB</a><a href="#blackbox">Black-box rule</a><a href="#contract">Plugin contract</a><a href="#interop">Interop</a>
<a href="#tests">Tests</a><a href="#decide">Decisions</a><a href="#notdone">Not done</a><a href="#run">Run it</a></nav>

<h2 id="hero">The dashboard, driven</h2>
<figure>{hero_html}
<figcaption>Recorded by <code>docs/record_v1_hero.js</code> from the real host serving recorded frame F2 (18:25, tview holds
272813EF), in headless Chrome. It shows: the top-bar chip and its causes; a cause opening the adapter's row (its own
self-report is <i>unknown: port held by tview</i>, with a hint that CAN 15's driver below is in error); the driver-registered
<b>Run comms check</b> button, blocked by the port holder with its run button disabled; the table; the activity log; and a
search. {hero_pass}/{len(hero_meta['steps'])} assertions passed during the recording. <a href="v1-report/hero.gif">GIF fallback</a>.</figcaption></figure>

<h2 id="trees">Five tree drawings, side by side, same frame</h2>
<p>Same host, same records, and the same row selected in all five (at 18:11 the 28B3A25D CAN bus, at 18:25 the CAN 15
driver). Only the tree renderer changes; the bottom-right switch in
the app flips between them live and remembers the choice. All five follow your rules: chevrons only on hover, the status
shape on the right, nothing green, greyed when not connected, and a row draws its own level. <b>Pick one below; v1 is the
default if you say nothing.</b></p>
<div class="treebar">
<span class="seg" data-group="frame"><button data-value="1811">F1 · 18:11 · plug</button><button data-value="1825">F2 · 18:25 · bus busy</button></span>
<span class="seg" data-group="scale"><button data-value="fit">Fit all five</button><button data-value="actual">Actual size (scroll)</button></span>
<span class="meta">click any image for its shipped pixels</span>
</div>
<div class="bleed"><div class="strip" id="strip" data-frame="1811">{tiles}</div></div>
<details><summary>The {len(json.loads((TREES / "tree-variants-report.json").read_text())["results"])} measured checks behind the five (all green)</summary>{matrix}</details>

<h2 id="dashboard">The whole dashboard</h2>
<div class="tabs" data-tabs="dash">{tab_buttons}</div>
{tab_panels}

<h2 id="live">Live on this PC, with the real drivers</h2>
{section_live(data, shots)}

<h2 id="beyond">Discovery beyond USB</h2>
{section_beyond_usb(data, shots)}

<h2 id="blackbox">The black-box rule, on a real tree</h2>
{section_black_box(data)}

<h2 id="contract">The plugin contract: what a manufacturer writes</h2>
{section_contract(data)}

<h2 id="interop">Interop: our records in stock viewers</h2>
{section_interop(lichtblick, shots)}

<h2 id="tests">Tests and evidence</h2>
<div class="table-wrap"><table><thead><tr><th>suite</th><th>result</th><th>notes</th></tr></thead><tbody>{tests_rows}</tbody></table></div>
<p class="meta">The pytest counts above were re-run while this report was built, rather than copied from a lane's
message. The node counts come from their own result files (<code>tree-variants-report.json</code>, <code>shots.json</code>,
<code>hero.json</code>, <code>verdict.json</code>).</p>

<h2 id="decide">Decisions: what needs you</h2>
<p>Each one has a recommendation and the reversible default that happens if you say nothing. Your choices are kept in this
browser only. <b>Copy</b> puts them on the clipboard as text you can paste into the chat.</p>
<div class="q" data-q="tree"><div class="qn">1</div><div><b>Tree drawing</b><p class="muted">Pick in the strip above. Recommendation: <b>v1 Quiet IDE tree</b>.
Its main job is plug-and-see (which port, what it is), so every healthy row stays visible but quiet, and the chip
already answers "what needs me". v3's healthy-row folding could become a toggle later.</p>
<span class="tag-def">default if silent: v1</span></div></div>
{decision_html()}
<div class="q" data-q="landing" data-default="as described (orchestrator commits; bam_ws branch not merged)"><div class="qn">6</div><div><b>Landing</b><p class="muted">The orchestrator commits after review. io-diagnostics goes on its
main: the code, <code>tests/fixtures/plugins/v6_frames.json</code> (271 KB), and the new <code>examples/</code> and <code>tools/</code>
files. <code>reports/media/</code> is gitignored and stays local. bam_ws gets two commits on <code>{BAM_WS_BRANCH}</code>, one per
driver, so they can be cherry-picked. Merging that branch is your call.</p><span class="tag-def">default if silent: committed on the branch, not merged</span></div></div>
<label class="meta" for="notes">Anything else (optional)</label>
<textarea id="notes" placeholder="e.g. v3, but keep v1's ids column"></textarea>
<div class="copybar"><button class="btn" id="copy">Copy my decisions</button><span class="meta" id="copied"></span></div>
<textarea id="packet" readonly></textarea>

<h2 id="notdone">Left, and deliberately not done</h2>
<p><b>Done and proved:</b> everything in the green column above, plus the integration fixes. intel_rgbd now accepts the
host's own skeleton id <code>usb:6-1</code>; before the fix, every host run ended in DeviceNotFound. <code>once</code> now
judges live records at the end of the runs, not at the start. And lane B's <code>io_facts/usb.py</code> fixes are in: a
1.5 Mb/s device no longer breaks the USB source, and hubs no longer list the camera's video nodes. The gaps closed
during the completeness check: PCIe link health (<code>tree.pci_statuses</code>), <code>--machine SPEC.json</code> for
hardware this PC lacks, <code>--enable NAME</code> and <code>diagnose_args</code>, one command through a hatchling build
backend, an end-to-end host → bridge → CDR test, and a README whose manufacturer example is tested against the files.</p>
<p><b>Not done</b>, all non-blocking:</p>
<ul class="tight">{not_done}</ul>
<p><b>Deliberately not done:</b> no git writes (the orchestrator commits after review). No active hardware run (hard rule:
yours). Actions are never executed in v1, by design. The tray was never launched on your desktop. EtherCAT segments are
detected but not scanned (as the build plan says). The record contract was not edited, because it is frozen at c9784d0.</p>

<h2 id="run">Run it</h2>
{command_block("Fixture demo: the 18:25 bus-busy frame on http://127.0.0.1:8812/ (the CAN boards turn stale about 11 s in)", "uv run --project /home/bam/io-diagnostics io-diagnostics serve --fixture F2 --offset 330")}
{command_block("Live, passive: this PC's real skeleton, no driver plugin runs", "uv run --project /home/bam/io-diagnostics io-diagnostics serve")}
{command_block("Live with the real drivers in --passive mode (behind passive_guard.py)", "uv run --project /home/bam/io-diagnostics io-diagnostics serve --plugins /home/bam/io-diagnostics/tests/fixtures/plugins-live-passive")}
{command_block("Hardware this PC lacks (frame grabber, x1-of-x4 capture card, EtherCAT NICs)", "uv run --project /home/bam/io-diagnostics io-diagnostics serve --machine /home/bam/io-diagnostics/tests/data/sysfs-rack-fixture.json")}
{command_block("The top-bar chip (tray) + dashboard", "uv run --project /home/bam/io-diagnostics io-diagnostics tray")}
{command_block("<b>Your ACTIVE run</b>: close realsense-viewer and tview first, with the rack powered", "uv run --project /home/bam/io-diagnostics io-diagnostics once --json --enable moteus_servo --enable intel_rgbd")}
{command_block("Rebuild this report (add --recapture for fresh passive captures, --record-hero for a new video)", "uv run --project /home/bam/io-diagnostics --with pillow python /home/bam/io-diagnostics/docs/build_v1_report.py")}

<p class="foot">Built by <code>docs/build_v1_report.py</code> on {esc(datetime.now().astimezone().isoformat(timespec='seconds'))}.
Host captures were taken {esc(taken)} on {esc(data['meta']['host'])} through <code>io-diagnostics once --json</code>, and
F1 was judged at its own frame clock ({esc(f1_state['clock']['iso'])}). The images are WebP crops, cut from the proof
shots named in each caption.</p>
</main>
<div id="lightbox" role="dialog" aria-label="image at shipped size"><img alt=""></div>
<script>{JS}</script>
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recapture", action="store_true", help="re-run the passive host captures and describes")
    parser.add_argument("--record-hero", action="store_true", help="re-record the hero video first (node, headless)")
    args = parser.parse_args()
    if args.record_hero:
        subprocess.run(["/usr/bin/node", str(REPO / "docs/record_v1_hero.js"), "--out", str(WORK)], check=True,
                       env=_clean_env())
    data = load_captures(args.recapture)
    page = build(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page)
    size = OUT.stat().st_size
    print(f"wrote {OUT} · {size / 1e6:.2f} MB" + ("" if size <= BUDGET_BYTES else
                                                  f" · OVER the {BUDGET_BYTES / 1e6:.1f} MB preview budget: browser only"))
    for label, count in sorted(media_bytes.items(), key=lambda item: -item[1])[:12]:
        print(f"  {count / 1e3:7.1f} kB  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
