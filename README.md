# IO diagnostics

Plug a sensor, actuator or bus adapter into the rack PC and see it: which port, what it is, its ids and versions,
whether an update is out, its read-only config, comms health, and the few utility buttons its driver registers.
One small host process builds ONE tree from two sources and serves it as a dashboard plus a top-bar chip:

- **The OS skeleton** (passive, always there): host → USB bus → port → hub → device, PCI/PCIe functions (frame
  grabbers and capture cards flagged), network interfaces (an EtherCAT master is detected, the segment is not
  scanned), serial ttys and video4linux nodes. Sysfs, `/dev/*/by-id`, the udev database and the kernel log only.
- **Driver plugins** (the driver's own code): `moteus_servo` hangs fdcanusb → CAN bus → boards → driver/motor
  under its adapter; `intel_rgbd` hangs a D405's colour/depth streams and its identity, firmware and USB link checks
  under the camera. Each runs in its driver's own uv environment as a subprocess.

Records are the v0 contract (`contract/record.py`, `docs/diagnostics-record-v0.md`): ROS `DiagnosticStatus` kept
whole plus path, code, two clocks and identity. The dashboard is ours; any stock viewer (Lichtblick, Foxglove,
`ros2 topic echo /diagnostics`) shows the same records through one adapter that strips the additions.

**Safe by construction:** the host is read-only and GET-only; actions are shown with their gates, never executed
(v1). Both shipped driver plugins are `enabled = false`, because their default `diagnose` talks to the hardware
(behind a port-holder check and the rack bus lock).

## Run it

Fixture demo, the recorded 18:25 "bus busy" frame (CAN boards turn stale about 11 s in), on http://127.0.0.1:8812/:

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics serve --fixture F2 --offset 330
```

Live, passive: this machine's real skeleton, no driver plugin runs:

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics serve
```

Live, with the real moteus_servo and intel_rgbd plugins in `--passive` mode (sysfs, by-id, `/proc` holders, the rack
inventory files: no CAN adapter or camera is opened; `passive_guard.py` refuses anything else):

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics serve --plugins /home/bam/io-diagnostics/tests/fixtures/plugins-live-passive
```

Hardware this PC does not have (a PCIe frame grabber with no driver, a capture card trained x1 of x4, two EtherCAT
NICs, a USB-serial adapter), from a fake machine's sysfs through the same io_facts sources:

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics serve --machine /home/bam/io-diagnostics/tests/data/sysfs-rack-fixture.json
```

The top-bar chip ("IO 3 ⚠", with a Recent list; it opens the dashboard). It re-executes itself under
`/usr/bin/python3`, which has `gi`:

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics tray
```

The installed app, RobotDiagnostics, runs in Stable / Preview release channels (`stable-preview.json`, the shared
stable_preview_switcher runtime): Stable on :8820 with the tray, Preview (the main checkout, live) on :8821, agent
lanes with `sps dev`. The dock icon opens Stable; its right-click opens Preview, makes Preview Stable, rolls Stable
back. Why each setting is what it is: `docs/release-channels.md`.

```bash
sps --root /home/bam/io-diagnostics status
```

One snapshot as JSON (state, records, activity, plugins), for scripts and tests:

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics once --json
```

The drivers' ACTIVE checks (CAN query frames, `conf get`, `can status`; one RealSense `camera_info` read) run only
when you say so for that run: `io-diagnostics serve --enable moteus_servo --enable intel_rgbd`. Close
realsense-viewer and tview first, or the checks report `BusyHeldBy` / `PortBusy` and touch nothing.

Fixture frames: `F1` (18:11, plug: 28B3A25D's bus error-passive, CAN 15 fault 33), `F2` (18:25, tview holds
272813EF), `F3` (19:02, after a restart). `--port N` picks another port; the default is 8812.

## Add a device: one executable + one toml

A plugin is any executable that answers three verbs with JSON lines on stdout. The host runs
`<command> diagnose --device <skeleton id>` every `period_s` for each matching device, with the device's tree path
and skeleton dict in the environment, and parses what comes back. It never imports the driver, so the driver's
dependencies, language and crashes stay its own. Full rules: `plugins.d/README.md`; the contract:
`docs/diagnostics-record-v0.md`.

`examples/acme_gripper/acme_gripper.py` (stdlib only; a real plugin reads its device where this one makes values up):

```python
"""The smallest IO diagnostics plugin: a made-up USB gripper (1234:5678) with one part, as JSON lines.

    <python> acme_gripper.py describe
    <python> acme_gripper.py diagnose --device usb:1-4

A real plugin reads its device here (inside its own driver's environment); this one only shows the shapes.
"""

import json
import os
import sys
import time

SCHEMA = "diagnostics-record/0"


def emit(**record):
    print(json.dumps({"schema": SCHEMA, **record}))


if sys.argv[1] == "describe":  # what it claims, and the buttons it registers (none here)
    print(json.dumps({"name": "acme_gripper", "version": "1.0", "schema": SCHEMA,
                      "matches": ["usb:1234:5678"], "actions": []}))
elif sys.argv[1] == "diagnose":  # one run for one device; paths are relative to the device's own row ("")
    device = json.loads(os.environ.get("IO_DIAGNOSTICS_DEVICE", "{}"))  # the host's skeleton dict for it
    now = time.time()
    emit(record="entity", path="", name=f"Acme gripper {device.get('serial') or ''}".strip(), kind="device",
         identity={"manufacturer": "Acme", "model": "Gripper G1", "sw_version": "2.3.0"})
    emit(record="entity", path="jaw", name="jaw", kind="part", part_of="")
    emit(record="status", path="", name="gripper: comms", level="ok", message="answers in 2 ms",
         stamp=now, expected_period_s=30)
    emit(record="status", path="jaw", name="jaw: temperature", level="warn", code="JawHot",
         message="jaw motor 71 °C (warn above 70 °C)", stamp=now, expected_period_s=30,
         measurements=[{"key": "temperature", "value": 71.0, "unit": "Cel", "warn_high": 70.0}])
```

`examples/acme_gripper/acme_gripper.toml`, dropped into `plugins.d/`:

```toml
name = "acme_gripper"
command = ["{python}", "{here}/acme_gripper.py"]
timeout_s = 10
period_s = 30
matches = ["usb:1234:5678"]
```

What the host does for you: the device's row (port, VID:PID, serial, link speed, plug history) already exists from
the OS; the plugin's entities hang under it; the jaw's WARN shows as the jaw's own shape and as a hint on the
gripper (black boxes self-report: the gripper says OK, the host never overrides it); a check not re-sent within
`max(3 × expected_period_s, 5 s)` turns STALE on the host's clock; a crash, a hang past `timeout_s`, or a malformed
line becomes a host check on the device row, never a dead dashboard. `tests/test_integration.py` runs this example
through the host and fails if this README drifts from the files.

For a driver with dependencies, `command` runs it in its own environment, e.g.
`["uv", "run", "--project", "/path/to/driver", "python", "-m", "my_driver.diagnostics"]` (see `plugins.d/*.toml`).
Options of the verb (a `--passive` flag) go in `diagnose_args`.

## Interop proof: our records in stock viewers

`io_diagnostics/interop/` publishes the host's records as `diagnostic_msgs/msg/DiagnosticArray` (ROS 2 CDR,
`ros2msg` schema) through foxglove-sdk, via `contract.record.to_diagnostic_status` (the one adapter). Proven:

- **ROS 2 Humble** (docker `ros2_gazebo_aruco:v1`, no network): rclpy decodes our bytes into the intended array,
  its own encoding matches ours byte for byte outside alignment padding, the schema matches the installed `.msg`
  files, a message crosses DDS unchanged, and `from_diagnostic_status` / `to_diagnostic_status` round-trip real
  rclpy messages. Evidence: `reports/media/interop/ros2/`.

```bash
uv run --project /home/bam/io-diagnostics --group interop python /home/bam/io-diagnostics/io_diagnostics/interop/ros2_check/run.py
```

- **Lichtblick** (open source, local docker): its stock Diagnostics Summary + Detail panels show every row,
  levels coloured, stale rows greyed. Evidence: `reports/media/interop/lichtblick/`. This starts the bridge and
  Lichtblick on http://127.0.0.1:8766/ (Ctrl-C stops both):

```bash
uv run --project /home/bam/io-diagnostics --group interop python /home/bam/io-diagnostics/io_diagnostics/interop/lichtblick.py
```

- **The real host → the bridge**: `tests/test_integration.py` serves the F1 frame from the real host and reads it
  through the bridge's `--from-host` source into stock CDR and back.

## Tests

```bash
uv run --project /home/bam/io-diagnostics --group dev --group interop pytest /home/bam/io-diagnostics/tests -q
```

```bash
/usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js
```

```bash
/usr/bin/node /home/bam/io-diagnostics/tools/shoot_dashboard.js
```

The driver plugins' own tests live in bam_ws. intel_rgbd's package suite queries the SDK at import, so on a machine
with RealSense cameras run it through the device-less shadow:

```bash
uv run --project /home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/core/mdp/agent/sensors/intel_rgbd --extra test python /home/bam/io-diagnostics/tools/safe_pytest_intel_rgbd.py /home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/core/mdp/agent/sensors/intel_rgbd/tests -q
```

```bash
uv run --project /home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/core/mdp/agent/actuators/moteus_servo --extra test pytest /home/bam/bam_ws/.claude/worktrees/traj-sim2real-rack/src/core/mdp/agent/actuators/moteus_servo/tests/test_diagnostics_plugin.py -q
```

The bam_ws paths are the worktree of branch `claude/traj-tracking-sim-to-real-a3da60` until it is merged; then
they become `/home/bam/bam_ws/src/core/mdp/agent/...` (also in `plugins.d/*.toml`).

## Layout

| Path | What |
|---|---|
| `io_diagnostics/` | The host: plugin registry + runner, tree, faults, activity, server (`docs/api-v1.md`), tray, CLI |
| `stable-preview.json` | The Stable / Preview channels (data for the stable_preview_switcher runtime; `docs/release-channels.md`) |
| `io_diagnostics/web/` | The dashboard page; `tree/v1..v5` are the five tree proposals (bottom-right switch, v1 default) |
| `io_diagnostics/interop/` | Stock adapter, foxglove bridge, Lichtblick + ROS 2 checks |
| `io_facts/` | Passive OS layer: `skeleton.py` (one tree from usb / pci / netdev / tty / v4l), kernel log, holders, locks |
| `contract/record.py` | The record contract, diagnostics-record/0 (frozen at c9784d0) |
| `plugins.d/` | Plugin declarations (both shipped disabled) and the plugin rules |
| `examples/acme_gripper/` | The smallest plugin |
| `tests/fixtures/` | V6's three recorded frames (fixture plugin), the misbehaving test plugins, the live-passive declarations |
| `docs/` | Build plan v1, converged design, record spec, API v1, research |
| `prototypes/`, `drivers_proposed/` | Babble round 1 and V6 (history; the design they converged on is above) |
| `tools/` | Headless proof shots, the RealSense test shadow, the GNOME Shell sandbox for real tray renders |

Measured traps worth keeping:
- `hold_bus_lock` takes the rack lock with `LOCK_NB` and refuses if held, so a check that test-acquires the lock can
  make an experiment refuse to start: read `/proc/locks` instead.
- USB descriptor serial != RealSense SDK serial (219323070276 vs 2186222…): `hardware_id` uses the USB one.
- moteus 1.0.0 never bounds a reply from a real fdcanusb, and a second transport in one event loop is never read:
  one guarded transport per check, one id per transaction (lane D, `moteus_servo/diagnostics.py`).
- ubuntu-appindicators (GNOME 46) shows an AppIndicator label only if it is set after registration, and unescapes
  DBusMenu labels with a non-global `replace(/_([^_])/, '$1')`: prefix a mnemonic `_` or underscores render doubled.
- GNOME 46's `org.freedesktop.Notifications` is a separate D-Bus-activated gjs service; a private bus must start it
  by hand (`tools/sandbox.sh`).
