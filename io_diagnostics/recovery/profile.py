"""A device class's reset ladder: which devices it covers, how to tell it is healthy, and its rungs in order.

One profile per device class, one per file in ``io_diagnostics/recovery/profiles/`` (Python ground truth: hand-authored
records are frozen dataclasses, the tables in docs/device-reset.md and the report are generated from them).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from io_diagnostics.recovery.rung import KINDS, PAGE, Rung

# How the ladder tells the device is healthy again (``RecoveryProfile.probe``), docs/device-reset.md "Probes"
PROBE_PRESENT = "present"  # on USB with its serial, configured, every interface bound to a driver, its /dev nodes there
PROBE_TTY = "tty_reply"  # present, and its tty answers `can status` on a fresh open within 1 s (fdcanusb)
PROBE_V4L2 = "v4l2_querycap"  # present, and VIDIOC_QUERYCAP answers on a fresh open of its video node (UVC; shared with a stream)
PROBE_PLUGIN = "plugin_check"  # the driver plugin's own check_comms dry run says it answers (a CAN board)
PROBES = (PROBE_PRESENT, PROBE_TTY, PROBE_V4L2, PROBE_PLUGIN)


@dataclass(frozen=True)
class RecoveryProfile:
    name: str  # fdcanusb | realsense | obsbot_ptz | moteus_board | usb_device
    title: str  # "mjbots fdcanusb (CAN-FD adapter)"
    matches: tuple[str, ...]  # "usb:0483:5740", "usb:8086:0b*", "product:fdcanusb", "can-node" (fnmatch on the device's keys)
    probe: str  # one of PROBES
    rungs: tuple[Rung, ...]
    page_text: str  # what the human is asked to do when every rung failed: "{name}" and "{port}" are filled in
    expects_nodes: tuple[str, ...] = ()  # sysfs class dirs a healthy device exposes: ("tty",) or ("video4linux",)
    node_rule: bool = True  # the installer's udev rule opens its usbfs node to plugdev (False: a vendor rule already does)
    node_rule_why: str = ""  # why not, when node_rule is False
    udev_match: tuple[tuple[str, str], ...] = ()  # the installer's ATTR{} pairs for this class (idVendor, idProduct / product)
    owner_locks: tuple[str, ...] = ()  # lock files whose holder owns the device (the rack bus lock): someone else holding one blocks
    quiet_s: float = 600.0  # an incident closes once the device has answered this long; a failure before that continues it
    max_incidents_per_day: int = 3  # beyond this the ladder pages only: a device that keeps dying needs a look, not more resets

    def __post_init__(self) -> None:
        if self.probe not in PROBES:
            raise ValueError(f"{self.name}: unknown probe {self.probe!r}")
        kinds = [rung.kind for rung in self.rungs]
        if not kinds or kinds[-1] != PAGE:
            raise ValueError(f"{self.name}: the last rung pages a human")
        if any(kind not in KINDS for kind in kinds) or len(set(kinds)) != len(kinds):
            raise ValueError(f"{self.name}: each kind at most once, in order")
        if not self.node_rule and not self.node_rule_why:
            raise ValueError(f"{self.name}: say why the installer leaves its node alone")

    def matches_keys(self, keys: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(key, pattern) for key in keys for pattern in self.matches)

    def rung(self, kind: str) -> Rung | None:
        return next((rung for rung in self.rungs if rung.kind == kind), None)

    def to_json(self) -> dict:
        return {"name": self.name, "title": self.title, "matches": list(self.matches), "probe": self.probe,
                "rungs": [rung.to_json() for rung in self.rungs], "page_text": self.page_text, "quiet_s": self.quiet_s,
                "max_incidents_per_day": self.max_incidents_per_day, "owner_locks": list(self.owner_locks)}
