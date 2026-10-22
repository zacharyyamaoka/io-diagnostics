# Diagnostics schema: what the ROS / robotics world changed and learned (2026-10-22)

Lane: ROS and robotics primary sources (repos, `.msg`/DSDL/XML definitions, REPs, GitHub issues/PRs, ROS Discourse).
Companion lanes: `reports/media/survey/lane_ros.md` (dashboards/UI, 2026-10-21) and the general/vendor lanes.
Every source was read on 2026-10-22 unless a date says otherwise. Message definitions are copied exactly, minus license
headers, and any trimmed comment block is marked `[...]`.

## Answer first

- **The message has not changed.** `diagnostic_msgs/DiagnosticStatus`, `DiagnosticArray` and `KeyValue` are
  byte-identical on Humble, Jazzy, Kilted, Lyrical and Rolling (checked by hash today). The only field-level edit in
  17 years was adding the constant `STALE=3`, in ROS 1 Indigo (2014). Nobody upstream is planning to change it. The
  one open proposal, for more levels (2025), stalled on a single objection: inserting levels breaks recorded bags and
  aggregation order.
- **The modern efforts all keep it and build beside it.** ros2_medkit consumes `/diagnostics` and turns it into
  *faults* (SOVD/ISO 17978 shape). Autoware's diagnostic graph keeps the DiagnosticStatus leaf verbatim and adds an
  explicit DAG on top. Greenwave, rosgraph_monitor, nav2 and ros2_control all still *publish* DiagnosticStatus. No
  project replaced it. They added layers and sibling messages.
- **What they added, by theme:** a fault record with a lifecycle (debounce, confirm, heal, clear, counters, freeze
  frame); identity and hierarchy as data, not name-string conventions; a static/dynamic split (topology published
  once, compact status published periodically); latching and hysteresis; typed state enums (mode, power, connectivity,
  `UNKNOWN=0`); per-record timestamps; and the declared expected rate that staleness is judged against.
- **What the field learned:** `level` conflates three things: severity, freshness (STALE) and readiness/mode. Keep
  them apart, as DroneCAN, Cyphal and MAVLink always have. Status, event and fault are different shapes. Severity
  depends on context, so it belongs in a configured graph, not in the driver. String-typed values cost every consumer.
  Identity fields need one meaning each. `/diagnostics` is not a liveness or safety channel; its own REP says so.
- **For Zach's contract:** start from DiagnosticStatus as the wire-compatible core, which is what
  `docs/converged-design-2026-10-22.md` already does. Add the handful of fields every newer system added (section 7.3).
  Faults and events go in separate sibling records, never into extra `level` values.

---

## 1. `diagnostic_msgs` over time

### 1.1 Exact current definitions (ROS 2 Rolling = Lyrical = Kilted = Jazzy = Humble)

Source: https://github.com/ros2/common_interfaces/tree/rolling/diagnostic_msgs/msg. A SHA-1 of each file on the
`humble`, `jazzy`, `kilted`, `lyrical` and `rolling` branches gives the same hash for all five.

`DiagnosticStatus.msg`
```
# This message holds the status of an individual component of the robot.

# Possible levels of operations.
byte OK=0
byte WARN=1
byte ERROR=2
byte STALE=3

# Level of operation enumerated above.
byte level
# A description of the test/component reporting.
string name
# A description of the status.
string message
# A hardware unique string.
string hardware_id
# An array of values associated with the status.
KeyValue[] values
```

`DiagnosticArray.msg`
```
# This message is used to send diagnostic information about the state of the robot.
std_msgs/Header header # for timestamp
DiagnosticStatus[] status # an array of components being reported on
```

`KeyValue.msg`
```
# What to label this value when viewing.
string key
# A value to track over time.
string value
```

Services in the same package: `SelfTest.srv` (`--- string id, byte passed, DiagnosticStatus[] status`) and
`AddDiagnostics.srv` (`string load_namespace --- bool success, string message`), which loads aggregator analyzers at
runtime.

**STALE=3 is in the message.** The constant is there; the *meaning* is a convention. The aggregator sets STALE when
an item stops updating. Publishers are not supposed to send it, though some (Greenwave) do. See 6.1.

ROS 1 Noetic (https://github.com/ros/common_msgs/tree/noetic-devel/diagnostic_msgs/msg) has the same fields and
constants. Only comment formatting differs, and the array uses `Header header`.

### 1.2 History of the definition

| Date | Change | Source |
|---|---|---|
| 2009-06-23 | `common_msgs` populated | https://github.com/ros/common_msgs/commits/noetic-devel/diagnostic_msgs/msg/DiagnosticStatus.msg |
| 2009-08-08 | `DiagnosticValue` renamed to `KeyValue`; status fixed. These are the pre-1.0 API changes | same history (`b3adccfa`, `d18460cc`) |
| 2009-08-10 | Level constants OK/WARN/ERROR documented | `460e36e7` |
| 2010-11-27 | REP-107 written. It shows the **3-level** message, without STALE | https://github.com/ros-infrastructure/rep/blob/master/rep-0107.rst |
| 2012-11-06 | PR to add `STALE=3`, since level 3 was already used as a convention. Closed by a branch switch. The MD5-checksum/bag-migration worry was raised | https://github.com/ros/common_msgs/pull/3 |
| 2014-04-09 | "Finally add STALE" (PR #33). Merged for **Indigo**. Bags auto-migrate because only a constant changed, but a mixed-distro robot/monitor silently mismatches | https://github.com/ros/common_msgs/pull/33 |
| 2016-04-15 | ROS 2: `diagnostics_msgs` renamed to `diagnostic_msgs` to match ROS 1. Fields unchanged | https://github.com/ros2/common_interfaces/commits/rolling/diagnostic_msgs |
| 2016 → 2026-10 | Only packaging/changelog commits. **No field change** | same |
| 2025-02-10 | Open issue: "Additional levels in DiagnosticStatus". Unresolved | https://github.com/ros2/common_interfaces/issues/268 |

**`byte` trap in ROS 2.** `rosidl_adapter` maps `.msg` `byte` to IDL `octet`
(https://github.com/ros2/rosidl/blob/rolling/rosidl_adapter/rosidl_adapter/msg/__init__.py, `'byte': 'octet'`).
So `level` is a `bytes` object in Python and `unsigned char` in C++. That is why `ros2 topic echo` prints
`level: "\0"` (seen in https://github.com/ros/diagnostics/issues/520). It is also why the aggregator code mixes
`int8_t` and `unsigned char` for levels, which a reviewer flagged in https://github.com/ros/diagnostics/pull/506.
In ROS 1, `byte` was an alias for `int8`. A new schema should use a plain integer enum.

### 1.3 REP-107 (Diagnostic System for Robots Running ROS)

https://github.com/ros-infrastructure/rep/blob/master/rep-0107.rst (Tully Foote; created 2010-11-27; Status: Final;
public domain). What it prescribes:

- **Purpose:** three capabilities: a glance that all systems are nominal, detail for debugging, and long-term logs
  for history and trends.
- **Weak typing on purpose:** "the diagnostics uses weakly typed information unlike the rest of ROS because the
  intended consumer is a person."
- **Name:** the component name, "usually the node name of the driver node" (e.g. `prosilica_driver`). Subcomponents
  use `component name: subcomponent name` (e.g. `prosilica_driver: Packet Status`,
  `prosilica_driver: Frequency Status`). They should be split out when they "could be interpreted independently";
  one per topic is common, because `diagnostic_updater` helpers create them.
- **hardware_id:** "If applicable this identifies the specific hardware running. This is for things like serial
  numbers for devices so that a piece of hardware can be tracked between robots if it is moved between robots or
  moved within a robot."
- **Levels:** OK, Warn ("unexpected behavior it should be resolved and may affect operation") and Error ("the
  component can not be relied upon to operate correctly"), coloured green/yellow/red. STALE is not in the REP.
- **Values:** string key/value pairs. Common data: "settings, serial numbers, firmware versions, error counts, and
  information on latest errors or timeouts."
- **Protocol:** `/diagnostics`, `DiagnosticArray`, default **1 Hz**.
- **Hierarchy:** the REP defines none. Hierarchy comes from the `diagnostic_aggregator`'s analyzer configuration
  (path prefixes, `contains`, `regex`), so "the tree" is a property of the aggregator config, not of the data.
- **Scope:** "this REP is targeted for hardware diagnostics only". All-software diagnostics would create "too much
  noise".
- **Improper usage, stated explicitly:** "This is **not** designed to be a keepalive", and "This is **not** going to
  halt the system in any way. If there is an unsafe condition it must be dealt with independently."

### 1.4 What changed *around* the message (tooling, not schema)

- **ROS 2 `diagnostic_updater` prefixes the node name:** `name = node_name + ": " + task` and stamps
  `DiagnosticArray.header.stamp`. Parameter `use_fqn` switches to the fully qualified node name. It warns once
  when no hardware_id is set. Source:
  https://github.com/ros/diagnostics/blob/ros2/diagnostic_updater/src/diagnostic_updater.cpp (lines 83, 128-190).
  The default period is 1.0 s (`diagnostic_updater.period`).
- **The aggregator's STALE roll-up was wrong for a decade, then fixed.** It used to publish ERROR when one item was
  STALE and the rest were not, so a group showed ERROR with no ERROR inside it. The proposal came in 2023
  (https://github.com/ros/diagnostics/issues/297) and PR #506 was open a year. It was merged as **#593 on
  2026-05-20** and backported to Humble/Jazzy/Kilted, with the rule "WARN/ERROR always beats STALE; only-STALE
  reports STALE". An open follow-up asks why WARN should beat STALE at all
  (https://github.com/ros/diagnostics/issues/658, 2026-07-16). The review also caught that an empty aggregator
  would publish a green top-level (https://github.com/ros/diagnostics/pull/506).
- **`critical` parameter** (2024-06, https://github.com/ros/diagnostics/pull/324): publish `/diagnostics_toplevel_state`
  immediately on any degradation instead of waiting for `pub_rate`. It was added because watchdogs parse the
  top-level state.
- **Aggregator defaults:** items not claimed by an analyzer go to "Other" and turn stale after 5 s.
  `/diagnostics_toplevel_state` is itself a `DiagnosticStatus`
  (https://github.com/ros/diagnostics/blob/ros2/diagnostic_aggregator/README.md).
- **`diagnostic_remote_logging`** (InfluxDB/Grafana, 2025, https://github.com/ros/diagnostics/pull/425). It exposed
  the identity ambiguity (see 2.4).
- **`diagnostic_topic_monitor`** "coming soon" (https://github.com/ros/diagnostics/pull/633). `diagnostic_analysis`
  is still "Not ported to ROS2 yet" (https://github.com/ros/diagnostics/blob/ros2/README.md).

---

## 2. Proposals and complaints

### 2.1 Index

| Topic | Where | Date | Status | Gist |
|---|---|---|---|---|
| More levels (INFO, NOTICE, CRITICAL, ALERT…) | https://github.com/ros2/common_interfaces/issues/268 + https://discourse.openrobotics.org/t/additional-levels-in-diagnosticstatus/41967 | 2025-02 | open, no consensus | see 2.2 |
| STALE vs ERROR roll-up | https://github.com/ros/diagnostics/issues/297, PR #315, #506 → #593 | 2023-04 → 2026-05 | merged + backported | STALE treated as worse than ERROR was a bug |
| WARN beats STALE? | https://github.com/ros/diagnostics/issues/658 | 2026-07 | open | ordering of freshness vs severity has no right answer on one axis |
| Header stamp missing | https://github.com/ros/diagnostics/pull/341 → #342 | 2024-04 | merged (in ntp_monitor) | "Some visualization tools require the timestamp" |
| hardware_id meaning | https://github.com/ros/diagnostics/issues/520 (+ PR #525) | 2025-08 | closed 2026-05 | InfluxDB exporter assumed hardware_id = node FQN; REP says serial number |
| First messages have empty hardware_id | https://github.com/ros/diagnostics/issues/666 | 2026-09 | open | Foxglove/console tools show one diagnostic twice |
| Heartbeat message | https://discourse.openrobotics.org/t/add-heartbeat-message-type/24162 | 2022-02 | discussion | per-publisher heartbeats; post-processing /diagnostics needs string parsing |
| Topic-rate monitoring | https://discourse.openrobotics.org/t/how-do-you-monitor-your-robot-diagnostics-topic-rates/41191 | 2024-12 | discussion | DiagnosedPublisher, Bonsai/rosgraph_monitor, DDS stats |
| ros2_control hardware status | https://discourse.openrobotics.org/t/proposal-standardized-hardware-status-reporting-in-ros2-control/49461 | 2025-08 | merged into control_msgs | see 6.3 |
| "What's missing" (Zach's link) | https://discourse.openrobotics.org/t/whats-missing-from-ros-2-diagnostics-and-what-we-built/52489 | 2026-02-12 | | see 2.5 |
| "Why we did not extend /diagnostics" | https://discourse.openrobotics.org/t/why-we-did-not-extend-diagnostics/57859 | 2026-09-04 | | see 2.5, 3 |

**Searched and not found:** no issue or PR in ros/diagnostics or ros2/common_interfaces proposes typed values,
units, per-status timestamps or fault codes on `DiagnosticStatus`. GitHub issue search for "typed", "numeric",
"fault" and "severity" returned nothing relevant. No published ROS 2 diagnostics roadmap or REP successor exists.
The pressure shows up instead as workarounds (2.3) and as new packages beside the message (sections 3-6).

### 2.2 The 2025 "additional levels" debate: where it landed

Proposal (nnarain, inspired by Cyphal's Severity): INFO, OK, NOTICE, WARN, ERROR, CRITICAL, ALERT, STALE.
Positions, paraphrased:
- **Maintainer (ct2034):** more granularity helps some users, but every team must agree what each level means, and
  that gets harder as states are added. Custom states belong in `ros/diagnostics`, not in `common_interfaces`.
- **PMC note (mjcarroll):** inserting values between existing ones renumbers them. That invalidates recorded bags
  and breaks the aggregator, which relies on numeric order.
- **Spacing idea (destogl, ct2034 agreeing):** move OK/WARN/ERROR to 10/20/30 and leave room in between.
- **Against (v4hn, Achllle, Timple):** INFO, NOTICE and ALERT are *events*, not states over time; they belong in
  logs. A driver cannot know whether its failure is CRITICAL, because that depends on whether the sensor matters in
  the current mode or is redundant. Downgrade analyzers already exist for exactly this. More levels mean more
  misclassification.
- **Safety framing (Shannon_Barber):** levels should map to *interventions*: may start motion, may continue,
  degraded, limp home, abort, arrest. Default must be `Unknown = 0` to catch zero-initialised messages. Values
  should differ by more than one bit, and the source must be authenticated.
- **Scope ruling (tfoote, 2025-02-19 and 03-07):** diagnostics was not designed as a safety system or as a pathway for
  automated responses. Use lifecycle and managed nodes for prompt event-driven responses, and an executive with
  lower-latency mechanisms for anything critical. Use SROS2 for authenticating the source.

Net: no change. The useful residue is the *distinctions* drawn: state vs event, local health vs system impact, and
reporting vs reaction.

### 2.3 The typed-values and units pain (evidence, not proposals)

- ros2_control's controller_manager packs a whole statistic into one string, so a consumer re-parses five numbers
  and a unit from text:
  `"Avg: %.2f [%.2f - %.2f] us, StdDev: %.2f"`, and for periodicity `"… Hz -> Desired : N Hz"`
  (https://github.com/ros-controls/ros2_control/blob/master/controller_manager/src/controller_manager.cpp, `make_stats_string`).
- The InfluxDB exporter guesses which values are numbers (`key2=42` vs `key1="value1"`) and had to guess what
  `hardware_id` means (https://github.com/ros/diagnostics/issues/520).
- Greenwave's dashboard works only because of *magic keys* (`frame_rate_node`, `frame_rate_msg`,
  `current_delay_from_realtime_ms`, `expected_frequency`, `tolerance`), and NVIDIA says it doesn't "guarantee schema
  stability for now" (https://github.com/NVIDIA-ISAAC-ROS/greenwave_monitor/blob/main/docs/DESIGN_AND_IMPLEMENTATION.md).
- An operator on a 10 kbps acoustic link said string key/values need a lot of diligence to keep consistent across
  developers (41967 #9).
- peci1 (2022) said post-processing rates from `/diagnostics` means string parsing, so he added a separate `Heartbeat`
  topic per sensor that carries the sensor's own Header (24162 #12, 41191 #6).
- Counter-examples that went typed: `rosgraph_monitor_msgs/TopicStatistic` uses `builtin_interfaces/Duration
  mean/min/max` (6.2), MAVLink fields carry `units=` attributes (6.6), and MAVLink events have typed arguments.

### 2.4 Identity ambiguity (name vs hardware_id)

- The REP says hardware_id is a serial number. The InfluxDB exporter used it as the node FQN, and `name` holds
  `node: task` (#520).
- **The aggregator rewrites `name`.** `/arm1/arm_communicator: errors` becomes
  `/Arm/Hardware/Control/ arm1 arm_communicator: errors`, so anything keyed on name after aggregation breaks (#520).
- Tools key on the pair. Lichtblick (the open Foxglove fork) builds the id `|hardware_id|name|`. An empty
  hardware_id in the first messages therefore shows one diagnostic twice (#666).
- medkit's bridge treats hardware_id as the reporting node *only if it contains a `/`*. That is a heuristic, because
  "hardware_id commonly contains a serial number, device path, or no value rather than a ROS node name"
  (https://github.com/selfpatch/ros2_medkit/blob/main/src/ros2_medkit_diagnostic_bridge/README.md).

### 2.5 The thread Zach linked, and its follow-up

**"What's missing from ROS 2 diagnostics (and what we built)"**, Michal Faferek (selfpatch), 2026-02-12, 3 posts
(https://discourse.openrobotics.org/t/whats-missing-from-ros-2-diagnostics-and-what-we-built/52489).
Part 1 of a series. Missing, per the post (paraphrased):
1. structured fault codes with severity, where today there are four levels and a string;
2. fault history, where today pub/sub forgets;
3. a fault lifecycle (report, confirm, heal, clear), where today every glitch is an event with no debounce;
4. a REST API for dashboards, fleet tools and alerting, where today you need rosbridge or a ROS client;
5. root-cause analysis;
6. automatic data capture on fault, where today you `rosbag record` and hope.

It points to cars: OBD-II gives a code, a freeze frame, first-occurrence history and a clear command, and ASAM SOVD
moves that to HTTP/JSON. What they built is **ros2_medkit**: fault lifecycle with debounce and filtering, rosbag
capture on fault, a REST/SSE gateway, root-cause correlation, Apache-2.0, Jazzy.
- **Tully Foote replied** that the comparison was not like-for-like: it set a low-level reporting mechanism against a
  GUI and ignored prior art. robot_monitor had aggregation, collapsing views and pausable, scrubbable time history in
  2010.
- **The author narrowed the claim.** The gap is not visualisation but the layer underneath: software faults (not
  only hardware), a lifecycle, persistence that outlives the viewer, freeze frames, and programmatic access from
  outside DDS.

**"Why we did not extend /diagnostics?"**, 2026-09-04, 9 posts
(https://discourse.openrobotics.org/t/why-we-did-not-extend-diagnostics/57859). The core argument, paraphrased: *a
status is information; a fault is a model you can query and act on.* The aggregator keeps one status per `name`
and overwrites it, so nothing in the stack knows when a fault started, how often it recurred, whether it is still
active, or whether anyone acknowledged it. A fault needs an *address* to attach evidence (a bag) and *verbs*
(acknowledge, fetch recording). Adding those to the aggregator makes it a fault manager. So: **DiagnosticStatus
stays the health stream, and the fault record is agreed on separately.** The post ends by asking whether the fault
record should become a shared ROS message.

Field reports in the same thread (manankharwar, sensor-fusion maintainer):
- His filter was badly broken three times while every status read OK, for example a GPS gate silently discarding
  100 % of fixes. Each case needed a *number against a threshold over a window*, not a level.
- Mapping STALE to CRITICAL fires nonstop on an outdoor robot, where GPS blackouts are normal.
- "Never heard from this sensor" differs from "stopped".
- **Time debounce and count debounce are not interchangeable.** A 3-second continuous window never fired on real
  driving, where the longest streak was 0.9 s. A vote ratio (200 votes, 80 % disagreement) worked.
- A gate can fail by being unable to fire at all: its maximum statistic stayed 39× below the threshold.
- A monitor can also answer when its inputs are invalid. The outcome needs a third value besides pass and fail.

Faferek's resolution, following AUTOSAR:
- Split **observation → monitor → fault manager.** The monitor does the maths, with windows and *enable conditions*.
  The fault manager does lifecycle, debounce, freeze frame and clearing.
- The measured quantity goes into the freeze frame as evidence, not into the fault record.
- Monitors return **PASS / FAIL / NOT_EVALUATED (with reason)**. NOT_EVALUATED never moves the counter and never
  turns the fault green ("test not completed", UDS-style), and it needs a declared list of expected sources.
- Tracked as https://github.com/selfpatch/ros2_medkit/issues/668 (keep DiagnosticStatus key-values as freeze-frame
  evidence; STALE severity configurable per name) and #669 (value detectors with time/counter/ratio windows and enable
  conditions). Both were open on 2026-09-08.

---

## 3. ros2_medkit

- Repo: https://github.com/selfpatch/ros2_medkit. Created 2025-11-17, v0.7.0 on 2026-09-01, Apache-2.0, ~277 stars.
  Targets Jazzy, Humble and Lyrical and is being released into the official ROS packages. Announced at
  https://discourse.openrobotics.org/t/ros2-medkit-api-first-diagnostics-for-ros-2/51428 (2025-12-16). There was a
  ROSCon 2026 talk ("Production diagnostics for ROS", 2026-09-24).
- **SOVD:** yes. The README says "The API follows SOVD (ISO 17978)", and the REST docs cite clauses (e.g.
  ISO 17978-3 §7.6). The entity model is SOVD's: **Area / Component / App / Function**. SOVD was handed from ASAM
  (v1.1) to ISO at the end of 2024. ISO 17978-3 (API) was published 2026-03, -2 (use cases) 2026-04 and -1
  (principles) 2026-05 (https://www.iso.org/standard/86587.html, https://www.iso.org/standard/86586.html,
  https://www.iso.org/standard/85133.html).
- **It keeps DiagnosticStatus. It does not replace it.** `ros2_medkit_diagnostic_bridge` subscribes to
  `/diagnostics` and reports through the same client the native reporters use. Nodes do not change.

| DiagnosticStatus level | Bridge action |
|---|---|
| OK (0) | sends PASSED (healing) |
| WARN (1) | FAILED, severity WARN (1) |
| ERROR (2) | FAILED, severity ERROR (2) |
| STALE (3) | FAILED, severity CRITICAL (3). Fires only if the bridge listens to `/diagnostics_agg`, because the aggregator is what produces STALE |

  Fault codes are derived from `name` (`motor: Status` → `MOTOR_STATUS`) or mapped by `name_to_code.<name>`, or taken
  from a `keyvalue_codes` key. By default the source is the bridge node itself. `use_hardware_id_as_source_id`
  uses hardware_id only if it contains `/`. **Today the bridge drops the key-values**; #668 is the fix.
  Source: https://github.com/selfpatch/ros2_medkit/blob/main/src/ros2_medkit_diagnostic_bridge/README.md.

### 3.1 Interface definitions (`ros2_medkit_msgs`, main, 2026-10-22)

https://github.com/selfpatch/ros2_medkit/tree/main/src/ros2_medkit_msgs. The package has msg ClusterInfo, EntityInfo,
EnvironmentData, ExtendedDataRecords, Fault, FaultEvent, MedkitDiscoveryHint, MutedFaultInfo and Snapshot, and srv
ClearFault, GetCapabilities, GetEntityData, GetFault, GetRosbag, GetSnapshots, ListEntities, ListFaults,
ListFaultsForEntity, ListRosbags and ReportFault.

`Fault.msg`
```
# Fault.msg - Core fault data model for ros2_medkit fault management system.
#
# A Fault represents an aggregated fault condition identified by a global fault_code.
# Multiple sources can report the same fault_code, and they are aggregated into a
# single Fault with tracked occurrence_count and reporting_sources.
#
# Debounce model (AUTOSAR DEM-style):
# - FAILED events decrement internal counter (towards confirmation)
# - PASSED events increment internal counter (towards healing)
# - Status reflects the current debounce state

# Global fault identifier (e.g., "MOTOR_OVERHEAT", "SENSOR_FAILURE_001")
string fault_code

# Fault severity level (use SEVERITY_* constants)
uint8 severity

# Human-readable description of the fault condition
string description

# Timestamp when the current occurrence started (reset when a FAILED event
# reactivates a CLEARED fault, so it moves with occurrence_count instead of
# marking the fault's first report ever)
builtin_interfaces/Time first_occurred

# Timestamp when this fault last occurred (FAILED events only; a PASSED
# event is the fault ending, not occurring, and does not touch this field)
builtin_interfaces/Time last_occurred

# Timestamp of the last PASSED event reported for this fault (zero = never)
builtin_interfaces/Time last_passed

# Number of times this fault has occurred, counted on edges: one for the first
# FAILED event, and one more each time a FAILED event arrives while the fault is
# CLEARED. Repeated FAILED events within one occurrence do not increment it;
# last_occurred is what advances on those.
uint32 occurrence_count

# Current fault status (use STATUS_* constants)
string status

# List of source identifiers that have reported this fault
string[] reporting_sources

# Severity level constants
# Informational message: No action required, used for logging or tracking.
uint8 SEVERITY_INFO = 0
# Warning: May require attention, but does not impact core functionality.
uint8 SEVERITY_WARN = 1
# Error: Impacts functionality and requires intervention.
uint8 SEVERITY_ERROR = 2
# Critical: Severe error requiring immediate attention; may compromise safety or system operation.
# CRITICAL severity bypasses debounce filtering and confirms immediately.
uint8 SEVERITY_CRITICAL = 3

# Status constants
# Fault status lifecycle with debounce filtering:
#
#   PREFAILED ←────────────────→ PREPASSED
#       │      (counter crosses 0)    │
#       ▼                             ▼
#   CONFIRMED                      HEALED
#       │                          (retained)
#       ▼
#   CLEARED (manual)
#
# [... per-status comments trimmed: PREFAILED counter<0 above threshold; PREPASSED counter>0 below healing
#  threshold; CONFIRMED counter <= confirmation_threshold (e.g. -3); HEALED counter >= healing_threshold (e.g. +3),
#  retained; CLEARED = acknowledged via ClearFault, retained ...]
string STATUS_PREFAILED = "PREFAILED"
string STATUS_PREPASSED = "PREPASSED"
string STATUS_CONFIRMED = "CONFIRMED"
string STATUS_HEALED = "HEALED"
string STATUS_CLEARED = "CLEARED"
```

`FaultEvent.msg` (published on `/fault_manager/events`, streamed as SSE)
```
# Event type identifier (use EVENT_* constants)
string event_type

# The fault data associated with this event (contains current state after the event)
Fault fault

# Timestamp when this event was generated
builtin_interfaces/Time timestamp

# Event type constants
# Emitted when a fault transitions from PREFAILED to CONFIRMED status
# (e.g., after debounce counter reaches confirmation threshold).
string EVENT_CONFIRMED = "fault_confirmed"
# Emitted when a fault ends: CLEARED via the ClearFault service, or HEALED when
# PASSED events drive it past the healing threshold. Check fault.status to tell
# the two apart.
string EVENT_CLEARED = "fault_cleared"
# Emitted when fault data changes without status transition (e.g., last_occurred
# advanced, severity escalated, new source added to reporting_sources).
# [... occurrence_count note trimmed ...]
string EVENT_UPDATED = "fault_updated"

# [... correlation comment trimmed ...]
string[] auto_cleared_codes
```

`Snapshot.msg` (freeze frame or rosbag)
```
# Snapshot type: "freeze_frame" or "rosbag"
string type

# Human-readable name for this snapshot
string name

# --- Freeze frame fields (empty for rosbag type) ---

# JSON-serialized message data
string data

# ROS topic name (e.g., "/motor/temperature")
string topic

# Message type (e.g., "sensor_msgs/msg/Temperature")
string message_type

# --- Rosbag fields (empty for freeze_frame type) ---

# [... bulk_data_id comment trimmed: the RECORDING id, not the fault code ...]
string bulk_data_id

# File size in bytes
uint64 size_bytes

# Recording duration in seconds
float64 duration_sec

# Recording format: "sqlite3" or "mcap"
string format

# --- Common metadata ---

# Capture timestamp (nanoseconds since epoch)
int64 captured_at_ns

# Snapshot type constants
string TYPE_FREEZE_FRAME = "freeze_frame"
string TYPE_ROSBAG = "rosbag"

# Rosbag format constants
string FORMAT_SQLITE3 = "sqlite3"
string FORMAT_MCAP = "mcap"
```

`EnvironmentData.msg` ("SOVD-compliant environment data for faults")
```
# Extended data records with occurrence timestamps
ExtendedDataRecords extended_data_records

# Array of snapshots (freeze frames and/or rosbag metadata)
Snapshot[] snapshots
```

`ExtendedDataRecords.msg`
```
# Timestamp of first occurrence (nanoseconds since epoch)
int64 first_occurrence_ns

# Timestamp of last occurrence (nanoseconds since epoch)
int64 last_occurrence_ns
```

`EntityInfo.msg` (SOVD entity)
```
# Unique entity identifier (e.g., "temp_sensor", "powertrain")
string id

# Human-readable entity name
string name

# Entity type: "app", "component", "area", "function"
string entity_type

# Parent entity ID ("" for top-level entities)
string parent_id

# Fully-qualified ROS 2 name (e.g., "/powertrain/engine/temp_sensor")
string fqn

# SOVD capabilities available for this entity
# (e.g., ["data", "faults", "operations", "configurations"])
string[] capabilities
```

`ReportFault.srv`
```
# Global fault identifier. Convention: UPPER_SNAKE_CASE (e.g., "MOTOR_OVERHEAT").
# Recommended max length: 64 characters. Allowed: A-Z, 0-9, underscore.
# Same fault_code from different sources = single aggregated fault.
string fault_code

# [... comment trimmed: FAILED decrements, PASSED increments the debounce counter ...]
uint8 event_type

# Event type constants
uint8 EVENT_FAILED = 0
uint8 EVENT_PASSED = 1

# Severity level: 0=INFO, 1=WARN, 2=ERROR, 3=CRITICAL.
# [... trimmed: only meaningful for FAILED; CRITICAL bypasses debounce ...]
uint8 severity

# Human-readable description of the fault condition.
# Only updated for FAILED events. PASSED events retain the existing description.
string description

# Identifier of the reporting node/entity. Recommended: fully qualified ROS 2 node name
# including namespace (e.g., "/powertrain/engine/temp_sensor").
# This value is added to the fault's reporting_sources array for multi-source tracking.
string source_id
---
# [... comment trimmed ...]
bool accepted
```

`ClearFault.srv` (fields only): `string fault_code`, `bool skip_correlation_auto_clear` → `bool success`, `string
message`, `string[] auto_cleared_codes`. `ListFaults.srv` request fields: `bool filter_by_severity`, `uint8 severity`,
`string[] statuses` (empty means CONFIRMED only), `bool include_muted` and `bool include_clusters`. Response fields:
`Fault[] faults`, `uint32 muted_count`, `MutedFaultInfo[] muted_faults`, `uint32 cluster_count` and
`ClusterInfo[] clusters`. `MutedFaultInfo` holds `fault_code`, `root_cause_code`, `rule_id` and `uint32 delay_ms`.
`ClusterInfo` holds `cluster_id`, `rule_id`, `rule_name`, `label`, `representative_code`, `representative_severity`,
`fault_codes[]`, `count`, `first_at` and `last_at`.

**"Operations" and "Configurations" are not message types.** They are SOVD REST resources. `/operations` executes
ROS 2 services and actions, and `/configurations` reads, sets and resets ROS 2 parameters
(https://github.com/selfpatch/ros2_medkit/blob/main/docs/api/rest.rst, "Operations Endpoints", "Configurations
Endpoints").

### 3.2 Lifecycle, debounce, freeze frame, extended data

Source: https://github.com/selfpatch/ros2_medkit/blob/main/src/ros2_medkit_fault_manager/README.md

- **Counter debounce (AUTOSAR DEM style).** FAILED moves the counter down and PASSED moves it up. The counter is
  clamped to `[confirmation_threshold, healing_threshold]`. CONFIRMED and HEALED **latch**, with hysteresis: one
  opposite event cannot flip them. Defaults are `confirmation_threshold: -1` (the first FAILED confirms),
  `healing_enabled: false` and `healing_threshold: 3`. The bringup launch turns healing on. Per-entity and
  per-fault-code threshold overrides exist. `auto_confirm_after_sec` confirms on elapsed time. **CRITICAL bypasses
  debounce.**
- **Documented trap: count debounce only works for reporters that repeat while the condition holds.** An
  edge-reporter that sends one FAILED and one PASSED never confirms at `-3` and never heals at `+3`. Advice: leave
  `-1` for such reporters and filter at the source.
- **Client-side filter in `FaultReporter`:** a WARN is held until 3 reports arrive within 10 s; ERROR passes at once;
  PASSED is held the same way (thread 57859).
- **`occurrence_count` counts edges**, i.e. outages, not reports. The **near-miss series** is append-only: FAILED
  reports that moved the counter without confirming, kept across clear, bounded at 200 per code. It answers "how often
  did this nearly fault".
- **Freeze frame:** on CONFIRMED, up to 16 topics of the reporting node are sampled (`fault_specific` > regex
  `patterns` > `default_topics`). One compact JSON frame per code **survives clear**; ordinary snapshots do not
  unless `snapshots.retain_on_clear`. **Rosbag:** a RAM ring buffer written to MCAP, 5 s before and 1 s after by
  default. **Storage:** SQLite (default `/var/lib/ros2_medkit/faults.db`) or PostgreSQL. **Audit log:** optional,
  hash-chained, tamper-evident (not tamper-proof).
- **Correlation:** hierarchical root-cause→symptom rules (mute symptoms, auto-clear with root) and auto-clusters
  ("communication storm").
- **SOVD status object on the REST fault detail:** `aggregatedStatus` (`active|passive|cleared`), `testFailed`,
  `confirmedDTC` and `pendingDTC` ("0"/"1"). These are the UDS DTC status bits. `?status=` maps `pending` to PREFAILED,
  `confirmed` to CONFIRMED, and `cleared` to CLEARED+HEALED+PREPASSED ("SOVD 'cleared' semantics"). Extended data
  records are `first_occurrence` and `last_occurrence`.
- Faults are keyed by `fault_code` **globally**. Per-entity routes only show a fault if *every* reporting source belongs
  to that entity, so a code shared by two entities is visible only on the global route.

---

## 4. Autoware: diagnostic graph

Sources: https://github.com/autowarefoundation/autoware_universe/tree/main/system/autoware_diagnostic_graph_aggregator,
`…/autoware_diagnostic_graph_utils`, `…/autoware_diagnostic_graph_merger`, `…/autoware_hazard_status_converter`;
public AD API messages at https://github.com/autowarefoundation/autoware_adapi_msgs/tree/main/autoware_adapi_v1_msgs/system/msg;
internal messages at https://github.com/tier4/tier4_autoware_msgs/tree/tier4/universe/tier4_system_msgs/msg; doc
https://docs.autoware.org/main/design/autoware-architecture-v1/interfaces/ad-api/features/diagnostics/.

### 4.1 Exact messages

Internal (`tier4_system_msgs`, topics `/diagnostics_graph/struct` and `/diagnostics_graph/status`):
```
# DiagGraphStruct.msg
builtin_interfaces/Time stamp
string id
tier4_system_msgs/DiagNodeStruct[] nodes
tier4_system_msgs/DiagLeafStruct[] diags
tier4_system_msgs/DiagLinkStruct[] links

# DiagGraphStatus.msg
builtin_interfaces/Time stamp
string id
tier4_system_msgs/DiagNodeStatus[] nodes
tier4_system_msgs/DiagLeafStatus[] diags
tier4_system_msgs/DiagLinkStatus[] links

# DiagNodeStruct.msg
string path
string type

# DiagNodeStatus.msg
byte level
byte input_level
byte latch_level
bool is_dependent
bool is_overridden

# DiagLeafStruct.msg
uint32 parent
string name

# DiagLeafStatus.msg
byte level
byte input_level
string message
string hardware_id
diagnostic_msgs/KeyValue[] values

# DiagLinkStruct.msg
# The index of nodes in the graph struct message.
uint32 parent
uint32 child

# DiagLinkStatus.msg
(empty)
```

Public AD API (`autoware_adapi_v1_msgs`, `/api/system/diagnostics/{struct,status,reset}`): the same shape without
`DiagLinkStatus`, without `type` or `is_overridden`, and with values as `autoware_adapi_v1_msgs/KvString[]` (`string
key`, `string value`):
```
# DiagNodeStatus.msg
# The level of diagnostic_msgs/msg/DiagnosticStatus.
byte level
byte input_level
byte latch_level
bool is_dependent

# DiagLeafStatus.msg
# The level of diagnostic_msgs/msg/DiagnosticStatus.
byte level
byte input_level
string message
string hardware_id
autoware_adapi_v1_msgs/KvString[] values
```
`ResetDiagGraph.srv`: `--- autoware_adapi_v1_msgs/ResponseStatus status`.
`Heartbeat.msg`:
```
# Timestamp in Autoware for delay checking.
builtin_interfaces/Time stamp

# Sequence number for order verification, wraps at 65535.
uint16 seq
```

**Link status was tried and dropped.** In 2024-03 (tier4 #113), `DiagLinkStatus` was `bool used` and
`DiagLinkStruct` had `bool is_leaf`. In 2025-06 (#183), link status became empty, leaves got a `parent` index, and
`input_level`, `latch_level`, `is_dependent` and `ResetDiagGraph` were added
(https://github.com/tier4/tier4_autoware_msgs/commit/f3a5fdc10c).

### 4.2 What changed vs the ROS tree aggregator

- **Explicit DAG, not a name-prefix tree.** A YAML "unit" graph (fault-tree analysis per operation mode). A child can
  have several parents.
- **The leaf is DiagnosticStatus, verbatim:** level, message, hardware_id, values. Leaves are matched by the *pair*
  `node` + `name` (the ROS 2 updater's `node: name`).
- **Unit types:** `diag`, `link`, `and` (max level), `or` (min level), `warn-to-ok`/`warn-to-error` remaps, and
  constants `ok/warn/error/stale`. **STALE is treated as ERROR inside and/or.** `short-circuit-and` is deprecated in
  favour of `dependent`.
- **Per-leaf `timeout`** (required) and **`hysteresis`** seconds: level changes shorter than this are ignored.
- **Per-unit `latch`** seconds: the worst level latches after persisting that long. `input_level` is the pre-latch
  level, `latch_level` the latched one. Clearing is an explicit reset service. `initial_latch_suppression: true`.
- **`dependent`:** if the named unit is not OK, this unit's `is_dependent=true`, so a UI can hide downstream
  symptoms and show the root cause.
- **Static/dynamic split:** the struct is published once (transient-local), the status is periodic (`rate: 10.0`)
  with the same array lengths and order, so status is index-aligned and compact. `id` guards against mixing a struct
  and a status from different runs. The **merger** concatenates two ECUs' graphs for redundant compute.
- **Outputs a capability, not a colour:** `OperationModeAvailability` (internal) is one bool per mode (`stop`,
  `autonomous`, `local`, `remote`, `emergency_stop`, `comfortable_stop`, `pull_over`). The AD API's
  `OperationModeState` carries `is_*_mode_available` bools.
- **Unmatched diagnostics are not lost:** they are republished on `/diagnostics_graph/unknowns`. A converter
  turns the graph back into a `DiagnosticArray` so `rqt_runtime_monitor` still works.
- **Severity in context (hazard).** The legacy `autoware_system_msgs/HazardStatus` (NO_FAULT, SAFE_FAULT,
  LATENT_FAULT, SINGLE_POINT_FAULT; `emergency`, `emergency_holding`; four `DiagnosticStatus[]` buckets) is now
  *computed* from the graph. A unit's hazard depends on its own level **and the level of the autonomous-mode root**:
  ERROR under an OK root is only a SAFE_FAULT, and under an ERROR root a SINGLE_POINT_FAULT
  (https://github.com/autowarefoundation/autoware_universe/blob/main/system/autoware_hazard_status_converter/src/converter.cpp).

---

## 5. Apex.AI Apex.Grace fault management (public material only)

- "Diagnostics Reimagined" blog (2025-07-09,
  https://www.apex.ai/post/diagnostics-reimagined-production-ready-fault-management-with-apex-grace):
  - **Fault Monitors** in applications capture diagnostic events and debounce them.
  - Validated faults go to a **Diagnostic Fault Manager (DFM)**, which maps them to **DTCs** and persists them in
    Apex.Grace Message Storage.
  - DTCs carry **snapshot and extended data** (timestamps, fault counters, custom OEM metadata).
  - Transport and services are UDS (ISO 14229) over DoIP (ISO 13400) and DoCAN (ISO 15765-2), with a UDS server and
    gateway. CAN/IP frames become ROS 2 messages.
- "Health and State Management" blog (2025-05-28,
  https://www.apex.ai/post/building-resilient-robotic-systems-with-apex-grace-health-and-state-management): AUTOSAR
  Classic-style. An Event System with a configurable dispatcher, ECU monitoring, execution monitoring (time,
  frequency, activation, via shared memory), PTP time-sync monitoring, a process manager, and a **System Manager**
  that executes fault reactions and state transitions.
- ROSCon 2025 talk "Production-Ready Diagnostics with Apex.OS" (Dongre): the slides are behind a bot-check
  (https://roscon.ros.org/de/2025/img/slides/S6_4__Dongre__ProductionReady_Diagonostics_with_ApexOS.pdf) and were
  **not read**.
- **No public message or data model.** Nothing public relates the DFM to `diagnostic_msgs`. The architecture is the
  same three stages as medkit: monitor (debounce) → fault manager (DTC + snapshot + extended data, persisted) →
  diagnostic server (UDS). Reaction lives in a separate System Manager.

---

## 6. Others, one block each

### 6.1 NVIDIA Greenwave Monitor (frequency diagnostics)
https://github.com/NVIDIA-ISAAC-ROS/greenwave_monitor, announced 2025-10-09
(https://discourse.openrobotics.org/t/nvidias-greenwave-monitor-a-tool-for-high-performance-topic-monitoring-and-diagnostics/50477),
1.0.0 in 2026-02.
- Publishes **standard `DiagnosticArray`** with conventional keys: `frame_rate_node` (receive-time rate),
  `frame_rate_msg` (header-stamp rate), `current_delay_from_realtime_ms`, `expected_frequency`, `tolerance`.
- Expectations are declared per topic (`gw_frequency_monitored_topics.<topic>.expected_frequency` / `.tolerance`, or
  `setExpectedDt(hz, tolerance_percent)`).
- **The publisher sets STALE itself** when nothing updated since the last publish.
- It separates header-time from receive-time and has presets for headerless topics.
- Lesson: rate monitoring wants *two* clocks and a *declared* expectation, and NVIDIA had to invent a key convention
  on top of KeyValue.

### 6.2 rosgraph_monitor (bonus: ROSGraph WG, 2025)
https://github.com/ros-tooling/graph-monitor
- Emits `DiagnosticStatus` for node liveness (once seen, now gone → ERROR), graph continuity (dangling pub/sub → WARN)
  and topic frequency. The **expected rate is the publisher's Deadline QoS**: declared on the endpoint itself, with no
  separate config.
- Typed statistics message `rosgraph_monitor_msgs/TopicStatistic`: `uint8 statistic_type` (PUBLISHED_PERIOD,
  RECEIVED_PERIOD, TAKE_AGE), `node_name`, `topic_name`, `int32 window_count`, and `builtin_interfaces/Duration
  mean/min/max`.

### 6.3 ros2_control hardware component diagnostics
- **Existing:** the controller_manager runs a `diagnostic_updater` with hardware_id `ros2_control` and tasks
  "Controllers Activity", "Hardware Components Activity" and "Controller Manager Activity". It reports per-component
  lifecycle state and `.execution_time` / `.periodicity` statistics *as strings with units* (2.3), with thresholds
  from parameters (https://github.com/ros-controls/ros2_control/pull/2005, #1871).
- **New (GSoC 2025, merged 2025-08-27):** `control_msgs/HardwareStatus` (fast, machine-readable) is kept apart from
  `HardwareDiagnostics` (slow, KeyValue, "ideally not parsed by control loops"). Default publisher merged 2025-10-02
  (#2476); `ros2 control view_hardware_status` CLI 2025-10-25 (#2495)
  (https://github.com/ros-controls/control_msgs/pull/240). Exact core fields:
```
# HardwareStatus.msg
std_msgs/Header header
string hardware_id
HardwareDeviceStatus[] hardware_device_states

# HardwareDeviceStatus.msg
std_msgs/Header header
string device_id
GenericHardwareState[] hardware_status
CANopenState[] canopen_states
EtherCATState[] ethercat_states
VDA5050State[] vda5050_states

# GenericHardwareState.msg (fields; constants abbreviated below)
uint8 health_status
uint8[] error_domain
uint8 operational_mode
uint8 power_state
uint8 connectivity_status
string manufacturer
string model
string firmware_version
diagnostic_msgs/KeyValue[] state_details

uint8 HEALTH_UNKNOWN  = 0
uint8 HEALTH_OK       = 1
uint8 HEALTH_DEGRADED = 2
uint8 HEALTH_WARNING  = 3
# Hardware stops publishing state when it returns ERROR/FATAL, how are these set/updated?
uint8 HEALTH_ERROR    = 4
uint8 HEALTH_FATAL    = 5
```
  - Other constants: `ERROR_NONE/UNKNOWN/HW/SW/OVER_TRAVEL`, `EMERGENCY_STOP_HW/SW`, `PROTECTIVE_STOP_HW/SW`,
    `SAFETY_STOP`, `CALIBRATION_REQUIRED`; `MODE_*` (13 values); `POWER_*` (UNKNOWN, OFF, STANDBY, ON, SLEEP, ERROR,
    LEVEL_LOW, LEVEL_CRITICAL, CHARGING, CHARGING_ERROR); `CONNECT_UNKNOWN/UP/DOWN/FAILURE`, `CONNECTION_SLOW`.
  - `CANopenState`: `node_id`, `nmt_state`, `dsp_402_state`, `uint32 last_emcy_code`.
  - `EtherCATState`: `slave_position`, `vendor_id`, `product_code`, `al_state`, `has_error`, `uint16 al_status_code`.
  - Note the open question committed as a comment in `GenericHardwareState`. The design is young.
  - A Discourse reply asked why not reuse ros/diagnostics instead of a new system (49461 #2). That tension is unresolved.

### 6.4 nav2: lifecycle + bond heartbeats
https://github.com/ros-navigation/navigation2/blob/main/nav2_lifecycle_manager/src/lifecycle_manager.cpp
- **Liveness is a separate channel.** Each managed node forms a `bond` with the lifecycle manager: `bond_timeout` 4.0 s,
  `bond_heartbeat_period` 0.25 s, `attempt_respawn_reconnection` true, `bond_respawn_max_duration` 10 s. A broken
  bond triggers a **hard reset of the whole stack** ("if one is down, bring them all down").
- `/diagnostics` only *reports* the lifecycle state ("Nav2 Health": active/inactive/unconfigured → OK, finalized →
  WARN, unknown → ERROR).
- `bond/Status.msg`: `std_msgs/Header header`, `string id`, `string instance_id`, `bool active`,
  `float32 heartbeat_timeout`, `float32 heartbeat_period`. Including both timeouts in every message makes
  side-to-side mismatches debuggable (https://github.com/ros/bond_core/blob/ros2/bond/msg/Status.msg).

### 6.5 Foxglove / Lichtblick diagnostics handling
- Foxglove docs (https://docs.foxglove.dev/docs/visualization/panels/diagnostics): accepts only `DiagnosticArray`;
  drill-down per "diagnostic name and hardware ID"; a user-set **stale timeout** in seconds.
- The open fork's code (https://github.com/lichtblick-suite/lichtblick/tree/develop/packages/suite-base/src/panels/DiagnosticSummary):
  - id = `|hardware_id|name|`, with the leading `/` stripped from hardware_id; grouped by hardware_id, then name;
    display `hardware_id: name`.
  - `DEFAULT_SECONDS_UNTIL_STALE = 5` ("ROS rqt_runtime_monitor default").
  - **Staleness is computed client-side from the `DiagnosticArray` header stamp**, so an unstamped array is
    instantly stale.
  - Values over 5000 chars are truncated.

### 6.6 MAVLink HEARTBEAT / SYS_STATUS / events (non-ROS robotics)
https://github.com/mavlink/mavlink/blob/master/message_definitions/v1.0/minimal.xml and `common.xml`
- **HEARTBEAT (id 0):** `type` (MAV_TYPE), `autopilot`, `base_mode` (MAV_MODE_FLAG bitmap), `custom_mode` (uint32),
  `system_status` (MAV_STATE), `mavlink_version`. The heartbeat is *presence*; identity is (system id, component id)
  in the frame header.
- **MAV_STATE:** UNINIT=0, BOOT, CALIBRATING, STANDBY, ACTIVE, CRITICAL, EMERGENCY, POWEROFF, FLIGHT_TERMINATION.
  That is readiness/mode, not severity.
- **SYS_STATUS (id 1):** three parallel bitmaps per subsystem: `onboard_control_sensors_present`, `…_enabled` and
  `…_health` (plus `_extended` versions). The bits are a fixed enum: gyro, accel, mag, GPS, motor outputs,
  RC, battery, pre-arm check, propulsion, … Then `load` (d%), `voltage_battery` (mV), `current_battery` (cA),
  `battery_remaining` (%), `drop_rate_comm` (c%), `errors_comm`, and `errors_count1..4` (autopilot-specific). **Every
  numeric field declares units.** *Present / enabled / healthy* are three different facts.
- **STATUSTEXT** with `MAV_SEVERITY` (RFC-5424 syslog levels, 8 values) is for *events*, not status.
- **Events interface (replacing STATUSTEXT)** (https://mavlink.io/en/services/events.html; EVENT id 410):
  - Each event has a 32-bit `id` (component namespace + event), `event_time_boot_ms`, a `uint16 sequence`, and
    separate *internal* and *external* log levels.
  - Up to 40 bytes of **typed arguments**. Text, argument types and groups (`health`, `arming_check`, …) live in
    **JSON metadata** fetched once.
  - `CURRENT_EVENT_SEQUENCE` every ~3 s plus `REQUEST_EVENT` give **gap detection and retransmission**.
  - A **health-and-arming-check** sub-protocol reports `can_arm` / `can_run` per mode plus per-component `is_present`
    / `error` / `warning`.

### 6.7 DroneCAN NodeStatus (and its successor, Cyphal Heartbeat)
https://github.com/dronecan/DSDL/blob/master/uavcan/protocol/341.NodeStatus.uavcan
```
uint16 MAX_BROADCASTING_PERIOD_MS = 1000
uint16 MIN_BROADCASTING_PERIOD_MS = 2
uint16 OFFLINE_TIMEOUT_MS = 3000
uint32 uptime_sec
uint2 HEALTH_OK         = 0     # The node is functioning properly.
uint2 HEALTH_WARNING    = 1     # A critical parameter went out of range or the node encountered a minor failure.
uint2 HEALTH_ERROR      = 2     # The node encountered a major failure.
uint2 HEALTH_CRITICAL   = 3     # The node suffered a fatal malfunction.
uint2 health
uint3 MODE_OPERATIONAL      = 0         # Normal operating mode.
uint3 MODE_INITIALIZATION   = 1         # Initialization is in progress; this mode is entered immediately after startup.
uint3 MODE_MAINTENANCE      = 2         # E.g. calibration, the bootloader is running, etc.
uint3 MODE_SOFTWARE_UPDATE  = 3         # New software/firmware is being loaded.
uint3 MODE_OFFLINE          = 7         # The node is no longer available.
uint3 mode
uint3 sub_mode
uint16 vendor_specific_status_code
```
(The original's comment lines are omitted. They say the message is required of every node, the period should not
change at runtime, uptime going backwards means a restart, and OFFLINE can be sent voluntarily before shutdown.)

- **Identity lives in a separate, rarely changing reply.** `GetNodeInfo` returns `name` (reverse-DNS, e.g.
  `com.manufacturer.project.product`), `HardwareVersion` (`major`, `minor`, `uint8[16] unique_id`, a 128-bit global
  UID, plus a certificate of authenticity) and `SoftwareVersion` (`major`, `minor`, `vcs_commit`, `uint64
  image_crc`, described as the "absolute identity guarantee" of the firmware).
- **Cyphal `uavcan.node.Heartbeat.1.0`**
  (https://github.com/OpenCyphal/public_regulated_data_types/blob/master/uavcan/node/7509.Heartbeat.1.0.dsdl):
  `uint32 uptime`, `Health` (**NOMINAL / ADVISORY / CAUTION / WARNING**, after FAA AC 25.1322-1), `Mode`
  (OPERATIONAL / INITIALIZATION / MAINTENANCE / SOFTWARE_UPDATE), `uint8 vendor_specific_status_code`,
  MAX_PUBLICATION_PERIOD 1 s, OFFLINE_TIMEOUT 3 s, fits one classic-CAN frame.
- **Severity for events is separate:** `uavcan.diagnostic.Record` has `timestamp`, `Severity` (8 levels, TRACE …
  ALERT) and text. The ROS "additional levels" proposal borrowed Cyphal's *log* severity for a *health* field. Cyphal
  itself keeps health at 4 levels.
- **Relevance to the rack:** a moteus board maps onto this one-to-one: `mode` → mode, `fault` → vendor code,
  uptime → restart detection. The "limiting" codes (96-105) are *advisory* (Cyphal ADVISORY), not faults. A 4-level
  health with a separate mode keeps that distinction without abusing ERROR.

---

## 7. Synthesis for this lane

### 7.1 Field table

"In DS?" means present in `DiagnosticStatus`/`DiagnosticArray` today.

| Field / concept | In DS? | Added by | Why |
|---|---|---|---|
| Severity level | yes: `byte level` OK/WARN/ERROR (+STALE) | medkit `severity` INFO/WARN/ERROR/CRITICAL; ros2_control `health_status`; DroneCAN `health` 4-level; Cyphal Health NOMINAL…WARNING | everyone kept a *small* severity scale; 8-level proposals were rejected or kept for logs |
| `UNKNOWN = 0` / not evaluated | no (0 = OK, so a zero-initialised message reads green) | ros2_control `HEALTH_UNKNOWN=0`; MAVLink `MAV_STATE_UNINIT=0`; medkit thread: PASS/FAIL/NOT_EVALUATED; UDS "test not completed" | a default must not mean healthy; "didn't run" ≠ "passed" |
| Freshness / STALE | only as level 3 (convention: aggregator-set) | DroneCAN `OFFLINE_TIMEOUT_MS=3000` + `MODE_OFFLINE`; Autoware per-leaf `timeout`; Greenwave sets STALE itself; Lichtblick 5 s client-side | freshness is a different axis from severity; putting it on `level` caused #297/#658 |
| Declared expected period / rate | no | DroneCAN MAX/MIN broadcasting period; Greenwave `expected_frequency`+`tolerance`; rosgraph_monitor Deadline QoS; Autoware `timeout` | staleness and rate checks need a per-source declared expectation |
| Operational mode / readiness | no | DroneCAN/Cyphal `mode`; MAVLink `system_status`, `base_mode`; ros2_control `operational_mode`, `power_state`, `connectivity_status`; nav2 lifecycle state | "initialising", "in maintenance", "updating firmware" are not WARN/ERROR |
| Present / enabled / healthy | no | MAVLink SYS_STATUS three bitmaps; MAVLink health report `is_present` | absent and disabled are not failures |
| Uptime / restart detection | no | DroneCAN `uptime_sec`; Cyphal `uptime` | a counter going backwards reveals a reboot that a level can hide |
| Per-record timestamp | no (only `DiagnosticArray.header.stamp`) | ros2_control per-device `Header`; medkit `first/last_occurred`, `last_passed`; Cyphal `Record.timestamp`; MAVLink `event_time_boot_ms`; Autoware graph `stamp` | aggregated or relayed statuses lose their own time |
| Sequence number / gap detection | no | MAVLink event `sequence` + CURRENT_EVENT_SEQUENCE; Autoware `Heartbeat.seq` | events must not be silently lost |
| Stable source identity | `name` + `hardware_id` (free text, overloaded) | medkit `source_id` (node FQN) / `reporting_sources`, `EntityInfo.id/fqn/parent_id`; ros2_control `hardware_id`+`device_id`; MAVLink sysid/compid | aggregator rewrites `name`; hardware_id means three things (#520, #666) |
| Hardware / firmware identity | by convention in `values` (REP-107 lists serials, firmware) | DroneCAN `unique_id[16]`, `image_crc`, reverse-DNS `name`; ros2_control `manufacturer`, `model`, `firmware_version` | track a part across robots; know exactly which firmware runs |
| Hierarchy / topology | no; name prefix + aggregator config | Autoware DAG (`DiagGraphStruct` nodes/diags/links, `parent` index); medkit SOVD entity tree Area/Component/App/Function | the tree must be data, not inferred from strings |
| Static vs dynamic split | no | Autoware struct (once, transient-local) vs status (periodic, index-aligned, `id`-guarded); MAVLink event metadata JSON vs EVENT; DroneCAN GetNodeInfo vs NodeStatus | send identity/topology once, keep the periodic message small |
| Combination logic | aggregator: worst-of + STALE rules | Autoware `and` (max) / `or` (min) / remap / const | redundancy (OR) cannot be expressed as worst-child |
| Dependency / root cause | no | Autoware `dependent` → `is_dependent`; medkit correlation (`root_cause_code`, muted symptoms, clusters) | show the cause, suppress symptoms |
| Hysteresis / latch / reset | no | Autoware `hysteresis`, `latch`, `input_level`/`latch_level`, ResetDiagGraph; medkit latched CONFIRMED/HEALED | flapping filtered; a transient emergency must stay visible until acknowledged |
| Fault code | no (only `message` text) | medkit `fault_code` (UPPER_SNAKE, ≤64); DroneCAN `vendor_specific_status_code`; MAVLink event `id`, `errors_count1..4`; CANopen `last_emcy_code`; EtherCAT `al_status_code`; Apex DTC | machines need an identity for "this fault" |
| Fault lifecycle | no (stateless snapshot) | medkit PREFAILED/PREPASSED/CONFIRMED/HEALED/CLEARED + SOVD bits; Apex DFM | confirm after debounce, heal, acknowledge, keep history |
| Occurrence counters / near misses | no | medkit `occurrence_count` (edges), near-miss series; Apex fault counters | "how often" and "nearly" are the maintenance signal |
| Freeze frame / snapshot / extended data | no | medkit `Snapshot` (freeze_frame or MCAP bag), `ExtendedDataRecords`; Apex snapshot + extended data | evidence at the moment of failure |
| Typed values + units | no (`KeyValue` strings) | MAVLink typed fields with `units`; MAVLink events typed args; rosgraph_monitor `Duration`; ros2_control typed enums (still KeyValue for details) | consumers parse strings (ros2_control stats, InfluxDB guessing, Greenwave magic keys) |
| Context-dependent impact | no | Autoware hazard converter (unit level × mode-root level); `OperationModeAvailability`; MAVLink `can_arm/can_run` | a driver can't know if its failure matters; the system graph can |
| Liveness / heartbeat | explicitly not (REP-107) | bond `Status` (+ both timeouts in-message); DroneCAN NodeStatus; MAVLink HEARTBEAT; Autoware Heartbeat; DDS liveliness/deadline | keepalive needs tight, reliable timing; `/diagnostics` is 1 Hz best effort |
| Actions / verbs | `SelfTest.srv` only | medkit REST operations (services/actions), configurations (parameters), clear; ResetDiagGraph | a fault needs verbs: acknowledge, fetch recording, reset |
| Source authenticity | no | SROS2 (tfoote's answer); medkit audit log (tamper-evident) | "who said this" matters once faults drive actions |

### 7.2 The lessons (most important first)

1. **Don't fork the wire format. Wrap it.** Seventeen years, one constant added, and every new effort (medkit,
   Autoware, Greenwave, rosgraph_monitor, nav2, ros2_control) still emits or consumes DiagnosticStatus. Any field
   added *inside* it breaks bags, the aggregator and every viewer. The successful pattern is DiagnosticStatus as the
   leaf/health stream, plus sibling records for what it can't say. Projecting your record *down* to DiagnosticStatus
   buys rqt, Foxglove, Lichtblick, medkit and Autoware for free.
2. **`level` conflates three axes. Keep them separate.** Severity (OK/WARN/ERROR), freshness (STALE) and
   readiness/mode (initialising, maintenance, updating, offline) are orthogonal. Encoding STALE as 3 > ERROR cost the
   aggregator a decade of wrong roll-ups (#297 → #593, 2026), and the ordering argument is still open (#658).
   DroneCAN, Cyphal and MAVLink always carried `health` and `mode` as separate fields. Add `UNKNOWN = 0` so a
   zero-initialised or never-evaluated record is never green.
3. **Status, event and fault are three shapes.**
   - A *status* is a sampled level over time (DroneCAN health, DiagnosticStatus).
   - An *event* is discrete, sequenced and needs reliable delivery and log severity (MAVLink events, Cyphal Record,
     8 syslog levels).
   - A *fault* is a stateful record with an address, lifecycle, counters and evidence (medkit, AUTOSAR DEM, Apex DFM).

   The 2025 "more levels" fight was mostly people wanting events or faults inside a status field.
4. **Severity of impact is contextual. Put it in a declared graph, not in the driver.** A driver reports *its own*
   health. Whether that matters depends on mode and redundancy (tfoote, Timple, v4hn). Autoware made this
   executable: an and/or DAG per operation mode, `dependent` for root-cause, and outputs that are capability
   booleans (`OperationModeAvailability`, MAVLink `can_arm`/`can_run`). The hazard level is computed from leaf level ×
   mode level.
5. **Identity must be explicit, stable and single-meaning.** `name` gets rewritten by the aggregator. `hardware_id`
   is used as a serial number, a node FQN and an empty string, and tools key on the pair, so an empty first message
   duplicates the row. Give each meaning its own field: source/reporter (node FQN or driver), position in the tree
   (path or entity id + parent), and device identity (serial/UID, model, firmware version and hash, as DroneCAN does).
   Send identity and topology once (static/dynamic split), not in every 1 Hz message.
6. **Typed values with units, or every consumer re-parses strings.** The evidence is ros2_control's
   `"Avg … [min - max] us, StdDev …"`, InfluxDB number-guessing and Greenwave's unversioned magic keys. MAVLink
   (units on every field) and rosgraph_monitor (`Duration` fields) show the alternative. Keep a free-form string
   key/value list for humans, but measurements that monitors or plots will read should be `{key, number, unit}`.
7. **Time and expectation travel with the record.** One `header.stamp` per array is not enough once records are
   relayed, aggregated or replayed. Viewers compute staleness from it (Lichtblick, 5 s) and break when it is unset
   (#341). Staleness is only meaningful against a *declared* expected period per source: DroneCAN's period plus
   3 s offline timeout, Greenwave's expected frequency plus tolerance, rosgraph_monitor's Deadline QoS, Autoware's
   per-leaf timeout. Add uptime so restarts are visible.
8. **Debounce, hysteresis, latching and "not evaluated" belong to a monitor/fault layer, never to the reporter's
   level.**
   - Time debounce and count debounce are not interchangeable.
   - Count debounce fails for edge-only reporters (medkit's documented trap).
   - Enable conditions beat softening a level.
   - A check that didn't run must not count as a pass.

   Liveness and safety reactions use their own channels (bond, DDS liveliness, heartbeat), as REP-107 said in 2010.

### 7.3 What this means for the IO-diagnostics contract (input for the orchestrator)

`docs/converged-design-2026-10-22.md` §3 proposes **DiagnosticStatus + `path` + `expected_period_s` + `identity`**.
The evidence supports that core. Every row in 7.1 that a newer system added and that applies to a hardware IO
dashboard suggests these small additions. Each one is something another project measured it needed:

- `stamp` per record, plus `uptime_s` where the device reports one. *Why:* relay/aggregation loses time; reboot
  detection (DroneCAN).
- `mode` as a separate enum: `unknown, initializing, operational, maintenance, updating, offline`. *Why:* keeps STALE
  and "busy updating firmware" out of `level` (DroneCAN/Cyphal/MAVLink).
- `level` gains `UNKNOWN` semantics for never-evaluated, and STALE stays *computed by the host* from
  `expected_period_s`, never sent by drivers. *Why:* #297/#658, Shannon's zero-init point, medkit's NOT_EVALUATED.
- `code` (optional, stable machine id of the current condition, e.g. the moteus fault number or the CANopen EMCY
  code) next to the human `message`. *Why:* medkit's fault_code, DroneCAN's vendor code, ros2_control's EMCY and AL
  codes.
- `measurements: [{key, value: float, unit}]` beside the string `values`. *Why:* section 2.3.
- Identity and topology published once and on change, with periodic records referencing them by `path`. *Why:*
  Autoware's struct/status split, DroneCAN GetNodeInfo vs NodeStatus.
- **Faults and events as sibling records, not new levels.** A plug journal entry is an *event* (MAVLink/Cyphal
  shape: id, time, severity, sequence). A latched fault with first/last seen, count and freeze frame is a *fault*
  (medkit `Fault` shape). Reuse medkit's field names, since it is the only ROS-native fault record, it is Apache-2.0,
  and it maps to ISO 17978.

The down-projection to `DiagnosticStatus` stays lossless for the classic fields. `path` goes into `name`
(`host/usb-1-5/…: comms`). The serial goes into `hardware_id`, never the node name. Mode, code and measurements go
into `values` with fixed keys, so rqt, Foxglove and medkit's bridge keep working.

---

### Gaps and caveats in this lane
- Apex: only two marketing-level blog posts are public. The ROSCon 2025 slides are behind a bot-check and were not
  read.
- docs.ros.org is behind a bot-check. Message definitions were taken from GitHub sources instead.
- Foxglove's own panel code is closed. Its behaviour was inferred from the docs plus the Lichtblick fork's code.
- medkit moves fast: v0.7.0, with issues #668/#669 open. Field comments may change. The quoted definitions are from
  `main` on 2026-10-22.
- The Discourse threads are summarised in paraphrase, attributed by post number. Exact wording is in the links.
