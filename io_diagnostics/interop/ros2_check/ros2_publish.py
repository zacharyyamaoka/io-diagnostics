"""Runs INSIDE the ROS 2 container: publish a stock DiagnosticArray (JSON from the bridge's projection) on
/diagnostics with rclpy, the way any ROS 2 node would. Python 3.10 + ROS 2 Humble; no io-diagnostics imports.

    python3 /check/ros2_publish.py /out/stock_array.json [seconds]
"""

from __future__ import annotations

import json
import sys

import rclpy
from builtin_interfaces.msg import Time
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Header

TOPIC = "/diagnostics"


def to_message(array: dict) -> DiagnosticArray:
    """Stock dict -> rclpy message. ``level`` is a .msg ``byte``, which rosidl maps to ``octet``: bytes of length 1."""

    stamp = array["header"]["stamp"]
    return DiagnosticArray(
        header=Header(stamp=Time(sec=stamp["sec"], nanosec=stamp["nanosec"]), frame_id=array["header"]["frame_id"]),
        status=[DiagnosticStatus(level=bytes([status["level"]]), name=status["name"], message=status["message"],
                                 hardware_id=status["hardware_id"],
                                 values=[KeyValue(key=pair["key"], value=pair["value"]) for pair in status["values"]])
                for status in array["status"]])


def to_dict(message: DiagnosticArray) -> dict:
    """rclpy message -> the stock dict shape (``level`` as int), for comparison with the bridge's JSON."""

    return {"header": {"stamp": {"sec": message.header.stamp.sec, "nanosec": message.header.stamp.nanosec},
                       "frame_id": message.header.frame_id},
            "status": [{"level": status.level[0] if isinstance(status.level, bytes) else int(status.level),
                        "name": status.name, "message": status.message, "hardware_id": status.hardware_id,
                        "values": [{"key": pair.key, "value": pair.value} for pair in status.values]}
                       for status in message.status]}


def main() -> int:
    array = json.load(open(sys.argv[1], encoding="utf-8"))
    # WHY a hard limit: a publisher left behind in a container is a process nobody owns; this one ends itself.
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    rclpy.init()
    node = Node("io_diagnostics_stock_publisher")
    publisher = node.create_publisher(DiagnosticArray, TOPIC, 10)
    message = to_message(array)
    node.create_timer(0.5, lambda: publisher.publish(message))
    deadline = node.get_clock().now().nanoseconds + seconds * 1e9
    try:
        while rclpy.ok() and node.get_clock().now().nanoseconds < deadline:
            rclpy.spin_once(node, timeout_sec=0.2)
    except (KeyboardInterrupt, ExternalShutdownException):  # the runner's kill (SIGTERM) is the normal ending
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
