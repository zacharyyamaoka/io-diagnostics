# IO diagnostics v1 — build plan (2026-10-22, Zach: "own the dashboard; yes please start implementing")

Turns V6 converged (prototypes/v6-converged) into the real tool. Spec sources: docs/converged-design-2026-10-22.md,
docs/diagnostics-record-v0.md + contract/record.py (the record contract — FROZEN once committed; lanes import it,
never edit it), docs/research/*.

## Decisions (Zach, 2026-10-22)
1. **Own the dashboard. Interop by adapter:** one pure function `to_stock(record) -> DiagnosticStatus dict` that
   strips our added fields; a standard viewer (Lichtblick, or ROS 2 `ros2 topic echo /diagnostics`) must show our
   records. "I always like that design, it makes it backwards compatible."
2. **Implement now:** one Python process, a tray chip, a web window; driver diagnostics in moteus_servo and intel_rgbd.
3. **Report feedback (visual noise):**
   - Disclosure chevrons appear on hover (Notion-style, over the existing icon) or IDE-style left of the row — never a
     permanent column of arrows.
   - Type icon and name sit together; the status icon moves to the RIGHT.
   - **Quiet when OK:** no green dots. OK rows show nothing. Warnings and errors use SHAPES (▲ warning, ⬣/✖ error,
     clock for stale), not just colour dots.
   - Not connected = fully greyed; connected = normal grey text; only problems get colour.
4. **Beyond USB:** discovery must also cover PCI/PCIe (hyperspectral camera on a frame grabber), network interfaces
   (EtherCAT segments: detect the NIC + any running master, don't scan the bus yet), serial ttys, video4linux.

## Plugin boundary (implementation decision — orchestrator, 2026-10-22)
A plugin is an EXECUTABLE that speaks JSON lines on stdout (Arduino pluggable-discovery precedent, research lane):
`<cmd> describe` → {name, version, schema: contract.record.SCHEMA ("diagnostics-record/0"), matches: [usb vid:pid | by-id glob | pci
class | netdev | tty glob], actions: [...]}; `<cmd> diagnose --device <id>` → v0 records as JSON lines, each tagged by its `record` key (Entity / DiagnosticStatus / Event; never Fault — faults are kept by the host's FaultManager);
`<cmd> act --device <id> --action <name> --confirm <token>` → result record. Plugins are declared in
`plugins.d/*.toml` (name, command, cwd, timeout, period). WHY a subprocess: driver dependencies (moteus,
pyrealsense2) stay in the driver's own uv env, a crashing driver can't take the host down, any language works, and
the driver's diagnostics.py is "the exact same code" the driver already has.

**A real `act` is a run on a terminal (additive, 2026-10-24; Zach: "I would like to see the terminal output ... or enter
user input"; contract: docs/runs-v1.md).** The host starts a real `act` (never a dry run) with stdin, stdout and stderr on
a PTY, so a tool prints progressively, redraws with `\r` and can wait for a typed answer, and passes the plugin a second
descriptor named in `IO_DIAGNOSTICS_RECORD_FD`:
- **Set:** the plugin writes its JSON-line records (the `action-result`, Events like `ActionCompleted`) to that fd and
  everything a person would read to stdout / stderr; a wrapped vendor tool INHERITS the terminal (live output, it can
  prompt). **Not set** (a dry run, `diagnose`, `probe`, `follow`, an old plugin that ignores the variable): stdout stays
  the record channel, exactly as before. An old plugin under a run still works: the host finds no record on the fd and
  reads the terminal's lines that parse as a record (they also show in the terminal).
- **`stop`, an optional verb** (`"stop": true` in `describe`): `<cmd> stop --device <id> [--node N]` puts the device in its
  quiet state (moteus: mode 0), needs no `--confirm` (stopping is the safe direction), prints JSON lines, and is safe at any
  time. The host runs it itself, never the page, after a `moves`-class run that did not finish (stopped, timed out, failed).
- **Stop** from the page is SIGINT to the plugin's process group, then SIGTERM and SIGKILL after 3 s each; a step that must
  survive a stopped plugin (a firmware flash) keeps its own session.

**`follow`, an optional long-running verb (additive, 2026-10-23; Zach: "something that feels a bit like system
monitor with the live data").** A plugin may declare in `describe` `"follow": {"period_s": 1}` (or `"follow": true`,
1 s). The host's service (`serve`, `tray`; never `once`) then runs NO periodic diagnose for that plugin's devices and
keeps ONE `<cmd> follow --device <id> [diagnose_args]` per matched device alive instead (`runner.Follower`):
- **Lines** are exactly diagnose's (`contract.record` JSON lines: Entity / DiagnosticStatus / Event, paths relative
  to the device, the same validation), printed and flushed one batch per period, forever. The host applies each line
  as it arrives, so the history fills at the plugin's rate. Statuses set `expected_period_s` to their real cadence.
- **Environment** as for diagnose; `IO_DIAGNOSTICS_NOW` is the start time.
- **Restarts:** when it exits (any code) or prints nothing for `max(10 × period_s, timeout_s)` (its process group
  is killed), the host restarts it after 1 s, doubling to 60 s, back to 1 s after a run of 60 s or more. While it is
  down the device row's `<plugin>: plugin run` is ERROR `PluginCrashed` ("follow exited 1: ... · restarting in 4 s");
  while it runs, OK "following since ...", re-said once per period, so a silent follower goes STALE.
- **Bounded:** a line over 256 KiB is rejected and skipped unread, stderr keeps its last 20 lines, the job's record
  cache keeps the latest per entity / check and at most 500 events. A device that leaves stops its follower.
- **Stop:** the host kills the process group (SIGKILL) on stop or when the device leaves; a follower must hold
  nothing that needs a clean exit. `plugins/computer` follows at 1 Hz (docs/api-v1.md `/api/v1/plugins` → `follow`).

**`probe`, an optional on-connect verb (additive, 2026-10-23; Zach: "each device tells diagnostics what to do").** A
plugin may declare in `describe` `"probe": {"timeout_s": 30}` (or `true`). The host's service then runs
`<cmd> probe --device <id> [diagnose_args]` once per connection (first match, host start, re-enumeration) in place of
that connection's first diagnose, starts the periodic diagnose / follow `period_s` after it, and runs it again when it
ended blocked (`PortBusy` / `BusLockHeld`): ~1 s after the last holder pid exits, plus a 10/20/40 s backoff capped at
`period_s`, inside the `exclusive` queue. The driver says what it found in its own `Found` / `Probed` / `ProbeBlocked`
events; the host derives none. The contract, with the standard codes and the safety rule (query-only): `docs/probe-on-connect.md`;
the host side: `io_diagnostics/probe.py`, `Host._probed`, `tests/test_probe.py`.

## Lanes (disjoint write sets)
- A **UI polish** — io_diagnostics/web/* (moved from prototypes/v6-converged/web) per decision 3; screenshots.
- B **Discovery sources** — io_facts/{pci,netdev,tty,v4l}.py + tests with sysfs fixtures; merge into the tree as
  OS skeleton siblings of USB (host → bus → device).
- C **Interop** — io_diagnostics/interop/foxglove_bridge.py (+ a ROS 2 docker check): the stock view IS
  `contract.record.to_diagnostic_status` / `from_diagnostic_status` (do not rewrite it), a foxglove-sdk
  WebSocket server publishing diagnostic_msgs/msg/DiagnosticArray (ros2msg schema); prove it in Lichtblick
  (open-source, run locally) and/or the ROS 2 docker image (`ros2 topic echo /diagnostics`).
- D **moteus_servo plugin** — bam_ws src/core/mdp/agent/actuators/moteus_servo/src/moteus_servo/diagnostics.py
  (+ tests): adapters by serial, nodes, identity, firmware vs latest, read-only config (tel stop + allow_any_response
  + own transport), `can status` counters, reply loss; actions registered (clear faults, reboot) but gated.
- E **intel_rgbd plugin** — bam_ws src/core/mdp/agent/sensors/intel_rgbd/.../diagnostics.py (+ tests): serials,
  USB type, firmware vs recommended, streams' configured rates; never opens a stream another process holds.
- F **Host + tray** — io_diagnostics/{host.py, plugins.py, tree.py, server.py, tray.py}: plugin registry +
  subprocess runner with timeouts, the tree (OS skeleton + plugin children, two parent edges), host rollup from
  contract/record.py (`summarize`, `effective_level`, `FaultManager` — V6's tree.py worst-child-headline rule is
  replaced by the black-box rule: an entity's own report is authoritative, rollup is an advisory hint), staleness, activity log + link health, the AppIndicator chip (V2's), one command
  `io-diagnostics`.

## Hard rules
- No lane opens a CAN adapter or a camera. Plugins are tested with fakes; the orchestrator does the one live run.
  Every active check: port-holder check + the rack bus lock (/home/bam/.cache/rack-commissioning/bus.lock, LOCK_NB,
  skip if held) — never steal a port. Actions never execute in tests.
- Never steal focus. Install nothing system-wide; project-local uv deps only.
- bam_ws edits land on branch claude/traj-tracking-sim-to-real-a3da60 as separate commits (one per driver) so they
  can be cherry-picked; io-diagnostics commits on its main.

## Frozen interfaces between lanes (orchestrator, 2026-10-22)
- **Record contract:** contract/record.py @ c9784d0 (SCHEMA "diagnostics-record/0"). Lanes import it; nobody edits it.
- **OS skeleton sources (lane B → lane F):** each `io_facts.<source>.devices()` returns a list of plain dicts
  `{"bus": "usb|pci|net|tty|v4l", "id": str (stable), "path": str (tree path segment), "label": str,
  "vendor": str|None, "product": str|None, "serial": str|None, "parent": str|None (another id), "attrs": dict}`.
  Lane F imports them defensively (a missing source = skipped with a note).
- **Server API (lane F → tree proposals):** lane F writes docs/api-v1.md before the tree proposals start; it is the
  only thing the web variants may rely on.
