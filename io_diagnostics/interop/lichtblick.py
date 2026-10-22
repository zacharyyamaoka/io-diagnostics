"""Our records in Lichtblick, the open-source Foxglove Studio fork: its STOCK Diagnostics panels, unmodified.

One command starts both servers the proof needs and stops both when it ends (Ctrl-C, SIGTERM or ``--duration``):
the Foxglove bridge (``foxglove_bridge.py``, ws://127.0.0.1:8765) and Lichtblick's own web image
(``ghcr.io/lichtblick-suite/lichtblick``, http://127.0.0.1:8766), opened on a layout with Diagnostics - Summary
beside Diagnostics - Detail (ROS). It prints the URL and never opens a browser.

    uv run --project /home/bam/io-diagnostics --group interop python /home/bam/io-diagnostics/io_diagnostics/interop/lichtblick.py

``--shoot`` drives that URL in headless Chrome (Playwright, ``lichtblick_shoot.mjs``) and saves screenshots and a
short video to ``reports/media/interop/lichtblick/``, then stops everything.
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

if __package__ in (None, ""):  # run as a file: make the repo root importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from io_diagnostics.interop import rack_fixture, stock  # noqa: E402
from io_diagnostics.interop.foxglove_bridge import (  # noqa: E402
    DEFAULT_PORT, DiagnosticsBridge, PortBusy, fixture_source, lichtblick_url, port_in_use,
)

IMAGE = "ghcr.io/lichtblick-suite/lichtblick:latest"
CONTAINER = "io-diagnostics-lichtblick"
DEFAULT_VIEWER_PORT = 8766
HERE = Path(__file__).resolve().parent
LAYOUT_FILE = HERE / "lichtblick_layout.json"
SHOOT_SCRIPT = HERE / "lichtblick_shoot.mjs"
MEDIA_DIR = HERE.parents[1] / "reports" / "media" / "interop" / "lichtblick"
PLAYWRIGHT = Path("/home/bam/aruco-studio/web/node_modules/playwright")  # an existing local install; nothing new
SUMMARY_PANEL = "DiagnosticSummary!iodiagnostics"
DETAIL_PANEL = "DiagnosticStatusPanel!iodiagnostics"


def layout(selected_hardware_id: str = rack_fixture.node_hardware_id(15), selected_name: str = "CAN 15: driver",
           topic: str = stock.TOPIC, seconds_until_stale: int = 5) -> dict:
    """Lichtblick layout: stock Summary (sorted by level) | stock Detail opened on one check.

    WHY Lichtblick's own panel types and config keys (DiagnosticSummary, DiagnosticStatusPanel, topicToRender,
    secondsUntilStale...): they are read from its shipped bundle, so the layout is what its UI would save.
    """

    return {
        "configById": {
            SUMMARY_PANEL: {"minLevel": 0, "pinnedIds": [], "hardwareIdFilter": "", "topicToRender": topic,
                            "sortByLevel": True, "secondsUntilStale": seconds_until_stale},
            DETAIL_PANEL: {"topicToRender": topic, "collapsedSections": [], "selectedHardwareId": selected_hardware_id,
                           "selectedName": selected_name, "splitFraction": 0.5,
                           "secondsUntilStale": seconds_until_stale},
        },
        "globalVariables": {},
        "userNodes": {},
        "playbackConfig": {"speed": 1},
        "layout": {"first": SUMMARY_PANEL, "second": DETAIL_PANEL, "direction": "row", "splitPercentage": 52},
    }


def write_layout(path: Path = LAYOUT_FILE) -> Path:
    path.write_text(json.dumps(layout(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def container_exists(name: str = CONTAINER) -> bool:
    found = subprocess.run(["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
                           capture_output=True, text=True, check=False)
    return name in found.stdout.split()


def start_viewer(port: int, layout_path: Path = LAYOUT_FILE) -> None:
    """Lichtblick's web image on 127.0.0.1:``port``; its entrypoint injects ``layout_path`` as the default layout."""

    if container_exists():
        raise RuntimeError(f"a container named {CONTAINER} already exists; stop it yourself first "
                           f"(docker stop {CONTAINER})")
    subprocess.run(["docker", "run", "--rm", "-d", "--name", CONTAINER, "-p", f"127.0.0.1:{port}:8080",
                    "-v", f"{layout_path}:/lichtblick/default-layout.json:ro", IMAGE],
                   check=True, capture_output=True, text=True)


def stop_viewer() -> None:
    """Stop the container this module started (by its own name; ``--rm`` removes it)."""

    subprocess.run(["docker", "stop", "-t", "3", CONTAINER], capture_output=True, check=False)


def wait_http(url: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"{url} did not answer within {timeout_s:.0f} s")
        time.sleep(0.5)


def shoot(url: str, out_dir: Path) -> int:
    """Headless Chrome through Playwright: screenshots + a short video of the stock panels showing our records."""

    out_dir.mkdir(parents=True, exist_ok=True)
    code = subprocess.run(["node", str(SHOOT_SCRIPT), url, str(out_dir), str(PLAYWRIGHT / "index.mjs")],
                          check=False, timeout=180).returncode
    video = out_dir / "lichtblick-hero.webm"
    if code == 0 and video.exists() and shutil.which("ffmpeg"):
        # mp4 for any player, a small GIF as the report's fallback when video does not play
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-crf", "28", "-movflags", "+faststart", str(video.with_suffix(".mp4"))], check=False)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
                        "fps=5,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
                        str(video.with_suffix(".gif"))], check=False)
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bridge-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--viewer-port", type=int, default=DEFAULT_VIEWER_PORT)
    parser.add_argument("--duration", type=float, default=None, help="stop after this many seconds")
    parser.add_argument("--shoot", action="store_true", help="take the headless proof shots, then stop")
    parser.add_argument("--out", type=Path, default=MEDIA_DIR)
    args = parser.parse_args(argv)

    for port in (args.bridge_port, args.viewer_port):
        if port_in_use("127.0.0.1", port):
            print(f"lichtblick: port {port} is already in use; refusing to start (pick another)", file=sys.stderr)
            return 2
    write_layout()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    viewer = f"http://127.0.0.1:{args.viewer_port}"
    bridge, runner, viewer_started = None, None, False
    try:
        start_viewer(args.viewer_port)
        viewer_started = True
        bridge = DiagnosticsBridge(fixture_source(), port=args.bridge_port).start()
        runner = threading.Thread(target=bridge.run, kwargs={"stop": stop}, daemon=True)
        runner.start()
        wait_http(viewer)
        url = lichtblick_url(viewer, bridge.url)
        print(f"lichtblick: {url}", flush=True)
        if args.shoot:
            return shoot(url, args.out.resolve())
        deadline = None if args.duration is None else time.monotonic() + args.duration
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            stop.wait(0.5)
        return 0
    except (PortBusy, RuntimeError, TimeoutError, subprocess.CalledProcessError) as error:
        print(f"lichtblick: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()
        if runner is not None:
            runner.join(timeout=5)
        if bridge is not None:
            bridge.stop()
        if viewer_started:  # only the container this run started; never one that was already there
            stop_viewer()
        print("lichtblick: stopped what this run started", flush=True)


if __name__ == "__main__":
    sys.exit(main())
