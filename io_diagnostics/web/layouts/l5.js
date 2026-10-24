// L5 · GNOME System Monitor / Cockpit. Top-level TABS: Overview · Devices · Activity · Runs. The device tree, with its search
// on top, lives inside the Devices tab (beside the entity page; a Tree | Table switch in the header); the Overview is the
// Computer's own page (its resources, the page d4 draws for the host) beside "what needs you"; Activity is the full log;
// Runs is the terminal panel given the whole tab. One tab answers one question; nothing is on screen twice.
'use strict';
(function () {
  const CSS = `
.lx-l5 { flex-direction: column; background: var(--io-surface-alt); }
.l5-head { flex: none; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; height: 46px; padding: 0 10px 0 14px; background: var(--io-surface-2); border-bottom: 1px solid var(--io-border); }
.l5-head .title { font-weight: 600; color: var(--io-text-strong); display: flex; align-items: center; gap: 8px; }
.l5-head .title .ic { color: var(--io-icon); }
.l5-head .right { justify-self: end; display: flex; align-items: center; gap: 6px; min-width: 0; }
.l5-head #mode { font-size: 11.5px; color: var(--io-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.l5-tabs button { padding: 5px 16px; }
.l5-seg { display: none; }
.lx-l5[data-l5on="devices"] .l5-seg { display: inline-flex; }
.l5-seg button { padding: 3px 10px; font-size: 12px; }
.l5-body { flex: 1; min-height: 0; display: flex; }
.l5-needs, .l5-tree, .l5-runs, .lx-l5 .lx-sash-l5needs, .lx-l5 .lx-sash-l5side { display: none; }
.lx-l5[data-l5on="overview"] .l5-needs { display: flex; }
.lx-l5[data-l5on="overview"] .lx-sash-l5needs { display: block; }
.lx-l5[data-l5on="devices"] .l5-tree { display: flex; }
.lx-l5[data-l5on="devices"] .lx-sash-l5side { display: block; }
.lx-l5[data-l5on="runs"] .l5-runs { display: flex; }
.lx-l5[data-l5on="runs"] #main { display: none; }
.lx-fold-l5needs .l5-needs, .lx-fold-l5side .l5-tree { display: none !important; }
.l5-needs { flex: none; width: var(--l5-needs-w, 360px); flex-direction: column; overflow: auto; background: var(--io-surface); border-right: 1px solid var(--io-border-soft); padding: 18px 18px 24px; min-width: 0; }
.l5-needs h2 { margin: 0; font: 600 20px/1.25 var(--io-font-display); color: var(--io-text-strong); }
.l5-needs .lede { margin: 4px 0 14px; color: var(--io-muted); font-size: 12.5px; }
.l5-needs h3 { margin: 22px 0 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: var(--io-muted); font-weight: 600; display: flex; align-items: baseline; gap: 8px; }
.l5-needs h3 button { margin-left: auto; border: 0; background: transparent; color: var(--io-accent-text); cursor: pointer; font-size: 12px; text-transform: none; letter-spacing: 0; padding: 0; }
.l5-need { display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: 2px 8px; width: 100%; text-align: left; padding: 8px 6px; border: 0; border-bottom: 1px solid var(--io-border-soft); background: transparent; cursor: pointer; }
.l5-need:hover { background: var(--io-hover); }
.l5-need .shape { width: 15px; height: 15px; margin-top: 2px; }
.l5-need .who { color: var(--io-text-strong); font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.l5-need .who span { color: var(--io-muted); font-weight: 400; }
.l5-need .why { grid-column: 2; color: var(--io-muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.l5-recent { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 8px; padding: 4px 6px; font-size: 12.5px; color: var(--io-text); }
.l5-recent .t { font-family: var(--io-font-mono); font-size: 11.5px; color: var(--io-muted); }
.l5-recent .x { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.l5-tree { flex: none; width: var(--l5-side-w, 290px); border-right: 0; }
.l5-body #main { flex: 1; min-width: 0; }
.l5-runs { flex: 1; min-width: 0; flex-direction: column; background: var(--io-surface); }
.l5-runs #runs-root.r1 { flex: 1; height: auto; border-top: 0; }
.l5-runs .r1-sash { display: none; }
`;
  const TABS = [['overview', 'Overview'], ['devices', 'Devices'], ['activity', 'Activity'], ['runs', 'Runs']];

  const layout = {
    name: 'System Monitor',
    description: 'GNOME System Monitor / Cockpit: top-level tabs Overview, Devices, Activity, Runs; the device tree with its search inside Devices; an Overview of the computer plus what needs you.',
    order: 5,
    keepTree: true,
    reach: {
      tree: { via: ['.l5-tabs [data-l5-tab="devices"]'], control: '.l5-seg [data-l5-view="tree"]', shows: '#tree-host .t6-row' },
      table: { via: ['.l5-tabs [data-l5-tab="devices"]'], control: '.l5-seg [data-l5-view="table"]', shows: 'table.grid' },
      activity: { control: '.l5-tabs [data-l5-tab="activity"]', shows: '.act .ev' },
      runs: { control: '.l5-tabs [data-l5-tab="runs"]', shows: '#runs-root.r1' },
    },
    mount(ctx) {
      ctx.css(CSS);
      const root = ctx.root;
      const params = new URLSearchParams(location.search);
      const stored = ctx.pref('l5.tab', 'overview');
      const s = { tab: params.get('sel') || params.get('group') || params.get('view') ? 'devices' : (TABS.some(([id]) => id === stored) ? stored : 'overview'),
        lastDevice: null, lastRuns: false, html: {}, needsOverview: false };
      s.needsOverview = s.tab === 'overview';
      this.s = s;
      root.dataset.l5on = s.tab;

      // ---- the header bar: title, the four tabs, the Devices-only Tree | Table switch, View options ----
      const head = document.createElement('header');
      head.className = 'l5-head';
      head.innerHTML = `<div class="title">${ctx.ico('i-io')}<span>RobotDx</span></div>`;
      const tabs = document.createElement('div');
      tabs.className = 'seg l5-tabs';
      tabs.setAttribute('role', 'tablist');
      tabs.innerHTML = TABS.map(([id, label]) => `<button role="tab" data-l5-tab="${id}">${label}</button>`).join('');
      tabs.addEventListener('click', (event) => { const button = event.target.closest('[data-l5-tab]'); if (button) this.setTab(ctx, button.dataset.l5Tab); });
      const right = document.createElement('div');
      right.className = 'right';
      const seg = ctx.el('<div class="seg l5-seg" role="group" aria-label="Devices view"><button data-l5-view="tree">Tree</button><button data-l5-view="table">Table</button></div>');
      seg.addEventListener('click', (event) => { const button = event.target.closest('[data-l5-view]'); if (button) ctx.go(button.dataset.l5View); });
      right.append(seg, ctx.take('inspToggle'), ctx.take('mode'), ctx.take('vo'));
      root.appendChild(ctx.take('voPop'));
      head.append(tabs, right);
      root.appendChild(head);
      s.tabs = tabs;

      // ---- the body: needs-you (Overview) | the tree pane (Devices) | the page | the terminal (Runs) ----
      const body = document.createElement('div');
      body.className = 'l5-body';
      s.needs = document.createElement('aside');
      s.needs.className = 'l5-needs';
      s.needs.setAttribute('aria-label', 'What needs you');
      s.needs.addEventListener('click', (event) => {
        const go = event.target.closest('[data-l5-go]');
        if (go) { this.setTab(ctx, go.dataset.l5Go); return; }
        const item = event.target.closest('[data-l5-open]');
        if (item) { this.setTab(ctx, 'devices'); ctx.select(item.dataset.l5Open); }
      });
      s.needsSash = ctx.sash({ key: 'l5needs', axis: 'x', anchor: 'start', def: 360, min: 260, max: (total) => total - 420, varName: '--l5-needs-w', root, within: body, label: 'Resize what needs you' });
      s.tree = ctx.treePane({ cls: 'l5-tree' });
      s.sideSash = ctx.sash({ key: 'l5side', axis: 'x', anchor: 'start', def: 290, min: 200, max: (total) => total - 460, varName: '--l5-side-w', root, within: body, label: 'Resize the device list' });
      const runs = document.createElement('section');
      runs.className = 'l5-runs';
      runs.appendChild(ctx.take('runsRoot'));
      body.append(s.needs, s.needsSash.el, s.tree, s.sideSash.el, ctx.take('sources'), ctx.take('main'), runs);
      root.appendChild(body);
      this.paint(ctx);
    },

    unmount() { this.s = null; },

    // -------------------------------------------------------------------------------------- the tabs ----
    // Each tab is a place in the page's own state: Overview selects the Computer, Devices the last device looked at,
    // Activity is the Activity view, Runs opens the page's terminal panel.
    setTab(ctx, tab) {
      const s = this.s;
      const S = ctx.S;
      const host = S.data && S.data.tree && S.data.tree.path;
      if (s.tab === 'devices' && S.sel && S.sel !== host) s.lastDevice = S.sel;
      // WHY busy: each step below re-renders the page, and update() would read the half-moved state as the viewer's own move
      s.busy = true;
      try {
        s.tab = tab;
        ctx.setPref('l5.tab', tab);
        ctx.runs.set(tab === 'runs');
        s.lastRuns = ctx.runs.open();
        if (tab === 'activity') ctx.go('activity');
        else if (S.view === 'activity' || (tab !== 'devices' && S.view === 'table')) ctx.go('tree');
        if (tab === 'overview' && host && S.sel !== host) ctx.select(host);
        if (tab === 'devices' && host && (S.sel === host || !S.sel)) {
          const attention = ctx.attention()[0];
          const target = s.lastDevice && S.byPath && S.byPath.has(s.lastDevice) ? s.lastDevice : attention && attention.path;
          if (target) ctx.select(target);
        }
      } finally { s.busy = false; }
      this.paint(ctx);
      this.update(ctx);
    },
    paint(ctx) {
      const s = this.s;
      ctx.root.dataset.l5on = s.tab;
      s.tabs.querySelectorAll('[data-l5-tab]').forEach((button) => { const on = button.dataset.l5Tab === s.tab; button.classList.toggle('on', on); button.setAttribute('aria-selected', String(on)); });
    },

    update(ctx) {
      const s = this.s;
      if (!s) return;
      const S = ctx.S;
      const host = S.data && S.data.tree && S.data.tree.path;
      if (s.busy) return;
      // ---- the first paint of the Overview: the Computer is selected once the data is here ----
      if (s.needsOverview && host) { s.needsOverview = false; if (S.sel !== host) ctx.select(host); }
      // ---- the tab follows the page when something else moved it (back / forward, a link, a run starting) ----
      const running = ctx.runs.open();
      if (running !== s.lastRuns) {
        s.lastRuns = running;
        if (running) s.tab = 'runs'; else if (s.tab === 'runs') s.tab = 'devices';
      } else if (S.view === 'activity') s.tab = 'activity';
      else if (s.tab === 'activity' || (s.tab === 'runs' && !running)) s.tab = 'devices';
      else if (S.view === 'table') s.tab = 'devices';
      else if (s.tab === 'overview' && S.sel && S.sel !== host && !s.needsOverview) s.tab = 'devices';
      if (s.tab === 'devices' && S.sel && S.sel !== host) s.lastDevice = S.sel;
      this.paint(ctx);
      s.tabs.querySelector('[data-l5-tab="runs"]').dataset.going = String(ctx.runs.count());
      s.tabs.querySelector('[data-l5-tab="runs"]').textContent = ctx.runs.count() ? `Runs · ${ctx.runs.count()}` : 'Runs';
      document.querySelectorAll('.l5-seg button').forEach((button) => button.classList.toggle('on', button.dataset.l5View === S.view));
      ctx.$('search').closest('.search').classList.toggle('has-query', Boolean(S.query));
      // ---- Overview: what needs you, then the recent events ----
      const need = ctx.attention();
      const shape = (level) => `<svg class="shape lv-${level}" role="img" aria-label="${level}"><use href="#s-${level}"/></svg>`;
      const recent = (S.data && S.data.recent) || [];
      const html = `<h2>${need.length ? `${need.length} need${need.length === 1 ? 's' : ''} you` : 'Everything is OK'}</h2>`
        + `<p class="lede">${need.length ? 'Click one to open it in Devices.' : 'Every device answers as expected.'} ${(S.data && S.data.summary.entities) || 0} entities on ${ctx.esc((S.data && S.data.host && S.data.host.name) || 'this computer')}.</p>`
        + need.map((item) => `<button class="l5-need" data-l5-open="${ctx.esc(item.path)}">${shape(item.level)}<span class="who">${ctx.esc(item.device && item.device.path !== item.path ? item.device.name : item.name)}${item.device && item.device.path !== item.path ? ` <span>› ${ctx.esc(item.name)}</span>` : ''}</span><span class="why">${ctx.esc(item.message)}</span></button>`).join('')
        + (recent.length ? `<h3>Recent<button data-l5-go="activity">Open the log</button></h3>${recent.slice(0, 5).map((event) => `<div class="l5-recent"><span class="t">${ctx.esc(String(event.time || '').slice(0, 5))}</span><span class="x" title="${ctx.esc(event.message)}">${ctx.esc(event.message)}</span></div>`).join('')}` : '');
      if (s.html.needs !== html) { s.html.needs = html; s.needs.innerHTML = html; }
    },
  };
  window.IOLayouts = window.IOLayouts || {};
  window.IOLayouts.l5 = layout;
}());
