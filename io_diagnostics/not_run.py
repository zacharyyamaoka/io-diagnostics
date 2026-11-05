"""Which statuses are a check that did NOT RUN, and the one rule about them.

A stock diagnostic that could not run (nothing feeds its input, no State history yet, too little of it, no decoder, numpy missing, a
diagnostic that raised) is posted as a status at level ``unknown`` so that nothing reads it as OK. That is the right thing to SHOW and the
wrong thing to ROLL UP: it says nothing about the entity, yet "unknown" outranks "warn" in the clock's order, so a board whose real checks
are all OK read "unknown" (purple in the 3D view, "unknown 8" in the header) because of rows that were never judged.

WHY (live Preview, 2026-11-04: all 8 rack boards unknown, caused by NoSource, now NotWired and NotMeasured, and NotEnoughHistory rows): a not-run status

- does not set or raise its entity's level, a parent's rollup, the summary's counts or ``summary.attention``
  (``tree.build_rows`` summarizes without them);
- stays in the entity's checks (``/node``, ``/checks``), where each row carries ``not_run: true`` and its own reason, and is
  NEVER shown as OK; the classic page folds them into one quiet line;
- a real driver check at level unknown (a board whose config could not be read, ``BrsNotTested``) is not one of them and still rolls up.

This module holds only the codes and the predicate, so ``stock_diagnostics`` (which emits them), ``tree`` (which rolls up) and ``host``
(which serializes) share one definition and import nothing of each other.
"""

from __future__ import annotations

SOURCE = "robotdx.diagnostics"  # the stock diagnostics' status source (robotdx.diagnostics.record.SOURCE; a test pins the two together)
UNKNOWN = "unknown"

NO_STATE = "NoState"  # the entity has no State history: not run, and the host posts no row for it
NOT_WIRED = "NotWired"  # an existing producer has the input or could publish it, and nothing maps it here yet: not run, says what would
NOT_MEASURED = "NotMeasured"  # no instrument on this entity can produce the input (no IMU, no pick log, no tag): not run, says what would
NOT_STREAMING = "NotStreaming"  # its stream feeds it, but nothing arrived in the window (it streams while a page watches): not run, no row
NO_DECODER = "NoDecoder"  # a frame in a format nothing here decodes (a JPEG without Pillow, the diagnostics extra)
NOT_ENOUGH_HISTORY = "NotEnoughHistory"  # the State history is shorter than the diagnostic's window
NUMPY_MISSING_CODE = "NumpyMissing"  # the arithmetic needs the diagnostics extra
DIAGNOSTIC_RAISED = "DiagnosticRaised"  # the diagnostic raised; the message holds the exception

# every code StockDiagnostics._not_run / _Missing can carry (tests/test_not_run_rollup.py scans stock_diagnostics.py for any other)
NOT_RUN_CODES = (NO_STATE, NOT_WIRED, NOT_MEASURED, NOT_STREAMING, NO_DECODER, NOT_ENOUGH_HISTORY, NUMPY_MISSING_CODE, DIAGNOSTIC_RAISED)


def is_not_run(status) -> bool:
    """Whether ``status`` is a stock check that did not run: one of the codes above, or any other stock status at level ``unknown``
    (the library's own not_run verdicts, e.g. ``NoValue``, record as unknown and nothing else does). A driver's unknown is not one.

    WHY (2026-11-04): one predicate for every place that rolls a level up or lists a check, so a new not-run code is added to
    ``NOT_RUN_CODES`` once and the tree, the summary, the health bars and the pages agree; it is never "OK", only "says nothing"."""

    return status.source == SOURCE and (status.code in NOT_RUN_CODES or status.level == UNKNOWN)


__all__ = ["SOURCE", "UNKNOWN", "NO_STATE", "NOT_WIRED", "NOT_MEASURED", "NOT_STREAMING", "NO_DECODER", "NOT_ENOUGH_HISTORY", "NUMPY_MISSING_CODE", "DIAGNOSTIC_RAISED",
           "NOT_RUN_CODES", "is_not_run"]
