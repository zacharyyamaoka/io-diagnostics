# Diagnostics record v0

*2026-10-22. Contract: `contract/record.py` (`SCHEMA = "diagnostics-record/0"`). Tests: `tests/test_record.py`.
Frozen for the IO v1 build: additive changes only (see Versioning). Evidence: `docs/research/diagnostics-schema-ros.md`
and `docs/research/diagnostics-schema-standards.md`, plus the primary sources re-checked for this spec (last section).*

## The answer

ros2_medkit and the other modern efforts did **not** change the message. `diagnostic_msgs/DiagnosticStatus` is
byte-identical on Humble, Jazzy and Rolling. Its only field-level change since 2009 is the `STALE=3` constant
(Indigo, 2014). What changed is everything around it. medkit turns `/diagnostics` into SOVD-style faults with a
lifecycle. Autoware builds a graph over the untouched leaf. ros2_control, DroneCAN and MAVLink carry mode and typed
state beside it. The field converged on the same moves:

- keep the status as the leaf stream;
- take three jobs off `level`: severity, freshness and readiness;
- give every non-OK report a machine code;
- type the numbers;
- make identity explicit, with two kinds of parent;
- give faults and events records of their own.

v0 is exactly that. It keeps ROS's five fields unchanged, adds nine optional fields beside them, and adds four
sibling records.

## Eight lessons, and who learned them

1. **Wrap the wire format; don't fork it.**
   - Seventeen years and one added constant. The 2025 proposal for more levels
     ([common_interfaces #268](https://github.com/ros2/common_interfaces/issues/268), still open) stalled because
     inserting values renumbers recorded bags and breaks the aggregator's numeric order.
   - Every newer project kept the message and built beside it: medkit `Fault`/`FaultEvent`/`Snapshot`, Autoware
     `DiagGraphStruct`/`DiagGraphStatus`, ros2_control `HardwareStatus`.
   - SOVD did the same with UDS. AUTOSAR R24-11 calls it "UDS as a subset".
2. **`level` carried three things. Split severity, freshness and readiness.**
   - Putting STALE (3) above ERROR (2) on one axis made the ROS aggregator report ERROR for any partly-stale group
     ([#297](https://github.com/ros/diagnostics/issues/297), 2023). The fix, [#593](https://github.com/ros/diagnostics/pull/593),
     was merged 2026-05-20 with the rule "WARN/ERROR always beats STALE".
   - Today `GenericAnalyzerBase::report()` on the `ros2` branch still turns a partly-stale group into ERROR: one
     codebase, two rules. Whether WARN should beat STALE is still open ([#658](https://github.com/ros/diagnostics/issues/658)).
   - DroneCAN `NodeStatus` (health + mode + uptime), Cyphal, MAVLink (`MAV_STATE` vs `SYS_STATUS` health), Redfish
     (`Health` vs `State`) and ros2_control (`health_status` vs `operational_mode`) always kept them apart.
3. **"Can't tell" is never green. Staleness is judged by the observer against a declared period.**
   - A zero value that reads green is a trap; the zero value must be UNKNOWN. ros2_control `HEALTH_UNKNOWN = 0`,
     MAVLink `MAV_STATE_UNINIT = 0`, K8s ("absence of a condition = Unknown") and UDS (0x50 after a clear: only the
     "not completed" bits) all avoid it.
   - Freshness needs a promise to judge against: DroneCAN's `OFFLINE_TIMEOUT_MS = 3000` against a ≤ 1 s period,
     Entity-Sensor MIB `ValueUpdateRate` (2002), Redfish `SensingInterval`, Greenwave's `expected_frequency`,
     rosgraph_monitor's Deadline QoS. Lichtblick computes staleness client-side from the array stamp, so an unstamped
     array is instantly stale ([#341](https://github.com/ros/diagnostics/pull/341)).
4. **Every non-OK report needs a stable machine code beside the human message.**
   - K8s made `reason` required on `metav1.Condition` (KEP-1623). Redfish's only required Condition field is
     `MessageId`. SOVD faults key on `code` (from the UDS DTC). medkit has `fault_code`; DroneCAN
     `vendor_specific_status_code`.
   - Without a code you cannot count, dedupe, debounce, suppress or translate.
5. **Status, event and fault are three shapes.**
   - "A status is information; a fault is a model you can query and act on" (medkit, "Why we did not extend
     /diagnostics", 2026-09).
   - UDS/AUTOSAR DEM keep the current test result (`testFailed`) apart from the stored fault (`confirmedDTC`), with
     debounce, healing, aging and freeze frames. Apex.Grace has the same chain: monitor → DFM → DTC.
   - The "more levels" fight was mostly people wanting events (INFO, NOTICE) inside a status.
   - Field-tested traps: time and count debounce are not interchangeable. Count debounce never confirms or heals an
     edge reporter, which is documented in medkit's README. A check that did not run must not count as a pass.
6. **Type the numbers, or every consumer re-parses strings.**
   - ros2_control packs `"Avg: … [min - max] us, StdDev: …"` into one value. The InfluxDB exporter guesses which
     values are numbers. Greenwave relies on unversioned magic keys.
   - Your own `bam_diagnostics` flattened its typed thresholds into key strings (`"mean_max [50.0, 60.0]"` →
     `"52.000"`).
   - Redfish `Sensor` (UCUM units, thresholds in the same unit, `ReadingTime`), Nagios perfdata (25 years) and
     MAVLink (units on every field) carry a typed value. OpenSOVD started with string pairs and added a typed status.
7. **Identity is minimal, repeatable across observers, and has two kinds of parent.**
   - `hardware_id` has been used as a serial, as a node FQN and as an empty string
     ([#520](https://github.com/ros/diagnostics/issues/520)), and tools key on it, so an empty first message
     duplicates a row (#666). The aggregator rewrites `name`.
   - OTel's entity model: "Two independent observers that report the same entity MUST be able to supply identical
     values for all identifying attributes". On this rack the D405's USB serial ≠ its SDK serial.
   - Home Assistant (Aug 2026) split `via_device_id` (*connectivity*) from child devices (*composition*). Children
     carry no connectivity or hardware identity of their own.
   - Send identity once, not in every report: Autoware struct vs status, DroneCAN `GetNodeInfo` vs `NodeStatus`.
8. **Rollup is the observer's job, follows a written rule, collapses knock-on failures, and respects black boxes.**
   - Redfish separates `Health` from `HealthRollup`; RFC 4268 separates raw from computed state. OpenBMC removed its
     rollup in 2024 because it is "nontrivial to get correct" and Redfish gives no algorithm.
   - Root cause, not symptoms: Nagios marks hosts behind a DOWN router UNREACHABLE, AUTOSAR DEM does not store a
     fault under a failed parent, and ROS's own aggregator has the two rules above.
   - Zach's rule (2026-10-22) sets whose judgement a level is: each black box self-reports what a failure means for
     that box. The host's rollup is only a hint.

## The shapes

| Shape | One sentence | Sent | Borrowed from |
|---|---|---|---|
| `DiagnosticStatus` | One check on one entity | Every period, or on change | ROS DiagnosticStatus, DroneCAN NodeStatus, Redfish Sensor |
| `Entity` | What and where a thing is, and the actions its owner registered | Once, and on change | HA device registry, OTel entity, Autoware struct, SOVD entity |
| `Event` | Something happened | When it happens | MAVLink events, Cyphal Record, Redfish MessageId + MessageArgs |
| `Fault` | The host's memory of one (entity, code) failing | On lifecycle transitions | ros2_medkit Fault, UDS DTC status, AUTOSAR DEM |
| `Action` | A utility the owner registers and runs | With its Entity | SOVD operations, HA button/update, printer-driver utilities |

```
driver / OS observer ── Entity (once) ─────────────────────────────┐
        │                                                           ▼
        └── DiagnosticStatus (periodic) ──► host: effective_level ──► summarize ──► rows: own level + rollup hint
                                                  └──► FaultManager ──► Fault + Event (FaultConfirmed, FaultHealed)
stock view:  to_diagnostic_status()  ──►  rqt · Lichtblick · Foxglove · medkit bridge
```

## Field tables

### DiagnosticStatus (one check on one entity)

| Field | Type | Required | Why it is here (lesson · source) | Stock DiagnosticStatus |
|---|---|---|---|---|
| `level` | `unknown \| ok \| warn \| error` | yes, default `unknown` | ROS `level` without STALE (derived), with UNKNOWN as the zero value (L2, L3) | `ok→0 warn→1 error→2 unknown→3` (STALE) |
| `name` | str | yes | ROS `name`: the check as a person reads it, REP-107 `component: subcomponent` | `name` |
| `message` | str | yes, may be "" | ROS `message`: a rendering of code + values (K8s, Redfish `Message`) | `message` |
| `hardware_id` | str | for physical things | ROS `hardware_id` in REP-107's original sense: the repeatable identifier (L7) | `hardware_id` |
| `values` | `KeyValue[]` | no | ROS `values`: strings for people ("the intended consumer is a person", REP-107) | `values`, first |
| `measurements` | `Measurement[]` | no | Typed numbers with unit and limits (L6) | Appended to `values` as `key: "37.2 V"` |
| `path` | str | yes, for native records | Where it is: the entity key (L7). Machines key on it, never on `name` | stripped |
| `code` | str, PascalCase, ≤ 64 | when `level != ok` | Stable machine reason (L4) | stripped |
| `mode` | `operational \| initialization \| maintenance \| software_update \| offline` | no | Readiness, not severity: DroneCAN `MODE_*` names (L2) | stripped |
| `stamp` | float, Unix s | recommended | Source time of the evaluation: Redfish `ReadingTime`. ROS has only one stamp per array | stripped (the array header keeps one) |
| `received` | float, Unix s | set by the observer | Observer's clock, which staleness uses: OTel `ObservedTimestamp` | stripped |
| `expected_period_s` | float or None | for periodic checks | The promise staleness is judged against (L3); your `dx_tick_hz` | stripped |
| `uptime_s` | float | no | Exposes a restart or brown-out that a level hides: DroneCAN/Cyphal uptime | stripped |
| `source` | str | no | Who reported it, when two observers report on one entity: medkit `source_id`, your `source_component` | stripped |
| `caused_by` | path[] | composites (v0.1 addendum) | The members whose own reports caused this verdict, so one fact counts once (AUTOSAR DEM consecutive fault, Icinga `--root-cause`) | one value `caused_by`, appended last |

`DiagnosticStatus.OK / WARN / ERROR / UNKNOWN` exist as class constants, so code reads like rclpy. There is
deliberately no `DiagnosticStatus.STALE`.

### Measurement (a typed value)

| Field | Type | Why |
|---|---|---|
| `key` | str | Unique across `values` and `measurements` (`validate` checks) |
| `value` | float, int, bool or None | None = could not be read now (Nagios `U`, HA unavailable). A sensor that is lying is the record's level + code, not a value flag |
| `unit` | UCUM string | Redfish `ReadingUnits` and OTel use UCUM: `V`, `A`, `Cel`, `Hz`, `s`, `ms`, `%`, `1` |
| `stamp` | float | When this value was read, if not the record's `stamp`: the "last read 18:13 by rack_inventory" case from V6's `Reading` |
| `warn_low`, `warn_high`, `error_low`, `error_high` | float | Limits in the value's unit (Redfish Caution/Critical, IPMI nc/cr, your `DiagnosticValue(warn, error)`). `Measurement.level`: a value strictly beyond a limit trips it; equal is inside. Unreadable or NaN = `unknown` |

### Entity (what and where; sent once and on change)

| Field | Type | Why |
|---|---|---|
| `path` | str | The key every status, event and fault refers to. Position in the tree; it changes when a device is replugged |
| `name`, `kind` | str | Label and icon (host / port / device / bus / node / part / stream) |
| `via` | path or None | **Connectivity** parent: the port, hub, adapter or bus it is reached through (HA `via_device_id`). This is the knock-on path |
| `part_of` | path or None | **Composition** parent: the product it is a logical part of (HA child devices). One parent edge per entity |
| `hardware_id` | str | What it is: repeatable by every observer. Parts carry none |
| `identity` | `{manufacturer, model, serial_number, hw_version, sw_version}` | Descriptive, may change, not identifying (HA `device_info`, OTel descriptive attributes). Parts carry none |
| `owner` | str | Who registered it and executes its actions |
| `actions` | `Action[]` | See Actions |

### Event (something happened)

| Field | Type | Why |
|---|---|---|
| `code` | str, PascalCase | `Plugged`, `Unplugged`, `ReEnumerated`, `Restarted`, `FaultConfirmed`, … |
| `stamp` | float | Required |
| `severity` | `info \| warn \| error` | Log severity. INFO lives here, never in a status level |
| `message`, `args` | str, dict | The message renders code + args (Redfish MessageArgs, HA translation placeholders) |
| `path`, `hardware_id` | str | `hardware_id` ties an unplug to the device after its path is gone |
| `seq`, `source` | int, str | Per-source sequence: a jump means events were lost (MAVLink events) |

### Fault (host memory; never sent by a driver)

| Field | Type | Why |
|---|---|---|
| `path`, `code` | str | The key: (entity, code), not a global code. medkit keys globally, so a code shared by two entities only shows on its global route |
| `severity` | `warn \| error` | Level of the latest failing report |
| `status` | `pending \| confirmed \| healed \| cleared` | medkit/SOVD names; see the lifecycle |
| `active` | bool | Failing in the latest evaluated report (UDS `testFailed`). A confirmed fault can be idle |
| `occurrence_count` | int | Confirmed occurrences, counted on edges, not reports (medkit) |
| `first_occurred`, `last_occurred`, `last_passed` | float | When the current occurrence started, last failed, last passed (medkit `Fault`) |
| `message`, `hardware_id` | str | |
| `snapshot` | dict | Freeze frame: `to_dict()` of the status that confirmed it (DEM freeze frame, medkit #668) |

### Action (registered by the owner)

The same dataclass V6 and the three proposed drivers already use, moved here. `contract/diagnostics.py` re-exports
it, so there is one definition.

| Field | Why |
|---|---|
| `name`, `title`, `description` | Machine name (`clear_faults`), button label, what it does |
| `danger` | `query` (reads only) · `interrupts` (drops the device for a moment) · `changes` (writes state) |
| `scope` | Which of the driver's entities it attaches to (`device`, `node`): a driver registers once for many entities |
| `confirm` | Confirm text with `{name}`, `{id}`, `{serial}`, `{latest}` placeholders |
| `preconditions` | `(check, text)` pairs the host evaluates passively: `port_free`, `bus_lock_free`, `camera_free`, `board_answers`, `update_available` |
| `kind` | Gate class: `passive` / `touches` / `changes` |

## Rules

### 1. Levels

- A producer reports `unknown`, `ok`, `warn` or `error`. The zero value is `unknown`, so a record nobody evaluated
  is never green. A check blocked by a held port reports `unknown` with a code (`PortBusy`): "not evaluated, and
  why".
- **STALE is never authored.** The observer derives it (rule 2). A stock message that says STALE comes in as
  `unknown`, which is what it meant.
- **Order, worst last: `ok < warn < unknown < stale < error`.**
  - Can't-tell outranks WARN because silence can hide an error.
  - It stays below ERROR because nothing has shown it broken.
  - `stale` outranks `unknown` because it had data and lost it.
  - ROS #593 chose WARN above STALE, and #658 is still open on exactly this. One line (`RANK`) changes it.
- **Stock projection:** `unknown` ↔ STALE (3), a bijection. Caveat: medkit's bridge maps STALE to a CRITICAL fault.
  If medkit listens, configure its per-name STALE severity (#668), or filter.

### 2. Staleness

- `effective_level(status, now)` is STALE once `now − received` exceeds `max(3 × expected_period_s, 5 s)`. It falls
  back to `stamp` when `received` is unset. A record with a period but no time at all is stale. An event-driven
  record (`expected_period_s = None`) never goes stale.
- It is judged on the observer's receive clock: a producer's clock drifts and can be unset. `stamp` is for ordering,
  plots and latency. That answers the open question in your old `DiagnosticClient`: "is it from the time it was
  received that matters? or the time it was published?"
- A deliberate goodbye is a last record with `mode: offline` and no period. It never goes stale, and the UI greys
  it out (not connected = fully greyed).

### 3. Composition: black boxes self-report (Zach, 2026-10-22)

> "In black-box design it's up to each lower-level black box to self-report what it means in the context of THAT
> black box. You can group those entities into larger entities and make your own diagnostics wrapper: say a robot
> with 8 legs, 1 leg says failure, the spider can still report warning."

`summarize(entities, statuses, now)` returns one `Summary` per entity:

- **`own`** is the entity's self-report: the worst effective level of its own records, or None when it has no
  records of its own (a pure grouping node).
- **`level`** is what the row shows: `own` when the entity self-reports, otherwise `rollup.level`. **The host never
  overrides a self-report.** It only marks one STALE when it is overdue, which annotates the claim's age, not its
  content.
- **`rollup`** is advisory, like Redfish `HealthRollup` next to `Health`. It holds the worst *independent cause* at
  or below the entity, its origin path, counts per level, and the collapsed knock-on list. A row shows it as a hint
  when it is worse than `level` ("a child is in ERROR").
- **Knock-on collapse.** A problem below a failed ancestor counts once, as that ancestor, and the rest go in
  `knock_on`. The ancestor is found on the `via` / `part_of` chain inside the subtree.
  - An ERROR ancestor explains anything below it.
  - A silent (`unknown`/`stale`) ancestor explains only silence below it. A fresh WARN or ERROR from below proves
    the path works and stays a cause.
  - Sources: AUTOSAR DEM consecutive faults, Nagios UNREACHABLE, `ifOperStatus lowerLayerDown`.

| Case (tests) | Result |
|---|---|
| Spider: own WARN (`LegFailed`), leg 3 ERROR | spider `level` WARN, `rollup` ERROR from leg 3. A grouping `cell` above shows ERROR (worst below) |
| fdcanusb 28B3A25D: adapter OK, bus ERROR `NoAck`, nodes stale/unknown/`NoReply` | host rollup = ERROR from the bus, counts `{error: 1}`, four knock-ons. The adapter row stays OK with an ERROR hint |
| Bus stale, node 1 stale, node 2 fresh ERROR (fault 33) | node 2 is its own cause (`{stale: 1, error: 1}`); only node 1 collapses |

**Deliberately unlike Autoware.** Autoware decides context in one central configured graph: and/or/remap per
operation mode, with hazard computed from leaf level × mode root. v0 puts the judgement in the black box that knows
what a failure means for it. A composite is a black box like any leaf (closure under composition). Where you want a
judgement at some level, write a wrapper there. A grouping node with no wrapper has no judgement, so it shows worst
below.

Surfaces: a tree row shows `level` plus the rollup hint. The top-bar chip answers "does anything need me?", so it
reads the root's `rollup`.

### 4. Identity and the two parent edges

- **`hardware_id` says what** a thing is. Every observer must produce the identical string (OTel). For a USB device,
  use what sysfs shows (`usb:<vid>:<pid>:<descriptor serial>`), never an SDK-only serial: the D405's SDK serial
  differs from its USB serial. The SDK serial goes in `identity.serial_number`.
- **`path` says where** a thing is. It changes on replug; `hardware_id` does not.
- **`via` is connectivity:** host → port → hub → fdcanusb → CAN bus → node. It answers "which wire" and is the
  knock-on path.
- **`part_of` is composition:** moteus node → driver, motor, encoder; D405 → colour and depth streams. Each entity
  has one parent edge. A part carries no `hardware_id` or `identity`, and parts are one level deep (HA child devices).
  A part's status records carry the physical parent's `hardware_id`, so stock viewers group them under the device.
- A record with no `path` (any stock `/diagnostics` message) attaches to the entity with the same `hardware_id`.
  That is the payoff of repeatable identity.
- Entities are sent once and on change. Status records refer to them by `path`.

### 5. Faults (`FaultManager`)

- **How a status stream becomes faults:**
  - A WARN or ERROR report is a FAILED result for (entity, `code`). `code` falls back to the check `name`.
  - An OK report is a PASSED result for the codes that check had open, and so is the same check failing with
    another code.
  - An UNKNOWN report, or silence, moves nothing. A check that did not run is not a pass (UDS "test not completed",
    medkit's NOT_EVALUATED).
- **Lifecycle:** `pending` →(debounce)→ `confirmed` →(`heal_count` passes)→ `healed`. `clear()` makes it `cleared`.
  A pending fault that passes is forgotten. A healed or cleared fault that fails again starts a new occurrence.
- **Debounce defaults** (`DEFAULT_DEBOUNCE`):
  - A WARN confirms on its 3rd consecutive failing report.
  - An ERROR confirms at once (DEM threshold 1; medkit lets ERROR through unfiltered).
  - A confirmed fault heals after 3 consecutive passes.
  - Per-code overrides via `rules={code: Debounce(...)}`.
- **Count rules vs time rules.** Count rules count *reports*, so they suit a stream that repeats. For an event-driven
  stream every count is 1. `Debounce(confirm_s=…)` confirms after failing continuously that long, and `tick(now)`
  applies it between reports. A periodic stream that went stale never confirms in `tick`, because silence is not
  evidence.
- A fault never feeds back into a status level: the self-report stands. Lifecycle transitions come out as `Event`s
  with `seq`.

### 6. Actions

- The entity's owner registers actions; the host shows them, evaluates preconditions, takes the gate (holder check +
  rack bus lock) and routes the click back to the owner. The host never speaks a device protocol. Whoever owns the
  port does the talking.
- **Update available** is a status check, not a new field: name `firmware`, code `UpdateAvailable`, level `warn`
  (never error), values `installed_version` and `latest_version` (HA update entity names). The `update_firmware`
  action sits beside it.

### 7. The stock projection

- `to_diagnostic_status(status)` returns the stock dict. It strips every added field ("it makes it backwards
  compatible") and appends measurements to `values` as strings, so stock viewers still show the numbers. `level` is an
  int; a ROS 2 Python bridge sends `bytes([level])`.
- `from_diagnostic_status(message, **added)` takes any stock message: a dict, or an rclpy message whose `level` is
  bytes. Levels outside 0-3 come in as `unknown`.
- `to_diagnostic_array(statuses, stamp)` requires the stamp. `from_diagnostic_array` copies the header stamp onto
  every record, so per-record time survives from then on.
- **Proved in `tests/test_record.py`:**
  - stock → v0 → stock is the identity, for 5 real-world messages and 2,000 fuzzed ones;
  - v0 → stock → v0 keeps the stock five, and keeps the measurements as their rendered strings;
  - `to_dict`/`from_dict` (JSON) is exact for all five shapes.
- The v1 build plan's `to_stock` should be this function, not a reimplementation.

### 8. Versioning

- `SCHEMA = "diagnostics-record/0"` travels in every `to_dict()` together with `record` (which shape). Readers refuse
  another number and ignore fields they do not know (open world).
- Additive changes keep the number: a new optional field with a default, or a new string enum value. Enums are
  strings, so adding a value never shifts another. That is the failure that froze ROS's levels.
- A breaking change becomes `/1`. Freeze as `/1` after the IO dashboard and one software component have used `/0`
  for a while.

### 9. What this record is not

- **Not a liveness channel.** REP-107: "not designed to be a keepalive". Use bond, DDS liveliness or a lease.
- **Not a safety path.** ISO 14229 via DEM: "monitor, event or DTC status cannot be used to control safety critical
  functionality".

## What `bam_diagnostics` already got right (like_keras archive, `lk/archive/common/diagnostics`, `lk/archive/msgs/diagnostic`)

Right, and kept:

- **Declare, don't scatter.** A typed `DiagnosticsConfig` dataclass of named tasks, with thresholds as data
  (`DiagnosticValue(warn, error)`, upper or lower bound): Redfish's thresholds-as-data idea.
- **A declared evaluation rate** (`dx_tick_hz`, default 1 Hz) and an automatic `component_heartbeat`: the expected
  period.
- **Count windows and time windows** (`queue_type="count" | "timed"`): both debounce flavours, before the field
  report that they are not interchangeable.
- **Its own `DiagnosticStatus`** added a per-record `timestamp` and a `source_component` kept apart from `name` and
  `hardware_id`: the per-record time and the reporter.
- **Lifecycle × execution × health as orthogonal dimensions** (`component_state_architecture.md`) and `ErrorInfo`
  with a namespaced code plus `error_msg` plus `fix_msg`: Redfish `MessageId` + `Message` + `Resolution`.
- **`DiagnosticClient` returned STALE for an empty window.** Absence was never green there.
- **Apex.Grace's monitor → DFM → DTC** was already the reference ("basically exactly what I have been thinking").

The traps it hit, which v0 closes:

- **Thresholds flattened into key strings:** `"mean_max [50.0, 60.0]"` → `"52.000"`.
- **`mergeSummary` takes a numeric max**, so STALE(3) beat ERROR(2), while `DiagnosticClient`'s own docstring
  ordered OK, STALE, WARN, ERROR.
- **"Not evaluated" reported as OK:** zero thresholds returned OK `"SKIPPING"`, and an empty window returned OK
  `"waiting for values..."`, with the STALE line commented out.
- **Receive time vs publish time** was left open.

## Deliberately left out

| Left out | Why | Revisit when |
|---|---|---|
| More severity levels (INFO, NOTICE, CRITICAL, FATAL) | ROS #268 stalled on exactly this; impact is contextual (the black box judges); INFO/NOTICE are events | Never for status; events already have `info` |
| STALE as an authored level | The observer derives it; authoring it put two axes on one field | — |
| A central configured graph (Autoware and/or/remap, hazard per mode) | Zach's black-box rule: the judgement lives in the box that knows; write a wrapper | A safety case needs a system-level verdict (that also needs a non-diagnostics channel) |
| Per-value validity enum (`ok/unavailable/invalid/untested`) | `value=None` is unavailable; "invalid" is the record's level + code; "untested" is `unknown` | A sensor needs to say "lying" per value |
| Hysteresis and dwell on limits (Redfish 2023.1, IPMI) | The producer applies them when it evaluates; the host does not re-evaluate limits in v0 | The host starts re-checking limits itself |
| Flap detection (Nagios) | Debounce plus the flap-coalescing journal cover v0 | A flapping check spams faults |
| `since` / `lastTransitionTime` on status | Transitions are Events; "error since" is the Fault's `first_occurred` | A late-joining consumer needs it without history |
| `config_generation` (K8s `observedGeneration`) | The rack's config interlock is real, but one user; carry it as a value | A second subsystem needs "judged against which config" |
| `diagnostic` vs `config` value category (HA `entity_category`) | Config shows in the driver's read-only config view, not in status | Status values and config share one table |
| Declared expected checks/entities (aggregator `num_items_expected`, V5 station board) | Host or station config, not the record | The station board lands |
| Embedding the full record in stock `values` for lossless ROS transport | Zach chose a stripped stock view; v0 travels as JSON lines | A ROS-only link must carry v0 end to end |
| Translation keys (HA `translation_key`, Redfish registries) | `code` makes translation possible later; English messages now | A second language or a fleet UI |
| Fault aging, operation cycles, several fault memories (SOVD `scope`), near-miss series, correlation clusters | The fault manager's next steps, not record fields | Faults persist across restarts |
| Source authentication (SROS2, medkit hash-chained audit log) | Local, single-user | Faults drive remote actions |
| Index-aligned compact status (Autoware) | Premature at ~1 Hz JSON | Bandwidth matters (radio links) |
| A sequence number on status | Status is level-based; only events need gap detection | — |

## Open questions (recommendation · default if you say nothing)

1. **A grouping node over a wrapper.** Your rule, taken literally, makes a grouping node with no wrapper show
   worst-below. So a `cell` over the spider shows ERROR while the spider says WARN.
   - Recommend keeping it: a folder cannot know what the spider tolerates, and the alternative (a folder shows its
     children's headlines) would let a self-only box hide its children. With that alternative the fdcanusb adapter
     (USB fine, OK) would hide its dead bus from everything above it.
   - If you want a judgement at the cell, write a cell wrapper.
   - *Default: as implemented.*
2. **WARN vs can't-tell.** Recommend `warn < unknown < stale < error` (fail-safe), the opposite of ROS #593.
   *Default: as implemented; one line to flip.*
3. **The stale rule.** `max(3 × period, 5 s)` on the receive clock. *Default: that.*
4. **medkit interop.** UNKNOWN goes out as STALE, which medkit's bridge turns into CRITICAL. Recommend configuring
   medkit (#668), not changing the projection. *Default: STALE.*
5. **Schema id.** The v1 build plan's plugin `describe` says `"io-diagnostics-record/0"`. The record is not
   IO-specific. *Default: plugins send `record.SCHEMA` (`"diagnostics-record/0"`).*
6. **Debounce defaults.** WARN 3 reports, ERROR at once, heal 3; time rules for edge reporters. *Default: those.*
7. **Faults to stock tools.** Recommend not publishing faults as `DiagnosticStatus`; medkit can derive its own from
   the stock stream. *Default: faults stay in the host.*

## Evidence re-checked for this spec (2026-10-22, primary sources)

- **`diagnostic_msgs`** `DiagnosticStatus.msg`: SHA-1 `c33f8c49…` on `humble`, `jazzy` and `rolling`. Fields as in
  the table; `KeyValue` is two strings; `DiagnosticArray` is `Header` + `status[]`.
- **STALE history:** ros/common_msgs #33 "Finally add STALE", merged 2014-04-24 into `indigo-devel`.
  common_interfaces #268 has been open since 2025-02-10.
- **The aggregator:** ros/diagnostics #593 was merged 2026-05-20 into `ros2`; its `analyzer_group.cpp` patch reads
  "WARN/ERROR always beats STALE; if only STALE present, report STALE". `generic_analyzer_base.hpp` on `ros2` still
  has `else if (header_status->level == STALE) header_status->level = ERROR`. #658 has been open since 2026-07-16.
- **medkit:** `ros2_medkit_msgs/msg/Fault.msg` (`fault_code`, `severity` INFO..CRITICAL, `first/last_occurred`,
  `last_passed`, `occurrence_count`, PREFAILED/PREPASSED/CONFIRMED/HEALED/CLEARED). Its fault manager's README spells
  out the edge-reporter trap: count debounce never confirms or heals a reporter that sends one FAILED and one PASSED.
- **DroneCAN `341.NodeStatus.uavcan`:** health OK/WARNING/ERROR/CRITICAL; modes OPERATIONAL, INITIALIZATION,
  MAINTENANCE, SOFTWARE_UPDATE, OFFLINE; `uptime_sec`; `OFFLINE_TIMEOUT_MS = 3000`.
- **Kubernetes `metav1.Condition`** (apimachinery `types.go`): `reason` and `lastTransitionTime` are `+required`.
- **OpenBMC docs commit `44f3de9`** (2024-04-02), "Remove Redfish Health Rollup": "nontrivial to get correct …
  Redfish probably needs better guidance".
- **Home Assistant device registry docs:** "`via_device_id` describes *connectivity* … whereas child devices describe
  *composition*". A child device "has no connectivity or hardware identity of its own"; children are a single level;
  "still being finalized".
- **OTel entity data model:** "Two independent observers that report the same entity MUST be able to supply
  identical values for all identifying attributes."
- **Nagios Core 4, network reachability:** hosts beneath a DOWN router are UNREACHABLE, not DOWN.
- **Not re-checked here; taken from the research lanes:** AUTOSAR DEM consecutive-fault storage (paywalled ISO text
  behind it), Redfish `Sensor` threshold fields, Greenwave keys, Lichtblick's 5 s default, the Apex.Grace blog posts.

## v0.1 (2026-10-22, additive)

The decisions are frozen in `docs/actions-vocabulary-v0.1.md`; this section says what changed in the contract and
how a reader tells.

- **What changed** (`contract/record.py`):
  - `IDENTITY_KEYS` gains `model_id`: the robotparts part id (`mjbots-moteus-c1`) as a plain string, Home
    Assistant `DeviceInfo`'s own key for a model identifier. The record never imports robotparts.
  - `DANGERS` gains `moves`: the action makes hardware move (a rotor spins, a loaded joint can fall). Danger is per
    driver: the same standard name can be `query` on one device and `moves` on another.
  - `STANDARD_ACTIONS`: the standard action names → the host's label, as data (`check_comms` "Check
    communication", `check_firmware`, `update_firmware`, `calibrate`, `update_config`, `clear_faults`, `reboot`,
    `stop_all`, `set_id`, `open_console`). `validate(Action)` flags a standard name whose `title` differs from its
    label. A non-standard name is allowed and is not checked.
- **How v0.1 is signalled: the schema string does not change.**
  - Every line still says `"schema": "diagnostics-record/0"`. Rule 8 already allows a new string enum value under
    the same number, and v0.1 adds only values (one identity key, one danger) and a table.
  - So a v0 reader still reads every v0.1 line. It does not refuse it; at worst its `validate` lists `model_id` or
    `moves` as a problem, and the host keeps the record.
  - The revision is a constant, `RECORD_VERSION = "0.1"`, and it never travels in a record line. A producer states
    the revision it writes once, in its `describe` answer (`"record_version": "0.1"`). The host states the one it
    validates against in `GET /api/v1/state` (`record_version`).
- **`expected` in `describe` (v0.1, optional):** the devices a plugin expects to find, so an absent one is named in
  plain words instead of simply not showing.
  - Shape: a list of `{hardware_id, name, last_seen, source}`. `hardware_id` is the repeatable id
    (`usb:<vid>:<pid>:<serial>`, what sysfs shows). `last_seen` is Unix seconds or the producer's own text.
    `source` says who knows (`"rack_inventory"`). The moteus lane fills it from its rack inventory.
  - The host adds the USB devices it has itself seen a plugin diagnose. It keeps them, with first and last seen,
    in `~/.local/state/io-diagnostics/seen.json` (`$IO_DIAGNOSTICS_STATE_DIR`; live mode only, written atomically).
  - Each expected or remembered device that is not on USB now gets an entity under `host/expected` ("Expected, not
    connected") and one status, `<name>: presence`. It is `warn` (absent, not broken) with code `ExpectedAbsent`.
    The message says what the kernel saw this boot (`journalctl -k`, passive):
    - never enumerated: `expected (last seen 2026-10-21 18:24, rack_inventory) · not on USB · the kernel saw no plug
      event since boot (2026-10-22 07:00): try a data cable or another port`;
    - came and went: `… · not on USB · plugged in at 09:00:01 on 1-5, gone at 09:30:00`;
    - the log is unreadable: `… · the kernel log could not be read (<why>): no evidence whether it ever enumerated`.
  - Its values: `what_to_check`, `kernel_evidence` (`never | left | unknown`), `expected_by`, `last_seen`.
  - In v0.1 only USB ids are judged. A non-USB expected id (a CAN board) stays the plugin's own diagnose to report.
  - WHY not `diagnostics-record/0.1` in every line: every v0 reader compares the string exactly and would refuse
    the whole stream for two new enum values. That is a break, which is what `/1` is for.
- **The stock projection is unchanged.** `model_id` lives on `Entity` and `moves` on `Action`, and neither reaches
  `DiagnosticStatus`. So stock → v0.1 → stock is still the identity; `tests/test_record.py` re-proves it for the
  real-world messages and the fuzz set, and checks that a status about a v0.1 entity projects to the stock five.

## v0.1 addendum: `caused_by` (2026-10-23, additive)

Why: knock-on collapse (rule 3) explains in ONE direction, down the parent edge. That is right for `via`: a dead
adapter explains its silent boards. It is backwards for a composite, whose verdict is caused by its members. Three
probes against this code showed it (`reports/diagnostics-rollup-proposals-2026-10-23.html`, "The barrier"): the rig
row and CAN 15's own report counted one missing board twice; a spider at ERROR filed its failed leg as its own
symptom; the Computer's checks written on the host swallowed the rack below.

- **The field.** `DiagnosticStatus.caused_by: tuple[str, ...]`, default `()`: the entity paths of the members whose
  own reports caused this record's verdict. Set it on a composite or rollup check, never on a leaf's own reading.
  A plugin writes the paths the way it writes `path` (relative to its device, or the full tree path); the host
  rebases both.
- **The rule** (`summarize`), the contract text:
  - A record is a **consequence** when it is not STALE and names members, and every member it names is an entity
    that self-reports a problem (its own level is not `ok` and not `None`). An entity whose every non-OK record is a
    consequence is its members' consequence: it goes in `knock_on` against the root cause of its worst member
    (then the shallowest, then tree order), and it is not counted.
  - A record that names nothing, names an `ok` entity, an entity with no records of its own, an entity nobody
    describes or itself, or is STALE (silence is the wrapper's own failure), is its own cause. So a partial list
    never hides a problem: a composite that cannot name every cause names none.
  - A consequence **explains nothing below it**: its verdict is its members' problem, not a failure of its own. So
    a spider at ERROR that names leg 3 no longer swallows leg 3; an unnamed WARN on leg 5 stays a cause too.
  - The `via` / `part_of` direction is unchanged for everything else: an ERROR ancestor that names nothing still
    explains everything below it, and a silent one only silence.
  - Members naming each other (a producer's mistake) are broken at the first entity of the ring in tree order, so a
    ring of consequences never explains a problem away.
  - **The composite's row keeps its own level and message** (the black-box rule). Only the counts, the attention
    list and `knock_on` change.
- **Where the host sets it** (`io_diagnostics`):
  - the rig row's `<rig>: expected setup` names, per missing item, the row that reports it: a board's own row
    (`BoardNotAnswering`), or an absent device's row under `host/expected` (`ExpectedAbsent`); for
    `ExpectedNotChecked`, the unchecked boards' rows. If any item has no row that says it, it names nothing;
  - the Computer's `USB/PCIe links` line names the port, PCIe and camera rows whose problems it repeats (only when
    it repeats them all);
  - the Computer's own checks stay a leaf beside the tree (`tree.HOST_SELF`); a `caused_by` naming `host` follows
    them there.
- **Stock side.** `to_diagnostic_status` appends one value, key `caused_by` (`CAUSED_BY_KEY`), the paths joined by
  `", "`, after the measurements, so a person in rqt still reads who caused it. `from_diagnostic_status` leaves a
  `caused_by` value a plain value, so stock → v0 → stock stays the identity (re-proved for the real-world messages,
  the fuzz set, and a stock message carrying a `caused_by` value anywhere). v0 → stock → v0 keeps it as that string,
  as it keeps a measurement.
- **Validation.** `validate` flags an empty member, a record naming its own entity, and a value or measurement
  keyed `caused_by` beside a non-empty `caused_by` (the projection would repeat the key).
- **Signalled** as v0.1 was: the schema string is unchanged and `RECORD_VERSION` stays `"0.1"`; a v0 reader ignores
  the field (open world) and simply counts as before.
- **Tests.** `tests/test_record.py` "v0.1 addendum": the three probes as regression tests (each also asserts the
  count without `caused_by`), the counting conditions, a dead adapter under a rig's named boards, a ring, the stock
  projection and validation. `tests/test_caused_by.py`: the same through the host (a plugin composite's relative
  member, the rig naming an absent adapter's row and naming nothing when no row says it, the links line, a check
  naming the host).
