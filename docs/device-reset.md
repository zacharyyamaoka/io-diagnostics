# Reset… and automatic recovery: every device's way back (2026-11-14)

Zach, 2026-11-14, after fdcanusb 0607623A stopped answering at 08:57 while staying enumerated (no kernel USB event; its own
`can status` hung) and the arm sat dead for 7 h until he replugged it by hand at 15:54: "when you deploy a robot you cannot be
there to unplug stuff manually".

This builds on `docs/usb-reset.md` (2026-10-24, the manual **Reset USB port**, `reset_port`, which stays as it is: the port
level) and `docs/actions-vocabulary-v0.1.md`. New: a **device-level** standard action, `reset` · **Reset…**, that runs the
device's own reset first and then the USB rungs, one rung at a time until it answers; and the same ladder **unattended**, for a
deployed robot, with a page to a human as its last rung.

Code: `io_diagnostics/recovery/` (stdlib only). Profiles, one per device class, are frozen dataclasses in
`io_diagnostics/recovery/profiles/*.py`; the tables in the report are generated from them
(`python -m io_diagnostics.recovery profiles --json`).

## 1. The ladder

Detect → reopen → the device's own reset → USB port reset → kernel deauthorize / reauthorize → port power cycle → page a
human. Each profile takes the rungs that apply to it, in that order; every rung carries a plain-words `why` (why it is at
that place) and an `expect` (what happens when it works and what is lost meanwhile), a `timeout_s` for the reset itself and a
`settle_s` to wait for the device to be back on USB before it is probed again.

| Device class (profile) | Probe (is it healthy?) | Own reset | USB rungs | Unattended? |
|---|---|---|---|---|
| mjbots fdcanusb (`fdcanusb`) | `tty_reply`: a fresh open answers `can status` within 1 s | `fdcanusb-reset --serial S --yes` (Sim to Real's `bam_rack_twin.hardware.fdcanusb_reset`; it IS a USBDEVFS_RESET, so the plain usbreset rung is skipped after it) | usbreset, authorized, power cycle | yes |
| Intel RealSense (`realsense`) | `present` (+ its video nodes) | the intel_rgbd driver's Reboot (librealsense `hardware_reset()`) | usbreset, authorized, power cycle | yes |
| OBSBOT PTZ (`obsbot_ptz`) | `v4l2_querycap` on a fresh open (shared with a running stream) | none exposed: reopen, then USB | usbreset, authorized, power cycle | yes |
| moteus board (`moteus_board`) | the moteus_servo driver's Check communication lists its id | the moteus_servo driver's Reboot: `d reset` | none (not a USB device: its adapter's ladder) | **no**: person only |
| any other USB device (`usb_device`) | `present` | none | usbreset, authorized, power cycle | yes |

**moteus `d reset`, verified rather than assumed**: the firmware's diagnostic command `d reset` calls `NVIC_SystemReset()` at
once (moteus-src 2b21a16f, `fw/board_debug.cc:971`); unlike `d flash` it does not call `MoteusEnsureOff()` first. The register
protocol has no reboot register, and moteus_tool 1.1.1 has no restart flag (it sends `d reset` only at the end of
`--restore-config`). After it the board knows its position only within one revolution, so a geared joint's output reference
is gone. Hence person-only: a loaded joint drops while it reboots, and the homed map is void (Zach 2026-11-13: home once per
power-up). The driver also sends it only to a board that answers a query, so a *silent* board's ladder ends in a page: "check
its CAN cable and 24 V; power-cycle its supply".

**Power cycle** uses the kernel's own per-port switch, `<hub>:1.0/<hub>-port<N>/disable` (Linux 6.0+; the interface uhubctl
itself uses there), on the port and its USB 3 `peer`, off 2 s, back on in a `finally`. It runs only when the hub's descriptor
says **per-port power switching**; on a ganged or non-switching hub `disable` is only a logical disconnect (which
`authorized` already did), so the rung is skipped with that reason. uhubctl is not needed.

## 2. Two modes, one engine

**Person** (`act --action reset`, the Reset… button, a RUN like every action): a dry run shows the plan, rung by rung, which
will run and why each other one will not; one confirmation token covers the device, the runnable rungs and who has it open;
a second confirmation when something has it open (as `reset_port`). The reopen rung is skipped (a person pressing Reset has
already restarted their app); person-only rungs run; it stops as soon as the device answers, else it ends saying what to do.

**Unattended** (`python -m io_diagnostics.recovery recover`): for a deployed robot, by the device's owner or a supervisor.

- **The owner calls it when its own reopen failed**, naming itself: `recover --device usb:0483:5740:0607623A --owner-pid $$
  --detected "no reply to 5 exchanges" --tried reopen[,native] [--step]`. The owner must be the calling process or one that
  started it (`OwnerNotCaller` otherwise), so no third party can name the owner's pid to slip past the holder check. Its own
  open handle, and its children's, are not "someone else". `--tried` lists rungs it already ran this outage (arm-hold's own
  reopen and its own fdcanusb-reset), so they are not run twice. `--step` runs one rung and returns 3: the owner reopens,
  and calls again if it still does not answer.
- **A supervisor** (no owner) may reset only what nobody has open; its probe asks the device itself.
- **Each rung at most once per incident.** An incident opens at the first failure and closes once the device has answered
  for `quiet_s` (600 s); a failure inside that window continues the same incident, its used rungs stay used. More than
  `max_incidents_per_day` (3) incidents in 24 h: the ladder pages only ("it needs a look, not another reset").
- **The rung is marked tried before it runs**, so a ladder killed mid-rung never repeats it.
- **A rung whose kernel write did not finish** (a deauthorize that hangs) stops the ladder and pages: nothing is stacked on a
  device the kernel is still busy with.
- **Person-only rungs never run** (the moteus reboot); they are recorded as skipped, with why.

### Gates, in order (each refusal exits 2: nothing was reset)

| Gate | Refusal |
|---|---|
| `~/.config/io-diagnostics/recovery.off` exists (unattended only) | `RecoveryOff` |
| a hold: `recover hold --device K --minutes M --reason R` (a firmware flash, an experiment) | `HoldDeclared` |
| the declared owner is not running, or is not this process or an ancestor | `OwnerGone`, `OwnerNotCaller` |
| this host replays a recording | `NotLive` |
| another ladder runs on this device (flock) | `LadderRunning` |
| person: the token does not match the plan as it is now | `ConfirmMismatch` |
| **anyone other than the owner has the device (or anything below it) open** | person: `HoldersPresent` until confirmed again; unattended: `HeldByOther` |
| **an owner lock is held by someone other than the owner** (the fdcanusb's is the rack bus lock) | `OwnerLockHeld` |
| unattended: the probe could not ask without disturbing its holder and the owner said nothing | `CannotTell` |

A holder or an owner lock that blocks every rung for 120 s of an open incident is paged rather than waited out.

### Exit codes

0 it answers (healthy, or cured by a rung) · 2 refused, nothing was reset · 3 a rung ran and it is back on USB, its owner judges
whether it answers · 4 a human is needed (paged; again: "already paged") · 1 the ladder itself failed.

## 3. Evidence and the page

Everything is under `~/.local/state/io-diagnostics/recovery/` (`$IO_DIAGNOSTICS_STATE_DIR`):

- `ledger.jsonl`, one line per attempt (`recovery-attempt/1`): incident, rung, outcome (`cured`, `not_cured`,
  `back_not_judged`, `failed`, `timeout`, `refused`, `skipped`, `paged`), the mechanism's words, the exact command, exit code,
  its output, seconds, how long it took to be back on USB, the probe's verdict, **before and after snapshots** (present, bus and
  device number, speed, `authorized`, configuration, each interface's driver and /dev nodes, by-id links, the port's `disable`
  and `state`, who had it open) and the **kernel's own lines** about that port since the rung started.
- `alarms.jsonl` (`recovery-alarm/1`): what a human is asked to do, once per incident. The page also emits a
  `RecoveryNeedsHuman` event and runs the deployment's page command when one is set (`--page-command`, or
  `$IO_DIAGNOSTICS_PAGE_COMMAND`, `{device}` and `{text}` filled in).
- `incidents/<device>.json`, `history/<device>.jsonl`, `holds/`, `locks/`.

`python -m io_diagnostics.recovery ledger` prints the attempts.

## 4. Permissions: no root at runtime

One installer, run once with sudo: `tools/install_device_reset_access.sh`. It copies the committed rules file
`tools/udev/70-robotdx-device-reset.rules` (generated by `python -m io_diagnostics.recovery udev-rules` from the profiles, and
a test keeps them equal) and reads each hub's power switching from its descriptor as root (stock `lsusb -v`) into
`/var/lib/robotdx/hub-power-switching.txt`, which the ladder reads as any user. RobotDx itself never runs sudo.

For the rig's classes only (fdcanusb, RealSense, OBSBOT; never every USB device, so a keyboard's node stays root's), each rule
grants plugdev: the device's usbfs node (0664, except RealSense, whose vendor rule already opens it), its sysfs `authorized`
switch, and the `disable` switch of the hub port it is plugged into and of that port's USB 3 peer. The two sysfs grants are
`RUN+=` chgrp / chmod (a sysfs attribute is not a device node, so `GROUP=` / `MODE=` cannot reach it).

`python -m io_diagnostics.recovery check` reports, for every device here and every rung, whether it can run now and in plain
words why not, and what would make it possible (the installer, a hub with per-port power switching, a driver plugin).

## 5. Proof

`tests/test_recovery.py` runs the ladder against a fake `/sys/bus/usb/devices`, a fake `/dev` (a pty stands in for the
fdcanusb's tty), a fake `/proc` with open files and locks, fake tools under the temp dir, and fake driver plugins. The test
tripwire (`tests/test_host_support.py`) refuses the real `fdcanusb-reset`, `usbreset` and `uhubctl`, a recovery run with no
fake sysfs named, and (in process) any sysfs write or device open outside the temp dir. `tests/mutate_recovery.py` breaks each
refusal path in a copy of the tree and a test must fail.

```bash
uv run --project /home/bam/io-diagnostics --group dev pytest /home/bam/io-diagnostics/tests/test_recovery.py
```
