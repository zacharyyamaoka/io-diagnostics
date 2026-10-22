"""Smoke test: the sandbox shell starts, Eval works with quotes, and who owns notifications."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell  # noqa: E402

time.sleep(3)
shell.leave_overview()
time.sleep(1)
print("eval with quotes:", shell.shell_eval("(() => { const s = 'a' + \"b\"; return s; })()"))
print("status area:", shell.status_keys())
print("notification names:", [name for name in shell.bus_names() if "otif" in name])
print("shell names:", [name for name in shell.bus_names() if "Shell" in name or "gnome" in name.lower()][:12])
print("shot:", shell.grab("smoke.png"))
