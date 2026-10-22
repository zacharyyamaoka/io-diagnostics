"""Build the IO-diagnostics converged-design report: one self-contained HTML file.

    python3 /home/bam/io-diagnostics/docs/build_design_report.py

Inputs (all local, read at build time):
  - Zach's 14 feedback screenshots in ~/zach_brain/Sources/Images/
  - the prior-art survey screenshots in reports/media/survey/
  - the converged mock, if the builder has landed it, in reports/media/converged/{hero,table,activity,action}.png

Output: reports/media/io-diagnostics-design-2026-10-22.html (every image inlined as a WebP data URI).
WHY WebP + a max width: the desktop preview tops out near 1.9 MB for a media page, so every
image is downscaled once here instead of shipping the raw captures (the survey alone is ~8 MB).
"""

from __future__ import annotations

import base64
import html
import io
import sys
from pathlib import Path

from PIL import Image

REPO = Path("/home/bam/io-diagnostics")
SURVEY = REPO / "reports/media/survey"
MOCK_DIR = REPO / "reports/media/converged"
VAULT_IMAGES = Path("/home/bam/zach_brain/Sources/Images")
OUT = REPO / "reports/media/io-diagnostics-design-2026-10-22.html"
VAULT_NOTE = "Projects/BAM Robotics/Notes/IO Diagnostics — Prior Art and Converged Design (2026-10-22).md"

image_bytes: dict[str, int] = {}


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def data_uri(path: Path, max_width: int = 900, quality: int = 62, crop=None, stack=None) -> str:
    """Downscale one capture (optionally a crop, or several crops stacked) to WebP and inline it."""
    if stack:
        parts = []
        base = Image.open(path).convert("RGB")
        for box in stack:
            parts.append(base.crop(box))
        width = max(p.width for p in parts)
        height = sum(p.height for p in parts) + 12 * (len(parts) - 1)
        image = Image.new("RGB", (width, height), (24, 24, 24))
        y = 0
        for part in parts:
            image.paste(part, (0, y))
            y += part.height + 12
    else:
        image = Image.open(path)
        if image.mode in ("P", "RGBA", "LA"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            rgba = image.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
        if crop:
            image = image.crop(crop)
    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, round(image.height * ratio)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=quality, method=6)
    raw = buffer.getvalue()
    image_bytes[f"{path.name}{'#crop' if crop or stack else ''}"] = len(raw)
    return "data:image/webp;base64," + base64.b64encode(raw).decode()


def pasted(stamp: str, **kwargs) -> str:
    return data_uri(VAULT_IMAGES / f"Pasted image {stamp}.png", quality=kwargs.pop("quality", 66), **kwargs)


# --------------------------------------------------------------------------------------------
# Sources (numbered once; every inline citation points into this list)
# --------------------------------------------------------------------------------------------

def obsidian(path: str) -> str:
    from urllib.parse import quote
    return f"obsidian://advanced-uri?vault=zach_brain&filepath={quote(path, safe='')}&openmode=true"


SOURCES = [
    ("Converged design spec, IO diagnostics (orchestrator, 2026-10-22)", "file:///home/bam/io-diagnostics/docs/converged-design-2026-10-22.md"),
    ("Zach, Daily Note Oct 22 2026, “Feedback on Io Diagonistics” (14 screenshots)", obsidian("Sources/Daily Notes/Daily Note - Oct 22 2026.md")),
    ("Survey lane ROS: diagnostics dashboards in the ROS ecosystem (accessed 2026-10-22)", "file:///home/bam/io-diagnostics/reports/media/survey/lane_ros.md"),
    ("Survey lane vendor: actuator, sensor and field-bus tools (accessed 2026-10-22)", "file:///home/bam/io-diagnostics/reports/media/survey/lane_vendor.md"),
    ("Survey lane general: device and health dashboards outside ROS (accessed 2026-10-22)", "file:///home/bam/io-diagnostics/reports/media/survey/lane_general.md"),
    ("Babble round 1 gallery: five top-bar directions (2026-10-21)", "file:///home/bam/io-diagnostics/reports/io-diagnostics-babble-2026-10-21.html"),
    ("DP - Diagonistics (Zach)", obsidian("Sources/Notes/DP - Diagonistics.md")),
    ("MOC - Diagnostics (Zach)", obsidian("Maps & Views/MOC/MOC - Diagnostics.md")),
    ("C - ROS Diagnostics Stack", obsidian("World Model/Chunks & Concepts/C - ROS Diagnostics Stack.md")),
    ("IoT Software Tools That Robotics Should Steal (Deep Research, 2026-07-16)", obsidian("Projects/like keras/IoT Software Tools That Robotics Should Steal (Deep Research, 2026-07-16).md")),
    ("Foxglove vs Rerun — Logging Boundary and Backend Decision (Deep Research, 2026-07-21)", obsidian("Projects/like keras/Foxglove vs Rerun — Logging Boundary and Backend Decision (Deep Research, 2026-07-21).md")),
    ("ROS 2 DiagnosticStatus.msg (common_interfaces, BSD-3)", "https://github.com/ros2/common_interfaces/blob/rolling/diagnostic_msgs/msg/DiagnosticStatus.msg"),
    ("ros/diagnostics: diagnostic_updater + diagnostic_aggregator", "https://github.com/ros/diagnostics"),
    ("Clearpath Cockpit ROS 2 Diagnostics docs", "https://docs.clearpathrobotics.com/docs/ros/cockpit/ros2_diagnostics"),
    ("ros2_medkit (selfpatch)", "https://github.com/selfpatch/ros2_medkit"),
    ("Open Robotics Discourse: What’s missing from ROS 2 diagnostics, and what we built", "https://discourse.openrobotics.org/t/whats-missing-from-ros-2-diagnostics-and-what-we-built/52489/3"),
    ("NVIDIA Isaac ROS Greenwave Monitor (Apache-2.0)", "https://github.com/NVIDIA-ISAAC-ROS/greenwave_monitor"),
    ("Foxglove Diagnostics panels docs", "https://docs.foxglove.dev/docs/visualization/panels/diagnostics"),
    ("Lichtblick (MPL-2.0)", "https://github.com/lichtblick-suite/lichtblick"),
    ("foxglove-sdk on PyPI (MIT)", "https://pypi.org/project/foxglove-sdk/"),
    ("CTRE Phoenix Tuner X: Devices page", "https://v6.docs.ctr-electronics.com/en/stable/docs/tuner/device-list.html"),
    ("DroneCAN GUI Tool (MIT)", "https://github.com/DroneCAN/gui_tool"),
    ("REV Hardware Client: SPARK MAX update", "https://docs.revrobotics.com/rev-hardware-client/ion/spark-max/update"),
    ("RealSense Viewer SW/FW update logic", "https://github.com/realsenseai/librealsense/blob/master/doc/viewer-sw-fw-update.md"),
    ("Home Assistant device registry", "https://developers.home-assistant.io/docs/device_registry_index/"),
    ("Home Assistant update entity", "https://contributors.home-assistant.io/core/entities/update/"),
    ("USB Device Tree Viewer (Uwe Sieber)", "https://www.uwe-sieber.de/usbtreeview_e.html"),
    ("usbview for Linux (GPL-2.0)", "https://github.com/gregkh/usbview"),
    ("Kvaser CanKing", "https://kvaser.com/canking/"),
    ("Linux SocketCAN documentation", "https://docs.kernel.org/networking/can.html"),
    ("Rockwell Automation completes acquisition of Clearpath Robotics (Oct 2, 2023)", "https://www.rockwellautomation.com/en-be/company/news/press-releases/Rockwell-Automation-completes-acquisition-of-autonomous-robotics-leader-Clearpath-Robotics-and-its-industrial-offering-OTTO-Motors.html"),
    ("io_facts live snapshot, rack PC, 2026-10-21 18:29 (real kernel log + holders)", "file:///home/bam/io-diagnostics/out/snapshot-live.json"),
    ("rqt_robot_monitor", "https://github.com/ros-visualization/rqt_robot_monitor"),
    ("ros2 doctor (ros2cli)", "https://github.com/ros2/ros2cli/tree/rolling/ros2doctor"),
    ("C - Board Farm (Permanent Station, Swappable DUT)", obsidian("World Model/Chunks & Concepts/C - Board Farm (Permanent Station, Swappable DUT).md")),
    ("io-diagnostics README: round-1 measured traps", "file:///home/bam/io-diagnostics/README.md"),
]
SRC = {i + 1: s for i, s in enumerate(SOURCES)}


def cite(*numbers: int) -> str:
    links = ",".join(f'<a href="#src-{n}" title="{esc(SRC[n][0])}">{n}</a>' for n in numbers)
    return f'<sup class="cite">{links}</sup>'


# --------------------------------------------------------------------------------------------
# What Zach picked: his own screenshots, his own words (quotes kept verbatim and short)
# --------------------------------------------------------------------------------------------

PICKS = [
    {
        "title": "V4’s window: a list on the left, detail on the right",
        "shots": [("20261022045252", "V4 Registry contract window: device list left, '3 need you' detail right", (245, 47, 1025, 706)),
                  ("20261022050354", "Close-up of V4's list rows")],
        "quote": "a nice simple layout... basically a bar on the left",
        "gave": "The window’s shape: a left column of devices, a search box on top, click a row for detail on the right. Round 1’s V4 dashboard became the frame for everything below.",
    },
    {
        "title": "Clearpath “All Diagnostics”: a tree with icon, status and name",
        "shots": [("20261022045420", "Clearpath All Diagnostics: nested tree with path under each name, Diagnostic Details drawer on the right")],
        "quote": "You have a left side hand.. that is essentially a tree.",
        "gave": "The tree row (status icon · name · path underneath) and the detail drawer’s fields: Path, Hardware ID, Level, Message, Values. You called it “the rockwell dash board”: Clearpath has been part of Rockwell Automation since Oct 2023.",
        "cites": (14, 31),
    },
    {
        "title": "V1’s rows: name, id, count and status in one line, but not in Quick Settings",
        "shots": [("20261022045924", "V1 Quick Settings: Rack IO toggle beside Wired and Wi-Fi with a device list", (0, 38, 447, 516)),
                  ("20261022050407", "Close-up of V1's rows: 272813EF 0 of 3 answer, D405 rows, OBSBOT new")],
        "quote": "I like how we see the kinda top level name, and ID",
        "gave": "The row grammar (icon · name · id or count · status), and one firm “no”: not inside Ubuntu’s built-in menu (“I wouldn’t put it on the built in ubunut thing”). The entry point is a chip of its own.",
    },
    {
        "title": "V3’s button placeholder, and “how do printers do it?”",
        "shots": [("20261022050026", "V3 Hub row card: firmware, driver, config, bus, replies, and a Reset board confirm")],
        "quote": "I think we can have place holders for buttons",
        "gave": "The action plane. A printer driver registers its utilities and the OS dialog only shows them; here each driver registers its buttons and executes them itself.",
    },
    {
        "title": "V2’s chip and Recent list: an activity log",
        "shots": [("20261022050145", "V2 Plug Journal tray: Recent section first, then one line per device", (62, 51, 842, 731))],
        "quote": "interesting idea here with like a activity log",
        "gave": "The activity log (plugged, unplugged, re-enumerated, fault in and out), and the idea you liked most: the computer should be “monitoring the wires” itself.",
    },
    {
        "title": "ros2_medkit: an Entity Tree, and Operations",
        "shots": [("20261022050628", "ros2_medkit entity browser: component tree left, Operations tab with services and a Run button"),
                  ("20261022051437", "ros2_medkit Entity Tree from the Open Robotics Discourse post")],
        "quote": "child parent relations you can do drop down",
        "gave": "Parent/child nesting for many nodes on one bus (“Ok they call it an Entiy Tree”), and the Operations tab: named actions with a Run button, which is the action plane already built.",
        "cites": (15, 16),
    },
    {
        "title": "Greenwave Monitor: a table with measured vs expected frequency",
        "shots": [("20261022050910", "NVIDIA Greenwave Monitor TUI: Topic, Message Type, Frequency, Expected Frequency, Status")],
        "quote": "turn it into more of like a table view for fast high level overview",
        "gave": "Table mode: Clearpath’s nested first column with aligned columns to the right, which should carry more than Message, “stuff like Frequency”: rate measured vs expected, last seen, firmware, errors.",
        "cites": (17,),
    },
    {
        "title": "rqt_robot_monitor: roll-up and a timeline strip",
        "shots": [("20261022051154", "rqt_robot_monitor with Error Device, Warned Device, All devices and the history strip"),
                  ("20261022051225", "ROS 1 Robot Monitor: (E: 1, W: 0) Computers : Error roll-up")],
        "quote": "Yes the roll up view I think important",
        "gave": "Worst-child-wins roll-up, with counts on the parent (“(E: 1, W: 0) Computers”), and the colored history strip that keeps a one-off red tick visible.",
        "cites": (33,),
    },
    {
        "title": "RealSense Viewer: the level of ambition",
        "shots": [("20261022051735", "RealSense Viewer Add Source: D405 (USB3.2) with serial, platform camera (USB2.0)"),
                  ("20261022051750", "RealSense D405 card: USB 3.2 badge, Record, Sync, Info, More")],
        "quote": "this is kinda at the level of like realsense viewer",
        "gave": "The scope line: plug it in, see what it is and how it is connected, a few “simple command controls”, and no robot control. Link speed sits beside the name (USB 3.2 vs USB 2.0).",
        "cites": (24,),
    },
]

# --------------------------------------------------------------------------------------------
# Ten examples worth stealing from (picked from ~50 in the three survey lanes)
# --------------------------------------------------------------------------------------------

TEN = [
    {
        "name": "Clearpath Cockpit ROS 2 Diagnostics", "kind": "ROS-native", "url": "https://github.com/clearpathrobotics/cockpit-ros2-diagnostics",
        "license": "LGPL-2.1 · Linux (Cockpit plugin; needs ROS 2 + foxglove_bridge)",
        "img": ("clearpath-cockpit-ros2-diagnostics.png", {}),
        "layout": [
            "One page of cards: a Timeline of status dots with Pause, then Errors and Warnings cards that read “No Errors” with a green check when clean, then All Diagnostics.",
            "All Diagnostics is one expandable tree (name above its path, Message column); selecting a row opens a details drawer with the key/values. “Generate Capture” zips a support bundle (admin only).",
        ],
        "take": "The tree row and drawer fields as they are, positive empty states, and the capture button as our “copy diagnostics for support” action.",
        "cites": (3, 14),
    },
    {
        "name": "ros2_medkit web UI", "kind": "ROS-native", "url": "https://github.com/selfpatch/ros2_medkit",
        "license": "Apache-2.0 · Linux (ROS 2 gateway; web UI in Docker, “early prototype”)",
        "img": ("ros2-medkit-web-ui-app-detail.png", {"max_width": 1100}),
        "layout": [
            "Left Entity Tree (server › area › component › app) with type pills, ⌘K search and a Logical/Functional toggle; breadcrumb across the top.",
            "Each entity gets tabs with counts: Overview · Data 13 · Operations 7 · Config 35 · Faults. Its bridge maps STALE to CRITICAL: a device that stopped talking outranks one reporting an error.",
        ],
        "take": "The detail panel’s skeleton (values, actions, read-only config, faults, each with a count) and STALE treated as worse than ERROR for hardware.",
        "cites": (3, 15),
    },
    {
        "name": "ROS diagnostics stack + rqt_robot_monitor", "kind": "ROS-native", "url": "https://github.com/ros/diagnostics",
        "license": "BSD-3 · Linux (needs a ROS 2 install)",
        "img": ("ros-diagnostics-aggregator-rqt_robot_monitor.png", {}),
        "layout": [
            "The record: DiagnosticStatus = level (OK / WARN / ERROR / STALE) + name + message + hardware_id + string key/values, stable since ROS 1. The aggregator rolls it into a tree where the parent takes the worst child, and “expected” makes an absent device a warning, not silence.",
            "rqt_robot_monitor stacks Error and Warn panes (leaf items only) above the All devices tree, with a history strip: “Last message received N seconds ago”, Pause.",
        ],
        "take": "The record shape unchanged, worst-child roll-up, “expected but absent”, and the history strip as the cheapest honest view of a flaky link.",
        "cites": (3, 12, 13),
    },
    {
        "name": "Foxglove / Lichtblick Diagnostics panels", "kind": "ROS-native", "url": "https://github.com/lichtblick-suite/lichtblick",
        "license": "Foxglove: proprietary, sign-in · Lichtblick fork: MPL-2.0, Linux desktop / web / Docker",
        "img": ("foxglove-diagnostics-with-detail-panel.webp", {}),
        "layout": [
            "Summary feed on top (minimum-level dropdown, fuzzy “Filter hardware id”, sort by level, pin), Detail below: level-colored title and a zebra key/value table.",
            "A per-panel stale timeout (5 s default) rewrites old entries to STALE on the client. Lichtblick accepts any channel whose schema is diagnostic_msgs/msg/DiagnosticArray, JSON included.",
        ],
        "take": "The zebra key/value table, hardware_id as the search key, and a free second view: emit the same records and Lichtblick renders them with zero UI code (unproven; a 30-minute spike).",
        "cites": (3, 18, 19),
    },
    {
        "name": "CTRE Phoenix Tuner X", "kind": "vendor", "url": "https://v6.docs.ctr-electronics.com/en/stable/docs/tuner/device-list.html",
        "license": "Proprietary freeware · no Linux build (Windows, macOS, Android, iOS)",
        "img": ("ctre-tuner-x.png", {}),
        "layout": [
            "The first screen is “Devices (17)”, as cards or a table: Name | Model | ID | CAN Bus | FirmVer | Status. The border color is the firmware state and is always repeated as a sentence (“A new firmware version is available.”); a duplicate ID is its own red state.",
            "A bulb on every card blinks the device; the footer carries “Latency: 0ms – Jitter: 0ms”.",
        ],
        "take": "Table-mode columns, color always repeated in words, duplicate-ID as a named state, identify on every row. Reference only: no Linux.",
        "cites": (4, 21),
    },
    {
        "name": "DroneCAN GUI Tool", "kind": "vendor", "url": "https://github.com/DroneCAN/gui_tool",
        "license": "MIT · Linux, Windows, macOS (pip)",
        "img": ("dronecan-gui-tool.png", {"stack": [(0, 120, 480, 200), (705, 595, 1336, 1066)], "max_width": 640}),
        "layout": [
            "Passive discovery: every node broadcasts a heartbeat, so it appears on its own. Table: Node ID | Name | Mode | Health | Uptime | vendor code.",
            "Double-click opens the node: software version + CRC, hardware version + unique ID, then Restart and Update firmware right under the identity, then config (Fetch / Store / Erase).",
        ],
        "take": "Presence from a declared heartbeat (our expected_period_s), uptime that resets exposes brown-outs, and identity + actions in one detail panel.",
        "cites": (4, 22),
    },
    {
        "name": "REV Hardware Client", "kind": "vendor", "url": "https://docs.revrobotics.com/rev-hardware-client/ion/spark-max/update",
        "license": "Free (proprietary) · Linux in RHC2 (Ubuntu 24.04, Debian 12+)",
        "img": ("rev-hardware-client.png", {}),
        "layout": [
            "The left tree shows the USB-attached controller (“USB; CAN ID 1”) as the bridge, a “CAN Bus” node under it, and the other controllers on that bus as children.",
            "Out-of-date rows carry a red triangle; Update All and Check for Updates sit on top with “Last check: 10:55 am”; the Update tab shows Current 1.5.0, Latest 1.5.2, Out-of-Date.",
        ],
        "take": "Exactly our fdcanusb › CAN bus › node shape, the Current / Latest block, and the last-check time beside the update button.",
        "cites": (4, 23),
    },
    {
        "name": "Intel RealSense Viewer", "kind": "vendor", "url": "https://github.com/realsenseai/librealsense",
        "license": "Apache-2.0 · Linux and Windows binaries",
        "img": ("realsense-viewer.png", {}),
        "layout": [
            "Devices appear on plug-in; the card title carries the USB generation (“3.2”), then Record · Sync · Info · More.",
            "“Firmware Update Recommended!”: model, serial, Current vs Recommended version, one sentence on why, Install / Dismiss. Bottom bar counts errors, warnings, info.",
        ],
        "take": "Link speed beside the name (it catches a D405 on USB 2), “recommended” rather than “latest”, and Install / Dismiss as the shape of the update action.",
        "cites": (4, 5, 24),
    },
    {
        "name": "Home Assistant device page + registry", "kind": "general", "url": "https://developers.home-assistant.io/docs/device_registry_index/",
        "license": "Apache-2.0 · Linux (Python)",
        "img": ("home-assistant-device-page.png", {"crop": (0, 0, 907, 640)}),
        "layout": [
            "A small Device info card (model, maker, “Connected via Zigbee Coordinator”), readings first, then a separate Diagnostic card with an Identify button; noisy diagnostics are off by default.",
            "Underneath: the device registry (manufacturer, model, serial_number, sw_version, hw_version, via_device) and the Update entity (installed_version, latest_version, release_url, install).",
        ],
        "take": "The identity schema word for word, via_device as the tree edge, the update entity as the update record, and button entities as the shape of a registered action.",
        "cites": (5, 25, 26),
    },
    {
        "name": "USB Device Tree Viewer (and Linux usbview)", "kind": "general", "url": "https://www.uwe-sieber.de/usbtreeview_e.html",
        "license": "Windows freeware · Linux counterpart usbview, GPL-2.0, GTK",
        "img": ("usb-device-tree-viewer.png", {}),
        "layout": [
            "My Computer › host controller › root hub › port › device, each node resolved to a human label (COM port, product name).",
            "A failed device gets a numbered problem badge (43) and a generic VID_0000&PID_0002; the detail pane shows the port chain, connection speed and a missing-serial warning.",
        ],
        "take": "The OS half of our tree: USB topology from sysfs, the port chain as “where is it plugged in”, and link speed plus enumeration failures as wire diagnostics.",
        "cites": (5, 27, 28),
    },
]

ALSO = [
    # (group, name, url, licence / platform, takeaway)
    ("ROS", "rqt_runtime_monitor", "https://github.com/ros-visualization/rqt_runtime_monitor", "BSD-3 · ROS 2, Qt", "Four severity buckets with counts; a real two-column key/value detail table."),
    ("ROS", "rqt_robot_dashboard", "https://github.com/ros-visualization/rqt_robot_dashboard", "BSD · ROS, legacy", "Oldest top-bar-chip design: color = worst level, grey = stale, badge shape as a second channel."),
    ("ROS", "rosboard", "https://github.com/dheera/rosboard", "BSD-3 · ROS", "Mobile-first card per topic; warns by example: device strings must be escaped."),
    ("ROS", "ros2 doctor", "https://github.com/ros2/ros2cli/tree/rolling/ros2doctor", "Apache-2.0 · ROS 2", "Checks vs reports; one verdict line; “update available” is a WARN, never an ERROR."),
    ("ROS", "Husarion WebUI", "https://snapcraft.io/husarion-webui", "Apache-2.0 snap · ROS 2", "A vendor reused Foxglove with a per-robot layout file instead of building a viewer."),
    ("ROS", "TurtleBot 4 diagnostics", "https://github.com/turtlebot/turtlebot4_robot", "Apache-2.0 · ROS 2", "Stock publisher + stock rqt_robot_monitor; nothing custom."),
    ("ROS", "Webviz (Cruise)", "https://github.com/cruise-automation/webviz", "Apache-2.0 · abandoned 2022", "Origin of the Summary/Detail panels: Pinned / Stale / Error / Warn / Ok sections."),
    ("ROS", "diagnostic_remote_logging", "https://github.com/ros/diagnostics/tree/ros2/diagnostic_remote_logging", "BSD-3 · ROS 2", "Long-term history goes to InfluxDB/Grafana, not the live panel."),
    ("vendor", "mjbots moteus tview / moteus_tool", "https://github.com/mjbots/moteus", "Apache-2.0 · Linux", "--info JSON is a ready identity record; flash = read version, back up config, flash, restore. No update check; fault is a bare integer."),
    ("vendor", "ODrive GUI / odrivetool", "https://docs.odriverobotics.com/v/latest/interfaces/gui.html", "Free hosted WebUSB app · Chromium on Linux", "Card: model + S/N + “Firmware 0.6.9 – available: 0.6.9-1”; the update screen says what it will erase."),
    ("vendor", "DYNAMIXEL Wizard 2.0", "https://emanual.robotis.com/docs/en/software/dynamixel/dynamixel_wizard2/", "Freeware · Ubuntu 22.04 / 24.04", "Tree port › baud › model › ID; ID Inspection with blink-to-confirm; comms-error classes in a bottom strip."),
    ("vendor", "maxon EPOS Studio", "https://support.maxongroup.com/hc/en-us/articles/360011546880-EPOS4-IDX-How-to-get-the-firmware-version", "Free · Windows", "Topology LocalHost › USB › node › CAN; Reset Node and Clear Errors as per-node verbs."),
    ("vendor", "CubeMars tool", "https://www.cubemars.com/images/file/20250207/1738921575887499.pdf", "Freeware · Windows", "Status card spells out versions, bus volts, temperatures, mode and “no fault” in words."),
    ("vendor", "MyActuator GUI 2.1", "https://cdn.robotshop.com/media/m/mya/rbc-mya-04/pdf/myactuator_gui_debugging_software_manual.pdf", "Freeware · Windows", "Three-field status bar: Device Version | Motor State | Fault Code; a documented bit-flag fault table."),
    ("vendor", "SteadyWin GIM", "https://hackaday.io/project/202366-the-unnamed-spider-bot/log/239239-steadywin-gim6010-8-vs-xiaomi-cybergear-micromotor", "No official tool found", "Nothing to borrow; ODrive-compatible drivers use the ODrive tools."),
    ("vendor", "Stereolabs ZED Diagnostic", "https://docs.stereolabs.com/docs/development/zed-tools/zed-diagnostics", "Free with SDK · Ubuntu", "Checklist starts with the host (driver, CUDA, GPU); each failed row says what to try; SAVE writes JSON for support."),
    ("vendor", "PEAK PCAN-View", "https://www.peak-system.com/PCAN-View.242.0.html", "Free · Windows (Linux ncurses with PEAK driver)", "One-line status bar: link, bit rate, controller status, overruns; bus-load gauge with mean and max."),
    ("vendor", "Kvaser CanKing 7", "https://kvaser.com/canking/", "Free · Windows, Linux (Kvaser drivers)", "Bus Statistics widget = the comms-check panel (shown in the wires section)."),
    ("vendor", "SavvyCAN", "https://github.com/collin80/SavvyCAN", "MIT · Linux AppImage", "Frames per second as a cheap liveness number; frame-level is the wrong altitude here."),
    ("vendor", "CANable / SocketCAN counters", "https://docs.kernel.org/networking/can.html", "Linux kernel", "ERROR-ACTIVE / WARNING / PASSIVE / BUS-OFF plus error counters: the vendor-neutral bus vocabulary."),
    ("vendor", "TwinCAT / IgH EtherCAT", "https://infosys.beckhoff.com/content/1033/tc3_io_intro/1345269771.html", "Windows / GPLv2 Linux", "Requested vs current state; one line per device with a separate error flag; per-port carrier."),
    ("vendor", "VESC Tool", "https://github.com/vedderb/vesc_tool", "GPL-3.0 · Linux", "Not profiled (no README screenshot); CAN scan per forum reports."),
    ("general", "Viam app", "https://docs.viam.com/monitor/monitor/", "SaaS; viam-server AGPL-3.0", "Verdict chip with a fraction (“0 / 2 parts live”) opening an identity dropdown; network check “ran N ago”."),
    ("general", "Formant", "https://docs.formant.io/docs/getting-started-device-sidebar", "Commercial SaaS + Linux agent", "Print the ratio on the parent (“0/73 devices online”); small multiples of one metric across devices."),
    ("general", "InOrbit Control", "https://www.inorbit.ai/docs", "Commercial SaaS", "Checks × devices grid; OK is blue so “fine” is quiet; trouble sorts left."),
    ("general", "Freedom Robotics", "https://docs.freedomrobotics.ai/docs/the-system-tab.md", "Commercial SaaS (status uncertain)", "One shared time bar with a LIVE chip; a deliberate “No alerts” empty state."),
    ("general", "Transitive health monitoring", "https://www.transitiverobotics.com/caps/transitive-robotics/health-monitoring", "Apache-2.0 framework", "Row format `[pill] subsystem: message` expanding in place; zero-config ping monitor."),
    ("general", "NVIDIA jtop", "https://github.com/rbonghi/jetson_stats", "AGPL-3.0 · Linux (Jetson)", "Identity and versions before any number; one daemon serving a TUI and a Python API."),
    ("general", "Netdata", "https://github.com/netdata/netdata", "GPL-3.0+ agent", "Live / Stale / Offline; a one-way plugin protocol with declared Functions as actions."),
    ("general", "Grafana Node Exporter Full", "https://grafana.com/grafana/dashboards/1860-node-exporter-full/", "Apache-2.0", "Each collector reports its own success and duration; collapsed rows say “(8 panels)”."),
    ("general", "GNOME Firmware / fwupd", "https://github.com/fwupd/fwupd", "LGPL-2.1 · Linux", "Parent/child device list, “No Releases Available”; record-and-replay device emulation for plugin tests."),
    ("general", "macOS System Information", "https://support.apple.com/guide/mac-help/get-system-information-about-your-mac-syspr35536/mac", "macOS", "Show More / Show Less depth switch; one saved report for support."),
    ("general", "iStat Menus", "https://bjango.com/mac/istatmenus/", "Commercial · macOS", "Threshold-for-duration rules so a blip does not alert."),
    ("general", "Stats (exelban)", "https://github.com/exelban/stats", "MIT · macOS", "One popup skeleton for every module; connectivity grid, latency, jitter."),
    ("general", "GNOME Vitals", "https://github.com/corecoding/Vitals", "GPL-2.0 · GNOME", "Category row = label + value + chevron in a 300 px dropdown; pin a reading into the bar."),
    ("general", "Indicator-SysMonitor", "https://github.com/fossfreedom/indicator-sysmonitor", "GPL-3.0 · Linux", "Template string in the bar; any shell command as a sensor."),
    ("general", "Ignition Gateway Status", "https://docs.inductiveautomation.com/docs/8.1/platform/gateway/status", "Commercial", "Neutral tiles with “1 / 2 connected”, one red outline for the broken one; Generate Diagnostic Bundle."),
    ("general", "Arduino IDE 2 / arduino-cli / PlatformIO", "https://arduino.github.io/arduino-cli/1.5/pluggable-discovery-specification/", "AGPL / GPL / Apache · Linux", "Pluggable discovery: HELLO / LIST / START_SYNC with add/remove events; match a board only when unambiguous."),
    ("general", "Particle Console", "https://docs.particle.io/getting-started/console/device-vitals/", "Commercial cloud", "“Last vitals N ago” + refresh; a layered device › link › service health check."),
    ("general", "balenaCloud device statuses", "https://docs.balena.io/learn/manage/device-statuses", "SaaS; openBalena AGPL-3.0", "Two independent link columns explain a degraded state; “N minutes in this state”."),
    ("general", "Uptime Kuma", "https://github.com/louislam/uptime-kuma", "MIT · self-hosted", "A banner sentence and a heartbeat strip per service."),
    ("general", "Klipper status reference", "https://www.klipper3d.org/Status_Reference.html", "Licence not checked", "CAN bus_state active / warn / passive / off plus rx_error, tx_error, tx_retries per MCU."),
]

# --------------------------------------------------------------------------------------------
# Diagrams (hand-authored inline SVG; currentColor + page tokens so both themes work)
# --------------------------------------------------------------------------------------------


def svg_text(x, y, text, cls="", size=12, anchor="start", weight=None):
    weight_attr = f' font-weight="{weight}"' if weight else ""
    return f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" class="{cls}"{weight_attr}>{esc(text)}</text>'


ARROW_DEFS = """<defs>
<marker id="{id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="d-arrowhead"/></marker>
<marker id="{id}-acc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="d-arrowhead-acc"/></marker>
</defs>"""


def status_dot(x, y, level):
    cls = {"ok": "d-ok", "warn": "d-warn", "err": "d-err", "stale": "d-stale"}[level]
    if level == "warn":
        return f'<polygon points="{x},{y-5} {x+5},{y+4} {x-5},{y+4}" class="{cls}"/>'
    if level == "err":
        return f'<rect x="{x-4.5}" y="{y-4.5}" width="9" height="9" rx="1.5" class="{cls}"/>'
    if level == "stale":
        return f'<circle cx="{x}" cy="{y}" r="4.5" class="d-stale-ring"/>'
    return f'<circle cx="{x}" cy="{y}" r="4.5" class="{cls}"/>'


def badge(x, y, n):
    return f'<circle cx="{x}" cy="{y}" r="9" class="d-badge"/>' + svg_text(x, y + 4, str(n), "d-badge-text", 11, "middle", 700)


def svg_layout() -> str:
    parts = ['<svg viewBox="0 0 880 440" role="img" aria-label="Layout: a top-bar chip with a Recent list opens one window with an entity tree on the left, detail on the right and an activity log underneath; the same tree flips to a table with aligned columns.">',
             ARROW_DEFS.format(id="a1")]
    # top bar
    parts.append('<rect x="20" y="8" width="840" height="28" rx="6" class="d-bar"/>')
    parts.append(svg_text(440, 27, "Wed Oct 22  09:41", "d-mute", 11, "middle"))
    parts.append('<rect x="716" y="12" width="86" height="20" rx="10" class="d-chip"/>')
    parts.append(svg_text(759, 26, "IO  3 ⚠", "d-acc-text", 12, "middle", 700))
    parts.append(badge(822, 22, 1))
    # dropdown
    parts.append('<rect x="604" y="42" width="256" height="122" rx="8" class="d-panel"/>')
    parts.append(svg_text(618, 62, "3 need you · 5 devices", "", 12, "start", 700))
    parts.append(svg_text(618, 82, "RECENT", "d-mute", 10))
    parts.append(svg_text(618, 100, "18:11  28B3A25D plugged on 1-5 (4 flaps)", "", 11))
    parts.append(svg_text(618, 117, "18:07  D405 …4411 dropped 4 s, back", "", 11))
    parts.append('<line x1="614" y1="128" x2="850" y2="128" class="d-rule"/>')
    parts.append(svg_text(618, 150, "Open IO diagnostics", "d-acc-text", 12, "start", 600))
    parts.append('<path d="M604,146 L586,146" class="d-line-acc" marker-end="url(#a1-acc)"/>')
    # window
    parts.append('<rect x="20" y="48" width="562" height="384" rx="8" class="d-panel"/>')
    parts.append('<line x1="20" y1="72" x2="582" y2="72" class="d-rule"/>')
    for i, cx in enumerate((34, 46, 58)):
        parts.append(f'<circle cx="{cx}" cy="60" r="3.5" class="d-dot"/>')
    parts.append(svg_text(300, 64, "IO diagnostics", "d-mute", 11, "middle"))
    parts.append('<rect x="32" y="80" width="172" height="22" rx="5" class="d-field"/>')
    parts.append(svg_text(42, 95, "⌕  name, id, serial…", "d-mute", 11))
    parts.append('<rect x="212" y="80" width="80" height="22" rx="5" class="d-field"/>')
    parts.append('<rect x="212" y="80" width="40" height="22" rx="5" class="d-sel"/>')
    parts.append(svg_text(232, 95, "Tree", "", 11, "middle", 600))
    parts.append(svg_text(272, 95, "Table", "d-mute", 11, "middle"))
    parts.append(badge(14, 120, 2))
    rows = [
        (0, "▾", "rack-pc", "", "err"),
        (1, "▾", "usb 1-3 · fdcanusb 272813EF", "ids 1,14,15", "err"),
        (2, "▾", "can · error-active", "3 of 3", "ok"),
        (3, "", "CAN 1", "fw 1.1.2", "ok"),
        (3, "", "CAN 14", "fw 1.1.2", "ok"),
        (3, "", "CAN 15", "fault 33", "err"),
        (1, "▸", "usb 1-5 · fdcanusb 28B3A25D", "0 of 4", "err"),
        (1, "▸", "usb 6-1 · D405 …6036", "moved 28 px", "warn"),
        (1, "▸", "usb 6-2 · D405 …4411", "", "ok"),
        (1, "", "usb 7-1.1.1 · Logi C920e", "no driver", "stale"),
    ]
    y = 124
    for depth, caret, name, right, level in rows:
        if name == "CAN 15":
            parts.append(f'<rect x="28" y="{y-12}" width="268" height="19" rx="4" class="d-sel"/>')
        x = 40 + depth * 14
        if caret:
            parts.append(svg_text(x - 10, y + 1, caret, "d-mute", 9))
        parts.append(status_dot(x + 4, y - 3, level))
        parts.append(svg_text(x + 14, y + 1, name, "", 11))
        if right:
            parts.append(svg_text(292, y + 1, right, "d-mute", 10, "end"))
        y += 20
    # detail pane
    parts.append('<line x1="302" y1="76" x2="302" y2="320" class="d-rule"/>')
    parts.append(svg_text(314, 124, "CAN 15 · moteus", "", 13, "start", 700))
    parts.append(svg_text(314, 140, "via fdcanusb 272813EF › can", "d-mute", 10))
    detail = [("IDENTITY", "mjbots moteus · serial … · hw …"), ("FIRMWARE", "1.1.2 installed · 1.1.2 latest · current"),
              ("COMMS", "replies 3 of 3 · last seen … · fault 33"), ("CONFIG · read-only", "ratio, kp, kd … as the driver reports")]
    yy = 162
    for label, value in detail:
        parts.append(svg_text(314, yy, label, "d-mute", 9, "start", 600))
        parts.append(svg_text(314, yy + 15, value, "", 11))
        yy += 34
    parts.append('<rect x="314" y="292" width="64" height="22" rx="5" class="d-btn"/>')
    parts.append(svg_text(346, 307, "Identify", "", 11, "middle"))
    parts.append('<rect x="386" y="292" width="92" height="22" rx="5" class="d-btn"/>')
    parts.append(svg_text(432, 307, "Clear faults…", "", 11, "middle"))
    parts.append(svg_text(486, 307, "← registered", "d-mute", 10))
    parts.append(svg_text(486, 319, "  by moteus_servo", "d-mute", 10))
    # activity log
    parts.append('<line x1="20" y1="328" x2="582" y2="328" class="d-rule"/>')
    parts.append(svg_text(32, 346, "ACTIVITY", "d-mute", 9, "start", 600))
    parts.append(badge(14, 360, 3))
    log = [("18:29", "272813EF", "blocked: open by tview (pid 640623)"),
           ("18:20", "28B3A25D", "unplugged from 1-5"),
           ("18:11", "28B3A25D", "plugged on 1-5 after 4 flaps on 1-7"),
           ("18:07", "D405 …4411", "dropped off USB for 4 s, back on 6-2")]
    yy = 364
    for t, dev, what in log:
        parts.append(svg_text(32, yy, t, "d-mute", 11))
        parts.append(svg_text(76, yy, dev, "", 11, "start", 600))
        parts.append(svg_text(160, yy, what, "", 11))
        yy += 17
    # table inset
    parts.append('<rect x="604" y="182" width="256" height="250" rx="8" class="d-panel"/>')
    parts.append(badge(850, 182, 4))
    parts.append(svg_text(616, 202, "Table mode: same rows, aligned", "", 12, "start", 700))
    cols = [(616, "name"), (722, "status"), (768, "rate m/e"), (822, "seen")]
    for x, c in cols:
        parts.append(svg_text(x, 222, c, "d-mute", 9, "start", 600))
    parts.append('<line x1="612" y1="228" x2="852" y2="228" class="d-rule"/>')
    trows = [(0, "272813EF", "err"), (1, "can", "ok"), (2, "CAN 1", "ok"), (2, "CAN 14", "ok"), (2, "CAN 15", "err"),
             (0, "28B3A25D", "err"), (0, "D405 …6036", "warn"), (0, "D405 …4411", "ok"), (0, "C920e", "stale")]
    yy = 246
    for depth, name, level in trows:
        parts.append(svg_text(616 + depth * 10, yy, ("▾ " if depth < 2 and name in ("272813EF", "can") else "") + name, "", 11))
        parts.append(status_dot(728, yy - 4, level))
        parts.append(svg_text(768, yy, "· / ·", "d-mute", 11))
        parts.append(svg_text(822, yy, "·", "d-mute", 11))
        yy += 20
    parts.append("</svg>")
    return "".join(parts)


def svg_planes() -> str:
    p = ['<svg viewBox="0 0 880 360" role="img" aria-label="Two planes: drivers emit read-only status records the window only renders; separately, drivers register actions the window shows as buttons, and the driver, which owns the port, executes them. The host never speaks a device protocol.">',
         ARROW_DEFS.format(id="a2")]
    p.append('<rect x="10" y="10" width="860" height="140" rx="10" class="d-band-status"/>')
    p.append(svg_text(24, 32, "STATUS PLANE · read-only · always on", "d-ok-text", 11, "start", 700))
    p.append('<rect x="10" y="160" width="860" height="190" rx="10" class="d-band-action"/>')
    p.append(svg_text(24, 340, "ACTION PLANE · opt-in · driver-registered, driver-executed", "d-warn-text", 11, "start", 700))
    # boxes
    p.append('<rect x="30" y="100" width="170" height="110" rx="8" class="d-panel"/>')
    p.append(svg_text(115, 128, "Device", "", 13, "middle", 700))
    p.append(svg_text(115, 148, "fdcanusb 272813EF", "", 11, "middle"))
    p.append(svg_text(115, 164, "› CAN 1, 14, 15", "", 11, "middle"))
    p.append(svg_text(115, 190, "speaks moteus protocol", "d-mute", 10, "middle"))
    p.append('<rect x="340" y="46" width="210" height="246" rx="8" class="d-panel-strong"/>')
    p.append(svg_text(445, 70, "Driver package", "", 13, "middle", 700))
    p.append(svg_text(445, 86, "moteus_servo/diagnostics.py", "d-mono", 11, "middle"))
    p.append(svg_text(445, 104, "owns the port", "d-acc-text", 11, "middle", 600))
    p.append('<rect x="356" y="114" width="178" height="30" rx="6" class="d-field"/>')
    p.append(svg_text(445, 134, "status() → records", "", 11, "middle"))
    p.append('<rect x="356" y="196" width="178" height="80" rx="6" class="d-field"/>')
    p.append(svg_text(445, 214, "actions:", "", 11, "middle", 600))
    p.append(svg_text(445, 231, "identify · clear_faults", "d-mono", 10, "middle"))
    p.append(svg_text(445, 246, "reboot · update_firmware", "d-mono", 10, "middle"))
    p.append(svg_text(445, 263, "danger, preconditions, confirm", "d-mute", 10, "middle"))
    p.append('<rect x="690" y="46" width="170" height="246" rx="8" class="d-panel"/>')
    p.append(svg_text(775, 70, "IO diagnostics", "", 13, "middle", 700))
    p.append(svg_text(775, 120, "renders the tree", "", 11, "middle"))
    p.append(svg_text(775, 136, "computes STALE", "", 11, "middle"))
    p.append(svg_text(775, 220, "shows a button only", "", 11, "middle"))
    p.append(svg_text(775, 236, "if it was registered;", "", 11, "middle"))
    p.append(svg_text(775, 252, "confirms, then asks", "", 11, "middle"))
    p.append(svg_text(775, 268, "the driver to run it", "", 11, "middle"))
    # status arrows
    p.append('<path d="M200,128 L338,128" class="d-line" marker-end="url(#a2)"/>')
    p.append(svg_text(269, 120, "telemetry, faults", "d-mute", 10, "middle"))
    p.append('<path d="M534,129 L688,129" class="d-line-ok" marker-end="url(#a2)"/>')
    p.append(svg_text(611, 120, "records every period", "d-mute", 10, "middle"))
    # action arrows
    p.append('<path d="M534,212 L688,212" class="d-line-dash" marker-end="url(#a2)"/>')
    p.append(svg_text(611, 204, "registers actions", "d-mute", 10, "middle"))
    p.append('<path d="M688,246 L536,246" class="d-line-acc" marker-end="url(#a2-acc)"/>')
    p.append(svg_text(622, 238, "run clear_faults (confirmed)", "d-acc-text", 10, "middle"))
    p.append('<path d="M340,236 L202,198" class="d-line-acc" marker-end="url(#a2-acc)"/>')
    p.append(svg_text(262, 238, "executes it", "d-acc-text", 10, "middle"))
    # forbidden path
    p.append('<path d="M775,292 L775,314 L116,314 L116,214" class="d-line-err" marker-end="url(#a2)"/>')
    p.append(svg_text(400, 306, "never: the host does not speak a device protocol", "d-err-text", 11, "middle", 600))
    p.append('<line x1="632" y1="306" x2="648" y2="322" class="d-x"/><line x1="648" y1="306" x2="632" y2="322" class="d-x"/>')
    p.append("</svg>")
    return "".join(p)


def svg_ownership() -> str:
    p = ['<svg viewBox="0 0 880 300" role="img" aria-label="Ownership: when the robot stack runs, its driver owns the port and publishes; IO diagnostics only listens. When nothing runs, IO diagnostics checks holders and the rack lock, then either starts the driver\'s own check as a short-lived owner or shows a blocked state naming the holder.">',
         ARROW_DEFS.format(id="a3")]
    p.append('<rect x="10" y="10" width="420" height="280" rx="10" class="d-panel"/>')
    p.append(svg_text(26, 34, "A · the stack is running", "", 13, "start", 700))
    p.append('<rect x="30" y="60" width="170" height="54" rx="8" class="d-panel-strong"/>')
    p.append(svg_text(115, 82, "stack’s driver", "", 12, "middle", 600))
    p.append(svg_text(115, 99, "(moteus_servo)", "d-mono", 10, "middle"))
    p.append('<rect x="250" y="60" width="160" height="54" rx="8" class="d-field"/>')
    p.append(svg_text(330, 82, "/dev/serial/by-id/", "d-mono", 10, "middle"))
    p.append(svg_text(330, 97, "…fdcanusb_272813EF", "d-mono", 10, "middle"))
    p.append('<path d="M200,87 L248,87" class="d-line-acc" marker-end="url(#a3-acc)"/>')
    p.append(svg_text(224, 80, "owns", "d-acc-text", 10, "middle"))
    p.append('<rect x="30" y="180" width="170" height="60" rx="8" class="d-panel"/>')
    p.append(svg_text(115, 204, "IO diagnostics", "", 12, "middle", 600))
    p.append(svg_text(115, 222, "pure subscriber", "d-mute", 10, "middle"))
    p.append('<path d="M115,114 L115,178" class="d-line-ok" marker-end="url(#a3)"/>')
    p.append(svg_text(124, 150, "publishes records", "d-mute", 10))
    p.append(svg_text(250, 210, "opens nothing,", "d-mute", 11))
    p.append(svg_text(250, 226, "cannot interfere", "d-mute", 11))
    p.append('<rect x="450" y="10" width="420" height="280" rx="10" class="d-panel"/>')
    p.append(svg_text(466, 34, "B · nothing is running", "", 13, "start", 700))
    p.append('<rect x="560" y="48" width="150" height="40" rx="8" class="d-panel-strong"/>')
    p.append(svg_text(635, 72, "IO diagnostics", "", 12, "middle", 600))
    p.append('<polygon points="635,106 730,140 635,174 540,140" class="d-field"/>')
    p.append(svg_text(635, 137, "port holder?", "", 11, "middle", 600))
    p.append(svg_text(635, 152, "rack lock held?", "", 11, "middle", 600))
    p.append('<path d="M635,88 L635,104" class="d-line" marker-end="url(#a3)"/>')
    p.append(svg_text(635, 190, "/proc/locks + find_port_holders", "d-mono", 9, "middle"))
    # free branch
    p.append('<path d="M730,140 L784,140 L784,206" class="d-line-ok" marker-end="url(#a3)"/>')
    p.append(svg_text(757, 132, "free", "d-ok-text", 10, "middle", 600))
    p.append('<rect x="668" y="208" width="192" height="66" rx="8" class="d-panel-strong"/>')
    p.append(svg_text(764, 228, "start the driver’s own check", "", 11, "middle", 600))
    p.append(svg_text(764, 244, "as a short-lived owner,", "", 11, "middle"))
    p.append(svg_text(764, 260, "read, release the port", "", 11, "middle"))
    # held branch
    p.append('<path d="M540,140 L506,140 L506,206" class="d-line-warn" marker-end="url(#a3)"/>')
    p.append(svg_text(523, 132, "held", "d-warn-text", 10, "middle", 600))
    p.append('<rect x="462" y="208" width="178" height="66" rx="8" class="d-warn-box"/>')
    p.append(svg_text(551, 228, "show it, do nothing", "d-warn-text", 11, "middle", 700))
    p.append(svg_text(551, 244, "“blocked: bus busy", "", 11, "middle"))
    p.append(svg_text(551, 260, "(tview pid 640623)”", "", 11, "middle"))
    p.append("</svg>")
    return "".join(p)


def svg_two_sources() -> str:
    rows = [
        (0, "os", "rack PC (host)", ""),
        (1, "os", "usb 1-3 · 12 Mb/s  →  fdcanusb 272813EF", "mjbots · /dev/ttyACM0 · by-id"),
        (2, "drv", "can · error-active · err 0 · warn 0", "moteus_servo"),
        (3, "drv", "CAN 1 · fw 1.1.2", ""),
        (3, "drv", "CAN 14 · fw 1.1.2", ""),
        (3, "drv", "CAN 15 · fw 1.1.2 · fault 33 on bridge enable", ""),
        (1, "os", "usb 1-5  →  fdcanusb 28B3A25D", "plugged 18:11 after 4 flaps on 1-7"),
        (2, "drv", "can · 0 of 4 boards answer (adapter fine, bus dead)", "moteus_servo"),
        (1, "os", "usb 6-1 · 5 Gb/s  →  RealSense D405 (USB serial 219323070276)", "/dev/video0-5"),
        (2, "drv", "colour + depth streams · SDK serial …276036 · moved 28 px", "intel_rgbd"),
        (1, "os", "usb 6-2 · 5 Gb/s  →  RealSense D405 (130523071221)", "dropped 4 s at 18:07"),
        (2, "drv", "colour + depth streams · SDK serial …4411", "intel_rgbd"),
        (1, "os", "usb 7-1 › 7-1.1 › 7-1.1.1  →  Logi Webcam C920e", "no driver diagnostics"),
    ]
    height = 52 + len(rows) * 24 + 20
    p = [f'<svg viewBox="0 0 880 {height}" role="img" aria-label="One tree from two sources: the operating system gives the USB skeleton from host to port to device; each device\'s driver hangs its buses, nodes and streams underneath. Real rack devices from the Oct 21 snapshot.">']
    p.append('<rect x="20" y="10" width="14" height="14" rx="3" class="d-os"/>')
    p.append(svg_text(42, 22, "from the OS: sysfs, /dev/serial/by-id, kernel log (always there, passive)", "", 11))
    p.append('<rect x="470" y="10" width="14" height="14" rx="3" class="d-drv"/>')
    p.append(svg_text(492, 22, "from the driver’s diagnostics.py (hung under its device)", "", 11))
    y0 = 56
    last_at_depth: dict[int, int] = {}
    for i, (depth, source, label, note) in enumerate(rows):
        y = y0 + i * 24
        x = 30 + depth * 26
        # connector from parent
        if depth > 0:
            parent_y = None
            for j in range(i - 1, -1, -1):
                if rows[j][0] == depth - 1:
                    parent_y = y0 + j * 24
                    break
            px = 30 + (depth - 1) * 26 + 6
            p.append(f'<path d="M{px},{parent_y + 7} L{px},{y} L{x - 2},{y}" class="d-tree"/>')
        p.append(f'<rect x="{x}" y="{y - 7}" width="12" height="12" rx="3" class="{"d-os" if source == "os" else "d-drv"}"/>')
        p.append(svg_text(x + 20, y + 4, label, "", 12, "start", 600 if depth <= 1 else None))
        if note:
            p.append(svg_text(860, y + 4, note, "d-mute", 10, "end"))
    p.append("</svg>")
    return "".join(p)


def svg_wires() -> str:
    p = ['<svg viewBox="0 0 880 270" role="img" aria-label="Every edge of the tree carries its own health: USB re-enumerations and link speed, CAN reply loss and bus state, and even a camera mount that moved. Each line is a real event from Oct 21.">',
         ARROW_DEFS.format(id="a5")]

    def node(x, y, w, label, cls="d-panel"):
        return f'<rect x="{x}" y="{y - 15}" width="{w}" height="30" rx="7" class="{cls}"/>' + svg_text(x + w / 2, y + 4, label, "", 11, "middle", 600)

    def edge(x1, x2, y, cls, label):
        return f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" class="{cls}"/>' + svg_text((x1 + x2) / 2, y - 8, label, "d-edge-label", 10, "middle")

    rows = [
        (36, [("host", 70, "d-panel-strong"), ("D405 …4411", 120, "d-panel")], [("d-wire-warn", "USB 6-2 · 5 Gb/s · re-enumerated once (18:07, 4 s gap)")]),
        (100, [("host", 70, "d-panel-strong"), ("fdcanusb 28B3A25D", 150, "d-panel"), ("4 boards", 100, "d-err-box")], [("d-wire-warn", "USB 1-7 → 1-5 · 5 enumerations in 17 s"), ("d-wire-err", "CAN · 0 of 4 reply · bus dead")]),
        (164, [("host", 70, "d-panel-strong"), ("fdcanusb 272813EF", 150, "d-panel"), ("CAN 15 · fault 33", 120, "d-err-box")], [("d-wire-ok", "USB 1-3 · 12 Mb/s · stable"), ("d-wire-ok", "CAN · error-active · 3 of 3 reply")]),
        (228, [("rack frame", 90, "d-panel-strong"), ("D405 …6036", 120, "d-panel")], [("d-wire-warn", "mount, a mechanical wire · moved 28 px since calibration")]),
    ]
    for y, nodes, edges in rows:
        x = 20
        positions = []
        gap = 240 if len(nodes) == 3 else 520
        for label, w, cls in nodes:
            positions.append((x, w))
            p.append(node(x, y, w, label, cls))
            x += w + gap
        for idx, (cls, label) in enumerate(edges):
            (ax, aw), (bx, _) = positions[idx], positions[idx + 1]
            p.append(edge(ax + aw, bx, y, cls, label))
    p.append("</svg>")
    return "".join(p)


def svg_stable() -> str:
    p = ['<svg viewBox="0 0 880 270" role="img" aria-label="Stable sub-assembly: the hardware IO surface, the contract and the IO diagnostics window form a stable base; the software stack above it changes weekly and joins later through the same records, which also feed rqt, Lichtblick and Foxglove as second views.">',
         ARROW_DEFS.format(id="a6")]
    p.append('<rect x="20" y="14" width="600" height="62" rx="8" class="d-dashed"/>')
    p.append(svg_text(36, 38, "Software stack: changes every week", "", 12, "start", 700))
    p.append(svg_text(36, 58, "rack runner, bam_rack_twin, ROS nodes later, planners, policies", "d-mute", 11))
    p.append('<path d="M320,78 L320,98" class="d-line-dash" marker-end="url(#a6)"/>')
    p.append(svg_text(330, 92, "joins later with the same records", "d-mute", 10))
    bands = [(102, "IO diagnostics: chip · tree · table · activity log · registered actions", "d-panel"),
             (152, "Contract: DiagnosticStatus + path + expected_period_s + identity", "d-panel-acc"),
             (202, "Hardware IO surface: ports, adapters, buses, devices", "d-panel-strong")]
    for y, label, cls in bands:
        p.append(f'<rect x="20" y="{y}" width="600" height="42" rx="8" class="{cls}"/>')
        p.append(svg_text(36, y + 26, label, "", 12, "start", 600))
    p.append('<path d="M632,104 L632,242" class="d-brace"/>')
    p.append(svg_text(644, 236, "stable", "d-ok-text", 12, "start", 700))
    p.append(svg_text(644, 50, "changing", "d-mute", 12, "start", 700))
    p.append('<path d="M620,173 L720,173" class="d-line-dash" marker-end="url(#a6)"/>')
    p.append('<rect x="722" y="140" width="150" height="68" rx="8" class="d-panel"/>')
    p.append(svg_text(797, 162, "second views", "", 11, "middle", 600))
    p.append(svg_text(797, 179, "rqt_robot_monitor", "d-mute", 10, "middle"))
    p.append(svg_text(797, 194, "Lichtblick · Foxglove", "d-mute", 10, "middle"))
    p.append("</svg>")
    return "".join(p)


def svg_lichtblick() -> str:
    p = ['<svg viewBox="0 0 880 200" role="img" aria-label="Reuse question: the same driver records feed IO diagnostics, which owns presence, plug events, the USB skeleton, updates and actions, and optionally a foxglove-sdk WebSocket that Lichtblick renders as Summary and Detail with no UI code.">',
         ARROW_DEFS.format(id="a7")]
    p.append('<rect x="20" y="70" width="200" height="60" rx="8" class="d-panel-strong"/>')
    p.append(svg_text(120, 96, "driver records", "", 12, "middle", 700))
    p.append(svg_text(120, 113, "Python dataclass → JSON", "d-mono", 10, "middle"))
    p.append('<path d="M220,90 C300,90 320,44 400,44" class="d-line-acc" marker-end="url(#a7-acc)"/>')
    p.append('<rect x="402" y="14" width="458" height="60" rx="8" class="d-panel-acc"/>')
    p.append(svg_text(418, 38, "IO diagnostics (owned, small)", "", 12, "start", 700))
    p.append(svg_text(418, 58, "chip · USB skeleton · plug events · update · actions · tree/detail", "", 11))
    p.append('<path d="M220,110 C300,110 320,156 400,156" class="d-line-dash" marker-end="url(#a7)"/>')
    p.append(svg_text(292, 168, "30-min spike", "d-mute", 10, "middle"))
    p.append('<rect x="402" y="126" width="458" height="60" rx="8" class="d-dashed"/>')
    p.append(svg_text(418, 150, "foxglove-sdk WebSocket → Lichtblick Summary + Detail", "", 12, "start", 700))
    p.append(svg_text(418, 170, "zero UI code; lacks presence, plug events, USB tree, update, actions", "d-mute", 11))
    p.append("</svg>")
    return "".join(p)


# --------------------------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------------------------

CSS = r"""
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b6760;--line:#e3dfd6;--line-strong:#c9c3b7;--panel:#ffffff;--panel2:#f3f1ec;
--accent:#2a7a6f;--accent-soft:#e3f1ee;--ok:#2e8b57;--warn:#b7791f;--err:#c4422f;--stale:#8a8580;--os:#8b8f98;--drv:#2a7a6f;
--band-status:#eef6f1;--band-action:#fbf3e6;--code:#f1efe9;--shadow:0 1px 2px rgba(0,0,0,.05)}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#141413;--fg:#ecebe7;--muted:#a5a19a;--line:#2e2d2a;--line-strong:#46443f;
--panel:#1c1b19;--panel2:#24231f;--accent:#5fb3a6;--accent-soft:#1d302d;--ok:#57c785;--warn:#f0b04a;--err:#ef6a55;--stale:#8f8a83;--os:#7d828c;--drv:#5fb3a6;
--band-status:#17221c;--band-action:#251f15;--code:#1f1e1b;--shadow:none}}
:root[data-theme="dark"]{--bg:#141413;--fg:#ecebe7;--muted:#a5a19a;--line:#2e2d2a;--line-strong:#46443f;--panel:#1c1b19;--panel2:#24231f;
--accent:#5fb3a6;--accent-soft:#1d302d;--ok:#57c785;--warn:#f0b04a;--err:#ef6a55;--stale:#8f8a83;--os:#7d828c;--drv:#5fb3a6;--band-status:#17221c;--band-action:#251f15;--code:#1f1e1b;--shadow:none}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Ubuntu,"Noto Sans",sans-serif}
main{max-width:1000px;margin:0 auto;padding:28px 16px 80px}
a{color:var(--accent)}
h1{font-size:30px;line-height:1.2;margin:6px 0 8px;letter-spacing:-.01em}
h2{font-size:22px;margin:56px 0 6px;padding-top:12px;border-top:1px solid var(--line)}
h3{font-size:17px;margin:0 0 6px}
p{margin:8px 0}
.kicker{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.lede{color:var(--muted);font-size:16px;max-width:760px}
.cite{font-size:11px;margin-left:1px}
.cite a{text-decoration:none}
.muted{color:var(--muted)}
.verdict{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:18px 20px;margin:22px 0;box-shadow:var(--shadow)}
.verdict h2{border:0;margin:0 0 8px;padding:0;font-size:19px}
.verdict ol{margin:6px 0 0;padding-left:22px}
.verdict li{margin:4px 0}
.decisions{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:16px 0 4px}
.decision{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;box-shadow:var(--shadow)}
.decision .q{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
.decision b.a{display:block;font-size:16px;margin-bottom:6px}
.decision p{font-size:14px;margin:4px 0}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:14px;margin:14px 0 0}
nav.toc a{text-decoration:none}
.pick,.ex{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:18px;align-items:start;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0;box-shadow:var(--shadow)}
.shots{display:flex;flex-direction:column;gap:8px}
.shots img,.ex-img img,.mock img,figure img{display:block;max-width:100%;height:auto;border-radius:6px;border:1px solid var(--line);background:#111}
.shots .pair{display:flex;gap:8px;align-items:flex-start}
.shots .pair img{flex:1 1 0;min-width:0}
blockquote{margin:6px 0 10px;padding:4px 0 4px 12px;border-left:3px solid var(--accent);color:var(--fg);font-style:italic}
blockquote footer{font-style:normal;font-size:12px;color:var(--muted)}
.gave{font-size:15px}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;vertical-align:2px;margin-left:6px;border:1px solid var(--line-strong);color:var(--muted)}
.tag.ros{color:var(--accent);border-color:var(--accent)}
.tag.vendor{color:var(--warn);border-color:var(--warn)}
.tag.general{color:var(--os);border-color:var(--os)}
.meta{font-size:13px;color:var(--muted);margin:2px 0 8px}
.ex ul{margin:6px 0;padding-left:18px;font-size:14.5px}
.ex li{margin:4px 0}
.ex li.take{list-style:none;margin-left:-18px;margin-top:8px;padding:8px 10px;background:var(--accent-soft);border-radius:8px}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:16px 0}
summary{cursor:pointer;font-weight:600}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;vertical-align:top;padding:7px 10px;border-bottom:1px solid var(--line)}
th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-weight:600}
tbody tr:nth-child(odd) td{background:color-mix(in srgb,var(--panel2) 55%,transparent)}
code,.mono{font-family:ui-monospace,"JetBrains Mono",Menlo,Consolas,monospace;font-size:.88em}
code{background:var(--code);padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}
.two>*,.decisions>*,.pick>*,.ex>*{min-width:0}
td:first-child code{white-space:nowrap;overflow-wrap:normal}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow-x:auto;font-size:12.5px;line-height:1.5}
pre code{background:none;padding:0}
figure{margin:18px 0;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;box-shadow:var(--shadow)}
figure svg{display:block;width:100%;height:auto;color:var(--fg)}
figcaption{font-size:13.5px;color:var(--muted);margin-top:10px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.callout{border:1px solid var(--line);border-left:4px solid var(--warn);background:var(--panel);border-radius:10px;padding:12px 16px;margin:14px 0}
.slot{border:2px dashed var(--warn);border-radius:12px;padding:26px 20px;text-align:center;color:var(--muted);background:var(--panel)}
.slot b{color:var(--fg)}
.mock{display:grid;gap:14px}
.mock figure{margin:0}
.lvl{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:5px;color:#fff}
.lvl.ok{background:var(--ok)}.lvl.warn{background:var(--warn)}.lvl.err{background:var(--err)}.lvl.stale{background:var(--stale)}
.log td:first-child{white-space:nowrap;font-family:ui-monospace,Menlo,monospace;font-size:13px}
.qs{counter-reset:q;padding:0;list-style:none}
.qs li{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:10px 0}
.qs li b{display:block;margin-bottom:4px}
.qs .rec{font-size:14.5px}
.qs .def{font-size:13.5px;color:var(--muted)}
ol.sources{font-size:13.5px;padding-left:28px}
ol.sources li{margin:3px 0;word-break:break-word}
.foot{font-size:13px;color:var(--muted);margin-top:40px}
/* SVG vocabulary */
svg text{fill:currentColor;font-family:system-ui,-apple-system,"Segoe UI",Ubuntu,sans-serif}
svg .d-mute,svg .d-edge-label{fill:var(--muted)}
svg .d-mono{fill:var(--muted);font-family:ui-monospace,Menlo,monospace}
svg .d-acc-text{fill:var(--accent)} svg .d-ok-text{fill:var(--ok)} svg .d-warn-text{fill:var(--warn)} svg .d-err-text{fill:var(--err)}
svg .d-badge{fill:var(--accent)} svg .d-badge-text{fill:#fff}
svg .d-bar{fill:var(--panel2);stroke:var(--line-strong)}
svg .d-chip{fill:var(--accent-soft);stroke:var(--accent);stroke-width:1.5}
svg .d-panel{fill:var(--panel);stroke:var(--line-strong)}
svg .d-panel-strong{fill:var(--panel2);stroke:var(--line-strong)}
svg .d-panel-acc{fill:var(--accent-soft);stroke:var(--accent)}
svg .d-field{fill:var(--panel2);stroke:var(--line)}
svg .d-sel{fill:var(--accent-soft);stroke:none}
svg .d-btn{fill:var(--panel2);stroke:var(--line-strong)}
svg .d-dot{fill:var(--line-strong)}
svg .d-rule{stroke:var(--line);stroke-width:1}
svg .d-ok{fill:var(--ok)} svg .d-warn{fill:var(--warn)} svg .d-err{fill:var(--err)}
svg .d-stale-ring{fill:none;stroke:var(--stale);stroke-width:1.6;stroke-dasharray:2 2}
svg .d-os{fill:var(--os)} svg .d-drv{fill:var(--drv)}
svg .d-tree{fill:none;stroke:var(--line-strong);stroke-width:1.2}
svg .d-line{fill:none;stroke:currentColor;stroke-width:1.4}
svg .d-line-acc{fill:none;stroke:var(--accent);stroke-width:1.8}
svg .d-line-ok{fill:none;stroke:var(--ok);stroke-width:1.8}
svg .d-line-warn{fill:none;stroke:var(--warn);stroke-width:1.8}
svg .d-line-err{fill:none;stroke:var(--err);stroke-width:1.6;stroke-dasharray:6 4}
svg .d-line-dash{fill:none;stroke:var(--muted);stroke-width:1.4;stroke-dasharray:5 4}
svg .d-x{stroke:var(--err);stroke-width:2.5}
svg .d-arrowhead{fill:var(--muted)} svg .d-arrowhead-acc{fill:var(--accent)}
svg .d-band-status{fill:var(--band-status)} svg .d-band-action{fill:var(--band-action)}
svg .d-warn-box{fill:var(--band-action);stroke:var(--warn)}
svg .d-err-box{fill:var(--panel);stroke:var(--err);stroke-width:1.6}
svg .d-dashed{fill:none;stroke:var(--muted);stroke-dasharray:6 4}
svg .d-brace{fill:none;stroke:var(--ok);stroke-width:2}
svg .d-wire-ok{stroke:var(--ok);stroke-width:3}
svg .d-wire-warn{stroke:var(--warn);stroke-width:3}
svg .d-wire-err{stroke:var(--err);stroke-width:3;stroke-dasharray:7 4}
@media (max-width:820px){
 .pick,.ex{grid-template-columns:1fr}
 .decisions{grid-template-columns:1fr}
 .two{grid-template-columns:1fr}
 h1{font-size:25px}
}
"""


def pick_html(index: int, pick: dict) -> str:
    imgs = []
    for shot in pick["shots"]:
        stamp, alt = shot[0], shot[1]
        crop = shot[2] if len(shot) > 2 else None
        width, height = Image.open(VAULT_IMAGES / f"Pasted image {stamp}.png").size
        if crop:
            width, height = crop[2] - crop[0], crop[3] - crop[1]
        # WHY flex-grow = aspect ratio: two shots side by side end at the same height, so neither is squashed
        imgs.append(f'<img alt="{esc(alt)}" src="{pasted(stamp, crop=crop)}" style="flex:{width / height:.3f} 1 0">')
    if len(imgs) == 2:
        shots_html = f'<div class="pair">{imgs[0]}{imgs[1]}</div>'
    else:
        shots_html = "".join(imgs)
    cites = cite(2, *pick.get("cites", ()))
    return f"""
<article class="pick" id="pick-{index}">
  <div class="shots">{shots_html}</div>
  <div>
    <h3>{esc(pick['title'])}</h3>
    <blockquote>“{esc(pick['quote'])}”<footer>Zach, Oct 22</footer></blockquote>
    <p class="gave"><b>What it contributed:</b> {esc(pick['gave'])}{cites}</p>
  </div>
</article>"""


def example_html(index: int, ex: dict) -> str:
    file_name, opts = ex["img"]
    src = data_uri(SURVEY / file_name, **opts)
    kind_cls = {"ROS-native": "ros", "vendor": "vendor", "general": "general"}[ex["kind"]]
    bullets = "".join(f"<li>{esc(b)}</li>" for b in ex["layout"])
    return f"""
<article class="ex" id="ex-{index}">
  <div class="ex-img"><img alt="{esc(ex['name'])} screenshot" src="{src}"></div>
  <div>
    <h3>{index}. {esc(ex['name'])}<span class="tag {kind_cls}">{esc(ex['kind'])}</span></h3>
    <p class="meta"><a href="{esc(ex['url'])}">{esc(ex['url'].split('//')[1].split('/')[0])}</a> · {esc(ex['license'])}</p>
    <ul>{bullets}<li class="take"><b>IO diagnostics takes:</b> {esc(ex['take'])}{cite(*ex['cites'])}</li></ul>
  </div>
</article>"""


def also_html() -> str:
    rows = []
    for group, name, url, lic, take in ALSO:
        rows.append(f"<tr><td>{esc(group)}</td><td><a href=\"{esc(url)}\">{esc(name)}</a></td><td>{esc(lic)}</td><td>{esc(take)}</td></tr>")
    return f"""
<details>
  <summary>Also surveyed: {len(ALSO)} more tools, with links (click to open)</summary>
  <p class="muted">From the three survey lanes{cite(3, 4, 5)}. Every fact was read from a primary source on 2026-10-22; the lanes say where a page could not be reached.</p>
  <div class="table-wrap"><table>
    <thead><tr><th>Lane</th><th>Tool</th><th>Licence · platform</th><th>The one thing worth taking</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</details>"""


def contract_html() -> str:
    fields = [
        ("level", "OK · WARN · ERROR · STALE", "ROS DiagnosticStatus", "Kept. STALE is set by the host, never sent."),
        ("name", "the check, e.g. <code>comms</code>, <code>firmware</code>", "ROS DiagnosticStatus", "Kept. Viewers key a row by (hardware_id, name)."),
        ("message", "one human sentence", "ROS DiagnosticStatus", "Kept; capped at 5,000 characters as Lichtblick does."),
        ("hardware_id", "device identity: serial, CAN id, USB path", "ROS DiagnosticStatus", "Kept; the search key."),
        ("values[key, value]", "string pairs: rates, counters, config", "ROS DiagnosticStatus", "Kept. Key conventions per kind, e.g. <code>rate_hz</code>, <code>rate_expected_hz</code>, <code>replies</code>."),
        ("path", "<code>host/usb-1-3/fdcanusb-272813EF/can/15</code>", "added", "Tree position; replaces the aggregator’s YAML."),
        ("expected_period_s", "how often a fresh record must arrive", "added", "Host marks STALE when a record is older than about 3× this."),
        ("identity", "manufacturer, model, serial_number, sw_version, hw_version, via_device", "Home Assistant device_info", "<code>via_device</code> is the parent edge of the tree."),
        ("update (optional)", "installed_version, latest_version, release_url", "Home Assistant update entity", "Update available is a WARN, never an ERROR (ros2 doctor)."),
    ]
    rows = "".join(f"<tr><td><code>{f}</code></td><td>{m}</td><td>{s}</td><td>{n}</td></tr>" for f, m, s, n in fields)
    actions = [
        ("id", "<code>identify</code>, <code>clear_faults</code>, <code>reboot</code>, <code>update_firmware</code>, <code>run_comms_check</code>"),
        ("title, description", "the button label and one sentence on what it does"),
        ("danger", "low · medium · high; the verbs stay unequal: clear faults &lt; restart &lt; factory reset"),
        ("preconditions", "e.g. port free, rack lock free, motor disabled"),
        ("confirm", "the sentence the dialog shows before it runs"),
    ]
    arows = "".join(f"<tr><td><code>{f}</code></td><td>{m}</td></tr>" for f, m in actions)
    example = """{
  "level": "ERROR",
  "name": "comms",
  "message": "CAN 15 fault 33 on bridge enable",
  "hardware_id": "fdcanusb-272813EF/can/15",
  "path": "host/usb-1-3/fdcanusb-272813EF/can/15",
  "expected_period_s": 1.0,
  "identity": {
    "manufacturer": "mjbots", "model": "moteus",
    "serial_number": "<from moteus_tool --info>",
    "sw_version": "1.1.2",
    "via_device": "fdcanusb-272813EF/can"
  },
  "update": {"installed_version": "1.1.2", "latest_version": "1.1.2"},
  "values": [["fault", "33"], ["replies", "3 of 3"]]
}"""
    return f"""
<div class="table-wrap"><table>
  <thead><tr><th>Field</th><th>Meaning</th><th>From</th><th>Note</th></tr></thead>
  <tbody>{rows}</tbody>
</table></div>
<div class="two">
  <div>
    <p><b>One record, written as JSON by a plain Python dataclass.</b> No ROS needed to emit it; a ROS node or a foxglove-sdk bridge can republish it later{cite(3, 12, 20)}. Values marked &lt;…&gt; are filled by the driver.</p>
    <pre><code>{esc(example)}</code></pre>
  </div>
  <div>
    <p><b>The action plane registers a second, separate record per action</b>, shaped like Home Assistant <code>button</code> entities and ros2_medkit Operations{cite(1, 5, 15)}:</p>
    <div class="table-wrap"><table><thead><tr><th>Field</th><th>Meaning</th></tr></thead><tbody>{arows}</tbody></table></div>
    <p class="muted">A button appears only if its driver registered it; the driver runs it; every action is confirmed and blocked while someone else holds the port.</p>
  </div>
</div>"""


def wires_log_html() -> str:
    events = [
        ("06:14:42", "plugged", "ok", "fdcanusb 272813EF (every device appears at 06:14)", "1-3", "kernel log"),
        ("12:16:54", "unplugged", "stale", "RealSense D435 038623020823", "6-2", "kernel log"),
        ("12:22:41", "plugged", "ok", "RealSense D405 130523071221 (…4411)", "6-2", "kernel log"),
        ("18:07:08", "re-enumerated", "warn", "D405 …4411: dropped off USB for 4 s, came back", "6-2", "kernel log, coalesced"),
        ("18:11:42", "plugged (flapping)", "warn", "fdcanusb 28B3A25D: 4 flaps on 1-7 (5 enumerations in 17 s), then settled", "1-5", "kernel log, coalesced"),
        ("18:20:46", "unplugged", "stale", "fdcanusb 28B3A25D", "1-5", "kernel log"),
        ("18:29", "blocked", "warn", "fdcanusb 272813EF: open by pid 640623 (tview); checks wait", "1-3", "/proc/locks + holder check"),
        ("Oct 21", "fault entered", "err", "CAN 15: fault 33 on bridge enable", "1-3 › can", "reported by the rack session"),
        ("Oct 21", "bus dead", "err", "28B3A25D: 0 of 4 boards answer; adapter fine, ACK-less bus", "1-5 › can", "reported by the rack session"),
        ("Oct 21", "moved", "warn", "D405 …6036: moved 28 px since calibration", "6-1", "bam_rack_twin calibration"),
        ("19:02", "no replies", "err", "272813EF: 0 of 3 boards answer (supply off)", "1-3 › can", "reported by the rack session"),
    ]
    rows = "".join(
        f'<tr><td>{t}</td><td><span class="lvl {lvl}">{esc(kind)}</span></td><td>{esc(what)}</td><td class="mono">{esc(port)}</td><td class="muted">{esc(src)}</td></tr>'
        for t, kind, lvl, what, port, src in events)
    return f"""
<div class="table-wrap log"><table>
  <thead><tr><th>Oct 21</th><th>Event</th><th>What</th><th>Port</th><th>Source</th></tr></thead>
  <tbody>{rows}</tbody>
</table></div>
<p class="muted">The six kernel-log rows and the 18:29 holder are read straight from <code>out/snapshot-live.json</code>{cite(32)}; the coalescer folds 28B3A25D’s five enumerations into one notice{cite(6)}. The four “reported” rows come from the rack session that evening{cite(1, 6)}, with times only where a time was recorded.</p>"""


_mock_cache: dict[str, str] = {}


def mock_uri(name: str) -> str:
    # WHY cached: the hero appears twice (under the verdict and in the mock section); encode it once
    if name not in _mock_cache:
        _mock_cache[name] = data_uri(MOCK_DIR / f"{name}.png", max_width=1100, quality=64)
    return _mock_cache[name]


def hero_html() -> str:
    """The first media under the verdict: the converged mock itself, when the builder has landed it."""
    if not (MOCK_DIR / "hero.png").exists():
        return ""
    return (f'<figure id="hero"><img alt="Converged mock, tree mode at the 18:11 plug" src="{mock_uri("hero")}">'
            f'<figcaption>The converged mock, tree mode at the 18:11 plug with CAN 15 selected. Table, activity and the action dialog are in '
            f'<a href="#mock">Converged mock</a> below.</figcaption></figure>')


def mock_html() -> str:
    names = ["hero", "table", "activity", "action"]
    found = [n for n in names if (MOCK_DIR / f"{n}.png").exists()]
    if not found:
        return f"""
<div class="slot" id="mock-slot">
  <p><b>Slot: the converged mock has not landed yet.</b></p>
  <p>A parallel builder is making a clickable mock of this design. When <code>{MOCK_DIR}/hero.png</code> (plus <code>table.png</code>, <code>activity.png</code>, <code>action.png</code>) exists, rebuild this page:</p>
  <p><code>python3 /home/bam/io-diagnostics/docs/build_design_report.py</code></p>
</div>"""
    captions = {"hero": "Tree mode at the 18:11 plug. The entity tree runs host › port › adapter › CAN bus › board › driver and motor, worst child rolling up. CAN 15 is selected: status with its declared period and STALE rule, identity as device_info, versions, the driver’s registered actions, comms, read-only config and values, each value tagged real, recorded, reported or simulated.",
                "table": "Table mode at 18:25, while tview holds 272813EF: nested first column, then aligned status, message, rate measured / expected, last seen, firmware and errors. The boards age toward STALE and the detail says checks and actions wait for the holder.",
                "activity": "Activity: the log (newest first, port on every entry), a one-cell-per-minute timeline per device, and the link-health table, the computer diagnosing its own wires.",
                "action": "The action plane: Reset board on CAN 15 opens a confirm listing its preconditions, which the driver re-checks before sending. At 18:25 it is blocked: tview holds the port."}
    figs = []
    for n in found:
        figs.append(f'<figure><img alt="Converged mock: {n}" src="{mock_uri(n)}"><figcaption>{esc(captions[n])}</figcaption></figure>')
    missing = [n for n in names if n not in found]
    note = f'<p class="muted">Not yet captured: {", ".join(missing)}.</p>' if missing else ""
    intro = ('<p class="muted">Captured from the parallel builder’s clickable mock (<code>prototypes/v6-converged/</code>), '
             'replaying the real Oct 21 rack at 18:11, 18:25 and 19:02. Looked at for this report, not driven by it.</p>')
    return f'{intro}<div class="mock">{"".join(figs)}</div>{note}'


def build() -> str:
    picks = "".join(pick_html(i + 1, p) for i, p in enumerate(PICKS))
    examples = "".join(example_html(i + 1, e) for i, e in enumerate(TEN))
    kvaser = data_uri(SURVEY / "kvaser-canking.webp", crop=(48, 520, 865, 790), max_width=760, quality=66)
    sources = "".join(f'<li id="src-{n}"><a href="{esc(url)}">{esc(title)}</a></li>' for n, (title, url) in SRC.items())

    body = f"""
<main>
<div class="kicker">BAM Robotics · IO diagnostics · design review · 2026-10-22</div>
<h1>IO diagnostics: the converged design</h1>
<p class="lede">Your Oct 22 feedback on round 1, set against ~50 tools that already do part of this, turned into one design. Read the verdict, then go as deep as you want.{cite(1, 2)}</p>

<section class="verdict" id="verdict">
  <h2>The design in five lines</h2>
  <ol>
    <li><b>A small top-bar chip</b> (“IO 3 ⚠”) with a Recent list opens one window. It does not live in Ubuntu’s Quick Settings.{cite(1, 2)}</li>
    <li><b>The window is an entity tree plus detail:</b> icon · status · name · id or count, search on top, parents take their worst child’s status; click a row for RealSense-Viewer-level detail.{cite(1)}</li>
    <li><b>The same tree flips to a table</b> (status, message, rate measured vs expected, last seen, firmware, errors), and an <b>activity log</b> runs underneath.{cite(1)}</li>
    <li><b>Two sources build the tree:</b> the OS gives the USB skeleton, drivers hang buses and nodes under their device, and every edge carries its own link health.{cite(1)}</li>
    <li><b>Drivers own everything device-specific:</b> they emit status records and register utility buttons; IO diagnostics renders and never speaks a device protocol.{cite(1)}</li>
  </ol>
  <div class="decisions">
    <div class="decision"><div class="q">Read-only or command?</div><b class="a">Both, as two planes</b>
      <p>The <b>status plane</b> is read-only and always on. The <b>action plane</b> is how printers do it: the driver registers its utilities, the window shows the buttons, the driver runs them.{cite(1, 5)}</p></div>
    <div class="decision"><div class="q">Who may talk to the hardware?</div><b class="a">Whoever owns the port</b>
      <p>While the stack runs, IO diagnostics only listens. When nothing runs, it may briefly start the driver’s own check after a holder and bus-lock check. On Oct 21 a config read collided with an open tview, the exact case this prevents.{cite(1, 2)}</p></div>
    <div class="decision"><div class="q">What is the contract?</div><b class="a">ROS DiagnosticStatus + 3 fields</b>
      <p><code>path</code>, <code>expected_period_s</code> and <code>identity</code> (Home Assistant <code>device_info</code>, whose <code>via_device</code> is the parent edge). No new schema, and no ROS needed to emit it.{cite(1, 12, 25)}</p></div>
  </div>
  <nav class="toc">
    <a href="#picked">What you picked</a><a href="#ten">Ten examples</a><a href="#layout">Layout</a><a href="#planes">Two planes</a><a href="#ownership">Ownership</a><a href="#contract">Contract</a><a href="#sources-tree">Two sources</a><a href="#wires">Wires</a><a href="#stable">Stable sub-assembly</a><a href="#lichtblick">Lichtblick?</a><a href="#mock">Mock</a><a href="#decide">Decisions</a><a href="#source-index">Sources</a>
  </nav>
</section>

{hero_html()}

<h2 id="picked">What you picked</h2>
<p class="muted">Your own screenshots from the Oct 22 note, grouped by what each one added. Quotes are verbatim.{cite(2)}</p>
{picks}

<h2 id="ten">Ten examples worth stealing from</h2>
<p class="muted">Picked from about 50 tools across three survey lanes: four ROS-native, four vendor tools, two general device managers{cite(3, 4, 5)}. Each card: the pattern, and exactly what IO diagnostics takes from it.</p>
{examples}
{also_html()}

<h2 id="layout">Layout: chip → tree + detail → table → activity log</h2>
<figure>{svg_layout()}<figcaption>① The chip and its Recent list. ② The window: tree on the left (icon · status · name · id/count, worst child rolls up), detail on the right with only the buttons a driver registered. ③ The activity log. ④ Table mode: the same rows with aligned columns. Device names and states are the real Oct 21 rack; values not known are drawn as “…” or “·”.{cite(1, 32)}</figcaption></figure>
<p>Shape status two ways, never by color alone: a filled circle is OK, a triangle is WARN, a square is ERROR, a dashed ring is STALE or “no driver”. Tuner X repeats every color as a sentence; medkit uses disc, ring and square{cite(3, 4)}.</p>

<h2 id="planes">The two planes: read-only status, driver-owned actions</h2>
<figure>{svg_planes()}<figcaption>Status flows one way and cannot disturb anything. Actions exist only if a driver registered them, and the driver, which already owns the port and the protocol, executes them. The host never sends a device command itself.{cite(1)}</figcaption></figure>
<p>This answers “is this read only or do we also allow commanding?” with both, kept apart. Off-the-shelf twins already do this: Home Assistant <code>button</code> and <code>update</code> entities, ros2_medkit’s Operations tab, and DroneCAN’s Restart / Update firmware beside the node’s identity{cite(1, 4, 5, 15)}. Staleness is computed, not reported: each record declares its period, and one older than about 3× that period turns STALE, ROS diagnostics’ fourth level{cite(1, 3)}.</p>

<h2 id="ownership">Ownership: whoever owns the port does the talking</h2>
<figure>{svg_ownership()}<figcaption>Case A is the normal one once the stack runs. Case B covers the bench before anything runs; “blocked: bus busy (tview pid 640623)” is a state the window shows, not an error it hides.{cite(1, 32)}</figcaption></figure>
<p>Permissions reduce to a rule each driver already obeys: one owner per port, the same move as a single writer per piece of state. The holder check reads <code>/proc/locks</code> and never takes the lock itself, because a registry that test-acquires the rack lock makes an experiment refuse to start (measured in round 1){cite(36)}.</p>

<h2 id="contract">The contract</h2>
<p>ROS <code>DiagnosticStatus</code> has been stable since ROS 1 and every viewer in the survey reads it{cite(3, 12)}. It lacks four things: a tree position, a staleness period, structured identity and update state. Three fields and one optional record add them, borrowed rather than invented{cite(1, 5, 25, 26)}.</p>
{contract_html()}

<h2 id="sources-tree">Two sources build one tree</h2>
<figure>{svg_two_sources()}<figcaption>Grey rows come from the OS and exist even with no driver; teal rows come from each driver’s diagnostics and hang under their device through <code>identity.via_device</code>. An unknown device, like the C920e, is still useful: presence, port, VID:PID, serial. Rows from the Oct 21 snapshot and that evening’s reports.{cite(1, 32)}</figcaption></figure>
<p>This is “which USB port is it plugged into”, answered by the OS (sysfs, <code>/dev/serial/by-id</code>, kernel log), the same skeleton USB Device Tree Viewer draws; REV’s tree shows the driver half, a USB bridge with a CAN bus and its nodes beneath{cite(4, 5, 27)}. Two traps from round 1: a D405’s USB descriptor serial differs from its SDK serial (219323070276 vs 2186222…), and with two adapters plugged the generic <code>/dev/fdcanusb</code> symlink can silently name the wrong one, so the tree keys adapters by serial{cite(6, 36)}.</p>

<h2 id="wires">The computer diagnoses its own wires</h2>
<p>Your idea, made first-class: every edge in the tree carries its own health, from passive sources{cite(1, 2)}.</p>
<figure>{svg_wires()}<figcaption>Four real edges from Oct 21. Green holds, amber is degraded, red dashed is broken. The camera mount counts as a wire too: a mechanical connection that drifted.{cite(1, 32)}</figcaption></figure>
<div class="table-wrap"><table>
  <thead><tr><th>Edge</th><th>Passive signals</th><th>Seen on Oct 21</th></tr></thead>
  <tbody>
    <tr><td>USB port → device</td><td>re-enumerations per port per hour, disconnects, link speed (a D405 on USB 2 instead of 3.2), enumeration failures</td><td>D405 …4411 dropped 4 s; 28B3A25D enumerated 5 times in 17 s</td></tr>
    <tr><td>adapter → CAN bus</td><td>bus state (error-active → passive → bus-off), error counters, ACK-less frames, bus voltage dips</td><td>28B3A25D: adapter fine, bus dead</td></tr>
    <tr><td>bus → node</td><td>reply loss per node, uptime resets (brown-outs), latched faults</td><td>CAN 15 fault 33 on bridge enable</td></tr>
    <tr><td>mount → sensor</td><td>calibration drift (a mechanical wire)</td><td>D405 …6036 moved 28 px</td></tr>
  </tbody>
</table></div>
<p>The kernel’s SocketCAN counters give the CAN vocabulary for free on any netdev adapter, but an fdcanusb is a USB-serial device with no kernel counters, so its bus health has to come from the moteus driver or round-trip checks{cite(4, 30)}. Kvaser CanKing’s Bus Statistics widget is close to the panel we want:{cite(4, 29)}</p>
<figure><img alt="Kvaser CanKing Bus Statistics widget: bus load, Rx/Tx messages, error frames, error counters, On Bus / Error Passive / Overrun lamps" src="{kvaser}"><figcaption>Kvaser CanKing 7 (Kvaser’s own half-light, half-dark render): bus load, totals and per-second rates, error counters, and On Bus / Error Passive / Overrun lamps.</figcaption></figure>
<p><b>The activity log, seeded with Oct 21:</b></p>
{wires_log_html()}

<h2 id="stable">Why it is a stable sub-assembly</h2>
<figure>{svg_stable()}<figcaption>The software stack changes every week; the hardware IO surface and the contract do not. Software can join later with the same records, which is what ROS diagnostics was designed for.{cite(1, 7)}</figcaption></figure>
<p>This is commitment 3 of your own <i>DP - Diagonistics</i>: “the diagnostic surface is stable”, the foundation tests, dashboards and the data flywheel build on{cite(7, 8)}. It is also the board-farm move: the station is permanent and its self-test is part of the station, separate from whatever device is under test{cite(35)}. Software components can join later (out of scope now){cite(1, 2)}.</p>

<h2 id="lichtblick">Open question: reuse Lichtblick as the dashboard?</h2>
<figure>{svg_lichtblick()}<figcaption>The same records can feed two views. Only the top one has the parts that make this tool.{cite(1, 3)}</figcaption></figure>
<div class="table-wrap"><table>
  <thead><tr><th></th><th>Lichtblick (MPL-2.0)</th><th>IO diagnostics</th></tr></thead>
  <tbody>
    <tr><td>Summary + detail of DiagnosticArray</td><td>yes, free, with stale timeout and hardware_id filter</td><td>tree + detail, roll-up</td></tr>
    <tr><td>Presence, plug and unplug events</td><td>no</td><td>yes (kernel log, coalesced)</td></tr>
    <tr><td>USB skeleton (which port)</td><td>no</td><td>yes</td></tr>
    <tr><td>Update available, actions</td><td>no</td><td>yes (update record, registered buttons)</td></tr>
    <tr><td>Top-bar chip</td><td>no (a separate app; no documented component embed)</td><td>yes</td></tr>
    <tr><td>Needs</td><td>a foxglove-sdk WebSocket host (MIT, no ROS); unproven end to end</td><td>one small Python process</td></tr>
  </tbody>
</table></div>
<p><b>Recommendation:</b> own the small chip, tree and actions; emit the standard records so Lichtblick, Foxglove and rqt work as second views, and prove the bridge with a ~30-minute spike{cite(1, 3, 11, 20)}. This matches the July decision to own the shell and treat any viewer as a replaceable lens{cite(11)}.</p>

<h2 id="mock">Converged mock</h2>
{mock_html()}

<h2 id="decide">Decision surface</h2>
<h3>Settled</h3>
<ul>
  <li>Scope: a <b>hardware IO diagnostics dashboard</b>, RealSense-Viewer level, that works before any robot stack runs.{cite(1, 2)}</li>
  <li>Layout: chip with Recent → tree + detail → table mode → activity log; not in Quick Settings.{cite(1, 2)}</li>
  <li>Two planes; read-only by default; a button only when a driver registered it; every action confirmed; blocked while the port is held.{cite(1)}</li>
  <li>Ownership rule: whoever owns the port does the talking.{cite(1)}</li>
  <li>Contract: DiagnosticStatus + <code>path</code> + <code>expected_period_s</code> + <code>identity</code>; update as Home Assistant’s update entity.{cite(1)}</li>
  <li>Stack default: V2’s single small Python process with a local web window; the plugin is each driver package’s own <code>diagnostics.py</code>, found by entry point.{cite(1, 6)}</li>
</ul>
<h3>Open, each with a recommendation and the default if you say nothing</h3>
<ol class="qs">
  <li><b>1. Exact STALE rule</b><span class="rec">Recommend STALE when a record is older than max(3 × expected_period_s, 5 s). 3× is the spec; the 5 s floor matches rqt, Lichtblick and the aggregator, so a fast record does not flicker.</span><br><span class="def">Default: that rule.</span></li>
  <li><b>2. Actions while the stack owns the port</b><span class="rec">Recommend: show the button disabled with the owner named (“owned by pid …”) in v1; forward through the stack driver’s own service later.</span><br><span class="def">Default: disabled, owner named.</span></li>
  <li><b>3. Where “latest version” comes from</b><span class="rec">Recommend a per-model table shipped inside each driver package, no network; “unknown” is OK-level; update available is WARN, never ERROR.</span><br><span class="def">Default: local table.</span></li>
  <li><b>4. Active link checks</b><span class="rec">Recommend passive signals and driver-reported counters, plus V2’s one gated comms check per newly settled adapter, only when the port and rack lock are free.</span><br><span class="def">Default: that.</span></li>
  <li><b>5. Lichtblick spike now or after v1</b><span class="rec">Recommend after the mock is accepted; it changes nothing in v1 because the records are already in its shape.</span><br><span class="def">Default: after v1.</span></li>
</ol>
<h3>Out of scope</h3>
<ul>
  <li><b>Software components</b> (nodes, planners, policies): later, with the same records.</li>
  <li><b>The CAD / 3D view</b> that paints these statuses onto links in the model.</li>
  <li>Robot control or motion, fleet and cloud views, long-term history (that belongs in a time-series store).</li>
</ul>

<h2 id="source-index">Source index</h2>
<ol class="sources">{sources}</ol>
<p class="foot">Built by <code>docs/build_design_report.py</code>. Vault note: <a href="{esc(obsidian(VAULT_NOTE))}">IO Diagnostics — Prior Art and Converged Design (2026-10-22)</a>. Repo: <a href="file:///home/bam/io-diagnostics/README.md">/home/bam/io-diagnostics</a>. Claude Code · Opus 5.5.</p>
</main>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>IO Diagnostics Design</title>
<style>{CSS}</style>
</head>
<body>{body}
</body>
</html>
"""


def main() -> int:
    page = build()
    OUT.write_text(page, encoding="utf-8")
    total_images = sum(image_bytes.values())
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB; {len(image_bytes)} images, {total_images / 1e6:.2f} MB raw WebP)")
    mock = "embedded" if (MOCK_DIR / "hero.png").exists() else "slot (mock not landed yet)"
    print(f"converged mock: {mock}")
    for name, size in sorted(image_bytes.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {size / 1024:6.1f} KB  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
