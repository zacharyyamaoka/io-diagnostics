"""Intel RealSense depth cameras (D405, D435): their reset ladder."""

from __future__ import annotations

from io_diagnostics.recovery.profile import PROBE_PRESENT, RecoveryProfile
from io_diagnostics.recovery.profiles.common import DEAUTHORIZE, PAGE_HUMAN, PORT_POWER_CYCLE, USB_PORT_RESET
from io_diagnostics.recovery.rung import NATIVE, REOPEN, VIA_PLUGIN, Rung

PROFILE = RecoveryProfile(
    name="realsense",
    title="Intel RealSense depth camera",
    matches=("usb:8086:0b*",),
    probe=PROBE_PRESENT,
    expects_nodes=("video4linux",),
    node_rule=False,
    node_rule_why="librealsense's own rule (60-librealsense2-udev-rules.rules) already gives plugdev 0666 on its USB node",
    udev_match=(("idVendor", "8086"), ("idProduct", "0b*")),
    rungs=(
        Rung(REOPEN, "Restart its stream",
             why="A RealSense whose frames went stale usually only needs its pipeline restarted by whoever streams it; RobotDx "
                 "checks it is still on USB with its video nodes, and resets nothing.",
             expect="Still there: the owner restarts its pipeline; nothing was reset.",
             timeout_s=5.0, settle_s=0.0),
        Rung(NATIVE, "Camera hardware reset (librealsense hardware_reset)",
             why="The camera's own restart: librealsense's hardware_reset() reboots its firmware (asynchronous, and it invalidates "
                 "every handle), through the intel_rgbd driver's Reboot action with its own holder check. It also clears a "
                 "wedged vision processor that no USB rung can see.",
             expect="Off USB for about 3 s, back with the same serial; every option returns to its firmware default and the "
                    "owner restarts its pipeline. The driver refuses while something still has the camera open.",
             timeout_s=60.0, settle_s=15.0, via=VIA_PLUGIN, plugin="intel_rgbd", action="reboot"),
        USB_PORT_RESET,
        DEAUTHORIZE,
        PORT_POWER_CYCLE,
        PAGE_HUMAN,
    ),
    page_text="Replug RealSense {serial} (USB port {port}): it still does not stream after {tried}.",
)
