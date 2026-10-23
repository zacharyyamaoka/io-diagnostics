"""``io-diagnostics [serve | tray | once --json | act | expect | forget | retire]``: the one command.

    uv run --project /home/bam/io-diagnostics io-diagnostics serve
    uv run --project /home/bam/io-diagnostics io-diagnostics serve --fixture F2 --offset 330
    uv run --project /home/bam/io-diagnostics io-diagnostics serve --machine /home/bam/io-diagnostics/tests/data/sysfs-rack-fixture.json
    uv run --project /home/bam/io-diagnostics io-diagnostics once --json --fixture F1
    /usr/bin/python3 /home/bam/io-diagnostics/io_diagnostics/cli.py tray

- ``serve``  the dashboard at http://127.0.0.1:8812/ (``--port``), live from this machine, or ``--fixture F1|F2|F3``
             (plug / bus-busy / after-restart): V6's three recorded frames replayed through the same plugin runner,
             or ``--machine SPEC.json``: a fake machine's sysfs (PCIe grabbers, EtherCAT) through the real sources.
             ``--enable NAME`` runs a plugin its declaration disables; ``--plugins DIR`` reads another directory.
- ``tray``   ``serve`` plus the top-bar chip in the same process (needs the system python's ``gi``; from the uv
             environment it re-executes itself under /usr/bin/python3). The dashboard opens only on a click.
- ``once``   refresh, run every matched plugin once, print the merged tree and records (``--json``) and exit:
             for tests and the rack session runner. ``--at S`` judges a fixture S seconds after its frame clock.
- ``act``    ``--path <entity path> --action <name> [--node N] [--new-id N] [--confirm TOKEN] [--dry-run]``: run one
             action the entity's plugin registered (``--new-id``: set_id's new CAN id), through the plugin's own
             ``act``, and print its answer as JSON. Only a person runs actions: with stdin not a TTY (an agent) only
             ``--dry-run`` runs, and any action whose danger is not ``query`` needs ``--confirm``. Exit 0 ran ·
             2 refused (nothing ran) · 1 the plugin failed.
- ``expect`` / ``forget`` / ``retire``  edit the expected setup (docs/expected-setup.md), as the dashboard's Expected
             setup card does: ``--path <row>`` (expect runs every plugin once to see the nodes answering now), or
             ``--hardware-id ID`` / ``--serial S`` (``expect`` takes ``--nodes can/1,can/14 --kind adapter --rig R``;
             ``retire`` needs ``--reason``). A file write only; exit 0 written · 2 refused.

The host itself is passive: it opens no CAN adapter or camera. The driver plugins in plugins.d (enabled by default)
run their own gated, query-only checks (port-holder check + the rack bus lock first), one at a time per
``exclusive`` group; a write runs only through ``act``, for a person.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # run as a file: import the package from the checkout, not this directory's modules
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from io_diagnostics import PACKAGE  # noqa: E402

SYSTEM_PYTHON = "/usr/bin/python3"
MARK = {"error": "✖", "warn": "▲", "unknown": "?", "stale": "◷", "ok": " ", None: " "}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="io-diagnostics", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("serve", "serve the dashboard and the JSON API"), ("tray", "serve + the top-bar chip, one process"),
                       ("once", "run everything once, print, exit"), ("act", "run one registered action (a person only)"),
                       ("expect", "expected setup: expect a device and the nodes answering below it now"),
                       ("forget", "expected setup: forget an absent device"),
                       ("retire", "expected setup: retire a device, with a reason")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--expected", type=Path, metavar="FILE",
                             help="the expected-setup file (default, live: ~/.config/io-diagnostics/expected.toml;"
                                  " a fixture or a fake machine reads one only when named)")
        command.add_argument("--fixture", help="replay a recorded frame: F1 / F2 / F3 (plug / bus-busy / after-restart)")
        command.add_argument("--plugins", type=Path, help="plugin directory (default: plugins.d, or the fixture plugins)")
        command.add_argument("--machine", type=Path, metavar="SPEC.json",
                             help="a fake machine's sysfs spec instead of this PC (tests/data/sysfs-rack-fixture.json:"
                                  " PCIe frame grabber, capture card, EtherCAT NICs)")
        command.add_argument("--enable", action="append", default=[], metavar="NAME",
                             help="run a plugin its declaration disables (enabled = false), this run only; repeatable")
        if name == "once":
            command.add_argument("--json", action="store_true", help="print state, records, activity and plugins as JSON")
            command.add_argument("--at", type=float, default=0.0, help="fixture only: judge S seconds after the frame clock")
        elif name == "act":
            command.add_argument("--path", required=True, help="the entity's tree path (/api/v1/node?path=...)")
            command.add_argument("--action", required=True, help="the action's machine name (check_comms, reboot, ...)")
            command.add_argument("--node", type=int, help="CAN id, for an action on one board")
            command.add_argument("--new-id", type=int, metavar="N", help="set_id only: the board's new CAN id (1-126)")
            command.add_argument("--confirm", metavar="TOKEN", help="the confirm token; required unless the danger is query")
            command.add_argument("--dry-run", action="store_true", help="run nothing: the plugin prints what it would run")
        elif name in ("expect", "forget", "retire"):
            command.add_argument("--path", help="the row's tree path (expect runs every plugin once to see its nodes)")
            command.add_argument("--hardware-id", default="", help="name the device by id instead (usb:<vid>:<pid>:<serial>)")
            command.add_argument("--serial", default="", help="name the device by a serial its row reports (RealSense SDK serial)")
            command.add_argument("--name", default="", help="--hardware-id / --serial only: the name to show")
            if name == "expect":
                command.add_argument("--rig", help="the rig (default: the device's own, else the only one)")
                command.add_argument("--nodes", default="", help="--hardware-id / --serial only: its node paths, e.g. can/1,can/14")
                command.add_argument("--kind", default="", help="--hardware-id / --serial only: adapter, camera, ...")
            if name == "retire":
                command.add_argument("--reason", required=True, help="why it is retired (shown when it is plugged in again)")
        else:
            command.add_argument("--port", type=int, default=8812)
            command.add_argument("--bind", default="127.0.0.1")
            command.add_argument("--offset", type=float, default=0.0, help="fixture only: start S seconds after the frame clock")
            command.add_argument("--no-udev", action="store_true", help="do not listen to udevadm monitor (live mode)")
    return parser


def _print_tree(node: dict, indent: int = 0) -> None:
    hint = f"  [below: {node['hint']['level']}]" if node.get("hint") else ""
    knock = "  (knock-on)" if node.get("knock_on_of") else ""
    print(f"{'  ' * indent}{MARK.get(node['level'], '?')} {node['name']}  {node['id_text']}  — {node['message']}{hint}{knock}")
    for child in node["children"]:
        _print_tree(child, indent + 1)


def _once(args) -> int:
    from io_diagnostics.host import make_host

    host = make_host(fixture=args.fixture, plugins_dir=args.plugins, once=True, at_s=args.at, enable=tuple(args.enable),
                     machine=args.machine, expected=args.expected)
    host.run_all_once()
    if host.fixture is None:
        # WHY: live, the one instant every record is judged at is the END of the runs; the start would hide each
        # plugin event (stamped while its plugin ran) as "in the future" (found with the real intel_rgbd plugin).
        host.clock.at = time.time()
    state = host.state()
    if args.json:
        json.dump({"state": state, "records": host.records(), "activity": host.activity(), "plugins": host.plugins()},
                  sys.stdout, default=str, ensure_ascii=False)
        print()
    else:
        print(f"{state['clock']['iso']}  {state['chip']['label']}  ({state['summary']['devices']} devices)")
        _print_tree(state["tree"])
    return 0


def _act(args) -> int:
    """Exit 0: the plugin ran and exited 0 · 2: the host refused (nothing ran) · 1: the plugin failed or hung."""

    from io_diagnostics.host import make_host

    host = make_host(fixture=args.fixture, plugins_dir=args.plugins, once=True, enable=tuple(args.enable),
                     machine=args.machine, expected=args.expected)
    host.refresh()
    result = host.act(args.path, args.action, node=args.node, confirm=args.confirm, dry_run=args.dry_run,
                      new_id=args.new_id)
    json.dump(result, sys.stdout, default=str, ensure_ascii=False)
    print()
    if result["refused"]:
        print(f"refused: {result['refused']}: {result['message']}", file=sys.stderr)
        return 2
    return 0 if result["outcome"] == "ok" else 1


def _setup_edit(args) -> int:
    """Exit 0: the file was rewritten · 2: refused (nothing written). A file write only: it never touches a device."""

    from io_diagnostics.host import make_host

    host = make_host(fixture=args.fixture, plugins_dir=args.plugins, once=True, enable=tuple(args.enable),
                     machine=args.machine, expected=args.expected)
    if args.path:
        if args.command == "expect":
            host.run_all_once()  # WHY: the nodes it records are the ones its plugin finds answering now
            if host.fixture is None:
                host.clock.at = time.time()
        else:
            host.refresh()
        result = host.setup_edit(args.command, args.path, rig=getattr(args, "rig", None), reason=getattr(args, "reason", ""))
    else:
        nodes = tuple(node for node in getattr(args, "nodes", "").replace(" ", ",").split(",") if node)
        result = host.setup_edit_ids(args.command, hardware_id=args.hardware_id, serial=args.serial, name=args.name,
                                     rig=getattr(args, "rig", None), reason=getattr(args, "reason", ""), nodes=nodes,
                                     kind=getattr(args, "kind", ""))
    json.dump(result, sys.stdout, default=str, ensure_ascii=False)
    print()
    if not result["ok"]:
        print(f"refused: {result['refused']}: {result['message']}", file=sys.stderr)
        return 2
    print(result["message"], file=sys.stderr)
    return 0


def _serve(args, *, tray: bool) -> int:
    from io_diagnostics.host import make_host
    from io_diagnostics.server import make_server, serve_in_thread

    host = make_host(fixture=args.fixture, plugins_dir=args.plugins, offset_s=args.offset, enable=tuple(args.enable),
                     machine=args.machine, expected=args.expected)
    try:
        server = make_server(host, args.port, args.bind)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            print(f"port {args.port} is in use (another io-diagnostics?); pass --port", file=sys.stderr)
            return 2
        raise
    host.start(udev=not args.no_udev)
    mode = (f"fixture {host.fixture['name']} ({host.fixture['label']})" if host.fixture
            else f"fake machine {args.machine}" if args.machine else "live")
    print(f"IO diagnostics on {host.url} · {mode} · {len(host.specs)} plugin(s) from {host.plugins_dir}", flush=True)
    try:
        if tray:
            from io_diagnostics.tray import run_tray

            serve_in_thread(server)
            run_tray(lambda: host.state()["chip"])
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        host.stop()
        server.shutdown() if tray else None
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = _parser().parse_args(argv)
    if args.command == "tray":
        from io_diagnostics.tray import gi_available

        if not gi_available():
            if os.path.realpath(sys.executable) == os.path.realpath(SYSTEM_PYTHON):
                print("the tray needs python3-gi (GTK + AyatanaAppIndicator3)", file=sys.stderr)
                return 2
            # WHY re-exec: gi is the system python's (apt python3-gi), not the uv environment's; the host is stdlib-only
            env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
            os.execve(SYSTEM_PYTHON, [SYSTEM_PYTHON, str(PACKAGE / "cli.py"), *argv], env)
        return _serve(args, tray=True)
    if args.command == "serve":
        return _serve(args, tray=False)
    if args.command == "act":
        return _act(args)
    if args.command in ("expect", "forget", "retire"):
        return _setup_edit(args)
    return _once(args)


if __name__ == "__main__":
    raise SystemExit(main())
