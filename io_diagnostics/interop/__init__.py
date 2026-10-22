"""Interop: our v0 records in STOCK diagnostics viewers (Lichtblick, Foxglove, ``ros2 topic echo``).

Zach, 2026-10-22: "a simple functional adapter that strips the fields we added and then it's just a stock ROS
DiagnosticStatus". That adapter is ``contract.record.to_diagnostic_status`` / ``from_diagnostic_status``; this
package only wraps it:

- ``stock``: what a stock viewer receives: the latest record per check, judged at publish time (an overdue
  record goes out as STALE, never as its last green), as a ``diagnostic_msgs/msg/DiagnosticArray`` dict and as
  ROS 2 CDR bytes with the ``ros2msg`` schema text.
- ``foxglove_bridge``: a foxglove-sdk WebSocket server publishing that array on ``/diagnostics``.
- ``lichtblick``: the Lichtblick layout (Diagnostics Summary + Detail) and a one-command demo (bridge + the
  open-source Lichtblick web image).
- ``rack_fixture``: today's rack as v0 records (CAN 1/14/15 with CAN 15's fault 33, a stale board, two D405s).
- ``ros2_check/``: the same records published and echoed by real ROS 2 (Humble, docker ``ros2_gazebo_aruco:v1``).

The modules import ``contract.record`` and ``io_diagnostics.interop``, so the repo root must be importable: tests
put it on ``sys.path``; the runnable scripts do it themselves (the project has no build system yet).
"""
