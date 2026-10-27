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
| K5 scrub: last seek → every panel of the entity drawn at t | n/a | **10-269 ms** end to end in 10 runs (FAIL at 50 ms in 9 of 10); 8-13 ms per seek once paused, every panel's own draw 0.5-3 ms | ≤ 50 ms | the test's own listeners on the clock and `data-views-live`; the slow part is the first frame after leaving live and frame scheduling in headless software rendering on a loaded machine, not the views' JavaScript (no long tasks seen) |
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
