"""obsbot_ptz_camera diagnostics: the worked example of ADDING a device type (amendment A2).

PROPOSED file: lands at src/core/mdp/agent/sensors/obsbot_ptz_camera/src/obsbot_ptz_camera/diagnostics.py.
Not plugged in on 2026-10-21; the prototypes inject a simulated OBSBOT to show it appear.

Wraps what the driver already has:
- presence + identity: discovery.find_obsbot_cameras() / types.DeviceInfo (sysfs vendor 3564);
- versions: obsbot_ptz_camera.__version__ + sysfs bcdDevice (firmware "1.2.5.4" is only
  hand-recorded in its DATASHEET; the vendor's extension unit is out of scope, so update = unknown);
- config: v4l2.V4l2ControlPlane.snapshot() + describe() (control ioctls are shared and do not
  contend with a video stream, per its README) over an explicit READ-ONLY name list;
- comms: sysfs speed + bcdUSB, and control round-trip latency (gated);
- reset: ImageControls.neutral() through the control plane: image controls only, NEVER pan,
  tilt or zoom (no motion).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from contract.diagnostics import CHANGES, PASSIVE, TOUCHES, Reading, UsbMatch  # noqa: E402

READ_ONLY_CONTROLS = ("brightness", "contrast", "saturation", "sharpness", "white_balance_automatic", "auto_exposure", "focus_automatic_continuous")


class ObsbotDiagnostics:
    name = "obsbot_ptz_camera"
    matches = (UsbMatch("3564"),)
    kinds = {"identity": PASSIVE, "versions": PASSIVE, "config": TOUCHES, "comms": PASSIVE, "reset": CHANGES}
    camera = False

    def nodes(self, device) -> list[str]:
        return list(device.video_nodes)

    def identity(self, device) -> dict[str, Reading]:
        return {"model": Reading(device.product or "OBSBOT Tiny", "USB descriptor (sysfs)"),
                "serial": Reading(device.serial, "USB descriptor (sysfs)"),
                "usb_id": Reading(device.usb_id, "sysfs")}

    def versions(self, device) -> dict[str, Reading]:
        return {"driver": Reading("obsbot_ptz_camera 0.1.0", "obsbot_ptz_camera.__version__"),
                "firmware": Reading(device.extra.get("bcd_device", "?"), "sysfs bcdDevice (vendor string needs the vendor tool)"),
                "update_available": Reading(None, "no vendor update source in the driver", state="unknown")}

    def comms(self, device) -> dict[str, Reading]:
        return {"link": Reading("USB 2 (480 Mb/s)" if device.speed_mbps == 480 else f"{device.speed_mbps} Mb/s", "sysfs speed")}

    def config(self, device) -> dict[str, Reading]:  # TOUCHES
        raise RuntimeError("config opens the control node; call through contract.diagnose")

    def reset(self, device, *, confirm: bool) -> Reading:  # CHANGES: image controls only
        raise RuntimeError("reset changes state; call through contract.request_reset")


DIAGNOSTICS = ObsbotDiagnostics()
