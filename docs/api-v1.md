# IO diagnostics — server API v1

*2026-10-22, lane F (host + tray). The only interface the web UI and the tree proposals may rely on. Served by
`io_diagnostics/server.py` (stdlib `http.server`, default `http://127.0.0.1:8812/`). Tests:
`tests/test_host_api.py` checks every endpoint against the shapes below; `tests/test_server_act.py` the one write.*

Every endpoint is `GET` and returns JSON (`Content-Type: application/json`, `Cache-Control: no-store`), except the
page's writes: `POST /api/v1/act`, the Utilities panel (see "Actions from the page" below), and `POST
/api/v1/expect | forget | retire`, the Expected setup card (host-side writes of the person's rig file, never a
device; "Editing the expected setup" below), and `POST /api/v1/settings`, the View options (the history window,
written to the person's settings file; "Settings" below), and `PUT | DELETE /api/v1/dashboards/<name>.base`, the person's dashboard
files ("Dashboards as files" below). Any other method or path returns **405**. **Agents never call `POST /api/v1/act`**: they dry-run with `io-diagnostics act --dry-run`; a real
action runs only from a person's click on the page (or `io-diagnostics act` typed by a person at a TTY). The record contract underneath is `contract/record.py`
(`SCHEMA = "diagnostics-record/0"`, `RECORD_VERSION = "0.2"`); `docs/diagnostics-record-v0.md` explains every rule
this API applies.

```bash
uv run --project /home/bam/io-diagnostics python /home/bam/io-diagnostics/io_diagnostics/cli.py serve --fixture F2
```

| Endpoint | What a page uses it for | Poll |
|---|---|---|
| `GET /api/v1/state` | Everything a page draws every tick: clock, chip, the whole tree (nested), the last 6 events | 1 s |
| `GET /api/v1/node?path=<entity path>` | The detail panel of one row: its checks, values, measurements, actions with gate state, faults, events | on select, then 1-2 s |
| `GET /api/v1/activity[?limit=N][&types=usb,driver][&fold=1&...]` | Activity log (all events, newest first, typed), faults, link-health rows | 2-5 s |
| `GET /api/v1/events[?types=...]` | Server-Sent Events: each new or changed activity event (a provisional plug, then the same id confirmed) and a `tick` per change | stream |
| `GET /api/v1/live` | Server-Sent Events, change-only: a `snapshot` (the body of `/state`), then an `upsert` for each row that changed, a `remove` for each that left, and `samples` for the State points that arrived; `Last-Event-ID` resumes. Replaces the 1 s `/state` poll; `/state` is unchanged | stream |
| `GET /api/v1/records` | The raw v0 records the host holds (entities, statuses with `received`, events, faults) | rarely |
| `GET /api/v1/plugins` | Plugin registry: loaded `plugins.d/*.toml`, matched devices, last run per device | rarely |
| `GET /api/v1/health` | `{"ok": true, ...}` liveness | — |
| `GET /api/v1/history?path=<entity path>[&status=<name>][&window=<duration>]` | One entity's statuses over the window: level spans (stale and no data included) and every measurement value | on select, then 2-5 s |
| `GET /api/v1/history/bar?path=<entity path>&bins=N[&window=<duration>]` | The health bar of an entity and of each child row: the worst level held per bin | 2-5 s |
| `GET /api/v1/history/tree?bins=N[&window=<duration>]` | The health bar of EVERY tree row in one answer (what `/history/bar` gives each row), so the tree can rewind to before the page opened | on open, then 2-5 s |
| `GET /api/v1/checks` | The latest checks of every entity in one answer, keyed by entity path, each as `/node` gives it: a Diagnostics table across a rack without one `/node` per entity | 1-2 s |
| `GET /api/v1/settings`, `POST /api/v1/settings` | The app-wide settings (the history window); the POST is the page's View options ONLY | on open / on change |
| `GET /api/v1/runs` (and `/runs/<id>`, `/runs/<id>/stream`, `/runs/<id>/cast`; `POST /runs/<id>/input`, `/signal`, `/resize`, page-only) | Every real utility is a run on a terminal: the list, one run, its live output (SSE), its recording, and the page's typing and Stop. The whole contract is docs/runs-v1.md | stream / on open |
| `GET /api/v1/values?path=<entity path>[&since=N]` | The entity page's live numbers and images (State), cheap: no tree build. Docs: docs/state-config-v1.md | 250 ms while State shows |
| `GET /api/v1/frames?path=&stream=` (and `/frames/stream`, MJPEG) | The latest image of a declared stream, and the live multipart stream | on show / stream |
| `POST /api/v1/watch` | The entity page's heartbeat ONLY: keeps the plugin's `watch` running while a page looks at the entity | 3 s while shown |
| `POST /api/v1/config/edit`, `/config/set`, `/config/persist`, `/config/reset` | The Configuration tab's edit session and its live writes ONLY, each with the session's token (docs/state-config-v1.md section 8) | on click / 3 s heartbeat |
| `GET /api/v1/arrangements`, `POST /api/v1/arrangements` | Pinned rows, manual order, folded folders and the sort, per entity kind and section; the POST is the page's ONLY (section 9) | on load / on change |
| `GET /api/v1/dashboards`, `GET`, `PUT`, `DELETE /api/v1/dashboards/<name>.base` | The person's dashboards as files (data-ui's `.base` text, opaque to the host) under the config directory: list, read, write (atomic, optional `If-Match`, or `If-None-Match: *` to create only) and move to `.trash/`; the PUT and DELETE are the page's ONLY | on load / on save |
| `GET /api/v1/diagnostics` | The stock diagnostics' registry (robotdx.diagnostics, stock and custom): each one's title, part types, kind and config dataclass as a schema (field, type, default, unit, doc) | on open |
| `GET /api/v1/diagnostics/config?path=<entity path>`, `POST /api/v1/diagnostics/config` | The stock diagnostics configured on one entity, each value with where it came from (default / toml / edited) and the latest status; the POST is the entity page's drill-in ONLY: one value, validated and written to diagnostics.toml | on unfold, then 4 s / on Enter |
| Robot description (2026-10-29): `GET /api/v1/robot-description`, `GET /api/v1/robot-description/files/<path>` | The 3D view's optional model: the URDF's URL, each `[[part]]`'s links, meshes or point and the entity path it resolves to (`null`: not detected), the adapters and computers with their parts, the devices not in the model, the kept descriptions, and the files of the description's folder (the URDF, its meshes), read-only. `present: false` without `<config dir>/robot-description.toml`. Docs: "Robot description" below and docs/robot-description.md | on open, then 5 s / per mesh |
| `POST /api/v1/act` | The Utilities panel ONLY: a dry run, then a person's confirmed run with the dry run's token | on click |
| `POST /api/v1/expect`, `/forget`, `/retire` | The Expected setup card ONLY: one edit of the rig file after a person's confirm | on click |
| `GET /`, `/<file>`, `/<dir>/` | The page in `io_diagnostics/web/`. A path ending in `/` is that directory's `index.html` when it has one (`/dx/` is `dx/index.html`; `/` is the web root's), else **404**; `/dx` with no slash is not redirected. `.base` files (data-ui's dashboards) are `text/plain; charset=utf-8`. Nothing outside the web root is served (a `..`, an encoded one, a symlink out: **404**) | — |

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
- **A read answers from the published view** (2026-11-04, docs/peps/0003-reads-answer-from-a-published-view.md). A running host builds its one view on a
  thread of its own, on each change and at least every 2 s, and every `GET` answers from the latest, at once: no read builds one, waits for one or waits for
  a pass over `/proc`. So an answer is a moment old (0.25 s behind a change on a quiet machine, more under load: a build never takes more than a fifth of
  a core). `clock.now` of `/api/v1/state` is the host's clock at the answer. A `POST` that changes what a read shows (`/act`, `/expect`, `/forget`,
  `/retire`, `/config/*`) publishes before it answers, so a person's write is in their next read. Before its first view a host answers
  `/api/v1/health` and the page itself; every other read waits for that view (seconds, once).

## The plugin environment

Besides `IO_DIAGNOSTICS_DEVICE`, `_DEVICE_PATH`, `_NOW` and `_SCHEMA`, every plugin invocation (`describe`, `diagnose`, `probe`, `watch`,
`follow`, `act`, `stop`) gets `IO_DIAGNOSTICS_RECORD_VERSION` (`"0.2"`: the record revision this host understands). A driver writes the
0.2 record kinds and describe fields only when it is set (docs/state-config-v1.md section 10); a watch also gets `IO_DIAGNOSTICS_FRAME_DIR`.

## `GET /api/v1/state`

```json
{
  "api": "io-diagnostics/1",
  "schema": "diagnostics-record/0",
  "record_version": "0.2",
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
  "chip": {"label": "IO 5 ⚠", "label_guide": "IO 00 ⚠", "icon": "attention", "status": "error", "title": "IO diagnostics",
           "menu": [{"text": "5 need you · 10 devices"}, {"sep": true}, "..."]},
  "tree": {"...": "node, see below; children nested"},
  "recent": [{"...": "event, see Activity"}],
  "expected_setup": {"file": "/home/bam/.config/io-diagnostics/expected.toml", "editable": true, "note": "",
                     "rigs": [{"name": "1-DOF test rack", "path": "host/rig-1-dof-test-rack", "devices": 4,
                               "members": ["host/usb-1-3/fdcanusb-272813ef"]}],
                     "retired": [{"name": "fdcanusb 28B3A25D", "hardware_id": "usb:0483:5740:28B3A25D", "serial": "",
                                  "date": "2026-10-23", "reason": "frames never reached the bus (2026-10-22) ...",
                                  "path": null}]},
  "composition": {"file": "/home/bam/.config/io-diagnostics/expected.toml", "problems": [],
                  "composites": [{"id": "lab/robot-1/arm", "path": "host/composite-lab-robot-1-arm", "kind": "arm", "title": "Arm 1",
                                  "parent": "host/composite-lab-robot-1", "plugin": "state_arm", "batch": "all", "source": null,
                                  "expects": {"tracking_error_rev": 0.5}, "level": "error",
                                  "children": [{"ref": "usb:0483:5740:272813EF#can/1", "id": "usb:0483:5740:272813EF#can/1", "kind": "entity",
                                                "path": "host/usb1/port-1-3/usb-1-3/can/1", "name": "CAN 1", "present": true,
                                                "level": "error", "message": "the driver is in fault"}],
                                  "members": ["host/usb1/port-1-3/usb-1-3/can/1"], "leaves": 1}]},
  "runs": {"active": 0, "total": 0, "items": []}
}
```

- `composition` (docs/composition-v1.md, 2026-10-24): the composites of the expected setup in tree order, each with its children as the
  file lists them (`ref`, the entity `path` now or `null`, `present`, the child's `level` and `message`), the `members` (paths of the
  present leaf children, which a page nests under the composite's row) and `leaves` (how many entities it contains, recursively).
  `problems` lists what is wrong with the composite tables; while any exists `composites` is empty and the Computer carries a
  `composition` check (`unknown`, `CompositionInvalid`). A composite's row is `kind` `"composite"`, `edge` `"via"` to its parent
  composite (or the Computer), its `id_text` the file's own word for it ("arm"); its check `<title>: children` is the worst child's
  level, with `caused_by` naming the children that report a problem.

- `record_version`: the additive revision of the record contract this host validates against (`"0.2"`: identity
  `model_id`, danger `moves`, the standard action names, and the Event as the log record). `schema` stays `"diagnostics-record/0"`.
- `fixture` is `null` in live mode. In fixture mode the host replays a recorded scenario through the same code path
  as live plugins; `clock` runs at 1× from the frame's clock (plus `--offset`).
- `host.sources`: which OS skeleton sources were used and which were skipped (with the reason), so a page can say
  "PCI discovery not available" instead of implying there is no PCI. A `"seen devices"` entry appears only when
  the remembered-devices file could not be read or written.
- **Expected, not connected** (record v0.1; Zach, 2026-10-24: a missing thing is a diagnostic on its parent, never a
  ghost row): a device a plugin's `describe` lists in `expected` (`[{hardware_id, name, last_seen, source}]`), or one
  the expected setup lists (retired ones excepted), that is not on USB now. Each one is a `warn` check
  `<name>: presence` with `code` `"ExpectedAbsent"` **on the Computer** (the `host` row), which makes the Computer's own
  level `warn`. Its `message` says what the kernel saw this boot:
  `"fdcanusb 28B3A25D expected (rack_inventory), not on USB · last seen 2026-10-21 18:24 · the kernel saw no plug
  event since boot (2026-10-22 07:00): try a data cable or another port"`, or `"… plugged in at 09:00:01 on 1-5,
  gone at 09:30:00"`, or that the kernel log could not be read. Its values are `what_to_check`, `kernel_evidence`
  (`never | left | unknown`), `expected_by`, `last_seen` and `hardware_id`. Only `usb:<vid>:<pid>:<serial>` ids are
  judged. There is no `host/expected` group and no row; to stop expecting one, `io-diagnostics forget --hardware-id`.
- **Recently seen** (2026-10-23): a USB device this host has only SEEN a plugin diagnose (kept in
  `~/.local/state/io-diagnostics/seen.json`, live mode only), expected by nothing above, that is not on USB now and
  was last seen within a day. One `ok` check named `Recently seen` on the Computer, whose `recently_seen` value lists
  them: `"fdcanusb FDA81D29 (seen 2026-10-23 14:26) · plugged in at 14:25:53 on 1-1, gone at 14:26:08"`. Never a
  warning and never a fault: its unplug is an `Unplugged` activity event. WHY (Zach, 2026-10-23): fdcanusb FDA81D29,
  plugged for 15 s, confirmed an `ExpectedAbsent` WARN from the memory alone.
- **A row exists for what was heard from** (2026-10-24): a plugin reports an entity for a board that answered; one that
  never answered has no row, and its bus says so (moteus: `2 of 3 expected boards answer · CAN 15 silent`, `warn`
  `ExpectedBoardsSilent`, `caused_by` the silent boards). A board that answered and then went quiet keeps its row until a
  person removes it (`POST /api/v1/forget` on the row, "Remove from bus"); the row is `presence.state` `"stale"`, and its
  bus gains the host's `<bus>: boards stopped answering` (`BoardsStoppedAnswering`, `caused_by` those boards, so the
  boards count once). A removed board that answers again comes back. The host keeps the last-known entities of every
  (plugin, device) in `~/.local/state/io-diagnostics/tree.json` (live mode only) and restores them at start with no
  health: `presence.restored` is true until a run that really looked reports them (a blocked, failed or timed-out run
  never removes a row and never marks one dead). `docs/roster` is `io_diagnostics/roster.py`.
- `summary` is the **root's advisory rollup** (the chip's question: "does anything need me?"):
  - `level`: the worst independent cause anywhere (`null` = nothing reports). It is `rollup.level` of the root, not
    the host row's `level`: with the computer plugin the host has records of its own and its row shows them, while
    the Computer's checks count here as one more cause beside the tree, never over it.
  - `counts`: independent causes per level; knock-on problems under a failed ancestor are not counted, nor is a
    composite whose verdict names, in `caused_by`, members that report the problem themselves (the rig row's
    "missing: CAN 15" and CAN 15's own report are one cause, CAN 15).
  - `attention`: every independent cause that is not `ok`, worst first; `device` is the nearest ancestor of kind
    `device` (`null` if none) so a page can say "fdcanusb 28B3A25D › 28B3A25D CAN bus".
  - `devices`: physical devices (USB devices and hubs, PCI functions; not their tty / video / NIC nodes); `entities`: all rows.
- `chip`: the top-bar chip as the AppIndicator renders it (`contract/indicator.py` model: `label`, `icon`
  `"ok" | "attention"`, `menu` items `{"text", "enabled"?, "children"?, "action"?}` or `{"sep": true}`; `status`, the
  top bar's dot since 2026-10-23: `"error"` over `"warn"` over `"stale"` (stale or unknown), `"ok"` = no dot). The web page
  may draw its own chip from `summary`; the `menu` is what the tray shows.
- `recent`: the 6 newest events (same shape as in `/api/v1/activity`).
- `runs` (2026-10-24): the newest 30 runs (docs/runs-v1.md: `id`, `label`, `state`, `active`, `elapsed_s`, `exit_code`,
  `interactive`...), newest first, with the count of `active` ones and the `total` the host holds. A real utility is a
  run on a pseudo-terminal; the page draws its tabs from this list.
- `expected_setup` (2026-10-23): the person's rig manifest (docs/expected-setup.md): its `file` (`null` = none on
  this host: a fixture or a fake machine without `--expected`), whether the panel may `editable`-ly rewrite it, the
  `note` saying why it does not read, its `rigs` (each with its rig row's `path` and `members`: the rows of its devices that are here) and its `retired` devices (`path`
  = the row of one that is plugged in again, else `null`).
- **Expected setup** rows: one composite row per rig, `host/rig-<name>` (`kind` `"rig"`, `icon` `"group"`), first
  under the host. Its `name` self-reports the counts ("1-DOF test rack · 2/2 adapters · 6/7 boards · 2/2 cameras");
  its one check `<rig>: expected setup` is `warn` `ExpectedMissing` naming what is missing (with `what_to_check`),
  `unknown` `ExpectedNotChecked` when an expected node's check could not run, else `ok`. An unexpected extra node, a
  model that differs and "its boards answer on fdcanusb 0607623A now" (an absent adapter's boards on another one)
  are notes in its message and values, never a level. A retired device that is plugged in again carries a `warn`
  check `<name>: retired` (`RetiredDevice`, "retired 2026-10-23: <reason>"). An expected device matched only by a
  driver's serial (the RealSense SDK serial) that is not here gets a Computer check `<name>: presence` (`ExpectedAbsent`,
  "no connected device reports serial …"); one with a USB `hardware_id` gets the kernel-evidence check above. A node
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
  "checks": 2, "not_run": 0, "actions": 1, "faults": 0, "collapsed": false,
  "presence": {"state": "heard", "age_s": 0.0, "restored": false, "text": ""}, "claimed": true,
  "usb": {"version": "2.10", "speed_mbps": 480},
  "rate": {"received_per_s": 0.4967, "expected_per_s": 0.5, "window_s": 30.0},
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
| `level`, `message` | **What the row shows** (contract `summarize` → `Summary.level` / `.message`). An entity with records of its own is a black box: this is ITS self-report (the worst effective level of its own checks). A pure grouping node (`own.level` = `null`: the bus groups, and the host while no plugin diagnoses it) shows its rollup instead. **The host never overrides a self-report.** With the computer plugin the host row has records of its own (the Computer's checks, its `USB/PCIe links` line): its `level` is then the Computer's own report, judged as a leaf beside its children, so a full disk never explains a dead CAN bus (`tree.HOST_SELF`). |
| `own` | The self-report alone; `own.level` = `null` means a pure grouping node. |
| `rollup` | Advisory "worst below" (Redfish `HealthRollup`): the worst *independent cause* at or below this row, where it is, its counts per level, and `knock_on` = `[{"path", "cause"}]` problems collapsed into an ancestor's failure. `level` = `"ok"` when everything below reports OK; `null` when nothing below reports. |
| `hint` | Set when `rollup.level` is worse than `level` (rank order): the "a child is in ERROR" hint a row draws beside its own status. `null` otherwise. Example: the spider self-reports WARN with an ERROR hint from leg 3. |
| `knock_on_of` | This row's own problem is explained by another row: a failed ancestor (one dead bus is one cause), or, for a composite whose checks name their members in `caused_by` (record v0.1 addendum), the root cause of the member it names (the rig row "missing: CAN 15" → CAN 15's row; a spider at ERROR → its failed leg). Draw it subdued; it is not counted in `summary`. `null` when the row is an independent cause or OK. |
| `stale` | `own.level` is `"stale"`: the host has not received this entity's periodic record for `max(3 × expected_period_s, 5 s)` on its own receive clock. |
| `connected` | `false` = not connected → render fully greyed: an empty USB port, or an entity whose latest record says `mode: "offline"` (a deliberate goodbye). |
| `hardware_id`, `identity` | Repeatable identifier (`usb:<vid>:<pid>:<serial>` for USB) and the descriptive `manufacturer / model / serial_number / hw_version / sw_version / model_id` (any subset; parts carry none). `model_id` (record v0.1) is the robotparts part id, e.g. `"mjbots-moteus-c1"`, a plain string a page may link to robotparts.dev. |
| `owner` | Who registered the entity and would run its actions: a plugin name, or `io_facts.usb` / `io_diagnostics` for OS rows. |
| `id_text` | Short text for the id column: a device's serial (a plugin may replace it, e.g. the RealSense SDK serial), a port's link speed or `empty`, a bus's / group's / the host's physical device count. |
| `last_received`, `age_s` | When the row was last heard from: the newest `received` among its PERIODIC own records (the ones STALE is judged on), else among all its own records (`null` = no records); age on the host clock. |
| `expected_period_s`, `stale_after_s` | Shortest declared period among the row's own periodic records and its STALE threshold (`null` = event-driven: never stale). |
| `checks`, `actions`, `faults` | Counts of own status records, registered actions, and the host's open faults (pending or confirmed) on this row; details via `/api/v1/node`. `checks` counts every row the entity lists, the ones that did not run included. |
| `not_run` | Additive (2026-11-04). How many of those `checks` are a stock check that did NOT RUN (the check's `not_run: true`, below). They are listed but say nothing about the entity: none sets `level`, `own`, `rollup` or `hint`, none is in `summary.counts` or `summary.attention`, and none counts toward `last_received`, `expected_period_s`, `presence` or `connected`. A row whose only checks did not run therefore has `own.level` `null`, like a pure grouping node (it used to read `unknown`). |
| `collapsed` | `true` for a group a page starts closed. Always `false` today (the `host/recently-seen` group is gone: those devices are a check on the Computer). |
| `presence` | The Claude-sidebar dot (2026-10-24), neutral, never a level: `{state, age_s, restored, text}` with `state` `"heard"` (filled: heard within its expected period), `"stale"` (hollow ring: known, not heard lately; `text` "last heard 6 min ago", `restored` true while it is only the last session's row) or `"live"` (pulsing: a follower is streaming its data). `null` for structure with nothing to hear from (ports, groups, rig rows) and for a row with no records. |
| `usb` | A USB device row's own `version` (its bcdUSB as sysfs shows it, `"2.10"`) and the `speed_mbps` of the link it runs at, so a page can say "USB 2.1" instead of "480 Mb/s" (`web/usb.js`; the raw speed stays in the tooltip). `null` for any other row. |
| `rate` | How fast the row's own PERIODIC records actually arrive, against what they promised (the Table's Rate column: "0.20 /s · expected 0.20"). `received_per_s` and `expected_per_s` are sums over the row's periodic records: arrivals per second over the last `max(30 s, 6 × period)` (at most the history window), and `1 / expected_period_s` each, so a board with three 2 s checks reads 1.5 against 1.5. A record is judged once the host has watched it for 3 of its periods; until then `received_per_s` and `window_s` are `null` (measuring) and `expected_per_s` is what is promised. A page shows a quiet note when `received_per_s` is under half of `expected_per_s`; it is not a level (staleness is the health signal). `null` for a row with no periodic record of its own (event-driven only, structure). Counted at the host's receive time (`history.py` `rates()`), so a replay re-stamps arrivals like everything else. |
| `claimed` | Whether some driver plugin claims the row. An OS device nobody claims (a keyboard, a mouse, a webcam, a NIC) is `false`; the page hides those by default (the Visibility control). Plugin rows, the expected setup's devices and all structure are `true`. |
| `children` | Child nodes, ordered: ports and groups by name, plugin entities in the order the plugin sent them. |

## `GET /api/v1/node?path=<entity path>`

(The entity page's additions, `badge`, `heard`, `config`, `watch`, `streams`, `tabs` and each action's `last_run`, are
specified in `docs/state-config-v1.md`; `history`'s `state: true` marks a series that carries State only: no levels, never in a health bar.)

```json
{
  "node": {"...": "the node as in /state, with children replaced by a list of child paths"},
  "ancestors": [{"path": "host", "name": "bam-GPU", "edge": null}, {"path": "host/usb1", "name": "USB bus 1 · 480 Mb/s", "edge": "via"}],
  "checks": [
    {"name": "CAN 15: comms", "level": "ok", "effective_level": "stale", "stale": true, "not_run": false,
     "message": "answers · 36.5 V · stopped", "code": "", "mode": null, "source": "moteus_servo (rack_inventory, real)",
     "hardware_id": "moteus 22fb62f0 · ABoAQVQwUBUgOTNO", "stamp": 1761096101.0, "received": 1761096101.0,
     "received_iso": "2026-10-21T18:21:41-07:00", "age_s": 199.0, "expected_period_s": 180.0, "stale_after_s": 540.0,
     "uptime_s": null, "caused_by": [],
     "values": [{"key": "voltage", "value": "36.5 V"}],
     "measurements": [{"key": "voltage", "value": 36.5, "unit": "V", "text": "36.5 V", "level": "ok",
                       "warn_low": null, "warn_high": null, "error_low": null, "error_high": null, "stamp": null}]}
  ],
  "actions": [
    {"name": "reset", "title": "Reset…", "description": "...", "danger": "interrupts", "scope": "node", "kind": "changes",
     "confirm": "Reboot CAN 15 (serial ABoA...)? ...",
     "preconditions": [{"check": "port_free", "text": "Port not held by another process", "state": "fail",
                        "detail": "port held by 640623 tview", "resolves": "blocks"}],
     "state": "blocked", "reason": "blocked: port held by 640623 tview", "executable": false, "owner": "moteus_servo",
     "second_confirm": "", "confirmations": [],
     "last_run": {"id": "r7", "state": "done", "active": false, "started": 1761096000.0, "started_iso": "2026-10-21T18:20:00-07:00",
                  "ended": 1761096012.0, "ended_iso": "2026-10-21T18:20:12-07:00", "exit_code": 0, "elapsed_s": 12.0}}
  ],
  "faults": [{"...": "fault, see Activity"}],
  "events": [{"...": "event for this path or below, newest first, at most 20"}],
  "badge": {"kind": "can", "text": "CAN 1 · fdcanusb FDA81D29", "usb": {"speed_mbps": 12, "version": "2.00"}},
  "heard": {"received": 1761095510.0, "age_s": 1.2},
  "config": [{"key": "servo.max_current_A", "value": 20.0, "unit": "A", "group": "servo", "expected": 25.0,
              "expected_source": "deployment record rack-2026-10-21", "differs": true, "stamp": 1761095500.0, "age_s": 10.0, "source": "",
              "writable": true, "type": "float", "range": [0.0, 30.0], "choices": [], "default": 20.0, "stored": 20.0, "safety": true,
              "unsaved": false, "edit": null}],
  "editable": true,
  "editing": {"active": false, "grace_s": 10.0, "board": ""},
  "watch": {"supported": true, "state": "idle", "reason": "", "holders": [], "pid": null, "since": null, "grace_s": 10.0,
            "period_s": 0.1, "gates": ["port_free"]},
  "streams": [{"name": "color", "kind": "image", "title": "Color", "colormap": null, "bands": null, "preview_band": null,
               "width": 640, "height": 480, "seq": 41, "stamp": 1761095510.3, "band": null, "age_s": 0.2, "fps": 5.0}],
  "tabs": [{"id": "processes", "title": "Processes", "kind": "table", "status": "Processes", "values": ["top_cpu", "top_memory"]}],
  "entity": {"schema": "diagnostics-record/0", "record": "entity", "path": "...", "...": "contract to_dict(Entity)"},
  "setup": {"role": "device", "file": "/home/bam/.config/io-diagnostics/expected.toml", "note": "", "editable": true,
            "rigs": ["1-DOF test rack"], "rig": "1-DOF test rack", "expected": true, "retired": null,
            "nodes_expected": ["can/1", "can/14", "can/15"], "node_notes": {"can/15": "being replaced"},
            "nodes_here": ["can/1", "can/14"],
            "can": {"expect": true, "forget": false, "retire": true},
            "confirm": {"expect": "Expect fdcanusb 272813EF in 1-DOF test rack with can/1, can/14 (can/15 no longer expected)?",
                        "retire": "Retire fdcanusb 272813EF? It leaves 1-DOF test rack. Plugged in again, ..."},
            "needs_rig": false},
  "composite": null
}
```

- `composite`: `null` for every entity but a composite row, where it is that composite's object as `GET /api/v1/state`
  `composition.composites[]` carries it. A composite's `actions` hold its plugin's own and, for each standard action that all (or, with
  `batch = "having"`, enough) of its leaves offer, one `all:<action>` (`scope` `"composite"`, a title that says the count, `batch`:
  `{"action", "n", "groups", "skipped"}`); `POST /api/v1/act` takes it like any action ("Composites" below).

- `checks[].not_run` (additive, 2026-11-04): `true` for a stock check that did NOT RUN (source `robotdx.diagnostics`, level `unknown`:
  codes `NoSource`, `NoState`, `NotStreaming`, `NoDecoder`, `NotEnoughHistory`, `NumpyMissing`, `DiagnosticRaised`, or the library's own
  not_run verdict). It says nothing about the entity: the host leaves it out of the row's `level`, every `rollup`, `summary` and the
  health bars, and a page lists it apart from the checks that ran, grey, with its own reason, never as OK. Its `level` /
  `effective_level` still read `unknown` (or `stale` once the beat stops) so a viewer that ignores the flag still never sees it as OK.
  A driver's own unknown (`BrsNotTested`, a config that could not be read) is not one: it keeps `not_run: false` and rolls up.
  Before this field a not-run row set its entity's level to `unknown`.
- `checks`: the row's own `DiagnosticStatus` records, latest per check name, worst first. `level` is what the
  producer said; `effective_level` is what to act on now (`"stale"` once overdue). Values and measurements are
  the producer's; `measurements[].level` comes from its limits (contract `Measurement.level`), `text` is the stock
  rendering (`"36.5 V"`). A check named `firmware` with `code` `"UpdateAvailable"` and values
  `installed_version` / `latest_version` is the update-available convention; a check whose name ends in `config`
  holds a read-only config view.
- `caused_by` (record v0.1 addendum, 2026-10-23): the entity paths whose own reports caused this check's verdict,
  set by a composite or rollup check: a rig row's `<rig>: expected setup` names the rows that report what is missing
  (a board's bus, or the Computer for an absent device), the Computer's `USB/PCIe links` line names the port / PCIe / camera
  rows that report each unhappy link, a plugin's composite (a spider) names its failed legs. `[]` = it judges on its
  own. When every named row reports a problem, the row is their consequence: `knock_on_of` names the cause and
  `summary` does not count it; the check's own `level` and `message` are unchanged.
- Host-authored checks appear here too, with `source` `"io_diagnostics.runner"` (a plugin that crashed, hung or sent
  malformed lines: `code` `PluginCrashed` / `PluginTimeout` / `PluginNotStarted` / `PluginOutputMalformed`) or
  `"io_facts.usb"` (presence, port link).
- `actions`: registered by the entity's owner, with each precondition evaluated **passively** by the host:
  - each precondition's `state` is `"pass"`, `"fail"`, `"unknown"` or `"waiting"` (held by this host's own check);
  - `port_free` (no process holds the device's tty / by-id), `bus_lock_free` (rack bus lock
    `/home/bam/.cache/rack-commissioning/bus.lock`, read from `/proc/locks`, never taken; only an exclusive `WRITE`
    holder, a rack experiment, blocks it: probes hold it shared), `camera_free`
    (no process holds its video nodes, no viewer is open), `board_answers` (the entity has a fresh own record whose code is not `NoReply` / `NoAck` /
    `PortBusy`), `update_available` (its `firmware` check says `UpdateAvailable`).
  - **The holders are read in one pass, off the request (2026-11-04, `io_facts/open_files.py`).** One process walks `/proc` every few seconds while a page looks
    and answers every gate of every device; `/node` reads the last answer and never waits, so a precondition here can be a few seconds old. A gate that has
    no answer yet is `unknown` with the detail `being read`. What acts on a gate (a watch starting, a configuration write) asks for a pass no older than
    2 s, and a run is checked again on the bus before anything is sent.
  - `state`: `"nothing_to_do"` (`update_available` fails: "up to date", whatever else holds, since a free port
    would not make the button useful), else `"blocked"` (a precondition `resolves` `"blocks"`), else `"waiting"` (one
    is held by this host's own running check, below), else `"ready"`. `reason` is the text to show beside a disabled
    button. **Since 2026-10-29 an action's `state` is never `"unknown"`** (it was "can't tell: ..."): see below.
  - **Actions: two states (2026-10-29, addition).** Zach: "I am confused by the can't tell: does that mean it can block
    or not block? The utilities menu should just have 2 states." Every precondition carries **`resolves`**, what it does
    to its action, and the `state` above is made from it alone:
    - `"ready"` (`state` `pass`), `"blocks"` (`fail`), `"waiting"` (`waiting`);
    - `"confirm"`: a precondition only a person can check (`joint_free`, `joint_clear`, `joints_clear`, `target_in_view`):
      never blocks; the action's new **`confirmations`** lists their texts, which the page's Run page states and the
      person confirms by pressing Run (the host's own `device_free` second confirmation stays in `second_confirm`);
    - `"at_run"`: one the host cannot read now (`state` `unknown`) and the driver checks on the bus right before it
      sends anything (it refuses with nothing sent): `board_idle` without a fresh State, `config_interlock`,
      `new_id_free`, `boards_idle`; never blocks;
    - an unknown `config_recorded` `"blocks"` with the detail "no recorded configuration for this board": the action
      needs a file that nobody said exists.
    New host checks: `board_idle` (the entity's latest fresh State, a watch's or a probe's `state` record: `mode` 0 and
    `fault` 0 pass, anything else fails with the mode or fault in words), `source_id_unique` (the driver's id check:
    `IdCollision` / `PossibleIdCollision` fail, else the board answering passes). **A driver answers a precondition only it
    can read** with a status value `precondition.<check>` on any of the entity's own statuses, `"pass: <why>"` or
    `"fail: <why>"` (moteus_servo: `precondition.config_recorded`, its config dir); the host reads it for any check that is
    not an OS gate (`port_free`, `bus_lock_free`, `camera_free`).
  - **A port held by a RobotDx check** (2026-10-29, "blocked: held by 1652054 python3": a routine `diagnose` of this host or
    of the other channel's, measured holding each fdcanusb a few seconds every 5 min): `port_free` is `"waiting"` with "this
    host's own check is running (a few s): ..." when every holder is one of this host's own processes, or with "RobotDx (tray,
    port 8822) has it open (moteus_servo.diagnostics diagnose, pid N) (a few s)" when every other holder was started by
    another RobotDx host (its ancestor runs `io_diagnostics/cli.py`). Any other holder is `"fail"`, named in plain words
    ("tview, moteus's console (pid N): close it to free the port"). This host's own watch never counts (it yields, as before).
  - **The rack bus lock held by this host itself** (round 3 brief item 10): when every holder of the lock belongs to
    one of THIS host's own running plugin or check processes (its session: `os.getsid(holder)` is one the host started,
    so `uv run` → python counts), `bus_lock_free` is `"waiting"` with the detail `"this host's own check is running (a
    few s): moteus_servo diagnose usb:1-3, 2 s so far"`, and the action is `"waiting"`, never `"blocked"`: it frees by
    itself, and the page greys it, never in a warning colour. Only a foreign holder is `"fail"` / `"blocked"`, with
    `"blocked by pid 4187 (python3 -m rack_session)"` (its command line from `/proc/<pid>/cmdline`).
  - **The host's own utilities** (docs/usb-reset.md): a USB device row and a USB port row also carry `reset_port` ("Reset USB port",
    `interrupts`, `owner` `io_diagnostics`) whatever driver owns the device. It is added to the entity's own actions unless its driver
    registered that name itself (then the driver's wins) or opted out in `describe` (`"opt_out": ["reset_port"]`; a hub is not
    offered either while a device behind it opted out). Its preconditions are evaluated by the host: `device_present` (something is
    plugged in at the port), `no_run_active` (no RobotDx run is active on the device, on anything below it, or on a hub above it;
    compared by tree path), `reset_privilege` (this user may use `usbreset`, else the `authorized` switch, else `uhubctl`; the
    detail says why not) and `device_free` (no other process has the device or anything below it open; this host's own watch /
    follow processes do not count). `device_free` failing never blocks the action: the node's `second_confirm` then holds the
    sentence the second confirmation names ("tview (pid 4242) has /dev/ttyACM0"), `""` for every other action.
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

## `GET /api/v1/activity[?limit=N][&types=<type>,...][&fold=1&channel=&path=&scope=&severity=]`

```json
{
  "clock": {"...": "as in /state"},
  "events": [
    {"stamp": 1761095502.0, "iso": "2026-10-21T18:11:42-07:00", "time": "18:11:42", "code": "Plugged",
     "kind": "plugged", "type": "usb", "id": "usb:1-7:21:add", "provisional": false, "severity": "info",
     "message": "fdcanusb 28B3A25D plugged on 1-5 after 4 flaps on 1-7",
     "path": "host/usb1/port-1-5", "name": "Port 1-5", "hardware_id": "usb:0483:5740:28B3A25D", "port": "1-5",
     "source": "io_facts.journal", "seq": 12, "args": {"port": "1-5", "flaps": 4, "merged": ["usb:1-7:21:add", "usb:1-7:21:remove"]},
     "channel": "activity", "count": 1, "last_stamp": null, "last_iso": null, "last_time": "", "received": 1761095503.4,
     "received_iso": "2026-10-21T18:11:43-07:00", "badges": [], "members": []}
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

- `events`: newest first, `limit` default 200; `types` (comma separated) keeps only events of those types (an
  unknown type → **400**, naming the known ones). `code` is the contract `Event.code`; `kind` its human label
  (`"re-enumerated"`, `"plugging in"`); `severity` is `debug | info | warn | error | fatal` (record v0.2); `port` is the USB port when known, else `""`.
  The v0.2 fields: `received` / `received_iso` (when the host first saw it: `stamp` is the source's clock),
  `count` / `last_stamp` / `last_iso` / `last_time` (a coalesced repeat: `count` identical events, `stamp` the first,
  `last_stamp` the latest; `count` 1 and `last_stamp` `null` otherwise), `channel` (`activity` here, `raw` in the log's
  Raw channel), and `badges` / `members` (empty here; the log model below fills them). The events are the host's
  whole kept history: they survive a restart of the host and a reboot of the machine (`io_diagnostics/eventlog.py`:
  append-only JSONL, one file per boot, `~/.local/state/io-diagnostics/log/`, about 20 MB in all; `$IO_DIAGNOSTICS_STATE_DIR` for a replay).
  Every event is **typed** (`type`), so a page can show "a complete view of all the port activity" (Zach, 2026-10-23)
  and filter it. Only passive sources feed it (the kernel log, udev, sysfs, the host's own memory); nothing opens a device.

  | `type` | `code`s | from | `args` beside `port` |
  |---|---|---|---|
  | `usb` | `Plugging`, `Unplugging` (**provisional**), `Plugged`, `Unplugged`, `ReEnumerated` (dropped and came back), `Flapped` (enumerated, dropped before settling 3 s, never came back) | udev at once; the kernel log (`io_facts.journal`'s coalescer) confirms | `product`, `identity`, `flaps`, `flap_ports`, `gap_s`, `merged` (the raw enumerations it folds); provisional: `product`, `serial`, `devnum` |
  | `link` | `LinkSpeed` (info: the speed each settled enumeration linked at), `LinkDowngraded` (warn: a USB 3 device, sysfs `version` ≥ 3, linked below 5 Gb/s, or one that linked at USB 3 earlier this boot) | the kernel's `new <speed> USB device` line, sysfs | `speed_mbps`, `identity`; `usb_version` or `before_mbps` |
  | `usb_error` | `UsbDescriptorReadError`, `UsbNotAcceptingAddress`, `UsbReset`, `UsbConfigFailed` (warn); `UsbOverCurrent`, `UsbEnumerationFailed`, `UsbCannotEnable`, `UsbResetFailed` (error); `UsbPortDisabled` ("disabled by hub (EMI?)", warn) | the kernel log (hub.c's words, `io_facts.kernel_log`); a burst on one port within 5 s is one event | `count`, `kernel` (the kernel's line) |
  | `driver` | `DriverBound`, `DriverUnbound`: "cdc_acm bound on 1-1 → /dev/ttyACM1", "uvcvideo bound on 1-1 → /dev/video0, /dev/video1", "uvcvideo unbound from 1-1 (/dev/video0 gone)" | udev (interface bind / unbind, tty / video4linux / net nodes) and the kernel log (`cdc_acm`, `Found UVC`, USB serial converters); one event per enumeration and driver | `driver`, `nodes` |
  | `network` | `NetCarrierUp` (info), `NetCarrierDown` (warn) | sysfs `carrier`, compared on every refresh | `interface`, `carrier_changes` |
  | `pcie` | `PcieAerError` (warn when Corrected, error when Uncorrected / Fatal) | the kernel log (`AER:`, `PCIe Bus Error`); one per burst | `device` (PCI address), `driver`, `count`, `kernel` |
  | `level` | `LevelChanged`: "<check>: ok → error (Code: message)", stale included | the history store (each status's effective level) | `status`, `from`, `to`, `status_code` |
  | `fault` | `FaultConfirmed`, `FaultHealed`, `FaultCleared` | the host's `FaultManager` | `fault`, `occurrence` |
  | `config` | `ConfigSet`, `ConfigRejected` (warn), `ConfigPersisted`, `ConfigReset`, `ConfigEditStarted`, `ConfigEditEnded`: "CAN 1 · fdcanusb 272813EF: servo.max_current_A set 20 → 15: accepted by the board" | the host's config editor (`io_diagnostics/config_edit.py`): every write of an edit session, accepted or not, and every refusal | `by` (`page`), `key`, `old`, `asked`, `kept`, `accepted`, `run_id`, `refused`, `session` |
  | `boot` | `Booted`, `ShutDown` | the kernel log, `journalctl --list-boots` | `devices` |
  | `run` | `RunStarted`, `RunEnded` (2026-10-24): one pair per real utility run, linked by `args.run_id`; `RunEnded` is warn when stopped or timed out, error when failed (docs/runs-v1.md section 5) | the host (`io_diagnostics.runs`) | `run_id`, `action`, `node`, `plugin`, `state`, `exit_code` |
  | `plugin` | anything a plugin sends, e.g. `NoAck`, `PortHeld`, and a probing driver's `Found`, `Probed`, `ProbeBlocked` (docs/probe-on-connect.md) | the plugin | as sent |

- `id` is the event's **stable** id; `provisional` is `true` while only udev has said it. A plug is
  `usb:<port>:<devnum>:add`, an unplug `...:remove` (the kernel's device number, which udev reports as `DEVNUM`), so
  the provisional `Plugging` and the `Plugged` (or `ReEnumerated`, `Flapped`) the kernel log settles it into are ONE
  event: the same `id` goes `provisional: true → false`, its `code`, `message` and `stamp` relabelled in place, and
  the log never shows both. A notice folding several enumerations (flaps) takes the first one's id; the others' rows
  end. A link event is `<its enumeration>:link`, a driver event `<its enumeration>/<driver>:bind|unbind`, a level
  change `level:<path>|<check>|<at>|<to>`, a plugin's `<source>#<seq>`. A page keys rows on `id` and replaces a row
  whose `id` comes again. Latency (measured 2026-10-23 with a fake udev stream, uevent → an `/api/v1/events`
  client): a provisional plug or unplug arrives in 0.10 s (the stream's 0.1 s coalescing); the kernel log confirms a
  plug in place 3.1 s after it (the settle window) and an unplug 10.1 s after it (the return window).
- `faults`: every fault the host remembers (`pending → confirmed → healed`, or `cleared`), contract `Fault` fields
  plus `*_iso` twins and the entity `name`. WARN confirms on its 3rd consecutive failing report, ERROR at once,
  3 passes heal (`contract.record.DEFAULT_DEBOUNCE`).
- `link_health`: "the computer diagnoses its own wires", one row per (edge, metric): USB ports (enumerations in
  the last hour, link speed) and every entity of kind `bus` (its own checks, and the age of its last report).
  `level` uses the same vocabulary as rows.

### The Activity log model: `fold=1` (record v0.2, 2026-10-24; `docs/activity-log-v1.md`)

`fold=1` answers the Activity view's model instead of the raw event list: the same events, folded into the rows a
person reads. It is a pure function of the stored events (`io_diagnostics/logview.py`), so a restart gives the same rows.

```json
{
  "clock": {"...": "as in /state"},
  "events": [
    {"stamp": 1761095504.0, "iso": "2026-10-21T18:11:44-07:00", "time": "18:11:44", "code": "BusErrorPassive",
     "kind": "bus error passive", "type": "plugin", "id": "moteus_servo#1@1761095504.000", "provisional": false,
     "severity": "warn", "message": "28B3A25D bus error-passive: nobody ACKs its frames", "path": "host/usb1/port-1-5/usb-1-5/can",
     "name": "CAN bus", "hardware_id": "", "port": "", "source": "moteus_servo", "seq": 1, "args": {"serial": "28B3A25D"},
     "channel": "activity", "count": 1, "last_stamp": null, "last_iso": null, "last_time": "", "received": 1761095504.6,
     "received_iso": "2026-10-21T18:11:44-07:00",
     "badges": [{"id": "fault:FaultConfirmed:host/usb1/port-1-5/usb-1-5/can|NoAck|1761095504.000", "code": "FaultConfirmed", "label": "fault confirmed", "stamp": 1761095504.0,
                 "severity": "error", "fault": "NoAck", "occurrence": 1, "state": "confirmed", "ended": null,
                 "message": "NoAck confirmed on host/usb1/port-1-5/usb-1-5/can: error-passive", "time": "18:11:44", "ended_time": ""}],
     "members": [{"id": "fault:FaultConfirmed:host/usb1/port-1-5/usb-1-5/can|NoAck|1761095504.000", "code": "FaultConfirmed", "severity": "error", "message": "NoAck confirmed",
                  "path": "host/usb1/port-1-5/usb-1-5/can", "name": "CAN bus", "stamp": 1761095504.0, "time": "18:11:44",
                  "args": {"fault": "NoAck", "occurrence": 1}}]}
  ],
  "log": {"channel": "activity", "path": "", "name": "", "scope": "children", "severity": "debug", "matching": 17, "shown": 17,
          "persisted": true, "note": ""}
}
```

- **Parameters.** `channel`: `activity` (default: the coded events), `raw` (the uncoded lines: this boot's kernel lines
  about the entity) or `all`. `path` + `scope`: only the log of that entity (**400** for an unknown one): `children`
  (default: the entity and everything below it, plus the rows that name its `hardware_id`), `self`, or `host` (children
  plus every ancestor, so the ports, hubs and controller it hangs off). `severity`: the floor, `debug` (default, every
  row) `info` `warn` `error` `fatal`; the page filters further itself. `types` and `limit` as above (up to 5000);
  `log.matching` counts the rows before `limit`.
- **One fact, one row.**
  - A fault's `FaultConfirmed` rides on the producer's own event it repeats (same entity, within 10 s, warn or worse) as a
    `badges[]` entry (`state` `confirmed`, then `healed` / `cleared` with `ended` when that happened) and in `members`.
  - A confirmation with no such event that the host made in its first 30 s is one of the `FoundAtStart` row per host
    process (`path` `host`, `args.faults` lists them): the memory catching up, not something that happened then.
  - Any other confirmation is a row of its own, and `FaultHealed` / `FaultCleared` stay rows (`args.confirmed_row` names the
    row the confirmation landed on).
  - `RunStarted` + `RunEnded` are one `Run` row (`stamp` the start, `last_stamp` the end, `args.state` `running | done |
    failed | stopped | timed_out | interrupted`, `args.run_id`); its output is the Raw channel under it (`web/runs/r4.js`).
  - Identical repeats (same `code`, `source`, `path`, `message`, `severity`, each within 60 s of the last) are one row with
    `count` and `last_stamp`; plugs, boots and the fault lifecycle never merge. Rows sort by `last_stamp`, newest first.
- **Status transitions** (`LevelChanged`): to warn or error, folded into its fault's row (`members`); to stale or unknown,
  a `warn` row only when no plug or unplug on its entity's path in the 2 minutes before explains it; every other
  transition is a `debug` row.
- **Raw rows** have `channel` `raw`, `code` `""`, `kind` `kernel`, `type` `raw`, `source` `kernel`, `path` the device (its
  port's lines are the device's) or the port; they are never stored (the journal keeps them).

## `GET /api/v1/events[?types=<type>,...]` (Server-Sent Events)

A `text/event-stream` (`Cache-Control: no-store`) for as long as the client stays: the page's `new
EventSource("/api/v1/events")`, same-origin and read-only (no CORS header, so another origin cannot read it). `types`
filters as on `/activity` (unknown → **400** before the stream starts); it takes the same `fold`, `channel`, `path`, `scope` and
`severity`, and then streams ROWS: a row is sent again when its `count`, `last_stamp`, `badges`, `members` or a run's
`args.state` changed, and a row can disappear (absorbed by another), so a page re-reads `/activity?fold=1` on a `tick`.

```
retry: 2000
event: hello
data: {"api": "io-diagnostics/1", "revision": 41, "now": 1792790150.2, "latest": "usb:1-1:12:add"}

id: usb:1-1:13:add
event: activity
data: {"...": "one event, exactly as in /api/v1/activity", "provisional": true, "message": "plugging in: fdcanusb FDA81D29 on 1-1…"}

event: tick
data: {"revision": 42, "reason": "udev", "now": 1792790150.9}
```

- `hello` once, on connect: the host's change `revision` and the newest event's `id` (what the page already has from
  `/activity`). Nothing older is replayed: after a reconnect, re-fetch `/api/v1/activity`.
- `activity`: an event that is new, or whose `code`, `provisional`, `message`, `stamp`, `severity` or `path` changed
  (the same `id` confirmed in place), oldest first, among the newest 200.
- `tick`: something a page draws changed (`reason`: `refresh` (every refresh, ~2 s), `udev`, `plugin` (a plugin's
  records, 1 Hz for a follower), `stop`); re-fetch `/state` or the open node on it instead of waiting for the poll.
  Changes within 0.1 s are one diff.
- `: keepalive` comment lines every 15 s while nothing changes. The stream ends when the host stops.

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
     "exclusive": "rack-bus", "kind": "driver", "attach": null,
     "description": "moteus boards",
     "matches": ["usb:0483:5740"],
     "describe": {"name": "moteus_servo", "version": "0.3.0", "schema": "diagnostics-record/0"},
     "jobs": [{"device_id": "usb:1-3", "device_path": "host/usb1/port-1-3/usb-1-3",
               "last_run": {"started": 1761095500.0, "duration_s": 1.2, "outcome": "ok", "returncode": 0,
                            "accepted": 14, "rejected": 0, "problems": [], "rejected_lines": [],
                            "message": "ran in 1.2 s · 14 records"}, "next_due_s": 28.8, "follow": null}]}
  ]
}
```

`outcome` is `ok`, `malformed` (some lines rejected), `crashed`, `timeout` or `not_started`; the same outcome is the
host check on the device row. `follow` is `null` unless the plugin's `describe` declares `follow` (docs/build-plan-v1.md
"Plugin boundary"): its job then runs no diagnose (`last_run` stays `null`, `next_due_s` `null`) and `follow` is
`{"state": "running" | "restarting" | "starting", "pid", "since", "first_started", "restarts", "lines", "records",
"rejected", "period_s", "retry_at", "exit", "stderr"}` (times on the host clock; `exit` how the last process ended,
`stderr` its last stderr line). The device row's `<plugin>: plugin run` check then says "following since 14:20:01
(pid 4242) · every 1 s · 3612 records" (OK, re-said once per period, so a silent follower goes STALE), or ERROR
`PluginCrashed` / `PluginNotStarted` "follow exited 1: ... · restarting in 4 s". `diagnose_args` is what the declaration appends after `diagnose --device <id>`
(e.g. a driver's `--passive`). `describe` is `null` until the plugin's `describe` verb has run (once per enabled
plugin, all in parallel, at the host's first refresh, before the first match); a v0.1 plugin may state
`"record_version": "0.1"` and its `expected` devices there (see "Expected, not connected"). `exclusive` is the
declaration's group (`""` = none): the host never runs two jobs (diagnose or act) of one group at once, across
plugins and across one plugin's devices; a due job whose group is busy waits its turn (oldest due first) and is
never failed for it. Both rack drivers declare `"rack-bus"`, because each takes the rack bus lock with
`flock LOCK_NB` and would otherwise fail its sibling with `BusLockHeld`. `next_due_s` stays `0.0` while a job waits
for its group.

**The on-connect probe** (additive, 2026-10-23; contract: `docs/probe-on-connect.md`): a plugin whose `describe` has
`"probe": {"timeout_s": 30}` (or `true` = its own `timeout_s`) gets `<command> probe --device <id> [diagnose_args]`
ONCE PER CONNECTION in place of that connection's first diagnose: when its device is first matched (plugged in, or
present when the host starts) and again when its kernel device number changes (re-enumeration). The periodic diagnose
(`period_s`) or follow starts `period_s` (follow: its period) after the probe; **a probe never runs on the period**. A
plugin without `probe` keeps diagnose-on-connect. The probe's records are applied exactly like diagnose's; its `last_run`
and `outcome` are the same, and it honours `exclusive`. The host derives NO device-specific event (no found / lost from
diffing entities): the driver writes its own `Found` / `Probed` / `ProbeBlocked` events, and they appear in
`/api/v1/activity` as they are, `type` `plugin`, `source` the plugin's name when the event names none.
- The device row's `<plugin>: plugin run` check reads `probed 15:12:31 · 3.1 s` (OK), or while blocked UNKNOWN with the
  holder's code (`PortBusy` / `BusLockHeld`) and the driver's `ProbeBlocked` sentence (the host words it itself,
  "tview has the port (pid 78183) · RobotDx probes again when it closes", when the driver sent none), plus the value
  `blocked_by`.
- **Blocked** = a status of the probe's records with code `PortBusy` or `BusLockHeld`. Its holders are read from the
  status's `port_holders` / `holders` / `lock_holders` value, else from the sentence after "held by", as `"pid program"`
  entries separated by ", " (the program name is derived from `/proc/<pid>/cmdline` when the plugin says only `python3`).
  The host then probes again **0.5 s after the last holder pid has exited** (it reads `/proc/<pid>/stat` about once a
  second, no subprocess; a zombie or a reused pid counts as gone: measured 1.0-1.3 s from exit to the probe starting) and,
  for holders it cannot watch, on a backoff of 10, 20, 40 s ... never beyond `period_s`. A blocked probe never turns into
  the periodic diagnose; a probe that crashed or timed out is not repeated (its row says so, and the periodic diagnose
  goes on).

**Monitoring Plugins checks** (`kind` `"check"`, round 3: "CLI as the seam"; plugins.d/README.md "Checks"): any
command speaking the Monitoring Plugins API is a diagnostic, no code needed. It has no `matches`; `attach` names its
one entity (`"host"`, a `hardware_id`, or `"rig:<rig name>"`). Its one job has `device_id` `"check:<name>"`,
`device_path` = the entity its result is attached to now, and `last_run` in the same shape (`outcome` `ok` |
`timeout` | `not_started`; `returncode` = the exit code; `accepted` 1). Its result is ONE check on that row, named
after the check, `source` `"io_diagnostics.check"`:

- level from the exit code (§2.4 Plugin Return Codes): `0` ok · `1` warn `CheckWarning` · `2` error `CheckCritical` ·
  `3` unknown `CheckUnknown`; any other code (a signal, 127, 255) is unknown `CheckUnknown`, the guidelines'
  "default catch-all". A run past `timeout_s` (its process group is killed) is unknown `CheckTimeout`, one that cannot
  start unknown `CheckNotStarted`: the check did not finish, so nothing says the service is broken;
- `message` = the first stdout line before `|` (§2 Plugin Output: "SERVICE STATUS: Information text"); later lines
  are the value `long_output`;
- perfdata (§2.6), after `|` on the first line and on later lines, each `'label'=value[UOM];[warn];[crit];[min];[max]`,
  becomes a measurement: `value` (`U` → `null`), its UOM as a UCUM unit by name (`""` → `1`, `s`/`ms`/`us`, `%`,
  `B`/`KB`/`MB`/`TB` → `By`/`kBy`/`MBy`/`TBy`, `c` → `{counter}`; never converted), warn → `warn_low`/`warn_high`, crit →
  `error_low`/`error_high` from the §2.5 range (`10` = 0..10, `10:`, `~:10`, `10:20`; an alert is outside, endpoints
  inside, which is the record's own rule). An `@` range (alert inside) has no limit form and stays a value
  (`<label> warn`); min/max are a value `<label> min..max`; an item that does not parse is listed in
  `perfdata_ignored`. Plus `exit_code`, `command` and the measurement `check duration`;
- `expected_period_s` = `period_s`, so a check the host stopped running goes stale. A check whose entity is not
  here now keeps a row of its own, `host/check-<name>`, with the value `attach: <entity> (not here now)`.

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
- Refused before anything runs: `NoSuchEntity`, `NoPlugin`, `ActionNotRegistered`. The host's own `reset_port` adds `NoDevice`
  (nothing is plugged in at the port), `RunActive` (a RobotDx run is active on it; dry runs too), `NotLive` (a fixture replay or a
  fake machine has no real port) and, from the verb, `NoPrivilege`, `HoldersPresent` and `ConfirmMismatch` (below). Its answer has
  `registered_by: "builtin"` and `plugin: "io_diagnostics"`; its confirmation token is the dry run's `action_result.token`, bound to
  the device and to the holders the dry run listed, so `--confirm-holders` (CLI) / `"confirm_holders": true` (page) is the second
  confirmation and a holder that came since voids the first.

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

`reset_port` (docs/usb-reset.md) may also send `"confirm_holders": true` with the real request: the person ticked "I understand
tview (pid 4242) will lose it". The server turns it into `--confirm-holders` on the verb's command line, for that action only; it
is the one flag a request can add, and the verb still refuses `HoldersPresent` without it.

`set_id` also names the board's new bus id as `new_id` (an integer; omit it or send `null` for every other action).
The server passes it to the plugin as `--new-id` and binds the token to it, so the run must repeat it: a token
issued for `CAN 15 → CAN 7` cannot run `CAN 15 → CAN 9` (`TokenMismatch`).

```json
{"path": "host/usb1/port-1-3/usb-1-3/can/15", "action": "set_id", "node": 15, "new_id": 7, "dry_run": true}
```

and answers `{"dry_run": false, "result": {...}}` with the `Host.act` result of the real run (`records` are the
plugin's lines: `ActionCompleted`, `ConfirmationRequired`, `ActionRefused`, ...). Since 2026-10-24 a real run is a RUN
(docs/runs-v1.md): the plugin runs on a pseudo-terminal, the answer carries `result.run_id`, and `"detach": true` in
the request answers **202** `{"dry_run": false, "run": {...}}` at once so the page can show the live output. A dry run
on a bus whose `exclusive` group a run is holding is refused `Busy` (nothing waits for minutes). The server passes the PLUGIN's
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

### Composites: `all:<action>` (docs/composition-v1.md section 6)

A composite offers `all:<action>` (its leaves' shared standard action; "Calibrate all 6…"). The same two requests, with the composite's
path and `"action": "all:calibrate"`: the dry run dry-runs every leaf's own action and answers one result with `batch`
(`{"action", "title", "n", "danger", "skipped": [{"path", "name", "why"}], "runs": [{"order", "path", "label", "node", "plugin", "group",
"starts", "after", "danger", "argv", "confirm"}]}`) and one token for the batch; the confirm with that token (and `"detach": true`) creates
all N runs at once, runs of one `exclusive` group going one at a time in the listed order, and answers **202** `{"dry_run": false, "runs":
[{...}, ...], "batch": {...}}`. Every refusal above applies; a leaf's own refusal (`Busy`, `ConfirmRequired`, ...) refuses the whole batch, and
the plan itself can be refused `ChildLacksAction`, `ChildMissing` (`batch = "all"`), `NotBatchable`, `TooFew` or `NoSuchComposite`.
`io-diagnostics act` runs a batch's dry run but never the real thing (`BatchFromPage`).

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
- `expect` on a **board** row (a node a plugin reported under an adapter; the row menu's Expect / Don't expect) toggles
  it in its adapter's entry of the rig file: expected -> "Stop expecting CAN 14 …", not expected -> "Expect CAN 14 …"
  (`NotExpected` when its adapter is not in the file yet: Expect what's here on the adapter first).
- `forget` on a **board** row is **Remove from bus** (Zach, 2026-10-24): the host forgets the row and everything under
  it (`io_diagnostics/roster.py`; it is not restored at the next start). It writes no file, so it works without an
  expected setup. A removed board that answers again comes back. On a **device** row, `forget` is Don't expect: it
  leaves the expected setup and stays on the bus as an ordinary device (`NotExpected` when it was not in the file).
- A device that is expected and not on USB has no row to click any more (it is a check on the Computer); to stop
  expecting one, `io-diagnostics forget --hardware-id usb:...`.
- `retire` (Retire…): on a device row, with a non-empty `reason` (`ReasonRequired`): removes it from every
  rig and lists it under `[[retired]]` with today's date; plugged in again, its row says "retired <date>: <reason>".

The answer is **200** `{"ok": true, "op", "path", "refused": null, "message": "done: ...", "file"}`, or **400**
`{"ok": false, "refused": "<Code>", "message": "<plain reason>", ...}` (`NotADevice`, `NotHere`, `NotAbsent`,
`ReasonRequired`, `RigRequired`, `ManifestUnavailable` when the file does not read or this host has none,
`NotWritten`), or **404** when no row has that `path`.

## `GET /api/v1/health`

`{"ok": true, "api": "io-diagnostics/1", "mode": "live" | "fixture", "uptime_s": 12.3}`

Answered from the moment the server is up (2026-11-04): a restarted host is up long before it has read its log, and a page or a release chip asking in that moment is answered, not refused.

## `GET /api/v1/history?path=<entity path>[&status=<name>][&window=<duration>]`

The host keeps a rolling history per (entity path, status name), in memory (`io_diagnostics/history.py`), saved at a stop and every 2 min so a restart
keeps it (`history.json` in the state directory, 2026-11-04): the
level transitions and every measurement value, each on its own receive stamp (`received`, the host's clock). What
it is fed: every NEW evaluation a plugin run brings (a replay re-sending its recording is one report), the host's
own `plugin run` checks, and, on change, the OS checks (presence, port link, PCIe link, expected-absent) and the
per-view lines (rig rows, `another adapter`, the Computer's `USB/PCIe links`). A check its source stops computing
(an unplugged device's, a removed job's) has no data from then on.

```json
{
  "path": "host/usb9/port-9-1/usb-9-1/can",
  "name": "DEAD CAN bus",
  "window_s": 900.0,
  "from": 130.0,
  "to": 1030.0,
  "statuses": [
    {"name": "DEAD CAN bus: bus", "state": false, "expected_period_s": 2, "stale_after_s": 6.0,
     "levels": [{"from": 130.0, "to": 1000.0, "level": null, "code": "", "message": ""},
                {"from": 1000.0, "to": 1010.0, "level": "ok", "code": "", "message": "error-active"},
                {"from": 1010.0, "to": 1012.0, "level": "stale", "code": "", "message": "no update for 6 s (last: error-active)"},
                {"from": 1012.0, "to": 1022.0, "level": "error", "code": "NoAck", "message": "error-passive: nobody ACKs"},
                {"from": 1022.0, "to": 1030.0, "level": "stale", "code": "", "message": "no update for 6 s (last: error-passive: nobody ACKs)"}],
     "measurements": [{"key": "link speed", "unit": "Mbit/s", "warn_low": null, "warn_high": null, "error_low": null,
                       "error_high": null, "points": [[1000.0, 12], [1004.0, 12]]}]}
  ]
}
```

- `window`: `900`, `"90s"`, `"15m"`, `"1h"`; default the app's window (`/api/v1/settings`), at least 1 min and at most
  what the history keeps (`retention`: the window, but at least 15 min). Out of range → **400**. `from` / `to` are
  `now − window` and `now` on the host clock. `status`: only that check (exact name; none known → `statuses: []`).
  An entity that is neither in the tree nor in the history → **404**; one that left the tree keeps its history.
- `levels` cover `[from, to]` exactly, oldest first, with **the honest async rule**:
  - each status holds its last level until it goes stale, `max(3 × expected_period_s, 5 s)` after its last arrival
    (the same rule as `effective_level`); an event-driven status (`expected_period_s` `null`) holds until its next
    report. A span is `"stale"` from then until the next arrival;
  - `level: null` is **no data**, its own state: before the status's first arrival in the window, and after its
    source stopped computing it. Never drawn as a level;
  - a step is drawn only at an arrival or where a promise ran out; two checks that arrive out of sync never produce
    a step between them. `code` and `message` are the span's (the latest arrival's message for a run of equal
    reports).
- `measurements`: one series per measurement key of that status (at most 32), `points` = `[received, value]` pairs in
  time order (`value` may be `null` = unreadable, or a bool), with the latest unit and limits.
- **Bounds:** at most 4,096 statuses (the least recently heard is dropped), 200,000 points in all (a level span or
  one value each; past it the longest series loses its oldest points). Measured with tracemalloc: a full history is
  16 MB (mostly measurement values) to 28 MB (every point a level change), under the 30 MB `tests/test_history.py`
  holds it to. Nothing is written to disk: a restart starts empty.

## `GET /api/v1/history/bar?path=<entity path>&bins=N[&window=<duration>]`

The health bar (rqt_robot_monitor's timeline) of one entity and of each of its child rows, over the same window.

```json
{
  "path": "host/usb9/port-9-1/usb-9-1",
  "window_s": 60.0,
  "from": 970.0,
  "to": 1030.0,
  "bins": 10,
  "bin_s": 6.0,
  "rows": [
    {"path": "host/usb9/port-9-1/usb-9-1", "name": "fdcanusb DEAD", "depth": 0, "basis": "own", "statuses": 3,
     "bar": [null, null, null, null, null, "ok", "stale", "ok", "stale", "stale"]},
    {"path": "host/usb9/port-9-1/usb-9-1/can", "name": "DEAD CAN bus", "depth": 1, "basis": "own", "statuses": 1,
     "bar": [null, null, null, null, null, "ok", "stale", "error", "error", "stale"]}
  ]
}
```

- `rows`: the entity (`depth` 0), then each child row in tree order (`depth` 1). `bins` 1..1000 (default 60),
  oldest first, each `bin_s` wide; not a whole number → **400**.
- A bin is **the worst level held by any status of the row in it** (rank order, `ok < warn < unknown < stale <
  error`), using each status's own timeline as in `/api/v1/history`; `null` = no status held anything in that bin
  (no data yet). So a bin never shows a level no status held.
- `basis`: `"own"` = the row's own statuses (what this black box reported, like its row's `level`); `"below"` = a
  pure grouping row with no statuses of its own, which shows every status at or below it (what was reported there,
  not knock-on collapsed); `"none"` = nothing in the history. `statuses` = how many fed the bar.

## `GET /api/v1/history/tree?bins=N[&window=<duration>]`

The health bar of **every row of the tree** over one window, in one answer (2026-10-27): what `/history/bar` gives an entity and its children,
for the whole tree, so a page's tree can draw each row's past from before it opened without a request per row. `bins` and `window` are
`/history/bar`'s (`bins` 1..1000, default 60; `window` default the app's, 1 min .. what the history keeps; out of range or not a whole number
→ **400**); there is no `path` (it is the whole tree from `host`).

```json
{
  "path": "host",
  "window_s": 60.0,
  "from": 1792631490.0,
  "to": 1792631550.0,
  "bins": 12,
  "bin_s": 5.0,
  "rows": [
    {"path": "host", "name": "bam-GPU", "depth": 0, "basis": "below", "statuses": 71,
     "bar": ["ok", "ok", "error", "error", "error", "error", "error", "error", "error", "error", "error", "error"]},
    {"path": "host/usb1/port-1-3/usb-1-3", "name": "fdcanusb 272813EF", "depth": 3, "basis": "own", "statuses": 3,
     "bar": [null, null, null, null, "ok", "stale", "stale", "stale", "stale", "stale", "stale", "stale"]}
  ]
}
```

- `rows`: every row of `/state`'s tree, parent first then its children in order, each exactly the row `/history/bar` returns for that entity
  (`path`, `name`, `basis`, `statuses`, `bar`, with the same rules: the row's own statuses when it has any, else everything below it), made by the
  same code, so the two cannot differ. `depth` is the row's depth **in the tree** (the host row is 0, as `/state`'s `depth`), where `/history/bar`
  counts from the entity it was asked about (0, then 1 for its children).
- A group's bar reads every status below it, so the host makes each status's timeline once and every row that reads it shares it.

## `GET /api/v1/checks`

The latest checks (diagnostic statuses) of every entity in one answer (2026-10-27), for a Diagnostics table across a rack: one request instead of one
`GET /api/v1/node` per entity. No parameters.

```json
{
  "clock": {"now": 1792631550.0, "iso": "2026-10-21T18:12:30-07:00", "time": "18:12:30", "mode": "fixture", "utc_offset_s": -25200,
            "rank": ["ok", "warn", "unknown", "stale", "error"], "stale_factor": 3.0, "stale_floor_s": 5.0},
  "checks": {
    "host/usb1/port-1-3/usb-1-3": [
      {"name": "v6_scenario: plugin run", "level": "ok", "effective_level": "stale", "stale": true, "not_run": false, "message": "ran in 0.043 s · 31 records",
       "code": "", "mode": null, "source": "io_diagnostics.runner", "hardware_id": "usb:0483:5740:272813EF", "stamp": 1792631510.0, "received": 1792631510.0,
       "received_iso": "2026-10-21T18:11:50-07:00", "age_s": 40.0, "expected_period_s": 2.0, "stale_after_s": 6.0, "uptime_s": null,
       "caused_by": [], "values": [{"key": "outcome", "value": "ok"}],
       "measurements": [{"key": "duration", "value": 0.05, "unit": "s", "text": "0.05 s", "level": "ok", "stamp": null, "warn_low": null,
                         "warn_high": 8.0, "error_low": null, "error_high": null}]}
    ]
  }
}
```

- `clock`: the host's clock as in `/state`; every `age_s` and `effective_level` below is judged at its `now`.
- `checks`: entity path → that entity's checks, **each exactly the object `/node?path=` gives in its `checks`** (the same `check_json`), worst first.
  An entity with no checks is absent (an absent path is "none", not "unknown"); a path is any entity `/node` knows.

## Settings: `GET /api/v1/settings`, `POST /api/v1/settings`

```json
{"history_window_s": 900.0, "visibility": {"usb:046d:c34b:KB1": "show"}, "dashboard": [],
 "depth_colormap": "Jet", "depth_equalize": true, "depth_min_m": 0.0, "depth_max_m": 6.0,
 "source": "default", "file": "/home/bam/.config/io-diagnostics/settings.toml", "note": "",
 "limits": {"history_window_s": {"min": 60.0, "max": 86400.0, "default": 900.0},
            "depth_colormap": {"choices": ["Jet", "Classic", "White to Black", "Black to White", "Bio", "Cold", "Warm", "Quantized", "Pattern", "Hue", "Turbo"], "default": "Jet"},
            "depth_equalize": {"default": true}, "depth_range_m": {"min": 0.0, "max": 16.0, "default": [0.0, 6.0]}}}
```

- One app-wide window for every health bar and history answer, default 15 min. Where it comes from (`source`), the
  first that applies: `serve --history-window 15m` (`"flag"`: this run only, never written), the file
  (`"file"`: `~/.config/io-diagnostics/settings.toml`, `$IO_DIAGNOSTICS_CONFIG_DIR` / `$XDG_CONFIG_HOME` honoured),
  the default (`"default"`); after a page change, `"page"`. `note` says why the file could not be read or written.
- `visibility` (2026-10-24): the Visibility choices, `{row id: "hide" | "show" | "ack"}`; a row id is its hardware id,
  else its path. `POST /api/v1/settings` with `{"visibility": {"<id>": "show", "<other id>": null}}` merges them (`null`
  forgets one) and writes a `[visibility]` table to the file. `hide` / `show` are the person's choice; `ack` is a hidden
  row whose ERROR was acknowledged (the page ends it when the error ends). The default is no choice: a row no driver
  claims (`claimed` false) is hidden, everything else shown, and a hidden row that reports a WARN or ERROR of its own shows itself again until acknowledged. It only
  filters what the tree draws: the counts, the chip and every parent's roll-up still count a hidden row's problems.
- `dashboard` (the chart view c4, `web/charts/c4.js`): the tiles a person pinned from sparklines, a list of objects each
  with a `spec` object (at most 60 tiles, 32 KB of JSON). The host stores it whole in the same file (a top-level JSON
  string, `dashboard = '[...]'`) and never reads inside a tile: the page owns the shape. `POST {"dashboard": [...]}`
  replaces it; a body with any refused key applies none of its keys.
- `depth_colormap`, `depth_equalize`, `depth_min_m`, `depth_max_m` (2026-10-24, Zach: "give the option to set the color map for
  the depth map"): how a camera's live depth picture is coloured, app-wide, shown in View options as "Depth colour map" (a select),
  "Depth equalization" (a switch) and, with equalization off, "Depth range" (metres). The choices are realsense-viewer's colour
  scheme names, which are the SDK colorizer's `color_scheme` option 0..9 in that order, then `Turbo` (the SDK has none; the
  RealSense driver's own map from before this setting). The defaults are the colorizer's own, which realsense-viewer leaves alone:
  Jet, equalization on, 0 .. 6 m (checked against pyrealsense2 2.58.3 and librealsense `src/proc/colorizer.cpp` / `common/subdevice-model.cpp`;
  the viewer narrows only a D405, to 0.05 .. 4 m). `POST` any of them (`{"depth_colormap": "Hue", "depth_equalize": false,
  "depth_min_m": 0.5, "depth_max_m": 3}`; a name in any case; the minimum below the maximum, 0 .. 16 m) to set it now and write it as
  `depth_colormap = "Hue"` etc. in the file. The host passes it to every `watch` in the environment
  (`docs/state-config-v1.md` section 4) and **restarts the running watches when a value changed**, so the next frame uses it.
- `POST /api/v1/settings` with `{"history_window_s": 600}` (seconds, or `"10m"`) sets it now AND writes the file
  (atomically; the file holds `history_window_s = 600`). The same page-only rule as `POST /api/v1/act`: the page's
  `X-IO-Diagnostics: ui` header, `Sec-Fetch-Site: same-origin`, a JSON body, else **403** (`NotFromPage`,
  `NotSameOrigin`, `NotJson`). A value outside 1 min .. 24 h, or an unknown setting → **400** `BadSetting`. Answers
  the settings as above.

## Stock diagnostics: `GET /api/v1/diagnostics`, `GET | POST /api/v1/diagnostics/config`

The host runs the robotdx.diagnostics library (`robotdx/diagnostics/`, lifted from sim2real's reference, 2026-10-28) on its entities
(`io_diagnostics/stock_diagnostics.py`). Which diagnostic runs on which entity, with what config, is `<config dir>/diagnostics.toml`
(`~/.config/io-diagnostics/`, `$IO_DIAGNOSTICS_CONFIG_DIR` honoured), else the host's default set (`io_diagnostics/diagnostics_default.d/`,
one file per part type, generated from the catalog by `io_diagnostics/default_diagnostics.py`; the keys are explained in
`stock_diagnostics.py`'s docstring); a fixture replay runs only the person's file. A `[[part]]` table says which entities are a part type
(by `model_id`, `path` or a composite's kind) and a binding's `part = "<type>"` runs on all of them, so an entity of a known type gets the
catalog's live diagnostics with no file of the person's. Each binding reads one typed input out of what the entity publishes
(`stock_diagnostics.SOURCES`): its State history; every State record's arrival (a rate); its frame records (stamp vs received); the
newest image of its colour stream (decoded on the beat: PNG with the stdlib and numpy, JPEG with Pillow from the `diagnostics` extra; a JPEG
without Pillow is `NoDecoder`); the fault memory's
`FaultConfirmed` events (a composite's: its leaves', never the stock diagnostics' own) with the history's operating time; link events;
its status's `queries` / `replies`; config records with an `expected` value; a composite's members' levels; its statuses' stamps. A servo
or encoder composite over one board also reads that board's State; a servo whose State has no commanded position reads the newest
recorded run of its joint (bam_rack_twin npz in the rack archive, `$IO_DIAGNOSTICS_RACK_ARCHIVE`, default `/archive/datasets/bam_rig`;
`dx.window.source` `recorded_run`, its `from` / `to` the run's own wall clock and `run` its file) and its holds; a deployment reads the
newest `bam_rack_twin.acceptance/1` there for its gate matrix (`source` `acceptance`, `file`, `generated_at`) and the Computer plugin's
`Temperature CPU` status for `cpu_temp_c`. A binding whose input no source on the entity carries is not run with one of two codes, and
the message "no <input> source on <entity>: <what would provide it>" and value `dx.gap`: `NotMeasured` (`not_measured`: no instrument on
the entity can produce it: no IMU, no pick log, no tag in view) or `NotWired` (`not_wired`: an existing producer has it or could
publish it, and nothing maps it yet): a row (unknown, never OK) where the entity reports State, none where it reports no State at all (value
`dx.quiet`); `NotStreaming` (its stream has sent nothing in the window: frames and records arrive while a page watches) and `NoState`
post no row. The drill-in always lists them. Every 5 s (a thread of its own, never the 10 Hz watch path) each
binding reads the last `window_s` of its entity's State history (kept at 1 Hz at most) through `stream.monitor`, and the newest window's
Result is an **ordinary record-v0.2 status** on the entity: `source` `robotdx.diagnostics`, `measurements[0]` the headline value with its
warn / error limits, `values` the library's `dx.*` keys (`dx.diagnostic`, `dx.config`, ...) plus `dx.binding` (the binding's id) and
`dx.window` (JSON `{"from", "to", "window_s", "state", "samples", "source"}`, host clock; `source` the input kind it was fed from), `expected_period_s` the beat (so a stopped beat goes STALE).
So it is in `/node`'s `checks`, `/checks`, `/history`, the history bar and `/live` with no change to any of them. A result that could not be
computed is `unknown`, never `ok`: `NotEnoughHistory`, `NoState`, `NotWired`, `NotMeasured`, `NotStreaming`, `NoDecoder`, `NoChannel`, `NumpyMissing` (the `diagnostics` extra is not installed),
`DiagnosticRaised` (the exception's text).

### `GET /api/v1/diagnostics`

```json
{"clock": {"now": 1792631550.0, "...": "as in /state"},
 "file": "/home/bam/.config/io-diagnostics/diagnostics.toml", "writes": "/home/bam/.config/io-diagnostics/diagnostics.toml",
 "problem": "", "defaults": true, "beat_s": 5.0, "numpy": true,
 "diagnostics": [
  {"id": "stock.box", "title": "box check", "summary": "min/max/mean/std of a channel vs four Limits (Zach's BoxChartTask)", "kind": "stock",
   "family": "health", "role": "health", "hint": "threshold_band", "emits": "series", "unit": "", "consumers": ["robotdx", "sim2real"],
   "view_extra": "", "part_types": ["arm", "bldc_driver", "deployment", "encoder", "ft_sensor", "gripper", "imu"], "config_type": "BoxConfig",
   "doc": "Zach's ``BoxChartThresholds`` (min, max, mean_min/mean_max, std) as one frozen config of four ``Limits``.",
   "config": [{"field": "channel", "type": "str", "default": "temp_c", "unit": "", "doc": "", "group": ""},
              {"field": "max.warn_high", "type": "float|null", "default": null, "unit": "Cel", "doc": "", "group": "max"},
              {"field": "headline", "type": "str", "default": "max", "unit": "", "doc": "which statistic is the headline number", "group": ""}]}]}
```

- `diagnostics`: every registered diagnostic, sorted by `id` (the registry name). `kind` `stock` (robotdx.diagnostics.stock) or `custom` (a
  `[[diagnostic]]` table with `function` in diagnostics.toml, `registry.load_toml`, or the `robotdx.diagnostics` entry point). `part_types`:
  the catalog's part types that use it (`robotdx/diagnostics/catalog.py`).
- `config`: the config dataclass as rows. `type` is `float`, `int`, `bool`, `str`, `tuple` or `float|null`; a `Limits` field is four rows
  `<field>.warn_low | warn_high | error_low | error_high` (`float|null`, null = no bound), in the value's unit. `doc` is the comment beside
  the field in the dataclass ("" when it has none).
- `file`: where the bindings were read (the person's file, the default set, or "" for none); `writes`: where a change goes (null: none);
  `problem`: why the person's file is not used (then nothing runs from it, never a guess); `numpy` false: every binding reports `NumpyMissing`.

### `GET /api/v1/diagnostics/config?path=<entity path>`

```json
{"clock": {"...": "as in /state"}, "path": "host/usb1/port-1-3/usb-1-3/can/1", "model_id": "mjbots-moteus-c1", "parts": ["bldc_driver"],
 "file": "...", "writes": "...", "problem": "", "defaults": true, "beat_s": 5.0, "numpy": true,
 "diagnostics": [
  {"id": "driver.board_temp", "diagnostic": "stock.box", "title": "board temperature", "summary": "...", "doc": "...", "kind": "stock",
   "match": {"path": "", "model_id": "", "part": "bldc_driver"}, "state": "motor", "window_s": 60.0, "edited": ["max.warn_high"],
   "catalog": {"part": "bldc_driver", "checks": "FET/board temperature stays inside its band", "provenance": "his code", "quote": "...", "where": "...",
               "hint": "threshold_band", "role": "health", "gate": "", "limits_provenance": "his code", "limits_source": "", "gating": true},
   "source": {"kind": "state", "input": "the board temperature", "from": "the driver's State (its watch, and each diagnose)", "missing": ""},
   "fields": [{"field": "max.warn_high", "type": "float|null", "default": null, "unit": "Cel", "doc": "", "group": "max",
               "value": 30.0, "source": "edited", "editable": true}],
   "status": {"name": "board temperature", "level": "warn", "...": "the check exactly as /node gives it"},
   "window": {"from": 1792631490.0, "to": 1792631550.0, "window_s": 60.0, "state": "motor", "samples": 60}}]}
```

- `source` (additive): what feeds the binding: `kind` (state | state_stamps | frame_stamps | frames | reply_counts | config | faults | link_events | members | evidence | holds | acceptance | none), `input` in plain words, `from` (where RobotDx has it) or, for `none`, `missing` (what would provide it), and `gap` (`not_wired` | `not_measured`: what its absence is). `status` is there for every binding, a `NotWired`, `NotMeasured` or `NotStreaming` one included, even when no row is posted.
- One entry per binding that matches the entity (its `path` and / or `model_id` fnmatch, and / or its `part` among the entity's `parts`); **404** for an entity the host does not know, an empty
  list for one nothing is configured on. `fields`: the effective value of every config field, and `source`: `default` (the dataclass's),
  `toml` (the file's), `edited` (written by the POST below; the file lists them in the binding's `edited`). A `Limits` field's four parts are
  `toml` when the file names the field at all. `status` / `window`: the latest result, null before the first beat. `catalog` (null for a
  binding whose id is not a catalog entry): `hint` is the data-ui view; `limits_provenance` whose thresholds these are (his / his code /
  agent / proposed / none); `gating` false = a crossing reports as a `Proposed...` warning, never an error (a value the person sets
  themselves makes it true).

### `POST /api/v1/diagnostics/config`

`{"path": "<entity path>", "id": "driver.board_temp", "field": "max.warn_high", "value": 30}`, or `{"path", "id", "field", "reset": true}`
(back to the dataclass default). The entity page's drill-in ONLY, under the same page-only rule as the other config writes (`X-IO-Diagnostics:
ui`, `Sec-Fetch-Site: same-origin`, the page's origin, a JSON body, else **403** `NotFromPage` / `NotSameOrigin` / `NotJson`). It is pure
software, the person's own file: no edit session, no dry run and no token.

- **accepted** (200): `{"state": "accepted", "ok": true, "path", "id", "field", "value": <read back>, "source": "edited" | "default", "saved": true,
  "note": "", "file": "<diagnostics.toml>", "reset": null | "default"}`. The value is checked against the dataclass field's type (a bool is not a
  number), a `Limits` part against the ordering `error_low <= warn_low <= warn_high <= error_high`, and the config is built whole; then the WHOLE
  set (the default set's bindings too, on the first change) is written to `<config dir>/diagnostics.toml` atomically (temporary file, fsync,
  rename; comments are not kept) and read back. The next beat uses it. A binding is shared by every entity it matches: a change on one board's
  page changes it for every board of its `model_id`.
- **accepted, not saved** (200): `"saved": false` and `note` says why, when the host keeps no file or cannot write it: the value holds for this run.
- **rejected** (200, the host answered the question): `{"state": "rejected", "ok": false, "refused": "BadValue" | "NoSuchField" |
  "NoSuchDiagnostic", "message": "<the reason>"}`; nothing is written. **400** `BadRequest` for a malformed body, **404** `NoSuchEntity`.

## `GET /api/v1/live` (Server-Sent Events, change-only)

The page's 1 s `GET /state` poll (the same ~70 KB of tree every second, mostly unchanged) as one stream. `/state` stays exactly as it
is, and so do `/events` and `/values`: a page that wants a whole state, or a script, keeps polling. Same-origin and read-only
like `/events` (no CORS header). Every event has an `id:` that rises by one per event, across every client of this host.

```
retry: 2000
id: 8
event: snapshot
data: {"api": "io-diagnostics/1", "...": "exactly what GET /api/v1/state returns"}

id: 9
event: upsert
data: {"table": "tree", "id": "host/usb1/port-1-3/usb-1-3/can/1/motor", "row": {"path": "host/usb1/port-1-3/usb-1-3/can/1/motor", "level": "ok", "...": "the node as in /state", "children": []}}

id: 10
event: upsert
data: {"table": "state", "id": "summary", "row": {"level": "error", "counts": {"error": 2, "warn": 3}, "...": "..."}}

id: 11
event: remove
data: {"table": "tree", "id": "host/usb1/port-1-4"}

id: 12
event: samples
data: {"path": "host/usb1/port-1-3/usb-1-3/can/1/driver", "now": 1792790150.0, "cursor": 412, "groups": [{"name": "...", "rows": [{"key": "voltage", "points": [[1792790150.0, 36.6]], "...": "..."}], "...": "..."}], "streams": [], "heard": {"...": "..."}, "watch": {"...": "..."}}
```

- **`snapshot`**: the first event of a connection (and the only one a client gets when it cannot resume), the body `GET /state` returns,
  as of the host's last tick, with the `id` of that tick. Samples are not in it: ask `GET /values?path=&since=0` for an entity's history.
- **`upsert`** `{table, id, row}`: a row whose JSON changed since the last tick, in tree order (a parent before its children). Two tables:
  `"tree"`, one node of `/state`'s tree keyed by its `path`, with `children` a **list of the child paths** instead of nested nodes (re-nest
  from them; a parent is upserted when a child is added or removed), and `"state"`, each other top-level key of `/state` (`clock`, `summary`,
  `chip`, `recent`, `expected_setup`, `composition`, `runs`, ...) keyed by its name, `row` its whole value. A client that applies the snapshot
  and every event in order holds `/state` as of the last tick. A row is sent whole, never as a patch.
- **`remove`** `{table, id}`: that row is gone (a device unplugged, a rig edited). Its parent is upserted in the same tick.
- **`samples`**: State points appended since the last tick for one entity, the answer `GET /api/v1/values?path=<path>&since=<its cursor>` gives
  (same fields), with `groups` cut to the groups and rows that have new `points`. One event per entity per tick. **An entity a page is watching** (its plugin's
  `watch` is running; `POST /api/v1/watch`) does not wait for the tick: its new points are sent as they arrive, coalesced to at most one `samples`
  event per watched entity per 100 ms (10 Hz; measured on a 10 Hz fixture watch: 20 events in 2.0 s). The shape is the same, and a point is sent
  once (both paths advance one cursor per entity under one lock), so the page's State moves at the watch's rate. Entities nobody watches keep the tick.
- **`emitted`** (2026-10-27): every event's JSON, of every kind (`snapshot`, `upsert`, `remove`, `samples`), has `"emitted": <unix milliseconds, float>`, the host's **wall clock**
  (`time.time()`) when it put the event on the feed. WHY: on a fixture `clock.now` is the replay clock, so without it a page cannot time an event from the host's send to
  its own draw. It is a top-level key of the event's data, beside `table`/`id`/`row` or `path`/`cursor`/`groups`; in a `snapshot` it sits beside `/state`'s own keys
  (`/state` itself does not have it, and it is not a state section a client should keep). A client that resumes with `Last-Event-ID` is sent the ring's bytes again, so a
  replayed event carries the stamp it first left with. The `: keepalive` heartbeat stays a comment line: no event, no stamp.
- **Time is not a change.** Pure functions of the host clock are not compared, or every row with records would change every tick: a row's
  `age_s` and `presence.age_s`, and `clock.now` / `iso` / `time`. A row that is sent for any other reason carries the age it has at that moment;
  a page that wants it now computes `clock.now` (as of the snapshot, then the time since) minus the row's `last_received`.
- **Resuming**: the browser's `EventSource` sends `Last-Event-ID` on reconnect, and the host replays only the events after it from a ring of
  its last 2000 events or 60 s (the ring is shared by every client). An id older than the ring, one this feed never gave (including after the
  feed stopped with no client for 60 s, or the host restarted), or one that is not a number, gets a fresh `snapshot` instead, so a client
  needs no rule of its own: `snapshot` replaces everything it holds.
- **Cost**: one thread serves every client; it wakes on the host's own change tick (the one `/events` waits on: every refresh, ~2 s, and each
  udev or plugin change), builds `state()` once and diffs it by row. It starts with the first client and stops 60 s after the last one leaves.
- `: keepalive` comment every 15 s while nothing changes; a client that closes is dropped within a second. The stream ends when the host stops
  (a client that connects to a stopping host gets **503**).
- Measured on `serve --fixture F1` (a replay in which every plugin record is received again each ~2 s tick, so most rows change every tick): 1.6 MB a
  minute for `/live`, snapshot included, against 4.0 MB a minute for `/state` polled at 1 Hz. A rig whose records come every 30 s or more sends far less.

## Dashboards as files: `GET /api/v1/dashboards`, `GET | PUT | DELETE /api/v1/dashboards/<name>.base`

A person's dashboards are plain files in `<config dir>/dashboards/` (`$IO_DIAGNOSTICS_CONFIG_DIR`, else
`$XDG_CONFIG_HOME/io-diagnostics`, else `~/.config/io-diagnostics`: the folder `settings.toml` is in). The format is the page's
(data-ui's `.base`); **the host never parses it**: it keeps and returns the text byte for byte and enforces only the file
store's own rules below. The folder is created by the first `PUT`, not by a read.

```json
{"files": [{"name": "Servos", "file": "Servos.base", "size": 1342, "mtime": 1792790150.5,
            "etag": "9f2c0b7e5c1d4a3b8e6f0a1b2c3d4e5f60718293a4b5c6d7e8f9012345678901"}]}
```

- `GET /api/v1/dashboards` lists them as an object with a **`files`** key, newest `mtime` first. `name` is the file's name
  without `.base`, `file` the name to put in the URL, `etag` the SHA-256 (hex) of the content. A file that breaks the name rule,
  is a link, or is over the cap is left out. (WHY an object, not a bare list: the unmerged branch `lane/dashboard-views`
  has its own `GET /api/v1/dashboards`, the dashboard proposals' boards, whose reply is separate from this file store; a
  `files` key lets both live in one reply later, and a page must ignore keys it does not know.)
- `GET /api/v1/dashboards/<name>.base` returns the raw text (`Content-Type: text/plain; charset=utf-8`) and the
  header `ETag: "<etag>"`. **404** `NoSuchDashboard`; **422** `NotUtf8` for a file somebody put there by hand that is not text.
- `PUT /api/v1/dashboards/<name>.base` writes the body (the raw text, any `Content-Type`; a `Content-Length` is required).
  **201** when it created the file, **200** when it replaced one; the answer is `{"ok": true, "name", "file", "size", "mtime",
  "etag"}` and the `ETag` header. The write is atomic (a temp file in the same folder, fsync, `os.replace`): a reader sees the
  old text or the new, never half.
- `DELETE /api/v1/dashboards/<name>.base` moves the file to `.trash/<name>.<UTC stamp>.base` in the same folder and answers
  `{"ok": true, "name", "file", "trashed": ".trash/Servos.20261027T180000.base", "etag"}`. Nothing is removed (put it back
  by moving it); a second delete of the same name lands beside the first. **404** `NoSuchDashboard`.
- **Names**: `<name>` matches `^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$` and the file ends in `.base`. The name is URL-decoded first,
  so `..%2fx.base`, `%2e%2e/x.base`, a `/` or `\` anywhere, or `..` are the characters they are and fail the rule: **400**
  `BadName`, for every method, and nothing is read or written outside the folder. A link inside the folder is never followed.
- **Size and encoding**: at most 1 048 576 bytes (**413** `TooLarge`, answered from the `Content-Length` before the body is
  read) and valid UTF-8 (**400** `NotUtf8`); a missing `Content-Length` is **411** `LengthRequired`.
- **`If-Match`** (optional, on `PUT` and `DELETE`): the etag (quoted or bare, a comma list, or `*` for "any existing file") the
  client last saw. A different one, or any `If-Match` for a file that is not there, is **412** `EtagMismatch` with the current
  `"etag"` (null if none) and nothing changes, so two tabs cannot silently overwrite each other. Without it the last write wins.
- **`If-None-Match`** (optional, on `PUT`; added 2026-10-29 for the page's Add to dashboard, PEP 0001 §17): create only. `*` with a
  file of that name already there, or an etag list naming the file as it is now, is **412** `Exists` with the current `"etag"`, and
  nothing changes (RFC 9110 §13.1.2). New dashboard… sends `*`, so a name another tab took a moment ago is never written over.
- **Page-only writes**, the same rule as `POST /api/v1/act`: the page's `X-IO-Diagnostics: ui` header, `Sec-Fetch-Site:
  same-origin`, and an `Origin` (if sent) that is this page's, else **403** `NotFromPage` / `NotSameOrigin`. The body is the
  file's text, so the JSON `Content-Type` check does not apply (a `PUT` with a custom header is preflighted by every
  browser, and this server never answers a preflight). No dry run and no token: no hardware is involved. `GET`s need no header.
  A host with no config directory (one built by hand) answers **503** to all of them. Every other `PUT` or `DELETE`
  (another path, or `/dashboards` itself) is still **405**.

## `GET /api/v1/robot-description`

The page's 3D view (2026-10-29, Zach: "basically like Fusion 360 ... navigate devices visually"): an optional URDF whose links are
mapped to the devices this PC detects, from `<config dir>/robot-description.toml` (the directory settings.toml and expected.toml
are in; the format is docs/robot-description.md). Read on each request, and again only when the file or its URDF changed on disk.
Without the file the answer is `present: false` and everything else on the page is unchanged.

```json
{
  "present": true,
  "problem": null,
  "file": "/home/bam/.config/io-diagnostics/robot-description.toml",
  "file_name": "robot-description.toml",
  "docs": "/home/bam/io-diagnostics/docs/robot-description.md",
  "urdf": "/api/v1/robot-description/files/one_dof_test_rack.urdf",
  "robot": "one_dof_test_rack",
  "links": ["base_link", "one_dof_arm"],
  "parts": [{"name": "CAN 21", "links": ["pole_mount"], "meshes": ["JMC60_Plate_1___JMC_60ASM200-5-17BCZ_2__*"], "at": null, "look_at": null,
             "match": {"hardware_id": "usb:0483:5740:0607623A", "node": "can/21"},
             "note": "video: tag 5 turned 137 deg", "path": "host/usb1/port-1-5/usb-1-5/can/21", "problem": null}],
  "unmapped_devices": [{"name": "RealSense D435 044322072026", "match": {"serial": "044322072026"},
                        "reason": "its frame shows the ceiling, not the rack", "path": "host/usb1/port-1-1/usb-1-1"}],
  "name": "one-dof-test-rack",
  "packages": {},
  "view": {"front": "+x"},
  "hardware": [{"path": "host/usb1/port-1-5/usb-1-5", "name": "fdcanusb 0607623A", "kind": "adapter", "parts": [0]}],
  "not_in_model": [{"path": "host/usb1/port-1-1/usb-1-1", "name": "RealSense D435 044322072026", "reason": "its frame shows the ceiling, not the rack"}],
  "library": [{"name": "one-dof-test-rack", "robot": "one_dof_test_rack", "parts": 10, "problem": null, "active": true}]
}
```

- `present`: whether the file exists. `problem`: why it does not draw (not TOML, a key wrong, the URDF missing or not XML), else
  `null`; with a problem `urdf` is `null` and no file is served. `file` is the file the host reads (also when it is absent: where to
  put one); `file_name` and `docs` (the format's documentation in this checkout) are what the empty state tells a person.
- `urdf`: the URL the page loads the URDF from; a mesh's relative `filename` resolves next to it. `robot` and `links`: the URDF's
  `<robot name>` and its link names in file order. `name`: the description's name (its `name`, else from the URDF's file name).
  `packages`: each `package://<name>/...` the URDF uses -> the folder under `files/` that holds it. `view`: the file's `[view]` table.
- `parts`: one per `[[part]]`, in the file's order. `links` are whole links, `meshes` patterns over the visuals' mesh file names,
  `at` and `look_at` a point of the base frame for a device the model does not have (`null` otherwise). `match` is the part's keys as
  written (`path`, `hardware_id`, `serial`, `node`, `model_id`, `can_id`); `path` is the entity it resolves to on this host now
  (expected.toml's rules: a device by hardware_id and / or serial, then `node` below it; `path` fnmatch, `model_id` and `can_id` narrow;
  the first row in tree order), or `null` when nothing matches (the page draws a ghost: not detected). `problem` names a link the URDF
  does not have, or a pattern no mesh matches.
- `unmapped_devices`: the file's `[[unmapped_device]]` tables (devices its author could not place in the model, with the `reason`),
  each resolved the same way, as written.
- `hardware`: each computer and adapter on this host (`kind`: `computer`, `adapter`), with `parts`, the indexes into `parts` of the devices
  below it: the page's chips and their leader lines. `not_in_model`: every device a person could place and nobody has, once each: the
  `unmapped_devices` that are still not placed, then every board and camera detected (`reason` empty). A chip is never in it.
- `library`: every description this PC has, by name; `active` is the one shown.
- **Colour is not here.** The page colours a mapped part by its entity's own row in `/state` / `/live` (`level`, `connected`, `stale`);
  this endpoint never carries a level.

`GET /api/v1/robot-description/files/<path>` serves one file under the description's folder (`root`, else the URDF's directory; `.urdf` /
`.xml` as `application/xml`, `.stl` as `model/stl`, else `application/octet-stream`; always `X-Content-Type-Options: nosniff` and
`Content-Security-Policy: sandbox`, since the page can upload them), read-only and `GET` only. Nothing outside that
directory is served: a `..` (raw or percent-encoded), an absolute path, a symlink that resolves outside it, a directory or a missing
file is **404**, as is every file when there is no description or it has a problem. The same guard as the web root's static files.

### The description's writes: `POST /api/v1/robot-description/upload | assign | activate | remove`

The page's own (2026-11-04, Zach: "I can just upload the URDF"), under the rule of the Expected setup card: `X-IO-Diagnostics: ui`, a
same-origin `Sec-Fetch-Site` and no token, because they write the person's files and never a device. A request without them is **403**
(`NotFromPage`, `NotSameOrigin`); a `GET` is **405**. Docs: docs/robot-description.md "From the page". Each answer is `{"ok": true, ...}`, or
`{"refused": <code>, "error": <why>}` with the status below.

- `POST /api/v1/robot-description/upload`, body a zip (`application/zip`) or a folder (`multipart/form-data`, one file field per
  file, its `filename` the path inside the folder), at most 1 GiB. `?name=` names it, else the URDF's `<robot name>`. It unpacks into
  `<config dir>/robot-descriptions/<name>/`, keeps the description that was active there, and makes this one active. Answer: `name`,
  `robot`, `links`, `meshes`, `missing_meshes`, `mapped` (parts read from a cad2robot/1 `robot.json`), `schema`, `warnings`, `previous`.
  Refused, leaving nothing behind: **400** `UnsafePath` (a member that would land outside the folder, a symlink), `BadUpload` (not a
  zip, an empty body), `NoRobot` (no `.urdf`, not XML, not `<robot>`); **413** `TooLarge` (the body, or more than 3 GiB or 20,000 files
  unpacked, counted as read).
- `POST /api/v1/robot-description/assign`, `{"path": <entity>, "links": [...], "meshes": [...]}`: puts them on the device at `path`,
  in the active file. What that device already holds comes off again (`"assigned": false`: the click that undoes); what another device
  holds moves here; a part left with nothing is removed. The new part is found by the device's repeatable keys (`hardware_id` and `node`,
  else `serial`, else `path`). Comments and other tables stay as written; the old file stays beside it as `.bak`. **400** `BadRequest`,
  `NoSuchLink`; **404** `NoSuchEntity`; **409** `NoDescription`.
- `POST /api/v1/robot-description/activate`, `{"name": <kept name>}`: keeps the active file under its own name and makes this one
  active. `POST /api/v1/robot-description/remove`, `{"name": <kept name>}`: removes it and its kept folder, nothing outside that folder;
  the active one goes back to `present: false`. **404** `NoSuchDescription`.

## What a page must NOT assume

- That `level` is the worst below: it is the row's self-report. Use `hint` / `rollup` for "something below is worse".
- That every row has records: groups have none (`own.level` = `null`), and so has the host until a plugin diagnoses
  it (the computer plugin). Nor that `summary.level` equals the host row's `level`: `summary` is the root's rollup
  (the worst independent cause anywhere), the host row's `level` its own report once it has one.
- That levels are only ok/warn/error: `unknown` (not evaluated, e.g. `PortBusy`) and `stale` (overdue) are first-class
  and rank above `warn`.
- That a path is stable across replugs (key selections on `path`, but expect it to vanish and reappear elsewhere;
  `hardware_id` follows the device).
- That a `GET` runs anything: `executable` is `false`; a button runs only through `POST /api/v1/act` after its dry run.
- That `danger` has three values: record v0.1 added `moves`; treat any value it does not know as needing a confirm.
