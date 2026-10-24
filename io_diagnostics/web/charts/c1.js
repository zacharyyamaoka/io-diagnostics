// Chart view c1 · Peek (lane chart-enlarge, 2026-10-24; contract: charts/core.js).
// Hover a sparkline (or press it, or Tab to its button and press Enter) and a popover chart opens beside it, in place: a larger
// line with a time axis, min / max / now, the status's limits, a cursor readout and a window selector. Hover shows it, a press
// pins it (it stays until Esc, the x, or a press outside). Nothing on the page moves.
// Precedent: the sparkline tooltips of Grafana and Datadog, and the hover cards of GitHub / Notion.
// WHY a popover on the body and not in the row: the detail is redrawn every second; a node inside it would be morphed away.
// WHY the window selector is local: peeking must not change every health bar in the app; it asks /api/v1/history for its own
// `window=` (the host allows up to the retention: the app window, at least 15 min).
'use strict';
(function (root) {
  const ID = 'c1';
  const OPEN_MS = 180;
  const CLOSE_MS = 240;
  const CSS = `
.ch1-pop { position: fixed; z-index: 60; width: 500px; max-width: calc(100vw - 16px); padding: 10px 12px 10px; background: var(--io-popover); color: var(--io-text);
  border: 1px solid var(--io-border); border-radius: var(--io-radius-lg); box-shadow: var(--io-shadow-pop); font-size: 12.5px; }
.ch1-pop.pinned { border-color: var(--io-selected-line); }
.ch1-head { display: flex; align-items: flex-start; gap: 8px; min-width: 0; }
.ch1-who { min-width: 0; display: flex; flex-direction: column; flex: 1; }
.ch1-val { text-align: right; flex: none; }
.ch1-title { font-weight: 600; color: var(--io-text-strong); font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch1-ent { color: var(--io-faint); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch1-now { font-family: var(--io-font-mono); font-size: 20px; line-height: 1.1; color: var(--io-text-strong); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ch1-now small { font-size: 11px; color: var(--io-muted); margin-left: 3px; }
.ch1-at { display: block; text-align: right; font-size: 10.5px; color: var(--io-faint); font-family: var(--io-font-mono); min-height: 14px; }
.ch1-x { border: 0; background: transparent; color: var(--io-muted); cursor: pointer; padding: 2px; border-radius: var(--io-radius-sm); display: none; }
.ch1-pop.pinned .ch1-x { display: inline-flex; }
.ch1-x:hover { background: var(--io-control-hover); color: var(--io-text); }
.ch1-leg { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 2px 0 2px; font-size: 11.5px; color: var(--io-muted); }
.ch1-leg span { display: inline-flex; align-items: center; gap: 5px; }
.ch1-foot { display: flex; align-items: center; gap: 12px; margin-top: 6px; flex-wrap: wrap; }
.ch1-foot .chc-seg { margin-left: auto; }
.ch1-hint { font-size: 10.5px; color: var(--io-faint); }
`;

  let env = null;
  let pop = null; // { el, key, spec, pinned, windowS, plot, parts }
  let openTimer = 0;
  let closeTimer = 0;

  const anchorOf = (key) => [...document.querySelectorAll('.ch-spark')].find((node) => node.getAttribute('data-ch-key') === key) || null;
  const appWindow = () => { const s = env.ctx && env.ctx.settings && env.ctx.settings(); return s ? Number(s.history_window_s) : 900; };
  function choices() {
    const app = appWindow();
    const retention = Math.max(app, 900);
    return [...new Set([60, 300, 900, app].filter((s) => s <= retention))].sort((a, b) => a - b);
  }
  const words = (s) => (s < 3600 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);

  function build(spec, key, anchor) {
    const el = document.createElement('div');
    el.className = 'ch1-pop';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', `${spec.l || 'Series'} chart`);
    el.dataset.ch1 = key;
    el.innerHTML = `<div class="ch1-head"><div class="ch1-who"><span class="ch1-title"></span><span class="ch1-ent"></span></div>`
      + `<div class="ch1-val"><span class="ch1-now"></span><span class="ch1-at"></span></div>`
      + `<button class="ch1-x" type="button" aria-label="Close" title="Close (Esc)">${env.icon('close')}</button></div>`
      + '<div class="ch1-leg" hidden></div><div class="ch1-plot"></div>'
      + '<div class="ch1-foot"><span class="chc-stat ch1-stats"></span><span class="ch1-hint"></span><span class="chc-seg" role="radiogroup" aria-label="Window"></span></div>';
    document.body.appendChild(el);
    const state = { el, key, spec, pinned: false, windowS: null, cursor: null, anchor };
    state.plot = new env.Plot(el.querySelector('.ch1-plot'), { height: 190, area: true, tip: true, onCursor: (t, rows) => { state.cursor = t === null ? null : { t, rows }; showNow(state); } });
    el.addEventListener('pointerenter', () => { clearTimeout(closeTimer); });
    el.addEventListener('pointerleave', () => { if (!state.pinned) scheduleClose(); });
    el.querySelector('.ch1-x').addEventListener('click', (event) => { event.stopPropagation(); close(); });
    el.querySelector('.chc-seg').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-w]');
      if (!button) return;
      const s = Number(button.dataset.w);
      state.windowS = s === appWindow() ? null : s;
      refresh(state);
    });
    return state;
  }

  function showNow(state) {
    const model = state.model;
    if (!model) return;
    const lead = model.series[0];
    const unit = lead ? env.unitText(lead.unit) : '';
    const shown = state.cursor && state.cursor.rows[0] ? { t: state.cursor.t, v: state.cursor.rows[0].v } : model.last;
    state.el.querySelector('.ch1-now').innerHTML = shown ? `${env.esc(env.num(shown.v))}<small>${env.esc(unit)}</small>` : '—';
    state.el.querySelector('.ch1-at').textContent = state.cursor ? `at ${env.clock(state.cursor.t)}` : (shown ? 'now' : '');
  }

  function refresh(state) {
    const model = env.resolve(state.spec, { window: state.windowS || undefined, everyMs: 1500 });
    state.model = model;
    const { el, spec } = state;
    el.querySelector('.ch1-title').textContent = spec.l || (model.series[0] ? model.series[0].key : 'Series');
    const entity = env.ctx && env.ctx.nameOf ? env.ctx.nameOf(spec.p) : spec.p;
    el.querySelector('.ch1-ent').textContent = [entity, model.series[0] && model.series[0].status !== spec.l ? model.series[0].status : ''].filter(Boolean).join(' · ');
    const leg = el.querySelector('.ch1-leg');
    leg.hidden = model.series.length < 2;
    leg.innerHTML = model.series.length < 2 ? '' : model.series.map((s, at) => `<span><i class="chp-sw chp-sw${at % 6}"></i>${env.esc(s.key)}</span>`).join('');
    state.plot.set(env.plotModel(model, { levels: false }));
    // min / max / now over the window of the first series
    const lead = model.series[0];
    const stats = lead ? env.statsOf(lead.points, model.from, model.to) : null;
    el.querySelector('.ch1-stats').innerHTML = stats && stats.n
      ? `min <b>${env.esc(env.num(stats.min))}</b> · max <b>${env.esc(env.num(stats.max))}</b> · now <b data-ch-now>${env.esc(env.num(stats.last.v))}</b> ${env.esc(env.unitText(lead.unit))}`
      : (model.loading ? 'loading…' : 'no data in this window');
    el.querySelector('.ch1-hint').textContent = state.pinned ? 'pinned · Esc closes' : 'press to pin';
    const app = appWindow();
    const seg = el.querySelector('.chc-seg');
    const html = choices().map((s) => `<button type="button" role="radio" data-w="${s}" class="${(state.windowS || app) === s ? 'on' : ''}" aria-checked="${(state.windowS || app) === s}">${words(s)}</button>`).join('');
    if (seg.innerHTML !== html) seg.innerHTML = html;
    showNow(state);
    place(state);
  }

  function place(state) {
    const anchor = (state.anchor && state.anchor.isConnected ? state.anchor : anchorOf(state.key));
    if (!anchor) return;
    state.anchor = anchor;
    const rect = (anchor.querySelector('.ch-line') || anchor).getBoundingClientRect();
    const { el } = state;
    const w = el.offsetWidth; const h = el.offsetHeight;
    let top = rect.bottom + 6;
    if (top + h > innerHeight - 8) top = Math.max(8, rect.top - h - 6);
    top = Math.min(top, Math.max(8, innerHeight - h - 8));
    const left = Math.min(Math.max(8, rect.right - w), innerWidth - w - 8);
    el.style.top = `${Math.round(top)}px`;
    el.style.left = `${Math.round(left)}px`;
  }

  function openFor(hit, pin) {
    clearTimeout(closeTimer);
    if (pop && pop.key === hit.key) { if (pin) pop.pinned = true; pop.el.classList.toggle('pinned', pop.pinned); refresh(pop); return; }
    close();
    pop = build(hit.spec, hit.key, hit.el);
    pop.pinned = Boolean(pin);
    pop.el.classList.toggle('pinned', pop.pinned);
    refresh(pop);
  }
  function close() {
    clearTimeout(openTimer); clearTimeout(closeTimer);
    if (!pop) return;
    pop.plot.destroy();
    pop.el.remove();
    pop = null;
  }
  function scheduleClose() {
    clearTimeout(closeTimer);
    closeTimer = setTimeout(() => { if (pop && !pop.pinned) close(); }, CLOSE_MS);
  }

  root.IOChartViews = root.IOChartViews || {};
  root.IOChartViews[ID] = {
    name: 'Peek',
    description: 'Hover or press a sparkline: a popover chart opens beside it with a time axis, min / max / now, the status limits, a cursor readout and a window selector. Nothing moves.',
    order: 1,
    mount(shell) { env = shell; env.addStyle(ID, CSS); },
    unmount() { close(); },
    tip: () => '', // WHY empty: the row's own "Open ..." tooltip must not appear over the popover
    icon: () => 'expand',
    btnOn: (spec, key) => Boolean(pop && pop.pinned && pop.key === key),
    on(kind, event, hit) {
      if (kind === 'pointerover' && hit.el) {
        clearTimeout(closeTimer);
        if (pop && pop.key === hit.key) return false;
        if (pop && pop.pinned) return false;
        clearTimeout(openTimer);
        openTimer = setTimeout(() => openFor(hit, false), OPEN_MS);
        return false;
      }
      if (kind === 'pointerout' && hit.el) {
        if (event.relatedTarget && hit.el.contains(event.relatedTarget)) return false;
        clearTimeout(openTimer);
        if (pop && !pop.pinned && !(event.relatedTarget && pop.el.contains(event.relatedTarget))) scheduleClose();
        return false;
      }
      if (kind === 'click') {
        if (hit.el) {
          if (pop && pop.key === hit.key && pop.pinned) { close(); return true; }
          openFor(hit, true);
          return true;
        }
        if (pop && pop.pinned && !pop.el.contains(event.target)) close();
        return false;
      }
      if (kind === 'keydown' && event.key === 'Escape' && pop) { close(); return true; }
      return false;
    },
    update() { if (pop) refresh(pop); },
    sync() { if (pop) { if (!anchorOf(pop.key) && !pop.pinned) close(); else place(pop); } },
  };
})(typeof window !== 'undefined' ? window : globalThis);
