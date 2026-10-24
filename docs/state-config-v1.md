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

## 2. Configuration is the same for every driver

```json
{"record": "config", "path": "can/1", "key": "servo.max_current_A", "value": 20.0, "unit": "A", "group": "servo",
 "expected": 25.0, "expected_source": "deployment record rack-2026-10-21", "stamp": 1761095500.0}
```

- `value` and `expected` are JSON scalars. `group` (optional) is a heading. `expected_source` names where `expected` came
  from (a deployment record, or "expected setup"). `stamp` is when the driver read the value.
- The host keeps the latest record per `(path, key)`; a key a driver stops sending stays, with its old stamp.
  `differs` is host-computed: numbers compare with a 1e-9 relative tolerance, everything else as text.
- A `config` record never writes anything. Editing is section 8: the same record says whether a setting is `writable`, its
  `type`, `stored` value and so on, and the writes are actions of an edit session. The page offers search, dot-notation
  folders (section 9), a mark on a value that differs from `expected` (with the expected value and its source) and Copy as text.

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
- **The host's own watch is never a holder.** A `watch` (and a `follow`) holds the port or the camera for the page's sake, so the
  host's `port_free` / `camera_free` gates ignore a holder that is one of its own watch processes (they are told apart by session,
  like the bus lock's own-check rule); a person's tool is still a holder. A driver may therefore declare
  `"gates": ["port_free", "bus_lock_free"]` for its watch and for `config_write`.
- **A run on the same board takes the board from the watch.** Before a configuration write (`set_config`, `persist_config`,
  `reset_config`) or any other act of the same plugin and device runs, the host stops every watch of that device and waits for
  the process to exit (SIGKILL of its process group), so the driver finds the port free at once; when the run ends it starts the
  watch again if the page is still sending heartbeats (the State tab is open), otherwise the watch ends at its grace like any
  other. Nothing waits for the grace period to run out. The stop happens when the run *starts* (after it has its `exclusive`
  group), not when it is pressed, so the State tab keeps moving while a press queues behind a routine diagnose.
- **Depth colour map.** Beside the frame directory the host puts the app's depth colour choice in every watch's environment
  (2026-10-24; the setting is `depth_colormap` etc. of `GET /api/v1/settings`): `IO_DIAGNOSTICS_DEPTH_COLORMAP` (a realsense-viewer
  colour scheme name: Jet, Classic, White to Black, Black to White, Bio, Cold, Warm, Quantized, Pattern, Hue, or Turbo),
  `IO_DIAGNOSTICS_DEPTH_EQUALIZE` (`1` / `0`), `IO_DIAGNOSTICS_DEPTH_MIN_M` and `IO_DIAGNOSTICS_DEPTH_MAX_M` (metres, used when
  equalization is off). A driver that draws depth uses them only under record revision 0.2 (the compatibility guard, section 10);
  without the variable it draws what it always did (RealSense: its turbo map), so its output is byte-for-byte unchanged. The host
  restarts a running watch when the person changes the setting: a watch reads its environment once, at start. A driver that does not
  draw depth ignores them. The RealSense driver uses the SDK's `rs.colorizer` (`color_scheme`, `min_distance`, `max_distance`,
  `histogram_equalization_enabled`), the filter realsense-viewer draws depth with.
- `GET /api/v1/values?path=` (cheap, no tree build) returns the latest State rows with a recent series each (about two
  minutes, for the sparkline), the streams with their size and fps, `heard` (when the host last received anything from
  this entity: the page's ticking "heard 2 s ago") and `watch`.

## 5. Log

The fourth tab is this entity's Activity: the Activity log's own compact view (`IOActivityLog.create({scope, compact})`,
docs/activity-log-v1.md), scoped to the entity and everything below it, with its Raw channel (the kernel's lines and the
output of its runs). A configuration write is a line of it (type `config`). A log line that raised or cleared a fault (the fault
badge on its row) opens that fault in Diagnostics, and the Diagnostics row of a fault opens its line here.

## 6. USB labels

Wherever a USB link is drawn it is named by version, never by speed: 1.5 / 12 Mb/s **USB 1.1**; 480 Mb/s **USB 2.0** (the
device's own 2.1 when its `bcdUSB` says so); 5 Gb/s **USB 3.2 Gen 1**; 10 Gb/s **USB 3.2 Gen 2**; 20 Gb/s **USB 3.2 Gen 2x2**;
40 Gb/s **USB4**; the raw speed is the tooltip. There is **one mapping, `web/usb.js`** (the entity-tree lane's `window.IOUsb`);
the host sends the raw link (`badge.usb = {speed_mbps, version}` in `node`) and the page names it. An earlier Python mapping of this
lane was dropped in favour of it.

## 7. Tabs

The header stays above the tabs and is always visible: the name and model, the connection badge (`USB 2.1`, `CAN 1 · fdcanusb
FDA81D29`), the ticking last-heard clock (`heard 2 s ago`, from `heard` in `node` and `values`), the Utilities menu and a
hardware line (serial, firmware, model id; the rest one click away).

1. **Diagnostics** (default) 2. **State** 3. **Configuration** 4. **Log** 5. **Utilities** (every action with its danger
class, whether it can run now and its last run; a click is the page's usual dry run first), then the driver's own tabs.

- **Driver tabs** are declared in `describe`: `"tabs": [{"id": "processes", "title": "Processes", "path": "", "kind":
  "table", "status": "Processes", "values": ["top_cpu", "top_memory"]}]`. `path` is relative to the device (`""`, or
  `"host"` for the Computer). A `table` tab draws each named value of the named status (its name, or the part after "<who>: ") as a sortable table: a tab-separated
  text whose first line is the header; a column named `*_bytes` is drawn as bytes, `*_percent` as %. The Computer's
  `computer` plugin declares **Processes**. A camera's streams stay in State.
- **Badges**, quiet, none when all is well: Diagnostics shows the worst non-OK level's mark and the count of rows not OK;
  Log the number of warnings or errors that arrived since the tab was last shown.
- **Navigation:** the tab is in the URL (`?page=<id>`, so back and forward work); it is remembered per entity kind, so
  another board opens on the same tab. Ctrl+PageUp / Ctrl+PageDown, or 1-9 / the arrow keys with the tab bar focused.
- **Live data:** the `watch` heartbeat runs only while the State tab is showing. Diagnostics, Log and a driver tab read what
  the host already polls.

## 8. Editing a setting (the Configuration tab)

The moteus model (tview's `conf set` is live in RAM; `conf write` persists), made safe. A `config` record says how a setting may be
edited and what the board stores:

| field | meaning |
|---|---|
| `value` | what the board runs now (live) |
| `stored` | what it keeps across a restart; `value != stored` is **not saved** |
| `writable` | the page may offer an edit (default false: the setting is read-only) |
| `type` | `int` `float` `bool` `enum` `string`: the page's input, and the host's check before anything is sent |
| `range` / `choices` | `(low, high)` of a number, the allowed values of an enum; a range is the **board's** to enforce (it may clamp) |
| `default` | the factory default, when the driver knows it |
| `safety` | true for a safety key (the interlock's set: rotor_to_output_ratio, kp / kd / ki, max current, output sign, watchdog, position limits) |

**Three actions** (standard names, `docs/actions-vocabulary-v0.1.md`, danger `changes`, scope node): `set_config` (`--key K --value V`),
`persist_config` (moteus `conf write`), `reset_config` (`--to stored|expected|default` [`--key K`]; no key and `--to stored` =
every setting). The driver prints the `config` records it now holds (the read-back) and exits 0, or prints an event
`ConfigRejected` and exits 1. They are driven by the edit session, not offered as menu items or in the Utilities tab.

**The edit session** (all page-only like `/act`; a script, another origin or another site gets **403**):
- `POST /api/v1/config/edit {path, op: "open", confirm: true}` -> `{token, board, grace_s}`: one confirm naming the board.
  `{op: "beat", token}` every 3 s keeps it; `{op: "close", token}` or `grace_s` (10 s) of silence ends it. The page ends it when
  the Configuration tab or the entity is left. One session per entity; a second replaces the first.
- Every write needs the token: `POST /api/v1/config/set {path, token, key, value, confirm_safety}`,
  `/config/persist {path, token}`, `/config/reset {path, token, to, key?, confirm_safety}`. No token or a dead one: **403
  `NoEditSession`**. A refusal the host can explain ("the board is not stopped") is a **200** answer with `refused`.

**What each setting says** (`config[].edit` and the answer of a write): **pending** (sent), **accepted** (the read-back equals what
was set), **rejected** (an error, or the board kept another value: `the board kept 10.0 (asked 50.0)`), **refused** (never sent:
a gate, the safety rule, a bad value), and **not saved** (`value != stored`, from `unsaved`). A bar collects them: "3 changes live,
not saved · Save to board · Revert all".

**What the host guarantees**, whatever the page does:
1. a write is a **run** of the plugin's own action (a recording, `RunStarted` / `RunEnded`), preceded by a dry run of the same
   write for the plugin's own confirm token, and goes through the same **gates** as a watch: a person's tool holding the port,
   the rack bus lock, an active run of the plugin's exclusive group (describe `config_write: {gates: [...]}`, default
   `port_free, bus_lock_free`);
2. a **safety key** is written only with `confirm_safety` (the page's second confirm), when the board's State reported
   `mode` 0 (stopped) within its own staleness window, and with no run active on its bus; an unknown or old mode is refused;
3. the value must be of the row's `type` (an enum: one of `choices`) or nothing is sent;
4. **every write is an Activity event** (type `config`: `ConfigSet`, `ConfigRejected`, `ConfigPersisted`, `ConfigReset`,
   `ConfigEditStarted`, `ConfigEditEnded`) with who (`by: page`), the key, old -> new, what the board kept, accepted or not,
   and the run's id.

## 9. One row model, and the arrangement

Diagnostics, State and Configuration rows share one model (Zach, 2026-10-24: "tview has groupings ... dot notation
arm.servo.current and you can parse a folder structure from that"):
- a name in **dot notation** (identifier characters, `.`, no spaces or colons) is a leaf in a **folder** tree; a chain of folders that
  each hold one folder is one row (`aux1.pins.0`); any other name stays at the top level;
- order inside a folder: **A-Z** (folders first, numbers in natural order) by default, **manual** (drag a row by its handle),
  and **starred** rows first in a Pinned block; Diagnostics also has **Severity**, its default;
- the arrangement is `{pins, order: {folder path: [names]}, folded: [folder paths], sort}` per **entity kind** and section
  (`diagnostics`, `state`, `config`), so every CAN board shares one. `GET /api/v1/arrangements` and the page-only `POST
  /api/v1/arrangements {kind, section, value}` keep it in `arrangements.json` beside `settings.toml` (written atomically; a bad
  value is **400**). When the host cannot keep it (an older host, a read-only home) the page keeps it in this browser's
  localStorage and says so in the Order control's tooltip. WHY a file of its own and not settings.toml: that file's writer is the View
  options' and rewrites one setting whole.

## 10. What a driver implements (moteus_servo, intel_rgbd)

**The compatibility guard.** The host sets `IO_DIAGNOSTICS_RECORD_VERSION=0.2` (the `RECORD_VERSION` it understands) in **every**
plugin invocation: `describe`, `diagnose`, `probe`, `watch`, `follow`, `act` and `stop`. A driver emits the record kinds and the
describe fields newer than 0.1 (`state`, `config`, `frame`; `watch`, `streams`, `tabs`, `config_write`; the `set_config`,
`persist_config`, `reset_config` actions) **only when the variable says 0.2 or later**. Without it (an older host, such as Stable's
release, or a driver run by hand) its output stays exactly what it was, so a driver that lands first cannot break an older host. The
fixture plugins in `tests/fixtures/plugins-state/` honour it the same way (`tests/test_state_config.py`).
 (moteus_servo, intel_rgbd)

1. `describe`: `"record_version": "0.2"`, `watch {period_s, gates}`, `streams` (RealSense: color, depth, ir).
2. `watch --device D --path P` (query-only): print `state` records (and `frame` records) at `period_s`, `status` on change,
   one blank line when idle (liveness); exit on SIGTERM; honour the gates yourself too.
3. `diagnose`: print `config` records (with `expected` from `IO_DIAGNOSTICS_EXPECTED` and its source, and `writable`, `type`,
   `range` / `choices`, `default`, `stored`, `safety` per setting) and move numeric `values` into `state` records per section 1.
4. `set_config`, `persist_config`, `reset_config` (section 8): register them on the board; read the value back after every
   write and print the `config` record; report the board's `mode` as a State measurement named `mode` (0 = stopped) so the safety
   rule can be judged; declare `config_write: {gates: [...]}` in `describe`; check the interlock yourself too.
5. `describe` may declare `tabs` (section 7).
6. **A long `diagnose` is fine for the host and the page, but two numbers are the driver's.** Measured on the fixture with the
   real shape (a 25 s bus check, then a 20 s configuration stage of 250 `config` records per board): the host applies a run's
   records when the run ends, so a run that outlives `timeout_s` is killed and **keeps nothing** (including the statuses that were
   printed before the kill; they age toward STALE): set `timeout_s` above the worst case (60 s for 45 s). Records are judged STALE
   after three times their `expected_period_s`, so a 45 s run on a 30 s period stays fresh. A person's press (a configuration
   write) waits for a routine run of the same `exclusive` group to finish: up to the run's length, about 22 s on average for a
   45 s run. Keep the configuration stage short (read it every Nth run, not every run) if that wait matters.
