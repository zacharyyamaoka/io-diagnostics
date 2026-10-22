# IO diagnostics (babble round 1, prototypes only)

Plug a sensor, actuator or bus adapter into the rack PC and see it in the top bar: what it is, its ids,
versions, comms and health, before any robot code runs, without disturbing an experiment. Each driver
package (moteus_servo, intel_rgbd, obsbot_ptz_camera) brings its own `diagnostics.py`; IO diagnostics is
the thin host. Five directions differ in surface, in how the host finds a driver, and in health policy.

The comparison: `reports/io-diagnostics-babble-2026-10-21.html` (rebuild: `docs/build_gallery.py`).

| Path | What |
|---|---|
| `docs/criteria.json` | Frozen criteria (commit 0a138b1) + amendments A1, A2 |
| `io_facts/` | Passive layer: sysfs USB, by-id, `/proc/locks` (never flock), kernel-log coalescer, the active-check gate |
| `contract/` | `DeviceDiagnostics` protocol, host-owned gate, confirmed+gated reset, generic views, menus, AppIndicator renderer |
| `drivers_proposed/` | The `diagnostics.py` each driver would gain (copies; bam_ws untouched) + per-direction binding files |
| `prototypes/v1..v5` | Quick Settings extension · Plug Journal tray · Hub row · Registry contract · Station board |
| `tools/sandbox.sh` | Throwaway GNOME Shell 46 on Xvfb (scratch HOME, keyfile settings, private D-Bus) for real renders |

Nothing here opens a CAN adapter or camera: every device-touching call is gated and, this round, disabled.

```bash
uv run --project /home/bam/io-diagnostics --group dev pytest /home/bam/io-diagnostics/tests -q
```

```bash
/usr/bin/python3 /home/bam/io-diagnostics/io_facts/snapshot.py
```

```bash
bash /home/bam/io-diagnostics/tools/sandbox.sh /home/bam/io-diagnostics/tools/shoot_native.py /tmp io-diagnostics@bamrobotics
```

Measured traps worth keeping:
- `hold_bus_lock` takes the rack lock with `LOCK_NB` and refuses if held, so a registry that test-acquires the lock can make an experiment refuse to start: read `/proc/locks` instead.
- USB descriptor serial != RealSense SDK serial (219323070276 vs 2186222…); an alias needs one gated read.
- ubuntu-appindicators (GNOME 46) shows an AppIndicator label only if it is set after registration, and unescapes DBusMenu labels with a non-global `replace(/_([^_])/, '$1')`: prefix a mnemonic `_` or underscores render doubled.
- GNOME 46's `org.freedesktop.Notifications` is a separate D-Bus-activated gjs service; a private bus must start it by hand.
