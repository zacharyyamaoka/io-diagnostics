"""Today's One Dof Test Rack as v0 records: what the interop proofs publish to stock viewers.

Sources (2026-10-21/22), so nothing here is invented without saying so:
- fdcanusb 272813EF on USB port 1-3, boards CAN 1 / 14 / 15 (moteus-c1, firmware 1.1.2 @ 657b5c5): uuids and
  serials from the rack's deployment.json; 36.5 V, mode 0, fault 0 from rack_inventory's 01:13:16Z snapshot.
- CAN 15 trips fault 33 (motor driver) about 20 ms after its bridge enables, twice on 10-21 (deployment.json note).
- CAN 14 is the stale board: its last reading is ``STALE_FOR_S`` old. On the rack this happens when tview holds
  the adapter and the read-only poller skips (rack_inventory latest.json: "busy ['640623 tview']").
- Two D405s: USB serials 130523071221 (port 6-2) and 219323070276 (port 6-1) are real sysfs reads; their SDK
  serials pairing, stream profiles and firmware numbers are V6's simulated values (a gated read, never run).

Pure functions of the clock: ``statuses(now)`` is deterministic, so the CDR bytes can be compared byte for byte
with what real ROS 2 (rclpy) serializes for the same array.
"""

from __future__ import annotations

from contract.record import (
    ERROR, OK, WARN, DiagnosticStatus, Entity, Measurement,
)

# 2026-10-22 07:00 -07:00: the fixed clock the golden files were captured at.
FIXTURE_NOW = 1_792_677_600.0
STALE_BOARD = 14
# WHY 47 s: past the 5 s floor of a 1 s period (stale) and short enough to read as "just went quiet".
STALE_FOR_S = 47.0
BOARD_PERIOD_S = 1.0  # moteus_servo's diagnostics tick

HOST = "host"
ADAPTER_PORT = "host/usb-1-3"
ADAPTER = "host/usb-1-3/fdcanusb-272813EF"
ADAPTER_ID = "usb:0483:5740:272813EF"  # the repeatable identity: what sysfs shows (record spec, section 4)
BUS = f"{ADAPTER}/can"

# can id -> (controller uuid, controller serial, where it sits) from deployment.json
BOARDS = {
    1: ("32bb145a", "AFQAQ0Y0UBkgNzAy", "GIM43 plate, upper-left joint (tag 0)"),
    14: ("c3414184", "AFoANlQwUBcgOTNO", "GIM43 plate, lower-left joint (tag 1)"),
    15: ("22fb62f0", "ABoAQVQwUBUgOTNO", "GIM43 plate, lower-right joint (tag 2)"),
}
FIRMWARE = "1.1.2 (657b5c5)"
BUS_VOLTAGE_V = 36.5

# usb serial -> (SDK serial, usb port)
CAMERAS = {
    "130523071221": ("218622274411", "6-2"),
    "219323070276": ("218622276036", "6-1"),
}
CAMERA_FIRMWARE, CAMERA_FIRMWARE_LATEST = "5.15.1.55", "5.16.0.1"  # simulated (V6)
OUTDATED_CAMERA = "219323070276"


def node_path(can_id: int) -> str:
    return f"{BUS}/{can_id}"


def node_hardware_id(can_id: int) -> str:
    return f"moteus:{BOARDS[can_id][0]}"


def camera_path(usb_serial: str) -> str:
    return f"host/usb-{CAMERAS[usb_serial][1]}/d405-{usb_serial}"


def camera_label(usb_serial: str) -> str:
    return f"D405 ·{CAMERAS[usb_serial][0][-4:]}"


def entities() -> list[Entity]:
    """What and where each thing is (sent once). Stock viewers never see these: they have no stock form."""

    found = [
        Entity(HOST, "rack PC", "host"),
        Entity(ADAPTER_PORT, "USB 1-3", "port", via=HOST),
        Entity(ADAPTER, "fdcanusb 272813EF", "device", via=ADAPTER_PORT, hardware_id=ADAPTER_ID,
               identity={"manufacturer": "mjbots", "model": "fdcanusb", "serial_number": "272813EF"},
               owner="moteus_servo"),
        Entity(BUS, "CAN bus", "bus", via=ADAPTER, owner="moteus_servo"),
    ]
    for can_id, (uuid, serial, _station) in BOARDS.items():
        found.append(Entity(node_path(can_id), f"CAN {can_id}", "node", via=BUS, hardware_id=node_hardware_id(can_id),
                            identity={"manufacturer": "mjbots", "model": "moteus-c1", "serial_number": serial,
                                      "sw_version": FIRMWARE},
                            owner="moteus_servo"))
        found.append(Entity(f"{node_path(can_id)}/driver", f"CAN {can_id} driver", "part", part_of=node_path(can_id),
                            owner="moteus_servo"))
    for usb_serial, (sdk_serial, port) in CAMERAS.items():
        found.append(Entity(f"host/usb-{port}", f"USB {port}", "port", via=HOST))
        found.append(Entity(camera_path(usb_serial), camera_label(usb_serial), "device", via=f"host/usb-{port}",
                            hardware_id=f"usb:8086:0b5b:{usb_serial}",
                            identity={"manufacturer": "Intel", "model": "RealSense D405", "serial_number": sdk_serial,
                                      "sw_version": CAMERA_FIRMWARE},
                            owner="intel_rgbd"))
    return found


def statuses(now: float, stale_since: float | None = None) -> list[DiagnosticStatus]:
    """The rack's checks as their producers would report them at ``now``.

    Every producer reported at ``now`` except CAN 14, whose last report is from ``stale_since`` (default
    ``now - STALE_FOR_S``). A live demo passes a fixed ``stale_since`` so the stale board keeps ageing.
    """

    stale_since = now - STALE_FOR_S if stale_since is None else stale_since
    fresh = {"stamp": now, "received": now}
    records = [
        DiagnosticStatus(
            OK, "fdcanusb 272813EF: USB", "on port 1-3 as /dev/ttyACM0", ADAPTER_ID,
            values=[("port", "1-3"), ("tty", "/dev/ttyACM0"),
                    ("by-id", "/dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00")],
            path=ADAPTER, source="io_facts.usb", **fresh,
            measurements=[Measurement("link_speed", 12, "Mbit/s")]),
        DiagnosticStatus(
            OK, "CAN bus: state", "error-active, no bus errors", ADAPTER_ID,
            values=[("state", "error-active")], path=BUS, source="moteus_servo",
            expected_period_s=BOARD_PERIOD_S, mode="operational", **fresh,
            measurements=[Measurement("tx_error_count", 0, "1", warn_high=96, error_high=127),
                          Measurement("rx_error_count", 0, "1", warn_high=96, error_high=127)]),
    ]
    for can_id, (_uuid, serial, station) in BOARDS.items():
        clock = {"stamp": stale_since, "received": stale_since} if can_id == STALE_BOARD else fresh
        records.append(DiagnosticStatus(
            OK, f"CAN {can_id}: comms", f"answers · {BUS_VOLTAGE_V} V · stopped", node_hardware_id(can_id),
            values=[("serial", serial), ("firmware", FIRMWARE), ("station", station), ("mode", "0 (stopped)")],
            path=node_path(can_id), source="moteus_servo", expected_period_s=BOARD_PERIOD_S, mode="operational",
            measurements=[Measurement("bus_voltage", BUS_VOLTAGE_V, "V", warn_low=30.0, error_low=24.0,
                                      warn_high=52.0, error_high=56.0)],
            **clock))
    records.append(DiagnosticStatus(
        ERROR, "CAN 15: driver", "fault 33 (motor driver) on bridge enable", node_hardware_id(15),
        values=[("fault", "33"), ("fault_name", "motor driver"),
                ("when", "~20 ms after the bridge enables, zero gains and torque; twice on 2026-10-21")],
        path=f"{node_path(15)}/driver", code="MoteusFault.33", source="moteus_servo",
        expected_period_s=BOARD_PERIOD_S, mode="operational", **fresh))
    for usb_serial, (sdk_serial, port) in CAMERAS.items():
        label, hardware_id, path = camera_label(usb_serial), f"usb:8086:0b5b:{usb_serial}", camera_path(usb_serial)
        records.append(DiagnosticStatus(
            OK, f"{label}: USB", f"USB 3.2 Gen 1 on port {port}", hardware_id,
            values=[("port", port), ("usb_serial", usb_serial), ("sdk_serial", sdk_serial)],
            path=path, source="io_facts.usb", measurements=[Measurement("link_speed", 5000, "Mbit/s")], **fresh))
        if usb_serial == OUTDATED_CAMERA:
            # record spec, section 6: "update available" is a status check: name firmware, code UpdateAvailable, warn
            records.append(DiagnosticStatus(
                WARN, f"{label}: firmware",
                f"firmware {CAMERA_FIRMWARE}, {CAMERA_FIRMWARE_LATEST} recommended", hardware_id,
                values=[("installed_version", CAMERA_FIRMWARE), ("latest_version", CAMERA_FIRMWARE_LATEST)],
                path=path, code="UpdateAvailable", source="intel_rgbd", **fresh))
        else:
            records.append(DiagnosticStatus(
                OK, f"{label}: streams", "depth 848x480 z16 · colour 848x480 rgb8, both 30 Hz (configured)",
                hardware_id, values=[("depth", "848x480 z16 @ 30 Hz"), ("colour", "848x480 rgb8 @ 30 Hz")],
                path=path, source="intel_rgbd", **fresh))
    return records
