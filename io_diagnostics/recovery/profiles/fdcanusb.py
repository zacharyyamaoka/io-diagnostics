"""mjbots fdcanusb, the arm's CAN-FD adapter: its reset ladder.

WHY (Zach, 2026-11-14): 0607623A stopped answering at 08:57 while staying enumerated (no kernel USB event; its own
`can status` hung); the reset it needed was root-only, and the arm sat dead 7 h until he replugged it by hand.
"""

from __future__ import annotations

from io_diagnostics.recovery.profile import PROBE_TTY, RecoveryProfile
from io_diagnostics.recovery.profiles.common import DEAUTHORIZE, PAGE_HUMAN, PORT_POWER_CYCLE, USB_PORT_RESET
from io_diagnostics.recovery.rung import NATIVE, REOPEN, USBRESET, VIA_COMMAND, Rung

PROFILE = RecoveryProfile(
    name="fdcanusb",
    title="mjbots fdcanusb (CAN-FD adapter)",
    matches=("usb:0483:5740", "usb:0483:5740:*", "product:fdcanusb"),
    probe=PROBE_TTY,
    expects_nodes=("tty",),
    udev_match=(("manufacturer", "mjbots"), ("product", "fdcanusb")),
    # WHY: whoever holds the rack bus lock owns the arm's bus (an experiment, arm-hold): nobody else resets its adapter
    owner_locks=("{rack_bus_lock}",),
    rungs=(
        Rung(REOPEN, "Reopen its port",
             why="arm-hold reopens its port itself after 5 exchange timeouts; a fresh open that asks `can status` tells a stale "
                 "handle from a dead adapter, and resets nothing. When the owner calls, its own reopen is this rung.",
             expect="It answers: only the handle was stale, the owner reopens and keeps going, nothing on the bus is touched.",
             timeout_s=5.0, settle_s=0.0),
        Rung(NATIVE, "Reset the adapter by serial (fdcanusb-reset)",
             why="The adapter has no restart command of its own on the wire. Sim to Real's helper (bam_rack_twin.hardware."
                 "fdcanusb_reset, installed as ~/.local/bin/fdcanusb-reset) resets it by its serial with USBDEVFS_RESET, the "
                 "same call arm-hold makes, so two identical adapters are never mixed up and it waits for /dev/serial/by-id.",
             expect="Off the bus for about 2 s and back with the same serial; open ports see a hang-up and reopen; the boards "
                    "keep holding position on their timeout_mode 10 watchdog meanwhile.",
             timeout_s=20.0, settle_s=10.0, via=VIA_COMMAND,
             command=("{fdcanusb_reset}", "--serial", "{serial}", "--yes", "--wait-s", "{wait_s}"),
             exit_codes=((0, "back"), (2, "no_permission"), (3, "not_found"), (4, "not_back")),
             same_as=USBRESET),
        USB_PORT_RESET,
        DEAUTHORIZE,
        PORT_POWER_CYCLE,
        PAGE_HUMAN,
    ),
    page_text="Replug fdcanusb {serial} (USB port {port}) or switch its hub off and on: it still does not answer after {tried}. "
              "The boards are holding position on their own watchdog meanwhile.",
)
