"""Every reset ladder RobotDx knows, most specific first: a device takes the first profile that matches it."""

from __future__ import annotations

from io_diagnostics.recovery.profile import RecoveryProfile
from io_diagnostics.recovery.profiles import fdcanusb, moteus_board, obsbot_ptz, realsense, usb_device

# WHY this order: the generic usb_device matches every USB device, so it is last
ALL: tuple[RecoveryProfile, ...] = (fdcanusb.PROFILE, realsense.PROFILE, obsbot_ptz.PROFILE, moteus_board.PROFILE,
                                    usb_device.PROFILE)
BY_NAME = {profile.name: profile for profile in ALL}


def profile_for(keys: list[str]) -> RecoveryProfile | None:
    return next((profile for profile in ALL if profile.matches_keys(keys)), None)
