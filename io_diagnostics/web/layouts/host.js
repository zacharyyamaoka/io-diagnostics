// Page layouts: five proposals for WHERE navigation, search, views and the log live (Zach, 2026-10-24: "please make 5
// proposals for how to better layout the app. I think the search bar should be inside of the entity tree on top just as
// obsidian does it"). Not a rewrite: a layout is a SHELL that re-arranges the page's existing parts (the tree pane, the
// search box, the main area with its three views and the detail, the runs panel, the status bar) by moving their DOM
// nodes into its own arrangement. Every behaviour (selection, search, history, utilities, runs) stays the page's.
//
//   window.IOLayouts[id] = {name, description, order, keepTree, reach, mount(ctx), unmount(ctx), update(ctx)}
//
// layouts/index.json pre-lists every proposal (the page loads layouts/<file> only for ready=true); the bottom-right
// "Layout" select switches them live and remembers the choice (never a URL flag). This file is the seam: the registry,
// the picker, the part mover (parts taken out of the page go back where they were on unmount), and the helpers every
// layout shares: the tree pane (search on top), draggable remembered panes that fold away, the View options anchor.
// app.js calls start(api) once and update() after every render; nothing else of the page knows a layout exists.
'use strict';
(function () {
  const PICK = 'io-diagnostics.page-layout';
  const NS = 'io-diagnostics.lx.';
  const DEFAULT = 'l1';
  // the page's own nodes a layout may take; looked up once, before anything moves
  const PARTS = {
    views: '#views', search: '.toolbar .search', vo: '.vo-wrap', voPop: '#view-options', inspToggle: '#insp-toggle',
    mode: '#mode', sources: '#sources', main: '#main', treeHost: '#tree-host', runsRoot: '#runs-root', runsStatus: '#runs-status',
  };
  const TOGGLES = '[data-t1-toggle],[data-t2-toggle],[data-v3-toggle],[data-v4-toggle],[data-t6-toggle]';

  const host = { api: null, ctx: null, id: null, ids: [] };
  window.IOLayouts = window.IOLayouts || {};
  window.IOLayoutHost = host;

  const read = (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch (_) { /* private mode: lasts this page only */ } };
  const registry = () => window.IOLayouts;
  const layoutIds = () => Object.keys(registry()).filter((id) => registry()[id] && typeof registry()[id].mount === 'function')
    .sort((a, b) => ((registry()[a].order ?? 50) - (registry()[b].order ?? 50)) || a.localeCompare(b));

  // ------------------------------------------------------------------------------------------ icons ----
  // the page's sprite has the entity icons; these are the layout chrome's (same 24-unit stroke family, class "ic")
  const SPRITE = `<svg id="lx-sprite" width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
    <symbol id="lx-collapse" viewBox="0 0 24 24"><path d="m7 20 5-5 5 5M7 4l5 5 5-5"/></symbol>
    <symbol id="lx-dash" viewBox="0 0 24 24"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/><rect x="13" y="11" width="8" height="10" rx="1.5"/><rect x="3" y="14" width="8" height="7" rx="1.5"/></symbol>
    <symbol id="lx-expand" viewBox="0 0 24 24"><path d="m7 15 5 5 5-5M7 9l5-5 5 5"/></symbol>
    <symbol id="lx-eye" viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></symbol>
    <symbol id="lx-eye-off" viewBox="0 0 24 24"><path d="M3 3l18 18M10.6 5.1A9.6 9.6 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.7A17 17 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 4.4-1M9.9 9.9a3 3 0 0 0 4.2 4.2"/></symbol>
    <symbol id="lx-plug" viewBox="0 0 24 24"><path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0V7ZM12 17v5"/></symbol>
    <symbol id="lx-sidebar" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></symbol>
    <symbol id="lx-panel-close" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M16 15l-3-3 3-3"/></symbol>
    <symbol id="lx-rightbar" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></symbol>
    <symbol id="lx-bottombar" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 14h18"/></symbol>
    <symbol id="lx-table" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/></symbol>
    <symbol id="lx-log" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10"/></symbol>
    <symbol id="lx-gear" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></symbol>
    <symbol id="lx-back" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></symbol>
    <symbol id="lx-forward" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></symbol>
    <symbol id="lx-devices" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="5" rx="1"/><rect x="14" y="10" width="7" height="4" rx="1"/><rect x="14" y="17" width="7" height="4" rx="1"/><path d="M6.5 8v10a1 1 0 0 0 1 1H14M6.5 12H14"/></symbol>
    <symbol id="lx-home" viewBox="0 0 24 24"><path d="M3 11 12 3l9 8M5 9.5V20h5v-5h4v5h5V9.5"/></symbol>
    <symbol id="lx-gauge" viewBox="0 0 24 24"><path d="M4 18a8 8 0 1 1 16 0M12 18l4-6"/></symbol>
    <symbol id="lx-cmd" viewBox="0 0 24 24"><path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3Z"/></symbol>
    <symbol id="lx-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></symbol>
    <symbol id="lx-x" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></symbol>
    <symbol id="lx-corner" viewBox="0 0 24 24"><path d="M4 4v10a4 4 0 0 0 4 4h12M15 13l5 5-5 5"/></symbol>
  </defs></svg>`;
  const ico = (name, cls = 'ic') => `<svg class="${cls}"><use href="#${name.startsWith('i-') || name.startsWith('lx-') || name.startsWith('s-') ? name : `lx-${name}`}"/></svg>`;

  // ------------------------------------------------------------------------------------- base style ----
  // Colours, fonts, radii and sizes are the page's tokens (tokens.css), so every skin and light / dark works.
  const BASE_CSS = `
.lx { flex: 1; min-height: 0; min-width: 0; display: flex; background: var(--io-surface); position: relative; overflow: hidden; }
:where(.lx) button { font: inherit; color: inherit; }
.lx #main { flex: 1; min-width: 0; min-height: 0; }
.lx #main.v-tree > .left, .lx #main.v-tree > .sash { display: none; }
.lx #sources { margin: 0; }
.lx .vo-pop { position: fixed; z-index: 60; }
.lx-ib { width: 26px; height: 26px; flex: none; display: inline-flex; align-items: center; justify-content: center; border: 0; padding: 0; background: transparent; color: var(--io-muted); border-radius: var(--io-radius-sm); cursor: pointer; position: relative; }
.lx-ib:hover { background: var(--io-hover); color: var(--io-text); }
.lx-ib.on, .lx-ib[aria-pressed="true"] { background: var(--io-pressed); color: var(--io-text-strong); }
.lx-ib[disabled] { opacity: .4; cursor: default; }
.lx-ib .ic { width: 16px; height: 16px; }
.lx .vo-btn { width: 26px; height: 26px; border: 0; background: transparent; border-radius: var(--io-radius-sm); }
.lx .vo-btn:hover { background: var(--io-hover); }
.lx .vo-btn.on { background: var(--io-pressed); }
.lx .vo-btn .ic { width: 16px; height: 16px; }
/* the tree pane: a header row, the search box ON TOP of the tree (Zach's ask), then the tree that scrolls */
.lx-tree { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--io-surface); }
.lx-tree > .search { width: auto; flex: none; margin: 0 10px 6px; }
.lx-tree > .search .lx-clear { display: none; width: 18px; height: 18px; padding: 0; border: 0; border-radius: var(--io-radius-sm); background: transparent; color: var(--io-faint); cursor: pointer; align-items: center; justify-content: center; }
.lx-tree > .search .lx-clear:hover { background: var(--io-hover); color: var(--io-text); }
.lx-tree > .search.has-query .lx-clear { display: inline-flex; }
.lx-tree > .search .lx-clear .ic { width: 12px; height: 12px; }
.lx-tree-scroll { flex: 1; min-height: 0; overflow: auto; }
.lx-tree-scroll .t6-head { height: 30px; }
/* a pane boundary: draggable, remembered, double-click for the default, arrow keys when focused (as the page's own sash). WHY z-index 7:
   the entity page's sticky header (d6-top) is 6 and covers the 3 px of the boundary that lies over the page, so a drag there missed */
.lx-sash { flex: none; position: relative; z-index: 7; touch-action: none; outline: none; }
.lx-sash-x { width: 7px; margin: 0 -3px; cursor: col-resize; }
.lx-sash-y { height: 7px; margin: -3px 0; cursor: row-resize; }
.lx-sash::before { content: ""; position: absolute; background: transparent; transition: background .1s; }
.lx-sash-x::before { top: 0; bottom: 0; left: 3px; width: 1px; background: var(--io-border); }
.lx-sash-y::before { left: 0; right: 0; top: 3px; height: 1px; background: var(--io-border); }
.lx-sash:hover::before, .lx-sash:focus-visible::before, body.lx-dragging .lx-sash.on::before { background: var(--io-selected-line); }
.lx-sash-x:hover::before, .lx-sash-x:focus-visible::before, body.lx-dragging .lx-sash-x.on::before { left: 2px; width: 3px; }
.lx-sash-y:hover::before, .lx-sash-y:focus-visible::before, body.lx-dragging .lx-sash-y.on::before { top: 2px; height: 3px; }
body.lx-dragging { user-select: none; }
body.lx-dragging.lx-x { cursor: col-resize; }
body.lx-dragging.lx-y { cursor: row-resize; }
`;

  // --------------------------------------------------------------------------------------- the ctx ----
  function makeCtx(api) {
    const S = api.S;
    const taken = new Map();   // part name -> {node, mark}
    const undo = [];           // cleanups, newest last
    const found = new Map(Object.entries(PARTS).map(([name, selector]) => [name, document.querySelector(selector)]));
    const ctx = {
      api, S, $: api.$, esc: api.esc, usb: api.usb, ico,
      root: null,
      // a part of the page, moved out of its place (a comment marks the place; unmount puts it back)
      take(name) {
        const node = found.get(name);
        if (!node) return null;
        if (!taken.has(name)) {
          const mark = document.createComment(`lx:${name}`);
          node.parentNode.insertBefore(mark, node);
          taken.set(name, { node, mark });
        }
        return node;
      },
      restoreParts() {
        for (const { node, mark } of taken.values()) if (mark.parentNode) mark.replaceWith(node);
        taken.clear();
      },
      cleanup(fn) { undo.push(fn); },
      runCleanups() { while (undo.length) { try { undo.pop()(); } catch (error) { console.error('layout cleanup:', error); } } },
      css(text) {
        const style = document.createElement('style');
        style.dataset.lxCss = '1';
        style.textContent = text;
        document.head.appendChild(style);
        ctx.cleanup(() => style.remove());
      },
      on(target, type, fn, opts) { target.addEventListener(type, fn, opts); ctx.cleanup(() => target.removeEventListener(type, fn, opts)); },
      el(html) { const box = document.createElement('div'); box.innerHTML = html.trim(); return box.firstElementChild; },
      pref: (key, fallback) => { const value = read(NS + key); return value === null || value === '' ? fallback : value; },
      setPref: (key, value) => write(NS + key, value),
      // a node that lives outside ctx.root (the status bar, document.body): removed again on unmount
      adopt(node, parent, before) { (parent || document.body).insertBefore(node, before || null); ctx.cleanup(() => node.remove()); return node; },
      iconButton({ icon, title, click, cls = '', pressed, id }) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `lx-ib ${cls}`.trim();
        if (id) button.id = id;
        button.title = title;
        button.setAttribute('aria-label', title);
        if (pressed !== undefined) button.setAttribute('aria-pressed', String(pressed));
        button.innerHTML = ico(icon);
        if (click) button.addEventListener('click', click);
        return button;
      },

      // ---- the page's views, history and tree (everything goes through the page's own handlers) ----
      go(view) { api.setView(view); },
      history: { back: () => history.back(), forward: () => history.forward() },
      select(path) { api.select(path); },
      tree: {
        // WHY clicks: expansion lives inside each tree style; the chevron is its one public control, so every style works
        each(open) {
          for (let pass = 0; pass < 60; pass += 1) {
            const items = [...document.querySelectorAll(`#tree-host [role=treeitem][aria-expanded="${open ? 'false' : 'true'}"]`)];
            const toggles = items.map((item) => item.querySelector(TOGGLES)).filter(Boolean);
            if (!toggles.length) return;
            for (const toggle of open ? toggles : toggles.reverse()) toggle.click();
            if (!open) return; // collapsing deepest-first closes every visible branch in one pass
          }
        },
        collapseAll() { ctx.tree.each(false); },
        expandAll() { ctx.tree.each(true); },
        // the structure's first toggle (p1: "Ports"): the "show what is hidden" switch of the View options popover
        hiddenToggle() {
          const structure = (window.IOStructures || {})[S.structure] || {};
          const toggle = (structure.toggles || []).find((item) => api.toggleShown(item));
          return toggle ? { key: toggle.key, label: toggle.label, on: Boolean(api.structureOptions()[toggle.key]) } : null;
        },
        setHidden(on) { const toggle = ctx.tree.hiddenToggle(); if (toggle) api.setToggle(toggle.key, on); },
        // Connected only (docs/api-v1.md "Presence"): the tree keeps what answers now; while on, the page keeps the presence checks running
        connected: () => Boolean(S.connected),
        setConnected(on) { if (api.setConnected) api.setConnected(on); },
        // while on: one quiet line per adapter it cannot look at now ("fdcanusb 272813EF: moteus_tool has the port"), else none. WHY on
        // the page and not only in the title: an adapter with no boards under it reads "nothing answers" unless it says it did not look
        connectedNotes() {
          if (!S.connected || !S.presence) return [];
          return (S.presence.adapters || []).map((adapter) => {
            const last = adapter.last || {};
            const why = adapter.state === 'paused' ? adapter.reason : last.outcome && last.outcome !== 'looked' ? last.message : '';
            return why ? `${adapter.name}: not looking (${why})` : '';
          }).filter(Boolean);
        },
        // the toggle's words: what it does, and while on, how often it looks and any adapter it cannot look at now (in plain words)
        connectedTitle() {
          if (!S.connected) return 'Connected only: show just what answers now';
          const answer = S.presence;
          const period = answer && answer.period_s ? ` every ${answer.period_s % 1 ? answer.period_s.toFixed(1) : answer.period_s} s` : '';
          const paused = ((answer && answer.adapters) || []).map((adapter) => {
            const last = adapter.last || {};
            const why = adapter.state === 'paused' ? adapter.reason : last.outcome && last.outcome !== 'looked' ? last.message : '';
            return why ? `${adapter.name}: ${why}` : '';
          }).filter(Boolean);
          return `Connected only is on: what answers now (each adapter's bus is checked${period}). Click to show everything.${paused.length ? `\n${paused.join('\n')}` : ''}`;
        },
      },
      // View options: any trigger carries data-vo-anchor (the page's own #vo-btn too); the popover is placed beside it
      openViewOptions(anchor) { host.placePopover(anchor || ctx.$('vo-btn')); api.setViewOptions(!S.vo); },
      runs: {
        button: () => ctx.$('runs-status').querySelector('button'),
        open: () => Boolean(ctx.$('runs-root') && !ctx.$('runs-root').hidden),
        set(open) { if (ctx.runs.open() !== open) { const button = ctx.runs.button(); if (button) button.click(); } },
        toggle() { ctx.runs.set(!ctx.runs.open()); },
        count: () => (S.data && S.data.runs ? S.data.runs.active || 0 : 0),
      },
      // The Activity log inside a layout's own panel or pane. WHY one seam: today it is the page's own log (S.act, eventRow,
      // renderActivity); the Activity view lane (activity/log.js) draws it with its own DOM, and only this function changes.
      //   draw(container)                 the whole log, as the Activity view shows it (its All events | Faults filter)
      //   draw(container, {scope, head})  one entity's events and below (scope: its path; null: every device) under `head`
      activity: {
        draw(container, opts) {
          if (!S.act) return;
          let html;
          if (!opts && api.renderActivity) html = api.renderActivity();
          else {
            opts = opts || { scope: null, title: 'Activity log' };
            const events = (S.act.events || []).filter((event) => !opts.scope || event.path === opts.scope || String(event.path || '').startsWith(`${opts.scope}/`));
            html = `<div class="act"><section><h2>${opts.title} <span class="act-count">· ${events.length} event${events.length === 1 ? '' : 's'}</span>${opts.controls || ''}</h2>`
              + `${events.map((event) => api.eventRow(event)).join('') || `<div class="muted">${opts.empty || 'Nothing logged yet.'}</div>`}</section></div>`;
          }
          if (container.dataset.lxLog !== html) { container.dataset.lxLog = html; container.innerHTML = html; }
        },
      },
      // the breadcrumb of the selection, root first, in the API's own names; a USB port and a CAN bus under its
      // adapter are plumbing, not places (Zach's example: bam-GPU › USB bus 1 › fdcanusb 272813EF › CAN 1)
      crumbs(path) {
        if (!path || !S.byPath) return [];
        const out = [];
        const parts = path.split('/');
        for (let at = 1; at <= parts.length; at += 1) {
          const prefix = parts.slice(0, at).join('/');
          const node = S.byPath.get(prefix);
          if (!node) continue;
          const parent = S.byPath.get(parts.slice(0, at - 1).join('/'));
          const last = at === parts.length;
          if (!last && (node.kind === 'port' || (node.kind === 'bus' && parent && parent.kind === 'device'))) continue;
          out.push({ path: prefix, name: window.IOUsb ? window.IOUsb.label(node.name) : node.name, raw: node.name, level: node.level || null });
        }
        return out;
      },
      counts() { return (S.data && S.data.summary && S.data.summary.counts) || {}; },
      attention() { return (S.data && S.data.summary && S.data.summary.attention) || []; },
    };

    // ---- the tree pane: [header] [search] [tree], one shape for every layout --------------------------------
    // WHY the search is moved, not copied: it is the page's own input (state, filter, placeholder), only its place changes
    ctx.treePane = function treePane({ cls = '', head = null, tools = [], below = null } = {}) {
      const pane = document.createElement('div');
      pane.className = `lx-tree ${cls}`.trim();
      pane.dataset.lxTree = '1';
      if (head) pane.appendChild(head);
      for (const node of tools) pane.appendChild(node);
      const search = ctx.take('search');
      if (search) {
        const clear = ctx.iconButton({ icon: 'x', title: 'Clear the search', cls: 'lx-clear' });
        clear.addEventListener('click', () => {
          const input = ctx.$('search');
          input.value = '';
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.focus();
        });
        search.appendChild(clear);
        ctx.cleanup(() => clear.remove());
        pane.appendChild(search);
      }
      const scroll = document.createElement('div');
      scroll.className = 'lx-tree-scroll';
      scroll.appendChild(ctx.take('treeHost'));
      pane.appendChild(scroll);
      if (below) pane.appendChild(below);
      pane.scroller = scroll;
      return pane;
    };

    // ---- pane boundaries: draggable, remembered, folding away, arrow keys ---------------------------------
    // o: {key, axis 'x'|'y', anchor 'start'|'end' (the pane's edge on the root: left / top or right / bottom), def, min,
    //     max(total)->px, varName, root (gets the css variable and the lx-fold-<key> class), within (the rect measured from), offset, folded (default)}
    ctx.sash = function sash(o) {
      const root = o.root || ctx.root;
      const horizontal = o.axis === 'x';
      const stored = Number(ctx.pref(o.key, ''));
      const st = { size: stored > 0 ? stored : o.def, folded: ctx.pref(`${o.key}.fold`, o.folded ? '1' : '0') === '1' }; // o.folded: closed until the person opens it
      const el = document.createElement('div');
      el.className = `lx-sash lx-sash-${o.axis} lx-sash-${o.key}`;
      el.setAttribute('role', 'separator');
      el.setAttribute('aria-orientation', horizontal ? 'vertical' : 'horizontal');
      el.setAttribute('aria-label', o.label || 'Resize the pane');
      el.title = 'Drag to resize · double-click to reset';
      el.tabIndex = 0;
      const rectOf = () => (o.within || root).getBoundingClientRect();
      const limit = () => {
        const total = horizontal ? rectOf().width : rectOf().height;
        return Math.max(o.min, o.max ? o.max(total) : total - 240);
      };
      const clamp = (value) => Math.round(Math.max(o.min, Math.min(value, limit())));
      const apply = () => {
        root.style.setProperty(o.varName, `${clamp(st.size)}px`);
        root.classList.toggle(`lx-fold-${o.key}`, st.folded);
        el.style.display = st.folded ? 'none' : '';
        if (o.onChange) o.onChange(st);
      };
      const fromPointer = (event) => {
        const rect = rectOf();
        const edge = o.offset || 0; // what sits between the measured edge and the pane (L2's activity bar)
        if (horizontal) return (o.anchor === 'end' ? rect.right - event.clientX : event.clientX - rect.left) - edge;
        return (o.anchor === 'end' ? rect.bottom - event.clientY : event.clientY - rect.top) - edge;
      };
      let drag = null;
      el.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        drag = { id: event.pointerId };
        el.setPointerCapture(event.pointerId);
        el.classList.add('on');
        document.body.classList.add('lx-dragging', horizontal ? 'lx-x' : 'lx-y');
        event.preventDefault();
      });
      el.addEventListener('pointermove', (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        st.size = clamp(fromPointer(event));
        apply();
      });
      const end = (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        drag = null;
        el.classList.remove('on');
        document.body.classList.remove('lx-dragging', 'lx-x', 'lx-y');
        ctx.setPref(o.key, String(st.size));
      };
      el.addEventListener('pointerup', end);
      el.addEventListener('pointercancel', end);
      el.addEventListener('dblclick', () => { st.size = o.def; ctx.setPref(o.key, ''); apply(); });
      el.addEventListener('keydown', (event) => {
        const keys = horizontal ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown'];
        if (!keys.includes(event.key)) return;
        const growing = keys[o.anchor === 'end' ? 0 : 1] === event.key;
        st.size = clamp(st.size + (growing ? 16 : -16));
        ctx.setPref(o.key, String(st.size));
        apply();
        event.preventDefault();
      });
      ctx.on(window, 'resize', apply);
      const handle = {
        el,
        size: () => st.size,
        folded: () => st.folded,
        fold(folded) { st.folded = Boolean(folded); ctx.setPref(`${o.key}.fold`, st.folded ? '1' : '0'); apply(); },
        toggle() { handle.fold(!st.folded); },
        apply,
      };
      ctx.cleanup(() => {
        root.style.removeProperty(o.varName);
        root.classList.remove(`lx-fold-${o.key}`);
      });
      apply();
      return handle;
    };
    return ctx;
  }

  // ------------------------------------------------------------------------------ view options anchor ----
  // WHY a fixed popover placed by the trigger: View options is one popover the page owns; the sliders button of one layout
  // and the gear of another both open it, each beside itself
  host.placePopover = function placePopover(anchor) {
    const pop = document.getElementById('view-options');
    if (!pop || !anchor) return;
    const rect = anchor.getBoundingClientRect();
    const place = anchor.dataset.voPlace || 'below';
    const width = 330;
    const clampX = (x) => Math.max(8, Math.min(x, window.innerWidth - width - 8));
    pop.style.right = pop.style.bottom = pop.style.top = pop.style.left = 'auto';
    if (place === 'right') { pop.style.left = `${rect.right + 8}px`; pop.style.bottom = `${Math.max(8, window.innerHeight - rect.bottom)}px`; }
    else if (place === 'above') { pop.style.left = `${clampX(rect.left)}px`; pop.style.bottom = `${window.innerHeight - rect.top + 6}px`; }
    else if (place === 'below-end') { pop.style.left = `${clampX(rect.right - width)}px`; pop.style.top = `${rect.bottom + 6}px`; }
    else { pop.style.left = `${clampX(rect.left)}px`; pop.style.top = `${rect.bottom + 6}px`; }
  };

  // ----------------------------------------------------------------------------------- mount / unmount ----
  function unmount() {
    const layout = registry()[host.id];
    const ctx = host.ctx;
    if (layout && layout.classic) { document.documentElement.removeAttribute('data-layout'); host.id = null; return; }
    if (!layout || !ctx || !ctx.root) return;
    try { layout.unmount(ctx); } catch (error) { console.error(`layout ${host.id} unmount:`, error); }
    ctx.runCleanups();
    ctx.restoreParts();
    ctx.root.remove();
    ctx.root = null;
    document.querySelector('.toolbar').style.display = '';
    document.documentElement.removeAttribute('data-layout');
    host.id = null;
  }

  function mount(id) {
    const layout = registry()[id];
    if (!layout) return;
    const ctx = host.ctx;
    if (host.id) unmount();
    if (layout.classic) { host.id = id; document.documentElement.dataset.layout = id; host.update(); return; } // the page as it was: nothing moves
    const windowEl = document.querySelector('.window');
    const root = document.createElement('div');
    root.className = `lx lx-${id}`;
    root.id = 'lx-root';
    windowEl.insertBefore(root, windowEl.querySelector('.statusbar'));
    ctx.root = root;
    document.querySelector('.toolbar').style.display = 'none'; // the page's own toolbar: its controls move into the layout
    host.id = id;
    document.documentElement.dataset.layout = id;
    try {
      layout.mount(ctx);
    } catch (error) {
      console.error(`layout ${id} mount:`, error);
      unmount();
      return;
    }
    // WHY a fixed-position popover: placed by whichever trigger opened it (see placePopover)
    host.placePopover(ctx.$('vo-btn'));
    host.update();
    window.dispatchEvent(new Event('resize'));
  }

  // L0: the page as it was before the layouts (its own toolbar over the tree | detail panes). Not a proposal: the baseline to
  // compare against, and the arrangement the page's older suites (test_utilities.js, test_tree_variants.js) measure.
  registry().l0 = { name: 'Today', description: 'The page as it was: the toolbar on top, the tree and the detail below it. No layout.', order: 0, classic: true, keepTree: false, mount() {}, unmount() {} };

  host.keepsTree = () => Boolean(host.id && registry()[host.id] && registry()[host.id].keepTree);
  host.update = function update() {
    const layout = host.id && registry()[host.id];
    if (!layout || !host.ctx || !host.api.S.data || !layout.update) return;
    try { layout.update(host.ctx); } catch (error) { console.error(`layout ${host.id} update:`, error); }
  };

  host.use = function use(id) {
    if (!registry()[id]) return;
    write(PICK, id);
    const picker = document.getElementById('layout-pick');
    if (picker) picker.value = id;
    mount(id);
    if (host.api.S.data) host.api.render();
  };

  async function readManifest() {
    try {
      const response = await fetch('layouts/index.json');
      const manifest = await response.json();
      return { default: manifest.default, entries: (manifest.layouts || []).filter((entry) => entry.ready) };
    } catch (error) {
      console.warn('manifest layouts/index.json:', error);
      return { default: null, entries: [] };
    }
  }
  const loadScript = (src) => new Promise((resolve) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = script.onerror = () => resolve();
    document.head.appendChild(script);
  });

  host.start = async function start(api) {
    host.api = api;
    const manifest = await readManifest();
    await Promise.all(manifest.entries.map((entry) => loadScript(`layouts/${entry.file}`)));
    const ids = layoutIds();
    host.ids = ids;
    if (!ids.length) return;
    document.body.insertAdjacentHTML('afterbegin', SPRITE);
    const style = document.createElement('style');
    style.textContent = BASE_CSS;
    document.head.appendChild(style);
    host.ctx = makeCtx(api);
    // the Layout picker (bottom-right, temporary: Zach's rule, never a URL flag)
    const picker = document.getElementById('layout-pick');
    const stored = read(PICK);
    const first = ids.includes(stored) ? stored : ids.includes(manifest.default) ? manifest.default : ids.includes(DEFAULT) ? DEFAULT : ids[0];
    if (picker) {
      picker.innerHTML = ids.map((id) => `<option value="${id}" title="${registry()[id].description || ''}">${id.toUpperCase()} · ${registry()[id].name || id}</option>`).join('');
      picker.value = first;
      picker.addEventListener('change', () => host.use(picker.value));
    }
    // View options' own trigger and every [data-vo-anchor]: the popover opens beside whichever was clicked
    document.addEventListener('click', (event) => {
      const anchor = event.target.closest('[data-vo-anchor]');
      if (anchor) {
        host.placePopover(anchor);
        event.stopPropagation(); // the page's document handler would see a click outside the popover and close it
        api.setViewOptions(!api.S.vo);
        return;
      }
      const own = event.target.closest('#vo-btn');
      if (own) host.placePopover(own);
    }, true);
    mount(first);
  };
}());
