# 0002: RobotDx is the robot-health super-app, layer by layer

- **Status:** decided by Zach, 2026-10-29. Seams and placeholders now; the layers get built out over time.
- **Supersedes:** the dashboard picks D1–D5 in `reports/dashboard-proposals-2026-10-24.html`, which predate data-ui.

## Decision
RobotDx is the one app for **robot health**, in the spirit of ros2_medkit. It covers getting a robot back to health and catching when it stops being healthy.
- **Who it's for:** the mechanics and engineers who repair and maintain robots.
- **What it is not:** fleet management, meaning task dispatch (Open-RMF, VDA 5050) or robot operations (Formant). That is a separate platform, one step above.

In Zach's words: "I 100% want to make this kinda a super app for robot health similar to ros2 med kit."

RobotDx must keep working from the bottom up. One computer with one servo plugged in, no robot description and no fleet works great. Each layer below builds on top of that, and you can zoom out seamlessly to a fleet across the planet.

## The layers, bottom to top
| # | Layer | Today | What it is |
|---|---|---|---|
| 1 | **Device management** | built: the tview / realsense-viewer layer | detect a device from USB etc. with no registration ahead of time; read and configure it; utilities. Everything else depends on it. |
| 2 | **Diagnostics** | built: checks plus robotdx.diagnostics | health checks and stock diagnostics over each device's live and recent data |
| 3 | **Software deploy, OTA and config** | placeholder | firmware, software, configs and neural networks, versioned together (a config is often pinned to a software version, and may be tuned). Fully tracked and traceable. Works on one device, or batched over a facility, a state or the fleet. Live NN updates make this matter more. ros2_medkit includes OTA: "a very big vote in favor of making one thing encompass a lot". |
| 4 | **Teleop** | placeholder | for when a robot is stuck: live camera over WebRTC, dragging the Cartesian end effector from the keyboard or mouse, and streaming input devices (e.g. a Leap Motion) |
| 5 | **Data storage** | placeholder; today RobotDx keeps only a short history, like a system monitor | where recorded data lives, how long it's kept and what it costs |
| 6 | **Fleet view** | being built: the Map plus the 3D workspace | physical entities at locations; zoom from Planet Earth to one servo. It is a reusable view, so other apps can show other data on it. |

Control software is out of scope for now.

## Rules that follow
- **Reuse where it fits.** The fleet view and data-ui views are shared building blocks, and a layer may be split into its own package where that is the right cut.
- **Dashboards are built with data-ui.**
  - Humans reorder and reorganise; data-ui already supports this.
  - **Add to dashboard** from any view of the data works like adding to a saved list: the item goes to the bottom of a chosen dashboard. This is a generic data-ui feature, not something specific to RobotDx.
  - "Open in Rerun" stays as an escape hatch that will eventually go. It also keeps some interoperability honest.
  - Most dashboards will be made by AI. The human curates them.
- **Placeholders are honest.** A layer that isn't built shows as a calm placeholder page saying what it will do and what it builds on. It never shows a fake control.
