#!/usr/bin/env python3
"""The integrated-picks report: one self-contained page next to its media (docs/record_integrated.js writes the media).

    /usr/bin/python3 /home/bam/io-diagnostics-lanes/integrate-picks/docs/build_integrated_report.py --facts FACTS.json

FACTS.json carries what only a run knows: the branch head, the gate counts, the mutation results, the date. Everything else below is the
brief's own structure: what is now true, the proof, the merges, the changed assertions, what is left, what needs Zach.
Output: /home/bam/io-diagnostics/reports/media/integrated-picks-2026-10-24.html (media in integrated-picks/ beside it).
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

OUT_DIR = Path("/home/bam/io-diagnostics/reports/media")
MEDIA = "integrated-picks"
SHOTS = [
    ("l1-shell", "The L1 shell with the tabbed entity page", "Ribbon of views at the left (Ctrl-K button on top), the tree pane with the search on top of it, the entity page in the main area."),
    ("tree-nested", "The Nested tree (the default)", "The computer and the rig are both top-level; the rig holds its adapter, boards and cameras; no section rows. Roll-ups are one icon."),
    ("state-unfolded", "State: c2 unfolded, the expand and pin buttons on the next row", "A press on a sparkline unfolds its chart under the row (c2). Hovering a row shows pin, expand (the focus page, c5) and unfold."),
    ("dashboard", "The Dashboard view (c4)", "Pinned values as tiles; the ribbon's Dashboard button opens it; the board is saved in the app's settings."),
    ("tree-into", "Stepped into an adapter, with its breadcrumb", "Top of the tree is the adapter; the bar under the search has up, home and the way down (rig > adapter)."),
    ("tree-inside", "Only inside: a chip above the tree", "Show only what's inside the rig: the rig and what it holds; the chip removes the filter."),
    ("tree-type", "Group by Type", "One folder per kind of top-level entity: Computers, Rigs, Adapters, Cameras ..."),
]
MERGES = [
    ("lane/chart-enlarge @ fec589c", "app.js DEFAULTS (kept main's d6 layouts, added `charts`) and index.html (script order: charts/core.js before d6-rows.js).",
     "Kept every line of main; the chart lane's `charts: 'c0'` rode along until c6 became the default."),
    ("lane/layout-proposals @ 1d0bd72", "app.js render() (the Activity view lane's own `#activity-host` against the layouts' `keepTree`); tests/test_tree_variants.js and tests/test_utilities.js (both lanes changed the browser launch line).",
     "render() keeps the Activity view's host and adds keepTree; the two suites pin the detail layout (d0) and the page layout (L0) through tests/pin_layout.js (`pinPageLayout` is new)."),
    ("lane/diagnostics-variants @ 721ca1d", "none.", "Merged last, as briefed. Two follow-ups done after it (one row model, one palette): see below."),
    ("main @ 5f433dc", "settings.py (dashboard and the depth colour map side by side, one json(), one __all__), host.py set_settings (both key sets, one validation order), docs/api-v1.md (both Settings entries).",
     "Union of both; a body with any refused key still applies none of its keys."),
    ("main @ 95b5999", "none.", "Tests only on main's side."),
]
ASSERTIONS = [
    ("tests/test_view_core.js", "STRUCTURES now lists p6 and p7 (every generic invariant runs on them); `extra columns declare mono` 3 -> 5 (p6 / p7 share p1's Connection column); `the default structure is p1` -> p6; the Group by list `p1:Devices,p2:Type,p4:Port,p5:Bus,p0:Physical` -> `p6:Nested,p1:Physical,p7:Type,p4:Port,p5:Bus,p0:API tree,p2:Purpose` (p1 is what Zach calls the physical view; p0 renamed so two groups are not both Physical; the old Type, p2, became Purpose: its fixed Actuators / Cameras / Network / Computer folders are the hard-coded split Zach asked to drop, and Type is now p7)."),
    ("tests/test_utilities.js", "defaults: structure p1 -> p6; Group by list as above; `expected: the rig row counts at a glance`: 'first row under the computer' -> 'a top-level row' (the rig is now beside the computer); browser contexts pin L0 and d0 (merge, tests/pin_layout.js)."),
    ("tests/test_tree_variants.js", "default structure p1 -> p6 (defaults, the skin x structure combos, the contrast gate's structure); contexts pin L0 and d0."),
    ("tests/test_layouts.js", "`.l4-pal*` selectors -> `.lx-pal*` (the palette is shared now); the clean-switch check: one more `style[data-lx-css]` and one overlay for L1 as well as L4 (they share the palette)."),
    ("tests/test_activity_view.js", "pins L0 (it clicks the top bar's views; L1 draws a ribbon)."),
    ("tests/test_roster_page.js", "pins Group by Physical (p1): its rows assert the computer as the one root and the absent adapter's warning on it."),
    ("tests/test_entity_page.js", "State row: the third cell is the chart wrapper (`.ch-spark`) as well as `.d6-ss`."),
    ("tests/test_charts.js", "the picker lists c0..c6; the c1-c5 sections pin the Computer page to d4 (the sparklines they were built on; d6 is the default entity page now); c6 section added; the dashboard is opened from the ribbon in L1."),
    ("tests/test_diag_variants.js", "row order drives the shared row model: `.ro-*` / `data-ro-*` -> `.er-*` / `data-er-*`, the A-Z / severity / manual buttons -> the Order select, the pin is read from the host's arrangements.json instead of localStorage, each variant starts from an empty arrangement (the host keeps it for every browser), folder rows `>= 15` -> `>= 10` (d1b draws a problem as a card outside the folder and this PC's Computer reports a real problem), and a folder shows no digit at all."),
]


def esc(text) -> str:
    return html.escape(str(text))


def build(facts: dict) -> str:
    gate = "".join(f"<tr><td>{esc(name)}</td><td>{esc(count)}</td></tr>" for name, count in facts["gate"])
    mutations = "".join(f"<tr><td>{esc(name)}</td><td>{esc(verdict)}</td></tr>" for name, verdict in facts["mutations"])
    shots = ""
    for slug, title, text in SHOTS:
        shots += (f"<figure><figcaption><b>{esc(title)}</b> — {esc(text)}</figcaption><div class='pair'>"
                  f"<a href='{MEDIA}/{slug}-light.png'><img loading='lazy' src='{MEDIA}/{slug}-light.png' alt='{esc(title)}, light'></a>"
                  f"<a href='{MEDIA}/{slug}-dark.png'><img loading='lazy' src='{MEDIA}/{slug}-dark.png' alt='{esc(title)}, dark'></a></div></figure>")
    merges = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in MERGES)
    asserts = "".join(f"<tr><td><code>{esc(a)}</code></td><td>{esc(b)}</td></tr>" for a, b in ASSERTIONS)
    notes = "".join(f"<li>{esc(n)}</li>" for n in facts["notes"])
    left = "".join(f"<li>{esc(n)}</li>" for n in facts["left"])
    needs = "".join(f"<li>{esc(n)}</li>" for n in facts["needs"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RobotDx · the integrated picks</title>
<style>
:root {{ --bg:#fafafa; --fg:#18181b; --mut:#52525b; --line:#e4e4e7; --card:#fff; --acc:#2563eb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#09090b; --fg:#fafafa; --mut:#a1a1aa; --line:#27272a; --card:#111113; --acc:#60a5fa; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 system-ui, sans-serif; }}
main {{ max-width:1120px; margin:0 auto; padding:28px 16px 80px; }}
h1 {{ font-size:24px; margin:0 0 6px; }} h2 {{ font-size:18px; margin:36px 0 10px; border-top:1px solid var(--line); padding-top:18px; }}
.verdict {{ font-size:17px; margin:8px 0 18px; }} .mut {{ color:var(--mut); }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }} td,th {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
code {{ font-size:12.5px; background:var(--card); padding:1px 4px; border-radius:4px; }}
video, img {{ max-width:100%; border:1px solid var(--line); border-radius:8px; background:var(--card); }}
.pair {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }} @media (max-width:760px) {{ .pair {{ grid-template-columns:1fr; }} }}
figure {{ margin:18px 0; }} figcaption {{ margin-bottom:6px; color:var(--mut); font-size:13.5px; }}
</style></head><body><main>
<h1>RobotDx · the integrated picks</h1>
<p class="mut">{esc(facts['date'])} · branch <code>lane/integrate-picks</code> @ <code>{esc(facts['sha'])}</code> · not merged into main</p>
<p class="verdict"><b>One branch now holds every pick: L1 with the Ctrl-K palette, c2 + the focus page + the Dashboard, r1, d1a (merged last), and the tree changes.</b>
It is on <code>lane/integrate-picks</code>, not merged; {esc(facts['verdict_tail'])}</p>

<h2>The hero flow</h2>
<video autoplay muted loop playsinline controls width="1120" poster="{MEDIA}/l1-shell-light.png"><source src="{MEDIA}/hero.mp4" type="video/mp4"><img src="{MEDIA}/hero.gif" alt="hero flow"></video>
<p class="mut">Open the app, search in the tree pane, open a board, unfold a State chart, step into an adapter, step back out. Recorded from the real page on the fixture host (<code>docs/record_integrated.js</code>); GIF fallback: <a href="{MEDIA}/hero.gif">hero.gif</a>.</p>

<h2>Screenshots, 1400×900, light and dark</h2>{shots}

<h2>Merges and their conflicts</h2>
<table><tr><th>merge</th><th>conflicts</th><th>resolution</th></tr>{merges}</table>

<h2>Every assertion I changed, and why</h2>
<table><tr><th>suite</th><th>change</th></tr>{asserts}</table>

<h2>Gate</h2>
<table><tr><th>suite</th><th>result</th></tr>{gate}</table>
<h3>Mutation checks (a scratch copy, one break each, the guarding suite must go red)</h3>
<table><tr><th>mutation</th><th>result</th></tr>{mutations}</table>

<h2>Notes</h2><ul>{notes}</ul>
<h2>Left</h2><ul>{left}</ul>
<h2>Needs you</h2><ul>{needs}</ul>
</main></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    args = parser.parse_args()
    facts = json.loads(Path(args.facts).read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "integrated-picks-2026-10-24.html"
    target.write_text(build(facts))
    print(target)


if __name__ == "__main__":
    main()
