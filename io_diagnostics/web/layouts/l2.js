// L2 · VS Code. An activity bar and a sidebar (the tree, its search on top), an editor area where entity pages open as TABS,
// a bottom PANEL holding Activity and the Runs terminal (toggle Ctrl+J), and the status bar. Keys as VS Code: Ctrl+B the
// sidebar, Ctrl+J the panel, Ctrl+Shift+F the search.
// WHY tabs, not splits: the page draws one detail from one loaded node (S.node), so a second board beside the first
// would need a second instance of it; a tab switch re-selects through the page's own history instead (docs: the report).
'use strict';
(function () {
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const CSS = `
.lx-l2 { flex-direction: column; }
.l2-top { flex: 1; min-height: 0; display: flex; }
.l2-act { flex: none; width: 48px; display: flex; flex-direction: column; align-items: center; padding: 4px 0; background: var(--io-surface-2); border-right: 1px solid var(--io-border); }
.l2-act .lx-ib { width: 48px; height: 46px; border-radius: 0; }
.l2-act .lx-ib .ic { width: 22px; height: 22px; }
.l2-act .lx-ib.on { background: transparent; color: var(--io-text-strong); box-shadow: inset 2px 0 0 var(--io-selected-line); }
.l2-act .gap { flex: 1; }
.l2-act .badge { position: absolute; top: 6px; right: 8px; min-width: 14px; height: 14px; padding: 0 3px; border-radius: 7px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10px; font-weight: 600; line-height: 14px; text-align: center; }
.l2-side { flex: none; width: var(--l2-side-w, 280px); background: var(--io-surface); border-right: 1px solid var(--io-border); }
.lx-fold-l2side .l2-side { display: none; }
.l2-title { flex: none; display: flex; align-items: center; gap: 2px; height: 35px; padding: 0 6px 0 16px; color: var(--io-muted); font-size: 11px; letter-spacing: .6px; text-transform: uppercase; }
.l2-title .grow { flex: 1; }
.l2-title .lx-ib { width: 24px; height: 24px; }
.l2-title .lx-ib .ic { width: 15px; height: 15px; }
.l2-editor { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--io-surface-alt); }
.l2-tabs { flex: none; display: flex; align-items: stretch; height: 35px; background: var(--io-surface-2); border-bottom: 1px solid var(--io-border); }
.l2-tabstrip { flex: 1; min-width: 0; display: flex; overflow-x: auto; scrollbar-width: none; }
.l2-tab { flex: none; display: flex; align-items: center; gap: 6px; max-width: 240px; padding: 0 6px 0 12px; border-right: 1px solid var(--io-border-soft); color: var(--io-muted); cursor: pointer; font-size: 12.5px; position: relative; white-space: nowrap; background: transparent; border-top: 0; border-bottom: 0; border-left: 0; }
.l2-tab:hover { background: var(--io-hover); color: var(--io-text); }
.l2-tab.on { background: var(--io-surface-alt); color: var(--io-text-strong); box-shadow: inset 0 1px 0 var(--io-selected-line); margin-bottom: -1px; }
.l2-tab.preview .name { font-style: italic; }
.l2-tab .ic { width: 15px; height: 15px; color: var(--io-icon); }
.l2-tab .name { overflow: hidden; text-overflow: ellipsis; }
.l2-tab .shape { width: 12px; height: 12px; }
.l2-tab .x { width: 18px; height: 18px; display: inline-flex; align-items: center; justify-content: center; border-radius: var(--io-radius-sm); color: var(--io-faint); border: 0; background: transparent; padding: 0; cursor: pointer; opacity: 0; }
.l2-tab:hover .x, .l2-tab.on .x { opacity: 1; }
.l2-tab .x:hover { background: var(--io-control-hover); color: var(--io-text); }
.l2-tab .x .ic { width: 12px; height: 12px; }
.l2-tabend { flex: none; display: flex; align-items: center; gap: 2px; padding: 0 8px 0 4px; }
.l2-tabend #mode { font-size: 11.5px; color: var(--io-faint); margin-right: 6px; }
.l2-crumbs { flex: none; display: flex; align-items: center; gap: 2px; height: 24px; padding: 0 12px; background: var(--io-surface-alt); font-size: 12px; color: var(--io-muted); white-space: nowrap; overflow: hidden; }
.l2-crumbs a { color: var(--io-muted); cursor: pointer; padding: 1px 3px; border-radius: var(--io-radius-sm); overflow: hidden; text-overflow: ellipsis; }
.l2-crumbs a:hover { background: var(--io-hover); color: var(--io-text); }
.l2-crumbs a.last { color: var(--io-text); cursor: default; }
.l2-crumbs a.last:hover { background: transparent; }
.l2-crumbs .sep { color: var(--io-faint); }
.l2-panel { flex: none; height: var(--l2-panel-h, 220px); display: flex; flex-direction: column; background: var(--io-surface); border-top: 1px solid var(--io-border); min-height: 0; position: relative; }
.lx-fold-l2panel .l2-panel { display: none; }
.l2-ptabs { flex: none; display: flex; align-items: stretch; height: 33px; padding: 0 8px 0 12px; gap: 14px; border-bottom: 1px solid var(--io-border-soft); }
.l2-ptab { display: inline-flex; align-items: center; gap: 6px; padding: 0; border: 0; border-bottom: 1px solid transparent; margin-bottom: -1px; background: transparent; color: var(--io-muted); font-size: 11px; letter-spacing: .5px; text-transform: uppercase; cursor: pointer; }
.l2-ptab:hover { color: var(--io-text); }
.l2-ptab.on { color: var(--io-text-strong); border-bottom-color: var(--io-text-strong); }
.l2-ptab .badge { min-width: 15px; height: 15px; padding: 0 4px; border-radius: 8px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10.5px; font-weight: 600; line-height: 15px; text-align: center; text-transform: none; letter-spacing: 0; }
.l2-ptabs .grow { flex: 1; }
.l2-ptabs .lx-ib { align-self: center; }
.l2-pbody { flex: 1; min-height: 0; position: relative; }
.l2-pact { position: absolute; inset: 0; overflow: auto; }
.l2-pact .act > section { padding: 6px 16px 14px; }
.l2-pterm { position: absolute; inset: 0; display: flex; flex-direction: column; }
.l2-panel #runs-root.r1 { flex: 1; height: auto; border-top: 0; }
.l2-panel .r1-sash { display: none; }
.l2-sb { display: inline-flex; align-items: center; gap: 12px; margin-right: 4px; }
.l2-sb button { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: var(--io-text); padding: 2px 6px; border-radius: var(--io-radius-sm); cursor: pointer; font-size: 12px; }
.l2-sb button:hover { background: var(--io-hover); }
.l2-sb .shape { width: 12px; height: 12px; }
.l2-sb .quiet { color: var(--io-muted); }
.lx-l2 .t6-head { display: none; }
`;
  const ACT = [
    { id: 'explorer', icon: 'devices', title: 'Devices (Ctrl+B)' },
    { id: 'search', icon: 'i-search', title: 'Search (Ctrl+Shift+F)' },
    { id: 'table', icon: 'table', title: 'Table' },
    { id: 'activity', icon: 'log', title: 'Activity (Ctrl+J)' },
    { id: 'terminal', icon: 'i-tty', title: 'Runs: the terminal' },
  ];

  const layout = {
    name: 'VS Code',
    description: 'An activity bar and a sidebar (the tree, search on top); entity pages open as tabs; a bottom panel holds Activity and the Runs terminal (Ctrl+J); the status bar.',
    order: 2,
    keepTree: true,
    reach: {
      tree: { control: '.l2-act [data-l2="explorer"]', shows: '#tree-host .t6-row' },
      table: { control: '.l2-act [data-l2="table"]', shows: 'table.grid' },
      activity: { control: '.l2-act [data-l2="activity"]', shows: '.l2-pact .act .ev' },
      runs: { control: '.l2-act [data-l2="terminal"]', shows: '#runs-root.r1' },
    },
    mount(ctx) {
      ctx.css(CSS);
      const root = ctx.root;
      const s = { act: new Map(), tabs: [], preview: null, tab: ctx.pref('l2.ptab', 'activity'), lastRuns: ctx.runs.open(), lastSel: null, html: {} };
      try { s.tabs = JSON.parse(ctx.pref('l2.tabs', '[]')).filter((path) => typeof path === 'string'); } catch (_) { s.tabs = []; }
      this.s = s;
      const top = document.createElement('div');
      top.className = 'l2-top';

      // ---- activity bar ----
      const act = document.createElement('nav');
      act.className = 'l2-act';
      act.setAttribute('aria-label', 'Activity bar');
      for (const item of ACT) {
        const button = ctx.iconButton({ icon: item.icon, title: item.title, pressed: false });
        button.dataset.l2 = item.id;
        if (item.id === 'terminal') button.insertAdjacentHTML('beforeend', '<span class="badge" hidden></span>');
        button.addEventListener('click', () => this.activate(ctx, item.id));
        act.appendChild(button);
        s.act.set(item.id, button);
      }
      act.insertAdjacentHTML('beforeend', '<span class="gap"></span>');
      const gear = ctx.iconButton({ icon: 'gear', title: 'Manage: View options (group, sort, skin, mode)' });
      gear.dataset.voAnchor = '1';
      gear.dataset.voPlace = 'right';
      act.appendChild(gear);
      top.appendChild(act);

      // ---- sidebar: title row with its actions, search on top of the tree ----
      const title = document.createElement('div');
      title.className = 'l2-title';
      title.innerHTML = '<span>Devices</span><span class="grow"></span>';
      title.append(ctx.iconButton({ icon: 'collapse', title: 'Collapse all', click: () => ctx.tree.collapseAll() }),
        ctx.iconButton({ icon: 'expand', title: 'Expand all', click: () => ctx.tree.expandAll() }), ctx.take('vo'));
      s.visible = ctx.iconButton({ icon: 'eye', title: 'Show what is hidden', pressed: false, click: () => ctx.tree.setHidden(!ctx.tree.hiddenToggle().on) });
      s.connected = ctx.iconButton({ icon: 'plug', title: ctx.tree.connectedTitle(), pressed: ctx.tree.connected(), id: 'connected-only',
        click: () => ctx.tree.setConnected(!ctx.tree.connected()) });
      title.append(s.connected, s.visible);
      root.appendChild(ctx.take('voPop'));
      s.side = ctx.treePane({ cls: 'l2-side', head: title });
      s.side.addEventListener('dblclick', (event) => { if (event.target.closest('[role=treeitem]') && ctx.S.sel) this.pin(ctx.S.sel); });
      top.appendChild(s.side);
      s.sideSash = ctx.sash({ key: 'l2side', axis: 'x', anchor: 'start', def: 280, min: 200, max: (total) => total - 48 - 460, varName: '--l2-side-w', root, within: top, offset: 48, label: 'Resize the sidebar' });
      top.appendChild(s.sideSash.el);

      // ---- editor: tabs, breadcrumbs, the page's main area ----
      const editor = document.createElement('section');
      editor.className = 'l2-editor';
      const tabs = document.createElement('div');
      tabs.className = 'l2-tabs';
      s.strip = document.createElement('div');
      s.strip.className = 'l2-tabstrip';
      s.strip.setAttribute('role', 'tablist');
      const end = document.createElement('div');
      end.className = 'l2-tabend';
      s.panelButton = ctx.iconButton({ icon: 'bottombar', title: 'Toggle the panel (Ctrl+J)', pressed: true, click: () => this.togglePanel(ctx) });
      end.append(ctx.take('mode'), ctx.take('inspToggle'), ctx.iconButton({ icon: 'sidebar', title: 'Toggle the sidebar (Ctrl+B)', click: () => s.sideSash.toggle() }), s.panelButton);
      tabs.append(s.strip, end);
      s.crumbs = document.createElement('div');
      s.crumbs.className = 'l2-crumbs';
      editor.append(tabs, s.crumbs, ctx.take('sources'), ctx.take('main'));
      top.appendChild(editor);
      root.appendChild(top);

      // ---- the bottom panel: Activity | Terminal ----
      const panel = document.createElement('section');
      panel.className = 'l2-panel';
      panel.setAttribute('aria-label', 'Panel');
      panel.innerHTML = '<div class="l2-ptabs" role="tablist"><button class="l2-ptab" data-ptab="activity" role="tab">Activity</button>'
        + '<button class="l2-ptab" data-ptab="terminal" role="tab">Terminal<span class="badge" hidden></span></button><span class="grow"></span></div>'
        + '<div class="l2-pbody"><div class="l2-pact"></div><div class="l2-pterm"></div></div>';
      s.pact = panel.querySelector('.l2-pact');
      panel.querySelector('.l2-pterm').appendChild(ctx.take('runsRoot'));
      panel.querySelector('.l2-ptabs').appendChild(ctx.iconButton({ icon: 'x', title: 'Close the panel', click: () => s.panelSash.fold(true) }));
      panel.querySelectorAll('[data-ptab]').forEach((button) => button.addEventListener('click', () => this.setPanelTab(ctx, button.dataset.ptab)));
      s.panel = panel;
      s.panelSash = ctx.sash({ key: 'l2panel', axis: 'y', anchor: 'end', def: 220, min: 100, max: (total) => total - 260, varName: '--l2-panel-h', root, label: 'Resize the panel' });
      root.append(s.panelSash.el, panel);
      s.panelSash.apply();

      // ---- status bar: what needs you, VS Code's Problems counters ----
      s.sb = document.createElement('div');
      s.sb.className = 'l2-sb';
      s.sb.addEventListener('click', () => this.setPanelTab(ctx, 'activity', true));
      const bar = document.querySelector('.statusbar');
      ctx.adopt(s.sb, bar, bar.firstChild);

      // ---- keys ----
      ctx.on(document, 'keydown', (event) => {
        if (event.target.closest && event.target.closest('.xterm')) return;
        if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
        if (event.key === 'b' || event.key === 'B') { s.sideSash.toggle(); event.preventDefault(); }
        else if (event.key === 'j' || event.key === 'J') { this.togglePanel(ctx); event.preventDefault(); }
        else if (event.shiftKey && (event.key === 'f' || event.key === 'F')) { this.focusSearch(ctx); event.preventDefault(); }
      });
      ctx.on(s.strip, 'click', (event) => {
        const close = event.target.closest('[data-l2-close]');
        if (close) { this.close(ctx, close.dataset.l2Close); event.stopPropagation(); return; }
        const tab = event.target.closest('[data-l2-tab]');
        if (!tab) return;
        if (tab.dataset.l2Tab === '@table') ctx.go('table'); else { this.openEntity(ctx, tab.dataset.l2Tab); }
      });
      ctx.on(s.strip, 'dblclick', (event) => { const tab = event.target.closest('[data-l2-tab]'); if (tab && tab.dataset.l2Tab !== '@table') this.pin(tab.dataset.l2Tab); });
      ctx.on(s.strip, 'auxclick', (event) => { const tab = event.target.closest('[data-l2-tab]'); if (event.button === 1 && tab && tab.dataset.l2Tab !== '@table') this.close(ctx, tab.dataset.l2Tab); });
      this.setPanelTab(ctx, s.tab, false);
    },

    unmount() { this.s = null; },

    // ------------------------------------------------------------------------------------ behaviour ----
    activate(ctx, id) {
      const s = this.s;
      if (id === 'explorer') { if (ctx.S.view !== 'tree') { ctx.go('tree'); s.sideSash.fold(false); } else s.sideSash.toggle(); }
      else if (id === 'search') this.focusSearch(ctx);
      else if (id === 'table') ctx.go('table');
      else if (id === 'activity') this.setPanelTab(ctx, 'activity', true);
      else if (id === 'terminal') this.setPanelTab(ctx, 'terminal', true);
    },
    focusSearch(ctx) { this.s.sideSash.fold(false); ctx.$('search').focus(); ctx.$('search').select(); },
    togglePanel(ctx) { const s = this.s; s.panelSash.toggle(); if (!s.panelSash.folded() && s.tab === 'terminal') ctx.runs.set(true); },
    setPanelTab(ctx, tab, reveal) {
      const s = this.s;
      s.tab = tab;
      ctx.setPref('l2.ptab', tab);
      if (reveal && s.panelSash.folded()) s.panelSash.fold(false);
      // the terminal is the page's runs panel: shown while its tab is, closed (its own toggle) while Activity is
      ctx.runs.set(tab === 'terminal');
      s.lastRuns = ctx.runs.open();
      this.paintPanel(ctx);
    },
    paintPanel(ctx) {
      const s = this.s;
      s.panel.querySelectorAll('[data-ptab]').forEach((button) => { const on = button.dataset.ptab === s.tab; button.classList.toggle('on', on); button.setAttribute('aria-selected', String(on)); });
      s.pact.style.display = s.tab === 'activity' ? '' : 'none';
      s.panel.querySelector('.l2-pterm').style.display = s.tab === 'terminal' ? '' : 'none';
      s.panelButton.classList.toggle('on', !s.panelSash.folded());
      s.panelButton.setAttribute('aria-pressed', String(!s.panelSash.folded()));
    },
    // opening an entity from a tab, the tree or a link is the page's own selection; the tab strip follows it
    openEntity(ctx, path) { if (ctx.S.view !== 'tree') ctx.go('tree'); if (path !== ctx.S.sel) ctx.select(path); },
    remember(ctx) { ctx.setPref('l2.tabs', JSON.stringify(this.s.tabs)); },
    pin(path) { const s = this.s; if (s.preview === path) { s.preview = null; } },
    close(ctx, path) {
      const s = this.s;
      const at = s.tabs.indexOf(path);
      if (at < 0 || s.tabs.length < 2) return;
      s.tabs.splice(at, 1);
      if (s.preview === path) s.preview = null;
      this.remember(ctx);
      if (path === ctx.S.sel) this.openEntity(ctx, s.tabs[Math.min(at, s.tabs.length - 1)]);
      else this.update(ctx);
    },

    update(ctx) {
      const s = this.s;
      if (!s) return;
      const S = ctx.S;
      // ---- tabs follow the selection: a new entity opens a PREVIEW tab (italic) that the next one replaces, unless pinned ----
      if (S.sel && S.byPath && S.byPath.has(S.sel) && S.sel !== s.lastSel) {
        s.lastSel = S.sel;
        if (!s.tabs.includes(S.sel)) {
          const replace = s.preview ? s.tabs.indexOf(s.preview) : -1;
          if (replace >= 0 && s.preview !== S.sel && S.view === 'tree') s.tabs[replace] = S.sel; else s.tabs.push(S.sel);
          s.preview = S.sel;
        }
        this.remember(ctx);
      }
      s.tabs = s.tabs.filter((path) => !S.byPath || S.byPath.has(path));
      // ---- tab strip ----
      const level = (path) => { const node = S.byPath && S.byPath.get(path); return node && PROBLEM.has(node.level) ? node.level : null; };
      const tabHtml = (key, label, icon, on, extra = '') => `<div class="l2-tab${on ? ' on' : ''}${key === s.preview ? ' preview' : ''}" role="tab" aria-selected="${on}" data-l2-tab="${ctx.esc(key)}" title="${ctx.esc(label)}">`
        + `${icon}<span class="name">${ctx.esc(label)}</span>${extra}</div>`;
      const html = tabHtml('@table', 'Table', ctx.ico('table'), S.view === 'table')
        + s.tabs.map((path) => {
          const lv = level(path);
          const shape = lv ? `<svg class="shape lv-${lv}" role="img" aria-label="${lv}"><use href="#s-${lv}"/></svg>` : '';
          const node = S.byPath && S.byPath.get(path);
          const x = s.tabs.length > 1 ? `<button class="x" data-l2-close="${ctx.esc(path)}" aria-label="Close" title="Close">${ctx.ico('x')}</button>` : '';
          return tabHtml(path, ctx.api.entityLabel(path), ctx.ico(`i-${(node && node.icon) || 'device'}`), S.view === 'tree' && path === S.sel, `${shape}${x}`);
        }).join('');
      if (s.html.tabs !== html) { s.html.tabs = html; s.strip.innerHTML = html; }
      const active = s.strip.querySelector('.l2-tab.on');
      if (active && s.html.active !== active.dataset.l2Tab) { s.html.active = active.dataset.l2Tab; active.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
      // ---- breadcrumbs ----
      const crumbs = ctx.crumbs(S.sel);
      const crumbHtml = S.view !== 'tree' ? '<a class="last">Devices</a>' : crumbs.map((crumb, at) => `<a${at === crumbs.length - 1 ? ' class="last"' : ` data-go="${ctx.esc(crumb.path)}"`} title="${ctx.esc(crumb.raw)}">${ctx.esc(crumb.name)}</a>`).join('<span class="sep">›</span>');
      if (s.html.crumbs !== crumbHtml) { s.html.crumbs = crumbHtml; s.crumbs.innerHTML = crumbHtml; }
      // ---- activity bar state ----
      const lit = { explorer: S.view === 'tree' && !s.sideSash.folded(), search: false, table: S.view === 'table', activity: !s.panelSash.folded() && s.tab === 'activity', terminal: !s.panelSash.folded() && s.tab === 'terminal' };
      for (const [id, button] of s.act) { button.classList.toggle('on', lit[id]); button.setAttribute('aria-pressed', String(lit[id])); }
      // ---- the terminal followed from outside (a run started, the status bar's toggle): the panel shows it ----
      const open = ctx.runs.open();
      if (open !== s.lastRuns) {
        s.lastRuns = open;
        if (open) { s.tab = 'terminal'; ctx.setPref('l2.ptab', 'terminal'); if (s.panelSash.folded()) s.panelSash.fold(false); } else if (s.tab === 'terminal') { s.tab = 'activity'; ctx.setPref('l2.ptab', 'activity'); }
      }
      this.paintPanel(ctx);
      const going = ctx.runs.count();
      for (const badge of [s.act.get('terminal').querySelector('.badge'), s.panel.querySelector('[data-ptab="terminal"] .badge')]) { badge.hidden = !going; badge.textContent = String(going); }
      // ---- the panel's Activity: the page's own log, drawn only when it changed ----
      if (!s.panelSash.folded() && s.tab === 'activity') ctx.activity.draw(s.pact);
      // ---- status bar counts ----
      const counts = ctx.counts();
      const shape = (lv) => `<svg class="shape lv-${lv}" role="img" aria-label="${lv}"><use href="#s-${lv}"/></svg>`;
      const parts = ['error', 'stale', 'unknown', 'warn'].filter((lv) => counts[lv]).map((lv) => `<button title="${counts[lv]} ${lv}: open the Activity panel">${shape(lv)}${counts[lv]}</button>`);
      const sb = parts.join('') || '<button class="quiet" title="Nothing needs you">Nothing needs you</button>';
      if (s.html.sb !== sb) { s.html.sb = sb; s.sb.innerHTML = sb; }
      ctx.$('search').closest('.search').classList.toggle('has-query', Boolean(S.query));
      const hidden = ctx.tree.hiddenToggle();
      s.visible.disabled = !hidden;
      s.visible.classList.toggle('on', Boolean(hidden && hidden.on));
      s.visible.setAttribute('aria-pressed', String(Boolean(hidden && hidden.on)));
      s.visible.title = hidden ? `${hidden.on ? 'Hide' : 'Show'} ${hidden.label.toLowerCase()}` : 'Nothing is hidden in this grouping';
      s.visible.innerHTML = ctx.ico(hidden && hidden.on ? 'eye' : 'eye-off');
      s.connected.classList.toggle('on', ctx.tree.connected());
      s.connected.setAttribute('aria-pressed', String(ctx.tree.connected()));
      s.connected.title = ctx.tree.connectedTitle();
    },
  };
  window.IOLayouts = window.IOLayouts || {};
  window.IOLayouts.l2 = layout;
}());
