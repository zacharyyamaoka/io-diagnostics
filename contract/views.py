"""One generic view per device, for every surface: built from the driver reports + a story frame.

Surfaces render ``DeviceView`` rows without knowing device types (that is fr2: a new driver needs
no UI work). Values come from the drivers' real passive reads; each frame then overlays what the
story needs at that moment, every overlay tagged ``reported`` (measured by the orchestrator today)
or ``simulated`` (invented for the prototype).

Frames (the shared fixture):
  F1 18:11  28B3A25D settles after 4 flaps; 272813EF answers with ids 1, 14, 15; CAN 15 faults on enable
  F2 18:25  tview holds 272813EF: every device-touching check waits, last known shown
  F3 19:02  after the restart: 272813EF has no board answering (motor supply off); 28B3A25D gone;
            an OBSBOT is plugged in and recognised through its own driver's diagnostics
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contract.host import scan  # noqa: E402
from io_facts import demo  # noqa: E402

FRAMES = {
    "F1": {"clock": "18:11", "label": "18:11 · plug", "devices": {"28b3": True, "obsbot": False}},
    "F2": {"clock": "18:25", "label": "18:25 · bus busy", "devices": {"28b3": False, "obsbot": False}},
    "F3": {"clock": "19:02", "label": "19:02 · after restart", "devices": {"28b3": False, "obsbot": True}},
}

EVENTS = {
    "F1": [("18:11", "plugged", "fdcanusb 28B3A25D plugged on 1-5 after 4 flaps on 1-7", "real"),
           ("18:07", "dropped", "D405 ·4411 dropped off USB for 4 s, back on 6-2", "real")],
    "F2": [("18:20", "unplugged", "fdcanusb 28B3A25D unplugged from 1-5", "real"),
           ("18:11", "plugged", "fdcanusb 28B3A25D plugged on 1-5 after 4 flaps on 1-7", "real")],
    "F3": [("19:02", "plugged", "OBSBOT Tiny 4K plugged on 7-1.2", "simulated"),
           ("18:38", "boot", "PC restarted: 272813EF and both D405s back", "real")],
}


@dataclass
class Row:
    label: str
    value: str
    source: str
    level: str = "ok"  # ok / warn / error / blocked / unknown / not run
    tag: str = "real"  # real / reported / simulated


@dataclass
class DeviceView:
    key: str
    title: str  # "fdcanusb 272813EF"
    kind: str  # bus / camera
    driver: str  # "moteus_servo"
    port: str
    headline: str  # the one line a glance needs
    level: str
    short: str = ""  # a few words for narrow rows (Quick Settings, hub rows); headline stays one step away
    ids: list = field(default_factory=list)  # children on a bus: (id, text, level)
    sections: dict[str, list[Row]] = field(default_factory=dict)
    gate: str = "free"  # free / blocked
    gate_reason: str = ""
    actions: list[str] = field(default_factory=list)  # "Run comms check", "Reset…" (confirmed, gated)
    simulated: bool = False


WORST = ["error", "warn", "blocked", "unknown", "not run", "ok"]


def worst(levels) -> str:
    levels = list(levels)
    return next((level for level in WORST if level in levels), "ok")


def local_time(stamp: str) -> str:
    """'2026-10-22T01:21:41Z' -> '18:21' (today) or 'Oct 9 20:02' (other days), in local time."""

    import datetime as dt
    import re

    if not stamp:
        return ""
    when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if when.tzinfo:
        when = when.astimezone().replace(tzinfo=None)
    return f"{when:%H:%M}" if when.date() == dt.date(2026, 10, 21) else f"{when:%b} {when.day} {when:%H:%M}"


def _source(text: str) -> str:
    import re

    text = re.sub(r"rack_inventory (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)", lambda m: f"rack_inventory snapshot {local_time(m[1])}", text)
    return re.sub(r"firmware run (\d{8})T\d+", lambda m: f"firmware tool run {m[1][4:6]}/{m[1][6:8]}", text)


def _rows(report) -> dict[str, list[Row]]:
    sections = {}
    for name, readings in report.sections.items():
        rows = []
        for key, reading in readings.items():
            if key == "_gate":
                continue
            value = reading.value
            if isinstance(value, dict) and "firmware" in value:
                value = f"fw {value['firmware']}" + (" · update available" if value["update_available"] else " · current")
            elif isinstance(value, dict):
                value = ", ".join(f"{k.split('.')[-1]} {v}" for k, v in value.items() if not str(v).startswith("ERR")) or "unread"
            elif isinstance(value, list):
                value = ", ".join(map(str, value)) or "none"
            at = local_time(reading.at) if reading.at and "rack_inventory" not in reading.source else ""
            rows.append(Row(key.replace("_", " "), str(value), _source(reading.source) + (f" · {at}" if at else ""), reading.state))
        sections[name] = rows
    return sections


def views(frame: str) -> list[DeviceView]:
    from contract.prototypes_loader import v2_drivers  # the naming-convention binding; any of the five finds the same drivers

    flags = FRAMES[frame]["devices"]
    devices = demo.demo_devices(include_28b3=flags["28b3"], include_obsbot=flags["obsbot"])
    if not flags["28b3"]:
        devices = [device for device in devices if device.serial != "28B3A25D"]
    result = scan(devices, v2_drivers())
    out = []
    for report in result.reports:
        device = report.device
        view = DeviceView(key=device.serial, title="", kind="bus" if report.driver == "moteus_servo" else "camera",
                          driver=report.driver, port=device.port, headline="", level="ok",
                          sections=_rows(report), gate=report.gate.state, gate_reason=report.gate.reason,
                          simulated=device.simulated)
        if report.driver == "moteus_servo":
            _overlay_bus(view, device, frame)
        elif report.driver == "intel_rgbd":
            _overlay_realsense(view, device, frame)
        else:
            _overlay_obsbot(view, device, frame)
        view.short = view.short or _short(view)
        out.append(view)
    return sorted(out, key=lambda view: (view.kind != "bus", view.title))


def _short(view: DeviceView) -> str:
    if view.gate == "blocked":
        return "busy · tview"
    if view.kind == "bus":
        if view.ids:
            faults = sum(1 for _id, _text, level in view.ids if level == "error")
            return f"ids {','.join(str(entry[0]) for entry in view.ids)}" + (" · 1 fault" if faults == 1 else f" · {faults} faults" if faults else "")
        return "0 of 4 · dead bus" if view.key == "28B3A25D" else "0 of 3 answer"
    if "moved" in view.headline:
        return "moved 28 px"
    if "new" in view.headline:
        return "new"
    return "OK"


def _comms(view: DeviceView, rows: list[Row]) -> None:
    view.sections["comms"] = [row for row in view.sections.get("comms", []) if row.label not in ("link",)] + rows


def _overlay_bus(view: DeviceView, device, frame: str) -> None:
    view.title = f"fdcanusb {device.serial}"
    view.actions = ["Run comms check", "View config", "Reset board…"]
    if device.serial == "272813EF":
        if frame == "F3":
            view.ids = []
            view.headline = "0 of 3 boards answer · motor supply off?"
            view.level = "warn"
            _comms(view, [Row("bus", "no node ACKs since the restart", "reported by the orchestrator, ~19:00", "warn", "reported")])
            view.sections["identity"] = [row for row in view.sections["identity"] if row.label != "ids"] + [
                Row("ids", "none answer (last 1, 14, 15 at 18:21)", "reported ~19:00 + rack_inventory 18:21", "warn", "reported")]
        else:
            view.ids = [(1, "CAN 1 · 36.5 V · idle", "ok"), (14, "CAN 14 · 36.5 V · idle", "ok"),
                        (15, "CAN 15 · fault 33 (motor driver) ~20 ms after bridge enable", "error")]
            # WHY pin the snapshot to the frame: an 18:21 reading must not appear in the 18:11 frame.
            as_of = "18:10" if frame == "F1" else "18:21"
            view.sections["identity"] = [row for row in view.sections["identity"] if row.label != "ids"] + [
                Row("ids", "1, 14, 15", f"rack_inventory snapshot {as_of}")]
            for row in view.sections.get("config", []):  # identical in the 18:10 and 18:21 snapshots (checked)
                row.source = row.source.replace("18:21", as_of)
            view.headline = "ids 1, 14, 15 · CAN 15 fault 33 on enable"
            view.level = "error"
            _comms(view, [Row("bus", "error-active · err 0 warn 0 · tdc 25", "can status, reported 2026-10-21", "ok", "reported"),
                          Row("replies", "3 of 3 boards", "rack_inventory snapshot 18:10", "ok")])
        if frame == "F2":
            view.gate, view.gate_reason = "blocked", "open by pid 640623 tview"
            view.headline = "busy: tview has it open · last known ids 1, 14, 15"
            view.level = "blocked" if view.level != "error" else "error"
            for section in ("config", "comms"):
                view.sections.setdefault(section, []).insert(0, Row("check", "waits: open by pid 640623 tview", "find_port_holders (real, 18:25)", "blocked"))
    else:  # 28B3A25D
        view.headline = "0 of 4 boards answer · bus dead, adapter fine"
        view.level = "error"
        view.ids = []
        _comms(view, [Row("adapter", "answers can status", "reported 2026-10-21", "ok", "reported"),
                      Row("bus", "error-passive · err 1 warn 1 · dlec 7 · tdc 0", "can status, reported 2026-10-21", "error", "reported"),
                      Row("meaning", "nobody ACKs: no powered node on this bus", "reported", "error", "reported")])
        view.sections["identity"] = [row for row in view.sections["identity"] if row.label != "ids"] + [
            Row("ids", "none on 1-126 (4 wired, 1 not powering on)", "rack_inventory 18:13 + reported", "error", "reported")]
        view.sections["identity"].append(Row("/dev/fdcanusb", "points HERE (the newest adapter): scripts that default to it hit the dead bus", "ls -l /dev/fdcanusb 18:11-18:20", "warn"))


def _overlay_realsense(view: DeviceView, device, frame: str) -> None:
    sdk = device.extra.get("sdk_serial", device.serial)
    view.title = f"D405 ·{sdk[-4:]}"
    view.actions = ["Run frame check", "View config", "Hardware reset…"]
    view.headline = "USB 3.2 · OK"
    view.sections.setdefault("versions", []).extend([
        Row("firmware", "5.15.1.55 · recommended 5.16.0.1 · update available", "camera_info (simulated values: not read this round)", "warn", "simulated")])
    if sdk.endswith("4411") and frame in ("F1", "F2"):
        # WHY ok, not warn: a drop that recovered by itself is history (Recent), not something that needs him.
        view.headline = "OK · dropped 4 s at 18:07, recovered"
    if sdk.endswith("6036"):
        view.sections["identity"].append(Row("position", "moved 28 px since calibration", "ArUco tag tracking, reported", "warn", "reported"))
        view.headline = "moved 28 px since calibration"
        view.level = "warn"


def _overlay_obsbot(view: DeviceView, device, frame: str) -> None:
    view.title = "OBSBOT Tiny 4K"
    view.headline = "new · recognised by obsbot_ptz_camera"
    view.level = "ok"
    view.actions = ["Check control latency", "View config", "Reset image controls…"]
