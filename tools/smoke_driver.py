"""Smoke test: the sandbox shell starts, Eval works, and a screenshot of its top bar can be taken."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell  # noqa: E402

time.sleep(3)
shell.leave_overview()
time.sleep(1)
print("eval 1+1:", shell.shell_eval("1+1"))
print("status area:", shell.status_keys())
print("shot:", shell.grab("smoke.png"))
