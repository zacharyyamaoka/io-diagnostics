"""Helpers for drivers running inside tools/sandbox.sh (throwaway shell only)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

OUT = Path(os.environ.get("IO_SANDBOX_DIR", "/tmp")) / "out"
SCREEN = os.environ.get("IO_SANDBOX_SCREEN", "1600x900")
REPO = Path(os.environ.get("IO_REPO", Path(__file__).resolve().parents[1]))


def shell_eval(js: str) -> tuple[bool, str]:
    """Run JS in the sandbox shell (Eval is on only there, via sandbox-driver). A real (s) variant,
    so quotes and braces in ``js`` arrive intact (gdbus's text parser mangled them)."""

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell", "Eval",
                          GLib.Variant("(s)", (js,)), GLib.VariantType("(bs)"), Gio.DBusCallFlags.NONE, 10000, None)
    ok, result = reply.unpack()
    return ok, result


def bus_names() -> list[str]:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                          None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 5000, None)
    return list(reply.unpack()[0])


def grab(name: str, crop: str | None = None) -> Path:
    """Screenshot the sandbox display (ffmpeg x11grab); ``crop`` is ffmpeg's w:h:x:y."""

    target = OUT / name
    command = ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", SCREEN, "-i", os.environ["DISPLAY"], "-frames:v", "1"]
    if crop:
        command += ["-vf", f"crop={crop}"]
    subprocess.run(command + [str(target)], check=True, timeout=20)
    return target


def leave_overview() -> None:
    for _ in range(20):
        shell_eval("Main.overview.hide()")
        if shell_eval("Main.overview.visible")[1] == "false":
            return
        time.sleep(0.4)


def status_keys() -> list[str]:
    import json

    ok, result = shell_eval("Object.keys(Main.panel.statusArea).join('|')")
    # Eval returns its value JSON-encoded: '"a|b"', so decode before splitting
    return [key for key in json.loads(result).split("|") if key] if ok else []
