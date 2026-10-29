# RobotDx — the robot description and the 3D view

*2026-10-29. Code: `io_diagnostics/robot_description.py` (the file, matching, the two endpoints in `server.py`), `web/threed/core.js`
(the view, its five info layers), `web/threed/scene.js` (three.js: the URDF, picking, colours, the camera), `web/threed/threed.css`.
Libraries vendored in `web/vendor/three/` and `web/vendor/urdf-loader/` (`web/vendor/three/NOTICE.md`). API: docs/api-v1.md "Robot
description". Tests: `tests/test_robot_description.py`, `tests/test_threed_view.js`. Example: `examples/one-dof-test-rack/`.*

Zach, 2026-10-29: a "3D" item in the activity bar; going there keeps the entity tree, and the area where the device page sits becomes
a 3D viewport of the robot, "basically like Fusion 360". The robot description maps 3D parts to the devices the computer detects, so a
person can **navigate devices visually**: one bad CAN board turns that servo's part red or yellow.

**Optional.** Without the file RobotDx works exactly as before: the 3D item still shows, with a calm empty state saying what to add
and where; no 3D library is loaded and no other view changes.

## Where it lives

`<config dir>/robot-description.toml`: the folder `settings.toml` and `expected.toml` are in (`~/.config/io-diagnostics/`;
`$IO_DIAGNOSTICS_CONFIG_DIR` overrides it, as for the expected setup). The host reads it on request and again only when the file or
its URDF changes on disk; there is nothing to restart. It is your data, never written by RobotDx.

## The format

```toml
version = 1
# The robot's URDF. Its meshes (filename="meshes/x.stl") resolve next to it. Absolute, or relative to this file.
urdf = "/home/bam/.cache/cad2robot-build/bundles/a7b1095/fixtures/cad2robot/one_dof_test_rack/urdf/one_dof_test_rack.urdf"

# One [[part]] per device you can place in the model: the URDF link(s) it is, and how to find the device.
[[part]]
name = "CAN 12 · one-DOF arm"         # optional: a label (the page names a found device by its own row)
link = "one_dof_arm"                   # one link, or links = ["a", "b"] (both draw in its colour)
hardware_id = "usb:0483:5740:0607623A" # the device, as expected.toml names it
node = "can/12"                        # and the node below it, as the driver writes it
note = "robot.json revolute_2 can_id 12"  # optional: where the mapping came from

# Optional: devices you could not place in the model, with why. Listed in the 3D view, never drawn.
[[unmapped_device]]
name = "RealSense D405 218622274411"
serial = "218622274411"
reason = "the model has no D405 mount"
```

| Key | Where | Meaning |
|---|---|---|
| `version` | top | `1` |
| `urdf` | top | the URDF file (required) |
| `link` / `links` | part | a URDF link name, or a list of them (one of the two is required) |
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
needs at least one key. Nothing matching is not an error: the part is **not detected** and draws as a ghost.

A file that does not read (not TOML, a key of the wrong type, no `urdf`, the URDF missing or not XML) is named in the 3D view's empty
state with the reason, and nothing is drawn. A `link` the URDF does not have is that part's own `problem` in the API.

## What the 3D view draws

- **The model**: the URDF's visual meshes (STL, and box / cylinder / sphere primitives) with every joint at zero, neutral grey, a
  soft hemisphere + key + fill light, a ground grid sized to the model and an axis gizmo (three's ViewHelper; URDF is Z up: the
  gizmo's Z is up). Mouse as Fusion: left drag orbits, middle or right drag pans, the wheel zooms; F (or the frame button) fits the
  whole model. A still scene draws nothing: frames are drawn on demand.
- **Colour sits on the lowest-level entity**, the device the part maps to, from its own row (`level` in `/api/v1/live`, the page's
  `/state` as the fallback): error, warn, unknown and stale in the skin's level tokens; OK as a light tint of the ok token (OK carries
  no colour on a row; here the tint only says "mapped and well"). Never a roll-up: a bad board colours its own part and nothing above
  it. **Not detected** (no row, or the row is not connected) is a ghost at 15 % opacity. A link no part names is plain grey.
- **Selection** is the page's: a tree click highlights the part (a blue tint and a blue outline, as Fusion) and flies the camera to
  it; a tree row above mapped devices (their adapter) highlights all of them. Clicking a part selects its row in the tree (the camera
  stays where you put it). A click on empty space changes nothing.
- **Hover** shows a small card in the viewport with Open, which goes to that entity's normal device page.
- **Back and forward**: the 3D view is a page of the browser's history (`view=threed`, the selection, and `cam`, the camera pose, kept
  up to date as you move it). Open from 3D, then Back (the breadcrumb's <, Alt+←, the mouse's back button) returns to the same
  selection and camera; Back never re-frames.

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

The rack's bundle (`examples/one-dof-test-rack/`: 223 visual STL meshes, 1.42 M triangles, ~70 MB) in headless Chrome on this PC with
software WebGL (SwiftShader), served by `serve` on localhost, over seven loads on 2026-10-29: the URDF parsed in about 0.06 s, **the first
frame with geometry at 0.16-0.36 s** and every mesh at 1.9-2.7 s (`tests/test_threed_view.js` and `docs/record_threed.js` print it; the legend shows it). Under the 3 s target,
so meshes are not merged; `scene.mergeByLink()` (merge each link's meshes per material in the browser) is there if a bigger model
needs it. The bundle is read in place, never converted or copied. Picking is GPU picking (each mesh drawn once in an id colour into a
1×1 target under the pointer), not a raycast through a million triangles.

## The rack's description (examples/one-dof-test-rack/robot-description.toml)

Mapped only where a written source ties a device to a link; everything else is an `[[unmapped_device]]` with the reason. Each
mapping's source is a comment in the file. As of bundle a7b1095 two boards are placed: **CAN 12 → `one_dof_arm`** (robot.json
`revolute_2.can_id = 12`) and **CAN 14 → the GIM43 tag-1 holder link** (deployment.json pairs tag 21 with "GIM43 Aruco Tag:1"). The
mesh names carry Fusion's occurrence indices, not ArUco ids, and the GIM43 plate's joints are not in the v1 export, so CAN 1, 11, 13,
15 and 21, the adapters and the D405s stay unmapped until a newer export names them. Install it with:

```bash
cp /home/bam/io-diagnostics/examples/one-dof-test-rack/robot-description.toml /home/bam/.config/io-diagnostics/robot-description.toml
```
