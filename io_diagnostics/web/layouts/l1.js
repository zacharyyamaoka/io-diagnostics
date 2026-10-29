// L1 · Obsidian. Zach's own reference (his screenshot of Obsidian's left sidebar, 2026-10-24): match its conventions.
//   - a vertical RIBBON at the far left carries the views (Devices, 3D, Table, Activity, Runs) and Settings at its foot;
//   - the left PANE has a header, a small toolbar row (collapse all, expand all, View options, visibility), then the SEARCH
//     FIELD on top of the tree, then the tree;
//   - the main area is the entity page under a slim view header (back / forward, the breadcrumb of the selection).
// Every part is the page's own, moved: the search input keeps its state, the tree its expansion.
// WHY: Zach, 2026-10-24: "I think the search bar should be inside of the entity tree on top just as obsidian does it".
// The ribbon has three styles, ported from Clank's sidebar (clank-workbench edbd2c1: RAIL_MODES / toggleRail, shadcn.css's
// data-rail-mode rules): collapsed (icons only, Obsidian's, the default), expanded (each icon with its label centred under it,
// Figma's), wide (icon and label rows, Shopify's). View options > Sidebar picks one; Ctrl+Alt+R, the rail's edge and the wide
// style's collapse button toggle collapsed <-> the last open style.
'use strict';
(function () {
  const CSS = `
.l1-ribbon { flex: none; position: relative; width: 44px; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 8px 0; background: var(--io-surface-2); border-right: 1px solid var(--io-border); }
.l1-ribbon .lx-ib { width: 32px; height: 32px; }
.l1-ribbon .lx-ib .ic { width: 18px; height: 18px; }
.l1-ribbon .lx-ib.on { background: var(--io-pressed); }
.l1-ribbon .gap { flex: 1; }
.l1-ribbon .badge { position: absolute; top: 3px; right: 2px; min-width: 14px; height: 14px; padding: 0 3px; border-radius: 7px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10px; font-weight: 600; line-height: 14px; text-align: center; }
/* ---- the three styles (Clank's shadcn.css), every rule keyed on data-rail-mode so Figma's never leak into wide ---- */
.l1-ribbon .l1-label { display: none; }
.l1-ribbon .l1-rail-head { display: none; }
.l1-ribbon .l1-rail-edge { position: absolute; top: 0; bottom: 0; right: -3px; width: 6px; z-index: 5; padding: 0; border: 0; background: transparent; cursor: e-resize; }
.l1-ribbon .l1-rail-edge::before { content: ""; position: absolute; top: 0; bottom: 0; left: 2px; width: 2px; background: transparent; transition: background .1s; }
.l1-ribbon .l1-rail-edge:hover::before, .l1-ribbon .l1-rail-edge:focus-visible::before { background: var(--io-selected-line); }
.l1-ribbon[data-rail-mode="expanded"] .l1-rail-edge, .l1-ribbon[data-rail-mode="wide"] .l1-rail-edge { cursor: w-resize; }
/* Figma: the label centred under the icon in a 72px rail; a name wraps at word and camelCase seams, two lines at most */
.l1-ribbon[data-rail-mode="expanded"] { width: 72px; padding: 8px 4px; }
.l1-ribbon[data-rail-mode="expanded"] .lx-ib { width: 100%; height: 58px; flex-direction: column; justify-content: center; gap: 3px; padding: 4px; font-size: 10px; line-height: 1.1; text-align: center; }
.l1-ribbon[data-rail-mode="expanded"] .lx-ib .ic { width: 22px; height: 22px; }
.l1-ribbon[data-rail-mode="expanded"] .l1-label { display: -webkit-box; max-width: 100%; overflow: hidden; white-space: normal; overflow-wrap: anywhere; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.l1-ribbon[data-rail-mode="expanded"] .badge { top: 5px; right: 12px; }
/* Shopify: a 240px list of icon + label rows, the logo row with the collapse button on top, Settings at the foot */
.l1-ribbon[data-rail-mode="wide"] { width: 240px; align-items: stretch; padding: 8px; gap: 2px; }
.l1-ribbon[data-rail-mode="wide"] .l1-rail-head { display: flex; align-items: center; gap: 4px; height: 32px; margin-bottom: 6px; padding-left: 8px; }
.l1-ribbon .l1-rail-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 600; color: var(--io-text-strong); }
.l1-ribbon[data-rail-mode="wide"] .lx-ib { width: 100%; height: 32px; justify-content: flex-start; gap: 8px; padding: 0 8px; font-size: 13px; text-align: left; }
.l1-ribbon[data-rail-mode="wide"] .l1-rail-head .lx-ib { width: 26px; height: 26px; padding: 0; justify-content: center; }
.l1-ribbon[data-rail-mode="wide"] .l1-label { display: block; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.l1-ribbon[data-rail-mode="wide"] .badge { position: static; flex: none; margin-left: auto; }
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
    { id: 'tree', icon: 'devices', title: 'Devices (the tree)', label: 'Devices' },
    // WHY here: Zach, 2026-10-29: a "3D" item in the activity bar; the left tree stays, the device page's area becomes the robot
    { id: 'threed', icon: 'i-cube', title: '3D: the robot, each part coloured by its device', label: '3D' },
    { id: 'table', icon: 'table', title: 'Table', label: 'Table' },
    { id: 'activity', icon: 'log', title: 'Activity', label: 'Activity' },
    { id: 'runs', icon: 'i-tty', title: 'Runs (the terminal panel)', label: 'Runs' },
  ];

  // ---- the sidebar style: the store, ported from Clank's workbench/store.ts (RAIL_MODES, toggleRail) ----
  // WHY the store takes its storage: tests/test_rail_mode.js runs it in node over a fake one, as Clank's store.test.ts does.
  // WHY lastOpen has its own key, written only when an open style is chosen: the toggle is collapsed <-> lastOpen, never a
  // three-way cycle, and the open style survives a reload while the rail is collapsed. An unknown stored value reads as collapsed.
  const RAIL_MODES = ['collapsed', 'expanded', 'wide'];
  const RAIL_LABELS = { collapsed: 'Icons only', expanded: 'Icon + label (Figma)', wide: 'Wide list (Shopify)' };
  const isRailMode = (value) => RAIL_MODES.includes(value);
  function createRailStore(storage) {
    const read = (key) => { try { return storage.get(key); } catch (_) { return null; } };
    const write = (key, value) => { try { storage.set(key, value); } catch (_) { /* private mode: lasts this page only */ } };
    const stored = read('sidebar');
    let mode = isRailMode(stored) ? stored : 'collapsed';
    let lastOpen = mode !== 'collapsed' ? mode : (read('sidebar.last') === 'wide' ? 'wide' : 'expanded');
    const listeners = new Set();
    const store = {
      mode: () => mode,
      lastOpen: () => lastOpen,
      set(next) {
        if (!isRailMode(next)) return;
        write('sidebar', next);
        mode = next;
        if (next !== 'collapsed') { lastOpen = next; write('sidebar.last', next); }
        for (const listener of listeners) listener(mode);
      },
      toggle() { store.set(mode === 'collapsed' ? lastOpen : 'collapsed'); },
      subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    };
    return store;
  }
  // Figma's rail breaks a long name at a camelCase seam (<wbr>) as well as at spaces; the full name stays in the button's title
  const labelHtml = (text, esc) => text.split(/(?<=[a-z])(?=[A-Z])/).map(esc).join('<wbr>');

  // The Dashboard (chart view c4's own view, with its own button in the classic views bar, which L1 does not draw): a ribbon button that
  // presses c4's, once c4 has mounted; the other ribbon buttons close the board first (c4 closes it only for clicks inside #views)
  const dashboardButton = () => document.querySelector('#views [data-ch4]');
  const closeDashboard = () => { const dash = dashboardButton(); if (dash && document.body.classList.contains('ch4-on')) dash.click(); };

  const layout = {
    name: 'Obsidian',
    description: 'A ribbon of views at the far left; the left pane has a toolbar row, the search field on top of the tree, then the tree; the main area is the entity page.',
    order: 1,
    keepTree: true,
    // the control that reaches each view and what must then be on screen (tests/test_layouts.js)
    reach: {
      tree: { control: '.l1-ribbon [data-l1="tree"]', shows: '#tree-host .t6-row' },
      threed: { control: '.l1-ribbon [data-l1="threed"]', shows: '#threed-host .td-root' },
      table: { control: '.l1-ribbon [data-l1="table"]', shows: 'table.grid' },
      activity: { control: '.l1-ribbon [data-l1="activity"]', shows: '.act .ev' },
      runs: { control: '.l1-ribbon [data-l1="runs"]', shows: '#runs-root.r1' },
    },
    mount(ctx) {
      ctx.css(CSS);
      const root = ctx.root;
      const s = { ribbon: new Map() };
      this.s = s;
      s.rail = createRailStore({ get: (key) => ctx.pref(key, null), set: (key, value) => ctx.setPref(key, value) });
      // the label every ribbon button carries (shown by the open styles, hidden when collapsed); the button's title keeps the full name
      const label = (button, text) => { button.insertAdjacentHTML('beforeend', `<span class="l1-label">${labelHtml(text, ctx.esc)}</span>`); return button; };
      s.label = label;

      // ---- the ribbon ----
      const ribbon = document.createElement('nav');
      ribbon.className = 'l1-ribbon';
      ribbon.setAttribute('aria-label', 'Views');
      // the wide style's logo row: the name and the collapse button (Shopify's, top right)
      const logoRow = document.createElement('div');
      logoRow.className = 'l1-rail-head';
      logoRow.innerHTML = '<span class="l1-rail-title">RobotDx</span>';
      logoRow.appendChild(ctx.iconButton({ icon: 'panel-close', title: 'Collapse the sidebar ( Ctrl+Alt+R )', click: () => s.rail.toggle() }));
      ribbon.appendChild(logoRow);
      const fold = label(ctx.iconButton({ icon: 'sidebar', title: 'Toggle the left pane', click: () => sash.toggle() }), 'Left pane');
      ribbon.appendChild(fold);
      // the Ctrl-K palette (layouts/palette.js, L4's, shared): jump to a device or run an action. Zach, 2026-10-24: "L1 obsidian",
      // with L4's palette on top
      s.palette = window.IOLayoutPalette.create(ctx, {
        anchor: () => gear,
        go: () => [
          { icon: 'lx-devices', label: 'Devices', sub: 'The tree and the entity page', run: () => ctx.go('tree') },
          { icon: 'i-cube', label: '3D', sub: 'The robot, its parts coloured by their devices', run: () => ctx.go('threed') },
          { icon: 'lx-table', label: 'Table', sub: 'Every device in one table', run: () => ctx.go('table') },
          { icon: 'lx-log', label: 'Activity', sub: 'The log, newest first', run: () => ctx.go('activity') },
          { icon: 'i-tty', label: 'Terminal', sub: 'Runs of utilities', run: () => ctx.runs.toggle() },
        ],
        actions: () => [{ icon: 'lx-sidebar', label: 'Toggle the left pane', sub: 'Ctrl+\\', run: () => sash.toggle() }],
      });
      const jump = ctx.iconButton({ icon: 'cmd', title: 'Jump to a device or run an action ( Ctrl K )', click: () => s.palette.open() });
      jump.dataset.l1Palette = '1';
      label(jump, 'Jump');
      ribbon.appendChild(jump);
      for (const item of RIBBON) {
        const button = ctx.iconButton({ icon: item.icon, title: item.title, pressed: false });
        button.dataset.l1 = item.id;
        label(button, item.label);
        button.addEventListener('click', () => {
          if (item.id !== 'runs') closeDashboard();
          if (item.id === 'runs') { ctx.runs.toggle(); return; }
          if (sash.folded() && (item.id === 'tree' || item.id === 'threed')) sash.fold(false);
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
      label(gear, 'Settings');
      ribbon.appendChild(gear);
      // the rail's edge (stock shadcn SidebarRail): a click toggles collapsed <-> the last open style
      const edge = document.createElement('button');
      edge.type = 'button';
      edge.className = 'l1-rail-edge';
      edge.title = 'Toggle the sidebar width ( Ctrl+Alt+R )';
      edge.setAttribute('aria-label', 'Toggle the sidebar width');
      edge.addEventListener('click', () => s.rail.toggle());
      ribbon.appendChild(edge);
      s.ribbon_el = ribbon;
      root.appendChild(ribbon);

      // View options > Sidebar (the setting), kept in step with the store.
      // WHY: Zach, 2026-10-26: "can you add an option in the settings to show it with the text and without text. or with the icon
      // and text center (figma style) this is similar to how shopify does it!"
      const choice = ctx.$('sidebar-style');
      const row = ctx.$('sidebar-switch');
      choice.innerHTML = RAIL_MODES.map((mode) => `<option value="${mode}">${RAIL_LABELS[mode]}</option>`).join('');
      const show = (mode) => { ribbon.dataset.railMode = mode; choice.value = mode; };
      show(s.rail.mode());
      ctx.cleanup(s.rail.subscribe(show));
      ctx.on(choice, 'change', () => s.rail.set(choice.value));
      row.hidden = false;
      ctx.cleanup(() => { row.hidden = true; });
      // Ctrl+Alt+R: a free chord here (Clank's own mod+alt+r)
      ctx.on(document, 'keydown', (event) => {
        if (event.ctrlKey && event.altKey && !event.metaKey && !event.shiftKey && event.code === 'KeyR') { event.preventDefault(); s.rail.toggle(); }
      });

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
        const on = id === 'runs' ? ctx.runs.open() : id === S.view && !document.body.classList.contains('ch4-on');
        button.classList.toggle('on', on);
        button.setAttribute('aria-pressed', String(on));
      }
      const dash = dashboardButton();
      if (dash && !s.dash) {
        s.dash = s.label(ctx.iconButton({ icon: 'dash', title: 'Dashboard: the charts you pinned', click: () => dashboardButton().click() }), 'Dashboard');
        s.dash.dataset.l1 = 'dashboard';
        s.dash.insertAdjacentHTML('beforeend', '<span class="badge" hidden></span>');
        s.ribbon_el.insertBefore(s.dash, s.ribbon_el.querySelector('.gap'));
      }
      if (s.dash) {
        const on = Boolean(dash && dash.hasAttribute('data-on'));
        s.dash.classList.toggle('on', on);
        s.dash.setAttribute('aria-pressed', String(on));
        const count = dash ? (dash.querySelector('.ch4-count') || {}).textContent || '' : '';
        const mark = s.dash.querySelector('.badge');
        mark.hidden = !count;
        mark.textContent = count;
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
      const crumbs = S.view === 'tree' || S.view === 'threed' ? ctx.crumbs(S.sel) : [];
      // the 3D view: "3D", then the selection's breadcrumb (a crumb selects that row, the view stays 3D)
      const threed = S.view === 'threed';
      const html = S.view === 'table' ? '<a class="last">Table</a>' : S.view === 'activity' ? '<a class="last">Activity</a>'
        : threed && !crumbs.length ? '<a class="last">3D</a>'
        : (threed ? '<a class="l1-view">3D</a><span class="sep">›</span>' : '') + (crumbs.length ? crumbs.map((crumb, at) => `<a${at === crumbs.length - 1 ? ' class="last"' : ` data-go="${ctx.esc(crumb.path)}"`} title="${ctx.esc(crumb.raw)}">${ctx.esc(crumb.name)}</a>`).join('<span class="sep">›</span>')
          : `<a class="last">${ctx.esc(S.selKey ? 'Group' : 'Devices')}</a>`);
      if (s.crumbsHtml !== html) { s.crumbsHtml = html; s.crumbs.innerHTML = html; }
    },
  };
  window.IOLayouts = window.IOLayouts || {};
  layout.createRailStore = createRailStore; // tests/test_rail_mode.js
  layout.labelHtml = labelHtml;               // tests/test_layouts.js
  window.IOLayouts.l1 = layout;
}());
