# IO diagnostics — server API v1

*2026-10-22, lane F (host + tray). The only interface the web UI and the tree proposals may rely on. Served by
`io_diagnostics/server.py` (stdlib `http.server`, default `http://127.0.0.1:8812/`). Tests:
`tests/test_host_api.py` checks every endpoint against the shapes below.*

All endpoints are `GET` and return JSON (`Content-Type: application/json`, `Cache-Control: no-store`). Any other
method returns **405**: actions never execute in v1, so there is nothing to `POST`. The record contract underneath is
`contract/record.py` (`SCHEMA = "diagnostics-record/0"`); `docs/diagnostics-record-v0.md` explains every rule this
API applies.

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
  "recent": [{"...": "event, see Activity"}]
}
```

- `fixture` is `null` in live mode. In fixture mode the host replays a recorded scenario through the same code path
  as live plugins; `clock` runs at 1× from the frame's clock (plus `--offset`).
- `host.sources`: which OS skeleton sources were used and which were skipped (with the reason), so a page can say
  "PCI discovery not available" instead of implying there is no PCI.
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
| `kind` | What the entity is (open string; the record contract's vocabulary plus `group`): `host`; `bus` (an OS bus from `io_facts.skeleton`: a USB root hub "USB bus 1", a PCI root bus; or a plugin's bus, e.g. a CAN bus); `group` (on-board / parentless nodes of one source, e.g. "On-board serial ports", or "USB" when the fallback reader is used); `port` (a USB port, added by the host between a device and its hub or root bus); `device` (every OS hardware or function node: a USB device, a hub, a PCI function, a tty, a video node, a NIC); and plugin kinds `node`, `part`, `stream`. |
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
| `hardware_id`, `identity` | Repeatable identifier (`usb:<vid>:<pid>:<serial>` for USB) and the descriptive `manufacturer / model / serial_number / hw_version / sw_version` (any subset; parts carry none). |
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
  "entity": {"schema": "diagnostics-record/0", "record": "entity", "path": "...", "...": "contract to_dict(Entity)"}
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
  - `executable` is always `false` in v1: the host shows actions and never runs them.
- `faults`: the host's `FaultManager` memory for this entity (see Activity).

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
     "cwd": "...", "timeout_s": 20.0, "period_s": 30.0, "enabled": true, "replay": false, "description": "moteus boards",
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
(e.g. a driver's `--passive`). `describe` is `null` until the plugin's `describe` verb has run (it runs once, in the
background, never blocking `diagnose`).

## `GET /api/v1/health`

`{"ok": true, "api": "io-diagnostics/1", "mode": "live" | "fixture", "uptime_s": 12.3}`

## What a page must NOT assume

- That `level` is the worst below: it is the row's self-report. Use `hint` / `rollup` for "something below is worse".
- That every row has records: groups and the host have none (`own.level` = `null`).
- That levels are only ok/warn/error: `unknown` (not evaluated, e.g. `PortBusy`) and `stale` (overdue) are first-class
  and rank above `warn`.
- That a path is stable across replugs (key selections on `path`, but expect it to vanish and reappear elsewhere;
  `hardware_id` follows the device).
- That buttons do anything: `executable` is `false` in v1.
