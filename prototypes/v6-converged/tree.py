"""The entity tree: one ROS ``DiagnosticStatus`` per row, plus ``path``, ``expected_period_s`` and ``identity``.

Two rules live here and nowhere else (the UI only renders their output):
- **Staleness is computed, not reported.** A record older than ``STALE_FACTOR`` x its expected
  period shows STALE (ROS diagnostics' 4th level), whatever level it last reported.
- **Parents roll up the worst child.** Rank: ok < warn < stale < error. The rolled-up message names
  the entity the level came from, so "fdcanusb 272813EF" reads "CAN 15 driver: fault 33 ...".

WHY stale ranks below error and is not promoted: ROS's aggregator turns a partly-stale group into
ERROR; here a busy port (tview open) makes its boards stale, which is "not watched right now", not
broken. A real error under a stale sibling still wins.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

LEVELS = ("ok", "warn", "stale", "error")
RANK = {level: index for index, level in enumerate(LEVELS)}
STALE_FACTOR = 3.0


@dataclass
class Entity:
    path: str  # tree position, e.g. "host/usb-1-3/fdcanusb-272813EF/can/15"
    name: str
    icon: str  # host / port / hub / adapter / bus / servo / chip / motor / camera / stream / webcam / hid / usb
    kind: str  # host / port / device / bus / node / part / stream
    level: str = "ok"  # as last REPORTED by its source
    message: str = ""
    label: str = ""  # globally readable name for roll-up messages ("CAN 15 driver"); defaults to name
    count: str = ""  # the id/count column
    hardware_id: str = ""
    expected_period_s: float | None = None  # None = event-driven (sysfs presence): never stale
    last_seen: dt.datetime | None = None
    tag: str = "real"  # real / reported / recorded / simulated
    source: str = ""
    os_only: bool = False  # no driver diagnostics: presence, port and descriptors only
    identity: dict = field(default_factory=dict)  # Home Assistant device_info fields
    versions: dict = field(default_factory=dict)
    config: list = field(default_factory=list)  # [key, value, source]
    comms: list = field(default_factory=list)  # [key, value, level, source, tag]
    values: list = field(default_factory=list)  # [key, value, level, source, tag]
    rate: dict | None = None  # {"measured_hz", "expected_hz", "tag"}
    errors: str = ""
    firmware: str = ""
    update: bool | None = None
    actions: list = field(default_factory=list)
    blocked: str = ""
    children: list["Entity"] = field(default_factory=list)


def age_s(entity: Entity, now: dt.datetime) -> float | None:
    return None if entity.last_seen is None else (now - entity.last_seen).total_seconds()


def level_at(entity: Entity, now: dt.datetime) -> str:
    """The level to show now: STALE once the record is older than STALE_FACTOR x its expected period."""

    age = age_s(entity, now)
    if entity.expected_period_s and age is not None and age > STALE_FACTOR * entity.expected_period_s:
        return "stale"
    return entity.level


def worst(levels) -> str:
    return max(levels, key=RANK.__getitem__, default="ok")


@dataclass
class Rolled:
    level: str
    message: str
    origin: Entity
    counts: dict


def rollup(entity: Entity, now: dt.datetime, cache: dict | None = None) -> Rolled:
    """Worst of the entity's own (staleness-aware) level and every descendant's; ties keep the closest."""

    cache = {} if cache is None else cache
    if entity.path in cache:
        return cache[entity.path]
    own = level_at(entity, now)
    best = Rolled(own, entity.message, entity, {level: 0 for level in LEVELS})
    for child in entity.children:
        rolled = rollup(child, now, cache)
        for level, number in rolled.counts.items():
            best.counts[level] += number
        best.counts[level_at(child, now)] += 1
        if RANK[rolled.level] > RANK[best.level]:
            origin = rolled.origin
            best = Rolled(rolled.level, f"{origin.label or origin.name}: {_own_message(origin, now)}", origin, best.counts)
    cache[entity.path] = best
    return best


def _own_message(entity: Entity, now: dt.datetime) -> str:
    if level_at(entity, now) == "stale" and entity.level != "stale":
        return f"no update for {_duration(age_s(entity, now))} (last: {entity.message})"
    return entity.message


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def walk(entity: Entity):
    yield entity
    for child in entity.children:
        yield from walk(child)


def to_json(entity: Entity, now: dt.datetime, cache: dict | None = None, depth: int = 0) -> dict:
    cache = {} if cache is None else cache
    rolled = rollup(entity, now, cache)
    own = level_at(entity, now)
    age = age_s(entity, now)
    return {
        "path": entity.path, "name": entity.name, "icon": entity.icon, "kind": entity.kind, "depth": depth,
        "level": rolled.level, "own_level": own, "reported_level": entity.level,
        "message": rolled.message if rolled.level != own else _own_message(entity, now),
        "own_message": entity.message, "origin": rolled.origin.path, "counts": rolled.counts,
        "count": entity.count, "hardware_id": entity.hardware_id,
        "expected_period_s": entity.expected_period_s,
        "last_seen": entity.last_seen.isoformat(timespec="seconds") if entity.last_seen else None,
        "age_s": age, "age": _duration(age) if age is not None else None, "stale": own == "stale",
        "stale_after_s": STALE_FACTOR * entity.expected_period_s if entity.expected_period_s else None,
        "tag": entity.tag, "source": entity.source, "os_only": entity.os_only,
        "identity": entity.identity, "versions": entity.versions, "config": entity.config,
        "comms": entity.comms, "values": entity.values, "rate": entity.rate, "errors": entity.errors,
        "firmware": entity.firmware, "update": entity.update, "actions": entity.actions, "blocked": entity.blocked,
        "children": [to_json(child, now, cache, depth + 1) for child in entity.children],
    }
