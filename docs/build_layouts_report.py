#!/usr/bin/env python3
"""The layout comparison report: five proposals for where navigation, search, views and the log live (Zach, 2026-10-24).

    /usr/bin/python3 /home/bam/io-diagnostics/docs/build_layouts_report.py \
        --media /home/bam/io-diagnostics/reports/media/layouts \
        --out /home/bam/io-diagnostics/reports/media/layout-proposals-2026-10-24.html \
        --proof view_core=128,utilities=84,tree=1076,runs=30,layouts=161

One self-contained .html (every image, video and GIF inlined as a data: URI, no network): each proposal's hero video (muted,
looping, with controls and a GIF fallback), light / dark screenshots, a pitch; a criteria table weighing the five against what
Zach asked; the recommendation and the decision surface. The media comes from docs/record_layouts.js.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path

PROPOSALS = [
    {
        "id": "l1", "name": "Obsidian", "tag": "your reference, matched",
        "pitch": "Your own reference, convention for convention. A ribbon of views at the far left (Devices, Table, Activity, Runs, with Settings at its foot); "
                 "the left pane has a header, a small toolbar row (collapse all, expand all, View options, show what is hidden), then the search field on top of the tree; "
                 "the main area is the entity page under a slim view header with back / forward and the breadcrumb of the selection. If you know Obsidian there is nothing to learn.",
        "keys": "Ribbon: Devices · Table · Activity · Runs · Settings (View options). Left pane: collapse all, expand all, View options, eye = show hidden (Ports). Back / forward and the mouse buttons as the page's history.",
        "costs": "The Runs ribbon item toggles the page's own bottom terminal panel (Obsidian has no such thing). Settings and the sliders button open the same View options popover. Only one tab in the pane header: Obsidian's second and third tabs have no equivalent here.",
        "shots": ["main", "table", "search"],
    },
    {
        "id": "l2", "name": "VS Code", "tag": "tabs and a bottom panel",
        "pitch": "An IDE's shape: an activity bar, a sidebar with the tree and its search on top, and an editor area where every entity you open becomes a tab "
                 "(a single click opens an italic preview tab the next click replaces; a double-click keeps it), with the breadcrumb under the tabs. A bottom panel holds Activity and the Runs terminal "
                 "under the page, not instead of it; the status bar counts what needs you. Best if you keep several boards open and want the log underneath them.",
        "keys": "Ctrl+B sidebar · Ctrl+J panel · Ctrl+Shift+F search. Activity bar: Devices, Search, Table, Activity, Terminal, Manage (View options). Tabs close with x or a middle click.",
        "costs": "Tabs only, not side by side: the page draws one detail from one loaded node (S.node), so a second board beside the first would need a second instance of it. A tab switch re-selects through the page's own history. "
                 "The panel is open by default, so the first screen is busier than the others.",
        "shots": ["main", "table"],
    },
    {
        "id": "l3", "name": "Claude desktop", "tag": "calm list, side pane on demand",
        "pitch": "Minimal chrome, like the Claude desktop app: a calm list of devices with the search on top and a quiet \"Show 38 hidden\" row at its foot; the entity in the middle with one Device | Table switch; "
                 "and a side pane that is closed until you ask for it, for the Activity of the selected device (or all of them) or for a terminal. The pane opens by itself, on the terminal, the moment a utility starts a run.",
        "keys": "Bar: Device | Table, Activity, Terminal. The pane: Activity (This device | All) and Terminal tabs, closes with x. \"Show N hidden\" flips the grouping's own switch (Ports) and counts what it adds with the page's own builder.",
        "costs": "The list is the same tree as every other layout; only its chrome is calmer. Activity in the pane is the page's own log, filtered to the selection.",
        "shots": ["main", "closed"],
    },
    {
        "id": "l4", "name": "Notion", "tag": "one calm page and Ctrl-K",
        "pitch": "One calm page. The first screen answers one question (\"6 things need you\" or \"Everything is OK\") and lists what needs you; the tree is a collapsible sidebar with the search on top; "
                 "every page carries the breadcrumb of the hierarchy (bam-GPU › USB bus 1 › fdcanusb 272813EF › CAN 15); and Ctrl-K opens a palette that jumps to any device and runs actions (Table, Activity, "
                 "Terminal, View options, light / dark, the selected device's utilities as dry runs).",
        "keys": "Ctrl+K palette (arrows, Enter, Esc) · Ctrl+\\ sidebar. Home is the first screen; any selection leaves it. The breadcrumb leaves out USB ports and the CAN bus under an adapter: plumbing, not places.",
        "costs": "Home hides the entity page until you pick something, so a viewer who wants the old \"first attention item open\" has one more click. The palette is a self-contained overlay: it would work on any of the other layouts.",
        "shots": ["main", "home", "palette"],
    },
    {
        "id": "l5", "name": "System Monitor", "tag": "four tabs, one question each",
        "pitch": "GNOME System Monitor / Cockpit: top-level tabs. Overview is the computer's own page (CPU, RAM, disks, temperatures, GPU, links) beside \"what needs you\" and the recent events; "
                 "Devices is the tree with its search beside the entity page, with a Tree | Table switch in the header; Activity is the full log; Runs gives the terminal the whole tab. Nothing is on screen twice.",
        "keys": "Tabs: Overview · Devices · Activity · Runs (a count while runs go). A \"needs you\" row opens that device in Devices; going back to Devices reopens the last device. The tab is remembered.",
        "costs": "The Overview's resources are the Computer entity's own page (detail layout d4), which needs the computer plugin: the F1 fixture has none, so its Overview screenshots come from a fixture host that also runs the passive, read-only computer plugin. "
                 "The search lives in the Devices tab only, so it is not on screen in the other three.",
        "shots": ["main", "overview"],
    },
]

# criteria before options (Zach's rule): weights 1-5 from what he asked for and what his memory says he wants; scores 1-5 are my reading
CRITERIA = [
    ("Search inside the tree pane, on top (his ask)", 5, "measured on all five: the input's rect lies inside the tree pane, above its first row. The score is how Obsidian-like the pane is, not whether it is there",
     {"l1": 5, "l2": 4, "l3": 4, "l4": 4, "l5": 3}),
    ("Matches a reference app he uses (zero relearning)", 4, "L1 is the one he named; Notion and Claude are daily tools; VS Code is in his own testing notes; System Monitor is a GNOME idiom",
     {"l1": 5, "l2": 3, "l3": 3, "l4": 4, "l5": 2}),
    ("First screen is one calm answer, detail one click deeper", 4, "feedback_calm_ui_progressive_disclosure: is it OK, what needs me?",
     {"l1": 3, "l2": 2, "l3": 3, "l4": 5, "l5": 4}),
    ("Every view, the log and the terminal one click away", 3, "L1 ribbon, L2 activity bar, L3 bar, L4 icons and Ctrl-K, L5 tabs",
     {"l1": 5, "l2": 4, "l3": 4, "l4": 4, "l5": 4}),
    ("Log or terminal beside the entity, not instead of it", 3, "L2 panel and L3 side pane keep the page; the rest swap the main area (L1 and L4 can still show the terminal panel under it)",
     {"l1": 2, "l2": 5, "l3": 5, "l4": 2, "l5": 1}),
    ("Every pane boundary draggable, remembered, can fold away", 3, "Zach's standing rule; measured on all five, L4 and L5 draw fewer boundaries",
     {"l1": 5, "l2": 5, "l3": 5, "l4": 4, "l5": 4}),
    ("Warn colours only for real problems; calm, stock shadcn", 3, "the layouts' CSS names no status token (tested); more shapes on screen = less calm",
     {"l1": 5, "l2": 4, "l3": 5, "l4": 5, "l5": 4}),
    ("Small to land and keep (shell, few moving parts)", 3, "L1 is a ribbon and a header; L2 keeps tab state; L5 maps tabs to selection and views",
     {"l1": 5, "l2": 2, "l3": 3, "l4": 3, "l5": 2}),
]


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def image_uri(path: Path) -> str:
    """A screenshot as a lossless-looking WebP (the report is one file: 2.5x smaller than the PNGs, text stays sharp)."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.open(path).convert("RGB").save(buffer, "WEBP", quality=90, method=6)
    return f"data:image/webp;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def build(media: Path, proof: dict[str, str], lane: str, main_sha: str) -> str:
    manifest = json.loads((media / "manifest.json").read_text())
    shots = {(s["layout"], s["state"], s["scheme"]): s for s in manifest["shots"]}
    heroes = {h["layout"]: h for h in manifest["heroes"]}
    totals = {p["id"]: sum(w * scores[p["id"]] for _, w, _, scores in CRITERIA) for p in PROPOSALS}
    best = max(totals.values())
    ceiling = sum(w * 5 for _, w, _, _ in CRITERIA)

    def figure(layout: str, state: str, caption: str) -> str:
        light, dark = shots.get((layout, state, "light")), shots.get((layout, state, "dark"))
        if not light or not dark:
            return ""
        label = html.escape(light["label"] or caption)
        return (f'<figure><img class="lt" alt="{label} (light)" src="{image_uri(media / light["file"])}">'
                f'<img class="dk" alt="{label} (dark)" src="{image_uri(media / dark["file"])}"><figcaption>{html.escape(caption)}</figcaption></figure>')

    sections = []
    for proposal in PROPOSALS:
        pid = proposal["id"]
        hero = heroes.get(pid)
        video = ""
        if hero:
            poster = shots.get((pid, "main", "light"))
            poster_attr = f' poster="{image_uri(media / poster["file"])}"' if poster else ""
            video = (f'<div class="hero"><video controls muted loop autoplay playsinline preload="auto"{poster_attr}>'
                     f'<source src="{data_uri(media / hero["mp4"], "video/mp4")}" type="video/mp4">'
                     f'<img alt="{html.escape(proposal["name"])} hero (GIF fallback)" src="{data_uri(media / hero["gif"], "image/gif")}"></video>'
                     f'<p class="cap">{hero["seconds"]:.0f} s from the real page on the F1 fixture: open the app, search a board, open it, glance at Activity.</p></div>')
        captions = {"main": "CAN 15 selected", "table": "the Table view", "search": "typing in the search, inside the tree pane", "closed": "the calm default: one list, one page",
                    "home": "the first screen", "palette": "Ctrl-K, typing \"can\"", "overview": "the Overview tab (with the computer plugin)"}
        gallery = "".join(figure(pid, state, captions.get(state, state)) for state in proposal["shots"])
        sections.append(
            f'<section class="proposal" id="{pid}"><h2><span class="id">{pid.upper()}</span> {html.escape(proposal["name"])} <small>{html.escape(proposal["tag"])}</small></h2>'
            f'<p class="pitch">{html.escape(proposal["pitch"])}</p>{video}<div class="shots">{gallery}</div>'
            f'<dl><dt>Controls</dt><dd>{html.escape(proposal["keys"])}</dd><dt>What it costs, what did not fit</dt><dd>{html.escape(proposal["costs"])}</dd></dl></section>')

    head_cells = "".join(f"<th>{p['id'].upper()}<br><span>{html.escape(p['name'])}</span></th>" for p in PROPOSALS)
    rows = []
    for name, weight, why, scores in CRITERIA:
        cells = "".join(f'<td class="s{scores[p["id"]]}">{scores[p["id"]]}</td>' for p in PROPOSALS)
        rows.append(f'<tr><th scope="row">{html.escape(name)}<span class="why">{html.escape(why)}</span></th><td class="w">{weight}</td>{cells}</tr>')
    total_cells = "".join(f'<td class="total{" best" if totals[p["id"]] == best else ""}">{totals[p["id"]]}<span>/{ceiling}</span></td>' for p in PROPOSALS)
    table = (f'<table class="criteria"><thead><tr><th>Criterion (weight 1-5, score 1-5)</th><th>w</th>{head_cells}</tr></thead><tbody>{"".join(rows)}'
             f'<tr class="sum"><th scope="row">Weighted total</th><td></td>{total_cells}</tr></tbody></table>')

    p = proof
    proof_rows = [
        ("tests/test_layouts.js", f"{p['layouts']}/{p['layouts']}", "search inside the tree pane and above its first row (getBoundingClientRect), every tree style draws, every view reachable, panes drag / remember / reset / fold, no horizontal scroll at 1280x720 and 1400x900, five layouts and back leave nothing behind, each layout's own behaviour, no colour literal or status token in the CSS, no page errors"),
        ("tests/test_view_core.js", f"{p['view_core']}/{p['view_core']}", "the view model, unchanged"),
        ("tests/test_runs_view.js", f"{p['runs']}/{p['runs']}", "the runs panel, on the default layout L1"),
        ("tests/test_utilities.js", f"{p['utilities']}/{p['utilities']}", "pinned to L0 \"Today\": it measures the classic arrangement"),
        ("tests/test_tree_variants.js", f"{p['tree']}/{p['tree']}", "pinned to L0 \"Today\": it measures the classic arrangement"),
    ]
    proof_html = "".join(f"<tr><td><code>{html.escape(a)}</code></td><td class=\"n\">{html.escape(b)}</td><td>{html.escape(c)}</td></tr>" for a, b, c in proof_rows)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RobotDx layouts: five proposals</title>
<style>
:root {{ --bg:#fafaf9; --card:#fff; --text:#1c1917; --muted:#57534e; --faint:#78716c; --line:#e7e5e4; --accent:#44403c; --soft:#f5f5f4; color-scheme: light; }}
:root[data-theme="dark"] {{ --bg:#141211; --card:#1c1917; --text:#f5f5f4; --muted:#d6d3d1; --faint:#a8a29e; --line:#2e2a28; --accent:#e7e5e4; --soft:#22201e; color-scheme: dark; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#141211; --card:#1c1917; --text:#f5f5f4; --muted:#d6d3d1; --faint:#a8a29e; --line:#2e2a28; --accent:#e7e5e4; --soft:#22201e; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1080px; margin: 0 auto; padding: 28px 20px 80px; }}
h1 {{ font-size: 28px; letter-spacing: -.3px; margin: 0 0 6px; }}
h2 {{ font-size: 22px; margin: 0 0 6px; letter-spacing: -.2px; }}
h2 small {{ font-size: 13px; font-weight: 400; color: var(--faint); margin-left: 8px; }}
h2 .id {{ display: inline-block; min-width: 34px; padding: 1px 8px; margin-right: 6px; border-radius: 6px; background: var(--soft); border: 1px solid var(--line); font: 600 14px ui-monospace, monospace; }}
h3 {{ scroll-margin-top: 56px; font-size: 13px; text-transform: uppercase; letter-spacing: .6px; color: var(--faint); margin: 36px 0 10px; }}
.verdict {{ font-size: 18px; margin: 14px 0 6px; }}
.sub {{ color: var(--muted); margin: 0; }}
.bar {{ position: sticky; top: 0; z-index: 5; display: flex; gap: 14px; align-items: center; flex-wrap: wrap; padding: 10px 0; background: color-mix(in srgb, var(--bg) 92%, transparent); backdrop-filter: blur(6px); border-bottom: 1px solid var(--line); margin-bottom: 8px; font-size: 13px; }}
.bar a {{ color: var(--muted); text-decoration: none; padding: 2px 8px; border-radius: 6px; }}
.bar a:hover {{ background: var(--soft); color: var(--text); }}
.bar .grow {{ flex: 1; }}
.bar button {{ border: 1px solid var(--line); background: var(--card); color: var(--text); border-radius: 6px; padding: 3px 10px; cursor: pointer; font: inherit; }}
code {{ font: 12.5px ui-monospace, monospace; background: var(--soft); padding: 1px 5px; border-radius: 4px; }}
pre {{ background: var(--soft); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; overflow-x: auto; font: 12.5px/1.5 ui-monospace, monospace; }}
.shots.one {{ grid-template-columns: minmax(0, 720px); }}
.proposal {{ scroll-margin-top: 56px; margin-top: 44px; padding-top: 8px; border-top: 1px solid var(--line); }}
.pitch {{ margin: 6px 0 14px; max-width: 80ch; }}
.hero {{ margin: 10px 0 14px; }}
.hero video, .hero img {{ width: 100%; max-width: 960px; display: block; border: 1px solid var(--line); border-radius: 10px; background: var(--card); }}
.cap {{ color: var(--faint); font-size: 12.5px; margin: 5px 0 0; }}
.shots {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 14px; margin: 12px 0; }}
figure {{ margin: 0; }}
figure img {{ width: 100%; display: block; border: 1px solid var(--line); border-radius: 10px; }}
figcaption {{ color: var(--faint); font-size: 12.5px; margin-top: 4px; }}
.dk {{ display: none; }}
:root[data-shots="dark"] .lt {{ display: none; }}
:root[data-shots="dark"] .dk {{ display: block; }}
dl {{ display: grid; grid-template-columns: 170px 1fr; gap: 6px 16px; margin: 12px 0 0; font-size: 14px; }}
dt {{ color: var(--faint); }}
dd {{ margin: 0; color: var(--muted); }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
.criteria thead th {{ font-size: 12px; text-transform: none; color: var(--muted); text-align: center; }}
.criteria thead th:first-child {{ text-align: left; }}
.criteria thead th span {{ font-weight: 400; color: var(--faint); }}
.criteria td {{ text-align: center; font-variant-numeric: tabular-nums; }}
.criteria td.w {{ color: var(--faint); }}
.criteria .why {{ display: block; font-weight: 400; font-size: 12px; color: var(--faint); margin-top: 2px; max-width: 52ch; }}
.criteria td.s5 {{ background: color-mix(in srgb, var(--accent) 16%, transparent); font-weight: 600; }}
.criteria td.s4 {{ background: color-mix(in srgb, var(--accent) 9%, transparent); }}
.criteria td.s2, .criteria td.s1 {{ color: var(--faint); }}
.criteria .sum th, .criteria .sum td {{ border-bottom: 0; font-weight: 600; background: var(--soft); }}
.criteria td.total span {{ color: var(--faint); font-weight: 400; font-size: 12px; }}
.criteria td.best {{ box-shadow: inset 0 -3px 0 var(--accent); }}
.proof td.n {{ font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 600; }}
ul {{ padding-left: 20px; }} li {{ margin: 4px 0; }}
.decide {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
.decide > div {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 4px 16px 10px; }}
.decide h4 {{ margin: 12px 0 4px; font-size: 13px; text-transform: uppercase; letter-spacing: .5px; color: var(--faint); }}
@media (max-width: 640px) {{ .shots {{ grid-template-columns: 1fr; }} dl {{ grid-template-columns: 1fr; }} }}
</style></head>
<body><main>
<div class="bar"><strong>RobotDx layouts</strong>{''.join(f'<a href="#{p["id"]}">{p["id"].upper()} {html.escape(p["name"])}</a>' for p in PROPOSALS)}<a href="#compare">Compare</a><a href="#decide">Decide</a><span class="grow"></span>
<button id="shots" type="button" title="Switch every screenshot between light and dark">Screenshots: light</button></div>
<h1>Five layouts for RobotDx</h1>
<p class="verdict"><strong>L1 Obsidian is the pick</strong>: it is the reference you named and it puts the search where you asked, with the least new machinery. Take one thing from L4 on top of it: the Ctrl-K palette. All five are built as shells on <code>{html.escape(lane)}</code>, not merged.</p>
<p class="sub">Each proposal re-arranges the page's own parts (the tree pane, the search input, the three views, the detail, the runs panel, the status bar) by moving their DOM nodes; none re-implements a behaviour. Switch live from the <strong>Layout</strong> select in the bottom-right of the page; it is remembered in the browser, never a URL flag. L0 \"Today\" in the same select is the page as it was, for comparison.</p>

<h3>Today, for reference</h3>
<div class="shots one">{figure("l0", "main", "L0 Today: the toolbar on top with the view tabs, the search and View options; the tree and the entity detail below it. Your screenshot, on the F1 fixture.")}</div>

{''.join(sections)}

<h3 id="compare">The five against what you asked for</h3>
<p class="sub">Criteria first, weighted from your own words and rules; scores are my reading of the screenshots and the behaviour, so argue with the weights, not the arithmetic.</p>
{table}

<h3>Proof</h3>
<table class="proof"><tbody>{proof_html}</tbody></table>
<p class="sub">Run: <code>/usr/bin/node /home/bam/io-diagnostics/tests/test_layouts.js</code> (it starts its own F1 fixture host, refuses to go on if a hardware plugin is loaded, and stops it). The media: <code>/usr/bin/node /home/bam/io-diagnostics/docs/record_layouts.js</code>.</p>

<h3 id="decide">Decision surface</h3>
<div class="decide">
<div><h4>Done and proved</h4><ul>
<li>Five layouts plus L0, one file each (<code>web/layouts/l1.js</code> to <code>l5.js</code>), one seam (<code>layouts/host.js</code>), the picker bottom-right.</li>
<li>The search sits inside the tree pane, above its first row, in all five (measured).</li>
<li>Every pane boundary drags, is remembered and folds away; no horizontal scroll at 1280x720 and 1400x900.</li>
<li>app.js changed in three places: one <code>start()</code> call, one <code>update()</code> hook, and <code>keepTree</code> (the tree stays drawn beside the Table and Activity when a layout holds it in a sidebar).</li></ul></div>
<div><h4>Left</h4><ul>
<li>Not merged, not pushed; rebased on main {html.escape(main_sha)}.</li>
<li>The Activity view lane (<code>activity/log.js</code>) is uncommitted in the main checkout. When it lands, its <code>activityView</code> branch and my <code>keepTree</code> edit meet in the four lines of <code>render()</code> that hide and draw the tree: keep both (the tree is hidden unless the view is Tree or the layout keeps it; the Activity view is theirs). L2 and L3 draw the log through one function, <code>ctx.activity.draw</code> in <code>host.js</code>; without <code>renderActivity</code> it already falls back to the page's event rows, and moving it onto <code>IOActivityLog.create({{scope, compact}})</code> is a change in that one place.</li>
<li>Run views r2, r3, r5 (prototypes on main) draw fixed-height panels of their own: they scroll inside L2's, L3's and L5's smaller containers, and r2 has no panel, so the Runs controls open nothing under it. The layouts are built around r1.</li>
<li>Side by side boards (L2's tabs are one board at a time): needs the detail to take a node as a parameter. Not a shell job.</li></ul></div>
<div><h4>Needs you</h4><ul>
<li><strong>Pick a layout.</strong> Recommendation: L1 as the default. Default if you say nothing: L1 stays the default, the other four stay in the picker.</li>
<li><strong>Keep the palette?</strong> Recommendation: yes, lift L4's Ctrl-K onto L1 (it is a self-contained overlay). Default: not done until you pick.</li>
<li><strong>L0 \"Today\" in the picker?</strong> Recommendation: keep it while you compare, drop it when you choose. Default: it stays.</li></ul></div>
<div><h4>Deliberately not done</h4><ul>
<li>No merge, no push, nothing in the main checkout except this report and its media.</li>
<li>No new tree renderer and no change to the page's views, detail, utilities or runs.</li>
<li>No real act, no hardware: every screenshot and video is the F1 fixture host (Overview also runs the passive computer plugin).</li></ul></div>
</div>
<script>
(function () {{
  var root = document.documentElement, button = document.getElementById('shots'), key = 'robotdx-layouts-report-shots';
  function set(mode) {{ root.dataset.shots = mode; button.textContent = 'Screenshots: ' + mode; try {{ localStorage.setItem(key, mode); }} catch (_) {{}} }}
  var stored = null; try {{ stored = localStorage.getItem(key); }} catch (_) {{}}
  set(stored === 'dark' || (stored === null && window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light');
  button.addEventListener('click', function () {{ set(root.dataset.shots === 'dark' ? 'light' : 'dark'); }});
  // a film plays while it is on screen, and rests while it is not
  if ('IntersectionObserver' in window) {{
    var watcher = new IntersectionObserver(function (entries) {{
      entries.forEach(function (entry) {{ var video = entry.target; if (entry.isIntersecting) {{ var played = video.play(); if (played && played.catch) played.catch(function () {{}}); }} else video.pause(); }});
    }}, {{ threshold: 0.35 }});
    document.querySelectorAll('video').forEach(function (video) {{ watcher.observe(video); }});
  }}
}})();
</script>
</main></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--media", required=True, help="the folder docs/record_layouts.js wrote")
    parser.add_argument("--out", required=True, help="the report .html (absolute path)")
    parser.add_argument("--proof", required=True, help="view_core=N,utilities=N,tree=N,runs=N,layouts=N")
    parser.add_argument("--lane", default="lane/layout-proposals")
    parser.add_argument("--main-sha", default="main", help="the main commit the lane is rebased on")
    args = parser.parse_args()
    proof = dict(item.split("=") for item in args.proof.split(","))
    report = build(Path(args.media), proof, args.lane, args.main_sha)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"{args.out}  {len(report) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
