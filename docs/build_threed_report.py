"""Builds the 3D view's report: one self-contained HTML file (screenshots as small JPEGs, the hero as MP4 with a GIF fallback, all
inlined), from the media ``docs/record_threed.js`` wrote, the rack's description and a facts file with the test results.

    /usr/bin/python3 /home/bam/io-diagnostics-lanes/threed-view/docs/build_threed_report.py --media /home/bam/io-diagnostics/reports/media/threed-view-2026-10-29 --facts /home/bam/io-diagnostics/reports/media/threed-view-2026-10-29/facts.json

``facts.json``: {"branch", "head", "tests": [{"name", "result"}], "commits": ["sha subject"]}. Writes <media>/report.html.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RACK = ROOT / "examples" / "one-dof-test-rack" / "robot-description.toml"
E = html.escape


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def jpeg(path: Path, width: int = 1100, quality: int = 7) -> str:
    """A PNG screenshot as a JPEG (the 3D view has shading, so a little more quality than a flat page)."""

    with tempfile.NamedTemporaryFile(suffix=".jpg") as out:
        subprocess.run(["/usr/bin/ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vf", f"scale={width}:-2", "-q:v", str(quality), out.name], check=True)
        return data_uri(Path(out.name), "image/jpeg")


def figure(media: Path, name: str, caption: str, *, wide: bool = False, width: int = 1100) -> str:
    path = media / f"{name}.png"
    if not path.exists():
        return f'<figure class="missing"><figcaption>{E(caption)} (not recorded)</figcaption></figure>'
    return (f'<figure{" class=wide" if wide else ""}><a href="{name}.png"><img src="{jpeg(path, width)}" alt="{E(caption)}" loading="lazy"></a>'
            f"<figcaption>{E(caption)}</figcaption></figure>")


def hero(media: Path) -> str:
    mp4, gif = media / "hero.mp4", media / "hero.gif"
    if not mp4.exists():
        return "<p>(hero not recorded)</p>"
    fallback = f'<details><summary>GIF fallback</summary><img src="{data_uri(gif, "image/gif")}" alt="the hero as a GIF"></details>' if gif.exists() else ""
    return (f'<figure class="clip"><video src="{data_uri(mp4, "video/mp4")}" muted loop autoplay playsinline controls preload="auto"></video>'
            "<figcaption>Recorded from the running page (fixture host): the 3D item, a hover card on the elbow, Open, its device page, the "
            "breadcrumb's Back (the same selection and camera), then a tree click that highlights and frames the wrist.</figcaption>"
            f"{fallback}</figure>")


def match_text(table: dict) -> str:
    return " · ".join(f"{key} {table[key]}" for key in ("hardware_id", "serial", "node", "path", "model_id", "can_id") if key in table)


def rack_tables() -> str:
    data = tomllib.loads(RACK.read_text())
    mapped = "".join(f"<tr><td>{E(part.get('name', ''))}</td><td><code>{E(match_text(part))}</code></td><td><code>{E(part.get('link') or ', '.join(part.get('links', [])))}</code></td>"
                     f"<td>{E(part.get('note', ''))}</td></tr>" for part in data.get("part", []))
    unmapped = "".join(f"<tr><td>{E(item.get('name', ''))}</td><td>{E(item.get('reason', ''))}</td></tr>" for item in data.get("unmapped_device", []))
    return (f'<table><thead><tr><th>Device</th><th>Matched by</th><th>URDF link</th><th>Source</th></tr></thead><tbody>{mapped}</tbody></table>'
            f'<h3>Not placed ({len(data.get("unmapped_device", []))}), listed in the view, never drawn</h3>'
            f'<table><thead><tr><th>Device</th><th>Why</th></tr></thead><tbody>{unmapped}</tbody></table>')


STYLE = """
:root { color-scheme: light dark; --bg:#fafaf9; --fg:#1c1917; --mut:#57534e; --line:#e7e5e4; --card:#fff; }
@media (prefers-color-scheme: dark) { :root { --bg:#161513; --fg:#f5f5f4; --mut:#a8a29e; --line:#2e2c29; --card:#1f1d1b; } }
* { box-sizing: border-box; } body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.55 system-ui, sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 28px 16px 80px; } h1 { font-size: 24px; margin: 0 0 6px; } h2 { font-size: 18px; margin: 36px 0 8px; } h3 { font-size: 15px; margin: 18px 0 6px; }
.verdict { font-size: 17px; font-weight: 600; margin: 8px 0 4px; } .mut { color: var(--mut); } code, pre { font: 12.5px ui-monospace, monospace; }
pre { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; overflow-x: auto; }
figure { margin: 0 0 14px; } figure img, figure video { width: 100%; height: auto; display: block; border: 1px solid var(--line); border-radius: 8px; background: var(--card); }
figcaption { color: var(--mut); font-size: 12.5px; margin-top: 4px; } .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; } figure.wide { grid-column: 1 / -1; }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 6px 0 12px; } th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid var(--line); }
th { color: var(--mut); font-weight: 600; } td code { overflow-wrap: anywhere; }
ul { padding-left: 20px; } li { margin: 4px 0; } .ok { color: #3f7f2f; } .bad { color: #b3261e; }
details summary { cursor: pointer; color: var(--mut); font-size: 13px; margin-top: 4px; }
"""


def build(media: Path, facts: dict) -> str:
    info = json.loads((media / "media.json").read_text()) if (media / "media.json").exists() else {}
    described = tomllib.loads(RACK.read_text())
    placed, unplaced = len(described.get("part", [])), len(described.get("unmapped_device", []))
    rack = info.get("timings", {}).get("rack-light", {})
    load = (f"{rack.get('meshes', '?')} visual meshes, {int(rack.get('triangles', 0)):,} triangles: first frame with geometry "
            f"<b>{rack.get('firstFrame', 0) / 1000:.2f} s</b>, every mesh {rack.get('done', 0) / 1000:.2f} s (URDF parsed in {rack.get('urdf', 0) / 1000:.2f} s)") if rack else "not measured"
    tests = "".join(f"<li><b>{E(test['name'])}</b>: <span class=\"{'ok' if test.get('ok', True) else 'bad'}\">{E(test['result'])}</span></li>" for test in facts.get("tests", []))
    commits = "".join(f"<li><code>{E(commit)}</code></li>" for commit in facts.get("commits", []))
    layers = [("v1", "v1 · Hover card: name, level, the worst checks and key State values, Open (click pins it)"),
              ("v2", "v2 · Callout labels: leader lines to every device that is not OK"),
              ("v3", "v3 · Peek drawer: the selection's summary and Diagnostics rows; resizable, foldable"),
              ("v4", "v4 · Split view: the viewport over the selected entity's real device page"),
              ("v5", "v5 · Problems tray: every device that is not OK; click flies, double-click opens")]
    light = "".join(figure(media, f"fixture-{key}-light", caption, wide=key == "v1") for key, caption in layers)
    dark = "".join(figure(media, f"fixture-{key}-dark", caption.split(":")[0] + " (dark)", width=760) for key, caption in layers)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RobotDx 3D view</title><style>{STYLE}</style></head><body><main>
<span class="mut">RobotDx · {E(facts.get('branch', 'lane/threed-view'))} @ {E(facts.get('head', ''))} · not merged · 2026-10-29</span>
<h1>RobotDx 3D view</h1>
<p class="verdict">A 3D item in the activity bar draws the robot from its URDF, each part coloured by its own device, and round-trips through Back / Forward.</p>
<p class="mut">On <code>lane/threed-view</code>, not merged. Without a robot description nothing changes but a calm empty state. The rack's own description places only {placed} of {placed + unplaced} devices: the v1 Cad2Robot export does not name the rest.</p>

<h2>The flow</h2>
{hero(media)}

<h2>Five ways to show a device (the temporary "3D info" picker, bottom-right; v1 is the default)</h2>
<p class="mut">Fixture robot (primitive boxes + one STL) on fixture frame F1: the shoulder and wrist boards OK, the elbow board warn,
the second adapter's CAN 1 error, the tool's board never answers (ghost), the base and columns map to nothing (grey). The elbow is selected.</p>
<div class="grid">{light}</div>
<h3>Dark</h3>
<div class="grid">{dark}</div>

<h2>The real rack: the Cad2Robot bundle a7b1095</h2>
<p>Served from <code>examples/one-dof-test-rack/robot-description.toml</code> over replay F3 (the recorded 19:02 frame; no hardware).
CAN 14 reports warn in that frame, so its part is orange; CAN 12's board is not on this replay's adapters, so the arm is a ghost.
Load time in headless Chrome with software WebGL: {load}. Under the 3 s target, so meshes are not merged.</p>
<div class="grid">{figure(media, 'rack-light', 'Light · All labels on, the "not in the model" list open', wide=True)}
{figure(media, 'rack-can14', 'A tree click on CAN 14 highlights and frames its part (the GIM43 tag-1 holder)')}
{figure(media, 'rack-dark', 'Dark · hover card on CAN 14')}</div>
<h3>The mapping (each line's source is a comment in the file)</h3>
{rack_tables()}
<p class="mut">Why so few: the ArUco ids the boards were camera-matched to are not in the bundle's mesh names (the names carry Fusion's
occurrence index: deployment.json pairs tag 21 with "GIM43 Aruco Tag:1"), the GIM43 plate's three joints are not joints in the v1
export, and robot.json names a CAN id for one joint only (revolute_2 = CAN 12). The D435 link stays grey: no serial ties a device to it.</p>

<h2>Without a description</h2>
<div class="grid">{figure(media, 'empty-light', 'The empty state: what to add, where, the format, the docs')}
{figure(media, 'empty-dark', 'Dark')}</div>

<h2>Proof</h2>
<ul>{tests}</ul>
<h3>Commits</h3><ul>{commits}</ul>

<h2>Stock parts</h2>
<table><thead><tr><th>Element</th><th>Today</th><th>Off-the-shelf part</th><th>Behaviour that must survive</th></tr></thead><tbody>
<tr><td>3D renderer</td><td>none</td><td>three.js 0.186.1 (vendored, importmap)</td><td>loaded only when the 3D view opens</td></tr>
<tr><td>URDF to scene</td><td>none</td><td>urdf-loader 0.13.1 (gkjohnson, src ESM vendored)</td><td>joints at zero, visual meshes only</td></tr>
<tr><td>Meshes</td><td>none</td><td>three's STLLoader</td><td>the bundle read in place, never copied</td></tr>
<tr><td>Camera</td><td>none</td><td>three's OrbitControls (Fusion mouse map)</td><td>pose in the history entry</td></tr>
<tr><td>Orientation gizmo</td><td>none</td><td>three's ViewHelper</td><td>click an axis to look along it</td></tr>
<tr><td>Device matching</td><td>expected.toml's rules</td><td>unchanged (stock seam): <code>expected.same_id</code>, the same keys</td><td>first row in tree order</td></tr>
<tr><td>Colours</td><td>the skin's level tokens</td><td>unchanged (stock seam)</td><td>light and dark, every skin</td></tr>
</tbody></table>
</main></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    args = parser.parse_args()
    facts = json.loads(args.facts.read_text())
    out = args.media / "report.html"
    out.write_text(build(args.media, facts))
    print(f"{out} · {out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
