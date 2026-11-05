# RobotDx — the robot description and the 3D view

*2026-10-29, the upload and the mapping 2026-11-04. Code: `io_diagnostics/robot_description.py` (the file, matching, read-only),
`io_diagnostics/robot_library.py` (the page's writes: upload, kept descriptions, assign), `web/threed/core.js` (the view, its info layers,
the chips, Map devices, the upload), `web/threed/scene.js` (three.js: the URDF, picking, colours, the camera), `web/threed/threed.css`.
Libraries vendored in `web/vendor/three/` and `web/vendor/urdf-loader/` (`web/vendor/three/NOTICE.md`). API: docs/api-v1.md "Robot
description". Tests: `tests/test_robot_description.py`, `tests/test_robot_library.py`, `tests/test_threed_view.js`,
`tests/test_threed_import.js`. Example: `examples/one-dof-test-rack/`.*

Zach, 2026-10-29: a "3D" item in the activity bar; going there keeps the entity tree, and the area where the device page sits becomes
a 3D viewport of the robot, "basically like Fusion 360". The robot description maps 3D parts to the devices the computer detects, so a
person can **navigate devices visually**: one bad CAN board turns that servo's part red or yellow.

Zach, 2026-11-04: "It would be awesome if I get the thing where I can just upload the URDF, etc. and then it could show the information
live ... The lowest rung for robot dx is I plug it in without any urdf etc and it just tells me what is up, and we always want that. At
some higher rung though we just plug it in, and like now we can see which motors are where."

**Optional, and the lowest rung stays.** Without the file RobotDx works exactly as before: the 3D item still shows, with a calm empty
state offering the upload and saying what to add and where; no 3D library is loaded and no other view changes.

## Where it lives

`<config dir>/robot-description.toml`: the folder `settings.toml` and `expected.toml` are in (`~/.config/io-diagnostics/`;
`$IO_DIAGNOSTICS_CONFIG_DIR` overrides it, as for the expected setup). The host reads it on request and again only when the file or
its URDF changes on disk; there is nothing to restart. It is your data: RobotDx writes it only when you do so on the page (an upload,
Map devices), keeping your comments and the old file beside it as `.bak`.

## The format

```toml
version = 1
name = "one-dof-test-rack"             # optional: its name in the page's list (default: from the URDF's file name)
# The robot's URDF. Its meshes (filename="meshes/x.stl") resolve next to it. Absolute, or relative to this file.
urdf = "/home/bam/.cache/cad2robot-build/bundles/a7b1095/fixtures/cad2robot/one_dof_test_rack/urdf/one_dof_test_rack.urdf"

[view]                                 # optional
front = "+x"                           # the way the robot faces in URDF axes (+x, -x, +y, -y): the home camera stands on that side

# One [[part]] per device you can place in the model: what it is on the model, and how to find the device.
[[part]]
name = "CAN 11 · one-DOF arm"          # optional: a label (the page names a found device by its own row)
link = "one_dof_arm"                   # one link, or links = ["a", "b"] (both draw in its colour)
meshes = ["One_Dof_Test_Rig_*JMC_Servo_1__*"]  # bodies inside a link, by mesh file name
hardware_id = "usb:0483:5740:0607623A" # the device, as expected.toml names it
node = "can/11"                        # and the node below it, as the driver writes it
note = "video: the tag strip turned, only CAN 11 moved"  # optional: where the mapping came from

# A device the model does not have, drawn as a small camera at a point of the URDF's base frame, in metres.
[[part]]
name = "D405 218622274411"
at = [0.614, 0.042, 0.25]
look_at = [0.154, 0.048, 0.144]        # optional: what it points at
serial = "218622274411"

# Optional: devices you could not place in the model, with why. Listed in the 3D view, never drawn.
[[unmapped_device]]
name = "RealSense D435 044322072026"
serial = "044322072026"
reason = "its frame shows the ceiling, not the rack"
```

| Key | Where | Meaning |
|---|---|---|
| `version` | top | `1` |
| `urdf` | top | the URDF file (required) |
| `name` | top | its name in the page's list; default: from the URDF's file name |
| `root` | top | the folder served to the page, which must hold the URDF; default: the URDF's own. An upload sets it, so a URDF's `../meshes` resolves |
| `front` | `[view]` | `+x`, `-x`, `+y` or `-y`: the way the robot faces. Default: broadside to its longest horizontal side |
| `link` / `links` | part | a URDF link name, or a list of them: the whole link is the part |
| `meshes` | part | patterns over the visuals' mesh file names, `*` and `?` only, wherever they sit. For a link that holds many bodies, as a Cad2Robot `base_link` does. A pattern beats a link. Part of the same part as any `links` |
| `at`, `look_at` | part | `[x, y, z]` in metres in the URDF's base frame: a device the model does not have, drawn as a small camera there and picked like any part |
| `name`, `note` | part | your label and your source; shown, never judged |
| `hardware_id` | part, unmapped | a device row's repeatable id, `usb:<vid>:<pid>:<serial>` (vid / pid in any case) |
| `serial` | part, unmapped | a serial the device row reports: its USB serial, or the driver's (a RealSense's SDK serial) |
| `node` | part, unmapped | a path below the device found by `hardware_id` / `serial` (`can/12`); without either, any row ending in it |
| `path` | part, unmapped | an fnmatch pattern over the entity path (`host/usb1/port-1-5/*/can/1`) |
| `model_id` | part, unmapped | the row's robotparts id must be this (`mjbots-moteus-r4-11`) |
| `can_id` | part, unmapped | the row must be `.../can/<id>` (an integer) |
| `name`, `reason` | unmapped | what it is and why it is not in the model |

**Matching is expected.toml's** (docs/expected-setup.md): a device by `hardware_id` and / or `serial`, then `node` below it;
`path`, `model_id` and `can_id` narrow that, or stand alone. The first row in tree order that matches is the part's entity. A part
needs at least one key, and one of `link`, `links`, `meshes`, `at`. Nothing matching is not an error: the part is **not detected** and
draws as a ghost.

A file that does not read (not TOML, a key of the wrong type, no `urdf`, the URDF missing or not XML) is named in the 3D view's empty
state with the reason, and nothing is drawn. A `link` the URDF does not have, or a pattern no mesh matches, is that part's own `problem`
in the API.

## What the 3D view draws

- **The model**: the URDF's visual meshes (STL, Collada, OBJ, and box / cylinder / sphere primitives) with every joint at zero, neutral
  grey, a soft hemisphere + key + fill light, a ground grid sized to the model and an axis gizmo (three's ViewHelper; URDF is Z up: the
  gizmo's Z is up). Mouse as Fusion: left drag orbits, middle or right drag pans, the wheel zooms; F (or the frame button) goes home. A
  still scene draws nothing: frames are drawn on demand.
- **Home**, the first view, as Fusion's: from the front's right and a little above, and the whole model fills the viewport (every mesh's
  box in view, centred, 92 % of the tighter way). `[view] front` says which side is the front.
- **Colour sits on the lowest-level entity**, the device the part maps to, from its own row (`level` in `/api/v1/live`, the page's
  `/state` as the fallback): error, warn, unknown and stale in the skin's level tokens; OK as a light tint of the ok token (OK carries
  no colour on a row; here the tint only says "mapped and well"). Never a roll-up: a bad board colours its own part and nothing above
  it. **Not detected** (no row, or the row is not connected) is a ghost at 15 % opacity. A link no part names is plain grey.
- **Colour stays on the device's own geometry.** A part names whole links or just bodies inside one, so one bad board colours its motor
  and not the base_link every motor of the rack is exported into.
- **Selection** is the page's: a tree click highlights the part (a blue tint and a blue outline, as Fusion) and flies the camera to
  it; a tree row above mapped devices (their adapter) highlights all of them. Clicking a part selects its row in the tree (the camera
  stays where you put it). A click on empty space changes nothing.
- **What no CAD has.** Adapters and computers float as a row of chips over the viewport, one each, in their row's own colour, with the
  number of their devices on the model. Hovered or selected, a chip lights its devices' parts and draws a leader line to each. WHY chips
  and not a place in 3D: an fdcanusb hangs off a USB cable somewhere near the PC, and a position for it would be invented. Clicking one
  selects it in the tree. Beside them, **Not in the model** lists every device a person could place and nobody has, each with its status
  and the reason if the file gives one (`[[unmapped_device]]`); clicking one selects it in the tree.
- **Hover** shows a small card in the viewport with Open, which goes to that entity's normal device page.
- **Back and forward**: the 3D view is a page of the browser's history (`view=threed`, the selection, and `cam`, the camera pose, kept
  up to date as you move it). Open from 3D, then Back (the breadcrumb's <, Alt+←, the mouse's back button) returns to the same
  selection and camera; Back never re-frames.

## From the page

**Bringing a robot in.** On the empty state, or from the Robot button, or by dropping anywhere on the view: a URDF with its meshes, as a
folder or a `.zip`. It shows at once. Each one is unpacked under `<config dir>/robot-descriptions/<name>/` and written as the active
`robot-description.toml`; the one that was active is kept under its own name. The Robot button lists what is kept: Show switches, Remove
(asked twice on the button) deletes that kept folder and nothing outside it. Removing the shown one goes back to the lowest rung.

- A URDF's meshes may sit next to it, in `../meshes`, or behind `package://<name>/...`: the host finds the package's folder in the upload.
  A mesh the upload lacks is counted on the page and the rest still draws. STL, Collada (`.dae`) and OBJ meshes are drawn; a `.xacro`
  must be expanded first, and the page says so.
- **A cad2robot/1 bundle** (a folder or zip with its `robot.json`) maps itself: each joint with a `can_id` onto its child link and motor,
  each camera with a `serial` onto its link. The URDF is the one `robot.json` names. What `robot.json` lacks is listed in the answer's
  `warnings`. A mapping from `robot.json` is only as good as the drive file behind it: the rack's says CAN 12 for the arm, which the
  cameras disproved (below).
- Refused, leaving nothing behind: a member that would land outside the folder (`..`, an absolute path, a symlink), not a zip, no
  URDF in it, over 1 GiB uploaded, over 3 GiB or 20,000 files unpacked (counted as read, not as the archive claims).

**Map devices** is the 10-minute fallback for when no file says which motor is which. Pick a device (a tree row, or the Not in the model
list), turn on Map devices, click its part: saved at once. Click the same part again to undo. A second body joins the same device, and
a body another device held moves here. With the adapter's Identify (wiggle each board), a person walks the rack and clicks each motor as it
moves.

A click takes the whole link when the link is one body. An assembly export lumps a whole rack into `base_link`, so there it takes the
component under the pointer, read from Cad2Robot's file names (`Plate_1___Motor_2__Bearing_1__0.stl`: level by level): the widest one
under 30 % of the link, a motor and not the rack. The tip names it before the click; `[` and `]`, Shift+wheel, or Narrower and Wider
take a smaller or bigger piece, and the step holds for the next body.

The edit changes only that `[[part]]`: comments, order and every other table stay as written, the new text must parse before it replaces the
file, and the old file stays beside it as `.bak`. A new part is found by the device's repeatable keys (`hardware_id` and `node`, else
`serial`), not by its USB port.

## The five info layers (temporary: the "3D info" picker, bottom-right)

The same scene, picking and colours; only how a device's information shows differs. The choice is remembered
(`localStorage io-diagnostics.threed.variant`); v1 is the default.

| | Name | What it shows |
|---|---|---|
| v1 | Hover card | A card at the cursor: name, level, the 2-3 worst checks (`/api/v1/checks`), key State values (`/api/v1/values`), Open. A click on the part pins it (Esc unpins). |
| v2 | Callout labels | Fusion-style leader lines from each device that is not OK (not detected included) to a name and status chip; "All labels" labels every mapped part. A chip click selects. |
| v3 | Peek drawer | Selecting opens a narrow right drawer: the entity's summary and its Diagnostics rows, Open. Resizable (remembered), foldable. |
| v4 | Split view | The viewport on top, the selected entity's real device page (its tabs) below; the divider drags (remembered), the page folds. |
| v5 | Problems tray | A bottom strip of every device that is not OK, worst first: click flies to it, double-click opens it. Hover shows a tooltip only. |

Every boundary of the view drags and is remembered in this browser (`io-diagnostics.threed.drawer`, `.split`, `.tray`), double-click
restores the default, the arrow keys move a focused one, and each panel folds (`...fold`) with a tab to bring it back.

## Load time

The rack's bundle (223 visual STL meshes, 1.42 M triangles, ~70 MB) in headless Chrome on this PC with
software WebGL (SwiftShader), served by `serve` on localhost, over seven loads on 2026-10-29: the URDF parsed in about 0.06 s, **the first
frame with geometry at 0.16-0.36 s** and every mesh at 1.9-2.7 s (`tests/test_threed_view.js` and `docs/record_threed.js` print it; the legend shows it). Under the 3 s target,
so meshes are not merged; `scene.mergeByLink()` (merge each link's meshes per material in the browser) is there if a bigger model
needs it. The bundle is read in place, never converted or copied. Picking is GPU picking (each mesh drawn once in an id colour into a
1×1 target under the pointer), not a raycast through a million triangles.

## The rack's description (examples/one-dof-test-rack/robot-description.toml)

Every one of the rack's eight boards and both D405s is placed, each on two proofs (2026-11-04, no motor moved; each mapping's evidence is
a comment in the file, the images are in `reports/media/rack-3d-2026-11-04/report.html`):

1. **Which tag the board turns.** Recorded video where only that board moved, DICT_4X4_50 tags tracked, the plate headers as the noise floor;
   for CAN 12, whose tag no later recording sees, the rest-vs-held still of 2026-10-23.
2. **Which CAD body the tag is on.** Each plate's tags against the CAD's motor axes, from the camera-measured as-built tag map: the same
   pattern within 3 mm on every plate, in the same left-to-right order, so the model is not mirrored. The plates' places along the rail differ
   from the CAD by up to 104 mm (they slide on the extrusion), so the fit is per plate.

| Device | On the model | Tag |
|---|---|---|
| CAN 1 | GIM4305-10SHS:3, hardstop + holder | 0 |
| CAN 14 | GIM4305-10SHS:1, tag holder | 1 |
| CAN 15 | GIM4305-10SHS:2, `gim43_arm` | 2 |
| CAN 4 | JMC 60ASM200:1, aruco holder | 4 |
| CAN 21 | JMC 60ASM200:2, `pole_mount` | 5 |
| CAN 13 | AD60-G10-D14:1, `jmc60_hardstop_plate` | 6 |
| CAN 12 | AD60-G10-D14:2, JMC 60ASM200:3, `jmc60_gearbox_aruco_mount` | 7 |
| CAN 11 | the rig's JMC Servo:1, `one_dof_arm` | 8-15 |
| D405 218622274411, 218622276036 | placed by solvePnP at their solved poses (0.77, 0.88 px RMS) | |

**The old CAN 12 on `one_dof_arm` was wrong.** It came from `robot.json`'s `can_id`; CAN 11 turns the arm. The D435 stays unplaced: its frame
shows the ceiling and no tag, so nothing ties its serial to the CAD's D435. Install the file with:

```bash
cp /home/bam/io-diagnostics/examples/one-dof-test-rack/robot-description.toml /home/bam/.config/io-diagnostics/robot-description.toml
```
