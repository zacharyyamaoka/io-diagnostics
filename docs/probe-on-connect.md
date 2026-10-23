# On-connect probe: the driver says what to check when a device is plugged in

*2026-10-23. Frozen contract: the host and the driver plugins implement against this file; a change starts here.*

Zach, 2026-10-23: "perhaps the drivers should like register a autorun on connect command, that diagnostics should run
on connection and it may like automatically do a whole bunch of basics things just to verify its correctly
connected... that way instead of diagnostics having to hold that information its each device that tells diagnostics
what to do... than the feedback can just go back via the standard diagnostics or logs".

Stock model: the Linux driver model. When a bus matches a device to a driver, the kernel calls the driver's
`probe()` once for that binding; the driver brings the device up, checks it and reports. The host here is the bus;
the plugin's `probe` verb is the driver's `probe()`.

## 1. Declaring it

A plugin opts in from `describe`:

```json
{"name": "moteus_servo", "probe": {"timeout_s": 30}, ...}
```

`"probe": true` means "use the plugin's `timeout_s`". A plugin without `probe` keeps today's behaviour: the host runs
`diagnose` on connect.

## 2. Running it

```
<command> probe --device <id> [diagnose_args]
```

The environment is diagnose's (`IO_DIAGNOSTICS_DEVICE_PATH`, `IO_DIAGNOSTICS_EXPECTED`, ...).

## 3. When the host runs it

- **Once per connection:**
  - when a device is first matched (plugged in, or already present when the host starts);
  - on every re-enumeration (unplug and back).
- **Again when a probe ended blocked:** its result carries a status with code `PortBusy` or `BusLockHeld`, plus the
  holders' `"pid program"`.
  - The host watches those pids (a `/proc/<pid>` check about once a second) and re-runs the probe about 1 s after the
    last one exits.
  - Meanwhile it retries on a backoff of 10, 20, 40 s, capped at `period_s`.
- **Never on a schedule.** A probe takes the place of the first `diagnose` of a connection. The periodic `diagnose`
  (or `follow`) starts `period_s` after the probe and is unchanged.
- It honours the `exclusive` group queue like any other job.

## 4. What it prints: the same records as diagnose

JSON lines of `contract/record.py` Entity / DiagnosticStatus / Event, applied exactly like diagnose's: the latest
status per entity and check wins.

The **driver says what it found**, in Events, and the host writes them to the Activity log as they are, with the
plugin as source. Three standard codes, each with one meaning on every driver:

| code | when | example message |
|---|---|---|
| `Found` | the probe heard a sub-device (or the device itself) answer | `CAN 12 answers on fdcanusb 0607623A (moteus-c1 · fw 1.1.2)` |
| `Probed` | one summary per probe, always last | `fdcanusb 0607623A: 1 of 4 expected boards answer (CAN 12) · CAN 11, CAN 13, CAN 21 silent · 5 Mbit/s BRS clean 412/412` |
| `ProbeBlocked` | the probe could not talk to the device | `tview has the port (pid 93263) · RobotDx probes again when it closes` |

What fails is a DiagnosticStatus at WARN or ERROR, with its `what_to_check`, as today. A board that later goes
silent is the driver's not-answering status, and its fault lifecycle (pending → confirmed → healed) is the log
of it. So there is no `Lost` event, and the host derives no device-specific events of its own: no diffing of
entity sets.

## 5. Safety

A probe is **query-only**, danger `query`: no motion, no config, id or firmware writes, no resets, no energizing. It
passes the same gates as diagnose (port-holder check, bus lock, frame budget) and is bounded by its `timeout_s`. What it
checks is the driver's call. For moteus:
- the expected ids first, then the rest of the range;
- identity and firmware against the latest release;
- the config against the inventory's safety keys;
- the 5 Mbit/s BRS link.

## 6. What stays the host's

The host keeps the OS layer's events (Plugged, Unplugged, ReEnumerated, LinkSpeed, DriverBound...), the fault
lifecycle, level changes, and its own job results. The device row's plugin-run check reads `probed 15:12:31 · 3.1 s`,
or, while blocked, the ProbeBlocked message.
