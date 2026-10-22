"""Comparing the bridge's ROS 2 wire with real ROS 2's: shared by the container check and the host unit tests.

Stdlib only and Python 3.10-safe (the ROS 2 Humble container imports it too).

- ``same_wire``: CDR bytes equal outside the alignment padding. WHY not plain equality: Humble's rclpy
  (Fast-CDR) skips over padding without writing it, so its bytes carry whatever was in the buffer there, while
  rosbags writes zeros. ``padding_offsets`` finds the padding by reading the message the way the CDR spec lays it
  out (XCDR1, little-endian; offsets aligned from the end of the 4-byte encapsulation header).
- ``schema_sections`` + ``fields``: the ``ros2msg`` schema text against the .msg files ROS 2 installs.
"""

from __future__ import annotations

import re
import struct

ENCAPSULATION_HEADER = 4
SCHEMA_SEPARATOR = "=" * 80
TOP_LEVEL_TYPE = "diagnostic_msgs/DiagnosticArray"


def fields(text: str, package: str) -> list[str]:
    """The field and constant lines of a .msg text: comments and blanks dropped, whitespace collapsed, and a type
    from the same package written short (``KeyValue`` == ``diagnostic_msgs/KeyValue``)."""

    found = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        line = re.sub(r"\s*=\s*", "=", re.sub(r"\s+", " ", line))
        line = re.sub(rf"^{re.escape(package)}/(msg/)?", "", line)
        found.append(line)
    return found


def schema_sections(schema: str) -> dict[str, str]:
    """``ros2msg`` concatenated text -> {"pkg/Type": its text}; the first section is the top-level type."""

    parts = schema.split(SCHEMA_SEPARATOR)
    sections = {TOP_LEVEL_TYPE: parts[0]}
    for part in parts[1:]:
        header, _, body = part.strip("\n").partition("\n")
        sections[header.removeprefix("MSG: ").strip().replace("/msg/", "/")] = body
    return sections


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.offset, self.padding = data, ENCAPSULATION_HEADER, []

    def align(self, size: int) -> None:
        misalignment = (self.offset - ENCAPSULATION_HEADER) % size
        if misalignment:
            gap = size - misalignment
            self.padding.extend(range(self.offset, self.offset + gap))
            self.offset += gap

    def uint32(self) -> int:
        self.align(4)
        (value,) = struct.unpack_from("<I", self.data, self.offset)
        self.offset += 4
        return value

    def octet(self) -> int:
        value = self.data[self.offset]
        self.offset += 1
        return value

    def string(self) -> bytes:
        length = self.uint32()  # includes the terminating NUL
        text = self.data[self.offset:self.offset + length]
        self.offset += length
        return text


def padding_offsets(data: bytes) -> list[int]:
    """Byte offsets of every alignment gap in a DiagnosticArray CDR message (raises if it does not parse whole)."""

    reader = _Reader(bytes(data))
    reader.uint32()  # header.stamp.sec (int32: same size and alignment)
    reader.uint32()  # header.stamp.nanosec
    reader.string()  # header.frame_id
    for _status in range(reader.uint32()):
        reader.octet()  # level (byte -> octet)
        reader.string()  # name
        reader.string()  # message
        reader.string()  # hardware_id
        for _pair in range(reader.uint32()):
            reader.string()  # key
            reader.string()  # value
    if reader.offset != len(reader.data):
        raise ValueError(f"parsed {reader.offset} of {len(reader.data)} bytes")
    return reader.padding


def same_wire(ours: bytes, theirs: bytes) -> tuple[bool, str]:
    """Whether two encodings are the same message on the wire: equal outside the padding, and ours zero-padded."""

    if len(ours) != len(theirs):
        return False, f"lengths differ: {len(ours)} vs {len(theirs)}"
    padding = set(padding_offsets(ours))
    if set(padding_offsets(theirs)) != padding:
        return False, "padding layout differs"
    differing = [index for index, (a, b) in enumerate(zip(ours, theirs)) if a != b]
    outside = [index for index in differing if index not in padding]
    if outside:
        return False, f"{len(outside)} payload bytes differ, first at {outside[0]}"
    if any(ours[index] for index in padding):
        return False, "our padding is not zero"
    return True, (f"{len(ours)} bytes, identical except {len(differing)} of {len(padding)} alignment padding bytes "
                  "(rclpy/Fast-CDR leaves padding unwritten; ours is zero)")
