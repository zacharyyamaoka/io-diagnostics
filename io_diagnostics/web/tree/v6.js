// Tree style v6 — "IDE × Notion" for IO diagnostics (round 2; docs/round2-brief-2026-10-22.md, seams in
// docs/round2-seams.md).
//
// Consumes display rows from the active structure (web/view/core.js), never the raw API tree. Registers
//   window.IOTreeRenderers.v6 = {name, consumes: 'rows', render(container, rows, {onSelect, onSelectEntity,
//                                 selectedPath, query, summary, host})}
//
// Between Quiet IDE (v1) and Notion (v2), as Zach asked (2026-10-22 14:46):
//   - IDE: every expandable row shows its chevron at rest, quiet, so you see at a glance which rows are folders;
//     leaves keep the empty slot so icons line up
//   - Notion: no indent guides, rounded rows, affordances revealed on hover (the chevron brightens, an open
//     branch's hint ring appears)
// The tree rules (2026-10-22) still hold: type icon + name together, status SHAPE at the far right, quiet when OK,
// not connected = fully grey, black box (own level shown; worst below is a quiet dot only on a collapsed branch).
'use strict';
(function () {
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const WORST_FIRST = ['error', 'stale', 'unknown', 'warn'];
  const WORD = { error: 'error', stale: 'stale', unknown: 'unknown', warn: 'warning' };
  const KNOWN_ICONS = new Set(['host', 'group', 'usb', 'pci', 'net', 'tty', 'video', 'port', 'hub', 'adapter', 'bus', 'servo',
    'chip', 'motor', 'camera', 'stream', 'webcam', 'hid', 'device']);

  // Type icons: the same 24-unit stroke family as web/index.html's sprite, carried here so the renderer is
  // self-contained.
  const TYPE_ICONS = {
    host: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    group: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    usb: '<path d="M12 6v11.5M12 15.5 7 12.5V10.2M12 13.5l5-3V9"/><path d="M12 2.6 14.1 6H9.9Z" fill="currentColor"/><circle cx="12" cy="19.6" r="1.9"/><rect x="5.5" y="7.2" width="3" height="3" rx=".5"/><circle cx="17" cy="7.6" r="1.5"/>',
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
  // Status SHAPES (16-unit): the shape alone must read without colour; the glyph is knocked out in --io-on-status.
  const SHAPES = {
    warn: '<path class="t6-fill" d="M7.13 2.2a1 1 0 0 1 1.74 0l6.03 10.6a1 1 0 0 1-.87 1.5H1.97a1 1 0 0 1-.87-1.5Z"/>'
      + '<path class="t6-ink" d="M8 6.1v3.4"/><circle class="t6-ink-dot" cx="8" cy="11.7" r=".95"/>',
    error: '<path class="t6-fill" d="M5.3 1.2h5.4l4.1 4.1v5.4l-4.1 4.1H5.3l-4.1-4.1V5.3Z"/>'
      + '<path class="t6-ink" d="m5.7 5.7 4.6 4.6m0-4.6-4.6 4.6"/>',
    stale: '<circle class="t6-line" cx="8" cy="8" r="6.2"/><path class="t6-line" d="M8 4.6V8l2.3 1.5"/>',
    unknown: '<circle class="t6-fill" cx="8" cy="8" r="6.8"/>'
      + '<path class="t6-ink" d="M6.25 6.3a1.85 1.85 0 0 1 3.6.6c0 1.25-1.85 1.5-1.85 2.75"/><circle class="t6-ink-dot" cx="8" cy="11.6" r=".9"/>',
  };

  const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const typeIcon = (name) => `<svg class="t6-ic" viewBox="0 0 24 24" aria-hidden="true">${TYPE_ICONS[KNOWN_ICONS.has(name) ? name : 'device']}</svg>`;
  const shape = (level, label) => `<svg class="t6-shape t6-${level}" viewBox="0 0 16 16" role="img" aria-label="${esc(label)}">${SHAPES[level]}</svg>`;
  const worse = (a, b) => (a in RANK ? RANK[a] : -1) > (b in RANK ? RANK[b] : -1);

  // ------------------------------------------------------------------------------ state ----
  const STATES = new WeakMap(); // container -> {root, head, list, open, ...}; expansion survives the 1 s re-render

  function stateFor(container) {
    let st = STATES.get(container);
    if (!st || !container.contains(st.root)) {
      container.innerHTML = '';
      const root = document.createElement('div');
      root.className = 't6';
      root.innerHTML = '<div class="t6-head"></div><div class="t6-rows" role="tree" tabindex="0" aria-label="IO entities"></div>';
      container.appendChild(root);
      st = { root, head: root.querySelector('.t6-head'), list: root.querySelector('.t6-rows'), open: new Map(), rows: [],
        byKey: new Map(), visible: [], selected: null, revealedFor: null, opts: {}, lastHtml: '', lastHead: '', scrollToSelected: false };
      STATES.set(container, st);
      // WHY data-t6-* attributes: the host page delegates [data-path] / [data-toggle] clicks at the document level
      st.list.addEventListener('click', (event) => onClick(st, event));
      st.list.addEventListener('dblclick', (event) => onDoubleClick(st, event));
      st.list.addEventListener('keydown', (event) => onKey(st, event));
      st.head.addEventListener('click', (event) => {
        const host = event.target.closest('[data-t6-host]');
        if (host && typeof st.opts.onSelectEntity === 'function') st.opts.onSelectEntity(host.getAttribute('data-t6-host'));
      });
    }
    return st;
  }

  const querying = (st) => Boolean(String(st.opts.query || '').trim());
  // a search shows every kept branch open (view/core.js keeps the hits and their ancestors only)
  const isOpen = (st, row) => row.children.length > 0 && (querying(st) ? true : st.open.has(row.key) ? st.open.get(row.key) : row.defaultOpen);

  function index(rows) {
    const byKey = new Map();
    (function walk(list) { for (const row of list) { byKey.set(row.key, row); walk(row.children); } })(rows);
    return byKey;
  }

  // `lasts`: for each level above the row and the row itself, whether it is the last of its siblings (box guides)
  function visibleRows(st) {
    const out = [];
    (function walk(list, lasts) {
      list.forEach((row, at) => {
        const open = isOpen(st, row);
        const mine = [...lasts, at === list.length - 1];
        out.push({ row, open, lasts: mine });
        if (open) walk(row.children, mine);
      });
    })(st.rows, []);
    return out;
  }

  // Box-drawing guides, when the skin asks for them (Terminal: --io-guide-style: box; --io-guide-glyph: "│├└─").
  // Read from the page's tokens on every paint, so a live skin switch redraws them.
  function guideStyle() {
    const style = getComputedStyle(document.documentElement);
    if (style.getPropertyValue('--io-guide-style').trim() !== 'box') return null;
    const glyphs = [...style.getPropertyValue('--io-guide-glyph').trim().replace(/^["']|["']$/g, '')];
    return glyphs.length >= 4 ? glyphs : ['│', '├', '└', '─'];
  }

  // "│  └─ " for a row two levels down: one 3-cell column per level below the top (the top level stays flush)
  function guideText(lasts, glyphs) {
    const [bar, tee, corner, dash] = glyphs;
    let text = '';
    for (let level = 1; level < lasts.length; level += 1) {
      const last = lasts[level];
      text += level === lasts.length - 1 ? `${last ? corner : tee}${dash} ` : `${last ? ' ' : bar}  `;
    }
    return text;
  }

  // ------------------------------------------------------------------------------ drawing ----
  function statusHtml(row, open) {
    const status = row.status || {};
    const own = status.own;
    const part = status.part;
    const hint = status.hint;
    if (own) {
      // its own report; a worse part or a worse hidden cause below adds a badge dot on the shape's corner
      const extra = part && worse(part.level, own) ? part.level : hint && worse(hint.level, own) ? hint.level : null;
      const badge = extra ? `<i class="t6-badge t6-${extra}${open && !(part && part.level === extra) ? ' t6-open' : ''}" aria-label="${esc(`below: ${extra}`)}"></i>` : '';
      return `<span class="t6-st"><span class="t6-mark t6-own${row.knock_on_of ? ' t6-subdued' : ''}">${shape(own, `${WORD[own]}${row.knock_on_of ? ' (knock-on)' : ''}`)}${badge}</span></span>`;
    }
    if (part) {
      // a folded part's own report (driver: fault 33), drawn on its owner because the part is not a row
      return `<span class="t6-st"><span class="t6-mark t6-part${part.knock_on_of ? ' t6-subdued' : ''}">${shape(part.level, `${part.label}: ${WORD[part.level]}`)}</span></span>`;
    }
    if (hint) {
      // WHY only a dot, and only on a collapsed branch: an open branch shows its cause a few rows down; repeating a
      // mark on every ancestor rebuilds the red chain Zach rejected. Open rows keep a ring one step away (hover).
      return `<span class="t6-st"><i class="t6-hint t6-${hint.level}${open ? ' t6-open' : ''}" aria-label="${esc(`below: ${hint.level}`)}"></i></span>`;
    }
    return '<span class="t6-st"></span>';
  }

  function highlight(text, q) {
    if (!q) return esc(text);
    const at = String(text).toLowerCase().indexOf(q);
    if (at < 0) return esc(text);
    return `${esc(text.slice(0, at))}<mark>${esc(text.slice(at, at + q.length))}</mark>${esc(text.slice(at + q.length))}`;
  }

  function rowHtml({ row, open, lasts }, st, q, glyphs) {
    const hasKids = row.children.length > 0;
    const status = row.status || {};
    const classes = ['t6-row'];
    if (hasKids) classes.push('t6-parent');
    if (open) classes.push('t6-open');
    if (row.connected === false) classes.push('t6-off');
    if (row.knock_on_of) classes.push('t6-knock');
    if (status.own && !row.knock_on_of) classes.push(`t6-lv-${status.own}`);
    if (row.key === st.selected) classes.push('t6-sel');
    if (row.hit) classes.push('t6-hit');
    const chevron = hasKids
      ? `<span class="t6-chev${open ? ' t6-open' : ''}" data-t6-toggle="${esc(row.key)}" title="${open ? 'Collapse' : 'Expand'}"><svg viewBox="0 0 16 16" aria-hidden="true">${CHEVRON}</svg></span>`
      : '<span class="t6-chev t6-leaf"></span>';
    const note = status.note ? `<span class="t6-note${status.part.knock_on_of ? ' t6-subdued' : ''}">${esc(status.note)}</span>` : '';
    // the seams (docs/round2-seams.md §3): lead = an inline note, attrs / suffix = muted text that is not the id
    const shownAttrs = (row.attrs || []).filter(Boolean);
    const lead = row.lead ? `<span class="t6-lead">${esc(row.lead)}</span>` : '';
    const attrs = shownAttrs.length ? `<span class="t6-attrs">${esc(shownAttrs.join(' · '))}</span>` : '';
    const suffix = row.suffix ? `<span class="t6-suffix">${esc(row.suffix)}</span>` : '';
    const id = row.idText ? `<span class="t6-id">${esc(row.idText)}</span>` : '';
    const selected = row.key === st.selected;
    // subtitle: an in-flow second line under the selected row; on any other row it appears on hover, over the next
    const sub = row.subtitle && !selected ? `<span class="t6-sub" aria-hidden="true">${esc(row.subtitle)}</span>` : '';
    const guide = glyphs ? `<span class="t6-guide" aria-hidden="true">${esc(guideText(lasts, glyphs))}</span>` : '';
    const html = `<div class="${classes.join(' ')}" role="treeitem" aria-level="${row.depth + 1}"${hasKids ? ` aria-expanded="${open}"` : ''}`
      + ` aria-selected="${selected}" data-t6-path="${esc(row.key)}" style="--t6-d:${row.depth}" title="${esc(row.title || row.label)}">`
      + `${guide}${chevron}${typeIcon(row.icon)}<span class="t6-name">${highlight(row.label, row.hit ? q : '')}</span>${lead}${note}${attrs}${suffix}`
      + `<span class="t6-fill"></span>${id}${statusHtml(row, open)}${sub}</div>`;
    return selected && row.subtitle
      ? `${html}<div class="t6-subline" style="--t6-d:${row.depth}" title="${esc(row.subtitle)}">${esc(row.subtitle)}</div>` : html;
  }

  function headHtml(st) {
    const summary = st.opts.summary || {};
    const counts = summary.counts || {};
    const host = st.opts.host;
    const parts = WORST_FIRST.filter((level) => counts[level])
      .map((level) => `<span class="t6-count" title="${counts[level]} independent ${WORD[level]}${counts[level] > 1 ? 's' : ''}">${shape(level, WORD[level])}${counts[level]}</span>`);
    // the elided host keeps one quiet, clickable place: the head (its detail is one click away)
    const title = host && host.elided
      ? `<button class="t6-host" data-t6-host="${esc(host.entity)}" title="${esc(`${host.name} · ${host.id_text || ''} — the only computer, so its children are the top level`)}">${typeIcon('host')}<span>${esc(host.name)}</span></button>`
      : '<span class="t6-title">Entities</span>';
    return `${title}<span class="t6-counts">${parts.join('') || '<span class="t6-quiet">nothing needs you</span>'}</span>`;
  }

  function paint(st) {
    st.byKey = index(st.rows);
    // reveal a new selection once (picked from the chip, the activity log, a link); later collapses stay collapsed
    if (st.selected && st.selected !== st.revealedFor && st.byKey.has(st.selected)) {
      for (let key = st.byKey.get(st.selected).parent; key; key = (st.byKey.get(key) || {}).parent) st.open.set(key, true);
      st.revealedFor = st.selected;
      st.scrollToSelected = true;
    }
    const q = String(st.opts.query || '').trim().toLowerCase();
    const rows = visibleRows(st);
    st.visible = rows.map(({ row }) => row.key);
    const head = headHtml(st);
    if (head !== st.lastHead) { st.head.innerHTML = head; st.lastHead = head; }
    const glyphs = guideStyle();
    st.root.classList.toggle('t6-box', Boolean(glyphs));
    const html = rows.map((entry) => rowHtml(entry, st, q, glyphs)).join('') || '<div class="t6-empty">Nothing matches.</div>';
    // WHY compare before writing: the page re-renders every second; an unchanged tree must not touch the DOM
    // (hover, focus and scroll stay exactly where they were)
    if (html !== st.lastHtml) { st.list.innerHTML = html; st.lastHtml = html; }
    if (st.scrollToSelected) {
      st.scrollToSelected = false;
      const element = st.list.querySelector('.t6-sel');
      if (element) element.scrollIntoView({ block: 'nearest' });
    }
  }

  // ------------------------------------------------------------------------------ input ----
  function select(st, key) {
    if (!key || !st.byKey.has(key)) return;
    st.selected = key;
    st.revealedFor = key; // picked in the tree: already visible
    paint(st);
    if (typeof st.opts.onSelect === 'function') st.opts.onSelect(key, st.byKey.get(key));
  }

  function toggle(st, key, wantOpen) {
    const row = st.byKey.get(key);
    if (!row || !row.children.length) return;
    st.open.set(key, wantOpen === undefined ? !isOpen(st, row) : wantOpen);
    paint(st);
  }

  function onClick(st, event) {
    const chevron = event.target.closest('[data-t6-toggle]');
    if (chevron) { toggle(st, chevron.getAttribute('data-t6-toggle')); return; }
    const row = event.target.closest('[data-t6-path]');
    if (row) select(st, row.getAttribute('data-t6-path'));
  }

  function onDoubleClick(st, event) {
    if (event.target.closest('[data-t6-toggle]')) return; // the single clicks already toggled it
    const row = event.target.closest('[data-t6-path]');
    if (row) toggle(st, row.getAttribute('data-t6-path'));
  }

  // VS Code explorer keys: up/down move, right opens then descends, left closes then climbs, Enter/Space toggles.
  function onKey(st, event) {
    const visible = st.visible;
    if (!visible.length) return;
    const at = Math.max(0, visible.indexOf(st.selected));
    const row = st.byKey.get(visible[at]);
    const go = (key) => { st.scrollToSelected = true; select(st, key); };
    const hasKids = row && row.children.length > 0;
    switch (event.key) {
      case 'ArrowDown': go(visible[Math.min(visible.length - 1, visible.indexOf(st.selected) + 1)]); break;
      case 'ArrowUp': go(visible[Math.max(0, at - 1)]); break;
      case 'Home': go(visible[0]); break;
      case 'End': go(visible[visible.length - 1]); break;
      case 'ArrowRight':
        if (hasKids && !isOpen(st, row)) toggle(st, row.key, true);
        else if (hasKids) go(row.children[0].key);
        break;
      case 'ArrowLeft':
        if (hasKids && isOpen(st, row)) toggle(st, row.key, false);
        else if (row.parent) go(row.parent);
        break;
      case 'Enter': case ' ': if (hasKids) toggle(st, row.key); break;
      default: return;
    }
    event.preventDefault();
  }

  // ----------------------------------------------------------------------------- export ----
  function render(container, rows, opts = {}) {
    if (!container) return;
    const st = stateFor(container);
    st.rows = Array.isArray(rows) ? rows : [];
    st.opts = opts || {};
    if (opts.selectedPath !== undefined) st.selected = opts.selectedPath;
    paint(st);
  }

  window.IOTreeRenderers = window.IOTreeRenderers || {};
  window.IOTreeRenderers.v6 = {
    name: 'IDE × Notion',
    description: 'Quiet chevrons always visible on folders (IDE), no guides, rounded rows and hover-revealed affordances (Notion); status shape at the far right, quiet when OK.',
    consumes: 'rows',
    order: 0,
    render,
  };
})();
