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
| `skins/claude.css` | the default skin, linked statically (no flash); other ready skins load from `skins/index.json` |
| `app.css`, `tree/v1..v6.css` | the page and the tree styles; colours only through tokens |
| `view/core.js` | `window.IOView` + the registry `window.IOStructures` |
| `tree/v1..v6.js` | `window.IOTreeRenderers` |
| `app.js` | boot: reads both manifests, loads ready skins and structures, then polls `/api/v1/state` |

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
  (`{structureId: {key: bool}}`); v2 keeps its own `io-diagnostics.tree.v2.open`.
- Controls (bottom-right `#controls`): `#structure`, `#structure-toggles [data-opt=<key>]`, `#tree-variant`,
  `#skin`, `#mode-switch [data-mode=auto|light|dark]`. Never a URL flag. Defaults: structure p1, tree v6, skin claude,
  mode follows the OS.
- Attention line `#attention` (under the story line): every `summary.attention` cause, worst first, `.att-where`
  (`device › name`, a link) + `.att-msg` (the plain words); the first one's `what_to_check` lines open
  (`.why-lines > div`, as authored), the others behind `[data-att]`. `S.why` caches each path's checklists from
  `/api/v1/node` (`what_to_check`, `kernel_evidence`). The detail panel's first card is `.card.why` ("What to
  check") for the selection and its folded problem parts.
- Utilities: `.card.utilities > .ugroup.ugroup-{checks|maintenance|motion|more}` > `.urow` > `button.u-btn[data-util=<name>]`
  (icon `svg.u-ic`, `.u-title`, `.u-desc`); a click dry-runs (`S.util`, `POST /api/v1/act`), the panel `.u-panel`
  shows `.u-cmd`, `.u-steps`, `.pre`, `.confirm`, `.u-argv`; `[data-util-run]` runs a query or opens the modal
  (`#modal .modal.m-<danger>`, `[data-util-tick]` for moves, `[data-util-confirm]`); the result is `.u-result`.
- In-page: `window.IOView`; the page's state `S` is a top-level `const` (reach it as `S` from `page.evaluate`, not
  `window.S`): `S.built` is the last `IOView.build(...)` result the page drew (`rows`, `flat`, `byKey`, `byEntity`,
  `host`, `columns`, `options`), `S.structure`, `S.skin`, `S.mode`, `S.sel` (entity), `S.selKey` (group row).
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
| `table message` | the Table's Message column narrower than 240 px at 1600 px with the detail pane open (every structure, its extra columns included) |

`--foundation` runs only the gates; `--mutate` proves the rulers fail (green names, pinned / hidden chevrons, a
faint token too light / too dark, an unbounded table: `no-green`, `chevrons`, both views' `quiet-ok`, `contrast` for
every skin and `table message` must fail). The per-style checks run on p0 (the physical tree they are about); the
foundation gates run every structure with Claude and every skin with p1 (the default). Screenshots:
`reports/media/round2/foundation-<tree|table>-<1811|1825>-<light|dark>.png` (a non-default combo is prefixed
`foundation-<skin>-<structure>-…`).

```bash
/usr/bin/node /home/bam/io-diagnostics/tests/test_utilities.js
```

`test_utilities.js` — the integration acceptance on a fixture server (it refuses to press anything unless only the
fixture plugin is loaded): defaults, the attention line and the detail's What to check (incl. an injected
ExpectedAbsent device in every structure), the Utilities card (groups, icons, dry run, the moves modal, a query's
result), the server's refusals, and every seam above drawn. Screenshots `reports/media/round2/integration-*.png`.

## Decisions taken here (reversible)

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
