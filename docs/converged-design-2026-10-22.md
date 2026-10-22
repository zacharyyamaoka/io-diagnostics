# IO diagnostics — converged design (2026-10-22)

Input: babble round 1 (5 variants, V2 Plug Journal provisional), Zach's feedback of 2026-10-22 (Daily Note,
"Feedback on Io Diagonistics", 14 screenshots), the prior-art survey (reports/media/survey/lane_{ros,vendor,general}.md)
and his own prior thinking ([[DP - Diagonistics]], [[MOC - Diagnostics]], [[C - ROS Diagnostics Stack]],
`bam_diagnostics` in like_keras).

## What it is (scope)

A **hardware IO diagnostics dashboard**: plug something into the computer and see it: which USB port, what it is,
its ids, versions, update available, read-only config, comms health (rate, loss, error counters), and a few
driver-registered utility buttons. RealSense-Viewer level, no robot control, works before any robot stack runs.
Software components later reuse the same contract; a CAD/3D view that paints the same statuses onto links is out
of scope for now.

## Layout (what Zach picked from the references)

1. **Entry point:** a small top-bar chip ("IO 3 ⚠") with a Recent list; NOT inside Ubuntu's Quick Settings.
   It opens the dashboard window.
2. **Dashboard window = entity tree + detail** (Clearpath "All Diagnostics", ros2_medkit "Entity Tree",
   rqt_robot_monitor, Dynamixel Wizard): left column is a tree with **icon · status · name · id/count**,
   a search box on top; click a row → the right panel shows details. Parents roll up the worst child.
3. **Table mode** of the same tree: nested first column, aligned columns to the right (Clearpath's nested Message
   column + NVIDIA Greenwave Monitor's Frequency / Expected Frequency / Status, Phoenix Tuner X's
   Name | Model | ID | Bus | FirmVer | Status). Columns: name/id · status · message · rate (measured vs expected) ·
   last seen (staleness) · firmware (update?) · errors.
4. **Activity log** (Clearpath cockpit history, rqt_robot_monitor's timeline strip): plugged / unplugged /
   re-enumerated / error-passive / fault entered / fault cleared, with time and port.
5. **Detail panel** (RealSense Viewer, Home Assistant device page, DroneCAN node dialog): identity
   (manufacturer, model, serial, hw/sw version, via parent), versions + update available, read-only config,
   comms stats, values table, and the driver's registered utility buttons.

## Thinking sharpened (orchestrator's view — challenges and decisions)

### 1. Split the STATUS plane from the ACTION plane — that answers "read-only or command?"
- **Status plane (read-only, always on).** Drivers emit standard records; the dashboard only renders. Nothing can
  interfere with the hardware. Staleness is computed, not reported: every record carries its expected period;
  a record older than ~3× its period turns STALE (ROS diagnostics' 4th level).
- **Action plane (opt-in, driver-owned).** "How do printers do it?" — a printer *driver* registers its utilities
  (print test page, clean heads, align); the OS dialog just shows the buttons. Same here: a driver registers named
  actions (`identify/blink`, `reset`, `clear_faults`, `update_firmware`, `run_comms_check`) with a description,
  a danger level, preconditions and a confirm text. The dashboard shows a button only if a driver registered it,
  and **the driver executes it** — the host never speaks the device protocol. Off-the-shelf twins: Home Assistant
  `button`/`update` entities, ros2_medkit's Operations tab, DroneCAN's Restart/Update dialog.
- So permissions reduce to one rule the driver already has to obey: **whoever owns the port does the talking.**

### 2. Ownership: the dashboard must never compete with the robot stack for a port
- When the stack runs, its drivers own the ports and *publish* diagnostics; IO diagnostics is a pure subscriber.
- When nothing runs, IO diagnostics may start a driver's own diagnostics entry point as a short-lived owner
  (holder check + the rack bus lock first; "blocked: bus busy (tview pid 640623)" is a shown state).
- Today's evidence: a config read collided with Zach's open tview → exactly the failure this rule prevents.

### 3. The contract = ROS `DiagnosticStatus` + three fields (don't invent a schema)
- Keep: `level` (OK/WARN/ERROR/STALE), `name`, `message`, `hardware_id`, `values[key,value]`.
- Add: **`path`** (entity tree position, e.g. `host/usb-1-5/fdcanusb-272813EF/can/14`),
  **`expected_period_s`** (staleness), **`identity`** (Home Assistant `device_info`: manufacturer, model,
  serial_number, sw_version, hw_version, `via_device` = the parent — the parent/child relation already exists there).
- Update availability = Home Assistant's update entity (`installed_version`, `latest_version`).
- Emitting it needs no ROS: a Python dataclass + JSON. A ROS node or a `foxglove-sdk` bridge can republish the same
  records later, so rqt_robot_monitor / Lichtblick / Foxglove render them for free.

### 4. Two sources build one tree
- **The OS gives the skeleton** (always available, passive): host → USB controller/port/hub → device (sysfs,
  /dev/serial/by-id, kernel log). This is "which USB port is it plugged into".
- **Drivers hang children under their device:** fdcanusb → CAN bus → node 1, 14, 15 → (motor, encoder, driver);
  D405 → colour/depth streams. Unknown device = OS row with "no driver diagnostics" (still useful: presence, port,
  VID:PID, serial).

### 5. The computer diagnoses its own wires (Zach: "love that idea")
First-class "link health" diagnostics on every edge of the tree, from passive sources:
- USB: re-enumerations per port per hour, disconnects, link speed (a D405 on USB 2 instead of 3.2).
- CAN: bus error counters and state transitions (error-active → passive → bus-off), reply loss per node,
  ACK-less frames (28B3A25D: adapter fine, bus dead), bus voltage dips.
- Today's real examples to seed it: D405 dropped 4 s and re-enumerated; 28B3A25D flapped 5× in 17 s;
  CAN 15 fault 33 on bridge enable; 276036 moved 28 px (a mechanical "wire").

### 6. Why it is a stable sub-assembly
The software stack keeps changing; the hardware IO surface and the diagnostics contract do not (Zach's own
[[DP - Diagonistics]] commitment 3: "the diagnostic surface is stable"). Software components can join later with
the same records — ROS diagnostics was designed for both.

### 7. Open question worth one experiment: reuse Lichtblick as the dashboard?
Lichtblick (MPL-2.0) already renders DiagnosticArray as Summary + Detail. If the host published via `foxglove-sdk`,
the detail view would cost zero UI code. It lacks presence/plug events, update and action buttons and the USB
skeleton — the parts that make this tool. Recommendation: own the small chip + tree + actions; emit the standard
records so Lichtblick/Foxglove/rqt work as a second view (a ~30 min spike to prove the bridge).

## Recommended defaults (silence = these)
- Name: IO diagnostics. Stack: V2's single small Python process + a local web window (the tree/detail UI), the
  tray chip as entry.
- Plugin = the driver package's own `diagnostics.py` (status records + registered actions), discovered by entry point.
- Read-only by default; actions shown only when registered; every action confirmed; blocked while the port is held.
