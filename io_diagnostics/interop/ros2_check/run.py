"""Prove the stock view in real ROS 2: the rack fixture, stripped by the contract, published and echoed by rclpy.

Runs the local image ``ros2_gazebo_aruco:v1`` (ROS 2 Humble; it ships diagnostic_msgs and diagnostic_updater, no
diagnostic_aggregator) with ``--network none`` (DDS stays inside the container), as the calling user, removed on
exit and killed after ``--timeout``. Evidence lands in ``reports/media/interop/ros2/``:

- ``fixture_v0.jsonl``: the v0 records (entities + statuses) before stripping;
- ``stock_array.json`` / ``bridge.cdr`` / ``schema.ros2msg``: exactly what the Foxglove bridge sends;
- ``ros2_topic_echo.txt``: ``ros2 topic echo /diagnostics --once`` of an rclpy publisher sending that array;
- ``ros2_topic_info.txt``, ``ros2_interface_show.txt``, ``msg/*.msg``: the installed ROS 2 side;
- ``verdict.json`` + ``verify.log``: decode / encode (byte-identical) / schema / transport / contract checks.

    uv run --project /home/bam/io-diagnostics --group interop python /home/bam/io-diagnostics/io_diagnostics/interop/ros2_check/run.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contract.record import to_dict  # noqa: E402
from io_diagnostics.interop import rack_fixture, stock  # noqa: E402

IMAGE = "ros2_gazebo_aruco:v1"
CONTAINER = "io-diagnostics-ros2-check"
CHECK_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = CHECK_DIR / "golden"
DEFAULT_OUT = REPO_ROOT / "reports" / "media" / "interop" / "ros2"


def prepare(out_dir: Path, now: float = rack_fixture.FIXTURE_NOW) -> dict:
    """Write the bridge's view of the fixture at ``now``: v0 records, stock JSON, CDR bytes, schema text."""

    out_dir.mkdir(parents=True, exist_ok=True)
    statuses = rack_fixture.statuses(now)
    records = rack_fixture.entities() + statuses
    (out_dir / "fixture_v0.jsonl").write_text(
        "".join(json.dumps(to_dict(record), ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    array = stock.stock_array(statuses, now)
    (out_dir / "stock_array.json").write_text(json.dumps(array, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "bridge.cdr").write_bytes(stock.to_cdr(array))
    (out_dir / "schema.ros2msg").write_text(stock.ros2msg_schema(), encoding="utf-8")
    return array


def run_container(out_dir: Path, timeout_s: float) -> int:
    command = [
        "docker", "run", "--rm", "--name", CONTAINER, "--network", "none",
        "--user", f"{os.getuid()}:{os.getgid()}",  # WHY: evidence files stay the caller's, not root's
        "-e", "HOME=/tmp", "-e", "ROS_LOG_DIR=/tmp/ros_log",
        "-v", f"{CHECK_DIR}:/check:ro", "-v", f"{REPO_ROOT / 'contract'}:/repo/contract:ro", "-v", f"{out_dir}:/out",
        IMAGE, "bash", "/check/in_container.sh",
    ]
    try:
        return subprocess.run(command, timeout=timeout_s, check=False).returncode
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", CONTAINER], check=False, capture_output=True)
        print(f"ros2_check: container exceeded {timeout_s:.0f} s and was killed", file=sys.stderr)
        return 124


def write_golden(out_dir: Path) -> None:
    """Keep real ROS 2's side as the unit tests' oracle: installed .msg files + rclpy's bytes for the fixture."""

    GOLDEN_DIR.mkdir(exist_ok=True)
    for message_file in sorted((out_dir / "msg").glob("*.msg")):
        shutil.copyfile(message_file, GOLDEN_DIR / message_file.name)
    shutil.copyfile(out_dir / "rclpy_fixture_array.cdr", GOLDEN_DIR / "rclpy_fixture_array.cdr")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--write-golden", action="store_true", help="refresh ros2_check/golden/ from this run")
    args = parser.parse_args(argv)
    out_dir = args.out.resolve()
    if shutil.which("docker") is None:
        print("ros2_check: docker is not installed", file=sys.stderr)
        return 2
    prepare(out_dir)
    code = run_container(out_dir, args.timeout)
    verdict_path = out_dir / "verdict.json"
    if not verdict_path.exists():
        print(f"ros2_check: no verdict (container exit {code}); see {out_dir}/verify.log", file=sys.stderr)
        return 1
    verdict = json.loads(verdict_path.read_text())
    for item in verdict["checks"]:
        print(f"{'PASS' if item['passed'] else 'FAIL'} {item['check']}: {item['detail']}")
    echo = (out_dir / "ros2_topic_echo.txt").read_text(errors="replace")
    print(f"ros2 topic echo /diagnostics --once: {len(echo.splitlines())} lines -> {out_dir / 'ros2_topic_echo.txt'}")
    if args.write_golden and verdict["all_passed"]:
        write_golden(out_dir)
        print(f"golden files refreshed in {GOLDEN_DIR}")
    return 0 if verdict["all_passed"] and echo.strip() else 1


if __name__ == "__main__":
    sys.exit(main())
