#!/usr/bin/env python3
"""The comparison report of the five Diagnostics variants (d1a-d1e) and the restyled health chart.

    /usr/bin/python3 /home/bam/io-diagnostics-lanes/diagnostics-variants/docs/build_diag_variants_report.py --media /abs/dir/diag-variants --out /abs/report.html

`--media` holds what docs/record_diag_variants.js wrote (shots.json, the PNGs, the hero videos and GIFs) plus gates.json (the test
counts, written by hand after the runs) and before-*.png (today's d1 shot before the restyle). The report links the media by relative
path, so it sits beside the folder (reports/media/). Stdlib only.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import health_palettes  # noqa: E402  (the contrast numbers come from the same file as the colours)

VARIANTS = [
    ("d1a", "Single-line table", "Zach's own spec, and his pick for the Diagnostics default. Name | Message | Current | History, one line a row, worst first. The status line drops down into one lane per diagnostic.",
     "the literal ask: \"name, the message, current status, and then optionally the sparkline for the health\""),
    ("d1b", "Problems first", "Every WARN / ERROR is a short card with its own numbered what-to-check; all the OK diagnostics fold into one \"N OK\" line that opens into single-line rows.",
     "triage: what needs me, and what do I do about it"),
    ("d1c", "State timeline lanes", "Grafana's state timeline. One lane per diagnostic across the whole window, name left, status right, the selected lane's message and steps under it.",
     "\"when did it go bad\", and the chart Zach pointed at"),
    ("d1d", "rqt_robot_monitor", "ROS's own viewer: Errors / Warnings / All, nested by the name prefix, a key / value panel for the selected status.",
     "a tool every ROS person already knows; not reinventing the wheel"),
    ("d1e", "Checklist", "A pre-flight list in plain words. A problem is first, bold and open with its steps; OK lines are dimmed; a spark shows only where the history held a problem.",
     "a person who does not know what \"CAN 15 driver: reading\" means"),
]
ENTITY_WORDS = {"computer": "the Computer", "fdcanusb": "an fdcanusb", "can15": "CAN 15 driver", "camera": "a RealSense D405", "stale": "a CAN bus that went stale"}

# the criteria, from Zach's 2026-10-24 notes: (what he asked, per variant: mark and note). ok = met, part = met with a caveat, no = not the point.
CRITERIA = [
    ("Single-line rows (\"each row a single line\")", {"d1a": ("ok", "every row"), "d1b": ("part", "OK rows; a problem is a card"), "d1c": ("ok", "every lane"), "d1d": ("ok", "every leaf"), "d1e": ("part", "a sentence may wrap")}),
    ("Name, message, current status, history, in that order", {"d1a": ("ok", "the columns"), "d1b": ("part", "cards for problems"), "d1c": ("part", "name, timeline, status"), "d1d": ("part", "tree, then panel"), "d1e": ("part", "one sentence")}),
    ("Health only: no value, no value sparkline", {v: ("ok", "tested") for v in ("d1a", "d1b", "d1c", "d1d", "d1e")}),
    ("The status line drops down into every diagnostic's lane", {"d1a": ("ok", "click it"), "d1b": ("ok", "click it"), "d1c": ("ok", "the lanes are the section"), "d1d": ("ok", "click it"), "d1e": ("ok", "click it")}),
    ("Thin tall bars with a clear gap (3 px, 2 px apart), never a line", {v: ("ok", "tested: <= 4.6 px wide, 2 px gap, 24 px tall") for v in ("d1a", "d1b", "d1c", "d1d", "d1e")}),
    ("Spark previews at a row's end", {"d1a": ("ok", "every row"), "d1b": ("ok", "every row"), "d1c": ("part", "lanes are full width"), "d1d": ("ok", "every leaf"), "d1e": ("part", "only where a problem happened")}),
    ("Statuspage's colours: green OK, yellow WARN, red ERROR, no data empty, no hatching", {v: ("ok", "tested, light and dark") for v in ("d1a", "d1b", "d1c", "d1d", "d1e")}),
    ("A rolled-up row shows only its worst icon, no count", {v: ("ok", "tested: folders and the status line") for v in ("d1a", "d1b", "d1c", "d1d", "d1e")}),
    ("Tells me at a glance when it went bad", {"d1a": ("part", "drop-down lanes"), "d1b": ("part", "the card's spark"), "d1c": ("ok", "the whole point"), "d1d": ("part", "the panel's timeline"), "d1e": ("no", "not its job")}),
    ("What to check is one click from the problem", {"d1a": ("part", "expand the row"), "d1b": ("ok", "already open"), "d1c": ("ok", "under the lane"), "d1d": ("ok", "in the panel"), "d1e": ("ok", "already open")}),
    ("Worst first, then by name", {v: ("ok", "sorted") for v in ("d1a", "d1b", "d1c", "d1e")} | {"d1d": ("part", "grouped Errors / Warnings / All")}),
    ("Dot-notation / \"who: what\" names fold into collapsible folders", {v: ("ok", "tested on the Computer") for v in ("d1a", "d1b", "d1c", "d1d", "d1e")}),
    ("\u2605 pin a row to the top, kept per entity kind", {v: ("ok", "tested, survives a reload") for v in ("d1a", "d1b", "d1c", "d1d", "d1e")}),
    ("Order inside a folder: severity, A-Z, or drag by hand", {"d1a": ("ok", "all three tested"), "d1b": ("part", "OK folders; cards stay worst-first"), "d1c": ("ok", "all three tested"), "d1d": ("part", "severity, A-Z; drag not tested"), "d1e": ("part", "severity, A-Z; drag not tested")}),
    ("No horizontal scroll at 1280x720", {v: ("ok", "tested on 4 real entities") for v in ("d1a", "d1b", "d1c", "d1d", "d1e")}),
]
MARK = {"ok": "&#10003;", "part": "~", "no": "&times;"}


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    media: Path = args.media
    rel = media.name
    shots = json.loads((media / "shots.json").read_text())
    gates = json.loads((media / "gates.json").read_text()) if (media / "gates.json").exists() else {}
    videos = {video["layout"]: video for video in shots["videos"]}
    have = {shot["file"] for shot in shots["shots"]}

    def picture(file: str, caption: str, cls: str = "") -> str:
        if not (media / file).exists():
            return ""
        return f'<figure class="{cls}"><a href="{rel}/{esc(file)}"><img src="{rel}/{esc(file)}" alt="{esc(caption)}" loading="lazy"></a><figcaption>{esc(caption)}</figcaption></figure>'

    parts: list[str] = []
    # ---- the chart: before / after
    ba = ""
    for entity, label in (("computer", "the Computer"), ("can15", "CAN 15 driver (a fault)")):
        for scheme in ("light", "dark"):
            before = f"before-d1-{entity}-{scheme}.png"
            after = f"d1-{entity}-{scheme}.png"
            if (media / before).exists() and after in have:
                ba += f'<div class="pair"><div><h4>Before · {esc(label)} · {scheme}</h4><a href="{rel}/{before}"><img src="{rel}/{before}" alt="before" loading="lazy"></a></div>' \
                      f'<div><h4>After · {esc(label)} · {scheme}</h4><a href="{rel}/{after}"><img src="{rel}/{after}" alt="after" loading="lazy"></a></div></div>'
    specimen = "".join(picture(f"specimen-{s}.png", f"Synthetic history, {s}: today's bar above, the new cells below, on the same bins", "spec") for s in ("light", "dark"))

    # ---- the variants
    cards = []
    for vid, name, idea, answers in VARIANTS:
        video = videos.get(vid)
        media_html = ""
        if video:
            media_html = (f'<video class="hero" src="{rel}/{esc(video["mp4"])}" autoplay muted loop playsinline controls preload="metadata">'
                          f'<img src="{rel}/{esc(video["gif"])}" alt="{esc(name)} hero (GIF fallback)"></video>'
                          f'<div class="cap">Hero video ({video["seconds"]:.0f} s, muted, loops; GIF fallback inside) on {esc(video["name"])}: starts on today\'s d1, picks {vid} from the Prototype select, then drives it.</div>')
        pics = ""
        for entity in ("computer", "fdcanusb", "can15", "camera"):
            for scheme in ("light", "dark"):
                file = f"{vid}-{entity}-{scheme}.png"
                if file in have:
                    pics += picture(file, f"{ENTITY_WORDS[entity]} · {scheme}")
        cards.append(f'<section class="variant" id="{vid}"><h3><code>{vid}</code> {esc(name)}</h3><p class="idea">{esc(idea)}</p><p class="best">Best for: {esc(answers)}.</p>{media_html}<div class="shots">{pics}</div></section>')

    # ---- criteria
    head = "".join(f"<th><code>{v[0]}</code><br><span>{esc(v[1])}</span></th>" for v in VARIANTS)
    rows = ""
    for ask, per in CRITERIA:
        cells = "".join(f'<td class="{per[v[0]][0]}" title="{esc(per[v[0]][1])}"><b>{MARK[per[v[0]][0]]}</b><span>{esc(per[v[0]][1])}</span></td>' for v in VARIANTS)
        rows += f"<tr><th>{esc(ask)}</th>{cells}</tr>"

    # ---- the colour section: the palette, its source, the numbers, the three entities
    numbers = health_palettes.report()["statuspage"]
    palette = health_palettes.palettes()["statuspage"]
    swatch = lambda hexv, label: f'<span class="sw"><i style="background:{hexv}"></i>{esc(label)} <code>{esc(hexv)}</code></span>'  # noqa: E731
    sw_light = "".join(swatch(palette["light"][k], k) for k in ("ok", "warn", "error", "stale", "unknown"))
    sw_dark = "".join(swatch(palette["dark"][k], k) for k in ("ok", "warn", "error", "stale", "unknown"))
    rows_n = ""
    for mode in ("light", "dark"):
        e = numbers[mode]
        c = e["contrast"]
        a = e["apart"]
        rows_n += (f"<tr><th>{mode} (page {e['background']})</th><td>{c['ok']}:1</td><td>{c['warn']}:1</td><td>{c['error']}:1</td><td>{c['stale']}:1</td><td>{c['unknown']}:1</td>"
                   f"<td>{a['ok/stale']['dE']} · {a['ok/unknown']['dE']} · {a['stale/unknown']['dE']}</td></tr>")
    colour_pics = "".join(picture(f"colours-{entity}-{scheme}.png", f"{ENTITY_WORDS.get(entity, entity)} · {scheme}") for entity in ("computer", "can15", "stale") for scheme in ("light", "dark"))
    colour_cmp = picture("colours-comparison.png", "The same three entities, light and dark, side by side (the Diagnostics section's top, in d1a)", "spec")
    colours_html = f"""<h2>Colours and bar shape: Statuspage's</h2>
<p>Your call: <em>"we don't need to reinvent the wheel ... my eye is trained actually to look for green as OK. when I see OK and grey I think it's something else"</em>, shown Atlassian Statuspage's 90-day uptime strip. So there is no palette to choose between (the five proposals are gone): one convention, one token set (<code>--hc-ok</code> ... in <code>detail/health.js</code>), drawn by every health cell: the d1 variants, the row-end History bars, the status line and its drop-down lanes, the children's mini bars, d2-d5, and the entity page's Diagnostics tab.</p>
<p><b>Source.</b> The stylesheet every Statuspage status page loads (<code>status_manifest-*.css</code> on <code>dka575ofm4ao0.cloudfront.net</code>, read from githubstatus.com on 2026-10-24), the uptime strip's rules: <code>.unit-container.green {{ background-color: #2ecc71 }}</code>, <code>.yellow {{ #f1c40f }}</code>, <code>.red {{ #e74c3c }}</code>, and the strip's shape, <code>.unit-container {{ height: 25px; margin-left: .75% }}</code> (thin bars with a gap). WARN is that yellow and ERROR that red, unchanged, in both themes. OK is that green "slightly muted so it's quiet": 70 % of <code>#2ecc71</code> mixed with 30 % of neutral grey <code>#9a9a9a</code>, which is <code>#4ebd7d</code> (my derivation, the one colour not copied). No data is <b>empty</b> (no fill, a faint outline) as you asked; Statuspage itself draws a light grey bar there (that is from your description, not from its CSS). Stale and unknown are two greys, never hatched.</p>
<p class="swatches"><b>light</b> {sw_light}</p><p class="swatches"><b>dark</b> {sw_dark}</p>
<p class="muted">The heavier grey is UNKNOWN (can't tell: look), the lighter STALE (it had data and lost it). In the light theme heavier is darker; in the dark theme a grey darker than the page disappears, so heavier is lighter there: the order of weight is kept, the direction of lightness flips.</p>
<p><b>The bar rule (what sets the number of divisions).</b> Each bar is <b>3 px wide</b> with a <b>2 px gap</b> and 24 px tall (14 px at the end of a row). The count is what the width holds, <code>floor((width + 2) / 5)</code>; each bar covers <code>History window / count</code> seconds. So a wider pane gives more bars (more resolution), the History window sets the span, and the gap never shrinks, so the bars never run into a line. 700 px holds 140 bars: 15 min is 6.4 s a bar, 1 h is 26 s. A row's 96 px History column holds 19. Each bar's hover gives its time range and the worst level any diagnostic held in it.</p>
<table><thead><tr><th>against the page</th><th>OK</th><th>WARN</th><th>ERROR</th><th>stale</th><th>unknown</th><th>delta E: ok/stale · ok/unknown · stale/unknown</th></tr></thead><tbody>{rows_n}</tbody></table>
<p class="muted">WCAG contrast ratios of each colour against the detail pane's own background (default skin), by <code>docs/health_palettes.py</code>, which reads the colours out of <code>health.js</code>. Honest numbers: Statuspage's yellow is only 1.6:1 on a white page (it is a saturated hue on a neutral ground, and is the same yellow Statuspage draws on white); everything else clears 2.2:1 and the problem colours clear 3.6:1 in the light theme. Delta E (CIE76) says how far apart two colours look: 27 or more between the greys, so they stay distinguishable.</p>
{colour_cmp}
<div class="shots">{colour_pics}</div>"""

    g = gates
    gate_html = (f"<ul><li><b>{esc(g.get('variants', '?'))}</b> <code>tests/test_diag_variants.js</code>: on the real entities of F1 and F2 (the Computer's 17 statuses, an fdcanusb, a CAN board with a fault, a camera): the variants (single-line rows, health only, the status line drops into lanes), the bar shape (3 px wide, 2 px apart, 24 px tall; the count follows the pane; the History window sets what a bar covers), the palette (Statuspage's yellow and red exactly, a muted green OK, no data empty, no hatching, two distinct greys, contrast floors, one token set in d1a, d1c and the entity page), folders / pins / order, icon-only rollups, and mutation checks that turn the gap, aspect, health-only, hatch, no-data and colour gates red</li>"
                 f"<li>Existing suites: {esc(g.get('existing', '?'))}</li></ul>")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RobotDx Diagnostics variants</title>
<style>
:root {{ color-scheme: light dark; --bg:#fbfaf8; --fg:#1d1c1a; --mut:#6a665f; --line:#e3dfd8; --card:#fff; --ok:#2f6f46; --warn:#a15c07; --no:#8a8780; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161513; --fg:#ecebe8; --mut:#a09c94; --line:#2d2b28; --card:#1d1c1a; --ok:#7fc59a; --warn:#e0a24a; --no:#77736c; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 system-ui, sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:28px 20px 80px; }}
h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ margin:42px 0 10px; font-size:20px; border-top:1px solid var(--line); padding-top:22px; }} h3 {{ margin:0 0 6px; font-size:18px; }} h4 {{ margin:0 0 6px; font-size:12px; color:var(--mut); font-weight:600; }}
.verdict {{ font-size:17px; margin:10px 0 18px; }} code {{ font:13px ui-monospace, monospace; background:color-mix(in srgb, var(--fg) 8%, transparent); padding:1px 5px; border-radius:4px; }}
.muted {{ color:var(--mut); }} pre {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; overflow:auto; font:13px ui-monospace, monospace; }}
img {{ max-width:100%; display:block; border:1px solid var(--line); border-radius:6px; }} video {{ width:100%; max-width:900px; border:1px solid var(--line); border-radius:8px; background:#000; display:block; }}
.variant {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px 20px; margin:18px 0; }} .idea {{ margin:0 0 4px; }} .best {{ margin:0 0 12px; color:var(--mut); }}
.cap {{ color:var(--mut); font-size:12.5px; margin:6px 0 14px; }} .shots {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(330px, 1fr)); gap:12px; }}
figure {{ margin:0; }} figcaption {{ font-size:12px; color:var(--mut); margin-top:3px; }} .pair {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:14px 0; }} .spec img {{ max-width:900px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; background:var(--card); }} th, td {{ border:1px solid var(--line); padding:6px 8px; vertical-align:top; }} thead th {{ text-align:center; }} thead th span {{ font-weight:400; color:var(--mut); font-size:12px; }}
tbody th {{ text-align:left; font-weight:500; width:26%; }} td b {{ display:block; font-size:15px; }} td span {{ color:var(--mut); font-size:11.5px; }} td.ok b {{ color:var(--ok); }} td.part b {{ color:var(--warn); }} td.no b {{ color:var(--no); }}
.decide li {{ margin:6px 0; }} .swatches {{ margin:4px 0; }} .sw {{ display:inline-flex; align-items:center; gap:5px; margin-right:14px; font-size:12.5px; }} .sw i {{ display:inline-block; width:12px; height:18px; border-radius:2px; border:1px solid var(--line); }}
.rec {{ border-left:4px solid var(--ok); padding:4px 14px; background:var(--card); border-radius:0 8px 8px 0; }}
@media (max-width:760px) {{ .pair {{ grid-template-columns:1fr; }} }}
</style></head><body><main>
<h1>RobotDx Diagnostics: five variants of the status monitor, and the new health chart</h1>
<p class="muted">2026-10-24 · lane <code>lane/diagnostics-variants</code> @ {esc(g.get('head', '?'))} · from Zach's notes on the entity page (daily note, "My feedback on the entity page")</p>
<p class="verdict"><b>d1a is the Diagnostics default: Name | Message | Current | History, one line a row, Statuspage's colours, thin 3 px bars, rolled-up rows show only their worst icon, and the entity page's Diagnostics tab is d1a.</b> The other four (d1b-d1e) stay in the Prototype picker. On <code>lane/diagnostics-variants</code>, not merged.</p>
<div class="rec"><p><b>What changed since the first report.</b> You picked d1a. On colours you said not to reinvent the wheel and that your eye reads green as OK, so the five-palette idea is gone: the palette is Atlassian Statuspage's own (section "Colours and bar shape"), the bars are its proportions (thin, fixed, a gap), the number of bars follows the width, the columns read Current and History, and a rolled-up row (a folder, the status line) shows only the highest value of what it holds, as an icon, never a count. The entity page's Diagnostics tab draws d1a through the one history renderer.</p></div>

<h2>The chart: before and after</h2>
<p>From your notes: a state timeline, <b>never a line, never a value sparkline</b>, thin so a wider pane gives more resolution. The renderer is one file, <code>detail/health.js</code>; d1, d1a-d1e, the entity page's Diagnostics tab and the older d2-d5 all draw through it (rule, palette and numbers below).</p>
{specimen}
{ba}

{colours_html}

<h2>The five variants</h2>
<p class="muted">Pick one from the bottom-right <b>Prototype</b> select (<code>d1a</code> .. <code>d1e</code>); each renders the real entities of the fixture hosts, and each also exports <code>diagnostics(node, detail, ctx)</code>, the section alone, for the entity-page lane.</p>
{''.join(cards)}

<h2>Folders, pins and order</h2>
<p>Your note: <em>"dot notation arm.servo.current and you can parse a folder structure from that... within the folders... alphabetical? manual? pin to the top?"</em> Every variant has all three, from one helper, <code>detail/roworder.js</code> (<code>window.IORowOrder</code>), which knows nothing about diagnostics: a row is <code>{{id, name, rank}}</code>, so the State and Configuration sections build the same tree.</p>
<ul>
<li><b>Folders.</b> A name reads <code>&lt;who&gt;: &lt;what&gt;</code>. The who and the what each split on dots when they are dot-notation (<code>arm.servo.current</code> or <code>arm.servo: current</code> is arm / servo / current). <b>What I mapped:</b> <code>bam-GPU: CPU Information</code> is folder <code>bam-GPU</code>; <code>fdcanusb 272813EF CAN 1: firmware</code> is <code>fdcanusb 272813EF</code> / <code>CAN 1</code> / firmware (a who ending in <code>CAN &lt;n&gt;</code> splits in two); <code>CAN 15: comms</code> is folder <code>CAN 15</code>; a version number (<code>5.15.1.55 installed</code>) is not a path. A folder holding one row is not drawn (the row keeps its full name); a single folder holding every row is dropped, because the entity's header already says it. Folders open and close, remembered.</li>
<li><b>Pin.</b> The &#9733; at the front of a row (shown on hover, solid once pinned) moves it to a <em>Pinned</em> block above the folders.</li>
<li><b>Sort inside a folder.</b> Severity (the Diagnostics default: worst first, then name), A-Z (numbers in order: CAN 2 before CAN 15), or Manual: a grip appears, drag a row or a folder onto a sibling of the same folder.</li>
<li><b>Kept</b> in the page's localStorage per section and entity kind: <code>io-diagnostics.rows.&lt;section&gt;.&lt;kind&gt;</code> (<code>diagnostics</code> here; the State lane uses <code>state</code>), holding <code>{{sort, pins, manual, closed, opened}}</code>.</li>
</ul>
<pre>IORowOrder.folderPath(name)                      -> {{path: [folders], leaf}}
IORowOrder.plan(rows, kind, section, {{idPrefix}}) -> {{pinned: [row], items: [folder | row item], sort}}
IORowOrder.prefs / setSort / togglePin / toggleFolder / isOpen / moveManual   (each saves)
IORowOrder.toolbar(kind, section)  star(kind, section, id)  dragAttrs / handle / wireDrag(container, onMove)</pre>

<h2>Against your asks</h2>
<table><thead><tr><th>Your ask</th>{head}</tr></thead><tbody>{rows}</tbody></table>
<p class="muted">&#10003; met · ~ met with the caveat shown · &times; not what it is for. The three "tested" rows are gates in <code>tests/test_diag_variants.js</code>.</p>

<h2>Proof</h2>
{gate_html}
<pre>/usr/bin/node /home/bam/io-diagnostics-lanes/diagnostics-variants/tests/test_diag_variants.js</pre>

<h2>Decision surface</h2>
<ul class="decide">
<li><b>Done and proved.</b> d1a is the Diagnostics default and the entity page's Diagnostics tab draws it (with that page's fault meaning, log link and flash kept, through hooks). One palette from Statuspage's CSS, in every health cell, light and dark; thin bars whose count follows the width; Current / History headers; icon-only rollups. Gate counts and the old suites are in Proof.</li>
<li><b>Changed in another lane's test.</b> <code>tests/test_entity_page.js</code> line 327 looked for the lit row as <code>.d6-r.d6-flash</code>; the row is now d1a's <code>.dx-r.d6-flash</code>, so the selector reads <code>.d6-panel .dx-r.d6-flash</code>. Nothing else in that file changed (92 / 92).</li>
<li><b>For the integration lane.</b> The entity page calls <code>IODetailLayouts.d1a.embed(container, node, detail, ctx, hooks)</code> (hooks: <code>rowClass(row)</code>, <code>extra(row)</code>); State and Configuration can call <code>IORowOrder.plan(rows, node.kind, 'state')</code> for the same folders, pins and sort. The d6 table that d1a replaced stays in <code>d6.js</code> as a fallback only if d1a did not load; the integration can delete it. d6's own row model (<code>IOEntityRows</code>) and my <code>IORowOrder</code> are two implementations of one idea: they should become one.</li>
<li><b>Not done, on purpose.</b> No value or measurement in any Diagnostics section. The tree's own rollups are in another lane. d4 and d5 still draw a value next to a health strip in a row (older proposals). No merge, no push.</li>
</ul>
</main></body></html>"""
    args.out.write_text(page)
    print(f"wrote {args.out} ({len(page) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
