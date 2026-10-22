"""``io-diagnostics once --json`` as a real command: V6's three frames replayed, the staleness flip V6 showed at
18:30:41, the same package under the system python the tray needs, and one live passive smoke with no plugins.

Nothing here opens a device: the fixtures are recordings and the live smoke reads sysfs and the kernel log only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_host_support import ROOT, node_by_name

CLI = ROOT / "io_diagnostics" / "cli.py"
SYSTEM_PYTHON = Path("/usr/bin/python3")


def once(*arguments: str, python: str = sys.executable) -> dict:
    completed = subprocess.run([python, str(CLI), "once", "--json", *arguments], capture_output=True, text=True, timeout=120,
                               cwd="/")  # WHY cwd=/: the command must work from any directory
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_f1_plug_the_dead_bus_and_fault_33_are_errors():
    out = once("--fixture", "F1")
    state = out["state"]
    assert state["clock"]["iso"] == "2026-10-21T18:11:50-07:00" and state["fixture"]["label"] == "18:11 · plug"
    assert state["summary"]["counts"] == {"error": 2, "warn": 3}
    causes = {item["name"]: item["level"] for item in state["summary"]["attention"]}
    assert causes == {"28B3A25D CAN bus": "error", "CAN 15 driver": "error", "Port 1-7": "warn", "D405 ·6036": "warn",
                      "D405 ·4411": "warn"}
    adapter = node_by_name(state["tree"], "fdcanusb 28B3A25D")
    assert (adapter["level"], adapter["hint"]["level"]) == ("ok", "error")  # the adapter is fine; its bus is not
    assert node_by_name(state["tree"], "Port 1-7")["connected"] is False
    assert all(job["last_run"]["outcome"] == "ok" for job in out["plugins"]["plugins"][0]["jobs"])
    assert len(out["plugins"]["plugins"][0]["jobs"]) == 4  # two fdcanusbs, two D405s: matched by usb id


def test_f2_bus_busy_the_port_holder_is_one_cause_and_boards_go_stale_at_18_30_41():
    before = once("--fixture", "F2", "--at", "340")["state"]  # 18:30:40
    after = once("--fixture", "F2", "--at", "342")["state"]  # 18:30:42 = 18:21:41 + 3 x 180 s + 1 s
    for name in ("CAN 1", "CAN 14", "CAN 15", "272813EF CAN bus"):
        assert node_by_name(before["tree"], name)["level"] == "ok"
        node = node_by_name(after["tree"], name)
        assert node["level"] == "stale" and node["stale"]
    adapter = node_by_name(after["tree"], "fdcanusb 272813EF")
    assert adapter["level"] == "unknown" and "tview pid 640623" in adapter["message"]  # PortBusy: not evaluated, and why
    assert node_by_name(after["tree"], "CAN 1")["knock_on_of"] == adapter["path"]  # silence explains silence
    assert before["summary"]["counts"] == after["summary"]["counts"] == {"error": 1, "unknown": 1, "warn": 3}


def test_f3_after_restart_the_nodes_say_no_reply_and_28b3a25d_is_gone():
    state = once("--fixture", "F3")["state"]
    for name in ("CAN 1", "CAN 14", "CAN 15"):
        node = node_by_name(state["tree"], name)
        assert node["level"] == "warn" and node["message"].startswith("no reply since the restart")
    with pytest.raises(KeyError):
        node_by_name(state["tree"], "fdcanusb 28B3A25D")
    boots = [event for event in state["recent"] + once("--fixture", "F3")["activity"]["events"] if event["code"] in ("Booted", "ShutDown")]
    assert {event["code"] for event in boots} == {"Booted", "ShutDown"}


@pytest.mark.skipif(not SYSTEM_PYTHON.exists(), reason="no system python")
def test_the_same_package_runs_under_the_system_python_the_tray_needs():
    state = once("--fixture", "F2", python=str(SYSTEM_PYTHON))["state"]
    assert state["summary"]["counts"] == {"error": 1, "unknown": 1, "warn": 3}


def test_live_passive_smoke_with_no_plugins(tmp_path):
    """This machine, read passively (sysfs, by-id, the kernel log); no plugin directory, so nothing touches hardware."""

    out = once("--plugins", str(tmp_path))
    state = out["state"]
    assert state["fixture"] is None and state["clock"]["mode"] == "live"
    assert state["tree"]["path"] == "host" and state["tree"]["kind"] == "host"
    assert out["plugins"]["plugins"] == [] and out["plugins"]["problems"] == []
    assert any(source["state"] == "used" for source in state["host"]["sources"])
    assert all(check_free(node) for node in walk(state["tree"]))


def walk(node: dict):
    yield node
    for child in node["children"]:
        yield from walk(child)


def check_free(node: dict) -> bool:
    """With no plugins, every row is the OS's: presence, links, structure; no action can exist."""

    return node["actions"] == 0 and (node["owner"] in ("io_diagnostics", "") or node["owner"].startswith("io_facts"))
