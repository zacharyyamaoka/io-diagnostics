// L3 · Claude desktop. Minimal chrome, like the Claude desktop app: a calm LEFT LIST of devices with the search on top (its
// session list), "Show N hidden" at the foot of the list, the entity in the MIDDLE, and a RIGHT pane that opens on demand for
// the Activity of the selection or a running terminal (its side panel). Nothing else is on screen until asked for.
// WHY the right pane opens by itself for a run: the Claude app shows the terminal beside the conversation the moment a
// command runs; a utility started on a board (Utilities ▾) opens its terminal here.
'use strict';
(function () {
  const CSS = `
.lx-l3 { background: var(--io-surface); }
.l3-side { flex: none; width: var(--l3-side-w, 272px); background: var(--io-surface-2); border-right: 1px solid var(--io-border-soft); }
.lx-fold-l3side .l3-side { display: none; }
.l3-top { flex: none; display: flex; align-items: center; gap: 4px; height: 44px; padding: 0 8px 0 12px; }
.l3-top .brand { font-weight: 600; color: var(--io-text-strong); letter-spacing: .1px; }
.l3-top .grow { flex: 1; }
.l3-side .lx-tree-scroll { padding-top: 2px; }
.l3-more { flex: none; display: flex; align-items: center; gap: 8px; margin: 0 6px 4px; padding: 0 10px 0 8px; height: 30px; border: 0; border-radius: var(--io-radius-sm); background: transparent; color: var(--io-muted); cursor: pointer; text-align: left; font-size: 12.5px; }
.l3-more:hover { background: var(--io-hover); color: var(--io-text); }
.l3-more .ic { width: 14px; height: 14px; transition: transform .12s; }
.l3-more[aria-pressed="true"] .ic { transform: rotate(90deg); }
.l3-more[hidden] { display: none; }
.l3-foot { flex: none; padding: 8px 14px 10px; border-top: 1px solid var(--io-border-soft); min-height: 34px; display: flex; align-items: center; }
.l3-foot #mode { font-size: 11.5px; color: var(--io-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.l3-center { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--io-surface-alt); }
.l3-bar { flex: none; display: flex; align-items: center; gap: 6px; height: 44px; padding: 0 10px 0 12px; min-width: 0; }
.l3-bar .path { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 6px; overflow: hidden; white-space: nowrap; }
.l3-bar .path .parent { color: var(--io-faint); font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; }
.l3-bar .path .parent a { color: inherit; cursor: pointer; }
.l3-bar .path .parent a:hover { color: var(--io-text); }
.l3-bar .path .here { color: var(--io-text-strong); font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
.l3-bar .seg { flex: none; }
.l3-bar .seg button { padding: 3px 10px; font-size: 12px; }
.l3-right { flex: none; width: var(--l3-right-w, 380px); display: flex; flex-direction: column; min-height: 0; background: var(--io-surface); border-left: 1px solid var(--io-border-soft); }
.lx-fold-l3right .l3-right { display: none; }
.l3-rhead { flex: none; display: flex; align-items: center; gap: 2px; height: 44px; padding: 0 8px 0 10px; border-bottom: 1px solid var(--io-border-soft); }
.l3-rtab { border: 0; background: transparent; padding: 4px 10px; border-radius: var(--io-radius-sm); color: var(--io-muted); cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.l3-rtab:hover { background: var(--io-hover); color: var(--io-text); }
.l3-rtab.on { background: var(--io-pressed); color: var(--io-text-strong); }
.l3-rtab .badge { min-width: 15px; height: 15px; padding: 0 4px; border-radius: 8px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10.5px; font-weight: 600; line-height: 15px; text-align: center; }
.l3-rhead .grow { flex: 1; }
.l3-rbody { flex: 1; min-height: 0; position: relative; }
.l3-ract { position: absolute; inset: 0; overflow: auto; }
.l3-ract .act > section { padding: 10px 14px 16px; }
.l3-ract .act h2 { flex-wrap: wrap; row-gap: 6px; }
.l3-ract .ev { grid-template-columns: 52px auto 1fr auto; }
.l3-ract .ev .p { display: none; }
.l3-ract .scope { margin-left: auto; text-transform: none; letter-spacing: 0; }
.l3-ract .scope button { font-family: var(--io-font-ui); }
.l3-rterm { position: absolute; inset: 0; display: flex; flex-direction: column; }
.l3-right #runs-root.r1 { flex: 1; height: auto; border-top: 0; }
.l3-right .r1-sash { display: none; }
.lx-l3 .t6-head { height: 28px; }
`;

  const layout = {
    name: 'Claude desktop',
    description: 'A calm list of devices with the search on top and "Show N hidden"; the entity in the middle; a side pane that opens for the selection\'s Activity or a running terminal.',
    order: 3,
    keepTree: true,
    reach: {
      tree: { control: '.l3-bar [data-l3-view="tree"]', shows: '#tree-host .t6-row' },
      table: { control: '.l3-bar [data-l3-view="table"]', shows: 'table.grid' },
      activity: { control: '.l3-bar [data-l3-pane="activity"]', shows: '.l3-ract .act .ev' },
      runs: { control: '.l3-bar [data-l3-pane="terminal"]', shows: '#runs-root.r1' },
    },
    mount(ctx) {
      ctx.css(CSS);
      const root = ctx.root;
      const s = { tab: ctx.pref('l3.tab', 'activity'), scope: ctx.pref('l3.scope', 'selection'), lastRuns: ctx.runs.open(), html: {} };
      this.s = s;

      // ---- the left list ----
      const top = document.createElement('div');
      top.className = 'l3-top';
      top.innerHTML = '<span class="brand">RobotDx</span><span class="grow"></span>';
      top.append(ctx.take('vo'), ctx.iconButton({ icon: 'sidebar', title: 'Hide the list', click: () => s.sideSash.fold(true) }));
      root.appendChild(ctx.take('voPop'));
      s.more = document.createElement('button');
      s.more.type = 'button';
      s.more.className = 'l3-more';
      s.more.setAttribute('aria-pressed', 'false');
      s.more.addEventListener('click', () => { const hidden = ctx.tree.hiddenToggle(); if (hidden) ctx.tree.setHidden(!hidden.on); });
      const foot = document.createElement('div');
      foot.className = 'l3-foot';
      foot.appendChild(ctx.take('mode'));
      const below = document.createElement('div');
      below.append(s.more, foot);
      s.side = ctx.treePane({ cls: 'l3-side', head: top, below });
      root.appendChild(s.side);
      s.sideSash = ctx.sash({ key: 'l3side', axis: 'x', anchor: 'start', def: 272, min: 200, max: (total) => total - 480, varName: '--l3-side-w', root, label: 'Resize the list' });
      root.appendChild(s.sideSash.el);

      // ---- the middle ----
      const center = document.createElement('section');
      center.className = 'l3-center';
      const bar = document.createElement('div');
      bar.className = 'l3-bar';
      s.open = ctx.iconButton({ icon: 'sidebar', title: 'Show the list', click: () => s.sideSash.fold(false) });
      s.path = document.createElement('div');
      s.path.className = 'path';
      const seg = ctx.el('<div class="seg" role="group" aria-label="View"><button data-l3-view="tree">Device</button><button data-l3-view="table">Table</button></div>');
      seg.addEventListener('click', (event) => { const button = event.target.closest('[data-l3-view]'); if (button) ctx.go(button.dataset.l3View); });
      s.activityButton = ctx.iconButton({ icon: 'log', title: 'Activity of this device', pressed: false });
      s.activityButton.dataset.l3Pane = 'activity';
      s.activityButton.addEventListener('click', () => this.pane(ctx, 'activity'));
      s.terminalButton = ctx.iconButton({ icon: 'i-tty', title: 'Terminal', pressed: false });
      s.terminalButton.dataset.l3Pane = 'terminal';
      s.terminalButton.addEventListener('click', () => this.pane(ctx, 'terminal'));
      bar.append(s.open, s.path, seg, ctx.take('inspToggle'), s.activityButton, s.terminalButton);
      s.seg = seg;
      center.append(bar, ctx.take('sources'), ctx.take('main'));
      root.appendChild(center);

      // ---- the right pane ----
      s.rightSash = ctx.sash({ key: 'l3right', axis: 'x', anchor: 'end', def: 380, min: 260, max: (total) => total - 272 - 460, varName: '--l3-right-w', root, label: 'Resize the side pane', folded: true });
      const right = document.createElement('aside');
      right.className = 'l3-right';
      right.setAttribute('aria-label', 'Side pane');
      right.innerHTML = '<div class="l3-rhead"><button class="l3-rtab" data-pane-tab="activity">Activity</button><button class="l3-rtab" data-pane-tab="terminal">Terminal<span class="badge" hidden></span></button><span class="grow"></span></div>'
        + '<div class="l3-rbody"><div class="l3-ract"></div><div class="l3-rterm"></div></div>';
      right.querySelector('.l3-rhead').appendChild(ctx.iconButton({ icon: 'x', title: 'Close the side pane', click: () => this.closePane(ctx) }));
      right.querySelectorAll('[data-pane-tab]').forEach((button) => button.addEventListener('click', () => this.pane(ctx, button.dataset.paneTab, true)));
      right.querySelector('.l3-rterm').appendChild(ctx.take('runsRoot'));
      s.pact = right.querySelector('.l3-ract');
      s.right = right;
      ctx.on(s.pact, 'click', (event) => {
        const scope = event.target.closest('[data-l3-scope]');
        if (scope) { s.scope = scope.dataset.l3Scope; ctx.setPref('l3.scope', s.scope); this.update(ctx); event.stopPropagation(); }
      });
      root.append(s.rightSash.el, right);
      s.rightSash.apply();
      if (!s.rightSash.folded()) this.paintPane(ctx);
    },

    unmount() { this.s = null; },

    // the pane's tab: Activity (this device's events) or Terminal (the page's runs panel); the same button closes it
    pane(ctx, tab, stay) {
      const s = this.s;
      if (!stay && !s.rightSash.folded() && s.tab === tab) { this.closePane(ctx); return; }
      s.tab = tab;
      ctx.setPref('l3.tab', tab);
      if (s.rightSash.folded()) s.rightSash.fold(false);
      ctx.runs.set(tab === 'terminal');
      s.lastRuns = ctx.runs.open();
      this.update(ctx);
    },
    closePane(ctx) {
      const s = this.s;
      s.rightSash.fold(true);
      ctx.runs.set(false);
      s.lastRuns = ctx.runs.open();
      this.update(ctx);
    },
    paintPane() {
      const s = this.s;
      s.right.querySelectorAll('[data-pane-tab]').forEach((button) => { const on = button.dataset.paneTab === s.tab; button.classList.toggle('on', on); button.setAttribute('aria-selected', String(on)); });
      s.pact.style.display = s.tab === 'activity' ? '' : 'none';
      s.right.querySelector('.l3-rterm').style.display = s.tab === 'terminal' ? '' : 'none';
    },

    update(ctx) {
      const s = this.s;
      if (!s) return;
      const S = ctx.S;
      // ---- the terminal followed from outside (a run started): the pane opens on it ----
      const running = ctx.runs.open();
      if (running !== s.lastRuns) {
        s.lastRuns = running;
        if (running) { s.tab = 'terminal'; ctx.setPref('l3.tab', 'terminal'); if (s.rightSash.folded()) s.rightSash.fold(false); } else if (s.tab === 'terminal') { s.rightSash.fold(true); }
      }
      this.paintPane(ctx);
      const paneOpen = !s.rightSash.folded();
      s.activityButton.classList.toggle('on', paneOpen && s.tab === 'activity');
      s.activityButton.setAttribute('aria-pressed', String(paneOpen && s.tab === 'activity'));
      s.terminalButton.classList.toggle('on', paneOpen && s.tab === 'terminal');
      s.terminalButton.setAttribute('aria-pressed', String(paneOpen && s.tab === 'terminal'));
      const going = ctx.runs.count();
      const badge = s.right.querySelector('[data-pane-tab="terminal"] .badge');
      badge.hidden = !going;
      badge.textContent = String(going);
      // ---- the middle bar: where you are, and the one view switch ----
      s.open.style.display = s.sideSash.folded() ? '' : 'none';
      s.seg.querySelectorAll('button').forEach((button) => button.classList.toggle('on', button.dataset.l3View === S.view || (S.view === 'activity' && button.dataset.l3View === 'tree')));
      const crumbs = ctx.crumbs(S.sel);
      const here = crumbs.length ? crumbs[crumbs.length - 1] : null;
      const parent = crumbs.length > 1 ? crumbs[crumbs.length - 2] : null;
      const html = S.view === 'table' ? '<span class="here">Table</span>'
        : (here ? `${parent ? `<span class="parent"><a data-go="${ctx.esc(parent.path)}">${ctx.esc(parent.name)}</a> /</span>` : ''}<span class="here" title="${ctx.esc(here.name)}">${ctx.esc(ctx.api.entityLabel(here.path))}</span>` : '<span class="here">Devices</span>');
      if (s.html.path !== html) { s.html.path = html; s.path.innerHTML = html; }
      // ---- "Show N hidden": what the grouping's switch would add, counted by the page's own builder ----
      const hidden = ctx.tree.hiddenToggle();
      let more = '';
      if (hidden && S.data && S.built) {
        const count = (rows) => rows.reduce((sum, row) => sum + 1 + count(row.children), 0);
        const base = { ...ctx.api.structureOptions(), query: '', sort: S.sort };
        const without = count(IOView.build(S.data, S.structure, { ...base, [hidden.key]: false }).rows);
        const withIt = count(IOView.build(S.data, S.structure, { ...base, [hidden.key]: true }).rows);
        const n = Math.abs(withIt - without);
        more = `${ctx.ico('i-chev')}<span>${hidden.on ? 'Hide' : 'Show'} ${n} hidden</span><span style="color:var(--io-faint)">· ${ctx.esc(hidden.label)}</span>`;
        s.more.title = `${hidden.on ? 'Hide' : 'Show'} the ${n} rows the ${hidden.label} switch adds`;
        s.more.setAttribute('aria-pressed', String(hidden.on));
      }
      s.more.hidden = !more;
      if (s.html.more !== more) { s.html.more = more; s.more.innerHTML = more; }
      ctx.$('search').closest('.search').classList.toggle('has-query', Boolean(S.query));
      // ---- the pane's Activity: this device's events (the page's own event rows), or every device's ----
      if (paneOpen && s.tab === 'activity') {
        const mine = S.sel && s.scope === 'selection';
        const scope = `<div class="seg small scope" role="radiogroup" aria-label="Scope"><button data-l3-scope="selection" role="radio" class="${mine ? 'on' : ''}">This device</button><button data-l3-scope="all" role="radio" class="${mine ? '' : 'on'}">All</button></div>`;
        ctx.activity.draw(s.pact, { scope: mine ? S.sel : null, title: `Activity · ${mine ? ctx.api.entityLabel(S.sel) : 'all devices'}`, controls: scope, empty: 'Nothing logged for this device yet.' });
      }
    },
  };
  window.IOLayouts = window.IOLayouts || {};
  window.IOLayouts.l3 = layout;
}());
