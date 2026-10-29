// Chart view c3 · Plot pane (lane chart-enlarge, 2026-10-24; contract: charts/core.js).
// tview's own model (moteus_gui tview.py: right-click a telemetry leaf -> "Plot Left" / "Plot Right"; one plot widget with a
// left and a right axis, a legend, a history length). Here: click a sparkline (or right-click: Plot Left / Plot Right) and it
// joins ONE docked plot pane; several series from any entities overlay, each with its colour, a legend with the value under
// the cursor (else the latest), and an axis chip you click to move it. The pane docks at the bottom or the right, its edge is
// draggable and the size is remembered per dock; closing it keeps the series.
// Precedent beyond tview: PlotJuggler (drag signals onto one plot, a legend with values at the cursor) and Foxglove's Plot panel.
// WHY mixed units pick their own axis: the first series is Left; a click on a series of another unit goes Right when that is free
// (tview makes you choose; a click must not stack a temperature on a percentage).
'use strict';
(function (root) {
  const ID = 'c3';
  const DEFAULT_SIZE = { bottom: 290, right: 520 };
  const CSS = `
.ch-spark[data-ch3-i] > .ch-line { box-shadow: inset 3px 0 0 var(--ch3-c, var(--io-text)); border-radius: 0 !important; }
.ch-spark[data-ch3-i="0"] { --ch3-c: var(--io-text); } .ch-spark[data-ch3-i="1"] { --ch3-c: var(--io-info); }
.ch-spark[data-ch3-i="2"] { --ch3-c: oklch(from var(--io-info) l c calc(h + 58)); } .ch-spark[data-ch3-i="3"] { --ch3-c: oklch(from var(--io-info) l c calc(h - 58)); }
.ch-spark[data-ch3-i="4"] { --ch3-c: var(--io-muted); } .ch-spark[data-ch3-i="5"] { --ch3-c: var(--io-faint); }
.ch3-pane { position: relative; flex: none; display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--io-surface); }
.ch3-pane[data-dock="bottom"] { border-top: 1px solid var(--io-border); }
.ch3-pane[data-dock="right"] { border-left: 1px solid var(--io-border); }
.ch3-sash { position: absolute; z-index: 4; touch-action: none; outline: none; }
.ch3-pane[data-dock="bottom"] .ch3-sash { top: -4px; left: 0; right: 0; height: 7px; cursor: row-resize; }
.ch3-pane[data-dock="right"] .ch3-sash { left: -4px; top: 0; bottom: 0; width: 7px; cursor: col-resize; }
.ch3-sash::before { content: ""; position: absolute; background: transparent; transition: background .1s; }
.ch3-pane[data-dock="bottom"] .ch3-sash::before { left: 0; right: 0; top: 2px; height: 3px; }
.ch3-pane[data-dock="right"] .ch3-sash::before { top: 0; bottom: 0; left: 2px; width: 3px; }
.ch3-sash:hover::before, .ch3-sash:focus-visible::before, .ch3-pane.sizing .ch3-sash::before { background: var(--io-selected-line); }
body.ch3-sizing-row { cursor: row-resize; user-select: none; } body.ch3-sizing-col { cursor: col-resize; user-select: none; }
.ch3-bar { display: flex; align-items: center; gap: 8px; padding: 5px 8px 5px 14px; border-bottom: 1px solid var(--io-border); background: var(--io-surface-2); flex: none; }
.ch3-title { font-weight: 600; color: var(--io-text-strong); font-size: 12.5px; }
.ch3-win, .ch3-n { color: var(--io-muted); font-size: 11.5px; }
.ch3-sp { flex: 1; }
.ch3-body { flex: 1; min-height: 0; display: flex; }
.ch3-pane[data-dock="right"] .ch3-body { flex-direction: column; }
.ch3-plot { position: relative; flex: 1; min-width: 0; min-height: 0; padding: 4px 6px 0 0; }
.ch3-leg { flex: none; overflow: auto; padding: 6px 8px; min-width: 0; }
.ch3-pane[data-dock="bottom"] .ch3-leg { width: 290px; border-left: 1px solid var(--io-border-soft); }
.ch3-pane[data-dock="right"] .ch3-leg { max-height: 40%; border-top: 1px solid var(--io-border-soft); }
.ch3-row { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto auto auto; gap: 0 8px; align-items: center; padding: 3px 0; font-size: 12px; }
.ch3-row .nm { min-width: 0; overflow: hidden; }
.ch3-row .nm b { display: block; font-weight: 500; color: var(--io-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch3-row .nm small { display: block; color: var(--io-faint); font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch3-row .v { font-family: var(--io-font-mono); color: var(--io-text-strong); font-variant-numeric: tabular-nums; white-space: nowrap; text-align: right; }
.ch3-row .v small { color: var(--io-muted); font-family: var(--io-font-ui); margin-left: 2px; }
.ch3-ax { border: 1px solid var(--io-border); background: var(--io-field); color: var(--io-muted); border-radius: var(--io-radius-sm); font-size: 10.5px; padding: 0 5px; cursor: pointer; font-family: var(--io-font-mono); }
.ch3-ax:hover { color: var(--io-text); background: var(--io-control-hover); }
.ch3-rm { border: 0; background: transparent; color: var(--io-faint); cursor: pointer; padding: 2px; border-radius: var(--io-radius-sm); display: inline-flex; }
.ch3-rm:hover { color: var(--io-text); background: var(--io-control-hover); }
.ch3-empty { padding: 18px 14px; color: var(--io-muted); font-size: 12.5px; line-height: 1.5; max-width: 52ch; }
.ch3-empty b { color: var(--io-text-strong); font-weight: 600; }
.ch3-menu { position: fixed; z-index: 70; min-width: 190px; padding: 4px; background: var(--io-popover); color: var(--io-text); border: 1px solid var(--io-border); border-radius: var(--io-radius-lg); box-shadow: var(--io-shadow-pop); font-size: 12.5px; }
.ch3-menu h6 { margin: 4px 8px 4px; font-size: 11px; font-weight: 500; color: var(--io-faint); max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch3-menu button { display: flex; width: 100%; align-items: center; gap: 8px; text-align: left; background: transparent; border: 0; padding: 5px 8px; border-radius: var(--io-radius-sm); cursor: pointer; color: var(--io-text); }
.ch3-menu button:hover, .ch3-menu button:focus-visible { background: var(--io-hover); }
.ch3-menu hr { border: 0; border-top: 1px solid var(--io-border-soft); margin: 4px 0; }
.main[data-ch3="right"].v-tree .left { flex: 0 1 var(--tree-w, 470px); min-width: 220px; }
.main[data-ch3="right"].v-tree .inspector { min-width: 300px; }
.main[data-ch3="right"].v-table .left { min-width: 300px; }
#ch3-btn { position: relative; }
#ch3-btn .ch3-count { font-size: 10px; margin-left: 3px; color: var(--io-muted); }
`;
  let env = null;
  let state = { series: [], dock: 'bottom', open: false };
  let pane = null; let plot = null; let menu = null; let button = null; let legendSig = '';
  let cursorValues = null; let paused = false;

  const sizeKey = (dock) => `c3.size.${dock}`;
  const idOf = (spec, row) => JSON.stringify([spec.p, row[0], row[1]]);
  // one entry per measurement of a wrapper (a CPU row has an average and a max)
  const singles = (spec) => (spec.s || []).map((row) => ({ id: idOf(spec, row), spec: { p: spec.p, l: spec.l, s: [[row[0], row[1], '']] },
    name: (spec.s.length > 1 ? `${spec.l} · ${row[1]}` : spec.l) || row[1], sub: row[0] }));
  const find = (id) => state.series.find((s) => s.id === id);
  const persist = () => { env.store.setJson('c3.series', state.series); env.store.set('c3.dock', state.dock); env.store.set('c3.open', state.open ? '1' : '0'); };
  const unitOf = (entry) => { const m = env.resolve(entry.spec, { everyMs: 3000 }); return m.series[0] ? m.series[0].unit : null; };
  const freeClass = () => { for (let i = 0; i < 6; i += 1) if (!state.series.some((s) => s.cls === i)) return i; return state.series.length % 6; };
  const redraw = () => { if (env.ctx && env.ctx.redraw) env.ctx.redraw(); };

  function smartAxis(entry) {
    const left = state.series.find((s) => s.axis === 'L'); const right = state.series.find((s) => s.axis === 'R');
    const unit = unitOf(entry);
    if (!left || unitOf(left) === unit) return 'L';
    if (!right || unitOf(right) === unit) return 'R';
    return 'L';
  }
  function add(spec, axis) {
    for (const one of singles(spec)) {
      const known = find(one.id);
      if (known) { if (axis) known.axis = axis; continue; }
      state.series.push({ ...one, axis: axis || smartAxis(one), cls: freeClass() });
    }
    state.open = true;
    persist(); layout(); redraw(); update();
  }
  function remove(ids) {
    state.series = state.series.filter((s) => !ids.includes(s.id));
    persist(); legendSig = ''; redraw(); update();
  }
  function toggle(spec) {
    const ones = singles(spec);
    if (ones.every((one) => find(one.id))) remove(ones.map((one) => one.id)); else add(spec, null);
  }

  // ---- the pane ----
  function ensurePane() {
    if (pane) return pane;
    pane = document.createElement('section');
    pane.className = 'ch3-pane';
    pane.id = 'ch3-pane';
    pane.setAttribute('aria-label', 'Plot');
    pane.innerHTML = '<div class="ch3-sash" role="separator" tabindex="0" title="Drag to resize · double-click to reset"></div>'
      + '<div class="ch3-bar"><span class="ch3-title">Plot</span><span class="ch3-win"></span><span class="ch3-n"></span><span class="ch3-sp"></span>'
      + '<button type="button" class="chc-btn" data-act="pause">Pause</button>'
      + `<button type="button" class="chc-btn icon" data-act="dock" title="Dock at the right / at the bottom">${env.icon('dockr')}</button>`
      + `<button type="button" class="chc-btn icon" data-act="clear" title="Remove every series">${env.icon('trash')}</button>`
      + `<button type="button" class="chc-btn icon" data-act="close" title="Close the pane (the series are kept)">${env.icon('close')}</button></div>`
      + '<div class="ch3-body"><div class="ch3-plot"></div><div class="ch3-leg"></div></div>';
    plot = new env.Plot(pane.querySelector('.ch3-plot'), { height: 'fill', tip: true, onCursor: (t, rows) => { cursorValues = t === null ? null : new Map(rows.map((r) => [r.series.id, r.v])); legendValues(); } });
    pane.querySelector('.ch3-bar').addEventListener('click', (event) => {
      const act = event.target.closest('[data-act]');
      if (!act) return;
      event.stopPropagation();
      if (act.dataset.act === 'close') { state.open = false; persist(); layout(); }
      else if (act.dataset.act === 'clear') remove(state.series.map((s) => s.id));
      else if (act.dataset.act === 'dock') { state.dock = state.dock === 'bottom' ? 'right' : 'bottom'; persist(); layout(); }
      else if (act.dataset.act === 'pause') { paused = !paused; act.classList.toggle('on', paused); act.textContent = paused ? 'Resume' : 'Pause'; if (!paused) update(); }
    });
    pane.querySelector('.ch3-leg').addEventListener('click', (event) => {
      const rm = event.target.closest('[data-rm]'); const ax = event.target.closest('[data-ax]');
      if (rm) remove([rm.dataset.rm]);
      if (ax) { const s = find(ax.dataset.ax); if (s) { s.axis = s.axis === 'L' ? 'R' : 'L'; persist(); legendSig = ''; redraw(); update(); } }
    });
    wireSash(pane.querySelector('.ch3-sash'));
    return pane;
  }
  const clampSize = (dock, px) => {
    const main = document.getElementById('main').getBoundingClientRect();
    return dock === 'bottom' ? Math.min(Math.max(150, px), Math.max(150, main.height * 0.7)) : Math.min(Math.max(260, px), Math.max(260, main.width - 680));
  };
  function applySize() {
    if (!pane) return;
    const px = clampSize(state.dock, Number(env.store.get(sizeKey(state.dock))) || DEFAULT_SIZE[state.dock]);
    pane.style.height = state.dock === 'bottom' ? `${px}px` : '';
    pane.style.width = state.dock === 'right' ? `${px}px` : '';
  }
  function wireSash(sash) {
    let drag = null;
    sash.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      drag = { id: event.pointerId };
      sash.setPointerCapture(event.pointerId);
      pane.classList.add('sizing');
      document.body.classList.add(state.dock === 'bottom' ? 'ch3-sizing-row' : 'ch3-sizing-col');
      event.preventDefault();
    });
    sash.addEventListener('pointermove', (event) => {
      if (!drag) return;
      const rect = pane.getBoundingClientRect();
      drag.px = clampSize(state.dock, state.dock === 'bottom' ? rect.bottom - event.clientY : rect.right - event.clientX);
      pane.style.height = state.dock === 'bottom' ? `${drag.px}px` : '';
      pane.style.width = state.dock === 'right' ? `${drag.px}px` : '';
    });
    const end = () => {
      if (!drag) return;
      if (drag.px) env.store.set(sizeKey(state.dock), String(Math.round(drag.px)));
      drag = null;
      pane.classList.remove('sizing');
      document.body.classList.remove('ch3-sizing-row', 'ch3-sizing-col');
    };
    sash.addEventListener('pointerup', end);
    sash.addEventListener('pointercancel', end);
    sash.addEventListener('dblclick', () => { env.store.set(sizeKey(state.dock), String(DEFAULT_SIZE[state.dock])); applySize(); });
    sash.addEventListener('keydown', (event) => {
      const grow = { bottom: { ArrowUp: 16, ArrowDown: -16 }, right: { ArrowLeft: 16, ArrowRight: -16 } }[state.dock][event.key];
      if (!grow) return;
      const px = clampSize(state.dock, (Number(env.store.get(sizeKey(state.dock))) || DEFAULT_SIZE[state.dock]) + grow);
      env.store.set(sizeKey(state.dock), String(px));
      applySize();
      event.preventDefault();
    });
  }
  // put the pane where it docks (or take it away): bottom = under the main area, right = inside it, after the inspector
  function layout() {
    const main = document.getElementById('main');
    if (!main) return;
    if (button) { button.classList.toggle('on', state.open); button.querySelector('.ch3-count').textContent = state.series.length ? String(state.series.length) : ''; }
    if (!state.open) { if (pane) pane.remove(); delete main.dataset.ch3; return; }
    ensurePane();
    pane.dataset.dock = state.dock;
    pane.querySelector('[data-act="dock"]').innerHTML = env.icon(state.dock === 'bottom' ? 'dockr' : 'dock');
    if (state.dock === 'bottom') { main.after(pane); delete main.dataset.ch3; } else { main.appendChild(pane); main.dataset.ch3 = 'right'; }
    applySize();
  }

  // ---- data ----
  function legendValues() {
    if (!pane) return;
    for (const row of pane.querySelectorAll('.ch3-row')) {
      const id = row.dataset.id;
      const slot = row.querySelector('.v');
      const model = env.resolve(find(id) ? find(id).spec : { p: '', s: [] }, { everyMs: 2000 });
      const series = model.series[0];
      const value = cursorValues && cursorValues.has(id) ? cursorValues.get(id) : (model.last ? model.last.v : null);
      slot.innerHTML = value === null || !series ? '—' : `${env.esc(env.num(value))}<small>${env.esc(env.unitText(series.unit))}</small>`;
    }
  }
  function update() {
    // WHY: off-screen charts keep their model and defer history/plot work until visible.
    if (!pane || !state.open || !env.showing(pane)) return;
    const bar = pane.querySelector('.ch3-bar');
    bar.querySelector('.ch3-n').textContent = state.series.length ? `${state.series.length} series` : '';
    const models = state.series.map((entry) => ({ entry, model: env.resolve(entry.spec, { everyMs: 2000 }) }));
    const known = models.filter((x) => x.model.series.length);
    const to = Math.max(0, ...known.map((x) => x.model.to));
    const win = Math.max(0, ...known.map((x) => x.model.window_s || (x.model.to - x.model.from)));
    bar.querySelector('.ch3-win').textContent = win ? `last ${env.spanText(win)}` : '';
    const sig = JSON.stringify(state.series.map((s) => [s.id, s.axis, s.cls, s.name]));
    const leg = pane.querySelector('.ch3-leg');
    if (sig !== legendSig) {
      legendSig = sig;
      leg.innerHTML = state.series.map((s) => `<div class="ch3-row" data-id="${env.esc(s.id)}"><i class="chp-sw chp-sw${s.cls % 6}"></i>`
        + `<span class="nm"><b title="${env.esc(`${s.name} · ${s.sub}`)}">${env.esc(s.name)}</b><small>${env.esc(`${env.ctx.nameOf ? env.ctx.nameOf(s.spec.p) : s.spec.p} · ${s.sub}`)}</small></span>`
        + `<span class="v">—</span><button type="button" class="ch3-ax" data-ax="${env.esc(s.id)}" title="Move to the ${s.axis === 'L' ? 'right' : 'left'} axis">${s.axis === 'L' ? 'Left' : 'Right'}</button>`
        + `<button type="button" class="ch3-rm" data-rm="${env.esc(s.id)}" title="Remove from the plot" aria-label="Remove ${env.esc(s.name)}">${env.icon('close')}</button></div>`).join('');
    }
    pane.querySelector('.ch3-plot').style.display = state.series.length ? '' : 'none';
    let empty = pane.querySelector('.ch3-empty');
    if (!state.series.length) {
      if (!empty) { empty = document.createElement('div'); empty.className = 'ch3-empty'; pane.querySelector('.ch3-body').prepend(empty); }
      empty.innerHTML = '<b>Nothing plotted yet.</b><br>Click any sparkline to plot it here, or right-click it for <b>Plot Left</b> / <b>Plot Right</b>. Series from different entities overlay on one time axis.';
    } else if (empty) empty.remove();
    leg.style.display = state.series.length ? '' : 'none';
    if (paused) return;
    if (state.series.length) {
      plot.set({ from: to - win, to, levels: null,
        series: models.flatMap(({ entry, model }) => (model.series[0] ? [{ ...model.series[0], id: entry.id, label: entry.name, cls: entry.cls, axis: entry.axis, dim: false }] : [])) });
    }
    legendValues();
  }

  // ---- the right-click menu ----
  function closeMenu() { if (menu) { menu.remove(); menu = null; } }
  function openMenu(x, y, spec) {
    closeMenu();
    const ones = singles(spec);
    const plotted = ones.filter((one) => find(one.id));
    menu = document.createElement('div');
    menu.className = 'ch3-menu';
    menu.setAttribute('role', 'menu');
    const left = state.series.some((s) => ones.some((one) => one.id === s.id) && s.axis === 'L');
    menu.innerHTML = `<h6>${env.esc(spec.l || '')}${ones.length > 1 ? ` · ${ones.length} series` : ''}</h6>`
      + `<button type="button" role="menuitem" data-m="L">Plot Left${left ? ' ✓' : ''}</button>`
      + `<button type="button" role="menuitem" data-m="R">Plot Right${plotted.some((one) => find(one.id).axis === 'R') ? ' ✓' : ''}</button>`
      + (plotted.length ? '<button type="button" role="menuitem" data-m="rm">Remove from the plot</button>' : '')
      + (state.series.length ? '<hr><button type="button" role="menuitem" data-m="clear">Clear the plot</button>' : '')
      + (state.open ? '' : '<hr><button type="button" role="menuitem" data-m="show">Show the plot pane</button>');
    document.body.appendChild(menu);
    menu.style.left = `${Math.min(x, innerWidth - menu.offsetWidth - 8)}px`;
    menu.style.top = `${Math.min(y, innerHeight - menu.offsetHeight - 8)}px`;
    menu.addEventListener('click', (event) => {
      const item = event.target.closest('[data-m]');
      if (!item) return;
      event.stopPropagation();
      const what = item.dataset.m;
      closeMenu();
      if (what === 'L' || what === 'R') add(spec, what);
      else if (what === 'rm') remove(ones.map((one) => one.id));
      else if (what === 'clear') remove(state.series.map((s) => s.id));
      else if (what === 'show') { state.open = true; persist(); layout(); update(); }
    });
    const first = menu.querySelector('button'); if (first) first.focus();
  }

  root.IOChartViews = root.IOChartViews || {};
  root.IOChartViews[ID] = {
    name: 'Plot pane',
    description: "tview's model: click a sparkline (or right-click: Plot Left / Plot Right) to add it to one docked, resizable plot pane; several series overlay with a legend and two axes.",
    order: 3,
    mount(shell) {
      env = shell;
      env.addStyle(ID, CSS);
      state = { series: env.store.json('c3.series', []), dock: env.store.get('c3.dock', 'bottom') === 'right' ? 'right' : 'bottom', open: env.store.get('c3.open') === '1' };
      state.series = (Array.isArray(state.series) ? state.series : []).filter((s) => s && s.spec && s.id);
      const bar = document.querySelector('.toolbar');
      button = document.createElement('button');
      button.type = 'button'; button.className = 'vo-btn'; button.id = 'ch3-btn';
      button.title = 'Plot pane: series you plotted from sparklines';
      button.setAttribute('aria-label', 'Plot pane');
      button.innerHTML = `${env.icon('plot', 'ic')}<span class="ch3-count"></span>`;
      button.addEventListener('click', () => { state.open = !state.open; persist(); layout(); update(); });
      const spacer = bar && bar.querySelector('.spacer');
      if (bar && spacer) bar.insertBefore(button, spacer);
      layout(); legendSig = '';
      update();
    },
    unmount() {
      closeMenu();
      if (plot) { plot.destroy(); plot = null; }
      if (pane) { pane.remove(); pane = null; }
      const main = document.getElementById('main'); if (main) delete main.dataset.ch3;
      if (button) { button.remove(); button = null; }
    },
    attrs(spec) {
      const hits = singles(spec).map((one) => find(one.id)).filter(Boolean);
      return hits.length ? ` data-plotted="${hits.map((h) => h.axis).join('')}" data-ch3-i="${hits[0].cls % 6}"` : '';
    },
    tip(spec) { return singles(spec).some((one) => find(one.id)) ? 'In the plot · click removes it, right-click for more' : 'Click to plot it · right-click: Plot Left / Plot Right'; },
    icon: () => 'plot',
    btnOn: (spec) => singles(spec).some((one) => find(one.id)),
    on(kind, event, hit) {
      if (kind === 'keydown') { if (event.key === 'Escape' && menu) { closeMenu(); return true; } return false; }
      if (kind === 'click') {
        if (menu && !menu.contains(event.target)) closeMenu();
        if (!hit.el || !hit.spec) return false;
        toggle(hit.spec);
        return true;
      }
      if (kind === 'contextmenu') {
        if (!hit.el || !hit.spec) { closeMenu(); return false; }
        openMenu(event.clientX, event.clientY, hit.spec);
        return true;
      }
      return false;
    },
    update() { update(); },
    sync() { if (state.open && pane && !pane.isConnected) layout(); },
  };
})(typeof window !== 'undefined' ? window : globalThis);
