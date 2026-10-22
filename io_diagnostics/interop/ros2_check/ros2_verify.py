"""Runs INSIDE the ROS 2 container: check the bridge's stock output against real ROS 2 (rclpy, Humble).

Five checks, each an oracle outside our code, written to /out/verdict.json:
1. decode    rclpy deserializes the bridge's CDR bytes into exactly the stock array the bridge meant to send;
2. encode    rclpy serializes that array to the bridge's bytes: same length, every payload byte equal (only the
             alignment padding differs: Fast-CDR leaves it unwritten, see wire_check.py);
3. schema    the bridge's ros2msg schema has the same fields as the .msg files installed in /opt/ros;
4. transport a /diagnostics message published by another rclpy process arrives over DDS unchanged;
5. contract  the record contract (mounted read-only) takes the received rclpy statuses back in
             (``from_diagnostic_status``: bytes level, attribute access) and projects them out again unchanged.

    python3 /check/ros2_verify.py /out
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.node import Node
from rclpy.serialization import deserialize_message, serialize_message

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ros2_publish import TOPIC, to_dict, to_message  # noqa: E402
from wire_check import fields, same_wire, schema_sections  # noqa: E402

SHARE = Path("/opt/ros/humble/share")
INSTALLED_MSGS = {
    "diagnostic_msgs/DiagnosticArray": SHARE / "diagnostic_msgs/msg/DiagnosticArray.msg",
    "diagnostic_msgs/DiagnosticStatus": SHARE / "diagnostic_msgs/msg/DiagnosticStatus.msg",
    "diagnostic_msgs/KeyValue": SHARE / "diagnostic_msgs/msg/KeyValue.msg",
    "std_msgs/Header": SHARE / "std_msgs/msg/Header.msg",
    "builtin_interfaces/Time": SHARE / "builtin_interfaces/msg/Time.msg",
}


def receive_one(seconds: float = 20.0):
    node = Node("io_diagnostics_stock_verifier")
    received = []
    node.create_subscription(DiagnosticArray, TOPIC, received.append, 10)
    deadline = time.monotonic() + seconds
    while not received and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.2)
    node.destroy_node()
    return received[0] if received else None


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/out")
    array = json.loads((out / "stock_array.json").read_text(encoding="utf-8"))
    ours = (out / "bridge.cdr").read_bytes()
    checks = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}", flush=True)

    decoded = to_dict(deserialize_message(ours, DiagnosticArray))
    check("decode", decoded == array, f"rclpy read {len(ours)} bytes -> {len(decoded['status'])} statuses, "
                                      f"{'equal to' if decoded == array else 'DIFFERENT from'} the bridge's JSON")

    theirs = bytes(serialize_message(to_message(array)))
    (out / "rclpy_fixture_array.cdr").write_bytes(theirs)
    check("encode", *same_wire(ours, theirs))

    sections = schema_sections((out / "schema.ros2msg").read_text(encoding="utf-8"))
    mismatched = []
    for type_name, path in INSTALLED_MSGS.items():
        package = type_name.split("/")[0]
        if fields(sections.get(type_name, ""), package) != fields(path.read_text(), package):
            mismatched.append(type_name)
    check("schema", not mismatched and set(sections) == set(INSTALLED_MSGS),
          f"{len(sections)} sections vs {len(INSTALLED_MSGS)} installed .msg files"
          + (f"; mismatched: {mismatched}" if mismatched else "; every field and constant matches"))

    rclpy.init()
    message = receive_one()
    if message is None:
        check("transport", False, f"no {TOPIC} message within 20 s")
    else:
        check("transport", to_dict(message) == array,
              f"received {len(message.status)} statuses over DDS from the publisher process")
        sys.path.insert(0, "/repo")
        from contract.record import from_diagnostic_status, to_diagnostic_status  # the frozen contract, read-only

        back = [to_diagnostic_status(from_diagnostic_status(status)) for status in message.status]
        level_type = type(message.status[0].level).__name__ if message.status else "?"
        outcome = "gave the same stock statuses back" if back == array["status"] else "CHANGED the statuses"
        check("contract", back == array["status"],
              f"from_diagnostic_status read {len(back)} rclpy statuses (level as {level_type}); "
              f"to_diagnostic_status {outcome}")
    rclpy.try_shutdown()

    import platform
    verdict = {"all_passed": all(item["passed"] for item in checks), "checks": checks,
               "ros_distro": "humble", "python": platform.python_version()}
    (out / "verdict.json").write_text(json.dumps(verdict, indent=1) + "\n")
    return 0 if verdict["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
