// Chart view c5 · Focus page (lane chart-enlarge, 2026-10-24; contract: charts/core.js).
// Press a sparkline (or its arrow) and the value gets a page of its own, covering the window: a large chart you can zoom (wheel,
// + / -) and pan (drag, arrow keys) over the history window, the status's HEALTH TIMELINE on the same time axis right under it
// (so "why did it spike" and "what did the status say then" line up), min / max / mean over what is in view, and a CSV of
// what is in view. Back (or Esc, or the browser's Back) returns to the entity exactly as it was. Each open is one browser-
// history entry (the shell's page drill: ?page=chart:...), so a reload or a pasted URL lands on the same chart.
// Precedent: Grafana Explore (a dedicated page per query: zoom, pan, export) and rqt_plot's zoomable canvas.
// WHY the timeline is drawn by the same Plot on the same x scale: two separate widgets drift when zoomed; one SVG cannot.
// WHY the page replaces the tree too: zoom and pan need the width; Back is one click and the entity page keeps its scroll.
'use strict';
(function (root) {
  const ID = 'c5';
  const PREFIX = 'chart:';
  const CSS = `
body.ch5-page .main { display: none !important; }
.ch5 { flex: 1; min-height: 0; display: none; flex-direction: column; background: var(--io-surface); }
body.ch5-page .ch5 { display: flex; }
.ch5-bar { display: flex; align-items: center; gap: 8px; padding: 7px 16px; border-bottom: 1px solid var(--io-border); background: var(--io-surface-2); flex: none; flex-wrap: wrap; }
.ch5-back { display: inline-flex; align-items: center; gap: 4px; background: transparent; border: 0; color: var(--io-accent-text); cursor: pointer; padding: 3px 6px 3px 2px; border-radius: var(--io-radius-sm); font-size: 12.5px; text-decoration: var(--io-link-decoration); }
.ch5-back:hover { background: var(--io-hover); }
.ch5-crumb { color: var(--io-muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.ch5-sp { flex: 1; }
.ch5-main { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; padding: 14px 24px 16px; width: 100%; max-width: 1500px; margin: 0 auto; }
.ch5-head { display: flex; align-items: flex-end; gap: 14px; flex: none; }
.ch5-head h1 { margin: 0; font-family: var(--io-font-display); font-size: 20px; font-weight: 600; color: var(--io-text-strong); line-height: 1.2; }
.ch5-sub { color: var(--io-muted); font-size: 12.5px; margin-top: 2px; overflow-wrap: anywhere; }
.ch5-now { margin-left: auto; font-family: var(--io-font-mono); font-size: 30px; line-height: 1; color: var(--io-text-strong); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ch5-now small { font-size: 14px; color: var(--io-muted); margin-left: 4px; font-family: var(--io-font-ui); }
.ch5-ctl { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: none; }
.ch5-range { color: var(--io-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.ch5-plotwrap { position: relative; flex: 1; min-height: 260px; outline: none; border: 1px solid var(--io-border-soft); border-radius: var(--io-radius-lg); background: var(--io-surface); padding: 4px 4px 0 0; }
.ch5-plotwrap:focus-visible { border-color: var(--io-selected-line); }
.ch5-hint { color: var(--io-faint); font-size: 11.5px; flex: none; }
.ch5-tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; flex: none; }
.ch5-tbl th { text-align: right; font-weight: 500; color: var(--io-faint); font-size: 11px; padding: 2px 10px 2px 0; }
.ch5-tbl th:first-child, .ch5-tbl td:first-child { text-align: left; }
.ch5-tbl td { padding: 3px 10px 3px 0; border-top: 1px solid var(--io-border-soft); font-family: var(--io-font-mono); text-align: right; font-variant-numeric: tabular-nums; color: var(--io-text); }
.ch5-tbl td:first-child { font-family: var(--io-font-ui); color: var(--io-muted); }
.ch5-key { display: inline-flex; align-items: center; gap: 7px; }
.ch5-lvl { display: inline-flex; align-items: center; gap: 5px; }
`;
  let env = null;
  let page = null; // { el, spec, key, plot, view, follow, yMode }
  let lastId = null;

  const words = (s) => (s < 3600 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);
  const WINDOWS = [60, 300, 600, 900, 1800, 3600, 10800, 21600, 43200, 86400];
  const pageId = (spec) => PREFIX + JSON.stringify(spec);
  function specOfPage(id) { try { return JSON.parse(id.slice(PREFIX.length)); } catch (_) { return null; } }

  function build(spec) {
    const el = document.createElement('section');
    el.className = 'ch5';
    el.id = 'ch5-page';
    el.setAttribute('aria-label', 'Chart page');
    el.innerHTML = `<div class="ch5-bar"><button type="button" class="ch5-back" data-back>${env.icon('back')}<span class="ch5-backt">Back</span></button><span class="ch5-crumb"></span><span class="ch5-sp"></span>`
      + `<span class="chc-seg" data-yseg hidden role="radiogroup" aria-label="Value axis"><button type="button" role="radio" data-y="fit">Fit</button><button type="button" role="radio" data-y="full">0–100 %</button></span>`
      + `<button type="button" class="chc-btn icon" data-zoom="in" title="Zoom in ( + )" aria-label="Zoom in">${env.icon('zoomin')}</button>`
      + `<button type="button" class="chc-btn icon" data-zoom="out" title="Zoom out ( - )" aria-label="Zoom out">${env.icon('zoomout')}</button>`
      + '<button type="button" class="chc-btn" data-zoom="reset" title="Show the whole window ( 0 )">Whole window</button>'
      + '<button type="button" class="chc-btn" data-live title="Follow the newest data" hidden>Live</button>'
      + '<label class="chc-stat">window <select class="chc-select" data-win aria-label="History window"></select></label>'
      + `<button type="button" class="chc-btn" data-csv title="Download what is in view as CSV">${env.icon('download')}CSV</button></div>`
      + '<div class="ch5-main"><div class="ch5-head"><div><h1></h1><div class="ch5-sub"></div></div><div class="ch5-now"></div></div>'
      + '<div class="ch5-ctl"><span class="ch5-range"></span></div>'
      + '<div class="ch5-plotwrap" tabindex="0" aria-label="Chart: wheel zooms, drag pans, arrow keys pan, + and - zoom"></div>'
      + '<div class="ch5-hint">Wheel zooms around the cursor · drag pans · double-click shows the whole window · the strip underneath is the status\'s health on the same time axis</div>'
      + '<table class="ch5-tbl"><thead><tr><th>series</th><th>unit</th><th>now</th><th>min</th><th>max</th><th>mean</th></tr></thead><tbody></tbody></table></div>';
    const state = { el, spec, key: env.specKey(spec), view: null, follow: true, yMode: 'fit', model: null };
    const wrap = el.querySelector('.ch5-plotwrap');
    state.plot = new env.Plot(wrap, { height: 'fill', zoom: true, strip: true, area: true, tip: true,
      onView: (view) => { state.view = view; state.follow = !view || view[1] >= state.model.to - 0.75; draw(state); } });
    el.addEventListener('click', (event) => {
      const target = event.target.closest('[data-back],[data-zoom],[data-live],[data-csv],[data-y]');
      if (!target) return;
      if (target.hasAttribute('data-back')) env.ctx.closePage();
      else if (target.dataset.zoom === 'in') state.plot.zoomBy(0.6);
      else if (target.dataset.zoom === 'out') state.plot.zoomBy(1.6);
      else if (target.dataset.zoom === 'reset') state.plot.reset();
      else if (target.hasAttribute('data-live')) { const [a, b] = state.plot.visible(); state.plot.setView([state.model.to - (b - a), state.model.to]); state.view = state.plot.view; state.follow = true; draw(state); }
      else if (target.hasAttribute('data-csv')) download(state);
      else if (target.dataset.y) { state.yMode = target.dataset.y; state.plot.o.yMode = state.yMode; state.plot.draw(); draw(state); }
    });
    el.querySelector('[data-win]').addEventListener('change', (event) => { if (env.ctx.setWindow) env.ctx.setWindow(Number(event.target.value)); });
    wrap.addEventListener('keydown', (event) => {
      const k = event.key;
      if (k === '+' || k === '=') state.plot.zoomBy(0.7);
      else if (k === '-' || k === '_') state.plot.zoomBy(1.4);
      else if (k === 'ArrowLeft') state.plot.panBy(-0.2);
      else if (k === 'ArrowRight') state.plot.panBy(0.2);
      else if (k === '0') state.plot.reset();
      else return;
      event.preventDefault();
    });
    return state;
  }

  function draw(state) {
    const { el, spec } = state;
    const model = env.resolve(spec, { everyMs: 1500 });
    state.model = model;
    const lead = model.series[0];
    const entity = env.ctx.nameOf ? env.ctx.nameOf(spec.p) : spec.p;
    el.querySelector('.ch5-backt').textContent = `Back to ${entity || 'the entity'}`;
    el.querySelector('.ch5-crumb').textContent = [entity, lead ? lead.status : '', lead ? lead.key : ''].filter(Boolean).join(' › ');
    el.querySelector('h1').textContent = spec.l || (lead ? lead.key : 'Series');
    // the status's own words now, from the latest level span; a problem keeps its colour (it is a diagnostic result)
    const spans = (model.levels || []).filter((span) => span.level);
    const latest = spans[spans.length - 1];
    const problem = latest && ['warn', 'error', 'stale', 'unknown'].includes(latest.level);
    el.querySelector('.ch5-sub').innerHTML = `${env.esc(lead ? lead.status : '')}${latest ? ` · <span class="ch5-lvl"${problem ? ' data-diagnostic' : ''}>${problem ? `<b style="color:var(--io-${latest.level}-text)">${env.esc(latest.level)}</b> · ` : ''}${env.esc(latest.message || '')}</span>` : ''}`;
    el.querySelector('.ch5-now').innerHTML = model.last ? `${env.esc(env.num(model.last.v))}<small>${env.esc(env.unitText(lead.unit))}</small>` : '—';
    // follow the newest data while the viewer has not panned away
    if (state.plot.view && state.follow && model.to) { const span = state.plot.view[1] - state.plot.view[0]; state.plot.view = [model.to - span, model.to]; }
    state.plot.set(env.plotModel(model));
    const [a, b] = state.plot.visible();
    const zoomed = Boolean(state.plot.view);
    el.querySelector('.ch5-range').textContent = model.to ? `${env.clock(a)} – ${env.clock(b)} · ${env.spanText(b - a)}${zoomed ? ' (zoomed)' : ' (the whole window)'}` : '';
    el.querySelector('[data-live]').hidden = !(zoomed && !state.follow);
    el.querySelector('[data-zoom="reset"]').disabled = !zoomed;
    const pct = lead && lead.unit === '%';
    const seg = el.querySelector('[data-yseg]');
    seg.hidden = !pct;
    for (const b2 of seg.querySelectorAll('button')) { b2.classList.toggle('on', b2.dataset.y === state.yMode); b2.setAttribute('aria-checked', String(b2.dataset.y === state.yMode)); }
    el.querySelector('tbody').innerHTML = model.series.map((s, at) => {
      const st = env.statsOf(s.points, a, b);
      const unit = env.unitText(s.unit);
      return `<tr><td><span class="ch5-key"><i class="chp-sw chp-sw${at % 6}"></i>${env.esc(s.key)}</span></td><td>${env.esc(unit || '—')}</td><td>${st.last ? env.esc(env.num(st.last.v)) : '—'}</td>`
        + `<td>${st.n ? env.esc(env.num(st.min)) : '—'}</td><td>${st.n ? env.esc(env.num(st.max)) : '—'}</td><td>${st.n ? env.esc(env.num(st.mean)) : '—'}</td></tr>`;
    }).join('') || `<tr><td>${model.loading ? 'loading…' : 'no data in the window'}</td><td></td><td></td><td></td><td></td><td></td></tr>`;
    const select = el.querySelector('[data-win]');
    const settings = env.ctx.settings && env.ctx.settings();
    if (settings) {
      const limits = (settings.limits || {}).history_window_s || { min: 60, max: 86400 };
      const now = Number(settings.history_window_s);
      const choices = [...new Set([...WINDOWS.filter((s) => s >= limits.min && s <= limits.max), now])].sort((x, y) => x - y);
      const html = choices.map((s) => `<option value="${s}">${words(s)}</option>`).join('');
      if (select.innerHTML !== html) select.innerHTML = html;
      if (document.activeElement !== select) select.value = String(now);
    }
  }

  // CSV of what is in view: time, epoch seconds, one column per series (unreadable values stay empty)
  function csvOf(state) {
    const model = state.model;
    const [a, b] = state.plot.visible();
    const quote = (text) => (/[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text);
    const times = [...new Set(model.series.flatMap((s) => s.points.map((p) => p[0]).filter((t) => t >= a && t <= b)))].sort((x, y) => x - y);
    const lookup = model.series.map((s) => new Map(s.points.map((p) => [p[0], p[1]])));
    const head = ['time', 'epoch_s', ...model.series.map((s) => `${s.key}${s.unit ? ` (${env.unitText(s.unit) || s.unit})` : ''}`)].map(quote).join(',');
    const rows = times.map((t) => [new Date(t * 1000).toISOString(), t.toFixed(3), ...lookup.map((m) => { const v = m.get(t); return env.isNum(env.asNum(v)) ? String(env.asNum(v)) : ''; })].join(','));
    return `${[head, ...rows].join('\n')}\n`;
  }
  function download(state) {
    const lead = state.model.series[0];
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..*/, '').replace('T', '-');
    const name = `${[env.ctx.nameOf ? env.ctx.nameOf(state.spec.p) : state.spec.p, lead ? lead.status : '', lead ? lead.key : ''].join('-')}-${stamp}.csv`.replace(/[^\w.\-]+/g, '_');
    const url = URL.createObjectURL(new Blob([csvOf(state)], { type: 'text/csv' }));
    const link = document.createElement('a');
    link.href = url; link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  // show or hide the page to match the shell's drill (a URL, a reload, Back, our own open)
  function syncPage() {
    if (!env || env.active !== ID) return;
    const id = env.ctx.page && env.ctx.page();
    const spec = id && id.startsWith(PREFIX) ? specOfPage(id) : null;
    if (!spec) { close(); return; }
    if (!page || lastId !== id) {
      close();
      const main = document.getElementById('main');
      page = build(spec);
      lastId = id;
      main.after(page.el);
      document.body.classList.add('ch5-page');
      page.el.querySelector('.ch5-plotwrap').focus({ preventScroll: true });
    }
    draw(page);
  }
  function close() {
    if (!page) { document.body.classList.remove('ch5-page'); return; }
    page.plot.destroy();
    page.el.remove();
    page = null; lastId = null;
    document.body.classList.remove('ch5-page');
  }

  root.IOChartViews = root.IOChartViews || {};
  root.IOChartViews[ID] = {
    name: 'Focus page',
    description: 'Press a sparkline: the value gets a page of its own, with zoom and pan over the history window, the health timeline on the same time axis, min / max / mean, and CSV. Back returns to the entity.',
    order: 5,
    mount(shell) { env = shell; env.addStyle(ID, CSS); window.addEventListener('popstate', onPop); setTimeout(syncPage, 0); },
    unmount() { window.removeEventListener('popstate', onPop); close(); },
    tip: () => 'Open this value on its own page (zoom, pan, health timeline, CSV)',
    icon: () => 'open',
    on(kind, event, hit) {
      if (kind === 'keydown') {
        if (event.key === 'Escape' && page && !(event.target && event.target.closest && event.target.closest('select'))) { env.ctx.closePage(); setTimeout(syncPage, 0); return true; }
        return false;
      }
      if (kind === 'click' && hit.el && hit.spec) { env.ctx.openPage(pageId(hit.spec)); syncPage(); return true; }
      return false;
    },
    update() { syncPage(); },
    sync() { syncPage(); },
  };
  function onPop() { setTimeout(syncPage, 0); }
})(typeof window !== 'undefined' ? window : globalThis);
