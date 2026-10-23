# IO diagnostics — round 2 seams (the contract for the skin and structure lanes)

*2026-10-22, foundation lane. Spec: `docs/round2-brief-2026-10-22.md`. API underneath: `docs/api-v1.md` (unchanged).
These seams are frozen once the foundation lands: a skin lane writes one `web/skins/<id>.css` and flips its own
manifest line; a structure lane writes one `web/view/structure/<id>.js` and flips its own manifest line. Neither
touches `app.js`, `app.css`, `tokens.css`, `core.js`, a tree style or the tests.*

## What loads, in order (`io_diagnostics/web/index.html`)

| File | Role |
|---|---|
| inline `<script>` in `<head>` | sets `<html data-skin data-mode>` from storage before the first paint |
| `tokens.css` | the token list (BASE + DERIVED), system-colour fallbacks |
| `skins/shadcn.css` | the default skin (stock shadcn/ui since round 3.5; round 3 had "Bare Metal" = `contrast`, round 2 Claude), linked statically (no flash); other ready skins load from `skins/index.json` |
| `app.css`, `tree/v1..v6.css` | the page and the tree styles; colours only through tokens |
| `view/core.js` | `window.IOView` + the registry `window.IOStructures` |
| `tree/v1..v6.js` | `window.IOTreeRenderers` |
| `app.js` | boot: reads the four manifests (skins, structures, `detail/index.json`, `utilities/index.json`), loads every ready file, then polls `/api/v1/state` |
| `detail/<id>.js`, `utilities/<id>.js` | (round 3, §6) `window.IODetailLayouts`, `window.IOUtilityMenus`, loaded by app.js |

Data flow every tick: `GET /api/v1/state` → `IOView.build(state, structureId, options)` → `{rows, byKey, byEntity, …}`
→ the active tree style (`consumes: 'rows'`) **and** the Table draw those rows. Nothing but the legacy styles v3–v5
reads the raw API tree.

## 1. Tokens (`web/tokens.css`)

A skin **must** define every BASE token for `[data-mode="light"]` **and** `[data-mode="dark"]` (the test reads the
skin's own rule through the CSSOM: a token inherited from the fallback counts as missing). DERIVED tokens are
computed in `tokens.css` from the base ones with `color-mix()`; a skin **may** override any of them.

### BASE (39)

| Token | Meaning |
|---|---|
| `--io-bg` | the desk behind the window (`body`) |
| `--io-surface` | main panes: tree, table, detail cards |
| `--io-surface-2` | raised chrome: toolbar, status bar, table header |
| `--io-surface-alt` | the detail pane behind its cards |
| `--io-field` | inputs, selects, the segmented-control track |
| `--io-popover` | chip pop-over, confirm dialog |
| `--io-chrome-bg`, `--io-chrome-text` | the desktop top-bar mock and its text |
| `--io-text` | body text, connected row names ("normal grey text") |
| `--io-text-strong` | headings, the selected / matched row's name |
| `--io-muted` | secondary text: labels, messages, column heads (AA on `--io-surface`) |
| `--io-faint` | tertiary text: ids, captions, counts (AA on `--io-surface`) |
| `--io-off` | not connected: the whole row greyed (exempt from AA, like disabled text) |
| `--io-icon` | type-icon stroke |
| `--io-border`, `--io-border-soft` | pane / card edges; row separators |
| `--io-hover` | row hover background |
| `--io-selected` | selected row background — keep it **neutral** (OK rows carry no colour) |
| `--io-selected-line` | selected row edge / keyboard focus inside a list |
| `--io-accent` | brand accent: open chip, active control, check boxes |
| `--io-accent-text` | links (AA on `--io-surface`) |
| `--io-focus` | focus ring |
| `--io-warn`, `--io-error`, `--io-stale`, `--io-unknown` | fills of the problem SHAPES (≥ 3:1 on `--io-surface`) |
| `--io-on-status` | the glyph knocked out of a filled shape (!, ×, ?) |
| `--io-ok` | the only coloured OK: a passing precondition, a "plugged" event — never a row |
| `--io-info` | neutral facts: query actions, re-enumerated events |
| `--io-shadow` | shadow / backdrop colour (mixed down, never used raw) |
| `--io-font-ui`, `--io-font-mono`, `--io-font-display` | UI text; ids/times/paths; detail title and modal headings |
| `--io-font-size` | body size (13px in Claude) |
| `--io-radius`, `--io-radius-sm`, `--io-radius-lg` | cards/inputs/buttons; rows/tags; pop-over/modal |
| `--io-row-h` | one tree **and** table row (both use it) |
| `--io-indent` | one nesting step |

### DERIVED (28; override optional)

`--io-{warn,error,stale,unknown,ok,info}-text` (level words, tinted names: status mixed 72% with `--io-text-strong`) ·
`--io-{warn,error,stale,unknown,ok,info,accent}-soft` (pill / banner backgrounds, ~15%) ·
`--io-{warn,error,stale,info,accent}-line` (tinted borders) · `--io-knock` (a knock-on shape) · `--io-guide`,
`--io-guide-strong` (indent guides) · `--io-control`, `--io-control-hover`, `--io-pressed` (button / tag / "on"
segment) · `--io-mark` (search highlight) · `--io-overlay` (modal backdrop) · `--io-shadow-pop` (a full
`box-shadow` value) · `--io-radius-pill`.

**Affordances (DERIVED, not colours; integration lane, 2026-10-22):** `--io-focus-width` (focus ring width, 2px;
High contrast 3px) · `--io-link-decoration` (links at rest, `none`; High contrast `underline`) · `--io-guide-style`
(`none`, or `box`: tree style v6 draws box-drawing glyphs as the indent; Terminal sets it) · `--io-guide-glyph` (the
glyphs in order bar, tee, corner, dash; default `"│├└─"`). `tokens.css` also ends with an `@media print` block that
forces a neutral light palette (`html[data-skin][data-mode]` outranks every skin rule), whatever the skin and mode.

Adding a token: add it to `tokens.css` (BASE above the `---- DERIVED` comment, or DERIVED below it) with a one-line
meaning, give it a value in every ready skin, and list it here. The test reads the list from `tokens.css` itself.

## 2. A skin (`web/skins/<id>.css`)

```css
/* Skin <n> · <Name> — <one line: what it imitates and why>. Defines every BASE token of web/tokens.css for both
 * modes; derived tokens come from tokens.css unless overridden below. */
[data-skin="<id>"][data-mode="light"] {
  --io-bg: …; --io-surface: …; --io-surface-2: …; --io-surface-alt: …; --io-field: …; --io-popover: …;
  --io-chrome-bg: …; --io-chrome-text: …;
  --io-text: …; --io-text-strong: …; --io-muted: …; --io-faint: …; --io-off: …; --io-icon: …;
  --io-border: …; --io-border-soft: …;
  --io-hover: …; --io-selected: …; --io-selected-line: …; --io-accent: …; --io-accent-text: …; --io-focus: …;
  --io-warn: …; --io-error: …; --io-stale: …; --io-unknown: …; --io-on-status: …; --io-ok: …; --io-info: …; --io-shadow: …;
  --io-font-ui: …; --io-font-mono: …; --io-font-display: …; --io-font-size: …;
  --io-radius: …; --io-radius-sm: …; --io-radius-lg: …; --io-row-h: …; --io-indent: …;
  /* optional DERIVED overrides, e.g. --io-warn-text: …; */
}
[data-skin="<id>"][data-mode="dark"] { /* the same 39 tokens */ }
```

Rules: only token declarations, inside exactly those two selectors (written exactly like that, so the CSSOM check
finds them). Colour literals are allowed **only** in `tokens.css` and `skins/`. A skin that needs structural CSS
beyond tokens (e.g. Terminal's box-drawing) does not have it today: say so in your handoff rather than adding
selectors — that is a new token or a seam change, not a skin.

`web/skins/claude.css` is the reference: copy it.

### Registering a skin (`web/skins/index.json`)

Every planned skin is pre-listed with `"ready": false`, one per line, blank-line separated (so parallel lanes merge
cleanly). Landing a skin = write `skins/<id>.css` and change **only your own line** to `"ready": true`. The page loads
every ready skin's file at boot and lists it in the Skin picker; the test gates it automatically (below).

| id | name | id | name |
|---|---|---|---|
| `claude` (default, ready) | Claude | `unstyled` | Unstyled |
| `obsidian` | Obsidian | `isa101` | ISA-101 HMI |
| `notion` | Notion | `macos` | macOS Settings |
| `terminal` | Terminal | `linear` | Linear |
| `foxglove` | Foxglove / medkit | `contrast` | High contrast |
| `shadcn` | shadcn/ui | | |

## 3. A structure (`web/view/structure/<id>.js`)

```js
'use strict';
(function (root) {
  const V = root.IOView;                       // view/core.js, loaded before any structure
  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p1 = {
    name: 'Devices, transport as columns',     // shown in the Structure picker as "p1 · <name>"
    description: '…',                          // tooltip
    order: 1,                                  // picker order (p0 = 0)
    toggles: [                                 // optional: the structure's own switches, shown in the cluster
      { key: 'showTransport', label: 'Ports', default: false, title: 'Show USB / PCIe ports as rows' },
    ],
    columns: [                                 // optional: extra Table columns, filled from row.columns[key]
      { key: 'transport', title: 'Transport', width: '200px' },
    ],
    handlesQuery: false,                       // optional: true = rows() filters options.query itself
    rows(state, options) {                     // state = GET /api/v1/state; returns the TOP-LEVEL rows
      return V.topLevel(state, options).nodes.map((node) => V.entityRow(node, options, { children: [/* rows */] }));
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
```

`options` = `IOView.DEFAULT_OPTIONS` `{query: '', showTransport: false, showEmpty: true, showParts: false,
elideSingleHost: true}`, then the structure's own toggle defaults, then the viewer's persisted toggle choices, then
`query` from the search box. A toggle's value arrives as `options[toggle.key]` (boolean).

After `rows()` returns, `core.js` **finalizes** every row (depth, parent, `path` / `name` aliases, default expansion),
applies the search (unless `handlesQuery`) and indexes the rows. A structure never sets `depth` or `parent` itself.

### The row object (what renderers and the Table read)

Build rows with `IOView.entityRow(apiNode, options, extra)` (one API entity) or `IOView.groupRow({key, label, icon,
kind, children, message, defaultOpen, columns})` (a structure's own grouping row, e.g. "Actuators", "Computer").

| Field | Meaning |
|---|---|
| `key` | unique row id (an entity row's key is its entity path; core suffixes duplicates `#n`) |
| `entity` | the API entity path this row stands for (detail panel, `/api/v1/node`), or `null` for a group row |
| `label` | what the row says: `model · id` for identified hardware, else the API name verbatim |
| `model`, `ident` | the two halves of the label (`''` when the label is the API name) |
| `apiName`, `apiDepth` | the API's own `name` and `depth` |
| `idText` | the id column: `id_text`, or `''` when the label already says it |
| `status.own` | the row's OWN problem (`warn|unknown|stale|error`) or `null` — the API self-report, never the rollup |
| `status.hint` | the API's worst-below hint `{level, origin, origin_name, message}` or `null`; on a grouping row its `level` (= rollup). A hint whose origin is one of this row's folded parts is `null` (it is `status.part`) |
| `status.part` | the worst folded part's own problem `{level, entity, label, message, text: "driver: fault 33 …", knock_on_of}` or `null` |
| `status.note` | `status.part.text` when the part is what the mark is about (no own problem, or a worse part), else `''` |
| `parts` | the folded parts `[{entity, name, label, icon, kind, level, message, own, problem, knock_on_of, connected, folded, node}]`; `folded: 'path'` marks a transport layer a structure folded in (it set `folded: 'path'` or `layer: true` on the copy it handed in): the detail panel lists those as "Connection (folded)", the rest as "Parts · composition (part_of)". `partsOf` never collects a part's own `via` children (the live CAN bus is `part_of` its adapter and its boards are `via` the bus: the boards are rows) |
| `children` | child rows (display order) |
| `defaultOpen` | the structure's expansion default (core: open when there is a hint, if unset) |
| `hit` | search matched this row (or one of its parts); present only while searching |
| `columns` | `{key: text}` for the structure's declared Table columns |
| `title` | the tooltip text (label, full API name, message, part, hint, knock-on, not connected, id) |
| `subtitle` | a muted second line under the selected row (in flow) and under a hovered row (an overlay, no layout jump); the Table draws it under the selected row. P3's breadcrumb. `''` = none |
| `lead` | an inline note right after the label, muted (P5's bus words: "error-passive · no ACK …"). `''` = none |
| `suffix` | muted text after the label that is NOT the id (P3's Paths, P4's folded path); the ID column keeps `idText` |
| `attrs` | short muted mono attributes after the label (P4's device nodes `/dev/ttyACM0`), joined with ` · `; `[]` = none |
| `depth`, `parent`, `path`, `name` | set by core: display depth (0 = top level), parent key, aliases of `key` / `label` |
| `kind`, `icon`, `edge`, `level`, `message`, `own`, `rollup`, `hint`, `knock_on_of`, `stale`, `connected`, `hardware_id`, `identity`, `owner`, `id_text`, `last_received`, `age_s`, `expected_period_s`, `stale_after_s`, `checks`, `actions`, `faults` | the API node's fields, unchanged (`hint` minus a part-explained hint) |
| `node` | the raw API node (`null` for a group row) |

### Helpers in `window.IOView` (all pure)

`topLevel(state, options)` → `{nodes, hosts, elided}` — single-host elision · `childrenOf(node, options)` → via
children (part_of only with `showParts`; without it a folded part's own via children are lifted in its place, so the
boards of a part_of CAN bus stay rows) · `isEmpty(node)` — an empty port / bus with no own problem ·
`labelOf(node)` → `{label, model, ident}` · `partsOf(node)` · `hintOf(node)` · `ownProblem(node)` · `isGrouping(node)` ·
`entityRow` · `groupRow` (its hint counts each member's `status.part` too) · `applyQuery(rows, q)` ·
`searchText(row)` (label, names, ids, path, message, hardware id, parts, `row.columns` values, subtitle / lead /
suffix / attrs) · `build(state, id, options)` · `structureIds()` · `defaultOptions(structure)` ·
`toggleShown(toggle, options)` · `RANK`, `PROBLEM`, `DEFAULT_OPTIONS`.

Structure fields beyond `rows()` (integration lane): `toggles[].when` — an option key (the toggle shows only while
that option is on: P2's Empty ports in Connections) or a function(options) → boolean · `hintScope: 'children'` — a
worst-below hint is dropped on every row without children (P5's leaf adapters) · `columns[].mono: true` — the
Table draws that column in `--io-font-mono` (P1 Connection, P3 Path, P4 Device nodes).

Rules every structure keeps (the tests check them on whatever `rows()` returns): rows are entities with `model · id`
labels; no composition part is a row unless the viewer turned it on; one host is elided; a row's own level is the
API's self-report and its hint is the API's (`status.*` comes from `entityRow`/`groupRow`, never hand-written).

### Registering a structure (`web/view/structure/index.json`)

Pre-listed `p0` … `p5`, blank-line separated, all ready since the wave. `"default": "p1"` (integration lane,
2026-10-22: Zach's "fdcanusb → Moteus c1 · CAN 15", the transport as columns, a Ports toggle). A remembered choice
still wins. The page loads ready structures at boot; the Structure picker lists every one that registered `rows()`.

`p0` "Physical (today)" (`view/structure/p0.js`) is the reference: the v1 physical tree through this seam, with
toggles `showParts` ("Parts as rows", off) and `showEmpty` ("Empty ports", on).

## 4. A tree style (`web/tree/<id>.js`, for completeness)

`window.IOTreeRenderers[id] = {name, description, consumes: 'rows', render(container, rows, opts)}` with
`opts = {onSelect(rowKey), onSelectEntity(entityPath), selectedPath: rowKey, query, summary, host: {entity, name,
id_text, elided}, structure}`. `render()` is called every second with the same container and must be idempotent.
Without `consumes: 'rows'` a style is legacy: it gets the raw API tree and is labelled "(legacy)" (v3–v5 today).
Picker order: v6 (default), v1, v2, v3, v4, v5.

## 5. Page state, persistence, test hooks

- `<html data-skin="<id>" data-mode="light|dark" data-mode-pref="auto|light|dark">`.
- localStorage (all wrapped in try/catch): `io-diagnostics.skin`, `io-diagnostics.mode` (`auto|light|dark`),
  `io-diagnostics.structure`, `io-diagnostics.tree-variant`, `io-diagnostics.structure-toggles`
  (`{structureId: {key: bool}}`); v2 keeps its own `io-diagnostics.tree.v2.open`. Round 3 adds `io-diagnostics.sort`
  (`status|name|change`), `io-diagnostics.pane.tree` / `io-diagnostics.pane.inspector` (px), `io-diagnostics.inspector-pinned`
  (`1|0`), `io-diagnostics.detail-layout`, `io-diagnostics.utility-menu`, `io-diagnostics.detail-sections` (the folded
  section ids, a JSON list). Round 3.5 adds `io-diagnostics.detail-sections-open` (the ids of `defaultOpen: false`
  sections the viewer opened); `io-diagnostics.detail-layout` is written only by an explicit Prototype pick.
- Controls (round 3): the **View options** popover `#view-options` (opened by `#vo-btn` beside the search box; a click
  outside or Escape closes it) holds `#structure` (Group by), `#sort`, `#structure-toggles [data-opt=<key>]` (switch
  rows), `#tree-variant`, `#history-window` (round 3.5, in `#window-switch`), `#skin` and `#mode-switch
  [data-mode=auto|light|dark]`. The bottom-right `#controls` holds `#skin-bar` (round 3.5: the same skin list, kept in
  step with `#skin`) and the temporary Prototype picker (`#detail-layout`, `#utility-menu`). Both skin pickers list the
  manifest's `primary` skins first (the default first: shadcn/ui, then Pretty), the `family: "bare-metal"` entries in a
  "Bare Metal" optgroup, the rest under "More skins". Never a URL flag for a choice (the URL carries only the
  navigation, §7). Defaults: structure p1, sort status, tree v6, skin `shadcn`, layout d0 (the Computer: d4), menu u0,
  mode follows the OS. A test opens the popover before `selectOption` (the selects are hidden until then):
  `tests/test_*.js` have a `pick(page, selector, value)` helper.
- Panes (round 3): `#main` is `#left` · `#sash` (`role=separator`) · `#inspector` (`.insp-bar` with `#insp-pin`,
  `#insp-close`, then `#detail`). Tree view: the sash sets `--tree-w` (default 470, min 280, the detail keeps 360). Table
  view: it sets `--insp-w` (default 460, min 320, the table keeps 420); the inspector is folded (`#inspector` width 0) until a
  row is clicked (`.main.insp-open`); the same row, the table's empty space, Escape or `#insp-close` fold it; `#insp-toggle`
  (toolbar) or `]` pins it (`S.inspPinned`). Double-click a sash = its default; arrow keys move a focused sash 16 px.
- Attention line `#attention` (under the story line): every `summary.attention` cause, worst first, `.att-where`
  (`device › name`, a link) + `.att-msg` (the plain words); the first one's `what_to_check` lines open
  (`.why-lines > div`, as authored), the others behind `[data-att]`. `S.why` caches each path's checklists from
  `/api/v1/node` (`what_to_check`, `kernel_evidence`). In d0 the detail's first section is `section.sec.why` ("What
  to check") for the selection and its folded problem parts.
- Utilities (round 3.5: only in the entity header's `.ent-tools`, §7; the rows are u0's): `#ent-util-btn` opens
  `.ent-menu` > `.ugroup.ugroup-{checks|maintenance|motion|more}` > `.urow` (`.busy` while asking / running, `.blocked`
  when not runnable) > `button.u-btn[data-util=<name>]` (icon `svg.u-ic`, `.u-title`, one muted line `.u-busy` or
  `.u-reason`, the danger word `.danger`, the tooltip `.u-desc[role=tooltip]`); a click dry-runs (`S.util`, `POST
  /api/v1/act`) and the sheet `.ent-sheet` shows `.u-panel`: `.u-cmd`, `.pre` (`li.pre-<state>`), `.confirm`, then
  `[data-sheet-more]` "How it runs ▸" over `.u-how` (`.u-steps`, `.u-argv`, always in the DOM); `[data-util-run]` runs a
  query or opens the modal (`#modal .modal.m-<danger>`, `[data-util-tick]` for moves, `[data-util-confirm]`),
  `[data-util-close]` ("Cancel") closes it; while it runs the sheet is gone, the menu row greyed, and `.ent-run.running`
  is the line under the header; done, `.ent-run.ok|.fail` with `[data-run-details]` over `.ent-run-details`.
- Detail sections (d0): `section.sec.<id>[data-sec=<id>]` > `h3.sec-h` > `button.sec-t[data-sec-toggle=<id>]` (+ `.right`),
  then `.sec-b` when open. A check is `#detail .check` (`.check-h`, `.msg`, `table.vt`, `.meta`). Link health is the
  `links` section on the host entity (and on a structure's Computer group row); the Activity view is one column
  (`.act .ev`, a diagnostic event `[data-diagnostic].sev-<level>`) with the `[data-act-filter=all|faults]` filter.
- In-page: `window.IOView`; the page's state `S` is a top-level `const` (reach it as `S` from `page.evaluate`, not
  `window.S`): `S.built` is the last `IOView.build(...)` result the page drew (`rows`, `flat`, `byKey`, `byEntity`,
  `host`, `columns`, `options`), `S.structure`, `S.skin`, `S.mode`, `S.sel` (entity), `S.selKey` (group row); round 3.5:
  `S.drill` (`{diag, dpath, m, page}`), `S.layout` (the layout drawing now), `S.layoutChoice` (the explicit pick or
  `null`), `S.menuOpen`, `S.util` / `S.runs` (an entity's run kept while another is selected), `S.ran` (the page's runs,
  drawn atop Activity), `S.settings` (`GET /api/v1/settings`), `S.cache` (the shared history cache).
- DOM: v6 rows `.t6-row[data-t6-path=<key>]` > `.t6-name`, status `.t6-st > .t6-mark.t6-own|.t6-part > .t6-shape`,
  chevron `.t6-chev` (`.t6-leaf` on leaves); Table rows `table.grid tbody tr[data-key=<key>]` > `td.namecell .row`
  > `.name`, `.note`, `.st > .shape` (+ `.badge`); detail title `.d-title span` (the row's label).

### Tests

```bash
/usr/bin/node /home/bam/io-diagnostics/tests/test_view_core.js
```

```bash
/usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js
```

`test_view_core.js` — core.js + every structure in plain Node: labels, single-host elision, parts folding, query, the
black-box hint passed through, the live part_of CAN bus, the row seams, expected-but-absent devices (synthetic
states, then every row of the real F1 / F2 / F3 frames and the live rack frame
`tests/fixtures/live/rack-state-2026-10-22-1741.json`; every structure keeps every board a row). `--unit` skips the
fixture servers (the live frame is read from disk).

`test_tree_variants.js` — headless Chrome on fixture servers F1 / F2 (each started on a free port and stopped by its
process group): the per-style checks for v1–v6, then the **foundation gates** for every ready skin (with p0) and every
ready structure (with Claude), in light and dark, for the v6 tree and the Table:

| Gate | Fails when |
|---|---|
| `literals` | a colour literal (hex, `rgb()`/`hsl()`/…, a named colour) in a declaration of `app.css` or `tree/*.css` |
| `var-refs` | any stylesheet or tree style reads a `var(--io-*)` that is not in `tokens.css` |
| `mode` | the Light / Dark control does not set `data-mode`, or a reload loses it |
| `tokens` | a token resolves empty on `<html>`, or the skin's own rule for the mode omits a BASE token |
| `quiet-ok` | an OK row (no own problem, no part problem, no hint) paints any colour (sRGB chroma ≥ .12) |
| `shapes` / `right` | a row with an own or part problem has no visible shape, or the shape sits left of the name |
| `labels` | a drawn name differs from the row's label, a part is a row, nothing is folded, or no `fdcanusb · …` label |

| `contrast` | (every skin, both modes, with p1) a text token (text, text-strong, muted, faint, accent-text, the six level `-text`) under 4.5:1 on surface, surface-2, surface-alt or popover, or a problem shape (warn, error, stale, unknown) under 3:1 on surface or on the selected row — measured on the browser's own resolved tokens |
| `table message` | the Table's Message column narrower than 240 px at 1600 px with the detail pane open (every structure, its extra columns included; round 3: the gate clicks a row first, the inspector is folded until then) |
| `warn-colours` | (round 3; the default skin, both modes) outside status marks (`.shape`, the top-bar chip) and true diagnostic rows / messages (`[data-diagnostic]`), an element computes to a warn / error / stale / unknown colour or its `-text` / `-soft` / `-line` tint: the Utilities section at rest, a moves dry run and its preconditions, its confirm modal, a blocked dry run, the activity log's non-diagnostic tags, View options and every control button (a gated colour that equals a neutral token in the skin is not distinguishable and not gated) |

`--foundation` runs only the gates; `--mutate` proves the rulers fail (green names, pinned / hidden chevrons, a
faint token too light / too dark, an unbounded table, and a warn / error / stale / unknown token on a utility's reason,
a danger word, a precondition, a primary button, a non-diagnostic activity tag and the View options button:
`no-green`, `chevrons`, both views' `quiet-ok`, `contrast` for every skin, `table message` and `warn-colours` naming all
six must fail). The per-style checks run on p0 (the physical tree they are about); the foundation gates run every
Group by structure with the default skin and every skin with p1 (the default). Screenshots:
`reports/media/round2/foundation-<tree|table>-<1811|1825>-<light|dark>.png` (a non-default combo is prefixed
`foundation-<skin>-<structure>-…`).

```bash
/usr/bin/node /home/bam/io-diagnostics/tests/test_utilities.js
```

`test_utilities.js` — the integration acceptance on a fixture server (it refuses to press anything unless only the
fixture plugin is loaded): defaults, the attention line and the detail's What to check (incl. an injected
ExpectedAbsent device in every structure), the Utilities card (groups, icons, dry run, the moves modal, a query's
result), the server's refusals, and every seam above drawn. Screenshots `reports/media/round2/integration-*.png`.
Round 3 adds: Group by's list and a remembered p3 falling back; u0's tooltip (hidden at rest, on hover, on keyboard
focus), danger as a plain word, the busy row (a run held 2.6 s at the network: greyed, "Checking communication… N s",
no panel, no live output); the **panes** (each sash dragged with the mouse, reloaded, measured; minimum widths; a
double-click resets; the inspector folded, opened by a row, folded by the same row / Escape / the empty space / its close
button, pinned by `]` and the toolbar toggle, remembered); screenshots `reports/media/round3/a-*-<light|dark>.png`.

## 6. Round 3: detail layouts and utility menus (`web/detail/<id>.js`, `web/utilities/<id>.js`)

*2026-10-23, lane R3-A. Spec: `docs/round3-brief-2026-10-23.md`.* Two registries, manifest-driven like skins and
structures, so the proposal lanes each rewrite ONE file: `detail/index.json` (`d0` + stubs `d1`-`d5`) and
`utilities/index.json` (`u0` + stubs `u1`-`u5`), every entry ready, blank-line separated. A lane rewrites only its own
`<id>.js` and may rename only its own manifest line. The page loads every ready file at boot; the temporary bottom-right
**Prototype** picker (`#detail-layout`, `#utility-menu`) switches both live and remembers them. Nothing else changes for a
lane: the page keeps the data, the polling, the selection, the dry-run / confirm / token flow and the modal.

### A detail layout

```js
'use strict';
(function (root) {
  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d1 = {
    name: 'Diagnostics first',      // the Prototype picker shows "d1 · <name>"
    description: '…',               // its tooltip
    order: 1,                       // picker order
    // Called every second (and on every change). container = #detail; detail = GET /api/v1/node for the selection,
    // or null while it loads and for a structure's grouping row (then ctx.group is set). Idempotent: draw through
    // ctx.draw(html), which writes only what changed (hover, focus, caret and scroll survive).
    render(container, detail, ctx) {
      ctx.draw(`${ctx.parts.head()}<div class="d-body">${ctx.section('checks', 'Checks', ctx.parts.checks())}</div>`);
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
```

`ctx` (built by `detailContext()` in app.js):

| Field | What it is |
|---|---|
| `detail` | the `/api/v1/node` JSON (`node`, `ancestors`, `checks` [name, level, effective_level, message, code, values, measurements, received_iso, age_s, expected_period_s, stale_after_s, source], `actions`, `faults`, `events`, `setup`), or `null` |
| `node` | the selection's node from `/api/v1/state` (always there for an entity: level, message, own, rollup, hint, knock_on_of, stale, connected, identity, hardware_id …) |
| `row` | the active structure's row for exactly this entity (label, parts, status …; `null` for a folded part) |
| `group` | a structure's own grouping row when one is selected (`node` and `detail` are `null`) |
| `isComputer` | the host entity, or a structure's Computer group row (link health belongs there) |
| `counts` | `{connection, parts, checks, members}` for section titles |
| `state`, `activity`, `byPath`, `view` | `/api/v1/state`, `/api/v1/activity`, path → state node, `tree|table` (the Table's inspector is narrower) |
| `parts.<name>()` | one section's BODY html, `''` when there is nothing to show: `head` (title, level, message, path — not a section), `banners` (knock-on, stale, not connected), `whatToCheck`, `setup` (Expected setup + its three edits), `status`, `identity`, `connection` (folded transport layers), `parts` (composition), `checks` (every check as `.check`), `faults`, `events`, `links` (link health; `''` unless `isComputer`), `members` (a group row's members). Round 3.5: no `utilities` part (§7: they live in the header) |
| `section(id, title, body, {right, defaultOpen})` | the collapsible section (`section.sec.<id>`, `h3.sec-h` > `button[data-sec-toggle]`); `''` when body is `''`; folded / opened state remembered per id across entities; `defaultOpen: false` (round 3.5) starts it folded until the viewer opens it |
| `isOpen(id, byDefault = true)` | whether that section is open |
| `h` | helpers: `esc`, `icon(name)`, `shape(level, title)` (the status shape; `''` for OK), `pill(level)` (round 3.5: a problem pill carries `data-diagnostic`), `ago(seconds)`, `kv([[k, v]])` (a key/value grid, values are HTML), `statusMark(row)`, `eventRow(event)`, `checkBlock(check)` |
| `draw(html)` | write the panel (only what changed); the page adds its own header tools and run line (§7) |
| `diagnostic`, `page` | (round 3.5) the page-level drill: the open diagnostic `{name, path, measurement}` (`path` = the entity whose check it is: the selection or one of its parts; `measurement` = a key, or `null`), or `null`; `page` = a layout's own sub-page id (d5: `about`, `events`, `links`, `setup`) or `null`. Both `null` on a grouping row |
| `openDiagnostic(name, {path, measurement, select})`, `openPage(id)`, `closeDiagnostic()` | (round 3.5) one browser-history entry each: open a diagnostic (of `path`, default the selection; `select: true` selects `path` first, as d4's part measurements do), a measurement below it, a sub-page; `closeDiagnostic()` goes one level up (measurement → diagnostic → entity), and IS the browser's Back when the viewer came down from that level |
| `history(path, {status, everyMs})`, `bar(path, bins = 60, {everyMs})` | (round 3.5) the shared history cache: `/api/v1/history` and `/api/v1/history/bar`, an entry `{data, error, loading, at}` (`data` `null` until the first answer; a failed refresh keeps the last answer and sets `error`); refreshed at most every `everyMs` (3 s) while asked for; a landed answer redraws the detail; cleared when the history window changes |
| `redraw()` | (round 3.5) draw the detail again now (a layout's own click, a hover scrub) |
| `settings` | (round 3.5) `GET /api/v1/settings` (`history_window_s`, `source`, `limits`), or `null` |

Page hooks a layout's HTML may use: `[data-go=<entity path>]` selects an entity; `[data-sec-toggle=<id>]` folds a
section; everything the parts emit carries its own hooks (utilities, setup). Classes: `.d-head`, `.d-body` (the compact
reading width, `--io-detail-max`), `.sec`, `.kv`, `.vt`, `.check`, `.part-row`, `.banner`; colours only through tokens,
warn / error colours only on diagnostics (the `warn-colours` gate).

### A utility menu

```js
'use strict';
(function (root) {
  root.IOUtilityMenus = root.IOUtilityMenus || {};
  root.IOUtilityMenus.u1 = {
    name: 'Icons only', description: '…', order: 1,
    html(ctx) {                      // → the Utilities section's body (a string); called whenever the detail draws
      return ctx.actions.map((action) => `<button class="u-btn" data-util="${ctx.h.esc(action.name)}">…</button>${ctx.panel(action)}`).join('');
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
```

`ctx` (built by `utilityContext()` in app.js):

| Field | What it is |
|---|---|
| `actions` | the entity's actions from `/api/v1/node` (name, title, description, danger `query|interrupts|changes|moves`, state `ready|blocked|nothing_to_do|unknown`, reason, preconditions, confirm, owner, scope) |
| `groups` | `[{id: checks|maintenance|motion|more, title, items}]`: the standard order (docs/actions-vocabulary-v0.1.md), empty groups dropped |
| `path`, `owner`, `active` | the entity, the plugin that runs them, `S.util` when a utility of this entity is open (`{name, phase: dry|shown|confirm|running|done, dry, result, …}`) |
| `isActive(action)`, `phase(action)` | whether this action is the open one; its phase (`idle` otherwise) |
| `runnable(action)` | its state lets it run (`ready` / `unknown`) |
| `needsId(action)`, `waiting(action)`, `newIdBox(action)` | set_id names its new CAN id in its row: the box html (`[data-util-newid]`); `waiting` = no valid id yet, so its button must stay disabled |
| `busy(action)` | the quiet line while it is asked or runs ("Asking moteus_servo what it would run…", "Checking communication… 3 s"), else `''` |
| `panel(action)` | the dry-run panel html when it is the open one (`''` while asking / running): what will run, steps, preconditions, the confirm text, Run / Continue…, the result |
| `h` | `esc`, `utilIcon(name)` (`svg.u-ic`), `plainTitle(title)`, `gerund(title)`, `shape(level)` |

The flow is the page's and is not optional: a `[data-util=<name>]` click is ALWAYS a dry run (`POST /api/v1/act`
`dry_run`); Run (a query) and Continue… live in `ctx.panel()`; anything but a query runs only from the confirm modal
(moves: its `[data-util-tick]` box), with the dry run's token. A menu must render `ctx.panel(action)` for the open
action and keep set_id's button disabled while `ctx.waiting(action)`. Warn / error colours are reserved for diagnostics:
danger is a word or an icon (`.danger.<danger>` is neutral in app.css). Round 3.5: a menu draws the ROWS of the header's
Utilities ▾ dropdown (§7); `ctx.panel()` returns `''` there, the dry run being the page's sheet.

## 7. Round 3.5: one Utilities place, action feedback, back / forward

*2026-10-23, round-3.5 web lane (Zach's feedback: "Lets please just use stock shadcn for now ... what matters is the
location of things"; "have that drop down button in the header of the entity page ... so I always know where it is";
"what happens when you press update firmware"; "As I drill down please support forwards/backwards buttons on the
mouse").*

**Skins.** `shadcn` is the default (`skins/index.json` `default`, index.html's inline script and its static `<link>`),
light / dark following the OS until the viewer picks a mode. Every skin stays one pick away in `#skin-bar` (bottom-right)
and `#skin` (View options). A remembered choice wins.

**The Utilities ▾ (drawn by app.js for every layout).** After a layout's `ctx.draw(html)` the page puts `.ent-tools` at
the top-right of the html's first `.d-head` (`.d-head.has-tools`; 12 px from its top, 14 px from the pane's right edge,
the same place on every entity and layout; a layout that drew no `.d-head` gets one), with `#ent-util-btn` "Utilities ▾"
(disabled, never absent, where the entity has no actions or a grouping row is selected). The button opens `.ent-menu`:
the active utility menu's rows (u0's quiet rows by default: icon + title, the description as its tooltip, Checks /
Maintenance / Motion / More), light-dismissed by a click elsewhere or Escape. No layout draws utilities; `[data-util]`
exists only inside `.ent-tools`. A layout may open the menu from its own words with `[data-util-menu]` (d3's "Update
firmware is under Utilities").

**The flow (feedback without another modal).**
1. Picking an item closes the menu and dry-runs it (`POST /api/v1/act` `dry_run`, page-only): the sheet `.ent-sheet`,
   anchored under the button, says "Asking <plugin> what it would run…", then what will run (`.u-cmd`), its
   preconditions, the plugin's confirm text, a blocked reason in neutral words, "How it runs ▸" (steps, the plugin
   command line), and Run <title> (a query) or Continue… plus Cancel. Escape or Cancel closes it; nothing ran.
2. Anything but a query: Continue… opens the existing confirm modal (moves: its ticked precondition box; set_id: CAN A →
   CAN B). That modal is a safety gate, not feedback; the run carries the dry run's token.
3. Running: the sheet is gone; one quiet line `.ent-run.running` sits right under the entity header, in muted text:
   "Updating firmware on moteus-c1 · CAN 21… 42 s" (the action's gerund, the row label, seconds). Its menu row greys
   out. Never a warning colour (the `warn-colours` gate measures it).
4. Done: the line becomes the result, `.ent-run.ok`: the host's own message ("Firmware updated to 1.1.2") · just now
   (then "N s ago") · Details ▸ (`[data-run-details]`: the run's records, rejected lines, stderr tail, plugin · outcome ·
   duration · exit · argv). A failure (refused, not run, a driver refusal) is `.ent-run.fail[data-diagnostic]` with the
   error shape and `--io-error-text`: the only coloured line of the flow.
5. The result lands in Activity (the page's own runs first, kind `utility`, newest first; the host logs no action runs
   yet) and the entity re-reads its node at once (and the activity log). A run belongs to its entity: selecting another
   keeps it (`S.runs`) and the line comes back with the entity.

**Back / forward.** Every entity or grouping-row selection, entity → diagnostic drill, measurement page, layout sub-page
and Tree / Table / Activity switch is `history.pushState` with a URL that restores it: `?sel=<path>` (or
`group=<row key>`) `&diag=<status name>` `&dpath=<entity of that check, when not the selection>` `&m=<measurement key>`
`&page=<sub-page id>` `&view=tree|table|activity`; popstate (and a reload) puts that exact state back, each step with the
scroll position it had. The page's own first selection replaces rather than pushes. So the browser's Back / Forward, the
mouse's back / forward buttons and Alt+← / → walk the drill-downs, in a tab and in the Chrome `--app` window (they are the
browser's own; the page blocks none of them). Layouts drill only through `ctx.openDiagnostic` / `openPage` /
`closeDiagnostic` (§6) and keep no stack of their own; d5's breadcrumb, Esc and Backspace go through it too.

**Default detail layout.** `d0`, except the Computer (the host entity or a structure's Computer row): `d4` "Resource
monitor" (Zach via the orchestrator, 2026-10-23: RAM, CPU, swap, load, disks, temperatures, GPU, network, USB). An
explicit Prototype pick (`io-diagnostics.detail-layout`) wins on every entity.

**History window.** View options' `#history-window` (1 min … 24 h within the host's `limits`) reads `GET /api/v1/settings`
and writes `POST /api/v1/settings {"history_window_s": N}` with the page's `X-IO-Diagnostics: ui` header (the same
page-only rule as `/api/v1/act`); the host writes settings.toml; the shared history cache is cleared and every bar and
trend answers over the new window.

Tests (`tests/test_utilities.js`): the shadcn default (light and dark), the bottom switcher (lists every ready skin,
switches, follows View options, remembered); the Utilities ▾ in every layout's header at the same place on three entities
and the utilities nowhere else; the sheet, the running line (held 2.6 s at the network), the result line and its
Details, a failed run's coloured line, the Activity entry and the re-read; page.goBack / goForward, the layout's own ←, the
mouse's back / forward buttons (CDP input events), a reload, a d5 measurement step; the Computer in d4 on a fresh profile
and an explicit pick sticking; the history window on a server with its own `IO_DIAGNOSTICS_CONFIG_DIR`.
Screenshots `reports/media/round3/r35-*.png`. Alt+← / → is a browser-UI accelerator headless Chrome does not have: not
driven by the tests.

## Decisions taken here (reversible)

- (round 3.5) The Utilities ▾ anchors to the layout's first `.d-head` (not the pane's corner): in d5 it sits below the
  breadcrumb, at the header's own top-right. The utility-menu picker stays: its menu draws the dropdown's rows (u0 by
  default); a menu that draws its own trigger (u2) then shows it inside the dropdown.
- (round 3.5) The dry-run sheet keeps the steps and the plugin command line behind "How it runs ▸" (in the DOM, folded):
  the brief asked for a small sheet (what will run, preconditions, Run / Cancel).
- (round 3.5) A run's line shows the row label ("on moteus-c1 · CAN 21"), not only the id part.

- (round 3) Sort by is stable at every level. A container row (a structure's grouping row, or the host's own row: P1's
  Computer) stays last under Name and Last change; under Status a problem anywhere comes first. A rig row (kind `rig`,
  the expected setup's summary) stays first under every sort. "Last change" = the newest `/api/v1/activity` event at the
  row's entity, its parts or anything below it (the API has no per-entity change time); a row with none keeps the
  structure's order after them.
- (round 3) A stored structure that left Group by (p3) falls back to the default instead of being kept invisibly.
- (round 3) The detail is patched node by node (`morph()` in app.js), not rewritten: its ages change every second and a
  rewrite restarted u0's hover tooltip each tick.
- (round 3) A utility's run result keeps a status shape when it failed (it is the outcome of a run, a status mark); its
  frame and heading are neutral. A passing precondition keeps `--io-ok-text` (the one coloured OK, tokens.css).
- (round 3) Tree style v5's fixed 56 % width is gone: the tree pane has one draggable width in every style.

- (integration) The CAN bus in F1-F3 is now `part_of` its adapter, boards `via` the bus, as the live moteus plugin
  sends it (`tests/fixtures/plugins/live_shape.py`, idempotent; it also adds `what_to_check` and the standard action
  vocabulary; the boards' recorded `reset` stays, pinned by tests/test_host_api.py, so it shows under More).
- (integration) `labelOf` unwraps a parenthesis that wraps the whole id: the live "RealSense D405 (USB 219323070276)"
  reads `RealSense D405 · USB 219323070276`; an inner parenthesis ("4411 (SDK)") stays. The full name is in the tooltip.
- (integration) The foundation ruler counts a composition part (kind `part` / `stream`) as a part row, not any row
  whose edge is `part_of`: the bus P5 draws as its top-level row is `part_of` its adapter in the live shape.

- Labels render the model verbatim (`moteus-c1 · CAN 15`, not "Moteus c1"): stored text renders as authored; the
  full API name is in the tooltip and the detail path line. The brief's example prettified it.
- A folded part's problem is drawn on its owner with the part's level (a shape when the owner has no own problem; a
  badge dot when the owner's own problem is milder) and its words (`driver: fault 33 …`), so the independent cause is
  still drawn exactly once. The owner's own level stays its self-report.
- v3–v5 still draw the raw physical API tree (labelled "legacy"; the Structure picker dims while one is active).
