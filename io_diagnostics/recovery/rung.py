"""One step of a device's reset ladder: what it does, why it is at this place, and what a person should expect.

Zach, 2026-11-14: "when you deploy a robot you cannot be there to unplug stuff manually" (fdcanusb 0607623A stopped
answering at 08:57 and the arm sat dead 7 h until he replugged it). docs/device-reset.md has the ladder and its rules.
"""

from __future__ import annotations

from dataclasses import dataclass

# The mechanisms a rung can use, gentlest first (docs/device-reset.md "The ladder"). A profile lists them in its order.
REOPEN = "reopen"  # open the device fresh and ask it: tells a stale handle from a dead device; resets nothing
NATIVE = "native"  # the device's own restart (RealSense hardware_reset, moteus `d reset`, fdcanusb USBDEVFS_RESET by serial)
USBRESET = "usbreset"  # USBDEVFS_RESET on its usbfs node: a port reset and a driver rebind, power stays on
AUTHORIZED = "authorized"  # the kernel deauthorizes and reauthorizes it: drivers unbind, it is configured again
POWER_CYCLE = "power_cycle"  # the hub cuts the port's VBUS and restores it: the only cure for a frozen microcontroller
PAGE = "page"  # tell a human, with what to do: the ladder's last rung, always available
KINDS = (REOPEN, NATIVE, USBRESET, AUTHORIZED, POWER_CYCLE, PAGE)
USB_KINDS = (USBRESET, AUTHORIZED, POWER_CYCLE)

# How a rung's own reset is run (``Rung.via``)
VIA_BUILTIN = "builtin"  # RobotDx's own code (reopen probes, usbfs ioctl, sysfs writes, the page)
VIA_COMMAND = "command"  # a stock or sibling tool with an exit-code contract (fdcanusb-reset)
VIA_PLUGIN = "plugin"  # a driver plugin's own `act` (intel_rgbd reboot, moteus_servo reboot), through its dry-run token


@dataclass(frozen=True)
class Rung:
    kind: str  # one of KINDS
    title: str  # what a person reads in the plan: "USB port reset (usbreset)"
    why: str  # plain words: why it is here and at this place in the order
    expect: str  # what happens if it works, and what is lost meanwhile
    timeout_s: float = 20.0  # the rung's own reset may take this long, then it is killed and counted as failed
    settle_s: float = 15.0  # after it, wait up to this long for the device to be back on USB before probing it
    unattended: bool = True  # False: only a person may run it (a reboot that drops a loaded joint)
    attended_why: str = ""  # why a person must run it, when unattended is False
    via: str = VIA_BUILTIN
    command: tuple[str, ...] = ()  # VIA_COMMAND: argv template; {serial} {port} {wait_s} {node} are filled in
    exit_codes: tuple[tuple[int, str], ...] = ()  # VIA_COMMAND: exit code -> outcome (back | no_permission | not_found | not_back)
    plugin: str = ""  # VIA_PLUGIN: the plugins.d name
    action: str = ""  # VIA_PLUGIN: the driver's standard action name (reboot)
    same_as: str = ""  # this rung IS another kind's mechanism (fdcanusb's native reset is a usbreset): that one is skipped after it
    danger: str = "interrupts"  # docs/actions-vocabulary-v0.1.md: interrupts | moves

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown rung kind {self.kind!r}")
        if self.via == VIA_COMMAND and not self.command:
            raise ValueError(f"{self.title}: a command rung names its command")
        if self.via == VIA_PLUGIN and not (self.plugin and self.action):
            raise ValueError(f"{self.title}: a plugin rung names its plugin and action")
        if not self.unattended and not self.attended_why:
            raise ValueError(f"{self.title}: a person-only rung says why")

    def to_json(self) -> dict:
        return {"kind": self.kind, "title": self.title, "why": self.why, "expect": self.expect, "timeout_s": self.timeout_s,
                "settle_s": self.settle_s, "unattended": self.unattended, "attended_why": self.attended_why, "via": self.via,
                "command": list(self.command), "plugin": self.plugin, "action": self.action, "same_as": self.same_as,
                "danger": self.danger}
