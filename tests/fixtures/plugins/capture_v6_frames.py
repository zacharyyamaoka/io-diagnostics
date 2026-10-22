"""Capture V6's three scenario frames as a replayable fixture: tests/fixtures/plugins/v6_frames.json.

    uv run --project /home/bam/io-diagnostics python /home/bam/io-diagnostics/tests/fixtures/plugins/capture_v6_frames.py

Each frame (F1 18:11 plug, F2 18:25 bus busy, F3 19:02 after restart) becomes two halves, so the host replays it
through the SAME code it runs live:

- the OS side (read by ``io_diagnostics.host.FixtureOs``): the skeleton dicts in lane B's shape (the live
  ``io_facts.skeleton`` dict of every device V6 shows on the same port with the same serial, its tty / video nodes,
  the USB buses; a device that is not plugged in now, 28B3A25D, gets ``usb_device_dict`` over V6's simulated
  descriptors), the kernel log of every boot up to the frame clock, and the holder / lock states V6 recorded;
- the driver side (emitted by ``v6_scenario.py``, a real plugin subprocess): v0 records converted from V6's
  driver rows (fdcanusb -> CAN bus -> nodes -> driver / motor parts; D405 -> streams), with V6's real / recorded /
  reported / simulated provenance kept in every ``source``.

Reads only files, sysfs and the journal (V6's frames.py does the same); opens no device. Machine-specific (it
needs /archive/datasets/bam_rig and the bam_ws deployment record); its output is committed so the replay is not.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
V6 = ROOT / "prototypes" / "v6-converged"
OUT = Path(__file__).resolve().parent / "v6_frames.json"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(V6))

import frames as v6  # noqa: E402  (V6's own frame builder)
from tree import walk  # noqa: E402  (V6's tree module)

from contract.record import IDENTITY_KEYS, Action, DiagnosticStatus, Entity, Event, KeyValue, Measurement, to_dict  # noqa: E402
from io_diagnostics.tree import usb_device_dict  # noqa: E402
from io_facts import journal  # noqa: E402

DRIVER_OF = {"adapter": "moteus_servo", "camera": "intel_rgbd"}


def unix(naive: dt.datetime | None, offset_s: int) -> float | None:
    if naive is None:
        return None
    return naive.replace(tzinfo=dt.timezone(dt.timedelta(seconds=offset_s))).timestamp()


def utc_offset_s(lines: list[str]) -> int:
    for line in lines:
        stamp = line.split(" ", 1)[0]
        try:
            offset = dt.datetime.fromisoformat(stamp).utcoffset()
        except ValueError:
            continue
        if offset is not None:
            return int(offset.total_seconds())
    return int(dt.datetime.now().astimezone().utcoffset().total_seconds())


def lines_until(lines: list[str], until: dt.datetime) -> list[str]:
    """Kernel-log lines up to the frame clock (aware compare), so the replay never shows an event from its future."""

    kept = []
    for line in lines:
        try:
            when = dt.datetime.fromisoformat(line.split(" ", 1)[0])
        except ValueError:
            continue
        if when.replace(tzinfo=None) <= until:
            kept.append(line)
    return kept


def identity(info: dict) -> dict[str, str]:
    return {key: str(info[key]) for key in IDENTITY_KEYS if info.get(key) not in (None, "", "—")}


def kv(rows) -> tuple[KeyValue, ...]:
    """V6 rows ``[key, value, level, source, tag]`` -> string pairs, provenance kept beside the value."""

    out = []
    for row in rows:
        key, value = str(row[0]), str(row[1])
        tag = row[4] if len(row) > 4 else ""
        out.append(KeyValue(key, f"{value} ({tag})" if tag and tag != "real" else value))
    return tuple(out)


def dedupe(pairs) -> tuple[KeyValue, ...]:
    seen, out = set(), []
    for pair in pairs:
        if pair.key not in seen:
            seen.add(pair.key)
            out.append(pair)
    return tuple(out)


def raw_actions(driver_name: str, scope: str) -> tuple[Action, ...]:
    return tuple(action for action in v6.driver_named(driver_name).actions if action.scope == scope)


def gate_states(entity, driver_name: str, scope: str) -> dict[str, list]:
    """V6's evaluated preconditions -> {check: [True | False | None, detail]} for the host-evaluated gate checks."""

    out = {}
    for shown, raw in zip(entity.actions, raw_actions(driver_name, scope)):
        for (check, _text), result in zip(raw.preconditions, shown["preconditions"]):
            if check in ("port_free", "bus_lock_free", "camera_free"):
                out[check] = [{"pass": True, "fail": False, "unknown": None}[result["state"]], result["detail"]]
    return out


def last_poll_of(serial: str, can_id: str, now: dt.datetime) -> dt.datetime | None:
    seen = [stamp for stamp, boards in v6.reads(serial, now) if can_id in boards]
    return seen[-1] if seen else None


def number(text: str) -> float | None:
    found = re.match(r"^\s*(-?\d+(\.\d+)?)", str(text))
    return float(found[1]) if found else None


class Converter:
    """One V6 device subtree -> v0 lines, paths relative to the device ("" = the device)."""

    def __init__(self, frame: v6.Frame, now: dt.datetime, offset_s: int, device, hardware_id: str):
        self.frame, self.now, self.offset_s = frame, now, offset_s
        self.device, self.hardware_id = device, hardware_id
        self.driver = DRIVER_OF[device.icon]
        self.lines: list[dict] = []

    def rel(self, entity) -> str:
        return entity.path[len(self.device.path):].lstrip("/")

    def emit(self, record, *, received: dt.datetime | None = None, icon: str | None = None) -> None:
        line = to_dict(record)
        if received is not None:
            line["received"] = unix(received, self.offset_s)
        if icon:
            line["icon"] = icon
        self.lines.append(line)

    def status(self, path: str, name: str, level: str, message: str, *, code: str = "", values=(), measurements=(),
               period: float | None = None, seen: dt.datetime | None = None, hardware_id: str = "", source: str = "",
               repeating: bool = False) -> None:
        """``seen`` = when the host received this evaluation in the recording (a replay keeps it); ``repeating`` =
        the driver re-reports it every run (PortBusy), so the replay leaves the receive time to the host's clock."""

        record = DiagnosticStatus(level=level, name=name, message=message, hardware_id=hardware_id or self.hardware_id,
                                  values=dedupe(values), path=path, code=code,
                                  stamp=unix(seen, self.offset_s) if seen and not repeating else None,
                                  expected_period_s=period, source=f"{self.driver} · {source}" if source else self.driver,
                                  measurements=tuple(measurements))
        self.emit(record, received=seen if period is not None and not repeating else None)

    def convert(self) -> list[dict]:
        device = self.device
        info = dict(device.identity)
        self.emit(Entity("", device.name, "device", identity=identity(info), owner=self.driver,
                         actions=raw_actions(self.driver, "device")), icon=device.icon)
        if device.icon == "adapter":
            self.adapter(device)
        else:
            self.camera(device)
        for child in device.children:
            if child.kind == "bus":
                self.bus(child)
            elif child.kind == "stream":
                self.stream(child)
        return self.lines

    # --- fdcanusb (moteus_servo) ------------------------------------------------------------------------------
    def adapter(self, device) -> None:
        os_rows = ("plugged", "link speed", "tty", "by-id", "video nodes", "enumerations / hour")
        versions = device.versions
        values = [pair for pair in kv(device.comms) if pair.key not in os_rows]
        values += [KeyValue("driver", f"{versions.get('driver', '?')} ({versions.get('driver_source', '')})".strip()),
                   KeyValue("boards latest firmware", f"{versions.get('boards_latest')} ({versions.get('boards_latest_source', '')})")]
        self.status("", f"{device.name}: adapter", "ok", device.message, values=values, source="sysfs + by-id (real)")
        if device.blocked:  # F2: tview holds the port, so the driver's comms check is "not evaluated, and why"
            since = v6.holder_at(device.name.split()[-1], self.now)[1]
            self.status("", f"{device.name}: comms", "unknown", f"{device.blocked}: checks wait", code="PortBusy",
                        values=[KeyValue("holder", device.blocked)], period=v6.POLL_S, seen=since or self.now,
                        source="rack_inventory skip record (real)", repeating=True)

    def bus(self, bus) -> None:
        path = self.rel(bus)
        serial = self.device.name.split()[-1]
        self.emit(Entity(path, bus.label, "bus", via=""), icon="bus")
        code = "NoAck" if bus.level == "error" else "NoReply" if bus.level == "warn" else ""
        answering, _, expected = bus.count.partition("/")
        measurements = []
        if answering.isdigit():
            measurements.append(Measurement("boards answering", int(answering), "1"))
        if expected.isdigit():
            measurements.append(Measurement("boards expected", int(expected), "1"))
        self.status(path, f"{bus.label}: bus", bus.level, bus.message, code=code, values=kv(bus.comms),
                    measurements=measurements, period=bus.expected_period_s, seen=bus.last_seen, source=bus.source)
        for node in bus.children:
            self.node(node, serial)

    def node(self, node, serial: str) -> None:
        path = self.rel(node)
        can_id = path.rsplit("/", 1)[-1]
        self.emit(Entity(path, node.name, "node", via=self.rel_parent(node), hardware_id=node.hardware_id,
                         identity=identity(node.identity), owner=self.driver, actions=raw_actions(self.driver, "node")), icon="servo")
        code = "NoReply" if node.level in ("warn", "error") else ""
        self.status(path, f"{node.name}: comms", node.level, node.message, code=code, values=kv(node.comms),
                    period=node.expected_period_s, seen=node.last_seen, hardware_id=node.hardware_id, source=node.source)
        versions = node.versions
        update = versions.get("update")
        firmware_values = [KeyValue("installed_version", str(versions.get("installed"))),
                           KeyValue("latest_version", str(versions.get("latest"))), KeyValue("git", str(versions.get("git")))]
        if update:
            self.status(path, f"{node.name}: firmware", "warn", f"{versions.get('installed')} installed, {versions.get('latest')} available",
                        code="UpdateAvailable", values=firmware_values, hardware_id=node.hardware_id,
                        source=f"{versions.get('installed_source')} / {versions.get('latest_source')}")
        else:
            self.status(path, f"{node.name}: firmware", "ok", f"{versions.get('installed')} (latest {versions.get('latest')})",
                        values=firmware_values, hardware_id=node.hardware_id,
                        source=f"{versions.get('installed_source')} / {versions.get('latest_source')}")
        if node.config:
            sources = sorted({row[2] for row in node.config})
            self.status(path, f"{node.name}: config", "ok", f"read-only config · {', '.join(sources)}",
                        values=[KeyValue(row[0], row[1]) for row in node.config], hardware_id=node.hardware_id,
                        source=", ".join(sources))
        polled = last_poll_of(serial, can_id, self.now)
        for part in node.children:
            self.part(part, node, polled)

    def rel_parent(self, entity) -> str:
        return entity.path[len(self.device.path):].lstrip("/").rsplit("/", 1)[0] if "/" in self.rel(entity) else ""

    def part(self, part, node, polled: dt.datetime | None) -> None:
        path = self.rel(part)
        self.emit(Entity(path, part.label or part.name, "part", part_of=self.rel(node)), icon=part.icon)
        # WHY skip a reading polled before this boot: the host restarted with the PC (F3), so it never received it
        stale_boot = polled is not None and polled < v6.BOOT <= self.now
        if part.icon == "chip" and not stale_boot:
            reading = [row for row in node.values if row[0] in ("voltage", "mode", "fault (latched)")]
            voltage = next((number(row[1]) for row in reading if row[0] == "voltage"), None)
            fault = next((number(row[1]) for row in reading if row[0] == "fault (latched)"), None)
            measurements = [Measurement("voltage", voltage, "V")] if voltage is not None else []
            if fault is not None:
                measurements.append(Measurement("fault (latched)", int(fault), "1", warn_high=0))
            reading_level = "warn" if fault else "ok"
            self.status(path, f"{part.label}: reading", reading_level,
                        f"{next((row[1] for row in reading if row[0] == 'voltage'), '—')} · fault {int(fault) if fault is not None else '—'}",
                        code="LatchedFault" if fault else "", values=[pair for pair in kv(reading) if pair.key == "mode"],
                        measurements=measurements, period=v6.POLL_S if polled else None, seen=polled,
                        hardware_id=node.hardware_id, source="rack_inventory poll (real)")
        if part.icon == "chip" and part.level == "error":  # CAN 15: the fault-on-enable is a run record, not a poll
            tripped, _reason = v6.can15_trip()
            extra = [row for row in part.values if row[0] in ("fault on enable", "fault cleared")]
            self.status(path, f"{part.label}: fault on enable", "error", part.message, code="MoteusFault.33",
                        values=kv(extra), seen=tripped, hardware_id=node.hardware_id,
                        source=f"{v6.CAN15_RUN.name} + deployment.json note (recorded)")
        if part.icon != "chip":
            self.status(path, f"{part.label}: mapping", "ok", part.message, values=kv(part.values),
                        hardware_id=node.hardware_id, source="deployment.json (rack record, real)")

    # --- D405 (intel_rgbd) ------------------------------------------------------------------------------------
    def camera(self, device) -> None:
        code = "CameraMoved" if device.level == "warn" else ""
        self.status("", f"{device.name}: camera", device.level, device.message, code=code, values=kv(device.values),
                    source="ArUco tag tracking (reported 2026-10-21)" if code else "sysfs + kernel log (real)")
        versions = device.versions
        values = [KeyValue("installed_version", str(versions.get("installed"))), KeyValue("latest_version", str(versions.get("latest")))]
        self.status("", f"{device.name}: firmware", "warn" if versions.get("update") else "ok",
                    f"{versions.get('installed')} installed, {versions.get('latest')} available (simulated: camera_info is a gated read, not run)",
                    code="UpdateAvailable" if versions.get("update") else "", values=values,
                    source=f"{versions.get('installed_source')} (simulated)")
        if device.config:
            self.status("", f"{device.name}: config", "ok", "configured streams (simulated: read_back() is gated)",
                        values=[KeyValue(row[0], row[1]) for row in device.config], source="RealSenseCamera.read_back (simulated)")

    def stream(self, stream) -> None:
        path = self.rel(stream)
        self.emit(Entity(path, stream.label, "stream", part_of=""), icon="stream")
        rate = stream.rate or {}
        measurements = [Measurement("frame rate", round(rate.get("measured_hz", 0.0), 2), "Hz", warn_low=28.5, error_low=15.0),
                        Measurement("dropped frames", 0, "1", warn_high=0)]
        # no "received" and no stamp: a live publisher, refreshed every run, so the host's receive clock keeps it fresh
        self.lines.append(to_dict(DiagnosticStatus(level="ok", name=f"{stream.label}: rate", message=stream.message,
                                                   hardware_id=self.hardware_id, path=path, expected_period_s=stream.expected_period_s,
                                                   source=f"{self.driver} · simulated frame counter", measurements=tuple(measurements))))


def driver_events(now: dt.datetime, offset_s: int, device_ids: dict[str, str]) -> dict[str, list[dict]]:
    """V6's bus and board events (activity._bus_events) as plugin Events, per device id, paths relative to it."""

    out: dict[str, list[dict]] = {}
    tripped, reason = v6.can15_trip()
    cleared = v6.can15_cleared()
    holder, since = v6.holder_at("272813EF", min(now, v6.SHUTDOWN - dt.timedelta(seconds=1)))
    polled = v6.reads("28B3A25D", now)
    rows = [
        ("272813EF", tripped, "BoardFault", "error", "can/15/driver",
         f"CAN 15 fault 33 (motor driver) ~20 ms after bridge enable · run can15_map_slow aborted ({reason})",
         f"{v6.CAN15_RUN.name} (mtime, recorded)"),
        ("272813EF", cleared, "BoardFaultCleared", "info", "can/15/driver",
         "CAN 15 reads fault 0 again (stopped); it trips again on the next enable", "rack_inventory (real)"),
        ("28B3A25D", dt.datetime(2026, 10, 21, 18, 11, 44), "BusErrorPassive", "warn", "can",
         "28B3A25D bus error-passive: nobody ACKs its frames (err 1 · warn 1 · dlec 7)", "can status by the orchestrator (reported)"),
        ("28B3A25D", polled[0][0] if polled else None, "NoReply", "warn", "can",
         "28B3A25D: 0 of 4 wired boards answer an id scan 1-126", "rack_inventory (real)"),
        ("272813EF", since if holder else None, "PortHeld", "warn", "",
         f"272813EF held by {holder}: polls and checks wait", "rack_inventory skip record (real)"),
        ("272813EF", v6.AFTER_RESTART_REPORT, "NoReply", "warn", "can",
         "272813EF: 0 of 3 boards answer since the restart (motor supply off?)", "can status by the orchestrator (reported)"),
    ]
    for serial, when, code, severity, path, message, source in rows:
        device_id = device_ids.get(serial)
        if device_id is None or when is None or when > now:
            continue
        events = out.setdefault(device_id, [])
        events.append(to_dict(Event(code, unix(when, offset_s), severity, message, path=path,
                                    args={"serial": serial}, seq=len(events) + 1, source=f"moteus_servo · {source}")))
    return out


def tty_function(device, port: str) -> list[dict]:
    """A lane-B-shaped tty node for a device that is not plugged in now (V6's simulated descriptors)."""

    return [{"bus": "tty", "id": f"tty:{port}:1.0", "path": Path(tty).name, "label": tty, "vendor": None, "product": None,
             "serial": None, "parent": f"usb:{port}",
             "attrs": {"kind": "serial", "by_id": list(device.by_id), "simulated": True}} for tty in device.ttys]


def capture() -> dict:
    from io_facts.skeleton import skeleton

    live = [node for node in skeleton() if node["bus"] in ("host", "usb", "tty", "v4l")]
    live_by_id = {node["id"]: node for node in live}
    recorded = v6.RECORDED_LOG.read_text().splitlines()
    this_boot = journal.todays_kernel_log(since=f"{v6.BOOT:%Y-%m-%d %H:%M:%S}")
    offset_s = utc_offset_s(recorded)
    document = {"captured": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "source": "prototypes/v6-converged/frames.py via tests/fixtures/plugins/capture_v6_frames.py", "frames": {}}
    for frame in v6.FRAMES.values():
        now = frame.clock
        root = v6.build(frame.id, now)
        present = v6.present_at(now)
        host = dict(live_by_id["host"])
        host["label"] = v6.HOSTNAME
        host["attrs"] = {key: host["attrs"].get(key) for key in ("kind", "os", "kernel")}
        nodes = [host] + [node for node in live if node["bus"] == "usb" and node["attrs"].get("kind") == "bus"]
        device_ids: dict[str, str] = {}
        for identity_key, event in sorted(present.items(), key=lambda item: item[1].port):
            device, tag = v6.descriptor(identity_key, event)
            device_id = f"usb:{event.port}"
            current = live_by_id.get(device_id)
            if current and (current.get("serial") or "") == (device.serial or "") and current["attrs"].get("vid") == device.vid.lower():
                node = json.loads(json.dumps(current))
                node["attrs"]["plugged_at"] = event.time.isoformat(timespec="seconds")
                nodes.append(node)
                nodes += [json.loads(json.dumps(child)) for child in live if child.get("parent") == device_id and child["bus"] in ("tty", "v4l")]
            else:
                node = usb_device_dict(device, source=f"V6 descriptors ({tag})")
                node["attrs"]["plugged_at"] = event.time.isoformat(timespec="seconds")
                node["parent"] = f"usb:{event.port.rsplit('.', 1)[0]}" if "." in event.port else f"usb:usb{event.port.split('-', 1)[0]}"
                nodes.append(node)
                nodes += tty_function(device, event.port)
            if device.serial:
                device_ids[device.serial] = device_id
        plugin_records: dict[str, list[dict]] = {}
        gates: dict[str, dict] = {}
        for entity in walk(root):
            if entity.kind != "device" or entity.os_only or entity.icon not in DRIVER_OF:
                continue
            port = entity.path.split("/")[-2].removeprefix("usb-").removeprefix("port-")
            device_id = f"usb:{port}"
            hardware_id = next((node["attrs"].get("hardware_id") for node in nodes if node["id"] == device_id), "")
            converter = Converter(frame, now, offset_s, entity, hardware_id)
            plugin_records[device_id] = converter.convert()
            gates[device_id] = gate_states(entity, converter.driver, "device")
            for node in walk(entity):
                if node.kind == "node":
                    gates[device_id].update({check: state for check, state in gate_states(node, converter.driver, "node").items()
                                             if check not in gates[device_id]})
        for device_id, events in driver_events(now, offset_s, device_ids).items():
            plugin_records.setdefault(device_id, []).extend(events)
        boots = [{"start": unix(v6.FIRST_BOOT, offset_s), "end": unix(v6.SHUTDOWN, offset_s), "lines": lines_until(recorded, now)}]
        if now >= v6.BOOT:
            boots.append({"start": unix(v6.BOOT, offset_s), "end": None, "lines": lines_until(this_boot, now)})
        document["frames"][frame.id] = {
            "name": frame.id, "label": frame.label, "story": frame.story, "clock": unix(now, offset_s),
            "clock_local": now.isoformat(), "utc_offset_s": offset_s, "hostname": v6.HOSTNAME,
            "skeleton": nodes, "kernel_boots": boots, "gates": gates, "plugin_records": plugin_records}
    return document


if __name__ == "__main__":
    document = capture()
    OUT.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n")
    for frame in document["frames"].values():
        print(frame["name"], frame["label"], f"{len(frame['skeleton'])} skeleton nodes,",
              f"{sum(len(lines) for lines in frame['plugin_records'].values())} plugin lines,",
              f"{sum(len(boot['lines']) for boot in frame['kernel_boots'])} kernel lines")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
