"""robotdx.diagnostics is an import alias onto robot_diagnostics (the library's own package since 2026-11-23): code outside this repo that
still imports the old name (bam_rack_twin, data-ui's gallery builders) gets the very same modules, so there is one registry, never two."""

from __future__ import annotations

import json
import subprocess
import sys

PROBE = r"""
import json
import robotdx.diagnostics.stock.health as old_health
from robotdx.diagnostics import REGISTRY as old_registry, evaluate
from robotdx.diagnostics.stock import BoxConfig
import robot_diagnostics, robot_diagnostics.stock.health as new_health
import robotdx.diagnostics as old
print(json.dumps({"same_module": old_health is new_health, "same_package": old is robot_diagnostics,
                  "same_registry": old_registry is robot_diagnostics.REGISTRY, "name": old_health.__name__,
                  "count": len(robot_diagnostics.names()), "config": BoxConfig.__module__}))
"""


def test_the_old_name_gives_the_new_modules_once():
    # WHY a fresh interpreter: the alias must work from a cold start, whatever this test session imported first
    done = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["same_module"] and out["same_package"] and out["same_registry"]
    assert out["name"] == "robot_diagnostics.stock.health" and out["config"] == "robot_diagnostics.stock.health"
    assert out["count"] >= 33  # every stock diagnostic registered once (a second copy would raise on the duplicate name)
