"""Helpers for drivers running inside tools/sandbox.sh (throwaway shell only)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

OUT = Path(os.environ.get("IO_SANDBOX_DIR", "/tmp")) / "out"
SCREEN = os.environ.get("IO_SANDBOX_SCREEN", "1600x900")
REPO = Path(os.environ.get("IO_REPO", Path(__file__).resolve().parents[1]))


def shell_eval(js: str) -> str:
    """Run JS in the sandbox shell (Eval is on only there, via sandbox-driver)."""

    result = subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path", "/org/gnome/Shell",
         "--method", "org.gnome.Shell.Eval", js],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip() or result.stderr.strip()


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
        if "false" in shell_eval("Main.overview.visible"):
            return
        time.sleep(0.4)


def status_keys() -> list[str]:
    raw = shell_eval("Object.keys(Main.panel.statusArea).join('|')")
    # gdbus prints (true, 'a|b|c')
    inner = raw.split(", ", 1)[1].rstrip(")").strip("'") if ", " in raw else ""
    return [key for key in inner.split("|") if key]
