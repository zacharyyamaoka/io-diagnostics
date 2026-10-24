#!/usr/bin/env python3
"""The comparison report of the five chart proposals (lane chart-enlarge, 2026-10-24): hero videos, stills in light and dark, the weighted
criteria, the scores with their evidence, the hard gates, the one-call adoption hook and the decision surface.

    /usr/bin/python3 /home/bam/io-diagnostics/docs/build_chart_enlarge_report.py --media DIR --out FILE --sha SHA --lane-url URL \
        --gates "charts=83/83;view_core=128/128;..."

``--media`` holds what docs/record_chart_heroes.js wrote (<id>-hero.mp4/.gif/.json and <id>-<state>-<light|dark>.png); the page links to them
by relative path, so the folder sits next to the report. The criteria and their weights are written here BEFORE the scores below them are
computed: the weighted total is a function of this file, never typed in.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

# ----- the criteria: written from Zach's note (daily note 2026-10-24), frozen before the scores are read -----------------------------
CRITERIA = [
    ("sense", "A sense of the line", 22,
     "Zach: \"it doesn't need to be crazy big, just getting like a sense of what is going on.\" Axes, scale, limits and a value under the cursor.",
     ("1", "Nothing more than the sparkline", "3", "A larger line, axes, one of limits / readout / min-max", "5", "Value and time axes, the status limits, a cursor readout, min / max / now")),
    ("place", "Stays where I am", 22,
     "\"does it happen on the same page.. in a different page..\": the cost, in context lost, of looking and of coming back.",
     ("1", "Leaves the entity; several steps back", "3", "Stays on the page but covers or moves a lot", "5", "Nothing moves or navigates; one gesture to look, one to dismiss")),
    ("many", "Several values at once", 10,
     "\"make like your own little custom monitoring dashboard????\": a wish with four question marks, so a modest weight.",
     ("1", "One value at a time", "3", "A few values readable together", "5", "Any values from any entities side by side or overlaid")),
    ("quiet", "Calm, standing rules", 14,
     "Calm stock shadcn; every pane boundary draggable and remembered; warning colours only for real warnings and errors.",
     ("1", "New chrome, loud, state lost on reload", "3", "Some new chrome, boundaries remembered", "5", "No new chrome; nothing floats unasked")),
    ("depth", "Depth when I want it", 8,
     "Zoom, pan, the health timeline on the same axis, an export: when the first look is not enough.",
     ("1", "None", "3", "Cursor and window", "5", "Zoom and pan, the health timeline on the same axis, CSV")),
    ("adopt", "Easy for the State section to adopt", 14,
     "The entity page's State section (rows: Name, current value, sparkline) adopts the chosen proposal with one call, whatever its row layout.",
     ("1", "Needs the section redrawn", "3", "One call plus a layout rule the row must meet", "5", "One call, no layout dependence")),
    ("small", "Small surface to keep", 10,
     "New state, new chrome, a host change, moving parts that can break under the 1 Hz redraw.",
     ("1", "A host change and a lot of new state", "3", "A few moving parts, no host change", "5", "Little state, nothing to keep in sync")),
]
GATES = [
    ("same", "Shows the same data as the sparkline", "the enlarged chart's last value is the last point of the series the sparkline is drawn from (the shell's own history cache)"),
    ("read", "Readable at 1400 x 900", "axis and label text 11 px, no two labels overlapping, every label inside the figure"),
    ("scroll", "No horizontal scroll at 1280 x 720", "page and body, with the proposal open (c3: docked at the bottom and at the right)"),
    ("warn", "Amber / red only where a status uses it", "limit lines and problem spans carry data-diagnostic; nothing else in the new surfaces computes to a warn / error colour"),
    ("panes", "Pane boundaries draggable and remembered", "only where a proposal has panes: c3's pane, c4's tiles"),
]

# ----- the proposals ----------------------------------------------------------------------------------------------------------------------
PROPOSALS = [
    dict(id="c1", name="Peek", file="c1.js",
         thesis="Hover or press a sparkline and a larger chart opens beside it, then goes away. Nothing on the page moves.",
         where="A popover beside the sparkline, on top of the page.",
         prior="Grafana and Datadog sparkline tooltips; GitHub's hover cards.",
         decisions=["Hover opens after 180 ms; a press pins it (Esc, the x or a press outside closes), so reading it needs no steady hand.",
                    "The popover has its own window selector (1 / 5 / 15 min, the API's window=): peeking never changes the app-wide window.",
                    "The row's own tooltip is suppressed on the sparkline: two tooltips at once would fight."],
         best="you glance at many values in a row and want the line larger for a second.",
         loses="you want two values next to each other, or the chart to stay while you scroll.",
         keep=["the pinned popover with a cursor readout", "the per-chart window selector", "tooltip-over-sparkline only, never over the row"],
         states=[("hover", "Hover on CPU: axes, limit line, the readout under the cursor"), ("limits", "Temperatures: warn and error limits as dashed lines"), ("pinned", "Pinned after a press (5 min window)")],
         scores=dict(sense=(4, "high", "500 x 285 px: value and time axes (11 px labels), limits as dashed lines, min / max / now, a cursor readout. Smaller than c2 and c5 by design."),
                     place=(5, "high", "Nothing moves or navigates. Hover opens in 180 ms, leaving closes, a press pins, Esc closes; Enter on the row's button pins from the keyboard."),
                     many=(1, "high", "One series group at a time."),
                     quiet=(4, "medium", "No new chrome. A hover popover can fire while the pointer only passes over the list; the press-to-pin is the answer to chasing it."),
                     depth=(2, "high", "Window selector and cursor; no zoom or pan, no health strip."),
                     adopt=(5, "high", "A body-level popover needs no room in the row, so any row layout works. One wrap() call."),
                     small=(4, "medium", "No host change. The hover timing and keeping the anchor across the 1 Hz redraw are the parts that can break; both are tested."))),
    dict(id="c2", name="Unfold in place", file="c2.js",
         thesis="Press a sparkline and its row unfolds into a full-width chart right there. Press the header and it folds.",
         where="Inside the row, under its name and value; the rows below move down.",
         prior="GNOME System Monitor's Resources sections; rqt_plot rows.",
         decisions=["The unfolded cell spans the whole row (grid-column 1 / -1), so the chart is as wide as the page and still 176 px tall: a sense of the line, not a chart you have to manage.",
                    "Open rows are remembered (a reload keeps them), several can be open at once and read in turn.",
                    "The chart sits in a shadow root under the cell, and the open / folded state is regenerated into the markup every draw, so the 1 Hz redraw cannot undo it."],
         best="you want the line bigger on the same page, one or two at a time, with the value still in view.",
         loses="the list is long and rows pushing each other down is annoying, or you want to overlay values.",
         keep=["full-width unfold with the window selector in the chart's foot", "open state remembered", "the header press that folds it"],
         states=[("open", "CPU unfolded under its row"), ("two-open", "Temperatures unfolded as well: limits, min / max / now, the window")],
         scores=dict(sense=(5, "high", "760 x 176 px at 1400 wide: value and time axes, the status limits, min / max / now, the window, a cursor readout, right under the row's value."),
                     place=(4, "high", "Same page, one press each way, remembered. The rows below shift down while it is open."),
                     many=(3, "high", "Several rows can be unfolded and read in turn; they never overlay."),
                     quiet=(4, "medium", "Nothing floats and there is no new chrome. The page's height changes when you press, which is what pressing means here."),
                     depth=(2, "high", "Window selector (app-wide) and cursor; no zoom or pan."),
                     adopt=(4, "medium", "The wrapper must be a grid child so the unfolded cell can span the row (a flex-basis fallback is there, untested on a real State row)."),
                     small=(4, "medium", "No host change. The morph risk was designed out (shadow root, state in the markup) and tested across redraws and a reload."))),
    dict(id="c3", name="Plot pane", file="c3.js",
         thesis="tview's own model: click or right-click a sparkline to plot it in one docked pane where several series overlay with a legend.",
         where="A pane docked at the bottom or the right of the window, over all pages.",
         prior="moteus tview (right-click a leaf: Plot Left / Plot Right, one plot, two axes, a legend); PlotJuggler; Foxglove Plot.",
         decisions=["A click toggles a series (a series in another unit goes to the right axis); right-click offers Plot Left / Plot Right, as tview does.",
                    "The legend shows each series' value under the cursor, else the latest; an axis chip moves a series.",
                    "The pane's edge is draggable, its size is remembered per dock, closing it keeps the series, and a toolbar button brings it back."],
         best="you already think in tview: pick some signals, look at them together on one time axis.",
         loses="you only want a quick larger look, or the screen is already full.",
         keep=["Plot Left / Plot Right with the axis chips", "the legend that doubles as the cursor readout", "the remembered dock and size"],
         states=[("menu", "Right-click on Network: Plot Left / Plot Right"), ("bottom", "Four series over two axes, the cursor reading them all"), ("right", "Docked at the right after dragging its edge")],
         scores=dict(sense=(4, "high", "A large plot with value and time axes and a legend with values. Several lines in one frame is busier than one."),
                     place=(4, "high", "No navigation; the pane docks and is remembered. It takes window space while it is open."),
                     many=(5, "high", "Any number of series from any entities overlay on one time axis; the test overlays four on two axes."),
                     quiet=(4, "medium", "One toolbar button and a pane you opened. The edge is draggable and remembered, as the standing rule asks."),
                     depth=(3, "high", "Cursor values for every series, pause, axis chips; no zoom or pan, no health strip."),
                     adopt=(4, "medium", "One wrap() call. It adds a pane to the window layout (the right dock needs two CSS overrides on .main)."),
                     small=(3, "medium", "The most layout moving parts of the five: two docks, two sashes, series persistence, the right-dock overrides."))),
    dict(id="c4", name="Pin to my dashboard", file="c4.js",
         thesis="Pin any sparkline and its chart becomes a tile on a Dashboard view: your own monitoring board, saved with the app's settings.",
         where="A Dashboard view beside Tree / Table / Activity: a grid of tiles from any entities.",
         prior="Grafana, Home Assistant and Netdata dashboards.",
         decisions=["Pinning is in place (a pin on the row, or a click on the sparkline, and a toast); looking at the board is a deliberate trip to its view.",
                    "Tiles resize by their corner (a 12-column grid) and reorder by dragging their header; x removes one.",
                    "The board is stored in the app's settings (POST /api/v1/settings {dashboard}), so it survives a reload, a cleared browser and another port. This is the one host change in the lane."],
         best="you keep coming back to the same handful of values and want them on one screen.",
         loses="you wanted a quick look: a board is setup you have to want.",
         keep=["pin from any row, tile size and order saved", "Add from this page", "the host-side board"],
         states=[("pin-button", "The pin on a row, shown on hover"), ("board", "Three tiles from the Computer page"), ("arranged", "After resizing CPU and reordering"), ("after-reload", "After a reload: the same board")],
         scores=dict(sense=(4, "high", "Tiles are chart-sized (at least 440 px) with axes, limits and a readout; between a peek and a page."),
                     place=(3, "high", "Pinning is in place, but reading the board is a trip to another view that hides the tree."),
                     many=(5, "high", "Any values, any entities (the board stores a path per tile), arranged by drag and resize. Browser-tested here with one entity only."),
                     quiet=(3, "medium", "A new view, a toolbar button with a count, a toast. Tiles resize and reorder and the board is saved, as the standing rule asks."),
                     depth=(2, "high", "Cursor readout only."),
                     adopt=(3, "medium", "One wrap() call plus a pin affordance on each cell; the host needed a new setting."),
                     small=(2, "high", "The largest surface: a settings key in the host and its test, drag / resize / reorder code, a board to keep compatible."))),
    dict(id="c5", name="Focus page", file="c5.js",
         thesis="Press a sparkline and the value gets a page of its own: zoom and pan the history, the health timeline underneath on the same axis, CSV.",
         where="A page that replaces the tree and detail; Back, Esc or the browser's Back returns.",
         prior="Grafana Explore; rqt_plot's zoomable canvas.",
         decisions=["The health timeline is drawn by the same chart on the same x scale: what the status said lines up with where the value spiked, also when zoomed.",
                    "Each open is one browser-history entry (the shell's page drill: ?page=chart:...), so a reload or a pasted URL lands on the same chart and Back works.",
                    "CSV exports what is in view, one column per series."],
         best="a value did something odd and you want to know why: zoom in, read the status at that moment, take the numbers away.",
         loses="you only wanted a larger look: a page is a trip.",
         keep=["the health strip on the same x scale", "wheel zoom, drag pan, double-click to reset", "CSV of the visible range"],
         states=[("page", "Temperatures on its own page: the health strip under the chart"), ("zoomed", "Zoomed in with the wheel (5 min of 15)")],
         scores=dict(sense=(5, "high", "The largest chart (1350 x 529 at 1400 x 900), zoom and pan, 11 px axes, min / max / mean of what is in view."),
                     place=(2, "high", "A page of its own: it replaces the tree. Back, Esc and the browser's Back return, but the entity is out of sight meanwhile."),
                     many=(2, "high", "The series of one wrapper (CPU shows average and max); no comparison across entities."),
                     quiet=(4, "medium", "Quiet and one thing at a time; the URL restores it. A full-window page is the heaviest change of context."),
                     depth=(5, "high", "Zoom and pan, the health timeline on the same x scale (tested: same extent), CSV of the range in view."),
                     adopt=(4, "medium", "One wrap() call plus the shell's page drill; three hooks were added to the chart ctx in app.js."),
                     small=(3, "medium", "Zoom / pan state, follow-live logic, CSV and a page URL: more code than a popover, no host change."))),
]


def totals(proposal: dict) -> float:
    return sum(weight * proposal["scores"][key][0] / 5 for key, _name, weight, *_rest in CRITERIA)


def flips(criterion: str, challenger: str, champion: str) -> float | None:
    """The smallest weight on ``criterion`` (the others shrinking in proportion, the sum staying 100) at which ``challenger`` beats ``champion``."""

    base = {key: weight for key, _name, weight, *_rest in CRITERIA}
    by_id = {p["id"]: p for p in PROPOSALS}
    others = 100 - base[criterion]
    for tenth in range(int(base[criterion] * 10), 700):
        w = tenth / 10
        scaled = {key: (w if key == criterion else base[key] * (100 - w) / others) for key in base}
        score = lambda pid: sum(scaled[key] * by_id[pid]["scores"][key][0] / 5 for key in base)  # noqa: E731
        if score(challenger) > score(champion):
            return w
    return None


def esc(text) -> str:
    return html.escape(str(text), quote=True)


CSS = """
:root { --bg:#fff; --surface:#fafafa; --text:#09090b; --muted:#52525b; --faint:#71717a; --border:#e4e4e7; --soft:#f4f4f5; --accent:#18181b; --info:#2563eb; --ok:#15803d; --warn:#b45309; }
@media (prefers-color-scheme: dark) { :root { --bg:#09090b; --surface:#101013; --text:#fafafa; --muted:#a1a1aa; --faint:#8b8b94; --border:#27272a; --soft:#18181b; --accent:#fafafa; --info:#60a5fa; --ok:#4ade80; --warn:#f59e0b; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.55 -apple-system, "Segoe UI", system-ui, Roboto, sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 28px 24px 80px; }
h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -.01em; } h2 { font-size: 20px; margin: 44px 0 10px; letter-spacing: -.01em; } h3 { font-size: 16px; margin: 0 0 4px; }
p { margin: 8px 0; } .muted { color: var(--muted); } .faint { color: var(--faint); font-size: 13px; }
code, pre { font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace; font-size: 12.5px; } code { background: var(--soft); padding: 1px 5px; border-radius: 4px; }
pre { background: var(--soft); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; overflow-x: auto; }
.verdict { border: 1px solid var(--border); background: var(--surface); border-radius: 12px; padding: 16px 18px; margin: 18px 0; }
.verdict b.big { font-size: 17px; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; } th, td { text-align: left; padding: 7px 10px 7px 0; border-top: 1px solid var(--border); vertical-align: top; }
th { color: var(--faint); font-weight: 500; font-size: 12px; border-top: 0; } td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.card { border: 1px solid var(--border); border-radius: 12px; padding: 18px 18px 14px; margin: 18px 0; background: var(--bg); }
.card header { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; } .tag { font: 600 12px ui-monospace, Menlo, monospace; background: var(--soft); border: 1px solid var(--border); border-radius: 6px; padding: 1px 7px; }
video, img { max-width: 100%; display: block; border-radius: 8px; border: 1px solid var(--border); background: var(--soft); }
.hero { margin: 12px 0; } .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; } .pair figure { margin: 0; } figcaption { font-size: 12.5px; color: var(--muted); margin-top: 4px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 28px; } .cols > div { min-width: 0; }
details { margin: 8px 0; } summary { cursor: pointer; color: var(--muted); font-size: 13.5px; }
.pass { color: var(--ok); font-weight: 600; } .na { color: var(--faint); }
.score { font-weight: 600; font-variant-numeric: tabular-nums; } .conf { color: var(--faint); font-size: 11.5px; }
.bar { height: 6px; background: var(--soft); border-radius: 3px; overflow: hidden; min-width: 80px; } .bar i { display: block; height: 100%; background: var(--info); }
.tick { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: var(--info); opacity: .85; }
.q li { margin: 3px 0; } .done li::marker { color: var(--ok); }
@media (max-width: 760px) { .pair, .cols { grid-template-columns: 1fr; } main { padding: 20px 16px 60px; } }
"""


def media_block(media: Path, proposal: dict) -> str:
    pid = proposal["id"]
    info_path = media / f"{pid}-hero.json"
    seconds = json.loads(info_path.read_text())["seconds"] if info_path.exists() else None
    cap = f"{seconds:.0f} s, muted, loops" if seconds else "muted, loops"
    video = (f'<figure class="hero"><video controls muted loop autoplay playsinline preload="auto" poster="">'
             f'<source src="chart-enlarge/{pid}-hero.mp4" type="video/mp4"><img src="chart-enlarge/{pid}-hero.gif" alt="{esc(proposal["name"])} hero (GIF fallback)">'
             f'</video><figcaption>Hero: the real page on the fixture host, {cap}. <a href="chart-enlarge/{pid}-hero.gif">GIF fallback</a></figcaption></figure>')
    stills = ""
    for state, caption in proposal["states"]:
        light, dark = f"chart-enlarge/{pid}-{state}-light.png", f"chart-enlarge/{pid}-{state}-dark.png"
        if not (media / f"{pid}-{state}-light.png").exists():
            continue
        stills += (f'<div class="pair" style="margin:10px 0"><figure><a href="{light}"><img loading="lazy" src="{light}" alt="{esc(caption)} (light)"></a><figcaption>{esc(caption)} · light</figcaption></figure>'
                   f'<figure><a href="{dark}"><img loading="lazy" src="{dark}" alt="{esc(caption)} (dark)"></a><figcaption>{esc(caption)} · dark</figcaption></figure></div>')
    return video + stills


def build(args) -> str:
    media = Path(args.media)
    ranked = sorted(PROPOSALS, key=totals, reverse=True)
    top = totals(ranked[0])
    co = [p for p in ranked if top - totals(p) <= 3.0]
    gates = dict(item.split("=") for item in args.gates.split(";") if item)
    out = []
    out.append(f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Enlarging a sparkline</title><style>{CSS}</style></head><body><main>")
    out.append("<p class='faint'>RobotDx · lane <code>lane/chart-enlarge</code> · 2026-10-24</p><h1>Five ways to enlarge a sparkline</h1>")
    out.append("<p class='muted'>Zach, 2026-10-24: \"it doesn't need to be crazy big, just getting like a sense of what is going on... does it happen on the same page.. in a different page..\" Each proposal answers \"where does the bigger line live\" differently; all five run in the real page on the Computer's Resource monitor rows, selected live from the bottom-right <b>Prototype</b> picker (<b>Charts</b>: c0 is today).</p>")
    lead_names = ", ".join(f"<b>{p['id']} {esc(p['name'])}</b> ({totals(p):.1f})" for p in co)
    out.append("<div class='verdict'><b class='big'>Three of the five are close enough that this is a taste call; I'd take c2 (Unfold in place) for the State section.</b>"
               f"<p>Weighted totals: {', '.join(f'{p['id']} {totals(p):.1f}' for p in ranked)} (out of 100). The top {len(co)} ({lead_names}) are within 3 points, so the ranking is fragile: the scores are my judgments from what ran, not measurements. "
               "c2 answers the question as asked (the same page, not crazy big, the value still in view) with the least new chrome; c1 is the lightest if you would rather not move the page; c3 is tview's model if you want to compare values. All five pass every hard gate. "
               "<b>Lives on the branch <code>lane/chart-enlarge</code>, not merged.</b> One host change: c4 needs a <code>dashboard</code> setting in <code>POST /api/v1/settings</code>.</p></div>")
    out.append("<h2>The decision surface</h2><div class='cols'><div><h3>Done and proved</h3><ul class='done'>"
               f"<li>Five proposals built over one shared helper (<code>web/charts/core.js</code>), one file each (<code>c1.js</code> .. <code>c5.js</code>), selectable live from the Prototype picker.</li>"
               f"<li><code>tests/test_charts.js</code>: <b>{esc(gates.get('charts', '?'))}</b> checks in the real page (headless Chrome, a fixture host with 2 Hz moving series): where each chart appears, the same data as the sparkline, readable axes, no horizontal scroll at 1280 x 720, the warn-colours rule, c3 overlays four series on two axes, c4's board survives a reload and a cleared browser.</li>"
               f"<li>Existing suites unchanged and green: view_core {esc(gates.get('view_core', '?'))}, p1_computer {esc(gates.get('p1_computer', '?'))}, utilities {esc(gates.get('utilities', '?'))}, tree_variants {esc(gates.get('tree_variants', '?'))}, runs_view {esc(gates.get('runs_view', '?'))}, r2 {esc(gates.get('r2', '?'))}, r3 {esc(gates.get('r3', '?'))}, r5 {esc(gates.get('r5', '?'))}; pytest {esc(gates.get('pytest', '?'))}.</li>"
               "</ul></div><div><h3>Left, and what needs you</h3><ul class='q'>"
               "<li><b>Pick one (or a splice).</b> My recommendation: c2. Default if you say nothing: the State-section lane adopts c2 with the one call below, and c1 stays selectable.</li>"
               "<li><b>c4's host change</b> (a <code>dashboard</code> key in settings.toml): only if c4 is chosen or kept. Default: it stays on this branch with the lane and is not merged on its own.</li>"
               "<li>Not yet seen on a rack with measurements from several entities (the fixture has one entity with series); c3 and c4 take any path, c1 / c2 / c5 are per row. Default: judge on the Computer page; I will look at a moteus board's series when its plugin reports them.</li>"
               "</ul><h3>Deliberately not done</h3><ul class='q'><li>Health history (the discrete rectangles) is another lane's chart; c5 only draws a status's spans under a value on the same axis and never mixes them into the value line.</li>"
               "<li>No touch-device pass, no merge, no push, nothing real acted on or touched.</li></ul></div></div>")

    out.append("<h2>What good means (written before the scores)</h2><p class='muted'>The brief fixed the five directions, not the criteria. I wrote these from Zach's note after the five were built and before any was scored; the weights are my reading of what he asked for (inferred, not stated by him), and the total is computed from this table, never typed.</p>")
    out.append("<table><tr><th>Criterion</th><th class='n'>Weight</th><th>Why, and what 1 / 3 / 5 look like</th></tr>")
    for _key, name, weight, why, anchors in CRITERIA:
        one, one_t, three, three_t, five, five_t = anchors
        out.append(f"<tr><td><b>{esc(name)}</b></td><td class='n'>{weight}%</td><td>{esc(why)}<div class='faint'>{one}: {esc(one_t)} · {three}: {esc(three_t)} · {five}: {esc(five_t)}</div></td></tr>")
    out.append("</table><h3 style='margin-top:18px'>Hard gates (pass / fail, a failure cannot win)</h3><ul>")
    for _key, name, how in GATES:
        out.append(f"<li><b>{esc(name)}</b> <span class='faint'>· {esc(how)}</span></li>")
    out.append("</ul>")

    out.append("<h2>The five, unranked</h2><p class='muted'>Each has a hero video of its main flow on the real page, then stills in light and dark. Order is by id, not by merit.</p>")
    for p in PROPOSALS:
        out.append(f"<section class='card' id='{p['id']}'><header><span class='tag'>{p['id']}</span><h3>{esc(p['name'])}</h3></header><p>{esc(p['thesis'])}</p>")
        out.append(f"<p class='muted'><b>Where the bigger line lives:</b> {esc(p['where'])} <span class='faint'>Prior art: {esc(p['prior'])}</span></p>")
        out.append(media_block(media, p))
        out.append("<details><summary>Decisions, best when, loses when, parts worth keeping</summary><div class='cols'><div><h3>Decisions</h3><ul>" + "".join(f"<li>{esc(d)}</li>" for d in p["decisions"]) + "</ul></div>"
                   f"<div><p><b>Best when</b> {esc(p['best'])}</p><p><b>Loses when</b> {esc(p['loses'])}</p><h3>Worth keeping in a splice</h3><ul>" + "".join(f"<li>{esc(k)}</li>" for k in p["keep"]) + "</ul></div></div></details></section>")

    out.append("<h2>The AI prune</h2><p class='muted'>Prototype behaviour and the test results above are what <i>is</i>; the scores, weights and the recommendation are my judgment of what <i>ought</i> to be chosen. Scores are 1-5 against the anchors above, each with the evidence that backs it and my confidence.</p>")
    out.append("<h3>Hard gates</h3><table><tr><th>Gate</th>" + "".join(f"<th>{p['id']}</th>" for p in PROPOSALS) + "</tr>")
    results = {"same": "pass", "read": "pass", "scroll": "pass", "warn": "pass", "panes": None}
    for key, name, _how in GATES:
        cells = []
        for p in PROPOSALS:
            if key == "panes":
                cells.append("<td class='pass'>pass</td>" if p["id"] in ("c3", "c4") else "<td class='na'>no panes</td>")
            else:
                cells.append(f"<td class='{'pass' if results[key] == 'pass' else ''}'>{results[key]}</td>")
        out.append(f"<tr><td>{esc(name)}</td>{''.join(cells)}</tr>")
    out.append("</table><h3 style='margin-top:20px'>Weighted scores</h3><table><tr><th>Criterion (weight)</th>" + "".join(f"<th>{p['id']} {esc(p['name'])}</th>" for p in PROPOSALS) + "</tr>")
    for key, name, weight, *_rest in CRITERIA:
        cells = "".join(f"<td><span class='score'>{p['scores'][key][0]}</span> <span class='conf'>{p['scores'][key][1]}</span><div class='faint'>{esc(p['scores'][key][2])}</div></td>" for p in PROPOSALS)
        out.append(f"<tr><td><b>{esc(name)}</b> ({weight}%)</td>{cells}</tr>")
    out.append("<tr><td><b>Total (0-100)</b></td>" + "".join(f"<td><span class='score'>{totals(p):.1f}</span><div class='bar'><i style='width:{totals(p):.0f}%'></i></div></td>" for p in PROPOSALS) + "</tr></table>")
    out.append("<h3 style='margin-top:20px'>Ranking and the decision hinge</h3><ol>" + "".join(f"<li><b>{p['id']} {esc(p['name'])}</b> {totals(p):.1f}</li>" for p in ranked) + "</ol>")
    out.append(f"<p><b>Provisional default (highest eligible):</b> {ranked[0]['id']} {esc(ranked[0]['name'])}, with {', '.join(p['id'] for p in ranked[1:] if top - totals(p) <= 3.0)} within 3 points: <b>fragile</b>. "
               f"<b>The hinge:</b> the weights. c3 passes c2 once \"several values at once\" is weighted {flips('many', 'c3', 'c2'):.0f}% instead of 10%, and c1 passes c2 once \"stays where I am\" is weighted {flips('place', 'c1', 'c2'):.0f}% instead of 22% (the other weights shrinking in proportion). "
               "None of the numbers is precise to a point. <b>My pick for the State section is c2</b> because it is the literal answer to \"the same page, not crazy big\"; c1 is the cheapest to add on top of it later (both read the same wrapper); c4 is the one to build only when the Visibility mode can say what goes on a board.</p>")

    out.append("<h2>Adopting it: one call</h2><p>The State section's rows (Name, current value, sparkline) wrap the sparkline they already draw. No listener, no state, no redraw call: the selected chart view does the rest, and with c0 selected the wrapper is a plain cell.</p>")
    out.append("<pre>const cell = IOCharts.wrap({ p: node.path, l: 'Memory', s: [[statusName, 'RAM Load Average']] }, sparklineSvgHtml);\n"
               "// p: entity path · l: the label the enlarged chart shows · s: [status name, measurement key, style?] per line the sparkline draws</pre>")
    out.append("<p class='muted'>Requirements on the row: the wrapper takes the sparkline's grid cell (c2's unfolded cell spans the row); the label should differ from a sibling row's that draws the same series (state is per wrapper). The contract is <code>docs/charts-seam.md</code>; <code>web/detail/d4.js</code> shows it in <code>charted()</code>. A new chart view is one file plus a line in <code>web/charts/index.json</code>.</p>")
    out.append("<h2>What was exercised, and the boundary</h2><ul>"
               "<li>The real page (<code>io_diagnostics/web</code>) in headless Chrome, on <code>cli.py serve --fixture F1 --plugins tests/fixtures/plugins-charts</code>: the Computer's statuses with deterministic sine series at 2 Hz and real limits (temperature crosses its warn limit every 150 s). The fixture plugin never touches hardware.</li>"
               "<li>Data: the existing <code>/api/v1/history</code> only. The one host addition is the <code>dashboard</code> setting for c4 (<code>io_diagnostics/settings.py</code>, <code>host.py</code>, <code>docs/api-v1.md</code>, <code>tests/test_history.py</code>).</li>"
               "<li>The page shell changed in three small places: a fifth Prototype select (<code>#chart-view</code>), <code>makeChartsCtx</code> in <code>app.js</code>, and <code>charted()</code> in <code>d4.js</code>.</li>"
               f"<li>Lane server and branch: <code>{esc(args.sha)}</code> on <code>lane/chart-enlarge</code>; {esc(args.lane_url)}</li></ul>")
    out.append("</main></body></html>")
    return "".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sha", default="(uncommitted)")
    parser.add_argument("--lane-url", default="")
    parser.add_argument("--gates", default="")
    args = parser.parse_args()
    Path(args.out).write_text(build(args))
    for p in sorted(PROPOSALS, key=totals, reverse=True):
        print(f"{p['id']} {p['name']:<22} {totals(p):.1f}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
