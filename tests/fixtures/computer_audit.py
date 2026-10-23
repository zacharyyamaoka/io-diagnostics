"""Run the computer plugin's diagnose in ITS env under a Python audit hook and refuse any /dev node it opens.

    uv run --project /home/bam/io-diagnostics/plugins/computer python tests/fixtures/computer_audit.py

Prints the plugin's JSON lines on stdout, then one JSON line on stderr: {"dev_opens": [...], "opens": N}. Exits 3 when
the plugin process opened a /dev node other than /dev/null (what subprocess.DEVNULL opens for nvidia-smi's stdin).
WHY an audit hook: every Python-level open (io.open, os.open, psutil's /proc and /sys readers) raises the "open"
audit event, so the plugin cannot reach a tty, video or CAN node without it showing here.
"""

import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "computer"
opened: list[str] = []


def hook(event: str, args: tuple) -> None:
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        opened.append(os.fsdecode(args[0]))


sys.path.insert(0, str(PLUGIN))
sys.addaudithook(hook)
import computer_diagnostics  # noqa: E402

code = computer_diagnostics.main(["diagnose", "--device", "host"])
dev = sorted({path for path in opened if path.startswith("/dev/") and path != "/dev/null"})
print(json.dumps({"dev_opens": dev, "opens": len(opened)}), file=sys.stderr)
sys.exit(3 if dev else code)
