# IO diagnostics — server API v1

*2026-10-22, lane F (host + tray). The only interface the web UI and the tree proposals may rely on. Served by
`io_diagnostics/server.py` (stdlib `http.server`, default `http://127.0.0.1:8812/`). Tests:
`tests/test_host_api.py` checks every endpoint against the shapes below; `tests/test_server_act.py` the one write.*

Every endpoint is `GET` and returns JSON (`Content-Type: application/json`, `Cache-Control: no-store`), except the
page's writes: `POST /api/v1/act`, the Utilities panel (see "Actions from the page" below), and `POST
/api/v1/expect | forget | retire`, the Expected setup card (host-side writes of the person's rig file, never a
device; "Editing the expected setup" below). Any other method or path returns **405**. **Agents never call `POST /api/v1/act`**: they dry-run with `io-diagnostics act --dry-run`; a real
action runs only from a person's click on the page (or `io-diagnostics act` typed by a person at a TTY). The record contract underneath is `contract/record.py`
(`SCHEMA = "diagnostics-record/0"`, `RECORD_VERSION = "0.1"`); `docs/diagnostics-record-v0.md` explains every rule
this API applies.

```bash
uv run --project /home/bam/io-diagnostics python /home/bam/io-diagnostics/io_diagnostics/cli.py serve --fixture F2
```

| Endpoint | What a page uses it for | Poll |
|---|---|---|
| `GET /api/v1/state` | Everything a page draws every tick: clock, chip, the whole tree (nested), the last 6 events | 1 s |
| `GET /api/v1/node?path=<entity path>` | The detail panel of one row: its checks, values, measurements, actions with gate state, faults, events | on select, then 1-2 s |
| `GET /api/v1/activity[?limit=N]` | Activity log (all events, newest first), faults, link-health rows | 2-5 s |
| `GET /api/v1/records` | The raw v0 records the host holds (entities, statuses with `received`, events, faults) | rarely |
| `GET /api/v1/plugins` | Plugin registry: loaded `plugins.d/*.toml`, matched devices, last run per device | rarely |
| `GET /api/v1/health` | `{"ok": true, ...}` liveness | — |
| `POST /api/v1/act` | The Utilities panel ONLY: a dry run, then a person's confirmed run with the dry run's token | on click |
| `POST /api/v1/expect`, `/forget`, `/retire` | The Expected setup card ONLY: one edit of the rig file after a person's confirm | on click |
| `GET /`, `/<file>` | The page in `io_diagnostics/web/` | — |

Errors: an unknown `path` → **404** `{"error": "..."}`; an exception while building a response → **500**
`{"error": "<Type>: <message>"}` (the server keeps serving).

## Conventions

- **Times** are Unix seconds (float, UTC) in fields named `stamp`, `received`, `now`, `*_at`, `first_occurred`...,
  each with an `*iso` / `iso` twin rendered in the host's display zone (`clock.utc_offset_s`; in fixture mode the
  zone the scenario was recorded in, so "18:11:50" reads as it did on the rack). Durations are seconds (`*_s`).
- **Levels** are strings, never numbers. A row's level is one of `"ok" | "warn" | "unknown" | "stale" | "error"`
  or `null` (nothing at or below reports anything). Order, worst last: `ok < warn < unknown < stale < error`
  (`clock.rank` repeats it). `stale` is never sent by a plugin: the host derives it.
- **Paths** are the entity keys: `/`-separated, rooted at `"host"`. A path changes when a device moves to another
  port; `hardware_id` does not.
- Unknown fields may be added to any object later (additive changes keep `api` = `"io-diagnostics/1"`); a page
  must ignore fields it does not know.

## `GET /api/v1/state`

```json
{
  "api": "io-diagnostics/1",
  "schema": "diagnostics-record/0",
  "record_version": "0.1",
  "clock": {"now": 1761095510.0, "iso": "2026-10-21T18:11:50-07:00", "time": "18:11:50",
            "mode": "fixture", "utc_offset_s": -25200,
            "rank": ["ok", "warn", "unknown", "stale", "error"], "stale_factor": 3.0, "stale_floor_s": 5.0},
  "fixture": {"name": "F1", "label": "18:11 · plug", "story": "28B3A25D settled on 1-5 ...", "clock": 1761095510.0,
              "frames": [{"name": "F1", "label": "18:11 · plug"}, {"name": "F2", "label": "18:25 · bus busy"},
                         {"name": "F3", "label": "19:02 · after restart"}]},
  "host": {"name": "bam-GPU", "sources": [{"name": "io_facts.usb", "state": "used", "note": "12 devices"},
                                          {"name": "io_facts.pci", "state": "skipped", "note": "ModuleNotFoundError: ..."}]},
  "summary": {"level": "error", "counts": {"error": 2, "warn": 3},
              "attention": [{"path": "host/usb1/port-1-5/usb-1-5/can", "name": "28B3A25D CAN bus",
                             "kind": "bus", "icon": "bus", "level": "error",
                             "message": "error-passive · no ACK: no powered node on this bus · 0 of 4 answer",
                             "device": {"path": "host/usb1/port-1-5/usb-1-5", "name": "fdcanusb 28B3A25D"}}],
              "entities": 41, "devices": 10},
  "chip": {"label": "IO 5 ⚠", "label_guide": "IO 00 ⚠", "icon": "attention", "title": "IO diagnostics",
           "menu": [{"text": "5 need you · 10 devices"}, {"sep": true}, "..."]},
  "tree": {"...": "node, see below; children nested"},
  "recent": [{"...": "event, see Activity"}],
  "expected_setup": {"file": "/home/bam/.config/io-diagnostics/expected.toml", "editable": true, "note": "",
                     "rigs": [{"name": "1-DOF test rack", "path": "host/rig-1-dof-test-rack", "devices": 4}],
                     "retired": [{"name": "fdcanusb 28B3A25D", "hardware_id": "usb:0483:5740:28B3A25D", "serial": "",
                                  "date": "2026-10-23", "reason": "frames never reached the bus (2026-10-22) ...",
                                  "path": null}]}
}
```

- `record_version`: the additive revision of the record contract this host validates against (`"0.1"`: identity
  `model_id`, danger `moves`, the standard action names). `schema` stays `"diagnostics-record/0"`.
- `fixture` is `null` in live mode. In fixture mode the host replays a recorded scenario through the same code path
  as live plugins; `clock` runs at 1× from the frame's clock (plus `--offset`).
- `host.sources`: which OS skeleton sources were used and which were skipped (with the reason), so a page can say
  "PCI discovery not available" instead of implying there is no PCI. A `"seen devices"` entry appears only when
  the remembered-devices file could not be read or written.
- **Expected, not connected** (record v0.1): a device a plugin's `describe` lists in `expected`
  (`[{hardware_id, name, last_seen, source}]`), or a USB device this host has seen a plugin diagnose (kept in
  `~/.local/state/io-diagnostics/seen.json`, live mode only), that is not on USB now. Each one is a tree row under
  the group `host/expected` (`kind` `"group"`, name `"Expected, not connected"`). The row's `kind` is `"device"`,
  its `owner` the plugin (or `io_diagnostics` for a remembered one), and its one check is `<name>: presence`.
  That check is `warn` with `code` `"ExpectedAbsent"`, and its `message` says what the kernel saw this boot:
  `"expected (last seen 2026-10-21 18:24, rack_inventory) · not on USB · the kernel saw no plug event since boot
  (2026-10-22 07:00): try a data cable or another port"`, or `"… plugged in at 09:00:01 on 1-5, gone at 09:30:00"`,
  or that the kernel log could not be read. Its values are `what_to_check`, `kernel_evidence`
  (`never | left | unknown`), `expected_by` and `last_seen`. Only `usb:<vid>:<pid>:<serial>` ids are judged.
- `summary` is the **root's advisory rollup** (the chip's question: "does anything need me?"):
  - `level`: the worst independent cause anywhere (`null` = nothing reports).
  - `counts`: independent causes per level; knock-on problems under a failed ancestor are not counted.
  - `attention`: every independent cause that is not `ok`, worst first; `device` is the nearest ancestor of kind
    `device` (`null` if none) so a page can say "fdcanusb 28B3A25D › 28B3A25D CAN bus".
  - `devices`: physical devices (USB devices and hubs, PCI functions; not their tty / video / NIC nodes); `entities`: all rows.
- `chip`: the top-bar chip as the AppIndicator renders it (`contract/indicator.py` model: `label`, `icon`
  `"ok" | "attention"`, `menu` items `{"text", "enabled"?, "children"?, "action"?}` or `{"sep": true}`). The web page
  may draw its own chip from `summary`; the `menu` is what the tray shows.
- `recent`: the 6 newest events (same shape as in `/api/v1/activity`).
- `expected_setup` (2026-10-23): the person's rig manifest (docs/expected-setup.md): its `file` (`null` = none on
  this host: a fixture or a fake machine without `--expected`), whether the panel may `editable`-ly rewrite it, the
  `note` saying why it does not read, its `rigs` (each with its rig row's `path`) and its `retired` devices (`path`
  = the row of one that is plugged in again, else `null`).
- **Expected setup** rows: one composite row per rig, `host/rig-<name>` (`kind` `"rig"`, `icon` `"group"`), first
  under the host. Its `name` self-reports the counts ("1-DOF test rack · 2/2 adapters · 6/7 boards · 2/2 cameras");
  its one check `<rig>: expected setup` is `warn` `ExpectedMissing` naming what is missing (with `what_to_check`),
  `unknown` `ExpectedNotChecked` when an expected node's check could not run, else `ok`. An unexpected extra node, a
  model that differs and "its boards answer on fdcanusb 0607623A now" (an absent adapter's boards on another one)
  are notes in its message and values, never a level. A retired device that is plugged in again carries a `warn`
  check `<name>: retired` (`RetiredDevice`, "retired 2026-10-23: <reason>"). An expected device matched only by a
  driver's serial (the RealSense SDK serial) that is not here gets a row under `host/expected` (`ExpectedAbsent`,
  "no connected device reports serial …"); one with a USB `hardware_id` gets the kernel-evidence row above. A node
  the file gives a `rotor_to_output_ratio` whose fresh config reading differs carries one `warn` check
  `<node>: expected ratio` (`RatioMismatch`, "config ratio 0.1667, expected 0.1 (10:1 gearbox)").
- **Another adapter works**: when a device's check says `FramesNotSent` (its frames never reached the wire) while
  another device of the same plugin on this PC has nodes answering, the host adds a `warn` check
  `<device>: another adapter` (`OtherAdapterWorks`, source `io_diagnostics.host`) on the same row: "fdcanusb 272813EF
  works on this PC: plug this bus into it to tell adapter from bus".

### The node (one tree row)

```json
{
  "path": "host/usb1/port-1-3/usb-1-3",
  "name": "fdcanusb 272813EF",
  "kind": "device",
  "icon": "adapter",
  "parent": "host/usb1/port-1-3",
  "edge": "via",
  "depth": 3,
  "level": "ok",
  "message": "adapter OK (answers can status)",
  "own": {"level": "ok", "message": "adapter OK (answers can status)"},
  "rollup": {"level": "error", "origin": "host/usb1/port-1-3/usb-1-3/can/15/driver",
             "origin_name": "moteus-c1 driver", "message": "moteus-c1 driver: fault 33 (motor driver) on bridge enable",
             "counts": {"error": 1}, "knock_on": []},
  "hint": {"level": "error", "origin": "host/usb1/port-1-3/usb-1-3/can/15/driver",
           "message": "moteus-c1 driver: fault 33 (motor driver) on bridge enable"},
  "knock_on_of": null,
  "stale": false,
  "connected": true,
  "hardware_id": "usb:0483:5740:272813EF",
  "identity": {"manufacturer": "mjbots", "model": "fdcanusb", "serial_number": "272813EF"},
  "owner": "moteus_servo",
  "id_text": "272813EF",
  "last_received": 1761095510.0, "age_s": 0.0, "expected_period_s": null, "stale_after_s": null,
  "checks": 2, "actions": 1, "faults": 0,
  "children": []
}
```

| Field | Meaning |
|---|---|
| `path`, `name` | The entity key (Conventions: changes on replug; key selections on it) and the name to show. A plugin may rename its device's OS row ("fdcanusb" → "D405 ·6036"); the name is rendered as authored. |
| `kind` | What the entity is (open string; the record contract's vocabulary plus `group`): `host`; `bus` (an OS bus from `io_facts.skeleton`: a USB root hub "USB bus 1", a PCI root bus; or a plugin's bus, e.g. a CAN bus); `group` (on-board / parentless nodes of one source, e.g. "On-board serial ports", or "USB" when the fallback reader is used); `port` (a USB port, added by the host between a device and its hub or root bus); `device` (every OS hardware or function node: a USB device, a hub, a PCI function, a tty, a video node, a NIC); plugin kinds `node`, `part`, `stream`; and `rig` (a rig row of the expected setup, first under the host). |
| `icon` | The icon TYPE to draw (closed vocabulary; unknown → draw `device`): `host`, `group`, `usb`, `pci`, `net`, `tty`, `video`, `port`, `hub`, `adapter`, `bus`, `servo`, `chip`, `motor`, `camera`, `stream`, `webcam`, `hid`, `device`. A plugin may hint it; otherwise the host derives it from kind, bus and USB class. |
| `parent`, `edge` | The ONE parent edge (contract `Entity`): `"via"` = connectivity (port, hub, adapter, bus it is reached through), `"part_of"` = composition (a node's driver / motor; a camera's streams). Draw them differently if you like; both nest in `children`. `null` for the root. |
| `depth` | 0 at `host`. |
| `level`, `message` | **What the row shows** (contract `summarize` → `Summary.level` / `.message`). An entity with records of its own is a black box: this is ITS self-report (the worst effective level of its own checks). A pure grouping node (`own.level` = `null`: host, the bus groups) shows its rollup instead. **The host never overrides a self-report.** |
| `own` | The self-report alone; `own.level` = `null` means a pure grouping node. |
| `rollup` | Advisory "worst below" (Redfish `HealthRollup`): the worst *independent cause* at or below this row, where it is, its counts per level, and `knock_on` = `[{"path", "cause"}]` problems collapsed into an ancestor's failure. `level` = `"ok"` when everything below reports OK; `null` when nothing below reports. |
| `hint` | Set when `rollup.level` is worse than `level` (rank order): the "a child is in ERROR" hint a row draws beside its own status. `null` otherwise. Example: the spider self-reports WARN with an ERROR hint from leg 3. |
| `knock_on_of` | This row's own problem is explained by that failed ancestor (one dead bus is one cause): draw it subdued; it is not counted in `summary`. `null` when the row is an independent cause or OK. |
| `stale` | `own.level` is `"stale"`: the host has not received this entity's periodic record for `max(3 × expected_period_s, 5 s)` on its own receive clock. |
| `connected` | `false` = not connected → render fully greyed: an empty USB port, or an entity whose latest record says `mode: "offline"` (a deliberate goodbye). |
| `hardware_id`, `identity` | Repeatable identifier (`usb:<vid>:<pid>:<serial>` for USB) and the descriptive `manufacturer / model / serial_number / hw_version / sw_version / model_id` (any subset; parts carry none). `model_id` (record v0.1) is the robotparts part id, e.g. `"mjbots-moteus-c1"`, a plain string a page may link to robotparts.dev. |
| `owner` | Who registered the entity and would run its actions: a plugin name, or `io_facts.usb` / `io_diagnostics` for OS rows. |
| `id_text` | Short text for the id column: a device's serial (a plugin may replace it, e.g. the RealSense SDK serial), a port's link speed or `empty`, a bus's / group's / the host's physical device count. |
| `last_received`, `age_s` | When the row was last heard from: the newest `received` among its PERIODIC own records (the ones STALE is judged on), else among all its own records (`null` = no records); age on the host clock. |
| `expected_period_s`, `stale_after_s` | Shortest declared period among the row's own periodic records and its STALE threshold (`null` = event-driven: never stale). |
| `checks`, `actions`, `faults` | Counts of own status records, registered actions, and the host's open faults (pending or confirmed) on this row; details via `/api/v1/node`. |
| `children` | Child nodes, ordered: ports and groups by name, plugin entities in the order the plugin sent them. |

## `GET /api/v1/node?path=<entity path>`

```json
{
  "node": {"...": "the node as in /state, with children replaced by a list of child paths"},
  "ancestors": [{"path": "host", "name": "bam-GPU", "edge": null}, {"path": "host/usb1", "name": "USB bus 1 · 480 Mb/s", "edge": "via"}],
  "checks": [
    {"name": "CAN 15: comms", "level": "ok", "effective_level": "stale", "stale": true,
     "message": "answers · 36.5 V · stopped", "code": "", "mode": null, "source": "moteus_servo (rack_inventory, real)",
     "hardware_id": "moteus 22fb62f0 · ABoAQVQwUBUgOTNO", "stamp": 1761096101.0, "received": 1761096101.0,
     "received_iso": "2026-10-21T18:21:41-07:00", "age_s": 199.0, "expected_period_s": 180.0, "stale_after_s": 540.0,
     "uptime_s": null,
     "values": [{"key": "voltage", "value": "36.5 V"}],
     "measurements": [{"key": "voltage", "value": 36.5, "unit": "V", "text": "36.5 V", "level": "ok",
                       "warn_low": null, "warn_high": null, "error_low": null, "error_high": null, "stamp": null}]}
  ],
  "actions": [
    {"name": "reset", "title": "Reset…", "description": "...", "danger": "interrupts", "scope": "node", "kind": "changes",
     "confirm": "Reboot CAN 15 (serial ABoA...)? ...",
     "preconditions": [{"check": "port_free", "text": "Port not held by another process", "state": "fail",
                        "detail": "port held by 640623 tview"}],
     "state": "blocked", "reason": "blocked: port held by 640623 tview", "executable": false, "owner": "moteus_servo"}
  ],
  "faults": [{"...": "fault, see Activity"}],
  "events": [{"...": "event for this path or below, newest first, at most 20"}],
  "entity": {"schema": "diagnostics-record/0", "record": "entity", "path": "...", "...": "contract to_dict(Entity)"},
  "setup": {"role": "device", "file": "/home/bam/.config/io-diagnostics/expected.toml", "note": "", "editable": true,
            "rigs": ["1-DOF test rack"], "rig": "1-DOF test rack", "expected": true, "retired": null,
            "nodes_expected": ["can/1", "can/14", "can/15"], "node_notes": {"can/15": "being replaced"},
            "nodes_here": ["can/1", "can/14"],
            "can": {"expect": true, "forget": false, "retire": true},
            "confirm": {"expect": "Expect fdcanusb 272813EF in 1-DOF test rack with can/1, can/14 (can/15 no longer expected)?",
                        "retire": "Retire fdcanusb 272813EF? It leaves 1-DOF test rack. Plugged in again, ..."},
            "needs_rig": false}
}
```

- `checks`: the row's own `DiagnosticStatus` records, latest per check name, worst first. `level` is what the
  producer said; `effective_level` is what to act on now (`"stale"` once overdue). Values and measurements are
  the producer's; `measurements[].level` comes from its limits (contract `Measurement.level`), `text` is the stock
  rendering (`"36.5 V"`). A check named `firmware` with `code` `"UpdateAvailable"` and values
  `installed_version` / `latest_version` is the update-available convention; a check whose name ends in `config`
  holds a read-only config view.
- Host-authored checks appear here too, with `source` `"io_diagnostics.runner"` (a plugin that crashed, hung or sent
  malformed lines: `code` `PluginCrashed` / `PluginTimeout` / `PluginNotStarted` / `PluginOutputMalformed`) or
  `"io_facts.usb"` (presence, port link).
- `actions`: registered by the entity's owner, with each precondition evaluated **passively** by the host:
  - `port_free` (no process holds the device's tty / by-id; `find_port_holders`), `bus_lock_free` (rack bus lock
    `/home/bam/.cache/rack-commissioning/bus.lock`, read from `/proc/locks`, never taken), `camera_free`
    (`camera_holders`), `board_answers` (the entity has a fresh own record whose code is not `NoReply` / `NoAck` /
    `PortBusy`), `update_available` (its `firmware` check says `UpdateAvailable`).
  - `state`: `"nothing_to_do"` (`update_available` fails: "up to date", whatever else holds, since a free port
    would not make the button useful), else `"blocked"` (another precondition fails), else `"unknown"` (one cannot be
    evaluated), else `"ready"`. `reason` is the text to show beside a disabled button.
  - `danger`: `query` (reads only) · `interrupts` (stops or resets what runs) · `changes` (writes stored state) ·
    `moves` (record v0.1: hardware moves, e.g. moteus calibration spins the rotor). Every danger but `query` needs a
    per-device confirmation. The same standard `name` can carry a different `danger` on another driver; its `title`
    is always the standard label (`contract.record.STANDARD_ACTIONS`, docs/actions-vocabulary-v0.1.md).
  - `confirm` and the precondition `text`s are filled by the host, as the drivers fill them: `{name}` = the
    physical device that owns the action (the nearest `device` at or above the row: a CAN board's adapter, a
    RealSense itself), `{id}` = the row's last path segment (a board's node id), `{serial}` = the row's serial, else
    that device's, `{latest}` = its `firmware` check's `latest_version`. So a board's "Calibrate CAN {id} on
    {name}?" reads "Calibrate CAN 1 on fdcanusb 272813EF?".
  - `executable` is always `false`: nothing runs from a `GET`. The page runs one only through `POST /api/v1/act`
    (a dry run first, then a person's click; "Actions from the page"), a person at a TTY through `io-diagnostics act`.
- `faults`: the host's `FaultManager` memory for this entity (see Activity).
- `setup` (2026-10-23): the row's Expected setup card, or `null` for a row it is not about. `role` is `device` (a
  physical device row itself), `rig` (a rig row) or `absent` (a row under "Expected, not connected"). `rig` /
  `expected` / `nodes_expected` (with the file's `node_notes`) say where the manifest has it, `nodes_here` the nodes answering below it now,
  `retired` its `{date, reason}`. `can` is what the card offers: a device row Expect what's here and Retire…, a rig
  row Expect what's here (every present device's nodes as they answer now), an absent row Forget and Retire…; all
  `false` while the file is not `editable`. `confirm[op]` is the exact sentence the confirm shows, built from the same
  plan the edit runs. `needs_rig`: the device is in no rig and there are several, so the page asks which.

## `GET /api/v1/activity[?limit=N]`

```json
{
  "clock": {"...": "as in /state"},
  "events": [
    {"stamp": 1761095502.0, "iso": "2026-10-21T18:11:42-07:00", "time": "18:11:42", "code": "Plugged",
     "kind": "plugged", "severity": "info", "message": "fdcanusb 28B3A25D plugged on 1-5 after 4 flaps on 1-7",
     "path": "host/usb1/port-1-5", "name": "Port 1-5", "hardware_id": "usb:0483:5740:28B3A25D", "port": "1-5",
     "source": "io_facts.journal", "seq": 12, "args": {"port": "1-5", "flaps": 4}}
  ],
  "faults": [
    {"path": "host/usb1/port-1-3/usb-1-3/can/15/driver", "code": "MoteusFault.33", "severity": "error",
     "status": "confirmed", "active": true, "occurrence_count": 1, "first_occurred": 1761090847.0,
     "first_occurred_iso": "...", "last_occurred": 1761090847.0, "last_occurred_iso": "...", "last_passed": null,
     "last_passed_iso": null, "message": "fault 33 (motor driver) on bridge enable", "hardware_id": "...",
     "name": "CAN 15 driver"}
  ],
  "link_health": [
    {"edge": "Port 1-7", "path": "host/usb1/port-1-7", "kind": "USB", "metric": "enumerations / hour", "value": "4",
     "level": "warn", "source": "journalctl -k (boot enumeration excluded)"}
  ]
}
```

- `events`: newest first, `limit` default 200. `code` is the contract `Event.code` (`Booted`, `ShutDown`,
  `Plugged`, `Unplugged`, `ReEnumerated` from the kernel log through `io_facts.journal`'s flap coalescer;
  `FaultConfirmed`, `FaultHealed`, `FaultCleared` from the host's `FaultManager`; anything a plugin sends, e.g.
  `NoAck`, `PortHeld`). `kind` is the human label of the code (`"re-enumerated"`, `"fault confirmed"`).
  `severity` is `info | warn | error`. `port` is the USB port when known, else `""`.
- `faults`: every fault the host remembers (`pending → confirmed → healed`, or `cleared`), contract `Fault` fields
  plus `*_iso` twins and the entity `name`. WARN confirms on its 3rd consecutive failing report, ERROR at once,
  3 passes heal (`contract.record.DEFAULT_DEBOUNCE`).
- `link_health`: "the computer diagnoses its own wires", one row per (edge, metric): USB ports (enumerations in
  the last hour, link speed) and every entity of kind `bus` (its own checks, and the age of its last report).
  `level` uses the same vocabulary as rows.

## `GET /api/v1/records`

```json
{"clock": {"...": "..."}, "entities": [{"schema": "diagnostics-record/0", "record": "entity", "...": "..."}],
 "statuses": [{"record": "status", "received": 1761095510.0, "...": "..."}],
 "events": [{"record": "event", "...": "..."}], "faults": [{"record": "fault", "...": "..."}]}
```

The host's current memory as contract `to_dict` records with absolute paths: what `io-diagnostics once --json`
prints under `records`, what the interop bridge (`contract.record.to_diagnostic_status`) projects for stock
viewers, and what the rack session runner can read.

## `GET /api/v1/plugins`

```json
{
  "directory": "/home/bam/io-diagnostics/plugins.d",
  "problems": ["broken.toml: command must be a non-empty list of strings"],
  "plugins": [
    {"name": "moteus_servo", "file": ".../plugins.d/moteus_servo.toml", "command": ["..."], "diagnose_args": [],
     "cwd": "...", "timeout_s": 20.0, "act_timeout_s": 900.0, "period_s": 30.0, "enabled": true, "replay": false,
     "exclusive": "rack-bus",
     "description": "moteus boards",
     "matches": ["usb:0483:5740"],
     "describe": {"name": "moteus_servo", "version": "0.3.0", "schema": "diagnostics-record/0"},
     "jobs": [{"device_id": "usb:1-3", "device_path": "host/usb1/port-1-3/usb-1-3",
               "last_run": {"started": 1761095500.0, "duration_s": 1.2, "outcome": "ok", "returncode": 0,
                            "accepted": 14, "rejected": 0, "problems": [], "rejected_lines": [],
                            "message": "ran in 1.2 s · 14 records"}, "next_due_s": 28.8}]}
  ]
}
```

`outcome` is `ok`, `malformed` (some lines rejected), `crashed`, `timeout` or `not_started`; the same outcome is the
host check on the device row. `diagnose_args` is what the declaration appends after `diagnose --device <id>`
(e.g. a driver's `--passive`). `describe` is `null` until the plugin's `describe` verb has run (once per enabled
plugin, all in parallel, at the host's first refresh, before the first match); a v0.1 plugin may state
`"record_version": "0.1"` and its `expected` devices there (see "Expected, not connected"). `exclusive` is the
declaration's group (`""` = none): the host never runs two jobs (diagnose or act) of one group at once, across
plugins and across one plugin's devices; a due job whose group is busy waits its turn (oldest due first) and is
never failed for it. Both rack drivers declare `"rack-bus"`, because each takes the rack bus lock with
`flock LOCK_NB` and would otherwise fail its sibling with `BusLockHeld`. `next_due_s` stays `0.0` while a job waits
for its group.

## Actions: `io-diagnostics act` (a person at a terminal)

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics act --path host/usb1/port-1-3/usb-1-3 --action check_comms --dry-run
```

`act --path <entity path> --action <name> [--node N] [--new-id N] [--confirm TOKEN] [--dry-run]` (also
`--plugins`, `--enable`, `--fixture`, `--machine` as for `once`) finds the job whose device row is at or above
`path`, looks the action up on the entity (after a diagnose) or else in the plugin's `describe` `actions`, and runs
the plugin's `<command> act --device <id> --action <name> [--node N] [--new-id N] [--confirm TOKEN] [--dry-run]`
inside its `exclusive` group. `--new-id` is `set_id`'s new bus id (`act --action set_id --node 15 --new-id 7`); the
host passes it through and the plugin checks it (moteus: 1-126, not the board's own id, no board answering it yet;
its confirm token names it). Host function: `Host.act(path, action, node=, confirm=, dry_run=, human=, new_id=)`.

**The rule: only a person runs an action; agents can't click.**
- With stdin not a TTY (an agent, a pipe, a cron job) the host refuses everything except `--dry-run`
  (`refused: "NotInteractive"`). A dry run runs nothing: the plugin prints what it would run.
- An action whose `danger` is not `query` needs `--confirm TOKEN` (`refused: "ConfirmRequired"`, with the action's
  confirm text). The host passes the token through; the plugin checks it, since only the owner knows what it
  confirms (moteus: the token its `ConfirmationRequired` event names; RealSense: the camera's serial).
- Refused before anything runs: `NoSuchEntity`, `NoPlugin`, `ActionNotRegistered`.

It prints one JSON object and exits 0 (ran, plugin exit 0), 2 (refused: nothing ran) or 1 (the plugin crashed or
hung):

```json
{"path": "host/usb1/port-1-3/usb-1-3", "action": "reboot", "node": 15, "new_id": null, "dry_run": true, "plugin": "moteus_servo",
 "device_id": "usb:1-3", "title": "Reboot…", "danger": "interrupts", "registered_by": "describe",
 "argv": ["uv", "...", "act", "--device", "usb:1-3", "--action", "reboot", "--node", "15", "--dry-run"],
 "refused": null, "outcome": "ok", "returncode": 0, "duration_s": 0.4,
 "records": [{"record": "action-result", "command": ["..."], "...": "the plugin's lines, verbatim"}],
 "action_result": {"record": "action-result", "action": "reboot", "dry_run": true, "command_kind": "argv",
                   "command": ["..."], "preconditions": [], "confirm_required": true, "message": "..."},
 "rejected": [], "stderr_tail": "", "message": "ran in 0.4 s · 1 lines"}
```

`outcome` is `refused`, `ok`, `crashed`, `timeout` or `not_started`; `records` are the plugin's JSON lines as it
wrote them (the host does not reinterpret an action's result); `action_result` is the first `record:
"action-result"` line (`null` if none) with the vocabulary's fields (`action`, `title`, `dry_run`, `danger`, `path`,
`hardware_id`, `command_kind` `argv | sdk`, `command`, `preconditions` `[{check, text, state, detail}]`,
`confirm_required`, `confirm_token`, `confirm_given`, `source`, `stamp`, `message`), and its `message` becomes the
result's; `registered_by` is `entity` or `describe`. The plugin's `act` is bounded by its declaration's
`act_timeout_s` (default 900 s; a firmware flash or a calibration outlasts any diagnose), never by `timeout_s`.

## Actions from the page: `POST /api/v1/act`

The Utilities panel's one write (`io_diagnostics/server.py`). **Never call it from an agent, a script or another
tool**: an agent proves a button with `io-diagnostics act --dry-run`, and only a person clicks. Every click on the page
is a dry run first:

```json
{"path": "host/usb1/port-1-3/usb-1-3/can/15", "action": "calibrate", "node": 15, "dry_run": true}
```

The answer carries the `Host.act(..., dry_run=True)` result (above: `argv`, `action_result` with the plugin's
`command`, `steps`, `preconditions`, `confirm`, and its own token) and, when the plugin answered (`outcome` `ok`), the
page's token for exactly that path / action / node / new id:

```json
{"dry_run": true, "result": {"path": "host/usb1/port-1-3/usb-1-3/can/15", "action": "calibrate", "outcome": "ok"}, "token": "c-3TW1BFkuLa5-UsYKpa3OMp", "window_s": 300.0}
```

The run repeats the request with that token and without `dry_run`:

```json
{"path": "host/usb1/port-1-3/usb-1-3/can/15", "action": "calibrate", "node": 15, "token": "c-3TW1BFkuLa5-UsYKpa3OMp"}
```

`set_id` also names the board's new bus id as `new_id` (an integer; omit it or send `null` for every other action).
The server passes it to the plugin as `--new-id` and binds the token to it, so the run must repeat it: a token
issued for `CAN 15 → CAN 7` cannot run `CAN 15 → CAN 9` (`TokenMismatch`).

```json
{"path": "host/usb1/port-1-3/usb-1-3/can/15", "action": "set_id", "node": 15, "new_id": 7, "dry_run": true}
```

and answers `{"dry_run": false, "result": {...}}` with the `Host.act` result of the real run (`records` are the
plugin's lines: `ActionCompleted`, `ConfirmationRequired`, `ActionRefused`, ...). The server passes the PLUGIN's
token from the dry run (`action_result.token`, or `confirm_token`) as `--confirm`; the plugin still checks it against
the device. It runs a real action only when ALL of these hold, else **403** `{"refused": "<Code>", "error": "<plain
reason>"}` and nothing reaches the plugin:

| Code | Refused when |
|---|---|
| `NotFromPage` | the `X-IO-Diagnostics: ui` header is missing (the page's own fetch sets it) |
| `NotSameOrigin` | `Sec-Fetch-Site` is not `same-origin` (a browser sets it; no page can forge it), or `Origin` is another site |
| `NotJson` | the body is not `Content-Type: application/json` (forces a CORS preflight this server never answers) |
| `NoDryRun` | no token, a token this server never issued, or one already used (tokens are single use) |
| `TokenExpired` | the dry run is older than `window_s` (300 s: a moteus token is good for at least one 5-minute window) |
| `TokenMismatch` | the token was issued for another path, action, node or new id |

A malformed body (`node` or `new_id` not an integer or `null`, ...) is **400** `BadRequest`. The page's rule on top
(web/app.js): a `query` runs with one more click; any other danger only from a confirm modal that repeats the
danger; `moves` only with a ticked box that states the physical precondition (e.g. "CAN 15's joint is free to
spin") and a button labelled with the action. `set_id`'s row holds a "New id (1-126)" number box: its dry run stays
disabled until the box holds a whole number 1-126 other than the board's own id, its confirm modal repeats
"CAN 15 → CAN 7", and typing another id closes a dry run shown for the old one. Agents must not
replay this flow: the headers are what a browser sends, and a command-line client can forge them, which is why the
rule is written here and not only enforced.

## Editing the expected setup: `POST /api/v1/expect`, `/forget`, `/retire`

The Expected setup card's three buttons (docs/expected-setup.md). Each rewrites the person's rig file
(`~/.config/io-diagnostics/expected.toml`, atomically) and nothing else: no device is touched, so there is no dry run
and no token. The page asks one plain confirm first, showing `setup.confirm[op]` from `/api/v1/node`. The same
same-origin rule as `/api/v1/act` applies (`NotFromPage`, `NotSameOrigin`, `NotJson`: **403**), and agents never call
them (the CLI has `io-diagnostics expect | forget | retire`).

```json
{"path": "host/usb1/port-1-5/usb-1-5", "rig": null, "reason": ""}
```

- `expect` (Expect what's here): on a device row, adds it (or updates it) with the nodes answering below it now, into
  its rig, else `rig`, else the only rig (several rigs and none named: `RigRequired` with `rigs`); it also takes a
  retired device back into service. On a rig row: every present device's nodes as they answer now.
- `forget`: on a row under "Expected, not connected" only (`NotAbsent` otherwise): removes it from the file and from
  the devices this host remembers (`seen.json`), and keeps a plugin's own list from bringing it back until it is
  seen again.
- `retire` (Retire…): on a device or absent row, with a non-empty `reason` (`ReasonRequired`): removes it from every
  rig and lists it under `[[retired]]` with today's date; plugged in again, its row says "retired <date>: <reason>".

The answer is **200** `{"ok": true, "op", "path", "refused": null, "message": "done: ...", "file"}`, or **400**
`{"ok": false, "refused": "<Code>", "message": "<plain reason>", ...}` (`NotADevice`, `NotHere`, `NotAbsent`,
`ReasonRequired`, `RigRequired`, `ManifestUnavailable` when the file does not read or this host has none,
`NotWritten`), or **404** when no row has that `path`.

## `GET /api/v1/health`

`{"ok": true, "api": "io-diagnostics/1", "mode": "live" | "fixture", "uptime_s": 12.3}`

## What a page must NOT assume

- That `level` is the worst below: it is the row's self-report. Use `hint` / `rollup` for "something below is worse".
- That every row has records: groups and the host have none (`own.level` = `null`).
- That levels are only ok/warn/error: `unknown` (not evaluated, e.g. `PortBusy`) and `stale` (overdue) are first-class
  and rank above `warn`.
- That a path is stable across replugs (key selections on `path`, but expect it to vanish and reappear elsewhere;
  `hardware_id` follows the device).
- That a `GET` runs anything: `executable` is `false`; a button runs only through `POST /api/v1/act` after its dry run.
- That `danger` has three values: record v0.1 added `moves`; treat any value it does not know as needing a confirm.
