// Chart view c2 · Unfold in place (lane chart-enlarge, 2026-10-24; contract: charts/core.js).
// Press a sparkline (or its chevron) and its row unfolds into a full-width chart right there: value axis, time axis, the
// status's warn / error limits as dashed lines, min / max / now and the history window. Press the chart's header to fold it
// again. Rows below slide down; unfolded rows stay unfolded (remembered) while you look at other entities.
// Precedent: GNOME System Monitor's Resources tab (one section per resource, each a real chart) and rqt_plot's per-topic rows.
// WHY the chart lives in a shadow root under the row's cell: the detail is rewritten (morph) every second, and an empty
// `.ch-host` in the markup is the one node morph leaves alone; the open / folded state IS in the markup (`data-open`),
// regenerated from this file's state on every draw, so the layout (the cell spans the row) never drifts from it.
'use strict';
(function (root) {
  const ID = 'c2';
  const CSS = `
.ch2 { padding: 4px 0 8px; }
.ch2-head { display: flex; align-items: center; gap: 8px; cursor: pointer; border-radius: var(--io-radius-sm); padding: 2px 2px; min-width: 0; }
.ch2-head:hover { background: var(--io-hover); }
.ch2-head .ch-ic { color: var(--io-muted); transform: rotate(180deg); }
.ch2-title { font-size: 12.5px; font-weight: 600; color: var(--io-text-strong); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch2-leg { display: inline-flex; gap: 12px; font-size: 11.5px; color: var(--io-muted); }
.ch2-leg span { display: inline-flex; align-items: center; gap: 5px; }
.ch2-now { margin-left: auto; font-family: var(--io-font-mono); font-size: 17px; color: var(--io-text-strong); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ch2-now small { font-size: 11px; color: var(--io-muted); margin-left: 3px; }
.ch2-foot { display: flex; align-items: center; gap: 10px; margin-top: 4px; flex-wrap: wrap; }
.ch2-foot label { margin-left: auto; font-size: 11.5px; color: var(--io-muted); display: inline-flex; align-items: center; gap: 6px; }
`;
  let env = null;
  let open = new Set();
  const reveal = new Set();
  const boxes = new Map(); // key -> { shadow, plot, el }

  const persist = () => env.store.setJson('c2.open', [...open]);
  const WINDOWS = [60, 300, 600, 900, 1800, 3600, 10800, 21600, 43200, 86400];
  const words = (s) => (s < 3600 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);

  function toggle(key) {
    if (open.has(key)) { open.delete(key); const box = boxes.get(key); if (box) { box.plot.destroy(); boxes.delete(key); } } else { open.add(key); reveal.add(key); }
    persist();
    if (env.ctx && env.ctx.redraw) env.ctx.redraw();
    env.scheduleSync();
  }

  function build(el, key, spec) {
    const shadow = env.hostRoot(el);
    if (!shadow) return null;
    let box = boxes.get(key);
    if (box && box.shadow === shadow && shadow.querySelector('.ch2')) return box;
    if (box) box.plot.destroy();
    for (const old of shadow.querySelectorAll('.ch2')) old.remove();
    const wrap = document.createElement('div');
    wrap.className = 'ch2';
    wrap.innerHTML = `<div class="ch2-head" data-ch2-fold role="button" tabindex="0" title="Fold the chart" aria-label="Fold the chart">${env.icon('chevron')}<span class="ch2-title"></span><span class="ch2-leg"></span><span class="ch2-now"></span></div>`
      + '<div class="ch2-plot"></div><div class="ch2-foot"><span class="chc-stat"></span><label>window <select class="chc-select" aria-label="History window"></select></label></div>';
    shadow.appendChild(wrap);
    const plot = new env.Plot(wrap.querySelector('.ch2-plot'), { height: 176, area: true, tip: true });
    wrap.querySelector('select').addEventListener('change', (event) => { if (env.ctx.setWindow) env.ctx.setWindow(Number(event.target.value)); });
    box = { shadow, plot, el: wrap, key, spec };
    boxes.set(key, box);
    return box;
  }

  function draw(box) {
    const model = env.resolve(box.spec, { everyMs: 2000 });
    const wrap = box.el;
    const lead = model.series[0];
    wrap.querySelector('.ch2-title').textContent = box.spec.l || (lead ? lead.key : 'Series');
    wrap.querySelector('.ch2-leg').innerHTML = model.series.length < 2 ? '' : model.series.map((s, at) => `<span><i class="chp-sw chp-sw${at % 6}"></i>${env.esc(s.key)}</span>`).join('');
    wrap.querySelector('.ch2-now').innerHTML = model.last ? `${env.esc(env.num(model.last.v))}<small>${env.esc(env.unitText(lead.unit))}</small>` : '—';
    box.plot.set(env.plotModel(model, { levels: false }));
    const stats = lead ? env.statsOf(lead.points, model.from, model.to) : null;
    wrap.querySelector('.chc-stat').innerHTML = stats && stats.n
      ? `last ${env.esc(env.spanText(model.window_s || model.to - model.from))} · min <b>${env.esc(env.num(stats.min))}</b> · max <b>${env.esc(env.num(stats.max))}</b> · now <b data-ch-now>${env.esc(env.num(stats.last.v))}</b> ${env.esc(env.unitText(lead.unit))}`
      : (model.loading ? 'loading…' : 'no data in the window');
    const select = wrap.querySelector('select');
    const settings = env.ctx.settings && env.ctx.settings();
    if (settings) {
      const limits = (settings.limits || {}).history_window_s || { min: 60, max: 86400 };
      const now = Number(settings.history_window_s);
      const choices = [...new Set([...WINDOWS.filter((s) => s >= limits.min && s <= limits.max), now])].sort((a, b) => a - b);
      const html = choices.map((s) => `<option value="${s}">${words(s)}</option>`).join('');
      if (select.innerHTML !== html) select.innerHTML = html;
      if (document.activeElement !== select) select.value = String(now);
    }
  }

  function sync() {
    const present = new Set();
    for (const el of document.querySelectorAll('.ch-spark[data-open]')) {
      const key = el.getAttribute('data-ch-key');
      const spec = env.specOf(el);
      if (!spec) continue;
      present.add(key);
      // WHY: the empty host has zero height after a reload. Give it a static shell before observing
      // visibility, then defer all history/model/plot work while the chart is off screen.
      const box = build(el, key, spec);
      if (box) box.spec = spec;
      // WHY: explicitly unfolding a chart brings it into view; background updates never scroll it.
      if (box && reveal.delete(key)) el.scrollIntoView({ block: 'nearest' });
      if (box && env.showing(el)) draw(box);
    }
    // an unfolded row that left the page (another entity) keeps its state in `open`, not its plot
    for (const [key, box] of boxes) if (!present.has(key)) { box.plot.destroy(); boxes.delete(key); }
  }

  root.IOChartViews = root.IOChartViews || {};
  root.IOChartViews[ID] = {
    name: 'Unfold in place',
    description: 'Press a sparkline: its row unfolds into a full-width chart right there with value and time axes, the status limits, min / max / now and the window. Press the header to fold it.',
    order: 2,
    mount(shell) {
      env = shell;
      env.addStyle(ID, CSS);
      env.shadowStyle(CSS);
      open = new Set(env.store.json('c2.open', []));
    },
    unmount() { for (const box of boxes.values()) box.plot.destroy(); boxes.clear(); },
    attrs: (spec, key) => (open.has(key) ? ' data-open' : ''),
    tip: (spec, key) => (open.has(key) ? 'Fold the chart' : 'Unfold a larger chart here'),
    icon: () => 'chevron',
    on(kind, event, hit) {
      if (kind !== 'click' || !hit.el) return false;
      const real = hit.real;
      if (real && real.closest && real.closest('select, option, label')) return false; // a control inside the chart: let it work
      if (hit.el.hasAttribute('data-open')) {
        if (real && real.closest && real.closest('[data-ch2-fold]')) { toggle(hit.key); return true; }
        return true; // the chart's body: nothing, and never the row's own click (it would open the diagnostic page)
      }
      toggle(hit.key);
      return true;
    },
    update() { sync(); },
    sync,
  };
  // Enter / Space on the focused fold header
  document.addEventListener('keydown', (event) => {
    if (!env || !env.isActive(ID) || (event.key !== 'Enter' && event.key !== ' ')) return;
    const path = event.composedPath ? event.composedPath()[0] : event.target;
    if (path && path.matches && path.matches('[data-ch2-fold]')) {
      const host = path.getRootNode().host;
      const wrapper = host && host.closest ? host.closest('.ch-spark') : null;
      if (wrapper) { event.preventDefault(); toggle(wrapper.getAttribute('data-ch-key')); }
    }
  }, true);
})(typeof window !== 'undefined' ? window : globalThis);
