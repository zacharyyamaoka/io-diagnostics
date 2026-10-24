# plugins.d: driver diagnostics plugins

Every `*.toml` here declares one plugin. The host (`io_diagnostics/plugins.py`, `io_diagnostics/runner.py`) loads
them at start, matches them to OS skeleton devices, and runs each matched (plugin, device) pair every `period_s`.
A broken file is skipped and named in `GET /api/v1/plugins` → `problems`; it never stops the host. A file here is a
decision to let that driver's code run.

## The declaration

```toml
name = "moteus_servo"                          # a word; also the owner shown on its rows
description = "moteus boards behind an fdcanusb"
command = ["uv", "run", "--project", "/home/bam/bam_ws/src/core/mdp/agent/actuators/moteus_servo",
           "python", "-m", "moteus_servo.diagnostics"]          # argv, never a shell string
cwd = "/home/bam/bam_ws"                       # optional; default: this file's directory
timeout_s = 20                                 # one diagnose's wall-clock limit; past it the whole process group is killed
act_timeout_s = 900                            # optional, default 900: one act's limit (firmware flash, calibration)
period_s = 30                                  # how often per matched device
matches = ["usb:0483:5740"]                    # see Matches
enabled = true                                 # optional, default true; `io-diagnostics ... --enable NAME` overrides false
env = { MOTEUS_DIAGNOSTICS_QUIET = "1" }       # optional extra environment
diagnose_args = ["--passive"]                  # optional: appended after `diagnose --device <id>` (options of the verb)
exclusive = "rack-bus"                         # optional group: never two runs (diagnose or act) of it at once
```

The two shipped declarations (`moteus_servo.toml`, `intel_rgbd.toml`) run the real drivers and are `enabled = true`
(2026-10-22: plug in the rack and see live what is connected). Their default `diagnose` is query-only traffic to the
hardware, behind the holder check and the rack bus lock, which the peer sessions sharing the rack approved; every
write is an `act`, which the host runs only for a person (below). `tests/fixtures/plugins-live-passive/` declares
the same two drivers with `diagnose_args = ["--passive"]` behind `passive_guard.py` (refuses any diagnose without
`--passive`, and any `act`): the real driver code on the real skeleton, with no adapter or camera opened. Tests
never load `plugins.d` into a running host.

**`exclusive`**: plugins that share a resource the OS cannot arbitrate for them declare one group name. The host
then runs at most one job of that group at a time, across plugins and across one plugin's devices, in `once`, in the
periodic scheduler and for `act`. A due job whose group is busy waits its turn (oldest due first); it is never failed
for it. WHY: both rack drivers take the rack bus lock with `flock LOCK_NB`, so two sibling runs started together
failed each other with `BusLockHeld` (live, 2026-10-22 17:41). Their lock still guards against other processes.

`{python}` (the host's interpreter), `{root}` (the io-diagnostics checkout) and `{here}` (this file's directory)
are expanded in `command`, `cwd` and `env`. A relative executable with a `/` resolves against `cwd`. `timeout` and
`period` (the build plan's spelling) and `act_timeout` are accepted for `timeout_s` / `period_s` / `act_timeout_s`; the
shipped files write the `_s` spelling.

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
| `host` (or `host:<hostname glob>`) | THE host entity, the Computer, and nothing else; no other kind ever matches it | `host` (`computer.toml`) |
| `composite:<kind glob>` | a composite of the expected setup (docs/composition-v1.md) whose `kind` matches, and nothing else; no device glob ever matches one. A composite runs it only when its `[[composite]]` names it (`plugin = "<name>"`) | `composite:arm` (`tests/fixtures/plugins-state/state_arm.toml`) |

The host passes the PHYSICAL device when a match is through one of its function nodes (the fdcanusb, not its
`/dev/ttyACM0`). Host structure (buses, groups, ports) never matches; the host itself matches only `host`, and its
records land on the host row (`--device` is the skeleton's host id, `""` is the Computer). `computer.toml` (enabled:
passive, read-only, no actions) runs `plugins/computer` that way: CPU, RAM, HD Usage, temperatures, GPU, NTP offset,
uptime, network and USB bandwidth, named after ROS's `diagnostic_common_diagnostics`; while a plugin diagnoses the
host, the host adds its own `USB/PCIe links` line there.

## The boundary (docs/build-plan-v1.md, "Plugin boundary")

```
<command> describe                                → one JSON object: {name, version, schema, matches, actions[, record_version, expected, opt_out]}
<command> diagnose --device <id>                  → JSON lines, one contract.record v0 record each
<command> act --device <id> --action <n> [--node N] [--confirm <token>] [--dry-run]   → JSON lines (the result)
<command> probe --device <id>                     → optional; the same JSON lines, once per connection (below)
<command> stop --device <id> [--node N]           → optional ("stop": true in describe); the device's quiet state, no --confirm (below)
```

**A real `act` runs on a terminal (docs/runs-v1.md).** The host gives it a PTY for stdin / stdout / stderr, so a tool
prints live, redraws with `\r` and can wait for a typed answer, and it names a second descriptor in
**`IO_DIAGNOSTICS_RECORD_FD`**. When that variable is set a plugin writes its JSON-line records to THAT fd and nothing but
records to it; everything a person should read goes to stdout / stderr, and a wrapped vendor tool inherits the terminal
(its stdin / stdout / stderr as the plugin got them). When it is not set (a dry run, `diagnose`, `probe`, `follow`) stdout is
the record channel as before, so a plugin written earlier keeps working: under a run the host finds no record on the fd and
reads the terminal's lines that parse as a record. A plugin knows it is on a run with `os.environ.get("IO_DIAGNOSTICS_RECORD_FD")`
and `isatty(0)`; `IO_DIAGNOSTICS_RUN_ID` names the run. A Stop is SIGINT to the plugin's process group (a step that must
outlive it, a firmware flash, keeps its own session), then SIGTERM and SIGKILL after 3 s each.

**`opt_out` (optional).** A list of the HOST'S own utilities this plugin's devices do not take (docs/usb-reset.md): `"opt_out":
["reset_port"]` removes "Reset USB port" from those devices' rows, their port rows and the hubs above them, for a device that must
not be reset while an experiment runs.

**`stop` (optional).** A plugin whose `moves` actions can leave a device energised declares `"stop": true` in `describe` and
implements `<command> stop --device <id> [--node N]`: the device's quiet state (moteus: mode 0, the real path of `stop_all`),
**no `--confirm`** (stopping is the safe direction), JSON lines like `act`, safe at any time, behind the same port-holder and
bus-lock gate as every bus action. The host runs it itself, after the process has exited, when a `moves` run ends stopped,
timed out or failed, and records what it said in the run. The page never calls it.

**A person's act waits for the rack bus lock.** When an `act` is refused with the event `ActionBlocked` and `args.reason`
`"BusLockHeld"` (message `... held by <pid> <program>`), the run is `waiting`: the host asks again every 0.5 s for up to
60 s (its terminal says who holds the lock), and only then refuses with the holder named. Keep `LOCK_NB` for `diagnose`
and `probe`: routine checks yield to people and experiments, never the reverse (docs/runs-v1.md section 6).

**`probe` (optional).** A driver that declares `"probe": {"timeout_s": 30}` (or `true`) in `describe` is probed ONCE per
connection, in place of that connection's first `diagnose`: when it is plugged in (or present at host start) and on every
re-enumeration. The periodic `diagnose` (or `follow`) starts `period_s` after the probe; a probe never runs on the period.
It prints what `diagnose` prints, plus the driver's OWN events, `Found` (a sub-device answered), `Probed` (one summary,
last) and `ProbeBlocked` (it could not talk to the device); the host adds no event about a device itself. A probe that ends
with a `PortBusy` / `BusLockHeld` status carrying its holders as `"pid program"` is run again ~1 s after the last holder
exits (and on a 10/20/40 s backoff capped at `period_s`), inside the `exclusive` queue. A probe is query-only (danger
`query`) and bounded by its `timeout_s`. A plugin without `probe` keeps diagnose-on-connect. Contract:
`docs/probe-on-connect.md`.

**A driver updated in place is described again.** The bam_ws drivers run live from their path, so the host looks at each driver's
Python source about every 10 s (the newest modification time and the file count under its `cwd` project's `src/`, or the whole
`cwd` when it has none, plus any `.py` file its `command` names; tests and hidden folders are skipped). When it changed, the host
runs `describe` again, applies what it now declares (the actions, `watch`, `streams`, `tabs`, `follow`, `probe`, `expected`, and
`matches` when they came from describe) and logs a `PluginReloaded` event on the Computer, listing the declaration keys that
changed. A running follow or watch is restarted only when its own declaration changed; a changed `probe` makes a job that is not
followed probe again. If the new `describe` fails, the old declaration keeps running and the Computer shows an UNKNOWN
`<plugin>: describe` line (`DescribeFailed`) until the next edit describes cleanly. Tests: `tests/test_plugin_reload.py`.

`act` runs only through `io-diagnostics act --path <entity path> --action <n> [--node N] [--confirm T] [--dry-run]`,
never on a schedule and never over HTTP in v1. The host refuses everything but `--dry-run` when stdin is not a TTY
(agents can't click), and any action whose `danger` is not `query` without `--confirm`; it passes the token through
and the plugin checks it. `--dry-run` must run nothing and print what it would run (docs/actions-vocabulary-v0.1.md).
Details: docs/api-v1.md, "Actions".

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
- **A composite names what caused its verdict** in `caused_by` (record v0.1 addendum): `"caused_by": ["leg3"]`,
  paths written like `path` (relative to the device). When every named member reports a problem itself, the chip
  counts the member once and the composite's row reads as its knock-on, keeping its own level and message. Name all
  the causes or none (docs/diagnostics-record-v0.md, "v0.1 addendum: caused_by").
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

## Checks: any Monitoring Plugins (Nagios) command, no code needed

A declaration with `kind = "check"` runs ANY command that speaks the stock Monitoring Plugins API, the CLI seam of
round 3 (docs/round3-brief-2026-10-23.md, "CLI as the seam"). The host follows the Monitoring Plugins Development
Guidelines (https://www.monitoring-plugins.org/doc/guidelines.html) as written:

```toml
name = "check_root_disk"
kind = "check"
command = ["/bin/sh", "{root}/plugins/checks/check_root_disk.sh"]   # argv, run as written (no --device, no verb)
period_s = 60                                  # how often
timeout_s = 10                                 # past it the process group is killed: unknown CheckTimeout
attach = "host"                                # the entity its result lands on: "host", a hardware_id, "rig:<rig name>"
enabled = false                                # cwd, env, description, exclusive as for a driver
```

- **Exit code → level** (§2.4 Plugin Return Codes): `0` ok, `1` warn (`CheckWarning`), `2` error (`CheckCritical`),
  `3` unknown (`CheckUnknown`); anything else is unknown, the guidelines' "default catch-all". WHY a check, and not a
  driver, may use the exit code: a driver's levels travel in its records, so a traceback's exit 1 must not pass for
  a WARNING (runner.py); a check has no other channel.
- **First stdout line → message** (§2 Plugin Output for Nagios, "SERVICE STATUS: Information text"); later lines →
  the value `long_output`.
- **Perfdata → measurements** (§2.6): after `|` on the first line and on later lines,
  `'label'=value[UOM];[warn];[crit];[min];[max]`, space separated (`''` is a quote inside a quoted label; `U` = could
  not be determined). warn / crit are §2.5 ranges (`[@]start:end`) and become the measurement's warn / error limits.
  Details and the unit mapping: docs/api-v1.md, "Monitoring Plugins checks".
- A check takes no `matches`, `diagnose_args`, `act_timeout_s` or `replay`, and has no actions.

`check_root_disk.toml` is the shipped example, disabled: a 5-line shell script over `df -P /`
(`plugins/checks/check_root_disk.sh`), because the stock `check_disk` is not installed on this PC; its comment shows
the stock plugin's line.

## Rules a plugin must keep (docs/build-plan-v1.md, Hard rules)

- Read only, unless a person ran an action (`io-diagnostics act`). Before any active check: the port-holder check and the
  rack bus lock (`/home/bam/.cache/rack-commissioning/bus.lock`, `LOCK_NB`, skip if held). Never steal a port: if
  another process holds it, report `unknown` + `PortBusy` and stop.
- Finish well inside `timeout_s`; the host kills the whole process group after it.

## Fixture plugin

`tests/fixtures/plugins/v6_scenario.toml` is a working example: a stdlib plugin that replays V6's three recorded
frames (`io-diagnostics serve --fixture F1|F2|F3`). `tests/test_host_plugins.py` exercises a good, a malformed, a
crashing and a hanging plugin.
