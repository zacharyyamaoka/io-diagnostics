"""Diagnostics record v0: ROS ``DiagnosticStatus`` kept whole, plus the records the field added beside it.

Spec, with the lesson behind every field and what was left out: ``docs/diagnostics-record-v0.md``.

Five shapes, stdlib only (no ROS):

- ``DiagnosticStatus``: one check on one entity, sent periodically. ROS's five fields unchanged, plus path, code,
  mode, two clocks, the expected period, uptime, source and typed measurements. ``to_diagnostic_status`` strips
  the additions for stock viewers (rqt, Lichtblick, Foxglove, medkit); ``from_diagnostic_status`` takes any
  stock message back in, and the trip stock -> v0 -> stock is the identity.
- ``Entity``: what and where a thing is: identity, two parent edges (``via`` = connectivity, ``part_of`` =
  composition) and the actions its owner registered. Sent once and on change, never in every report.
- ``Event``: something happened (plugged, restarted, fault confirmed). Sequenced, so gaps show.
- ``Fault``: the host's memory of one (entity, code) failing: pending -> confirmed -> healed or cleared.
- ``Action``: a utility the owner registers and executes; the host only shows it and routes the click back.

Three host rules live here so every surface computes them the same way: staleness (``effective_level``), the
composition rule (``summarize``: an entity's own self-report vs the advisory worst-below rollup, with knock-on
collapse) and fault debounce (``FaultManager``).
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, NamedTuple

# Bump the trailing number only for a breaking change. Additive changes (a new optional field with a default)
# keep it, and readers ignore fields they do not know (open world).
SCHEMA = "diagnostics-record/0"
# The additive revision this module validates against. It never travels in a record line (every line still says
# SCHEMA, so a v0 reader reads it); a producer states the revision it writes once, in its describe answer
# (``"record_version": "0.1"``), and the host states its own in its API. v0.1 (2026-10-22): identity ``model_id``,
# danger ``moves``, the standard action names (docs/diagnostics-record-v0.md "v0.1"); its addendum (2026-10-23):
# ``DiagnosticStatus.caused_by`` and the counting rule in ``summarize`` (same doc, "v0.1 addendum: caused_by").
# v0.2 (2026-10-24): the Event is the log record (docs/diagnostics-record-v0.md "v0.2: the Event is the log record").
RECORD_VERSION = "0.2"
# 0.2 also (2026-10-24, docs/state-config-v1.md): the ``state``, ``config`` and ``frame`` record kinds, ``Measurement.meaning`` and
# the ``watch`` verb: the entity page's State, Configuration and image sections.

# caused_by on the stock side: one value, appended last, the member paths joined by ", ". A person reads it in rqt;
# ``from_diagnostic_status`` leaves it a plain value (a stock message is never re-read as v0 structure), so the trip
# stock -> v0 -> stock stays the identity.
CAUSED_BY_KEY = "caused_by"
CAUSED_BY_SEPARATOR = ", "

# ----- levels -------------------------------------------------------------------------------------------------
UNKNOWN, OK, WARN, ERROR = "unknown", "ok", "warn", "error"
LEVELS = (UNKNOWN, OK, WARN, ERROR)  # what a producer may report
STALE = "stale"  # derived by the observer from expected_period_s; never authored
# WHY this order (worst last): "can't tell" (UNKNOWN, STALE) outranks WARN because silence can hide an error,
# and stays below ERROR because nothing has shown it broken. ROS put STALE=3 above ERROR=2 and its aggregator
# turned partly-stale groups into ERROR for a decade (ros/diagnostics #297 -> #593, 2026-05). STALE outranks
# UNKNOWN: it had data and lost it, where UNKNOWN never had any. Strings, not numbers, so adding a value can
# never renumber the others (the reason ROS's 2025 "more levels" proposal stalled).
RANK = {OK: 0, WARN: 1, UNKNOWN: 2, STALE: 3, ERROR: 4}

# Stock diagnostic_msgs bytes. UNKNOWN projects to STALE (3): the only stock value that means "no current
# claim" and never renders green. The mapping is a bijection, so stock -> v0 -> stock is exact.
ROS_LEVEL = {OK: 0, WARN: 1, ERROR: 2, UNKNOWN: 3}
FROM_ROS_LEVEL = {byte: level for level, byte in ROS_LEVEL.items()}

# Readiness, orthogonal to level: DroneCAN uavcan.protocol.NodeStatus MODE_* names, lowercased.
MODES = ("operational", "initialization", "maintenance", "software_update", "offline")

# A record is STALE when the observer has not received it for max(STALE_FACTOR x expected_period_s,
# STALE_FLOOR_S). 3x is DroneCAN's 1 s period vs 3 s offline timeout; 5 s is rqt's, Lichtblick's and the ROS
# aggregator's default, so a fast check does not flicker on a busy machine.
STALE_FACTOR = 3.0
STALE_FLOOR_S = 5.0

# Log severity: INFO is an event, never a status (ROS #268 debate). v0.2 adds debug and fatal so an Event maps onto
# rcl_interfaces/Log's five levels (10 / 20 / 30 / 40 / 50) and OpenTelemetry's six bands without a lossy squeeze.
EVENT_SEVERITIES = ("debug", "info", WARN, ERROR, "fatal")
ROS_LOG_LEVEL = {"debug": 10, "info": 20, WARN: 30, ERROR: 40, "fatal": 50}
PENDING, CONFIRMED, HEALED, CLEARED = "pending", "confirmed", "healed", "cleared"
FAULT_STATUSES = (PENDING, CONFIRMED, HEALED, CLEARED)
# HA device_info names. v0.1 adds model_id (HA DeviceInfo's own key): the robotparts part id ("mjbots-moteus-c1"),
# a plain string; the record never imports robotparts.
IDENTITY_KEYS = ("manufacturer", "model", "serial_number", "hw_version", "sw_version", "model_id")

# Gate classes of an Action (and of a driver's sections): how much it touches the device.
PASSIVE = "passive"  # sysfs / by-id / files: safe while an experiment runs
TOUCHES = "touches"  # talks to the device: only through the gate, query-only
CHANGES = "changes"  # reset / config write: explicit, per device, confirmed, AND gated
# v0.1 adds "moves": hardware moves (a rotor spins, a loaded joint can fall). Danger is per driver: the same
# standard name can be "query" on one device and "moves" on another (docs/actions-vocabulary-v0.1.md).
DANGERS = ("query", "interrupts", "changes", "moves")

# The standard action names (v0.1, frozen 2026-10-22: docs/actions-vocabulary-v0.1.md) -> the host's label. Same
# name = same meaning and same label on every driver; a driver's ``title`` for a standard name must equal it.
# WHY data, not prose: the validator and every surface read one table, so "Reboot…" cannot drift per driver.
STANDARD_ACTIONS = {
    "check_comms": "Check communication",
    "check_firmware": "Check firmware",
    "update_firmware": "Update firmware…",
    "calibrate": "Run calibration…",
    "update_config": "Update configuration…",
    "clear_faults": "Clear faults",
    "reboot": "Reboot…",
    "stop_all": "Stop all",
    "set_id": "Change CAN id…",
    "open_console": "Open console",
    "identify": "Identify",
    # v0.2: the Configuration tab's three writes (docs/state-config-v1.md section 8), driven by its edit session, not by a menu
    "set_config": "Set configuration…",
    "persist_config": "Save configuration to the board…",
    "reset_config": "Reset configuration…",
}
CONFIG_ACTIONS = ("set_config", "persist_config", "reset_config")
CONFIG_TYPES = ("int", "float", "bool", "enum", "string")
# WHY: "Identify" means one thing on every driver (show which physical thing this row is, harmlessly), but how it
# shows itself is the driver's, and the person should read it before pressing: a motor wiggles, a camera takes a
# snapshot. So these names keep their label and may add the way in brackets: "Identify (wiggle)". Every other
# standard name must carry its label exactly.
# open_console (2026-10-24, Zach: "a utility on the fdcanusb to launch tview ... you need to specify which one" - it existed as
# "Open console" and he did not recognise it): the title names the vendor's console, "Open console (tview)".
QUALIFIED_ACTIONS = frozenset({"identify", "open_console"})
QUALIFIER_EXAMPLE = {"identify": "wiggle", "open_console": "tview"}
_QUALIFIER = re.compile(r"^ \([a-z][a-z0-9 -]*\)$")

# The standard event codes (docs/probe-on-connect.md): a driver's probe writes these itself, one meaning on every
# driver, and the host logs them as they are (it derives no device-specific events). Code -> when it is written.
STANDARD_EVENT_CODES = {
    "Found": "the probe heard a sub-device (or the device itself) answer",
    "Probed": "one summary per probe, always last",
    "ProbeBlocked": "the probe could not talk to the device (a holder or the bus lock); the host probes again when it clears",
}

# PascalCase, optionally qualified ("MoteusFault.33"): K8s reason / Redfish MessageKey style, at most 64 chars
# (ros2_medkit's limit for fault codes, which maps 1:1 to its UPPER_SNAKE).
_CODE = re.compile(r"^[A-Z][A-Za-z0-9]*(\.[A-Za-z0-9_]+)*$")


# ----- the shapes ---------------------------------------------------------------------------------------------
class KeyValue(NamedTuple):
    """``diagnostic_msgs/KeyValue``: a string pair for people (REP-107: "the intended consumer is a person")."""

    key: str
    value: str


@dataclass(frozen=True)
class Measurement:
    """A typed value: number + UCUM unit + limits in the same unit + its own read time (Redfish ``Sensor``)."""

    key: str
    value: float | int | bool | None = None  # None = could not be read now (Nagios "U", HA unavailable)
    unit: str = ""  # UCUM: "V", "A", "Cel", "Hz", "s", "ms", "%", "1" (dimensionless)
    stamp: float | None = None  # when THIS value was read, when that differs from the record's stamp
    warn_low: float | None = None
    warn_high: float | None = None
    error_low: float | None = None
    error_high: float | None = None
    # v0.2: the human reading of the value, for an enumeration or a code ("mode 10" -> "position", "fault 33" -> "motor
    # driver fault on bridge enable"); the page draws "33 · motor driver fault on bridge enable"
    meaning: str = ""

    @property
    def level(self) -> str:
        """Limits -> level. A value strictly beyond a limit trips it; a value equal to the limit is inside."""

        value = self.value
        if value is None:
            return UNKNOWN
        if isinstance(value, bool):
            return OK
        if isinstance(value, float) and not math.isfinite(value):
            return UNKNOWN
        if (self.error_low is not None and value < self.error_low) or (self.error_high is not None and value > self.error_high):
            return ERROR
        if (self.warn_low is not None and value < self.warn_low) or (self.warn_high is not None and value > self.warn_high):
            return WARN
        return OK

    def render(self) -> str:
        """The string a stock viewer shows for this measurement: ``"37.2 V"``."""

        if self.value is None:
            text = "unavailable"
        elif isinstance(self.value, bool):
            text = "true" if self.value else "false"
        elif isinstance(self.value, int):
            text = str(self.value)
        else:
            text = format(self.value, "g")
        return f"{text} {self.unit}" if self.unit not in ("", "1") else text


@dataclass(frozen=True)
class DiagnosticStatus:
    """One check on one entity: ROS ``diagnostic_msgs/DiagnosticStatus`` plus what the field added beside it."""

    # --- the stock five: same names, same meaning as diagnostic_msgs -----------------------------------------
    level: str = UNKNOWN  # unknown | ok | warn | error. The zero value is UNKNOWN: never green by accident
    name: str = ""  # the check, as a person reads it ("fdcanusb 272813EF: comms"); machines key on path
    message: str = ""  # one human sentence: a rendering of code + values
    hardware_id: str = ""  # what it is: a repeatable identifier every observer reads identically (REP-107 serial)
    values: tuple[KeyValue, ...] = ()  # strings for people
    # --- added; to_diagnostic_status strips them ---------------------------------------------------------------
    path: str = ""  # where it is: the entity's position in the tree, "host/usb-1-3/fdcanusb-272813EF/can/15"
    code: str = ""  # stable machine reason, PascalCase; required whenever level != ok
    mode: str | None = None  # readiness (DroneCAN modes); None = not stated
    stamp: float | None = None  # source time of the evaluation, seconds since the Unix epoch (UTC)
    received: float | None = None  # observer's clock on arrival; staleness is judged on this one
    expected_period_s: float | None = None  # producer's promise: a fresh record at least this often; None = on change
    uptime_s: float | None = None  # seconds since the reporter started; going backwards = it restarted
    source: str = ""  # who reported it ("moteus_servo", "io_facts.usb")
    measurements: tuple[Measurement, ...] = ()  # typed values; rendered into values for stock viewers
    # v0.1 (2026-10-23): a composite's or rollup's verdict names the members whose own reports caused it (entity
    # paths): the rig row's "missing: CAN 15", the spider's "leg 3 failed". summarize then counts the record as their
    # consequence, never as a cause of its own (docs/diagnostics-record-v0.md "v0.1: caused_by"). On the stock side it
    # rides in one value key, CAUSED_BY_KEY.
    caused_by: tuple[str, ...] = ()

    # ROS-style constants, so ``status.level == DiagnosticStatus.WARN`` reads like rclpy code. No STALE:
    # in v0 nobody authors it; the observer derives it.
    OK: ClassVar[str] = OK
    WARN: ClassVar[str] = WARN
    ERROR: ClassVar[str] = ERROR
    UNKNOWN: ClassVar[str] = UNKNOWN

    def __post_init__(self):
        values = tuple(KeyValue(pair["key"], pair["value"]) if isinstance(pair, Mapping) else KeyValue(*pair)
                       for pair in self.values)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "measurements", tuple(self.measurements))
        caused_by = (self.caused_by,) if isinstance(self.caused_by, str) else self.caused_by  # one path is one member
        object.__setattr__(self, "caused_by", tuple(caused_by))


@dataclass(frozen=True)
class State:
    """v0.2: an entity's live values, with no health claim (docs/state-config-v1.md section 1).

    No level and no code: the host does not roll it up, debounce it into a fault or draw it in a health bar. ``name`` is
    the group heading in the State section (empty = the entity's one group). ``received`` is set by the observer.
    """

    path: str = ""
    name: str = ""
    stamp: float | None = None
    received: float | None = None
    expected_period_s: float | None = None
    source: str = ""
    measurements: tuple[Measurement, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "measurements", tuple(self.measurements))


@dataclass(frozen=True)
class Config:
    """v0.2: one read-only setting of an entity, as the driver read it (docs/state-config-v1.md section 2)."""

    path: str = ""
    key: str = ""
    value: Any = None  # a JSON scalar
    unit: str = ""
    group: str = ""
    expected: Any = None  # what it should be, and where that comes from
    expected_source: str = ""
    stamp: float | None = None  # when the driver read it
    source: str = ""
    # v0.2, editing (docs/state-config-v1.md section 8): ``value`` is what the board runs now (live); ``stored`` is what it keeps
    # across a restart (a different value = "not saved"); the rest tells a page how to offer an edit and a reset
    writable: bool = False
    type: str = ""  # int | float | bool | enum | string
    range: tuple | None = None  # (low, high) for a number
    choices: tuple = ()  # the allowed values of an enum
    default: Any = None  # the factory default, when the driver knows it
    stored: Any = None
    safety: bool = False  # a safety key: asks a second confirm and is refused unless the board is stopped (mode 0)


@dataclass(frozen=True)
class Frame:
    """v0.2: the metadata of one image of a declared stream; the image itself is the file ``file`` in the directory the
    host named in ``IO_DIAGNOSTICS_FRAME_DIR`` (docs/state-config-v1.md section 3)."""

    path: str = ""
    stream: str = ""
    file: str = ""  # a plain file name
    format: str = "jpeg"  # jpeg | png
    width: int | None = None
    height: int | None = None
    stamp: float | None = None
    seq: int | None = None
    band: int | None = None  # hyperspectral: the band index this preview shows
    received: float | None = None


@dataclass(frozen=True)
class Action:
    """A utility the owner registers (printer-driver style); the host only shows it and the owner runs it.

    ``preconditions`` are ``(check, text)`` pairs from a small vocabulary the host can evaluate passively:
    ``port_free``, ``bus_lock_free``, ``camera_free``, ``board_answers``, ``update_available``.
    ``confirm`` may use ``{name}``, ``{id}``, ``{serial}``, ``{latest}``, filled in by the host.
    """

    name: str  # "run_comms_check", "clear_faults", "reset", "update_firmware", "identify"
    title: str  # the button label
    description: str
    danger: str  # "query" (reads only) / "interrupts" (drops the device for a moment) / "changes" (writes state) / "moves" (hardware moves)
    scope: str  # which of the driver's entities it attaches to: "device" (the USB device) or "node" (one board)
    confirm: str
    preconditions: tuple[tuple[str, str], ...] = ()
    kind: str = CHANGES


@dataclass(frozen=True)
class Entity:
    """What and where a thing is. Sent once and on change (Autoware struct vs status, DroneCAN GetNodeInfo)."""

    path: str  # the key every status, event and fault refers to
    name: str = ""
    kind: str = ""  # host / port / device / bus / node / part / stream: the UI's icon and wording
    via: str | None = None  # connectivity parent: the port, hub, adapter or bus it is reached through
    part_of: str | None = None  # composition parent: the product it is a logical part of
    hardware_id: str = ""  # the repeatable identifier; parts carry none (it belongs to the physical parent)
    identity: Mapping[str, str] = field(default_factory=dict)  # descriptive, may change: IDENTITY_KEYS
    owner: str = ""  # who registered it and executes its actions ("whoever owns the port does the talking")
    actions: tuple[Action, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "actions", tuple(self.actions))


@dataclass(frozen=True)
class Event:
    """Something happened: discrete, sequenced, with a log severity (MAVLink events, Cyphal Record)."""

    code: str  # "Plugged", "Unplugged", "ReEnumerated", "Restarted", "FaultConfirmed", ...
    stamp: float | None = None
    severity: str = "info"  # debug | info | warn | error | fatal
    message: str = ""
    path: str = ""
    hardware_id: str = ""  # so an unplug is tied to the device even after its path is gone
    args: Mapping[str, Any] = field(default_factory=dict)  # what the message renders: {"port": "1-5", "flaps": 4}
    seq: int | None = None  # per source, +1 per event: a jump means events were lost
    source: str = ""
    # v0.2, the log record (OpenTelemetry LogRecord: Timestamp is ``stamp``, ObservedTimestamp is ``received``)
    received: float | None = None  # when the observer (the host) first saw it, on its own clock; ``stamp`` is the source's
    count: int = 1  # a coalesced repeat: this many identical events (same code, source, path, message) in one row
    last_stamp: float | None = None  # ... the latest of them; ``stamp`` stays the first (K8s Event series.count)


@dataclass
class Fault:
    """The host's memory of one (entity, code) failing (ros2_medkit Fault, SOVD fault, UDS DTC status)."""

    path: str
    code: str
    severity: str = WARN  # warn | error: the level of the latest failing report
    status: str = PENDING  # pending -> confirmed -> healed; cleared by the operator
    active: bool = False  # failing in the latest evaluated report (UDS testFailed); confirmed faults may be idle
    occurrence_count: int = 0  # confirmed occurrences, counted on edges, not reports
    first_occurred: float | None = None  # start of the current occurrence
    last_occurred: float | None = None  # latest failing report
    last_passed: float | None = None  # latest passing report
    message: str = ""
    hardware_id: str = ""
    snapshot: Mapping[str, Any] = field(default_factory=dict)  # freeze frame: to_dict() of the confirming status


_KINDS = {"status": DiagnosticStatus, "entity": Entity, "event": Event, "fault": Fault, "action": Action,
          "state": State, "config": Config, "frame": Frame}
_KIND_OF = {cls: kind for kind, cls in _KINDS.items()}


# ----- stock ROS projection -------------------------------------------------------------------------------
def _get(message, key, default=None):
    if isinstance(message, Mapping):
        return message.get(key, default)
    return getattr(message, key, default)


def to_diagnostic_status(status: DiagnosticStatus) -> dict:
    """The stock ``diagnostic_msgs/DiagnosticStatus`` as a plain dict: what rqt, Lichtblick, Foxglove, medkit read.

    Strips every added field (Zach, 2026-10-22: "it makes it backwards compatible"). Typed measurements are
    rendered after the producer's own strings, so a stock viewer still shows the numbers, and ``caused_by`` (v0.1)
    last, as one ``caused_by`` value (the paths joined by ", "), so a person in rqt still reads who caused it.
    ``level`` is an int; a ROS 2 Python bridge sends ``bytes([level])``, because rosidl maps .msg ``byte`` to ``octet``.
    """

    if status.level not in ROS_LEVEL:
        raise ValueError(f"level {status.level!r} is not one of {LEVELS} (STALE is derived, never sent)")
    return {
        "level": ROS_LEVEL[status.level],
        "name": status.name,
        "message": status.message,
        "hardware_id": status.hardware_id,
        "values": [{"key": key, "value": value} for key, value in status.values]
        + [{"key": measurement.key, "value": measurement.render()} for measurement in status.measurements]
        + ([{"key": CAUSED_BY_KEY, "value": CAUSED_BY_SEPARATOR.join(status.caused_by)}] if status.caused_by else []),
    }


def from_diagnostic_status(message, **added) -> DiagnosticStatus:
    """Any stock DiagnosticStatus (a dict, or an rclpy message) -> v0. ``added`` sets v0-only fields the
    caller knows (``path``, ``stamp``, ``received``, ``source``). Levels outside 0-3 are not valid stock
    messages; they come in as UNKNOWN. A ``caused_by`` value stays a value: like a rendered measurement, it is a
    string for people once it went through a stock viewer."""

    level = _get(message, "level", 3)
    if isinstance(level, (bytes, bytearray)):  # ROS 2 Python delivers a byte field as bytes of length 1
        level = level[0]
    values = tuple(KeyValue(_get(pair, "key", ""), _get(pair, "value", "")) for pair in _get(message, "values", ()) or ())
    return DiagnosticStatus(level=FROM_ROS_LEVEL.get(int(level), UNKNOWN), name=_get(message, "name", ""),
                            message=_get(message, "message", ""), hardware_id=_get(message, "hardware_id", ""),
                            values=values, **added)


def to_diagnostic_array(statuses, stamp: float, frame_id: str = "") -> dict:
    """A stock ``diagnostic_msgs/DiagnosticArray``. The stamp is required: an unstamped array is instantly stale
    in Lichtblick and missing in other tools (ros/diagnostics #341)."""

    seconds = math.floor(stamp)
    nanoseconds = round((stamp - seconds) * 1e9)
    if nanoseconds >= 1_000_000_000:
        seconds, nanoseconds = seconds + 1, nanoseconds - 1_000_000_000
    return {"header": {"stamp": {"sec": int(seconds), "nanosec": int(nanoseconds)}, "frame_id": frame_id},
            "status": [to_diagnostic_status(status) for status in statuses]}


def from_diagnostic_array(array, received: float | None = None) -> list[DiagnosticStatus]:
    """A stock ``/diagnostics`` message -> v0 records. Each record gets the array's header stamp as its own
    ``stamp``: the only source time a stock stream has, and it now travels with the record."""

    stamp_message = _get(_get(array, "header", {}), "stamp", {})
    stamp = _get(stamp_message, "sec", 0) + _get(stamp_message, "nanosec", 0) / 1e9
    return [from_diagnostic_status(status, stamp=stamp, received=received) for status in _get(array, "status", ())]


# ----- stock ROS log projection (v0.2) --------------------------------------------------------------------------
# WHY these placements (docs/diagnostics-record-v0.md "v0.2"): rcl_interfaces/Log has stamp, level, name, msg, file,
# function and line. The logger ``name`` is the entity path dotted, so ROS's own hierarchy (``abc.def`` sits under
# ``abc``) gives "this entity and its children"; ``function`` carries the code (an empty one is a raw line), ``file``
# the source and ``line`` the per-source sequence (0 = none). It is the trick ``caused_by`` plays on DiagnosticStatus:
# a place a stock viewer already shows, so rqt_console's Location column reads ``source:Code:seq``. ``received``,
# ``args``, ``hardware_id`` and ``last_stamp`` have no stock field and are stripped.
def _split_stamp(stamp: float) -> tuple[int, int]:
    # microsecond resolution: a float64 near 1.8e9 s holds ~0.24 us, so finer digits are noise and would break the
    # stock -> v0 -> stock identity; any stamp a ROS clock wrote at us resolution comes back exactly
    seconds = math.floor(stamp)
    microseconds = round((stamp - seconds) * 1e6)
    if microseconds >= 1_000_000:
        seconds, microseconds = seconds + 1, microseconds - 1_000_000
    return int(seconds), int(microseconds) * 1000


def to_ros_log(event: Event) -> dict:
    """The stock ``rcl_interfaces/msg/Log`` as a plain dict: what rqt_console, Foxglove's Log panel and Lichtblick read.

    ``level`` is 10 / 20 / 30 / 40 / 50 (debug .. fatal). A coalesced repeat is one stock line with `` (xN)`` after
    its message: ROS has no count, and a person in rqt_console should still read that it repeated."""

    if event.severity not in ROS_LOG_LEVEL:
        raise ValueError(f"severity {event.severity!r} is not one of {EVENT_SEVERITIES}")
    if event.stamp is None:
        raise ValueError("an event needs a stamp: a stock Log without one is unordered in every viewer")
    seconds, nanoseconds = _split_stamp(event.stamp)
    return {"stamp": {"sec": seconds, "nanosec": nanoseconds}, "level": ROS_LOG_LEVEL[event.severity],
            "name": event.path.replace("/", "."), "msg": event.message + (f" (x{event.count})" if event.count > 1 else ""),
            "file": event.source, "function": event.code, "line": event.seq or 0}


def _severity_of_level(level: int) -> str:
    for name, below in (("debug", 20), ("info", 30), (WARN, 40), (ERROR, 50)):
        if level < below:
            return name
    return "fatal"


def from_ros_log(message, **added) -> Event:
    """Any stock ``rcl_interfaces/msg/Log`` (a dict, or an rclpy message) -> an Event. A /rosout line has no code, so it
    comes in as a raw line (``code`` ""); one that went through ``to_ros_log`` brings its code back from ``function``.
    ``added`` sets v0-only fields the caller knows: ``path`` when the entity is not just the dotted logger name,
    ``hardware_id``, ``args``, ``received``. Levels between the stock five fall to the band below (25 is info)."""

    stamp_message = _get(message, "stamp", {})
    level = _get(message, "level", 20)
    if isinstance(level, (bytes, bytearray)):  # ROS 2 Python delivers a byte field as bytes of length 1
        level = level[0]
    stamp = _get(stamp_message, "sec", 0) + _get(stamp_message, "nanosec", 0) / 1e9
    fields = {"path": str(_get(message, "name", "")).replace(".", "/"), **added}
    return Event(code=_get(message, "function", ""), stamp=stamp, severity=_severity_of_level(int(level)),
                 message=_get(message, "msg", ""), seq=int(_get(message, "line", 0)) or None,
                 source=_get(message, "file", ""), **fields)


# ----- JSON transport (all five shapes) ------------------------------------------------------------------------
def _default(spec):
    if spec.default is not dataclasses.MISSING:
        return spec.default
    if spec.default_factory is not dataclasses.MISSING:
        return spec.default_factory()
    return dataclasses.MISSING


def _encode(value):
    if isinstance(value, KeyValue):
        return {"key": value.key, "value": value.value}
    if dataclasses.is_dataclass(value):
        return _fields(value)
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _fields(record) -> dict:
    out = {}
    for spec in dataclasses.fields(record):
        value = getattr(record, spec.name)
        if spec.name != "level" and value == _default(spec):
            continue  # omit defaults (K8s omitempty); level is always written so a line reads on its own
        out[spec.name] = _encode(value)
    return out


def to_dict(record) -> dict:
    """JSON-ready dict of any v0 record: ``schema`` + ``record`` (which shape) + every field that differs from its
    default. WHY "record", not "kind": ``Entity.kind`` (host, port, device...) is a field of its own."""

    return {"schema": SCHEMA, "record": _KIND_OF[type(record)], **_fields(record)}


def _build(cls, data: Mapping):
    names = {spec.name for spec in dataclasses.fields(cls)}
    kwargs = {}
    for name, value in data.items():
        if name not in names:
            continue  # open world: a newer minor's optional fields are ignored, never an error
        if name == "values":
            value = tuple(KeyValue(pair["key"], pair["value"]) for pair in value)
        elif name == "measurements":
            value = tuple(_build(Measurement, item) for item in value)
        elif name == "actions":
            value = tuple(_build(Action, item) for item in value)
        elif name == "preconditions":
            value = tuple(tuple(pair) for pair in value)
        elif name == "caused_by":
            value = (value,) if isinstance(value, str) else tuple(value)
        elif name in ("range", "choices") and isinstance(value, list):
            value = tuple(value)
        kwargs[name] = value
    return cls(**kwargs)


def from_dict(data: Mapping):
    """The inverse of ``to_dict``. Refuses another schema version rather than misread it."""

    schema = data.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise ValueError(f"schema {schema!r} is not {SCHEMA!r}")
    return _build(_KINDS[data["record"]], data)


# ----- conformance ----------------------------------------------------------------------------------------------
def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _code_problem(code: str, what: str) -> list[str]:
    if not code:
        return [f"{what}: code is required"]
    if len(code) > 64 or not _CODE.match(code):
        return [f"{what}: code {code!r} must be PascalCase, optionally dotted, at most 64 characters"]
    return []


def _measurement_problems(where: str, measurements) -> list[str]:
    problems: list[str] = []
    for measurement in measurements:
        value = measurement.value
        if not measurement.key:
            problems.append(f"{where}: a measurement has no key")
        if value is not None and not isinstance(value, bool) and not _finite(value):
            problems.append(f"{where}: {measurement.key} must be a finite number, a bool or None")
        limits = [measurement.error_low, measurement.warn_low, measurement.warn_high, measurement.error_high]
        if any(limit is not None and not _finite(limit) for limit in limits):
            problems.append(f"{where}: {measurement.key} limits must be finite")
        present = [limit for limit in limits if limit is not None]
        if present != sorted(present):
            problems.append(f"{where}: {measurement.key} limits must satisfy error_low <= warn_low <= warn_high <= error_high")
    return problems


def validate(record) -> list[str]:
    """Problems with a natively produced record; [] = conforms. Records imported from a stock stream lack path
    and code by construction and are not expected to pass."""

    problems: list[str] = []
    if isinstance(record, DiagnosticStatus):
        where = f"status {record.path or '?'} / {record.name or '?'}"
        if record.level not in LEVELS:
            problems.append(f"{where}: level {record.level!r} must be one of {LEVELS}; STALE is derived by the observer")
        elif record.level != OK:
            problems += _code_problem(record.code, where)
        if not record.path:
            problems.append(f"{where}: path is required")
        if not record.name:
            problems.append(f"{where}: name is required")
        if record.mode is not None and record.mode not in MODES:
            problems.append(f"{where}: mode {record.mode!r} must be one of {MODES}")
        if record.expected_period_s is not None and not (_finite(record.expected_period_s) and record.expected_period_s > 0):
            problems.append(f"{where}: expected_period_s must be a positive number or None")
        if record.uptime_s is not None and not (_finite(record.uptime_s) and record.uptime_s >= 0):
            problems.append(f"{where}: uptime_s must be >= 0")
        keys = [pair.key for pair in record.values] + [measurement.key for measurement in record.measurements]
        for key, count in Counter(keys).items():
            if count > 1:
                problems.append(f"{where}: key {key!r} appears {count} times across values and measurements")
        if record.caused_by:
            if any(not isinstance(member, str) or not member for member in record.caused_by):
                problems.append(f"{where}: caused_by must list entity paths (non-empty strings)")
            elif record.path and record.path in record.caused_by:
                problems.append(f"{where}: caused_by names its own entity; a record cannot be its own member's consequence")
            if CAUSED_BY_KEY in keys:
                problems.append(f"{where}: key {CAUSED_BY_KEY!r} is where caused_by rides on the stock side; rename the value")
        problems += _measurement_problems(where, record.measurements)
    elif isinstance(record, Entity):
        where = f"entity {record.path or '?'}"
        if not record.path:
            problems.append(f"{where}: path is required")
        if record.via and record.part_of:
            problems.append(f"{where}: has both via and part_of; an entity has one parent edge")
        if record.path and record.path in (record.via, record.part_of):
            problems.append(f"{where}: is its own parent")
        if record.part_of and (record.hardware_id or record.identity):
            problems.append(f"{where}: a part carries no hardware_id or identity; they belong to the physical parent")
        for key in record.identity:
            if key not in IDENTITY_KEYS:
                problems.append(f"{where}: identity key {key!r} is not one of {IDENTITY_KEYS}")
        for action in record.actions:
            problems += validate(action)
    elif isinstance(record, State):
        where = f"state {record.path or '?'} / {record.name or '-'}"
        if record.expected_period_s is not None and not (_finite(record.expected_period_s) and record.expected_period_s > 0):
            problems.append(f"{where}: expected_period_s must be a positive number or None")
        keys = [measurement.key for measurement in record.measurements]
        for key, count in Counter(keys).items():
            if count > 1:
                problems.append(f"{where}: key {key!r} appears {count} times")
        problems += _measurement_problems(where, record.measurements)
    elif isinstance(record, Config):
        where = f"config {record.path or '?'} / {record.key or '?'}"
        if not record.key:
            problems.append(f"{where}: key is required")
        for name in ("value", "expected"):
            if isinstance(getattr(record, name), (dict, list)):
                problems.append(f"{where}: {name} must be a JSON scalar")
        if record.expected is None and record.expected_source:
            problems.append(f"{where}: expected_source without an expected value")
        if record.type and record.type not in CONFIG_TYPES:
            problems.append(f"{where}: type {record.type!r} must be one of {CONFIG_TYPES}")
        if record.range is not None and not (len(record.range) == 2 and all(_finite(bound) for bound in record.range)
                                             and record.range[0] <= record.range[1]):
            problems.append(f"{where}: range must be (low, high) with low <= high")
        if record.safety and not record.writable:
            problems.append(f"{where}: a safety key that is not writable has nothing to guard")
    elif isinstance(record, Frame):
        where = f"frame {record.path or '?'} / {record.stream or '?'}"
        if not record.stream:
            problems.append(f"{where}: stream is required")
        if not record.file or "/" in record.file or record.file.startswith("."):
            problems.append(f"{where}: file must be a plain file name in the frame directory, got {record.file!r}")
        if record.format not in ("jpeg", "png"):
            problems.append(f"{where}: format {record.format!r} must be jpeg or png")
    elif isinstance(record, Event):
        where = f"event {record.code or '?'}"
        problems += _code_problem(record.code, where)
        if record.stamp is None:
            problems.append(f"{where}: stamp is required")
        if record.severity not in EVENT_SEVERITIES:
            problems.append(f"{where}: severity {record.severity!r} must be one of {EVENT_SEVERITIES}")
        if not isinstance(record.count, int) or isinstance(record.count, bool) or record.count < 1:
            problems.append(f"{where}: count must be a whole number >= 1")
        if record.last_stamp is not None and record.stamp is not None and not record.last_stamp >= record.stamp:
            problems.append(f"{where}: last_stamp must not be before stamp")
    elif isinstance(record, Fault):
        where = f"fault {record.path} / {record.code}"
        problems += _code_problem(record.code, where)
        if record.status not in FAULT_STATUSES:
            problems.append(f"{where}: status {record.status!r} must be one of {FAULT_STATUSES}")
        if record.severity not in (WARN, ERROR):
            problems.append(f"{where}: severity must be warn or error")
    elif isinstance(record, Action):
        where = f"action {record.name or '?'}"
        if not re.match(r"^[a-z][a-z0-9_]*$", record.name):
            problems.append(f"{where}: name must be snake_case")
        if record.danger not in DANGERS:
            problems.append(f"{where}: danger {record.danger!r} must be one of {DANGERS}")
        label = STANDARD_ACTIONS.get(record.name)
        if label is not None and record.title != label:
            qualified = (
                record.name in QUALIFIED_ACTIONS
                and record.title.startswith(label)
                and _QUALIFIER.match(record.title[len(label):]) is not None
            )
            if not qualified:
                how = f" (or the label plus the way in brackets, like '{label} ({QUALIFIER_EXAMPLE[record.name]})')" if record.name in QUALIFIED_ACTIONS else ""
                problems.append(f"{where}: a standard action's title must be its label {label!r}{how}, got {record.title!r}")
        if record.kind not in (PASSIVE, TOUCHES, CHANGES):
            problems.append(f"{where}: kind {record.kind!r} must be passive, touches or changes")
    else:
        problems.append(f"not a v0 record: {type(record).__name__}")
    return problems


# ----- host rule 1: freshness ----------------------------------------------------------------------------------
def effective_level(status: DiagnosticStatus, now: float, *, factor: float = STALE_FACTOR,
                    floor_s: float = STALE_FLOOR_S) -> str:
    """The level to act on now: the reported level, or STALE once the record is overdue.

    Judged on ``received`` (the observer's clock), falling back to ``stamp``: a producer's clock can drift or
    be unset. An event-driven record (no expected period) never goes stale; a periodic one with no time at all
    is stale, as Lichtblick treats an unstamped array.
    """

    if status.level not in RANK:
        return UNKNOWN  # a level nobody defined is a claim nobody can read: "can't tell", never green
    if status.expected_period_s is None:
        return status.level
    seen = status.received if status.received is not None else status.stamp
    if seen is None or now - seen > max(factor * status.expected_period_s, floor_s):
        return STALE
    return status.level


def restarted(previous: DiagnosticStatus, current: DiagnosticStatus) -> bool:
    """True when the reporter's uptime went backwards: a reboot or brown-out a level can hide (DroneCAN)."""

    return previous.uptime_s is not None and current.uptime_s is not None and current.uptime_s < previous.uptime_s


# ----- host rule 2: composition (Zach, 2026-10-22: black boxes self-report) -------------------------------------
@dataclass(frozen=True)
class Rollup:
    """Advisory "worst below": the worst independent cause at or below an entity, knock-on failures collapsed."""

    level: str | None = None  # None = nothing at or below reports anything
    origin: str = ""  # path of the cause the level came from
    message: str = ""  # "<cause name>: <its message>"
    counts: Mapping[str, int] = field(default_factory=dict)  # independent causes per level
    knock_on: tuple[tuple[str, str], ...] = ()  # (path, root-cause path): shown on their rows, not counted


@dataclass(frozen=True)
class Summary:
    path: str
    own: str | None  # the entity's self-report: worst effective level of its own records; None = pure grouping
    level: str | None  # what its row shows: own when it self-reports, else rollup.level
    message: str
    rollup: Rollup


def _explains(ancestor_level: str, level: str) -> bool:
    """Whether a failed ancestor accounts for a problem below it (AUTOSAR DEM consecutive fault, Nagios
    UNREACHABLE). An ERROR explains anything below; silence explains only silence. A fresh report from below
    proves the path works, so a stale parent never swallows a child's fresh WARN or ERROR."""

    return ancestor_level == ERROR or (ancestor_level in (UNKNOWN, STALE) and level in (UNKNOWN, STALE))


def _stale_message(status: DiagnosticStatus, now: float) -> str:
    seen = status.received if status.received is not None else status.stamp
    age = "no update time" if seen is None else f"no update for {now - seen:.0f} s"
    return f"{age} (last: {status.message})" if status.message else age


def summarize(entities, statuses, now: float, *, factor: float = STALE_FACTOR,
              floor_s: float = STALE_FLOOR_S) -> dict[str, Summary]:
    """Every entity's row: its own self-report, and the advisory rollup of everything at or below it.

    The composition rule (Zach, 2026-10-22, "In black-box design it's up to each lower-level black box to
    self-report what it means in the context of THAT black box"):

    - An entity with records of its own is a black box: its row shows its own level, the worst of its own
      records (STALE when overdue). A composite may wrap its children into that judgement: a spider with one
      failed leg reports WARN. The host never overrides a self-report.
    - ``rollup`` is advisory, Redfish ``HealthRollup`` to ``Health``: the worst independent cause at or below,
      so a row can hint "a child is in ERROR". A pure grouping node (no records of its own) shows its rollup.
    - Knock-on collapse: a problem below a failed ancestor (on the via / part_of chain, inside the subtree)
      counts once, as that ancestor; the rest are listed in ``knock_on``. One dead bus is one cause, not N+1.
    - ``caused_by`` (v0.1 addendum) explains the other way, UP: a composite or rollup whose every non-OK record
      names members that each self-report a problem is those members' consequence. It is listed in ``knock_on``
      against its worst member's root cause and never counted, and it explains nothing below it, so a spider at
      ERROR naming leg 3 no longer swallows leg 3. A record that names nothing, names an entity that is OK, has
      no records or does not exist, or is STALE itself, stays its own cause; its row keeps its own level and
      message either way.
      ``via`` is unchanged: a dead adapter still explains its silent boards.

    ``statuses`` may hold history; the last record per (entity, name) wins. A record with no path attaches to
    the entity with the same ``hardware_id`` (repeatable identity), else to an implicit root entity.
    """

    by_path: dict[str, Entity] = {entity.path: entity for entity in entities}
    by_hardware = {entity.hardware_id: entity.path for entity in entities if entity.hardware_id and not entity.part_of}
    latest: dict[tuple[str, str], DiagnosticStatus] = {}
    for status in statuses:
        key = status.path or by_hardware.get(status.hardware_id) or status.hardware_id or status.name
        by_path.setdefault(key, Entity(path=key, name=key))
        latest[(key, status.name)] = status
    own_records: dict[str, list[DiagnosticStatus]] = defaultdict(list)
    for (key, _name), status in latest.items():
        own_records[key].append(status)

    parent = {path: (entity.part_of or entity.via) for path, entity in by_path.items()}
    children: dict[str, list[str]] = defaultdict(list)
    roots = []
    for path in by_path:
        if parent[path] in by_path:
            children[parent[path]].append(path)
        else:
            parent[path] = None
            roots.append(path)
    order: list[str] = []  # pre-order, so ancestors come first and a subtree is a contiguous slice
    depth: dict[str, int] = {}
    stack = [(root, 0) for root in reversed(roots)]
    while stack:
        path, level_depth = stack.pop()
        depth[path] = level_depth
        order.append(path)
        stack.extend((child, level_depth + 1) for child in reversed(children[path]))
    if len(order) != len(by_path):
        raise ValueError(f"parent edges form a cycle: {sorted(set(by_path) - set(order))}")
    index = {path: position for position, path in enumerate(order)}
    size = {path: 1 for path in order}
    for path in reversed(order):
        if parent[path] is not None:
            size[parent[path]] += size[path]

    own: dict[str, str | None] = {}
    own_message: dict[str, str] = {}
    for path in order:
        records = own_records.get(path)
        if not records:
            own[path], own_message[path] = None, ""
            continue
        worst = max(records, key=lambda status: RANK[effective_level(status, now, factor=factor, floor_s=floor_s)])
        own[path] = effective_level(worst, now, factor=factor, floor_s=floor_s)
        own_message[path] = _stale_message(worst, now) if own[path] == STALE else worst.message

    def members(path: str) -> list[str] | None:
        """The members whose own problems cause every problem this entity reports, or None: it is its own cause."""

        named: list[str] = []
        for status in own_records.get(path, ()):
            level = effective_level(status, now, factor=factor, floor_s=floor_s)
            if level == OK:
                continue
            # WHY a stale composite is its own cause: silence is the wrapper's own failure, whatever it last said
            if level == STALE or not status.caused_by:
                return None
            for member in status.caused_by:
                # WHY only a member that itself reports a problem: naming an OK, absent or record-less entity
                # cannot explain anything, so the composite's verdict must stand as a cause on its own
                if member == path or own.get(member) in (None, OK):
                    return None
                if member not in named:
                    named.append(member)
        return named or None

    blamed: dict[str, str] = {}  # composite -> the member it is the consequence of (its worst, then the shallowest)
    for path in order:
        if own[path] in (None, OK):
            continue
        named = members(path)
        if named:
            blamed[path] = max(named, key=lambda member: (RANK[own[member]], -depth[member], -index[member]))

    nearest: dict[str, str | None] = {}  # what explains this entity's problem: a failed ancestor, or a named member
    for path in order:
        nearest[path] = None
        if own[path] in (None, OK):
            continue
        if path in blamed:
            nearest[path] = blamed[path]
            continue
        ancestor = parent[path]
        while ancestor is not None:
            # WHY a consequence explains nothing below: its verdict is its members' problem, not a failure of its own
            if own[ancestor] is not None and ancestor not in blamed and _explains(own[ancestor], own[path]):
                nearest[path] = ancestor
                break
            ancestor = parent[ancestor]
    # members can name each other (a producer's mistake): break every cycle at its first entity in tree order, so a
    # problem is never explained away by a ring of consequences
    resolved: set[str] = set()
    for start in order:
        trail, current = [], start
        while current is not None and current not in resolved and current not in trail:
            trail.append(current)
            current = nearest[current]
        if current is not None and current in trail:
            ring = trail[trail.index(current):]
            nearest[min(ring, key=index.__getitem__)] = None
        resolved.update(trail)

    def label(path: str) -> str:
        return by_path[path].name or path

    summaries: dict[str, Summary] = {}
    for path in order:
        low, high = index[path], index[path] + size[path]

        def inside(other: str | None) -> bool:
            # WHY by subtree slice, not depth: a named member can sit anywhere in the tree (the rig row's boards
            # hang under their adapters); for an ancestor the two tests agree
            return other is not None and low <= index[other] < high

        causes, knock_on, reported = [], [], False
        for member in order[low:high]:
            if own[member] is None:
                continue
            reported = True
            if own[member] == OK:
                continue
            cause = nearest[member]
            if not inside(cause):
                causes.append(member)
                continue
            while inside(nearest[cause]):
                cause = nearest[cause]
            knock_on.append((member, cause))
        if causes:
            # worst first; among equals the shallowest (closest to this row), then tree order
            top = max(causes, key=lambda member: (RANK[own[member]], -depth[member]))
            rollup = Rollup(own[top], top, f"{label(top)}: {own_message[top]}",
                            dict(Counter(own[member] for member in causes)), tuple(knock_on))
        else:
            rollup = Rollup(OK if reported else None)
        if own[path] is not None:
            summaries[path] = Summary(path, own[path], own[path], own_message[path], rollup)
        else:
            summaries[path] = Summary(path, None, rollup.level, rollup.message, rollup)
    return summaries


# ----- host rule 3: faults ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Debounce:
    """When a failing check becomes a confirmed fault, and when a confirmed fault heals.

    Counts are consecutive evaluated *reports*, so they only work for a stream that repeats while the condition
    holds (``expected_period_s`` set). An event-driven stream reports each change once; for it counts are 1 and
    a time rule (``confirm_s``, also applied by ``FaultManager.tick``) is the real debounce: ros2_medkit's
    documented trap, and the field report that time and count debounce are not interchangeable.
    """

    confirm_count: int = 3
    confirm_s: float | None = None  # time rule: failing continuously this long; replaces the count rule
    heal_count: int = 3


# WHY these defaults: a WARN must repeat before it is remembered (ros2_medkit holds a WARN until 3 reports; DEM's
# threshold 3 is "confirm in the second cycle after"); an ERROR is remembered at once (DEM threshold 1,
# "immediate confirmation, typical for non-OBD DTCs"; medkit lets ERROR through unfiltered).
DEFAULT_DEBOUNCE = {WARN: Debounce(confirm_count=3), ERROR: Debounce(confirm_count=1)}


@dataclass
class _Track:
    stream: tuple[str, str]  # (entity key, check name) that reports this code
    event_driven: bool = False
    fail_streak: int = 0
    fail_since: float | None = None
    pass_streak: int = 0


class FaultManager:
    """Turns the status stream into faults. A fault never feeds back into a status level: the self-report stands.

    - A WARN or ERROR report is a FAILED result for (entity, code); ``code`` falls back to the check name.
    - An OK report, or the same check failing with another code, is a PASSED result for the codes it had open.
    - An UNKNOWN report, and silence, move nothing: a check that did not run is not a pass (UDS "test not
      completed", medkit's NOT_EVALUATED).
    - pending --(debounce)--> confirmed --(heal_count passes)--> healed; ``clear`` -> cleared. A pending fault that
      passes is forgotten. A healed or cleared fault that fails again starts a new occurrence.
    """

    def __init__(self, rules: Mapping[str, Debounce] | None = None,
                 defaults: Mapping[str, Debounce] = DEFAULT_DEBOUNCE):
        self.rules = dict(rules or {})  # per-code overrides
        self.defaults = dict(defaults)  # per severity
        self._faults: dict[tuple[str, str], Fault] = {}
        self._tracks: dict[tuple[str, str], _Track] = {}
        self._last: dict[tuple[str, str], DiagnosticStatus] = {}
        self._seq = 0

    @property
    def faults(self) -> list[Fault]:
        return list(self._faults.values())

    def fault(self, path: str, code: str) -> Fault | None:
        return self._faults.get((path, code))

    def _rule(self, code: str, severity: str) -> Debounce:
        return self.rules.get(code) or self.defaults.get(severity) or Debounce()

    def _event(self, code: str, fault: Fault, stamp: float, severity: str, message: str) -> Event:
        self._seq += 1
        return Event(code=code, stamp=stamp, severity=severity, message=message, path=fault.path,
                     hardware_id=fault.hardware_id, args={"fault": fault.code, "occurrence": fault.occurrence_count},
                     seq=self._seq, source="fault_manager")

    def observe(self, status: DiagnosticStatus, now: float | None = None) -> list[Event]:
        """Feed one status record; returns the lifecycle events it caused (FaultConfirmed, FaultHealed)."""

        stamp = next((t for t in (status.stamp, status.received, now) if t is not None), None)
        if stamp is None:
            raise ValueError("observe needs a time: status.stamp, status.received or now")
        if status.level not in (OK, WARN, ERROR):
            return []
        key = status.path or status.hardware_id or status.name
        stream = (key, status.name)
        self._last[stream] = status
        failing = (status.code or status.name) if status.level in (WARN, ERROR) else None
        events: list[Event] = []
        for fault_key, track in list(self._tracks.items()):
            if track.stream == stream and fault_key[1] != failing:
                events += self._passed(fault_key, status, stamp)
        if failing:
            events += self._failed((key, failing), stream, status, stamp)
        return events

    def _failed(self, fault_key, stream, status: DiagnosticStatus, stamp: float) -> list[Event]:
        track = self._tracks.setdefault(fault_key, _Track(stream))
        track.event_driven = status.expected_period_s is None
        fault = self._faults.get(fault_key)
        if fault is None or fault.status in (HEALED, CLEARED):
            if fault is None:
                fault = self._faults[fault_key] = Fault(path=fault_key[0], code=fault_key[1], hardware_id=status.hardware_id)
            fault.status, fault.first_occurred = PENDING, stamp
            track.fail_streak = 0
        if track.fail_streak == 0:
            track.fail_since = stamp
        track.fail_streak += 1
        track.pass_streak = 0
        fault.active, fault.last_occurred = True, stamp
        fault.severity, fault.message = status.level, status.message
        if fault.status == PENDING and self._due(track, self._rule(fault.code, fault.severity), stamp):
            return [self._confirm(fault, status, stamp)]
        return []

    @staticmethod
    def _due(track: _Track, rule: Debounce, stamp: float) -> bool:
        if rule.confirm_s is not None:
            return stamp - track.fail_since >= rule.confirm_s
        return track.fail_streak >= (1 if track.event_driven else rule.confirm_count)

    def _confirm(self, fault: Fault, status: DiagnosticStatus, stamp: float) -> Event:
        fault.status = CONFIRMED
        fault.occurrence_count += 1
        fault.snapshot = to_dict(status)
        return self._event("FaultConfirmed", fault, stamp, fault.severity,
                           f"{fault.code} confirmed on {fault.path}: {fault.message}")

    def _passed(self, fault_key, status: DiagnosticStatus, stamp: float) -> list[Event]:
        fault, track = self._faults.get(fault_key), self._tracks[fault_key]
        if fault is None or fault.status in (HEALED, CLEARED):
            return []
        fault.active, fault.last_passed = False, stamp
        track.fail_streak = 0
        track.pass_streak += 1
        if fault.status == PENDING:  # never confirmed: nothing to remember
            del self._faults[fault_key], self._tracks[fault_key]
            return []
        if track.pass_streak >= (1 if track.event_driven else self._rule(fault.code, fault.severity).heal_count):
            fault.status = HEALED
            return [self._event("FaultHealed", fault, stamp, "info", f"{fault.code} healed on {fault.path}")]
        return []

    def tick(self, now: float) -> list[Event]:
        """Apply time rules between reports: an event-driven stream may say FAILED once and then stay quiet.
        A periodic stream that went stale is silent, and silence is not evidence: it never confirms here."""

        events = []
        for fault_key, fault in list(self._faults.items()):
            track = self._tracks[fault_key]
            rule = self._rule(fault.code, fault.severity)
            if fault.status != PENDING or not fault.active or rule.confirm_s is None:
                continue
            last = self._last.get(track.stream)
            if last is None or effective_level(last, now) == STALE:
                continue
            if now - track.fail_since >= rule.confirm_s:
                events.append(self._confirm(fault, last, now))
        return events

    def clear(self, path: str, code: str, now: float) -> Event | None:
        """The operator's acknowledgement (UDS ClearDTC, medkit ClearFault). Fails again -> a new occurrence."""

        fault = self._faults.get((path, code))
        if fault is None:
            return None
        fault.status, fault.active = CLEARED, False
        track = self._tracks.get((path, code))
        if track:
            track.fail_streak = track.pass_streak = 0
        return self._event("FaultCleared", fault, now, "info", f"{fault.code} cleared on {fault.path}")


__all__ = [
    "SCHEMA", "RECORD_VERSION", "CAUSED_BY_KEY", "CAUSED_BY_SEPARATOR", "STANDARD_ACTIONS", "QUALIFIED_ACTIONS", "STANDARD_EVENT_CODES", "UNKNOWN", "OK", "WARN", "ERROR", "STALE", "LEVELS", "RANK", "ROS_LEVEL", "FROM_ROS_LEVEL", "MODES",
    "STALE_FACTOR", "STALE_FLOOR_S", "EVENT_SEVERITIES", "PENDING", "CONFIRMED", "HEALED", "CLEARED",
    "FAULT_STATUSES", "IDENTITY_KEYS", "PASSIVE", "TOUCHES", "CHANGES", "DANGERS",
    "KeyValue", "Measurement", "DiagnosticStatus", "Action", "Entity", "Event", "Fault",
    "to_diagnostic_status", "from_diagnostic_status", "to_diagnostic_array", "from_diagnostic_array", "to_ros_log", "from_ros_log", "ROS_LOG_LEVEL",
    "to_dict", "from_dict", "validate", "effective_level", "restarted",
    "Rollup", "Summary", "summarize", "Debounce", "DEFAULT_DEBOUNCE", "FaultManager",
]
