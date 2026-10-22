# Diagnostic status records outside ROS: standards, field definitions, lessons

- **Lane:** non-ROS standards (one of two parallel lanes; the ROS lane covers `diagnostic_msgs` and its descendants).
- **Question (Zach, 2026-10-22):** ROS `DiagnosticStatus` (level OK/WARN/ERROR/STALE, name, message, hardware_id, values[key,value]) is a good contract, like URDF. What has changed since, and what have other fields learned about what a good diagnostic status record looks like?
- **Accessed:** 2026-10-22. Primary sources only, fetched raw (schemas, RFC text, spec PDFs, upstream doc repos). No sign-ins.
- **Quoting policy:** field names, enum literals and types are verbatim. Definitions are short quotes (marked with quotation marks) or close paraphrase; follow the URL for the full normative text.
- **Paywalled:** ISO 14229-1 (UDS) and ISO 17978 (SOVD) are paywalled, and the body of ASAM SOVD 1.0 is members-only. For these I used three free primary sources instead. (1) AUTOSAR R24-11, which implements both standards and states the mapping normatively. (2) ASAM's free table of contents and release deck. (3) The Eclipse OpenSOVD reference code. Section 9 lists what remains unverified.

---

## 0. Verdict

**Keep the ROS record as the wire shape, but split it the way everyone else did.** Since about 2005, every mature non-ROS standard has pulled apart four things that `DiagnosticStatus` mixes together:

1. **Identity + containment.** A stable, repeatable entity id plus a parent edge, kept separate from status: HA `device_info`, the OTel entity model, Entity MIB `entPhysicalContainedIn`, Redfish resources.
2. **Typed observations.** Each value carries a unit, a source timestamp and thresholds (with hysteresis) in the same units: Redfish `Sensor`, Entity-Sensor-MIB, Nagios perfdata, IETF `observedValue`/`observedUnit`.
3. **Normalized health.** This is a 3-level severity, **plus "can't tell" as its own value rather than a severity**. Each entity has a raw health and a separately *computed* rollup: Redfish `Health` vs `HealthRollup`, and RFC 4268's "raw state" vs "computed state".
4. **Conditions/faults** are a list keyed by a **stable machine code**, each with a lifecycle that separates "failing right now" from "confirmed and stored". They come with a snapshot captured at the moment of the fault: K8s conditions, Redfish `Conditions`, UDS DTC status byte + AUTOSAR DEM debounce/aging/freeze frames, SOVD `faults` + `environment_data`.

**The single biggest gap in ROS's record is the missing machine code.** Today `message` is free text and `name` names the *check*, not the *reason*. Every newer standard has a stable reason code that is separate from the human text: K8s `reason`, Redfish `MessageId`, SOVD `code`, HA `issue_id`/`translation_key`. The second-biggest gap is untyped `values`: strings with no unit, no thresholds and no timestamp.

---

## 1. What changed: timeline

| When | What | Why it matters here |
|---|---|---|
| 2002-12 | RFC 3433 Entity Sensor MIB: value + type + scale + precision + `OperStatus` + `ValueTimeStamp` + `ValueUpdateRate` | Typed value with an update rate, i.e. staleness, 24 years ago |
| 2005-11 | RFC 4268 Entity State MIB: separate admin / oper / usage / alarm / standby states; "raw" vs "computed" state | Severity ≠ lifecycle; rollup is a different field |
| 2006 → 2020 | UDS ISO 14229 (first 2006; AUTOSAR R24-11 cites 14229-1:2020): the 8-bit DTC status byte | Current test result ≠ stored fault |
| 2013-05 / 2013-10 | RFC 6933 Entity MIB v4 (adds `entPhysicalUUID`); IPMI v2.0 rev 1.1 | Containment as a strict tree; thresholds nc/cr/nr + hysteresis |
| 2018-04 | K8s KEP-589: node heartbeat moved to a separate `Lease` object | Liveness ≠ status |
| 2020-03 (k8s 1.19) | K8s KEP-1623: one standard `metav1.Condition`; `lastHeartbeatTime` removed; `reason` required | Machine reason code is mandatory |
| 2020.4 → 2025.2 | Redfish `Status.Conditions` (2020.4), `Resolution` (2021.4), Sensor hysteresis/dwell (2023.1), `ResolutionSteps` (2023.3), `State: Degraded` (2024.1), `ConditionType` (2025.2), `VendorCode` (2026.2) | Hardware health record still evolving, in the direction of conditions + fixes |
| 2021-10 (expired 2022-04) | IETF draft-inadarei-api-health-check-06, `application/health+json` | Never became an RFC; widely copied anyway |
| 2022-06-30 | ASAM SOVD 1.0.0 | REST/JSON diagnostics with an entity tree, faults, data, operations, modes, **locks** |
| 2024-04-02 | OpenBMC removes Redfish HealthRollup support | Rollup is hard; a lesson on the record |
| 2024-11-27 | AUTOSAR R24-11 normatively maps UDS DTC → SOVD fault | The best free statement of SOVD fault fields |
| 2026-03 / 04 / 05 | ISO 17978-3 (API), -2 (use cases), -1 (general) published | SOVD is now an ISO standard |
| 2026 (Development) | OTel `hw.*` semconv and the Entity data model | Identity rules: minimal + repeatable |
| 2026-08-24 | Home Assistant documents **child devices** (`parent_device_id`, composition) as distinct from `via_device_id` (connectivity) | Two kinds of parent edge, not one |

---

## 2. ASAM SOVD / ISO 17978 (Service-Oriented Vehicle Diagnostics)

**Status/dates.** ASAM SOVD 1.0.0 was released 2022-06-30 ([ASAM page](https://www.asam.net/standards/detail/sovd/); [release deck PDF](https://www.asam.net/index.php?eID=dumpFile&t=f&f=5035&token=e64977333fa4379bc8222b5ed74849270627f91f); [TOC PDF](https://www.asam.net/index.php?eID=dumpFile&t=f&f=5036&token=b339d6abff02d6b0f6884f4fe6e0681955160505)). ISO published the three parts in 2026: [ISO 17978-3:2026 API](https://www.iso.org/standard/86587.html) (2026-03), [ISO 17978-2:2026 use cases](https://www.iso.org/standard/86586.html) (2026-04) and [ISO 17978-1:2026 general](https://www.iso.org/standard/85133.html) (2026-05). Eclipse hosts an open implementation, [Eclipse OpenSOVD](https://github.com/eclipse-opensovd) (active Oct 2026).

**Entity model** (TOC §4.2 "Entity Types", §4.2.3 "Entity Hierarchy and Relations", §4.2.4 "Entity Identifier and Path"):
- The entity types are the SOVD server, **areas**, **components**, **apps** and **functions**.
- The release deck: "Areas represent a topological view on the entities", able to represent both domain-oriented and zone-oriented architectures.
- Discovery is by API: "Discover Contained Entities", "Query Sub-Entities of an Entity" and "Query Related Entities of an Entity" (TOC §6.5.2). So an entity has both a containment parent and related-entity links.
- In AUTOSAR's mapping, the Diagnostic Manager is an SOVD *component*, and each Diagnostic Server Instance is an SOVD *sub-component* ([AUTOSAR AP EXP SOVD R24-11 §2.2](https://www.autosar.org/fileadmin/standards/R24-11/AP/AUTOSAR_AP_EXP_SOVD.pdf)).

**Resource collections per entity** (TOC §6.6–6.14):
- **faults**: read all, read details, delete all, delete one
- **data**: categories and groups, read one or many, data-lists, write
  - the release deck names the data categories `currentData`, `identData`, `storedData` and `sysInfo`
- **configurations**: as bulk data or as parameters
- **operations**: start, list executions, status, stop; supports sync and async
- **modes**: maps UDS session control, security access, communication control and DTC settings
- **locks**: acquire, list, modify expiry, release
- **updates**, **bulk-data** and **logs**: RFC 5424 syslog or AUTOSAR DLT contexts

**Fault fields.** AUTOSAR R24-11 states these normatively, deriving each from the UDS DTC ([AUTOSAR AP SWS Diagnostics R24-11 §7.3.3.5.4](https://www.autosar.org/fileadmin/standards/R24-11/AP/AUTOSAR_AP_SWS_Diagnostics.pdf), SWS_DM_01567 / 01414 / 01413 / 01561):

| SOVD fault attribute | Derived from (AUTOSAR) |
|---|---|
| `code` | `udsDtcValue` of the DTC |
| `scope` | which fault memory (`DiagnosticMemoryDestination` shortName); read/delete without `scope` targets the primary memory |
| `display_code` | DTC shortName |
| `fault_name` | DTC longName |
| `severity` | DTC severity |
| `status` | "a JSON object literal with entries according to the DTC Status Mask" of ISO 14229-1 |
| `symptom` | optional string (DRAFT in R24-11) |
| `environment_data` | object with `extended_data_records` and `snapshots[]`; each snapshot has `name` (configured name, or `first_occurence` / `last_occurence` / `occurence_{n}`, spelled that way in the spec), `record_number` and `data{<element>: …}` |

The open implementation is [OpenSOVD fault-lib `SovdFault`](https://github.com/eclipse-opensovd/fault-lib/blob/main/src/dfm_lib/src/sovd_fault_manager.rs) (Apache-2.0, 2026). It has:
- the core fields `code`, `display_code`, `scope`, `fault_name`, `fault_translation_id`, `severity: u32`, `status: HashMap<String,String>`, `symptom` and `symptom_translation_id`;
- additional fields `typed_status` (the eight UDS bits as `Option<bool>` plus `mask`), `occurrence_counter`, `aging_counter`, `healing_counter`, `first_occurrence` and `last_occurrence` (ISO 8601).
- Note that its `status` is string key/value pairs, the same shape as ROS `values`, and it then added a typed alternative.

**What SOVD kept vs replaced from UDS:**
- **Kept** ([AUTOSAR EXP SOVD §3.1](https://www.autosar.org/fileadmin/standards/R24-11/AP/AUTOSAR_AP_EXP_SOVD.pdf)):
  - the DTC model: "SOVD fault is derived from DiagnosticTroubleCodeUDS";
  - DIDs → data and configuration;
  - routines → operations;
  - UDS 0x28 CommunicationControl and 0x85 ControlDTCSettings → standardized modes;
  - the UDS security-role model, reused for OAuth authorization.
- **Replaced:**
  - Binary encoding interpreted on the client through ODX files becomes a "Self-explaining protocol" in JSON with online capability descriptions (OpenAPI).
  - The ECU-centric static addressing becomes a discoverable entity tree. ASAM: "UDS is a static approach to diagnostics" ([ASAM page](https://www.asam.net/standards/detail/sovd/)).
  - AUTOSAR names the cost: the diagnostic manager must now interpret the bytes itself (compuMethods). It calls this "a big contradiction to the UDS (ISO 14229-1) solution", where the server just passed bytes through.
- **Added:** logs, bulk data, software update, locking, and dynamic discovery. ASAM's stated intent is "not to replace … the UDS protocol but to coexists with UDS". AUTOSAR lists "UDS as a subset" as a key property.

**Lesson for us.** SOVD treats a fault as more than a status value. It is a separate resource collection with a stable `code`, a key/value `status` lifecycle, and `environment_data` captured at the fault. **Locks** and **modes** are first-class resources, which is the standardized form of io-diagnostics' "whoever owns the port does the talking" rule.

---

## 3. AUTOSAR DEM + UDS ISO 14229-1 DTCs

Sources:
- [AUTOSAR CP SWS Diagnostic Event Manager R24-11](https://www.autosar.org/fileadmin/standards/R24-11/CP/AUTOSAR_CP_SWS_DiagnosticEventManager.pdf) (2024-11-27), which implements the UDS status byte "according to the definition in ISO-14229-1".
- The ISO 14229-1 text itself is paywalled.

### 3.1 DTC status byte (`Dem_UdsStatusByteType`, SWS_Dem_00928)

| Bit | Mask | Name | Set by / cleared by (DEM §7.7.1.3) | Stored |
|---|---|---|---|---|
| 0 | 0x01 | `testFailed` | Set by a qualified FAILED. Cleared by PASSED, reset, or clear. | config: volatile or NV |
| 1 | 0x02 | `testFailedThisOperationCycle` | Set by FAILED. Cleared at restart of the operation cycle, or clear. | NV |
| 2 | 0x04 | `pendingDTC` | Set by FAILED. Cleared when a cycle completes tested with no failure, or on clear or overwrite. | NV |
| 3 | 0x08 | `confirmedDTC` | Set by FAILED once the failure counter reaches `DemEventConfirmationThreshold`. Cleared by aging, overwrite, or clear. | NV |
| 4 | 0x10 | `testNotCompletedSinceLastClear` | 1 after a clear. Cleared by the first PASSED or FAILED. | NV |
| 5 | 0x20 | `testFailedSinceLastClear` | Set by FAILED. Cleared by clear (and optionally by aging). | NV |
| 6 | 0x40 | `testNotCompletedThisOperationCycle` | 1 at cycle start. Cleared by the first PASSED or FAILED. | NV |
| 7 | 0x80 | `warningIndicatorRequested` | Set by FAILED ∧ warning-indicator-on criteria. Cleared by healing (indicator-off criteria) or clear. | may be computed |

- After a clear, the byte is **0x50**: only the two "not completed" bits are set (SWS_Dem_00385). "Never tested" is explicit, never confused with "passed".
- `DemEventConfirmationThreshold` values (DEM Table 7.1):
  - 1 = "immediate confirmation", typical for non-OBD DTCs;
  - 2 = confirm in the next operation cycle (US OBD);
  - 3 = confirm in the second cycle after (EU OBD).
- The monitor's own status is just bit 0 + bit 6 (`Dem_MonitorStatusType`). The other six bits are the *stored* view.

### 3.2 Debounce, operation cycles, aging, healing, freeze frames

**Monitor reports** (`Dem_EventStatusType`):
- `PASSED` and `FAILED` (qualified).
- `PREPASSED` and `PREFAILED` (non-qualified; these feed the DEM's debouncer).
- `FDC_THRESHOLD_REACHED`: triggers freeze-frame / extended-data storage *before* confirmation.

**Counter-based debounce** (SWS_Dem_00414–00420):
- Each event has an internal counter. It steps up on PREFAILED and down on PREPASSED, clamped at `DemDebounceCounterFailedThreshold` / `PassedThreshold`.
- FAILED jumps the counter straight to the failed threshold.
- The counter is exposed externally as the UDS **fault detection counter**, −128..+127.

**Time-based debounce** (SWS_Dem_00426–00431):
- A timer starts on PREFAILED. A repeated PREFAILED does not restart it, and it qualifies FAILED after `DemDebounceTimeFailedThreshold`.
- The same −128..+127 mapping applies.

**Operation cycles** (§7.6): examples are ignition, power-up/down, the OBD driving cycle, warm-up, and accumulated operating time. Cycles are what confirmation, aging and "this operation cycle" bits count.

**Aging** (§7.7.8): removing a no-longer-failing event from memory after N cycles ("aging" or "unlearning").

**Healing** (§7.7.9): turning the warning indicator off after N passed cycles. Healing is about the indicator; aging is about memory.

**Freeze frames / snapshot records and extended data** (§7.7.7, glossary):
- A freeze frame = a list of DIDs captured at a configured trigger (first/last occurrence, etc.); the spec says freeze frames are the same as ISO's snapshot records.
- An extended data record holds statistical data per DTC (occurrence counter, aging counter, …).

**Component dependencies** (§7.5, §7.3.6):
- Events attach to `DemComponent`s in a DAG.
- A FAILED with a FAILED parent (or a higher-priority sibling) is a **CONSECUTIVE FAULT**. Otherwise it is a **CAUSAL FAULT**, optionally "preliminary-causal" for `DemCausalityDelayTime`.
- Consecutive faults "shall not be stored to the failure memory" (SWS_Dem_01126–01130).

**Severity** is not "how bad", it is **when to act** (UDS severity bits in DEM): `MAINTENANCE_ONLY` 0x20, `CHECK_AT_NEXT_HALT` 0x40, `CHECK_IMMEDIATELY` 0x80. WWH-OBD adds classes A/B1/B2/C.

**Safety boundary** (DEM §7.7.1.2, citing ISO 14229-1): "monitor, event or DTC status cannot be used to control safety critical functionality".

### 3.3 Why automotive separated "current test result" from "stored fault state"

This is synthesized from the sources above; it is the design rationale the bit semantics encode.

- **Monitors run intermittently.** Many tests only execute under enabling conditions (warm engine, a given speed). "Not tested" must therefore be distinct from "passed" (bits 4 and 6; 0x50 after a clear).
- **Intermittent faults vanish before anyone looks.** The tester, a human at a service bay, arrives after the event. So the record keeps `testFailedSinceLastClear`, `confirmedDTC` and the freeze frame, which survive after `testFailed` returns to 0.
- **False alarms are expensive.** A warning light, a tow, or a legislated emissions-light trigger costs money. So confirmation requires repeated detection over cycles: DEM glossary, "repeated detection of qualified events over cycles or time". The thresholds are legislated per region (Table 7.1).
- **Memory is finite and history must expire.** Hence aging and displacement, with counters as extended data.
- **The tester and the monitor are different actors with different clocks.** The monitor reports results at its own rate. The tester reads a *summary state* at an arbitrary later time. The status byte is that summary.

**Lesson for us.** One record that says only "ERROR now" cannot answer the bench questions:
- "did CAN 15 fault while I wasn't looking?"
- "has the D405 dropout check even run since replug?"

Two to four of the UDS bits plus a snapshot answer them. All eight are not needed.

---

## 4. DMTF Redfish (hardware health with rollup)

Sources:
- the schema [Resource.json](https://redfish.dmtf.org/schemas/v1/Resource.json) (latest Resource v1_25_0, release 2026.2)
- [Sensor.v1_14_0](https://redfish.dmtf.org/schemas/v1/Sensor.v1_14_0.json) (release 2026.2)
- the spec [DSP0266 1.23.0](https://www.dmtf.org/sites/default/files/standards/documents/DSP0266_1.23.0.pdf) (2025-09-05)

### 4.1 `Resource.Status` (on every resource)

| Field | Values / type | Definition |
|---|---|---|
| `Health` | `OK` \| `Warning` \| `Critical` \| null | "The health state of this resource in the absence of its dependent resources." OK = "Normal."; Warning = "A condition requires attention."; Critical = "A critical condition requires immediate attention." |
| `HealthRollup` | same enum | "the health state of the resource and its dependent resources" |
| `State` | `Enabled`, `Disabled`, `StandbyOffline`, `StandbySpare`, `InTest`, `Starting`, `Absent`, `UnavailableOffline`, `Deferring`, `Quiesced`, `Updating`, `Degraded` (v1_19, 2024.1); `Qualified` deprecated | lifecycle/availability, orthogonal to Health |
| `Conditions[]` | `Condition` (added v1_11_0, 2020.4) | "Conditions in this resource that require attention." They may affect Health or HealthRollup. Conditions from children may be rolled up into the parent via the `ConditionInRelatedResource` message. |

### 4.2 `Condition`

`MessageId` is the only required field.

| Field | Definition (short) |
|---|---|
| `MessageId` | registry key, format `<MessageRegistryPrefix>.<MajorVersion>.<MinorVersion>.<MessageKey>`, with a PascalCase key "shall not include spaces, periods, or special characters" (DSP0266 §9.5.11.2) |
| `MessageArgs[]` | strings substituted into the registry's message template |
| `Message` | the human-readable rendering |
| `Severity` | `Health` enum; the service may override the registry's default |
| `Timestamp` | "The time the condition occurred." |
| `OriginOfCondition` | link to the resource that caused it; absent if the cause is this resource |
| `LogEntry` | link to the log entry created for the condition |
| `Resolution` (2021.4), `ResolutionSteps[]` (2023.3) | how to fix it |
| `ConditionType` (2025.2) | `Alert` (requires correction, e.g. a fault), `Informational` (needs attention or maintenance, e.g. a reset to activate new firmware), `Subsystem` (the health of a subsystem) |
| `VendorCode` (2026.2) | a vendor/legacy code equivalent to the MessageId |

The spec's rationale for registries is that they make translation easy and keep large strings out of the implementation (DSP0266 §9.5.11.2).

### 4.3 `Sensor` (typed reading with thresholds)

| Field | Definition |
|---|---|
| `Reading` | the sensor value |
| `ReadingUnits` | "in UCUM c/s format". These units apply to the reading, thresholds and related properties |
| `ReadingTime` | when the reading was acquired; it "does not represent the time at which the resource was accessed" (v1_1_0) |
| `SensingInterval` | the time interval between readings (v1_1_0) |
| `ReadingRangeMin` / `ReadingRangeMax` | the valid range; values outside it "are discarded as reading errors" |
| `Accuracy` | ± percent error |
| `PhysicalContext` | the area or device the measurement applies to |
| `Thresholds` | `LowerCaution`, `LowerCritical`, `LowerFatal`, `UpperCaution`, `UpperCritical`, `UpperFatal`, plus `*User` variants (user-set when the service value is read-only) |
| `Threshold.Reading` | the threshold value, in the same units as `Reading` |
| `Threshold.Activation` | `Increasing` \| `Decreasing` \| `Either` \| `Disabled` |
| `Threshold.DwellTime` | how long the value must violate before activation (ISO 8601 duration) |
| `Threshold.HysteresisReading` (2023.1) | the offset to clear: −2 means the reading must fall 2 units below an upper threshold |
| `Threshold.HysteresisDuration` (2023.1) | how long the value must not violate before deactivation. Both hysteresis conditions must be met. |

### 4.4 Absent resources (DSP0266 §9.11.2)

- A removable or optional slot that is empty still exists, with `State: Absent`. This lets a client see the capability, e.g. an empty DIMM socket.
- Properties unknowable while absent are not returned, and child resources are not populated.
- Clients must assume a re-populated resource may be a different physical item.

### 4.5 Hard-won lesson: OpenBMC removed HealthRollup (2024-04-02)

The [openbmc/docs commit 44f3de9](https://github.com/openbmc/docs/commit/44f3de929bbee0dbacd024b9724fe72817fd1f48) (Gunnar Mills) removed the rollup design. Its implementation had been "only half done" and unowned. The commit warns that health rollup "is nontrivial to get correct, performance problems are a concern". It adds that "Redfish probably needs better guidance on how health rollup needs to work functionally". The schema still defines the field but gives no algorithm.

**Lesson for us.**
- Keep raw health and rollup as separate fields, and compute the rollup in the host, never in a leaf driver.
- Write the rollup rule down (worst-of, with explicit STALE/unknown handling).
- Note that Redfish already carries the full sensor contract we want: units in UCUM, thresholds in the same units, dwell, hysteresis, `ReadingTime` ≠ access time, and `SensingInterval`.

---

## 5. IPMI SEL / sensor thresholds, SNMP Entity MIBs

### 5.1 IPMI v2.0 rev 1.1

Source: [IPMI v2.0 rev 1.1 PDF](https://www.intel.com/content/dam/www/public/us/en/documents/product-briefs/ipmi-second-gen-interface-spec-v2-rev1-1.pdf) (Intel et al., 2013-10-01).

**Thresholds** come in six: lower/upper × non-critical / critical / non-recoverable. In the spec's health-summary vocabulary:
- `nc` = "‘warning’: hardware outside normal operating range"
- `cr` = "‘fatal’: hardware exceeding specified ratings"
- `nr` = "‘potential damage’"
- `uf` = unspecified fault
- `??` = unknown

**Hysteresis** (§35.6, §35.13.2–3):
- Separate positive-going and negative-going hysteresis values per sensor.
- Example: an upper non-recoverable threshold of 98h with hysteresis 2 asserts at 98h and does not clear until 95h.
- **Re-arm**: an auto re-arm sensor reports its present status. A manual re-arm sensor *latches* its status ("sometime referred to as the ‘Event History’") until explicitly re-armed. This is the same present-vs-history split as UDS bits 0 vs 5.

**Reading/state unavailable** (§16.4): a bit telling software it "must ignore the reading and/or state information". Examples: a hot-swap supply that isn't installed, or a sensor that is re-arming. Validity is a field, not a magic value.

**SEL event record** (§32.1, 16 bytes):
- `Record ID`, `Record Type`, `Timestamp`
- `Generator ID` (who raised it)
- `EvM Rev`, `Sensor Type`, `Sensor #`
- `Event Dir | Event Type` (assertion vs deassertion; threshold vs discrete)
- `Event Data 1–3`

**Entity Association records** (§1.7.13): they group entities, e.g. power supplies into a redundant unit. Without them, ‘redundancy lost’ and ‘power supply failed’ "would be disjoint events".

### 5.2 SNMP Entity MIBs

**[RFC 6933 Entity MIB v4](https://www.rfc-editor.org/rfc/rfc6933)** (2013-05):
- Each `entPhysicalEntry` has `Descr`, `VendorType`, **`ContainedIn`** (the parent index; the containment relationships "define a strict hierarchy"), `Class`, `ParentRelPos`, `Name`, `HardwareRev`, `FirmwareRev`, `SoftwareRev`, `SerialNum`, `MfgName`, `ModelName`, `Alias`, `AssetID`, `IsFRU`, `MfgDate`, `Uris` and `UUID`.
- `Class` values: `other`, `unknown`, `chassis`, `backplane`, **`container`** (a slot, modelled even when empty), `powerSupply`, `fan`, `sensor`, `module`, `port`, `stack` and `cpu`.

**[RFC 3433 Entity Sensor MIB](https://www.rfc-editor.org/rfc/rfc3433)** (2002-12):
- `entPhySensorType`, `Scale` (an exponent), `Precision` (decimal places), `Value`, **`OperStatus`**, `UnitsDisplay`, **`ValueTimeStamp`** and **`ValueUpdateRate`** (in ms; 0 means on-demand, event-driven, or unknown).
- `OperStatus` is `ok`, `unavailable` (can't obtain the value now) or `nonoperational` ("the agent believes the sensor is broken"; a disconnected wire, or jittery or out-of-range readings).

**[RFC 4268 Entity State MIB](https://www.rfc-editor.org/rfc/rfc4268)** (2005-11), from ITU X.731:
- `AdminState` (`locked` / `shuttingDown` / `unlocked`)
- `OperState` (`disabled` / `enabled` / `testing`)
- `UsageState` (`idle` / `active` / `busy`)
- `AlarmStatus` (bits, including warning, indeterminate and underRepair)
- `StandbyStatus`
- Every state also has `unknown`.
- §2.1 names the two kinds of state: **"raw state"** (the entity alone) and **"computed state"** (influenced by parents and children). "All state objects in this memo are raw state."
- The Interfaces MIB `ifOperStatus` has `lowerLayerDown`, i.e. "down because what I depend on is down".

**Lesson for us.**
- Containment is a strict tree, and an empty port or slot is an entity.
- Sensor validity has two failure modes: *can't read now* vs *sensor is lying*.
- A per-sensor update rate is the 2002 version of `expected_period_s`.
- "Down because the parent is down" deserves its own value.

---

## 6. Nagios / Monitoring Plugins (Icinga uses the same contract)

Sources: [Monitoring Plugins Development Guidelines](https://www.monitoring-plugins.org/doc/guidelines.html) (© 2000–2024), and the Nagios Core 4 docs for [state types](https://assets.nagios.com/downloads/nagioscore/docs/nagioscore/4/en/statetypes.html), [freshness](https://assets.nagios.com/downloads/nagioscore/docs/nagioscore/4/en/freshness.html), [flapping](https://assets.nagios.com/downloads/nagioscore/docs/nagioscore/4/en/flapping.html) and [network reachability](https://assets.nagios.com/downloads/nagioscore/docs/nagioscore/4/en/networkreachability.html).

**Exit codes** (§2.4):

| Code | State | Meaning |
|---|---|---|
| 0 | OK | the service was checked and appeared to function properly |
| 1 | WARNING | above a warning threshold, or not working properly |
| 2 | CRITICAL | not running, or above a critical threshold |
| 3 | UNKNOWN | bad arguments, or low-level failure *internal to the plugin* (can't fork, can't open a socket) |

Higher-level errors such as timeouts and DNS failures "should generally NOT be reported as UNKNOWN".

**Perfdata** (§2.6): `'label'=value[UOM];[warn];[crit];[min];[max]`
- UOM is none (a count), `s`/`us`/`ms`, `%`, `B`/`KB`/`MB`/`TB`, or `c` (a continuous counter).
- warn and crit use the range syntax `[@]start:end`, with `~` for −∞. Alert when outside the range; `@` means alert when inside it.
- value may be a literal "U": the value couldn't be determined.
- All of warn, crit, min and max must share the value's UOM.

**SOFT vs HARD states:** a non-OK result is SOFT until it has been rechecked `max_check_attempts` times, then it becomes HARD. Notifications fire only on HARD. A recovery is soft or hard to match.

**Freshness:** for passively submitted results, the age of the last result is compared with `freshness_threshold`. If it is older, the result "is considered "stale"" and an active check is forced. The docs recommend setting the threshold explicitly.

**Flapping:** the last 21 results are kept and a weighted percent-state-change is computed (newest weighted 50% more). It is compared against high and low thresholds, which is hysteresis on the alerting itself.

**DOWN vs UNREACHABLE:** with `parents` configured along the network path, hosts behind a failed router are UNREACHABLE, not DOWN. This is the root-cause reachability logic.

**Lesson for us.**
- An inline typed-value grammar with thresholds has survived 25 years.
- UNKNOWN means "the *check* broke", which is different from STALE ("no fresh report").
- Debounce (SOFT→HARD) and flap detection belong to the consumer; freshness needs a declared threshold.
- "Unreachable because the parent is down" stops one dead fdcanusb from showing as N dead servos.

---

## 7. Kubernetes `conditions`

Sources: [API conventions, "Typical status properties"](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md#typical-status-properties); [KEP-1623 Standardize Conditions](https://github.com/kubernetes/enhancements/tree/master/keps/sig-api-machinery/1623-standardize-conditions) (created 2020-03-23, implemented in 1.19); [KEP-589 Efficient Node Heartbeats](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/589-efficient-node-heartbeats) (created 2018-04-27).

### 7.1 `metav1.Condition`

The list is a map keyed by `type`.

| Field | Req. | Definition |
|---|---|---|
| `type` | yes | CamelCase, or `foo.example.com/CamelCase` |
| `status` | yes | `True` \| `False` \| `Unknown` |
| `observedGeneration` | no | the `.metadata.generation` the condition was computed from; a lower number means the condition is out of date with respect to the current spec |
| `lastTransitionTime` | yes | when the status last changed |
| `reason` | yes, non-empty | a CamelCase programmatic identifier for the last transition |
| `message` | yes, may be "" | human-readable details |

### 7.2 Documented lessons

- **Phase → conditions.** "Phase was essentially a state-machine enumeration field"; adding enum values breaks clients. Conditions expose the individual properties instead, and some types keep a uniform meaning across all resources.
- **Observations, not machines.** "conditions are observations and not, themselves, state machines". The system is level-based and assumes an Open World.
- **Absent = Unknown.** "the absence of a condition should be interpreted the same as `Unknown`". Controllers should publish a condition as `Unknown` on first visit.
- **Polarity.** Some conditions are normal-true (`Ready`), others normal-false (`MemoryExhausted`). Without knowing each one, "it is not possible to compute a generic summary". The convention prefers one top-level summary condition (`Ready`/`Succeeded`) for simple consumers.
- **Naming.** Type names describe the current state as an adjective or past participle (`Ready`, `OutOfDisk`, `Succeeded`), not a transition verb (`Deploying`). Long transitions may be their own condition, e.g. `Resizing`.
- **Reason vs message.** `reason` is a one-word category for summaries and one-line output; `message` is for detailed views.
- **Required fields** (KEP-1623 "Noteworthy choices"):
  - `lastTransitionTime` is required, so consumers don't guess.
  - `reason` is required, "even if that value is 'unknown unknowns'".
  - **`lastHeartbeatTime` was removed: it "caused excessive write loads as we scaled".**
- **Liveness split from status** (KEP-589): heartbeats move to a tiny `Lease` (`holderIdentity`, `leaseDurationSeconds`, `acquireTime`, `renewTime`, `leaseTransitions`). The full NodeStatus is sent only when it changes meaningfully or every `node-status-update-period`. A node with no update for the grace period (default 40 s) goes NotReady.
- **Big or fast data elsewhere.** Large or rapidly changing status, such as resource usage, belongs in separate objects.

**Lesson for us.**
- A machine `reason` must be present on every status.
- `since` (last transition) is more useful than a per-message heartbeat stamp.
- `observedGeneration` is how a record says which config it was judged against. That is the rack's config interlock / `config_history` idea.
- Liveness (STALE) should come from a cheap separate signal, or a declared period, not from re-sending the whole record.
- A normalized severity level, as ROS has, is what makes generic rollup possible; mixed-polarity conditions can't be rolled up generically.

---

## 8. IETF "Health Check Response Format for HTTP APIs"

Source: [draft-inadarei-api-health-check-06](https://datatracker.ietf.org/doc/html/draft-inadarei-api-health-check-06). It is dated 2021-10-16, expired 2022-04-19, and never became an RFC (datatracker shows rev 06 as the last). Media type `application/health+json`.

**Top level:**
- `status` (required): `pass` (aliases `ok`, `up`), `fail` (`error`, `down`), or `warn` ("healthy, with some concerns"). It is tied to the HTTP code: pass/warn → 2xx–3xx, fail → 4xx–5xx.
- Optional: `version`, `releaseId`, `notes[]`, `output` (raw error text; omit on pass), `checks`, `links`, `serviceId`, `description`.

**`checks`:**
- An object keyed `"{componentName}:{measurementName}"`; colons are forbidden inside each part.
- Predefined measurements: `utilization`, `responseTime`, `connections`, `uptime`.
- Each value is an **array**, one element per node of that sub-component.

**Fields of each check element:**

| Field | Definition |
|---|---|
| `componentId` | unique id of the sub-component instance |
| `componentType` | `component` \| `datastore` \| `system` \| a well-known term \| a URI |
| `observedValue` | any JSON value |
| `observedUnit` | "SHOULD be present if observedValue is present" |
| `status` | the same pass/fail/warn, per component. The draft's §4.5 text says it has the same meaning as the top-level "output", an apparent copy-paste slip |
| `affectedEndpoints` | URI templates hurt by this check; omit on pass |
| `time` | "the date-time, in ISO8601 format, at which the reading of the observedValue was recorded". The draft assumes cached, non-real-time readings |
| `output`, `links` | same as top level |
| (additional keys) | allowed; consumers may ignore them |

**Lesson for us.**
- Its strongest idea is that a check can have a *value + unit + time*, keyed by `component:measurement`.
- Its weaknesses are no stable reason code, no thresholds, no expected period, and no parent links beyond one level.
- Treat it as an export format for HTTP, not as the core contract.

---

## 9. OpenTelemetry (identity + typed metrics + severity scale)

All of this is **Development** status in Oct 2026, so treat the conventions as vocabulary, not as stable contracts.

**Hardware semconv** ([hardware/common.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/hardware/common.md), [registry](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/registry/attributes/hardware.md)):
- **Metrics:** `hw.energy` (Counter, J), `hw.power` (Gauge, W), `hw.errors` (Counter, `{error}`), and `hw.status` (UpDownCounter: 1 or 0 *for each possible state*; intended to become an OpenMetrics **StateSet**).
- **Attributes:**
  - `hw.id` (required; unique on the host)
  - `hw.type` (required): `battery`, `cpu`, `disk_controller`, `enclosure`, `fan`, `gpu`, `logical_disk`, `memory`, `network`, `physical_disk`, `power_supply`, `tape_drive`, `temperature`, `voltage`
  - `hw.name`
  - **`hw.parent`**: "typically the `hw.id` attribute of the enclosure, or disk controller"
  - `hw.state`: `ok`, `degraded`, `failed`, `needs_cleaning`, `predicted_failure`
  - `hw.vendor`, `hw.model`, `hw.serial_number`, `hw.firmware_version`, `hw.driver_version`, `hw.sensor_location`
- **Limits are their own series:** e.g. `hw.temperature.limit` with `hw.limit_type` = `low.degraded` / `low.critical` / `high.degraded` / `high.critical` / `degraded` / `critical` / `max` / `throttled` / `turbo`.

**Entity data model** ([entities/data-model.md](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/entities/data-model.md)):
- An entity is `Type` (immutable) + `ID` (identifying attributes; immutable, at least one) + `Description` (descriptive attributes; may change; not part of the identity).
- **Minimally sufficient identity:** no extra attributes in the ID.
- **Repeatable identity:** two independent observers "MUST be able to supply identical values for all identifying attributes". An observer that cannot must not emit that entity type; it delegates to the observer that can, or uses a different type.

**Logs data model** ([logs/data-model.md](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/logs/data-model.md)):
- `Timestamp` (source clock, optional) vs **`ObservedTimestamp`** (collector clock).
- `SeverityNumber` ranges: 1–4 TRACE, 5–8 DEBUG, 9–12 INFO, 13–16 WARN, 17–20 ERROR, 21–24 FATAL; 0 = unspecified; ≥17 means "erroneous". Also `SeverityText` (original), `EventName`, `Resource`, `Attributes`, and `Body`.

**Lesson for us.**
- Identity attributes must be ones that *both* the OS observer and the driver observer can produce identically. This is exactly the io-diagnostics trap where the USB descriptor serial ≠ the RealSense SDK serial. Pick the one both can read, or make one of them the source of truth.
- Keep two timestamps.
- Thresholds/limits are data published beside the value.
- A fine-grained severity scale maps cleanly onto 3–4 health levels for export.

---

## 10. Home Assistant (device/entity identity, diagnostics category, repairs, updates)

Sources: developer docs from the upstream repo ([device registry](https://developers.home-assistant.io/docs/device_registry_index/), [entity](https://developers.home-assistant.io/docs/core/entity/), [entity-unavailable rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unavailable/), [log-when-unavailable rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/log-when-unavailable/), [repairs](https://developers.home-assistant.io/docs/core/platform/repairs/), [update entity](https://developers.home-assistant.io/docs/core/entity/update/)). The device registry doc was last changed 2026-08-27; child devices were documented 2026-08-24.

**Device fields:**
- **`identifiers`**: a set of `(DOMAIN, identifier)` tuples that "identify the device in the outside world", e.g. a serial number.
- **`connections`**: a set of `(connection_type, id)`, e.g. a MAC.
- `manufacturer`, `model`, `model_id`, `serial_number` (need not be unique), `sw_version` (firmware), `hw_version`, `name`, `name_by_user`, `configuration_url`, `entry_type` (`service`), `disabled_by`, `area_id`, `labels`, `created_at`, `modified_at`.
- **`via_device_id`**: the device that routes messages to HA, e.g. a hub.
- **`parent_device_id`** (new): a child device.

**Two parent edges** (the Aug 2026 change):
- "`via_device_id` describes *connectivity* … whereas child devices describe *composition*".
- A child device (one outlet of a power strip) has no `connections`, `via_device_id`, manufacturer, model, firmware or serial of its own; those belong to the physical parent.
- Children are one level only. Delete/disable cascade, the area is inherited, and targeting the parent targets the children.
- The docs warn the design "is still being finalized".

**Entity fields:**
- `unique_id` (stable, not user-changeable)
- `available` (bool)
- `has_entity_name`
- `translation_key`
- **`entity_category`**: `CONFIG` (changes configuration) or **`DIAGNOSTIC`** (exposes configuration or diagnostics without changing it, e.g. RSSI or MAC; also identify buttons)

**Unavailable vs unknown:**
- Can't fetch from the device → `unavailable`.
- Fetched, but a few pieces of data are missing → `unknown`.

**Log once:** "log once when unavailable and once when back connected", at `info` level.

**Repairs** (the issue registry): `domain`, `issue_id` (unique per domain), `translation_key` + `translation_placeholders`, `severity` (`CRITICAL` "only used for true panic" / `ERROR` broken now / `WARNING` breaks in the future), `is_fixable`, `is_persistent`, `learn_more_url`, `breaks_in_ha_version`, `data`. A fix happens through a `RepairsFlow`; a fixed issue is removed.

**Update entity:** `installed_version`, `latest_version`, `in_progress`, `update_percentage`, `release_summary` (≤255 chars), `release_url`, `title`, `auto_update`; features `INSTALL`, `SPECIFIC_VERSION`, `PROGRESS`, `BACKUP`, `RELEASE_NOTES`.

**Lesson for us.** The converged design used `via_device` as "the parent". HA now says there are **two** edges:
- **Connectivity (`via`)** covers USB hub → fdcanusb → CAN bus → node. It is the "which port / which wire" tree and the root-cause path.
- **Composition (child)** covers moteus node → motor/encoder/driver, and D405 → colour/depth streams. A child carries no identity of its own.

The path tree should be the connectivity tree; composition children hang off a node without identity fields. Also adopt:
- a **diagnostic vs config** category for values;
- **unavailable vs unknown**;
- the **repairs** idea: a persistent, translatable, optionally fixable issue, distinct from the momentary status.

---

## 11. Synthesis: concept → who has it → recommended field

The recommended names extend the ROS record without breaking it (`level`, `name`, `message`, `hardware_id`, `values` stay), and match `docs/converged-design-2026-10-22.md` (`path`, `expected_period_s`, `identity`).

| Concept | Standards that have it (field) | ROS today | Recommended field |
|---|---|---|---|
| **Stable machine code vs human message** | K8s `reason` (CamelCase, required) + `message`; Redfish `MessageId` (`Registry.Maj.Min.Key`) + `MessageArgs` + `Message`; SOVD `code` + `display_code` + `fault_name` + translation id; HA `issue_id` / `translation_key` + placeholders; OTel `EventName`; IPMI sensor/event type codes. Nagios and the IETF draft lack it (free text) | none (`message` is free text; `name` names the check) | **`code`**: stable PascalCase key, required when `level != OK` (e.g. `NoAck`, `ReEnumerated`, `MoteusFault`). **`message`**: rendered text. Optional **`args`**. |
| **Severity vs lifecycle state** | Redfish `Health` vs `State` (Enabled/Absent/Starting/Updating/StandbyOffline/UnavailableOffline/InTest…); RFC 4268 admin/oper/usage/alarm/standby; UDS severity (when to act) vs status bits; SOVD `severity` vs `status{}`; K8s `status` vs `reason`; Nagios state vs SOFT/HARD; HA `available` vs state | `level` only | keep **`level`** (OK/WARN/ERROR/STALE) as normalized severity; add **`state`** (`present` \| `absent` \| `starting` \| `updating` \| `disabled` \| `busy` \| `testing`) orthogonal to it |
| **"Can't tell" is not a severity** | K8s `Unknown` (absent = Unknown); Nagios UNKNOWN; HA `unavailable` vs `unknown`; IPMI reading/state-unavailable; Entity-Sensor `unavailable` vs `nonoperational`; UDS "not completed" bits; Redfish `null` Health | `STALE` covers only "no fresh message" | keep `STALE` (no fresh report). Add **`validity`** per value: `ok` \| `unavailable` \| `invalid` (sensor lying) \| `untested` |
| **Typed value + unit + thresholds** | Nagios `'label'=value[UOM];warn;crit;min;max`; Redfish `Reading`/`ReadingUnits` (UCUM)/`Thresholds.{Lower,Upper}{Caution,Critical,Fatal}`/`ReadingRangeMin/Max`/`Accuracy`; IPMI lnc/lc/lnr/unc/uc/unr; Entity-Sensor type/scale/precision; IETF `observedValue`/`observedUnit`; OTel metric unit + `hw.*.limit` | `values[{key,value}]`, strings | **`values[]: {key, value (typed), unit (UCUM), warn, crit, min, max, category: diagnostic\|config}`**; flatten to strings only at a ROS bridge |
| **Per-record timestamp + expected period / staleness** | Redfish `ReadingTime` + `SensingInterval`; Entity-Sensor `ValueTimeStamp` + `ValueUpdateRate`; OTel `Timestamp` + `ObservedTimestamp`; IETF `time`; K8s `lastTransitionTime` (required), `lastHeartbeatTime` removed → `Lease` (`renewTime`, `leaseDurationSeconds`); Nagios `freshness_threshold` | header stamp on the array, no period | **`stamp`** (source/measurement time), **`received`** (host clock), **`expected_period_s`** (producer-declared), **`since`** (last `level` transition). The consumer computes STALE as `now − received > k·expected_period_s` |
| **Identity + containment** | HA `identifiers`, `connections`, manufacturer/model/model_id/serial_number/sw_version/hw_version, `via_device_id` (connectivity), `parent_device_id` (composition); OTel entity `type` + minimal repeatable `ID` + `Description`, `hw.id`/`hw.parent`; Entity MIB `ContainedIn` (strict tree), `Class` incl. `container`, revs/serial/UUID; Redfish `Absent`; SOVD entity path + sub/related entities; IPMI Entity ID/Instance + Entity Association | `hardware_id` string | **`path`** (connectivity tree, root → leaf); **`entity_type`**; **`identity{identifiers[], manufacturer, model, serial_number, hw_version, sw_version}`** (only on physical devices; the identifier must be readable by both OS and driver); composition children carry **`part_of`** and no identity |
| **Rollup rules** | Redfish `Health` (self) vs `HealthRollup` (self + dependents) + `ConditionInRelatedResource`; RFC 4268 raw vs computed; IETF top-level status over `checks`; K8s top-level `Ready`; DEM component FAILED propagation, consecutive vs causal faults; Nagios UNREACHABLE; `ifOperStatus` `lowerLayerDown`; Redfish `OriginOfCondition`. OpenBMC dropped its rollup as too hard | none (aggregator config) | the driver emits raw `level` only; the host computes **`rollup_level`** = worst of self + children, with a declared rule for STALE/unknown children. A child whose parent link is down becomes **`unreachable`** (shown, not counted) with **`cause_path`** pointing at the causal entity |
| **Debounce / hysteresis** | DEM counter/time debounce (FDC −128..127, PREFAILED/PREPASSED), confirmation over operation cycles, aging, healing; Redfish `DwellTime` + `HysteresisReading` + `HysteresisDuration`; IPMI ± hysteresis, auto vs manual re-arm; Nagios `max_check_attempts` SOFT/HARD + flap detection (21 samples). K8s deliberately has none (level-based) | none | the producer debounces cheap high-rate checks (fault-lib: "debouncing should be in the fault lib to reduce the traffic"); thresholds carry **`hysteresis`** and **`dwell_s`**; the host computes **`flapping`** |
| **Current result vs stored fault** | UDS bit 0 vs bits 2/3/5 + NV storage; IPMI auto re-arm vs latched "history"; Nagios SOFT vs HARD; SOVD `faults` collection separate from data; HA repairs (persistent) vs entity state | none (status is momentary) | a separate **`faults[]`** list per entity: `{code, severity, active, confirmed, tested_this_session, first_seen, last_seen, count, snapshot}`, where "session" = the operation cycle (rack power-up or host session) |
| **Snapshot on fault** | DEM freeze frames (snapshot records) + extended data records, `FDC_THRESHOLD_REACHED`, pre-stored freeze frames; SOVD `environment_data{extended_data_records, snapshots[{name, record_number, data}]}`; IPMI SEL record; Redfish Condition → `LogEntry` | none | **`faults[].snapshot{first, last}`**: a copy of `values[]` at the transition (and at first/last occurrence), not continuous logging |
| **Config the status was judged against** | K8s `observedGeneration`; DEM enable/storage conditions; Redfish `*User` thresholds | none | **`config_generation`** (hash or UTC of the config read the check used) |
| **Severity semantics** | Redfish OK/Warning/Critical; IPMI ok/nc/cr/nr/uf/??; Nagios 0–3; IETF pass/warn/fail; HA repairs WARNING ("breaks in the future") / ERROR / CRITICAL; UDS = *when to act* (maintenance only / next halt / immediately); OTel 1–24 | OK/WARN/ERROR/STALE | keep 3 + STALE; optionally **`action`** on faults (`none` \| `maintenance` \| `next_stop` \| `now`), borrowed from UDS severity |

---

## 12. The lessons (most important first)

1. **Every record needs a stable machine reason, not just prose.**
   - K8s made `reason` required and non-empty in 2020. Redfish makes `MessageId` the only required Condition field. SOVD faults key on `code`. HA keys issues on `issue_id` + `translation_key`.
   - Without a code you can't count, dedupe, suppress, translate or test against reasons.
   - The prose (`message`) is a rendering of code + args.
2. **Separate "I can't tell" from "it's bad", and separate "no fresh report" from "the check itself broke".**
   - This distinction appears independently in K8s (absent = `Unknown`), Nagios (UNKNOWN = plugin failure, vs stale = freshness), HA (`unavailable` vs `unknown`), IPMI (reading/state unavailable), Entity-Sensor (`unavailable` vs `nonoperational`) and UDS (not-completed bits; 0x50 after clear).
   - Absence of a report must never render green.
3. **Current test result ≠ stored fault state.**
   - Automotive split them because monitors run intermittently, faults vanish before the tester arrives, false alarms are costly and must be confirmed over cycles, and history must age out.
   - IPMI's manual re-arm "history" and Nagios's SOFT/HARD are the same idea.
   - Debounce and confirmation live near the producer; aging and clearing live in the manager.
4. **Raw health and rollup are different fields, and rollup belongs to the consumer, with a written rule.**
   - Redfish (`Health` vs `HealthRollup`) and RFC 4268 ("raw" vs "computed" state) both separate them.
   - OpenBMC removed its rollup in 2024 as too hard and slow to get right, and asked DMTF for a functional definition.
   - Keep the rule trivial: worst-of, with explicit STALE handling.
5. **Suppress consequential faults along the dependency edge.**
   - DEM marks a failure under a FAILED parent as CONSECUTIVE and doesn't store it. Nagios shows hosts behind a dead router as UNREACHABLE, not DOWN. The Interfaces MIB has `lowerLayerDown`. Redfish points `OriginOfCondition` at the cause.
   - On the rack: a dead bus (fdcanusb 28B3A25D: adapter fine, no ACK) should be one causal fault plus N unreachable nodes, not N+1 errors.
6. **Values are typed, with units and thresholds in the same units, plus hysteresis/dwell; validity is a separate flag.**
   - Redfish `Sensor` (UCUM units, Caution/Critical/Fatal, `DwellTime`, `HysteresisReading`/`Duration`), IPMI thresholds with hysteresis, Nagios perfdata, and Entity-Sensor type/scale/precision/status all agree.
   - String key/value pairs are the lowest common denominator for transport, not for the contract. Even OpenSOVD started with `HashMap<String,String>` status and added a typed alternative.
7. **Identity is minimal, repeatable across observers, and separate from status; containment has two edge kinds.**
   - OTel: two observers must produce identical identifying attributes, or one delegates to the other.
   - HA (Aug 2026): connectivity (`via_device_id`) vs composition (child devices).
   - Entity MIB: a strict containment tree, and an empty `container` is still an entity (cf. Redfish `Absent`).
8. **Staleness needs a declared period and a source timestamp, and liveness should be cheap and separate.**
   - Entity-Sensor `ValueUpdateRate` (2002), Redfish `SensingInterval` + `ReadingTime` (≠ access time) and Nagios `freshness_threshold` all declare a period.
   - K8s removed `lastHeartbeatTime` from conditions for write load, moved liveness to a tiny `Lease`, and sends full status on change or a max period. Keep two clocks (OTel `Timestamp` vs `ObservedTimestamp`).

Also worth keeping:
- Diagnostic status must not drive safety (the ISO 14229 rule restated by DEM).
- Faults ≠ DTCs (OpenSOVD: "HPC faults are internal diagnostic signals; they are not themselves DTCs"); one exported code may synthesize several internal faults.
- A reporting library "shall never block an application" (OpenSOVD fault-lib).
- Lock/mode resources (SOVD) are the standard shape of the action plane's port ownership.

---

## 13. What this suggests for the io-diagnostics record (sketch for the orchestrator, not a decision)

Below is one rack example (values illustrative, not measured): CAN 15 faulting on a bus that is up. It has two entity levels and the four separated concerns. Field names follow §11. Everything beyond the ROS five is optional, so a plain ROS `DiagnosticStatus` still round-trips.

```yaml
- path: host/usb-1-5/fdcanusb-272813EF/can0/node-15   # connectivity tree (HA via_device)
  name: moteus node 15                                  # ROS name
  hardware_id: moteus:can0:15                           # ROS hardware_id = repeatable identifier
  entity_type: moteus_servo
  identity: {identifiers: [[moteus, "can0:15"]], manufacturer: mjbots, model: "…", sw_version: "…", hw_version: "…"}
  level: ERROR                  # raw, this entity only (Redfish Health / RFC 4268 raw state)
  state: present                # lifecycle, orthogonal (Redfish State)
  code: MoteusFault             # stable machine reason (K8s reason / Redfish MessageKey)
  message: "fault 33 on bridge enable"
  args: {fault: 33}
  stamp: 2026-10-21T22:14:03.120Z      # source time of the reading
  received: 2026-10-21T22:14:03.131Z   # host clock (OTel ObservedTimestamp)
  expected_period_s: 1.0               # STALE when received is older than k * period
  since: 2026-10-21T22:13:58.002Z      # last level transition (K8s lastTransitionTime)
  config_generation: "conf@2026-10-21T21:40Z"   # K8s observedGeneration
  values:
    - {key: bus_voltage, value: 37.2, unit: V, warn: "34:", crit: "30:", category: diagnostic}
    - {key: fault, value: 33, unit: "1", category: diagnostic}
  faults:
    - code: MoteusFault.33
      severity: ERROR
      action: next_stop          # UDS severity semantics: when to act
      active: true               # UDS testFailed
      confirmed: true            # UDS confirmedDTC (after debounce)
      tested_this_session: true  # not UDS testNotCompletedThisOperationCycle
      first_seen: 2026-10-21T22:13:58.002Z
      last_seen: 2026-10-21T22:14:03.120Z
      count: 3
      snapshot: {first: {bus_voltage: 36.9, mode: 11, position: 0.12}}
- path: host/usb-1-5/fdcanusb-272813EF/can0/node-15/motor   # composition child: no identity
  part_of: host/usb-1-5/fdcanusb-272813EF/can0/node-15
  level: OK
```

The host adds `rollup_level` on every parent. Under a no-ACK bus, it marks children `unreachable` with `cause_path` pointing at the bus entity.

---

## 14. Gaps and what I could not verify

- **ISO 17978 / ASAM SOVD body text** (paywalled or members-only):
  - The exact JSON schema of the SOVD fault (types, the meaning of `severity` values, the full `status` key set) is not verified against the standard.
  - The field list comes from AUTOSAR R24-11's normative mapping plus the OpenSOVD Apache-2.0 code. OpenSOVD types `severity` as `u32`; UDS/DEM severity is a bitmask of action urgency. Treat SOVD severity semantics as **unconfirmed**.
- **ISO 14229-1 text** (paywalled): bit names and transitions are taken from AUTOSAR DEM R24-11, which implements them by reference.
- **SOVD entity-type definitions** (what exactly distinguishes a component, an app and a function): only the ASAM TOC and the release deck were free. AUTOSAR maps ECU-like servers to components and sub-components.
- **OTel `hw.*` and the Entity data model** are Development status. **HA child devices** are flagged by HA as still being finalized.
- **The IETF health-check draft** expired in 2022 and was never standardized. Its §4.5 per-check `status` text contains a copy-paste slip (it says "output").

---

## Source index

- ASAM SOVD 1.0.0 page, release deck, TOC: https://www.asam.net/standards/detail/sovd/
- ISO 17978-1/-2/-3:2026: https://www.iso.org/standard/85133.html, https://www.iso.org/standard/86586.html, https://www.iso.org/standard/86587.html
- AUTOSAR AP EXP SOVD R24-11: https://www.autosar.org/fileadmin/standards/R24-11/AP/AUTOSAR_AP_EXP_SOVD.pdf
- AUTOSAR AP SWS Diagnostics R24-11 (SOVD fault mapping §7.3.3.5.4): https://www.autosar.org/fileadmin/standards/R24-11/AP/AUTOSAR_AP_SWS_Diagnostics.pdf
- AUTOSAR CP SWS Diagnostic Event Manager R24-11: https://www.autosar.org/fileadmin/standards/R24-11/CP/AUTOSAR_CP_SWS_DiagnosticEventManager.pdf
- Eclipse OpenSOVD fault-lib (design + SovdFault): https://github.com/eclipse-opensovd/fault-lib
- Redfish Resource schema: https://redfish.dmtf.org/schemas/v1/Resource.json ; Sensor v1_14_0: https://redfish.dmtf.org/schemas/v1/Sensor.v1_14_0.json
- Redfish Specification DSP0266 1.23.0: https://www.dmtf.org/sites/default/files/standards/documents/DSP0266_1.23.0.pdf
- OpenBMC "Remove Redfish Health Rollup" (2024-04-02): https://github.com/openbmc/docs/commit/44f3de929bbee0dbacd024b9724fe72817fd1f48
- IPMI v2.0 rev 1.1: https://www.intel.com/content/dam/www/public/us/en/documents/product-briefs/ipmi-second-gen-interface-spec-v2-rev1-1.pdf
- RFC 6933 Entity MIB v4: https://www.rfc-editor.org/rfc/rfc6933 ; RFC 3433 Entity Sensor MIB: https://www.rfc-editor.org/rfc/rfc3433 ; RFC 4268 Entity State MIB: https://www.rfc-editor.org/rfc/rfc4268
- Monitoring Plugins guidelines: https://www.monitoring-plugins.org/doc/guidelines.html
- Nagios Core 4 state types / freshness / flapping / reachability: https://assets.nagios.com/downloads/nagioscore/docs/nagioscore/4/en/statetypes.html , …/freshness.html , …/flapping.html , …/networkreachability.html
- Kubernetes API conventions: https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md
- KEP-1623 Standardize Conditions: https://github.com/kubernetes/enhancements/tree/master/keps/sig-api-machinery/1623-standardize-conditions
- KEP-589 Efficient Node Heartbeats: https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/589-efficient-node-heartbeats
- IETF draft-inadarei-api-health-check-06: https://datatracker.ietf.org/doc/html/draft-inadarei-api-health-check-06
- OpenTelemetry hardware semconv: https://github.com/open-telemetry/semantic-conventions/blob/main/docs/hardware/common.md ; attribute registry: https://github.com/open-telemetry/semantic-conventions/blob/main/docs/registry/attributes/hardware.md
- OpenTelemetry entity data model: https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/entities/data-model.md ; logs data model: https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/logs/data-model.md
- Home Assistant developer docs: https://developers.home-assistant.io/docs/device_registry_index/ , https://developers.home-assistant.io/docs/core/entity/ , https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unavailable/ , https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/log-when-unavailable/ , https://developers.home-assistant.io/docs/core/platform/repairs/ , https://developers.home-assistant.io/docs/core/entity/update/
