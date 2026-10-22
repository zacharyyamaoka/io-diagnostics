"""Interactive hero specimens for the five directions, generated from contract.views.

Every hero is one GNOME desktop strip (Yaru-dark colours sampled from the real sandbox renders)
with five CSS-driven scenes the gallery's story stepper switches by ``data-story-state``:
plug -> buses -> device -> blocked -> now. Direct controls carry ``data-story-to`` so a click
and the stepper move the same state machine. No control is fake: anything off this round is
drawn disabled and says why.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contract.views import EVENTS, views  # noqa: E402

E = html.escape
STATES = ("plug", "buses", "device", "blocked", "now")
FRAME_OF = {"plug": "F1", "buses": "F1", "device": "F1", "blocked": "F2", "now": "F3"}
CLOCK = {"F1": "18:11", "F2": "18:25", "F3": "19:02"}

SVG = {
    "bus": '<svg viewBox="0 0 22 22" width="16" height="16"><path d="M3 15H19M5 15V9M11 15V9M17 15V9" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="5" cy="7.5" r="2.3" fill="currentColor"/><circle cx="11" cy="7.5" r="2.3" fill="currentColor"/><circle cx="17" cy="7.5" r="2.3" fill="currentColor"/></svg>',
    "busdot": '<svg viewBox="0 0 22 22" width="18" height="18"><path d="M3 15H19M5 15V9M11 15V9M17 15V9" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="5" cy="7.5" r="2.3" fill="currentColor"/><circle cx="11" cy="7.5" r="2.3" fill="currentColor"/><circle cx="17" cy="7.5" r="2.3" fill="currentColor"/><circle cx="18" cy="5" r="3.6" fill="#f9a826"/></svg>',
    "cam": '<svg viewBox="0 0 16 16" width="15" height="15"><rect x="1.5" y="4" width="10" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M11.5 7l3-2v6l-3-2z" fill="currentColor"/></svg>',
    "net": '<svg viewBox="0 0 16 16" width="15" height="15"><rect x="6" y="1.5" width="4" height="4" fill="currentColor"/><rect x="1.5" y="10.5" width="4" height="4" fill="currentColor"/><rect x="10.5" y="10.5" width="4" height="4" fill="currentColor"/><path d="M8 5.5V8M3.5 10.5V8h9v2.5" stroke="currentColor" stroke-width="1.4" fill="none"/></svg>',
    "vol": '<svg viewBox="0 0 16 16" width="15" height="15"><path d="M2 6h3l4-3v10L5 10H2z" fill="currentColor"/><path d="M11 5.5a3.5 3.5 0 010 5" stroke="currentColor" stroke-width="1.4" fill="none"/></svg>',
    "pow": '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 1.5v6" stroke="currentColor" stroke-width="1.7"/><path d="M4.5 4a5.2 5.2 0 107 0" stroke="currentColor" stroke-width="1.7" fill="none"/></svg>',
    "warn": '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 1.5L15 14H1z" fill="currentColor"/><path d="M8 6v4M8 11.5v1.2" stroke="#131313" stroke-width="1.6"/></svg>',
    "err": '<svg viewBox="0 0 16 16" width="14" height="14"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M4.5 8h7" stroke="#2a2a2a" stroke-width="2"/></svg>',
    "lock": '<svg viewBox="0 0 16 16" width="14" height="14"><rect x="3" y="7" width="10" height="7.5" rx="1.5" fill="currentColor"/><path d="M5 7V5a3 3 0 016 0v2" stroke="currentColor" stroke-width="1.6" fill="none"/></svg>',
    "chev": '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.8" fill="none"/></svg>',
    "down": '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M3 6l5 5 5-5" fill="currentColor"/></svg>',
    "ring": '<svg viewBox="0 0 22 22" width="18" height="18"><circle cx="11" cy="11" r="7" stroke="rgba(255,255,255,.22)" stroke-width="3" fill="none"/><path d="M11 4a7 7 0 016.6 9.3" stroke="#4a8ff0" stroke-width="3" fill="none" stroke-linecap="round"/><circle cx="18" cy="5" r="3.4" fill="#f9a826"/></svg>',
    "gear": '<svg viewBox="0 0 16 16" width="14" height="14"><circle cx="8" cy="8" r="2.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1v2.2M8 12.8V15M1 8h2.2M12.8 8H15M3 3l1.6 1.6M11.4 11.4L13 13M13 3l-1.6 1.6M4.6 11.4L3 13" stroke="currentColor" stroke-width="1.5"/></svg>',
}
LEVEL_ICON = {"error": ("err", "#e8604c"), "warn": ("warn", "#f9a826"), "blocked": ("lock", "#c8c8c8")}

CSS = """
<style>
.iod{--bar:#131313;--qs:#111111;--tog:#2a2a2a;--on:#7e2a0d;--on2:#93391a;--menu:#313131;--pop:#1d1d1d;--hl:#4a4a4a;--txt:#f6f6f6;--dim:#a3a3a3;--faint:#7c7c7c;--err:#e8604c;--warn:#f9a826;--okc:#57c785;
  font-family:Ubuntu,"Ubuntu Sans",Cantarell,"Noto Sans",system-ui,sans-serif;color:var(--txt);}
.iod *{box-sizing:border-box}
.iod .desk{position:relative;width:780px;height:680px;margin:0 auto;background:linear-gradient(165deg,#062a5e 0%,#023c88 60%,#0a4b9f 100%);overflow:hidden;border-radius:6px;font-size:13px;line-height:1.35}
.iod .bar{position:relative;height:30px;background:var(--bar);display:flex;align-items:center;justify-content:space-between;padding:0 6px 0 10px;font-size:13.5px;font-weight:500}
.iod .ws{display:flex;gap:5px;align-items:center}.iod .ws i{width:6px;height:6px;border-radius:3px;background:#8a8a8a}.iod .ws i:first-child{width:24px;background:#fff}
.iod .clock{position:absolute;left:50%;top:7px;transform:translateX(-50%);white-space:nowrap}
.iod .right{display:flex;align-items:center;gap:6px}
.iod .sys{display:flex;gap:9px;align-items:center;padding:3px 9px;border-radius:13px}
.iod .ind{display:flex;gap:5px;align-items:center;padding:3px 9px;border-radius:13px;white-space:nowrap}
.iod [data-story-to]{cursor:pointer}
.iod .bar [data-story-to]:hover,.iod .bar .open{background:#3b3b3b}
.iod .bar .open{background:#454545}
.iod .scene{display:none;position:absolute;inset:0}
.prototype[data-story-state="plug"] .iod .s-plug,.prototype[data-story-state="buses"] .iod .s-buses,.prototype[data-story-state="device"] .iod .s-device,
.prototype[data-story-state="blocked"] .iod .s-blocked,.prototype[data-story-state="now"] .iod .s-now{display:block}
.iod .chips{position:absolute;left:12px;bottom:12px;display:flex;gap:6px;z-index:5}
.iod .chip{font:600 11px/1 Ubuntu,system-ui,sans-serif;padding:7px 10px;border-radius:14px;background:rgba(0,0,0,.45);color:#dfe8f5;border:1px solid rgba(255,255,255,.18)}
.iod .chip:hover{background:rgba(0,0,0,.7)}
.prototype:is([data-story-state="plug"],[data-story-state="buses"],[data-story-state="device"]) .iod .chip.c1,
.prototype[data-story-state="blocked"] .iod .chip.c2,.prototype[data-story-state="now"] .iod .chip.c3{background:#fff;color:#111;border-color:#fff}
.iod .tag{position:absolute;left:12px;bottom:50px;max-width:300px;font:600 10px/1.35 ui-monospace,monospace;color:#cfe0ff;opacity:.85;text-align:left}
.iod .banner{position:absolute;top:40px;left:50%;transform:translateX(-50%);width:390px;background:#282828;border-radius:12px;padding:10px 14px 12px;box-shadow:0 6px 24px rgba(0,0,0,.45)}
.iod .banner .src{display:flex;gap:6px;align-items:center;color:var(--dim);font-size:11.5px;margin-bottom:5px}
.iod .banner b{display:block;font-weight:600;margin-bottom:2px}
.iod .banner .x{position:absolute;right:10px;top:9px;width:22px;height:22px;border-radius:11px;background:#3a3a3a;display:grid;place-items:center;font-size:12px;color:#ddd}
.iod .qs{position:absolute;top:34px;right:6px;width:356px;background:var(--qs);border-radius:16px;padding:12px;box-shadow:0 8px 30px rgba(0,0,0,.5)}
.iod .qs .icons{display:flex;justify-content:space-between;margin-bottom:10px}.iod .qs .icons span{width:30px;height:30px;border-radius:15px;background:var(--tog);display:grid;place-items:center;color:#ddd}
.iod .tg{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.iod .t{display:flex;align-items:center;height:42px;border-radius:21px;background:var(--tog);padding-left:14px;gap:8px;overflow:hidden;font-size:13px}
.iod .t.on{background:var(--on)}.iod .t .a{margin-left:auto;align-self:stretch;width:34px;display:grid;place-items:center;background:rgba(255,255,255,.07)}
.iod .t.on .a{background:var(--on2)}.iod .t small{display:block;font-size:11px;opacity:.85;margin-top:-2px}
.iod .qm{margin-top:10px;background:var(--menu);border-radius:14px;padding:10px 8px 8px}
.iod .qm .hd{display:flex;gap:10px;align-items:center;padding:2px 6px 10px}.iod .qm .hd i{width:34px;height:34px;border-radius:17px;background:#e95420;display:grid;place-items:center;color:#fff}
.iod .qm .hd b{font-size:16px;font-weight:500;display:block}.iod .qm .hd span{font-size:11.5px;color:#d6d6d6}
.iod .row{display:flex;align-items:center;gap:8px;padding:8px 9px;border-radius:9px;white-space:nowrap}
.iod .row.click:hover,.iod .row.sel{background:var(--hl)}
.iod .row .nm{font-weight:500}.iod .row .st{margin-left:auto;font-size:11.5px;color:#d0d0d0}
.iod .sub{padding:2px 6px 6px 33px}.iod .sub div{padding:4px 0;color:var(--dim);white-space:normal}
.iod .sub .hot{color:#ffd8cf}
.iod .btn{display:inline-block;padding:6px 12px;border-radius:8px;background:#454545;color:#f2f2f2;font-size:12px;margin:4px 6px 0 0;border:0}
.iod .btn.off{opacity:.45;cursor:not-allowed}.iod .btn.go{background:#e95420;color:#fff}
.iod .confirm{margin:8px 0 2px;padding:9px 10px;border-radius:10px;background:#222;border:1px solid #4a4a4a;color:#e6e6e6;white-space:normal}
.iod .why{font-size:11px;color:var(--faint);margin-top:4px}
.iod .dm{position:absolute;top:34px;width:380px;background:var(--pop);border-radius:12px;padding:6px;box-shadow:0 8px 30px rgba(0,0,0,.5)}
.iod .dm .r{padding:7px 12px;border-radius:7px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.iod .dm .r.act{color:var(--txt)}.iod .dm .r[data-story-to]:hover,.iod .dm .r.sel{background:#3a3a3a;color:var(--txt)}
.iod .t:not([data-story-to]),.iod .qs .icons span,.iod .dm .r:not([data-story-to]),.iod .ghost,.iod .acct{cursor:default}
.iod .acct .ghost{opacity:.5}
.iod .dm hr{border:0;border-top:1px solid #3a3a3a;margin:5px 10px}
.iod .dm .in{background:#262626;border-radius:8px;margin:2px 4px}
.iod .dlg{position:absolute;left:50%;top:210px;transform:translateX(-50%);width:330px;background:#2b2b2b;border-radius:14px;padding:16px;box-shadow:0 10px 40px rgba(0,0,0,.6);text-align:center}
.iod .dlg b{display:block;font-size:14px;margin-bottom:6px}
.iod .hub{position:absolute;top:34px;right:46px;width:470px;background:#262624;border-radius:14px;padding:12px 12px 10px;box-shadow:0 8px 30px rgba(0,0,0,.55);font-family:"Styrene B","Inter",system-ui,sans-serif;color:#f5f4ef}
.iod .hub .hh{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-weight:600}.iod .hub .hh .sp{margin-left:auto;display:flex;gap:10px;color:#bdb9b0}
.iod .acct{display:flex;align-items:center;gap:8px;height:30px;padding:0 8px;border-radius:8px;font-size:12.5px;color:#d9d6cd}
.iod .acct .bar2{width:90px;height:5px;border-radius:3px;background:rgba(255,255,255,.06);overflow:hidden}.iod .acct .bar2 i{display:block;height:100%;background:#4a8ff0}
.iod .acct .ghost,.iod .iorow .ghost{margin-left:auto;padding:3px 10px;border-radius:7px;border:1px solid #4a4944;color:#e8e5dc;font-size:11.5px}
.iod .sec{margin:10px 8px 4px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#a8a59d;display:flex;justify-content:space-between}
.iod .iorow{display:flex;align-items:center;gap:8px;height:34px;padding:0 8px;border-radius:8px;font-size:12.5px;white-space:nowrap}
.iod .iorow.click:hover,.iod .iorow.sel{background:#30302e}.iod .iorow .dot{width:8px;height:8px;border-radius:4px;background:#57c785}
.iod .iorow .dot.error{background:#e8604c}.iod .iorow .dot.warn{background:#e0a43a}.iod .iorow .dot.blocked{background:#8f8c84}
.iod .iorow .s{color:#a8a59d;overflow:hidden;text-overflow:ellipsis;max-width:190px}
.iod .card{margin:2px 8px 6px;padding:9px 10px;border-radius:9px;background:#30302e;font-size:12px}
.iod .kv{display:grid;grid-template-columns:86px 1fr;gap:3px 10px}.iod .kv span:nth-child(odd){color:#a8a59d}
.iod .src{color:#8f8c84;font-size:10.5px}
.iod .win{position:absolute;top:44px;left:40px;width:700px;height:540px;background:#202124;border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.55);overflow:hidden;font-family:Inter,system-ui,sans-serif}
.iod .win .tb{height:30px;background:#2b2b2e;display:flex;align-items:center;gap:8px;padding:0 10px;font-size:11.5px;color:#bbb}
.iod .win .tb i{width:10px;height:10px;border-radius:5px;background:#555}.iod .win .tb span{margin-left:8px;padding:3px 10px;border-radius:6px;background:#1c1c1f}
.iod .win .body{display:grid;grid-template-columns:230px 1fr;height:510px}
.iod .win .list{border-right:1px solid #333;padding:10px 8px;overflow:hidden}.iod .win .pane{padding:12px 14px;overflow:hidden}
.iod .win h4{margin:0 0 2px;font-size:15px;font-weight:600}.iod .win .lead{color:#c9c9c9;margin-bottom:8px}
.iod .win table{border-collapse:collapse;width:100%;font-size:11.5px}.iod .win td{padding:3px 6px 3px 0;border-bottom:1px solid #2e2e30;vertical-align:top}
.iod .win td.k{color:#9aa0a6;width:84px}.iod .win td.s{color:#7d8288;font-size:10.5px}
.iod pre{margin:8px 0 0;padding:8px 10px;background:#141416;border-radius:7px;color:#cfe3c9;font:10.5px/1.4 ui-monospace,Menlo,monospace;white-space:pre-wrap}
.iod .lrow{display:flex;gap:7px;align-items:center;padding:7px 8px;border-radius:7px;font-size:12px}.iod .lrow.click:hover,.iod .lrow.sel{background:#2f3033}
.iod .lrow .s{margin-left:auto;color:#9aa0a6;font-size:11px;max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.iod .slots{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;padding:12px}
.iod .slot{border-radius:9px;border:1px solid #3a3a3d;padding:8px;min-height:92px;font-size:11.5px;background:#26272a}
.iod .slot b{display:block;font-size:11.5px;margin-bottom:4px}.iod .slot.drift{border-color:#e8604c;box-shadow:inset 0 0 0 1px #e8604c}
.iod .slot.missing{border-style:dashed;opacity:.75}.iod .slot.ok{border-color:#3d6b52}.iod .slot.sel{outline:2px solid #fff}
.iod .ev{color:#9aa0a6}.iod .exp{color:#9aa0a6;font-size:10.5px}
</style>
"""


def icon(name: str, color: str = "currentColor") -> str:
    return f'<span style="color:{color};display:inline-flex">{SVG[name]}</span>'


def level_icon(level: str) -> str:
    if level in LEVEL_ICON:
        name, color = LEVEL_ICON[level]
        return icon(name, color)
    return ""


def bar(clock: str, indicator: str) -> str:
    return (f'<div class="bar"><div class="ws"><i></i><i></i><i></i><i></i></div><div class="clock">Oct 21&nbsp; {clock}</div>'
            f'<div class="right">{indicator}<div class="sys">{icon("net")}{icon("vol")}{icon("pow")}</div></div></div>')


def chips() -> str:
    return ('<div class="chips"><span class="chip c1" data-story-to="plug">18:11 · plug</span>'
            '<span class="chip c2" data-story-to="blocked">18:25 · bus busy</span>'
            '<span class="chip c3" data-story-to="now">19:02 · after restart</span></div>')


def banner(source: str, title: str, body: str, src_icon: str = "gear") -> str:
    return (f'<div class="banner"><div class="src">{icon(src_icon)} {E(source)} · just now</div><span class="x">✕</span>'
            f'<b>{E(title)}</b><div>{E(body)}</div></div>')


def views_by_frame() -> dict[str, list]:
    return {frame: views(frame) for frame in ("F1", "F2", "F3")}


def _find(device_views, key):
    return next(view for view in device_views if view.key == key)


def _detail_lines(view, compact: bool = False) -> list[tuple[str, str, str]]:
    """(label, value, source) rows a detail view shows, in scope order: identity, versions, config, comms."""

    rows = []
    want = {"identity": ("ids", "usb serial", "sdk serial", "serial", "position", "/dev/fdcanusb"),
            "versions": ("firmware", "driver"),
            "config": ("CAN 15", "check"), "comms": ("bus", "adapter", "meaning", "replies", "link", "check")}
    for section, labels in want.items():
        if section == "versions":
            rows += _firmware_summary(view)
        for row in view.sections.get(section, []):
            if row.label in labels:
                if compact and row.label == "ids" and view.ids:
                    continue  # the bus list above already shows the ids
                label = "config " + row.label if section == "config" and row.label.startswith("CAN") else row.label
                if row.label == "check":
                    if any(existing[0] == "checks" for existing in rows):
                        continue  # config and comms wait for the same holder: say it once
                    label = "checks"
                rows.append((label, row.value, row.source))
    return rows[:8] if compact else rows


def _firmware_summary(view) -> list[tuple[str, str, str]]:
    """Per-board firmware rows folded into one line when they agree (they did: all 1.1.2)."""

    boards = [row for row in view.sections.get("versions", []) if row.label.startswith("CAN")]
    latest = next((row.value for row in view.sections.get("versions", []) if row.label == "latest"), None)
    if not boards:
        return []
    versions = {row.value.split(" · ")[0].removeprefix("fw ") for row in boards}
    ids = ", ".join(row.label.removeprefix("CAN ") for row in boards)
    if len(versions) == 1:
        version = versions.pop()
        state = "current" if version == latest else f"update available ({latest})"
        return [("firmware", f"{version} on CAN {ids} · latest {latest} · {state}", "moteus_tool --info in the flash tool's run records, Oct 9-18")]
    return [(f"fw {row.label}", row.value, row.source) for row in boards]


TAG = '<div class="tag">real: ids, fw, ports, holder · reported: can status, 19:00 · simulated: OBSBOT, D405 fw</div>'


# ---------------------------------------------------------------- V1 Quick Settings
def v1(data) -> str:
    def panel(device_views, expanded: str | None, mode: str) -> str:
        attention = [view for view in device_views if view.level in ("error", "warn", "blocked")]
        rows = []
        for view in device_views:
            glyph = "bus" if view.kind == "bus" else "cam"
            name = view.key if view.kind == "bus" else view.title
            is_open = view.key == expanded
            target = "buses" if is_open else ("device" if view.key == "272813EF" and mode == "F1" else "")
            attrs = f' data-story-to="{target}"' if target else ""
            new = ' <span style="font-size:10px;background:#e95420;border-radius:8px;padding:1px 6px">new</span>' if "new" in view.headline else ""
            rows.append(f'<div class="row{" click" if target else ""}{" sel" if is_open else ""}"{attrs}>{icon(glyph)}<span class="nm">{E(name)}</span>{new}'
                        f'<span class="st">{E(view.short)}</span>{level_icon(view.level)}{icon("down" if is_open else "chev")}</div>')
            if is_open:
                lines = [f'<div class="hot">{E(view.headline)}</div>'] + [f'<div>{E(text)}</div>' for _id, text, _level in view.ids]
                # WHY only two rows: Quick Settings is ~350 px wide; config and sources stay one action away
                lines += [f'<div>{E(label)}: {E(value)}</div>' for label, value, _src in _detail_lines(view, compact=True)
                          if label in ("firmware", "bus", "link", "position", "meaning")]
                if view.gate == "blocked":
                    lines.append(f'<div class="hot">{icon("lock")} Checks wait: {E(view.gate_reason)}</div>')
                    lines.append('<div><span class="btn off">Run comms check</span><span class="btn off">Reset board…</span>'
                                 '<div class="why">Both wait until nothing holds the port and the rack lock is free.</div></div>')
                else:
                    lines.append('<div data-hint="reset"><div class="confirm"><b>Reboot CAN 15?</b> Restarts the board; nothing moves; '
                                 'waits for a free port and rack lock.<br>'
                                 '<span class="btn" data-story-to="buses">Cancel</span><span class="btn off">Reboot</span>'
                                 '<div class="why">Off this round (passive-only).</div></div></div>')
                rows.append(f'<div class="sub">{"".join(lines)}</div>')
        subtitle = f"{len(attention)} need you" if attention else "all OK"
        return (f'<div class="qs"><div class="icons"><span>{icon("gear")}</span><span>{icon("lock")}</span><span>{icon("pow")}</span></div>'
                '<div class="tg"><div class="t on">' + icon("net") + 'Wired<span class="a">' + icon("chev") + '</span></div>'
                '<div class="t">Wi-Fi<span class="a">' + icon("chev") + '</span></div>'
                '<div class="t on">Power Mode<span class="a">' + icon("chev") + '</span></div><div class="t">Night Light</div>'
                '<div class="t on">Dark Style</div>'
                f'<div class="t on" data-story-to="buses">{icon("bus")}<span>Rack IO<small>{E(subtitle)}</small></span><span class="a">{icon("down")}</span></div></div>'
                f'<div class="qm"><div class="hd"><i>{icon("bus")}</i><div><b>Rack IO</b><span>{len(device_views)} devices · {E(subtitle)}</span></div></div>'
                + "".join(rows) + '<div class="why" style="padding:6px 9px 0">Drivers: moteus_servo · intel_rgbd · obsbot_ptz_camera</div></div></div>')

    def sysbtn(open_: bool) -> str:
        cls = "sys open" if open_ else "sys"
        return f'<div class="{cls}" data-story-to="buses">{icon("warn", "#f9a826")}{icon("net")}{icon("vol")}{icon("pow")}</div>'

    def scene(state: str, inner: str, clock: str, open_: bool) -> str:
        top = bar(clock, "").replace(f'<div class="sys">{icon("net")}{icon("vol")}{icon("pow")}</div>', sysbtn(open_))
        return f'<div class="scene s-{state}">{top}{inner}</div>'

    f1, f2, f3 = data["F1"], data["F2"], data["F3"]
    first = EVENTS["F1"][0]
    scenes = [
        scene("plug", banner("System", "IO diagnostics · plugged", first[2] + " · " + _find(f1, "28B3A25D").headline), "18:11", False),
        scene("buses", panel(f1, None, "F1"), "18:11", True),
        scene("device", panel(f1, "272813EF", "F1"), "18:11", True),
        scene("blocked", panel(f2, "272813EF", "F2"), "18:25", True),
        scene("now", panel(f3, None, "F3"), "19:02", True),
    ]
    return wrap(scenes)


# ---------------------------------------------------------------- V2 Plug Journal (AppIndicator)
def _dbus_menu(device_views, frame: str, expanded: str | None, right: int, reset_dialog: bool = False) -> str:
    attention = [view for view in device_views if view.level in ("error", "warn", "blocked")]
    mark = {"ok": "●", "warn": "▲", "error": "■", "blocked": "◌"}
    rows = [f'<div class="r">{len(attention)} need you · {len(device_views)} devices</div><hr><div class="r">Recent</div>']
    rows += [f'<div class="r">&nbsp;&nbsp; {E(t)}&nbsp;&nbsp; {E(text)}</div>' for t, _k, text, _tag in EVENTS[frame]]
    rows.append("<hr>")
    for view in device_views:
        is_open = view.key == expanded
        target = "buses" if is_open else ("device" if view.key == "272813EF" and frame == "F1" else "")
        attrs = f' data-story-to="{target}"' if target else ""
        rows.append(f'<div class="r act{" sel" if is_open else ""}"{attrs}>{mark[view.level]}&nbsp; {E(view.title)} — {E(view.headline)}</div>')
        if is_open:
            inner = [f'<div class="r">{mark[level]}&nbsp; {E(text)}</div>' for _id, text, level in view.ids]
            inner += [f'<div class="r">●&nbsp; {E(label)}: {E(value)}</div>' for label, value, _s in _detail_lines(view, compact=True)[:6]
                      if label != "checks"]  # the ◌ gate line below says it once
            if view.gate == "blocked":
                inner.append(f'<div class="r">◌&nbsp; checks wait: {E(view.gate_reason)}</div>')
            hint = ' data-hint="reset"' if reset_dialog else ""
            inner.append(f'<div class="r"{hint}>Run comms check (off this round) · Reset board… (asks first)</div>')
            rows.append(f'<div class="in">{"".join(inner)}</div>')
    rows.append('<hr><div class="r">Driver diagnostics found: moteus_servo, intel_rgbd, obsbot_ptz_camera</div><div class="r act">Quit</div>')
    return f'<div class="dm" style="right:{right}px">{"".join(rows)}</div>'


def v2(data) -> str:
    def ind(count: int, open_: bool) -> str:
        cls = "ind open" if open_ else "ind"
        return f'<div class="{cls}" data-story-to="buses">{SVG["busdot"]}<span>{count}</span></div>'

    def scene(state, frame, inner, open_):
        device_views = data[frame]
        count = len([view for view in device_views if view.level in ("error", "warn", "blocked")])
        return f'<div class="scene s-{state}">{bar(CLOCK[frame], ind(count, open_))}{inner}</div>'

    first = EVENTS["F1"][0]
    dialog = ('<div class="dlg" data-hint="reset"><b>Reboot CAN 15 on 272813EF?</b>The board restarts; nothing moves.<br>'
              'Waits if anything holds the port or the rack lock.<br><span class="btn" data-story-to="buses">Cancel</span>'
              '<span class="btn off">Reboot</span><div class="why">A small GTK dialog: the one window V2 ever opens, and only on your click. '
              'Reboot is off this round.</div></div>')
    scenes = [
        scene("plug", "F1", banner("IO diagnostics", "IO diagnostics · plugged", first[2] + " · " + _find(data["F1"], "28B3A25D").headline, "bus"), False),
        scene("buses", "F1", _dbus_menu(data["F1"], "F1", None, 92), True),
        scene("device", "F1", _dbus_menu(data["F1"], "F1", "272813EF", 92) + dialog, True),
        scene("blocked", "F2", _dbus_menu(data["F2"], "F2", "272813EF", 92), True),
        scene("now", "F3", _dbus_menu(data["F3"], "F3", None, 92), True),
    ]
    return wrap(scenes)


# ---------------------------------------------------------------- V3 Hub row (Claude Hub popover)
def v3(data) -> str:
    accounts = [("bam", 41), ("personal", 68), ("ambience", 23)]

    def hub(device_views, expanded: str | None, frame: str) -> str:
        attention = [view for view in device_views if view.level in ("error", "warn", "blocked")]
        out = [f'<div class="hub"><div class="hh">Claude Hub<span class="sp">{icon("gear")}<span>⇅</span><span>＋</span></span></div>']
        out += [f'<div class="acct"><span class="dot" style="width:7px;height:7px;border-radius:4px;background:#57c785"></span>{name}'
                f'<span class="bar2"><i style="width:{pct}%"></i></span>{pct}%<span style="color:#8f8c84">resets in 3d</span><span class="ghost">Open</span></div>'
                for name, pct in accounts]
        out.append(f'<div class="sec"><span>IO · {len(attention)} need you</span><span>moteus_servo · intel_rgbd · obsbot_ptz_camera</span></div>')
        for view in device_views:
            is_open = view.key == expanded
            target = "buses" if is_open else ("device" if view.key == "272813EF" and frame == "F1" else "")
            attrs = f' data-story-to="{target}"' if target else ""
            check = "waits" if view.gate == "blocked" else "Check"
            out.append(f'<div class="iorow{" click" if target else ""}{" sel" if is_open else ""}"{attrs}><span class="dot {view.level}"></span>'
                       f'<b style="font-weight:500">{E(view.title)}</b><span class="s">{E(view.headline)}</span>'
                       f'<span class="ghost" style="opacity:.55" title="off this round (passive-only)">{check}</span></div>')
            if is_open:
                kv = "".join(f'<span>{E(label)}</span><span>{E(value)} <span class="src">· {E(source)}</span></span>'
                             for label, value, source in _detail_lines(view, compact=True))
                gate_line = (f'<div class="why">{icon("lock")} Check and reset wait: {E(view.gate_reason)}</div>' if view.gate == "blocked" else
                             '<div class="confirm" data-hint="reset"><b>Reset board…</b> uses the hub\'s Close-style confirm: '
                             'choose CAN 15, confirm once, the hub re-checks holders + rack lock right before sending. '
                             '<span class="btn" data-story-to="buses">Cancel</span><span class="btn off">Reboot CAN 15</span>'
                             '<div class="why">off this round (passive-only)</div></div>')
                out.append(f'<div class="card"><div class="kv">{kv}</div>{gate_line}</div>')
        out.append('<div class="why" style="margin:6px 8px 0">Usage rows illustrative; IO rows from the drivers\' diagnostics.</div></div>')
        return "".join(out)

    def ind(open_: bool) -> str:
        return f'<div class="{"ind open" if open_ else "ind"}" data-story-to="buses">{SVG["ring"]}</div>'

    def scene(state, frame, inner, open_):
        return f'<div class="scene s-{state}">{bar(CLOCK[frame], ind(open_))}{inner}</div>'

    first = EVENTS["F1"][0]
    scenes = [
        scene("plug", "F1", banner("Claude Hub", "IO · plugged", first[2] + " · " + _find(data["F1"], "28B3A25D").headline, "ring"), False),
        scene("buses", "F1", hub(data["F1"], None, "F1"), True),
        scene("device", "F1", hub(data["F1"], "272813EF", "F1"), True),
        scene("blocked", "F2", hub(data["F2"], "272813EF", "F2"), True),
        scene("now", "F3", hub(data["F3"], None, "F3"), True),
    ]
    return wrap(scenes)


# ---------------------------------------------------------------- V4 Registry contract (label + dashboard)
def v4(data) -> str:
    def label(frame: str, ticker: bool = False) -> str:
        device_views = data[frame]
        attention = [view for view in device_views if view.level in ("error", "warn", "blocked")]
        text = "IO + 28B3A25D · 0 of 4" if ticker else f"IO {len(device_views)} · {len(attention)} ⚠"
        return f'<div class="ind" data-story-to="buses">{SVG["busdot"]}<span>{E(text)}</span></div>'

    consumer = {
        "F1": '{"ok": true, "path": "/dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00", "ids": [1, 14, 15]}',
        "F2": '{"ok": false, "why": "busy: open by pid 640623 tview", "path": "/dev/serial/by-id/…272813EF-if00"}',
        "F3": '{"ok": false, "why": "no board answers on 272813EF: 0 of 3 boards answer · motor supply off?"}',
    }

    def window(frame: str, selected: str | None) -> str:
        device_views = data[frame]
        rows = []
        for view in device_views:
            is_sel = view.key == selected
            target = "buses" if is_sel else ("device" if view.key == "272813EF" and frame == "F1" else "")
            attrs = f' data-story-to="{target}"' if target else ""
            rows.append(f'<div class="lrow{" click" if target else ""}{" sel" if is_sel else ""}"{attrs}>{level_icon(view.level) or icon("net" if view.kind == "bus" else "cam")}'
                        f'<span>{E(view.title)}</span><span class="s">{E(view.short)}</span></div>')
        events = "".join(f'<div class="ev">{E(t)} · {E(text)}</div>' for t, _k, text, _tag in EVENTS[frame])
        if selected:
            view = _find(device_views, selected)
            table = "".join(f'<tr><td class="k">{E(label)}</td><td>{E(value)}</td><td class="s">{E(source)}</td></tr>'
                            for label, value, source in _detail_lines(view)[:10])
            pane = (f'<h4>{E(view.title)}</h4><div class="lead">{E(view.headline)}</div><table>{table}</table>'
                    f'<pre data-hint="reset"># rack session runner, before it opens a port\nresolve(registry, "272813EF")\n→ {E(consumer[frame])}</pre>'
                    '<div class="why">Reset is a request to the daemon: confirmed in this window, queued until the port and rack lock are free (off this round).</div>')
        else:
            attention = [view for view in device_views if view.level in ("error", "warn", "blocked")]
            pane = (f'<h4>{len(attention)} need you</h4><div class="lead">registry io-diagnostics/1 · updated {CLOCK[frame]} · $XDG_RUNTIME_DIR/io-diagnostics.json</div>'
                    + "".join(f'<div class="lrow">{level_icon(v.level)}<span>{E(v.title)}: {E(v.headline)}</span></div>' for v in attention)
                    + f'<div style="margin-top:10px;font-size:11px;color:#9aa0a6">Events</div>{events}'
                    f'<pre># the same file, read by any tool\njq \'.devices[] | {{key, headline, gate}}\' io-diagnostics.json</pre>')
        return (f'<div class="win"><div class="tb"><i></i><i></i><i></i><span>IO diagnostics · localhost:8794</span></div>'
                f'<div class="body"><div class="list">{"".join(rows)}</div><div class="pane">{pane}</div></div></div>')

    def scene(state, frame, inner, ticker=False):
        return f'<div class="scene s-{state}">{bar(CLOCK[frame], label(frame, ticker))}{inner}</div>'

    hint = '<div class="banner" style="width:330px;top:44px;left:auto;right:8px;transform:none;background:rgba(40,40,40,.92)"><div class="src">no banner in V4</div>The top-bar label itself is the notice: it reads <b style="display:inline">IO + 28B3A25D · 0 of 4</b> for 10 s, then settles to the count.</div>'
    scenes = [
        scene("plug", "F1", hint, ticker=True),
        scene("buses", "F1", window("F1", None)),
        scene("device", "F1", window("F1", "272813EF")),
        scene("blocked", "F2", window("F2", "272813EF")),
        scene("now", "F3", window("F3", None)),
    ]
    return wrap(scenes)


# ---------------------------------------------------------------- V5 Station board (drift badge)
def v5(data, drift_lines: dict[str, list[str]]) -> str:
    sys.path.insert(0, str(ROOT / "prototypes" / "v5-station"))
    import drift  # noqa: PLC0415

    def badge(frame: str) -> str:
        count = len(drift_lines[frame])
        return f'<div class="ind" data-story-to="buses">{SVG["busdot"]}<span>Rack · {count} drift{"s" if count != 1 else ""}</span></div>'

    def drift_menu(frame: str) -> str:
        rows = [f'<div class="r">One Dof Test Rack · drift from station.toml</div><hr>']
        rows += [f'<div class="r act">■&nbsp; {E(line)}</div>' for line in drift_lines[frame]]
        target = "device" if frame == "F1" else ""
        rows.append(f'<hr><div class="r act"{f" data-story-to={chr(34)}{target}{chr(34)}" if target else ""}>Open station board</div><div class="r act">Quit</div>')
        return f'<div class="dm" style="right:92px;width:420px">{"".join(rows)}</div>'

    def board(frame: str, selected: str) -> str:
        slots, _unassigned = drift.drift(frame)
        cards = []
        for slot in slots:
            cls = "slot" + (" missing" if not slot.present else " drift" if slot.drift else " ok") + (" sel" if slot.name.startswith(selected) else "")
            view = next((v for v in data[frame] if slot.device is not None and v.key == slot.device.serial), None)
            body = (f'<div class="exp">expected · {E(", ".join(slot.drift) or "matches")}</div>' if not slot.present else
                    f'<div>{E(view.short if view else "present")}</div>' + "".join(f'<div class="exp">{E(text)}</div>' for text in slot.drift))
            cards.append(f'<div class="{cls}"><b>{E(slot.name)}</b>{body}</div>')
        sel_view = _find(data[frame], "272813EF")
        golden = ("golden check: postponed, bus busy (tview) · retries when the port is free" if sel_view.gate == "blocked"
                  else "golden check: daily 06:00 when the rack lock is free · last run 06:14 (simulated)")
        table = "".join(f'<tr><td class="k">{E(label)}</td><td>{E(value)}</td><td class="s">{E(source)}</td></tr>'
                        for label, value, source in _detail_lines(sel_view, compact=True)[:7])
        expected = '<tr><td class="k">expected</td><td>ids 1, 14, 15 · fw 1.1.2</td><td class="s">station.toml</td></tr>'
        return (f'<div class="win"><div class="tb"><i></i><i></i><i></i><span>Station board · One Dof Test Rack</span></div>'
                f'<div class="slots">{"".join(cards)}</div><div class="pane" style="padding:0 16px" data-hint="reset">'
                f'<h4>Bus A (GIM43 plate) · {E(sel_view.headline)}</h4><table>{expected}{table}</table>'
                f'<div class="why">{E(golden)}. Reset board… is per slot, confirmed, and waits for the lock (off this round).</div>'
                '<span class="btn" data-story-to="buses">‹ Drift list</span></div></div>')

    def scene(state, frame, inner):
        return f'<div class="scene s-{state}">{bar(CLOCK[frame], badge(frame))}{inner}</div>'

    scenes = [
        scene("plug", "F1", banner("IO diagnostics", "Rack drift · Bus B (big plates)", "28B3A25D plugged: 0 of 4 expected boards answer · bus dead, adapter fine", "bus")),
        scene("buses", "F1", drift_menu("F1")),
        scene("device", "F1", board("F1", "Bus A")),
        scene("blocked", "F2", board("F2", "Bus A")),
        scene("now", "F3", board("F3", "Bus A")),
    ]
    return wrap(scenes)


def wrap(scenes: list[str]) -> str:
    return f'{CSS}<div class="iod"><div class="desk">{"".join(scenes)}{chips()}{TAG}</div></div>'


STORY = {
    "v1": [("plug", "Plug it in", "18:11 · 28B3A25D settles after 4 flaps on 1-7: one GNOME banner and a warning glyph in the system area. Click the system area.", ".s-plug [data-story-to='buses']"),
           ("buses", "Rack IO in Quick Settings", "A Rack IO toggle beside Wired and Wi-Fi; its menu lists each device like Bluetooth's. Click 272813EF.", ".s-buses [data-story-to='device']"),
           ("device", "Diagnostics + confirmed reset", "Ids, firmware 1.1.2 (current, real), bus counters; Reset asks inline and waits for the gate.", ".s-device [data-hint='reset']"),
           ("blocked", "Bus busy", "18:25 · tview holds 272813EF (real holder): checks and reset wait and say who.", ".s-blocked .sub"),
           ("now", "After the restart", "19:02 · 0 of 3 boards (supply off), 28B3A25D gone, the OBSBOT appears via its own driver.", ".s-now .qm")],
}
for _variant in ("v2", "v3", "v4", "v5"):
    STORY[_variant] = list(STORY["v1"])
STORY["v2"][0] = ("plug", "Plug it in", "18:11 · one libnotify banner for 5 enumerations; the tray glyph shows 3. Click the tray item.", ".s-plug [data-story-to='buses']")
STORY["v2"][1] = ("buses", "Recent first", "The menu leads with what changed, then one line per device. Click 272813EF.", ".s-buses [data-story-to='device']")
STORY["v2"][2] = ("device", "Diagnostics + reset dialog", "Text-only rows (DBusMenu cannot style); Reset opens a small confirm dialog.", ".s-device .dlg")
STORY["v2"][3] = ("blocked", "Bus busy", "18:25 · '◌ checks wait: open by pid 640623 tview' inside the submenu.", ".s-blocked .in")
STORY["v2"][4] = ("now", "After the restart", "19:02 · Recent shows the OBSBOT plug and the reboot; rows updated.", ".s-now .dm")
STORY["v3"][0] = ("plug", "Plug it in", "18:11 · the Claude Hub ring gets an amber dot and the hub posts one banner. Click the ring.", ".s-plug [data-story-to='buses']")
STORY["v3"][1] = ("buses", "An IO section in the hub", "Under the account rows, one fixed-height row per device, same grammar. Click 272813EF.", ".s-buses [data-story-to='device']")
STORY["v3"][2] = ("device", "Card with sources", "Every value shows its source; Reset reuses the hub's Close-confirm pattern.", ".s-device [data-hint='reset']")
STORY["v3"][3] = ("blocked", "Bus busy", "18:25 · the row's Check says 'waits'; the card names tview.", ".s-blocked .card")
STORY["v3"][4] = ("now", "After the restart", "19:02 · rows updated; the OBSBOT row appears from its driver's CLI.", ".s-now .hub")
STORY["v4"][0] = ("plug", "The label is the notice", "18:11 · no banner: the top-bar line reads 'IO + 28B3A25D · 0 of 4' for 10 s. Click it.", ".s-plug [data-story-to='buses']")
STORY["v4"][1] = ("buses", "Dashboard opened from the label", "A local page over the registry file: who needs you, events, the file path. Click 272813EF.", ".s-buses [data-story-to='device']")
STORY["v4"][2] = ("device", "What other tools read", "The same record the session runner reads; resolve() returns the by-id path.", ".s-device [data-hint='reset']")
STORY["v4"][3] = ("blocked", "Bus busy", "18:25 · the runner's resolve() refuses: 'busy: open by pid 640623 tview'.", ".s-blocked pre")
STORY["v4"][4] = ("now", "After the restart", "19:02 · resolve() refuses 'no board answers'; OBSBOT listed from its entry point.", ".s-now .list")
STORY["v5"][0] = ("plug", "Drift, not presence", "18:11 · the banner says what is wrong against the station file: Bus B, 0 of 4 expected. Click the badge.", ".s-plug [data-story-to='buses']")
STORY["v5"][1] = ("buses", "Only drift", "The badge menu lists drift lines only. Click 'Open station board'.", ".s-buses [data-story-to='device']")
STORY["v5"][2] = ("device", "Station board", "Slots: expected vs present; Bus A's ids and firmware match, CAN 15's fault is the drift.", ".s-device .slots")
STORY["v5"][3] = ("blocked", "Bus busy", "18:25 · Bus B unplugged reads 'expected, missing'; Bus A's golden check is postponed (tview).", ".s-blocked [data-hint='reset']")
STORY["v5"][4] = ("now", "After the restart", "19:02 · Bus A 0 of 3 (missing 1, 14, 15), Bus B missing, the new PTZ slot matches.", ".s-now .slots")
