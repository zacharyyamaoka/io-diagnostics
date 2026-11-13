// The Ctrl-K / Ctrl-P / Ctrl-O (⌘ on a Mac) command palette, shared by the layouts that offer it (L1 Obsidian, L4 Notion). It began as L4's own; Zach picked
// L1 with L4's palette on top (2026-10-24), so it lives here once. It jumps to a device (what needs you first, then every device the
// words match) and runs actions (go to a view, View options, collapse / expand the tree, step into the selected device, the
// selected device's utilities as a dry run, light / dark). Nothing runs until a row is chosen; a utility opens its dry-run sheet.
//
//   const palette = IOLayoutPalette.create(ctx, { anchor: () => node, go: () => [item...], after: () => {} });
//   palette.open() / .close() / .toggle() / .isOpen()
//
// item = { icon, label, sub, level?, run }. `go` is the layout's own "Go to" group (its views); `after` runs after any choice (L4 leaves
// its Home screen). Colours come from tokens only; the status shape is the page's own sprite (#s-<level>).
'use strict';
(function () {
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  // WHY three keys: Zach, 2026-11-13, "bind ctrl P ctrl K and ctrl O to open the search/command". Ctrl+P (VS Code's quick open) and
  // Ctrl+O replace the browser's print and open-file on this page; ⌘ works the same on a Mac.
  const PALETTE_KEYS = new Set(['k', 'p', 'o']);
  const CSS = `
.lx-pal-back { position: fixed; inset: 0; z-index: 70; background: var(--io-overlay); display: flex; justify-content: center; align-items: flex-start; padding-top: 14vh; }
.lx-pal-back[hidden] { display: none; }
.lx-pal { width: min(620px, calc(100vw - 32px)); max-height: 62vh; display: flex; flex-direction: column; background: var(--io-popover); border: 1px solid var(--io-border); border-radius: var(--io-radius-lg); box-shadow: var(--io-shadow-pop); overflow: hidden; }
.lx-pal-in { flex: none; display: flex; align-items: center; gap: 10px; padding: 0 14px; height: 46px; border-bottom: 1px solid var(--io-border-soft); color: var(--io-muted); }
.lx-pal-in input { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--io-text); font: inherit; font-size: 14.5px; }
.lx-pal-list { flex: 1; min-height: 0; overflow: auto; padding: 4px 6px 6px; }
.lx-pal-g { padding: 8px 8px 3px; font-size: 11px; color: var(--io-faint); font-weight: 600; letter-spacing: .3px; }
.lx-pal-i { display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 10px; border-radius: var(--io-radius-sm); cursor: pointer; color: var(--io-text); }
.lx-pal-i.on { background: var(--io-selected); color: var(--io-text-strong); }
.lx-pal-i .ic { width: 16px; height: 16px; color: var(--io-icon); }
.lx-pal-i .lab { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lx-pal-i .sub { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-faint); font-size: 12px; }
.lx-pal-i .shape { width: 13px; height: 13px; margin-left: auto; }
.lx-pal-foot { flex: none; padding: 6px 14px; border-top: 1px solid var(--io-border-soft); color: var(--io-faint); font-size: 11.5px; }
.lx-pal-empty { padding: 16px 12px; color: var(--io-muted); }
`;

  function create(ctx, opts = {}) {
    ctx.css(CSS);
    const s = { hit: 0, items: [] };
    const back = ctx.el('<div class="lx-pal-back" hidden><div class="lx-pal" role="dialog" aria-label="Jump to a device or run an action">'
      + `<div class="lx-pal-in">${ctx.ico('i-search')}<input type="text" placeholder="Jump to a device, or run an action…" autocomplete="off" spellcheck="false" aria-label="Jump to or run"></div>`
      + '<div class="lx-pal-list" role="listbox"></div><div class="lx-pal-foot">↑ ↓ move · Enter open · Esc close</div></div></div>');
    ctx.adopt(back, document.body);
    s.back = back;
    s.input = back.querySelector('input');
    s.list = back.querySelector('.lx-pal-list');

    const api = {
      isOpen: () => !back.hidden,
      open() { back.hidden = false; s.input.value = ''; s.hit = 0; paint(); s.input.focus(); },
      close() { back.hidden = true; },
      toggle() { if (back.hidden) api.open(); else api.close(); },
    };

    function markHit() {
      s.list.querySelectorAll('[data-i]').forEach((row) => { const on = Number(row.dataset.i) === s.hit; row.classList.toggle('on', on); row.setAttribute('aria-selected', String(on)); if (on) row.scrollIntoView({ block: 'nearest' }); });
    }
    function choose(at) {
      const item = s.items[at];
      if (!item) return;
      api.close();
      item.run();
      if (opts.after) opts.after();
    }

    function paint() {
      const S = ctx.S;
      const query = s.input.value.trim().toLowerCase();
      const words = query.split(/\s+/).filter(Boolean);
      const matches = (text) => words.every((word) => text.toLowerCase().includes(word));
      const shapeOf = (level) => (PROBLEM.has(level) ? `<svg class="shape lv-${level}" role="img" aria-label="${level}"><use href="#s-${level}"/></svg>` : '');
      const open = (path) => { if (S.view !== 'tree') ctx.go('tree'); ctx.select(path); };
      const groups = [];
      const add = (name, items) => { if (items.length) groups.push({ name, items }); };
      const parentOf = (path) => { const crumbs = ctx.crumbs(path); return crumbs.slice(0, -1).map((crumb) => crumb.name).join(' › '); };
      // what needs you comes first, then every device the words match
      const attention = ctx.attention().map((item) => ({ icon: `i-${item.icon || 'device'}`, label: item.name, sub: item.device && item.device.path !== item.path ? item.device.name : item.message, level: item.level, run: () => open(item.path) }));
      if (!query) add('Needs you', attention.slice(0, 6));
      else add('Needs you', attention.filter((item) => matches(`${item.label} ${item.sub}`)).slice(0, 4));
      if (query && S.byPath) {
        const found = [];
        for (const node of S.byPath.values()) {
          const label = ctx.api.entityLabel(node.path);
          if (matches(`${label} ${node.name} ${node.path} ${node.kind}`)) found.push({ icon: `i-${node.icon || 'device'}`, label, sub: parentOf(node.path), level: node.level, run: () => open(node.path) });
          if (found.length >= 40) break;
        }
        add('Devices', found);
      }
      const go = opts.go ? opts.go() : [];
      // the Dashboard (chart view c4's own button in the views bar), when that view is mounted
      const dashboard = document.querySelector('#views [data-ch4]');
      if (dashboard) go.push({ icon: 'lx-table', label: 'Dashboard', sub: 'Your pinned charts', run: () => dashboard.click() });
      add('Go to', go.filter((item) => !query || matches(item.label)));
      const actions = [
        { icon: 'lx-gear', label: 'View options', sub: 'Group by, sort, tree style, skin, mode', run: () => setTimeout(() => ctx.openViewOptions(opts.anchor ? opts.anchor() : null), 0) },
        { icon: 'lx-collapse', label: 'Collapse all', sub: 'The device tree', run: () => ctx.tree.collapseAll() },
        { icon: 'lx-expand', label: 'Expand all', sub: 'The device tree', run: () => ctx.tree.expandAll() },
        ...['auto', 'light', 'dark'].map((mode) => ({ icon: 'lx-gear', label: `Appearance: ${mode}`, sub: 'Light or dark', run: () => document.querySelector(`#mode-switch [data-mode="${mode}"]`).click() })),
      ];
      if (opts.actions) actions.push(...opts.actions());
      // Rerun: a device's live streams in the recorder's viewer (host rerun_links.py reads its discovery doc; RobotDx stays passive).
      // WHY match "open", "rerun", "view", "stream" and the device's words: Zach, 2026-11-13, "if I search for rerun or open etc
      // than I want to see thoose commands come up". The selected device's own comes first, then the cell, then every device's.
      const rerunUrl = (node) => (node && node.rerun && /^https?:\/\//i.test(node.rerun.url || '') ? node.rerun.url : null);
      // two of the same camera share a name: the port tells them apart (".../port-6-1/usb-6-1" → "port 6-1")
      const portOf = (path) => { const port = path.split('/').filter((part) => part.startsWith('port-')).pop(); return port ? port.replace('port-', 'port ') : ''; };
      const openTab = (url) => () => window.open(url, '_blank', 'noopener');
      const rerun = [];
      const selected = S.sel && S.byPath ? S.byPath.get(S.sel) : null;
      if (selected && selected.path !== 'host' && rerunUrl(selected)) {
        rerun.push({ icon: 'i-video', label: `Open ${ctx.api.entityLabel(selected.path)} in Rerun`, sub: `${selected.rerun.live ? 'Live' : 'Not streaming now'} · the selected device · new tab`,
          words: 'open rerun view viewer stream live data', run: openTab(rerunUrl(selected)) });
      }
      const cell = S.byPath ? S.byPath.get('host') : null;
      if (rerunUrl(cell)) rerun.push({ icon: 'i-video', label: 'Open the cell in Rerun', sub: 'Every device and joint in one view · new tab', words: 'open rerun view viewer stream cell robot whole all', run: openTab(rerunUrl(cell)) });
      if (query && S.byPath) {
        const seen = new Set(selected ? [selected.path] : []);   // byPath can hold a node under more than one key
        for (const node of S.byPath.values()) {
          if (seen.has(node.path)) continue;
          seen.add(node.path);
          if (node.path === 'host' || node === selected || node.kind === 'rig' || !rerunUrl(node)) continue;
          const name = ctx.api.entityLabel(node.path);
          rerun.push({ icon: 'i-video', label: `Open ${name} in Rerun`, sub: [node.rerun.live ? 'Live' : 'Not streaming now', portOf(node.path), parentOf(node.path)].filter(Boolean).join(' · '),
            words: `open rerun view viewer stream ${name} ${node.name} ${node.rerun.label || ''}`, run: openTab(rerunUrl(node)) });
        }
      }
      add('Rerun', rerun.filter((item) => (query ? matches(`${item.label} ${item.words}`) : true)).slice(0, query ? 20 : 2));
      const hidden = ctx.tree.hiddenToggle();
      if (hidden) actions.splice(3, 0, { icon: 'lx-eye', label: `${hidden.on ? 'Hide' : 'Show'} ${hidden.label.toLowerCase()}`, sub: 'The tree', run: () => ctx.tree.setHidden(!hidden.on) });
      actions.splice(3, 0, { icon: 'lx-plug', label: ctx.tree.connected() ? 'Show everything' : 'Connected only', sub: 'The tree: just what answers now',
        run: () => ctx.tree.setConnected(!ctx.tree.connected()) });
      // step into the selected device (VS Code's Enter Folder) and back out; narrow the tree to what is inside it
      if (S.sel && ctx.api.stepInto && ctx.api.canEnter(S.sel)) {
        const name = ctx.api.entityLabel(S.sel);
        actions.splice(3, 0,
          { icon: 'lx-expand', label: `Step into ${name}`, sub: 'Make it the top of the tree', run: () => ctx.api.stepInto(S.sel) },
          { icon: 'lx-collapse', label: `Show only what's inside ${name}`, sub: 'A filter chip above the tree', run: () => ctx.api.addInside(S.sel) });
      }
      if (S.into && ctx.api.stepUp) actions.splice(3, 0, { icon: 'lx-collapse', label: 'Step back out', sub: 'Ctrl+Shift+Backspace', run: () => ctx.api.stepUp() });
      // the selected device's utilities: a dry run, as the Utilities menu does (nothing runs until its sheet is confirmed)
      const utilities = S.node && S.sel && S.node.node && S.node.node.path === S.sel ? (S.node.actions || []).map((action) => ({ icon: 'lx-more', label: action.title || action.name, sub: `Utility · ${ctx.api.entityLabel(S.sel)} · dry run first`, run: () => ctx.api.startDryRun(action.name) })) : [];
      add('Utilities', utilities.filter((item) => query && matches(item.label)));
      add('Actions', actions.filter((item) => !query || matches(`${item.label} ${item.sub}`)));
      s.items = groups.flatMap((group) => group.items);
      s.hit = Math.min(s.hit, Math.max(0, s.items.length - 1));
      let at = 0;
      s.list.innerHTML = groups.map((group) => `<div class="lx-pal-g">${ctx.esc(group.name)}</div>${group.items.map((item) => {
        const html = `<div class="lx-pal-i" role="option" data-i="${at}">${ctx.ico(item.icon)}<span class="lab">${ctx.esc(item.label)}</span><span class="sub">${ctx.esc(item.sub || '')}</span>${shapeOf(item.level)}</div>`;
        at += 1;
        return html;
      }).join('')}`).join('') || '<div class="lx-pal-empty">Nothing matches.</div>';
      markHit();
    }

    back.addEventListener('mousedown', (event) => { if (event.target === back) api.close(); });
    s.input.addEventListener('input', () => { s.hit = 0; paint(); });
    s.list.addEventListener('click', (event) => { const row = event.target.closest('[data-i]'); if (row) choose(Number(row.dataset.i)); });
    s.list.addEventListener('mousemove', (event) => { const row = event.target.closest('[data-i]'); if (row && Number(row.dataset.i) !== s.hit) { s.hit = Number(row.dataset.i); markHit(); } });
    ctx.on(document, 'keydown', (event) => {
      if (event.target.closest && event.target.closest('.xterm')) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && PALETTE_KEYS.has(event.key.toLowerCase())) { event.preventDefault(); api.toggle(); return; }
      if (back.hidden) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); api.close(); }
      else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const count = s.items.length;
        if (count) { s.hit = (s.hit + (event.key === 'ArrowDown' ? 1 : count - 1)) % count; markHit(); }
      } else if (event.key === 'Enter') { event.preventDefault(); choose(s.hit); }
    }, true);
    return api;
  }

  window.IOLayoutPalette = { create };
}());
