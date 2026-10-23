# One action vocabulary across drivers (record v0.1) — frozen 2026-10-22

Zach (Daily Note 2026-10-22, "# Ok what is my goal today?"): "across different drivers … we can use the same commands so
we forget about the implementation details behind." The model is a printer driver's utilities window (Canon "Cleaning":
icon, title, one-line description per utility; `Sources/Images/Pasted image 20261022173436.png`).

## The rule
- A driver offers its actions under these **machine names**. Same name ⇒ same meaning, same label, everywhere.
  The label is the host's: a driver's `title` for a standard name must equal the label below (the validator checks).
- A driver may add a non-standard action. It is shown under "More" and must not reuse a standard name for another
  meaning.
- `danger` is per driver: the same name can be `query` on one device and `moves` on another (moteus calibration spins
  the rotor; RealSense on-chip calibration does not move anything). The UI derives the confirmation from `danger`.

## Danger classes (record v0.1 adds `moves`)
`query` reads only · `interrupts` stops or resets what runs (stream drops, board reboots) · `changes` writes stored
state (config, ids, faults) · **`moves` makes hardware move** (a motor spins or a loaded joint can fall).
Every action except `query` needs an explicit, per-device confirmation; agents never run them — only a human click.

## Standard names
| name | label | means | moteus (fdcanusb + boards) | RealSense (intel_rgbd) |
|---|---|---|---|---|
| `check_comms` | Check communication | ask it and report who answers, reply loss, link/bus state | addressed id scan (`d_find` = `utils/moteus_find_ids.py`) + bus triage (`d_triage` = `utils/can_bus_triage.py`) · query · scope device | SDK enumerate + `camera_info` · query |
| `check_firmware` | Check firmware | installed vs latest known | board fw vs latest official release (cache) · query | installed vs SDK-recommended · query |
| `update_firmware` | Update firmware… | install the latest official firmware: back up config, flash, verify, restore | `Settings/Skills/update-moteus-firmware/scripts/update_moteus_firmware.py --target N` · changes · scope node | `rs-fw-update` with the recommended image, when one is known · changes |
| `calibrate` | Run calibration… | the vendor's calibration routine | `python3 -m moteus.moteus_tool --target N --calibrate` (`d_calibrate`) · **moves** (rotor spins ~30 s; the joint must be free: no load, no hard stop in reach) · scope node | on-chip self-calibration · changes |
| `update_config` | Update configuration… | write the stored configuration from the recorded file (back up the current one first) | stock `moteus_tool --target N --dump-config` (backup) then `--restore-config <file>`; the file is the board's recorded config · changes · scope node | not offered |
| `clear_faults` | Clear faults | acknowledge latched faults | stop command to the board (moteus clears a fault by mode stop) · changes · scope node | not offered |
| `reboot` | Reboot… | restart the device | board reset · interrupts · scope node | `hardware_reset()` · interrupts |
| `stop_all` | Stop all | stop every motor on this bus now | `moteus_tool --stop` to every answering board (`d_stop`) · interrupts (loaded joints fall when their servo stops) · scope device | not offered |
| `set_id` | Change CAN id… | give a board a new bus id | `utils/moteus_reassign_id.py --target A --new B` (`d_reassign`) · changes · scope node | not offered |
| `open_console` | Open console | the vendor's own console, named in the description (holds the port until closed) | `utils/moteus_with_targets.py tview` (`d_tview`) · interrupts · scope device | `realsense-viewer` · interrupts |

## Dry run (plugin boundary, additive)
`act --device <id> --action <name> [--node <CAN id>] --confirm <token> --dry-run` runs nothing and prints one
`action-result` line whose `command` is the exact argv the button would run, plus the preconditions it would check.
Dry runs are how agents prove a write button without writing.

## Identity: `model_id` (record v0.1)
`Entity.identity["model_id"]` carries the robotparts part id (e.g. `mjbots-moteus-c1`) as a plain string — Home
Assistant DeviceInfo's own key for a model identifier. robotdiagnostics never imports robotparts; the UI may link the
id to robotparts.dev JSON for a photo / datasheet. Empty until the RobotParts Agent freezes the ids.
