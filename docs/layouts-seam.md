# Page layouts: the seam (web/layouts/)

Five proposals for where navigation, search, views and the log live (Zach, 2026-10-24: "the search bar inside of the entity tree on
top just as obsidian does it"). A layout is a **shell**: it re-arranges the page's own parts by moving their DOM nodes into its own
arrangement. It never re-implements a behaviour; selection, search, history, utilities and runs stay the page's. L0 "Today" is the
page as it was (nothing moves). Comparison report: `reports/media/layout-proposals-2026-10-24.html` (main checkout, gitignored).

```js
window.IOLayouts.l1 = { name, description, order, keepTree, reach, mount(ctx), unmount(ctx), update(ctx) };
```

- `layouts/index.json` pre-lists every layout (`ready` flips per lane); the bottom-right **Layout** select lists every one that
  registered `mount()` and remembers the pick in this browser (`io-diagnostics.page-layout`; never a URL flag). Default: the manifest's.
- `keepTree: true` keeps the tree drawn beside the Table and Activity views (app.js hides it outside the Tree view otherwise).
- `reach` is for tests: per view, the control to click (optionally `via` first) and a selector that must then be on screen.
- app.js knows three things: `IOLayoutHost.start(api)` once in `boot()`, `IOLayoutHost.update()` at the end of `render()`, `keepsTree()`.

**ctx** (layouts/host.js): `take(name)` moves a page part out (`search`, `vo`, `voPop`, `inspToggle`, `mode`, `sources`, `main`, `treeHost`,
`runsRoot`, `runsStatus`, `views`; a comment marks its place and unmount puts it back); `treePane({head, tools, below})` builds the
tree pane with the search on top of the tree (`[data-lx-tree]`, what the test measures); `sash({key, axis, anchor, def, min, max, varName,
within, offset, folded})` a draggable boundary that is remembered, resets on double-click, moves by arrow keys and folds
(`lx-fold-<key>` on the root); `tree.collapseAll / expandAll / hiddenToggle / setHidden`; `runs.open / set / toggle`; `activity.draw`
(the one place the log is drawn into a panel); `openViewOptions(anchor)` and `data-vo-anchor` on any trigger; `crumbs(path)`;
`css(text)`, `on(...)`, `cleanup(fn)`, `adopt(node, parent)` (undone on unmount); `go(view)`, `select(path)`, `history`.

**Traps.** The page delegates clicks at the document level on `[data-tab]`, `[data-view]`, `[data-go]`, `[data-key]`, `[data-toggle]`,
`[data-close]`, `[data-util]`: a layout never puts those attributes on its own elements (an ancestor with `data-tab` once turned every
click inside it into a view switch). Colours come from tokens only and no status token (`--io-warn` ...): warn colours belong to
diagnostics, drawn by the page's own shapes (tests/test_layouts.js greps for both).

Gate: `tests/test_layouts.js`. `tests/test_utilities.js` and `tests/test_tree_variants.js` pin L0 (they measure the classic arrangement).
