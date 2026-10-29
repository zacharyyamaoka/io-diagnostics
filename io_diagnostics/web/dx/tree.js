/* The entity tree: the classic page's own (docs/peps/0001-robotdx-on-data-ui.md §16), drawn as of the page clock's t.

   Zach, 2026-10-28: "I am fine just using react-arborist or whatever tree we were using before.. please switch back to that
   old entity tree for when you are looking at it as a tree." So this file draws no row. The tree is web/tree/v6.js ("IDE ×
   Notion", the classic page's default style), its rows web/view/core.js with the classic default structure (p6, Nested, on
   p1), its header counts web/summary.js, its "Show N hidden" rows web/visibility.js, all loaded by dx/index.html as the
   classic page loads them. What this file adds is the clock: it rebuilds /state's shape at t from the entities dataset (each
   version keeps the node as the host sent it, feed.js) and hands it to the renderer, which keeps its own open rows in the
   same localStorage as the classic page, so both pages open the same rows.

   Read-only, as this page is (PEP §12.2): the renderer's writes (hide or show a row, expect, remove from bus) are not offered
   here and are blocked if reached; the classic page has them. Group by, Sort by and "show what is hidden" are the classic
   page's own preferences (the same keys), so a choice made on either page holds on both. */

const PREFS = { structure: 'io-diagnostics.structure', sort: 'io-diagnostics.sort', toggles: 'io-diagnostics.structure-toggles' };
const DEFAULTS = { structure: 'p6', sort: 'status' };
// the structures this page loads (index.html): the classic Group by's first three
const GROUPS = [['p6', 'Nested'], ['p1', 'Physical'], ['p7', 'Type']];
const SORTS = [['status', 'Status'], ['name', 'Name']];
// the renderer's own controls that write (web/tree/v6.js): the eye per row, its menu's Hide / Expect / Remove from bus
const WRITES = '[data-t6-eye], [data-t6-mi="vis"], [data-t6-mi^="setup:"], [data-t6-mi="confirm"]';

const read = (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } };
const write = (key, value) => { try { localStorage.setItem(key, value); } catch (_) { /* private window: this visit only */ } };
const readJson = (key) => { try { return JSON.parse(read(key) || '{}') || {}; } catch (_) { return {}; } };

/** The classic page's rows renderer and view model, as index.html loaded them (null when a script did not load). */
export function classicTree() {
  const r = window.IOTreeRenderers && window.IOTreeRenderers.v6;
  return r && window.IOView && window.IOSummary ? { renderer: r, view: window.IOView } : null;
}

/** /state's shape at t from the page's datasets: the tree from the entities' versions in force at t, the summary at t. */
export function stateAt(feed, t) {
  const ents = feed.dataset('entities');
  const rows = ents ? ents.snapshot(t, { at: 'latest', limit: Infinity }) : [];
  const byPath = new Map();
  for (const r of rows) if (r._node) byPath.set(r.path, r._node);
  const root = byPath.get('host') || [...byPath.values()].find((n) => !n.parent) || null;
  const nest = (n, seen) => {
    if (seen.has(n.path)) return null;
    seen.add(n.path);
    return { ...n, children: (n.children || []).map((p) => byPath.get(p)).filter(Boolean).map((c) => nest(c, seen)).filter(Boolean) };
  };
  const sum = feed.dataset('summary') && feed.dataset('summary').latestAt(t, 'host');
  return {
    tree: root ? nest(root, new Set()) : null,
    summary: (sum && sum._summary) || null,
    composition: (sum && sum._composition) || { composites: [] },
    clock: { now: t, rank: feed.host.rank || ['ok', 'warn', 'unknown', 'stale', 'error'] },
    // the versions this state is made of: the same list is the same tree (nothing to draw again)
    sig: `${rows.map((r) => r._seq).join(',')}|${sum ? sum._seq : ''}`,
  };
}

/**
 * Mount the classic tree in `pane` (Tree | Table, the toolbar of the classic Obsidian layout, the search on top).
 * `table` is the data-ui Table view element the Table mode shows. Returns {select(path), destroy()}.
 */
export function mountTree(pane, { feed, clock, table, onSelect }) {
  const classic = classicTree();
  pane.innerHTML = `
    <div class="dx-tree-head">
      <div class="dx-tree-mode" id="dx-tree-mode" role="tablist" aria-label="Tree or table">
        <button type="button" role="tab" data-mode="tree">Tree</button><button type="button" role="tab" data-mode="table">Table</button>
      </div>
      <span class="dx-grow"></span>
      <button type="button" class="dx-ic-btn" id="dx-tree-collapse" title="Collapse all" aria-label="Collapse all"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 20 5-5 5 5M7 4l5 5 5-5"/></svg></button>
      <button type="button" class="dx-ic-btn" id="dx-tree-expand" title="Expand all" aria-label="Expand all"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 15 5 5 5-5M7 9l5-5 5 5"/></svg></button>
      <button type="button" class="dx-ic-btn" id="dx-tree-options" title="View options: group, sort" aria-haspopup="dialog" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg></button>
      <button type="button" class="dx-ic-btn" id="dx-tree-hidden" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"></svg></button>
      <div class="dx-tree-pop" id="dx-tree-pop" role="dialog" aria-label="View options" hidden>
        <label>Group by <select id="dx-tree-group" aria-label="Group by">${GROUPS.map(([id, name]) => `<option value="${id}">${name}</option>`).join('')}</select></label>
        <label>Sort by <select id="dx-tree-sort" aria-label="Sort by">${SORTS.map(([id, name]) => `<option value="${id}">${name}</option>`).join('')}</select></label>
        <p>Shared with the classic page. Hiding a row, Expect and Remove from bus are there (this page only reads).</p>
      </div>
    </div>
    <label class="dx-tree-searchbox"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg><input id="dx-tree-search" type="search" placeholder="Search entities, ids, ports…" autocomplete="off" aria-label="Search entities"></label>
    <div id="dx-tree" class="dx-classic-tree" aria-label="Entities"></div>`;
  const host = pane.querySelector('#dx-tree');
  const search = pane.querySelector('#dx-tree-search');
  const pop = pane.querySelector('#dx-tree-pop');
  const optionsBtn = pane.querySelector('#dx-tree-options');
  const hiddenBtn = pane.querySelector('#dx-tree-hidden');
  pane.append(table);
  const MODE_KEY = 'robotdx.dx.tree-mode';
  const st = { query: '', selected: null, mode: read(MODE_KEY) === 'table' ? 'table' : 'tree', sig: null, raf: 0, built: null, destroyed: false };

  // ---- read-only: the visibility module keeps its arrangement (default-hidden rows, "Show N hidden") but cannot write
  const vis = window.IOVisibility;
  if (vis && !vis.__dxReadOnly) {
    for (const name of ['set', 'toggleRow', 'expireAcks']) vis[name] = () => {};
    vis.__dxReadOnly = true;
    if (typeof vis.load === 'function') vis.load().then(() => repaint(true), () => {});   // GET /api/v1/settings: the host's choices
  }
  const unsubscribe = vis && typeof vis.subscribe === 'function' ? vis.subscribe(() => repaint(true)) : null;
  // WHY blocked as well as hidden (dx.css): a control hidden by a style is still a control; the page's contract is that it sends
  // no write but the State heartbeat and your own dashboard files
  const block = (e) => { if (e.target.closest && e.target.closest(WRITES)) { e.preventDefault(); e.stopImmediatePropagation(); } };
  host.addEventListener('click', block, true);

  // ---- the classic preferences
  const structure = () => { const s = read(PREFS.structure); return GROUPS.some(([id]) => id === s) ? s : DEFAULTS.structure; };
  const sort = () => { const s = read(PREFS.sort); return SORTS.some(([id]) => id === s) ? s : DEFAULTS.sort; };
  const toggles = () => {
    const id = structure();
    const def = (window.IOStructures || {})[id] || {};
    const chosen = readJson(PREFS.toggles)[id] || {};
    const out = {};
    for (const t of def.toggles || []) out[t.key] = t.key in chosen ? Boolean(chosen[t.key]) : Boolean(t.default);
    return out;
  };
  // the structure's first toggle shown (p1's): the classic layout's "Show what is hidden" eye
  const hiddenToggle = () => {
    const def = (window.IOStructures || {})[structure()] || {};
    const opts = toggles();
    const t = (def.toggles || []).find((x) => classic && classic.view.toggleShown(x, opts));
    return t ? { key: t.key, label: t.label, on: Boolean(opts[t.key]) } : null;
  };
  const EYE = '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z"/><circle cx="12" cy="12" r="2.6"/>';
  const EYE_OFF = `${EYE}<path d="m4 20 16-16"/>`;
  function drawHidden() {
    const t = hiddenToggle();
    hiddenBtn.disabled = !t;
    hiddenBtn.setAttribute('aria-pressed', String(Boolean(t && t.on)));
    hiddenBtn.title = t ? `${t.on ? 'Hide' : 'Show'} ${String(t.label).toLowerCase()}` : 'Nothing is hidden in this grouping';
    hiddenBtn.setAttribute('aria-label', hiddenBtn.title);
    hiddenBtn.querySelector('svg').innerHTML = t && t.on ? EYE : EYE_OFF;
  }

  // ---- drawing at t
  const tNow = () => (clock.liveEnd !== null && Number.isFinite(clock.t) && clock.datasets.includes(feed.dataset('entities')) ? clock.t : feed.dataset('entities').end ?? Infinity);
  function repaint(force = false) {
    if (st.destroyed || !classic) return;
    if (force) st.sig = null;
    if (st.raf) return;
    st.raf = requestAnimationFrame(() => { st.raf = 0; draw(); });
  }
  function draw() {
    if (st.destroyed || st.mode !== 'tree') return;
    const t = tNow();
    const state = stateAt(feed, t);
    const sig = `${state.sig}|${structure()}|${sort()}|${JSON.stringify(toggles())}|${st.query}|${st.selected}`;
    if (sig !== st.sig) {
      st.sig = sig;
      const built = state.tree ? classic.view.build(state, structure(), { ...toggles(), query: st.query, sort: sort() }) : null;
      st.built = built;
      classic.renderer.render(host, built ? built.rows : [], {
        onSelect: (key) => { const row = built && built.byKey && built.byKey.get(key); if (row && row.entity) onSelect(row.entity); },
        onSelectEntity: (path) => onSelect(path),
        selectedPath: built && st.selected ? (built.byEntity && built.byEntity.get(st.selected)) || st.selected : null,
        query: st.query, summary: state.summary, host: built ? built.host : null, structure: built ? built.structure : structure(),
      });
      drawHidden();
    }
    host.dataset.t = String(t);
    host.dispatchEvent(new CustomEvent('dx-tree-draw', { detail: { t, following: clock.following, rows: host.querySelectorAll('[role=treeitem][data-t6-path]').length } }));
  }
  const onClock = () => repaint(false);
  clock.addEventListener('time', onClock);
  clock.addEventListener('state', onClock);
  const sets = [feed.dataset('entities'), feed.dataset('summary')];
  const onData = () => repaint(false);
  for (const ds of sets) ds.addEventListener('change', onData);

  // ---- the toolbar
  function setMode(mode) {
    st.mode = mode;
    write(MODE_KEY, mode);
    for (const b of pane.querySelectorAll('#dx-tree-mode [data-mode]')) b.setAttribute('aria-selected', String(b.dataset.mode === mode));
    host.hidden = mode !== 'tree';
    table.hidden = mode !== 'table';
    for (const id of ['#dx-tree-collapse', '#dx-tree-expand', '#dx-tree-hidden']) pane.querySelector(id).hidden = mode !== 'tree';
    if (mode === 'tree') repaint(true);
  }
  pane.querySelector('#dx-tree-mode').addEventListener('click', (e) => { const b = e.target.closest('[data-mode]'); if (b) setMode(b.dataset.mode); });
  // WHY clicks on the chevrons (the classic layouts/host.js does the same): expansion lives inside the tree style; its chevron
  // is its one public control, so the open rows stay the renderer's own
  function each(open) {
    for (let pass = 0; pass < 60; pass += 1) {
      const items = [...host.querySelectorAll(`[role=treeitem][aria-expanded="${open ? 'false' : 'true'}"]`)];
      const toggles = items.map((item) => item.querySelector('[data-t6-toggle]')).filter(Boolean);
      if (!toggles.length) return;
      for (const toggle of open ? toggles : toggles.reverse()) toggle.click();
      if (!open) return;
    }
  }
  pane.querySelector('#dx-tree-collapse').addEventListener('click', () => each(false));
  pane.querySelector('#dx-tree-expand').addEventListener('click', () => each(true));
  hiddenBtn.addEventListener('click', () => {
    const t = hiddenToggle();
    if (!t) return;
    const all = readJson(PREFS.toggles);
    all[structure()] = { ...(all[structure()] || {}), [t.key]: !t.on };
    write(PREFS.toggles, JSON.stringify(all));
    repaint(true);
  });
  optionsBtn.addEventListener('click', () => {
    pop.hidden = !pop.hidden;
    optionsBtn.setAttribute('aria-expanded', String(!pop.hidden));
  });
  const group = pane.querySelector('#dx-tree-group');
  const sortSel = pane.querySelector('#dx-tree-sort');
  group.value = structure();
  sortSel.value = sort();
  group.addEventListener('change', () => { write(PREFS.structure, group.value); repaint(true); });
  sortSel.addEventListener('change', () => { write(PREFS.sort, sortSel.value); repaint(true); });
  search.addEventListener('input', () => { st.query = search.value.trim(); repaint(false); });
  setMode(st.mode);

  return {
    // WHY drawn at once, not at the next frame: Back moves the URL first and the page's selection follows only after the
    // detail of the selection before it has loaded (app.js render queues); the tree marks the row as the URL changes
    select(path) {
      st.selected = path || null;
      if (st.raf) { cancelAnimationFrame(st.raf); st.raf = 0; }
      draw();
    },
    get built() { return st.built; },
    destroy() {
      st.destroyed = true;
      cancelAnimationFrame(st.raf);
      clock.removeEventListener('time', onClock);
      clock.removeEventListener('state', onClock);
      for (const ds of sets) ds.removeEventListener('change', onData);
      if (unsubscribe) unsubscribe();
    },
  };
}
