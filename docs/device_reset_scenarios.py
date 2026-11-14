"""Replays of the 2026-11-14 fdcanusb wedge through the reset ladder, on the FAKE rig of tests/test_recovery.py (no hardware): what
the ladder prints, its exit, and the ledger and alarm lines it writes, as JSON for docs/build_device_reset_report.py.

    uv run --project /home/bam/io-diagnostics --group dev python /home/bam/io-diagnostics/docs/device_reset_scenarios.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import test_recovery as fixture  # noqa: E402  (arms the hardware tripwire too)

from io_diagnostics.recovery.incident import state_dir  # noqa: E402
from io_diagnostics.recovery.ladder import Ladder, Request  # noqa: E402


class Env:
    """The slice of pytest's monkeypatch the fake rig uses, on os.environ (this script is its own process)."""

    def setenv(self, name, value):
        os.environ[name] = str(value)

    def delenv(self, name, raising=True):
        os.environ.pop(name, None)


def scenario(title: str, story: str, *, power: str, cure_by, node_writable=True, sysfs_writable=True, holder=False,
             request: dict, later_s: float = 0.0) -> dict:
    root = Path(tempfile.mkdtemp(prefix="device-reset-scenario-")) / "rig"
    rig = fixture.Rig(root, Env(), cure_by=cure_by, power=power, node_writable=node_writable, sysfs_writable=sysfs_writable)
    if holder:
        rig.process(4242, "tview", parent=1, holds="ttyACM0")
    runs = []
    for attempt in range(2 if later_s else 1):
        lines: list[str] = []
        result = Ladder(Request(key=fixture.FDCANUSB, **request), rig.seams(), say=lines.append).run()
        runs.append({"at_s": round(rig.t - 1000.0, 1), "lines": lines, "code": result.code, "outcome": result.outcome,
                     "message": result.message})
        rig.t += later_s
    ledger = state_dir() / "ledger.jsonl"
    alarms = state_dir() / "alarms.jsonl"
    keep = ("rung", "title", "outcome", "reason", "words", "command", "exit_code", "seconds", "back_after_s", "probe", "before",
            "after", "because")
    entries = [{key: value for key, value in json.loads(line).items() if key in keep}
               for line in (ledger.read_text().splitlines() if ledger.exists() else [])]
    return {"title": title, "story": story, "runs": runs, "ledger": entries,
            "alarms": [json.loads(line) for line in (alarms.read_text().splitlines() if alarms.exists() else [])],
            "resets": rig.resets()}


OWNER = {"owner_pid": fixture.ME, "detected": "no reply to 5 exchanges; its own fdcanusb-reset brought it back still silent",
         "tried_by_owner": ("reopen", "native")}

SCENARIOS = [
    dict(title="08:57 again, after the installer, on a hub that switches each port's power",
         story="arm-hold reopened (no answer) and ran its own fdcanusb-reset (back on USB, still silent), then asks RobotDx. A frozen "
               "microcontroller: only the power cycle cures it.",
         power="individual", cure_by="power_cycle", request=OWNER),
    dict(title="08:57 again, after the installer, on this PC's root hub if it cannot switch port power",
         story="Same, but the hub cannot cut VBUS per port: the kernel deauthorize is the last thing RobotDx may try, then it pages.",
         power="ganged", cure_by=None, request=OWNER),
    dict(title="08:57 again, today (installer not run yet)",
         story="The 99-fdcanusb rule opens the node, so arm-hold's own reset ran; `authorized` and the port switch are root only: "
               "RobotDx pages at once instead of 7 h of silence.",
         power="unknown", cure_by=None, sysfs_writable=False, request=OWNER),
    dict(title="tview still has the adapter open",
         story="arm-hold asks, but another process (tview) has the port: nothing is reset; still blocked 2 min later, a human is paged.",
         power="individual", cure_by="power_cycle", holder=True, request=OWNER, later_s=121.0),
]


def main() -> None:
    os.environ.setdefault("IO_DIAGNOSTICS_TRIPWIRE", "1")
    print(json.dumps([scenario(**each) for each in SCENARIOS], default=str))


if __name__ == "__main__":
    main()
