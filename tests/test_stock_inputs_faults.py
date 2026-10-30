"""A fault count over a composite reads the drivers' faults, never the stock diagnostics' own. Kept beside the ruler tests/test_stock_inputs.py,
which stays as committed; it shares that file's fixture entities."""

from __future__ import annotations

from test_stock_inputs import fed, status_of  # noqa: F401 - ``fed`` is the module fixture


def test_the_fault_count_is_the_drivers_fault_never_the_diagnostics_own(fed):  # noqa: F811
    # CAN 1's driver fault (MoteusFault.33) is the one confirmed fault below the servo and the deployment; the stock diagnostics' own
    # failures (its loop rate, its fault codes) are confirmed too, and counting them would make an MTTF feed on itself
    for entry_id in ("servo.mttf", "rack.uptime"):
        events = {m.key: m.value for m in status_of(fed, entry_id).measurements}["events"]
        assert events == 1, (entry_id, events)
