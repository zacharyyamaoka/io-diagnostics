"""Activity: the event log, per-device timeline strips (rqt_robot_monitor style) and link health.

USB events are the kernel log through ``io_facts.journal``'s coalescer (one notice per flap burst,
"dropped" for a short drop); bus and board events come from the rack's own files or are reported.
The strips are the SAME tree sampled once a minute, so a strip can never disagree with the tree.
"""

from __future__ import annotations

import datetime as dt

import frames  # first: puts the io-diagnostics root on sys.path
from io_facts import journal  # noqa: E402
from tree import level_at, rollup, walk  # noqa: E402

KIND_OF = {"plugged": "plugged", "unplugged": "unplugged", "dropped": "re-enumerated"}


def _usb_notices(now: dt.datetime) -> list[dict]:
    out = []
    for boot, events, tag in ((frames.FIRST_BOOT, frames.recorded_events(), "recorded"), (frames.BOOT, frames.live_events(), "real")):
        if boot > now:
            continue
        events = [event for event in events if event.time <= now]
        notices = journal.replay(events)
        at_boot = [notice for notice in notices if notice.kind == "plugged" and (notice.time - boot).total_seconds() <= 15]
        if at_boot:
            out.append({"time": boot, "kind": "boot", "port": "—", "device": "host",
                        "text": f"PC booted · {len(at_boot)} USB devices enumerated", "tag": tag, "source": "journalctl -k"})
        for notice in notices:
            if notice in at_boot or notice.time > now:
                continue
            product = notice.product.replace("Intel(R) RealSense(TM) Depth Camera ", "RealSense D")
            out.append({"time": notice.time, "kind": KIND_OF[notice.kind], "port": notice.port, "device": notice.identity,
                        "text": journal.Notice(notice.time, notice.kind, notice.identity, notice.port, product,
                                               notice.flaps, notice.gap_s).text(),
                        "tag": tag, "source": "journalctl -k, coalesced (io_facts.journal)"})
    if now >= frames.SHUTDOWN:
        out.append({"time": frames.SHUTDOWN, "kind": "shutdown", "port": "—", "device": "host", "text": "PC shut down for a restart",
                    "tag": "real", "source": "journalctl --list-boots"})
    return out


def _bus_events(now: dt.datetime) -> list[dict]:
    out = []
    tripped, reason = frames.can15_trip()
    if tripped:
        out.append({"time": tripped, "kind": "fault entered", "port": "1-3", "device": "272813EF/15",
                    "text": f"CAN 15 fault 33 (motor driver) ~20 ms after bridge enable · run can15_map_slow aborted ({reason})",
                    "tag": "recorded", "source": f"{frames.CAN15_RUN} (mtime)"})
    cleared = frames.can15_cleared()
    if cleared:
        out.append({"time": cleared, "kind": "fault cleared", "port": "1-3", "device": "272813EF/15",
                    "text": "CAN 15 reads fault 0 again (stopped); it trips again on the next enable",
                    "tag": "real", "source": "rack_inventory: first read after the trip"})
    out.append({"time": dt.datetime(2026, 10, 21, 18, 11, 44), "kind": "error-passive", "port": "1-5", "device": "28B3A25D",
                "text": "28B3A25D bus error-passive: nobody ACKs its frames (err 1 · warn 1 · dlec 7)",
                "tag": "reported", "source": "can status by the orchestrator (time approximate)"})
    polled = frames.reads("28B3A25D", now)
    if polled:
        out.append({"time": polled[0][0], "kind": "no reply", "port": "1-5", "device": "28B3A25D",
                    "text": "28B3A25D: 0 of 4 wired boards answer an id scan 1-126", "tag": "real", "source": "rack_inventory"})
    holder, since = frames.holder_at("272813EF", min(now, frames.SHUTDOWN - dt.timedelta(seconds=1)))
    if holder and since:
        out.append({"time": since, "kind": "port held", "port": "1-3", "device": "272813EF",
                    "text": f"272813EF held by {holder}: polls and checks wait", "tag": "real",
                    "source": "rack_inventory skip record"})
    out.append({"time": frames.AFTER_RESTART_REPORT, "kind": "no reply", "port": "1-3", "device": "272813EF",
                "text": "272813EF: 0 of 3 boards answer since the restart (motor supply off?)", "tag": "reported",
                "source": "can status by the orchestrator ~19:00"})
    return [event for event in out if event["time"] <= now]


def events(now: dt.datetime) -> list[dict]:
    rows = sorted(_usb_notices(now) + _bus_events(now), key=lambda event: event["time"], reverse=True)
    for row in rows:
        row["at"] = row["time"].isoformat(timespec="seconds")
        row["time"] = f"{row['time']:%H:%M:%S}"
    return rows


def strips(frame_id: str, now: dt.datetime, minutes: int = 60) -> list[dict]:
    """One cell per minute for the last hour, per device: the device's rolled-up level at the minute's end."""

    samples = [now - dt.timedelta(minutes=minutes - 1 - index) for index in range(minutes)]
    trees = [frames.build(frame_id, when) for when in samples]
    rows: dict[str, dict] = {}
    host_cells = []
    for index, (when, root) in enumerate(zip(samples, trees)):
        cache: dict = {}
        host_cells.append(rollup(root, when, cache).level if root.children else "absent")
        for entity in walk(root):
            if entity.kind != "device":
                continue
            key = entity.hardware_id or entity.path
            row = rows.setdefault(key, {"name": entity.name, "path": entity.path, "os_only": entity.os_only, "cells": ["absent"] * minutes})
            row["cells"][index] = rollup(entity, when, cache).level
            row["path"] = entity.path
    raw = frames.recorded_events() + frames.live_events()
    for row in rows.values():
        port = row["path"].split("/")[-2].removeprefix("usb-").removeprefix("port-")
        row["marks"] = [index for index, when in enumerate(samples)
                        if any(event.port == port and when - dt.timedelta(minutes=1) < event.time <= when
                               and (event.time - frames.boot_of(event.time)).total_seconds() > 15 for event in raw)]
    ordered = sorted(rows.values(), key=lambda row: (row["os_only"], row["name"]))
    return [{"name": "all IO (host roll-up)", "path": "host", "os_only": False, "cells": host_cells, "marks": []}] + ordered


def link_health(root, now: dt.datetime) -> list[dict]:
    """§5 "the computer diagnoses its own wires": one row per edge, from passive sources."""

    rows = []
    for entity in walk(root):
        if entity.kind == "port":
            device = entity.children[0] if entity.children else None
            speed = next((row for row in entity.comms if row[0] == "link speed"), None)
            enum = entity.comms[0]
            rows.append({"edge": f"{entity.name} → {device.name if device else 'empty'}", "path": entity.path,
                         "kind": "USB", "metric": "enumerations / hour", "value": enum[1], "level": enum[2],
                         "source": enum[3], "tag": enum[4]})
            if device and speed and not device.os_only:
                rows.append({"edge": f"{entity.name} → {device.name}", "path": device.path, "kind": "USB", "metric": "link speed",
                             "value": speed[1], "level": speed[2], "source": speed[3], "tag": speed[4]})
        elif entity.kind == "bus":
            for key in ("state", "error counters", "reply loss"):
                row = next((row for row in entity.comms if row[0] == key), None)
                if row:
                    rows.append({"edge": entity.label, "path": entity.path, "kind": "CAN", "metric": key, "value": row[1],
                                 "level": row[2], "source": row[3], "tag": row[4]})
            rows.append({"edge": entity.label, "path": entity.path, "kind": "CAN", "metric": "last poll",
                         "value": f"{frames.hms(entity.last_seen)} ({'STALE' if level_at(entity, now) == 'stale' else 'fresh'})",
                         "level": "stale" if level_at(entity, now) == "stale" else "ok", "source": entity.source, "tag": entity.tag})
        elif entity.kind == "device" and any(row[0] == "position" for row in entity.values):
            row = next(row for row in entity.values if row[0] == "position")
            rows.append({"edge": f"{entity.name} mount", "path": entity.path, "kind": "mechanical", "metric": "camera pose",
                         "value": row[1], "level": row[2], "source": row[3], "tag": row[4]})
    return rows


def chip(tree_json: dict, recent: list[dict], url: str) -> dict:
    """The top-bar chip, as the SAME model V2's AppIndicator renders (contract/indicator.py Indicator.show)."""

    devices = []

    def visit(node):
        if node["kind"] == "device":
            devices.append(node)
        for child in node["children"]:
            visit(child)

    visit(tree_json)
    attention = [node for node in devices if node["level"] != "ok"]
    label = f"IO {len(attention)} ⚠" if attention else "IO ✓"
    mark = {"error": "■", "warn": "▲", "stale": "◌", "ok": "●"}
    menu = [{"text": f"{len(attention)} need you · {len(devices)} devices" if attention else f"All {len(devices)} devices OK"}, {"sep": True},
            {"text": "Recent"}, *[{"text": f"   {event['time'][:5]}   {event['text']}"} for event in recent[:5]], {"sep": True},
            *[{"text": f"{mark[node['level']]}  {node['name']}  —  {node['message']}"} for node in attention],
            {"sep": True}, {"text": "Open IO diagnostics", "action": f"open:{url}"}, {"text": "Quit", "action": "quit"}]
    return {"icon": "attention" if attention else "ok", "label": label, "label_guide": "IO 00 ⚠", "title": "IO diagnostics",
            "menu": menu, "attention": [{"path": node["path"], "name": node["name"], "level": node["level"], "message": node["message"]}
                                        for node in attention], "devices": len(devices)}
