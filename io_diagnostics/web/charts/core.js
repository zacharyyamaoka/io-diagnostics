// Chart helper · the shared half of the "enlarge a sparkline" proposals (lane chart-enlarge, 2026-10-24).
//
// A page that draws a value's sparkline wraps it in ONE call:
//
//     IOCharts.wrap({ p: <entity path>, l: <label>, s: [[<status name>, <measurement key>, <style>?], ...] }, sparklineHtml)
//
// It returns the grid cell (`span.ch-spark`) that carries what the sparkline shows. Nothing else is wired by the page:
// the helper listens on `document`, reads the series from the shell's own history cache (/api/v1/history, the URL the
// layouts already ask for) and hands the click / hover to the selected CHART VIEW (window.IOChartViews, charts/c1..c5.js),
// switched from the bottom-right Prototype picker. With `c0` selected the wrapper is a plain cell: nothing changes.
//
// WHY one wrapper and delegated events: the detail panel is redrawn (morphed) every second; a listener or a popover bound
// to a sparkline's own nodes would not survive it. The wrapper's markup is regenerated every draw from the view's state,
// the enlarged charts live in a shadow root or on the body, and a MutationObserver re-syncs them after each draw.
//
// A chart view: window.IOChartViews[id] = { name, description, order, mount(env), unmount(env), on(kind, event, hit),
//   update(env) (1 Hz), sync(env) (after each draw of the detail), attrs(spec, key), tip(spec, key), icon(spec, key) }.
// `hit` = { el, spec, key, real } for events on a wrapper (real = the innermost node, inside a shadow root too).
'use strict';
(function (root) {
  const PREFIX = 'io-diagnostics.chart.';
  const PROBLEM = new Set(['warn', 'error', 'stale', 'unknown']);

  // ------------------------------------------------------------------------------ small helpers ----
  const esc = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const UNITS = { GiBy: 'GiB', MiBy: 'MiB', KiBy: 'KiB', By: 'B', Cel: '°C', 1: '', '%': '%' };
  const unitText = (unit) => (unit in UNITS ? UNITS[unit] : unit || '');
  function num(value, step) {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    const v = Number(value);
    if (!Number.isFinite(v)) return String(value);
    if (step) { const dec = (String(+step.toPrecision(6)).split('.')[1] || '').length; return v.toFixed(Math.min(dec, 6)); }
    const a = Math.abs(v);
    if (a === 0) return '0';
    if (a >= 100) return v.toFixed(0);
    if (a >= 10) return v.toFixed(1);
    if (a >= 0.01) return v.toFixed(2);
    return v.toPrecision(2);
  }
  const fmt = (value, unit) => { const u = unitText(unit); return `${num(value)}${u ? ` ${u}` : ''}`; };
  const pad2 = (n) => String(n).padStart(2, '0');
  const clock = (t, seconds = true) => { const d = new Date(t * 1000); return Number.isNaN(d.getTime()) ? '—' : `${pad2(d.getHours())}:${pad2(d.getMinutes())}${seconds ? `:${pad2(d.getSeconds())}` : ''}`; };
  const spanText = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const asNum = (v) => (typeof v === 'boolean' ? Number(v) : v);
  const store = {
    get(key, fallback = null) { try { const v = localStorage.getItem(PREFIX + key); return v === null ? fallback : v; } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem(PREFIX + key, value); } catch (_) { /* private mode: the choice lasts this page only */ } },
    json(key, fallback) { try { return JSON.parse(store.get(key)); } catch (_) { return fallback; } },
    setJson(key, value) { store.set(key, JSON.stringify(value)); },
  };

  // ------------------------------------------------------------------------------------ icons ----
  const ICONS = {
    expand: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    plot: '<path d="M3 3v18h18"/><path d="m7 15 4-5 3 3 5-6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    pin: '<path d="M9 4h6l-1 6 3 3H7l3-3ZM12 13v7"/>',
    pinned: '<path d="M9 4h6l-1 6 3 3H7l3-3ZM12 13v7" fill="currentColor"/>',
    open: '<path d="M7 17 17 7M8 7h9v9"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    dock: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 14h18"/>',
    dockr: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
    back: '<path d="m15 6-6 6 6 6"/>',
    zoomin: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3M11 8v6M8 11h6"/>',
    zoomout: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3M8 11h6"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  };
  const icon = (name, cls = 'ch-ic') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`;

  // -------------------------------------------------------------------------------------- styles ----
  // Tokens only (no colour literal). Warn / error colours appear only on threshold lines and problem spans, which carry
  // [data-diagnostic]: a limit IS a status's own result (the warn-colours gate). The series colours are rotations of the
  // skin's info hue plus neutrals, so they never read as a warning.
  const CSS = `
.ch-spark { display: block; position: relative; min-width: 0; }
.ch-line { display: block; position: relative; min-width: 0; }
.ch-host { display: none; }
.ch-btn { display: none; position: absolute; right: 0; top: 50%; transform: translateY(-50%); width: 22px; height: 22px; padding: 0; align-items: center; justify-content: center;
  border: 1px solid var(--io-border); background: var(--io-popover); color: var(--io-muted); border-radius: var(--io-radius-sm); cursor: pointer; opacity: 0; transition: opacity .1s; }
.ch-btn:hover { color: var(--io-text-strong); background: var(--io-control-hover); }
.ch-btn.on { color: var(--io-text-strong); background: var(--io-pressed); opacity: 1; }
html[data-chart]:not([data-chart="c0"]) .ch-btn { display: inline-flex; }
html[data-chart]:not([data-chart="c0"]) .ch-spark:hover .ch-btn, html[data-chart]:not([data-chart="c0"]) .ch-btn:focus-visible { opacity: 1; }
html[data-chart]:not([data-chart="c0"]) .ch-line { cursor: zoom-in; border-radius: var(--io-radius-sm); }
html[data-chart]:not([data-chart="c0"]) .ch-spark:hover .ch-line { background: var(--io-hover); }
.ch-ic { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; flex: none; }
.ch-spark[data-open] { grid-column: 1 / -1; flex: 1 0 100%; width: 100%; }
.ch-spark[data-open] > .ch-line { display: none; }
.ch-spark[data-open] > .ch-host { display: block; }

/* ---- the plot (charts/core.js Plot) ------------------------------------------------------- */
.chp { position: relative; width: 100%; min-width: 0; color: var(--io-text); font-family: var(--io-font-ui); }
.chp-svg { display: block; width: 100%; overflow: visible; touch-action: none; }
.chp-fill { position: absolute; inset: 0; }
.chp-grid { stroke: var(--io-border-soft); stroke-width: 1; }
.chp-axis { stroke: var(--io-border); stroke-width: 1; }
.chp-tick { fill: var(--io-faint); font-size: 11px; font-family: var(--io-font-mono); font-variant-numeric: tabular-nums; }
.chp-unit { fill: var(--io-muted); font-size: 11px; font-family: var(--io-font-ui); }
.chp-nodata { fill: var(--io-faint); font-size: 12px; text-anchor: middle; }
.chp-ln { fill: none; stroke-width: 1.6; stroke-linejoin: round; stroke-linecap: round; }
.chp-ar { stroke: none; opacity: .55; }
.chp-s0 { stroke: var(--io-text); } .chp-s1 { stroke: var(--io-info); }
.chp-s2 { stroke: oklch(from var(--io-info) l c calc(h + 58)); } .chp-s3 { stroke: oklch(from var(--io-info) l c calc(h - 58)); }
.chp-s4 { stroke: var(--io-muted); } .chp-s5 { stroke: var(--io-faint); }
.chp-ln.dim { stroke-dasharray: 5 3; }
.chp-fill-s0 { fill: var(--io-control); } .chp-fill-s1 { fill: var(--io-info); opacity: .14; }
.chp-sw0 { background: var(--io-text); } .chp-sw1 { background: var(--io-info); }
.chp-sw2 { background: oklch(from var(--io-info) l c calc(h + 58)); } .chp-sw3 { background: oklch(from var(--io-info) l c calc(h - 58)); }
.chp-sw4 { background: var(--io-muted); } .chp-sw5 { background: var(--io-faint); }
.chp-lim { stroke-width: 1; stroke-dasharray: 5 4; }
.chp-lim.warn { stroke: var(--io-warn); } .chp-lim.error { stroke: var(--io-error); }
.chp-limt { font-size: 10.5px; text-anchor: start; font-family: var(--io-font-ui); }
.chp-limt.warn { fill: var(--io-warn-text); } .chp-limt.error { fill: var(--io-error-text); }
.chp-cur { pointer-events: none; }
.chp-cur line { stroke: var(--io-muted); stroke-width: 1; stroke-dasharray: 2 3; }
.chp-cur circle { fill: var(--io-surface); stroke-width: 2; }
.chp-cur .c0 { stroke: var(--io-text); } .chp-cur .c1 { stroke: var(--io-info); }
.chp-cur .c2 { stroke: oklch(from var(--io-info) l c calc(h + 58)); } .chp-cur .c3 { stroke: oklch(from var(--io-info) l c calc(h - 58)); }
.chp-cur .c4 { stroke: var(--io-muted); } .chp-cur .c5 { stroke: var(--io-faint); }
.chp-lv { stroke: none; }
.chp-lv.ok { fill: var(--io-border); } .chp-lv.warn { fill: var(--io-warn); } .chp-lv.error { fill: var(--io-error); }
.chp-lv.stale { fill: var(--io-stale); } .chp-lv.unknown { fill: var(--io-unknown); }
.chp-lvbg { fill: var(--io-border-soft); }
.chp-tip { position: absolute; top: 4px; left: 0; z-index: 2; pointer-events: none; min-width: 120px; max-width: 280px; padding: 5px 8px; font-size: 12px; line-height: 1.35;
  background: var(--io-popover); color: var(--io-text); border: 1px solid var(--io-border); border-radius: var(--io-radius); box-shadow: var(--io-shadow-pop); white-space: nowrap; }
.chp-tip .t { color: var(--io-faint); font-family: var(--io-font-mono); font-size: 11px; }
.chp-tip .r { display: flex; align-items: center; gap: 6px; }
.chp-tip .r b { font-weight: 500; color: var(--io-muted); overflow: hidden; text-overflow: ellipsis; }
.chp-tip .r i { margin-left: auto; padding-left: 10px; font-style: normal; font-family: var(--io-font-mono); color: var(--io-text-strong); font-variant-numeric: tabular-nums; }
.chp-sw { width: 8px; height: 8px; border-radius: 50%; flex: none; display: inline-block; }
.chp-zoomable .chp-svg { cursor: crosshair; }
.chp-zoomable.panning .chp-svg { cursor: grabbing; }

/* ---- controls shared by the proposals ------------------------------------------------------- */
.chc-seg { display: inline-flex; background: var(--io-field); border: 1px solid var(--io-border); border-radius: var(--io-radius); padding: 2px; gap: 1px; }
.chc-seg button { background: transparent; border: 0; padding: 2px 8px; border-radius: calc(var(--io-radius) - 2px); cursor: pointer; color: var(--io-muted); font-size: 11.5px; font-weight: 500; }
.chc-seg button:hover { color: var(--io-text); }
.chc-seg button.on { background: var(--io-pressed); color: var(--io-text-strong); }
.chc-btn { display: inline-flex; align-items: center; gap: 5px; background: var(--io-control); border: 1px solid var(--io-border); border-radius: var(--io-radius); padding: 3px 9px; cursor: pointer; color: var(--io-text); font-size: 12px; }
.chc-btn:hover { background: var(--io-control-hover); }
.chc-btn.icon { padding: 3px 6px; }
.chc-btn.on { background: var(--io-pressed); color: var(--io-text-strong); }
.chc-btn[hidden], .chc-seg[hidden], .chc-stat[hidden] { display: none !important; }
.chc-select { background: var(--io-field); color: var(--io-text); border: 1px solid var(--io-border); border-radius: var(--io-radius); padding: 2px 6px; font-size: 12px; font-family: inherit; }
.chc-stat { font-size: 12px; color: var(--io-muted); font-variant-numeric: tabular-nums; }
.chc-stat b { color: var(--io-text-strong); font-weight: 600; font-family: var(--io-font-mono); }
`;
  let styleNode = null;
  function injectStyle() {
    if (typeof document === 'undefined' || document.querySelector('style[data-charts-core]')) return;
    styleNode = document.createElement('style');
    styleNode.dataset.chartsCore = '1';
    styleNode.textContent = CSS;
    document.head.appendChild(styleNode);
  }
  // a proposal's own stylesheet, once (prefix its classes ch1- ... ch5-)
  function addStyle(id, css) {
    if (typeof document === 'undefined' || document.querySelector(`style[data-charts="${id}"]`)) return;
    const style = document.createElement('style');
    style.dataset.charts = id;
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ----------------------------------------------------------------------------- specs and data ----
  // identity of the SERIES a spec names (c3 / c4 / c5 key their state on it); viewKey adds the label, because one series can be
  // drawn twice on a page (a group's header and its own row) and a wrapper's own state (a peek's anchor, an unfold) is per wrapper
  const specKey = (spec) => JSON.stringify([spec.p, (spec.s || []).map((s) => [s[0], s[1]])]);
  const viewKey = (spec) => JSON.stringify([spec.p, (spec.s || []).map((s) => [s[0], s[1]]), spec.l || '']);
  const specCache = new Map();
  function specOf(el) {
    const text = el && el.getAttribute && el.getAttribute('data-spark');
    if (!text) return null;
    if (!specCache.has(text)) { try { specCache.set(text, JSON.parse(text)); } catch (_) { specCache.set(text, null); } }
    return specCache.get(text);
  }
  const historyUrl = (path, windowS) => `/api/v1/history?path=${encodeURIComponent(path)}${windowS ? `&window=${windowS}` : ''}`;
  const limitsOf = (m) => [['warn_low', m.warn_low], ['warn_high', m.warn_high], ['error_low', m.error_low], ['error_high', m.error_high]].filter(([, v]) => isNum(v));

  // memo: the history answer object -> spec key -> resolved model (a new answer arrives only when the shell refetches)
  const memo = new WeakMap();
  // The series a spec names, from the shell's history cache: { entry, loading, error, from, to, window_s, series, levels, last }.
  // opts.window (seconds): this chart's own window (the API's `window=`), else the app-wide one.
  function resolve(spec, opts = {}) {
    if (!ctx || !spec || !spec.p) return { spec, series: [], levels: null, from: 0, to: 0, loading: true, error: '', last: null };
    const entry = ctx.fetch(historyUrl(spec.p, opts.window), opts.everyMs ?? 2000);
    const data = entry.data;
    if (!data) return { spec, entry, series: [], levels: null, from: 0, to: 0, loading: !entry.error, error: entry.error, last: null };
    let byKey = memo.get(data);
    if (!byKey) { byKey = new Map(); memo.set(data, byKey); }
    const key = specKey(spec);
    if (byKey.has(key)) return byKey.get(key);
    const statuses = new Map((data.statuses || []).map((s) => [s.name, s]));
    const series = [];
    (spec.s || []).forEach(([status, name, style], at) => {
      const st = statuses.get(status);
      const m = st && (st.measurements || []).find((x) => x.key === name);
      if (!m) return;
      series.push({ id: JSON.stringify([status, name]), status, key: name, label: name, style: style || '', unit: m.unit, points: m.points || [], period: st.expected_period_s, limits: limitsOf(m), index: at });
    });
    const first = statuses.get((spec.s && spec.s[0] && spec.s[0][0]) || '');
    const model = { spec, entry, series, levels: first ? first.levels : null, from: data.from, to: data.to, window_s: data.window_s, loading: false, error: entry.error,
      last: lastOf(series[0] ? series[0].points : []), status: first || null };
    byKey.set(key, model);
    return model;
  }
  function lastOf(points) {
    for (let i = points.length - 1; i >= 0; i -= 1) { const v = asNum(points[i][1]); if (isNum(v)) return { t: points[i][0], v }; }
    return null;
  }
  // min / max / mean / last over [a, b] of one series
  function statsOf(points, a, b) {
    let lo = Infinity; let hi = -Infinity; let sum = 0; let n = 0; let last = null;
    for (const [t, raw] of points) {
      if (t < a || t > b) continue;
      const v = asNum(raw);
      if (!isNum(v)) continue;
      lo = Math.min(lo, v); hi = Math.max(hi, v); sum += v; n += 1; last = { t, v };
    }
    return n ? { min: lo, max: hi, mean: sum / n, n, last } : { min: null, max: null, mean: null, n: 0, last: null };
  }
  // The Plot's input from a resolved model; `extra` overrides per series (axis, colour index).
  function plotModel(model, extra = {}) {
    return { from: model.from, to: model.to, levels: extra.levels === false ? null : model.levels,
      series: model.series.map((s, at) => ({ ...s, cls: (extra.cls ?? at), axis: extra.axis || 'L', dim: s.style === 'dim' })) };
  }

  // ------------------------------------------------------------------------------------- the Plot ----
  function niceTicks(lo, hi, count = 4) {
    const span = hi - lo || 1;
    const raw = span / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const norm = raw / mag;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
    const ticks = [];
    for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) ticks.push(+v.toFixed(12));
    return { ticks, step };
  }
  const TIME_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400];
  function timeTicks(a, b, width) {
    const want = Math.max(2, Math.floor(width / 96));
    const step = TIME_STEPS.find((s) => (b - a) / s <= want) || TIME_STEPS[TIME_STEPS.length - 1];
    const offset = -new Date(a * 1000).getTimezoneOffset() * 60; // tick on the viewer's wall-clock multiples
    const out = [];
    for (let t = Math.ceil((a + offset) / step) * step - offset; t <= b; t += step) out.push(t);
    return { ticks: out, step, seconds: step < 60 };
  }
  const el = (tag, cls) => { const node = document.createElement(tag); if (cls) node.className = cls; return node; };

  // y range of a group of series over the visible window (the d4 rule: positive quantities read from zero when that does
  // not flatten them; a percentage never goes past 0..100; limits near the data are inside the range)
  function yRange(list, a, b, mode) {
    const unit = list[0] && list[0].unit;
    const values = [];
    for (const s of list) for (const [t, raw] of s.points) { const v = asNum(raw); if (t >= a && t <= b && isNum(v)) values.push(v); }
    if (mode === 'full' && unit === '%') return [0, 100];
    if (!values.length) return unit === '%' ? [0, 100] : [0, 1];
    let lo = Math.min(...values); let hi = Math.max(...values);
    const span = Math.max(hi - lo, Math.abs(hi) * 0.35, 1e-9);
    for (const s of list.slice(0, 1)) for (const [, v] of s.limits || []) if (v >= lo - span && v <= hi + span) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    if (lo >= 0 && lo < span) lo = 0;
    if (hi === lo) { hi += Math.abs(hi) * 0.1 || 1; lo -= Math.abs(lo) * 0.1 || 0; }
    const pad = (hi - lo) * 0.08;
    lo = lo === 0 ? 0 : lo - pad; hi += pad;
    if (unit === '%') { lo = Math.max(lo, 0); hi = Math.min(hi, 100); }
    return [lo, hi];
  }

  class Plot {
    // o: { height, zoom, strip, area, tip, yMode: 'fit'|'full', onCursor(t, rows), onView(a, b) }
    constructor(parent, o = {}) {
      this.o = { height: 180, zoom: false, strip: false, area: false, tip: true, yMode: 'fit', ...o };
      this.root = el('div', `chp${this.o.zoom ? ' chp-zoomable' : ''}${this.o.height === 'fill' ? ' chp-fill' : ''}`);
      this.root.innerHTML = '<svg class="chp-svg" role="img"></svg><div class="chp-tip" hidden></div>';
      this.svg = this.root.firstChild;
      this.tip = this.root.lastChild;
      parent.appendChild(this.root);
      this.model = null; this.view = null; this.cursorT = null; this.width = 0; this.geom = null;
      this.resize = new ResizeObserver(() => { if (this.root.clientWidth !== this.width || (this.o.height === 'fill' && this.root.clientHeight !== this.seenHeight)) this.draw(); });
      this.resize.observe(this.root);
      this.hover = false; // WHY: a redraw (a resize, new data, a scroll that moved the figure) must not leave a cursor where the pointer no longer is
      this.root.addEventListener('pointerenter', () => { this.hover = true; });
      this.root.addEventListener('pointermove', (event) => { this.hover = true; this.move(event); });
      this.root.addEventListener('pointerleave', () => { this.hover = false; if (!this.drag) this.leave(); });
      if (this.o.zoom) {
        this.root.addEventListener('wheel', (event) => this.wheel(event), { passive: false });
        this.root.addEventListener('pointerdown', (event) => this.down(event));
        this.root.addEventListener('pointerup', (event) => this.up(event));
        this.root.addEventListener('pointercancel', (event) => this.up(event));
        this.root.addEventListener('dblclick', () => this.reset());
      }
    }
    destroy() { this.resize.disconnect(); this.root.remove(); }
    set(model) { this.model = model; this.draw(); }
    setView(view) { this.view = view; this.draw(); }
    visible() { return this.view || (this.model ? [this.model.from, this.model.to] : [0, 1]); }

    draw() {
      const model = this.model;
      const width = this.root.clientWidth;
      this.width = width;
      if (!width) return;
      const hasRight = Boolean(model && model.series.some((s) => s.axis === 'R'));
      const stripH = this.o.strip ? 16 : 0;
      const H = this.o.height === 'fill' ? Math.max(90, this.root.clientHeight) : this.o.height;
      this.seenHeight = this.root.clientHeight;
      const ml = 50; const mr = hasRight ? 50 : 12; const mt = 18; const mb = 22 + (stripH ? stripH + 6 : 0);
      const pw = Math.max(40, width - ml - mr); const ph = Math.max(30, H - mt - mb);
      const [a, b] = this.visible();
      const x = (t) => ml + ((t - a) / (b - a)) * pw;
      this.svg.setAttribute('viewBox', `0 0 ${width} ${H}`);
      this.svg.setAttribute('height', String(H));
      this.geom = { ml, mr, mt, ph, pw, a, b, x, H, stripH, width };
      let svg = '';
      if (!model || !model.series.length || !(b > a)) {
        svg = `<text class="chp-nodata" x="${width / 2}" y="${H / 2}">${model && model.loading ? 'loading…' : 'no data in the window'}</text>`;
        this.svg.innerHTML = svg; this.geom.y = {};
        return;
      }
      const axes = { L: model.series.filter((s) => s.axis !== 'R'), R: model.series.filter((s) => s.axis === 'R') };
      const yfor = {}; const ticksFor = {};
      for (const side of ['L', 'R']) {
        if (!axes[side].length) continue;
        const [lo, hi] = yRange(axes[side], a, b, this.o.yMode);
        const { ticks, step } = niceTicks(lo, hi, ph > 150 ? 5 : 4);
        yfor[side] = (v) => mt + ph - ((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * ph;
        ticksFor[side] = { ticks, step, lo, hi };
      }
      this.geom.y = yfor;
      // horizontal grid + y labels
      for (const side of ['L', 'R']) {
        if (!ticksFor[side]) continue;
        const { ticks, step } = ticksFor[side];
        // an axis shared by series of different units says so (tview does the same): "°C · Mbit/s"
        const unit = [...new Set(axes[side].map((s) => unitText(s.unit)).filter(Boolean))].join(' · ');
        for (const v of ticks) {
          const y = yfor[side](v).toFixed(1);
          if (side === 'L') svg += `<line class="chp-grid" x1="${ml}" x2="${ml + pw}" y1="${y}" y2="${y}"/>`;
          svg += side === 'L'
            ? `<text class="chp-tick" x="${ml - 6}" y="${(+y + 3.5).toFixed(1)}" text-anchor="end">${esc(num(v, step))}</text>`
            : `<text class="chp-tick" x="${ml + pw + 6}" y="${(+y + 3.5).toFixed(1)}" text-anchor="start">${esc(num(v, step))}</text>`;
        }
        if (unit) svg += `<text class="chp-unit" x="${side === 'L' ? ml - 6 : ml + pw + 6}" y="${mt - 6}" text-anchor="${side === 'L' ? 'end' : 'start'}">${esc(unit)}</text>`;
      }
      // x axis
      const base = mt + ph;
      svg += `<line class="chp-axis" x1="${ml}" x2="${ml + pw}" y1="${base}" y2="${base}"/>`;
      const tt = timeTicks(a, b, pw);
      const live = model.to - b < 1;
      for (const t of tt.ticks) {
        const px = x(t);
        if (live && ml + pw - px < 34) continue; // the "now" label owns the right end
        svg += `<line class="chp-axis" x1="${px.toFixed(1)}" x2="${px.toFixed(1)}" y1="${base}" y2="${base + 4}"/>`
          + `<text class="chp-tick" x="${px.toFixed(1)}" y="${base + 16}" text-anchor="middle">${clock(t, tt.seconds)}</text>`;
      }
      if (live) svg += `<text class="chp-tick" x="${ml + pw}" y="${base + 16}" text-anchor="end">now</text>`;
      // limits of the first series on the left axis: a status's own thresholds (the only amber / red on a chart)
      const lead = axes.L[0] || axes.R[0];
      const leadY = yfor[lead.axis === 'R' ? 'R' : 'L'];
      const rangeOf = ticksFor[lead.axis === 'R' ? 'R' : 'L'];
      for (const [name, v] of lead.limits || []) {
        if (v < rangeOf.lo || v > rangeOf.hi) continue;
        const kind = name.startsWith('error') ? 'error' : 'warn';
        const y = leadY(v).toFixed(1);
        svg += `<g data-diagnostic><line class="chp-lim ${kind}" x1="${ml}" x2="${ml + pw}" y1="${y}" y2="${y}"/>`
          + `<text class="chp-limt ${kind}" x="${ml + 4}" y="${(+y - 4).toFixed(1)}">${kind} ${name.endsWith('low') ? '<' : '>'} ${esc(fmt(v, lead.unit))}</text></g>`;
      }
      // the lines
      model.series.forEach((s) => {
        const yy = yfor[s.axis === 'R' ? 'R' : 'L'];
        const gap = s.period ? Math.max(3 * s.period, 5) : Infinity;
        const pts = decimate(s.points, a, b, pw);
        let d = ''; let prev = null; let open = false; const runs = []; let run = null;
        for (const [t, raw] of pts) {
          const v = asNum(raw);
          if (!isNum(v)) { open = false; prev = t; continue; }
          const broken = prev !== null && t - prev > gap;
          const px = x(Math.min(Math.max(t, a), b)); const py = yy(v);
          d += `${!open || broken ? 'M' : 'L'}${px.toFixed(1)} ${py.toFixed(1)} `;
          if (!open || broken) { run = []; runs.push(run); }
          run.push([px, py]);
          open = true; prev = t;
        }
        d = d.replace(/M([\d.]+) ([\d.]+) (?=M|$)/g, (_, px, py) => `M${px} ${py} L${Math.min(ml + pw, Number(px) + 2).toFixed(1)} ${py} `);
        if (this.o.area && s === model.series[0]) {
          for (const r of runs) if (r.length > 1) svg += `<path class="chp-ar chp-fill-s${Math.min(s.cls, 1)}" d="M${r[0][0].toFixed(1)} ${base} ${r.map((p) => `L${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')} L${r[r.length - 1][0].toFixed(1)} ${base}Z"/>`;
        }
        if (d) svg += `<path class="chp-ln chp-s${s.cls % 6}${s.dim ? ' dim' : ''}" data-series="${esc(s.id)}" d="${d.trim()}"/>`;
      });
      // the health timeline under the axis, on the same x scale (a status's honest spans; no data draws nothing)
      if (this.o.strip && model.levels) {
        const y0 = base + 22;
        svg += `<rect class="chp-lvbg" x="${ml}" y="${y0}" width="${pw}" height="${stripH}" rx="2"/>`
          + `<text class="chp-tick" x="${ml - 6}" y="${y0 + stripH - 3}" text-anchor="end">health</text>`;
        for (const span of model.levels) {
          if (!span.level || span.to < a || span.from > b) continue;
          const x1 = x(Math.max(span.from, a)); const x2 = x(Math.min(span.to, b));
          const tall = PROBLEM.has(span.level) ? stripH : Math.max(3, stripH * 0.3);
          svg += `<rect class="chp-lv ${span.level}"${PROBLEM.has(span.level) ? ' data-diagnostic' : ''} x="${x1.toFixed(1)}" y="${(y0 + stripH - tall).toFixed(1)}" width="${Math.max(1, x2 - x1).toFixed(1)}" height="${tall}"><title>${clock(span.from)}–${clock(span.to)} ${span.level}${span.message ? `: ${esc(span.message)}` : ''}</title></rect>`;
        }
      }
      svg += '<g class="chp-cur" hidden></g>';
      this.svg.innerHTML = svg;
      if (this.cursorT !== null && this.hover) this.showCursor(this.cursorT); else this.cursorT = null;
    }

    // --- cursor ---
    timeAt(event) {
      const g = this.geom; if (!g || !g.y) return null;
      const rect = this.svg.getBoundingClientRect();
      const px = event.clientX - rect.left;
      if (px < g.ml || px > g.ml + g.pw) return null;
      return g.a + ((px - g.ml) / g.pw) * (g.b - g.a);
    }
    move(event) {
      if (this.drag) { this.pan(event); return; }
      const t = this.timeAt(event);
      if (t === null) { this.leave(); return; }
      this.cursorT = t;
      this.showCursor(t);
    }
    leave() {
      this.cursorT = null;
      const g = this.svg.querySelector('.chp-cur');
      if (g) g.setAttribute('hidden', '');
      this.tip.hidden = true;
      if (this.o.onCursor) this.o.onCursor(null, []);
    }
    showCursor(t) {
      const model = this.model; const g = this.geom;
      if (!model || !g || !g.y || !model.series.length) return;
      const rows = [];
      let svg = '';
      let at = null;
      for (const s of model.series) {
        const nearest = nearestPoint(s.points, t);
        if (!nearest) continue;
        const [pt, raw] = nearest; const v = asNum(raw);
        const gap = s.period ? Math.max(3 * s.period, 5) : (g.b - g.a) / 20;
        if (!isNum(v) || Math.abs(pt - t) > gap) continue;
        const yy = g.y[s.axis === 'R' ? 'R' : 'L'];
        if (!yy) continue;
        const px = g.x(pt);
        if (at === null) at = pt;
        rows.push({ series: s, t: pt, v });
        svg += `<circle class="c${s.cls % 6}" cx="${px.toFixed(1)}" cy="${yy(v).toFixed(1)}" r="3.5"/>`;
      }
      const cx = g.x(Math.min(Math.max(at ?? t, g.a), g.b));
      const cur = this.svg.querySelector('.chp-cur');
      if (cur) { cur.innerHTML = `<line x1="${cx.toFixed(1)}" x2="${cx.toFixed(1)}" y1="${g.mt}" y2="${g.mt + g.ph}"/>${svg}`; cur.removeAttribute('hidden'); }
      if (this.o.tip) {
        if (!rows.length) { this.tip.hidden = true; } else {
          this.tip.innerHTML = `<div class="t">${clock(at ?? t)}</div>${rows.map((r) => `<div class="r"><span class="chp-sw chp-sw${r.series.cls % 6}"></span><b>${esc(r.series.label)}</b><i>${esc(fmt(r.v, r.series.unit))}</i></div>`).join('')}`;
          this.tip.hidden = false;
          const w = this.tip.offsetWidth;
          const left = cx + 12 + w > g.width ? cx - 12 - w : cx + 12;
          this.tip.style.left = `${Math.max(0, left).toFixed(0)}px`;
        }
      }
      if (this.o.onCursor) this.o.onCursor(at ?? t, rows);
    }

    // --- zoom and pan (o.zoom): the view is [a, b] inside the model's window ---
    clampView(a, b) {
      const full = [this.model.from, this.model.to];
      let span = Math.min(Math.max(b - a, 5), full[1] - full[0]);
      if (a < full[0]) a = full[0];
      if (a + span > full[1]) a = full[1] - span;
      return [a, a + span];
    }
    emitView(view) {
      const full = this.model ? [this.model.from, this.model.to] : [0, 0];
      const whole = view && view[0] <= full[0] + 0.5 && view[1] >= full[1] - 0.5;
      this.view = whole ? null : view;
      this.draw();
      if (this.o.onView) this.o.onView(this.view);
    }
    wheel(event) {
      if (!this.model) return;
      const t = this.timeAt(event);
      if (t === null) return;
      event.preventDefault();
      const [a, b] = this.visible();
      const k = event.deltaY < 0 ? 0.8 : 1.25;
      const na = t - (t - a) * k; const nb = t + (b - t) * k;
      this.emitView(this.clampView(na, nb));
    }
    zoomBy(k) {
      if (!this.model) return;
      const [a, b] = this.visible(); const mid = (a + b) / 2;
      this.emitView(this.clampView(mid - (mid - a) * k, mid + (b - mid) * k));
    }
    panBy(fraction) {
      if (!this.model) return;
      const [a, b] = this.visible(); const d = (b - a) * fraction;
      this.emitView(this.clampView(a + d, b + d));
    }
    reset() { if (this.model) this.emitView(null); }
    down(event) {
      if (event.button !== 0 || !this.model || !this.geom || !this.geom.y) return;
      const t = this.timeAt(event);
      if (t === null) return;
      this.drag = { x: event.clientX, view: this.visible().slice(), id: event.pointerId };
      this.root.setPointerCapture(event.pointerId);
      this.root.classList.add('panning');
    }
    pan(event) {
      const g = this.geom; const d = this.drag;
      if (!g || !d) return;
      const dt = -((event.clientX - d.x) / g.pw) * (d.view[1] - d.view[0]);
      this.emitView(this.clampView(d.view[0] + dt, d.view[1] + dt));
    }
    up(event) {
      if (!this.drag) return;
      try { this.root.releasePointerCapture(this.drag.id); } catch (_) { /* gone */ }
      this.drag = null;
      this.root.classList.remove('panning');
    }
  }
  // one pixel column keeps its first, lowest, highest and last point, so a dense series stays a faithful outline
  function decimate(points, a, b, width) {
    const inside = [];
    let before = null;
    for (const p of points) {
      if (p[0] < a) { before = p; continue; }
      if (p[0] > b) { inside.push(p); break; }
      if (before) { inside.push(before); before = null; }
      inside.push(p);
    }
    if (inside.length <= width * 2) return inside;
    const out = []; const bucket = (b - a) / width; let key = null; let cell = [];
    const flush = () => {
      if (!cell.length) return;
      const nums = cell.filter((p) => isNum(asNum(p[1])));
      if (nums.length < cell.length) { out.push(...cell); return; } // an unreadable value (a gap) is kept as it is
      const lo = nums.reduce((m, p) => (asNum(p[1]) < asNum(m[1]) ? p : m)); const hi = nums.reduce((m, p) => (asNum(p[1]) > asNum(m[1]) ? p : m));
      const set = [...new Set([cell[0], lo, hi, cell[cell.length - 1]])].sort((p, q) => p[0] - q[0]);
      out.push(...set);
    };
    for (const p of inside) { const k = Math.floor((p[0] - a) / bucket); if (k !== key) { flush(); cell = []; key = k; } cell.push(p); }
    flush();
    return out;
  }
  function nearestPoint(points, t) {
    let lo = 0; let hi = points.length - 1;
    if (hi < 0) return null;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (points[mid][0] < t) lo = mid + 1; else hi = mid; }
    const cand = [points[lo], points[lo - 1]].filter(Boolean).filter((p) => isNum(asNum(p[1])));
    if (!cand.length) return null;
    return cand.reduce((m, p) => (Math.abs(p[0] - t) < Math.abs(m[0] - t) ? p : m));
  }

  // ------------------------------------------------------------------- registry, wrap, events ----
  let ctx = null;
  let activeId = null;
  let mounted = false;
  const views = () => root.IOChartViews || {};
  const current = () => views()[activeId] || null;
  const ids = () => Object.keys(views()).sort((p, q) => ((views()[p].order ?? 50) - (views()[q].order ?? 50)) || p.localeCompare(q));

  // The one call a page makes. `html` is the sparkline as the page draws it today.
  function wrap(spec, html) {
    const view = current();
    const key = viewKey(spec);
    const attrs = view && view.attrs ? view.attrs(spec, key) : '';
    const tip = view && view.tip ? view.tip(spec, key) : null;
    const iconName = view && view.icon ? view.icon(spec, key) : '';
    const button = iconName ? `<button type="button" class="ch-btn${view.btnOn && view.btnOn(spec, key) ? ' on' : ''}" data-ch-btn tabindex="0" aria-label="${esc(tip || 'Enlarge')}">${icon(iconName)}</button>` : '';
    return `<span class="ch-spark" data-spark="${esc(JSON.stringify(spec))}" data-ch-key="${esc(key)}"${tip === null ? '' : ` title="${esc(tip)}"`}${attrs}><span class="ch-line">${html}${button}</span><span class="ch-host"></span></span>`;
  }

  // a persistent shadow root under a wrapper's host (the detail's morph() cannot reach into it)
  const roots = new WeakMap();
  function hostRoot(wrapper) {
    const host = wrapper && wrapper.querySelector(':scope > .ch-host');
    if (!host) return null;
    if (!roots.has(host)) {
      const shadow = host.attachShadow({ mode: 'open' });
      const style = document.createElement('style');
      style.textContent = CSS + extraCss();
      shadow.appendChild(style);
      roots.set(host, shadow);
    }
    return roots.get(host);
  }
  const extras = [];
  const extraCss = () => extras.join('\n');
  // CSS a proposal draws inside shadow roots too
  function shadowStyle(css) { extras.push(css); }

  function route(kind, event) {
    const view = current();
    if (!view || !view.on) return;
    const real = event.composedPath ? event.composedPath()[0] : event.target;
    const target = event.target && event.target.closest ? event.target : null;
    const el = target ? target.closest('.ch-spark') : null;
    const spec = el ? specOf(el) : null;
    const hit = { el, spec, key: el ? el.getAttribute('data-ch-key') : null, real, button: Boolean(real && real.closest && real.closest('[data-ch-btn]')) };
    let handled;
    try { handled = view.on(kind, event, hit); } catch (error) { console.error(`chart view ${activeId} ${kind}:`, error); }
    if (el && (kind === 'click' || kind === 'contextmenu' || kind === 'pointerdown')) {
      // a click on a sparkline (or in its enlarged chart) never reaches the row's own handler (it would open the diagnostic)
      event.stopPropagation();
      if (handled) event.preventDefault();
    }
  }

  let queued = false;
  function scheduleSync() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const view = current();
      if (view && view.sync) { try { view.sync(env); } catch (error) { console.error(`chart view ${activeId} sync:`, error); } }
    });
  }

  function select(id) {
    const next = views()[id] ? id : (views().c0 ? 'c0' : ids()[0] || null);
    if (next === activeId) return;
    const prev = current();
    if (prev && prev.unmount) { try { prev.unmount(env); } catch (error) { console.error(`chart view ${activeId} unmount:`, error); } }
    activeId = next;
    if (typeof document !== 'undefined') document.documentElement.dataset.chart = next || 'c0';
    const view = current();
    if (view && view.mount) { try { view.mount(env); } catch (error) { console.error(`chart view ${next} mount:`, error); } }
    if (ctx && ctx.redraw) ctx.redraw();
    scheduleSync();
  }

  // The shell calls this once, after the chart views are loaded: its hooks (see makeChartsCtx in app.js).
  function mount(shell) {
    ctx = shell;
    injectStyle();
    if (mounted) return;
    mounted = true;
    for (const kind of ['click', 'contextmenu', 'pointerover', 'pointerout', 'pointerdown', 'keydown']) document.addEventListener(kind, (event) => route(kind, event), true);
    const detail = document.getElementById('detail');
    if (detail) new MutationObserver(scheduleSync).observe(detail, { childList: true, subtree: true });
    setInterval(() => {
      const view = current();
      if (view && view.update) { try { view.update(env); } catch (error) { console.error(`chart view ${activeId} update:`, error); } }
    }, 1000);
  }

  // c0: today's page, nothing enlarges (the default until a proposal is picked)
  root.IOChartViews = root.IOChartViews || {};
  root.IOChartViews.c0 = { name: 'Sparkline only', description: 'Today: the sparkline stays as small as it is; nothing enlarges.', order: 0, mount() {}, unmount() {} };

  const env = {
    wrap, select, mount, ids, hostRoot, shadowStyle, addStyle, scheduleSync, resolve, plotModel, statsOf, lastOf, specKey, viewKey, specOf, historyUrl,
    Plot, store, icon, ICONS, esc, num, fmt, unitText, clock, spanText, isNum, asNum, niceTicks, limitsOf,
    get ctx() { return ctx; }, get active() { return activeId; },
  };
  root.IOCharts = env;
  injectStyle();
})(typeof window !== 'undefined' ? window : globalThis);
