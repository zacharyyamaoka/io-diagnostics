# State, Configuration and the entity page — contract v1

*2026-10-24, lane entity-page. Spec: Zach's daily note 2026-10-24, "My feedback on the entity page". Record contract
revision **0.2**, additive on 0.1 (a reader that knows 0.1 ignores what it does not know). Served by
`io_diagnostics/server.py`; the records are `contract/record.py`; the page is `web/detail/d6.js`.*

An entity page is a header that is always visible and **tabs**, each tab one kind of record, all joined on the entity
path (Zach, 2026-10-24: "faster to switch between things than having to scroll down"; System Monitor and tview do it).
They are **not** merged into one type: health, values, settings, actions and logs have different lifetimes and readers.

| Tab | Record | SOVD resource (ISO 17978; ros2_medkit implements it) | Read from |
|---|---|---|---|
| **Diagnostics** | `status` (+ the host's `Fault` memory) | faults | `GET /api/v1/node` `checks`, `faults` |
| **State** | `state` and every `Measurement` | data | `GET /api/v1/values`, `/frames` |
| **Configuration** | `config` | configurations | `GET /api/v1/node` `config` |
| **Utilities** (the ☰ menu in the header too) | `Action` | operations | `POST /api/v1/act` |
| **Log** | `event` | logs | `GET /api/v1/activity` |

## 1. State is the entity's live values

State is `Measurement`s (key, value, UCUM unit, limits, own read time). They arrive in a new `state` record, or ride
on a `status` (the host lifts a status's measurements into State, grouped by the status's name; the host's own
`io_diagnostics.*` statuses are not lifted).

```json
{"record": "state", "path": "can/1", "name": "motor", "stamp": 1761095510.2, "expected_period_s": 0.1,
 "measurements": [{"key": "position", "value": 0.3125, "unit": "rev"},
                  {"key": "mode", "value": 10, "meaning": "position"},
                  {"key": "fault", "value": 33, "meaning": "motor driver fault on bridge enable"}]}
```

- `State` has **no level and no code**. It makes no health claim: the host does not roll it up, debounce it into a
  fault, or draw it in a health bar. `name` is the group heading (empty = the entity's one group).
- `Measurement.meaning` (new, optional) is the human reading of a value, for enumerations and codes ("mode 10 =
  position", "fault 33 = motor driver fault on bridge enable"). The page draws `33 · motor driver fault on bridge enable`.
  A value is never reduced to a colour word.
- A limit (`warn_*` / `error_*`) colours that State row only when the value is beyond it (`Measurement.level`). It
  creates no status and no fault.
- `expected_period_s` is the staleness promise of the group, judged on the host's receive time, with the same rule as a
  status (`max(3 × period, 5 s)`). A stale group is said plainly ("last values 40 s ago"), never in a warning colour.

### Which values move to Measurements, so a value is never shown as health

| Today (a `status`) | Becomes |
|---|---|
| a number that changes while the device runs, written as a `values` string ("voltage": "24.1 V") | a `Measurement` in a `state` record |
| a status whose only job is to show a number ("temperature: 41 °C — ok") | a `Measurement`; a status stays only if the device makes a **claim** about it |
| an enumeration or code in a status ("mode": "10") | a `Measurement` with `meaning` |
| the verdict about a number ("bus voltage low", with a code) | stays a `status`; its message may cite the number; the number is also a `Measurement` |
| what explains a verdict (`what_to_check`, `port_holders`, versions, a command line, an id) | stays in the status `values` (Diagnostics) |

Diagnostics rows show name, message, level and the code's meaning; State rows show name, value, unit and a sparkline.
Neither section draws the other's field. A device fault code travels in `values` as `fault_code` (the number) and
`fault_meaning` (one human sentence); the Diagnostics row draws `33 · motor driver fault on bridge enable`.

## 2. Configuration is read-only and the same for every driver

```json
{"record": "config", "path": "can/1", "key": "servo.max_current_A", "value": 20.0, "unit": "A", "group": "servo",
 "expected": 25.0, "expected_source": "deployment record rack-2026-10-21", "stamp": 1761095500.0}
```

- `value` and `expected` are JSON scalars. `group` (optional) is a heading. `expected_source` names where `expected` came
  from (a deployment record, or "expected setup"). `stamp` is when the driver read the value.
- The host keeps the latest record per `(path, key)`; a key a driver stops sending stays, with its old stamp.
  `differs` is host-computed: numbers compare with a 1e-9 relative tolerance, everything else as text.
- Writing a setting is the `update_config` utility, never this record. The page offers search, grouping, a mark on a
  value that differs from `expected` (with the expected value and its source) and Copy as text.

## 3. Image state

A plugin declares its streams per entity in `describe` (paths relative to the device, `""` = the device itself):

```json
{"streams": [{"path": "", "streams": [{"name": "color", "kind": "image", "title": "Color"},
                                      {"name": "depth", "kind": "image", "title": "Depth", "colormap": "turbo"}]}]}
```

Frames come from the plugin as **files** in a directory the host names for the watch (`IO_DIAGNOSTICS_FRAME_DIR`), plus
one metadata record per frame:

```json
{"record": "frame", "path": "", "stream": "color", "file": "color.jpg", "format": "jpeg", "width": 640, "height": 480,
 "stamp": 1761095510.3, "seq": 41}
```

The plugin writes the file to a temporary name in that directory and renames it over `file` (atomic: a reader sees the
old frame or the new one, never half). `file` is a plain file name in the directory. The host reads it only when a page
asks.

WHY files and not bytes on the record fd: the follower reads lines of at most 256 KB, and a base64 JPEG at 5–30 Hz would
be rejected or re-parsed every frame; an atomic rename is crash-safe (a plugin that dies leaves its last whole frame),
works from any language, and keeps the JSON stream small enough to apply at 10 Hz.

- `GET /api/v1/frames?path=&stream=` → the latest frame, `image/jpeg` or `image/png`, headers `X-Frame-Seq`,
  `X-Frame-Stamp`, `X-Frame-Width`, `X-Frame-Height`; 404 if there is none.
- `GET /api/v1/frames/stream?path=&stream=[&fps=N]` → `multipart/x-mixed-replace` (MJPEG): each new frame once.
- **Hyperspectral-ready:** a stream may declare `bands` (wavelengths in nm) and `preview_band` (index); a `frame` record
  may say which `band` its preview shows. The page shows the preview image; a per-pixel spectrum
  (`GET /api/v1/frames/spectrum?path=&stream=&x=&y=`, from a cube file the plugin names) is reserved and not built.

## 4. Live while viewing: `watch`

A plugin that can stream declares `"watch": {"period_s": 0.1, "gates": ["port_free", "bus_lock_free"]}` in `describe`
(`danger` is `query` and the host refuses anything else). `gates` are the host's passive checks (`port_free`,
`bus_lock_free`, `camera_free`) that must pass before it runs.

- `POST /api/v1/watch {"path": "<entity path>"}` is the page's heartbeat (page-only, like `/act`). It answers
  `{"watch": {"state", "reason", "holders", "pid", "grace_s", "period_s"}}`. A page sends one about every 3 s while the
  entity is open and visible, and none after it leaves.
- While any page watches a path, the host runs `<command> watch --device <id> --path <relative path>` (a long-running
  process, restarted with a backoff like `follow`). Its `state`, `status`, `config`, `frame` and `event` records are applied
  as they arrive. **It stops about 10 s after the last heartbeat.** It never runs on a schedule and never without a page.
- **Gates.** If a person's tool holds the port or camera, the rack bus lock is held, or a run of the plugin's `exclusive`
  group is active, the host does not run it (or stops it) and the state is `paused` with the reason in plain words
  ("tview has the port (pid 640623)"); it resumes by itself when the gate clears. A plugin that finds the port busy
  itself prints a `PortBusy` / `BusLockHeld` status and the host reads the holders from it (the probe's rule).
- `GET /api/v1/values?path=` (cheap, no tree build) returns the latest State rows with a recent series each (about two
  minutes, for the sparkline), the streams with their size and fps, `heard` (when the host last received anything from
  this entity: the page's ticking "heard 2 s ago") and `watch`.

## 5. Log

The fourth tab is this entity's Activity (`GET /api/v1/activity`): events whose `path` is the entity or below it,
newest first, with a Raw channel (the source lines an event folds: the kernel line, `merged` raw enumerations, the full
`args`). A line that raised or cleared a fault (`FaultConfirmed` / `FaultHealed`) links to that fault's row in
Diagnostics, and the row links back to its line. The Activity lane's per-entity scope replaces the filter here once it
is on main.

## 6. USB labels

Wherever a USB link is drawn: from the device's `bcdUSB` (sysfs `version`) and the link speed. 1.5 / 12 Mb/s →
**USB 1.1**; 480 Mb/s → **USB 2.0** (the device's own 2.1 when `bcdUSB` says so); 5 Gb/s → **USB 3.2 Gen 1**; 10 Gb/s →
**USB 3.2 Gen 2**; 20 Gb/s → **USB 3.2 Gen 2x2**; 40 Gb/s → **USB4**. The raw speed is the tooltip.

## 7. Tabs

The header stays above the tabs and is always visible: the name and model, the connection badge (`USB 2.1`, `CAN 1 · fdcanusb
FDA81D29`), the ticking last-heard clock (`heard 2 s ago`, from `heard` in `node` and `values`), the Utilities menu and a
hardware line (serial, firmware, model id; the rest one click away).

1. **Diagnostics** (default) 2. **State** 3. **Configuration** 4. **Log** 5. **Utilities** (every action with its danger
class, whether it can run now and its last run; a click is the page's usual dry run first), then the driver's own tabs.

- **Driver tabs** are declared in `describe`: `"tabs": [{"id": "processes", "title": "Processes", "path": "", "kind":
  "table", "status": "Processes", "values": ["top_cpu", "top_memory"]}]`. `path` is relative to the device (`""`, or
  `"host"` for the Computer). A `table` tab draws each named value of the named status as a sortable table: a tab-separated
  text whose first line is the header; a column named `*_bytes` is drawn as bytes, `*_percent` as %. The Computer's
  `computer` plugin declares **Processes**. A camera's streams stay in State.
- **Badges**, quiet, none when all is well: Diagnostics shows the worst non-OK level's mark and the count of rows not OK;
  Log the number of warnings or errors that arrived since the tab was last shown.
- **Navigation:** the tab is in the URL (`?page=<id>`, so back and forward work); it is remembered per entity kind, so
  another board opens on the same tab. Ctrl+PageUp / Ctrl+PageDown, or 1-9 / the arrow keys with the tab bar focused.
- **Live data:** the `watch` heartbeat runs only while the State tab is showing. Diagnostics, Log and a driver tab read what
  the host already polls.

## 8. What a driver implements (moteus_servo, intel_rgbd)

1. `describe`: `"record_version": "0.2"`, `watch {period_s, gates}`, `streams` (RealSense: color, depth, ir).
2. `watch --device D --path P` (query-only): print `state` records (and `frame` records) at `period_s`, `status` on change,
   one blank line when idle (liveness); exit on SIGTERM; honour the gates yourself too.
3. `diagnose`: print `config` records (with `expected` from `IO_DIAGNOSTICS_EXPECTED` and its source) and move numeric
   `values` into `state` records per section 1.
