"""Build the IO diagnostics round-2 handoff report: one self-contained HTML file.

    uv run --project /home/bam/io-diagnostics --with pillow python /home/bam/io-diagnostics/docs/build_round2_report.py

Inputs, all local (taken by the orchestrator against the REAL rack, read-only):
  - reports/media/round2/live/*.png        headless captures of the live dashboard (scratch live_capture.js)
  - reports/media/round2/live/hero.mp4     the hero video, same run
  - reports/media/round2/live/dry_runs.json  every utility's dry run through `io-diagnostics act --dry-run`
  - reports/media/round2/live/state.json   GET /api/v1/state of the live server at capture time

Output: reports/media/io-diagnostics-round2-2026-10-22.html. Images are inlined as WebP data URIs and the video as
an MP4 data URI (helpers shared with docs/build_v1_report.py). Nothing here opens a CAN adapter or a camera.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

REPO = Path("/home/bam/io-diagnostics")
LIVE = REPO / "reports/media/round2/live"
OUT = REPO / "reports/media/io-diagnostics-round2-2026-10-22.html"
BAM_WS = Path("/home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack")

_spec = importlib.util.spec_from_file_location("v1_report", REPO / "docs/build_v1_report.py")
v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1)
esc, webp, img_tag, video_uri = v1.esc, v1.webp, v1.img_tag, v1.video_uri

STRUCTURES = [
    ("p1", "Devices, transport as columns (default)",
     "Only the things you plugged in: the adapter holds its boards. The USB / PCIe path is a quiet Connection column "
     "and tooltip; the Ports toggle brings the layers back."),
    ("p2", "Two views, one toggle",
     "Devices by what they are for (Actuators / Cameras / Network / Computer) or by connection, like Windows Device "
     "Manager's two views; the same row stays selected across the toggle."),
    ("p3", "Path on demand",
     "The entity tree, with the connection path as a breadcrumb under the selected row, a Path column, and a Paths "
     "toggle."),
    ("p4", "Compact folders",
     "Today's physical tree with every single-child chain joined into one row (VS Code's compact folders); tty and "
     "video nodes become attributes."),
    ("p5", "Bus lens",
     "The buses first (CAN · fdcanusb 272813EF, USB cameras, PCIe, Ethernet), the computer's own topology folded into "
     "one Computer row."),
    ("p0", "Physical (round 1)", "Round 1's physical tree, kept for comparison."),
]
SKINS = [("claude", "Claude (default)"), ("obsidian", "Obsidian"), ("notion", "Notion"), ("terminal", "Terminal"),
         ("foxglove", "Foxglove / medkit"), ("shadcn", "shadcn/ui"), ("unstyled", "Unstyled (bring your own)"),
         ("isa101", "ISA-101 HMI"), ("macos", "macOS Settings"), ("linear", "Linear"), ("contrast", "High contrast")]
ALIASES = {
    "check_comms": "d_find + d_triage", "stop_all": "d_stop", "open_console": "d_tview", "calibrate": "d_calibrate",
    "update_config": "d_config (its script is gone; stock moteus_tool --dump-config / --restore-config)",
    "set_id": "d_reassign", "update_firmware": "the update-moteus-firmware skill",
}


def head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True).stdout.strip()


def shot(name: str, **kwargs) -> str:
    path = LIVE / f"{name}.png"
    if not path.exists():
        return f'<p class="muted">missing capture: {esc(path)}</p>'
    return img_tag(webp(path, label=name, **kwargs), name)


def figure(name: str, caption: str, **kwargs) -> str:
    return f'<figure>{shot(name, **kwargs)}<figcaption>{caption}</figcaption></figure>'


def buttons_table(dry_runs: list[dict]) -> str:
    rows = []
    for run in dry_runs:
        where = "RealSense D405 · 6036" if "usb6" in run["path"] else ("fdcanusb 272813EF" + (f" · CAN {run['node']}" if run["node"] else ""))
        command = " ".join(map(str, run["command"] or [])) or (run.get("refused") or run.get("message") or "")
        command = command.replace(str(BAM_WS / "src/core/mdp/agent/actuators/moteus_servo/.venv/bin/python"), "py")
        alias = ALIASES.get(run["action"], "") if "usb6" not in run["path"] else ""
        rows.append(f"<tr><td><b>{esc(run.get('title') or run['action'])}</b><br><span class='muted'>{esc(where)}</span></td>"
                    f"<td><span class='danger d-{esc(run.get('danger') or 'none')}'>{esc(run.get('danger') or '—')}</span></td>"
                    f"<td><code>{esc(command)}</code></td><td class='muted'>{esc(alias)}</td></tr>")
    return ("<div class='table-wrap'><table><thead><tr><th>Button</th><th>Danger</th><th>Exact command its dry run "
            "reports</th><th>Replaces</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


CSS = r"""
:root{--bg:#faf9f5;--surface:#fff;--text:#2b2a27;--muted:#6b6a64;--border:#e6e3da;--accent:#c96442;--err:#b3261e;--warn:#8a5a00;--mov:#9c2b2b;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1f1e1b;--surface:#262521;--text:#ecebe6;--muted:#a8a69e;--border:#3a3934;--accent:#e08a6a;--err:#f2867d;--warn:#e7b65c;--mov:#f29b9b;}}
:root[data-theme="dark"]{--bg:#1f1e1b;--surface:#262521;--text:#ecebe6;--muted:#a8a69e;--border:#3a3934;--accent:#e08a6a;--err:#f2867d;--warn:#e7b65c;--mov:#f29b9b;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 80px}h1{font:600 30px/1.2 Georgia,serif;margin:0 0 6px}h2{font:600 22px/1.25 Georgia,serif;margin:44px 0 10px;padding-top:10px;border-top:1px solid var(--border)}
h3{font-size:16px;margin:22px 0 6px}.verdict{font-size:18px;font-weight:600;margin:10px 0 4px}.muted{color:var(--muted)}
figure{margin:12px 0;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:8px}figure img,video{width:100%;height:auto;display:block;border-radius:6px}
figcaption{font-size:13px;color:var(--muted);padding:6px 4px 2px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.grid figure{margin:0}.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:800px){.two{grid-template-columns:1fr}}
.table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:10px;background:var(--surface)}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;vertical-align:top;padding:7px 10px;border-bottom:1px solid var(--border)}code{font:12px/1.4 ui-monospace,Menlo,monospace;word-break:break-word}
.danger{font:600 11px/1 ui-monospace,monospace;text-transform:uppercase;padding:3px 6px;border-radius:5px;border:1px solid var(--border)}
.d-moves{color:var(--mov);border-color:var(--mov)}.d-changes{color:var(--warn)}.d-interrupts{color:var(--warn)}.d-query{color:var(--muted)}
ul.tight li{margin:3px 0}.pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid var(--border);color:var(--muted)}
"""


def build() -> str:
    dry_runs = json.loads((LIVE / "dry_runs.json").read_text()) if (LIVE / "dry_runs.json").exists() else []
    state = json.loads((LIVE / "state.json").read_text()) if (LIVE / "state.json").exists() else {}
    taken = (state.get("clock") or {}).get("iso", "?")
    hero = video_uri(LIVE / "hero.mp4", "hero.mp4")
    hero_html = (f'<video class="hero" autoplay muted loop playsinline controls src="{hero}"></video>' if hero
                 else '<p class="muted">hero.mp4 missing</p>')
    io_head, ws_head = head(REPO), head(BAM_WS)
    structures = "".join(
        # WHY the tree pane 1:1: the structure is the tree; scaled whole-window shots were unreadable
        f'<figure>{shot(f"structure-{sid}", crop=(0, 212, 470, 980), max_width=470, quality=60)}<figcaption><b>{esc(sid)} · {esc(name)}</b> — {esc(text)}</figcaption></figure>'
        for sid, name, text in STRUCTURES)
    skins = "".join(
        f'<figure>{shot(f"skin-{sid}-light", crop=(0, 212, 900, 620), max_width=560, quality=52)}'
        f'{shot(f"skin-{sid}-dark", crop=(0, 212, 900, 620), max_width=560, quality=52)}<figcaption><b>{esc(name)}</b></figcaption></figure>'
        for sid, name in SKINS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IO diagnostics round 2</title><style>{CSS}</style></head><body><main>
<h1>IO diagnostics · round 2</h1>
<p class="verdict">Plug the rack in and the panel says what is connected, what isn't, and what to check, in plain words.
Printer-style utilities wrap your d_* tools, and every button dry-runs first.</p>
<p class="muted">io-diagnostics main @ {esc(io_head)} (local repo) · drivers on bam_ws <code>claude/traj-tracking-sim-to-real-a3da60</code> @ {esc(ws_head)}, not merged ·
live captures {esc(taken)}, real rack, read-only. No button that writes or moves was pressed.</p>
{hero_html}
<p class="muted">Hero: the live dashboard on this PC.
<ul class="tight"><li>fdcanusb 272813EF holds CAN 1 / 14 / 15.</li><li>CAN 1 → Run calibration… dry run, showing the exact command and preconditions.</li><li>The confirm modal won't enable until you tick "joint is free".</li><li>Cancel.</li><li>The second adapter 28B3A25D: "frames never reached the bus" and what to check.</li></ul></p>

<h2>The rack, live</h2>
<div class="two">{figure("tree-light", "Default view (structure P1, tree v6, skin Claude): entities only. The adapter holds its boards; the computer is not a fold.", crop=(0, 30, 1600, 1000))}
{figure("adapter-28B3A25D", "The second adapter: on USB, but its frames never reach the CAN wire (8,192 broadcast queries left lec 7 / dlec 7, err 0). The panel says so and lists what to unplug and measure.", crop=(0, 30, 1600, 1000))}</div>
<div class="two">{figure("adapter-272813EF", "fdcanusb 272813EF selected: the Utilities card, printer-style. Checks, then Maintenance, then Motion set apart.", crop=(460, 30, 1600, 1000))}
{figure("can1-calibrate-dry-run", "CAN 1 → Run calibration…: the dry run shows the exact moteus_tool command, each precondition with its state, and the confirm text naming CAN 1's hard stop and gravity load.", crop=(460, 30, 1600, 1000))}</div>
<div class="two">{figure("can1-calibrate-modal", "Motion needs a ticked physical precondition before the button enables. The capture pressed Cancel.", crop=(400, 150, 1200, 850))}
{figure("table-light", "The table uses the same rows as the tree, and the Message column no longer collapses (your screenshot showed “ME”).", crop=(0, 30, 1600, 1000))}</div>

<h2>Every button, and what it would run</h2>
<p>These are real dry runs through the host (<code>io-diagnostics act … --dry-run</code>) against this PC's adapter and camera. Nothing ran.
Same name = same meaning on every driver (<code>docs/actions-vocabulary-v0.1.md</code>).</p>
{buttons_table(dry_runs)}

<h2>Five ways to see the entities</h2>
<p>All six are live in the bottom-right Structure switcher, on the same live data. P1 is the default because it is
literally “fdcanusb → Moteus c1 · CAN 15” plus a Ports toggle for the USB / PCIe detail. The single computer is
never a fold in any of them.</p>
<div class="grid">{structures}</div>

<h2>Eleven skins, light and dark</h2>
<p>The seven you named, plus four of mine:
<ul class="tight"><li><b>ISA-101 HMI:</b> grey, colour only for abnormal. It is the standard behind “quiet when OK”.</li><li><b>macOS Settings.</b></li><li><b>Linear.</b></li><li><b>High contrast:</b> AAA.</li></ul>
Every skin and mode passes the WCAG gate in the test, measured: text ≥ 4.5:1 on four surfaces, and problem shapes ≥ 3:1 on the surface and the selected row. OK rows stay uncoloured. Claude is the default.</p>
<div class="grid">{skins}</div>

<h2>What the live rack caught</h2>
<ul class="tight">
<li><b>Our own checks raced for the rack bus lock:</b> one camera reported "lock held". Plugins sharing a gate now queue.</li>
<li><b>Config reads failed or garbled values:</b> the inventory and config check read raw streams past the frame guard. Now paced; CAN 1 and 14 read every key and firmware 657b5c5, read-only.</li>
<li><b>Firmware 1.1.2 has no <code>servo.derate_temperature</code>:</b> every board read "unknown". Now "not on this firmware", and derating comes from fault_temperature − temperature_margin.</li>
<li><b>A silent adapter was blamed on "frames are ACKed":</b> now <code>FramesNotSent</code> when dlec stays 7.</li>
<li><b>Your <code>d_*</code> aliases point at a moved folder:</b> <code>src/core/mdp/actuators</code> → <code>src/core/mdp/agent/actuators</code>.</li>
<li><b><code>d_find --range</code> re-launched the triage script:</b> fixed.</li>
<li><b>A silent board printed "MISMATCH kp, ki, kd…":</b> now "NO REPLY".</li>
</ul>

<h2>Decisions</h2>
<h3>Done and proved</h3>
<ul class="tight"><li>The live panel shows both adapters, CAN 1 / 14 / 15 and both D405s, with plain-words reasons (captures above, real rack).</li>
<li>Every utility dry-runs to the exact command (the table above). Gates: 876 tree/skin checks with mutations caught, 118 view-core, 37 utilities, 182 host pytest, 687 moteus_servo, 163 intel_rgbd, 275 rack twin.</li></ul>
<h3>Needs you</h3>
<ul class="tight"><li><b>Pick a structure and a skin.</b> Default if you say nothing: P1 + Claude.</li>
<li><b>28B3A25D:</b> unplug its CAN cable at the board end, then press Check communication. The panel says whether the board or the adapter holds the bus.</li>
<li><b>CAN 1 now reads ratio 0.1, but the record holds 1.</b> Say “record it” and I run <code>config-record --can-id 1</code>. CAN 1 motion stays refused until then.</li>
<li><b>Update configuration has no recorded per-board .cfg files yet,</b> so it refuses with a dump command. Default: I seed them from the 10-10 / 10-19 firmware-run dumps when you say so.</li></ul>
<h3>Deliberately not done</h3>
<ul class="tight"><li>No write or motion button was pressed by any agent: Stop all, firmware, calibration, config and ids are yours to click.</li>
<li>Nothing is merged; the drivers stay on their branch.</li></ul>
</main></body></html>"""


def main() -> int:
    OUT.write_text(build())
    total = sum(v1.media_bytes.values())
    print(f"wrote {OUT} · {OUT.stat().st_size / 1e6:.2f} MB · media {total / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
