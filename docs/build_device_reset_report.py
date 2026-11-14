"""The report of Reset… and automatic recovery (2026-11-14): one self-contained HTML page, built from what the code says and what
it did, never from hand-copied tables.

    uv run --project /home/bam/io-diagnostics --group dev python /home/bam/io-diagnostics/docs/build_device_reset_report.py

Reads: the profiles (the device x reset table), docs/device_reset_scenarios.py (the 08:57 wedge replayed on the fake rig), a live
read-only `python -m io_diagnostics.recovery check --json` on this machine (opens no device), and the evidence files in
reports/media/device-reset/ (pytest and mutation output, the page's screenshots and hero clip, when present).
Writes reports/media/device-reset-2026-11-14.html when it inlines media, else reports/device-reset-2026-11-14.html.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from io_diagnostics.recovery.profiles import ALL  # noqa: E402
from io_diagnostics.recovery.rung import KINDS  # noqa: E402

MEDIA = ROOT / "reports" / "media" / "device-reset"
DATE = "2026-11-14"
TMP = re.compile(r"/tmp/device-reset-scenario-[^/]+/rig(?=/)")
KIND_WORDS = {"reopen": "Reopen", "native": "Its own reset", "usbreset": "USB port reset", "authorized": "Kernel deauthorize",
              "power_cycle": "Port power cycle", "page": "Page a human"}


def esc(text) -> str:
    return html.escape(TMP.sub("", str(text)))


def run_json(argv: list[str]) -> object:
    done = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT, timeout=300)
    if done.returncode != 0:
        raise SystemExit(f"{argv} failed: {done.stderr[-800:]}")
    return json.loads(done.stdout)


def media(name: str, mime: str) -> str | None:
    path = MEDIA / name
    if not path.exists():
        return None
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def text_file(name: str) -> str:
    path = MEDIA / name
    return path.read_text() if path.exists() else ""


# ----- the device x mechanism table, from the profiles ---------------------------------------------------------------------
def mechanism_table() -> str:
    head = "".join(f"<th>{KIND_WORDS[kind]}</th>" for kind in KINDS)
    rows = []
    for profile in ALL:
        cells = []
        for kind in KINDS:
            rung = profile.rung(kind)
            if rung is None:
                cells.append('<td class="none">—</td>')
                continue
            person = '<span class="tag warn">person only</span>' if not rung.unattended else ""
            same = f'<div class="muted small">skipped after its own reset (the same ioctl)</div>' if any(
                other.same_as == kind for other in profile.rungs) else ""
            cells.append(f'<td><b>{esc(rung.title)}</b> {person}{same}<details><summary>why · expect</summary>'
                         f'<p>{esc(rung.why)}</p><p class="muted">{esc(rung.expect)}</p>'
                         f'<p class="muted small">timeout {rung.timeout_s:g} s · settle {rung.settle_s:g} s</p></details></td>')
        rows.append(f"<tr><th class='row'>{esc(profile.title)}<div class='muted small'>probe: {esc(profile.probe)}</div></th>"
                    + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table class="ladder"><tr><th>Device class</th>{head}</tr>{"".join(rows)}</table></div>'


# ----- what this machine can run today -----------------------------------------------------------------------------------
def availability_table(report: dict) -> str:
    rows = []
    for device in report["devices"]:
        if device["profile"] == "usb_device":
            continue  # keyboards, mice, the webcam: not the rig's; the installer never opens them
        cells = []
        for rung in device["rungs"]:
            mark = "yes" if rung["usable"] else "no"
            needs = f'<div class="muted small">needs {esc(rung["needs"])}</div>' if rung["needs"] else ""
            cells.append(f'<td class="{mark}"><b>{mark}</b> <span class="small">{esc(rung["why"])}</span>{needs}</td>')
        rows.append(f"<tr><th class='row'>{esc(device['name'])}<div class='muted small'>{esc(device['port'])} · "
                    f"{esc(device['profile'])}</div></th>" + "".join(cells) + "</tr>")
    return "<div class='scroll'><table class='avail'>" + "".join(rows) + "</table></div>"


def scenario_block(each: dict) -> str:
    runs = []
    for run in each["runs"]:
        lines = "\n".join(esc(line) for line in run["lines"])
        runs.append(f'<p class="small muted">at +{run["at_s"]:g} s · exit {run["code"]} · <b>{esc(run["outcome"])}</b></p>'
                    f"<pre>{lines}</pre>")
    ledger = "".join(f"<tr><td>{esc(line.get('rung') or '—')}</td><td><b>{esc(line['outcome'])}</b></td>"
                     f"<td class='small'>{esc(line.get('reason') or '')} {esc(line.get('words') or line.get('because') or '')}</td>"
                     f"<td class='small'>{esc(_evidence(line))}</td></tr>" for line in each["ledger"])
    return (f'<div class="card"><h3>{esc(each["title"])}</h3><p>{esc(each["story"])}</p>{"".join(runs)}'
            f"<details><summary>ledger lines ({len(each['ledger'])}) and alarms ({len(each['alarms'])})</summary>"
            f"<table class='small'><tr><th>rung</th><th>outcome</th><th>why</th><th>evidence (before → after)</th></tr>{ledger}</table>"
            + "".join(f"<pre>{esc(json.dumps(alarm, indent=1))}</pre>" for alarm in each["alarms"]) + "</details></div>")


def _evidence(line: dict) -> str:
    before, after = line.get("before") or {}, line.get("after") or {}
    if not before and not after:
        return ""
    parts = [f"device {before.get('devnum', '–')} → {after.get('devnum', 'gone')}"]
    if after.get("port_state"):
        parts.append(f"port {after['port_state'].get('state')}")
    if after.get("kernel"):
        parts.append(f"{len(after['kernel'])} kernel lines")
    probe = line.get("probe") or {}
    if probe:
        parts.append(f"probe: {probe.get('ok')}")
    return " · ".join(parts)


def main() -> None:
    profiles = [profile.to_json() for profile in ALL]
    scenarios = run_json([sys.executable, str(ROOT / "docs" / "device_reset_scenarios.py")])
    here = run_json([sys.executable, "-m", "io_diagnostics.recovery", "check", "--json"])
    facts = here["machine"]
    pytest_tail = text_file("pytest.txt").strip().splitlines()[-1:] or ["(run the tests to fill this in)"]
    mutation = text_file("mutations.txt")
    caught = re.search(r"(\d+) of (\d+) mutations caught", mutation)
    hero = media("hero.mp4", "video/mp4")
    gif = media("hero.gif", "image/gif")
    stills = [(name, media(name, "image/png")) for name in sorted(os.listdir(MEDIA)) if name.endswith(".png")] if MEDIA.exists() else []
    captions = json.loads(text_file("captions.json") or "{}")
    hero_html = ""
    if hero:
        hero_html = (f'<video autoplay muted loop playsinline controls src="{hero}"></video>'
                     f'<p class="muted small">{esc(captions.get("hero.mp4", ""))}</p>'
                     + (f'<details><summary class="muted">GIF fallback</summary><img alt="hero" src="{gif}"></details>' if gif else ""))
    stills_html = "".join(f'<figure><img alt="{esc(name)}" src="{uri}"><figcaption>{esc(captions.get(name, name))}</figcaption></figure>'
                          for name, uri in stills if uri)
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Device reset ladder</title>
<style>
:root{{--bg:#fbfbfa;--ink:#1f2328;--muted:#5d6670;--card:#fff;--line:#e3e5e8;--ok:#1a7f37;--no:#9a6700;--warn:#bc4c00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181b;--ink:#e6e7e9;--muted:#9aa3ad;--card:#1f2226;--line:#30343a;--ok:#3fb950;--no:#d29922;--warn:#f0883e}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Inter,system-ui,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 16px 64px}} h1{{font-size:26px;margin:0 0 6px}} h3{{margin:0 0 6px;font-size:16px}}
h2{{font-size:18px;margin:34px 0 10px;padding-top:8px;border-top:1px solid var(--line)}} .muted{{color:var(--muted)}} .small{{font-size:13px}}
code,pre{{font:12.5px/1.45 ui-monospace,Menlo,monospace}} code{{background:var(--card);border:1px solid var(--line);padding:0 4px;border-radius:4px}}
pre{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;overflow-x:auto;white-space:pre-wrap}}
video,img{{max-width:100%;border:1px solid var(--line);border-radius:8px;display:block}} figure{{margin:0 0 14px}} figcaption{{color:var(--muted);font-size:13px;margin:6px 2px 0}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border-bottom:1px solid var(--line);padding:7px;vertical-align:top;text-align:left}}
th.row{{min-width:150px}} td.none{{color:var(--muted);text-align:center}} td.yes b{{color:var(--ok)}} td.no b{{color:var(--no)}}
.tag{{font-size:11px;border:1px solid currentColor;border-radius:9px;padding:0 6px;white-space:nowrap}} .tag.warn{{color:var(--warn)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}}
details summary{{cursor:pointer;color:var(--muted);font-size:13px}} ul{{padding-left:20px}} li{{margin:6px 0}}
table.ladder td{{min-width:150px}}
</style></head><body><main>
<h1>Reset… and automatic recovery</h1>
<p><b>Every device RobotDx knows now has a reset ladder: its own reset first, then the USB rungs, then a page to a human. A deployed robot runs it
by itself, and a person runs it with one click.</b> It is on RobotDx <code>main</code> @ <code>{esc(sha)}</code>. Two rungs are not yet usable here:
the kernel deauthorize and the port power cycle wait for your one-time sudo, and a power cycle also needs a hub that can switch each port's power.
No live device was reset. Every run shown here is on the fake rig.</p>
{hero_html}

<h2>The 08:57 wedge, replayed</h2>
<p>Each block below is the real ladder on the test suite's fake rig: a fake sysfs, /dev and /proc, with the fdcanusb "frozen" so that only the chosen
rung cures it. The text is what it printed and wrote. arm-hold calls it as the owner, after its own reopen and its own <code>fdcanusb-reset</code>.</p>
{"".join(scenario_block(each) for each in scenarios)}

<h2>Device × reset mechanism</h2>
<p class="muted">Generated from <code>io_diagnostics/recovery/profiles/</code>. Each cell is one rung, in ladder order. Open one to read why it sits at
that place and what to expect.</p>
{mechanism_table()}
<p class="small"><b>moteus, checked against the firmware rather than assumed:</b> <code>d reset</code> calls <code>NVIC_SystemReset()</code> straight away
(moteus-src 2b21a16f, <code>fw/board_debug.cc:971</code>), without the <code>MoteusEnsureOff()</code> that <code>d flash</code> runs. There is no reboot
register, and moteus_tool 1.1.1 has no restart flag. After the reset the board knows its position only within one revolution. So this rung is
person only: a loaded joint drops, and the homed map is void. A silent board never gets the reset, so its ladder ends with "check its cable and
24 V".</p>

<h2>What this machine can run today</h2>
<p class="muted small">Live, read-only (<code>python -m io_diagnostics.recovery check</code>; it opens no device). Kernel {esc(facts["kernel"])}, port disable
switch {"available" if facts["port_disable_interface"] else "missing"}, in plugdev: {"yes" if facts["in_plugdev"] else "no"}.
usbreset: {esc(facts["usbreset"] or "missing")}. fdcanusb-reset: {esc(facts["fdcanusb_reset"] or "missing")}. uhubctl: {esc(facts["uhubctl"] or "not installed (not needed)")}.
Rules: {esc(facts["installed_rules"] or "not installed")}. Hub facts: {esc(facts["hub_facts"] or "not read yet")}.</p>
{availability_table(here)}
<p class="small">Already usable with no change: the fdcanusb's own reset and a plain <code>usbreset</code> work, because the
<code>99-fdcanusb-usb-access.rules</code> you installed at 16:01 opens its node to plugdev. The RealSense cameras' own
<code>hardware_reset()</code> and <code>usbreset</code> work too, because librealsense's own rule opens their nodes.</p>

<h2>Needs you: one sudo, once</h2>
<pre>sudo bash /home/bam/io-diagnostics/tools/install_device_reset_access.sh</pre>
<ul>
<li>It installs <code>tools/udev/70-robotdx-device-reset.rules</code>. That file is generated from the profiles, a test keeps the two equal, and
<code>udevadm verify</code> passes on it. The rules cover the rig's device classes only: fdcanusb, RealSense and OBSBOT. For those, plugdev gets the
device's usbfs node, its <code>authorized</code> switch, and the <code>disable</code> switch of the hub port it sits on (plus that port's USB 3 peer).
Your keyboard's node stays root's.</li>
<li>It reads each hub's power switching, as root, from its hub descriptor (<code>lsusb -v</code>) and writes it to
<code>/var/lib/robotdx/hub-power-switching.txt</code>. From then on the ladder knows, as any user, which ports really cut VBUS.</li>
<li>It unlocks the kernel deauthorize for all three classes, plus <code>usbreset</code> for the OBSBOT. The port power cycle is unlocked only
where the hub switches each port.</li>
<li>Undo: <code>sudo rm /etc/udev/rules.d/70-robotdx-device-reset.rules /var/lib/robotdx/hub-power-switching.txt && sudo udevadm control --reload</code>.
Run that yourself.</li>
</ul>

<h2>Needs hardware: a hub that really cuts VBUS</h2>
<p>For a frozen microcontroller, cutting the port's power is the only sure cure. A USB reset needs the MCU's own USB stack to still respond. At 08:57
the adapter stayed enumerated while its data path was dead, so nobody knows yet whether a reset alone would have brought it back. The next wedge
will tell: arm-hold now tries its reset first.</p>
<ol>
<li><b>Check the PC's own root hub first.</b> The fdcanusb sits on root hub <code>usb7</code>, port 1, with nothing in between. The uhubctl README
notes that quite a few modern motherboards' root hubs do switch each port's power. The installer reads this for <code>usb7</code>. If it reports
<code>individual</code>, the power cycle already works and you need to buy nothing. Then do the uhubctl README's lamp test (a USB lamp on that
port) to confirm VBUS really drops.</li>
<li><b>Otherwise, buy a StarTech HB31C3A1CPD3</b> (5-port USB 3.1, <code>14b0:013a</code>). In the uhubctl table it is the only USB 3 hub with an
explicit VBUS-cut report (issue #656: "It works (including VBUS)", charging stopped on the switched port). Put only the fdcanusb (USB 2, full speed)
on it. The RealSense cameras stay on the PC: their own <code>hardware_reset()</code> is their cure, and on these hubs a USB 3 device's VBUS often
drops only when every port is off (issue #601 on the HB31C2A2CB). Caveats: there is a single report, the hub is expensive, and its price and
availability are not checked (the vendor pages refused automated reads). The long-standing alternative is the Plugable USB3-HUB7BC
(<code>2109:0813</code>), but nothing beyond its table row confirms VBUS on it.</li>
</ol>

<h2>How an owner calls it (for Sim to Real's arm-hold)</h2>
<pre>python3 -m io_diagnostics.recovery recover --device usb:0483:5740:0607623A --owner-pid $PID \\
    --detected "no reply to 5 exchanges" --tried reopen,native [--step] [--page-command "..."]</pre>
<ul>
<li>Exit codes: 0 it answers · 2 refused, nothing was reset · 3 a rung ran, so reopen and judge · 4 a human is needed and has been paged. The
owner must be the calling process or one of its parents. The owner's own open handle, and its helpers', do not block the reset. Anyone else's
handle does, and so does anyone else holding the rack bus lock.</li>
<li>Each rung runs at most once per incident. An incident closes after 600 s of answering, and more than 3 incidents in 24 h page instead of
resetting again. Every attempt goes to <code>~/.local/state/io-diagnostics/recovery/ledger.jsonl</code> with before/after snapshots and the
kernel's lines; every page goes to <code>alarms.jsonl</code>.</li>
</ul>

<h2>Proof</h2>
<ul>
<li>Tests: {esc(pytest_tail[0])} (<code>tests/test_recovery.py</code> and the host wiring's <code>tests/test_host_reset_action.py</code>). All run on fake trees.
The tripwire refuses the real <code>fdcanusb-reset</code>, <code>usbreset</code> and <code>uhubctl</code>, any recover or act without a fake sysfs, and,
in process, any sysfs write or device open outside the temp dir.</li>
<li>Mutation checks of the refusal paths: <b>{esc(caught.group(0) if caught else "not run")}</b> (<code>tests/mutate_recovery.py</code>).
<details><summary>each mutation</summary><pre>{esc(mutation)}</pre></details></li>
<li>The rules file: <code>udevadm verify</code> passes. The installer refuses to run without root and touches nothing.</li>
</ul>
{('<h2>On the page</h2>' + stills_html) if stills_html else ""}

<h2>Decided for you</h2>
<ul>
<li>A new standard action, <code>reset</code> · "Reset…", for the device level. <code>reset_port</code> stays as it was, for the port level. Undo:
<code>git revert</code> the commits.</li>
<li>The power cycle uses the kernel's own port <code>disable</code> (Linux 6.0+, the same thing uhubctl does there), so uhubctl is not installed.</li>
<li>The moteus board reboot is never automatic. The OBSBOT has no own reset, because the camera exposes none.</li>
<li>Profiles are frozen dataclasses, one per file (Python ground truth). The rules file and these tables are generated from them.</li>
</ul>
<h2>Left, and deliberately not done</h2>
<ul>
<li>I did not reset any live device. No live trial has happened. The arm rack is Sim to Real's.</li>
<li>arm-hold calling <code>recover</code> after its own reset is Sim to Real's change to make. The owner contract is above.</li>
<li>The camera lock. At 13:44:50 an intel_rgbd diagnose took the rack bus lock. That plugin (on bam_ws, Sim to Real's worktree) has no lock option
and always takes <code>bus.lock</code> shared. Giving cameras their own lock is a change in that plugin. RobotDx's own reset ladder never takes the
rack lock for a camera.</li>
<li>No supervisor runs the ladder yet for devices nobody owns, such as a systemd timer that calls <code>recover</code>. The verb is ready for one.</li>
</ul>
</main></body></html>"""
    has_media = bool(hero or stills)
    out = (ROOT / "reports" / "media" / f"device-reset-{DATE}.html") if has_media else (ROOT / "reports" / f"device-reset-{DATE}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(out, f"{out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
