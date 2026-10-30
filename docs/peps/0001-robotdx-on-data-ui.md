# PEP 0001: RobotDx on data-ui — inventory, stock parts, and what data-ui must learn to show live data

- **Status:** proposed 2026-10-26 (phase 1: inventory and design; no product code). The live API is deliberately open (§10).
- **Asked by:** Zach, 2026-10-26: "reimplement robotdx using data-ui … upgrade data-ui to have these views, cards, etc. robotdx is
  basically yet again just like a dataset and viewer … AND we also want this ability to make dashboards. One big difference here is
  that it is LIVE data … I really like rerun … at any moment you can just kinda like 'rewind' … (rewind) I would say is my primary
  prior art reference. Things to think about here are performance… lets set that up as KPI for robotdx."
- **Read for this:** io-diagnostics main `ae938a4` (server.py, host.py, history.py, runner.py, web/**, docs/api-v1.md,
  state-config-v1.md, runs-v1.md, charts-seam.md), `lane/dashboard-views` (6a4e07b), data-ui main `c343fc8` (CONTRACT.md, PEP 0001,
  0003) and lanes `dashboards-land`, `dashboard-cards` (PEP 0005, clock.js, playback, timeseries), `types`, `perf-worker`,
  `analyses`; vault: *Tracking-Run Data — data-ui vs Rerun (2026-10-26)*. Numbers in §4 and §8 were measured on fixture F1 (§11).

## 1. The answer

- **RobotDx keeps its host and replaces its page.** The host (plugins, staleness, rollup, faults, gates, every write) stays as it is
  and gains one live feed; the page (19,400 lines of JavaScript) becomes one data-ui dashboard (`.base` file + `<data-dashboard>`).
- **The tree is a table, the entity page is a set of panels.** The tree is a `table` with `tree:` and `select: path`; Diagnostics,
  State, Configuration and Log are views over linked datasets that the selection filters, placed by `layout:` like any dashboard.
- **Live is a data-ui tier, built once.** Datasets that change while open, a retention window, and the dashboard Clock that follows
  live and rewinds when you scrub back (Rerun's model). RobotDx is its first user; Vibe Tracks runs and rack sessions are next.
- **Everything that writes stays RobotDx's own shell around the dashboard:** utilities, configuration edits, the watch heartbeat,
  rig-file edits, terminals. data-ui never sends a request to the host that changes anything.
- **Performance is a KPI from day one.** Today one open page costs 2.3 requests/s and 63 KB/s, and 82 % of those bytes are the
  once-a-second full-tree poll (§8).

## 2. What the ask assumes, and where it needs care

| The ask says | What the code says | So |
|---|---|---|
| "just like a dataset and viewer" | True for everything RobotDx *reads*. Its value is in the host: rollup, knock-on, staleness on the host's clock, fault debounce, gates, dry-run tokens, the watch lifecycle. | Rebuild the page, not the host. |
| "LIVE data" | Two kinds: rows that change **in place** (the tree, checks, config: replace by key) and rows that **grow** (State samples, events, level changes: append with a time). They need different retention and rewind rules. | The live tier supports both (§5a). |
| "rewind, like Rerun" | The host keeps 15 min of levels, messages and measurements, the event log and run output; it does **not** keep the tree's structure, presence, rollup or config values at past times, nor past camera frames (§5c4). | Rewind ships in two steps (§9, decision 3). |
| "upgrade data-ui" | data-ui has no live tier on main. Its nearest parts are unmerged: `refresh:` (whole-file re-fetch on an interval) and the Clock + playback card (recorded playback only), both on `lane/dashboard-cards`. | Build on those; never a second clock. |

## 3. Inventory and stock parts

Branches: **main** = data-ui main; **dl** = `lane/dashboards-land` (`<data-dashboard>`, PEP 0004; *not* on main, although PEP 0004's
status line says so); **dc** = `lane/dashboard-cards` (PEP 0005: cards, Clock, playback, `refresh:`); **tm** = `lane/toolbar-mix`;
**ty** = `lane/types`. "Shell" = stays RobotDx (§6). Feeds per element: §4.

| Element | Today | data-ui part (or MISSING) | Behaviour that must survive |
|---|---|---|---|
| E1 Top bar: clock, "Live · host" / "Replay F1 (recorded)", skipped OS sources | app.js `render()` | Shell | A replay says recorded; a skipped source is named, never implied absent |
| E2 Status chip + popover: counts, what needs you (worst first), Recent 5 | app.js `renderChip`, summary.js | `kpi` strip (main) + attention `table`; click a count to filter = `select:` (dl) | Counts in increasing severity, zero hidden; one cause counted once (host counts); "fdcanusb › CAN bus" naming |
| E3 Tray chip (GNOME AppIndicator) | tray.py, contract/indicator.py | Shell (outside the page) | Same model as E2 |
| E4 Tree / Table / Activity switch, back/forward drill | app.js nav (`sel, diag, page, view, into, inside` in the URL) | `<data-views>` / `<data-dashboard>` URL state (main, dl) | Every drill step is a browser-history entry; mouse back/forward work |
| E5 Search | app.js → view/core.js `applyQuery` | `<data-views>` search (main); toolbar (tm) | A match keeps its ancestors visible |
| E6 View options: Group by (structures), Sort (status, name, last change) | view/structure/p0–p7, core.js | Saved `views:` with `tree: {parent}`, `groupBy`, `sort` (main); host emits one parent column per structure | p6 Nested is the default; an entity appears once; a row that lost a child gets its hint recomputed (today in JS: MISSING b3) |
| E7 Page layout L1 (ribbon, tree pane with search on top), Ctrl-K palette | layouts/l1.js, palette.js | Shell; resizable panels (main §11) | Search on top of the tree; every boundary drags, remembers, resets |
| E8 Status bar "Last update N s ago" / "Host unreachable" | app.js `renderStatusbar` | MISSING (a9) | A host restart is one blip; panes keep their state |
| E9 Entity tree: presence dot, icon, "model · id", health shape, row ⋮ menu, header counts | tree/v6.js over p6 + composites.js | `table` + `tree: {parent}` (main §10), `select: path` (dl); MISSING level shape and presence dot (b2, b4) | A shape only for warn / unknown / error, nothing for OK; presence is a neutral dot, never a level; a collapsed row shows the worst below; knock-on rows subdued; expansion remembered |
| E10 Step into / Only inside (re-root + chips) | app.js, core.js `reroot`/`keepInside` | MISSING (b7) | In the URL; Ctrl+Shift+Backspace steps out |
| E11 Visibility: unclaimed rows hidden, "Show N hidden", acknowledge | visibility.js, `settings.visibility` | A view filter (main); MISSING "Show N hidden" row (b7); the choice is a write (Shell) | A hidden row never hides a fault; counts still include it |
| E12 Table: Name, ID, Message, Rate, Below, Last seen, Checks, Faults; folding inspector | app.js `renderTable` | `table` (main), `freeze`/`wrap` (ty), side peek in split mode (main §13) | Message keeps a minimum width; Rate is a quiet note ("0.20 /s · expected 0.20"), never a colour |
| E13 Rigs and composites nested in the tree, `↳ CAN 13` source links | p6.js, composites.js, d6.js `sourceLink` | `tree:` with the host's containment parent (main); `relation: true` + peek trail (main §14) | Members in the file's order; an absent child is a check on its parent, never a ghost row |
| E14 Entity header: name, model, level, badge (USB 2.1 / CAN 1 · fdcanusb), ticking "heard 2 s ago", hardware line, banners | d6.js `headerHtml` | `record` sections (main); MISSING ticking age (a8) | Always visible above the tabs; USB named by version (usb.js), raw speed in the tooltip |
| E15 Tabs: Diagnostics, State, Configuration, Log, Utilities, driver tabs; badges; remembered per entity kind | d6.js | A panel's `views: [...]` shown as tabs (dl); MISSING tab badges (b7) | Tab in the URL; keys 1–9, Ctrl+PgUp/PgDn; Diagnostics never shows values, State never shows health |
| E16 Diagnostics rows (d1a): Name, Message, Current, History; worst first; dot folders; pins | d1a.js, d1x.js, d6-rows.js | `table` + `tree: {path, separator: "."}` (main §10); MISSING level-strip cell and pins (b1, b7) | One line per row, full message on hover; order ok < warn < unknown < stale < error |
| E17 Health bar and one timeline lane per diagnostic | health.js, d1x.js | `status` card (dc) for one strip; `timeline` (main, vis-timeline) for lanes; MISSING as a cell (b1) | A bar is the worst level held in its bin; no data is an empty outline, never a level; bars 3 px with a 2 px gap, count from width |
| E18 Below: children with mini health bars, problems first | d6.js `childrenList` | `table` + level-strip cell (b1) | Problems first; the child's own shape and its hint side by side |
| E19 Faults, Link health, Rollup, Connection, What to check | app.js parts, d6.js | `table` / `record` (main); `display: markdown` for the checklist (main) | `what_to_check` lines as authored, in order |
| E20 Expected setup card (Expect / Forget / Retire) | app.js `setupBody` | Shell (writes the rig file) | The confirm shows the host's own sentence; nothing reaches a device |
| E21 State rows: Name, Value (+ meaning), 60 s sparkline; groups; "last values 40 s ago" | d6.js `statePanel` | `table` `display: sparkline` (main; values only, no time axis); MISSING live time-windowed sparkline (a1, a6) | "33 · motor driver fault on bridge enable", never a colour word; staleness said plainly |
| E22 Live note: "live · every 0.1 s" / "paused: tview has the port" / "not live" | d6.js `watchNote` | MISSING (a9) | Paused reason in plain words, never a warning colour |
| E23 Image tiles (MJPEG), on/off switch, fps, lightbox | d6.js `tile`, `/frames/stream` | `image` card (dc) with the MJPEG URL as `src`; MISSING frame history (b8, c4) | "waiting for the first frame"; fps shown; the switch is remembered |
| E24 Sparkline → unfold, focus page (zoom, pan, health strip on the same x axis, CSV), pin | charts/c6 = c2 + c5 + c4 | `timeseries` card (dc), `interactive` contracted, building on `lane/dc-plot`; level spans as `bands: x` | Limits dashed in the diagnostic colours; gaps where a status went stale; Back returns exactly |
| E25 Dashboard of pinned tiles (c4; the lane's D1) | charts/c4.js, `settings.dashboard` | `<data-dashboard>` `layout:` (dl) + Arrange (dc, gridstack) | Mixes entities; follows the person (host-stored), not one browser |
| E26 Configuration: search, folders, read vs expected (+ source), copy; edit session | d6-rows.js | Read side: `table` + `tree: {path, separator}` (main); MISSING value-vs-expected cell (b9); editing is Shell | `value ≠ stored` reads "not saved"; safety keys asked twice; nothing written without the session token |
| E27 Log tab: Activity scoped to the entity and below, Raw channel, fault ↔ Diagnostics links | activity/log.js `create({scope})` | `table` over events (main); MISSING subtree scope (b6) and live tail (a7) | Scope = the entity, everything below it, and rows naming its hardware id |
| E28 Utilities: menu, tab, dry-run sheet, confirm modal, run line | app.js, d6.js, utilities/u0.js | Shell | Every click dry-runs first; moves needs a ticked box; tokens single-use, 300 s |
| E29 Driver tabs (Processes: sortable tables) | d6.js `driverPanel` (TSV in a status value) | `table` (main), `format` per type (ty), if the host sends rows instead of TSV | Sortable; bytes and % formatted |
| E30 Runs panel: terminal tabs, typing, Stop, recording | runs/core.js, r1.js, xterm | Shell | Resume from the byte offset; Stop = interrupt, then escalate |
| E31 Activity view: folded rows, typed search, severity floor, hide/highlight, live tail, volume strip with a time brush, row detail, Raw, run output | activity/log.js, core.js | `table` + rail facets (`min-level`, `date`, `search`; main §7) + `chart` histogram (main); MISSING live tail (a7), time brush (b5) | One fact, one row (host folds); the tail pauses when scrolled or opened ("Paused · Resume (N new)"); search cannot lower the floor |
| E32 History window, depth colour map, arrangements | settings.py, arrangements.py | Window = the live tier's retention (a2); the rest are writes (Shell) | Window 1 min – 24 h, 15 min default; a colour-map change restarts the watches |
| E33 Prototype pickers and their variants | */index.json | Dropped (§6) | — |

## 4. Feeds: endpoint · shape · rate

Rates are from the code (constant and file named) unless marked *measured*. "Live kind": **snapshot** = replaced by key; **append** = grows
with a time; **blob** = latest file only.

| Feeds | Endpoint | Shape (fields that matter) | Rate | Retention · live kind |
|---|---|---|---|---|
| E1–E13, the runs list | `GET /api/v1/state` | `clock`, `summary {level, counts, attention[]}`, `chip`, `tree` (nested nodes: `path, name, kind, icon, parent, edge, level, message, own, rollup, hint, knock_on_of, stale, connected, hardware_id, identity, presence, rate, usb, claimed`, counts, `children`), `recent[6]`, `expected_setup`, `composition`, `runs[30]` | page 1 s (`app.js setInterval(tick, 1000)`); host recomputes every 2 s, 0.5 s while a plug is provisional (`host.py REFRESH_S`, `FAST_REFRESH_S`); *measured* 52.8 KB, 8 ms median, 50 rows | now only · snapshot |
| E14–E16, E19, E26, E28 | `GET /api/v1/node?path=` | `node`, `ancestors`, `checks[]` (latest per name: `level, effective_level, message, code, values, measurements, received, age_s, expected_period_s`), `actions[]`, `faults[]`, `events[≤20]`, `badge`, `heard`, `config[]` (`key, value, unit, expected, differs, writable, type, range, stored, safety, unsaved`), `watch`, `streams`, `tabs`, `setup`, `composite` | while selected, at most every 1.8 s (`app.js`; *measured* 30/min); 11–14 KB | now only (config: latest per key, plus `ConfigSet` events) · snapshot |
| E17, E18, E24, E25 | `GET /api/v1/history?path=[&status][&window]` | `statuses[{name, state, expected_period_s, levels[{from, to, level, code, message}], measurements[{key, unit, limits, points[[received, value]]}]}]` | charts 2 s, layouts 3 s, Diagnostics 4 s (`charts/core.js`, `app.js HISTORY_EVERY_MS`, `d1x.js`) | max(window, 15 min) in memory, 200,000 points, 4,096 series, State at 1 Hz (`history.py`); lost on restart · append |
| E17, E18 | `GET /api/v1/history/bar?path=&bins=` | `rows[{path, depth, basis, bar[level or null]}]`; bins = pane width ÷ 5 px | 3 s | derived from the row above |
| E21, E22, E23 | `GET /api/v1/values?path=&since=<cursor>` | `cursor`, `groups[{name, stale, age_s, rows[{key, value, unit, text, meaning, level, limits, source, points[[received, value, serial]]}]}]`, `streams[{name, width, height, seq, fps, age_s}]`, `heard`, `watch` | 250 ms while the watch runs, 1 s idle, 5 s when absent (`d6.js`); drivers stream 10 Hz (moteus `WATCH_PERIOD_S` 0.1) and 5 Hz (RealSense 0.2) | server ring 1,200 points / 120 s per value (`runner.py`); page ring 1,500 / 120 s (`d6.js`) · append, cursor per plugin job |
| E21–E23 | `POST /api/v1/watch {path}` | heartbeat → `watch {state, reason, holders, period_s}` | every 3 s while the State tab shows; the host stops the watch 10 s after the last beat (`WATCH_GRACE_S`) | **starts a driver process on the hardware** (Shell, §6) |
| E23 | `GET /api/v1/frames/stream?path=&stream=&fps=` | MJPEG (`multipart/x-mixed-replace`) | tiles fps 10, lightbox 15; the server looks every 40 ms, at most 30 fps (`server.py`) | latest frame only · blob |
| E2, E27, E31 | `GET /api/v1/activity[?fold=1&channel&path&scope&severity&limit≤5000]` | `events[{id, stamp, received, code, kind, type, severity, message, path, name, port, source, args, count, last_stamp, badges, members, channel}]`, `faults[]`, `link_health[]`, `log {matching, shown}` | shell every 4 s (`app.js`); Activity view on each SSE `activity` (+250 ms) and every 8 s, 2,000 rows (`log.js`); *measured* 13–19 KB | JSONL per boot, 20 MiB, survives restarts; folds the newest 8,000 (`eventlog.py`, `host.py`) · append |
| E31 | `GET /api/v1/events` (SSE) | `hello {revision, latest}`, `activity` (one row, keyed by `id`), `tick {revision, reason}` | changes coalesced 0.1 s, keepalive 15 s, a 10 Hz watch notifies at most 1 Hz (`server.py`, `host.py`); *measured* 8 ticks in 12 s idle | no replay after a reconnect (re-fetch) · push |
| E28, E30 | `GET /api/v1/runs`, `/runs/<id>`, SSE `/runs/<id>/stream?from=<byte>`, `/cast` | `output {offset, t, b64}`, `end` | live; the list rides on `/state` (1 s) | 2 MiB ring per run, 64 MiB of recordings on disk (`terminal.py`) · append, **already rewindable** |
| E11, E25, E32 | `GET/POST /api/v1/settings`, `/arrangements` | settings: `history_window_s`, `visibility`, `dashboard`, depth colour map | on load / on change | file · config |

**Upstream cadences** (what the rows can show): plugin `diagnose` every `period_s` (computer 10 s with a 1 s follower, intel_rgbd 60 s,
moteus_servo 300 s, fixtures 30 s); a check is stale after max(3 × its period, 5 s), judged on the host's receive clock.

**Measured baseline, one open page** (fixture F1, 50 rows, GET only): 2.2–2.35 requests/s and 63–64 KB/s in every view, 52 KB/s of it
`/state`; the host's CPU goes from 0.3 % to 1.1 % of a core; the page's main thread is busy 1.7–3.4 %, JS heap 4–6 MB, 5,500–15,400 DOM
nodes; page weight about 592 KB gzipped (every JS and CSS file, xterm 121 KB of it). The State tab was not measured (its heartbeat is
a POST, which this measurement refuses to send).

## 5. MISSING

### (a) Live tier primitives (data-ui core; the live lane's scope)

1. **Live dataset.** Rows that change while the page is open, in both kinds: replace by key (entities, checks, config, with removal
   when an entity leaves) and append with a time (samples, events, level changes). Today `el.data = …` replaces the dataset and
   recomputes everything; `<data-dashboard>` reads only files; `refresh:` (dc) re-fetches a whole file and skips it if unchanged.
2. **Retention.** A per-dataset window (time and/or count), oldest dropped first, memory bounded. Default: the host's history window.
3. **A clock that follows live.** The Clock (dc, `clock.js`) gains a growing end and a *follow* state: following keeps the cursor on the
   newest data; a seek, scrub, zoom or pause stops following; a Live button returns. Today the span is declared or the union of
   the cards' extents, and play loops or stops at the end.
4. **Rewind on the playback bar.** The track spans the retention window ending at now; scrubbing back moves every panel. The
   `rerun` / `rerun-streams` playback variants (`lane/dc-pb`) are the starting point.
5. **"As of t" for every view, not only plots.** Rerun's *latest-at*: for each row key, the newest value at or before the cursor.
   Tables, cards, KPIs and the record must show the cursor's moment; plots and logs use the range up to it. Nothing does this today.
6. **Re-render on append.** Views receive what changed (added, replaced, dropped) and update in place; at most one draw per animation
   frame however many rows arrive; hidden panels and tabs skip; the time series appends through Vega's change sets (or uPlot, decision
   6) instead of rebuilding (`timeseries.js` rebuilds every series from all rows and swaps the whole data set on each update); the
   row model is not recomputed whole per append (the perf-worker lane's 5,000-row worker threshold will be crossed by the event log).
7. **Live tail for table and list.** Newest on top, following while at the top, paused when scrolled or a row is opened, "N new ·
   Resume". RobotDx's Activity log already behaves this way (`activity/log.js`): copy its rules.
8. **Time-derived cells.** "heard 2 s ago" changes with no new row; a now-tick redraws ages. Staleness itself stays the host's call.
9. **Connection state per live source:** live, paused (and why), reconnecting, unreachable, and what a reconnect re-reads.

### (b) Views and cards

1. A **level-strip cell** in a table (a health history per row, Statuspage bars, no data as an outline). `status` (dc) is a card only.
2. A **level display**: a shape for warn / unknown / error and nothing for OK, the hint beside it, knock-on rows subdued, and levels
   ranked ok < warn < unknown < stale < error for sorting. `select` / `status` types (ty) have options and colours, no rank.
3. A **worst-by-rank roll-up** up a page-side tree (dl's `rollup:` sums; it needs Max by rank). Only for regroupings: the host's
   rollup stays the truth.
4. A **presence dot** (heard, not heard lately, streaming now), neutral colours only.
5. A **time-range brush that filters** other panels (the Activity volume strip, a plot). `select:` (dl) filters by value only.
6. A **subtree scope** for a link or filter: an entity, everything below it, and rows naming its hardware id. `links:` are equality.
7. **Pinned rows and manual order** per entity kind, a "Show N hidden" row, **tab badges**, **step into** and **inside X** chips.
8. **Frame scrubbing.** The `image` card shows an MJPEG URL live today; going back needs frames kept (host, c4).
9. A **value-vs-expected cell** for configuration: read value, expected value, its source, "not saved".
10. The **interactive time series** (zoom, pan, cursor, CSV): contracted on dc (`timeseries.interactive`), not built (`lane/dc-plot`).

### (c) Host seams (how the stdlib server feeds a data-ui live dataset)

1. **No dataset-shaped endpoint.** The page reads a nested tree and per-entity objects; data-ui wants `{properties, rows}` per record
   kind. Cheapest first step: the host serves each kind as a dataset URL and the `.base` uses `refresh:` (dc), with today's polling cost.
2. **No delta feed.** The 1 s full-tree poll is 82 % of the bytes, and every `/state` request rebuilds the whole view (`host.py`
   `view()`: merge, rig evaluation, summarize; nothing cached), so the host's cost is open pages × tree size. `host.revision` and the
   `host.changes` condition already exist and drive `/api/v1/events`; SSE is proven twice (events, run output).
3. **Cursors are per source.** `/values` counts per plugin job (`JobCache.serial`); `/events` replays nothing (no `since`,
   `Last-Event-ID` ignored); only run output resumes by byte offset.
4. **Rewind coverage today.** Kept: status levels and messages per span, measurements (15 min, in memory, State thinned to 1 Hz),
   events (the whole boot, on disk), run output (on disk). Not kept: the tree's structure, presence, rollup and knock-on at a past
   time; config values at a past time (only `ConfigSet` events); status `values` text such as `what_to_check`; frames (latest only);
   State at 10 Hz beyond 120 s.
5. **Going stale is a change with no arrival.** A delta feed must push it (the refresh loop sees it every 2 s).
6. **Live State needs a page's heartbeat.** `POST /api/v1/watch` starts the driver's watch on the device; a non-page client cannot send
   it (the dashboard-views lane hit this with its bridges). The data-ui adapter must not own it.
7. **stdlib limits.** `ThreadingHTTPServer` holds one thread per open stream: fine for SSE (one long GET per page). WebSocket is not in
   the standard library (hand-written framing or a dependency, against "the host stays stdlib-only"); writes are rare POSTs anyway.
8. **Three clocks.** Host epoch seconds (`received`), a fixture replay clock (1× from the recorded frame), and data-ui's clock (seconds
   from a start). The live time axis must be the host's clock.

## 6. What stays outside data-ui, and what is dropped

**Stays RobotDx's own (hardware-safe by construction):** the host process and its judgement (plugins, runner, staleness, rollup,
knock-on, faults, roster, expected setup, composition, gates); every write: `POST /api/v1/act` (utilities, `all:<action>` batches,
`reset_port`), `/config/edit|set|persist|reset`, `/watch`, `/runs/<id>/input|signal|resize`, `/expect|forget|retire`, `/settings`,
`/arrangements`; the runs terminal; the Utilities menu, dry-run sheet and confirm modal; the Expected setup card; the tray chip; the
Stable / Preview chip; the L1 shell and Ctrl-K palette; the Rerun / Lichtblick bridges. **Rule:** data-ui views never POST to the host;
a click on an action or a setting raises an event the RobotDx shell handles. `host.propose` / `acceptEdit` are never wired to a device.

**Dropped (defaults Zach already picked stay):** tree styles v1–v5 (v6 stays); structures p0, p2, p3, p4, p5 (p6 Nested, p1 Physical,
p7 Type become saved views); detail layouts d0–d5 and d1b–d1e (d6 + d1a stay); utility menus u1–u5 (stubs equal to u0); run views
r2–r5 (r1 stays); chart views c1, c3 (c6 = c2 + c5 + c4 stays); page layouts L2–L5 (L1 stays); 15 of 16 skins (Zach, 10-23: "just
use stock shadcn"; data-ui's tokens default to shadcn's names); the bottom-right Prototype picker; the lane's D1 board store
(`/api/v1/dashboards`) and c4's `dashboard` setting (read once to migrate pins into the `.base` layout); the 1 s full-tree poll.

## 7. `lane/dashboard-views` (D1–D5)

Unmerged: 4 commits on merge-base `53704f4`, 34 behind main; its `web/charts/core.js` differs from main's, so a whole-lane merge
conflicts. Its own report recommended D5.

| Proposal | Fate | Why |
|---|---|---|
| D1 Own board with Views (tiles, `$sel`, export / import, `/api/v1/dashboards`) | Superseded by `<data-dashboard>` + cards | One board, data-ui's. Reuse its tile list as the card checklist, `$sel` as `select: path`, and `IODash.live` (many entities' heartbeats + cursor polls) as the RobotDx side of the live adapter |
| D2 Rerun embedded · D3 Lichtblick embedded · D5 our board + Rerun pane | Superseded | Zach, 10-26: our layers are owned and "Open in Rerun" is the backdoor; Rerun's web viewer is 51.8 MiB of wasm |
| D4 Open in… + bridges (Rerun 0.40, Lichtblick 1.30, poller, projection table) | Reuse as the backdoor | Cherry-pick `io_diagnostics/bridges/` and its tests when wanted. Pin one Rerun version: pidtuna links 0.26, and an `.rrd` from 0.40 will not open there |

**In-flight data-ui lanes this collides with:**
- **The Clock has one writer** (PEP 0005 §4, on dc: unmerged, 22 behind main). `lane/dc-pb` (playback variants, `rerun` among them)
  and `lane/dc-plot` (the interactive plot, uPlot benchmark) sit at the cards tip with nothing committed. The live mode (a3–a5) edits
  the same `clock.js` and playback card: agree the Clock's live fields first, then build; never a second clock.
- **CONTRACT numbers are contested:** §17 is claimed by both dl and ty, §19 by `lane/analyses` and `lane/value-sources`; §15 is
  reserved for tm and §18 is dc. The live amendment takes the next free number once those land (§20 at the earliest).
- **data-ui PEP numbers:** 0001–0005 and 0007 exist across branches; 0006 is cited by PEP 0004 and exists nowhere. The live tier's PEP
  takes 0008 unless 0006 is confirmed free.
- **`<data-dashboard>` is not on main** (dl is 8 ahead and 8 behind); the RobotDx page waits on it.
- **`lane/perf-worker`** moves the row model to a worker at 5,000+ rows; a live tail must not re-send 5,000 rows per append (a6).

## 8. Performance KPIs (measure before and after every step)

Baselines are today's page on fixture F1 (§11). Targets are proposals for Zach.

| KPI | How measured | Today | Proposed target |
|---|---|---|---|
| K1 Live latency: host receives a record → it is drawn | stamp in the record vs `requestAnimationFrame` after draw | not measured; bounded by the 1 s poll for the tree (plus the host's 2 s refresh for OS facts) and the 250 ms poll for State | p95 ≤ 250 ms for State at 10 Hz, ≤ 500 ms for a check |
| K2 Bytes per second, one idle page | headless Chrome on the page, counting `/api/` requests and bytes (§11) | 63–64 KB/s, 2.3 requests/s | ≤ 8 KB/s and ≤ 1 request/s idle (a delta stream) |
| K3 Host CPU per open page | `/proc/<pid>/stat` | +0.8 % of a core (50 rows) | flat in page count; grows with changes, not with tree size |
| K4 Page main thread, steady | CDP `TaskDuration` | 1.7–3.4 % (no State tab) | ≤ 10 % with 20 series at 10 Hz and 2 image streams |
| K5 Scrub: cursor moved → every panel drawn at t | event → last panel's draw | n/a | ≤ 50 ms over a 15 min window (60 frames/s while dragging) |
| K6 Memory at full retention | JS heap after 15 min | 4–6 MB (nothing retained) | ≤ 150 MB at 20 series × 10 Hz × 15 min (180,000 points) |
| K7 Cold load to the first useful paint | navigation → tree drawn | not measured; 592 KB gz of page code | ≤ 1.5 s on this PC; data-ui core 82 KB gz + table 12 KB, chart 302 KB loads only when a plot shows |

## 9. Open decisions for Zach

| # | Question | Recommendation | Default if you say nothing (reversible) |
|---|---|---|---|
| 1 | Rebuild the host too? | No: the page only; the host gains a feed | Page only |
| 2 | Where does the live tier live? | data-ui core, on `lane/live` (created 23:31 today at data-ui main, empty); start it from `lane/dashboard-cards` instead, so it extends the one Clock | That |
| 3 | What does rewind cover first? | Step A: what the host keeps (levels, messages, measurements, events, run output) plus the page's own 10 Hz rings; the tree's structure as of t comes in step B, once the host records the row states it computed (it already computes them every 2 s) | Step A |
| 4 | Retention? | One window, the host's history window | 15 min; 10 Hz for the last 2 min, 1 Hz before (today's server behaviour) |
| 5 | Transport? | SSE for the feed, POST for writes; WebSocket only if §10 finds a need | SSE |
| 6 | Chart engine for live plots? | Benchmark Vega-Lite change sets against uPlot (already a dependency on dc) on K4; Vega-Lite stays unless it misses. PEP 0001 of data-ui says one engine, so this is a durable call | Vega-Lite |
| 7 | Drop the variants in §6? | Yes; today's page keeps running until the new one reaches parity | Drop |
| 8 | Migration path? | The data-ui page at a second URL on the same host until parity, then switch | Side by side |
| 9 | KPI targets | As proposed in §8, measured by a script in this repo; on Vibe Tracks when you say | As proposed |

## 10. Live API (pending prior-art note)

**Placeholder.** The live API is designed after the prior-art note another agent is writing. Until then nothing below is decided.
The note and this section must answer:

1. How a host hands data-ui a live dataset (a source object to subscribe to, or rows pushed in), keeping "views never fetch".
2. What is recorded for rewind: the host's computed row states (so the page never re-implements rollup), or its inputs.
3. One ordering number across sources, `since=` and reconnect semantics.
4. Latest-at and range queries: computed in the page, on the host, or split by retention.
5. Follow / pause / rewind controls and keys, copied from Rerun's time panel (and how a table's live tail joins the same clock).
6. Retention, memory limits and what is thrown away first.
7. Frame budget and back-pressure for 10 Hz × N series and image streams.
8. Which clock the axis shows (§5c8).

## 11. Rules for every agent on this work, and how the numbers were taken

- **Fixtures only.** `plugins.d/moteus_servo.toml`, `intel_rgbd.toml` and `computer.toml` are `enabled = true` (the README's "both
  shipped driver plugins are enabled = false" is out of date): any `serve`, `once` or `act` without `--plugins <fixture dir>` runs the
  real drivers against the CAN adapters and cameras, and `--enable` turns their active checks on.
- **Never POST.** `/act` (including `reset_port`, `calibrate`, `set_id`, batches), `/config/*`, `/watch` (it starts a driver process on
  the device), `/runs/<id>/input|signal` (types into a vendor console), `/settings` (a colour-map change restarts watches),
  `/expect|forget|retire` (the rig file), `/arrangements`. The page-only headers can be forged from a script; the rule is ours to keep.
- **Leave Stable (8820, backend 8822) and Preview (8821) alone**, and never edit the main checkout: Preview runs it live and restarts
  on every `.py` save.
- **Measured here:** `IO_DIAGNOSTICS_CONFIG_DIR` and `IO_DIAGNOSTICS_STATE_DIR` set to scratch folders,
  `cli.py serve --fixture F1 --plugins tests/fixtures/plugins-state --port 5491 --no-udev`; `/api/v1/*` sampled with GETs; the page
  loaded in headless Chrome (Playwright) for 20–30 s per view with every non-GET request aborted (none was attempted); host CPU from
  `/proc`; the server stopped by its recorded PID. Not observed: a real rack's row count, a running watch, cold-load time.

## 12. Phase 3: the page on data-ui (`web/dx/`), its contract

*2026-10-27, lane/robotdx-data-ui. Written before the code (the lane's rule: contract, then acceptance checks, then code).
It builds on data-ui's live tier (CONTRACT §21, PEP 0008) and app rail (§22, PEP 0009), vendored from lane/live with
lane/app-rail merged in; nothing here changes `/api/v1` or record v0.2.*

**What it is.** A second page on the same host, `/dx/index.html` (today's page at `/` gets no edits; default: both
ship, Zach decides later whether it replaces the old one). It only reads: every write the host offers (§6) stays on
today's page, which the new one links to for the entity on screen.

| Part | What it is | Stock part |
|---|---|---|
| Shell | `<data-rail id="dx-rail">` (three styles, setting in Settings, Ctrl+Alt+R) beside the destination; the time bar and the connection line along the bottom | `<data-rail>`, `bindRailSelect` |
| **Explore** (`?go=explore[&sel=<path>]`) | the entity tree (`<data-views id="dx-tree">`: a `table` with `tree: {parent}`, search on top, a side pane that drags, remembers, resets and folds) and the selected entity as a dashboard (`<data-dashboard id="dx-detail">` from `dx/explore.base` with `{path}` filled in) | `<data-views>`, `<data-dashboard>`, `makeResizable` |
| **Activity** (`?go=activity`) | `dx/activity.base`: the event log as a live tail, the faults, what needs you | `<data-dashboard>`, the tail |
| **Dashboards** (`?go=dashboards[&board=<id>]`) | the starter dashboards (`dx/dashboards/*.base`, listed in `dx/dashboards/index.json`) and your own; New, Add panel, Edit .base, Arrange, Save as file, Copy layout, Load .base (picker or drag and drop), Delete | `<data-dashboard>`, Arrange (gridstack) |
| **Runs** (`?go=runs`) | the host's runs as a table (no terminal: §12.6) | `<data-views>` |
| **Settings** (`?go=settings`) | the sidebar style, light / dark / system, the host's history window (read only) | `bindRailSelect` |

**One clock for the page** (Rerun has one time panel). `window.robotdx.clock` is a `LiveClock`, bound to every live
dataset the page holds; it sits on the app element (`#dx-app.clock`) so every `<data-views>` finds it with `clockFor`,
and every `<data-dashboard>` the page opens is given it (`dash.clock = clock` before `load()`). Rewinding in Explore and
opening a dashboard shows the same t. The time bar `#dx-time` (a `<data-views>` holding one `playback` view) shows
unless the open dashboard has a `playback` panel of its own; `data-live-status#dx-status` is the connection line. Keys
outside a dashboard are the dashboard's: Space, End and Ctrl/Cmd+Shift+→ (go live), Home and Ctrl/Cmd+Shift+←, ← / →.
The URL is `/dx/index.html?go=<destination>[&sel=<path>][&board=<id>]#t=live|<seconds>`: a destination or a selection
is a history entry (Back and Forward walk them, E4); t is replaced in place.

### 12.1 Datasets: what the adapter (`dx/feed.js`) makes of `/api/v1`

Every row's time `t` is the **host's** clock (§5c8), never the page's. Schemas (properties, labels, units) are the
files `dx/datasets/<name>.json` (rows empty), so a `.base` names them like any data file. **A binding** (word coined
here) is how a `.base` dataset reaches the adapter: its `data:` file name says the kind and its query says the entity
(`datasets/samples.json?path=host/usb1/port-1-3/usb-1-3/can/1`). The static server ignores the query, so the file still
loads as a plain schema; the page hands `<data-dashboard>.load()` the adapter's own `LiveDataset` for it, one per kind and
entity, shared by every view on the page (so history survives switching destinations). A `.base` dataset that is not a
binding loads as data-ui loads it.

| Dataset | Kind (§21) | Key / stream | Fields | Fed by | How often |
|---|---|---|---|---|---|
| `entities` | keyed | `path` | `path parent name kind icon depth health level rank message below kind model serial firmware model_id id_text hardware_id presence link rate heard checks actions faults knock_on connected stale claimed owner` | `GET /state` (the tree, flattened); a version only when a row changed; a row that left is removed | on each SSE `tick`, at most every 2 s, and every 5 s without one |
| `summary` | keyed | `host` | `level health needs errors warns unknowns stales devices entities chip` | `GET /state` `summary`, `chip` | as `entities` |
| `attention` | keyed | `path` | `path name device level health rank message` | `GET /state` `summary.attention` | as `entities` |
| `runs` | keyed | `id` | `id label state active started elapsed exit_code` | `GET /state` `runs.items` | as `entities` |
| `events` | append | `id` | `id time severity type code kind message path name port source count` | `GET /activity?limit=500` once (the events inside the host's window), then SSE `/events` `activity`; a provisional plug is left out and its confirmation kept (an append dataset cannot replace a row, and one fact stays one row) | pushed |
| `faults` | keyed | `id` (`path#code`) | `path name code severity status active count first last message` | `GET /activity` `faults` | on a `tick`, at most every 4 s |
| `checks` | keyed | `id` (`path#name`) | `path entity name health level said rank message code received period source why` | `GET /node?path=` of each watched entity; once, its past from `GET /history` level spans (level, message, code; no-data spans as removals) | 2 s while watched |
| `config` | keyed | `id` (`path#key`) | `path key value unit expected differs source stored unsaved writable` | `GET /node?path=` | as `checks` |
| `levels?path=P` | append, stream `check` | `check` | `check level health code message` | `GET /history?path=P` (the window, then `window=60s`; a row per level span, `null` = no data) | 4 s while watched |
| `samples?path=P` | append, stream `key` | `key` | `key value unit meaning group` | `GET /history?path=P` State series (1 Hz, the window) as the backlog, then `GET /values?path=P&since=<cursor>` | 250 ms while the watch runs, 1 s otherwise |

- `health` is a shape and a word for warn / unknown / stale / error (`▲ warn`, `? unknown`, `◷ stale`, `✖ error`) and
  empty for OK and for nothing reported (E9: a shape only for a problem); `rank` orders `ok 0 < warn 1 < unknown 2 <
  stale 3 < error 4`, `-1` for no level; `presence` is a neutral word (`heard`, `last heard 6 min ago`, `streaming`).
- **Watched entity** (word coined here): the entity Explore shows, and every `?path=` a panel of the open dashboard draws (a dataset a `.base` declares but no panel reads is not watched). A **peek** (`use('peek', path)`) reads an entity's `/node` and `/values` to learn what it has (its State keys, its settings) and never sends the heartbeat. Its feeds
  start when the first view needs it and stop 10 s after the last one leaves; nothing polls while the tab is hidden
  (SSE stays open). `/state` is re-read on a `tick`, not on a 1 s timer (today's 82 % of the bytes, §8).
- Retention: the page's buffer is data-ui's (256 MB); `samples` keep 15 min (`age`), the keyed ones their newest
  version per key and 15 min of history. Staleness stays the host's call: the datasets carry `stale: 0` and the page
  sets their connection state (`live`, `reconnecting` while the host is unreachable, the fixture's name as the detail).

### 12.2 Requests (checked: every request a test sees is matched against this list)

- `GET` the page's own files (`/dx/**`, `/vendor/data-ui/**`, `/usb.js`) and `/api/v1/state`, `/node`, `/values`,
  `/history`, `/activity`, `/events` (SSE), `/settings`, `/health`.
- **`POST /api/v1/watch {path}`, the only write**, for an entity whose State is drawn by a panel on screen while the
  page is visible, every 3 s (today's State tab heartbeat; the host stops the watch 10 s after the last one). `dx/watch.js`
  is the only code that sends it. Nothing else is ever sent: no `/act`, `/config/*`, `/runs/*/input|signal|resize`,
  `/settings`, `/expect|forget|retire`, `/arrangements`.

### 12.3 Dashboards: files, your own, saving

- A dashboard is a data-ui `.base` with `layout:` (PEP 0004/0005 grammar: `layout`, panels, `clock:`, the `playback`
  card, `plot`, `levels`, `kpi`, tables). Starters are files; yours live in this browser (`localStorage`
  `robotdx.dx.boards`: `[{id, title, text, updated}]`, every access in try/catch) until the host has a place for them
  (§12.6). **Save as file** downloads the `.base` text (with the arrangement as its `layout:`), **Copy layout** puts the
  same text on the clipboard, **Load .base** reads a file you pick or drop on the page and adds it to yours.
- New dashboard starts from a starter or empty; **Add panel** appends a view and a layout panel to the text (a plot,
  levels, KPI cards or a table of one entity, or the playback card); **Edit .base** is the text itself, applied when it
  parses. Arrange (data-ui's) writes the arrangement back into your dashboard's text.

### 12.4 The page's test surface

`<data-rail id="dx-rail">`, `main#dx-main[data-dest=<destination>]`, `#dx-tree` (rows carry `data-id` = the path), `#dx-tree-pane`
(the side pane: `data-resizable="right"`, key `dx-tree`) and its fold button `#dx-tree-fold` (outside the pane, so it can bring
it back), `data-dashboard#dx-detail` (Explore's entity; each panel's `<data-views>` is `#dx-detail-<panel key>`:
`diagnostics`, `configuration`, `signals`, …), the open dashboard `data-dashboard.dx-board` (`id="dx-board-<board id>"`, so
data-ui keeps each dashboard's own arrangement), `#dx-time`, `#dx-status`; on Dashboards the list items
`[data-board-id][data-board-kind=starter|mine]`, the bar's buttons `[data-board-act=new|duplicate|add|edit|save|copy|load|delete]`,
the file input `#dx-load-input` (the page also takes a `.base` dropped on `#dx-main`), and the Add panel form
`dialog#dx-add-panel` (`select[name=type]`: plot, levels, kpi, table, playback; `select[name=entity]`: entity paths;
`select[name=field]`: that entity's State keys; `[data-add-panel=ok]`); on Settings `select[data-setting=rail]` (the
sidebar style, `bindRailSelect`) and `select[data-setting=theme]`. `window.robotdx = {clock, feed, boards, parseBase,
version}`: read-only handles (`feed.datasets()`, `feed.dataset(kind, path)`, `feed.stats()` counts requests and bytes per
endpoint). Delete asks with the browser's `confirm()`.

### 12.5 Performance (the §8 KPIs this page is measured on)

K2 (bytes and requests per second, one idle page), K4 (main-thread busy, steady), K5 (scrub to drawn), K7 (cold load to
the tree drawn), measured by the page's checks in headless Chrome on fixture F1 and reported beside today's page.

### 12.6 Not in this phase (the full list by E-number is "Dropped / deferred" below, once built)

Every write (utilities, configuration edits, expected setup, settings, run input): today's page. Host-stored
dashboards (`/api/v1/dashboards` does not exist; the RobotDx session was asked). The runs terminal (xterm). Frame history.

## 13. Phase 3 as built (2026-10-27): dropped / deferred, measured, asks

The page is `web/dx/` (§12), on `lane/robotdx-data-ui`, not merged; today's page is untouched and keeps every write. Its
checks are `tests/test_dx_page.js` (58, measured), its media `docs/record_dx.js`, its launcher `tools/dx_demo.py`.

### Dropped / deferred

By element (§3). **Built** = on the new page; **deferred** = not in this phase, with what it waits for; **classic** = stays
on today's page (a write, §6), linked from Explore ("Open on the classic page" for the entity on screen).

| E | Fate on the new page | Why / waits for |
|---|---|---|
| E1 | Built: the connection line says "Live · replay of F1 (recorded) · N sources · last data N s ago"; the time readout is the host's clock. **Deferred:** the skipped OS sources (`host.sources`). | A small summary panel; not asked for this phase |
| E2 | Built: Overview (counts, what needs you worst first, faults, newest events) as Explore's empty state and a starter. **Deferred:** a click on a count filters; counts in increasing severity with zeros hidden. | data-ui `select:` on a KPI card |
| E3 | Unchanged (the tray, outside the page). | — |
| E4 | Built: destinations, selection and dashboards in the URL, each a history entry; Tree / Table as view tabs. | — |
| E5 | Built: data-ui's search on the tree. **Not verified:** that a match keeps its ancestors visible in a tree view. | a check in data-ui's tree search |
| E6 | Partly: data-ui's group and sort menus on the tree. **Deferred:** structures p1 Physical and p7 Type as saved views. | the host to send one parent column per structure |
| E7 | Built: `<data-rail>` (three styles, Ctrl+Alt+R, resizable wide) and the tree pane (drags, remembers, resets, folds), both measured. **Deferred:** the Ctrl-K palette. | the rail's `data-rail-search` event is there to hang it on |
| E8 | Built: "last data N s ago" (a summary version per `/state` answer), "Reconnecting · try N". | — |
| E9 | Built: the nested tree, a shape and a word for warn / unknown / stale / error and nothing for OK, Below (the host's hint), ID, model, presence as a word. **Deferred:** icons, knock-on rows subdued, expansion kept across reloads, header counts, the row menu (classic: writes). | data-ui level display (b2) and presence dot (b4) |
| E10 | **Deferred:** step into, only inside. | data-ui b7 |
| E11 | Built: unclaimed rows hidden unless they report a problem (a view filter). **Deferred:** "Show N hidden"; acknowledge (classic: a write). | data-ui b7 |
| E12 | Built: the Table tab (name, health, message, ID, rate, last heard, checks, faults). | — |
| E13 | Built where the host nests (the tree's `parent`). **Not verified** on a composite (the dx fixture has none). **Deferred:** `↳ CAN 13` source links. | a composite fixture in the dx checks |
| E14 | Built: the Entity panel as of t (name, health, message, below, model, ID, firmware, USB version, presence, last heard as a clock time). **Deferred:** the ticking "heard N s ago", the badge, banners. | data-ui's new `display: age` measures against the browser's clock and reads 6 days on a replayed fixture (ask below) |
| E15 | Changed: the entity's sections are panels side by side, not tabs; State and Signals have view tabs. **Deferred:** tab badges, the tab remembered per entity kind, keys 1-9. | — |
| E16 | Built: Diagnostics worst first, rewindable over the host's window. **Deferred:** dot folders, pins, the level-strip cell (the Health card shows the same history beside it). | data-ui b1, b7 |
| E17 | Built: the `levels` card, one strip per check, worst level per bin, no data an empty outline. | — |
| E18 | **Deferred:** children with mini health bars. | a level-strip cell in a table (b1) |
| E19 | Partly: faults (Activity, Overview), what to check (a Diagnostics column). **Deferred:** link health, the rollup and connection sections. | — |
| E20 | Classic (writes the rig file). | §6 |
| E21 | Built: State as KPI cards (value, 30 s spark, "as of" t) and a State table (latest per key with its meaning). **Deferred:** "last values 40 s ago" on a stale group. | — |
| E22 | **Deferred:** the live note ("paused: tview has the port"); the feed reads the watch state. | a small panel or the connection line per entity |
| E23 | **Deferred:** image tiles (MJPEG). `/api/v1/frames/stream` is outside this phase's request list. | data-ui's `image` card takes the URL; add it with the frames endpoint to §12.2 |
| E24 | Partly: the Signals plot (drag to zoom the window, click to seek, rewind). "Keep as a dashboard" and Add panel are the pin. **Deferred:** CSV, the focus page. | data-ui's interactive timeseries (dc-plot) |
| E25 | Built: Dashboards, yours in this browser and as `.base` files. **Deferred:** stored on the host (follows the person). | `/api/v1/dashboards` (ask below) |
| E26 | Built: the read side (dot-notation folders, value, expected, differs, source, not saved). Editing: classic. | §6 |
| E27 | Built: the Log panel (the entity and everything below it), rewindable. **Deferred:** rows naming its hardware id, the Raw channel, fault ↔ Diagnostics links. | data-ui b6 |
| E28 | Classic (utilities: dry run, confirm, tokens). | §6 |
| E29 | **Deferred:** driver tabs (Processes). | the host to send rows instead of TSV in a status value |
| E30 | Runs as a read-only table; the terminal and Stop: classic. | §6, xterm |
| E31 | Built: the event log as a live tail ("N new · Resume") with a rail (severity, type, entity), faults and what needs you, rewindable. **Deferred:** folded rows (`fold=1` badges and members), the volume strip with a time brush, the Raw channel, run output, the boot's history beyond the host's window. | data-ui b5; a row that replaces by id in an append dataset |
| E32 | The window shown (Settings). Edits: classic. | §6 |
| E33 | Dropped (as §6). | — |

### Measured (fixture F1, headless Chrome 1400×900, this PC under other agents' load)

| KPI | Today's page | The new page | Target | How |
|---|---|---|---|---|
| K2 bytes, requests per s, idle (Explore, nothing selected) | 54.7-57.9 KB/s, 2.1-2.3 req/s | **26.0-27.6 KB/s, 0.73-0.75 req/s** | ≤ 8 KB/s, ≤ 1 req/s | the same host, the same run, 15 s each (`tests/test_dx_page.js`); `/state` on SSE ticks (≥ 2 s) instead of a 1 s timer. The rest needs a host delta feed |
| K5 scrub: last seek → every panel of the entity drawn at t | n/a | **10-269 ms** as first measured, **an artefact of the check** (it took the last draw at t, and a keyed panel draws again at the same t whenever a new version arrives; corrected in phase 3b, §14: 9-27 ms); 8-13 ms per seek once paused, every panel's own draw 0.5-3 ms | ≤ 50 ms | the test's own listeners on the clock and `data-views-live`; the slow part is the first frame after leaving live and frame scheduling in headless software rendering on a loaded machine, not the views' JavaScript (no long tasks seen) |
| K7 cold load → the tree drawn | not measured | **120-221 ms** (221 inside the full suite run) | ≤ 1.5 s | an in-page 10 ms timer from navigation start |
| Page weight (Explore) | 592 KB gz | **216 KB gz** (data-ui core 103, live views 30, kpi 14, table 12, dashboard 15, the page 30) | — | gzip -9 of each file the page loads |

Not measured in this phase: K1 (receive → drawn), K3 (host CPU per page), K4 (main thread with 20 series), K6 (heap at full
retention).

### Asks of the RobotDx session (the frozen `/api/v1`; nothing here was edited)

1. A delta feed for the tree (K2 ≤ 8 KB/s): rows of the entities that changed (and removals) on the existing SSE stream,
   so `/state` is read once and not on every tick.
2. `GET /api/v1/dashboards` / `PUT` (a list of `{id, title, text}`), so a dashboard follows the person; the page's store has
   that shape.
3. The tree's row states over the host's window (step B), so the tree rewinds before the page opened.
4. `/events` honouring `Last-Event-ID` (a reconnect replays what it missed instead of a re-read).
5. One answer with the latest checks of every entity (a Diagnostics table across a rack without one `/node` per entity).
6. Serving: `/dx/` answers with `dx/index.html` (today only `/` maps to an index), and `.base` as `text/plain`.

### Asks of data-ui (follow-ups found by this page; the page works around the first three in `dx.css`)

1. Playback card: the ● LIVE pill changes width ("LIVE" → "LIVE −396 s") and moves the track under the pointer during
   a drag, so a scrub lands at another t. Keep one width.
2. Arrange: a one-row panel (`h: 1`, gridstack writes no `gs-h`) collapses to 0 px (`height: auto`).
3. `levels`: "No levels in this window yet" stays above the strips once they arrive.
4. `display: age` while following measures against `Date.now()`: wrong for a host on its own clock (a replayed fixture
   reads days); measure against the newest data's time plus the time since it arrived.
5. `plot` and `levels` draw every stream of a dataset and take no filters, so the page keeps one dataset per entity;
   `only:` on `levels` (or filters on the native cards) would let one dataset serve a rack.
6. An append dataset that replaces a row by id (a provisional plug, then its confirmation): the page drops the
   provisional row instead.
7. K5: the first frame after leaving live is the slow one; worth a line in `bench/live.py`.
8. `<data-dashboard>` without `src`: its arrangement is kept under the element's id, so a host that draws several
   dashboards in one element must change the id per dashboard (the page does); and a dashboard handed a clock before
   `load()` writes no `#t` (the page writes it). Worth a sentence in CONTRACT §17 / §21.

## 14. Phase 3b: the host's live feed and its dashboard files (contract, before the code)

*2026-10-27, after main gained `GET /api/v1/live` (bbc3e00) and dashboards as files (7e2e789, d0b9223); shapes in
docs/api-v1.md. One seam each, in the page's adapter and its board store; what the views read does not change.*

- **Live** (`dx/feed.js`): the page opens `GET /api/v1/live` and applies `snapshot` (the `/state` body: everything it holds
  is replaced), `upsert` (`{table: "tree", id: path, row}` with `children` as paths, or `{table: "state", id: section, row}`),
  `remove` and `samples` (the `/values?since=` shape, taken for an entity whose State the page watches, after its backlog) to
  the same datasets as before (§12.1); the browser's `EventSource` resumes with `Last-Event-ID`. **Time is not diffed** by the
  host: a version's time is `clock.now` of the snapshot advanced by the page's own clock since (re-anchored on each `samples`
  event's `now`), and an age is derived from `last_received`, never read from `age_s`. With the feed on, nothing polls `/state`,
  `/values` (after a backlog) or `/events` ticks for it; `/node` (checks, settings) is read when the entity's tree row changes
  (at most once a second) and every 10 s, `/history` (levels) when its row changes (at most every 2 s) and every 15 s.
  **Fallback:** an `EventSource` that closes before its first event (a 404 on an older host) makes the page poll `/state`
  exactly as in phase 3. `robotdx.feed.mode` is `"live"` or `"poll"`.
- **Dashboards** (`dx/boards.js`): `GET /api/v1/dashboards` answering `{files}` puts the store on the host: the list is the
  files (a name is its id: `^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$`; a starter's id is never taken), a dashboard is read with
  `GET …/<name>.base` (its `ETag` kept), every change is a `PUT` of the whole text with `If-Match` (none for a new file),
  Delete is a `DELETE` with `If-Match` (the host moves the file to `.trash/`). **412** shows a conflict
  (`[data-board-conflict]`: reload theirs, or save yours as a copy); nothing is overwritten. **413** says the text is over the
  host's 1 MB and offers Save as file. A dashboard still in this browser from phase 3 is listed as a **draft**
  (`[data-board-kind=draft]`) and moves to the host on its first save (a free name from its title; the browser copy is then
  removed). Save as file and Load .base (picker, drop) stay; a loaded file becomes a host file. **Fallback:** a 404 (an older
  host) or 503 (a host with no config dir) keeps phase 3's store in this browser. The writes carry the page-only header.
- **Requests** (§12.2) gain `GET /api/v1/live`, `GET /api/v1/dashboards` and `GET /api/v1/dashboards/<name>.base`; `PUT` and
  `DELETE /api/v1/dashboards/<name>.base` are the page's second and third writes (a file under the host's config dir; no
  device). The checks allow them only while the saving checks run, against the fixture host's scratch config dir
  (`IO_DIAGNOSTICS_CONFIG_DIR`; the host's own rule: that variable, else `$XDG_CONFIG_HOME/io-diagnostics`, else
  `~/.config/io-diagnostics`).

### 14.1 As built and measured (2026-10-27)

**Rebase.** lane/robotdx-data-ui sits on main d0b9223 (78ac3e1, 7e2e789, bbc3e00, d0b9223); data-ui re-vendored at lane/live
bfc632f + lane/app-rail e1dc0b9 (scratch merge 7f2b620; bfc632f changed only data-ui's bench, so every bundle is the same).

**Tests** (`run_suite.sh`: pytest, then every `tests/test_*.js`):

| | pytest | JS files | test_dx_page.js |
|---|---|---|---|
| phase 3, before the rebase (on ae938a4) | 602 passed, 2 failed (the folder name; a timing test that passes alone), 1 skipped | 18 of 18 pass (2,580 checks) | 57/58 (K5, the old oracle) |
| rebased, phase 3's page (2efd9e3) | 662 passed, 1 failed, 1 skipped | 19 of 19 pass (main added test_rail_mode.js 6/6; test_layouts.js 200/200; test_diag_variants.js 405, was 406, no failure) | 57/58 (K5, the old oracle) |
| phase 3b (e948f69) | 662 passed, 1 failed, 1 skipped | 20 of 20 pass (2,685 checks) | **67/67** (the K5 oracle corrected; the conflict check's own 412 expected) |

The one pytest failure every time is `test_host_plugins.py::test_good_declaration_loads_with_placeholders_and_defaults`, which
asserts that the checkout's folder is named `io-diagnostics`: a lane worktree is not.

**KPIs, as published** to `/home/bam/vt/zach/producers/robotdx` (`tools/bench_robotdx.py`, run 20261027-235638 at e948f690,
trust quiet: load 12.6-13.1 on 24 CPUs, calibration 389-418 ms against a best of 389; GPU: ANGLE on Vulkan, RTX 4080 SUPER):

| KPI | Value | Budget | Notes |
|---|---|---|---|
| `first_paint_ms` (p95 of 5 cold loads) | **165** | 1,500 | 130-165 ms |
| `ingest_to_pixel_ms` (p95, 6,000 samples/s into 6 plots, host emit) | **36.8** (p50 16.2) | 30 | 600×2: 32.8; 24,000×12: 60.3. On the fixture host from the adapter's receive: 14.4 p95 (8.2 p50), plus the host's own ~1 s change tick |
| `frame_time_ms` (p95, 6,000×6) | **50.0** (p50 33.3) | 16.7 | 600×2: 16.8 (60 fps, none dropped); 24,000×12: 66.7. The frame loop's own work is 3-11 ms p95: the rest is parsing 360 small SSE events a second and drawing six canvases |
| `heap_mb` (64 MB cap, 90 s; short) | **22.6** | (diagnostic) | the store at its cap (63.7 MB of typed arrays outside the JS heap) after 20 s; `--long` not run yet |
| `scrub_ms` (p95 of 30) | **25.8** (p50 23.3) | 50 | the test measures 9-27 ms |
| `back_to_live_ms` (p95 of 10) | **263.5** (p50 20.2) | 100 | one slow go-live of ten; the first run of the bench had 29.5 |
| `idle_traffic_kb_s` | **26.3** | 8 | on `/api/v1/live`. The page-level traffic, from the checks: **1.48 MB/min** live, 1.56 MB/min on the `/state` fallback, 3.68 MB/min for today's page; the RobotDx session measured 1.62 MB/min for `/live` against 4.02 MB/min polling on F1 |

**Asks of the RobotDx session** (the API stays frozen here):
1. `/live`'s `samples` come with the host's change tick, at most about once a second while a 10 Hz watch runs, so State on
   the page now moves in one-second steps (phase 3 polled `/values` at 4 Hz). A watched entity's samples sent as they arrive
   (or a tick at the watch's period) would bring the plot back to 10 Hz.
2. A wall-clock emit stamp on each `/live` event (the fixture's `now` is its replay clock), so ingest to pixel can be timed
   from the host's emit on a fixture host too, not only from the page's receive.
3. Still open from §13: the tree's states over the window (step B), all checks in one answer, `/dx/` serving its index.

**Ask of data-ui:** a paused keyed view redraws whenever a version arrives, even one after its t (the `change` event
carries `from`; an upsert newer than t changes nothing the view shows). It cost K5's first measurement its meaning and
costs a paused page work it does not need.

## 15. Phase 3c: the host's four answers, and a frame-time pass (contract, before the code)

*2026-10-27, after main gained the asks of §13 and §14.1 (5cc429f, 5cdb4b9, 63019fb, 4e2ec56; shapes in docs/api-v1.md). The
branch is rebased onto main 4e2ec56. Each answer is one seam in the adapter; each keeps a fallback for a host without it.*

- **State at the watch's rate** (5cc429f: a watched entity's `samples` come as its points arrive, one event per entity per
  100 ms at most). The page tells the two kinds of host apart by the feed's own pace, per watched entity, and needs no flag:
  1. *Cadence*: while the entity's watch runs, three of its `samples` events in a row, each less than 500 ms after the one
     before (timed by their `emitted`, else by the page's clock), say the feed keeps up.
  2. *Who comes first* (a watch slower than 2 Hz never shows that cadence): twelve polls in a row that brought no point the
     feed had not already brought, while the feed brought points first, say the same.
  Until then, and for good on a host whose samples wait for its ~1-2 s change tick, the page polls
  `GET /values?since=<cursor>` every 250 ms while the watch runs (phase 3's rate; a point arrives once, by whichever read it
  first: the page keeps the newest time per key). Once the feed keeps up, that entity's poll stops. A new `snapshot` (a
  reconnect, perhaps to another host) forgets what the page learned. `robotdx.feed.entity(path).feedKeepsUp` says which.
- **`emitted`** (5cdb4b9: every `/live` event's host wall clock, unix ms). In a `snapshot` it is a top-level key beside
  `/state`'s; the page leaves it out of the state sections it keeps (the mirror holds `/state`'s sections only). The page
  reads it for the cadence rule; the bench times `ingest_to_pixel_ms` from it, on the stub and on a fixture host, and records
  on every point which stamp it used (`emitted`, else the stub's `now`, else the adapter's receive). A resumed connection's
  replayed events keep the stamp they first left with, so the bench counts reconnects in its sweep and says so.
- **The tree's past** (63019fb, `GET /api/v1/history/tree?bins=N`): read once, after the first snapshot (or the first
  `/state`), with `bins` = the host's window over 2 s (at most 1,000) and the host's window. Each row's bar becomes versions
  of that row in `entities` before the moment the page opened: a version at a bin's start whenever the level changes,
  carrying today's row (its name, its place in the tree, its identity) with that bin's level (`health`, `rank`, `stale`) and
  nothing else of the row (message, what is below it, presence, rate, counts: empty). The note before the page opened
  (`#dx-before`) says so, with the bin. A 404 (a host before 63019fb) keeps phase 3's empty tree and its note.
- **Every check in one answer** (63019fb, `GET /api/v1/checks`). A `checks` binding with no `?path=` (every entity's
  checks) is read with one `GET /api/v1/checks`: when the view opens, when a tree row changes (at most once a second) and
  every 10 s on `/live`, every 2 s on the `/state` poll. Its rows are those a per-entity `/node` makes (§12.1), and both
  readers share one map of what was last written, so one check is one row whoever read it. A new starter, **Diagnostics**
  (`dashboards/diagnostics.base`), is every check on the rack, worst first. Explore keeps reading `/node` for its one entity
  (the same answer has its settings and its watch). On a host before 63019fb the board shows the checks of the entities the
  page watches.
- **`/dx/`** (4e2ec56): the page's address is `/dx/`; the launcher prints it and the checks and the bench open it. `.base` is
  served as `text/plain`; the page had no workaround for it.
- **Requests** (§12.2) gain `GET /api/v1/history/tree` and `GET /api/v1/checks`.
- **The stub** (`bench/robotdx_stub.py`, test-only) speaks the new feed: a watched entity (a `POST /watch` in the last 10 s)
  gets one `samples` event per 100 ms (`--coalesce`) as its points come, the others wait for the 1 s tick (`--tick`); every
  event has `emitted`, the snapshot as a top-level key; `GET /api/v1/checks` and `/history/tree` answer. Points come at the
  rate asked, exactly (a slow rate is slow). `--coalesce 0` is a host before 5cc429f; `--coalesce 0.0167` is phase 3b's sweep
  (an event per entity every frame).
- **One frame-time pass.** One profile of the 6,000×6 sweep (Chrome's trace and V8's sampling profiler, 6 s, the bench's
  `--profile`) splits the main thread's time into the `/live` handler (parse, the adapter's rows, the store's append), the
  frame loop's publish and clock, the views' draw (data-ui's reads and uPlot, the canvas calls apart), style / layout / paint,
  and GC. If the handler is the larger part and the feed brings more than one event per stream per frame, the adapter queues
  parsed events and applies them once per animation frame (one append per stream); if the draw is, nothing changes here or in
  data-ui, and the fix is named for the live lane. Before and after from the same bench on the same renderer:
  `frame_time_ms` (the three N×M), `ingest_to_pixel_ms` p50 / p95, `scrub_ms`, `back_to_live_ms`. The sweep now runs at the
  host's cadence (100 ms); each point says so (`feed: watch 100 ms`), since phase 3b's points were taken at a frame's.

### 15.1 As built and measured (2026-10-27)

**On main 4e2ec56; data-ui re-vendored** at lane/live 3289ab5 + lane/app-rail e1dc0b9 (scratch merge a85ebc1; a conflict in
docs/CONTRACT.md only, both §21 and §22 kept; of the files the page loads only `data-ui.js` changed). The machine was shared all
evening (load average 15-90 on 24 CPUs from other sessions' runs), so every number below is marked with how it was taken.

**The four answers, as the checks see them** (`tests/test_dx_page.js`):
- *State at the watch's rate*: on F1 the joint's 10 Hz watch comes as `/live` samples about every 100 ms; the cadence rule stops
  the `/values` poll in the watch's first half second, and in the 4 s after no `/values` request goes out while State moves 4.0 s.
  On the stub (50 samples/s over 5 keys): at the host's cadence, no poll and State in steps of at most 116 ms; an older host's 1 s
  tick, 3.25-3.5 polls a second and State in steps of at most 343 ms; a slow watch (a point a second), the "who comes first" rule
  stops the poll.
- *`emitted`*: the host's first `/live` event has it (one of 14 top-level keys); the page's mirror keeps `/state`'s 12 sections.
- *The tree's past*: one `GET /api/v1/history/tree?bins=450` when the page opens (141 KB on F1; the host answers in about 11 ms).
  Scrubbed 6 s before the page opened, the tree draws at that t with all 44 rows, each with the level of the host's own bar (44
  compared); on the stub's synthetic past (stub/0 ▲ warn for its first 6 s) the tree shows warn then and OK after. With a 404 the
  tree before the page opened is empty (drawn at t, no rows) under phase 3's note.
- *Every check in one answer*: the Diagnostics starter lists F1's 53 checks worst first from `GET /api/v1/checks` (4 reads in 8 s:
  the open and the row changes) and no `/node`.
- *`/dx/`*: the launcher prints it; the checks and the bench open it.

**Found by the new checks, and fixed** (each was in phase 3 already):
1. *The tree never rewound.* data-ui's `<data-dashboard>` binds the page's shared clock to its own datasets and lets go of every
   other one (`dashboard/src/live.js` `bindLive`), so a view outside the open dashboard (the tree) drew its newest rows at any t.
   The page binds every dataset it holds again after each dashboard binds. Phase 3's tree check passed vacuously; the checks now
   need the tree's own draw at the clock's t.
2. *The note before the page opened could stay hidden after a scrub*: a throttle that dropped its last call.
3. *An entity selected before its first State point never got State panels*; they now come with the point (peek kept while
   selected).
4. *The connection line popped in about 1 s after load and moved the time bar's track by 345 px* (a GPU scrub in that second
   pressed past the track's end): `<data-live-status for="dx-app">` connected before `#dx-app` had its clock. It now has a fixed
   width and reads the clock from the first frame.
5. *First paint 1.5-1.9 s on 7 of 24 cold loads*: data-ui's live `<data-views>` builds its first model before its live feed is
   attached and serves it while the view's module loads, so a live draw in that window drew an empty tree until the next change
   (F1's ~2 s tick); the tree's past landing early made that window likelier. The page loads the first screen's view modules
   beside its first requests. Interleaved with phase 3b's page on two hosts, 24 cold loads each: **p50 178 ms, p95 440** against
   237 and 705 (phase 3b's page has the same race: one load of 2,019 ms).

**K5 read both ways** (the data-ui fix 3289ab5): the first draw of each panel at t and every draw at t in the 500 ms after the
seek agree, with no redraw at t, in every run since the re-vendor: the final run 18.2 / 18.2 ms in software and 44.9 / 44.9 ms
on the GPU (load 21; earlier runs 14-32 ms on the GPU). Phase 3b's every-draw reading was 237-442 ms.

**The frame-time pass.** One profile of the 6,000×6 dashboard (6 s, GPU, `tools/bench_robotdx.py --profile --maps`, load 40-48):

| Main thread, per second | host's cadence (60 events/s) | one event per frame (339 events/s) |
|---|---|---|
| `/live` handler (JSON.parse, the adapter's rows, the store's append) | **25.8 ms (2.5 %)** | 9.0 ms (0.9 %) |
| frame loop: publish, clock | 14.6 ms (1.5 %) | 8.1 ms (0.8 %) |
| draw: data-ui's reads and models | 101.3 ms (10.1 %) | 103.1 ms (10.3 %) |
| draw: the plot view (uPlot and its data) | 68.1 ms (6.8 %) | 78.9 ms (7.9 %) |
| draw: canvas calls | 25.4 ms (2.5 %) | 28.9 ms (2.9 %) |
| native: style, layout, paint, **Commit** (trace: Commit 356 / 601 ms) | 394 ms (39 %) | 679 ms (68 %) |
| GC | 9.8 ms | 22.2 ms |
| idle | 354 ms (35 %) | 43 ms (4 %) |

The hot lines: uPlot's `Path2D` and `lineTo` (every series' path over the whole window, on every draw), data-ui's
`live/window.js` 170-196 (the column cache writes every pixel column of every series again each draw), `views/live/src/plot.js:164`,
uPlot's `getHPos` / `getVPos`, `live/feed.js` 199-201 (the label map and an empty TanStack table built for every plot draw), and
`views/playback/src/index.js:152` (`getBoundingClientRect` and the ticks' `innerHTML` on every update: a forced layout a frame).
**Decision, by §15's rule:** the handler is at most 2.5 % of the main thread, and the host's feed brings at most one event per
stream per frame, so the page queues nothing; the cost is the draw and its Commit; nothing changed in data-ui; the fixes are
asked of the live lane below.

**Before and after, the same bench and renderer** (GPU, ANGLE on Vulkan). Sequential runs were not comparable (one run's
calibration was 1.55× the host's best, the next one's 1.1×), so the two pages ran **interleaved**: phase 3b's page (723d7be) and
this one, each from its own checkout's stub and fixture host, alternating, three runs per sweep point and two fixture rounds
(load median 34). Medians of the runs (each run's own p95 / p50):

| | phase 3b's page | this page |
|---|---|---|
| `frame_time_ms` 600×2 p95 / p50 | 16.8 / 16.7 (runs 16.8, 16.8, 50.1) | 50.0 / 16.7 (runs 50.0, 16.7, 83.3) |
| `frame_time_ms` 6,000×6 p95 / p50 | 33.4 / 16.7 | 33.3 / 16.7 |
| `frame_time_ms` 24,000×12 p95 / p50 | 100.0 / 16.7 | 83.3 / 16.7 |
| `ingest_to_pixel_ms` 6,000×6 p95 / p50 (emitted) | 27.2 / 16.8 | 22.8 / 18.6 |
| `ingest_to_pixel_ms` on the fixture host p95 / p50 (emitted) | 27.8 / 12.0 | 22.8 / 11.5 |
| `first_paint_ms` p95 / p50 (12 cold loads) | 1,128.7 / 163.9 | **116.3 / 100.8** |
| `scrub_ms` p95 / p50 (40 seeks) | 32.2 / 24.1 | 36.8 / 27.4 (the tree now draws at t too) |
| `back_to_live_ms` p95 / p50 (12) | 37.0 / 21.1 | 33.8 / 17.6 |

So: frame time and ingest are the same within the spread of the runs; first paint is better (fix 5); a scrub costs about 3 ms
more, the tree's own draw at t (fix 1). Against phase 3b's published points the sweep's frame time fell (6,000×6 p95 50.0 → 33.3,
p50 33.3 → 16.7) because the host now sends 60 events a second there, not 360: the page did not get faster, its feed got calmer.

**The 10-minute run, as published** to `/home/bam/vt/zach/producers/robotdx` (run 20261028-030207, code cb6b5ac6: the page as
committed at 0f2cea3 but for the Diagnostics starter's path column, which the bench does not draw; GPU; `systemd-run --user
--scope -p MemoryMax=16G`; load 25 falling to 15, calibration 389-426 ms against a best of 380, so "noisy" by the load rule only,
published with `--include-noisy` and labelled so):

| KPI | Value | Budget | Phase 3b (published, one event per frame) |
|---|---|---|---|
| `first_paint_ms` (p95 of 5) | **125.7** (97-126) | 1,500 | 165.1 |
| `ingest_to_pixel_ms` (6,000×6, from `emitted`) p95 / p50 | **16.4** / 14.8 | 30 | 36.8 / 16.2 (the stub's `now`) |
| ingest 600×2 / 24,000×12 / the fixture host (p95) | 7.0 / 21.9 / 20.5 | 30 | 32.8 / 60.3 / 14.4 (adapter receive) |
| `frame_time_ms` p95 / p50: 600×2, 6,000×6, 24,000×12 | 16.7 / 16.7, **16.7 / 16.7**, 50.0 / 16.7 | 16.7 | 16.8, 50.0 / 33.3, 66.7 |
| `heap_mb` (256 MB cap, 600 s) | **54.8** (the store at its cap after 85 s) | diagnostic | 22.6 (64 MB cap, 90 s) |
| `scrub_ms` p95 / p50 | 29.1 / 25.7 | 50 | 25.8 / 23.3 |
| `back_to_live_ms` p95 / p50 | **27.2** / 19.3 | 100 | 263.5 / 20.2 |
| `idle_traffic_kb_s` | 27.0 | 8 | 26.3 |

**Tests.** `tests/test_dx_page.js` **86/86** (phase 3b: 67; every check above is in it). The full suite (`run_suite.sh`: pytest,
then every `tests/test_*.js`) in a fresh detached checkout of 0f2cea3 named `io-diagnostics`, 2026-10-28, load 9-15: pytest **702
passed, 1 failed, 1 skipped**; **19 of 20 JS files pass**, 2,704 of 2,705 checks. Neither failure is this lane's: the lane
changes no host code and no classic-page file (`git diff main...HEAD`), and
- `test_diag_variants.js` "d1b · order: A-Z sorts the rows inside the folder" fails the same way on a pristine checkout of main
  4e2ec56 (405 passed, 1 failed): its rows are this machine's own computer checks ("bam-GPU: Network Usage", "NTP offset");
- the pytest failure is a different host timing test in each run: `test_live_feed.py`'s watch-rate gap (two events under 99 ms
  apart by `emitted`) in the run the session limit cut short (load 30-90), `test_runs.py`'s lock bound in the full re-run.

**Asks of data-ui's live lane** (none edited here; the page works around 2-4):
1. **The plot draw** (the frame time above 6,000×6): on every draw uPlot builds every series' `Path2D` over the whole window and
   the column cache writes every pixel column again (`live/window.js` 170-196), for each plot whose dataset changed, and each
   redrawn canvas is a Commit. At the host's 10 Hz per entity that is 6 plot draws per 100 ms at 6,000×6 and 12 at 24,000×12.
   Candidates: draw a streaming plot as a strip chart (scroll what is drawn by the elapsed pixels, draw only the new columns), or
   redraw a plot at most when its x moves a pixel and draw every due plot in one frame (one Commit). Smaller, from the same
   profile: `live/feed.js` 199-201 builds the label map and an empty TanStack table on every plot and levels draw; the playback
   card reads `getBoundingClientRect` and rewrites its ticks' `innerHTML` on every update (`views/playback/src/index.js:152`).
2. **The first live model**: `liveData()` assigns `el._live` after the LiveFeed's constructor has started the element, so its
   first model is the placeholder's empty rows; `_render()`'s reuse key (revision, query, filters, view keys) ignores live data,
   so `_liveDraw()`'s fallback while the view's module loads serves it until a later change. (Fix 5 above.)
3. **`bindLive` on a shared clock**: `<data-dashboard>` lets go of every dataset on its clock it did not bind itself; it should
   let go only of its own. (Fix 1 above.)
4. **`<data-live-status for>`** looks for its clock only when it connects and at its 1 s tick. (Fix 4 above.)

**Needs a decision (Zach's or the orchestrator's), not done here:** Explore leaves out an empty State panel (phase 3's rule), so
an entity whose State comes only from its watch would never show State: the watch starts only while State is on screen. The
classic page shows a State tab for every entity whose plugin has a watch. Recommendation: keep the State panel for a
watch-capable entity (the default if nobody says otherwise: leave the rule as it is until a real plugin needs it; moteus and the
fixtures report State outside a watch).

**Asks of the RobotDx session:** a fixture in which some tree row's level changes during a run. F1 holds one level per row for
the whole run, so on a real host the tree's past can be compared with the host but not told from its present; the page's check
of that uses the stub's synthetic past.

**Not done, on purpose:** per-frame batching of `/live` events (the profile: the handler is at most 2.5 % of the main thread and
the host sends at most one event per stream per frame); any change in data-ui (the asks above); the State-panel rule above.

## 16. The rework (Zach, 2026-10-28) and the timeline and Log toolbar (2026-10-29), as built

*2026-10-29. The branch `lane/robotdx-dx2` is main d8e734a with the rework's acceptance (42989ca: written before its code) and the
previous owner's work in progress (dc5da40) cherry-picked; data-ui is re-vendored at `integrate/packet` 5b4e793. The asks of
2026-10-29 have their acceptance in 5dae710, committed red before their code (32eb4f3). Nothing in `/api/v1`, the record or the
classic page changed (`git diff main...HEAD -- io_diagnostics/*.py io_diagnostics/web/*.js io_diagnostics/web/tree io_diagnostics/web/view`
is empty).*

### 16.1 The rework (asked 2026-10-28)

- **The tree is the classic page's own** ("please switch back to that old entity tree"): `web/tree/v6.js` over `web/view`'s rows
  (the default structure p6 on p1, `summary.js`'s header counts, `visibility.js`'s "Show N hidden" rows), loaded by `dx/index.html`
  as the classic page loads them. `dx/tree.js` draws it as of the clock's t: `/state`'s shape is rebuilt from the `entities`
  versions in force at t (each version keeps its node as the host sent it), so it rewinds with everything else. Group by, Sort by
  and "show what is hidden" are the classic page's own preferences (the same keys: a choice on either page holds on both). The
  renderer's writes (the eye, Hide, Expect, Remove from bus) are hidden and blocked: this page only reads (§12.2). Tree | Table
  (the table is data-ui's), Collapse all, Expand all, the search on top.
- **Each entity has tabs**: Overview (Explore's starter), then the classic entity page's Diagnostics, State, Configuration, Log and
  Utilities under the classic page's ids (`?page=`), each a history entry, the last one remembered per kind of entity, each a
  dashboard of its own (`dashboards/entity-*.base`; Utilities lists the host's utilities and links the classic tab, where they run).
- **Three playback bars to compare** (data-ui's `playback.variant`: today's `bar`, `rerun`, `foxglove`), switched by a temporary
  bottom-right picker and remembered on this device. Rerun's and Foxglove's have no way back to the newest data (data-ui's proposals
  predate its live tier), so the page draws its own LIVE beside them.
- **Back marks the tree's row as the URL changes.** `render()` queues destinations; Back moved the URL first and the tree's mark only
  after the selection before it had loaded its panels (up to 2.5 s). What the URL asks is now marked at once (`handle.mark`).

### 16.2 data-ui at `integrate/packet` 5b4e793

`tools/vendor_data_ui.py --ref integrate/packet` builds the ref without checking it out anywhere (`git archive` into a scratch
folder, `node_modules` linked from a worktree whose `package-lock.json` is the commit's, `npm run build`); `VERSION` names the ref,
the commit and its parents. The same nine view bundles; the chart's plot and chunk are files of their own now.

One real change of behaviour: **a dashboard retires a clock a host handed it** (sim2real gap #13, "a new document gets a new
clock": `<data-dashboard>.load()` of a new document pauses the clock it holds, lets go of every dataset, stops it and makes its
own). This page hands every dashboard its one clock (§12), so after the first load the views inside a dashboard followed a clock
the tree and the time bar did not, and the page's clock sat paused with no data (13 checks of `test_dx_page.js`). `holdClock()` in
`app.js` gives each dashboard an own `clock` property it reads and cannot replace, and `openBase` puts following back after the
load. Asked of data-ui below.

### 16.3 The timeline: a panel that rises from the bottom (asked 2026-10-29)

*"The playback/rewind bar becomes a panel that rises from the bottom of the page, collapses, and is brought back from the bottom
status bar, like the Terminal panel on the classic page today ... It is live by default, like a system monitor, and you can rewind
at any moment. Its height is draggable and remembered, and it stays collapsed or open across reloads."*

- **The classic Terminal panel's conventions** (`web/runs/r1.js`): `#dx-timeline` sits between the page and a 32 px status bar
  (`#dx-statusbar`, the classic `.statusbar`'s height). Its top edge is a horizontal separator (`role=separator`, keyboard-reachable)
  that drags (120 px to the window less 220), is kept (`robotdx.dx.timeline.height`), resets on a double-click and moves 16 px per
  arrow key; a chevron in its head hides it; the status bar's **Timeline** toggle (`aria-pressed`, `aria-expanded`,
  `aria-controls`) and Ctrl+` bring it back and hide it, at the height it had. Collapsed or open is kept
  (`robotdx.dx.timeline.open`; only a closed panel is written: open is the default).
- **Live by default, rewind at any moment.** The clock follows on load as before; the panel holds the page's one time bar. Collapsing
  it never moves the clock: the toggle says **● LIVE**, or how far behind (`○ −8 min`), so a collapsed panel still tells you when.
- **Like a system monitor.** Under the bar, the rack over the host's window, both as of t, both stock data-ui views over the summary
  dataset: a `levels` strip (**Rack**: the worst level per bin) and a `plot` of the problem counts (errors, warnings, stale,
  unknown). Its past before the page opened is the root row of `GET /history/tree` (read once already, §15); the counts are not kept
  for the past and stay empty there. Dragging the panel taller gives the plot the room.
- The connection line and the temporary pickers sit in the status bar (the classic page keeps its Prototype pickers there). At 390 px
  the connection line folds (its state is the toggle's word) and the status bar stays one row.

### 16.4 The Log toolbar: five one-line proposals (asked 2026-10-29)

*"If you are using data-ui then the ways of doing this are already quite supported. for example we don't want to tool bar to split
onto two lines."* The classic Log tab wraps: its count, the level segment, Raw, search and Live, then "Log of CAN 13 + children /
this only / + path to host" on a second line. Every proposal here is data-ui's one-row bar (`toolbar="bar"`, CONTRACT §15: it never
wraps, its presets are attributes) and the log's controls become data-ui's own things:

| Proposal | The bar (data-ui's attributes) | Level floor | Scope | Raw lines |
|---|---|---|---|---|
| 1 · Overflow menu (default) | Shopify: `views="dropdown" promote=""` | the view chip | a clause in Display | a clause in Display |
| 2 · Scope as a chip | `views="dropdown" promote=""` | typed: `level:>=2` | the view chip | typed: `-channel:raw` |
| 3 · Search on focus | `views="dropdown" promote="filter" search="icon" labels="icons"` | the view chip | behind Filter | behind Filter |
| 4 · Scope in the breadcrumb | `views="tabs" promote="filter"` | tabs | a select in the entity's breadcrumb | behind Filter |
| 5 · Level as a facet | `views="tabs" promote=""` + the dashboard's rail | a facet (Debug+ … Error+) | tabs | a facet (Hide raw lines) |

The count is data-ui's status line under the table in all five; Live is the page's one LIVE (the timeline and the status bar:
Rerun's single way back), never a second control in the Log. A temporary bottom-right picker (`#dx-log-variant`) switches them live
and remembers the pick on this device; proposal 1 is the default. The data under them:

- The Log reads the entity's **raw lines** (`GET /activity?channel=raw&path=…&scope=host`, the widest scope, so every scope is a
  filter of what is held), every 5 s while it is on screen, as the classic entity log does, **inside the host's window** as the
  activity backlog is (§12.1; 16.5 says why); they go into the events dataset as a stream of their own (each read is in time
  order; another entity's lines may be older). Activity, the overview's Recent and Explore's own Log keep raw lines out, as the
  classic Activity log does. F1 has no raw line inside its window (its kernel lines are from the rack's boot), so on F1 the raw
  controls have nothing to act on.
- Events gain `sev` (the floor as a number: debug 0, info 1, warn 2, error 3), `channel` and `lineage` (the path and every path
  above it), so "+ children" is one clause, `lineage.contains(p)`, which data-ui draws by the entity's name (`labels`), and
  "+ path to host" is it or-ed with each path above.
- **Why no query tokens**: proposal 2 first drew the scope as a token in the query box (`filters="query-tokens"`); on a board's Log
  the token wrapped data-ui's query box onto a second line at 1280 px (the bar 71 px, the boxes' tops 15 px apart) and onto three
  over the bar at 390 px. Asked of data-ui below.

### 16.5 Found on a host up longer than its window, and fixed

The checks' fixture host is seconds old when the page opens; a real one has been up for hours. Two faults showed only there:

1. **The tree's past emptied a tick after the page opened, and the clock could not go back before the open.** dc5da40 kept the
   entities for the host's window (main keeps none for them), and data-ui's keyed age retention drops every version older than
   end − age but each key's newest, so the version still in force at the cut goes too. The tree's past (§15) is one version per row
   at the window's start: on a host whose history fills the window it sat at the cut and went at once; the clock's span then
   started at the page's open, and the Rack strip lost its past. Later, any row that changed inside the window was missing from a
   rewind back to its change. The feed now writes the version in force at the cut again just inside the window, before the cut
   reaches it (`_carryForward`: entities and summary, every 2 s and at once after the tree's past is read, only for a version that is
   not its key's newest). `tests/test_dx_tree_past.js` (a fixture host with a 60 s window, up 70 s): 4/7 before, 7/7 after. The
   first seconds at the window's far edge still have no row (data-ui's rule; asked below).
2. **Opening a Log stretched the time bar to the device's plug.** An entity's raw lines go back to its plug (F1's are 12 h before the
   replay) and the clock spans what its datasets hold: the bar went from 15 min to 12 h (on a host up for days, days) until a reload.
   Letting the events go of the clock keeps the bar but stops the Log rewinding (data-ui draws a dataset its clock is not bound to at
   its newest rows, `live/feed.js:178`), so raw lines are kept inside the window, as the activity backlog is.
   `tests/test_dx_time_span.js`: 4/6 before, 6/6 after.

### 16.6 Measured (2026-10-29, this PC, load 3-9 on 24 CPUs)

**Checks** at the final commit: `tests/test_dx_page.js` **99/101**, `test_dx_timeline.js` **27/27**, `test_dx_log_toolbar.js`
**38/41**, `test_dx_tree_past.js` **7/7**, `test_dx_time_span.js` **6/6**; pytest (a detached checkout named `io-diagnostics`)
**778 passed, 1 skipped**. The five failures, none edited:
- 42989ca's two shape checks ("…each leaf with the shape of the level the host kept for it then"; "the tree's past is the host's,
  where it differs from now") read a row's level from `.t6-st svg.shape` and its `lv-<level>` class; the classic tree draws its
  shapes as `svg.t6-shape.t6-<level>` (`web/summary.js` since 854c0f0, 2026-10-24), so the reader finds no level on any row and
  every warn or error row compares as none. Read with the classic markup, the same comparisons pass: on F1 5 leaves compared, 0
  wrong; on the stub, stub/0 warn then, OK a few seconds later and now, stub/1 never. The fix is the test's selector.
- 5dae710's three raw-line checks counted F1's 12 h old kernel lines, which the page must not hold (16.5, 2); F1 has no raw line
  inside its window for any entity. They need a fixture with raw lines inside the window.

**The one-line toolbar** (a single row of boxes, the same top within 2 px, nothing under the bar): every proposal at 1280 and 390
px, light and dark, 20 of 20; the boxes' tops spread 1.5-2 px (data-ui's controls are 25, 28 and 32 px tall, centred).

**The timeline**: on the status bar, the status bar on the window's bottom edge (±1 px); the top edge drags +80 px with the page
above giving up 80 px (±3), kept on reload, ArrowUp +16, double-click back to 200 px; collapsed and open kept across reloads;
Ctrl+` both ways; the Rack strip's window ends at the clock's t when paused (±0.5 s); 390 px one row, no sideways scroll.

**KPIs** (`test_dx_page.js`): K2 idle 25.3 KB/s on the live feed against 61.5 for the classic page; K5 scrub to every live panel
drawn 6-8 ms software, 9-13 ms GPU; K7 cold load 107-157 ms. **The bench** (`tools/bench_robotdx.py --phases fixture`, GPU,
every run "quiet"), interleaved with main d8e734a:

| KPI (pooled over every sample of the runs) | main d8e734a, 6 runs | this branch f59bfe8, 2 runs | budget |
|---|---|---|---|
| `first_paint_ms` p50 / p95 (cold loads) | 88.2 / 117.5 (30) | 109.2 / 126.0 (10) | 1,500 |
| `scrub_ms` p50 / p95 | 25.2 / 28.6 (180) | 24.0 / 27.0 (60) | 50 |
| `back_to_live_ms` p50 / p95 | 14.9 / 24.9 (60) | 12.8 / 14.5 (20) | 100 |
| `idle_traffic_kb_s` (per run) | 25.3-27.0 | 26.9-27.0 | 8 |

The cold load is about 20 ms later than main's (p50): data-ui 5b4e793's core is 428 KB against 324, the classic tree adds its eight
scripts and three style sheets, and the timeline two live views; not profiled further (the budget is 1,500 ms). The bench's first
paint read data-ui's old table tree; it times the classic tree since 79bb56a.

### 16.7 Asks of data-ui (none edited here; the page works around 1-3)

1. **Retire only a clock the dashboard made.** `<data-dashboard>.load()` of a new document pauses, unbinds and stops the clock it
   holds, a host's included (sim2real gap #13), and makes its own (`dashboard/src/index.js` `_setupClock` → `_retireClock`). The page
   holds its one clock with an own `clock` property and puts following back (`holdClock`).
2. **`bindLive` lets go of every dataset on a shared clock that it did not bind** (asked in §15.1; still in 5b4e793). `rebind()`.
3. **Keyed age retention should keep the version in force at the cut** (`live/store.js` `_dropVersions`: `vs[0].t < before` drops
   it); as it is, a key is missing from every t between the cut and its next change. `_carryForward`.
4. **The query box wraps** (`filters="query-tokens"`): `.dv-qbox` is `flex-wrap: wrap` with a 100 px text field, so a token that
   leaves less room puts the field on a second line, and on a narrow bar the box opens over the views and wraps there; the bar is then
   not one row (§15's note). Measured on a board's Log: 71 px tall at 1280 px (boxes' tops 15 px apart), 76-89 px over the bar at 390.
5. **The playback proposals other than `bar` have no ● LIVE** (`rerun`, `foxglove`, …): the page draws its own beside them.
6. **One control height in the bar** (minor): the query box 32 px, a labelled tool 28, an icon-only tool 25, centred; a row is
   1.5-3.5 px uneven at the top.

### 16.8 Left

- 42989ca's shape reader and 5dae710's raw-line fixture (16.6): the owner's call, not edited here.
- The old raw lines (before the window) are only on the classic entity Log. Recommendation if they are wanted here: a second,
  static panel under the Log ("before the window"), whose rows are at or before every t the clock can reach.
- Explore at 390 px (not part of these asks): the tree pane keeps its 380 px and scrolls inside the page; the Explore bar wraps
  over the tree's head.
- The Log proposals' known edges: 1 keeps scope and raw lines a page deep (Display, Filter); 2's level is typed; 5's floors are
  data-ui's `popular` checkboxes, so two picked floors keep the higher.
- The temporary pickers (Log toolbar, playback bar) go once Zach picks.


### 16.9 Rebased onto main 591858c (2026-10-29)

Main moved while this was built (13 commits: the 3D view, the classic page's performance pass, Utilities' two states, PEP 0002's
placeholder workspaces; no file in common with this branch). The branch is rebased onto it, so it lands by fast-forward; the
version on d8e734a is kept as `lane/robotdx-dx2-on-d8e734a` (dc480ec), and the shas the commit messages name as red are that
version's. Measured again on the rebased head: `test_dx_page.js` 99/101, `test_dx_timeline.js` 27/27, `test_dx_log_toolbar.js`
38/41, `test_dx_tree_past.js` 7/7, `test_dx_time_span.js` 6/6 (the same five failures as 16.6); pytest 826 passed, 1 failed, 1
skipped: the failure is `test_live_feed.py`'s watch-rate gap (two samples events under 99 ms apart by `emitted`), the host timing
check §15.1 already saw flake under the suite's load; alone it passed 5 of 5, and this branch changes no Python the suite imports.
K2: the classic page's idle traffic fell to 32.9 KB/s with main's performance pass (this page's live feed: 25.3). The bench, two
more runs interleaved with d8e734a's: `first_paint_ms` 107.7 / 135.4 (p50 / p95), `scrub_ms` 24.0 / 25.6, `back_to_live_ms`
13.4 / 18.1, `idle_traffic_kb_s` 26.9-27.0.

### 16.10 The five failures, settled (the orchestrator's follow-ups, 2026-10-29)

- 42989ca's two shape checks read the classic tree's `svg.t6-shape.t6-<level>` as well as the old `svg.shape.lv-<level>` (the
  orchestrator's fix, 948df47). They pass with real comparisons: on F1 5 leaves compared and none wrong; on the stub, stub/0 ▲ warn
  then and no mark a few seconds later, stub/1 never.
- 5dae710's three raw-line checks are rewritten (558dd66, with the orchestrator's leave). They had pinned the page holding F1's
  12 h old kernel lines, the behaviour 15574d7 removed, and F1 has no raw line inside its window to measure the rule with. They now
  run on the test-only stub (`bench/robotdx_stub.py --raw --raw-from --raw-old`, off by default): while stub/0's raw lines are only
  three from 12 h back (F1's case), the page holds none of them and the clock's data spans at most the window; both fail on
  33844b6, the commit before the rule (3 held, a 12 h span). Then, with lines inside the window, the lines draw and rewind (absent
  half a second before their time, there after it), the breadcrumb's scope keeps the entity's own lines, adds those below it and
  then those above it, the Level facet's Warn+ leaves warnings and errors, the old lines stay out and the span stays the window,
  and the rail hides the raw lines.
- Measured at 558dd66 (rebased on main 591858c): `test_dx_page.js` **101/101**, `test_dx_log_toolbar.js` **46/46**,
  `test_dx_timeline.js` **27/27**, `test_dx_tree_past.js` **7/7**, `test_dx_time_span.js` **6/6**; pytest (a detached checkout
  named `io-diagnostics`) **827 passed, 1 skipped**. KPIs: K2 idle 25.3 KB/s against the classic page's 32.9, K5 10 ms software /
  11 ms GPU, K7 116 ms.

## 17. Add to dashboard (data-ui CONTRACT §34, PEP 0014), as built (2026-10-29)

*Zach, 2026-10-29 (relayed): "Dashboards are built with data-ui. Humans curate, AI builds." A reader adds what they are looking at to
a dashboard of theirs, "like the amazon thing of adding to a saved list", and it lands at the bottom, to be arranged later. Branch
`lane/robotdx-add-to-dashboard` on main 5575808: data-ui re-vendored at `integrate/packet` 0db2785 (96c731c; every check green on
it before any page change, so no drift), the acceptance committed red first (31a3d3e), the host's one additive change (a222578),
the page (7720e5d), and At this moment (red 0126d70, b523933).*

- **What the reader gets is data-ui's own:** Add to dashboard… on every panel's ⋯ (Explore's tabs, Activity, Runs, the starters, your
  dashboards; measured on a table and a plot) and on a KPI card's item menu (measured with a right-click; long-press and the
  ContextMenu key are data-ui's);
  the picker with your dashboards, New dashboard… and At this moment; the panel at the bottom, full width. Not on a plot's legend:
  data-ui's live `plot` draws a static key (below).
- **The host's half** (`dx/add.js`): `dashboards()` on `#dx-app` maps `GET /api/v1/dashboards` (newest first) to `{id, title, url,
  text}`, each text read once per etag. The `data-add-to-dashboard` listener appends the panel (data-ui's exported `appendPanel`) and
  sends one `PUT` with `If-Match`, the etag of the text it appended to. A 412 (another tab or person saved since) reads the file
  again, appends again and retries once (insert-only, so theirs is kept); a second 412 answers `{ok: false, reason: "<id> changed
  while you were adding; reload it and add again"}`. New dashboard… is `If-None-Match: *` under the name typed (the host's name rule;
  a starter's name is refused too): a file of that name is refused, never written over. It answers `{ok: true, title, href}`;
  "Open ↗" goes to `?go=dashboards&board=<id>` in the page. On a host with no `/api/v1/dashboards` the same is done in this browser's
  store (§14's fallback).
- **The panel reads the file its source panel reads.** The page hands every view of a kind one shared dataset (§12.1: checks,
  config, events, actions for every entity), so a dataset's `url` is whichever binding made it first, most often none: data-ui's
  panel for a board's Configuration said `/dx/datasets/config.json`, and saved so it read nobody's settings once the page reloaded.
  The page keeps the files each drawn dashboard declares (`openBase`) and the saved panel reads the one declared for its dataset
  (`config.json?path=<the board>`). Data paths are written relative to `dashboards/`, the folder the page opens dashboards from, as
  the starters' are, so a dataset the target already reads is found by its file and not added twice.
- **A dashboard open on the page is drawn again** with the text and etag saved (so its own next save is not refused as somebody
  else's). When the add came from one of its own panels, drawing it again replaces that panel and the picker inside it, so the
  picker first says "Added to …" and the dashboard is drawn again when the reader closes it, or 2 s later.
- **The host** (docs/api-v1.md): `PUT /api/v1/dashboards/<name>.base` honours `If-None-Match` (additive): `*` with a file there, or
  an etag list naming the file as it is now, is **412** `Exists` with its etag, and nothing changes. The page's own New, Duplicate,
  Load and a draft's move now create with it too and take the next free name on a 412: two tabs pressing New at once had both found
  "Untitled dashboard" free, and the second write replaced the first.
- **At this moment stands at its t** (b523933; red first, 0126d70). data-ui gives a pinned panel (a layout panel's `t`) a plain
  `Clock` paused at t, but a view of a live dataset that finds a clock that is not live follows a `LiveClock` of its own
  (`live/feed.js` `_clock`), and every dataset here is live: the panel said "at t = …" and drew the newest data (measured: its clock
  4.3 s past t, its card 0.2228 where the host had −0.2307 at t). After each dashboard draws (`openBase`, and a microtask after
  `data-dashboard-live` for its own redraws, Arrange), `holdPins` pauses each pinned panel's views' own clocks at t
  (`LiveClock.restore({t})`); a clock already standing there is left alone, so nothing is needed from here once data-ui pins a live
  panel itself.

### 17.1 Asks of data-ui (none edited here; the page works around 2-4)

1. **The live `plot`'s legend is a static key** (CONTRACT §26's own "Not yet": not §31's interactive legend), so no plot on /dx/ has
   a series item menu, and Add “velocity” to dashboard… is not offered (the one failing check, below). With §31's legend on `plot`,
   the page needs nothing more: a series panel reads the same file as its plot.
2. **In a dashboard, `panelSpec()`'s `dataset.data` should be the file the dashboard declares for the panel's dataset**, not the
   dataset object's `url`: a host may hand one object to several declared files (this page's shared datasets, §12.1). The page
   puts the declared file back before it appends (`dx/add.js`).
3. **Pin a live panel with a `LiveClock`** restored at its t (`_pinnedClock`), or let `live/feed.js` `_clock` honour a paused plain
   clock. The page pauses the views' clocks (`holdPins`).
4. **Keep the picker across a reload of the dashboard it came from**, or say "Added to …" where it outlives the panel: a host that
   reloads the dashboard it shows (§34's own advice) replaces the panel and the picker inside it before the picker can say anything.
   The page draws its dashboard again when the picker closes, or 2 s after it said so.
5. **Write t as the clock writes it** (minor): "At this moment t = 1792631514.923 s" in the picker and "at t = 1792631514.923 s" on
   the panel, on a wall clock; the playback card already writes wall-clock times for t ≥ 10⁹ s.

### 17.2 Measured (2026-10-29, this PC, load 4-15 on 24 CPUs from other sessions)

**The acceptance** (`tests/test_dx_add_to_dashboard.js`, against a fixture host; the oracles are the host's folder on disk and its own
`GET /api/v1/dashboards`, the requests Chrome sent, data-ui's exported `appendPanel`, and getBoundingClientRect for "at the bottom"):
**3/17 red** at 31a3d3e (data-ui's items are there; nothing listed, nothing saved), **16/17** at 7720e5d (three runs) and at b523933.

| Check | Measured at b523933 |
|---|---|
| a table panel's ⋯ → Add to dashboard… → the picker lists the host's dashboards in its order | PASS (Clash, Retry, Servos) |
| a plot panel's ⋯, the same | PASS |
| a KPI card's item menu (right-click) → "Add “position (rev)” to dashboard…", the same | PASS |
| a legend series' item menu → "Add “velocity” to dashboard…" | **FAIL**: the entry is the live plot's static `.dv-plot-key`, no item menu (17.1, 1) |
| existing: exactly one PUT, `If-Match` = the sha256 of the text appended to, 200 | PASS |
| existing: the file is `appendPanel(old, panel)` byte for byte, the panel reading the board's `config.json?path=` (Servos already declared it: found by its file, not added twice) | PASS |
| "Added to Servos" with Open ↗ to `/dx/?go=dashboards&board=Servos` | PASS |
| Open lands there; the new panel below every other (rects); its rows the source's 8 | PASS |
| after a reload, the same 8 rows, still at the bottom | PASS |
| one 412 (the file changed between the list and the PUT): two PUTs (412 with the old etag, 200 with the new), the file `appendPanel(theirs, panel)`, "Added to Retry" | PASS |
| two 412s: two PUTs, no third; "Could not add to Clash: Clash changed while you were adding; reload it and add again"; the other writer's text kept | PASS |
| New dashboard… "Fresh adds": one PUT with `If-None-Match: *`, 201, the file `appendPanel('', panel)` | PASS |
| New dashboard… "Servos": one PUT with `If-None-Match: *`, 412; Servos.base unchanged, no other file | PASS |
| the dashboard open when an add to it lands: one more panel, at the bottom, its text the host's file, the URL unchanged | PASS |
| every write is the watch heartbeat or a dashboard PUT; no page or console error but the asked 412s | PASS |

**The gate fails when it should:** without the declared-file rule the same test is 11/17 (the three byte-for-byte checks, the open
dashboard, and the reload: the added Configuration panel read no rows). Open's own check passes on that mutant, since the page still
held the board's settings: only the reload shows it, which is why the test reloads.
`tests/test_dx_add_at_moment.js` **2/4 red** at 7720e5d (the panel's clock 4.3 s past t, following) and **4/4** at b523933: the
panel's views stand at t while the page's clock is 4 s on, and its card is −0.1046, the host's own value at t (its history and
/values ring, read by the test). Ad hoc, not in a test: the pin holds through Arrange and a reload beside a live panel; and on a
host that 404s `/api/v1/dashboards`, the picker lists this browser's dashboards by title, adds and creates there, nothing is sent
to the host.

**The rest, at b523933** (all as on main 5575808): `test_dx_page.js` **101/101**, `test_dx_log_toolbar.js` **46/46**,
`test_dx_timeline.js` **27/27**, `test_dx_tree_past.js` **7/7**, `test_dx_time_span.js` **6/6**; K2 idle 25.3 KB/s on the live feed
against 33.2 for the classic page, K5 8 ms software / 13 ms GPU, K7 125 ms. pytest (a detached checkout named `io-diagnostics`):
**827 passed, 1 failed, 1 skipped**: the failure is `test_live_feed.py`'s watch-rate gap (two samples events under 99 ms apart by
`emitted`), the host timing check §15.1 and §16.9 saw flake under the suite's load; alone it passed 5 of 5, and the lane's Python is
the dashboard PUT only (`test_dashboard_files.py` 42/42, one new).

**The bench** (`tools/bench_robotdx.py --phases fixture`, GPU, every run "quiet"), two runs each, interleaved with main 5575808 from a
detached checkout of it:

| KPI (pooled over every sample of the runs) | main 5575808 | this branch b523933 | budget |
|---|---|---|---|
| `first_paint_ms` p50 / p95 (cold loads) | 114.0 / 135.8 (10) | 130.2 / 180.3 (10) | 1,500 |
| `scrub_ms` p50 / p95 | 24.9 / 28.0 (60) | 25.0 / 32.8 (60) | 50 |
| `back_to_live_ms` p50 / p95 | 15.1 / 25.3 (20) | 16.1 / 28.5 (20) | 100 |
| `idle_traffic_kb_s` (per run) | 27, 27 | 27, 27 | 8 |

The first paint's gap did not hold when the loads were paired: single cold loads alternated across main, the re-vendor alone (96c731c)
and this branch, 20 each in a random order per round (each build's own `tools/dx_demo.py`, load 10.9-12): p50 **130.0 / 126.9 /
122.1 ms**; the 330-500 ms loads fell in the same rounds for all three. Interleaving whole runs was too coarse at that load (one of
this branch's later runs had five loads at 240-564 ms, the re-vendor's beside it up to 165).

### 17.3 Left

- **The legend series** (the one failing check): data-ui's (17.1, 1). Nothing to change here once the live `plot` has §31's legend.
- **The picker reads each dashboard's text once** on its first open (one GET each, then only a file whose etag moved): fine for tens
  of dashboards. A host that answered the list with the texts, or data-ui reading a listed `url` only when it is picked while
  keeping a separate `base`, would make it one request.
- **Not measured on /dx/**: the picker at 390 px, the keyboard routes (ContextMenu, Shift+F10) and long-press, and a peek field's item
  (this page turns a row's click into a selection); data-ui's own 70 checks cover them on its fixture.
- **Two choices made here, reversible:** a New dashboard… name that is taken (a file, or a starter's) is refused with the reason,
  not renamed (the reader typed it; New on the Dashboards list still takes the next free name); a dashboard that the add came from is
  drawn again on the picker's close, or 2 s after "Added".

### 17.4 data-ui answered §17.1 (integrate/packet 20bbafe), and the page's workarounds went (2026-10-29)

data-ui 20bbafe merges lane/dx-asks 72a899a: the five asks of §17.1 (CONTRACT §26's, §31's and §34's addenda), with P5's view
hints (§35) beside them. Re-vendored alone first (60f1fba): every check green with the page's workarounds still in
(`test_dx_add_to_dashboard.js` **17/17**: the legend series now has its item menu; the rest as at b523933), so the vendor drifts
nothing for the page. Then the page (eed9fd8):

- **`el.baseUrl = baseUrl` before `el.load()`** in `openBase` (§34: "a host that draws a .base itself with load() sets baseUrl to
  where it lives"). panelSpec's file is then the one the dashboard declares (`dashboardPanel.file`), `config.json?path=<the board>`,
  so the declared-file swap in `dx/add.js` and app.js's `filesOf` / `declaredFile` went. The gate fails without the line: **10/17**
  (the panel says `/datasets/config.json?path=…`, resolved against `/dx/`; the byte-for-byte, Open, reload, 412, New and
  open-dashboard checks fail, with 404s).
- **holdPins went**: a pinned panel's clock is data-ui's own `LiveClock`, restored at its t, which stays there on this page's live
  data. The `data-dashboard-live` listener is `rebind` alone again.
- **A dashboard an add saved is drawn again at once**, also when the add came from one of its own panels: data-ui's picker waits for
  the element of the same id the redraw makes, moves into it and says "Added to …" there. The picker-close / 2 s wait went
  (§17.3's second reversible choice is gone with it).

**Measured at eed9fd8** (this PC, load 5-13 on 24 CPUs): `test_dx_add_to_dashboard.js` **17/17** (unedited), `test_dx_add_at_moment.js`
**4/4**, `test_dx_page.js` **101/101**, `test_dx_log_toolbar.js` **46/46**, `test_dx_timeline.js` **27/27**, `test_dx_tree_past.js`
**7/7**, `test_dx_time_span.js` **6/6**; pytest (a detached checkout named `io-diagnostics`) **828 passed, 1 skipped**. The bench
(`tools/bench_robotdx.py --phases fixture`, GPU, every run "quiet"), two runs each interleaved with main from a detached checkout
of it, every sample pooled:

| KPI | main 5575808 | this branch eed9fd8 | budget |
|---|---|---|---|
| `first_paint_ms` p50 / p95 (cold loads) | 124.7 / 146.2 (10) | 119.1 / 154.9 (10) | 1,500 |
| `scrub_ms` p50 / p95 | 24.7 / 26.7 (60) | 24.3 / 26.8 (60) | 50 |
| `back_to_live_ms` p50 / p95 | 14.0 / 23.6 (20) | 17.1 / 25.7 (20) | 100 |
| `idle_traffic_kb_s` (per run) | 27, 27 | 27, 27 | 8 |
| `ingest_to_pixel_ms` on the fixture host p50 / p95 (emitted) | 9.7 / 18.6 (400) | 10.7 / 18.5 (399) | 30 |

The same within the runs' spread. **Still worked around** (§16.7, not answered by 20bbafe, read in its source): a dashboard's
`load()` of a new document still retires the clock it holds, a host's included (`_retireClock`: `holdClock` stays), and `bindLive`
still lets go of every dataset on its clock that it did not bind (`rebind` stays).

## 18. The Diagnostics tab: each stock diagnostic in data-ui's view for its hint (2026-10-29)

*WHY (Codex audit 2026-10-29, round 2, gap 4): "The newer data-ui Diagnostics tab uses a generic checks table and health history; its
check-row conversion drops the diagnostic view hints and measurements." And Zach, 2026-10-28 (data-ui CONTRACT §35): "Data-UI should
have standard views for showing diagnostics data. Also for showing gates, and KPIs etc. RobotDx should be using those stock parts".
On `lane/robotdx-add-to-dashboard` after §17.4: the acceptance first, red (ac442a0), then the page (606d082). data-ui is the §17.4
vendor (20bbafe, which has P5's §35).*

### 18.1 The row keeps the stock diagnostic

A stock diagnostic's record carries what robotdx.diagnostics' `catalog.dress` put on its Result (`record.to_record`: `dx.*` values and
the measurements, the headline first). `feed.js` `checkRow` now keeps it on the check's row, under PEP 0010 §10's names: `diagnostic`
(the catalog entry's id), the title (the row's `name`), `role`, `family`, `hint`, `emits`, `binding`; the headline (`metric`, `value`,
`unit`, `warn_lo`, `warn_hi`, `error_lo`, `error_hi`); `target`, `better`, `t0`, `t1`, `run`, `basis`, `dx.why` beside what_to_check
under `why`; `measured` (every measurement as the host writes it), `_measurements` (each: key, value, unit, text, level, limits) and
`_payload` (the series or matrix behind the number). Units stay UCUM (`Cel`); the views write them for people. Every check keeps its
measurements, a stock diagnostic or not. Two derived datasets carry what the views draw:

- **`parts`** (keyed, page-wide): each payload as rows, written with its check at the check's time, so they rewind with it: a matrix's
  cells (a gate matrix's verdicts and values, a repeatability floor's pairwise numbers), a series' points (x, y), and a roll-up's tree
  as the producer's own rows (the roll-up with its level, each member with its verdict). The page judges nothing.
- **`measures`** (append, per entity, a stream per check): each stock diagnostic's headline over the host's window, from `/history`
  (a point per evaluation; the window before the page opened too).

**Found by the new test, and fixed:** `_checksBack` (the checks' past from the host's level spans, §15) wrote each span's version at
the span's start, and a span that starts at the report `/node` had just written is the same t: the later write is the newer version
of the key, so right after the page opened every check showed the span's thinner row (no what_to_check, no receive time, and now no
stock view or measurements) until its next report (a plugin that reports every 30 s: up to 30 s). A span's version is now written only
before `/node`'s own. 14/24 before the fix (the nine row checks), 23/24 after.

### 18.2 The views

`app.js` `diagnosticsText` writes the tab: the checks table (every check, worst first) and the Health strips as before, then a stock
view per hint the entity's checks carry; the tab is written again when they change (`sigOf`, kept per write of the checks).

| Hint (§35) | data-ui view | On /dx/ | Measured on the fixture |
|---|---|---|---|
| `gate_matrix` | `tracker` + `column` / `columns` | a matrix per check over its parts: `groupBy` the payload's rows (in their order), `columns` its gates | 8 columns T1-T8, rows CAN 1, CAN 2, all 16 cells the payload's verdicts, 4 pass = `cells_passing` |
| status | `display: status` | the roll-up tree's levels and verdicts; the matrix's cells carry the vocabulary's glyphs | every badge a glyph and its word |
| `threshold_band` | `plot` + `limits: {warn, error}` | a live plot per check over its readings, §35's limits by property | the value over time (newest 52 = the headline); **the band is not drawn** (18.4, 1) |
| `kpi_trend` | `kpi` + `spark` + `target` | a KPI card per check over its readings: `target` and `better` its row's own | 0.5 %, the spark, "target 10%", "−9.5 pts vs target" |
| `rollup_tree` | `table` + `tree: {parent}` + roll-up | a table tree over the roll-up's parts: the producer's roll-up (§35: "RobotDx's causal roll-up … arrives as rows"), not §17's Max of rank (18.4, 2) | the roll-up ok, its 3 members pass, aria-level 1 then 2 |
| `evidence` | `record.sections[].view` | the checks table's side peek: the level, Measured, Evidence (window, run, basis, and a table of the data it was judged on), Stock diagnostic | the floor's 25 pairwise numbers, the largest = `max_pair` |
| `level_history` | `levels` | the Health strip every check already has | its newest bin = the check's level |
| `distribution` | `chart` (histogram / box) | a chart per check: a histogram of a series' points, a heatmap of a matrix's cells | exactly the payload's 3 stop errors (`n` 3) |
| `path_overlay` | `trajectory3d` + `color` / `tolerance` | **left out**: no entity reports a pose path with an error per point (State is numbers per key; the nearest is the arm composite's planar end effector x / y); the scene3d bundle (784 KB) is not vendored for it, and the tab says why beside the check | no 3D view; in the table; the note |
| (a hint with no data-ui view) | none | the checks table (and its Health strip), nothing else | `radar_sweep`: in the table only |

### 18.3 Measured (2026-10-29, this PC, load 5-14 on 24 CPUs from other sessions)

**The acceptance** (`tests/test_dx_diagnostics_hints.js`, a fixture host with the state plugins, headless Chrome; written before the
code and not edited after it). Its oracle is `tests/fixtures/dx_hints/records.json`: one catalog entry per hint (driver.board_temp,
driver.link_loss, driver.faults, servo.stop_error, servo.floor, arm.joints, rack.gate_matrix, arm.tool_error_at_stop) run on small
synthetic inputs and dressed by `catalog.dress`, kept as the dressed Result and as its record, in two frames whose numbers differ, plus
one record that is not the library's (the board temperature's under the hint `radar_sweep`). `catalog.dress` is on
`lane/stock-functions` (14b6320) and not on main yet, so the file was made from `git archive lane/stock-functions robotdx contract` at
29f03b2 by `make_records.py` beside it (`--check` compares it again). The state fixture plugin prints a frame on its CAN 1 when
`$STATE_DX_RECORDS` names the file (opt-in; it merges cleanly with stock-functions' own change to that plugin, `git merge-tree`).
**4/24 red** at ac442a0 (the host serves the fixture; level_history is the Health strip already there; the unknown hint's fallback held
trivially; no errors), **14/24** before the `_checksBack` fix, **23/24** at 606d082:

| Check | Measured at 606d082 |
|---|---|
| the host serves the fixture's 9 stock diagnostics on CAN 1, each with its `dx.hint` (its own `/node`) | PASS |
| row, for each of the 9 (8 hints and `radar_sweep`): the row keeps the dressed view (diagnostic, title, role, family, hint), its headline (metric, value, unit, four limits, target, better) and every measurement | PASS ×9 |
| threshold_band: a plot of the check over time, on screen, its newest value the headline, a warn and an error limit at the limits | **FAIL**: the plot draws the value (newest 52 = the headline) and no limit (`guides` []; 18.4, 1) |
| kpi_trend: a KPI card, its number the headline, its spark, its target | PASS (0.5 %, "target 10%") |
| level_history: a levels strip, its newest bin the check's level | PASS (ok) |
| gate_matrix: a tracker matrix, columns the gates, rows the subjects, every cell the payload's verdict, as many pass as the headline | PASS (T1-T8 × CAN 1, CAN 2; 4 pass) |
| rollup_tree: a table tree, the roll-up with its level, each member under it with its verdict, as many as `members` | PASS (ok; 3 × pass) |
| status: those levels are data-ui's badges, a glyph and the word | PASS |
| distribution: a chart drawing exactly the payload's points, as many as `n` | PASS (3) |
| evidence: the row's peek embeds a view of its data, its values the payload's, the largest `max_pair` | PASS (25 rows) |
| a hint with no data-ui view falls back to the checks table, no stock view draws it | PASS |
| path_overlay with no pose data: no 3D view, in the table, the tab says why | PASS |
| the fixture moved on: live, frame B (the KPI, the plot's newest value, the matrix) | PASS |
| rewind: at a t between the frames (the host's own `/history`: its last A and first B point, 2.0 s apart), every view frame A, each drawn at t (table, levels, kpi, plot, tracker, chart) | PASS |
| …and back to live, frame B | PASS |
| no page error, console error or failed request | PASS |

**The rest at 606d082:** `test_dx_page.js` **101/101** (K2 idle 25.3 KB/s against 33.2 for the classic page, K5 10.3 ms software / 13.1 ms GPU, K7 127 ms),
`test_dx_log_toolbar.js` **46/46**, `test_dx_timeline.js` **27/27**, `test_dx_tree_past.js` **7/7**, `test_dx_time_span.js` **6/6**,
`test_dx_add_to_dashboard.js` **17/17**, `test_dx_add_at_moment.js` **4/4**; pytest (a detached checkout named `io-diagnostics`) **828 passed, 1 skipped** (a second run: the first, at load 14, had `test_live_feed.py`'s watch-rate gap fail, the host timing flake §15.1, §16.9 and §17.2 record, and alone it passed 5 of 5). The bench
(`tools/bench_robotdx.py --phases fixture`, GPU, every run "quiet"; F1 has no stock diagnostic, so it measures that nothing else
moved), two runs each interleaved with main 5575808:

| KPI (pooled over every sample of the runs) | main 5575808 | this branch 606d082 | budget |
|---|---|---|---|
| `first_paint_ms` p50 / p95 (cold loads) | 126.0 / 161.0 (10) | 123.0 / 144.2 (10) | 1,500 |
| `scrub_ms` p50 / p95 | 24.8 / 28.3 (60) | 24.2 / 29.3 (60) | 50 |
| `back_to_live_ms` p50 / p95 | 15.8 / 26.0 (20) | 15.0 / 20.5 (20) | 100 |
| `idle_traffic_kb_s` (per run) | 27, 27 | 27, 27 | 8 |
| `ingest_to_pixel_ms` on the fixture host p50 / p95 (emitted) | 9.4 / 18.7 (399) | 10.4 / 18.6 (404) | 30 |

The same within the runs' spread.

### 18.4 Asks of data-ui (none edited here)

1. **The live plot draws no §35 limits on live data.** It draws limits (and property rules and bands) from the rows a view holds, and a
   plot on a `LiveDataset` gets none (`core/src/live/feed.js`: `LIVE_NATIVE` views read typed arrays, `rows: []`), so `guides.js`
   `limitGuides` finds no series and returns nothing, for numbers as for properties (its series and x extent come from those rows).
   Numeric `rules` are drawn without rows, but y takes in only the property guides' range, so a rule away from the data is off the
   plot: no stand-in either. Measured on /dx/: the board temperature's plot, `input.rows` 0 with 45 readings in its store,
   `plotView.guides` []. Ask: compute a live plot's limits from the store (each stream's rows in its window, or `latestAt`), and take
   its rules into y. The page already writes §35's key, so nothing changes here then, and the one failing check passes.
2. **§17's dataset roll-up reads a `LiveDataset`'s newest rows, not the rows at t** (`dashboard/src/model.js` `applyRollups`: `rows` is
   `snapshot(Infinity)`), and writes its cells onto those row objects, so a roll-up on live data does not rewind. RobotDx's roll-up is
   the producer's anyway (§35); a computed one would need this.
3. **A peek section with nothing for its row still draws** (`record.sections`: "—" for each field, an embedded view's "No rows match
   this view."). The Diagnostics table holds plain checks beside stock diagnostics, so a plain check's peek carries three sections of
   dashes and an empty table. Ask: fold a section whose fields and view are empty for the peeked row (or a `when:` on a section).
4. (minor) A rich cell (`{value, unit}`) in a number column keeps only its value (`live/store.js`), so a KPI card's unit rides a
   column with no type (`measures.reading`).

### 18.5 Left

- **threshold_band's band**: data-ui's (18.4, 1). Until then the plot draws the value; the limits are in the row's peek.
- **catalog.dress on main**: the fixture is frozen from stock-functions 29f03b2; once it lands, `make_records.py --check` re-derives it.
  On main today the host's own bindings write the primitive's hint and its name as `dx.diagnostic` (`stock.box`); stock-functions' host
  writes the catalog entry's hint, role and family, and keeps the entry's id in `dx.binding`. The page keeps both as written.
- **path_overlay**: when an entity reports a pose path with an error per point, vendor scene3d and draw `trajectory3d`.
- **The peek's dashes** for a plain check (18.4, 3). A reversible choice made here: one table for every check, its peek the stock
  diagnostic's sections, rather than a second table of the stock diagnostics alone.
- **Not measured on /dx/**: the tab at 390 px, dark mode, and a real host's own bindings (the fixture plugin prints the records).

### 18.6 The 24th check is green (2026-10-29)

The one failing check in 18.3 (threshold_band's warn and error limits) passes: data-ui answered 18.4, 1 at main `e798a90` (a live plot
draws its §35 limits), and the vendored copy is now that commit (`937d0df`; `VERSION` names it) with nothing else changed here.
`tests/test_dx_diagnostics_hints.js` is **24/24** unedited (the plot's newest value 52, `limits` warn_hi 60 and error_hi 70; frame B 64
with the same limits). The rest at 937d0df: `test_dx_page.js` 101/101, `test_dx_log_toolbar.js` 46/46, `test_dx_timeline.js` 27/27,
`test_dx_tree_past.js` 7/7, `test_dx_time_span.js` 6/6, `test_dx_add_to_dashboard.js` 17/17, `test_dx_add_at_moment.js` 4/4; pytest
(a detached checkout named `io-diagnostics`, the repo's `.venv`) 828 passed, 1 skipped. 18.4, 1 and the first bullet of 18.5 are closed.
