"""Today's rack as three switchable scenario frames, built into ONE entity tree from two sources.

- **OS skeleton** (passive, always there): host -> USB port -> device, from live sysfs, /dev/serial/by-id
  and the kernel log (``io_facts.usb`` + ``io_facts.journal``). An unknown device is an OS-only row.
- **Driver children**: each driver hangs its own rows under its device (fdcanusb -> CAN bus -> CAN 1/14/15
  -> driver + motor; D405 -> depth + colour). Readings come through the driver-owned contract
  (``contract.host.scan`` over the V2 naming-convention binding); actions are the ones the driver registered.

Every value carries a tag:
- ``real``: read passively now (sysfs, by-id, journalctl -k, /proc) or from the rack's own files
  (rack_inventory's 3-minute read-only poller, deployment.json, firmware run records);
- ``recorded``: today's kernel log as captured in tests/data (journalctl's own lines from the previous boot);
- ``reported``: measured on the hardware by the orchestrator on 2026-10-21 (can status, the 28 px move);
- ``simulated``: invented for the prototype (stream frame rates, D405 firmware numbers, 28B3A25D's USB
  descriptors now that it is unplugged, the USB <-> SDK serial pairing).

Nothing here opens a device: the frame clock is a replay, the reads are files and sysfs.
"""

from __future__ import annotations

import bisect
import datetime as dt
import functools
import json
import math
import re
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from contract.host import scan  # noqa: E402
from contract.prototypes_loader import v2_drivers  # noqa: E402
from io_facts import demo, fixture, journal, usb  # noqa: E402
from io_facts.usb import UsbDevice  # noqa: E402

from tree import Entity  # noqa: E402

RECORDED_LOG = ROOT / "tests" / "data" / "kernel-usb-2026-10-21.log"
INVENTORY_DIR = fixture.INVENTORY_DIR
DEPLOYMENT = fixture.DEPLOYMENT
CAN15_RUN = Path("/archive/datasets/bam_rig/rack_2026-10-21/camera_map/can15_map_slow.json")
# WHY constants: `journalctl --list-boots` (read 2026-10-22) - boot -1 ended 18:37:37, boot 0 began 18:38:30.
SHUTDOWN = dt.datetime(2026, 10, 21, 18, 37, 37)
BOOT = dt.datetime(2026, 10, 21, 18, 38, 30)
FIRST_BOOT = dt.datetime(2026, 10, 21, 6, 14, 39)
# WHY 180 s: rack_inventory's own cadence (measured 181-186 s between snapshots on 10-21/22).
POLL_S = 180.0
STREAM_PERIOD_S = 1.0
# The orchestrator's can status after the restart: no node ACKs on 272813EF (reported, ~19:00).
AFTER_RESTART_REPORT = dt.datetime(2026, 10, 21, 19, 0, 0)
HOSTNAME = socket.gethostname()


@dataclass(frozen=True)
class Frame:
    id: str
    label: str
    clock: dt.datetime
    story: str


FRAMES = {
    "F1": Frame("F1", "18:11 · plug", dt.datetime(2026, 10, 21, 18, 11, 50),
                "28B3A25D settled on 1-5 after 4 flaps on 1-7 and its bus is error-passive (no ACK); 272813EF answers with "
                "ids 1, 14, 15; CAN 15 trips fault 33 on enable; D405 ·4411 dropped 4 s at 18:07; D405 ·6036 moved 28 px."),
    "F2": Frame("F2", "18:25 · bus busy", dt.datetime(2026, 10, 21, 18, 25, 0),
                "tview (pid 640623) has held 272813EF since 18:24:46: rack_inventory skips its poll, every check and button "
                "waits, and the boards' last readings age toward STALE (3 x 3 min after 18:21:41 = 18:30:41)."),
    "F3": Frame("F3", "19:02 · after restart", dt.datetime(2026, 10, 21, 19, 2, 0),
                "The PC restarted at 18:38. 272813EF is back on 1-3 but 0 of 3 boards answer (motor supply off); "
                "28B3A25D is gone. USB tree and kernel log here are this boot's, read live."),
}

FDCANUSB, D405 = ("0483", "5740"), ("8086", "0b5b")
USB_GENERATION = {1.5: "USB 1.1 low-speed", 12: "USB full-speed", 480: "USB 2 high-speed", 5000: "USB 3.2 Gen 1",
                  10000: "USB 3.2 Gen 2"}
EXPECTED_SPEED = {FDCANUSB: 12, D405: 5000}


# ----- small time helpers ---------------------------------------------------------------------------
def hms(when: dt.datetime | None) -> str:
    return f"{when:%H:%M:%S}" if when else "—"


def _local(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def _ttl(seconds: float):
    """Cache a zero-argument read for ``seconds`` (live sysfs and journal reads are cheap, not free)."""

    def wrap(function):
        state = {"at": -1e9, "value": None}

        @functools.wraps(function)
        def cached():
            if time.monotonic() - state["at"] > seconds:
                state["value"], state["at"] = function(), time.monotonic()
            return state["value"]

        return cached

    return wrap


# ----- OS skeleton: live sysfs + the kernel log -----------------------------------------------------
def _sys(port: str, attribute: str) -> str:
    try:
        return (usb.SYSFS_USB / port / attribute).read_text(errors="replace").strip()
    except OSError:
        return ""


@_ttl(2.0)
def live_devices() -> dict[str, UsbDevice]:
    """``{identity: device}`` as sysfs shows them right now (identity = serial, else vid:pid@port)."""

    return {device.serial or f"{device.vid}:{device.pid}@{device.port}": device for device in usb.list_usb_devices()}


@functools.cache
def recorded_events() -> tuple[journal.UsbEvent, ...]:
    return tuple(journal.parse_kernel_log(RECORDED_LOG.read_text().splitlines()))


@_ttl(10.0)
def live_events() -> tuple[journal.UsbEvent, ...]:
    """This boot's USB events, read live from ``journalctl -k`` (passive)."""

    try:
        return tuple(journal.parse_kernel_log(journal.todays_kernel_log(since=f"{BOOT:%Y-%m-%d %H:%M:%S}")))
    except Exception:  # noqa: BLE001 - no journal access = no live events, never a crash
        return ()


def events_for(when: dt.datetime) -> list[journal.UsbEvent]:
    """The raw USB events of the boot that contains ``when``, up to ``when``."""

    events = live_events() if when >= BOOT else recorded_events()
    return [event for event in events if event.time <= when]


def identity_of(event) -> str:
    return journal.Coalescer.identity(event)


def present_at(when: dt.datetime) -> dict[str, journal.UsbEvent]:
    """Devices attached at ``when``: the last raw event per identity is an add. Shutdown clears all."""

    if SHUTDOWN <= when < BOOT:
        return {}
    state: dict[str, journal.UsbEvent] = {}
    for event in events_for(when):
        if event.kind == "add":
            state[identity_of(event)] = event
        else:
            state.pop(identity_of(event), None)
    return state


def boot_of(when: dt.datetime) -> dt.datetime:
    return BOOT if when >= BOOT else FIRST_BOOT


def enumerations(port: str, start: dt.datetime, end: dt.datetime) -> list[journal.UsbEvent]:
    """Adds on ``port`` in [start, end], boot enumeration excluded (that is not a re-enumeration)."""

    boot = boot_of(end)
    return [event for event in events_for(end) if event.kind == "add" and event.port == port
            and start <= event.time and (event.time - boot).total_seconds() > 15]


def descriptor(identity: str, event: journal.UsbEvent) -> tuple[UsbDevice, str]:
    """Descriptors for a present device: live sysfs when it is plugged in now, else the fixture's."""

    live = live_devices().get(identity)
    if live and live.port == event.port:
        return live, "real"
    if identity == "28B3A25D":
        return demo._fdcanusb_28b3(), "simulated"
    return UsbDevice(port=event.port, vid=event.vid, pid=event.pid, manufacturer="", product=event.product,
                     serial=event.serial, speed_mbps=0, devnum=event.devnum, plugged_at=event.time.isoformat()), "recorded"


# ----- the rack's own files ----------------------------------------------------------------------
@functools.cache
def inventory() -> tuple[tuple[dt.datetime, dict], ...]:
    """rack_inventory's snapshots (its 3-minute read-only poller) from 10-21 17:00 on, oldest first."""

    snapshots = []
    for path in sorted(INVENTORY_DIR.glob("2026-10-2[12]T*Z.json")):
        when = _local(path.stem)
        if when >= dt.datetime(2026, 10, 21, 17, 0):
            snapshots.append((when, json.loads(path.read_text()).get("adapters", {})))
    return tuple(snapshots)


def reads(serial: str, when: dt.datetime) -> list[tuple[dt.datetime, dict]]:
    """Successful polls of one adapter up to ``when`` (a skipped poll is not a read)."""

    snapshots = inventory()
    end = bisect.bisect_right([stamp for stamp, _ in snapshots], when)
    return [(stamp, adapters[serial]) for stamp, adapters in snapshots[:end]
            if isinstance(adapters.get(serial), dict) and "skipped" not in adapters[serial]]


def holder_at(serial: str, when: dt.datetime) -> tuple[str | None, dt.datetime | None]:
    """Who held the adapter at ``when``, per rack_inventory's own skip records (and since when)."""

    if when >= BOOT:
        return None, None
    since = None
    holder = None
    for stamp, adapters in inventory():
        if stamp > when:
            break
        value = adapters.get(serial)
        if isinstance(value, dict) and "skipped" in value:
            match = re.search(r"'(\d+) ([^']+)'", value["skipped"])
            holder = f"{match[2]} pid {match[1]}" if match else value["skipped"]
            since = since or stamp
        elif value is not None:
            holder, since = None, None
    return holder, since


@functools.cache
def deployment_boards() -> dict[str, dict[int, dict]]:
    """``{adapter serial: {can id: component}}`` from the rack's deployment record."""

    boards: dict[str, dict[int, dict]] = {}
    if DEPLOYMENT.exists():
        for component in json.loads(DEPLOYMENT.read_text()).get("components", []):
            if component.get("type") == "moteus_servo":
                boards.setdefault(component["adapter_serial"], {})[component["can_id"]] = component
    return boards


@functools.cache
def can15_trip() -> tuple[dt.datetime | None, str]:
    """When CAN 15 last tripped, from the aborted camera_map run's own record (file mtime + reason)."""

    if not CAN15_RUN.exists():
        return None, ""
    return dt.datetime.fromtimestamp(CAN15_RUN.stat().st_mtime).replace(microsecond=0), json.loads(CAN15_RUN.read_text()).get("reason", "")


def can15_cleared() -> dt.datetime | None:
    """First rack_inventory read after the trip that shows CAN 15 with fault 0."""

    tripped, _reason = can15_trip()
    for stamp, adapters in inventory():
        board = adapters.get("272813EF", {}).get("15") if isinstance(adapters.get("272813EF"), dict) else None
        if tripped and stamp > tripped and isinstance(board, dict) and board.get("fault") == 0:
            return stamp
    return None


@_ttl(5.0)
def driver_reports() -> dict[str, object]:
    """``{usb serial: contract Report}`` through the driver-owned contract (V2's naming-convention binding)."""

    devices = [device for device in live_devices().values() if (device.vid, device.pid) in (FDCANUSB, D405)]
    if not any(device.serial == "28B3A25D" for device in devices):
        devices.append(demo._fdcanusb_28b3())
    drivers = v2_drivers()
    return {report.device.serial: report for report in scan(devices, drivers).reports} | {"_drivers": drivers}


def driver_named(name: str):
    return next(driver for driver in driver_reports()["_drivers"] if driver.name == name)


# ----- action evaluation (placeholders: shown, never executed) ------------------------------------
def evaluate_actions(driver, scope: str, checks: dict, fill: dict) -> list[dict]:
    """The driver's registered actions for this row, with each precondition checked passively."""

    out = []
    for action in getattr(driver, "actions", ()):
        if action.scope != scope:
            continue
        results = []
        for check, text in action.preconditions:
            state, detail = checks.get(check, (None, "not evaluated"))
            results.append({"text": text.format(**fill), "state": {True: "pass", False: "fail", None: "unknown"}[state],
                            "detail": detail})
        failing = [result for result, (check, _text) in zip(results, action.preconditions) if result["state"] == "fail"]
        nothing_to_do = [result for result, (check, _text) in zip(results, action.preconditions)
                         if result["state"] == "fail" and check == "update_available"]
        out.append({"name": action.name, "title": action.title, "description": action.description, "danger": action.danger,
                    "scope": action.scope, "kind": action.kind, "confirm": action.confirm.format(**fill),
                    "preconditions": results,
                    "blocked": (f"up to date: {nothing_to_do[0]['detail']}" if nothing_to_do and failing[0] is nothing_to_do[0]
                                else f"blocked: {failing[0]['detail']}" if failing else ""),
                    "driver": driver.name})
    return out


# ----- tree building ---------------------------------------------------------------------------
def _speed(mbps: float) -> str:
    if not mbps:
        return "—"
    name = USB_GENERATION.get(mbps, "")
    rate = f"{mbps / 1000:g} Gb/s" if mbps >= 1000 else f"{mbps:g} Mb/s"
    return f"{name} · {rate}" if name else rate


def _identity(device: UsbDevice, via: str, **extra) -> dict:
    """Home Assistant ``device_info`` fields (manufacturer, model, serial_number, hw/sw version, via_device)."""

    bcd = _sys(device.port, "bcdDevice") if not device.simulated else device.extra.get("bcd_device", "")
    hw = f"bcdDevice {int(bcd[:2]):d}.{bcd[2:]}" if len(bcd) == 4 and bcd.isalnum() else (bcd or "—")
    info = {"manufacturer": device.manufacturer or "—", "model": device.product or "—", "serial_number": device.serial or "—",
            "hw_version": hw, "sw_version": "—", "via_device": via, "usb_id": device.usb_id}
    info.update(extra)
    return info


def build(frame_id: str, now: dt.datetime) -> Entity:
    frame = FRAMES[frame_id]
    present = present_at(now)
    host = Entity("host", HOSTNAME, "host", "host", message="", tag="real",
                  source="socket.gethostname(); boot from journalctl --list-boots",
                  identity={"manufacturer": "—", "model": "rack PC", "serial_number": "—", "hw_version": "—",
                            "sw_version": "Ubuntu 24.04", "via_device": "—", "booted": hms(boot_of(now))})
    if not present:
        host.message, host.level = "powered off (between shutdown 18:37:37 and boot 18:38:30)", "stale"
        return host
    devices = {}
    for identity, event in present.items():
        device, tag = descriptor(identity, event)
        devices[event.port] = (identity, event, device, tag)
    window_start = now - dt.timedelta(hours=1)
    ports = set(devices) | {event.port for event in events_for(now) if event.time >= window_start
                            and (event.time - boot_of(now)).total_seconds() > 15}
    port_entities: dict[str, Entity] = {}
    device_entities: dict[str, Entity] = {}
    for port in sorted(ports, key=lambda name: [int(part) for part in re.split(r"[-.]", name)]):
        parent_port = port.rsplit(".", 1)[0] if "." in port else None
        parent = device_entities.get(parent_port) if parent_port else host
        parent = parent or host
        entity = _port_entity(parent, port, devices.get(port), now)
        port_entities[port] = entity
        parent.children.append(entity)
        if port in devices:
            identity, event, device, tag = devices[port]
            child = _device_entity(entity, frame, identity, event, device, tag, now)
            device_entities[port] = child
            entity.children.append(child)
    for hub in device_entities.values():
        if hub.icon == "hub":
            used = sum(1 for child in hub.children if child.children)
            hub.message = f"hub · {used} port{'s' if used != 1 else ''} in use"
    count = len(devices)
    host.count = f"{count} USB devices"
    host.values = [["USB devices", str(count), "ok", "sysfs + kernel log", "real"],
                   ["boot", f"{boot_of(now):%H:%M:%S}", "ok", "journalctl --list-boots", "real"]]
    return host


def _port_entity(parent: Entity, port: str, present, now: dt.datetime) -> Entity:
    bus = port.split("-")[0]
    path = f"{parent.path}/usb-{port}" if parent.kind == "host" else f"{parent.path}/port-{port}"
    entity = Entity(path, f"Port {port}", "port", "port", label=f"Port {port}", tag="real", source="sysfs + journalctl -k")
    hour = enumerations(port, now - dt.timedelta(hours=1), now)
    controller = _sys(f"usb{bus}", "serial")
    entity.identity = {"manufacturer": "—", "model": f"USB bus {bus} port", "serial_number": "—", "hw_version": "—",
                       "sw_version": "—", "via_device": parent.name, "controller": f"xHCI {controller}" if controller else "—",
                       "sysfs": f"/sys/bus/usb/devices/{port}"}
    flaps = len(hour)
    if present:
        _identity_key, event, device, _tag = present
        entity.count = _speed(device.speed_mbps).split(" · ")[-1]
        entity.message = "1 device"
    else:
        last = max((event for event in events_for(now) if event.port == port), key=lambda event: event.time, default=None)
        entity.count = "empty"
        entity.message = f"empty · last: {last.product} {last.serial} {'left' if last.kind == 'remove' else 'arrived'} {hms(last.time)}" if last else "empty"
    if flaps >= 3:
        entity.level = "warn"
        entity.message = f"{flaps} enumerations in the last hour (flapping) · " + entity.message
    elif flaps and present:
        entity.message = f"{flaps} enumeration{'s' if flaps != 1 else ''} in the last hour"
    entity.comms = [["enumerations / hour", str(flaps), "warn" if flaps >= 3 else "ok", "journalctl -k (boot enumeration excluded)",
                     "recorded" if now < BOOT else "real"]]
    if present:
        entity.comms.append(["link speed", _speed(present[2].speed_mbps), "ok", "sysfs speed", present[3]])
    return entity


def _device_entity(port: Entity, frame: Frame, identity: str, event, device: UsbDevice, tag: str, now: dt.datetime) -> Entity:
    key = (device.vid, device.pid)
    slug = {FDCANUSB: "fdcanusb", D405: "d405"}.get(key) or re.sub(r"[^A-Za-z0-9]+", "-", re.sub(r"\(.*?\)", "", device.product or "device")).strip("-").lower()
    path = f"{port.path}/{slug}-{device.serial or device.port}"
    if key == FDCANUSB:
        return _fdcanusb(path, port, frame, device, tag, event, now)
    if key == D405:
        return _d405(path, port, frame, device, tag, event, now)
    return _os_only(path, port, device, tag, event, now)


def _plug_rows(device: UsbDevice, event, tag: str, now: dt.datetime) -> list:
    boot = boot_of(now)
    at_boot = (event.time - boot).total_seconds() <= 15
    log_tag = "recorded" if now < BOOT else "real"
    rows = [["plugged", f"{hms(event.time)}" + (" (at boot)" if at_boot else ""), "ok", "journalctl -k", log_tag],
            ["link speed", _speed(device.speed_mbps), "ok", "sysfs speed", tag]]
    if device.ttys:
        rows.append(["tty", ", ".join(device.ttys), "ok", "sysfs", tag])
    if device.by_id:
        rows.append(["by-id", device.by_id[0], "ok", "/dev/serial/by-id", tag])
    if device.video_nodes and (device.vid, device.pid) != ("1a40", "0101"):
        rows.append(["video nodes", f"{len(device.video_nodes)} ({device.video_nodes[0]} …)", "ok", "sysfs video4linux", tag])
    return rows


def _os_only(path: str, port: Entity, device: UsbDevice, tag: str, event, now: dt.datetime) -> Entity:
    hub = _sys(device.port, "bDeviceClass") == "09"
    product = device.product or device.usb_id
    icon = "hub" if hub else "webcam" if device.video_nodes else "hid" if re.search(r"mouse|keyboard|K320", product, re.I) else "usb"
    entity = Entity(path, product, icon, "device", message="no driver diagnostics" if not hub else "hub",
                    label=product, count=device.usb_id, tag=tag, source="sysfs + by-id + kernel log", os_only=True,
                    hardware_id=device.serial or device.usb_id)
    entity.identity = _identity(device, port.name)
    entity.comms = _plug_rows(device, event, tag, now)
    if not hub and device.video_nodes:
        entity.message = "no driver diagnostics · UVC camera (presence, port and descriptors only)"
    return entity


def _fdcanusb(path: str, port: Entity, frame: Frame, device: UsbDevice, tag: str, event, now: dt.datetime) -> Entity:
    serial = device.serial
    scenario = fixture.scenario()["adapters"].get(serial, {})
    driver = driver_named("moteus_servo")
    report = driver_reports().get(serial)
    versions = report.sections.get("versions", {}) if report else {}
    latest = versions.get("latest").value if versions.get("latest") else None
    adapter = Entity(path, f"fdcanusb {serial}", "adapter", "device", label=f"fdcanusb {serial}", tag=tag,
                     hardware_id=f"mjbots fdcanusb {serial}", source="sysfs + /dev/serial/by-id; children from moteus_servo")
    adapter.identity = _identity(device, port.name, sw_version="unread (needs a gated read)")
    adapter.versions = {"installed": "—", "latest": "—", "update": None,
                        "driver": versions["driver"].value if "driver" in versions else "moteus_servo ?",
                        "driver_source": versions["driver"].source if "driver" in versions else "",
                        "boards_latest": latest, "boards_latest_source": versions["latest"].source if "latest" in versions else ""}
    adapter.comms = _plug_rows(device, event, tag, now)
    # /dev/fdcanusb follows the NEWEST adapter; on 18:11-18:20 that was the dead bus (fixture, real ls -l)
    if now < BOOT and frame.id == "F1":
        adapter.comms.append(["/dev/fdcanusb", "-> /dev/ttyACM1 = 28B3A25D (newest adapter)", "warn" if serial == "28B3A25D" else "ok",
                              "ls -l /dev/fdcanusb 18:11-18:20", "real"])
    elif now >= BOOT:
        adapter.comms.append(["/dev/fdcanusb", f"-> {usb.generic_fdcanusb_target() or 'absent'}", "ok", "readlink, live", "real"])
    holder, holder_since = holder_at(serial, now)
    gate = report.gate if report else None
    if now >= BOOT and gate and gate.state == "blocked":
        holder = gate.reason.removeprefix("open by ")
    if holder:
        adapter.blocked = f"port held by {holder}" + (f" since {hms(holder_since)}" if holder_since else "")
    expected = deployment_boards().get(serial, {})
    polls = reads(serial, now)
    bus = _can_bus(adapter, scenario, expected, polls, frame, now, serial)
    adapter.children.append(bus)
    adapter.count = f"ids {', '.join(map(str, sorted(expected)))}" if expected else f"0 of {scenario.get('expected_count', '?')}"
    adapter.message = "adapter OK (answers can status)"
    lock_state = (None, "not recorded at this time")
    if now >= BOOT and gate:
        lock_state = (not gate.lock_holders, "rack bus lock free (/proc/locks, live)" if not gate.lock_holders
                      else f"rack bus lock held by pid {gate.lock_holders[0].pid}")
    elif polls and not holder:
        lock_state = (True, f"rack_inventory took it for its {hms(polls[-1][0])} poll")
    port_state = (False, f"port held by {holder}") if holder else (
        (True, "no holder (find_port_holders, live)") if now >= BOOT else
        (True, f"rack_inventory polled it at {hms(polls[-1][0])}") if polls else (None, "no poll recorded yet"))
    checks = {"port_free": port_state, "bus_lock_free": lock_state}
    adapter.actions = evaluate_actions(driver, "device", checks, {"name": adapter.name})
    for can_id in sorted(expected):
        bus.children.append(_node(bus, serial, can_id, expected[can_id], polls, versions, latest, driver, checks, frame, now))
    return adapter


def _can_bus(adapter: Entity, scenario: dict, expected: dict, polls: list, frame: Frame, now: dt.datetime, serial: str) -> Entity:
    reported = scenario.get("bus", {})
    bus = Entity(f"{adapter.path}/can", "CAN bus", "bus", "bus", label=f"{serial} CAN bus", tag="reported",
                 expected_period_s=POLL_S, source="can status (reported 2026-10-21) + rack_inventory polls (real)")
    want = len(expected) or scenario.get("expected_count", 0)
    last = polls[-1] if polls else None
    bus.last_seen = last[0] if last else None
    answering = len(last[1]) if last and now < BOOT else 0
    state = reported.get("state", "unknown")
    counters = f"err {reported.get('err', '?')} · warn {reported.get('warn', '?')}" + (f" · dlec {reported['dlec']}" if "dlec" in reported else "")
    if serial == "28B3A25D":
        bus.level = "error"
        bus.message = f"error-passive · no ACK: no powered node on this bus · 0 of {want} answer"
        if not last:  # before rack_inventory's first poll of it (18:13:16): the orchestrator's can status right after the plug
            bus.last_seen = dt.datetime(2026, 10, 21, 18, 11, 44)
            bus.source = "can status right after the plug (reported; time approximate)"
    elif now >= BOOT:
        bus.level, state = "warn", "no ACK since restart"
        bus.message = f"0 of {want} expected boards answer · motor supply off?"
        bus.last_seen = AFTER_RESTART_REPORT
        bus.source = "can status by the orchestrator ~19:00 (reported)"
    else:
        bus.message = f"{state} · {counters} · {answering} of {want} answer"
    bus.count = f"{answering}/{want}"
    loss = 1 - answering / want if want else 0.0
    bus.errors = counters if serial == "28B3A25D" or now < BOOT else "no ACK"
    bus.comms = [["state", state, bus.level if bus.level != "ok" else "ok", "can status (reported 2026-10-21)", "reported"],
                 ["error counters", counters, "error" if reported.get("err") else "ok", "can status (reported 2026-10-21)", "reported"],
                 ["tdc", str(reported.get("tdc", "—")), "ok", "can status (reported 2026-10-21)", "reported"],
                 ["reply loss", f"{loss:.0%} ({answering} of {want} answered the last poll)", "error" if loss == 1 else "warn" if loss else "ok",
                  f"rack_inventory {hms(last[0])}" if last else "no poll", "real"]]
    if now >= BOOT:
        bus.comms[3] = ["reply loss", f"100% (0 of {want} ACK since the restart)", "warn", "can status ~19:00 (reported)", "reported"]
    if len(polls) >= 2 and now < BOOT:  # after the restart no poll has run: no measured rate
        interval = (polls[-1][0] - polls[-2][0]).total_seconds()
        bus.rate = {"measured_hz": 1 / interval, "expected_hz": 1 / POLL_S, "tag": "real", "source": "interval between the last two rack_inventory polls"}
        bus.comms.append(["poll interval", f"{interval:.0f} s (expected {POLL_S:.0f} s)", "ok", "rack_inventory snapshot times", "real"])
    if adapter.blocked:
        bus.comms.insert(0, ["checks", f"wait: {adapter.blocked}", "warn", "rack_inventory skip record", "real"])
    return bus


def _node(bus: Entity, serial: str, can_id: int, component: dict, polls: list, versions: dict, latest: str | None,
          driver, adapter_checks: dict, frame: Frame, now: dt.datetime) -> Entity:
    path = f"{bus.path}/{can_id}"
    uuid = component.get("controller_uuid", "")
    node = Entity(path, f"CAN {can_id}", "servo", "node", label=f"CAN {can_id}", count=uuid[:8] or str(can_id),
                  hardware_id=f"moteus {uuid} · {component.get('controller_serial', '')}", expected_period_s=POLL_S,
                  source="rack_inventory (real 3-min read-only poller, standing in for moteus_servo's diagnostics entry point)")
    seen = [(stamp, boards[str(can_id)]) for stamp, boards in polls if str(can_id) in boards]
    last = seen[-1] if seen else None
    node.last_seen = last[0] if last else None
    board = last[1] if last else {}
    missed = sum(1 for _stamp, boards in polls[-10:] if str(can_id) not in boards)
    if last:
        node.message = f"answers · {board.get('voltage', '?')} V · {'stopped' if board.get('mode') == 0 else 'mode ' + str(board.get('mode'))}"
    else:
        node.level, node.message = "error", "no reply in any poll"
    reading = versions.get(f"CAN {can_id}")
    firmware = reading.value if reading else {}
    installed = firmware.get("firmware", component.get("firmware", "—"))
    update = bool(firmware.get("update_available")) if firmware else None
    node.firmware, node.update = installed, update
    node.versions = {"installed": installed, "latest": latest or "—", "update": update,
                     "installed_source": reading.source if reading else "deployment.json",
                     "latest_source": versions["latest"].source if "latest" in versions else "",
                     "git": component.get("firmware_git", "—")}
    node.identity = {"manufacturer": "mjbots", "model": "moteus-c1", "serial_number": component.get("controller_serial", "—"),
                     "hw_version": "—", "sw_version": f"{installed} ({component.get('firmware_git', '?')})",
                     "via_device": f"fdcanusb {serial} · CAN bus", "uuid": component.get("controller_uuid_full", "—"),
                     "station": component.get("station", "—")}
    config = component.get("moteus_config", {})
    source = config.get("source", "deployment.json")
    node.config = [[key, "—" if value is None else str(value), source] for key, value in config.items() if key != "source"]
    node.values = [["voltage", f"{board.get('voltage', '—')} V", "ok", f"rack_inventory {hms(last[0])}" if last else "—", "real"],
                   ["mode", str(board.get("mode", "—")) + (" (stopped)" if board.get("mode") == 0 else ""), "ok",
                    f"rack_inventory {hms(last[0])}" if last else "—", "real"],
                   ["fault (latched)", str(board.get("fault", "—")), "ok" if not board.get("fault") else "error",
                    f"rack_inventory {hms(last[0])}" if last else "—", "real"]]
    node.comms = [["reply", "answered the last poll" if last and polls and last[0] == polls[-1][0] else "no reply",
                   "ok" if last and polls and last[0] == polls[-1][0] else "error", "rack_inventory", "real"],
                  ["reply loss", f"{missed} of {min(len(polls), 10)} recent polls missed", "warn" if missed else "ok", "rack_inventory", "real"]]
    if len(seen) >= 2 and now < BOOT:
        interval = (seen[-1][0] - seen[-2][0]).total_seconds()
        node.rate = {"measured_hz": 1 / interval, "expected_hz": 1 / POLL_S, "tag": "real", "source": "rack_inventory poll interval"}
    if now >= BOOT:
        # the driver's after-restart poll is a FRESH record that says "no reply"; the values below keep their 18:21 age
        node.comms[0] = ["reply", "no ACK since the restart (motor supply off?)", "warn", "can status ~19:00 (reported)", "reported"]
        node.level, node.tag, node.last_seen = "warn", "reported", AFTER_RESTART_REPORT
        node.message = f"no reply since the restart · last answered {hms(last[0]) if last else '—'}"
    node.errors = "fault 33 on enable" if can_id == 15 else "—"
    answers = (True, f"CAN {can_id} answered the {hms(last[0])} poll") if last and polls and last[0] == polls[-1][0] and now < BOOT else (
        False, f"CAN {can_id} has not answered since {hms(last[0]) if last else 'boot'}")
    checks = dict(adapter_checks, board_answers=answers,
                  update_available=(update, f"installed {installed}, latest {latest}" if update is not None else "no flash record"))
    node.actions = evaluate_actions(driver, "node", checks, {"id": can_id, "serial": component.get("controller_serial", ""),
                                                             "latest": latest or "?", "name": node.name})
    # driver + motor parts: values inside the node's own record, so they share its staleness (no clock of their own)
    part = Entity(f"{path}/driver", "moteus-c1 driver", "chip", "part", label=f"CAN {can_id} driver",
                  source=node.source, count=f"{board.get('voltage', '—')} V")
    part.values = node.values
    if can_id == 15:
        tripped, reason = can15_trip()
        cleared = can15_cleared()
        part.level, part.tag = "error", "recorded"
        part.message = f"fault 33 (motor driver) on bridge enable · last trip {hms(tripped)}"
        part.errors = "fault 33"
        part.values = node.values + [["fault on enable", f"33 · {fixture.MOTEUS_FAULTS[33]} ({reason or 'twice on 10-21'})", "error",
                                      f"{CAN15_RUN.name} {hms(tripped)} + deployment.json note", "recorded"],
                                     ["fault cleared", f"by {hms(cleared)} (first read with fault 0)" if cleared else "—", "ok",
                                      "rack_inventory", "real"]]
    else:
        part.message = f"{board.get('voltage', '—')} V · fault {board.get('fault', '—')}" if board else "no reading"
    motor = Entity(f"{path}/motor", f"{component.get('motor', 'motor')}", "motor", "part", label=f"CAN {can_id} motor",
                   source="deployment.json (rack record)",
                   count=component.get("gearbox", "").split(" ")[0])
    tags = ", ".join(str(tag["id"]) for tag in component.get("camera_tags", []))
    motor.message = f"{component.get('gearbox', '—')} · output sign {config.get('output_sign', '?')}" + (f" · tag {tags}" if tags else "")
    motor.values = [["gearbox", component.get("gearbox", "—"), "ok", "deployment.json", "real"],
                    ["rotor_to_output_ratio", str(config.get("rotor_to_output_ratio", "—")), "ok", source, "real"],
                    ["output_sign", str(config.get("output_sign", "—")), "ok", source, "real"],
                    ["station", component.get("station", "—"), "ok", "deployment.json", "real"],
                    ["mapping", component.get("mapping_status", "—"), "ok", "deployment.json", "real"]]
    node.children = [part, motor]
    return node


def _d405(path: str, port: Entity, frame: Frame, device: UsbDevice, tag: str, event, now: dt.datetime) -> Entity:
    camera = fixture.scenario()["cameras"].get(device.serial, {})
    sdk = camera.get("sdk_serial", device.serial)
    driver = driver_named("intel_rgbd")
    report = driver_reports().get(device.serial)
    entity = Entity(path, f"D405 ·{sdk[-4:]}", "camera", "device", label=f"D405 ·{sdk[-4:]}", count=sdk, tag=tag,
                    hardware_id=f"RealSense D405 {sdk} (USB {device.serial})", source="sysfs + intel_rgbd diagnostics")
    installed, latest = "5.15.1.55", "5.16.0.1"  # simulated: camera_info is a gated read, not run this round
    entity.firmware, entity.update = installed, True
    driver_version = report.sections.get("versions", {}).get("driver") if report else None
    entity.versions = {"installed": installed, "latest": latest, "update": True, "installed_source": "camera_info (simulated: gated read not run)",
                       "latest_source": "camera_info.recommended_firmware_version (simulated)",
                       "driver": driver_version.value if driver_version else "intel_rgbd ?", "driver_source": driver_version.source if driver_version else "",
                       "tag": "simulated"}
    entity.identity = _identity(device, port.name, model="RealSense D405", serial_number=f"{sdk} (SDK)", usb_serial=device.serial,
                                sw_version=f"{installed} (simulated)")
    entity.comms = _plug_rows(device, event, tag, now)
    entity.comms[1][2] = "warn" if device.speed_mbps and device.speed_mbps < EXPECTED_SPEED[D405] else "ok"
    hour = enumerations(device.port, now - dt.timedelta(hours=1), now)
    entity.comms.append(["enumerations / hour", str(len(hour)), "ok", "journalctl -k", "recorded" if now < BOOT else "real"])
    entity.config = [["depth profile", "848x480 z16 @ 30 Hz (simulated)", "RealSenseCamera.read_back() is gated; not run this round"],
                     ["colour profile", "848x480 rgb8 @ 30 Hz (simulated)", "RealSenseCamera.read_back() is gated; not run this round"],
                     ["sdk serial", f"{sdk} (pairing simulated)", camera.get("source", "fixture")]]
    entity.message = f"{_speed(device.speed_mbps).split(' · ')[0]} · OK"
    if hour:
        gap = next((notice.gap_s for notice in journal.replay(events_for(now)) if notice.kind == "dropped" and notice.identity == device.serial), 0)
        entity.message = f"OK · dropped {gap:.0f} s at {hms(hour[-1].time)}, recovered"
    if camera.get("moved_px"):
        entity.level = "warn"
        entity.message = f"moved {camera['moved_px']} px since calibration"
        entity.values = [["position", f"moved {camera['moved_px']} px since calibration", "warn", "ArUco tag tracking (reported 2026-10-21)", "reported"]]
    entity.errors = f"{len(hour)} re-enum" if hour else "—"
    camera_gate = report.gate if report else None
    camera_state = (None, "camera holder check: not recorded at this time")
    if now >= BOOT and camera_gate:
        camera_state = ({"free": True, "blocked": False}.get(camera_gate.state), camera_gate.reason)
    entity.actions = evaluate_actions(driver, "device", {"camera_free": camera_state, "update_available": (True, f"{installed} < {latest} (simulated)")},
                                      {"name": entity.name, "latest": latest})
    for stream in ("depth", "colour"):
        child = Entity(f"{path}/{stream}", f"{stream} stream", "stream", "stream", label=f"{entity.name} {stream}",
                       expected_period_s=STREAM_PERIOD_S, tag="simulated", source="frame-rate monitor (simulated publisher)")
        # a live publisher: its record is refreshed every period while the device is present
        child.last_seen = now - dt.timedelta(seconds=0.2)
        measured = 30 - 0.25 * (1 + math.sin(now.timestamp() / 7 + len(stream)))
        child.rate = {"measured_hz": measured, "expected_hz": 30.0, "tag": "simulated", "source": "simulated frame counter"}
        child.count = "848x480"
        child.message = f"{measured:.1f} Hz of 30 Hz · 0 dropped"
        child.comms = [["frame rate", f"{measured:.2f} Hz (expected 30 Hz ± 5%)", "ok", "simulated frame counter", "simulated"],
                       ["dropped frames", "0 in the last 10 s", "ok", "simulated frame counter", "simulated"]]
        entity.children.append(child)
    return entity
