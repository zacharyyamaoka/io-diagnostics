"""Builds the entity page report: one self-contained HTML file (images and video inlined as data URIs), from the media that
``docs/record_entity_page.js`` wrote and a facts file with the gate counts and shas.

    /usr/bin/python3 docs/build_entity_page_report.py --media reports/media/entity-page --facts reports/media/entity-page/facts.json \\
        --out /home/bam/io-diagnostics/reports/media/entity-page-v2-2026-10-24.html

``facts.json``: {"contract_sha", "branch_head", "lane_command", "gates": {name: "n/n"}, "tests": ["..."]}.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import subprocess
import tempfile
from pathlib import Path


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def jpeg(path: Path, quality: int = 5) -> str:
    """A PNG screenshot as a small JPEG (the page is flat colour and text: q5 reads cleanly)."""

    with tempfile.NamedTemporaryFile(suffix=".jpg") as out:
        subprocess.run(["/usr/bin/ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-q:v", str(quality), out.name], check=True)
        return data_uri(Path(out.name), "image/jpeg")


def figure(media: Path, name: str, caption: str, *, wide: bool = False) -> str:
    path = media / f"{name}.png"
    if not path.exists():
        return f'<figure class="missing"><figcaption>{html.escape(caption)} (not recorded)</figcaption></figure>'
    return f'<figure{" class=wide" if wide else ""}><img src="{jpeg(path)}" alt="{html.escape(caption)}" loading="lazy"><figcaption>{html.escape(caption)}</figcaption></figure>'


def clip(media: Path, name: str, caption: str) -> str:
    mp4, gif = media / f"{name}.mp4", media / f"{name}.gif"
    if not mp4.exists():
        return f"<p>{html.escape(caption)} (not recorded)</p>"
    fallback = f'<details><summary>GIF fallback</summary><img src="{data_uri(gif, "image/gif")}" alt="{html.escape(caption)} (gif)"></details>' if gif.exists() else ""
    return (f'<figure class="clip"><video src="{data_uri(mp4, "video/mp4")}" muted loop autoplay playsinline controls preload="auto"></video>'
            f"<figcaption>{html.escape(caption)}</figcaption>{fallback}</figure>")


STYLE = """
:root { color-scheme: light dark; --bg:#fafaf9; --fg:#1c1917; --mut:#57534e; --line:#e7e5e4; --card:#fff; --acc:#1c1917; }
@media (prefers-color-scheme: dark) { :root { --bg:#161513; --fg:#f5f5f4; --mut:#a8a29e; --line:#2e2c29; --card:#1f1d1b; --acc:#f5f5f4; } }
* { box-sizing: border-box; } body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.55 system-ui, sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 28px 20px 80px; } h1 { font-size: 24px; margin: 0 0 6px; } h2 { font-size: 17px; margin: 34px 0 8px; }
.verdict { font-size: 17px; font-weight: 600; margin: 8px 0 4px; } .mut { color: var(--mut); } code, pre { font: 12.5px ui-monospace, monospace; }
pre { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; overflow-x: auto; }
figure { margin: 0 0 14px; } figure img, figure video { width: 100%; height: auto; display: block; border: 1px solid var(--line); border-radius: 8px; background: var(--card); }
figcaption { color: var(--mut); font-size: 12.5px; margin-top: 4px; } .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; } figure.wide { grid-column: 1 / -1; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; } .tag { display:inline-block; font-size:11px; text-transform:uppercase; letter-spacing:.6px; color:var(--mut); }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; } th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
details { margin-top: 6px; } .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; }
@media (max-width: 760px) { .grid, .cols { grid-template-columns: 1fr; } }
"""


def build(media: Path, facts: dict) -> str:
    gates = "".join(f"<tr><td>{html.escape(name)}</td><td>{html.escape(count)}</td></tr>" for name, count in facts["gates"].items())
    driver = """
<ol>
<li><b>describe</b>: <code>"record_version": "0.2"</code>; <code>watch {period_s, gates: [port_free, bus_lock_free | camera_free], danger: "query"}</code>; RealSense: <code>streams</code> (color, depth, ir; a <code>colormap</code> for depth; <code>bands</code> for a hyperspectral camera); <code>config_write {gates}</code>; <code>tabs</code> if it has an extra one.</li>
<li><b>watch --device D --path P</b> (query only; a long-running process, stdout JSON lines): <code>state</code> records at <code>period_s</code> (position, velocity, torque, voltage, temperature, <b>mode</b> as a measurement with <code>meaning</code>, <b>fault</b> with its meaning), <code>frame</code> records after atomically renaming each image into <code>$IO_DIAGNOSTICS_FRAME_DIR</code>; a blank line when idle; a <code>PortBusy</code> / <code>BusLockHeld</code> status and exit when the port is held; exit on SIGTERM.</li>
<li><b>diagnose</b>: <code>config</code> records with <code>writable, type, range|choices, default, stored, safety</code> and <code>expected</code> (+ <code>expected_source</code>) from <code>IO_DIAGNOSTICS_EXPECTED</code>; numbers move from status <code>values</code> to <code>state</code> records; a fault status carries <code>fault_code</code> and <code>fault_meaning</code>.</li>
<li><b>set_config / persist_config / reset_config</b> (moteus <code>conf set</code>, <code>conf write</code>, <code>conf load</code>): registered on the board, danger <code>changes</code>; read the value back and print the <code>config</code> record; on a refusal print <code>ConfigRejected</code> and exit 1; the interlock's keys have <code>safety: true</code>; a dry run answers with the plugin's own token.</li>
</ol>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RobotDx entity page, v2</title><style>{STYLE}</style></head><body><main>
<h1>RobotDx entity page, v2</h1>
<div class="mut">2026-10-24 · lane <code>lane/entity-page</code> · fixture host only, no hardware</div>
<p class="verdict">A header that stays above tabs (Diagnostics, State, Configuration, Log, Utilities, a driver's own), State that is live while you look and shows images, and a Configuration tab that edits the board live and says whether it was accepted.</p>
<p class="mut">On <code>lane/entity-page</code> @ <code>{html.escape(facts['branch_head'])}</code>, not merged. Contract: <code>docs/state-config-v1.md</code> @ <code>{html.escape(facts['contract_sha'])}</code>. The moteus and RealSense plugins are untouched: they get their half in a later lane (the brief is at the end).</p>

<h2>Hero: a board, then a camera</h2>
{clip(media, 'hero', 'Open a board: Diagnostics, State live (numbers and sparklines), Configuration (pin a row), Log; then the camera and a stream toggle.')}

<h2>Before and after</h2>
<div class="cols">{figure(media, 'before-joint', 'Before: the detail panel top to bottom (a joint)')}{figure(media, 'joint-state-light', 'After: the same board, State tab')}
{figure(media, 'before-camera', 'Before: a camera')}{figure(media, 'camera-state-light', 'After: the camera, State tab: images first')}</div>

<h2>Light and dark, 1400×900</h2>
<div class="grid">{figure(media, 'joint-state-light', 'Joint · State · light')}{figure(media, 'joint-state-dark', 'Joint · State · dark')}
{figure(media, 'camera-state-light', 'Camera · State · light')}{figure(media, 'camera-state-dark', 'Camera · State · dark')}
{figure(media, 'joint-diagnostics-light', 'Joint · Diagnostics (a fault code with its meaning inline) · light')}{figure(media, 'joint-diagnostics-dark', 'Joint · Diagnostics · dark')}
{figure(media, 'joint-config-light', 'Joint · Configuration (dot-notation folders; expected vs read) · light')}{figure(media, 'joint-config-dark', 'Joint · Configuration · dark')}
{figure(media, 'camera-diagnostics-light', 'Camera · Diagnostics · light')}{figure(media, 'camera-diagnostics-dark', 'Camera · Diagnostics · dark')}</div>

<h2>Editing a setting, live and safe</h2>
{clip(media, 'config', 'Edit… (one confirm naming the board), Enter applies live, accepted / rejected (the board kept 10.0) / not saved, Save to board, a safety key asks a second confirm.')}
<div class="grid">{figure(media, 'config-edit-confirm', 'One confirm names the board and says it is live')}{figure(media, 'config-edit-live', 'accepted · not saved · the bar · reset to stored, the record or the default')}
{figure(media, 'config-safety-confirm', 'A safety key asks a second confirm (and is refused unless the board is stopped)')}{figure(media, 'joint-log-light', 'Log: every write is a line (who, key, old → new, accepted or not)')}
{figure(media, 'joint-utilities-light', 'Utilities: every action with its danger and last run')}{figure(media, 'computer-processes', 'The Computer\\'s own tab: Processes, sortable')}</div>

<h2>Done and proved</h2>
<div class="card"><table><tr><th>gate</th><th>result</th></tr>{gates}</table>
<p class="mut">Mutation checks (<code>tests/mutate_entity_page.py</code>): each gate was broken in a scratch copy and its test went red: safety (stopped, second confirm, unknown or old mode), session (token, expiry, confirm), bus gates, type and choice checks, the clamped value, the page-only rule on three endpoints, the watch's grace and holder pause, the frame directory, the state/health split, the expected mark.</p></div>

<h2>What is left</h2>
<ul><li><b>Next, not blocked:</b> the moteus and RealSense plugins (brief below); the diagnostics-variants lane's winner replaces <code>diagnosticsPanel()</code> in <code>detail/d6.js</code>; a per-pixel spectrum endpoint for hyperspectral cameras (reserved in the contract, not built).</li></ul>

<h2>What needs you</h2>
<table><tr><th>question</th><th>my recommendation</th><th>default if you say nothing</th></tr>
{facts['decisions']}</table>

<h2>Deliberately not done</h2>
<ul>{facts['not_done']}</ul>

<h2>What the moteus and RealSense plugins must implement</h2>
<div class="card">{driver}</div>

<h2>Run it</h2>
<pre>{html.escape(facts['lane_command'])}</pre>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    facts = json.loads(args.facts.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(args.media, facts))
    print(f"{args.out}  {args.out.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
