"""Run intel_rgbd's pytest suite with pyrealsense2 shadowed by a device-less copy, so no camera is ever opened.

    uv run --project /home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/core/mdp/agent/sensors/intel_rgbd --extra test python /home/bam/io-diagnostics/tools/safe_pytest_intel_rgbd.py /home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/core/mdp/agent/sensors/intel_rgbd/tests -q

WHY: a plain `pytest` in that package queries the SDK at import (conftest find_device_serials) and its contract suite
starts real pipelines when a RealSense is plugged in; on the rack PC that opens the D405s. Arguments go to pytest.
"""

import os
import sys

SHADOW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rs_shadow")
sys.path.insert(0, SHADOW)
sys.dont_write_bytecode = True  # WHY: keep __pycache__ out of tools/rs_shadow

import pyrealsense2  # noqa: E402

assert pyrealsense2.__file__.startswith(SHADOW), pyrealsense2.__file__
assert getattr(pyrealsense2, "SHADOW", False) and list(pyrealsense2.context().query_devices()) == []
print(f"shadow pyrealsense2 {pyrealsense2.__version__} active: {pyrealsense2.__file__}", flush=True)

import pytest  # noqa: E402

sys.exit(pytest.main(sys.argv[1:]))
