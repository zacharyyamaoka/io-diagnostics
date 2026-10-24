// Chart view c4 · Pin to my dashboard (lane chart-enlarge, 2026-10-24; contract: charts/core.js).
// A pin on any sparkline (hover its row; or click the sparkline) adds that value's chart as a tile on a "Dashboard" view, the
// person's own monitoring board: a 12-column grid of tiles from any entities, each with a header (name, entity, latest value)
// and a chart with axes and limits. Drag a tile's header to reorder, its corner to resize, x to remove. The board is saved
// with the app's settings (POST /api/v1/settings {dashboard: [...]}, so it follows the person across browsers and ports) and
// mirrored in this browser. Add… on the board lists every value on the entity page you were looking at.
// Precedent: Grafana, Home Assistant and Netdata dashboards; Foxglove's panel layouts.
// WHY the board is a view of its own, not part of the entity page: a monitoring board mixes entities (the Computer's memory
// beside a CAN bus's voltage), so it cannot live under any one of them (Zach: "your own little custom monitoring dashboard").
// The tree work's Visibility mode can pick what is on it through IOChartViews.c4.pin(spec) / unpin(spec).
'use strict';
(function (root) {
  const ID = 'c4';
  const COLS = 12;
  const ROW_PX = 84;
  const GAP = 10;
  const CSS = `
.ch-spark[data-pinned] > .ch-line .ch-btn { opacity: 1; color: var(--io-text-strong); }
.ch-spark[data-pinned] > .ch-line .ch-btn svg path { fill: currentColor; }
body.ch4-on .main { display: none !important; }
.ch4-dash { flex: 1; min-height: 0; display: none; flex-direction: column; background: var(--io-surface-alt); }
body.ch4-on .ch4-dash { display: flex; }
body.ch4-on .seg button.on:not([data-ch4]) { background: transparent; color: var(--io-muted); }
.seg button[data-ch4][data-on] { background: var(--io-pressed); color: var(--io-text-strong); }
.seg button[data-ch4] .ch4-count { margin-left: 5px; font-size: 10.5px; color: var(--io-muted); }
.ch4-bar { display: flex; align-items: center; gap: 10px; padding: 8px 16px; border-bottom: 1px solid var(--io-border); background: var(--io-surface-2); flex: none; }
.ch4-bar h2 { margin: 0; font-size: 14px; font-weight: 600; color: var(--io-text-strong); }
.ch4-hint { color: var(--io-muted); font-size: 12px; }
.ch4-sp { flex: 1; }
.ch4-scroll { flex: 1; min-height: 0; overflow: auto; padding: 14px 16px 24px; }
.ch4-grid { display: grid; grid-template-columns: repeat(${COLS}, minmax(0, 1fr)); grid-auto-rows: ${ROW_PX}px; gap: ${GAP}px; grid-auto-flow: row dense; }
.ch4-tile { position: relative; display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--io-surface); border: 1px solid var(--io-border); border-radius: var(--io-radius-lg); overflow: hidden; }
.ch4-tile.dragging { box-shadow: var(--io-shadow-pop); opacity: .92; z-index: 5; }
.ch4-th { display: flex; align-items: baseline; gap: 8px; padding: 7px 8px 2px 12px; cursor: grab; min-width: 0; touch-action: none; user-select: none; }
.ch4-tile.dragging .ch4-th { cursor: grabbing; }
.ch4-t { font-size: 12.5px; font-weight: 600; color: var(--io-text-strong); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; background: none; border: 0; padding: 0; cursor: pointer; font-family: inherit; text-align: left; }
.ch4-t:hover { text-decoration: underline; }
.ch4-e { font-size: 11px; color: var(--io-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; flex: 1; }
.ch4-v { font-family: var(--io-font-mono); font-size: 17px; color: var(--io-text-strong); font-variant-numeric: tabular-nums; white-space: nowrap; margin-left: auto; }
.ch4-v small { font-size: 11px; color: var(--io-muted); margin-left: 3px; font-family: var(--io-font-ui); }
.ch4-x { border: 0; background: transparent; color: var(--io-faint); cursor: pointer; padding: 2px; border-radius: var(--io-radius-sm); display: inline-flex; align-self: center; }
.ch4-x:hover { background: var(--io-control-hover); color: var(--io-text); }
.ch4-body { position: relative; flex: 1; min-height: 0; }
.ch4-body .chp-fill { right: 14px; bottom: 2px; }
.ch4-rs { position: absolute; right: 0; bottom: 0; width: 18px; height: 18px; cursor: nwse-resize; touch-action: none; z-index: 3; }
.ch4-rs::before { content: ""; position: absolute; right: 4px; bottom: 4px; width: 8px; height: 8px; border-right: 2px solid var(--io-faint); border-bottom: 2px solid var(--io-faint); border-radius: 0 0 2px 0; opacity: .7; }
.ch4-empty { margin: 8vh auto 0; max-width: 460px; text-align: center; color: var(--io-muted); font-size: 13px; line-height: 1.55; }
.ch4-empty b { color: var(--io-text-strong); }
.ch4-add { position: relative; }
.ch4-menu { position: fixed; z-index: 70; width: 360px; max-height: 60vh; overflow: auto; padding: 4px; background: var(--io-popover); border: 1px solid var(--io-border); border-radius: var(--io-radius-lg); box-shadow: var(--io-shadow-pop); font-size: 12.5px; }
.ch4-menu h6 { margin: 6px 8px 4px; font-size: 11px; font-weight: 500; color: var(--io-faint); }
.ch4-menu button { display: flex; width: 100%; justify-content: space-between; gap: 10px; text-align: left; background: transparent; border: 0; padding: 5px 8px; border-radius: var(--io-radius-sm); cursor: pointer; color: var(--io-text); }
.ch4-menu button:hover, .ch4-menu button:focus-visible { background: var(--io-hover); }
.ch4-menu button span:last-child { color: var(--io-faint); font-size: 11px; }
.ch4-toast { position: fixed; left: 16px; bottom: 46px; z-index: 80; display: flex; gap: 10px; align-items: center; padding: 7px 12px; background: var(--io-popover); color: var(--io-text); border: 1px solid var(--io-border); border-radius: var(--io-radius); box-shadow: var(--io-shadow-pop); font-size: 12.5px; }
.ch4-toast button { background: transparent; border: 0; padding: 0; color: var(--io-accent-text); cursor: pointer; font: inherit; text-decoration: underline; }
`;
  let env = null;
  let tiles = [];
  let view = false; // the Dashboard view is showing
  let adopted = false; // the server's copy has been looked at once
  let dash = null; let button = null; let menu = null; let toast = null; let toastTimer = 0; let saveTimer = 0;
  const plots = new Map(); // tile id -> Plot
  const sigs = new Map();

  const idOf = (spec) => env.specKey(spec);
  const find = (spec) => tiles.find((tile) => tile.key === idOf(spec));
  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

  // ---- persistence: the app's settings, mirrored in this browser ----
  function save() {
    env.store.setJson('c4.tiles', tiles);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      if (!env.ctx || !env.ctx.saveSettings) return;
      env.ctx.saveSettings({ dashboard: tiles }).catch((error) => { console.warn('dashboard not saved to the settings (kept in this browser):', error); });
    }, 350);
  }
  function adopt() {
    if (adopted) return;
    const settings = env.ctx.settings && env.ctx.settings();
    if (!settings) return;
    adopted = true;
    if (Array.isArray(settings.dashboard) && settings.dashboard.length) { tiles = settings.dashboard.filter((tile) => tile && tile.spec && tile.key); env.store.setJson('c4.tiles', tiles); legend(); }
    else if (tiles.length) save(); // this browser had a board and the host none: the host now keeps it
  }

  // ---- pin / unpin ----
  function pin(spec) {
    if (find(spec)) return;
    tiles.push({ key: idOf(spec), id: `t${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`, spec, w: spec.s && spec.s.length > 1 ? 6 : 4, h: 3 });
    save(); refresh(); redraw();
    flash(spec);
  }
  function unpin(spec) {
    tiles = tiles.filter((tile) => tile.key !== idOf(spec));
    save(); refresh(); redraw();
  }
  const redraw = () => { if (env.ctx && env.ctx.redraw) env.ctx.redraw(); };
  function flash(spec) {
    clearTimeout(toastTimer);
    if (toast) toast.remove();
    toast = document.createElement('div');
    toast.className = 'ch4-toast';
    toast.setAttribute('role', 'status');
    toast.innerHTML = `<span>Pinned <b>${env.esc(spec.l || 'a value')}</b> to the dashboard</span><button type="button" data-open>Open dashboard</button>`;
    toast.querySelector('button').addEventListener('click', () => { show(true); toast.remove(); toast = null; });
    document.body.appendChild(toast);
    toastTimer = setTimeout(() => { if (toast) { toast.remove(); toast = null; } }, 4000);
  }

  // ---- the Dashboard view ----
  function build() {
    if (dash) return dash;
    dash = document.createElement('section');
    dash.className = 'ch4-dash';
    dash.id = 'ch4-dash';
    dash.setAttribute('aria-label', 'Dashboard');
    dash.innerHTML = '<div class="ch4-bar"><h2>Dashboard</h2><span class="ch4-hint"></span><span class="ch4-sp"></span>'
      + '<button type="button" class="chc-btn ch4-add" data-add>Add from this page…</button><button type="button" class="chc-btn" data-clear>Clear</button></div>'
      + '<div class="ch4-scroll"><div class="ch4-grid"></div></div>';
    dash.addEventListener('click', (event) => {
      const add = event.target.closest('[data-add]'); const clear = event.target.closest('[data-clear]');
      const x = event.target.closest('[data-x]'); const title = event.target.closest('[data-go]');
      if (add) { openMenu(add); return; }
      if (clear) { if (tiles.length && confirm(`Remove all ${tiles.length} tiles from the dashboard?`)) { tiles = []; save(); refresh(); redraw(); } return; }
      if (x) { const tile = tiles.find((t) => t.id === x.dataset.x); if (tile) unpin(tile.spec); return; }
      if (title) { const tile = tiles.find((t) => t.id === title.dataset.go); if (tile && env.ctx.openEntity) { show(false); env.ctx.openEntity(tile.spec.p); } }
    });
    wireDrag(dash.querySelector('.ch4-grid'));
    return dash;
  }
  function layout() {
    const main = document.getElementById('main');
    if (!main) return;
    build();
    if (dash.previousElementSibling !== main) main.after(dash);
  }
  function show(on) {
    view = on;
    env.store.set('c4.view', on ? '1' : '0');
    document.body.classList.toggle('ch4-on', on);
    if (button) { if (on) button.setAttribute('data-on', ''); else button.removeAttribute('data-on'); button.setAttribute('aria-pressed', String(on)); }
    layout(); refresh();
  }

  function legend() { // the count on the toolbar button and the board's hint line
    if (button) button.querySelector('.ch4-count').textContent = tiles.length ? String(tiles.length) : '';
    if (dash) dash.querySelector('.ch4-hint').textContent = tiles.length ? `${tiles.length} tile${tiles.length === 1 ? '' : 's'} · drag a header to reorder, a corner to resize · saved with the app's settings` : '';
  }
  function refresh() {
    legend();
    if (!dash) return;
    const grid = dash.querySelector('.ch4-grid');
    const sig = JSON.stringify(tiles.map((t) => [t.id, t.w, t.h]));
    if (sigs.get('grid') !== sig) {
      sigs.set('grid', sig);
      for (const [id, plot] of plots) if (!tiles.some((t) => t.id === id)) { plot.destroy(); plots.delete(id); }
      const existing = new Map([...grid.querySelectorAll('.ch4-tile')].map((node) => [node.dataset.id, node]));
      tiles.forEach((tile, at) => {
        let node = existing.get(tile.id);
        if (!node) {
          node = document.createElement('article');
          node.className = 'ch4-tile';
          node.dataset.id = tile.id;
          node.innerHTML = `<header class="ch4-th" data-handle><button type="button" class="ch4-t" data-go="${env.esc(tile.id)}" title="Go to this entity"></button><span class="ch4-e"></span><span class="ch4-v"></span>`
            + `<button type="button" class="ch4-x" data-x="${env.esc(tile.id)}" title="Remove from the dashboard" aria-label="Remove">${env.icon('close')}</button></header>`
            + '<div class="ch4-body"></div><div class="ch4-rs" data-rs title="Drag to resize"></div>';
          plots.set(tile.id, new env.Plot(node.querySelector('.ch4-body'), { height: 'fill', area: true, tip: true }));
        }
        node.style.gridColumn = `span ${clamp(tile.w, 3, COLS)}`;
        node.style.gridRow = `span ${clamp(tile.h, 2, 10)}`;
        if (grid.children[at] !== node) grid.insertBefore(node, grid.children[at] || null);
        existing.delete(tile.id);
      });
      for (const stale of existing.values()) stale.remove();
      let empty = dash.querySelector('.ch4-empty');
      if (!tiles.length) {
        if (!empty) { empty = document.createElement('div'); empty.className = 'ch4-empty'; grid.after(empty); }
        empty.innerHTML = '<b>Your dashboard is empty.</b><br>Hover any sparkline on an entity page and press its pin, or click the sparkline. Tiles from different entities sit side by side here; <b>Add from this page…</b> lists everything the page you were on shows.';
      } else if (empty) empty.remove();
    }
    if (!view) return;
    for (const tile of tiles) {
      const node = grid.querySelector(`.ch4-tile[data-id="${tile.id}"]`);
      if (!node) continue;
      const model = env.resolve(tile.spec, { everyMs: 2000 });
      const lead = model.series[0];
      node.querySelector('.ch4-t').textContent = tile.spec.l || (lead ? lead.key : 'Series');
      node.querySelector('.ch4-e').textContent = [env.ctx.nameOf ? env.ctx.nameOf(tile.spec.p) : tile.spec.p, lead && lead.status !== tile.spec.l ? lead.status : ''].filter(Boolean).join(' · ');
      node.querySelector('.ch4-v').innerHTML = model.last ? `${env.esc(env.num(model.last.v))}<small>${env.esc(env.unitText(lead.unit))}</small>` : '—';
      const plot = plots.get(tile.id);
      if (plot) plot.set(env.plotModel(model, { levels: false }));
    }
  }

  // ---- drag to reorder, drag the corner to resize ----
  function wireDrag(grid) {
    let drag = null;
    grid.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      const tileNode = event.target.closest('.ch4-tile');
      if (!tileNode) return;
      const tile = tiles.find((t) => t.id === tileNode.dataset.id);
      if (!tile) return;
      if (event.target.closest('[data-rs]')) {
        const r = tileNode.getBoundingClientRect();
        drag = { kind: 'resize', tile, node: tileNode, left: r.left, top: r.top, id: event.pointerId, el: event.target.closest('[data-rs]') };
      } else if (event.target.closest('[data-handle]') && !event.target.closest('button')) {
        drag = { kind: 'move', tile, node: tileNode, id: event.pointerId, el: event.target.closest('[data-handle]'), moved: false, x: event.clientX, y: event.clientY };
      } else return;
      drag.el.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    grid.addEventListener('pointermove', (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      if (drag.kind === 'resize') {
        const g = grid.getBoundingClientRect();
        const col = (g.width - GAP * (COLS - 1)) / COLS;
        drag.tile.w = clamp(Math.round((event.clientX - drag.left + GAP) / (col + GAP)), 3, COLS);
        drag.tile.h = clamp(Math.round((event.clientY - drag.top + GAP) / (ROW_PX + GAP)), 2, 10);
        drag.node.style.gridColumn = `span ${drag.tile.w}`;
        drag.node.style.gridRow = `span ${drag.tile.h}`;
        return;
      }
      if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 6) return;
      if (!drag.moved) { drag.moved = true; drag.node.classList.add('dragging'); }
      // the tile under the pointer (the dragged one is under it too: look past it)
      drag.node.style.pointerEvents = 'none';
      const under = document.elementFromPoint(event.clientX, event.clientY);
      drag.node.style.pointerEvents = '';
      const other = under && under.closest ? under.closest('.ch4-tile') : null;
      if (!other || other === drag.node) return;
      const from = tiles.indexOf(drag.tile); const to = tiles.findIndex((t) => t.id === other.dataset.id);
      if (to < 0 || from === to) return;
      tiles.splice(to, 0, tiles.splice(from, 1)[0]);
      sigs.delete('grid'); refresh();
    });
    const end = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      drag.node.classList.remove('dragging');
      try { drag.el.releasePointerCapture(drag.id); } catch (_) { /* gone */ }
      const changed = drag.kind === 'resize' || drag.moved;
      drag = null;
      if (changed) { sigs.delete('grid'); save(); refresh(); }
    };
    grid.addEventListener('pointerup', end);
    grid.addEventListener('pointercancel', end);
  }

  // ---- Add from this page… ----
  function closeMenu() { if (menu) { menu.remove(); menu = null; } }
  function openMenu(anchor) {
    closeMenu();
    // the values the page you were on shows: every sparkline wrapper in the detail (the board hides it, but it is still drawn)
    const seen = new Map();
    for (const node of document.querySelectorAll('#detail .ch-spark')) { const spec = env.specOf(node); if (spec) seen.set(idOf(spec), spec); }
    menu = document.createElement('div');
    menu.className = 'ch4-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `<h6>${seen.size ? `On ${env.esc(env.ctx.nameOf ? env.ctx.nameOf(env.ctx.selected()) || '' : '')} (${seen.size})` : 'No sparkline on the page you were on'}</h6>`
      + [...seen.values()].map((spec) => `<button type="button" role="menuitem" data-k="${env.esc(idOf(spec))}"><span>${env.esc(spec.l || spec.s[0][1])}</span><span>${find(spec) ? 'pinned ✓' : 'pin'}</span></button>`).join('');
    document.body.appendChild(menu);
    const r = anchor.getBoundingClientRect();
    menu.style.top = `${r.bottom + 4}px`;
    menu.style.left = `${Math.max(8, Math.min(r.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8))}px`;
    menu.addEventListener('click', (event) => {
      const item = event.target.closest('[data-k]');
      if (!item) return;
      const spec = seen.get(item.dataset.k);
      if (spec) { if (find(spec)) unpin(spec); else pin(spec); }
      closeMenu();
    });
  }

  root.IOChartViews = root.IOChartViews || {};
  root.IOChartViews[ID] = {
    name: 'Pin to my dashboard',
    description: 'Pin any sparkline: its chart becomes a tile on a Dashboard view, your own monitoring board mixing entities, resizable and reorderable, saved with the app settings.',
    order: 4,
    mount(shell) {
      env = shell;
      env.addStyle(ID, CSS);
      tiles = (env.store.json('c4.tiles', []) || []).filter((tile) => tile && tile.spec && tile.key);
      adopted = false;
      const views = document.getElementById('views');
      button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('data-ch4', '');
      button.innerHTML = 'Dashboard<span class="ch4-count"></span>';
      views.appendChild(button);
      button.addEventListener('click', () => show(!view));
      // the other views close the board (app.js switches the view itself)
      views.addEventListener('click', onViews, true);
      layout();
      show(env.store.get('c4.view') === '1');
    },
    unmount() {
      closeMenu();
      if (toast) { toast.remove(); toast = null; }
      document.body.classList.remove('ch4-on');
      for (const plot of plots.values()) plot.destroy();
      plots.clear(); sigs.clear();
      if (dash) { dash.remove(); dash = null; }
      const views = document.getElementById('views');
      if (views) views.removeEventListener('click', onViews, true);
      if (button) { button.remove(); button = null; }
      view = false;
    },
    attrs: (spec) => (find(spec) ? ' data-pinned' : ''),
    tip: (spec) => (find(spec) ? 'On your dashboard · click to unpin' : 'Pin to your dashboard'),
    icon: () => 'pin',
    btnOn: (spec) => Boolean(find(spec)),
    pin, unpin,
    on(kind, event, hit) {
      if (kind === 'keydown') { if (event.key === 'Escape' && menu) { closeMenu(); return true; } return false; }
      if (kind === 'click') {
        if (menu && !menu.contains(event.target) && !(event.target.closest && event.target.closest('[data-add]'))) closeMenu();
        if (!hit.el || !hit.spec) return false;
        if (find(hit.spec)) unpin(hit.spec); else pin(hit.spec);
        return true;
      }
      return false;
    },
    update() { adopt(); refresh(); if (button && view) button.setAttribute('data-on', ''); },
    sync() {},
  };
  function onViews(event) {
    const clicked = event.target.closest('button');
    if (clicked && !clicked.hasAttribute('data-ch4') && view) show(false);
  }
})(typeof window !== 'undefined' ? window : globalThis);
