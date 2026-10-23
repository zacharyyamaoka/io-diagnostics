# IO diagnostics — the expected setup (a rig manifest)

*2026-10-23. Code: `io_diagnostics/expected.py` (+ `host.py`, `server.py`, `cli.py`, the Expected setup card in
`web/app.js`). Tests: `tests/test_host_setup.py`, `tests/test_utilities.js` ("expected setup"). API:
docs/api-v1.md "Expected setup" and "Editing the expected setup".*

Zach, 2026-10-23, after fdcanusb 28B3A25D enumerated on USB but never put a frame on the wire: "it detects the canusb
but something is wrong with it so you don't see any CAN things connected … in diagnostics we need to be able to like
easily configure expected number of things". The expected setup is that: a small file that says what this PC should
see, so the page can say "6/7 boards" and name the missing one, instead of only showing what happens to answer.

## Where it lives

`~/.config/io-diagnostics/expected.toml` (`$IO_DIAGNOSTICS_CONFIG_DIR` overrides the directory, else
`$XDG_CONFIG_HOME/io-diagnostics`). WHY outside the checkout: it is your data, like `seen.json` in
`~/.local/state/io-diagnostics/`. A live host reads it and re-reads it within one refresh (2 s) of a hand edit. A
fixture or a fake machine reads one only when it is named: `serve --fixture F1 --expected FILE`.

A file that does not read (bad TOML, a device with no id) is named in the page's sources line ("expected setup
skipped") and the panel refuses to rewrite it until it reads again: it never overwrites what you wrote.

## The format

```toml
# IO diagnostics · expected setup: the rigs this PC should see, and the devices retired from service.
version = 1

# One [[rig]] per rig; its row is "1-DOF test rack · 2/2 adapters · 6/7 boards · 2/2 cameras".
[[rig]]
name = "1-DOF test rack"

# A device is matched by its hardware_id (the OS row's, usb:<vid>:<pid>:<serial>), else by a serial its row
# reports: the USB serial, or a driver's own (a RealSense's SDK serial). kind is the word the rig row counts it as.
[[rig.device]]
name = "fdcanusb 272813EF"
kind = "adapter"
hardware_id = "usb:0483:5740:272813EF"
model_id = "mjbots-fdcanusb"
# The nodes the driver should find under it, relative to the device as the driver writes them. Each is a path, or
# a table with the path and, optionally, the model it should report (robotparts id) and its own repeatable id.
# CAN 15's moteus-c1 is being replaced (2026-10-23): until it answers, the row says "CAN 15 on fdcanusb 272813EF".
nodes = [
  { path = "can/1", model_id = "mjbots-moteus-c1" },
  { path = "can/14", model_id = "mjbots-moteus-c1" },
  { path = "can/15", model_id = "mjbots-moteus-c1" },
]

# note: what the board drives (camera-confirmed 2026-10-23 by wiggling each board and tracking its ArUco tags);
# rotor_to_output_ratio: what its config should say, so a wrong one reads "config ratio 0.1667, expected 0.1 (10:1
# gearbox)" on that board. CAN 21 was CAN 1 until 2026-10-23 (renamed at Zach's request).
[[rig.device]]
name = "fdcanusb 0607623A"
kind = "adapter"
hardware_id = "usb:0483:5740:0607623A"
model_id = "mjbots-fdcanusb"
nodes = [
  { path = "can/21", model_id = "mjbots-moteus-r4-11", note = "tag 5 · direct", rotor_to_output_ratio = 1.0 },
  { path = "can/11", model_id = "mjbots-moteus-r4-11", note = "link tags 8-12 · 10:1", rotor_to_output_ratio = 0.1 },
  { path = "can/12", model_id = "mjbots-moteus-r4-11", note = "tag 7 · 10:1", rotor_to_output_ratio = 0.1 },
  { path = "can/13", model_id = "mjbots-moteus-r4-11", note = "tag 6 · 10:1", rotor_to_output_ratio = 0.1 },
]

# The D405s go by their RealSense serial; the USB id (its USB serial differs) lets the host say what the kernel saw
# when one is missing, and match it before its driver has run.
[[rig.device]]
name = "RealSense D405 218622274411"
kind = "camera"
hardware_id = "usb:8086:0b5b:219323070276"
serial = "218622274411"
model_id = "realsense-d405"
nodes = []

[[rig.device]]
name = "RealSense D405 218622276036"
kind = "camera"
hardware_id = "usb:8086:0b5b:130523071221"
serial = "218622276036"
model_id = "realsense-d405"
nodes = []

# A retired device is expected nowhere; plugged in again, its row says "retired <date>: <reason>" (a warning).
[[retired]]
name = "fdcanusb 28B3A25D"
hardware_id = "usb:0483:5740:28B3A25D"
date = "2026-10-23"
reason = "frames never reached the bus (2026-10-22); replaced by 0607623A; suspected damage from the mains-ground event (Zach, 2026-10-23)"
```

| Key | Where | Meaning |
|---|---|---|
| `version` | top | `1` |
| `name` | `[[rig]]` | the rig's name, shown on its row (required) |
| `name` | `[[rig.device]]` | what the rig row calls it when it is missing |
| `hardware_id` | device, retired | the OS row's repeatable id; USB ids compare vid / pid in any case |
| `serial` | device, retired | a serial its row reports (the USB serial, or the driver's `identity.serial_number`) |
| `kind` | device | the word counted on the rig row: `adapter`, `camera`, ... (default: the row's icon type) |
| `model_id` | device, node | the robotparts id it should report; a node that reports another is a note, never a problem |
| `nodes` | device | relative node paths (strings or `{path, model_id?, hardware_id?, note?, rotor_to_output_ratio?}` tables) |
| `note` | node | your label for it ("tag 5 · direct"), shown in its Expected setup card; never judged |
| `rotor_to_output_ratio` | node | what its config should read (moteus `motor_position.rotor_to_output_ratio`); a fresh reading that differs is one warn on that node, "config ratio 0.1667, expected 0.1 (10:1 gearbox)" |
| `date`, `reason` | retired | shown on its row when it is plugged in again |

At least one of `hardware_id` / `serial` per device. A device known only by a driver's serial is matched from its
driver's first report on (so its plugin gets `IO_DIAGNOSTICS_EXPECTED` from its second run); when it is missing, its row
under "Expected, not connected" says "no connected device reports serial …" instead of the kernel's evidence.

## What the page shows

- **One rig row per rig**, first under the host: `<name> · 2/2 adapters · 6/7 boards · 2/2 cameras`. A device counts
  when its row is here; a node counts when it answers (any fresh report that is not `NoReply` / `NoAck` /
  `BoardNotAnswering`). The kinds come in the file's order; the boards right after the first kind that has nodes.
- Its level: **warn** (`ExpectedMissing`) when anything expected is missing, naming each ("missing: CAN 15 on
  fdcanusb 272813EF"), with a numbered what-to-check; **unknown** (`ExpectedNotChecked`) when a node's check could not
  run (port busy, passive run, stale); else **ok** ("everything expected is here").
- **Counted once** (record v0.1 `caused_by`, 2026-10-23): the rig row's check names, per missing item, the row that
  reports it (the board's own `BoardNotAnswering` row, or the absent device's row under "Expected, not connected").
  So the chip counts the missing board once, as the board, and the rig row reads as its knock-on while keeping its
  own warn and words. When some item has no row that says it (a node its driver never mentions), the rig names
  nothing and counts as a cause of its own.
- **A wrong ratio** is the board's own warning, not the rig row's: `<node>: expected ratio` (`RatioMismatch`),
  "config ratio 0.1667, expected 0.1 (10:1 gearbox)", judged on the driver's latest fresh config reading.
- Notes that never change its level: an extra node that answers but is not listed ("also here, not expected: CAN 2 on
  fdcanusb 0607623A"), a node reporting another model, and an absent adapter whose boards answer on another one
  ("fdcanusb 28B3A25D's boards answer on fdcanusb 0607623A now": by the nodes' own ids when the file has them, else
  when one other adapter of the same model answers every one of its node paths).
- **Expected, not connected**: an expected device that is not here gets a row with the kernel's evidence (its USB id),
  beside the devices this host remembers seeing (`seen.json`) and those a driver lists (moteus: the rack inventory).
- **Retired**: plugged in again, a retired device's own row is a warning, "retired 2026-10-23: frames never reached
  the bus …", so a known-bad adapter is recognised at once. It is never "expected, not connected".

## What each driver is told

On every `diagnose` and `act`, a device the file lists gets

```
IO_DIAGNOSTICS_EXPECTED={"nodes": ["can/1", "can/14", "can/15"], "rig": "1-DOF test rack"}
```

and any other device gets `IO_DIAGNOSTICS_EXPECTED=` (empty: no expected setup, the driver uses its own source).
moteus_servo prefers it over its rack inventory: those ids are the boards it asks for by name and names when they do
not answer ("expected by 1-DOF test rack"); without it, the inventory's last-known ids stay the fallback.

## Editing it from the page

The selected row's **Expected setup** card (host-side file writes only; nothing touches a device, so a plain confirm
and no token):

| Row | Buttons |
|---|---|
| a device (adapter, camera, ...) | **Expect what's here**: add it (or update it) with the nodes answering below it now; takes a retired one back into service · **Retire…**: with a reason |
| a rig row | **Expect what's here**: every present device's nodes as they answer now (the confirm lists what changes) |
| a row under Expected, not connected | **Forget**: out of the file and out of `seen.json`, and kept out of a driver's own list until it is seen again · **Retire…** |

The same from a terminal (a person or the orchestrator; `--path` needs the host to see the row, so `expect --path` runs
every plugin once):

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics retire --hardware-id usb:0483:5740:28B3A25D --reason "frames never reached the bus"
```

```bash
uv run --project /home/bam/io-diagnostics io-diagnostics expect --hardware-id usb:0483:5740:0607623A --name "fdcanusb 0607623A" --kind adapter --nodes can/1,can/11,can/12,can/13 --rig "1-DOF test rack"
```

The panel and the CLI rewrite the file whole (atomically: a temporary file renamed over it), so comments other than
the header are not kept.

## Dead-adapter diagnosis (the generalisation of 2026-10-22)

- The driver's own verdict: moteus_servo's `FramesNotSent` (no board answered and the adapter's `can status` reads
  `dlec 7`, error-active: nothing reached the wire) lists, quickest first: unplug the CAN cable at the board end and
  run Check communication; if it still sends nothing it is the adapter (replug its USB); swap in another fdcanusb on
  this bus (if it sees boards, this adapter's transmitter is dead); meter CAN_H to CAN_L with the power off.
- The host's note: when another adapter of the same plugin on this PC has boards answering, the dead one's row gets
  "fdcanusb 272813EF works on this PC: plug this bus into it to tell adapter from bus".
- Once the boards answer on the new adapter, the rig row says where they moved; Expect what's here on the new one and
  Retire… on the old one make the file say so.
