"""The host's fault memory: a thin use of ``contract.record.FaultManager`` (the rules live there, not here).

Fed every NEW status evaluation once, on arrival (``runner.JobCache.apply`` decides what is new, so a replay that
re-sends its recording is one report, not N). Lifecycle transitions come back as ``Event`` records
(``FaultConfirmed`` / ``FaultHealed``) for the activity log. A fault never feeds back into a row's level: the
self-report stands (docs/diagnostics-record-v0.md rule 5).
"""

from __future__ import annotations

import dataclasses
import threading

from contract.record import CONFIRMED, PENDING, Event, Fault, FaultManager, to_dict


class Faults:
    def __init__(self, manager: FaultManager | None = None):
        self.manager = manager or FaultManager()
        self.events: list[Event] = []
        self._lock = threading.Lock()

    def observe(self, statuses, now: float) -> list[Event]:
        events: list[Event] = []
        with self._lock:
            for status in statuses:
                events += self.manager.observe(status, now)
            events += self.manager.tick(now)
            self.events += events
        return events

    def tick(self, now: float) -> list[Event]:
        with self._lock:
            events = self.manager.tick(now)
            self.events += events
        return events

    def all(self) -> list[Fault]:
        with self._lock:
            return [dataclasses.replace(fault) for fault in self.manager.faults]

    def open_by_path(self) -> dict[str, int]:
        """Faults a row should count: pending or confirmed (healed and cleared ones are history)."""

        counts: dict[str, int] = {}
        for fault in self.all():
            if fault.status in (PENDING, CONFIRMED):
                counts[fault.path] = counts.get(fault.path, 0) + 1
        return counts

    def records(self) -> list[dict]:
        return [to_dict(fault) for fault in self.all()]
