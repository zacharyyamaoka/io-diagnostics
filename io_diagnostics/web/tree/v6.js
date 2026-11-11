// Tree style v6 — "IDE × Notion" for IO diagnostics (round 2; docs/round2-brief-2026-10-22.md, seams in
// docs/round2-seams.md).
//
// Consumes display rows from the active structure (web/view/core.js), never the raw API tree. Registers
//   window.IOTreeRenderers.v6 = {name, consumes: 'rows', render(container, rows, {onSelect, onSelectEntity,
//                                 selectedPath, query, summary, host, onRowAction})}
//
// Between Quiet IDE (v1) and Notion (v2), as Zach asked (2026-10-22 14:46):
//   - IDE: every expandable row shows its chevron at rest, quiet, so you see at a glance which rows are folders;
//     leaves keep the empty slot so icons line up
//   - Notion: no indent guides, rounded rows, affordances revealed on hover (the chevron brightens, the row's ⋮ appears)
//
// Two marks, never mixed (Zach, 2026-10-24: "I am a bit confused right now with the icons and colors"):
//   - PRESENCE, a small neutral dot before the icon, like the Claude app's sidebar, ONE mapping for every level (web/status-dot.js):
//     solid = connected and live, hollow ring = known but not heard lately ("last heard 6 min ago"), and a slow pulse on either
//     = the host says something is running on it now (a utility, a console, a flash, its first look). It is never amber or red:
//     warning colours are for real WARN / ERROR only.
//   - HEALTH, a shape at the far right, ONLY for the diagnostics levels (contract/record.py): ▲ amber warn, ✖ red error,
//     ? unknown. Stale is the hollow dot, not a second mark. A collapsed row rolls up the worst level below it with the
//     SAME shape (never a smaller dot). A row explained by a failed cause above (knock-on) shows no mark: its words
//     stay in its tooltip and its detail. OK rows show nothing.
// Not connected rows are absent (a missing thing is a status on its parent); the header counts come from web/summary.js.
// Rows that no driver claims are hidden by default (web/visibility.js); the status bar's Visibility shows an eye per row.
'use strict';
(function () {
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const MARKS = new Set(['warn', 'unknown', 'error']); // the levels a row draws a shape for; stale is the hollow dot
  const WORD = { error: 'error', stale: 'stale', unknown: 'unknown', warn: 'warning' };
  const OPEN_KEY = 'io-diagnostics.tree.open';
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
  const DOTS = '<circle cx="8" cy="3" r="1.3"/><circle cx="8" cy="8" r="1.3"/><circle cx="8" cy="13" r="1.3"/>';
  const EYE = '<path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z"/><circle cx="8" cy="8" r="2"/>';
  const EYE_OFF = '<path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z"/><circle cx="8" cy="8" r="2"/><path d="m2.5 13.5 11-11"/>';

  const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const typeIcon = (name) => `<svg class="t6-ic" viewBox="0 0 24 24" aria-hidden="true">${TYPE_ICONS[KNOWN_ICONS.has(name) ? name : 'device']}</svg>`;
  const small = (path) => `<svg viewBox="0 0 16 16" aria-hidden="true">${path}</svg>`;
  const summaryLib = () => window.IOSummary;
  const visibility = () => window.IOVisibility;
  const shape = (level, label) => summaryLib().shape(level, label);
  const worse = (a, b) => (a in RANK ? RANK[a] : -1) > (b in RANK ? RANK[b] : -1);

  // plain words for the tooltips (docs/api-v1.md; Zach: "I don't know what the grey question mark means")
  const MEANING = {
    unknown: "Unknown: plugged in, but RobotDx couldn't check it (for example tview has the port).",
    warn: 'Warning: something is off, and it still works.',
    error: 'Error: broken.',
    stale: 'Stale: known, but not heard from lately.',
  };

  // ------------------------------------------------------------------------------ state ----
  const STATES = new WeakMap(); // container -> {root, head, list, open, ...}; expansion survives every re-render

  // WHY keyed by the hardware id when a row has one (Zach, 2026-10-24: "I cannot even drop down and then all of a sudden
  // they appear"): a path changes when a cable moves to another port or a host restarts, an id does not. Two rows with one
  // id fall back to their paths. Remembered in the browser too, so a reload keeps the tree as it was.
  function rowId(row, dupes) {
    return row.hardware_id && !(dupes && dupes.has(row.hardware_id)) ? `hw:${row.hardware_id}` : `path:${row.key}`;
  }

  function loadOpen() {
    try {
      const saved = JSON.parse(window.localStorage.getItem(OPEN_KEY) || '{}');
      return new Map(Object.entries(saved).filter(([, value]) => typeof value === 'boolean'));
    } catch (error) { return new Map(); }
  }

  function saveOpen(st) {
    try { window.localStorage.setItem(OPEN_KEY, JSON.stringify(Object.fromEntries(st.open))); } catch (error) { /* private window */ }
  }

  function stateFor(container) {
    let st = STATES.get(container);
    if (!st || !container.contains(st.root)) {
      container.innerHTML = '';
      const root = document.createElement('div');
      root.className = 't6';
      root.innerHTML = '<div class="t6-head"><span class="t6-title"></span><span class="t6-counts"></span></div>'
        + '<div class="t6-rows" role="tree" tabindex="0" aria-label="IO entities"></div><div class="t6-menu" role="menu" hidden></div>';
      container.appendChild(root);
      st = { root, head: root.querySelector('.t6-head'), counts: root.querySelector('.t6-counts'), title: root.querySelector('.t6-title'),
        list: root.querySelector('.t6-rows'), menuEl: root.querySelector('.t6-menu'), open: loadOpen(), rows: [], shown: [], byKey: new Map(),
        visible: [], selected: null, revealedFor: null, opts: {}, lastHtml: '', lastTitle: '', scrollToSelected: false, filter: null,
        menu: null, dupes: new Set() };
      STATES.set(container, st);
      // WHY data-t6-* attributes: the host page delegates [data-path] / [data-toggle] clicks at the document level
      st.list.addEventListener('click', (event) => onClick(st, event));
      st.list.addEventListener('dblclick', (event) => onDoubleClick(st, event));
      // Right-click a row: the same menu as its ⋮, at the mouse (Zach, 2026-11-10: "support right clicking on the entity tree")
      st.list.addEventListener('contextmenu', (event) => {
        const rowEl = event.target.closest('[role=treeitem][data-t6-path]');
        if (!rowEl) return;
        event.preventDefault();
        if (st.menu) closeMenu(st);
        openMenu(st, rowEl.getAttribute('data-t6-path'), null, { x: event.clientX, y: event.clientY });
      });
      st.list.addEventListener('keydown', (event) => onKey(st, event));
      st.menuEl.addEventListener('click', (event) => onMenuClick(st, event));
      st.title.addEventListener('click', (event) => {
        const host = event.target.closest('[data-t6-host]');
        if (host && typeof st.opts.onSelectEntity === 'function') st.opts.onSelectEntity(host.getAttribute('data-t6-host'));
      });
      document.addEventListener('click', (event) => {
        if (st.menu && !event.target.closest('.t6-menu') && !event.target.closest('[data-t6-more]')) closeMenu(st);
      }, true);
      document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && st.menu) { closeMenu(st); } });
      if (visibility()) {
        const off = visibility().subscribe(() => {
          if (!container.isConnected) { off(); return; }
          paint(st);
        });
      }
    }
    return st;
  }

  const querying = (st) => Boolean(String(st.opts.query || '').trim());
  // a search shows every kept branch open (view/core.js keeps the hits and their ancestors only)
  const isOpen = (st, row) => {
    if (!row.children.length) return false;
    if (querying(st) || st.filter) return true;
    const id = rowId(row, st.dupes);
    return st.open.has(id) ? st.open.get(id) : row.defaultOpen;
  };

  function index(rows) {
    const byKey = new Map();
    const seen = new Map();
    const dupes = new Set();
    (function walk(list) {
      for (const row of list) {
        byKey.set(row.key, row);
        if (row.hardware_id) { if (seen.has(row.hardware_id)) dupes.add(row.hardware_id); seen.set(row.hardware_id, row.key); }
        walk(row.children);
      }
    })(rows);
    return { byKey, dupes };
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
    })(st.shown, []);
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

  // ------------------------------------------------------------------------------ filter ----
  // The header's count filters the tree (Zach: clicking a count shows those rows): rows whose own report, folded part or
  // presence is that level, plus their ancestors.
  function levelOf(row, level) {
    const status = row.status || {};
    if (level === 'stale') return Boolean(row.presence && row.presence.state === 'stale') || status.own === 'stale';
    return status.own === level || Boolean(status.part && status.part.level === level);
  }

  function filterRows(rows, level) {
    return rows.reduce((out, row) => {
      const children = filterRows(row.children, level);
      const hit = levelOf(row, level) && !row.knock_on_of;
      if (hit || children.length) out.push({ ...row, children, hit: false, defaultOpen: true });
      return out;
    }, []);
  }

  // ------------------------------------------------------------------------------ drawing ----
  // A camera's USB generation as a small badge right after its name (Zach, 2026-11-10: "at a glance I want to be able to quickly see if
  // its usb 2.1 or 3.1"): web/usb.js's own words ("USB 2.1", "USB 3.2", or "USB 3.2 · on a USB 2.0 link"), the raw speed in its tooltip.
  // WHY cameras only: their generation decides which streams they can give; every fdcanusb is the same full-speed device.
  const USB_BADGE_ICONS = new Set(['camera', 'webcam']);
  function usbBadge(row) {
    if (!row.usb || !USB_BADGE_ICONS.has(row.icon) || !window.IOUsb) return '';
    const { text, tip } = window.IOUsb.describe(row.usb);
    return text ? `<span class="t6-usb" data-t6-usb title="${esc(tip || text)}">${esc(text)}</span>` : '';
  }

  // The presence dot: neutral by construction (docs/api-v1.md "presence"); what it draws is web/status-dot.js's, the same for every row.
  const presenceHtml = (row) => window.IOStatusDot.html(row);

  function statusHtml(row, open) {
    const status = row.status || {};
    const knock = Boolean(row.knock_on_of);
    const own = MARKS.has(status.own) && !knock ? status.own : null;
    const part = status.part && MARKS.has(status.part.level) && !status.part.knock_on_of ? status.part : null;
    const hint = status.hint && status.hint.level in RANK && RANK[status.hint.level] > 0 && !knock ? status.hint : null;
    const marks = [];
    // a folded part's own report (driver: fault 33), drawn on its owner because the part is not a row
    if (own) marks.push(`<span class="t6-mark t6-own" title="${esc(`${MEANING[own]}${row.message ? `\n${row.message}` : ''}`)}">${shape(own, WORD[own])}</span>`);
    else if (part) marks.push(`<span class="t6-mark t6-part" title="${esc(`${part.label}: ${MEANING[part.level]}`)}">${shape(part.level, `${part.label}: ${WORD[part.level]}`)}</span>`);
    // the roll-up: the worst level below, with the SAME shape (Zach, 2026-10-24: "not just a small dot"). WHY only on a
    // collapsed branch, and on hover for an open one: an open branch shows its cause a few rows down.
    const shown = own || (part && part.level);
    // WHY not stale: that is the hollow dot, not a second mark (Zach: warn, error and unknown are the marks)
    if (hint && MARKS.has(hint.level) && (!shown || worse(hint.level, shown)) && row.children.length) {
      const levelName = WORD[hint.level] || hint.level;
      marks.unshift(`<span class="t6-mark t6-roll${open ? ' t6-open' : ''}" title="${esc(`below: ${levelName}${hint.message ? ` · ${hint.message}` : ''}`)}">${shape(hint.level, `below: ${levelName}`)}</span>`);
    }
    return `<span class="t6-st">${marks.join('')}</span>`;
  }

  function highlight(text, q) {
    if (!q) return esc(text);
    const at = String(text).toLowerCase().indexOf(q);
    if (at < 0) return esc(text);
    return `${esc(text.slice(0, at))}<mark>${esc(text.slice(at, at + q.length))}</mark>${esc(text.slice(at + q.length))}`;
  }

  function bundleHtml({ row, lasts }, st, glyphs) {
    const classes = ['t6-row', 't6-bundle'];
    if (row.key === st.selected) classes.push('t6-sel');
    const worst = row.worst ? `<span class="t6-mark t6-roll" title="${esc(`hidden, but ${WORD[row.worst] || row.worst} below`)}">${shape(row.worst, `hidden: ${WORD[row.worst]}`)}</span>` : '';
    const guide = glyphs ? `<span class="t6-guide" aria-hidden="true">${esc(guideText(lasts, glyphs))}</span>` : '';
    return `<div class="${classes.join(' ')}" role="treeitem" aria-level="${(row.depth || 0) + 1}" aria-selected="false" data-t6-path="${esc(row.key)}" data-t6-bundle="${esc(row.bundleOf)}"`
      + ` style="--t6-d:${row.depth || 0}" title="${row.open ? 'Hide the hidden rows again' : 'Rows no driver claims, or that you hid. A hidden row never hides a fault.'}">`
      + `${guide}<span class="t6-chev t6-leaf"></span><span class="t6-pres t6-pres-none" aria-hidden="true"></span>`
      + `<svg class="t6-ic" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">${row.open ? EYE : EYE_OFF}</svg>`
      + `<span class="t6-name">${esc(row.label)}</span><span class="t6-fill"></span><span class="t6-st">${worst}</span><span class="t6-act"></span></div>`;
  }

  function rowHtml({ row, open, lasts }, st, q, glyphs) {
    if (row.bundle) return bundleHtml({ row, lasts }, st, glyphs);
    const hasKids = row.children.length > 0;
    const presence = row.presence;
    const classes = ['t6-row'];
    if (hasKids) classes.push('t6-parent');
    if (open) classes.push('t6-open');
    if (row.connected === false) classes.push('t6-off'); // an empty port that flaps is the one row still drawn when nothing is there
    if (row.dimmed) classes.push('t6-dim');
    if (presence && presence.state === 'stale') classes.push('t6-stalerow');
    if (row.knock_on_of) classes.push('t6-knock');
    if (row.key === st.selected) classes.push('t6-sel');
    if (row.hit) classes.push('t6-hit');
    const chevron = hasKids
      ? `<span class="t6-chev${open ? ' t6-open' : ''}" data-t6-toggle="${esc(row.key)}" title="${open ? 'Collapse' : 'Expand'}"><svg viewBox="0 0 16 16" aria-hidden="true">${CHEVRON}</svg></span>`
      : '<span class="t6-chev t6-leaf"></span>';
    const status = row.status || {};
    const note = status.note ? `<span class="t6-note${status.part && status.part.knock_on_of ? ' t6-subdued' : ''}">${esc(status.note)}</span>` : '';
    // the seams (docs/round2-seams.md §3): lead = an inline note, attrs / suffix = muted text that is not the id
    const shownAttrs = (row.attrs || []).filter(Boolean);
    // a row not heard from lately says when, in its own second line of text (Zach: "last heard 6 min ago")
    const heard = presence && presence.state === 'stale' && presence.text ? `<span class="t6-heard">${esc(presence.text)}</span>` : '';
    const lead = row.lead ? `<span class="t6-lead">${esc(row.lead)}</span>` : '';
    const attrs = shownAttrs.length ? `<span class="t6-attrs">${esc(shownAttrs.join(' · '))}</span>` : '';
    const suffix = row.suffix ? `<span class="t6-suffix">${esc(row.suffix)}</span>` : '';
    const id = row.idText ? `<span class="t6-id">${esc(row.idText)}</span>` : '';
    const selected = row.key === st.selected;
    // subtitle: an in-flow second line under the selected row; on any other row it appears on hover, over the next
    const sub = row.subtitle && !selected ? `<span class="t6-sub" aria-hidden="true">${esc(row.subtitle)}</span>` : '';
    const guide = glyphs ? `<span class="t6-guide" aria-hidden="true">${esc(guideText(lasts, glyphs))}</span>` : '';
    const vis = visibility();
    const verdict = vis && row.entity ? vis.isHidden(row) : { hidden: false, surfaced: false };
    const hiddenNow = Boolean(row.dimmed) || verdict.hidden;
    const eye = row.entity && vis
      ? `<button type="button" class="t6-eye" data-t6-eye="${esc(row.key)}" aria-label="${hiddenNow ? 'Show this row' : verdict.surfaced ? 'Acknowledge the error and hide this row' : 'Hide this row'}"`
        + ` title="${hiddenNow ? 'Hidden: click to show it' : verdict.surfaced ? 'Shown because it is in error: click to acknowledge and hide it' : 'Click to hide this row'}">`
        + `${small(hiddenNow ? EYE_OFF : EYE)}</button>` : '';
    // a folder row of a structure's own (Type's "Adapters") has no entity but still has the navigation items
    const more = row.entity || hasKids ? `<button type="button" class="t6-more" data-t6-more="${esc(row.key)}" aria-label="Row menu" aria-haspopup="menu" title="Row menu">${small(DOTS)}</button>` : '';
    const html = `<div class="${classes.join(' ')}" role="treeitem" aria-level="${row.depth + 1}"${hasKids ? ` aria-expanded="${open}"` : ''}`
      + ` aria-selected="${selected}" data-t6-path="${esc(row.key)}" style="--t6-d:${row.depth}" title="${esc(row.title || row.label)}">`
      + `${guide}${chevron}${presenceHtml(row)}${typeIcon(row.icon)}<span class="t6-name">${highlight(row.label, row.hit ? q : '')}</span>${usbBadge(row)}${heard}${lead}${note}${attrs}${suffix}`
      + `<span class="t6-fill"></span>${id}${statusHtml(row, open)}<span class="t6-act">${eye}${more}</span>${sub}</div>`;
    return selected && row.subtitle
      ? `${html}<div class="t6-subline" style="--t6-d:${row.depth}" title="${esc(row.subtitle)}">${esc(row.subtitle)}</div>` : html;
  }

  function paintHead(st) {
    const host = st.opts.host;
    // the elided host keeps one quiet, clickable place: the title (its detail is one click away). With the computer as
    // the root row (p1) there is nothing elided: the head only says what the list is.
    const title = host && host.elided
      ? `<button class="t6-host" data-t6-host="${esc(host.entity)}" title="${esc(`${host.name} · ${host.id_text || ''} — the only computer, so its children are the top level`)}">${typeIcon('host')}<span>${esc(host.name)}</span></button>`
      : 'Entities';
    if (title !== st.lastTitle) { st.title.innerHTML = title; st.lastTitle = title; }
    summaryLib().render(st.counts, st.opts.summary, {
      filter: st.filter,
      onFilter: (level) => { st.filter = level; paint(st); },
    });
  }

  function paint(st) {
    const base = st.rows;
    const indexed = index(base);
    st.dupes = indexed.dupes;
    const vis = visibility();
    if (vis) vis.expireAcks(base);
    let rows = vis ? vis.arrange(base, { querying: querying(st) || Boolean(st.filter) }) : base;
    if (st.filter) rows = filterRows(rows, st.filter);
    st.shown = rows;
    st.byKey = index(rows).byKey;
    for (const [key, row] of indexed.byKey) if (!st.byKey.has(key)) st.byKey.set(key, row); // a selected row that is hidden stays addressable
    // reveal a new selection once (picked from the chip, the activity log, a link); later collapses stay collapsed
    if (st.selected && st.selected !== st.revealedFor && st.byKey.has(st.selected)) {
      for (let key = st.byKey.get(st.selected).parent; key; key = (st.byKey.get(key) || {}).parent) {
        const parent = st.byKey.get(key);
        if (parent) st.open.set(rowId(parent, st.dupes), true);
      }
      st.revealedFor = st.selected;
      st.scrollToSelected = true;
    }
    const q = String(st.opts.query || '').trim().toLowerCase();
    const list = visibleRows(st);
    st.visible = list.map(({ row }) => row.key);
    paintHead(st);
    const glyphs = guideStyle();
    st.root.classList.toggle('t6-box', Boolean(glyphs));
    const html = list.map((entry) => rowHtml(entry, st, q, glyphs)).join('') || '<div class="t6-empty">Nothing matches.</div>';
    // WHY compare before writing: the page re-renders every second; an unchanged tree must not touch the DOM
    // (hover, focus and scroll stay exactly where they were)
    if (html !== st.lastHtml) { st.list.innerHTML = html; st.lastHtml = html; }
    if (st.menu) paintMenu(st);
    if (st.scrollToSelected) {
      st.scrollToSelected = false;
      const element = st.list.querySelector('.t6-sel');
      if (element) element.scrollIntoView({ block: 'nearest' });
    }
  }

  // ------------------------------------------------------------------------------ input ----
  function select(st, key) {
    if (!key || !st.byKey.has(key)) return;
    const row = st.byKey.get(key);
    if (row.bundle) return;
    st.selected = key;
    st.revealedFor = key; // picked in the tree: already visible
    paint(st);
    if (typeof st.opts.onSelect === 'function') st.opts.onSelect(key, row);
  }

  function toggle(st, key, wantOpen) {
    const row = st.byKey.get(key);
    if (!row || !row.children.length) return;
    st.open.set(rowId(row, st.dupes), wantOpen === undefined ? !isOpen(st, row) : wantOpen);
    saveOpen(st);
    paint(st);
  }

  function onClick(st, event) {
    const eye = event.target.closest('[data-t6-eye]');
    if (eye) {
      const row = st.byKey.get(eye.getAttribute('data-t6-eye'));
      if (row && visibility()) visibility().toggleRow(row);
      return;
    }
    const more = event.target.closest('[data-t6-more]');
    if (more) { openMenu(st, more.getAttribute('data-t6-more'), more); return; }
    const bundle = event.target.closest('[data-t6-bundle]');
    if (bundle) { visibility().toggleReveal(bundle.getAttribute('data-t6-bundle')); return; }
    const chevron = event.target.closest('[data-t6-toggle]');
    if (chevron) { toggle(st, chevron.getAttribute('data-t6-toggle')); return; }
    const row = event.target.closest('[data-t6-path]');
    if (row) select(st, row.getAttribute('data-t6-path'));
  }

  function onDoubleClick(st, event) {
    if (event.target.closest('[data-t6-toggle], [data-t6-more], [data-t6-eye], [data-t6-bundle]')) return; // the single clicks already acted
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
    // VS Code Enter Folder's key: step into the selected folder (the way back out is Ctrl+Shift+Backspace, the page's)
    if (event.key === 'Enter' && event.shiftKey && (event.ctrlKey || event.metaKey)) {
      if (hasKids && typeof st.opts.onRowNav === 'function') { event.preventDefault(); st.opts.onRowNav('into', row); }
      return;
    }
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
      case 'Enter': case ' ':
        if (row && row.bundle) visibility().toggleReveal(row.bundleOf);
        else if (hasKids) toggle(st, row.key);
        break;
      default: return;
    }
    event.preventDefault();
  }

  // ----------------------------------------------------------------------------- the row menu ----
  // Claude's ⋮ on hover. Items: Copy id; for a board, Remove from bus and Expect / Don't expect; for an adapter, Expect
  // what's here / Don't expect; the row's Utilities shortcuts (Identify, Open console (tview) …); Hide / Show. Writes go
  // through the same page-only endpoints as the Expected setup card (POST /api/v1/expect | forget), after a confirm that
  // is the host's own sentence for the exact edit.
  const POST = (path, body) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify(body) })
    .then((response) => response.json());

  function openMenu(st, key, anchor, point = null) {
    if (st.menu && st.menu.key === key) { closeMenu(st); return; }
    const row = st.byKey.get(key);
    if (!row || !(row.entity || row.children.length)) return;
    const rect = point ? { right: point.x, bottom: point.y } : anchor.getBoundingClientRect();
    st.menu = { key, row, entity: row.entity, nav: typeof st.opts.onRowNav === 'function' ? st.opts.onRowNav : null, info: row.entity ? null : {}, confirm: null, result: null, busy: false, x: rect.right, y: rect.bottom, atPoint: Boolean(point) };
    paintMenu(st);
    if (!row.entity) return;
    fetch(`/api/v1/node?path=${encodeURIComponent(row.entity)}`, { signal: AbortSignal.timeout(8000) }).then((response) => response.json()).then((info) => {
      if (st.menu && st.menu.key === key) { st.menu.info = info; paintMenu(st); }
    }).catch(() => { if (st.menu && st.menu.key === key) { st.menu.info = { error: true }; paintMenu(st); } });
  }

  function closeMenu(st) {
    st.menu = null;
    st.menuEl.hidden = true;
    st.menuEl.innerHTML = '';
  }

  const item = (id, label, hint, disabled) => `<button type="button" class="t6-mi" role="menuitem" data-t6-mi="${esc(id)}"${disabled ? ' disabled' : ''}>`
    + `<span class="t6-mi-l">${esc(label)}</span>${hint ? `<span class="t6-mi-h">${esc(hint)}</span>` : ''}</button>`;

  function menuItems(menu) {
    const row = menu.row;
    const setup = menu.info && menu.info.setup;
    const items = [];
    const copy = row.hardware_id || row.entity;
    if (copy) items.push(item('copy', 'Copy id', copy));
    // Zach, 2026-10-24: "filter to only show things inside certain things" and step into a folder (VS Code's Enter Folder)
    if (row.children.length && typeof menu.nav === 'function') {
      if (copy) items.push('<div class="t6-mi-sep"></div>');
      items.push(item('nav:inside', "Show only what's inside", 'a filter chip above the tree'));
      items.push(item('nav:into', 'Step into', 'Ctrl+Shift+Enter · make this the top of the tree'));
    }
    if (setup && setup.role === 'board') {
      items.push(item('setup:expect', setup.expected ? "Don't expect" : 'Expect', setup.rig ? `in ${setup.rig}` : "its adapter isn't in the expected setup", !setup.can.expect));
    } else if (setup && setup.role === 'device') {
      items.push(item('setup:expect', "Expect what's here", setup.rig ? `updates ${setup.rig}` : 'adds it to the expected setup', !setup.can.expect));
      if (setup.expected) items.push(item('setup:forget', "Don't expect", `leaves ${setup.rig}`, !setup.can.forget));
    }
    const actions = (menu.info && menu.info.actions) || [];
    if (actions.length) items.push('<div class="t6-mi-sep"></div>');
    for (const action of actions) items.push(item(`util:${action.name}`, action.title, action.state === 'ready' ? '' : action.reason || action.state, false));
    if (!menu.info) items.push('<div class="t6-mi-note">Loading…</div>');
    else if (menu.info.error) items.push('<div class="t6-mi-note">The host did not answer.</div>');
    const removable = setup && setup.role === 'board' && setup.can.forget;
    if (visibility() || removable) items.push('<div class="t6-mi-sep"></div>');
    if (visibility()) {
      const verdict = visibility().isHidden(row);
      items.push(item('vis', verdict.hidden || row.dimmed ? 'Show' : verdict.surfaced ? 'Acknowledge and hide' : 'Hide', 'Visibility, kept with the app settings'));
    }
    // WHY Remove beside Hide, one click (Zach, 2026-11-10: "if I unplug a can id or change the id etc. than I can just remove it there
    // quickly"): Hide keeps the row out of sight for good; Remove forgets it, and it comes back the moment it reports again, so it needs no
    // confirm. Boards only: a device on USB would be back at once.
    if (removable) items.push(item('remove', 'Remove', 'Forgets the row · it comes back as soon as it reports again'));
    return items.join('');
  }

  function paintMenu(st) {
    const menu = st.menu;
    if (!menu) return;
    let body;
    if (menu.result) {
      body = `<div class="t6-mi-note">${esc(menu.result.ok ? menu.result.message || 'Done' : `Not done: ${menu.result.message || menu.result.error || menu.result.refused || 'refused'}`)}</div>`;
    } else if (menu.confirm) {
      body = `<div class="t6-mi-confirm">${esc(menu.confirm.text)}</div><div class="t6-mi-foot">`
        + `<button type="button" class="t6-btn" data-t6-mi="cancel">Cancel</button>`
        + `<button type="button" class="t6-btn t6-primary" data-t6-mi="confirm"${menu.busy ? ' disabled' : ''}>${esc(menu.confirm.verb)}</button></div>`;
    } else {
      body = menuItems(menu);
    }
    st.menuEl.innerHTML = body;
    st.menuEl.hidden = false;
    const rootRect = st.root.getBoundingClientRect();
    const width = st.menuEl.offsetWidth || 260;
    // a ⋮ opens it leftwards from the button; a right-click opens it rightwards from the mouse, as menus do
    st.menuEl.style.left = `${Math.max(4, Math.min(menu.x - rootRect.left - (menu.atPoint ? 0 : width), rootRect.width - width - 4))}px`;
    st.menuEl.style.top = `${Math.max(4, menu.y - rootRect.top + 2)}px`;
  }

  const VERBS = { expect: 'Confirm', forget: 'Confirm' };

  function onMenuClick(st, event) {
    const target = event.target.closest('[data-t6-mi]');
    const menu = st.menu;
    if (!target || !menu) return;
    let id = target.getAttribute('data-t6-mi');
    if (id === 'cancel') { menu.confirm = null; paintMenu(st); return; }
    if (id.startsWith('nav:')) { closeMenu(st); menu.nav(id.slice(4), menu.row); return; }
    if (id === 'copy') {
      const text = menu.row.hardware_id || menu.row.entity;
      try { navigator.clipboard.writeText(text); } catch (error) { /* not allowed here */ }
      closeMenu(st);
      return;
    }
    if (id === 'vis') { visibility().toggleRow(menu.row); closeMenu(st); return; }
    if (id === 'remove') { menu.confirm = { op: 'forget', text: '', verb: '' }; id = 'confirm'; }
    if (id.startsWith('util:')) {
      const name = id.slice(5);
      closeMenu(st);
      if (typeof st.opts.onRowAction === 'function') st.opts.onRowAction(name, menu.row);
      else if (typeof st.opts.onSelectEntity === 'function') st.opts.onSelectEntity(menu.row.entity); // the detail's Utilities has it
      return;
    }
    if (id.startsWith('setup:')) {
      const op = id.slice(6);
      const setup = menu.info && menu.info.setup;
      menu.confirm = { op, text: (setup && setup.confirm && setup.confirm[op]) || `${op}?`, verb: VERBS[op] };
      paintMenu(st);
      return;
    }
    if (id === 'confirm' && menu.confirm && !menu.busy) {
      menu.busy = true;
      paintMenu(st);
      POST(`/api/v1/${menu.confirm.op}`, { path: menu.entity, rig: null, reason: '' })
        .then((result) => {
          menu.result = result; menu.busy = false; paintMenu(st);
          // a removed row that was selected would leave the detail asking for an entity that is gone: select its parent
          if (result.ok && menu.confirm.op === 'forget' && menu.info && menu.info.setup && menu.info.setup.role === 'board' && st.selected === menu.key && menu.row.parent && st.byKey.has(menu.row.parent)) select(st, menu.row.parent);
          setTimeout(() => { if (st.menu === menu) closeMenu(st); }, 1800);
        })
        .catch((error) => { menu.result = { ok: false, message: `the host did not answer: ${error}` }; menu.busy = false; paintMenu(st); });
    }
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
    description: 'Quiet chevrons always visible on folders (IDE), no guides, rounded rows and hover-revealed affordances (Notion); a neutral presence dot per row, the diagnostics level as a shape at the far right, quiet when OK.',
    consumes: 'rows',
    order: 0,
    render,
  };
})();
