// L4 · Notion. One calm page. A breadcrumb of the hierarchy (bam-GPU › USB bus 1 › fdcanusb 272813EF › CAN 1) on top of
// it; the tree as a collapsible sidebar with the search on top; a Ctrl-K / ⌘K COMMAND PALETTE for jumping and for actions;
// and a first screen that is one calm answer, "Everything is OK" or what needs you. Quiet by default: nothing is drawn
// until it is asked for (Settings/Memory/feedback_calm_ui_progressive_disclosure.md).
'use strict';
(function () {
  const CSS = `
.lx-l4 { background: var(--io-surface); }
.l4-side { flex: none; width: var(--l4-side-w, 260px); background: var(--io-surface-2); }
.lx-fold-l4side .l4-side { display: none; }
.l4-ws { flex: none; display: flex; align-items: center; gap: 6px; height: 44px; padding: 0 8px 0 14px; }
.l4-ws .name { flex: 1; min-width: 0; font-weight: 600; color: var(--io-text-strong); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: flex; align-items: center; gap: 8px; }
.l4-ws .name .ic { color: var(--io-icon); }
.l4-nav { flex: none; padding: 0 6px 4px; }
.l4-item { display: flex; align-items: center; gap: 8px; width: 100%; height: 28px; padding: 0 8px; border: 0; border-radius: var(--io-radius-sm); background: transparent; color: var(--io-muted); cursor: pointer; text-align: left; font-size: 13px; }
.l4-item:hover { background: var(--io-hover); color: var(--io-text); }
.l4-item.on { background: var(--io-selected); color: var(--io-text-strong); }
.l4-item .ic { width: 16px; height: 16px; color: var(--io-icon); }
.l4-item .count { margin-left: auto; color: var(--io-faint); font-size: 11.5px; display: inline-flex; align-items: center; gap: 4px; }
.l4-item .count .shape { width: 12px; height: 12px; }
.l4-item kbd { margin-left: auto; font: 11px var(--io-font-ui); color: var(--io-faint); }
.l4-lab { flex: none; padding: 8px 14px 2px; font-size: 11px; color: var(--io-faint); font-weight: 600; letter-spacing: .3px; }
.l4-side .lx-tree-scroll .t6-head { display: none; }
.l4-page { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--io-surface); position: relative; }
.l4-top { flex: none; display: flex; align-items: center; gap: 4px; height: 44px; padding: 0 10px 0 10px; min-width: 0; }
.l4-crumbs { flex: 1; min-width: 0; display: flex; align-items: center; gap: 1px; overflow: hidden; white-space: nowrap; font-size: 13px; color: var(--io-muted); }
.l4-crumbs a { color: var(--io-muted); padding: 2px 6px; border-radius: var(--io-radius-sm); cursor: pointer; overflow: hidden; text-overflow: ellipsis; }
.l4-crumbs a:hover { background: var(--io-hover); color: var(--io-text); }
.l4-crumbs a.last { color: var(--io-text-strong); cursor: default; }
.l4-crumbs a.last:hover { background: transparent; }
.l4-crumbs .sep { color: var(--io-faint); }
.l4-top #mode { flex: none; font-size: 11.5px; color: var(--io-faint); margin-right: 6px; }
.l4-jump { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 8px 0 10px; border: 1px solid var(--io-border); border-radius: var(--io-radius); background: var(--io-field); color: var(--io-muted); cursor: pointer; font-size: 12.5px; }
.l4-jump:hover { color: var(--io-text); background: var(--io-hover); }
.l4-jump kbd { font: 11px var(--io-font-ui); color: var(--io-faint); }
.l4-jump .ic { width: 14px; height: 14px; }
.l4-body { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; position: relative; }
.l4-body #main { flex: 1; }
.l4-home { position: absolute; inset: 0; overflow: auto; background: var(--io-surface); }
.l4-home-in { max-width: 680px; margin: 0 auto; padding: 12vh 24px 40px; }
.l4-home h1 { margin: 0; font: 600 34px/1.2 var(--io-font-display); color: var(--io-text-strong); letter-spacing: -.3px; }
.l4-home .lede { margin: 8px 0 0; color: var(--io-muted); font-size: 14.5px; }
.l4-need { margin-top: 26px; border-top: 1px solid var(--io-border-soft); }
.l4-need button { display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 4px 10px; width: 100%; text-align: left; padding: 10px 6px; border: 0; border-bottom: 1px solid var(--io-border-soft); background: transparent; cursor: pointer; border-radius: 0; }
.l4-need button:hover { background: var(--io-hover); }
.l4-need .shape { width: 16px; height: 16px; margin-top: 2px; }
.l4-need .who { color: var(--io-text-strong); font-weight: 600; }
.l4-need .who span { color: var(--io-muted); font-weight: 400; }
.l4-need .why { grid-column: 2; color: var(--io-muted); font-size: 12.5px; }
.l4-foot { margin-top: 22px; color: var(--io-faint); font-size: 12.5px; }
.l4-body #runs-root.r1 { flex: none; }
`;

  const layout = {
    name: 'Notion',
    description: 'One calm page: a breadcrumb of the hierarchy, the tree as a collapsible sidebar with search on top, a Ctrl-K palette for jumping and actions, and a first screen that is one calm answer.',
    order: 4,
    keepTree: true,
    reach: {
      tree: { control: '.l4-top [data-l4-view="tree"]', shows: '#tree-host .t6-row' },
      table: { control: '.l4-top [data-l4-view="table"]', shows: 'table.grid' },
      activity: { control: '.l4-top [data-l4-view="activity"]', shows: '.act .ev' },
      runs: { control: '.l4-top [data-l4-runs]', shows: '#runs-root.r1' },
    },
    mount(ctx) {
      ctx.css(CSS);
      const root = ctx.root;
      const params = new URLSearchParams(location.search);
      const s = { home: !(params.get('sel') || params.get('group') || params.get('view')), lastSel: undefined, lastKey: undefined, html: {}, palette: null };
      this.s = s;

      // ---- the sidebar: workspace row, search, Home, the tree ----
      const ws = document.createElement('div');
      ws.className = 'l4-ws';
      ws.innerHTML = `<span class="name">${ctx.ico('i-host')}<span class="ws-name">RobotDx</span></span>`;
      ws.append(ctx.take('vo'), ctx.iconButton({ icon: 'sidebar', title: 'Close the sidebar (Ctrl+\\)', click: () => s.sideSash.fold(true) }));
      root.appendChild(ctx.take('voPop'));
      s.wsName = ws.querySelector('.ws-name');
      const nav = document.createElement('div');
      nav.className = 'l4-nav';
      s.homeItem = ctx.el(`<button class="l4-item" data-l4-home>${ctx.ico('home')}<span>Home</span><span class="count"></span></button>`);
      s.homeItem.addEventListener('click', () => { s.home = true; ctx.go('tree'); this.update(ctx); });
      const jump = ctx.el(`<button class="l4-item" data-l4-palette>${ctx.ico('cmd')}<span>Jump to or run…</span><kbd>Ctrl K</kbd></button>`);
      jump.addEventListener('click', () => s.palette.open());
      nav.append(s.homeItem, jump);
      const lab = document.createElement('div');
      lab.className = 'l4-lab';
      lab.textContent = 'Devices';
      s.side = ctx.treePane({ cls: 'l4-side', head: ws });
      // header, then the search (the tree pane puts it right after the header), then Home + the label, then the tree
      s.side.insertBefore(nav, s.side.querySelector('.lx-tree-scroll'));
      s.side.insertBefore(lab, s.side.querySelector('.lx-tree-scroll'));
      root.appendChild(s.side);
      s.side.addEventListener('click', (event) => { if (event.target.closest('[role=treeitem]') && s.home) { s.home = false; this.update(ctx); } });
      s.sideSash = ctx.sash({ key: 'l4side', axis: 'x', anchor: 'start', def: 260, min: 200, max: (total) => total - 480, varName: '--l4-side-w', root, label: 'Resize the sidebar' });
      root.appendChild(s.sideSash.el);

      // ---- the page: top bar (breadcrumb, quiet actions), then Home or the page ----
      const page = document.createElement('section');
      page.className = 'l4-page';
      const top = document.createElement('div');
      top.className = 'l4-top';
      s.open = ctx.iconButton({ icon: 'sidebar', title: 'Open the sidebar (Ctrl+\\)', click: () => s.sideSash.fold(false) });
      s.crumbs = document.createElement('div');
      s.crumbs.className = 'l4-crumbs';
      const mkView = (view, icon, title) => {
        const button = ctx.iconButton({ icon, title, pressed: false });
        button.dataset.l4View = view;
        button.addEventListener('click', () => { s.home = false; ctx.go(view === 'activity' && ctx.S.view === 'activity' ? 'tree' : view === 'table' && ctx.S.view === 'table' ? 'tree' : view); this.update(ctx); });
        return button;
      };
      s.views = [mkView('tree', 'devices', 'The device page'), mkView('table', 'table', 'Table of every device'), mkView('activity', 'log', 'Activity log')];
      s.terminal = ctx.iconButton({ icon: 'i-tty', title: 'Terminal (runs)', pressed: false, click: () => ctx.runs.toggle() });
      s.terminal.dataset.l4Runs = '1';
      const jumpBtn = ctx.el(`<button class="l4-jump" data-l4-palette title="Jump to a device or run an action">${ctx.ico('i-search')}<span>Jump to</span><kbd>Ctrl K</kbd></button>`);
      jumpBtn.addEventListener('click', () => s.palette.open());
      top.append(s.open, s.crumbs, ctx.take('mode'), ctx.take('inspToggle'), ...s.views, s.terminal, jumpBtn);
      s.body = document.createElement('div');
      s.body.className = 'l4-body';
      s.homeEl = document.createElement('div');
      s.homeEl.className = 'l4-home';
      s.homeEl.addEventListener('click', (event) => { if (event.target.closest('[data-go]')) { s.home = false; this.update(ctx); } });
      s.body.append(ctx.take('sources'), ctx.take('main'), s.homeEl, ctx.take('runsRoot'));
      page.append(top, s.body);
      root.appendChild(page);

      // ---- the palette: the shared Ctrl-K palette (layouts/palette.js), with this layout's own Home / Table / Activity / Terminal ----
      s.palette = window.IOLayoutPalette.create(ctx, {
        anchor: () => s.crumbs,
        after: () => { s.home = false; this.update(ctx); },
        go: () => [
          { icon: 'lx-home', label: 'Home', sub: 'The calm answer', run: () => { s.home = true; ctx.go('tree'); } },
          { icon: 'lx-table', label: 'Table', sub: 'Every device in one table', run: () => ctx.go('table') },
          { icon: 'lx-log', label: 'Activity', sub: 'The log, newest first', run: () => ctx.go('activity') },
          { icon: 'i-tty', label: 'Terminal', sub: 'Runs of utilities', run: () => ctx.runs.toggle() },
        ],
        actions: () => [{ icon: 'lx-sidebar', label: 'Toggle the sidebar', sub: 'Ctrl+\\', run: () => s.sideSash.toggle() }],
      });
      ctx.on(document, 'keydown', (event) => {
        if (event.target.closest && event.target.closest('.xterm')) return;
        if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key === '\\') { event.preventDefault(); s.sideSash.toggle(); }
      }, true);
    },

    unmount(ctx) { ctx.$('main').style.display = ''; this.s = null; },

    // -------------------------------------------------------------------------------------- the page ----
    update(ctx) {
      const s = this.s;
      if (!s) return;
      const S = ctx.S;
      // a new selection (a tree click, a link, the palette) leaves Home; the page's own first pick does not
      const key = S.selKey || S.sel;
      if (s.lastKey !== undefined && key !== s.lastKey && s.home) s.home = false;
      s.lastKey = key;
      if (S.view !== 'tree' && s.home) s.home = false;
      const summary = (S.data && S.data.summary) || { counts: {}, attention: [], entities: 0 };
      const need = summary.attention || [];
      const home = s.home;
      ctx.$('main').style.display = home ? 'none' : '';
      s.homeEl.hidden = !home;
      s.homeEl.style.display = home ? '' : 'none';
      s.homeItem.classList.toggle('on', home);
      const shape = (level) => `<svg class="shape lv-${level}" role="img" aria-label="${level}"><use href="#s-${level}"/></svg>`;
      // ---- Home: one calm answer ----
      const calm = !need.length;
      const homeHtml = `<div class="l4-home-in"><h1>${calm ? 'Everything is OK' : `${need.length} thing${need.length === 1 ? '' : 's'} need${need.length === 1 ? 's' : ''} you`}</h1>`
        + `<p class="lede">${calm ? 'Every device answers as expected.' : 'The rest answers as expected.'} ${summary.entities || 0} entities on ${ctx.esc((S.data && S.data.host && S.data.host.name) || 'this computer')}.</p>`
        + (calm ? '' : `<div class="l4-need">${need.map((item) => `<button data-go="${ctx.esc(item.path)}">${shape(item.level)}<span class="who">${ctx.esc(ctx.usb(item.device && item.device.path !== item.path ? item.device.name : item.name))}${item.device && item.device.path !== item.path ? ` <span>› ${ctx.esc(ctx.usb(item.name))}</span>` : ''}</span><span class="why">${ctx.esc(ctx.usb(item.message))}</span></button>`).join('')}</div>`)
        + '<p class="l4-foot">Open a device in the sidebar, or press Ctrl K to jump.</p></div>';
      if (s.html.home !== homeHtml) { s.html.home = homeHtml; s.homeEl.innerHTML = homeHtml; }
      const count = ['error', 'stale', 'unknown', 'warn'].filter((level) => (summary.counts || {})[level]).map((level) => `${shape(level)}${summary.counts[level]}`).join('');
      if (s.html.count !== count) { s.html.count = count; s.homeItem.querySelector('.count').innerHTML = count; }
      // ---- the breadcrumb ----
      const crumbs = ctx.crumbs(S.sel);
      const html = home ? '<a class="last">Home</a>' : S.view === 'table' ? '<a class="last">Table</a>' : S.view === 'activity' ? '<a class="last">Activity</a>'
        : crumbs.map((crumb, at) => `<a${at === crumbs.length - 1 ? ' class="last"' : ` data-go="${ctx.esc(crumb.path)}"`} title="${ctx.esc(crumb.raw)}">${ctx.esc(crumb.name)}</a>`).join('<span class="sep">›</span>') || '<a class="last">Devices</a>';
      if (s.html.crumbs !== html) { s.html.crumbs = html; s.crumbs.innerHTML = html; }
      s.open.style.display = s.sideSash.folded() ? '' : 'none';
      for (const button of s.views) {
        const on = !home && button.dataset.l4View === S.view;
        button.classList.toggle('on', on);
        button.setAttribute('aria-pressed', String(on));
      }
      s.terminal.classList.toggle('on', ctx.runs.open());
      s.terminal.setAttribute('aria-pressed', String(ctx.runs.open()));
      ctx.$('search').closest('.search').classList.toggle('has-query', Boolean(S.query));
      if (S.data && S.data.host) s.wsName.textContent = S.data.host.name;
    },
  };
  window.IOLayouts = window.IOLayouts || {};
  window.IOLayouts.l4 = layout;
}());
