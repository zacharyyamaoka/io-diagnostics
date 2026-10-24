// L1 · Obsidian. Zach's own reference (his screenshot of Obsidian's left sidebar, 2026-10-24): match its conventions.
//   - a vertical RIBBON at the far left carries the views (Devices, Table, Activity, Runs) and Settings at its foot;
//   - the left PANE has a header, a small toolbar row (collapse all, expand all, View options, visibility), then the SEARCH
//     FIELD on top of the tree, then the tree;
//   - the main area is the entity page under a slim view header (back / forward, the breadcrumb of the selection).
// Every part is the page's own, moved: the search input keeps its state, the tree its expansion.
// WHY: Zach, 2026-10-24: "I think the search bar should be inside of the entity tree on top just as obsidian does it".
'use strict';
(function () {
  const CSS = `
.l1-ribbon { flex: none; width: 44px; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 8px 0; background: var(--io-surface-2); border-right: 1px solid var(--io-border); }
.l1-ribbon .lx-ib { width: 32px; height: 32px; }
.l1-ribbon .lx-ib .ic { width: 18px; height: 18px; }
.l1-ribbon .lx-ib.on { background: var(--io-pressed); }
.l1-ribbon .gap { flex: 1; }
.l1-ribbon .badge { position: absolute; top: 3px; right: 2px; min-width: 14px; height: 14px; padding: 0 3px; border-radius: 7px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10px; font-weight: 600; line-height: 14px; text-align: center; }
.l1-body { flex: 1; min-width: 0; display: flex; }
.l1-side { flex: none; width: var(--l1-side-w, 280px); border-right: 0; }
.lx-fold-l1side .l1-side { display: none; }
.l1-head { flex: none; display: flex; align-items: stretch; height: 34px; padding: 0 6px; background: var(--io-surface-2); border-bottom: 1px solid var(--io-border-soft); }
.l1-tab { display: inline-flex; align-items: center; gap: 6px; padding: 0 10px; margin-top: 4px; border-radius: var(--io-radius-sm) var(--io-radius-sm) 0 0; color: var(--io-text-strong); background: var(--io-surface); border: 1px solid var(--io-border-soft); border-bottom-color: var(--io-surface); margin-bottom: -1px; font-size: 12px; }
.l1-tab .ic { width: 15px; height: 15px; color: var(--io-icon); }
.l1-tools { flex: none; display: flex; justify-content: center; align-items: center; gap: 2px; padding: 5px 8px 4px; }
.l1-main { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--io-surface-alt); }
.l1-title { flex: none; display: flex; align-items: center; gap: 4px; height: 34px; padding: 0 10px 0 6px; background: var(--io-surface-2); border-bottom: 1px solid var(--io-border-soft); min-width: 0; }
.l1-crumbs { flex: 1; min-width: 0; display: flex; align-items: center; gap: 2px; overflow: hidden; white-space: nowrap; font-size: 12.5px; color: var(--io-muted); padding-left: 6px; }
.l1-crumbs a { color: var(--io-muted); text-decoration: none; cursor: pointer; padding: 2px 4px; border-radius: var(--io-radius-sm); overflow: hidden; text-overflow: ellipsis; }
.l1-crumbs a:hover { background: var(--io-hover); color: var(--io-text); }
.l1-crumbs a.last { color: var(--io-text-strong); font-weight: 600; cursor: default; }
.l1-crumbs a.last:hover { background: transparent; }
.l1-crumbs .sep { color: var(--io-faint); flex: none; }
.l1-title #mode { flex: none; font-size: 11.5px; color: var(--io-faint); }
.l1-main #runs-root.r1 { flex: none; }
`;
  const RIBBON = [
    { id: 'tree', icon: 'devices', title: 'Devices (the tree)' },
    { id: 'table', icon: 'table', title: 'Table' },
    { id: 'activity', icon: 'log', title: 'Activity' },
    { id: 'runs', icon: 'i-tty', title: 'Runs (the terminal panel)' },
  ];

  const layout = {
    name: 'Obsidian',
    description: 'A ribbon of views at the far left; the left pane has a toolbar row, the search field on top of the tree, then the tree; the main area is the entity page.',
    order: 1,
    keepTree: true,
    // the control that reaches each view and what must then be on screen (tests/test_layouts.js)
    reach: {
      tree: { control: '.l1-ribbon [data-l1="tree"]', shows: '#tree-host .t6-row' },
      table: { control: '.l1-ribbon [data-l1="table"]', shows: 'table.grid' },
      activity: { control: '.l1-ribbon [data-l1="activity"]', shows: '.act .ev' },
      runs: { control: '.l1-ribbon [data-l1="runs"]', shows: '#runs-root.r1' },
    },
    mount(ctx) {
      ctx.css(CSS);
      const root = ctx.root;
      const s = { ribbon: new Map() };
      this.s = s;

      // ---- the ribbon ----
      const ribbon = document.createElement('nav');
      ribbon.className = 'l1-ribbon';
      ribbon.setAttribute('aria-label', 'Views');
      const fold = ctx.iconButton({ icon: 'sidebar', title: 'Toggle the left pane', click: () => sash.toggle() });
      ribbon.appendChild(fold);
      for (const item of RIBBON) {
        const button = ctx.iconButton({ icon: item.icon, title: item.title, pressed: false });
        button.dataset.l1 = item.id;
        button.addEventListener('click', () => {
          if (item.id === 'runs') { ctx.runs.toggle(); return; }
          if (sash.folded() && item.id === 'tree') sash.fold(false);
          ctx.go(item.id);
        });
        if (item.id === 'runs') button.insertAdjacentHTML('beforeend', '<span class="badge" hidden></span>');
        ribbon.appendChild(button);
        s.ribbon.set(item.id, button);
      }
      ribbon.insertAdjacentHTML('beforeend', '<span class="gap"></span>');
      const gear = ctx.iconButton({ icon: 'gear', title: 'Settings (View options: group, sort, skin, mode)' });
      gear.dataset.voAnchor = '1';
      gear.dataset.voPlace = 'right';
      ribbon.appendChild(gear);
      root.appendChild(ribbon);

      // ---- the left pane: header, toolbar row, search, tree ----
      const body = document.createElement('div');
      body.className = 'l1-body';
      const head = ctx.el('<div class="l1-head"><span class="l1-tab"><svg class="ic"><use href="#lx-devices"/></svg>Devices</span></div>');
      const tools = document.createElement('div');
      tools.className = 'l1-tools';
      s.collapse = ctx.iconButton({ icon: 'collapse', title: 'Collapse all', click: () => ctx.tree.collapseAll() });
      s.expand = ctx.iconButton({ icon: 'expand', title: 'Expand all', click: () => ctx.tree.expandAll() });
      s.visible = ctx.iconButton({ icon: 'eye', title: 'Show what is hidden', pressed: false, click: () => ctx.tree.setHidden(!ctx.tree.hiddenToggle().on) });
      tools.append(s.collapse, s.expand, ctx.take('vo'), s.visible);
      ctx.take('voPop');
      root.appendChild(ctx.take('voPop'));
      const side = ctx.treePane({ cls: 'l1-side', head, tools: [tools] });
      body.appendChild(side);
      const sash = ctx.sash({ key: 'l1side', axis: 'x', anchor: 'start', def: 280, min: 200, max: (total) => total - 480, varName: '--l1-side-w', root, within: body, label: 'Resize the left pane' });
      body.appendChild(sash.el);
      s.sideSash = sash;

      // ---- the main area: view header, then the page's main area and the runs panel ----
      const main = document.createElement('section');
      main.className = 'l1-main';
      const title = document.createElement('div');
      title.className = 'l1-title';
      s.back = ctx.iconButton({ icon: 'back', title: 'Back', click: () => ctx.history.back() });
      s.forward = ctx.iconButton({ icon: 'forward', title: 'Forward', click: () => ctx.history.forward() });
      s.crumbs = document.createElement('div');
      s.crumbs.className = 'l1-crumbs';
      title.append(s.back, s.forward, s.crumbs, ctx.take('mode'), ctx.take('inspToggle'));
      main.append(title, ctx.take('sources'), ctx.take('main'), ctx.take('runsRoot'));
      body.appendChild(main);
      root.appendChild(body);
    },

    unmount() { this.s = null; },

    update(ctx) {
      const s = this.s;
      if (!s) return;
      const S = ctx.S;
      for (const [id, button] of s.ribbon) {
        const on = id === 'runs' ? ctx.runs.open() : id === S.view;
        button.classList.toggle('on', on);
        button.setAttribute('aria-pressed', String(on));
      }
      const badge = s.ribbon.get('runs').querySelector('.badge');
      const going = ctx.runs.count();
      badge.hidden = !going;
      badge.textContent = String(going);
      const hidden = ctx.tree.hiddenToggle();
      s.visible.disabled = !hidden;
      s.visible.classList.toggle('on', Boolean(hidden && hidden.on));
      s.visible.setAttribute('aria-pressed', String(Boolean(hidden && hidden.on)));
      s.visible.title = hidden ? `${hidden.on ? 'Hide' : 'Show'} ${hidden.label.toLowerCase()}` : 'Nothing is hidden in this grouping';
      s.visible.innerHTML = ctx.ico(hidden && hidden.on ? 'eye' : 'eye-off');
      ctx.$('search').closest('.search').classList.toggle('has-query', Boolean(S.query));
      // the view header: the breadcrumb of the selection (Tree), or the view's own name
      const crumbs = S.view === 'tree' ? ctx.crumbs(S.sel) : [];
      const html = S.view === 'table' ? '<a class="last">Table</a>' : S.view === 'activity' ? '<a class="last">Activity</a>'
        : (crumbs.length ? crumbs.map((crumb, at) => `<a${at === crumbs.length - 1 ? ' class="last"' : ` data-go="${ctx.esc(crumb.path)}"`} title="${ctx.esc(crumb.name)}">${ctx.esc(crumb.name)}</a>`).join('<span class="sep">›</span>')
          : `<a class="last">${ctx.esc(S.selKey ? 'Group' : 'Devices')}</a>`);
      if (s.crumbsHtml !== html) { s.crumbsHtml = html; s.crumbs.innerHTML = html; }
    },
  };
  window.IOLayouts = window.IOLayouts || {};
  window.IOLayouts.l1 = layout;
}());
