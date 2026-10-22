"""Sandbox debug: does the AppIndicator extension receive and show the V4 label?"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell  # noqa: E402

time.sleep(2)
shell.leave_overview()
process = subprocess.Popen(["/usr/bin/python3", str(shell.REPO / "tools/topbar_runner.py"), "--variant", "v4", "--frame", "F1"])
time.sleep(5)
shell.grab("label-debug.png")
key = next((k for k in shell.status_keys() if "io_diagnostics_v4" in k), None)
print("key:", key)
for js in (
    f"Main.panel.statusArea['{key}']._indicator.label",
    f"Main.panel.statusArea['{key}']._label ? Main.panel.statusArea['{key}']._label.text : 'no _label'",
    f"Main.panel.statusArea['{key}']._label ? Main.panel.statusArea['{key}']._label.visible : 'n/a'",
    f"Object.keys(Main.panel.statusArea['{key}']).filter(k => k.startsWith('_')).join(',')",
    "imports.misc.extensionUtils ? 'eu' : 'no eu'",
):
    print(js[-60:], "->", shell.shell_eval(js))
process.terminate()
