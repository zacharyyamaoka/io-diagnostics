"""The report of RobotDx's 2026-11-10 round (Zach's feedback on the wrist adapter, cameras, the tree menu and the Computer): one self-contained HTML
page, every image and the clip inline, built from its folder.

    python3 /home/bam/io-diagnostics/docs/build_robotdx_round2_report.py /home/bam/io-diagnostics/reports/media/robotdx-round2-2026-11-10

Reads hero.mp4 / hero.gif (Stable, read only) and the stills; writes report.html.
"""

import base64
import os
import sys

import cv2

FOLDER = sys.argv[1]
SHA = os.environ.get("REPORT_SHA", "6172da0")


def data_uri(name: str, mime: str) -> str:
    with open(os.path.join(FOLDER, name), "rb") as handle:
        return f"data:{mime};base64,{base64.b64encode(handle.read()).decode()}"


def still(name: str, caption: str, width: int = 900) -> str:
    picture = cv2.imread(os.path.join(FOLDER, name))
    if picture is None:
        raise SystemExit(f"no image {name}")
    if picture.shape[1] > width:
        picture = cv2.resize(picture, (width, round(picture.shape[0] * width / picture.shape[1])), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 74])
    if not ok:
        raise SystemExit(f"could not encode {name}")
    return f'<figure><img alt="{caption}" src="data:image/jpeg;base64,{base64.b64encode(data.tobytes()).decode()}"><figcaption>{caption}</figcaption></figure>'


def items(lines) -> str:
    return "<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>"


ASKED = [
    ("Boards in about 1 s after the port shows up", "A new connection's first look is now a quick presence pass (discovery + the known ids), whether or not a page "
     "is open; the slow full read (config reads, 40-90 s) follows. The first pass lands 0.5-0.8 s after the process starts (was 4.2 s: 2.2 s went to "
     "scanning every process's open files with realpath, 1.9 s to a full id sweep). After Preview's restart all six live boards were heard by the first poll."),
    ("The errors on the wrist adapter", "Port 1-3 dropped off USB twice in 30 s (\"3 enumerations in the last hour (flapping)\"); the read running then died "
     "with \"SerialException … device disconnected\" and was confirmed as a red PluginCrashed fault. A run whose device left under it now says "
     "\"it dropped off USB during this probe\" (unknown, no fault). The flapping itself is the cable or port: worth a look."),
    ("Why depth shows on the D405", "The D405 gives three streams (Color from one imager through its processor, Depth from the two, Infrared one imager's raw "
     "picture). On its USB 2 link the watch fell back to colour only and the page waited for the other two for ever. It now tries any shape the link "
     "accepts first: on Preview it streams all three, 848×480. A stream the camera does not give says \"not streaming · why\"."),
    ("Does the bus lock make sense for the camera?", "Yes, as a rule of the rig: fivebar_run opens the D435 mid-run, and the RealSense SDK opens every "
     "camera to list them, so a RobotDx look during an experiment could make the experiment's own open fail. What was wrong was the wording; it now "
     "names the program (\"fivebar_run has the rack bus lock\")."),
    ("Opening the pictures is buggy", "The browser keeps 6 connections per host, shared by every tab; each camera page held one per live tile, so the "
     "enlarged picture's stream could queue behind them and draw nothing. It now shows the newest frame at once, the tiles step down to single frames "
     "while it is open, and the box has the stream's shape. A test reproduces the blank lightbox (two tabs) on the old code."),
    ("USB 2.1 / 3.2 at a glance", "A badge right after a camera's name, before the grey text; its tooltip keeps the raw link. The name gives way in a narrow pane."),
    ("Hide the expected ones; right-click; Remove", "\"Only what's connected\" is now in View options too (the same setting as the plug button; your "
     "window had the older page: a reload shows the plug). Right-click a row opens the ⋮ menu at the mouse. Remove sits next to Hide, one click, no "
     "confirm; the row comes back as soon as the board reports again."),
    ("CAN bus \"no update for 1638 s\" over boards heard 1 s ago; empty dots", "A row's dot now follows the driver's own newest evidence: a board a "
     "presence pass heard a moment ago is solid even while its 5-minute full read is old (an experiment held the bus), and a bus is heard when its "
     "boards are. Checks the host recomputes every beat no longer count as hearing (they made CAN 1, silent 41 min, read \"last heard 2 s ago\")."),
    ("gigabyte_wmi \"critical temperature\"", "temp5 is probably the VRM MOS (it tracks CPU load, not the GPU; temp3 matches the CPU's own Tctl, r 0.99; "
     "lm-sensors' Gigabyte configs). 95 °C was RobotDx's own default, not the sensor's; Gigabyte publishes no limit, so it now reads \"no limit known\". "
     "The heat is real: 97-100 °C under this load, on a board with no VRM heatsink."),
    ("Fans; GPU detail; grouped by device; sources", "The GPU is read through NVML (NVIDIA's own library): fans as a share of their maximum, power vs its "
     "320 W limit, clocks and why they are held back, and its own limits (90 °C max operating, 96 °C slowdown, 101 °C shutdown, fans aim for 84 °C), "
     "linked to NVIDIA's spec page. The board's fan controller (IT8689E) has no Linux driver in kernel 6.8, which the Fans row says. The Computer's "
     "checks fold into CPU, RAM, Disks, GPU 0, Board, Network; an opened check lists where each limit comes from, with a link."),
]

DECIDED = [
    "<b>Landed on <code>main</code> @ <code>6172da0</code></b> and <b>promoted to Stable</b>. Undo: Roll Stable Back (the dock icon's right-click) returns "
    "to this morning's build; <code>git -C /home/bam/io-diagnostics revert</code> the commits 71bba12, 99e6cf3, b038e8c, 6172da0.",
    "<b>bam_ws plugin commits</b> on the lane branch the apps run from (<code>claude/traj-tracking-sim-to-real-a3da60</code>): 6f1412f3 (fast first look, "
    "faster holder scan), fb07a37a (camera streams). Not pushed.",
    "<b>Grouping by name folders, not part entities</b>: statuses on part paths would leave the Computer's own list; the page already folds \"Device: check\" names.",
    "<b>A changed driver restarts its followers</b> (found verifying: Preview's Computer ran 4.4-hour-old code).",
    "<b>No VRM warning</b>: no published number exists; the honest signal would be the CPU clock dropping under load.",
]

LEFT = [
    "The camera tiles say \"waiting for the first frame\" while the watch is paused (another process holds the camera); they could say paused.",
    "Two RobotDx tabs side by side: the enlarged picture shows the newest frame, updating about once a second instead of live.",
    "Board fans need a newer kernel or the out-of-tree it87 driver (root): not installed.",
    "MotorBridge research (vault note below): its main lesson is one process that owns each adapter's port instead of every job opening it; that would "
    "end most \"paused\" states. A design change worth its own session.",
    "test_host_plugins asserts the repo path ends in io-diagnostics (fails in any worktree); test_entity_page's camera-tile fps check is flaky under load "
    "(fails the same on the commit before this round).",
]

hero = data_uri("hero.mp4", "video/mp4")
gif = data_uri("hero.gif", "image/gif")
rows = "".join(f"<tr><td><b>{ask}</b></td><td>{answer}</td></tr>" for ask, answer in ASKED)
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RobotDx round 2</title>
<style>
:root{{--bg:#fbfbfa;--ink:#1f2328;--muted:#5d6670;--card:#fff;--line:#e3e5e8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181b;--ink:#e6e7e9;--muted:#9aa3ad;--card:#1f2226;--line:#30343a}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Inter,system-ui,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:28px 16px 64px}} h1{{font-size:26px;margin:0 0 6px}}
h2{{font-size:18px;margin:34px 0 10px;padding-top:8px;border-top:1px solid var(--line)}} .muted{{color:var(--muted)}}
code{{font:13px ui-monospace,Menlo,monospace;background:var(--card);border:1px solid var(--line);padding:0 4px;border-radius:4px}}
video,img{{max-width:100%;border:1px solid var(--line);border-radius:8px;display:block}} figure{{margin:0}} figcaption{{color:var(--muted);font-size:13px;margin:6px 2px 0}}
.pair{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}} .wide{{display:grid;gap:14px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td{{border-bottom:1px solid var(--line);padding:8px;vertical-align:top}} td:first-child{{width:26%}}
ul{{padding-left:20px}} li{{margin:6px 0}}
</style></head><body><main>
<h1>RobotDx round 2</h1>
<p><b>Your 2026-11-10 list is done and on Stable (<code>{SHA}</code>).</b> The wrist adapter's errors were its port dropping off USB (a cable or port to check),
and the Computer's "critical" VRM temperature was RobotDx's own default, though the VRM really runs at 97-100 °C under this load.</p>
<video autoplay muted loop playsinline controls src="{hero}"></video>
<p class="muted">Stable, read only: the tree with the USB badges; a right-click on CAN 17, a board the wrist adapter no longer has, opens the row menu at
the mouse; the D405, paused because another process held the camera at that moment.</p>
<details><summary class="muted">GIF fallback</summary><img alt="RobotDx round 2" src="{gif}"></details>

<h2>What you asked, what changed</h2>
<table>{rows}</table>

<h2>The tree</h2>
<div class="pair">{still("tree.png", "Physical grouping: live boards solid, the wrist's old ids hollow with their true last-heard times; USB badges on the cameras", 520)}
{still("rightclick.png", "Right-click: the ⋮ menu at the mouse", 520)}</div>
<div class="wide">{still("badge-widths.png", "The badge in a narrow and a wide pane: the name gives way, the badge stays")}</div>

<h2>The cameras</h2>
<div class="wide">{still("d405-state.png", "Preview: the D405 on its USB 2 link streams Color, Depth and Infrared at 848×480")}</div>

<h2>The Computer</h2>
<div class="wide">{still("computer-grouped.png", "Grouped by device; gigabyte_wmi temp5 reads OK, no limit known")}
{still("computer-gpu.png", "An opened GPU check: its own limits through NVML, and NVIDIA's spec page")}
{still("computer-gigabyte.png", "An opened gigabyte_wmi check: what temp5 probably is, and why there is no limit")}</div>

<h2>Decided for you</h2>{items(DECIDED)}
<h2>Left</h2>{items(LEFT)}
<h2>Needs you</h2><p>Nothing that blocks. The flapping port 1-3 and the VRM heat are yours to look at on the bench.</p>
</main></body></html>"""
with open(os.path.join(FOLDER, "report.html"), "w", encoding="utf-8") as handle:
    handle.write(page)
print(os.path.join(FOLDER, "report.html"), f"{os.path.getsize(os.path.join(FOLDER, 'report.html')) / 1e6:.2f} MB")
