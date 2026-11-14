"""Any other USB device RobotDx knows (a webcam, a hub, a USB-serial adapter): the plain USB ladder, no native reset."""

from __future__ import annotations

from io_diagnostics.recovery.profile import PROBE_PRESENT, RecoveryProfile
from io_diagnostics.recovery.profiles.common import DEAUTHORIZE, PAGE_HUMAN, PORT_POWER_CYCLE, USB_PORT_RESET
from io_diagnostics.recovery.rung import REOPEN, Rung

PROFILE = RecoveryProfile(
    name="usb_device",
    title="USB device",
    matches=("usb:*",),
    probe=PROBE_PRESENT,
    rungs=(
        Rung(REOPEN, "Check it is still there",
             why="RobotDx knows no way to ask this device anything, so it checks it is on USB, configured, with a driver bound "
                 "to every interface; whoever uses it reopens it.",
             expect="Still there: the owner reopens it; nothing was reset.",
             timeout_s=5.0, settle_s=0.0),
        USB_PORT_RESET,
        DEAUTHORIZE,
        PORT_POWER_CYCLE,
        PAGE_HUMAN,
    ),
    page_text="Replug {name} (USB port {port}): it is still not back after {tried}.",
)
