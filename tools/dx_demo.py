#!/usr/bin/env python3
"""The RobotDx data-ui page (io_diagnostics/web/dx/) on a FIXTURE host: one command, no hardware.

    /usr/bin/python3 /home/bam/io-diagnostics/tools/dx_demo.py
    /usr/bin/python3 /home/bam/io-diagnostics/tools/dx_demo.py --port 5620 --fixture F2 --nodes 3

It starts ``io_diagnostics/cli.py serve --fixture F1 --plugins <scratch copy of tests/fixtures/plugins-state> --no-udev``
with its own config and state directories (so nothing of the person's settings, event log or rig file is read or
written), checks that only the fixture plugins loaded (it stops the host and exits 2 otherwise), prints the page's URL
and stays in the foreground until Ctrl-C, which stops the host. Run it twice and the second one takes the next free
port.

WHY a launcher and not ``sps dev``: ``sps dev`` runs ``preview.command`` (``cli.py serve --port N``) with no
``--plugins``, and plugins.d/ ships the REAL drivers enabled (moteus_servo, intel_rgbd, computer): it would run them
against the rack. This script can only start the fixture plugins: a joint-like adapter whose watch streams State at
10 Hz and a camera-like device that streams frames (``tests/fixtures/plugins-state/state_plugin.py``, stdlib, it only
reads and writes the scratch files named below).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "plugins-state"
FIXTURE_PLUGINS = {"state_joint", "state_camera", "state_arm"}
PAGE = "dx/index.html"


def free_port(first: int, last: int, bind: str) -> int:
    for port in range(first, last + 1):
        with socket.socket() as probe:
            try:
                probe.bind((bind, port))
            except OSError:
                continue
            return port
    raise SystemExit(f"no free port in {first}-{last}")


def plugin_dir(root: Path, period_s: float) -> Path:
    """A copy of the fixture declarations with {here} resolved and a short period (the page wants checks every few s)."""

    out = root / "plugins"
    out.mkdir(parents=True, exist_ok=True)
    names = ["state_joint.toml", "state_camera.toml"]
    for name in names:
        text = (SOURCE / name).read_text().replace("{here}", str(SOURCE))
        text = "\n".join(f"period_s = {period_s:g}" if line.startswith("period_s") else line for line in text.splitlines())
        (out / name).write_text(text + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=0, help="the page's port (default: the first free one from 5600)")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--fixture", default="F1", choices=("F1", "F2", "F3"), help="the recorded frame the host replays")
    parser.add_argument("--nodes", type=int, default=1, help="boards on each fixture adapter (STATE_NODES; 3 for an arm)")
    parser.add_argument("--period", type=float, default=2.0, help="the fixture plugins' diagnose period, s (default 2)")
    parser.add_argument("--state", type=Path, help="keep the host's scratch files here (default: a new temp dir)")
    parser.add_argument("--quiet", action="store_true", help="print only the URL line")
    args = parser.parse_args(argv)

    port = args.port or free_port(5600, 5699, args.bind)
    root = args.state or Path(tempfile.mkdtemp(prefix="robotdx-dx-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    plugins = plugin_dir(root, args.period)
    board = {"STATE_CONFIG_FILE": root / "board.json", "STATE_MODE_FILE": root / "mode", "STATE_LOG": root / "plugin.log"}
    if not board["STATE_MODE_FILE"].exists():
        board["STATE_MODE_FILE"].write_text("10")
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env.update({key: str(value) for key, value in board.items()})
    env.update({"IO_DIAGNOSTICS_CONFIG_DIR": str(root / "config"), "IO_DIAGNOSTICS_STATE_DIR": str(root / "state"),
                "STATE_NODES": str(max(1, args.nodes)), "PYTHONUNBUFFERED": "1"})
    command = [sys.executable, str(ROOT / "io_diagnostics" / "cli.py"), "serve", "--fixture", args.fixture,
               "--plugins", str(plugins), "--port", str(port), "--bind", args.bind, "--no-udev"]
    # WHY these two words are checked here as well: the hardware rule is "never a host without --plugins/--fixture"
    assert "--plugins" in command and "--fixture" in command and "--no-udev" in command
    log = open(root / "server.log", "w")
    child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (root / "server.pid").write_text(f"{child.pid}\n")

    def stop(*_args) -> None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, lambda *_a: (stop(), sys.exit(0)))
    base = f"http://{args.bind}:{port}"
    try:
        for _ in range(240):
            if child.poll() is not None:
                print((root / "server.log").read_text(), file=sys.stderr)
                return 1
            try:
                with urllib.request.urlopen(f"{base}/api/v1/plugins", timeout=2) as answer:
                    loaded = {plugin["name"] for plugin in json.load(answer)["plugins"]}
                break
            except OSError:
                time.sleep(0.25)
        else:
            print("the host did not answer in 60 s", file=sys.stderr)
            stop()
            return 1
        if not loaded <= FIXTURE_PLUGINS:
            print(f"refusing to go on: loaded {sorted(loaded)}, not only the fixture plugins", file=sys.stderr)
            stop()
            return 2
        print(f"RobotDx on data-ui: {base}/{PAGE}  (fixture {args.fixture}, plugins {', '.join(sorted(loaded))}; "
              f"Ctrl-C stops it)", flush=True)
        if not args.quiet:
            print(f"  today's page on the same host: {base}/   scratch: {root}", flush=True)
        child.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
        log.close()
        if args.state is None:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
