// Tree proposal v1 — "Quiet IDE tree" (the VS Code explorer feel) for IO diagnostics.
//
// Round 2: consumes display rows from the active structure (web/view/core.js; docs/round2-seams.md), never the
// raw API tree. A row carries the API node's fields plus label / status / parts / defaultOpen. Registers
//   window.IOTreeRenderers['v1'] = {name, consumes: 'rows', render(container, rows, {onSelect, selectedPath, query?, summary?})}
//
// Zach's tree rules (2026-10-22), and where each one lives below:
//   1. chevrons only on hover, in a left gutter (IDE style)       -> .t1-chev, opacity in v1.css
//   2. type icon + name together; status at the far RIGHT          -> rowHtml(): icon, name, id, then .t1-st
//   3. problems are SHAPES: triangle / octagon-x / clock / ?       -> SHAPES
//   4. quiet when OK: no green anywhere, OK rows carry no mark     -> statusHtml() returns '' for ok
//   5. not connected = fully grey; connected = normal grey text    -> .t1-off / default text colour in v1.css
//   6. black box: a row shows its OWN level; the rollup is a quiet hint (a small dot, never a shape)
'use strict';
(function () {
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const WORST_FIRST = ['error', 'stale', 'unknown', 'warn'];
  const WORD = { error: 'error', stale: 'stale', unknown: 'unknown', warn: 'warning' };
  const INDENT_PX = 12; // keep equal to --t1-indent in v1.css (the indent guides are drawn on that pitch)
  const KNOWN_ICONS = new Set(['host', 'group', 'usb', 'pci', 'net', 'tty', 'video', 'port', 'hub', 'adapter', 'bus', 'servo',
    'chip', 'motor', 'camera', 'stream', 'webcam', 'hid', 'device']);

  // ------------------------------------------------------------------------------ icons ----
  // Type icons: 24-unit stroke drawings in the same family as web/index.html's sprite, carried here so the
  // renderer is self-contained (it may be hosted by a page that has no sprite).
  const TYPE_ICONS = {
    host: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    group: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    usb: '<circle cx="12" cy="20" r="2"/><path d="M12 18V3M12 3 9.5 6M12 3l2.5 3M12 14l-5-3V8M12 12l5-3V7"/><rect x="5.6" y="5.6" width="2.8" height="2.8" rx=".4"/><circle cx="17" cy="6" r="1.4"/>',
    pci: '<rect x="3" y="6" width="18" height="10" rx="1.5"/><path d="M6 16v3M9 16v3M12 16v3M15 16v3M18 16v3M7 10h4"/>',
    net: '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-3h14v3"/>',
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
  const CHEVRON = '<path d="m6 4 4 4-4 4"/>';
  // Status SHAPES (16-unit, filled where the shape carries the meaning): the shape alone must read without colour.
  const SHAPES = {
    warn: '<path class="t1-fill" d="M7.13 2.2a1 1 0 0 1 1.74 0l6.03 10.6a1 1 0 0 1-.87 1.5H1.97a1 1 0 0 1-.87-1.5Z"/>'
      + '<path class="t1-ink" d="M8 6.1v3.4"/><circle class="t1-ink-dot" cx="8" cy="11.7" r=".95"/>',
    error: '<path class="t1-fill" d="M5.3 1.2h5.4l4.1 4.1v5.4l-4.1 4.1H5.3l-4.1-4.1V5.3Z"/>'
      + '<path class="t1-ink" d="m5.7 5.7 4.6 4.6m0-4.6-4.6 4.6"/>',
    stale: '<circle class="t1-line" cx="8" cy="8" r="6.2"/><path class="t1-line" d="M8 4.6V8l2.3 1.5"/>',
    unknown: '<circle class="t1-line" cx="8" cy="8" r="6.2"/>'
      + '<path class="t1-line" d="M6.25 6.3a1.85 1.85 0 0 1 3.6.6c0 1.25-1.85 1.5-1.85 2.75"/><circle class="t1-dot" cx="8" cy="11.6" r=".9"/>',
  };

  const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const typeIcon = (name) => `<svg class="t1-ic" viewBox="0 0 24 24" aria-hidden="true">${TYPE_ICONS[KNOWN_ICONS.has(name) ? name : 'device']}</svg>`;
  const shape = (level, label) => `<svg class="t1-shape t1-${level}" viewBox="0 0 16 16" role="img" aria-label="${esc(label)}">${SHAPES[level]}</svg>`;

  // ------------------------------------------------------------------- what a row shows ----
  const isGrouping = (node) => !node.own || node.own.level === null || node.own.level === undefined;

  // The row's OWN problem, drawn as a shape (black-box rule: a self-report, never the rollup).
  function ownProblem(node) {
    if (isGrouping(node)) return null;
    return PROBLEM.has(node.level) ? node.level : null;
  }

  // The quiet hint: "something below is worse than what I report". A pure grouping row (host, OS bus) has no
  // self-report, so its rollup is ALSO only a hint. WHY: drawing grouping rollups as full shapes is exactly the
  // red chain from host down to the cause that made the V6 tree noisy (Zach's screenshot, 2026-10-22).
  function hintOf(node) {
    if (node.status) return node.status.hint ? { level: node.status.hint.level, message: node.status.hint.message || '' } : null;
    if (isGrouping(node)) {
      const level = node.level || (node.rollup && node.rollup.level);
      if (!PROBLEM.has(level)) return null;
      return { level, message: (node.rollup && node.rollup.message) || node.message || '' };
    }
    if (node.hint && PROBLEM.has(node.hint.level)) return { level: node.hint.level, message: node.hint.message || '' };
    return null;
  }

  // ---------------------------------------------------------------------------- expansion ----
  // Open the way to anything that needs attention; keep quiet branches closed (a calm first screen).
  function defaultOpen(node) {
    if (!node.children || !node.children.length) return false;
    if (typeof node.defaultOpen === 'boolean') return node.defaultOpen; // the structure's call (view/core.js)
    if (node.kind === 'host' || node.kind === 'group') return true;
    if (node.kind === 'bus' && node.depth <= 1) return true; // OS buses: show what is plugged in
    if (node.kind === 'port') return true; // a port is "where", its device is the row that matters
    return Boolean(hintOf(node));
  }

  // the top-level rows of a structure (single-host elision means there may be several)
  function indexTree(forest) {
    const byPath = new Map();
    const parentOf = new Map();
    let count = 0;
    (function visit(node, parent) {
      count += 1;
      byPath.set(node.path, node);
      parentOf.set(node.path, parent ? parent.path : null);
      (node.children || []).forEach((child) => visit(child, node));
    })({ path: null, children: forest }, null);
    byPath.delete(null);
    return { byPath, parentOf, count: count - 1 };
  }

  // view/core.js has already searched (labels, ids, messages, folded parts) and marked each kept row's `hit`
  const matches = (node, q) => (typeof node.hit === 'boolean' ? node.hit : [node.name, node.id_text, node.path, node.message, node.hardware_id]
    .some((value) => String(value || '').toLowerCase().includes(q)));

  // ------------------------------------------------------------------------------- state ----
  const STATES = new WeakMap(); // container -> renderer state (expansion survives the 1 s re-render)

  function stateFor(container) {
    let st = STATES.get(container);
    if (!st) {
      st = { open: new Map(), selected: null, revealedFor: null, apiTree: null, opts: {}, lastHtml: '', root: null, scrollToSelected: false };
      STATES.set(container, st);
    }
    if (!st.root || !container.contains(st.root)) mount(container, st);
    return st;
  }

  function mount(container, st) {
    container.innerHTML = '';
    const root = document.createElement('div');
    root.className = 't1-tree';
    root.innerHTML = '<div class="t1-head"></div><div class="t1-rows" role="tree" tabindex="0" aria-label="IO entities"></div>';
    container.appendChild(root);
    st.root = root;
    st.head = root.querySelector('.t1-head');
    st.rowsEl = root.querySelector('.t1-rows');
    st.lastHtml = '';
    st.lastHead = '';
    // Our own attribute names (data-t1-*), so a host page's document-level [data-path] handlers never double-fire.
    st.rowsEl.addEventListener('click', (event) => onClick(container, st, event));
    st.rowsEl.addEventListener('dblclick', (event) => onDoubleClick(container, st, event));
    st.rowsEl.addEventListener('keydown', (event) => onKey(container, st, event));
  }

  const isOpen = (st, node) => (st.open.has(node.path) ? st.open.get(node.path) : defaultOpen(node));

  function visibleRows(st, forest, query) {
    const q = String(query || '').trim().toLowerCase();
    const keep = new Set();
    if (q) {
      (function mark(node, trail) {
        if (matches(node, q)) { trail.forEach((path) => keep.add(path)); keep.add(node.path); }
        (node.children || []).forEach((child) => mark(child, trail.concat(node.path)));
      })({ path: null, children: forest }, []);
      keep.add(null);
    }
    const rows = [];
    (function walk(node) {
      if (q && !keep.has(node.path)) return;
      const kids = node.children || [];
      const open = kids.length > 0 && (q ? true : isOpen(st, node));
      if (node.path !== null) rows.push({ node, open, hasKids: kids.length > 0, hit: Boolean(q) && matches(node, q) });
      if (open || node.path === null) kids.forEach(walk);
    })({ path: null, children: forest });
    return rows;
  }

  // ------------------------------------------------------------------------------ drawing ----
  function statusHtml(node, open) {
    const problem = ownProblem(node);
    const part = node.status && node.status.part;
    if (!problem && part) {
      // a folded part's own report (driver: fault 33), drawn on its owner because the part is not a row
      return `<span class="t1-st"><span class="t1-mark t1-part${part.knock_on_of ? ' t1-subdued' : ''}">${shape(part.level, `${part.text}`)}</span></span>`;
    }
    // a folded part worse than the row's own report badges the shape like a worse cause below
    const hint = part && problem && RANK[part.level] > RANK[problem] ? { level: part.level, message: part.text } : hintOf(node);
    const hintWorse = hint && (!problem || RANK[hint.level] > RANK[problem]);
    // Knock-on: explained by a failed ancestor, counted once there -> the shape is drawn subdued (grey).
    const subdued = problem && node.knock_on_of ? ' t1-subdued' : '';
    let html = '';
    if (problem) {
      html += `<span class="t1-mark${subdued}">${shape(problem, `${WORD[problem]}${node.knock_on_of ? ' (knock-on)' : ''}`)}`;
      if (hintWorse) html += `<i class="t1-badge t1-${hint.level}${open ? ' t1-open' : ''}" aria-label="${esc(`below: ${hint.level}`)}"></i>`;
      html += '</span>';
    } else if (hint) {
      // WHY only when collapsed: an open branch already draws its cause a few rows down, so repeating a mark on every
      // ancestor rebuilds the red chain of the V6 tree. Open rows keep it one step away (hover / selection, v1.css).
      html += `<i class="t1-hint t1-${hint.level}${open ? ' t1-open' : ''}" aria-label="${esc(`below: ${hint.level}`)}"></i>`;
    }
    return `<span class="t1-st">${html}</span>`;
  }

  function tooltip(node, byPath) {
    const lines = [node.name];
    if (node.message) lines.push(node.message);
    const hint = hintOf(node);
    if (hint && !(isGrouping(node) && hint.message === node.message)) lines.push(`below: ${hint.level} · ${hint.message}`);
    else if (hint) lines.push(`(worst below: ${hint.level})`);
    if (node.knock_on_of) {
      const cause = byPath.get(node.knock_on_of);
      lines.push(`knock-on: explained by ${cause ? cause.name : node.knock_on_of} above, counted once there`);
    }
    if (node.connected === false) lines.push('not connected');
    if (node.id_text) lines.push(`id: ${node.id_text}`);
    if (node.edge === 'part_of') lines.push('part of its parent');
    return lines.join('\n');
  }

  function highlight(text, q) {
    if (!q) return esc(text);
    const at = String(text).toLowerCase().indexOf(q);
    if (at < 0) return esc(text);
    return `${esc(text.slice(0, at))}<mark>${esc(text.slice(at, at + q.length))}</mark>${esc(text.slice(at + q.length))}`;
  }

  function rowHtml(row, st, byPath, q) {
    const { node, open, hasKids, hit } = row;
    const problem = ownProblem(node);
    const classes = ['t1-row'];
    if (node.connected === false) classes.push('t1-off');
    if (node.knock_on_of) classes.push('t1-knock');
    if (problem && !node.knock_on_of) classes.push(`t1-lv-${problem}`);
    if (isGrouping(node) && !hasKids) classes.push('t1-empty');
    if (node.path === st.selected) classes.push('t1-sel');
    if (hit) classes.push('t1-hit');
    const chevron = hasKids
      ? `<span class="t1-chev${open ? ' t1-open' : ''}" data-t1-toggle="${esc(node.path)}"><svg viewBox="0 0 16 16" aria-hidden="true">${CHEVRON}</svg></span>`
      : '<span class="t1-chev t1-leaf"></span>';
    const idText = node.idText !== undefined ? node.idText : node.id_text;
    const id = idText ? `<span class="t1-id">${esc(idText)}</span>` : '';
    return `<div class="${classes.join(' ')}" role="treeitem" aria-level="${node.depth + 1}"${hasKids ? ` aria-expanded="${open}"` : ''}`
      + ` aria-selected="${node.path === st.selected}" data-t1-path="${esc(node.path)}" style="--t1-d:${node.depth}" title="${esc(node.title || tooltip(node, byPath))}">`
      + `<span class="t1-guides"></span>${chevron}${typeIcon(node.icon)}`
      + `<span class="t1-name">${highlight(node.name, hit ? q : '')}</span>${id}${statusHtml(node, open)}</div>`;
  }

  function headHtml(summary, entities) {
    const counts = (summary && summary.counts) || {};
    const parts = WORST_FIRST.filter((level) => counts[level])
      .map((level) => `<span class="t1-count" title="${counts[level]} independent ${WORD[level]}${counts[level] > 1 ? 's' : ''}">${shape(level, WORD[level])}${counts[level]}</span>`);
    return `<span class="t1-title">Entities <span class="t1-num">${entities}</span></span>`
      + `<span class="t1-counts">${parts.join('') || '<span class="t1-quiet">nothing needs you</span>'}</span>`;
  }

  function paint(container, st) {
    if (!st.apiTree) return;
    const forest = st.apiTree;
    const { byPath, parentOf, count } = indexTree(forest);
    // Reveal a new selection once (e.g. picked from the chip's attention list); later collapses stay collapsed.
    if (st.selected && st.selected !== st.revealedFor && byPath.has(st.selected)) {
      for (let path = parentOf.get(st.selected); path; path = parentOf.get(path)) st.open.set(path, true);
      st.revealedFor = st.selected;
    }
    const q = String(st.opts.query || '').trim().toLowerCase();
    const rows = visibleRows(st, forest, q);
    st.visible = rows.map((row) => row.node.path);
    st.byPath = byPath;
    st.parentOf = parentOf;
    const head = headHtml(st.opts.summary, count);
    if (head !== st.lastHead) { st.head.innerHTML = head; st.lastHead = head; }
    const html = rows.map((row) => rowHtml(row, st, byPath, q)).join('');
    // WHY compare before writing: the page re-renders every second; an unchanged tree must not touch the DOM
    // (keeps hover, focus and scroll exactly where they were).
    if (html !== st.lastHtml) { st.rowsEl.innerHTML = html; st.lastHtml = html; }
    if (st.scrollToSelected) {
      st.scrollToSelected = false;
      const row = st.rowsEl.querySelector('.t1-sel');
      if (row) row.scrollIntoView({ block: 'nearest' });
    }
  }

  // ------------------------------------------------------------------------------ input ----
  function select(container, st, path) {
    if (!path || !st.byPath || !st.byPath.has(path)) return;
    st.selected = path;
    st.revealedFor = path; // the user picked it in the tree: it is already visible
    const handler = st.opts.onSelect;
    paint(container, st);
    if (typeof handler === 'function') handler(path, st.byPath.get(path));
  }

  function toggle(container, st, path, wantOpen) {
    const node = st.byPath && st.byPath.get(path);
    if (!node || !(node.children || []).length) return;
    st.open.set(path, wantOpen === undefined ? !isOpen(st, node) : wantOpen);
    paint(container, st);
  }

  function onClick(container, st, event) {
    const chevron = event.target.closest('[data-t1-toggle]');
    if (chevron) { toggle(container, st, chevron.getAttribute('data-t1-toggle')); return; }
    const row = event.target.closest('[data-t1-path]');
    if (row) select(container, st, row.getAttribute('data-t1-path'));
  }

  function onDoubleClick(container, st, event) {
    if (event.target.closest('[data-t1-toggle]')) return;
    const row = event.target.closest('[data-t1-path]');
    if (row) toggle(container, st, row.getAttribute('data-t1-path'));
  }

  // VS Code explorer keys: up/down move, right opens then descends, left closes then climbs, Enter/Space toggles.
  function onKey(container, st, event) {
    const visible = st.visible || [];
    if (!visible.length) return;
    const at = Math.max(0, visible.indexOf(st.selected));
    const node = st.byPath.get(visible[at]);
    const go = (path) => { st.scrollToSelected = true; select(container, st, path); };
    const hasKids = node && (node.children || []).length > 0;
    switch (event.key) {
      case 'ArrowDown': go(visible[Math.min(visible.length - 1, visible.indexOf(st.selected) + 1)]); break;
      case 'ArrowUp': go(visible[Math.max(0, at - 1)]); break;
      case 'Home': go(visible[0]); break;
      case 'End': go(visible[visible.length - 1]); break;
      case 'ArrowRight':
        if (hasKids && !isOpen(st, node)) toggle(container, st, node.path, true);
        else if (hasKids) go(node.children[0].path);
        break;
      case 'ArrowLeft':
        if (hasKids && isOpen(st, node)) toggle(container, st, node.path, false);
        else if (st.parentOf.get(node.path)) go(st.parentOf.get(node.path));
        break;
      case 'Enter': case ' ': if (hasKids) toggle(container, st, node.path); break;
      default: return;
    }
    event.preventDefault();
  }

  // ----------------------------------------------------------------------------- export ----
  function render(container, rows, opts = {}) {
    if (!container) return;
    const st = stateFor(container);
    st.apiTree = Array.isArray(rows) ? rows : [];
    st.opts = opts || {};
    if (opts.selectedPath !== undefined && opts.selectedPath !== null) st.selected = opts.selectedPath;
    paint(container, st);
  }

  window.IOTreeRenderers = window.IOTreeRenderers || {};
  window.IOTreeRenderers.v1 = {
    name: 'Quiet IDE tree',
    consumes: 'rows',
    description: 'VS Code explorer: hover chevrons in a left gutter, type icon + name, muted ids, a status shape at the far right only on problem rows; rollups are a quiet dot.',
    render,
  };
})();
