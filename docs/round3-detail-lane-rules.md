# Round 3 — diagnostics-page proposal lanes (d1–d5), shared rules

## Read first
- docs/round3-brief-2026-10-23.md;
- docs/round2-seams.md §6: `window.IODetailLayouts[id] = { name, description, order, render(container, detail, ctx) }` and the ctx building blocks;
- web/detail/d0.js (today's default);
- docs/api-v1.md: `/api/v1/node`, `/api/v1/history`, `/api/v1/history/bar`, `/api/v1/settings`; the history window is app-wide, default 15 min;
- docs/round2-lane-rules.md: run and shell rules. They still apply.

Zach's words (Daily Note 2026-10-23, "# feedback on Digoinastics"); screenshots in /home/bam/zach_brain/Sources/Images/Pasted image 20261023…:
- "I don't like having the utilities here front and center. What are the most important things to first show as soon as you click it? If this was diagnostics I would be expecting to see like a diagnostics status panel … it tells me specifically the diagnostics message" (…083756: ROS diagnostics).
- "Color coding is obviously essential. I prefer quiet by default and only call out the things with errors."
- "when we click it we should see the chart of the diagnostics status as that is what is rolling up to impact the top level entity".
- The ROS2 System Webview is a great viewer, e.g. for the Computer (…084333): CPU per core, memory, swap, load, network sparklines, USB bandwidth. "holding/storing the temporal information within some type of window so that we can optionally show the historical trend whenever we want … set a window for the entire diagnostics app … that could determine the length of this health bar" (…084557, rqt_robot_monitor). "that health bar is not completely straightforward as the diagnostics may not all arrive on the same synchronized step".
- "in this ideal of drill down I think the first thing you see on the entity is the table that shows the different diagnostics, their current status and the message. and then if you click it will maybe drop down and you can see the attached values etc or meta data … perhaps little spark lines? or if you click to expand into the diagnostic that should bring you to a page that really gets more in depth on that specific diagnostic. So the step down is computer -> entity -> diagnostic. I guess entities can have children."
- "I think the eye easily reads top to bottom not in these grid patterns … collapse header model … compact width … I like how quiet this updating text is" (…085331, …085430, …085552).

## Every proposal must
- show the entity's diagnostics status (level · name · message) as the FIRST thing, and say what rolls up to the entity's level, using `caused_by` / the API's hint;
- stay quiet when OK, with warning colours only on real diagnostic results (the warn-colours gate);
- give a history view over the app-wide window: sparklines from `/api/v1/history` measurements, and a health bar from `/api/v1/history/bar`. Never fabricate a step: honour "no data", stale and held levels;
- let you drill entity → diagnostic. A diagnostic's own page shows its message history, values and measurement trends, metadata (code, source, expected period, last received) and its what_to_check. The entity's children are reachable;
- keep the utilities available but not front and center (folded, at the end, or behind one quiet control). Use `ctx.parts.utilities` / the active IOUtilityMenus menu; never re-implement the dry-run flow;
- read top to bottom at the compact width (`--io-detail-max`), with no card grids;
- look right in both default skins, Bare Metal (`contrast`) and Pretty (`notion`), in light and dark;
- make the Computer (host) page good: it now carries 14 statuses with per-core CPU, load, RAM/swap, disk, temperatures, GPU, NTP, network and USB bandwidth measurements (plugins/computer).

## Your write set
- Exactly `io_diagnostics/web/detail/d<N>.js`.
- If you need CSS, inject one `<style data-layout="d<N>">` into `document.head` once, with classes prefixed `d<N>-` and tokens only, no colour literals.
- Do NOT edit `detail/index.json` (report the name you want), `app.js`, `app.css`, the tests, other lanes' files or Python.
- Five lanes run in parallel, plus one utility-menu lane (u1–u5).

## Data
- **Fixture servers:** `--fixture F1|F2|F3` on your own port.
- **A live Computer page:** you MAY run the read-only computer plugin alone, via `serve --plugins <scratch dir holding only a copy of plugins.d/computer.toml with an absolute command path>`. Never the moteus or RealSense drivers, and never :8820 (Zach's live server).

## Gates
All green; a failure in another lane's file is theirs, so report it:
- `/usr/bin/node /home/bam/io-diagnostics/tests/test_view_core.js`
- `/usr/bin/node /home/bam/io-diagnostics/tests/test_utilities.js`
- `/usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js`

## Proof
- Your own scratch check script: select your layout via the Prototype picker or its localStorage key; open an entity with a problem (F1: the 28B3A25D CAN bus, CAN 15) and the Computer; assert the status table comes first, a sparkline and a health bar render from the history API, drill-down to a diagnostic works, and no warn colours appear outside diagnostics.
- Screenshots: `reports/media/round3/d<N>-{entity,diagnostic,computer}-{bare,pretty}-{light,dark}.png`. Look at them.

## Report
- the idea and its precedent;
- the name you want in the picker;
- gate counts;
- screenshot paths;
- what you'd change in the seams.
