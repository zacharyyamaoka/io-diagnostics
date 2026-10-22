"""Our v0 records in a stock viewer: a Foxglove WebSocket server publishing ROS 2 ``/diagnostics``.

Lichtblick and Foxglove connect to it as a "Foxglove WebSocket" data source and show the records in their stock
Diagnostics Summary / Detail panels: channel ``/diagnostics``, schema ``diagnostic_msgs/msg/DiagnosticArray``
(``ros2msg``), CDR messages, one array per tick. Everything v0 added (path, code, mode, clocks, period, source,
typed measurements) is stripped by the contract's ``to_diagnostic_status``; measurements arrive as value strings.

Sources: today's rack fixture (default; every producer ticks, CAN 14 stays stale); ``--from-host URL``: the
running io-diagnostics host's ``GET /api/v1/records`` (docs/api-v1.md); or ``--records FILE``: v0 JSON lines as
plugins print them (``diagnose``), re-read every tick so a producer that rewrites the file stays live. Entity,
Event and Fault records have no stock form and are skipped.

    uv run --project /home/bam/io-diagnostics --group interop python /home/bam/io-diagnostics/io_diagnostics/interop/foxglove_bridge.py

WHY foxglove-sdk: it is Foxglove's own server for its WebSocket protocol, the one Lichtblick's
"Foxglove WebSocket" source speaks; nothing protocol-level is hand-rolled here.
"""

from __future__ import annotations

import argparse
import errno
import json
import signal
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

if __package__ in (None, ""):  # run as a file: make the repo root (contract/, io_diagnostics/) importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contract.record import DiagnosticStatus, from_dict  # noqa: E402
from io_diagnostics.interop import rack_fixture, stock  # noqa: E402

DEFAULT_HOST = "127.0.0.1"  # WHY loopback: the diagnostics of a lab PC are not for the LAN unless asked (--host)
DEFAULT_PORT = 8765  # Foxglove's conventional WebSocket port
DEFAULT_PERIOD_S = 1.0

Source = Callable[[float], Iterable[DiagnosticStatus]]


class PortBusy(RuntimeError):
    """Something already listens on the port: the bridge refuses rather than fight it."""


def port_in_use(host: str, port: int) -> bool:
    """True when something already listens where the bridge would (what ``ss -ltn`` would list).

    WHY a bind probe with SO_REUSEADDR, not a connect: it fails exactly when a listener holds the address (the
    wildcard and the loopback conflict both ways), ignores TIME_WAIT leftovers, and never pokes the other
    server (a bare TCP connect shows up in its log as a failed WebSocket handshake).
    """

    if port == 0:
        return False
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host or "0.0.0.0", port))
        except OSError as error:
            return error.errno == errno.EADDRINUSE
    return False


def fixture_source(started: float | None = None) -> Source:
    """Today's rack, live: every producer reports each tick, CAN 14's last report is frozen before ``started``."""

    stale_since = (time.time() if started is None else started) - rack_fixture.STALE_FOR_S
    return lambda now: rack_fixture.statuses(now, stale_since=stale_since)


def records_file_source(path: Path) -> Source:
    """v0 JSON lines from a file, re-read every tick; only ``"record": "status"`` lines have a stock form."""

    def read(_now: float) -> list[DiagnosticStatus]:
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            return []
        found = []
        for line in lines:
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("record") == "status":
                found.append(from_dict(data))
        return found

    return read


def host_api_source(base_url: str, timeout_s: float = 2.0) -> Source:
    """The running io-diagnostics host: ``GET <base_url>/api/v1/records`` (docs/api-v1.md) every tick.

    When the host does not answer, the last records it gave are kept, so they age into STALE on their own clock
    instead of vanishing or staying green.
    """

    url = base_url.rstrip("/") + "/api/v1/records"
    last: list[DiagnosticStatus] = []

    def read(_now: float) -> list[DiagnosticStatus]:
        nonlocal last
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as response:
                payload = json.load(response)
        except (OSError, ValueError):
            return last
        last = [from_dict(data) for data in payload.get("statuses", []) if data.get("record") == "status"]
        return last

    return read


class DiagnosticsBridge:
    """One foxglove-sdk server + one ``/diagnostics`` channel, in its own SDK context (so several can coexist)."""

    def __init__(self, source: Source, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 topic: str = stock.TOPIC, name: str = "io-diagnostics", frame_id: str = ""):
        self.source, self.host, self.port, self.topic, self.name, self.frame_id = source, host, port, topic, name, frame_id
        self.context = None
        self.server = None
        self.channel = None
        self.published = 0
        self.last_array: dict | None = None

    def start(self) -> "DiagnosticsBridge":
        if port_in_use(self.host, self.port):
            raise PortBusy(f"port {self.port} on {self.host} is already in use; refusing to start (pick --port)")
        import foxglove  # lazy: the stock projection and its tests do not need the server

        # WHY keep the context: when it is garbage collected the SDK closes its channels ("Cannot log on closed
        # channel"), and the server keeps running with nothing to advertise.
        self.context = foxglove.Context()
        self.server = foxglove.start_server(name=self.name, host=self.host, port=self.port, context=self.context)
        self.port = self.server.port  # the bound port (port 0 asks the OS for a free one)
        self.channel = foxglove.Channel(
            self.topic, message_encoding=stock.MESSAGE_ENCODING, context=self.context,
            schema=foxglove.Schema(name=stock.SCHEMA_NAME, encoding=stock.SCHEMA_ENCODING,
                                   data=stock.ros2msg_schema().encode()))
        return self

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def publish(self, now: float | None = None) -> dict:
        """Judge the source's records at ``now`` and send one stock array; returns the array that went out."""

        now = time.time() if now is None else now
        array = stock.stock_array(self.source(now), now, frame_id=self.frame_id)
        # WHY log_time = the array stamp: Lichtblick's live clock follows log times, and it marks an entry stale
        # when its array stamp falls 5 s behind that clock; the two must be the same clock.
        self.channel.log(stock.to_cdr(array), log_time=round(now * 1e9))
        self.published += 1
        self.last_array = array
        return array

    def run(self, period_s: float = DEFAULT_PERIOD_S, stop: threading.Event | None = None,
            duration_s: float | None = None) -> None:
        stop = stop or threading.Event()
        deadline = None if duration_s is None else time.monotonic() + duration_s
        while not stop.is_set():
            self.publish()
            if deadline is not None and time.monotonic() >= deadline:
                break
            stop.wait(period_s if deadline is None else max(0.0, min(period_s, deadline - time.monotonic())))

    def stop(self) -> None:
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.context = None

    def __enter__(self) -> "DiagnosticsBridge":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()


def lichtblick_url(viewer: str, bridge_url: str) -> str:
    """A Lichtblick (or app.foxglove.dev) URL that opens the bridge as a Foxglove WebSocket source."""

    return f"{viewer.rstrip('/')}/?ds=foxglove-websocket&ds.url={bridge_url}"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--topic", default=stock.TOPIC)
    parser.add_argument("--period", type=float, default=DEFAULT_PERIOD_S, help="seconds between arrays")
    parser.add_argument("--duration", type=float, default=None, help="stop after this many seconds")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--records", type=Path, default=None, help="v0 JSON lines to publish instead of the fixture")
    sources.add_argument("--from-host", default=None, metavar="URL",
                         help="a running io-diagnostics host, e.g. http://127.0.0.1:<its port> (GET /api/v1/records)")
    parser.add_argument("--dump", action="store_true", help="print the stock array once as JSON and exit (no server)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.from_host:
        source = host_api_source(args.from_host)
    elif args.records:
        source = records_file_source(args.records)
    else:
        source = fixture_source()
    if args.dump:
        now = time.time()
        print(json.dumps(stock.stock_array(source(now), now), indent=1, ensure_ascii=False))
        return 0
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        bridge = DiagnosticsBridge(source, host=args.host, port=args.port, topic=args.topic).start()
    except PortBusy as error:
        print(f"foxglove_bridge: {error}", file=sys.stderr)
        return 2
    print(f"foxglove_bridge: {args.topic} ({stock.SCHEMA_NAME}, {stock.MESSAGE_ENCODING}) on {bridge.url}; "
          f"open it in Lichtblick or Foxglove as a Foxglove WebSocket source", flush=True)
    try:
        bridge.run(args.period, stop, args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        print(f"foxglove_bridge: stopped after {bridge.published} arrays", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
