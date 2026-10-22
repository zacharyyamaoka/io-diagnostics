"""Sandbox driver: real GNOME Shell 46 renders of every native top-bar surface (V1, V2, V4, V5).

Run:  tools/sandbox.sh tools/shoot_native.py <scratch> "io-diagnostics@bamrobotics"
Opens menus through Eval (on only inside the throwaway shell); no synthetic clicks.
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell  # noqa: E402

REPO = shell.REPO
PY = "/usr/bin/python3"


def indicator_key(fragment: str) -> str | None:
    return next((key for key in shell.status_keys() if fragment in key), None)


def open_indicator(fragment: str, submenu_text: str | None = None) -> None:
    key = indicator_key(fragment)
    print("indicator", fragment, "->", key)
    if not key:
        return
    shell.shell_eval(f"Main.panel.statusArea['{key}'].menu.open()")
    time.sleep(1.0)
    if submenu_text:
        print("submenu:", shell.shell_eval(
            f"(() => {{ const items = Main.panel.statusArea['{key}'].menu._getMenuItems();"
            f" const hit = items.find(i => i.label && i.label.text && i.label.text.includes('{submenu_text}'));"
            " if (hit && hit.setSubmenuShown) { hit.setSubmenuShown(true); return 'opened'; } return 'none'; })()"))
        time.sleep(1.0)


def close_all() -> None:
    shell.shell_eval("Main.panel.menuManager.activeMenu?.close(); Main.messageTray._updateState?.(); 'ok'")
    time.sleep(0.6)


def tray(args: list[str], seconds: float = 3.5) -> subprocess.Popen:
    process = subprocess.Popen([PY, *args])
    time.sleep(seconds)
    return process


time.sleep(2)
shell.leave_overview()
notifications = subprocess.Popen(["/usr/bin/gjs", "-m", "/usr/share/gnome-shell/org.gnome.Shell.Notifications"])
time.sleep(1.5)

try:
    # ---------------- V1: Quick Settings (extension) ----------------
    print("v1 present:", shell.shell_eval("typeof globalThis.__ioDiagnostics"))
    shell.shell_eval("globalThis.__ioDiagnostics.refresh('F1', true); 'ok'")
    time.sleep(3.0)
    shell.grab("v1-F1-banner.png")
    time.sleep(5.5)
    shell.shell_eval("Main.panel.statusArea.quickSettings.menu.open(); 'ok'")
    time.sleep(1.0)
    shell.grab("v1-F1-quick.png")
    shell.shell_eval("globalThis.__ioDiagnostics.toggle.menu.open(); 'ok'")
    time.sleep(1.2)
    shell.grab("v1-F1-menu.png")
    shell.shell_eval("globalThis.__ioDiagnostics.toggle._rows['272813EF'].setSubmenuShown(true); 'ok'")
    time.sleep(1.0)
    shell.grab("v1-F1-device.png")
    close_all()
    for frame in ("F2", "F3"):
        shell.shell_eval(f"globalThis.__ioDiagnostics.refresh('{frame}', false); 'ok'")
        time.sleep(2.5)
        shell.shell_eval("Main.panel.statusArea.quickSettings.menu.open(); 'ok'")
        time.sleep(0.8)
        shell.shell_eval("globalThis.__ioDiagnostics.toggle.menu.open(); 'ok'")
        time.sleep(1.0)
        if frame == "F2":
            shell.shell_eval("globalThis.__ioDiagnostics.toggle._rows['272813EF'].setSubmenuShown(true); 'ok'")
            time.sleep(0.8)
        shell.grab(f"v1-{frame}-menu.png")
        close_all()
    shell.shell_eval("globalThis.__ioDiagnostics._indicator.visible = false; 'ok'")
    # an idle Xvfb session never expires banners (GNOME waits for user activity): clear V1's before V2's
    shell.shell_eval("Main.messageTray.getSources().forEach(source => source.destroy()); 'ok'")
    time.sleep(1.0)

    # ---------------- V2: Plug Journal tray ----------------
    v2 = tray([str(REPO / "prototypes/v2-plug-journal/io_tray.py"), "--frame", "F1", "--notify"], 2.0)
    time.sleep(1.6)
    shell.grab("v2-F1-banner.png")
    time.sleep(6)
    open_indicator("io_diagnostics_v2")
    shell.grab("v2-F1-menu.png")
    close_all()
    open_indicator("io_diagnostics_v2", "272813EF")
    shell.grab("v2-F1-device.png")
    close_all()
    v2.terminate()
    v2.wait(5)
    v2 = tray([str(REPO / "tools/topbar_runner.py"), "--variant", "v2", "--frame", "F2"])
    open_indicator("io_diagnostics_v2", "272813EF")
    shell.grab("v2-F2-device.png")
    close_all()
    v2.terminate()
    v2.wait(5)

    # ---------------- V4 / V5: one-line label, drift badge ----------------
    for variant, frame in (("v4", "F1"), ("v4", "F3"), ("v5", "F1"), ("v5", "F3")):
        process = tray([str(REPO / "tools/topbar_runner.py"), "--variant", variant, "--frame", frame])
        shell.grab(f"{variant}-{frame}-bar.png")
        open_indicator(f"io_diagnostics_{variant}")
        shell.grab(f"{variant}-{frame}-menu.png")
        close_all()
        process.terminate()
        process.wait(5)
finally:
    notifications.terminate()
