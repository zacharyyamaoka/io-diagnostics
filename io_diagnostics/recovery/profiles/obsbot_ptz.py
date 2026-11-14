"""OBSBOT PTZ webcams (Tiny 4K, plain UVC): their reset ladder. No native reset: the camera exposes none."""

from __future__ import annotations

from io_diagnostics.recovery.profile import PROBE_V4L2, RecoveryProfile
from io_diagnostics.recovery.profiles.common import DEAUTHORIZE, PAGE_HUMAN, PORT_POWER_CYCLE, USB_PORT_RESET
from io_diagnostics.recovery.rung import REOPEN, Rung

PROFILE = RecoveryProfile(
    name="obsbot_ptz",
    title="OBSBOT PTZ webcam (UVC)",
    matches=("usb:3564:*",),
    probe=PROBE_V4L2,
    expects_nodes=("video4linux",),
    udev_match=(("idVendor", "3564"),),
    rungs=(
        Rung(REOPEN, "Reopen its video node",
             why="A UVC camera often only needs its stream reopened. VIDIOC_QUERYCAP on a fresh open of its video node is shared "
                 "with a running stream, so it tells a dead camera from a dead stream without taking the camera from anyone.",
             expect="It answers: the owner (obsbot-viewer, a recorder) restarts its stream; nothing was reset.",
             timeout_s=5.0, settle_s=0.0),
        USB_PORT_RESET,
        DEAUTHORIZE,
        PORT_POWER_CYCLE,
        PAGE_HUMAN,
    ),
    page_text="Replug the OBSBOT camera (USB port {port}): it still does not answer after {tried}.",
)
