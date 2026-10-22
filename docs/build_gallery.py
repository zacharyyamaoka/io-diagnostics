"""Build the babble gallery: docs/criteria.json (+ amendment A2) -> spec -> skill's gallery.py.

    /usr/bin/python3 /home/bam/io-diagnostics/docs/build_gallery.py

Writes out/gallery-spec.json and reports/io-diagnostics-babble-2026-10-21.html. Exhibits are
real: sandbox renders (out/native via docs/crops.py), CLI/registry/drift output produced by
running the prototypes now, and binding costs counted from the files.
"""

from __future__ import annotations

import base64
import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docs"))
sys.path.insert(0, str(ROOT / "prototypes" / "v5-station"))
sys.path.insert(0, str(ROOT / "prototypes" / "v4-registry"))

import heroes  # noqa: E402
from io_facts import journal  # noqa: E402

E = html.escape
GALLERY = Path("/home/bam/.claude-bam/skills/babble/scripts/gallery.py")
OUT = ROOT / "out"
REPORT = ROOT / "reports" / "io-diagnostics-babble-2026-10-21.html"
CROPS = OUT / "crops"


def img(name: str, alt: str) -> str:
    data = base64.b64encode((CROPS / f"{name}.webp").read_bytes()).decode()
    return f'<img alt="{E(alt)}" src="data:image/webp;base64,{data}" style="border-radius:6px">'


def pre(text: str) -> str:
    return (f'<pre style="margin:0;padding:10px 12px;background:#141416;color:#d6e8d0;border-radius:6px;'
            f'font:11px/1.45 ui-monospace,Menlo,monospace;white-space:pre-wrap;max-width:420px">{E(text)}</pre>')


def lines(path: Path) -> int:
    return sum(1 for line in path.read_text().splitlines() if line.strip())


def cost_table(rows: list[tuple[str, str]], verdict: str) -> str:
    body = "".join(f'<tr><td style="padding:4px 10px 4px 0;color:#57534e">{E(a)}</td><td style="padding:4px 0">{E(b)}</td></tr>' for a, b in rows)
    return (f'<div style="font:12.5px/1.45 Inter,system-ui,sans-serif;color:#20201f;max-width:430px"><table style="border-collapse:collapse">{body}</table>'
            f'<div style="margin-top:6px;font-weight:600">{E(verdict)}</div></div>')


# ------------------------------------------------------------------ criteria with amendment A2
def criteria() -> tuple[list, list, dict]:
    frozen = json.loads((ROOT / "docs" / "criteria.json").read_text())
    a2 = next(amendment for amendment in frozen["amendments"] if amendment["id"] == "A2")
    requirements = []
    for requirement in frozen["requirements"]:
        change = a2["changes"].get(requirement["id"])
        if change:
            requirement = {**requirement, "name": f"{change['name']} (A2)", "passCondition": change["passCondition"], "anchors": change["anchors"],
                           "why": requirement["why"] + " A2, Zach's evening direction: the plugin is the device's own driver/SDK package; scope is identity, versions, update, config view, reset and comms; no motion."}
        requirements.append(requirement)
    gates = []
    for gate in frozen["hardGates"]:
        if gate["id"] == "g2":
            gate = {**gate, "why": gate["why"] + " A2: " + a2["changes"]["g2"]["addition"]}
        gates.append(gate)
    return requirements, gates, frozen


# ------------------------------------------------------------------ real exhibits
def v2_replay() -> str:
    log = (ROOT / "tests" / "data" / "kernel-usb-2026-10-21.log").read_text().splitlines()
    events = journal.parse_kernel_log(log)
    notices = [notice for notice in journal.replay(events) if notice.time.hour >= 18]
    shown = "\n".join(f"{notice.time:%H:%M:%S}  {notice.kind:<9} {notice.text()}" for notice in notices)
    flaps = sum(1 for event in events if event.serial == "28B3A25D" and event.kind == "add")
    return pre(f"journal.replay(today's real kernel log)\n{len(events)} USB events -> {len(journal.replay(events))} notices\n"
               f"28B3A25D: {flaps} enumerations -> 1 'plugged' notice\n\n{shown}")


def v3_cli() -> str:
    outputs = []
    for executable, port in (("moteus_servo-diagnostics", "1-3"), ("obsbot_ptz_camera-diagnostics", "7-1.2")):
        completed = subprocess.run([str(ROOT / "prototypes" / "v3-hub-row" / "bin" / executable), "diagnose", "--port", port, "--fixture"],
                                   capture_output=True, text=True, timeout=30)
        report = json.loads(completed.stdout)
        sections = {name: {key: value["value"] for key, value in values.items() if key != "_gate"} for name, values in report["sections"].items()}
        brief = {"driver": report["driver"], "gate": report["gate"], "identity": sections.get("identity"), "versions": {k: v for k, v in list(sections.get("versions", {}).items())[:3]}}
        outputs.append(f"$ {executable} diagnose --port {port}\nexit {completed.returncode}   (0 OK · 1 WARNING · 2 CRITICAL · 3 UNKNOWN)\n"
                       + json.dumps(brief, indent=1, default=str, ensure_ascii=False)[:520])
    return pre("\n\n".join(outputs))


def v4_contract() -> str:
    import consumer_example  # noqa: PLC0415
    import registryd  # noqa: PLC0415

    text = []
    for frame in ("F1", "F2", "F3"):
        document = registryd.document(frame)
        result = consumer_example.resolve(document, "272813EF")
        text.append(f"{heroes.CLOCK[frame]}  resolve(registry, '272813EF') -> {json.dumps(result, ensure_ascii=False)}")
    record = next(device for device in registryd.document("F2")["devices"] if device["key"] == "272813EF")
    excerpt = {key: record[key] for key in ("key", "driver", "headline", "gate", "gate_reason", "by_id")}
    return pre("io-diagnostics/1 record (18:25)\n" + json.dumps(excerpt, indent=1, ensure_ascii=False) + "\n\n" + "\n".join(text))


def v5_station() -> str:
    import drift  # noqa: PLC0415

    station = (ROOT / "prototypes" / "v5-station" / "station.toml").read_text().split("\n\n")
    first, obsbot = station[1], station[-1]
    output = "\n".join(drift.drift("F3")[1])
    return pre(f"station.toml (excerpt)\n{first}\n...\n{obsbot.strip()}\n\n$ drift.py --frame F3\n{output}")


def costs() -> dict[str, str]:
    driver = ROOT / "drivers_proposed" / "obsbot_ptz_camera" / "src" / "obsbot_ptz_camera"
    diag = lines(driver / "diagnostics.py")
    manifest = lines(driver / "io-diagnostics.json")
    shared = ("in the driver", f"obsbot_ptz_camera/diagnostics.py · {diag} lines here (~130-160 real, per the driver's own seams)")
    host = ("host / UI edits", "0 files (tests/test_bindings.py: unrecognised without the unit, diagnosed with it)")
    return {
        "v1": cost_table([shared, ("binding unit", f"io-diagnostics.json · {manifest} lines (USB matcher + entry), shipped as package data"), host],
                         "Two files in the driver; the shell only ever reads the JSON."),
        "v2": cost_table([shared, ("binding unit", "none: the file name is the contract (<pkg>/diagnostics.py defines DIAGNOSTICS)"), host],
                         "One file in the driver, nothing else."),
        "v3": cost_table([shared, ("binding unit", "a 2-line main() + 1 [project.scripts] line + an install"), ("any language", "a vendor CLI joins through a 10-line wrapper named *-diagnostics"), host],
                         "One file + one pyproject line; works for non-Python vendor tools."),
        "v4": cost_table([shared, ("binding unit", "2 [project.entry-points] lines + install into the registry's environment"), ("coupling", "the registry env must hold every driver's deps (moteus, pyrealsense2, opencv)"), host],
                         "One file + two pyproject lines + an install."),
        "v5": cost_table([shared, ("binding unit", "a 4-line [[slot]] in station.toml per physical device"), ("ad-hoc plug", "undeclared devices show as 'unassigned' until a slot names them"), host],
                         "One file per type, plus one slot per device."),
    }


# ------------------------------------------------------------------ variants
def variants(data, drift_lines) -> list[dict]:
    cost = costs()
    real_v1 = img("v1-quick", "Real GNOME Shell 46 Quick Settings with the Rack IO toggle open")
    story = {variant: {"title": "Walk the shared fixture", "steps": [{"label": label, "caption": caption, "state": state, "target": target}
                                                                     for state, label, caption, target in heroes.STORY[variant]]} for variant in heroes.STORY}
    return [
        {
            "id": "v1", "name": "Quick Settings device list", "accent": "#e05a3f",
            "thesis": "Rack IO becomes a native GNOME Quick Settings toggle with a Bluetooth-style device list; the panel shows a glyph only when something needs you.",
            "bestWhen": "You want it to feel like part of Ubuntu, reached from the same corner as Wi-Fi and Bluetooth, and the menu only needs a few words per device.",
            "losesWhen": "GNOME upgrades: a shell extension is pinned to shell-version 46 and must be enabled; the narrow menu cannot show sources and ages.",
            "decisions": [
                {"label": "Surface", "value": "QuickMenuToggle + SystemIndicator (GNOME 46 API), rows like the Bluetooth device list."},
                {"label": "Driver binding", "value": "io-diagnostics.json manifest inside each driver package; the shell reads data only."},
                {"label": "Process boundary", "value": "helper.py runs every driver's diagnostics out of gnome-shell, so a hung driver cannot freeze the top bar."},
            ],
            "keepParts": ["Rack IO toggle with 'N need you' subtitle", "glyph in the system area only on attention", "data-only manifest matching in-shell"],
            "proof": ["Real render: the extension running in a throwaway GNOME Shell 46 (Ubuntu mode, Yaru-dark), drivers' real passive reads.",
                      "test_bindings: the OBSBOT is unrecognised without its manifest and diagnosed with it."],
            "previewLabel": "interactive · 5 states", "story": story["v1"], "preview": heroes.v1(data),
            "media": [
                {"label": "Real render · GNOME Shell 46", "caption": "The actual extension in a sandboxed shell: Rack IO beside Wired and Wi-Fi, 272813EF expanded; firmware 1.1.2 is the real flash-tool record.", "html": real_v1},
                {"label": "Real render · banner", "caption": "Main.notify in the same shell: one System banner, no window, no focus change.", "html": img("v1-banner", "Real GNOME banner from the extension")},
                {"label": "Cost to add the OBSBOT", "caption": "What a new device type needs in this direction, counted from the files.", "html": cost["v1"]},
            ],
            "scores": {
                "fr1": {"score": 4, "evidence": "Real GNOME banner rendered from the extension; one notice per settled plug via the shared coalescer, but the in-shell watcher (inotify on /dev/serial/by-id) is designed, not built.", "confidence": "medium"},
                "fr2": {"score": 4, "evidence": "Driver's diagnostics.py + a 6-line manifest; zero host/UI edits (tested). The manifest repeats the USB matcher already in diagnostics.py.", "confidence": "high"},
                "fr3": {"score": 5, "evidence": "Native toggle subtitle '3 need you' and a glyph only on attention; detail is one expand away inside the same menu (real render).", "confidence": "medium"},
                "fr4": {"score": 3, "evidence": "Values and fault lines read in full (wrapped), but the 356 px menu has no room for sources and ages; blocked shows who.", "confidence": "high"},
                "fr5": {"score": 2, "evidence": "Serial-keyed rows and the /dev/fdcanusb trap appear, but there is no copyable path and no machine-readable output.", "confidence": "high"},
                "fr6": {"score": 2, "evidence": "A shell extension: pinned to shell-version 46, must be enabled, JS in gnome-shell (mitigated by data-only rendering).", "confidence": "high"},
            },
            "gateResults": {
                "g1": {"pass": True, "evidence": "Main.notify banner and a panel glyph; nothing raised or activated (real render)."},
                "g2": {"pass": True, "evidence": "Every touching call goes through contract.diagnose / request_reset (holders + passive lock read); reset is inline-confirmed and off this round."},
                "g3": {"pass": True, "evidence": "Enabled only in a scratch keyfile inside the Xvfb sandbox; sandbox.sh checks Zach's dconf mtime unchanged (PASS)."},
            },
        },
        {
            "id": "v2", "name": "Plug Journal tray", "accent": "#2f7f74",
            "thesis": "A small standalone tray leads with what just changed: one coalesced banner per plug, a Recent list, then a submenu per device from its driver.",
            "bestWhen": "The moment of plugging is what matters and you want the fewest moving parts: one Python process, stock AppIndicator and libnotify.",
            "losesWhen": "You want a styled glance: DBusMenu renders text rows only, info rows look greyed, and long lines run wide.",
            "decisions": [
                {"label": "Surface", "value": "Ayatana AppIndicator (stock ubuntu-appindicators) + libnotify banners, no server."},
                {"label": "Driver binding", "value": "Naming convention: any <pkg>/diagnostics.py that defines DIAGNOSTICS is found; no metadata."},
                {"label": "Events", "value": "udevadm monitor -> the same Coalescer that turns today's 5 enumerations of 28B3A25D into one notice."},
            ],
            "keepParts": ["coalesced plug notices (flaps folded in)", "Recent section first", "one gated check per newly settled adapter"],
            "proof": ["Real render: the tray and its libnotify banner in the sandboxed GNOME Shell 46.",
                      "test_facts: today's real kernel log replays to one notice for 4 flaps and one for the D405's 4 s drop."],
            "previewLabel": "interactive · 5 states", "story": story["v2"], "preview": heroes.v2(data),
            "media": [
                {"label": "Real render · tray menu", "caption": "The actual AppIndicator menu: Recent first, 272813EF expanded. Text only: DBusMenu cannot colour or lay out rows.", "html": img("v2-menu", "Real AppIndicator menu in GNOME Shell 46")},
                {"label": "Real render · banner", "caption": "The libnotify banner, sourced 'IO diagnostics'; no window, no focus change.", "html": img("v2-banner", "Real libnotify banner")},
                {"label": "Coalescer on today's log", "caption": "Real output: the live coalescer replayed over today's kernel log.", "html": v2_replay()},
                {"label": "Cost to add the OBSBOT", "caption": "What a new device type needs in this direction.", "html": cost["v2"]},
            ],
            "scores": {
                "fr1": {"score": 5, "evidence": "Real banner + live udev path into the coalescer proven on today's log (5 enumerations -> 1 notice; 4 s drop -> 1 'dropped'); a live plug was not observed this session.", "confidence": "medium"},
                "fr2": {"score": 5, "evidence": "One file in the driver, no metadata; test drops the file and the OBSBOT turns unrecognised, restores it and it is diagnosed.", "confidence": "high"},
                "fr3": {"score": 3, "evidence": "Glyph + count is small and calm, but the real menu shows every row as dim text with marks; no visual hierarchy beyond order.", "confidence": "high"},
                "fr4": {"score": 3, "evidence": "Ids, firmware, bus counters and the blocking holder are listed, but sources and ages do not fit a menu row.", "confidence": "high"},
                "fr5": {"score": 3, "evidence": "'Copy by-id path' and the /dev/fdcanusb trap row; no machine-readable output.", "confidence": "high"},
                "fr6": {"score": 5, "evidence": "One small system-python process on stock AppIndicator + libnotify; no extension, no daemon, no server.", "confidence": "high"},
            },
            "gateResults": {
                "g1": {"pass": True, "evidence": "Banner + tray only; the reset confirm is a small dialog opened by his own click, never by an event."},
                "g2": {"pass": True, "evidence": "The one automatic check (after a new adapter settles) and reset both go through the shared gate; off this round."},
                "g3": {"pass": True, "evidence": "Runs from the repo; rendered only inside the sandbox."},
            },
        },
        {
            "id": "v3", "name": "Hub row", "accent": "#7667c6",
            "thesis": "No new top-bar item: Claude Hub's existing popover gains an IO section, one fixed-height row per device in the hub's own row grammar.",
            "bestWhen": "You want exactly one icon in the top bar for everything on this machine and already live in the hub popover.",
            "losesWhen": "IO and plan usage share one ring icon, so 'something needs you' is ambiguous; the hub polls only while its page is open.",
            "decisions": [
                {"label": "Surface", "value": "A section in /home/bam/claude-hub-tray's 560 px WebKit popover; the usage ring gets an amber dot."},
                {"label": "Driver binding", "value": "CLI convention: <pkg>-diagnostics executables, JSON + Monitoring Plugins exit codes, spawned with a timeout."},
                {"label": "Reset", "value": "Reuses the hub's Close-confirm grammar: pick, confirm, re-check holders and lock right before sending."},
            ],
            "keepParts": ["CLI + exit-code contract (any language)", "card with a source on every value", "Close-style confirm for reset"],
            "proof": ["Real output: the console-script stand-ins run now (exit codes below).",
                      "Hub popover drawn with the hub's row grammar; the hub itself was not modified (mock, not a hub build)."],
            "previewLabel": "interactive · 5 states", "story": story["v3"], "preview": heroes.v3(data),
            "media": [
                {"label": "Real CLI output", "caption": "Each driver's diagnostics as a process: JSON plus a Monitoring Plugins exit code; what the hub (Node) would spawn.", "html": v3_cli()},
                {"label": "Cost to add the OBSBOT", "caption": "What a new device type needs in this direction.", "html": cost["v3"]},
            ],
            "scores": {
                "fr1": {"score": 3, "evidence": "A banner is possible from the hub tray, but the hub polls only while its page is visible, so plug detection needs a new always-on watcher in server.js (not built).", "confidence": "medium"},
                "fr2": {"score": 4, "evidence": "diagnostics.py + a 2-line main + 1 pyproject line; language-agnostic (vendor CLIs via wrapper); tested discovery.", "confidence": "high"},
                "fr3": {"score": 3, "evidence": "No new icon, but the glance is a usage ring with an amber dot: is it usage or IO? Rows are calm one-liners.", "confidence": "medium"},
                "fr4": {"score": 4, "evidence": "Card shows every value with its source; blocked names tview; layers adapter/bus/node as rows.", "confidence": "medium"},
                "fr5": {"score": 4, "evidence": "Every driver is a CLI any script can run for JSON + exit code; no single shared snapshot.", "confidence": "high"},
                "fr6": {"score": 3, "evidence": "Reuses an existing product, but adds an API route, a watcher and Python spawns to a Node hub with request signing.", "confidence": "medium"},
            },
            "gateResults": {
                "g1": {"pass": True, "evidence": "Popover only on click (the hub's existing behaviour); banners from the tray process."},
                "g2": {"pass": True, "evidence": "The CLI's diagnose goes through contract.diagnose; reset re-checks the gate before sending; off this round."},
                "g3": {"pass": True, "evidence": "The hub repo is untouched; the section is a mock in the gallery."},
            },
        },
        {
            "id": "v4", "name": "Registry contract", "accent": "#b7791f",
            "thesis": "The product is one versioned registry file every tool reads; the top bar is a one-line label that opens a local dashboard over it.",
            "bestWhen": "Scripts and agents (the rack session runner first) should stop guessing /dev/fdcanusb and resolve hardware by serial from one shared truth.",
            "losesWhen": "You want a pop-up: the label ticker is easy to miss, and a daemon + schema + dashboard is the most to keep alive.",
            "decisions": [
                {"label": "Surface", "value": "AppIndicator text label 'IO 4 · 3 ⚠'; the menu opens a localhost dashboard (the only window, on click)."},
                {"label": "Driver binding", "value": "Packaging entry points, group io_diagnostics.drivers (stock importlib.metadata)."},
                {"label": "Contract", "value": "$XDG_RUNTIME_DIR/io-diagnostics.json (io-diagnostics/1), written by rename; resolve() for consumers."},
            ],
            "keepParts": ["versioned registry file", "resolve(serial) for the session runner", "value + source + age table"],
            "proof": ["Real render: the label in the sandboxed top bar.",
                      "Real output: registryd + consumer_example run now for all three frames."],
            "previewLabel": "interactive · 5 states", "story": story["v4"], "preview": heroes.v4(data),
            "media": [
                {"label": "Real render · label", "caption": "The AppIndicator label in GNOME Shell 46 (it only shows when set after registration: measured).", "html": img("v4-bar", "Real AppIndicator label") + "<div style='height:8px'></div>" + img("v4-menu", "Real label menu")},
                {"label": "Contract + consumer", "caption": "Real output: the registry record and what the session runner gets back at 18:11, 18:25 and 19:02.", "html": v4_contract()},
                {"label": "Cost to add the OBSBOT", "caption": "What a new device type needs in this direction.", "html": cost["v4"]},
            ],
            "scores": {
                "fr1": {"score": 3, "evidence": "No banner by design; the label reads 'IO + 28B3A25D · 0 of 4' for 10 s; events are in the dashboard.", "confidence": "medium"},
                "fr2": {"score": 4, "evidence": "diagnostics.py + 2 pyproject lines + an install; stock importlib.metadata discovery tested; one env must hold all drivers' deps.", "confidence": "high"},
                "fr3": {"score": 4, "evidence": "The always-visible count is calm and exact; detail lives in a separate window it opens (allowed, but one step heavier).", "confidence": "medium"},
                "fr4": {"score": 5, "evidence": "Dashboard table shows value, source and age per row; blocked names tview; events listed.", "confidence": "medium"},
                "fr5": {"score": 5, "evidence": "Versioned file + resolve(): the consumer refuses with 'busy: open by pid 640623 tview' and 'no board answers' (real run).", "confidence": "high"},
                "fr6": {"score": 2, "evidence": "Daemon, schema versioning, dashboard server and a label: the most always-on parts.", "confidence": "high"},
            },
            "gateResults": {
                "g1": {"pass": True, "evidence": "No banners; the dashboard opens only from his click through the focus-safe opener."},
                "g2": {"pass": True, "evidence": "Reset is a queued request the daemon runs only behind the gate; off this round."},
                "g3": {"pass": True, "evidence": "registryd writes into out/; nothing installed or started as a service."},
            },
        },
        {
            "id": "v5", "name": "Station board", "accent": "#3f6fa8",
            "thesis": "The rack is declared as slots (board-farm style); the top bar counts drift between expected and present, and the board shows each slot.",
            "bestWhen": "The rack is a station with a known build, and 'what differs from how it should be' is the question, before any experiment runs.",
            "losesWhen": "You plug things in ad hoc: an undeclared device is only 'unassigned' until a slot names it, and the file has to be kept true.",
            "decisions": [
                {"label": "Surface", "value": "AppIndicator label 'Rack · 3 drifts'; the menu lists only drift; 'Open station board' shows the slots."},
                {"label": "Driver binding", "value": "station.toml names the driver per slot (labgrid-style resource + driver); discovery still flags unassigned devices."},
                {"label": "Health", "value": "Scheduled golden check when the rack lock is free; postponed (and said so) while the bus is busy."},
            ],
            "keepParts": ["drift count as the glance", "expected-vs-present per slot", "station file the session runner can check against"],
            "proof": ["Real render: the drift badge and its menu in the sandboxed top bar.",
                      "Real output: drift.py for 18:11 / 18:25 / 19:02 from the same driver reports."],
            "previewLabel": "interactive · 5 states", "story": story["v5"], "preview": heroes.v5(data, drift_lines),
            "media": [
                {"label": "Real render · badge + drift menu", "caption": "The drift badge and menu in GNOME Shell 46 at 19:02.", "html": img("v5-bar", "Real drift badge") + "<div style='height:8px'></div>" + img("v5-menu", "Real drift menu")},
                {"label": "Station file + drift", "caption": "Real output: what drift.py prints at 19:02 from the station file and the drivers' reports.", "html": v5_station()},
                {"label": "Cost to add the OBSBOT", "caption": "What a new device type needs in this direction.", "html": cost["v5"]},
            ],
            "scores": {
                "fr1": {"score": 4, "evidence": "The banner says what is wrong against the station ('0 of 4 expected boards'); an undeclared plug is only 'unassigned'.", "confidence": "medium"},
                "fr2": {"score": 3, "evidence": "Same driver diagnostics.py, but every physical device also needs a 4-line slot before it means anything.", "confidence": "high"},
                "fr3": {"score": 5, "evidence": "'Rack · 3 drifts' answers 'what needs me' in the bar itself; the menu lists only drift (real render).", "confidence": "medium"},
                "fr4": {"score": 4, "evidence": "Expected vs present per slot plus the value/source table; golden-check status explained while blocked.", "confidence": "medium"},
                "fr5": {"score": 3, "evidence": "Slots are named in a file scripts can read; no versioned live snapshot.", "confidence": "medium"},
                "fr6": {"score": 3, "evidence": "Stock AppIndicator plus a station file, a scheduled check and a board page.", "confidence": "medium"},
            },
            "gateResults": {
                "g1": {"pass": True, "evidence": "Banner on drift change only; the board opens on his click."},
                "g2": {"pass": True, "evidence": "Golden checks take the lock only when it is free (passive /proc/locks first) and postpone otherwise; off this round."},
                "g3": {"pass": True, "evidence": "Runs from the repo; rendered only inside the sandbox."},
            },
        },
    ]


def main() -> int:
    requirements, gates, frozen = criteria()
    data = heroes.views_by_frame()
    import drift  # noqa: PLC0415

    drift_lines = {frame: drift.drift(frame)[1] for frame in ("F1", "F2", "F3")}
    spec = {
        "schemaVersion": 3,
        "title": "IO diagnostics: five top-bar directions",
        "kicker": "Babble & Prune · round 1 · N = 5 · 2026-10-21",
        "brief": ("Plug a sensor, actuator or bus adapter into the rack PC and see it pop up in the top bar: what it is, its ids, versions, "
                  "comms and health, before any robot code runs, without disturbing an experiment. Each driver package (moteus_servo, "
                  "intel_rgbd, obsbot_ptz_camera) brings its own diagnostics; IO diagnostics is the thin host. Criteria frozen in commit "
                  "0a138b1; amendment A2 (Zach's evening direction: drivers own diagnostics; scope; reset rules) changed fr2, fr4 and g2, weights unchanged."),
        "count": 5,
        "invariants": frozen["invariants"] + [
            "A2: one shared driver-side interface (identity, versions, config, comms, confirmed reset); directions differ in how the host finds it, the surface, and the health policy.",
            "Fixture frames: 18:11 plug (real kernel log), 18:25 bus busy (real holder: tview), 19:02 after the restart (reported: no board answers).",
        ],
        "boundary": ("Real: USB/sysfs detection, the plug coalescer on today's kernel log, port-holder and lock checks, firmware 1.1.2 per board from "
                     "the flash tool's records, the five discovery mechanisms (tested), and GNOME Shell 46 renders of V1/V2/V4/V5 in a sandbox. "
                     "Reported by the orchestrator: can status counters, 19:00 'no board answers'. Simulated: the OBSBOT being plugged, D405 "
                     "firmware values, the USB-to-SDK serial pairing, hub usage rows. Never run: any CAN query, camera open, reset or config write."),
        "axes": frozen["axes"][:1] + [
            {"name": "Driver binding (A2)", "values": ["manifest in the driver", "module naming convention", "CLI + exit codes", "packaging entry points", "station file declares slots"]},
        ] + frozen["axes"][1:2] + frozen["axes"][3:],
        "requirements": requirements,
        "hardGates": gates,
        "variants": variants(data, drift_lines),
        "defaultId": "v2",
        "defaultWhy": ("Plug Journal tray leads at 82/100: the only direction that pairs a proven one-notice-per-plug path with a one-file driver "
                       "binding and a single stock process. It gives up glance polish (text-only DBusMenu) and machine-readable output. "
                       "Registry contract (77) and Station board (76) are the strong alternatives, not co-leaders (more than 3 points behind)."),
        "decisionHinge": ("Weight on keeping it small to run (fr6, 10%) versus a styled, sourced glance (fr3 + fr4). Move fr6's 10 points to fr3 and "
                          "the order flips: Registry contract 81, Quick Settings 80, Station board 80, Plug Journal 78. Second hinge: if you would "
                          "declare every device in a station file anyway, Station board's fr2 rises to 4 and it ties Plug Journal at 81-82. "
                          "Natural splice: V2's tray + coalescer + naming convention, V5's drift count as the label, V4's registry file for the session runner."),
        "checks": ["Exactly five directions", "Same three-frame fixture in every hero", "Criteria frozen before variants; A2 visible",
                   "Real renders and real outputs as supporting media", "Every score carries evidence and confidence"],
    }
    OUT.mkdir(exist_ok=True)
    spec_path = OUT / "gallery-spec.json"
    spec_path.write_text(json.dumps(spec, indent=1))
    built = OUT / "gallery-build.html"
    built.unlink(missing_ok=True)
    completed = subprocess.run(["/usr/bin/python3", str(GALLERY), "build", "--spec", str(spec_path), "--output", str(built), "--strict"],
                               capture_output=True, text=True)
    print(completed.stdout, completed.stderr)
    if completed.returncode:
        return completed.returncode
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(built.read_text())
    print(f"{REPORT} {REPORT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
