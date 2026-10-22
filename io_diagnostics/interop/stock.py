"""The stock view: what a standard diagnostics viewer receives from our v0 records.

The adapter is the record contract's own ``to_diagnostic_status`` (it strips every field we added); this module
only decides WHICH records go out and at WHAT level, then puts them on the ROS 2 wire:

- the latest record per check (``path`` + ``name``), judged at publish time with the contract's
  ``effective_level``: a record overdue on its promised period goes out as stock STALE (level 3) with its age,
  never as its last green. WHY: a stock viewer that is handed an old OK has no way to know it is old (rqt and
  ``ros2 topic echo`` judge nothing; Lichtblick judges only the array stamp), and "can't tell" is never green.
- one ``diagnostic_msgs/msg/DiagnosticArray`` per publish, stamped with the observer's clock, like ROS's own
  aggregator: the host republishes its current view, so the array stamp is the time of that view.
- ROS 2 CDR bytes + the ``ros2msg`` schema text, from rosbags' Humble typestore (a stock serializer, never a
  hand-rolled one). The message is byte-identical on Humble, Jazzy and Rolling (record spec, preamble).
"""

from __future__ import annotations

import dataclasses
import functools
from typing import Iterable

from contract.record import (
    STALE, UNKNOWN, DiagnosticStatus, effective_level, from_diagnostic_array, to_diagnostic_array,
)
# WHY a private import: the dashboard's stale rows are worded by this function (``summarize``); a stock viewer
# showing the same record must say the same words. If the contract renames it, the interop tests fail loudly.
from contract.record import _stale_message

TOPIC = "/diagnostics"
SCHEMA_NAME = "diagnostic_msgs/msg/DiagnosticArray"
SCHEMA_ENCODING = "ros2msg"  # Foxglove/Lichtblick: CDR messages need ros2msg (or ros2idl/omgidl) schemas
MESSAGE_ENCODING = "cdr"


def observed(status: DiagnosticStatus, now: float) -> DiagnosticStatus:
    """The record as the observer stands behind it at ``now`` (a v0 record the contract can project)."""

    level = effective_level(status, now)
    if level == STALE:
        # STALE is derived, never sent as a v0 level; UNKNOWN is the contract's only projection onto stock 3 (STALE)
        return dataclasses.replace(status, level=UNKNOWN, message=_stale_message(status, now))
    if level != status.level:  # a level nobody defined: "can't tell", never green
        return dataclasses.replace(status, level=level)
    return status


def latest(statuses: Iterable[DiagnosticStatus]) -> list[DiagnosticStatus]:
    """The last record per check, in first-seen order (``statuses`` may hold history)."""

    by_check: dict[tuple[str, str], DiagnosticStatus] = {}
    for status in statuses:
        by_check[(status.path or status.hardware_id, status.name)] = status
    return list(by_check.values())


def stock_array(statuses: Iterable[DiagnosticStatus], now: float, frame_id: str = "") -> dict:
    """The stock ``DiagnosticArray`` (plain dict, ``level`` as int) a viewer gets for these records at ``now``."""

    return to_diagnostic_array([observed(status, now) for status in latest(statuses)], stamp=now, frame_id=frame_id)


# ----- ROS 2 wire (CDR + ros2msg schema) -----------------------------------------------------------------------
@functools.cache
def _typestore():
    from rosbags.typesys import Stores, get_typestore  # lazy: only the wire needs it

    return get_typestore(Stores.ROS2_HUMBLE)


def ros2msg_schema() -> str:
    """``diagnostic_msgs/msg/DiagnosticArray`` with its dependencies, in the concatenated ``ros2msg`` form rosbag2
    writes into MCAP (``MSG: pkg/Type`` sections)."""

    return _typestore().generate_msgdef(SCHEMA_NAME, ros_version=2)[0]


def to_cdr(array: dict) -> bytes:
    """A stock ``DiagnosticArray`` dict -> ROS 2 CDR (little-endian, with the 4-byte encapsulation header)."""

    types = _typestore().types
    key_value = types["diagnostic_msgs/msg/KeyValue"]
    status_type = types["diagnostic_msgs/msg/DiagnosticStatus"]
    stamp = array["header"]["stamp"]
    message = types[SCHEMA_NAME](
        header=types["std_msgs/msg/Header"](
            stamp=types["builtin_interfaces/msg/Time"](sec=stamp["sec"], nanosec=stamp["nanosec"]),
            frame_id=array["header"]["frame_id"]),
        status=[status_type(level=status["level"], name=status["name"], message=status["message"],
                            hardware_id=status["hardware_id"],
                            values=[key_value(key=pair["key"], value=pair["value"]) for pair in status["values"]])
                for status in array["status"]])
    return bytes(_typestore().serialize_cdr(message, SCHEMA_NAME))


def from_cdr(data: bytes) -> dict:
    """ROS 2 CDR -> the stock dict shape ``to_diagnostic_array`` produces (for tests and stock consumers)."""

    message = _typestore().deserialize_cdr(data, SCHEMA_NAME)
    return {"header": {"stamp": {"sec": message.header.stamp.sec, "nanosec": message.header.stamp.nanosec},
                       "frame_id": message.header.frame_id},
            "status": [{"level": int(status.level), "name": status.name, "message": status.message,
                        "hardware_id": status.hardware_id,
                        "values": [{"key": pair.key, "value": pair.value} for pair in status.values]}
                       for status in message.status]}


def records_from_cdr(data: bytes, received: float | None = None) -> list[DiagnosticStatus]:
    """The way back in: stock bytes -> v0 records through the contract's ``from_diagnostic_array``."""

    return from_diagnostic_array(from_cdr(data), received=received)
