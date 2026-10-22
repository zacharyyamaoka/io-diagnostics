"""V2 "Plug Journal": a standalone top-bar tray (AppIndicator) + GNOME notifications.

    /usr/bin/python3 /home/bam/io-diagnostics/prototypes/v2-plug-journal/io_tray.py --frame F1 --notify

Event-first: the menu leads with what changed (coalesced plug notices), then one submenu per
device. Drivers are found by naming convention (binding.py); every row comes from the driver's
own diagnostics through contract.views. Live mode listens to udev and coalesces flaps. Run it in
tools/sandbox.sh for screenshots; it is a prototype, not something to start in Zach's session.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from contract.indicator import Indicator  # noqa: E402  (pins Gtk 3.0 before Gtk is imported)
from gi.repository import GLib, Gtk  # noqa: E402

from contract.menus import plug_journal  # noqa: E402
from io_facts import journal, udev  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", default="F3", choices=["F1", "F2", "F3"], help="story frame (F3 = after the 19:00 restart)")
    parser.add_argument("--notify", action="store_true", help="show the frame's newest event as a banner after 2 s")
    parser.add_argument("--live", action="store_true", help="also listen to udev and announce coalesced plug events")
    args = parser.parse_args()

    tray = Indicator("io-diagnostics-v2", "IO diagnostics")
    model = plug_journal(args.frame)
    tray.show(model)
    if args.notify:
        GLib.timeout_add_seconds(2, lambda: (tray.notify(model["notify"]["title"], model["notify"]["body"]), False)[1])
    if args.live:
        coalescer = journal.Coalescer()
        process = udev.monitor()

        def on_notices(notices):
            for notice in notices:
                tray.notify(f"IO diagnostics · {notice.kind}", notice.text())
            return False

        def pump():
            for event in udev.parse_blocks(iter(process.stdout.readline, "")):
                GLib.idle_add(on_notices, coalescer.feed(event))

        threading.Thread(target=pump, daemon=True).start()
        GLib.timeout_add_seconds(1, lambda: (on_notices(coalescer.tick(dt.datetime.now())), True)[1])
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
