# plugins.d: driver diagnostics plugins

Every `*.toml` here declares one plugin. The host (`io_diagnostics/plugins.py`, `io_diagnostics/runner.py`) loads
them at start, matches them to OS skeleton devices, and runs each matched (plugin, device) pair every `period_s`.
A broken file is skipped and named in `GET /api/v1/plugins` → `problems`; it never stops the host. Nothing in this
directory is enabled by default: a file here is a decision to let that driver's code run.

## The declaration

```toml
name = "moteus_servo"                          # a word; also the owner shown on its rows
description = "moteus boards behind an fdcanusb"
command = ["uv", "run", "--project", "/home/bam/bam_ws/src/core/mdp/agent/actuators/moteus_servo",
           "python", "-m", "moteus_servo.diagnostics"]          # argv, never a shell string
cwd = "/home/bam/bam_ws"                       # optional; default: this file's directory
timeout_s = 20                                 # one run's wall-clock limit; past it the whole process group is killed
period_s = 30                                  # how often per matched device
matches = ["usb:0483:5740"]                    # see Matches
enabled = true                                 # optional, default true; `io-diagnostics ... --enable NAME` overrides false
env = { MOTEUS_DIAGNOSTICS_QUIET = "1" }       # optional extra environment
diagnose_args = ["--passive"]                  # optional: appended after `diagnose --device <id>` (options of the verb)
```

The two shipped declarations (`moteus_servo.toml`, `intel_rgbd.toml`) run the real drivers but say `enabled = false`:
their default `diagnose` talks to the hardware (behind the holder check and the rack bus lock). Turn one on for a run
with `--enable moteus_servo`, never by editing the file. `tests/fixtures/plugins-live-passive/` declares the same two
drivers with `diagnose_args = ["--passive"]` behind `passive_guard.py` (refuses any diagnose without `--passive`,
and any `act`): the real driver code on the real skeleton, with no adapter or camera opened.

`{python}` (the host's interpreter), `{root}` (the io-diagnostics checkout) and `{here}` (this file's directory)
are expanded in `command`, `cwd` and `env`. A relative executable with a `/` resolves against `cwd`. `timeout` and
`period` (the build plan's spelling) are accepted for `timeout_s` / `period_s`.

**No `matches` here?** Then the host asks the plugin once (`<command> describe`, which must be passive, bounded by
`timeout_s`) and uses the `matches` it returns, as strings (`"usb:8086:0b*"`) or as `{"bus": "usb", "vid": "8086",
"pid": "0b*"}`. A plugin with matches in neither place is named in `/api/v1/plugins` → `problems` and never runs.

## Matches

| Form | Matches | Example |
|---|---|---|
| `usb:VID:PID` | a USB device; `*` globs, PID optional | `usb:0483:5740`, `usb:8086:*` |
| `pci:VENDOR:DEVICE` | a PCI function | `pci:10ec:8168` |
| `pci-class:<hex prefix or name glob>` | a PCI function by class code (`0c03`) or class name (`"*Network*"`) | `pci-class:0400` |
| `by-id:<glob>` | a device whose own or function nodes' `/dev/serial/by-id` / `/dev/v4l/by-id` links match | `by-id:*fdcanusb*` |
| `tty:<glob>` / `v4l:<glob>` | a device exposing that tty / video node, or the node itself | `tty:/dev/ttyACM*` |
| `netdev:<glob>` | a network interface | `netdev:enp*` |

The host passes the PHYSICAL device when a match is through one of its function nodes (the fdcanusb, not its
`/dev/ttyACM0`). Host structure (the host, buses, groups, ports) never matches.

## The boundary (docs/build-plan-v1.md, "Plugin boundary")

```
<command> describe                                → one JSON object: {name, version, schema, matches, actions}
<command> diagnose --device <id>                  → JSON lines, one contract.record v0 record each
<command> act --device <id> --action <n> --confirm <token>   → never called by the host in v1
```

`diagnose` gets, besides `--device <id>` (the skeleton id, e.g. `usb:1-3`), these environment variables:

| Variable | What |
|---|---|
| `IO_DIAGNOSTICS_DEVICE_PATH` | the device's tree path now, e.g. `host/usb1/port-1-3/usb-1-3` |
| `IO_DIAGNOSTICS_DEVICE` | the device's skeleton dict as JSON (`bus, id, path, label, vendor, product, serial, parent, attrs`) plus `functions`: its tty / video / NIC nodes (by-id links live there) |
| `IO_DIAGNOSTICS_NOW` | the host clock, Unix seconds (a fixture replay's clock in fixture mode) |
| `IO_DIAGNOSTICS_SCHEMA` | `diagnostics-record/0` |
| `IO_DIAGNOSTICS_FIXTURE`, `IO_DIAGNOSTICS_FIXTURE_FILE` | fixture mode only: the frame name and the recording |

### Each line

`contract.record.to_dict(record)` of an `Entity`, a `DiagnosticStatus` or an `Event` (the `record` key says which;
`schema` must be `diagnostics-record/0` or absent). Never a `Fault` (faults are the host's `FaultManager`'s) and
never a bare `Action` (actions travel inside their `Entity`).

- **Paths are relative to the device.** `""` is the device itself (its `Entity` adds identity and actions to the OS
  row; its statuses are the device row's own checks), `"can"` is a child, `"can/15/driver"` deeper. A path that
  already starts with `IO_DIAGNOSTICS_DEVICE_PATH` is accepted as is. WHY relative: the device's tree path changes
  when it moves to another port; relative records follow it.
- **A plugin that roots its records at a path of its own** (e.g. `host/usb-6-2/d405-<serial>`) is re-rooted by
  identity: the entity whose `hardware_id` equals the device's (`usb:<vid>:<pid>:<USB serial>`, what sysfs shows)
  IS the device row, and every record under its path moves with it. Without that entity, such paths are taken as
  relative and nest under the device.
- **Two parent edges:** `via` = connectivity (`"can/15"` via `"can"`, the bus via `""`), `part_of` = composition (a
  node's `driver` / `motor`, a camera's streams). An entity with neither hangs via the device; a parent that never
  arrives re-hangs it on its nearest ancestor by path.
- **Black boxes self-report:** a composite reports what a child's failure means for IT (the spider says WARN with
  one leg in ERROR). The host never overrides it; the worst-below rollup is only a hint.
- **Every non-OK status needs a `code`** (PascalCase): `NoAck`, `NoReply`, `PortBusy` (a check that could not run
  because another process holds the port is `unknown` + `PortBusy`), `UpdateAvailable` (check name `... firmware`,
  values `installed_version` / `latest_version`), `MoteusFault.33`.
- **Periodic checks set `expected_period_s`.** The host derives STALE when it has not RECEIVED one for
  `max(3 × period, 5 s)` on its own clock; a `received` you write is ignored (except for `replay = true` plugins,
  which re-deliver a recording). Event-driven checks leave it unset and never go stale.
- **Optional `icon` on an `Entity` line** (`servo`, `chip`, `motor`, `camera`, `stream`, `adapter`, `bus`, ...; list
  in docs/api-v1.md) picks the row's icon; it is a host hint, not part of the record.

### What the host does with a run

| What happened | What the device row shows (a host check named `<plugin>: plugin run`) |
|---|---|
| exit 0, every line valid | OK, "ran in 0.4 s · 14 records" |
| some lines not JSON / unknown record / other schema / a Fault | WARN `PluginOutputMalformed`; the good lines are used |
| a record breaks `contract.record.validate` (e.g. WARN without a code) | WARN `PluginOutputMalformed`; the record is still used |
| exits non-zero (any code: levels travel in the records, and a traceback also exits 1), or dies on a signal | ERROR `PluginCrashed`, with the last stderr line; lines printed before it are still used |
| still running at `timeout_s` | ERROR `PluginTimeout`; its process group is killed; earlier records age toward STALE |
| cannot start (missing executable) | ERROR `PluginNotStarted` |

The host merges each run into what it already holds (latest per entity and check): a check that could not run this
time is simply not re-sent, and its last record goes STALE on the host's clock.

## Rules a plugin must keep (docs/build-plan-v1.md, Hard rules)

- Read only, unless an action ran (and in v1 no action runs). Before any active check: the port-holder check and the
  rack bus lock (`/home/bam/.cache/rack-commissioning/bus.lock`, `LOCK_NB`, skip if held). Never steal a port: if
  another process holds it, report `unknown` + `PortBusy` and stop.
- Finish well inside `timeout_s`; the host kills the whole process group after it.

## Fixture plugin

`tests/fixtures/plugins/v6_scenario.toml` is a working example: a stdlib plugin that replays V6's three recorded
frames (`io-diagnostics serve --fixture F1|F2|F3`). `tests/test_host_plugins.py` exercises a good, a malformed, a
crashing and a hanging plugin.
