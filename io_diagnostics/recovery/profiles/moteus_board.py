"""A moteus board on a CAN-FD bus: its reset ladder. Not a USB device: when every board on a bus is silent, the suspect
is the adapter, and the fdcanusb ladder is the one to run.

Verified in the moteus firmware, not assumed (moteus-src 2b21a16f, fw/board_debug.cc:971): the diagnostic command
`d reset` calls NVIC_SystemReset() at once, with no motor-off first (`d flash` calls MoteusEnsureOff(); `d reset` does
not). The register protocol has no reboot register, and moteus_tool 1.1.1 has no restart flag: it sends `d reset` only
at the end of --restore-config. After a restart the board knows its position only within one revolution (moteus
client-tools docs), so a geared joint's output reference is gone.
"""

from __future__ import annotations

from io_diagnostics.recovery.profile import PROBE_PLUGIN, RecoveryProfile
from io_diagnostics.recovery.profiles.common import PAGE_HUMAN
from io_diagnostics.recovery.rung import NATIVE, REOPEN, VIA_PLUGIN, Rung

PROFILE = RecoveryProfile(
    name="moteus_board",
    title="moteus board (CAN-FD)",
    matches=("can:*",),
    probe=PROBE_PLUGIN,
    node_rule=False,
    node_rule_why="a CAN board has no USB node of its own: its adapter's rule covers the bus",
    rungs=(
        Rung(REOPEN, "Ask it again",
             why="A board that missed a query is often fine. The moteus_servo driver asks it again on a fresh transport "
                 "(Check communication), behind the port-holder check and the rack bus lock.",
             expect="It answers: nothing was wrong with the board; nothing is sent to it.",
             timeout_s=60.0, settle_s=0.0),
        Rung(NATIVE, "Restart the board (moteus `d reset`)",
             why="The board's own restart, through the moteus_servo driver's Reboot action (it is sent only to a board that "
                 "answers a query). It clears a latched fault that a stop does not.",
             expect="Off the bus for about a second, then it answers again with its faults cleared. Its output position is "
                    "known again only within one revolution: the arm must be homed before it moves.",
             timeout_s=60.0, settle_s=5.0, via=VIA_PLUGIN, plugin="moteus_servo", action="reboot",
             unattended=False, danger="moves",
             attended_why="`d reset` restarts the board at once with no motor-off first: a joint under gravity drops while it "
                          "reboots, and the homed map is void afterwards (Zach, 2026-11-13: home once per power-up, never re-home "
                          "without a reason). Only a person who has made the joint safe runs it."),
        PAGE_HUMAN,
    ),
    page_text="CAN {node} on fdcanusb {serial} does not answer: check its CAN cable and its 24 V. Restarting it needs a person "
              "(a reboot drops its joint and voids the homed map); a silent board restarts only when its supply is cycled.",
    quiet_s=600.0,
)
