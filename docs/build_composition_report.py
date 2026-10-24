"""Builds the composites report: one self-contained HTML file (images and the hero inlined as data URIs), from the media that
``docs/record_composition.js`` wrote and a facts file with the gate counts and shas.

    /usr/bin/python3 docs/build_composition_report.py --media reports/media/composition --facts reports/media/composition/facts.json \\
        --out /home/bam/io-diagnostics/reports/media/composites-2026-10-24.html

``facts.json``: {"contract_commit", "contract_blob", "branch_head", "base", "gates": {name: "n/n"}, "mutations": "n caught, m missed",
"mutation_lines": ["caught ... "], "lane_command": "...", "generator_run": {"command", "stdout"}, "files": ["..."]}.
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


def jpeg(path: Path, quality: int = 14) -> str:
    """A PNG screenshot as a small JPEG, 940 px wide (the page is flat colour and text: it reads cleanly)."""

    with tempfile.NamedTemporaryFile(suffix=".jpg") as out:
        subprocess.run(["/usr/bin/ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vf", "scale=940:-2", "-q:v", str(quality), out.name], check=True)
        return data_uri(Path(out.name), "image/jpeg")


def figure(media: Path, name: str, caption: str) -> str:
    path = media / f"{name}.png"
    if not path.exists():
        return f'<figure class="missing"><figcaption>{html.escape(caption)} (not recorded)</figcaption></figure>'
    return f'<figure><img src="{jpeg(path)}" alt="{html.escape(caption)}" loading="lazy"><figcaption>{html.escape(caption)}</figcaption></figure>'


def pair(media: Path, name: str, caption: str) -> str:
    """The same screen in light and dark, side by side."""

    return f'<div class="grid">{figure(media, name + "-light", caption + " · light")}{figure(media, name + "-dark", caption + " · dark")}</div>'


def clip(media: Path, name: str, caption: str) -> str:
    mp4 = media / f"{name}.mp4"
    if not mp4.exists():
        return f"<p>{html.escape(caption)} (not recorded)</p>"
    with tempfile.NamedTemporaryFile(suffix=".gif") as out:  # the fallback, small: 380 px, 5 fps
        subprocess.run(["/usr/bin/ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf", "fps=5,scale=380:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=32[p];[b][p]paletteuse=dither=none", out.name], check=True)
        fallback = f'<details><summary>GIF fallback</summary><img src="{data_uri(Path(out.name), "image/gif")}" alt="{html.escape(caption)} (gif)"></details>'
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
figcaption { color: var(--mut); font-size: 12.5px; margin-top: 4px; } .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; } table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
details { margin-top: 6px; } .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; }
@media (max-width: 760px) { .grid, .cols { grid-template-columns: 1fr; } }
"""

FILE_EXAMPLE = """# ~/.config/io-diagnostics/expected.toml  (the same file as the rigs)
[[composite]]
id = "rack"
kind = "rack"
title = "Test rack"
batch = "having"                      # "Calibrate all 6" skips the two cameras and says so
children = ["rack/robot-1", "rack/robot-2"]

[[composite]]
id = "rack/robot-1"
kind = "robot"
children = ["rack/robot-1/arm", "usb:8086:0b5b:219323070276"]    # a composite by id, a camera by hardware id

[[composite]]
id = "rack/robot-1/arm"
kind = "arm"
title = "Arm 1"
plugin = "state_arm"                  # someone else's python: end effector, tracking error, Home the arm
expects = { tracking_error_rev = 0.5, links_m = [0.3, 0.25, 0.1] }     # handed to it verbatim, never read here
children = ["usb:0483:5740:272813EF#can/1", "usb:0483:5740:272813EF#can/2", "usb:0483:5740:272813EF#can/3"]
"""


def build(media: Path, facts: dict) -> str:
    gates = "".join(f"<tr><td>{html.escape(name)}</td><td>{html.escape(count)}</td></tr>" for name, count in facts["gates"].items())
    mutations = "".join(f"<li>{html.escape(line)}</li>" for line in facts.get("mutation_lines", []))
    generator = facts.get("generator_run") or {}
    generator_html = (f"<pre>{html.escape(generator.get('command', ''))}\n\n{html.escape(generator.get('stdout', ''))}</pre>" if generator
                      else "<p class='mut'>not run in this build</p>")
    files = "".join(f"<li><code>{html.escape(name)}</code></li>" for name in facts.get("files", []))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Composites in RobotDx</title><style>{STYLE}</style></head><body><main>
<h1>Composites in RobotDx</h1>
<p class="verdict">A file now defines an arm, a robot, a rack and what each contains; RobotDx nests them, rolls the worst child up as an icon, calibrates all six joints from one confirm, and never learns what an arm is. On <code>lane/composition</code> @ <code>{html.escape(facts['branch_head'])}</code>, not merged.</p>
<p class="mut">Zach, 2026-10-24: "we need a file 100% that basically defines those parent children relationships ... the high level things should also by design be implemented in someone else's python ... that is the seam we want." Contract: <code>docs/composition-v1.md</code> (first committed in <code>{html.escape(facts['contract_commit'])}</code>, blob <code>{html.escape(facts['contract_blob'])}</code> now). Everything below was recorded from the real page on the fixture host (F1: two fdcanusb adapters of three joints, two cameras, an arm plugin); no hardware: the plugins are stdlib fixtures, and the six runs of the batch are the fixture's, which only print a refusal.</p>

<h2>The hero: open an arm, follow a child link, open "Calibrate all 6…"</h2>
{clip(media, 'hero', 'Robot rack on the fixture host: the tree nests rack > robot > arm > boards; the arm\'s State draws "↳ CAN 3" and a click opens that board; the rack\'s Utilities offer "Calibrate all 6…" and its dry-run sheet lists the six runs.')}

<h2>1. The file, the tree</h2>
<p>Composites are <code>[[composite]]</code> tables in the expected setup (one manifest, not two): an id, a kind word, the children (a board as <code>adapter#node</code>, a device by hardware id, an entity path, or another composite by id), an optional plugin, an optional <code>expects</code> handed to it verbatim. Any depth, several roots. A mistake is a status, never a crash: the Computer gets one UNKNOWN naming it, no composite loads (half a hierarchy would roll up wrongly), the rigs still do, and the panel will not rewrite the file while it is wrong.</p>
<pre>{html.escape(FILE_EXAMPLE)}</pre>
{pair(media, 'hierarchy', 'Test rack > Robot 1 > Arm 1 > CAN 1..3 and the camera; each level shows the worst below it as an icon, and the chip still counts the two CAN 1 faults once')}
<div class="cols"><div>{figure(media, 'collapsed-light', 'Collapsed, a composite still says what is below it (the same icon)')}</div><div>{figure(media, 'broken-file-light', 'A broken file (here: a duplicate id): one UNKNOWN on the Computer, no composite rows')}</div></div>

<h2>2. What RobotDx does by itself: roll up, count once</h2>
<ul>
<li><b>Level</b>: the worst child's, a child composite at its final level; a child that is not here is a warn; nothing reporting is unknown, never green.</li>
<li><b>Icon only</b>: the row draws the level and no words; the status message is counts and names.</li>
<li><b><code>caused_by</code></b> names the children that themselves report a problem, so the arm, robot and rack are listed as their consequence: the chip counts a joint's fault once, not four times. An arm plugin's own verdict ("tracking error", no <code>caused_by</code>) still counts, as it should.</li>
</ul>
{pair(media, 'arm-diagnostics', 'The arm\'s page: the rollup status, the plugin\'s own checks, "Explained by CAN 1 above: counted once, as that cause"')}

<h2>3. What a composite plugin adds (someone else's python)</h2>
<p>The same boundary as a driver: describe, diagnose, watch, act, run per composite with <code>--device composite:&lt;id&gt;</code>. It is told who it serves and where its children are in <code>IO_DIAGNOSTICS_COMPOSITE</code> (id, kind, <code>expects</code>, children with their entity paths) and reads their live levels and State from a file the host keeps current (<code>IO_DIAGNOSTICS_COMPOSITE_VALUES</code>; a page looking at the composite keeps the children's watches running). Its records land on the composite: statuses, State, Configuration, utilities. A State value may say whose it is (<code>source = {{path, key}}</code>), and the page draws a link.</p>
<table><tr><th>RobotDx</th><th>The arm plugin (tests/fixtures/plugins-state/arm_plugin.py)</th></tr>
<tr><td>which entities the composite contains; their levels; the rollup; the batch</td><td>what an arm is: planar forward kinematics for the end effector, the largest deviation of a joint from the arm's mean as "tracking error", "all joints answer", "Home the arm" (dry run only)</td></tr>
<tr><td>passes <code>expects</code> through unread; never reads <code>kind</code></td><td>reads <code>expects.tracking_error_rev</code> and <code>links_m</code>; knows its own kind</td></tr>
<tr><td>draws "↳ CAN 2" and selects the child on a click</td><td>writes <code>source = {{path, key}}</code> on the joint angles it shows</td></tr></table>
{pair(media, 'arm-state', 'The arm\'s State: joint angles with links to their boards, the end effector from the plugin\'s own maths, live at 10 Hz')}
{figure(media, 'followed-link-light', 'The link followed: CAN 2\'s own page')}

<h2>4. Batch: "Calibrate all 6…", one dry run, one confirm, six runs</h2>
<p>When every leaf under a composite offers the same standard action that needs nothing typed per board, the composite offers it with the count. <code>batch = "having"</code> (a rack with cameras) takes the leaves that have it and lists the rest as not included; the default refuses and names the board that lacks it. One dry run dry-runs every board's own action (so each plugin's preconditions and its per-board token are honoured) and lists the six runs; one confirm creates them all, queued by each board's <code>exclusive</code> group, so the bus never has more than one at a time. Each is an ordinary run with its own terminal tab.</p>
{pair(media, 'arm-utilities', 'An arm offers Calibrate all 3 and Clear faults on all 3 beside the plugin\'s own Home the arm')}
{pair(media, 'batch-dry-run', 'One dry run lists the six runs and when each starts; the two cameras are listed as not included')}
<div class="cols"><div>{figure(media, 'batch-confirm-light', 'One confirm, with the moves tick')}</div><div>{figure(media, 'batch-running-light', 'The six runs in the runs panel: one running, the rest queued (the fixture\'s run lasts 1.4 s so the queue can be seen)')}</div></div>
{figure(media, 'batch-done-light', 'Done: the entity\'s one-line summary (this fixture refuses a real run by design, so it says 0 of 6 done)')}

<h2>5. Cad2Robot</h2>
<p>A <code>cad2robot/1.x</code> bundle lists its joints with a <code>can_id</code>; the generator writes the tables, taking each joint's adapter from the expected setup's rigs, so the bundle and the rack's own file agree for free. It is a stub: one robot per bundle (<code>--group arm=a,b,c</code> splits an arm out by hand), a joint it cannot place is a note and never a guess. Run for real (read only) on the as-built One_Dof_Test_Rack bundle against Zach's real expected setup:</p>
{generator_html}

<h2>6. Proof</h2>
<table><tr><th>Gate</th><th>Result</th></tr>{gates}</table>
<p class="mut">The <code>test_host_plugins</code> checkout-name test fails in any worktree (the checkout is not named io-diagnostics); it fails the same on main's tree.</p>
<h2>Mutation checks of the rules</h2>
<p>Each mutation breaks one rule in a scratch copy; the named tests must go red ({html.escape(facts['mutations'])}):</p>
<ul>{mutations}</ul>
<h2>Files</h2>
<ul>{files}</ul>

<h2>What is left, what needs you, what I did not do</h2>
<div class="card">{facts['decision_html']}</div>
</main></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    facts = json.loads(args.facts.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(args.media, facts))
    print(f"{args.out}: {args.out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
