"""Refuse anything but ``describe`` or ``diagnose ... --passive``, then exec the real driver command.

    <python> passive_guard.py <driver argv...> diagnose --device usb:1-3 --passive

WHY a guard in front of the real drivers: the live-passive declarations rely on the host appending their
``diagnose_args`` (``--passive``). If that ever breaks (a host bug, or a mutation test of the runner, which on
2026-10-22 ran moteus_servo's ACTIVE diagnose once against the rack's fdcanusb), the driver must not run at all.
This check does not depend on any io-diagnostics code. Stdlib only.
"""

from __future__ import annotations

import os
import sys

VERBS = ("describe", "diagnose", "act")


def main(argv: list[str]) -> int:
    verbs = [index for index, word in enumerate(argv) if word in VERBS]
    if not argv or not verbs:
        print(f"passive_guard: no verb in {argv!r}; refused", file=sys.stderr)
        return 2
    verb = argv[verbs[0]]
    after = argv[verbs[0] + 1:]
    if verb == "act" or (verb == "diagnose" and "--passive" not in after):
        print(f"passive_guard: refused {verb} without --passive (argv after the verb: {after!r})", file=sys.stderr)
        return 2
    os.execvp(argv[0], argv)
    return 1  # not reached


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
