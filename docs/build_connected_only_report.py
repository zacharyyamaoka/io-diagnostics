"""The report of Connected only (2026-11-09): one self-contained HTML page (every image and both clips inline), built from its folder.

    python3 /home/bam/io-diagnostics/docs/build_connected_only_report.py /home/bam/io-diagnostics/reports/media/connected-only-2026-11-09

Reads hero.mp4 / hero.gif (the final code on the rack, after 272813EF's boards lost power), eight-boards.mp4 (earlier, all eight live),
the stills, and facts.json (the drive runs and the three-adapter soak); writes report.html.
"""

import base64
import json
import os
import sys

import cv2

FOLDER = sys.argv[1]
SHA = os.environ.get("REPORT_SHA", "7294594")
PLUGIN = "1c95d926, 6b467192, b656adea"


def data_uri(name: str, mime: str) -> str:
    with open(os.path.join(FOLDER, name), "rb") as handle:
        return f"data:{mime};base64,{base64.b64encode(handle.read()).decode()}"


def still(name: str, caption: str, width: int = 900, crop: tuple | None = None) -> str:
    picture = cv2.imread(os.path.join(FOLDER, name))
    if picture is None:
        raise SystemExit(f"no image {name}")
    if crop:
        x, y, w, h = crop
        picture = picture[y:y + h, x:x + w]
    picture = cv2.resize(picture, (width, round(picture.shape[0] * width / picture.shape[1])), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 72])
    if not ok:
        raise SystemExit(f"could not encode {name}")
    return (f'<figure><img alt="{caption}" src="data:image/jpeg;base64,{base64.b64encode(data.tobytes()).decode()}">'
            f"<figcaption>{caption}</figcaption></figure>")


def rows(lines) -> str:
    return "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in line) + "</tr>" for line in lines)


def items(lines) -> str:
    return "<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>"


with open(os.path.join(FOLDER, "facts.json"), encoding="utf-8") as handle:
    facts = json.load(handle)
soak = facts["soak"]
run2 = facts["run2"]

SPEED = [
    ("Click → every board that answers is in the tree (8 boards, 2 adapters)", f"{run2['seenAll']:.1f} s",
     "the scratch host on the rack, before 272813EF lost power; includes starting 3 presence processes and their first full sweep"),
    ("Click → remembered boards nobody hears are gone (CAN 1, CAN 15)", f"{run2['goneAll']:.2f} s", "instant: they were never heard this session"),
    ("A board goes dark → the next pass sees it", f"≤ {facts['unplug_gap_s']:.0f} s",
     f"real, on the rack: 272813EF's three boards lost power at {facts['transitions'][-1]['at'][:8]}; the pass before heard them, this one did not"),
    ("A board goes dark → hidden in Connected only", "≈ 4 s", "two passes that looked must miss it (one lost reply is not an unplug): tests/test_presence.py"),
    ("A board answers again → shown", "≤ 2 s", "the next pass; its not-heard line goes at once (tests/test_presence.py, tests/test_roster.py)"),
    ("An adapter is plugged in → its boards", "≈ 3 s", "udev shows the adapter at once (as before); its presence process starts at the next 0.5 s tick, first pass ≈ 2.5 s"),
    ("Before this, with the view off", "300 s", "the full read's period (and up to 15 min before a silent board went hollow)"),
]

COST = [(dev, f"{s['first_lock_s']:.2f} s", f"{s['lock_median']:.2f} s", f"{s['lock_max']:.2f} s", f"{s['phase_max'] * 1000:.0f} ms", str(s["passes"]))
        for dev, s in soak.items()]

DONE = [
    f"<b>The button</b>: plug icon in the Devices header (collapse · expand · View options · <b>plug</b> · eye), remembered per browser, the same on "
    "<code>/</code> (L1, L2, the command palette) and <code>/dx/</code>. <code>tests/test_connected_page.js</code> 13/13 on a fixture host: hides the "
    "stale board, keeps the live one, survives a reload, composes with the eye and Group by, beats only while on.",
    "<b>The filter</b> (<code>view/core.js keepConnected</code>): a row stays when its presence is heard or live and it is not an empty port, or when "
    "it holds such a row; a row that lost children rolls up only what it still draws. <code>tests/test_connected_only.js</code> 18/18 across Physical, "
    "Nested, Type, search and sort.",
    "<b>The host</b> (<code>POST /api/v1/presence</code>, docs/api-v1.md \"Presence\"): one query-only <code>presence</code> process per adapter while "
    "the view beats, stopped 10 s after; a pass that looked marks what it heard live and, after two misses, what it did not as not heard (with the true "
    "last-heard time); a blocked pass judges nothing; a pass never overwrites a full read's identity of the same board; a different board on an id "
    "replaces the row and the readings of the one before. <code>tests/test_presence.py</code> 13/13 with real follower processes.",
    "<b>The plugin</b> (bam_ws <code>moteus_servo presence</code>): discovery broadcast + addressed UUID reads + serial registers, inside "
    "<code>bus_gate</code> (port lock + rack lock <code>LOCK_SH</code>), transport opened and closed inside the gate each pass, nothing held between "
    "passes, passes on the wall clock's 2 s marks. 27 tests + the 151 existing plugin tests green.",
    "<b>On the rack</b>: 0607623A CAN 4, 11, 12, 13, 21 and 272813EF CAN 12, 13, 14 live with their serials (AB4AQE4wUAYgNDlF, AD0AVkswUBkgODhZ, "
    "AEkAQ04wUAcgNDlF), FDA81D29 none; then the real power-off of 272813EF's boards, seen by the next pass.",
    "<b>Found and fixed on the way</b>: a not-heard line outlived the board's return, so a board that answered again read \"stale · last heard 0 s ago\" "
    "until someone removed its row (now gone the moment it is heard; red first). And the first live run read \"last heard 20767 days ago\" for a "
    "restored row (now the ledger's time).",
    "<b>Suites</b>: Python 1501 passed, 1 failed (<code>test_host_plugins</code> asserts the repo path ends in <code>io-diagnostics</code>, which a "
    "worktree's does not; environmental), the page suites (tree variants 1164/1164, layouts 200/200, dx page 101/101, view core 132/132).",
]

DECIDED = [
    f"<b>Landed on <code>main</code> @ <code>{SHA}</code></b> (Preview runs it live). Undo: <code>git -C /home/bam/io-diagnostics revert {SHA}</code>.",
    f"<b>The plugin commits ride on the bam_ws lane branch the apps run from</b> (<code>claude/traj-tracking-sim-to-real-a3da60</code>, worktree "
    f"<code>traj-sim2real-rack</code>): {PLUGIN}. Not pushed, not merged to bam_ws main (that whole lane is unmerged). Undo: revert them there.",
    "<b>Polling runs only while the view is on and the page is in view</b>, not always: each pass holds the rack bus lock, and an experiment takes it "
    "<code>LOCK_NB</code> and refuses rather than waits (bam_rack_twin <code>hold_bus_lock</code>).",
    "<b>Discovery plus addressed asks</b>, not discovery alone: CAN 4 on 0607623A runs firmware 1.0.0 and does not answer the broadcast (it answers an "
    "addressed UUID read). The brief's \"0607623A live = 11, 12, 13, 21\" was the discovery-only view; CAN 4 is live.",
    "<b>A brand-new board shows CAN id, UUID and serial at once</b>, and the model and firmware the host remembers for that UUID; a board never seen "
    "before gets its model at the next full read (the model-number register reads 0 on every board).",
    "<b>Rig rows stay</b> as the container of what answers (they pad nothing in: a missing board was never a row).",
]

LEFT = [
    "<b>Root cause of one hang</b>: in a 20 s run, two of three presence processes stopped mid-pass in a futex wait still holding the rack lock, the "
    "port lock and the tty, and ignored SIGTERM. Not reproduced in 75 s × 2 adapters, 12 SIGTERM rounds or a 120 s × 3 soak. Bounded now: a "
    "faulthandler watchdog ends a pass after 6 s (and dumps its stack to the host's stderr tail), and the host kills a presence process silent for 10 s.",
    "<b>Lock cost while the view is on</b>: 0.32 s per 2 s on a bus whose boards all answer, 0.55 s on 272813EF (it asks its remembered, silent CAN 1 "
    "and 15 every pass). An experiment started while the view is open is refused about 1 time in 4 to 6. Next lever: ask silent ids every third pass.",
    "<b>Stale text elsewhere</b>: a stale board's tree line said \"last heard 2 s ago\" on Preview because host-derived stock checks count as heard "
    "(CAN 1 at 08:23). Not this change; worth its own fix.",
]


def diagram() -> str:
    return """<svg viewBox="0 0 900 230" role="img" aria-label="Page beats the host, the host runs one presence process per adapter, each pass goes through bus_gate to the bus and back as rows">
<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--ink)"/></marker></defs>
<g font-size="13" fill="var(--ink)" stroke="var(--ink)" stroke-width="1.2">
<rect x="10" y="20" width="170" height="70" rx="8" fill="var(--card)"/><text x="95" y="48" text-anchor="middle" stroke="none" font-weight="600">Page</text><text x="95" y="68" text-anchor="middle" stroke="none">plug on · in view</text>
<rect x="250" y="20" width="190" height="70" rx="8" fill="var(--card)"/><text x="345" y="48" text-anchor="middle" stroke="none" font-weight="600">Host</text><text x="345" y="68" text-anchor="middle" stroke="none">one presence per adapter</text>
<rect x="510" y="20" width="170" height="70" rx="8" fill="var(--card)"/><text x="595" y="44" text-anchor="middle" stroke="none" font-weight="600">moteus presence</text><text x="595" y="62" text-anchor="middle" stroke="none">every 2 s, on the mark</text><text x="595" y="80" text-anchor="middle" stroke="none">nothing held between</text>
<rect x="740" y="20" width="150" height="70" rx="8" fill="var(--card)"/><text x="815" y="44" text-anchor="middle" stroke="none" font-weight="600">bus_gate</text><text x="815" y="62" text-anchor="middle" stroke="none">port lock (EX)</text><text x="815" y="80" text-anchor="middle" stroke="none">rack lock (SH)</text>
<path d="M180 45H248" marker-end="url(#a)"/><text x="214" y="38" text-anchor="middle" stroke="none" font-size="11">POST /presence 3 s</text>
<path d="M440 45H508" marker-end="url(#a)"/><text x="474" y="38" text-anchor="middle" stroke="none" font-size="11">spawn</text>
<path d="M680 45H738" marker-end="url(#a)"/>
<rect x="740" y="140" width="150" height="70" rx="8" fill="var(--card)"/><text x="815" y="166" text-anchor="middle" stroke="none" font-weight="600">CAN bus</text><text x="815" y="186" text-anchor="middle" stroke="none">discovery 0.1 s</text><text x="815" y="202" text-anchor="middle" stroke="none">+ addressed UUID</text>
<path d="M815 90V138" marker-end="url(#a)"/>
<path d="M740 175H600" marker-end="url(#a)"/><text x="670" y="168" text-anchor="middle" stroke="none" font-size="11">PresencePass</text>
<rect x="420" y="140" width="180" height="70" rx="8" fill="var(--card)"/><text x="510" y="164" text-anchor="middle" stroke="none" font-weight="600">heard → live</text><text x="510" y="182" text-anchor="middle" stroke="none">2 misses → not heard</text><text x="510" y="200" text-anchor="middle" stroke="none">blocked → no verdict</text>
<path d="M420 175H190" marker-end="url(#a)"/><text x="305" y="168" text-anchor="middle" stroke="none" font-size="11">/state presence</text>
<rect x="10" y="140" width="180" height="70" rx="8" fill="var(--card)"/><text x="100" y="166" text-anchor="middle" stroke="none" font-weight="600">keepConnected</text><text x="100" y="186" text-anchor="middle" stroke="none">heard / live rows</text><text x="100" y="202" text-anchor="middle" stroke="none">+ the way to them</text>
</g></svg>"""


hero = data_uri("hero.mp4", "video/mp4")
gif = data_uri("hero.gif", "image/gif")
eight = data_uri("eight-boards.mp4", "video/mp4")
TREE = (50, 60, 280, 640)  # the Devices pane of a 1400 x 900 shot
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connected only</title>
<style>
:root{{--bg:#fbfbfa;--ink:#1f2328;--muted:#5d6670;--card:#fff;--line:#e3e5e8;--accent:#2f6f4f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181b;--ink:#e6e7e9;--muted:#9aa3ad;--card:#1f2226;--line:#30343a;--accent:#7fc29b}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Inter,system-ui,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:28px 16px 64px}}
h1{{font-size:26px;margin:0 0 6px}} h2{{font-size:18px;margin:36px 0 10px;padding-top:8px;border-top:1px solid var(--line)}}
.verdict{{font-size:17px;margin:8px 0 18px}} .muted{{color:var(--muted)}} code{{font:13px ui-monospace,Menlo,monospace;background:var(--card);border:1px solid var(--line);padding:0 4px;border-radius:4px}}
video,img{{max-width:100%;border:1px solid var(--line);border-radius:8px;display:block}} figure{{margin:0}} figcaption{{color:var(--muted);font-size:13px;margin:6px 2px 0}}
.pair{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}} .wide{{display:grid;grid-template-columns:1fr;gap:14px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}} th{{color:var(--muted);font-weight:600}}
ul{{padding-left:20px}} li{{margin:6px 0}} svg{{width:100%;height:auto}}
</style></head><body><main>
<h1>Connected only</h1>
<p class="verdict"><b>Plug in an fdcanusb and its boards show in about 3 s; a board that goes dark is hidden in about 4 s.</b> Click the plug in the Devices
header. On <code>main</code> @ <code>{SHA}</code> (Preview) and promoted to Stable; while the view is on, an experiment started at that moment can be refused (1 in 4 to 6).</p>

<h2>The real rack, final code</h2>
<video autoplay muted loop playsinline controls src="{hero}"></video>
<p class="muted">Off: the tree pads in what the model remembers (CAN 1, 15 and 272813EF's boards, which had just lost power). Click the plug: what was never
heard this session goes at once, and 0607623A's five boards come back live within seconds. 272813EF stays empty: its boards are dark (Stable and Preview say so too:
"nobody ACKs this adapter's frames").</p>
<details><summary class="muted">GIF fallback (the Devices pane)</summary><img alt="Connected only, the Devices pane" src="{gif}"></details>

<h2>Earlier the same morning: all eight live boards</h2>
<video muted loop playsinline controls src="{eight}"></video>
<p class="muted">Before 272813EF's boards lost power: all eight boards that answer were in the tree {run2['seenAll']:.1f} s after the click. (The button sat one place to
the right then; it now sits before the eye.)</p>
<div class="pair">
{still("live-off.png", "Off: every board the model remembers, heard or not", 420, TREE)}
{still("live-on-before-power-off.png", "On, before the power-off: 3 + 5 boards, nothing else", 420, TREE)}
{still("live-on.png", "On, now: 272813EF's boards are dark, so it has none", 420, TREE)}
</div>
<div class="wide">{still("new-board.png", "A board the model does not expect (CAN 13 on 272813EF): CAN id, serial, UUID from the pass; model and firmware the host remembered for that UUID")}</div>

<h2>How fast</h2>
<table><tr><th>What</th><th>Time</th><th>Evidence</th></tr>{rows(SPEED)}</table>

<h2>What it costs the rack</h2>
<p>Only while the view is on and the page is in view. Each pass holds the rack bus lock <code>LOCK_SH</code>; an experiment takes it <code>LOCK_EX | LOCK_NB</code> and
refuses rather than waits. Passes start on the wall clock's 2 s marks, so every adapter's lock time falls in the same window (120 s soak, three adapters):</p>
<table><tr><th>Adapter</th><th>First pass (full sweep) holds</th><th>Then, median</th><th>Then, max</th><th>Off the 2 s mark</th><th>Passes</th></tr>{rows(COST)}</table>

<h2>The seam</h2>
{diagram()}

<h2>Done and proved</h2>{items(DONE)}
<h2>Decided for you</h2>{items(DECIDED)}
<h2>Left</h2>{items(LEFT)}
<h2>Needs you</h2><p>Nothing.</p>
<h2>Not done, on purpose</h2>{items([
    "No simulated replug: resetting an fdcanusb needs root or a new udev rule, and it is a hardware action nobody asked for. The adapter-plug time is the "
    "same code path measured from a fresh process; the board unplug is the real one above.",
    "No audit: worth a <code>/llm-judge</code> on the host's presence absorber and the plugin's per-pass event loop (the unexplained hang).",
])}
</main></body></html>"""

with open(os.path.join(FOLDER, "report.html"), "w", encoding="utf-8") as handle:
    handle.write(page)
print(os.path.join(FOLDER, "report.html"), f"{os.path.getsize(os.path.join(FOLDER, 'report.html')) / 1e6:.2f} MB")
