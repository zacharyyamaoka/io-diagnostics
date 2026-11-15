# Reset USB port: the host's own utility (2026-10-24)

Zach, Daily Note 2026-10-22, listing what RobotDx should be: "Ok we can also incoperate like standard OS things in here as
well like to manually reset a usb port etc…". Until now RobotDx only *detected* a reset (the kernel log's `UsbReset`,
`io_facts/kernel_log.py`). This is the button.

For the device-level **Reset…** (`reset`: the device's own reset first, then these USB rungs, one at a time until it answers, and
the same ladder unattended on a deployed robot), see `docs/device-reset.md`.

`reset_port` · **Reset USB port** · danger `interrupts` (docs/actions-vocabulary-v0.1.md). It is offered on **every USB device
row and every USB port row**, whatever driver owns the device. A port row resets the device in it; a hub resets everything
plugged into it too, and the confirmation says so.

## 1. The mechanism, and why (off the shelf first)

Three stock mechanisms exist. The first one this user may use runs; the dry run shows all three, which one it picked, and why
each other one is out.

| # | Mechanism | What it does | Needs | Why this place in the order |
|---|---|---|---|---|
| 1 | `usbreset BBB/DDD` (usbutils) | `USBDEVFS_RESET` ioctl on ONE device's usbfs node: the kernel resets that device and rebinds its drivers | write access to `/dev/bus/usb/BBB/DDD` | **Preferred.** The stock tool, already installed here (`/usr/bin/usbreset`, usbutils 1:017-3build1), touches one device and nothing else on the hub, needs no new code, and a udev rule is enough to run it without root. Addressed by bus/device number, never by vid:pid, so two identical fdcanusbs are never mixed up. |
| 2 | `echo 0 > /sys/bus/usb/devices/<port>/authorized`, then `echo 1` | the kernel deauthorizes the device (drivers unbind, it is logically disconnected) and authorizes it again | root: a sysfs attribute cannot be given to a group by a udev `MODE` | Needs no tool, so it is the fallback when `usbreset` is missing; but root only here (`-rw-r--r-- root root`). The switch is written back to `1` in a `finally`, so a Stop between the two writes never leaves the device off. |
| 3 | `uhubctl -l <hub> -p <port> -a cycle` | really cuts the port's power and restores it | `uhubctl` installed, access to the hub's node, and a hub with per-port power switching | The only one that power-cycles, but most hubs (and this PC's root hubs) cannot switch ports, it is not installed here, and it drops everything on that port's hub path. Last resort. |

### What this machine has (checked 2026-10-24, read-only)

- `usbreset`: installed, `/usr/bin/usbreset`; `uhubctl`: **not installed**. Nothing was installed for this feature.
- USB nodes: most are `crw-rw-r-- root root` (the fdcanusbs `/dev/bus/usb/001/007`, `001/010`, `001/011`; the webcam and the
  keyboard); the RealSense D435 and the D405s are `root plugdev 0666` because librealsense's own rule
  (`/lib/udev/rules.d/60-librealsense2-udev-rules.rules`) already opens them. So **the cameras can be reset today with no
  change; the fdcanusbs cannot**, until the rule below is installed.
- `authorized`: `-rw-r--r-- root root`. `sudo -n` asks for a password. The user is in `plugdev`.

## 2. The privilege, and the rule Zach would install

A user cannot reset a device whose node is `root:root 0664`. When no mechanism is allowed, the run is refused **before anything
happens**, the button is blocked with the reason, and the dry run's sheet shows, under **To allow it**, the exact line for THAT
device and the commands to apply it. RobotDx never runs `sudo` and never edits a rule itself.

`/etc/udev/rules.d/70-robotdx-usb-reset.rules`, the rack's devices (checked with `udevadm verify`):

```
# RobotDx "Reset USB port" (docs/usb-reset.md): let the plugdev group open these devices' USB nodes, so `usbreset`
# works without root. One line per vendor:product; add a line for any other device you want to reset from RobotDx.
#
# Install:
#   sudo cp 70-robotdx-usb-reset.rules /etc/udev/rules.d/
#   sudo udevadm control --reload
#   sudo udevadm trigger --subsystem-match=usb --action=change

# mjbots fdcanusb (the rack's CAN adapters)
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0483", ATTR{idProduct}=="5740", GROUP="plugdev", MODE="0664"
# Terminus 4-port hub on the rack bench
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="1a40", ATTR{idProduct}=="0101", GROUP="plugdev", MODE="0664"
# Genesys Logic hub
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="05e3", ATTR{idProduct}=="0610", GROUP="plugdev", MODE="0664"
```

`udevadm trigger --subsystem-match=usb --action=change` re-applies the permissions to what is already plugged in; no replug.
**One vendor:product per line on purpose.** The one-line alternative that covers every device
(`SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", GROUP="plugdev", MODE="0664"`) would also let any process of the `plugdev`
group open the raw node of a keyboard, so it is not what the page suggests.

Mechanism 2 works the same way for the person who runs RobotDx as root; it has no group rule.

## 3. The flow (a run, like any action)

```
click "Reset USB port"  ->  dry run: python -m io_diagnostics.usb_reset act --device usb:1-3 --action reset_port --dry-run
                        ->  the sheet: exact command, the three mechanisms, preconditions, holders, the fix when it cannot
                        ->  Continue…  ->  confirm modal (names the device; warns that whatever holds it loses it;
                                           when something holds it, a box to tick: "I understand tview (pid 4242) will lose …")
                        ->  a RUN (docs/runs-v1.md): live terminal line, RunStarted, the reset, RunEnded
                        ->  the kernel's own lines follow in Activity (unplug / plug, or a bare reset line in Raw)
                        ->  every driver job on that device is probed again (docs/probe-on-connect.md)
```

`python -m io_diagnostics.usb_reset` is stdlib only and speaks the plugin boundary (`describe`, `act` with `--confirm`,
`--dry-run`, `IO_DIAGNOSTICS_RECORD_FD`), so the host runs it with the same code as a driver's act
(`io_diagnostics/host_utilities.py` declares it as a `PluginSpec` the host never loads from a file).

### Gates, in the order they apply

| # | Gate | Where | Refusal |
|---|---|---|---|
| 1 | a driver did not opt the device out (and none below a hub) | host (`Host._offers`) | the action is not on the row; `act` answers `ActionNotRegistered` |
| 2 | something is plugged in at the port | host (`_plan_utility`) and verb | `NoDevice` |
| 3 | **no RobotDx run is active on the device**, on anything below it, or on a hub above it (compared by tree path, dry run too) | host | `RunActive`, naming the run |
| 4 | only a person runs it; with the dry run's token | host, server | `NotInteractive`, `ConfirmRequired`, `NoDryRun` / `TokenMismatch` |
| 5 | this host is live: a recording or a fake machine has no real port | host and verb | `NotLive` |
| 6 | the token still matches the device AND the holders the dry run listed | verb | `ConfirmMismatch` |
| 7 | **this user may use a mechanism** | verb (the host shows the same check as a precondition) | `NoPrivilege`, with the rule |
| 8 | **the holder check: nothing else has it open, else the person confirms again** | verb | `HoldersPresent`, listing them; passes with `--confirm-holders` |

A refusal ends the run `failed` (exit 2) with the reason in the terminal and in the run's records; nothing ran.

### Holders

The verb lists every process (other than itself) that has one of the port's device nodes open: the usbfs node
`/dev/bus/usb/BBB/DDD`, and the `tty`, `video4linux`, `hidraw`, `usbmisc` nodes of its interfaces, for the device and
everything below it (found in sysfs, read from `/proc/<pid>/fd`). It prints `tview (pid 4242) has /dev/ttyACM0`. RobotDx's
own `watch` / `follow` processes are not holders: the host stops its watches of these devices before the reset runs
(`Host._utility_hold`), and the verb ignores the host's passive sessions. A process of another user cannot be looked into;
the answer says how many it could not see.

The **second confirmation** is the ticked box in the confirm modal (the page sends `confirm_holders: true`, which becomes
`--confirm-holders`; docs/api-v1.md). The confirmation token is bound to the holders the dry run listed, so a holder that
appears after the dry run voids the first confirmation instead of riding on it.

### After the reset

- **RunStarted / RunEnded** are Activity events of type `run` (folded into one `Run` row), like every run.
- **The kernel's lines follow.** `usbreset` usually leaves one `reset full-speed USB device number N` line (the device keeps
  its number); `authorized` and a power cycle leave a disconnect and a new enumeration, which Activity shows as Unplugged /
  Plugged / ReEnumerated. The verb waits up to `IO_DIAGNOSTICS_RESET_WAIT_S` (15 s) for the port to be back and prints
  `back after 1.2 s as device 12`, or `reset in place after 0.4 s (device number 11 unchanged)`.
- **The probe runs again, whichever of the two happened.** The host already re-probes when a device number changes between
  two refreshes; a reset in place changes nothing visible, so when a `reset_port` run ends the host marks every driver job on
  devices in that port as not probed and reads the OS side again at once (`Host._after_reset`), even when the reset failed.

## 4. Opting out

A driver that has a device which must not be reset (an experiment is running on it, a firmware flash is in flight) says so in
`describe`:

```json
{"name": "moteus_servo", "schema": "diagnostics-record/0", "opt_out": ["reset_port"], "actions": []}
```

The action is then absent from that device's row and its port row, and from the rows of every hub above it (a hub reset takes
the device down). A driver that registers `reset_port` itself, with the standard title, replaces the built-in on its entities.
The host reads `describe` once per plugin (`Host.describes`), so a driver changed in place is picked up by its source check.

## 5. Proof and the tests

`tests/test_usb_reset.py` runs everything against a fake `/sys/bus/usb/devices`, a fake `/dev`, a fake `/proc` with open files,
and a fake `usbreset` that appends to a log and writes the kernel lines a reset leaves. Nothing touches a real device: the
environment variables `IO_DIAGNOSTICS_USB_SYSFS`, `IO_DIAGNOSTICS_DEV_ROOT`, `IO_DIAGNOSTICS_PROC`, `IO_DIAGNOSTICS_USBRESET`
and `IO_DIAGNOSTICS_UHUBCTL` repoint the module, and the test tripwire (`tests/test_host_support.py`) refuses the real
`usbreset` / `uhubctl` and any real reset without the fake tree named. `tests/mutate_usb_reset.py` breaks each gate in a copy of
the tree and a test must fail.

```bash
uv run --project /home/bam/io-diagnostics --group dev pytest /home/bam/io-diagnostics/tests/test_usb_reset.py
```
