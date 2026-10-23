# IO diagnostics — round 3 brief (Zach's feedback, Daily Note 2026-10-23, "# feedback on Digoinastics")

Screenshots: /home/bam/zach_brain/Sources/Images/Pasted image 20261023081913.png … 20261023085904.png (18).

## Settled (lane R3-A builds these; they are constraints for every proposal)
1. **Resizable panes.** The tree|detail boundary and the table|inspector boundary are draggable. Widths are remembered (localStorage, try/catch), double-click restores the default, and every pane has a minimum width. Standing rule: Settings/Memory/feedback_resizable_panels.md.
2. **The inspector folds in the Table view.** By default the table gets the full width. Clicking a row slides the inspector in; clicking back into the table, Escape, or the inspector's close button hides it. A toggle (and a key, `]`) pins it open.
3. **P1 is the default, opened** (rows with children open; Computer stays closed).
4. **One View-options control,** like Claude Transcripts' filter panel (Pasted image …082249): a sliders button beside the search box opens a popover.
   - **Group by:** Devices (p1, default) · Type (p2) · Port (p4) · Bus (p5) · Physical (p0). P3 is dropped from the picker; Zach wants one row per entity, with detail living in the Table.
   - **Sort by:** Status (problems first) · Name · Last change.
   - The structure's own toggles (Ports, Empty ports).
   - Tree style, skin, mode.
   The bottom-right cluster shrinks to the skin/mode controls, or goes away if the popover holds everything.
5. **Two default skins.** **Bare Metal** is the high-contrast skin, renamed, and the default. **Pretty** is Notion, renamed. The other nine move under "More skins".
6. **Warning colours are reserved for diagnostics.** warn/error/stale/unknown colours (and their soft tints) appear ONLY on true diagnostic results.
   - Utilities lose their amber/red danger pills; danger becomes a neutral word or icon.
   - Preconditions that are not met read in muted text, not orange.
   - A token-usage gate in the test enforces this.
7. **Utilities, quieter.**
   - Each row is the icon + title; the description becomes a hover/focus tooltip.
   - While an action runs, its button greys out and quiet muted text sits under it ("Checking communication… 3 s", like the "received 08:55:30 · 1 s ago" line Zach liked).
   - No live streaming wall.
8. **Detail panel reads top to bottom.** Sections with collapsible headers (remembered per section), no card grid. Default width compact (Pasted image …085430).
9. **Activity is one full-width column.** The fault memory and link health leave this view: link health belongs to the Computer entity, and fault memory becomes a filter of the log.
10. **Self-held lock is not "blocked".** When the rack bus lock is held by THIS host's own running diagnose, a precondition reads "this host's own check is running (a few s)". It never reads "blocked by pid …" (host-side).

## Three sets of 5 proposals (after R3-A lands; live in the app, switchable bottom-right, variant 1 = default)
- **Diagnostics page** (what you see when you click an entity). First: the diagnostics status table (level · name · message), quiet by default, colour only on problems — what rolls up to the entity's level. Then:
  - row expand → values / metadata, plus sparklines;
  - row click → a page for that one diagnostic (drill: Computer → entity → diagnostic; entities can have children);
  - a health bar over a rolling window (rqt_robot_monitor, Pasted image …084557). Per-status timestamps make out-of-sync arrivals honest.
  - App-wide history window setting.
  - Reference for the Computer: the ROS2 System Webview (Pasted image …084333).
- **Utility menu.** Less at a glance, descriptions on hover, quiet busy state, no warning colours.
- **Bare Metal variants.** 5 more takes on the high-contrast "bare metal, terminal-like but readable" feel, applying ISA-101 high-performance HMI concepts (grey normal, colour only for abnormal, analog-indicator thinking, alarm priority by shape + position).

## CLI as the seam (Zach: "should all of the controls register themselves as CLI? then diagnostics never cares about any internal communication")
Yes, it already is one: a driver is an executable speaking `describe | diagnose | act`, and each utility runs a command line. Round 3 adds the stock **Monitoring Plugins (Nagios) API**: any command whose exit code 0/1/2/3 = OK/WARN/CRITICAL/UNKNOWN, with the first stdout line as the message and perfdata after `|`, is a diagnostic. No code is needed for simple checks.
