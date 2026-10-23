# IO diagnostics — round 2 brief (Zach's feedback, 2026-10-22 14:46, Daily Note "Feedback on thing")

## What he said (paraphrased; his screenshots: Sources/Images/Pasted image 20261022144642.png, …144719.png, …145139.png)
- Grouping on the computer is cool, but the physical layers (host, USB bus, port, tty, CAN bus, driver/motor parts)
  **obscure the actual entities**. His mental model: `fdcanusb` → `Moteus c1 · CAN 15`, `Moteus c1 · CAN 12`.
- **Single computer = an unnecessary fold** (VS Code: the workspace root only appears once there are several top-level
  folders; with one you are "within" it). Elide `bam-GPU` when there is one host.
- Tree style: **between Quiet IDE (v1) and Notion (v2)**. Quiet IDE's benefit: you see at a glance which rows are
  folders (can expand). **The table view must match the tree** (same structure, same row grammar).
- "How do we reconcile the USB data and the simpler data? Maybe a toggle to show USB/PCIe ports?" → **5 proposals** for
  entities at a glance + detail when you want it.
- The table (aligned columns) is the shape he likes — without the extra structural rows.
- **11 skins**, each with **light and dark**: 1 Claude, 2 Obsidian, 3 Notion, 4 Terminal, 5 Foxglove/medkit,
  6 stock shadcn/ui, 7 Unstyled (bring your own tokens), 8–11 orchestrator's picks, orthogonal. Users may pick a skin,
  but there must be a **great default**.
- His goal today: "I plug it in and now I need it to run — such a basic thing."

## Decisions (orchestrator)
- **Rows are entities, labelled `model · id`** (e.g. `Moteus c1 · CAN 15`, `RealSense D405 · 4411`, `fdcanusb · 272813EF`).
  Composition parts (driver, motor) are not rows by default: their status surfaces on the owner row
  ("driver: fault 33"), the parts are listed in the detail panel. The black-box rule is unchanged (own report shown,
  worst-below as a hint).
- **Single-host elision** in every proposal: one host → its children are the top level; several hosts → host rows appear.
- **Tree styles**: keep v1 (Quiet IDE) and v2 (Notion); add **v6 "IDE × Notion"**: chevrons always visible but quiet on
  expandable rows (you see the folders), Notion's restraint (no guides, hover-revealed affordances). v3–v5 stay
  available but are no longer the focus.
- **Default skin = 1 Claude** (variant 1 applied as default); light/dark follows the OS (`prefers-color-scheme`) with
  a manual toggle; all choices persist (localStorage, try/catch) and switch live from the bottom-right control.

## The 5 structure proposals (entities at a glance, transport on demand)
- **P1 Devices, transport as columns** — only entities in the tree; the physical path becomes quiet columns/badges
  (`USB 1-3 · 12 Mb/s · ttyACM0`, `PCIe 0000:01:00.0 x1/x4`). Table-native.
- **P2 Two views, one toggle** — "Devices" (by function: Actuators · Cameras · Buses · Computer) ↔ "Connections"
  (the physical USB/PCIe/net tree). Precedents: Windows Device Manager "Devices by type / by connection",
  ros2_medkit Logical / Functional.
- **P3 Path on demand** — entity tree; the connection path appears as a muted breadcrumb under the hovered/selected
  row and in full in the detail panel (`USB bus 1 › Port 1-3 › ttyACM0`).
- **P4 Compact folders** — the physical tree, but pass-through nodes auto-fold: single-child chains join into one row
  (VS Code `explorer.compactFolders`), empty buses/ports hide behind "show empty", tty nodes become attributes.
- **P5 Bus lens** — top level = the communication buses the robot cares about (CAN 272813EF, CAN 28B3A25D, the USB
  cameras, PCIe capture), each listing its nodes; the computer's internal topology collapses into one "Computer"
  row that expands to the physical tree.

## The 11 skins (CSS tokens only — every skin works with every structure and tree style)
1 Claude (warm neutrals, serif display, generous spacing) · 2 Obsidian (dark-first purple accent, compact) ·
3 Notion (black-on-white restraint, emoji-free iconography) · 4 Terminal (monospace, ANSI palette, box-drawing) ·
5 Foxglove / ros2_medkit (dense robotics tooling, cool greys) · 6 stock shadcn/ui (zinc, default radius) ·
7 Unstyled (structure + semantic tokens only; a documented token sheet to bring your own) ·
8 **ISA-101 high-performance HMI** (industrial: grey everywhere, colour only for abnormal — the standard behind
"quiet when OK") · 9 **macOS System Settings** (native Apple HIG) · 10 **Linear** (precise, dense, keyboard-first) ·
11 **High contrast / WCAG AAA** (accessibility, also prints well).
Gates for every skin in both modes: quiet-when-OK holds (no colour on OK rows), WCAG AA contrast for text and status
shapes (measured), problem shapes stay distinguishable without colour.

## Shared seams (frozen once the foundation lands)
- `web/view/core.js`: API state → display rows; `IOStructures[id] = {name, rows(state, options)}`; options include
  `showTransport`, `showEmpty`, `query`.
- `web/tokens.css`: the semantic token list (`--io-bg`, `--io-surface`, `--io-text`, `--io-muted`, `--io-border`,
  `--io-accent`, `--io-warn`, `--io-error`, `--io-stale`, `--io-unknown`, `--io-font-ui`, `--io-font-mono`,
  `--io-radius`, `--io-row-h`, `--io-focus`, …); skins live in `web/skins/<id>.css` as
  `[data-skin=id][data-mode=light|dark] { … }`.
- Tree renderers and the table consume rows from the active structure, never the raw API tree.
