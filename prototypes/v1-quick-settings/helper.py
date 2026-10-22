"""V1 helper: runs the drivers' diagnostics OUT of gnome-shell and prints one JSON model.

    /usr/bin/python3 /home/bam/io-diagnostics/prototypes/v1-quick-settings/helper.py --frame F3

The extension spawns this (Gio.Subprocess) and renders the result; no driver code ever runs
inside the shell. Drivers are found through the io-diagnostics.json manifests (binding.py).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from contract.views import EVENTS, FRAMES, views  # noqa: E402


def model(frame: str) -> dict:
    device_views = views(frame)
    attention = [view for view in device_views if view.level in ("error", "warn", "blocked")]
    first = EVENTS[frame][0]
    return {
        "frame": frame, "clock": FRAMES[frame]["clock"],
        "subtitle": f"{len(attention)} need you" if attention else f"{len(device_views)} devices OK",
        "attention": bool(attention),
        "devices": [dataclasses.asdict(view) for view in device_views],
        "notify": {"title": f"IO diagnostics · {first[1]}", "body": first[2]},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame", default="F3")
    print(json.dumps(model(parser.parse_args().frame)))
