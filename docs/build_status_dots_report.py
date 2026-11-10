"""The report of the status dots (2026-11-10): one self-contained HTML page (every image and the clip inline), built from the capture folder.

    python3 /home/bam/io-diagnostics/docs/build_status_dots_report.py /home/bam/io-diagnostics/reports/media/status-dots-2026-11-10 /home/bam/io-diagnostics/reports/status-dots-2026-11-10.html

Reads <label>-<state>-<row>.png (docs/record_status_dots.js, label before / after), after-pulse.mp4 / .gif, preview-tree.png (the real Preview
app, read only) and the two facts.json; writes the page.
"""

import base64
import html
import json
import os
import subprocess
import sys

import cv2

FOLDER, OUT = sys.argv[1], sys.argv[2]
SHA = os.environ.get("REPORT_SHA") or subprocess.run(["git", "-C", "/home/bam/io-diagnostics", "rev-parse", "--short", "HEAD"],
                                                      capture_output=True, text=True).stdout.strip()
TESTS = os.environ.get("REPORT_TESTS", "")


def data_uri(name: str, mime: str) -> str:
    with open(os.path.join(FOLDER, name), "rb") as handle:
        return f"data:{mime};base64,{base64.b64encode(handle.read()).decode()}"


def close_up(name: str, caption: str) -> str:
    """A row's close-up as captured (deviceScaleFactor 2), drawn at half its pixels so it is native."""

    picture = cv2.imread(os.path.join(FOLDER, name))
    height, width = picture.shape[:2]
    return (f'<figure><img alt="{html.escape(caption)}" width="{width // 2}" height="{height // 2}" src="{data_uri(name, "image/png")}">'
            f"<figcaption>{html.escape(caption)}</figcaption></figure>")


def facts(label: str) -> dict:
    with open(os.path.join(FOLDER, f"{label}-facts.json"), encoding="utf-8") as handle:
        return json.load(handle)


before, after = facts("before"), facts("after")
ROWS = [("adapter", "adapter (alive: a follower streams)"), ("board", "board CAN 1 (heard)"), ("board2", "board on the other adapter (stale)"),
        ("camera", "camera (heard)")]


def pair(state: str, heading: str, note: str) -> str:
    cells = ""
    for label, title in (("before", "Before"), ("after", "After")):
        cells += f"<div><h4>{title}</h4>" + "".join(close_up(f"{label}-{state}-{key}.png", words) for key, words in ROWS) + "</div>"
    return f"<h3>{heading}</h3><p class=muted>{note}</p><div class=pair>{cells}</div>"


def style_table() -> str:
    lines = []
    for state in ("idle", "running"):
        for key, words in ROWS:
            old, new = before["states"][state][key], after["states"][state][key]
            lines.append(f"<tr><td>{state}</td><td>{html.escape(words)}</td><td><code>{old['classes'].replace('t6-pres ', '')}</code> · animation <code>{old['animationName']}</code></td>"
                         f"<td><code>{new['classes'].replace('t6-pres ', '')}</code> · animation <code>{new['animationName']}</code>"
                         f"{' · ' + new['animationDuration'] if new['animationName'] != 'none' else ''}</td></tr>")
    return ("<table><tr><th>State</th><th>Row</th><th>Before (computed)</th><th>After (computed)</th></tr>" + "".join(lines) + "</table>")


def tree_jpeg() -> str:
    picture = cv2.imread(os.path.join(FOLDER, "preview-tree.png"))
    ok, data = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return f"data:image/jpeg;base64,{base64.b64encode(data.tobytes()).decode()}"


WORDS = [
    ("hollow ring", "stale: known, but not heard lately", "<code>presence.state</code> <code>stale</code> (the ring's tooltip says when: &quot;last heard 41 min ago&quot;)"),
    ("solid dot", "connected and live, a plugged-in port against an empty one", "<code>presence.state</code> <code>heard</code> or <code>live</code>, at every level (adapter, board, camera)"),
    ("slow pulse", "something is running on it now; added to either shape", "<code>activity</code> on the row, from real signals only (next table)"),
]
SIGNALS = [
    ("a utility or action", "an active run (queued, waiting or going) in the run registry", "the row, the CAN bus and the adapter above it; never the USB bus, port or host",
     "tests/test_dot_activity.py (a real run of the fixture plugin; a queued one)"),
    ("commanded from a console", "a console RobotDx launched (tview, realsense-viewer) whose process lives", "its adapter", "test_an_open_console_pulses_its_adapter_until_the_process_closes"),
    ("flashed, or held by an experiment", "the adapter's probe was blocked and a holder it watches is still alive (a real holder process in the test)", "its adapter",
     "test_a_port_held_by_a_tool_pulses_the_adapter_and_stops_when_it_lets_go"),
    ("brought up right after plug-in", "<code>Job.bringing_up</code>: the connection's first look (its on-connect probe, or a plugin's first read) is in flight", "its adapter",
     "the probe test, the first-read test, and a re-plug that probes again"),
    ("routine polling (must not count)", "the periodic diagnose, a follower's lines, a watch, a presence pass", "nothing", "test_a_plugin_without_a_probe_is_brought_up_by_its_first_read_only (a routine read with the same sleep: no pulse)"),
]
GAPS = [
    "<b>A tool the host did not launch and that does not hold the lock</b> gives no signal. A flash or tview started in a terminal shows only while the on-connect probe was blocked "
    "by it (the <code>held</code> cause); a tool that starts after the probe finished, on an adapter nobody is probing, is invisible until the host sees its holder. "
    "That signal exists only for plugins that declare a probe (moteus_servo does; a camera's held-by-realsense-viewer shows through the console cause only when RobotDx launched it).",
    "<b>The presence process's own blocked passes</b> (<code>BusLockHeld</code> with holders, Connected only) are not used: they exist only while a page asks, so the dot would "
    "depend on a toggle. The probe's holders are the stable signal.",
    "<b>A row with no dot cannot pulse</b>: a board that has not reported a check yet (presence null) has no dot to animate, so a run on it shows on its adapter and CAN bus only.",
]

page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Status dots</title>
<style>
:root{{--bg:#fbfbfa;--ink:#1f2328;--muted:#5d6670;--card:#fff;--line:#e3e5e8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181b;--ink:#e6e7e9;--muted:#9aa3ad;--card:#1f2226;--line:#30343a}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Inter,system-ui,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:28px 16px 64px}}
h1{{font-size:26px;margin:0 0 6px}} h2{{font-size:18px;margin:36px 0 10px;padding-top:8px;border-top:1px solid var(--line)}} h3{{font-size:15px;margin:22px 0 2px}} h4{{margin:10px 0 6px;font-size:13px;color:var(--muted);font-weight:600}}
.verdict{{font-size:17px;margin:8px 0 18px}} .muted{{color:var(--muted)}} code{{font:12.5px ui-monospace,Menlo,monospace;background:var(--card);border:1px solid var(--line);border-radius:4px;padding:0 4px}}
img,video{{border:1px solid var(--line);border-radius:6px;display:block;max-width:100%}} figure{{margin:0 0 10px}} figcaption{{color:var(--muted);font-size:12.5px;margin:3px 2px 0}}
.pair{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}} th{{color:var(--muted);font-weight:600;font-size:13px}}
ul{{padding-left:20px}} li{{margin:6px 0}} pre{{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;font-size:12.5px}}
</style></head><body><main>
<h1>Status dots: ring, dot, breathe</h1>
<p class="verdict"><b>One mapping now draws every row of the tree: a hollow ring is stale, a solid dot is connected, and a slow breathe means something is running on it.</b>
The adapter no longer pulses for being alive. On <code>main</code> @ <code>{SHA}</code> (Preview runs it live); Stable is not promoted (see the end).</p>

<h2>The rule</h2>
<table><tr><th>Mark</th><th>Means</th><th>Comes from</th></tr>{''.join(f'<tr><td><b>{a}</b></td><td>{b}</td><td>{c}</td></tr>' for a, b, c in WORDS)}</table>
<p class="muted">Zach, 2026-11-10: "like a plugged-in port versus an empty one" (the mapping's WHY comment, <code>web/status-dot.js</code>). One function, <code>IOStatusDot.visual(row)</code>,
is the only thing that turns a row into a dot, for every level; the tree (both pages) calls it and nothing else. Never amber or red: the dot is neutral, warnings stay on the right-hand shapes.
The pulse is a 2.8 s opacity breathe (1 to 0.4), no scale. With prefers-reduced-motion nothing animates and a busy dot gets a steady halo (a ring plus a dot).</p>

<h2>Before and after, on the real page</h2>
<p class="muted">The entity page's fixture host (stand-in drivers, no hardware), the same story on the commit before and on the change: the adapter's presence is <code>live</code> (a follower
streaming), the other adapter's board is stale, then Run calibration on CAN 1 and Identify on the camera start (real runs of a stand-in process that sleeps and refuses). Close-ups are native
pixels (captured at 2x, drawn at half size).</p>
{pair("idle", "Connected and idle", "Before, the alive adapter breathed (the left, mid-breath) while its boards sat still: that was the confusion. After, every connected row is the same solid dot and nothing moves; the stale board is the ring.")}
{pair("running", "Something running", "Before, a running utility changed nothing on screen. After, CAN 1, its CAN bus and its adapter, and the camera breathe (caught at the dimmest of the breathe); the stale board stays a still ring.")}
<h3>prefers-reduced-motion</h3>
<p class="muted">No animation: a busy dot is the dot with a steady halo.</p>
<div class="pair">{close_up("after-reduced-adapter.png", "adapter")}{close_up("after-reduced-board.png", "board")}{close_up("after-reduced-camera.png", "camera")}</div>

<h2>The pulse, 7 s of the real page</h2>
<video controls muted loop autoplay playsinline src="{data_uri('after-pulse.mp4', 'video/mp4')}"><img alt="the pulsing rows" src="{data_uri('after-pulse.gif', 'image/gif')}"></video>
<p class="muted">Frames taken from the page at native resolution while the two utilities ran: the adapter, CAN 1 and the camera breathe together; the stale ring and the idle adapter below do not.
{after['clip']['frames']} frames over 7 s. If the clip does not play, the GIF inside it does.</p>
{style_table()}

<h2>The real app, idle</h2>
<p>Preview (this checkout, on the rack's real adapters), read only, nothing sent to any bus: the computer and the adapters are solid dots, every stale board is a hollow ring, and no dot has an
animation (computed <code>animation-name</code> <code>none</code> on all 20 rows). Before, the computer's dot breathed all day.</p>
<img alt="Preview's tree" width="440" src="{tree_jpeg()}">

<h2>Where "active" comes from</h2>
<table><tr><th>Zach's case</th><th>The real signal</th><th>Marks</th><th>Proved by</th></tr>{''.join(f'<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>' for a, b, c, d in SIGNALS)}</table>
<p>Where there was no real signal:</p><ul>{''.join(f'<li>{gap}</li>' for gap in GAPS)}</ul>

<h2>Tests</h2>
<pre>{html.escape(TESTS)}</pre>

<h2>Decided for you, left, needs you</h2>
<ul>
<li><b>Decided:</b> <code>presence</code> keeps its three states (the API and the Connected filter use them); the pulse is a new row field, <code>activity</code>, so "alive" and "busy" cannot be confused again. Undo: <code>git -C /home/bam/io-diagnostics revert {SHA}</code>.</li>
<li><b>Decided:</b> the CAN bus row pulses with its board's run (Zach: "on it or on its bus"); the USB bus, port and computer never do.</li>
<li><b>Decided:</b> <code>tests/test_roster_page.js</code> now says <code>solid</code> / <code>hollow</code> (the same two facts, the new words). Its Identify step already timed out before this change (same on the parent commit).</li>
<li><b>Not done:</b> Stable is not promoted. The docs list <code>sps promote</code> but nowhere say an agent promotes verified work, and it restarts the tray Zach is using. When you want it: <code>sps --root /home/bam/io-diagnostics promote</code>; undo with <code>rollback</code>.</li>
<li><b>Left:</b> the gaps above (a flash started outside RobotDx after the probe; a camera held by a tool RobotDx did not launch).</li>
</ul>
</main></body></html>"""

with open(OUT, "w", encoding="utf-8") as handle:
    handle.write(page)
print(f"{OUT}: {os.path.getsize(OUT) / 1024:.0f} KB")
