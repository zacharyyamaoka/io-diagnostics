// Tree proposal v2 — "Notion toggle" for IO diagnostics.
//
// Relies only on docs/api-v1.md (the node shape inside GET /api/v1/state → tree). Registers
//   window.IOTreeRenderers['v2'] = {name, render(container, apiTree, {onSelect, selectedPath, query?})}
//
// The idea: Notion's sidebar. A row is icon · name, nothing else until something needs you. Hovering a row that has
// children swaps its TYPE icon for the disclosure chevron (no permanent chevron column), thin indentation guides
// carry the structure, and a COLLAPSED parent with problems inside shows one small count pill on the right ("2 ▲").
//
// Zach's tree rules (2026-10-22), and where each one lives below:
//   1. chevrons only on hover, over the type icon (Notion)          -> .t2-lead holds .t2-ic + .t2-chev; swap in v2.css
//   2. type icon + name together; status at the far RIGHT           -> rowHtml(): lead, name, fill, pill, id, status
//   3. problems are SHAPES: triangle / octagon-x / clock / ?        -> STATUS_SHAPES
//   4. quiet when OK: no green anywhere, an OK row carries no mark   -> statusHtml() draws nothing for ok / null
//   5. not connected = fully grey; connected = normal grey text     -> .t2-off in v2.css (the problem shape keeps colour)
//   6. black box: a row draws its OWN level; the rollup is a quiet hint -> statusHtml() reads node.level of a
//      self-reporting row only; the rollup appears solely as the collapsed-row pill (pillHtml)
//
// WHY the rollup is shown only on COLLAPSED rows: every independent problem is then drawn exactly once — as its own
// shape when its row is visible, or folded into the pill of the nearest collapsed ancestor. V6 painted the same
// error on every ancestor (host, bus, port, adapter, CAN bus, node: six red dots for one fault), the noise Zach
// asked to calm down (Sources/Images/Pasted image 20261022061923.png). A pure grouping row (own.level = null: the
// host, a USB bus) has no self-report, so it never draws a shape of its own; its rollup is the pill when collapsed.
'use strict';
(function () {
  const WORST_FIRST = ['error', 'stale', 'unknown', 'warn'];
  const WORD = { error: 'error', stale: 'stale', unknown: 'unknown', warn: 'warning' };
  const KNOWN_ICONS = new Set(['host', 'group', 'usb', 'pci', 'net', 'tty', 'video', 'port', 'hub', 'adapter', 'bus', 'servo',
    'chip', 'motor', 'camera', 'stream', 'webcam', 'hid', 'device']);
  const STORE_KEY = 'io-diagnostics.tree.v2.open';
  const STORE_MAX = 400; // remembered expand/collapse choices; paths change on replug, so old ones are pruned
  const SCRIPT_SRC = (document.currentScript && document.currentScript.src) || '';
  // Fields a row actually draws: a re-render is skipped when none changed, so the 1 s poll does not rebuild rows
  // (and drop the hovered chevron) just because age_s ticked. Level words are listed so rollup.counts survive.
  const DRAWN_FIELDS = ['path', 'name', 'kind', 'icon', 'level', 'own', 'rollup', 'hint', 'message', 'knock_on_of',
    'connected', 'stale', 'id_text', 'edge', 'children', 'counts', 'origin', 'origin_name', 'ok', 'warn', 'unknown',
    'error'];

  // ------------------------------------------------------------------------------ icons ----
  // Type icons: 24-unit stroke drawings (Lucide-style, ISC; the same family as web/index.html's sprite), carried here
  // so the renderer is self-contained. Unknown icon names draw `device` (api-v1: closed vocabulary, unknown → device).
  const TYPE_ICONS = {
    host: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    group: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    // an upright USB trident: the slanted Lucide glyph read as a crossed-out pen at 16 px
    usb: '<path d="M12 6v11.5M12 15.5 7 12.5V10.2M12 13.5l5-3V9"/><path d="M12 2.6 14.1 6H9.9Z" fill="currentColor"/><circle cx="12" cy="19.6" r="1.9"/><rect x="5.5" y="7.2" width="3" height="3" rx=".5"/><circle cx="17" cy="7.6" r="1.5"/>',
    pci: '<rect x="3" y="6" width="18" height="10" rx="1.5"/><path d="M6 16v3M9 16v3M12 16v3M15 16v3M18 16v3M7 10h4"/>',
    net: '<path d="m15 20 3-3h2a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h2l3 3z"/><path d="M6 8v1M10 8v1M14 8v1M18 8v1"/>',
    tty: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3M11 15h5"/>',
    video: '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="m16 10 6-3v10l-6-3"/>',
    port: '<rect x="3" y="7" width="18" height="10" rx="1.5"/><path d="M7 11h3M14 11h3"/>',
    hub: '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8"/>',
    adapter: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    bus: '<path d="M3 17h18M6 17v-6M12 17v-6M18 17v-6"/><circle cx="6" cy="8" r="2"/><circle cx="12" cy="8" r="2"/><circle cx="18" cy="8" r="2"/>',
    servo: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/><path d="M12 4v3M20 12h-3M12 20v-3M4 12h3"/>',
    chip: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
    motor: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    stream: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    webcam: '<circle cx="12" cy="10" r="8"/><circle cx="12" cy="10" r="3"/><path d="M7 22h10M12 22v-4"/>',
    hid: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M8 13h.01M12 13h.01M16 13h.01M7 16h10"/>',
    device: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="12" cy="12" r="2"/>',
  };
  // Status shapes, 16-unit, filled with currentColor (the level colour) and a dark glyph, so the SHAPE reads even
  // without colour: warning triangle, error octagon with a cross, stale = a hollow clock (old data, not a new fault),
  // unknown = a disc with a question mark.
  const GLYPH = '#1b1c1f';
  const STATUS_SHAPES = {
    warn: '<path d="M8 1.5c.42 0 .8.22 1.01.58l6.16 10.8c.43.76-.12 1.7-1 1.7H1.83c-.88 0-1.43-.94-1-1.7L6.99 2.08C7.2 1.72 7.58 1.5 8 1.5Z" fill="currentColor"/>'
      + `<path d="M8 6.1v3.6" stroke="${GLYPH}" stroke-width="1.7" stroke-linecap="round"/><circle cx="8" cy="12" r="1" fill="${GLYPH}"/>`,
    error: '<path d="M5.1 1h5.8L15 5.1v5.8L10.9 15H5.1L1 10.9V5.1Z" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linejoin="round"/>'
      + `<path d="m5.6 5.6 4.8 4.8m0-4.8-4.8 4.8" stroke="${GLYPH}" stroke-width="1.8" stroke-linecap="round"/>`,
    stale: '<circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" stroke-width="1.6"/>'
      + '<path d="M8 4.6V8l2.4 1.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    unknown: '<circle cx="8" cy="8" r="7" fill="currentColor"/>'
      + `<path d="M6.05 6.2a2 2 0 0 1 3.9.55c0 1.35-1.95 1.65-1.95 2.95" fill="none" stroke="${GLYPH}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="8" cy="12.1" r=".95" fill="${GLYPH}"/>`,
  };

  const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function ensureAssets() {
    if (!document.getElementById('t2-sprite')) {
      const symbols = Object.entries(TYPE_ICONS).map(([name, body]) => `<symbol id="t2-i-${name}" viewBox="0 0 24 24">${body}</symbol>`).join('')
        + '<symbol id="t2-chev" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></symbol>'
        + Object.entries(STATUS_SHAPES).map(([name, body]) => `<symbol id="t2-s-${name}" viewBox="0 0 16 16">${body}</symbol>`).join('');
      const holder = document.createElement('div');
      holder.innerHTML = `<svg id="t2-sprite" width="0" height="0" style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true"><defs>${symbols}</defs></svg>`;
      document.body.prepend(holder.firstElementChild);
    }
    // the stylesheet sits beside this file; link it once unless the page already did (a switcher may)
    if (SCRIPT_SRC && !document.querySelector('link[data-t2-css], link[href$="tree/v2.css"]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = SCRIPT_SRC.replace(/v2\.js(?:[?#].*)?$/, 'v2.css');
      link.dataset.t2Css = '1';
      document.head.appendChild(link);
    }
  }

  // ------------------------------------------------------------------ expand / collapse memory ----
  const OPEN = (() => {
    try { return new Map(Object.entries(JSON.parse(localStorage.getItem(STORE_KEY) || '{}'))); } catch (_) { return new Map(); }
  })();
  function remember(path, open) {
    OPEN.delete(path); // re-insert so the Map's order is "least recently set first" for pruning
    OPEN.set(path, open);
    while (OPEN.size > STORE_MAX) OPEN.delete(OPEN.keys().next().value);
    try { localStorage.setItem(STORE_KEY, JSON.stringify(Object.fromEntries(OPEN))); } catch (_) { /* private mode: in-memory only */ }
  }

  // Independent problems strictly BELOW a row: the row's own rollup counts minus its own self-report.
  // WHY not the sum of the children's rollups: the host judges knock-on relative to the subtree it rolls up, so a
  // knock-on child counts ITSELF in its own rollup (F2 + 400 s: "CAN 1" stale, knock-on of the busy adapter, has
  // rollup {stale: 1}); summing children would put a coloured "1 ◷" on a folded CAN 1 that the summary never counts.
  function belowCounts(node) {
    const counts = {};
    if (node.rollup && node.rollup.counts) {
      for (const [level, n] of Object.entries(node.rollup.counts)) if (WORST_FIRST.includes(level) && n > 0) counts[level] = n;
      const selfReport = node.own && node.own.level !== null && node.own.level !== undefined;
      if (selfReport && counts[node.level]) counts[node.level] -= 1; // the row's own cause is its shape, not "below"
    } else {
      for (const child of node.children || []) { // older host without rollup: fall back to the children's rollups
        for (const [level, n] of Object.entries((child.rollup && child.rollup.counts) || {})) {
          if (WORST_FIRST.includes(level) && n > 0) counts[level] = (counts[level] || 0) + n;
        }
      }
    }
    for (const level of Object.keys(counts)) if (counts[level] <= 0) delete counts[level];
    return counts;
  }
  const hasProblemsBelow = (node) => Object.keys(belowCounts(node)).length > 0;

  // Default expansion: the frame (host, groups, OS buses, ports) is open; a device, node or plugin bus opens only
  // when something below it needs attention, so quiet branches stay folded (and carry no mark at all).
  function defaultOpen(node) {
    if (node.kind === 'host' || node.kind === 'group') return true;
    if (node.kind === 'bus' && node.depth <= 1) return true;
    if (node.kind === 'port') return true; // a port is a wrapper: the device in it is what you look for
    return hasProblemsBelow(node);
  }
  const isOpen = (node) => (OPEN.has(node.path) ? OPEN.get(node.path) : defaultOpen(node));

  const matches = (node, q) => [node.name, node.id_text, node.path, node.message, node.hardware_id]
    .some((value) => String(value || '').toLowerCase().includes(q));

  function collectRows(tree, query) {
    const q = String(query || '').trim().toLowerCase();
    const keep = q ? new Set() : null;
    if (keep) {
      (function mark(node, trail) {
        if (matches(node, q)) { trail.forEach((path) => keep.add(path)); keep.add(node.path); }
        (node.children || []).forEach((child) => mark(child, trail.concat(node.path)));
      })(tree, []);
    }
    const rows = [];
    (function walk(node, ancestors) {
      if (keep && !keep.has(node.path)) return;
      const kids = node.children || [];
      const hasKids = kids.length > 0;
      const open = hasKids && (keep ? kids.some((child) => keep.has(child.path)) : isOpen(node));
      rows.push({ node, ancestors, hasKids, open, hit: Boolean(keep) && matches(node, q) });
      if (open) kids.forEach((child) => walk(child, ancestors.concat(node)));
    })(tree, []);
    return rows;
  }

  function ancestorsOf(tree, path) {
    let found = null;
    (function find(node, trail) {
      if (found) return;
      if (node.path === path) { found = trail; return; }
      (node.children || []).forEach((child) => find(child, trail.concat(node)));
    })(tree, []);
    return found;
  }

  // ------------------------------------------------------------------------------- one row ----
  function statusHtml(node) {
    const level = node.level;
    const selfReport = node.own && node.own.level !== null && node.own.level !== undefined;
    if (!selfReport || !WORST_FIRST.includes(level)) return '<span class="t2-status"></span>';
    const knock = node.knock_on_of ? ' t2-knock' : '';
    const why = node.knock_on_of ? ' (knock-on: explained by a failed ancestor and counted once, there)' : '';
    return `<span class="t2-status t2-${level}${knock}" title="${esc(`${WORD[level]}: ${node.message || ''}${why}`)}">`
      + `<svg class="t2-shape"><use href="#t2-s-${level}"/></svg></span>`;
  }

  function pillHtml(node) {
    const counts = belowCounts(node);
    const levels = WORST_FIRST.filter((level) => counts[level]);
    if (!levels.length) return '';
    const rollup = node.rollup || {};
    const worst = rollup.origin && rollup.origin !== node.path && rollup.message ? ` · worst: ${rollup.message}` : '';
    const title = `Inside (folded): ${levels.map((level) => `${counts[level]} ${WORD[level]}`).join(' · ')}${worst}`;
    return `<span class="t2-pill" title="${esc(title)}">${levels.map((level) => `<span class="t2-seg t2-${level}"><b>${counts[level]}</b>`
      + `<svg class="t2-mini"><use href="#t2-s-${level}"/></svg></span>`).join('')}</span>`;
  }

  function rowHtml(row, view) {
    const { node, ancestors, hasKids, open, hit } = row;
    const selected = node.path === view.selected;
    const cls = ['t2-row', hasKids && 't2-parent', open && 't2-open', node.connected === false && 't2-off',
      node.knock_on_of && 't2-knocked', selected && 't2-sel', hit && 't2-hit'].filter(Boolean).join(' ');
    // one guide per ancestor; the guide of the selection's scope is a touch brighter (VS Code's active indent guide);
    // a composition edge (part_of: a node's driver / motor, a camera's streams) gets a dotted last segment
    const guides = ancestors.map((ancestor, i) => {
      const on = ancestor.path === view.scope ? ' on' : '';
      const part = i === ancestors.length - 1 && node.edge === 'part_of' ? ' part' : '';
      return `<i class="${on}${part}"></i>`;
    }).join('');
    const icon = `<svg class="t2-ic"><use href="#t2-i-${KNOWN_ICONS.has(node.icon) ? node.icon : 'device'}"/></svg>`;
    const lead = hasKids
      ? `<span class="t2-lead" data-t2-toggle="${esc(node.path)}" title="${open ? 'Collapse' : 'Expand'}">${icon}<svg class="t2-chev"><use href="#t2-chev"/></svg></span>`
      : `<span class="t2-lead">${icon}</span>`;
    const pill = hasKids && !open ? pillHtml(node) : '';
    const id = node.id_text ? `<span class="t2-id" title="${esc(node.id_text)}">${esc(node.id_text)}</span>` : '';
    const title = `${node.name}${node.message ? ` — ${node.message}` : ''}${node.connected === false ? ' (not connected)' : ''}`;
    return `<div class="${cls}" role="treeitem" aria-level="${ancestors.length + 1}"${hasKids ? ` aria-expanded="${open}"` : ''}`
      + ` aria-selected="${selected}" data-t2-path="${esc(node.path)}" title="${esc(title)}">`
      + `<span class="t2-guides">${guides}</span>${lead}<span class="t2-name">${esc(node.name)}</span>`
      + `<span class="t2-fill"></span>${pill}${id}${statusHtml(node)}</div>`;
  }

  // -------------------------------------------------------------------------------- render ----
  const STATES = new WeakMap(); // container -> {root, tree, opts, rows, signature, lastSelected}

  function paint(state, force) {
    const { root, tree, opts } = state;
    if (!tree) {
      root.innerHTML = '<div class="t2-empty">No entities yet.</div>';
      state.signature = '';
      state.rows = [];
      return;
    }
    const selected = opts.selectedPath || null;
    let reveal = false;
    if (selected !== state.lastSelected) {
      // a new selection (a click here, the chip's list, the activity log) unfolds the way to it once
      state.lastSelected = selected;
      reveal = true;
      (ancestorsOf(tree, selected) || []).forEach((ancestor) => { if (!isOpen(ancestor)) remember(ancestor.path, true); });
    }
    const signature = `${JSON.stringify(tree, DRAWN_FIELDS)}|${selected}|${opts.query || ''}`;
    if (!force && !reveal && signature === state.signature) return;
    state.signature = signature;
    state.rows = collectRows(tree, opts.query);
    const selectedRow = state.rows.find((row) => row.node.path === selected);
    const scope = selectedRow ? (selectedRow.open ? selectedRow.node.path : (selectedRow.ancestors.at(-1) || {}).path) : null;
    root.innerHTML = state.rows.map((row) => rowHtml(row, { selected, scope })).join('');
    if (reveal) {
      const element = root.querySelector('.t2-row.t2-sel');
      if (element) element.scrollIntoView({ block: 'nearest' });
    }
  }

  function toggle(state, path) {
    const row = state.rows.find((r) => r.node.path === path);
    if (!row || !row.hasKids) return;
    remember(path, !row.open);
    paint(state, true);
  }

  function choose(state, path) {
    if (!path) return;
    state.opts = { ...state.opts, selectedPath: path };
    paint(state, true); // draw the selection now; the host re-renders with the same path (a no-op) or overrides it
    if (typeof state.opts.onSelect === 'function') state.opts.onSelect(path);
  }

  function onKey(state, event) {
    const rows = state.rows;
    if (!rows.length) return;
    const index = rows.findIndex((row) => row.node.path === state.opts.selectedPath);
    const row = rows[index];
    const go = (i) => choose(state, rows[Math.max(0, Math.min(rows.length - 1, i))].node.path);
    switch (event.key) {
      case 'ArrowDown': go(index < 0 ? 0 : index + 1); break;
      case 'ArrowUp': go(index < 0 ? 0 : index - 1); break;
      case 'Home': go(0); break;
      case 'End': go(rows.length - 1); break;
      case 'ArrowRight':
        if (!row || !row.hasKids) return;
        if (!row.open) toggle(state, row.node.path); else go(index + 1);
        break;
      case 'ArrowLeft':
        if (!row) return;
        if (row.open) toggle(state, row.node.path);
        else if (row.ancestors.length) choose(state, row.ancestors.at(-1).path);
        break;
      case 'Enter': case ' ':
        if (!row || !row.hasKids) return;
        toggle(state, row.node.path);
        break;
      default: return;
    }
    event.preventDefault();
  }

  function bind(state) {
    const root = state.root;
    // WHY data-t2-* attributes (not data-path / data-toggle): the host page delegates clicks on those names at the
    // document level; distinct names keep one click from being handled twice.
    root.addEventListener('click', (event) => {
      const lead = event.target.closest('[data-t2-toggle]');
      if (lead && root.contains(lead)) return toggle(state, lead.dataset.t2Toggle);
      const row = event.target.closest('[data-t2-path]');
      if (row && root.contains(row)) choose(state, row.dataset.t2Path);
    });
    root.addEventListener('dblclick', (event) => {
      if (event.target.closest('[data-t2-toggle]')) return; // the single clicks already toggled it
      const row = event.target.closest('[data-t2-path]');
      if (row && root.contains(row)) toggle(state, row.dataset.t2Path);
    });
    root.addEventListener('keydown', (event) => onKey(state, event));
  }

  function render(container, apiTree, opts = {}) {
    ensureAssets();
    let state = STATES.get(container);
    if (!state || state.root.parentNode !== container) {
      // the root element persists across renders so keyboard focus and listeners survive the 1 s poll
      const root = document.createElement('div');
      root.className = 't2';
      root.tabIndex = 0;
      root.setAttribute('role', 'tree');
      root.setAttribute('aria-label', 'IO entities');
      container.replaceChildren(root);
      state = { root, tree: null, opts: {}, rows: [], signature: '', lastSelected: undefined };
      STATES.set(container, state);
      bind(state);
    }
    state.tree = apiTree || null;
    state.opts = opts || {};
    paint(state, false);
  }

  window.IOTreeRenderers = window.IOTreeRenderers || {};
  window.IOTreeRenderers.v2 = {
    name: 'Notion toggle',
    description: 'Hover a row to swap its icon for the chevron; thin indent guides; a folded parent with problems shows a count pill ("2 ▲").',
    render,
  };
})();
