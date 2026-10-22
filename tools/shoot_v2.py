"""Sandbox driver: V2's real AppIndicator, its notification banner and its menu, in GNOME Shell 46."""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell  # noqa: E402

TRAY = shell.REPO / "prototypes" / "v2-plug-journal" / "io_tray.py"

time.sleep(2)
shell.leave_overview()
tray = subprocess.Popen(["/usr/bin/python3", str(TRAY), "--demo", "--demo-plug"])
try:
    time.sleep(4.2)
    shell.grab("v2-banner.png")
    time.sleep(6)  # let the banner time out
    keys = shell.status_keys()
    print("status keys:", keys)
    key = next((k for k in keys if "appindicator" in k.lower() or "io-diagnostics" in k.lower()), None)
    print("indicator key:", key)
    if key:
        shell.shell_eval(f"Main.panel.statusArea['{key}'].menu.open()")
        time.sleep(1.2)
        shell.grab("v2-menu.png")
        js = (
            f"(() => {{ const items = Main.panel.statusArea['{key}'].menu._getMenuItems();"
            " const hit = items.find(i => i.label && i.label.text && i.label.text.includes('272813EF'));"
            " if (hit && hit.setSubmenuShown) {{ hit.setSubmenuShown(true); return 'opened ' + hit.label.text; }}"
            " return 'labels: ' + items.map(i => i.label ? i.label.text : '?').join(' / '); }})()"
        )
        print("submenu:", shell.shell_eval(js))
        time.sleep(1.2)
        shell.grab("v2-submenu.png")
finally:
    tray.terminate()
    tray.wait(5)
