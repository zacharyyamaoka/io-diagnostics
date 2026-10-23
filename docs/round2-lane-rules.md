# Round 2 lane rules (structure and skin lanes, 2026-10-22)

Read first: `docs/round2-brief-2026-10-22.md` (Zach's feedback + the decisions), `docs/round2-seams.md` (the frozen
seams), and Zach's screenshots `/home/bam/zach_brain/Sources/Images/Pasted image 20261022144642.png`, `…144719.png`,
`…145139.png`. His words (Daily Note 2026-10-22, "# Feedback on thing"): the physical layers "obscure the actual
entities"; he thinks "fdcanusb → Moteus c1 - CAN 15, Moteus c1 - CAN 12"; a single computer is "an unnecessary fold";
between Quiet IDE (v1) and Notion (v2), and Quiet IDE's value is "you see which are folders at a glance"; the table
must match the tree; "see at a glance at the entities BUT also provide the detailed information if you want to see it";
his goal today: "I plug it in and now I need it to run".

## Your write set
- A **structure lane** writes exactly `io_diagnostics/web/view/structure/<id>.js` (a stub copy of p0 is there now:
  replace it). A **skin lane** writes exactly its `io_diagnostics/web/skins/<id>.css` files (stubs = copies of
  Claude). Both may write scratch scripts in their own scratchpad and screenshots to
  `/home/bam/io-diagnostics/reports/media/round2/<id>-*.png`.
- The manifests already list every stub as ready — do NOT edit `index.json`, `app.js`, `app.css`, `tokens.css`,
  `core.js`, any tree renderer, `index.html`, the tests, Python files or another lane's file. If the seams cannot
  express something you need, deliver the best version inside them and describe the exact seam you would add (field,
  where it is read) in your report — the orchestrator adds seams after the wave.

## Running it
- Fixture server (never the real hardware): `env -C /home/bam uv run --project /home/bam/io-diagnostics io-diagnostics serve --fixture F2 --offset 330 --port <free port> --no-udev`
  — also look at F1 and F3 (see `io-diagnostics serve --help` for `--fixture` / `--offset`). **Always pass
  `--fixture`**: the driver plugins are now enabled by default, so a `serve` without it talks to the real CAN adapter
  and cameras, which only the orchestrator may do. `--machine /home/bam/io-diagnostics/tests/data/sysfs-rack-fixture.json`
  adds a fake PCIe frame grabber, capture card and EtherCAT NICs (check that your structure handles them). Pick a free port
  (e.g. 19000 + a random offset); stop the server by the PID you started, never by port.
- A real live frame from the rack (2026-10-22 17:41, CAN bus error-passive, boards silent, two D405s) is at
  `/tmp/claude-1000/-home-bam-bam-ws--claude-worktrees-vibrant-jemison-a12441/56e62701-653a-41fa-b911-d154f3596ddd/scratchpad/live_once_1.json`
  (`.state` has the `/api/v1/state` shape): feed it to `IOView.build` in node as an extra check that live data reads well.
- Headless Chrome only: Playwright from `/home/bam/aruco-studio/web/node_modules/playwright`, `executablePath:
  '/usr/bin/google-chrome'`. Never open a window on Zach's screen, never the in-app browser pane.
- Gates that must stay green (run them, do not edit them): `/usr/bin/node /home/bam/io-diagnostics/tests/test_view_core.js`
  and `/usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js` (gates every ready skin: tokens complete,
  quiet-when-OK, shapes on the right). Several lanes run them at once; a failure in another lane's file is theirs —
  report it, don't fix it.
- **Look at your screenshots** (Read the PNG) before reporting; fix what looks wrong.
- Another lane is changing the Python host right now (API gains `identity.model_id`, danger `moves`, absent-device
  rows). Do not depend on those fields; do not break if they appear.

## Shell
A hook refuses `cd`, `env -u`, shell variables inside commands, and heredocs: use absolute paths and the Write tool
for scripts. No git writes (the orchestrator commits). No network fonts or CDNs: system font stacks only (it is an
offline local tool).

## Report back (concise)
Files written; what your structure/skin does in 3-5 bullets; gate results (exact pass counts); screenshot paths you
looked at; any seam you would add; anything that looked wrong that you could not fix.
