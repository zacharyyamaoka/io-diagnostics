"""Show one direction's top-bar presence as a real AppIndicator (sandbox only).

    /usr/bin/python3 /home/bam/io-diagnostics/tools/topbar_runner.py --variant v4 --frame F1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "prototypes" / "v5-station"))

from contract.indicator import Indicator  # noqa: E402  (pins Gtk 3.0 before Gtk is imported)
from gi.repository import GLib, Gtk  # noqa: E402

from contract.menus import plug_journal, registry_label, station_badge  # noqa: E402


def build(variant: str, frame: str) -> dict:
    if variant == "v2":
        return plug_journal(frame)
    if variant == "v4":
        return registry_label(frame)
    import drift  # noqa: PLC0415

    return station_badge(frame, drift.drift(frame)[1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=["v2", "v4", "v5"])
    parser.add_argument("--frame", default="F3")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()
    model = build(args.variant, args.frame)
    tray = Indicator(f"io-diagnostics-{args.variant}", "IO diagnostics")
    tray.show(model)
    if args.notify and model.get("notify"):
        GLib.timeout_add_seconds(2, lambda: (tray.notify(model["notify"]["title"], model["notify"]["body"]), False)[1])
    Gtk.main()
