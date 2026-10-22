"""V3 binding: a CLI convention. Every executable named ``*-diagnostics`` on the search path is a
driver; the hub (Node) spawns it with an outer timeout and reads JSON + a Monitoring Plugins exit
code. Language-agnostic: a vendor CLI joins through a wrapper script.

A driver declares, in its own pyproject.toml:

    [project.scripts]
    obsbot_ptz_camera-diagnostics = "obsbot_ptz_camera.diagnostics:main"

Prototype: bin/ holds the console scripts an install would create.
Cost to add a device type: diagnostics.py (+ a 2-line main) + 1 line of pyproject + an install.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def executables(search: list[Path] | None = None) -> list[Path]:
    return sorted(path for folder in (search or [HERE / "bin"]) for path in folder.glob("*-diagnostics") if path.is_file())


def describe(executable: Path, timeout_s: float = 5.0) -> dict:
    # WHY an outer timeout on every spawn: a hung vendor tool must never hang the top bar.
    completed = subprocess.run([str(executable), "describe"], capture_output=True, text=True, timeout=timeout_s)
    return json.loads(completed.stdout)


def diagnose(executable: Path, port: str, fixture: bool = True, timeout_s: float = 8.0) -> tuple[int, dict]:
    command = [str(executable), "diagnose", "--port", port] + (["--fixture"] if fixture else [])
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s)
    return completed.returncode, json.loads(completed.stdout)


def find_drivers(search: list[Path] | None = None) -> list[dict]:
    return [{**describe(executable), "executable": str(executable)} for executable in executables(search)]
