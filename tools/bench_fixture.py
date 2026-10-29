"""Benchmark-only send clocks on the real fixture HTTP host; no hardware entry point."""
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from io_diagnostics.cli import main
from io_diagnostics.server import Handler


def stamped_json(self, payload, code=200, extra=None):
    # WHY: the classic poll predates SSE's emitted field. Stamp only this fixture's
    # HTTP responses, leaving Host.state(), the SSE diff and production API intact.
    if code == 200 and urlparse(self.path).path in ("/api/v1/state", "/api/v1/values"):
        payload = {**payload, "emitted": time.time() * 1000}
    return original_json(self, payload, code, extra)


if __name__ == "__main__":
    if ("--fixture" not in sys.argv or "--port" not in sys.argv
            or sys.argv[sys.argv.index("--port") + 1] != "0"):
        raise SystemExit("bench requires --fixture and --port 0")
    original_json = Handler._json
    Handler._json = stamped_json
    main()
