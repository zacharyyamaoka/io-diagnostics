"""Shared helpers for tests/test_host_*.py: a fake OS source, a settable clock, lane-B-shaped skeleton dicts and
plugin declarations that run tests/fixtures/plugins/cases/case_plugin.py as a REAL subprocess."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from io_diagnostics.host import Clock, Host  # noqa: E402

CASES = ROOT / "tests" / "fixtures" / "plugins" / "cases" / "case_plugin.py"


class SetClock(Clock):
    """A host clock the test moves by hand."""

    def __init__(self, at: float = 1000.0):
        self.at = at

    def now(self) -> float:
        return self.at


class FakeOs:
    """An OsSource over hand-written skeleton dicts; no kernel log, recorded gate answers."""

    mode = "live"

    def __init__(self, skeleton: list[dict], gates: dict | None = None, hostname: str = "rig"):
        self.devices, self.gates, self.hostname = skeleton, gates or {}, hostname

    def invalidate(self) -> None:
        pass

    def skeleton(self):
        return [dict(device) for device in self.devices], [{"name": "fake", "state": "used", "note": f"{len(self.devices)}"}]

    def boots(self):
        return []

    def gate(self, device, check):
        return tuple(self.gates.get(check, (None, f"{check}: not recorded")))


def host_dict() -> dict:
    return {"bus": "host", "id": "host", "path": "host", "label": "rig", "vendor": None, "product": None, "serial": None,
            "parent": None, "attrs": {"kind": "host", "os": "Ubuntu 24.04", "kernel": "6.8.0"}}


def usb_bus(number: int) -> dict:
    return {"bus": "usb", "id": f"usb:usb{number}", "path": f"usb{number}", "label": f"USB bus {number}", "vendor": None,
            "product": None, "serial": None, "parent": "host", "attrs": {"kind": "bus"}}


def usb_device(port: str, vid: str, pid: str, serial: str = "", *, kind: str = "device", label: str = "thing",
               parent: str | None = None) -> dict:
    bus = port.split("-", 1)[0]
    return {"bus": "usb", "id": f"usb:{port}", "path": f"usb-{port}", "label": label, "vendor": "maker", "product": label,
            "serial": serial or None, "parent": parent or (f"usb:{port.rsplit('.', 1)[0]}" if "." in port else f"usb:usb{bus}"),
            "attrs": {"kind": kind, "port": port, "vid": vid, "pid": pid, "usb_id": f"{vid}:{pid}", "speed_mbps": 12,
                      "hardware_id": f"usb:{vid}:{pid}:{serial}" if serial else f"usb:{vid}:{pid}@{port}"}}


def tty(parent: str, name: str, by_id: str) -> dict:
    return {"bus": "tty", "id": f"tty:{parent}", "path": name, "label": f"/dev/{name}", "vendor": None, "product": None,
            "serial": None, "parent": parent, "attrs": {"kind": "serial", "by_id": [by_id]}}


def write_plugin(directory: Path, name: str, case: str, matches: list[str], *, timeout_s: float = 10, period_s: float = 2,
                 replay: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text(f'name = "{name}"\ncommand = ["{{python}}", "{CASES}", "--case", "{case}"]\n'
                    f"timeout_s = {timeout_s}\nperiod_s = {period_s}\nreplay = {'true' if replay else 'false'}\n"
                    f"matches = {matches!r}\n".replace("'", '"'))
    return path


def make_host(tmp_path: Path, skeleton: list[dict], plugins: list[tuple], *, at: float = 1000.0, gates=None) -> Host:
    """``plugins``: (name, case, matches[, options]) tuples, written as declarations under tmp_path."""

    directory = tmp_path / "plugins.d"
    directory.mkdir(parents=True, exist_ok=True)
    for name, case, matches, *options in plugins:
        write_plugin(directory, name, case, matches, **(options[0] if options else {}))
    return Host(plugins_dir=directory, clock=SetClock(at), os_source=FakeOs(skeleton, gates))


def standard_skeleton(*devices: dict) -> list[dict]:
    return [host_dict(), usb_bus(9), *devices]


def node_by_name(tree: dict, name: str) -> dict:
    stack = [tree]
    while stack:
        node = stack.pop()
        if node["name"] == name:
            return node
        stack.extend(node["children"])
    raise KeyError(name)


def test_support_builds_a_lane_b_shaped_device():
    device = usb_device("9-1", "1234", "abcd", "SER")
    assert set(device) == {"bus", "id", "path", "label", "vendor", "product", "serial", "parent", "attrs"}
    assert device["parent"] == "usb:usb9" and device["attrs"]["hardware_id"] == "usb:1234:abcd:SER"
