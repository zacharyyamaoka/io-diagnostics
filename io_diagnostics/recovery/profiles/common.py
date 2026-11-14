"""The USB rungs every USB device class shares, and the page. A profile takes the ones that apply, in this order."""

from __future__ import annotations

from io_diagnostics.recovery.rung import AUTHORIZED, PAGE, POWER_CYCLE, USBRESET, Rung

USB_PORT_RESET = Rung(
    USBRESET, "USB port reset (usbreset)",
    why="The kernel resets this one device's USB link and rebinds its drivers (USBDEVFS_RESET on /dev/bus/usb/BBB/DDD, the stock "
        "usbreset tool). Power stays on and nothing else on the hub is touched, so it comes before the heavier rungs.",
    expect="Gone for about a second, back with the same device number; whatever had it open sees a hang-up and must reopen.",
    timeout_s=15.0, settle_s=15.0)

DEAUTHORIZE = Rung(
    AUTHORIZED, "Kernel deauthorize and reauthorize",
    why="Writes 0 then 1 to its sysfs `authorized`: every driver unbinds and the device is configured again from scratch, which "
        "clears a stuck configuration that a port reset keeps. Still no power cut.",
    expect="Gone for about a second, back with a new device number and fresh /dev nodes; whatever had it open must reopen.",
    timeout_s=10.0, settle_s=15.0)

PORT_POWER_CYCLE = Rung(
    POWER_CYCLE, "Port power cycle",
    why="The hub cuts the port's 5 V and restores it (the kernel's port `disable`, the interface uhubctl uses on Linux 6.0+). The "
        "only rung that restarts a frozen microcontroller, so it is last before a person; only on a hub that can switch each "
        "port's power (per-port power switching), which most hubs and many root hubs cannot.",
    expect="Off for 2 s, then it boots and enumerates like a fresh plug-in (2 to 5 s); anything else on that port goes down too.",
    timeout_s=15.0, settle_s=20.0)

PAGE_HUMAN = Rung(
    PAGE, "Page a human",
    why="Every rung RobotDx may run failed or is not available here: a person has to replug it or cut its power.",
    expect="An alarm with what to do, once per incident (the alarms log, the event, and the deployment's page command if set).",
    timeout_s=10.0, settle_s=0.0)
