// IO diagnostics tree proposal v4 — "Status rail".
//
// Relies on docs/api-v1.md only (the node shape of GET /api/v1/state → tree). Exposes
//   window.IOTreeRenderers.v4 = {name, render(container, apiTree, {onSelect, selectedPath, query?})}
// render() is idempotent and cheap to call every poll: it keeps its own expand state per container and only touches
// the DOM when the drawn rows actually change (so hover, focus and scroll survive the 1 s tick).
//
// The design, against Zach's tree rules of 2026-10-22:
//   - Left side is purely TYPE icon + name + id. Disclosure chevrons replace the type icon on hover (Notion), so there
//     is never a permanent column of arrows. Indent guides also appear only while the pointer is over the tree.
//   - Right edge: a 3 px STATUS RAIL per row, invisible when OK. A problem the row reports itself paints the rail in
//     its colour and puts its SHAPE beside it: octagon-x error, triangle-! warning, clock stale, circle-? unknown.
//     Consecutive problem rows join into one gutter line, like an editor's change gutter.
//   - Quiet when OK: no green anywhere. Healthy connected rows are normal grey text; only problems get colour;
//     a not-connected row (empty port, offline entity, a bus with nothing on it) is fully greyed.
//   - Black-box rule: the solid shape is ONLY the entity's own self-report. "Something below is worse" (hint, or a
//     pure grouping row's rollup) is a quiet hint: a 1 px dotted rail that traces the way down to the cause, plus a
//     small hollow shape while the row is collapsed (the cause is hidden, so the row says where to look).
//   - A knock-on problem (explained by a failed ancestor) is drawn subdued: grey hollow shape, dashed grey rail.
(function () {
  'use strict';

  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const WORD = { warn: 'warning', unknown: 'unknown', stale: 'stale', error: 'error', ok: 'OK' };
  const isProblem = (level) => RANK[level] > 0; // null / undefined / "ok" → false
  const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // Type icons: the same Lucide-style strokes as the page's sprite (web/index.html), inlined so this renderer stands
  // alone. Closed vocabulary from api-v1 `icon`; anything unknown draws `device`.
  const ICON = {
    host: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    group: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    usb: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>',
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
  const typeIcon = (name) => `<svg class="v4-type" viewBox="0 0 24 24" aria-hidden="true">${ICON[name] || ICON.device}</svg>`;
  const CHEVRON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';

  // Problem SHAPES (16-unit box). `solid` = the row's own report (filled, knocked-out mark); otherwise an outline
  // (hint / knock-on). WHY shapes and not dots: they survive colour blindness and greyscale, and an octagon reads as
  // "stop" before its colour does (Zach's rule 3).
  function shape(level, solid) {
    const body = solid ? 'v4-fill' : 'v4-edge';
    const mark = solid ? 'v4-ink' : 'v4-mark';
    const dot = solid ? 'v4-inkdot' : 'v4-markdot';
    switch (level) {
      case 'error': // octagon with a cross
        return `<path class="${body}" d="M5.4 1.4h5.2l4 4v5.2l-4 4H5.4l-4-4V5.4z"/><path class="${mark}" d="M5.9 5.9l4.2 4.2M10.1 5.9l-4.2 4.2"/>`;
      case 'warn': // triangle with an exclamation mark
        return `<path class="${body}" d="M8 1.7c.3 0 .6.2.8.5l6.3 11.1c.4.6-.1 1.3-.8 1.3H1.7c-.7 0-1.2-.7-.8-1.3L7.2 2.2c.2-.3.5-.5.8-.5z"/><path class="${mark}" d="M8 6.1v3.6"/><circle class="${dot}" cx="8" cy="12.1" r="1"/>`;
      case 'stale': // a clock: overdue, the last values are old
        return `<circle class="${body}" cx="8" cy="8" r="6.6"/><path class="${mark}" d="M8 4.4V8l2.5 1.7"/>`;
      case 'unknown': // circle with a question mark: not evaluated (e.g. port busy)
        return `<circle class="${body}" cx="8" cy="8" r="6.6"/><path class="${mark}" d="M6.1 6.3a2 2 0 1 1 2.8 1.8c-.6.3-.9.7-.9 1.3v.3"/><circle class="${dot}" cx="8" cy="11.9" r="1"/>`;
      default:
        return '';
    }
  }
  const glyph = (level, variant, title) => `<span class="v4-glyph-box v4-l-${level}" title="${esc(title)}">`
    + `<svg class="v4-glyph v4-${variant}" viewBox="0 0 16 16" aria-hidden="true">${shape(level, variant === 'self')}</svg></span>`;

  // ---------------------------------------------------------------------------------------- row semantics --------
  // Everything visual about one row is decided here, from api-v1 fields only.
  function rowStatus(node) {
    const grouping = !node.own || node.own.level === null;
    // the solid shape: an entity's own self-report only (black-box rule); a pure grouping row has none of its own
    const self = !grouping && isProblem(node.level) ? node.level : null;
    // the quiet hint: something below is worse. For a grouping row its shown level IS the rollup.
    let hint = null;
    let hintText = '';
    if (grouping && isProblem(node.level)) {
      hint = node.level;
      hintText = `worst below: ${WORD[hint]} — ${node.message || ''}`;
    } else if (node.hint && isProblem(node.hint.level) && (!self || RANK[node.hint.level] > RANK[self])) {
      hint = node.hint.level;
      const origin = (node.rollup && node.rollup.origin_name) || node.hint.origin || '';
      hintText = `below: ${origin} is ${WORD[hint]} — ${node.hint.message || ''}`;
    }
    const knock = Boolean(self && node.knock_on_of);
    // WHY "vacant" also covers an empty grouping row: a USB root bus with nothing plugged in is, to the reader, the
    // same as an empty port (nothing connected), so it greys out instead of adding five more grey rows of noise.
    const vacant = node.connected === false
      || (grouping && (!node.children || node.children.length === 0) && node.level == null);
    return { grouping, self, hint, hintText, knock, vacant };
  }

  function rowTitle(node, status) {
    const lines = [node.name + (node.id_text ? ` · ${node.id_text}` : '')];
    if (node.connected === false) lines.push('not connected');
    if (status.self) lines.push(`${WORD[status.self]}: ${node.message || ''}`);
    else if (!status.grouping && node.message) lines.push(node.message);
    if (status.knock) lines.push(`knock-on: explained by ${node.knock_on_of}`);
    if (status.hint) lines.push(status.hintText);
    return lines.join('\n');
  }

  // ------------------------------------------------------------------------------------------- expansion --------
  // Default: the OS skeleton (host, groups, buses, ports, hubs) is open so "which port is it on" is visible at once;
  // a device's own detail (ttys, video nodes, CAN nodes, streams) stays closed unless a worse cause is down there.
  function defaultOpen(node) {
    if (!node.children || node.children.length === 0) return false;
    if (node.kind === 'host' || node.kind === 'group' || node.kind === 'port') return true;
    if (node.kind === 'bus' && node.depth <= 1) return true;
    if (node.icon === 'hub') return true;
    const grouping = !node.own || node.own.level === null;
    return Boolean(node.hint) || (grouping && isProblem(node.level));
  }

  function isOpen(state, node) {
    if (state.query) return true;
    return state.open.has(node.path) ? state.open.get(node.path) : defaultOpen(node);
  }

  const searchText = (node) => [node.name, node.id_text, node.path, node.message, node.hardware_id].join('\u0000').toLowerCase();

  function visibleRows(state) {
    const rows = [];
    const query = state.query.trim().toLowerCase();
    let keep = null;
    if (query) {
      keep = new Set();
      (function mark(node, trail) {
        if (searchText(node).includes(query)) { trail.forEach((path) => keep.add(path)); keep.add(node.path); }
        (node.children || []).forEach((child) => mark(child, trail.concat(node.path)));
      })(state.tree, []);
    }
    (function walk(node) {
      if (keep && !keep.has(node.path)) return;
      rows.push({ node, hit: Boolean(keep) && searchText(node).includes(query) });
      if (node.children && node.children.length && isOpen(state, node)) node.children.forEach(walk);
    })(state.tree);
    return rows;
  }

  // ---------------------------------------------------------------------------------------------- drawing --------
  function rowHtml(state, node, hit) {
    const status = rowStatus(node);
    const kids = Boolean(node.children && node.children.length);
    const open = kids && isOpen(state, node);
    const selected = node.path === state.selectedPath;
    // the rail's colour: own problem first; otherwise the hint's trail
    const railLevel = status.self || status.hint;
    const classes = ['v4-row',
      railLevel ? `v4-l-${railLevel}` : '',
      status.self ? 'v4-self' : status.hint ? 'v4-trail' : '',
      status.knock ? 'v4-knock' : '',
      status.vacant ? 'v4-vacant' : '',
      kids ? 'v4-kids' : '',
      open ? 'v4-open' : '',
      selected ? 'v4-sel' : '',
      hit ? 'v4-hit' : '',
      node.edge === 'part_of' ? 'v4-part' : ''].filter(Boolean).join(' ');
    // WHY skip a repeated id: "fdcanusb 272813EF  272813EF" says the serial twice. The full id stays one hover away
    // (row title) and in the detail panel; the name itself is always rendered exactly as authored.
    const id = node.id_text && !String(node.name || '').includes(node.id_text) ? node.id_text : '';
    let glyphs = '';
    // the hint shape shows while the row is collapsed (the cause is hidden) or when the rail is busy with the row's
    // own, milder problem (a WARN spider with an ERROR leg); an open row's trail already points down to the cause.
    // A knock-on row stays subdued: no coloured hint on it while its cause is in view.
    const railBusy = status.self && !status.knock;
    if (status.hint && (!open || railBusy)) glyphs += glyph(status.hint, 'hint', status.hintText);
    if (status.self) glyphs += glyph(status.self, status.knock ? 'knock' : 'self', `${WORD[status.self]}: ${node.message || ''}`);
    const lead = `<span class="v4-lead">${typeIcon(node.icon)}`
      + (kids ? `<span class="v4-chev" data-v4-toggle="1" title="${open ? 'Collapse' : 'Expand'}">${CHEVRON}</span>` : '') + '</span>';
    return `<div class="${classes}" role="treeitem" aria-level="${(node.depth || 0) + 1}"`
      + `${kids ? ` aria-expanded="${open}"` : ''} aria-selected="${selected}" data-path="${esc(node.path)}"`
      + ` style="--d:${node.depth || 0}" title="${esc(rowTitle(node, status))}">`
      + `${lead}<span class="v4-name">${esc(node.name)}</span>`
      + (id ? `<span class="v4-id">${esc(id)}</span>` : '')
      + `<span class="v4-status">${glyphs}</span><span class="v4-rail"></span></div>`;
  }

  function revealSelection(state) {
    // when the selection changes from outside (activity log, chip, attention list) open the way to it, once
    if (!state.selectedPath || state.selectedPath === state.revealed) return;
    state.revealed = state.selectedPath;
    const trail = [];
    (function find(node, ancestors) {
      if (node.path === state.selectedPath) { trail.push(...ancestors); return true; }
      return (node.children || []).some((child) => find(child, ancestors.concat(node)));
    })(state.tree, []);
    trail.forEach((ancestor) => { if (!isOpen(state, ancestor)) state.open.set(ancestor.path, true); });
  }

  function draw(state, { scroll = false } = {}) {
    if (!state.tree) return 0;
    revealSelection(state);
    const rows = visibleRows(state);
    state.visible = rows.map((row) => row.node);
    const html = rows.map(({ node, hit }) => rowHtml(state, node, hit)).join('');
    if (html !== state.lastHtml) {
      state.root.innerHTML = html;
      state.lastHtml = html;
    }
    if (scroll && state.selectedPath) {
      const element = state.root.querySelector(`[data-path="${CSS.escape(state.selectedPath)}"]`);
      if (element) element.scrollIntoView({ block: 'nearest' });
    }
    return rows.length;
  }

  // ---------------------------------------------------------------------------------------- interaction ---------
  const nodeAt = (state, path) => state.visible.find((node) => node.path === path) || null;

  function choose(state, path, scroll) {
    if (!path) return;
    state.selectedPath = path;
    state.revealed = path; // a click inside the tree never needs revealing
    draw(state, { scroll });
    if (state.onSelect) state.onSelect(path);
  }

  function toggle(state, path) {
    const node = nodeAt(state, path);
    if (!node || !node.children || !node.children.length) return;
    state.open.set(path, !isOpen(state, node));
    draw(state);
  }

  function onKey(state, event) {
    const list = state.visible;
    if (!list.length) return;
    const index = Math.max(0, list.findIndex((node) => node.path === state.selectedPath));
    const node = list[index];
    const kids = node && node.children && node.children.length;
    let handled = true;
    switch (event.key) {
      case 'ArrowDown': choose(state, (list[index + 1] || node).path, true); break;
      case 'ArrowUp': choose(state, (list[index - 1] || node).path, true); break;
      case 'Home': choose(state, list[0].path, true); break;
      case 'End': choose(state, list[list.length - 1].path, true); break;
      case 'ArrowRight':
        if (kids && !isOpen(state, node)) toggle(state, node.path);
        else if (kids) choose(state, list[index + 1].path, true);
        break;
      case 'ArrowLeft':
        if (kids && isOpen(state, node) && !state.query) toggle(state, node.path);
        else if (node.parent && nodeAt(state, node.parent)) choose(state, node.parent, true);
        break;
      case 'Enter': case ' ': if (kids) toggle(state, node.path); break;
      default: handled = false;
    }
    if (handled) event.preventDefault();
  }

  function wire(state) {
    const { root } = state;
    root.addEventListener('click', (event) => {
      const row = event.target.closest('.v4-row');
      if (!row || !root.contains(row)) return;
      if (event.target.closest('[data-v4-toggle]')) { event.stopPropagation(); toggle(state, row.dataset.path); return; }
      choose(state, row.dataset.path, false);
    });
    root.addEventListener('dblclick', (event) => {
      const row = event.target.closest('.v4-row');
      if (row && !event.target.closest('[data-v4-toggle]')) toggle(state, row.dataset.path);
    });
    root.addEventListener('keydown', (event) => onKey(state, event));
  }

  function stateFor(container) {
    let state = container.__ioTreeV4;
    if (!state || !container.contains(state.root)) {
      const root = document.createElement('div');
      root.className = 'v4-tree';
      root.setAttribute('role', 'tree');
      root.setAttribute('aria-label', 'IO entities');
      root.tabIndex = 0;
      container.replaceChildren(root);
      // keep what the reader expanded across a renderer switch and back
      state = { root, open: state ? state.open : new Map(), lastHtml: '', visible: [], selectedPath: state ? state.selectedPath : null, revealed: null, query: '' };
      wire(state);
      container.__ioTreeV4 = state;
    }
    return state;
  }

  function render(container, apiTree, options = {}) {
    if (!container || !apiTree) return 0;
    const state = stateFor(container);
    state.tree = apiTree;
    state.onSelect = typeof options.onSelect === 'function' ? options.onSelect : null;
    if (options.selectedPath !== undefined) state.selectedPath = options.selectedPath;
    state.query = String(options.query || '');
    return draw(state);
  }

  // the stylesheet sits beside this file; link it once unless the page already did (the switcher may)
  (function linkStylesheet() {
    const script = document.currentScript;
    if (!script || document.querySelector('link[href$="tree/v4.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = new URL('v4.css', script.src).href;
    document.head.appendChild(link);
  })();

  window.IOTreeRenderers = window.IOTreeRenderers || {};
  window.IOTreeRenderers.v4 = {
    name: 'Status rail',
    description: 'Type icon + name + id on the left; a right-edge rail that is invisible when OK and takes the problem shape and colour otherwise.',
    render,
  };
})();
