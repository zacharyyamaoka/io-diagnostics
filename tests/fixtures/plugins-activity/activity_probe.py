"""A driver that probes on connect, for the Activity log's hero video (docs/record_activity_hero.js). Never touches hardware.

    <python> activity_probe.py describe
    <python> activity_probe.py probe --device usb:1-5

It says what a real adapter driver says when its on-connect probe runs (docs/probe-on-connect.md): a ``Found`` for the adapter
that answered, then ``Probed`` last. The stamps are $HERO_FOUND_AT (the recording's clock, a few seconds after the plug the
kernel log has at 18:11:42), so the replay shows them in the order they would have happened. The adapter answers and no board
does: the story of fixture F1, whose bus then goes error-passive. Only the adapter on port 1-5 (28B3A25D) says anything: the
frame has a second fdcanusb on 1-3 that this driver leaves to others.
"""

from __future__ import annotations

import json
import os
import sys

SCHEMA = "diagnostics-record/0"


def line(record: str, **fields) -> None:
    print(json.dumps({"schema": SCHEMA, "record": record, **fields}), flush=True)


def main() -> int:
    verb = next((word for word in sys.argv[1:] if word in ("describe", "diagnose", "probe")), "describe")
    if verb == "describe":
        print(json.dumps({"name": "activity_probe", "schema": SCHEMA, "record_version": "0.2", "matches": [], "actions": [], "probe": {"timeout_s": 10}}))
        return 0
    device = sys.argv[sys.argv.index("--device") + 1] if "--device" in sys.argv else ""
    at = float(os.environ.get("HERO_FOUND_AT", "0")) or None
    if verb == "probe" and device.endswith("1-5"):
        line("event", code="Found", severity="info", stamp=at, source="moteus_servo.diagnostics", seq=1,
             message="fdcanusb 28B3A25D answers on 1-5 (mjbots fdcanusb, firmware 1.3)")
        line("event", code="Probed", severity="info", stamp=at, source="moteus_servo.diagnostics", seq=2,
             message="probed fdcanusb 28B3A25D: the adapter answers, 0 of 4 expected boards do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
