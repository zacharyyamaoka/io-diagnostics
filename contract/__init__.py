"""The small diagnostics interface a driver package exposes (amendment A2).

A driver (moteus_servo, intel_rgbd, obsbot_ptz_camera, ...) adds ONE module, ``diagnostics.py``,
that implements ``DeviceDiagnostics``. IO diagnostics is only the host: it finds those modules
(each prototype direction finds them a different way), matches plugged-in USB devices to them,
puts every device-touching call behind the shared gate, and renders the results.
"""
