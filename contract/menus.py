"""Top-bar menu models for the three AppIndicator directions, all built from contract.views.

V2 leads with events, V4 is one line that opens the dashboard, V5 counts drift against the
station file. Same views, three different glances.
"""

from __future__ import annotations

from contract.views import EVENTS, FRAMES, DeviceView, views

MARK = {"ok": "●", "warn": "▲", "error": "■", "blocked": "◌", "unknown": "○", "not run": "○"}


def _needs_you(device_views: list[DeviceView]) -> list[DeviceView]:
    return [view for view in device_views if view.level in ("error", "warn", "blocked")]


def _detail(view: DeviceView) -> list[dict]:
    rows: list[dict] = [{"text": f"{MARK[level]}  {text}"} for _id, text, level in view.ids]
    if view.ids:
        rows.append({"sep": True})
    picks = {"identity": ("adapter", "usb serial", "serial", "/dev/fdcanusb", "position"), "versions": ("CAN 15", "firmware", "latest", "driver"),
             "comms": ("bus", "adapter", "meaning", "link"), "config": ("CAN 15",)}
    for section, labels in picks.items():
        for row in view.sections.get(section, []):
            if row.label in labels:
                rows.append({"text": f"{MARK.get(row.level, '●')}  {row.label}: {row.value}"})
    if view.gate == "blocked":
        rows.append({"text": f"◌  checks wait: {view.gate_reason}"})
    rows.append({"sep": True})
    for action in view.actions:
        rows.append({"text": action + ("  (waits: port busy)" if view.gate == "blocked" else "  (off this round)"), "enabled": False})
    by_id = next((row.value for row in view.sections.get("identity", []) if row.label == "by id"), "")
    if by_id:
        rows.append({"text": "Copy by-id path", "action": f"copy:{by_id}"})
    return rows


def plug_journal(frame: str) -> dict:
    """V2: what changed first, then one submenu per device."""

    device_views = views(frame)
    attention = _needs_you(device_views)
    menu = [{"text": f"{len(attention)} need you · {len(device_views)} devices" if attention else f"All {len(device_views)} devices OK"},
            {"sep": True}, {"text": "Recent"}]
    menu += [{"text": f"   {time}   {text}"} for time, _kind, text, _tag in EVENTS[frame]]
    menu.append({"sep": True})
    for view in device_views:
        menu.append({"text": f"{MARK[view.level]}  {view.title}  —  {view.headline}", "children": _detail(view)})
    menu += [{"sep": True}, {"text": "Driver diagnostics found: moteus_servo, intel_rgbd, obsbot_ptz_camera"}, {"text": "Quit", "action": "quit"}]
    first = EVENTS[frame][0]
    return {"icon": "attention" if attention else "ok", "label": str(len(attention)) if attention else "", "label_guide": "9",
            "title": "IO diagnostics", "menu": menu,
            "notify": {"title": f"IO diagnostics · {first[1]}", "body": first[2] + _plug_suffix(device_views, first)}}


def _plug_suffix(device_views: list[DeviceView], event) -> str:
    for view in device_views:
        if view.key in event[2] or view.title.split()[-1] in event[2] or view.title in event[2]:
            return f"\n{view.headline}"
    return ""


def registry_label(frame: str) -> dict:
    """V4: one line in the top bar; the menu only opens the dashboard and names the contract."""

    device_views = views(frame)
    attention = _needs_you(device_views)
    label = f"IO {len(device_views)}" + (f" · {len(attention)} ⚠" if attention else " ✓")
    return {"icon": "attention" if attention else "ok", "label": label, "label_guide": "IO 00 · 0 ⚠", "title": "IO diagnostics",
            "menu": [{"text": "Open IO dashboard", "action": "open:/home/bam/io-diagnostics/out/v4-dashboard.html"},
                     {"sep": True},
                     {"text": f"Registry updated {FRAMES[frame]['clock']} · $XDG_RUNTIME_DIR/io-diagnostics.json"},
                     {"text": "Read by: rack session runner, agents (io-diagnostics/1)"},
                     {"sep": True},
                     *[{"text": f"{MARK[view.level]}  {view.title}  —  {view.headline}"} for view in attention],
                     {"sep": True}, {"text": "Quit", "action": "quit"}]}


def station_badge(frame: str, drift_lines: list[str]) -> dict:
    """V5: the badge counts drift from the station file; the menu lists only drift."""

    label = f"Rack · {len(drift_lines)} drift" + ("s" if len(drift_lines) != 1 else "") if drift_lines else "Rack ✓"
    return {"icon": "attention" if drift_lines else "ok", "label": label, "label_guide": "Rack · 00 drifts", "title": "IO diagnostics",
            "menu": [{"text": "One Dof Test Rack · drift from station.toml"}, {"sep": True},
                     *[{"text": f"■  {line}"} for line in drift_lines],
                     {"sep": True}, {"text": "Open station board", "action": "open:/home/bam/io-diagnostics/out/v5-station.html"},
                     {"text": "Quit", "action": "quit"}]}
