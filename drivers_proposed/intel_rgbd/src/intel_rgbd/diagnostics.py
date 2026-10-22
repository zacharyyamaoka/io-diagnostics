"""intel_rgbd diagnostics: a RealSense camera's identity, versions, link, config and reset.

PROPOSED file: lands at src/core/mdp/agent/sensors/intel_rgbd/src/intel_rgbd/diagnostics.py.

Wraps what the driver already has (paths in that package):
- identity: sysfs USB serial (passive); the SDK serial comes from real.find_device_serials(),
  which talks to the device, so it is gated and cached as an alias USB serial -> SDK serial;
- versions: CameraDescription.firmware (real.py camera_info.firmware_version) plus
  camera_info.recommended_firmware_version (new: unused today) -> update available;
- link: sysfs speed (passive) and camera_info.usb_type_descriptor (gated);
- config: RealSenseCamera.read_back() (gated: opening a camera another process streams kills it);
- reset: RealSenseCamera.hardware_reset() (exists; reverts every option to default, per its
  README, so the confirm dialog says so).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from contract.diagnostics import CHANGES, PASSIVE, TOUCHES, Action, Reading, UsbMatch  # noqa: E402

DRIVER_PYPROJECT = Path("/home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/core/mdp/agent/sensors/intel_rgbd/pyproject.toml")
USB_GENERATION = {480: "USB 2", 5000: "USB 3.2 Gen 1", 10000: "USB 3.2 Gen 2"}


class RealSenseDiagnostics:
    name = "intel_rgbd"
    matches = (UsbMatch("8086", "0b5b"), UsbMatch("8086", "0b07"))  # D405, D435
    kinds = {"identity": PASSIVE, "versions": TOUCHES, "config": TOUCHES, "comms": PASSIVE, "reset": CHANGES}
    camera = True
    _free = (("camera_free", "No other process streams this camera (camera_holders)"),)
    actions = (
        Action("run_comms_check", "Run frame check", "Stream 2 s of depth + colour and compare the frame rate with "
               "the profile's 30 Hz; reads the USB descriptor type.", "query", "device",
               "Stream 2 s from {name} and measure frame rate. Opening a camera another process streams would kill that stream.",
               _free, TOUCHES),
        Action("reset", "Hardware reset…", "RealSenseCamera.hardware_reset(): the camera re-enumerates and EVERY option "
               "reverts to its default (per the driver README).", "interrupts", "device",
               "Hardware-reset {name}? It drops off USB for about 3 s and every option returns to default.", _free),
        Action("update_firmware", "Update firmware…", "Flash the recommended firmware (camera_info.recommended_firmware_version).",
               "changes", "device", "Flash firmware {latest} onto {name}? Takes about 1 min; do not unplug.",
               (("update_available", "Recommended firmware is newer than installed"),) + _free),
    )

    def nodes(self, device) -> list[str]:
        return list(device.video_nodes)

    def identity(self, device) -> dict[str, Reading]:
        model = "D405" if device.pid == "0b5b" else "D435"
        readings = {"model": Reading(f"RealSense {model}", "USB descriptor (sysfs)"),
                    "usb_serial": Reading(device.serial, "USB descriptor (sysfs)"),
                    "port": Reading(device.port, "sysfs")}
        alias = device.extra.get("sdk_serial")
        readings["sdk_serial"] = (Reading(alias, "alias learned from an earlier gated read (fixture)")
                                  if alias else Reading(None, "needs one gated read: USB serial != SDK serial", state="unknown"))
        return readings

    def comms(self, device) -> dict[str, Reading]:
        speed = USB_GENERATION.get(device.speed_mbps, f"{device.speed_mbps} Mb/s")
        state = "warn" if device.speed_mbps < 5000 else "ok"
        return {"link": Reading(speed, "sysfs speed", state=state),
                "plugged_at": Reading(device.plugged_at, "sysfs node time")}

    def versions(self, device) -> dict[str, Reading]:  # TOUCHES (librealsense camera_info)
        raise RuntimeError("versions talk to the camera; call through contract.diagnose")

    def last_known_versions(self, device) -> dict[str, Reading]:
        driver = re.search(r'^version\s*=\s*"([^"]+)"', DRIVER_PYPROJECT.read_text(), re.M) if DRIVER_PYPROJECT.exists() else None
        return {"driver": Reading(f"intel_rgbd {driver[1] if driver else '?'}", "pyproject.toml")}

    def config(self, device) -> dict[str, Reading]:  # TOUCHES
        raise RuntimeError("config opens the camera; call through contract.diagnose")

    def reset(self, device, *, confirm: bool) -> Reading:  # CHANGES
        raise RuntimeError("reset changes state; call through contract.request_reset")


DIAGNOSTICS = RealSenseDiagnostics()
