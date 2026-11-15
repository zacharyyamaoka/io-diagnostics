"""plugins/checks/dora_node.py: a driver's dora node as a Monitoring Plugins check (robotparts docs/drivers.md).

The verdict is pure (status exit code + the status object + dora list + the supervisor unit -> level and message), so
it is tested directly; the whole script runs once against fake status commands and a fake `dora` (sh scripts), and
both shipped declarations load as checks with the attachments RobotDx needs. Nothing here talks to dora or a device.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from io_diagnostics.plugins import load_plugin
from io_diagnostics.runner import parse_check_output

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "plugins" / "checks" / "dora_node.py"
spec = importlib.util.spec_from_file_location("dora_node_check", CHECK)
dora_node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dora_node)


def verdict(exit_code, status, **kwargs):
    options = dict(dataflow="obsbot", node="obsbot_node", running=True, unit_active=True, summary_keys=[])
    options.update(kwargs)
    return dora_node.verdict(exit_code, status, **options)


def test_live_and_healthy_is_ok_with_age_perfdata():
    code, message, perf = verdict(0, {"age_s": 0.3, "health": {"level": "ok", "message": "streaming 15 fps"}})
    assert code == dora_node.OK and "live" in message and "streaming 15 fps" in message
    assert perf == ["age_s=0.3s;2;5;0"]


def test_node_warning_is_a_warning_with_the_nodes_words():
    code, message, _ = verdict(0, {"age_s": 0.2, "health": {"level": "warn", "message": "stream held by obsbot-viewer"}})
    assert code == dora_node.WARNING and "held by obsbot-viewer" in message


def test_device_error_or_fault_is_critical():
    assert verdict(0, {"age_s": 0.1, "health": {"level": "error", "message": "no OBSBOT"}})[0] == dora_node.CRITICAL
    assert verdict(0, {"age_s": 0.1, "mode": "fault", "fault": 33})[0] == dora_node.CRITICAL


def test_belt_style_status_without_a_level_is_ok_and_shows_its_keys():
    code, message, _ = verdict(0, {"age_s": 0.01, "mode": "idle_forward", "lamps": "unknown", "fault": 0},
                               dataflow="netbelt", node="belt_node", summary_keys=["mode", "lamps", "lease_holder"])
    assert code == dora_node.OK
    assert "mode idle_forward" in message and "lamps unknown" in message and "lease_holder" not in message


def test_stale_no_gateway_and_not_running_are_critical():
    assert verdict(5, {"age_s": 9.0})[0] == dora_node.CRITICAL
    assert "stale" in verdict(5, {"age_s": 9.0})[1]
    assert verdict(3, None)[0] == dora_node.CRITICAL
    code, message, _ = verdict(3, None, running=False)
    assert code == dora_node.CRITICAL and "not Running" in message


def test_supervisor_down_adds_a_warning_to_a_live_node():
    code, message, _ = verdict(0, {"age_s": 0.3, "health": {"level": "ok"}}, unit_active=False)
    assert code == dora_node.WARNING and "supervisor" in message


def test_script_end_to_end_with_fake_dora_and_status(tmp_path):
    dora = tmp_path / "dora"
    dora.write_text("#!/bin/sh\nprintf 'UUID Name Status Nodes\\nu1 obsbot Running 2\\nu0 obsbot Failed 0\\n'\n")
    dora.chmod(0o755)
    status = tmp_path / "status"
    status.write_text("#!/bin/sh\necho '" + json.dumps({"age_s": 0.4, "pan_deg": 0.0,
                                                          "health": {"level": "ok", "message": "streaming"}}) + "'\n")
    status.chmod(0o755)
    done = subprocess.run([sys.executable, str(CHECK), "--dataflow", "obsbot", "--node", "obsbot_node",
                           "--summary", "pan_deg", "--dora", str(dora), "--", str(status)],
                          capture_output=True, text=True, timeout=30, env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stdout + done.stderr
    first = done.stdout.splitlines()[0]
    assert first.startswith("DORA OK - dora node live") and "pan_deg 0.0" in first and "| age_s=0.4s" in first
    level, _code, message, _values, measurements = parse_check_output(done.stdout, done.returncode)
    assert level == "ok" and message.startswith("DORA OK")
    assert [(m.key, m.value) for m in measurements] == [("age_s", 0.4)]


def test_shipped_declarations_load_as_checks_on_the_right_rows():
    (obsbot, obsbot_problems) = load_plugin(ROOT / "plugins.d" / "dora_obsbot.toml")
    (belt, belt_problems) = load_plugin(ROOT / "plugins.d" / "dora_netbelt.toml")
    assert obsbot_problems == [] and belt_problems == []
    assert (obsbot.kind, obsbot.attach) == ("check", "usb:3564:fef4@1-2")
    assert (belt.kind, belt.attach) == ("check", "host")
    for plugin in (obsbot, belt):
        assert plugin.enabled and "--" in plugin.command
        assert str(CHECK) in " ".join(plugin.command)
