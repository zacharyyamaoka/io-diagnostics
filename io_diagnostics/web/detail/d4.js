// Detail layout d4 · Resource monitor (round 3 proposal; contract: docs/round2-seams.md §6).
// The ROS2 System Webview's Resource view (Pasted image 20261023084333) and nvtop, stacked top to bottom: first the
// diagnostics status table (level · name · message, quiet), then one full-width row per measurement group (name, big
// current value, a sparkline over the app-wide window with its limits as faint lines), then the rest folded. A status
// or a measurement opens that diagnostic's own page: a larger trend with its limits, its message history, values and
// metadata. WHY stacked rows and not the webview's cards: Zach, 2026-10-23: "the eye easily reads top to bottom not in
// these grid patterns". The default for the Computer (round 3.5); the utilities are the page's, in the header's
// Utilities ▾. The history and health bars come from the page's shared cache (ctx.history, ctx.bar, every 3 s for the
// selection and its parts); a folded part's node is read here; the diagnostic page is the page's drill
// (ctx.diagnostic: back / forward work).
'use strict';
(function (root) {
  const ID = 'd4';
  const POLL_MS = 3000;
  const BINS = 60;
  const MAX_PARTS = 8;
  const PROBLEM = new Set(['warn', 'error', 'stale', 'unknown']);
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };

  // ------------------------------------------------------------------------------- styles ----
  // Tokens only, no colour literals. Warn / error colours appear only inside [data-diagnostic] (the warn-colours gate).
  const CSS = `
.d4-roll { font-size: 12.5px; color: var(--io-muted); margin: 2px 0 6px; }
.d4-roll b { color: var(--io-text-strong); font-weight: 600; }
.d4-st { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 12.5px; }
.d4-st td { padding: 4px 6px 4px 0; border-top: 1px solid var(--io-border-soft); vertical-align: top; min-width: 0; }
.d4-st tr:first-child td { border-top: 0; }
.d4-st tr[data-d4-diag] { cursor: pointer; }
.d4-st tr[data-d4-diag]:hover td { background: var(--io-hover); }
.d4-st .c-lv { width: 52px; white-space: nowrap; color: var(--io-faint); font-size: 11px; }
.d4-st .c-lv .shape { width: 12px; height: 12px; margin-right: 3px; }
.d4-st .c-name { width: 34%; color: var(--io-text); overflow-wrap: anywhere; }
.d4-st .c-msg { color: var(--io-muted); overflow-wrap: anywhere; }
.d4-st .c-strip { width: 64px; padding-top: 7px; }
.d4-st .c-age { width: 40px; text-align: right; color: var(--io-faint); font-family: var(--io-font-mono); font-size: 11px; white-space: nowrap; }
.d4-st .cause { display: block; color: var(--io-faint); font-size: 11.5px; }
.d4-lv-warn { color: var(--io-warn-text); } .d4-lv-error { color: var(--io-error-text); }
.d4-lv-stale { color: var(--io-stale-text); } .d4-lv-unknown { color: var(--io-unknown-text); }
.d4-strip { display: block; width: 100%; height: 8px; }
.d4-strip .b-ok, .d4-bar .b-ok { fill: var(--io-border); }
.d4-strip .b-warn, .d4-bar .b-warn { fill: var(--io-warn); } .d4-strip .b-error, .d4-bar .b-error { fill: var(--io-error); }
.d4-strip .b-stale, .d4-bar .b-stale { fill: var(--io-stale); } .d4-strip .b-unknown, .d4-bar .b-unknown { fill: var(--io-unknown); }
.d4-strip .b-none, .d4-bar .b-none { fill: var(--io-border-soft); }
.d4-health { margin-top: 10px; }
.d4-hrow { display: grid; grid-template-columns: minmax(90px, 34%) 1fr; gap: 0 10px; align-items: center; font-size: 12px; padding: 2px 0; }
.d4-hrow .n { color: var(--io-muted); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.d4-hrow .n.depth1 { padding-left: 12px; }
.d4-hrow .n a { color: var(--io-accent-text); cursor: pointer; text-decoration: var(--io-link-decoration); }
.d4-bar { display: block; width: 100%; height: 10px; }
.d4-hnone { font-size: 11.5px; color: var(--io-faint); margin-top: 4px; }
.d4-hnone a { color: var(--io-accent-text); cursor: pointer; text-decoration: var(--io-link-decoration); }
.d4-axis { display: flex; justify-content: space-between; color: var(--io-faint); font-size: 10.5px; font-family: var(--io-font-mono); }
.d4-g { padding: 8px 0 6px; border-top: 1px solid var(--io-border-soft); }
.d4-g:first-child { border-top: 0; }
.d4-gh { display: grid; grid-template-columns: minmax(120px, 30%) 1fr; gap: 0 12px; align-items: end; cursor: pointer; border-radius: var(--io-radius-sm); }
.d4-gh:hover, .d4-r:hover { background: var(--io-hover); }
.d4-gname { font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: var(--io-muted); font-weight: 600; display: flex; align-items: center; gap: 5px; min-width: 0; }
.d4-gname span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.d4-gname .shape { width: 12px; height: 12px; }
.d4-big { font-family: var(--io-font-mono); font-size: 22px; line-height: 1.15; color: var(--io-text-strong); font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.d4-big small { font-size: 12px; color: var(--io-muted); margin-left: 3px; }
.d4-big .lbl { font-size: 10.5px; color: var(--io-faint); margin: 0 8px 0 2px; }
.d4-gsub { font-size: 11.5px; color: var(--io-faint); margin-top: 2px; overflow-wrap: anywhere; }
.d4-spark { display: block; width: 100%; overflow: visible; }
.d4-spark .ln { fill: none; stroke: var(--io-text); stroke-width: 1.4; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
.d4-spark .ln.alt { stroke: var(--io-accent); }
.d4-spark .ln.dim { stroke: var(--io-faint); stroke-width: 1; }
.d4-spark .ar { fill: var(--io-control); opacity: .7; stroke: none; }
.d4-spark .lim { stroke: var(--io-muted); stroke-width: 1; vector-effect: non-scaling-stroke; stroke-dasharray: 4 3; opacity: .7; }
.d4-spark .lim.err { stroke-dasharray: 1 2; }
.d4-spark .base { stroke: var(--io-border-soft); stroke-width: 1; vector-effect: non-scaling-stroke; }
.d4-cores { display: flex; gap: 2px; height: 26px; margin: 8px 0 2px; align-items: stretch; }
.d4-cores .core { flex: 1 1 0; min-width: 3px; background: var(--io-control); border-radius: 2px; position: relative; overflow: hidden; }
.d4-cores .core i { position: absolute; left: 0; right: 0; bottom: 0; background: var(--io-muted); }
.d4-cores-cap { font-size: 10.5px; color: var(--io-faint); font-family: var(--io-font-mono); display: flex; justify-content: space-between; }
.d4-rows { margin-top: 4px; }
.d4-r { display: grid; grid-template-columns: minmax(120px, 30%) 96px 1fr; gap: 0 12px; align-items: center; font-size: 12px; padding: 2px 0; cursor: pointer; border-radius: var(--io-radius-sm); }
.d4-r .n { color: var(--io-muted); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: flex; align-items: center; gap: 4px; }
.d4-r .n .shape { width: 11px; height: 11px; }
.d4-r .v { font-family: var(--io-font-mono); color: var(--io-text); text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.d4-more { font-size: 11.5px; color: var(--io-faint); padding: 2px 0; }
.d4-vals { font-size: 12.5px; }
.d4-vals h4 { margin: 8px 0 3px; font-size: 12px; font-weight: 600; color: var(--io-text); }
.d4-vals h4:first-child { margin-top: 2px; }
.d4-back { display: inline-flex; align-items: center; gap: 4px; margin: 8px 0 0; font-size: 12.5px; color: var(--io-accent-text); cursor: pointer; background: none; border: 0; padding: 2px 0; font-family: inherit; text-decoration: var(--io-link-decoration); }
.d4-dh { margin: 10px 0 4px; }
.d4-dh .t { font-family: var(--io-font-display); font-size: 16px; font-weight: 600; color: var(--io-text-strong); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.d4-dh .t .shape { width: 15px; height: 15px; }
.d4-dh .m { font-size: 13px; color: var(--io-text); margin-top: 3px; overflow-wrap: anywhere; }
.d4-dh .m .code { font-family: var(--io-font-mono); color: var(--io-muted); margin-left: 6px; font-size: 12px; }
.d4-chart { margin: 8px 0 10px; }
.d4-chart-h { display: flex; justify-content: space-between; gap: 10px; font-size: 12px; color: var(--io-muted); }
.d4-chart-h .v { font-family: var(--io-font-mono); color: var(--io-text-strong); }
.d4-plot { position: relative; margin: 10px 0 0 44px; }
.d4-plot .yl { position: absolute; left: -44px; width: 40px; text-align: right; font-family: var(--io-font-mono); font-size: 10.5px; color: var(--io-faint); transform: translateY(-50%); white-space: nowrap; }
.d4-plot .ll { position: absolute; right: 2px; font-size: 10.5px; color: var(--io-muted); transform: translateY(-100%); background: var(--io-surface-alt); padding: 0 3px; white-space: nowrap; }
.d4-plot + .d4-axis { margin-left: 44px; }
.d4-msgs { width: 100%; border-collapse: collapse; font-size: 12px; table-layout: fixed; }
.d4-msgs td { padding: 3px 6px 3px 0; border-top: 1px solid var(--io-border-soft); vertical-align: top; }
.d4-msgs .tm { width: 132px; font-family: var(--io-font-mono); color: var(--io-faint); font-size: 11px; white-space: nowrap; }
.d4-msgs .lv { width: 62px; white-space: nowrap; color: var(--io-faint); }
.d4-msgs .lv .shape { width: 11px; height: 11px; margin-right: 3px; }
.d4-msgs .ms { color: var(--io-text); overflow-wrap: anywhere; }
.d4-msgs .ms.none { color: var(--io-faint); font-style: italic; }
.d4-msgs .ms .code { font-family: var(--io-font-mono); color: var(--io-muted); margin-left: 6px; }
.d4-mt { width: 100%; border-collapse: collapse; font-size: 12px; }
.d4-mt th { text-align: left; font-weight: 500; color: var(--io-faint); font-size: 11px; padding: 2px 6px 2px 0; }
.d4-mt td { padding: 2px 6px 2px 0; border-top: 1px solid var(--io-border-soft); font-family: var(--io-font-mono); color: var(--io-text); overflow-wrap: anywhere; }
.d4-mt td:first-child { font-family: var(--io-font-ui); color: var(--io-muted); }
.d4-quiet { color: var(--io-faint); font-size: 12px; }
.d4-why { font-size: 12.5px; } .d4-why div { margin: 2px 0; }
`;
  function injectStyle() {
    if (typeof document === 'undefined' || document.querySelector(`style[data-layout="${ID}"]`)) return;
    const style = document.createElement('style');
    style.dataset.layout = ID;
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  // ------------------------------------------------------------------------ history cache ----
  // path -> {at, loading, data, error}: a folded part's node detail; the history and the health bar are the page's
  // shared cache (ctx.history, ctx.bar), the same entry shape
  const cache = { node: new Map() };
  const URLS = { node: (path) => `/api/v1/node?path=${encodeURIComponent(path)}` };
  function want(kind, path) {
    if (kind === 'hist') return last.ctx.history(path, { everyMs: POLL_MS });
    if (kind === 'bar') return last.ctx.bar(path, BINS, { everyMs: POLL_MS });
    const map = cache[kind];
    const known = map.get(path);
    const now = Date.now();
    if (known && (known.loading || now - known.at < POLL_MS)) return known;
    const entry = { ...(known || {}), loading: true, at: now };
    map.set(path, entry);
    fetch(URLS[kind](path)).then((response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
      .then((data) => map.set(path, { at: Date.now(), loading: false, data, error: null }))
      .catch((error) => map.set(path, { at: Date.now(), loading: false, data: (known && known.data) || null, error: String(error.message || error) }));
    return entry;
  }
  const got = (kind, path) => (kind === 'node' ? (cache.node.get(path) || {}).data || null : want(kind, path).data || null);

  // ------------------------------------------------------------------------------- state ----
  let last = null;

  // A status or a measurement opens its diagnostic's page through the page's drill (one browser-history entry); a part's
  // measurement (it also carries data-go) selects that part and opens its diagnostic there, in ONE entry: this handler
  // runs before the page's and stops the click.
  function wire(container) {
    if (container.dataset.d4Wired) return;
    container.dataset.d4Wired = '1';
    container.addEventListener('click', (event) => {
      if (!last || !container.querySelector('.d4-root')) return;
      const t = event.target;
      const back = t.closest('[data-d4-back]');
      const diag = t.closest('[data-d4-diag]');
      if (!back && !diag) return;
      event.stopPropagation();
      if (back) { last.ctx.closeDiagnostic(); return; }
      last.ctx.openDiagnostic(diag.dataset.d4Diag, { path: diag.dataset.d4Path, select: Boolean(diag.dataset.go) });
    });
    container.addEventListener('keydown', (event) => {
      if (!last || !container.querySelector('.d4-root')) return;
      if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-d4-diag]')) { event.preventDefault(); event.target.click(); }
      // the header's Utilities ▾ menu or its dry-run sheet take their Escape first (the page's handler)
      if (event.key === 'Escape' && last.ctx.diagnostic && !container.querySelector('.ent-menu, .ent-sheet')) { const back = container.querySelector('[data-d4-back]'); if (back) { event.stopPropagation(); back.click(); } }
    });
  }

  // ------------------------------------------------------------------------------ helpers ----
  const UNITS = { GiBy: 'GiB', MiBy: 'MiB', KiBy: 'KiB', By: 'B', Cel: '°C', 1: '', '%': '%' };
  const unitText = (unit) => (unit in UNITS ? UNITS[unit] : unit || '');
  function num(value) {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    const v = Number(value);
    if (!Number.isFinite(v)) return String(value);
    const a = Math.abs(v);
    if (a === 0) return '0';
    if (a >= 1000) return v.toFixed(0);
    if (a >= 100) return v.toFixed(0);
    if (a >= 10) return v.toFixed(1);
    if (a >= 0.01) return v.toFixed(2);
    return v.toPrecision(2);
  }
  // display only: the raw `text` the host sent stays on the diagnostic page's measurement table
  const fmt = (value, unit) => { const u = unitText(unit); return `${num(value)}${u ? (u === '%' || u === '°C' ? ` ${u}` : ` ${u}`) : ''}`; };
  const clock = (t) => { const d = new Date(t * 1000); return Number.isNaN(d.getTime()) ? '—' : d.toTimeString().slice(0, 8); };
  const spanText = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
  const worst = (levels) => levels.filter(Boolean).reduce((a, b) => ((RANK[b] ?? -1) > (RANK[a] ?? -1) ? b : a), 'ok');
  const lvClass = (level) => (PROBLEM.has(level) ? ` class="d4-lv-${level}"` : '');
  // a problem value carries data-diagnostic: it is a true diagnostic result, the only place warn colours may show
  const tinted = (level, html) => (PROBLEM.has(level) ? `<span data-diagnostic${lvClass(level)}>${html}</span>` : html);
  const limitsOf = (m) => [['warn_low', m.warn_low], ['warn_high', m.warn_high], ['error_low', m.error_low], ['error_high', m.error_high]]
    .filter(([, v]) => typeof v === 'number');
  const limitWords = (m) => {
    const words = [];
    if (typeof m.warn_high === 'number') words.push(`warn > ${fmt(m.warn_high, m.unit)}`);
    if (typeof m.error_high === 'number') words.push(`error > ${fmt(m.error_high, m.unit)}`);
    if (typeof m.warn_low === 'number') words.push(`warn < ${fmt(m.warn_low, m.unit)}`);
    if (typeof m.error_low === 'number') words.push(`error < ${fmt(m.error_low, m.unit)}`);
    return words.join(' · ');
  };
  // "bam-GPU: CPU Information" -> "CPU Information" on the bam-GPU page; a name of another entity's stays whole
  function shortName(name, ctx) {
    const prefixes = [ctx.node && ctx.node.name, ctx.row && ctx.row.label].filter(Boolean).map((p) => `${p}: `);
    const hit = prefixes.find((p) => name.startsWith(p) && name.length > p.length);
    return hit ? name.slice(hit.length) : name;
  }
  const attr = (h, path, status, extra = '') => `data-d4-diag="${h.esc(status)}" data-d4-path="${h.esc(path)}" tabindex="0" role="button"${extra}`;

  // ------------------------------------------------------------------------------ drawing ----
  // A sparkline over [from, to]: each series a line, broken where a value is unreadable or where the status went
  // stale (the host's own rule: max(3 x expected period, 5 s) after an arrival); its limits as faint dashed lines.
  // WHY a percentage keeps 0-100: a CPU at 12 % must not look full (the ROS webview's bars do the same).
  function range(series, opts) {
    const values = series.flatMap((s) => s.points.map((p) => (typeof p[1] === 'boolean' ? Number(p[1]) : p[1])).filter((v) => typeof v === 'number' && Number.isFinite(v)));
    const unit = series[0] && series[0].unit;
    if (unit === '%' && !opts.auto) return [0, 100];
    if (!values.length) return [0, 1];
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    const span = Math.max(hi - lo, Math.abs(hi) * 0.35, 1e-9);
    for (const s of series) {
      for (const [, v] of limitsOf(s)) if (v >= lo - span && v <= hi + span) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
    if (lo >= 0 && lo < span) lo = 0; // a positive quantity reads from zero when that does not flatten it
    if (hi === lo) { hi += Math.abs(hi) * 0.1 || 1; lo -= Math.abs(lo) * 0.1 || 0; }
    const pad = (hi - lo) * 0.08;
    return [lo === 0 ? 0 : lo - pad, hi + pad];
  }
  function sparkPath(points, from, to, lo, hi, w, h, period) {
    const gap = period ? Math.max(3 * period, 5) : Infinity;
    let d = '';
    let prev = null;
    let open = false;
    const xs = [];
    for (const [t, raw] of points) {
      const v = typeof raw === 'boolean' ? Number(raw) : raw;
      if (t < from) { prev = t; continue; }
      if (typeof v !== 'number' || !Number.isFinite(v)) { open = false; prev = t; continue; }
      const x = ((t - from) / (to - from)) * w;
      const y = h - ((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * h;
      const broken = prev !== null && t - prev > gap;
      d += `${!open || broken ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)} `;
      xs.push([x, y, !open || broken]);
      open = true;
      prev = t;
    }
    // a lone point (first arrival, or after a gap) draws as a short tick so it is visible
    d = d.replace(/M([\d.]+) ([\d.]+) (?=M|$)/g, (_, x, y) => `M${x} ${y} L${Math.min(w, Number(x) + 2).toFixed(1)} ${y} `);
    return { d: d.trim(), xs };
  }
  function spark(series, from, to, { h = 30, area = true, auto = false } = {}) {
    const w = 300;
    const live = series.filter((s) => s && s.points);
    if (!live.length || !(to > from)) return `<svg class="d4-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="height:${h}px"><line class="base" x1="0" y1="${h}" x2="${w}" y2="${h}"/></svg>`;
    const [lo, hi] = range(live, { auto });
    const yOf = (v) => h - ((v - lo) / (hi - lo)) * h;
    let body = `<line class="base" x1="0" y1="${h}" x2="${w}" y2="${h}"/>`;
    for (const s of live.slice(0, 1)) {
      for (const [k, v] of limitsOf(s)) if (v >= lo && v <= hi) body += `<line class="lim${k.startsWith('error') ? ' err' : ''}" x1="0" x2="${w}" y1="${yOf(v).toFixed(1)}" y2="${yOf(v).toFixed(1)}"/>`;
    }
    live.forEach((s, at) => {
      const { d, xs } = sparkPath(s.points, from, to, lo, hi, w, h, s.period);
      if (!d) return;
      if (area && at === 0 && xs.length > 1) {
        // one area per unbroken run, down to the baseline
        const runs = [];
        for (const p of xs) { if (p[2] || !runs.length) runs.push([]); runs[runs.length - 1].push(p); }
        for (const run of runs) if (run.length > 1) body += `<path class="ar" d="M${run[0][0].toFixed(1)} ${h} ${run.map((p) => `L${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')} L${run[run.length - 1][0].toFixed(1)} ${h}Z"/>`;
      }
      body += `<path class="ln${s.style ? ` ${s.style}` : ''}" d="${d}"/>`;
    });
    return `<svg class="d4-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="height:${h}px" aria-hidden="true">${body}</svg>`;
  }
  // One status's own level timeline (the honest spans of /api/v1/history): no data draws nothing.
  function strip(levels, from, to, cls = 'd4-strip', h = 8) {
    if (!levels || !(to > from)) return '';
    const w = 300;
    const rects = levels.filter((span) => span.level).map((span) => {
      const x = ((Math.max(span.from, from) - from) / (to - from)) * w;
      const x2 = ((Math.min(span.to, to) - from) / (to - from)) * w;
      const tall = PROBLEM.has(span.level) ? h : Math.max(2, h * 0.3);
      return `<rect class="b-${span.level}" x="${x.toFixed(1)}" y="${(h - tall).toFixed(1)}" width="${Math.max(0.8, x2 - x).toFixed(1)}" height="${tall.toFixed(1)}"><title>${clock(span.from)}–${clock(span.to)} ${span.level}${span.message ? `: ${span.message.replace(/[<&"]/g, '')}` : ''}</title></rect>`;
    }).join('');
    return `<svg class="${cls}" data-diagnostic viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="height:${h}px">${rects}</svg>`;
  }
  // The health bar of /api/v1/history/bar: the worst level held per bin; null = nothing held, drawn as nothing.
  function healthBar(bar) {
    const w = 300;
    const bw = w / bar.length;
    // WHY a thin grey band for OK and full height only for a problem: quiet by default, the eye finds the tall marks
    const rects = bar.map((level, at) => {
      if (!level) return '';
      const tall = PROBLEM.has(level) ? 10 : 3;
      return `<rect class="b-${level}" x="${(at * bw).toFixed(2)}" y="${10 - tall}" width="${Math.max(0.6, bw - 0.6).toFixed(2)}" height="${tall}"/>`;
    }).join('');
    return `<svg class="d4-bar" data-diagnostic viewBox="0 0 ${w} 10" preserveAspectRatio="none">${rects}</svg>`;
  }
  const axis = (seconds) => `<div class="d4-axis"><span>−${spanText(seconds)}</span><span>−${spanText(seconds / 2)}</span><span>now</span></div>`;

  // ------------------------------------------------------------------------- measurements ----
  // One item per (status, measurement key): the current value (the node's check) and the points (its history).
  function itemsOf(path, nodeJson, hist) {
    const statuses = new Map(((hist && hist.statuses) || []).map((s) => [s.name, s]));
    const out = [];
    for (const check of (nodeJson && nodeJson.checks) || []) {
      const hs = statuses.get(check.name);
      const series = new Map(((hs && hs.measurements) || []).map((m) => [m.key, m]));
      for (const m of check.measurements || []) {
        const s = series.get(m.key);
        out.push({ path, status: check.name, check, key: m.key, value: m.value, unit: m.unit, text: m.text, level: m.level,
          warn_low: m.warn_low, warn_high: m.warn_high, error_low: m.error_low, error_high: m.error_high,
          points: s ? s.points : [], period: check.expected_period_s });
      }
    }
    return out;
  }
  const keyed = (items, key) => items.find((item) => item.key === key) || null;
  const statusItems = (items, status) => items.filter((item) => item.status === status);

  // The Computer's groups, in the ROS webview's order (CPU, load, memory, swap, disks, temperatures, GPU, network,
  // USB bandwidth), keyed on the computer plugin's ROS diagnostic_common_diagnostics names; anything else is generic.
  function computerGroups(items, ctx) {
    const byShort = new Map();
    for (const item of items) {
      const short = shortName(item.status, ctx);
      if (!byShort.has(short)) byShort.set(short, []);
      byShort.get(short).push(item);
    }
    const groups = [];
    const used = new Set();
    const take = (short) => { used.add(short); return byShort.get(short) || []; };
    const cpu = take('CPU Information');
    if (cpu.length) {
      const avg = keyed(cpu, 'CPU Load Average');
      const cores = cpu.filter((item) => /^CPU \d+ Load$/.test(item.key)).sort((a, b) => parseInt(a.key.slice(4), 10) - parseInt(b.key.slice(4), 10));
      const max = keyed(cpu, 'CPU max core load');
      if (avg) {
        groups.push({ id: 'cpu', title: 'CPU', items: [avg], big: avg, spark: [avg, max && { ...max, style: 'dim' }].filter(Boolean),
          sub: [max && `worst core ${fmt(max.value, max.unit)}`, cores.length && `${cores.length} cores`, limitWords(avg)].filter(Boolean).join(' · '),
          extra: cores.length ? coreBars(cores, ctx.h) : '' });
      }
      const loads = ['load 1 min', 'load 5 min', 'load 15 min'].map((key) => keyed(cpu, key)).filter(Boolean);
      if (loads.length) {
        groups.push({ id: 'load', title: 'Load average', items: loads,
          bigHtml: loads.map((item) => tinted(item.level, ctx.h.esc(num(item.value)))).join('<span class="lbl">·</span>'),
          spark: loads.map((item, at) => ({ ...item, style: at ? 'dim' : '' })), sparkAuto: true,
          sub: 'runnable tasks, 1 / 5 / 15 min' });
      }
    }
    const ram = take('RAM Information');
    if (ram.length) {
      const pct = keyed(ram, 'RAM Load Average');
      const v = (key) => keyed(ram, key);
      const ramUsed = v('RAM used'); const total = v('RAM total');
      if (pct) {
        groups.push({ id: 'ram', title: 'Memory', items: [pct], big: pct, spark: [pct],
          sub: [ramUsed && total && `${fmt(ramUsed.value, ramUsed.unit)} of ${fmt(total.value, total.unit)} used`,
            v('buffers') && `buffers ${fmt(v('buffers').value, v('buffers').unit)}`, v('cached') && `cached ${fmt(v('cached').value, v('cached').unit)}`,
            v('RAM available') && `available ${fmt(v('RAM available').value, v('RAM available').unit)}`, limitWords(pct)].filter(Boolean).join(' · ') });
      }
      const swap = v('swap used %');
      if (swap) {
        const su = v('swap used'); const st = v('swap total');
        groups.push({ id: 'swap', title: 'Swap', items: [swap], big: swap, spark: [swap],
          sub: [su && st && `${fmt(su.value, su.unit)} of ${fmt(st.value, st.unit)}`, limitWords(swap)].filter(Boolean).join(' · ') });
      }
    }
    const disks = take('HD Usage');
    if (disks.length) {
      const mounts = disks.filter((item) => / available$/.test(item.key));
      const fullest = mounts.reduce((a, b) => (a && a.value <= b.value ? a : b), null);
      groups.push({ id: 'disks', title: 'Disks', items: disks, big: fullest, bigSuffix: fullest ? ` free · ${fullest.key.replace(/ available$/, '')}` : '',
        spark: fullest ? [fullest] : [],
        sub: fullest ? `fullest mount · ${limitWords(fullest)}` : '',
        rows: mounts.map((item) => {
          const mount = item.key.replace(/ available$/, '');
          const free = keyed(disks, `${mount} free`); const total = keyed(disks, `${mount} total`);
          return { item, label: mount, value: `${fmt(item.value, item.unit)} free`, title: free && total ? `${fmt(free.value, free.unit)} free of ${fmt(total.value, total.unit)}` : '' };
        }) });
    }
    const temps = [...byShort.keys()].filter((short) => /^Temperature /.test(short)).flatMap((short) => take(short).map((item) => ({ item, chip: short.replace(/^Temperature /, '') })));
    if (temps.length) {
      // the hottest relative to its own warn limit leads (a 75 °C CPU at warn 85 is closer than a 52 °C NVMe at 81)
      const lead = temps.reduce((a, b) => {
        const margin = (t) => (typeof t.item.warn_high === 'number' ? t.item.warn_high - t.item.value : 1e9 - t.item.value);
        return margin(b) < margin(a) ? b : a;
      });
      groups.push({ id: 'temps', title: 'Temperatures', items: temps.map((t) => t.item), big: lead.item, bigSuffix: ` · ${lead.chip} ${lead.item.key}`,
        spark: [lead.item], sub: `closest to its limit · ${limitWords(lead.item)}`,
        rows: temps.map((t) => ({ item: t.item, label: `${t.chip} · ${t.item.key}`, value: fmt(t.item.value, t.item.unit), title: limitWords(t.item) })) });
    }
    for (const short of [...byShort.keys()].filter((s) => /^GPU \d+/.test(s))) {
      const gpu = take(short);
      const util = keyed(gpu, 'utilization');
      const memPct = keyed(gpu, 'memory used %'); const memUsed = keyed(gpu, 'memory used'); const memTotal = keyed(gpu, 'memory total');
      const temp = keyed(gpu, 'temperature');
      const rows = [
        memPct && { item: memPct, label: 'memory', value: fmt(memPct.value, memPct.unit), title: memUsed && memTotal ? `${fmt(memUsed.value, memUsed.unit)} of ${fmt(memTotal.value, memTotal.unit)}` : '' },
        temp && { item: temp, label: 'temperature', value: fmt(temp.value, temp.unit), title: limitWords(temp) },
      ].filter(Boolean);
      groups.push({ id: `gpu-${short}`, title: short, items: gpu, big: util || gpu[0], bigSuffix: util ? ' utilization' : '', spark: [util || gpu[0]],
        sub: [memUsed && memTotal && `${fmt(memUsed.value, memUsed.unit)} of ${fmt(memTotal.value, memTotal.unit)}`, temp && fmt(temp.value, temp.unit)].filter(Boolean).join(' · '), rows });
    }
    const net = take('Network Usage');
    if (net.length) {
      const ifaces = [...new Set(net.filter((item) => / rx$/.test(item.key)).map((item) => item.key.replace(/ rx$/, '')))];
      const rows = ifaces.map((name) => {
        const rx = keyed(net, `${name} rx`); const tx = keyed(net, `${name} tx`); const speed = keyed(net, `${name} link speed`);
        return { item: rx, label: name, value: `↓${num(rx.value)} ↑${tx ? num(tx.value) : '—'}`, spark: [rx, tx && { ...tx, style: 'alt' }].filter(Boolean),
          title: `rx ${fmt(rx.value, rx.unit)} · tx ${tx ? fmt(tx.value, tx.unit) : '—'}${speed ? ` · link ${fmt(speed.value, speed.unit)}` : ''}`, auto: true };
      });
      const first = rows[0];
      groups.push({ id: 'net', title: 'Network', items: net,
        bigHtml: first ? `↓${ctx.h.esc(num(first.item.value))}<span class="lbl"> </span>↑${ctx.h.esc(first.spark[1] ? num(first.spark[1].value) : '—')}<small>${ctx.h.esc(unitText(first.item.unit))}</small>` : '—',
        spark: first ? first.spark : [], sparkAuto: true, item0: first && first.item,
        sub: first ? `${first.label} · rx solid, tx accent${keyed(net, `${first.label} link speed`) ? ` · link ${fmt(keyed(net, `${first.label} link speed`).value, 'Mbit/s')}` : ''}` : '',
        rows: rows.length > 1 ? rows : [] });
    }
    const usb = take('USB Bandwidth');
    if (usb.length) {
      const buses = usb.filter((item) => / claimed %$/.test(item.key));
      const busiest = buses.reduce((a, b) => (a && a.value >= b.value ? a : b), null);
      const capOf = (bus) => keyed(usb, `${bus} capacity`);
      const claimedOf = (bus) => keyed(usb, `${bus} claimed`);
      groups.push({ id: 'usb', title: 'USB bus bandwidth', items: usb, big: busiest, bigSuffix: busiest ? ` · ${busiest.key.replace(/ claimed %$/, '')}` : '', spark: busiest ? [busiest] : [],
        sub: busiest ? `busiest bus, periodic bandwidth claimed · ${limitWords(busiest)}` : '',
        rows: buses.map((item) => {
          const bus = item.key.replace(/ claimed %$/, '');
          const cap = capOf(bus); const claimed = claimedOf(bus);
          return { item, label: `${bus}${cap ? ` · ${fmt(cap.value, cap.unit)}` : ''}`, value: fmt(item.value, item.unit), title: claimed && cap ? `${fmt(claimed.value, claimed.unit)} of ${fmt(cap.value, cap.unit)} claimed` : '' };
        }) });
    }
    const rest = items.filter((item) => !used.has(shortName(item.status, ctx)));
    return groups.concat(genericGroups(rest, ctx));
  }

  // Any entity: one group per status with measurements; its first measurement is the big value, the rest are rows.
  function genericGroups(items, ctx, label = null) {
    const order = [];
    const by = new Map();
    for (const item of items) {
      const k = `${item.path}#${item.status}`;
      if (!by.has(k)) { by.set(k, []); order.push(k); }
      by.get(k).push(item);
    }
    return order.map((k) => {
      const list = by.get(k);
      const head = list[0];
      const owner = label ? label(head) : '';
      const name = owner && head.status.startsWith(`${owner}: `) ? head.status.slice(owner.length + 2) : shortName(head.status, ctx);
      return { id: `g-${k}`, title: owner ? `${owner} · ${name}` : name, items: list, big: head, bigSuffix: list.length > 1 ? ` ${head.key}` : '',
        spark: [head], sub: [list.length === 1 ? head.key : '', limitWords(head)].filter(Boolean).join(' · '),
        rows: list.slice(1).map((item) => ({ item, label: item.key, value: fmt(item.value, item.unit), title: limitWords(item) })) };
    });
  }

  function coreBars(cores, h) {
    const bars = cores.map((item) => {
      const v = typeof item.value === 'number' ? Math.max(0, Math.min(100, item.value)) : 0;
      return `<span class="core" title="${h.esc(`${item.key.replace(' Load', '')} · ${fmt(item.value, item.unit)}`)}"><i style="height:${v.toFixed(0)}%"></i></span>`;
    }).join('');
    return `<div class="d4-cores" aria-label="per-core load">${bars}</div><div class="d4-cores-cap"><span>core 0</span><span>per-core load now</span><span>core ${cores.length - 1}</span></div>`;
  }

  function groupHtml(group, win, ctx) {
    const h = ctx.h;
    const lead = group.big || group.item0 || group.items[0];
    const level = worst(group.items.map((item) => item.level).concat(group.items.map((item) => item.check.effective_level)));
    const goAttr = (item) => (item.path !== ctx.node.path ? ` data-go="${h.esc(item.path)}"` : '');
    const bigHtml = group.bigHtml
      || (lead ? `${tinted(lead.level, h.esc(num(lead.value)))}<small>${h.esc(unitText(lead.unit))}${h.esc(group.bigSuffix || '')}</small>` : '—');
    const from = win(lead).from; const to = win(lead).to;
    let html = `<div class="d4-g" data-d4-group="${h.esc(group.id)}"><div class="d4-gh" ${attr(h, lead.path, lead.status, goAttr(lead))} title="Open ${h.esc(lead.status)}">`
      + `<div><div class="d4-gname">${h.shape(level, level)}<span>${h.esc(group.title)}</span></div><div class="d4-big">${bigHtml}</div></div>`
      + `<div>${spark(group.spark.map((item) => ({ ...item })), from, to, { h: 34, auto: group.sparkAuto })}</div></div>`
      + (group.sub ? `<div class="d4-gsub">${h.esc(group.sub)}</div>` : '')
      + (group.extra || '');
    const rows = group.rows || [];
    if (rows.length) {
      const shown = rows.slice(0, 12);
      html += `<div class="d4-rows">${shown.map((row) => {
        const w = win(row.item);
        return `<div class="d4-r" ${attr(h, row.item.path, row.item.status, goAttr(row.item))} title="${h.esc([row.item.status, row.title].filter(Boolean).join(' · '))}">`
          + `<span class="n">${h.shape(row.item.level, row.item.level)}<span>${h.esc(row.label)}</span></span>`
          + `<span class="v">${tinted(row.item.level, h.esc(row.value))}</span>`
          + `<span>${spark(row.spark || [row.item], w.from, w.to, { h: 16, area: false, auto: row.auto })}</span></div>`;
      }).join('')}</div>`;
      if (rows.length > shown.length) html += `<div class="d4-more">${rows.length - shown.length} more on its page</div>`;
    }
    return `${html}</div>`;
  }

  // ------------------------------------------------------------------------- status table ----
  function statusTable(detail, hist, ctx) {
    const h = ctx.h;
    const statuses = new Map(((hist && hist.statuses) || []).map((s) => [s.name, s]));
    const checks = [...detail.checks].sort((a, b) => (RANK[b.effective_level] ?? -1) - (RANK[a.effective_level] ?? -1)
      || detail.checks.indexOf(a) - detail.checks.indexOf(b));
    const rows = checks.map((check) => {
      const level = check.effective_level;
      const hs = statuses.get(check.name);
      const causes = (check.caused_by || []).map((path) => {
        const node = ctx.byPath.get(path);
        return `<a class="link" data-go="${h.esc(path)}">${h.esc(node ? node.name : path)}</a>`;
      }).join(', ');
      return `<tr ${attr(h, detail.node.path, check.name)}${PROBLEM.has(level) ? ' data-diagnostic' : ''} title="${h.esc(check.name)}">`
        + `<td class="c-lv">${PROBLEM.has(level) ? `${h.shape(level, level)}<span${lvClass(level)}>${h.esc(level)}</span>` : 'ok'}</td>`
        + `<td class="c-name"><span${lvClass(level)}>${h.esc(shortName(check.name, ctx))}</span></td>`
        + `<td class="c-msg">${h.esc(check.message || '—')}${check.code ? ` <span class="mono">${h.esc(check.code)}</span>` : ''}${causes ? `<span class="cause">caused by ${causes}</span>` : ''}</td>`
        + `<td class="c-strip">${hs && hist ? strip(hs.levels, hist.from, hist.to) : ''}</td>`
        + `<td class="c-age">${h.esc(h.ago(check.age_s))}</td></tr>`;
    }).join('');
    return `<table class="d4-st">${rows}</table>`;
  }

  // What rolls up to the entity's level: its own statuses (the worst of them is its self-report), plus the hint of a
  // worse row below (CAN 15 reads OK while its driver part is in ERROR).
  function rollLine(detail, ctx) {
    const h = ctx.h;
    const node = ctx.node;
    const own = node.own && node.own.level;
    const setters = detail.checks.filter((check) => PROBLEM.has(check.effective_level) && check.effective_level === worst(detail.checks.map((c) => c.effective_level)));
    let html = own === null || own === undefined
      ? `<b>${h.esc(node.name)}</b> has no statuses of its own: a grouping row`
      : setters.length
        ? `<b>${h.esc(node.name)}</b> reads ${tinted(own, h.esc(own))} from ${setters.map((c) => `<span${lvClass(c.effective_level)} data-diagnostic>${h.esc(shortName(c.name, ctx))}</span>`).join(', ')}`
        : `<b>${h.esc(node.name)}</b> reads ok: all ${detail.checks.length} of its statuses are ok`;
    if (node.hint) {
      html += ` · below, <a class="link" data-go="${h.esc(node.hint.origin)}">${h.esc(node.rollup.origin_name || node.hint.origin)}</a> is ${tinted(node.hint.level, h.esc(node.hint.level))}`;
    }
    return `<div class="d4-roll">${html}</div>`;
  }

  function healthBlock(bar, ctx) {
    const h = ctx.h;
    if (!bar || !bar.rows || !bar.rows.length) return '';
    const empty = bar.rows.filter((row) => row.depth > 0 && row.bar.every((level) => !level));
    const rows = bar.rows.filter((row) => !empty.includes(row)).map((row) => {
      const name = row.depth === 0 ? h.esc(row.name) : `<a data-go="${h.esc(row.path)}">${h.esc(row.name)}</a>`;
      const basis = row.basis === 'none' ? ' · nothing in the history' : row.basis === 'below' ? ' · everything below it' : ` · ${row.statuses} status${row.statuses === 1 ? '' : 'es'}`;
      return `<div class="d4-hrow"><span class="n depth${row.depth}" title="${h.esc(row.name + basis)}">${name}</span>${healthBar(row.bar)}</div>`;
    }).join('');
    const quiet = empty.length ? `<div class="d4-hnone">no history in the window: ${empty.map((row) => `<a data-go="${h.esc(row.path)}">${h.esc(row.name)}</a>`).join(', ')}</div>` : '';
    return `<div class="d4-health">${rows}<div class="d4-hrow"><span></span>${axis(bar.window_s)}</div>${quiet}</div>`;
  }

  // checks with no measurements: their values, so a quiet entity still reads as its table plus its values
  function valuesBlock(checks, ctx) {
    const h = ctx.h;
    return checks.filter((check) => (check.values || []).some((v) => !HIDDEN_VALUES.has(v.key))).map((check) => `<h4 ${attr(h, ctx.node.path, check.name)}>${h.esc(shortName(check.name, ctx))}</h4>`
      + h.kv(check.values.filter((v) => !HIDDEN_VALUES.has(v.key)).map((v) => [v.key, h.esc(v.value)]))).join('');
  }
  const HIDDEN_VALUES = new Set(['what_to_check', 'kernel_evidence']);

  // ------------------------------------------------------------------- the entity's page ----
  function entityPage(detail, ctx) {
    const p = ctx.parts;
    const s = ctx.section;
    const h = ctx.h;
    const node = ctx.node;
    const histEntry = want('hist', node.path);
    const hist = histEntry.data && histEntry.data.path === node.path ? histEntry.data : null;
    want('bar', node.path);
    const bar = got('bar', node.path);
    // parts (part_of children) carry this entity's measurements too: a moteus board's voltage lives on its driver
    const partPaths = (node.children || []).map((child) => (typeof child === 'string' ? ctx.byPath.get(child) : child))
      .filter((child) => child && child.edge === 'part_of').map((child) => child.path).slice(0, MAX_PARTS);
    const partItems = partPaths.flatMap((path) => {
      want('node', path); want('hist', path);
      return itemsOf(path, got('node', path), got('hist', path));
    });
    const own = itemsOf(node.path, detail, hist);
    const windows = new Map([[node.path, hist]].concat(partPaths.map((path) => [path, got('hist', path)])));
    const win = (item) => { const hh = windows.get(item.path); return hh ? { from: hh.from, to: hh.to } : { from: 0, to: 0 }; };
    const groups = (ctx.isComputer ? computerGroups(own, ctx) : genericGroups(own, ctx))
      .concat(genericGroups(partItems, ctx, (item) => (ctx.byPath.get(item.path) || {}).name || item.path));
    const measured = new Set(own.map((item) => item.status));
    const plain = detail.checks.filter((check) => !measured.has(check.name));
    const winText = hist ? `last ${spanText(hist.window_s)}` : (histEntry.error ? `history: ${h.esc(histEntry.error)}` : 'history loading…');

    const statusBody = detail.checks.length
      ? `${rollLine(detail, ctx)}${statusTable(detail, hist, ctx)}${healthBlock(bar, ctx)}`
      : `${rollLine(detail, ctx)}<div class="d4-quiet">No statuses reported for this entity.</div>${healthBlock(bar, ctx)}`;
    const resTitle = ctx.isComputer ? 'Resources' : 'Measurements';
    const resBody = groups.map((group) => groupHtml(group, win, ctx)).join('');
    // the graceful fallback: few measurement groups, so the values of the statuses without measurements show here
    const valuesBody = groups.length < 3 ? valuesBlock(plain, ctx) : '';
    return `${p.head()}${p.banners()}<div class="d-body d4-root">`
      + s('d4-status', `Diagnostics · ${detail.checks.length}`, statusBody, { right: `${winText} · click a row for its page` })
      + s('why', 'What to check', p.whatToCheck())
      + s('d4-res', `${resTitle} · ${groups.length}`, resBody, { right: winText })
      + s('d4-values', 'Values', valuesBody ? `<div class="d4-vals">${valuesBody}</div>` : '')
      + s('faults', 'Faults', p.faults())
      + (bar ? '' : s('parts', `Parts · ${ctx.counts.parts}`, p.parts()))
      + s('connection', `Connection (folded) · ${ctx.counts.connection}`, p.connection())
      + s('links', 'Link health', p.links(), { right: 'the computer diagnoses its own wires' })
      + s('identity', 'Identity', p.identity())
      + s('events', 'Events · this row and below', p.events())
      + s('setup', 'Expected setup', p.setup(), { right: 'the rig file, never the device' })
      + '</div>';
  }

  // ---------------------------------------------------------------- one diagnostic's page ----
  function chart(item, from, to, ctx, { h = 84 } = {}) {
    const hh = ctx.h;
    const series = [{ ...item }];
    const [lo, hi] = range(series, {});
    const pct = (v) => (1 - (v - lo) / (hi - lo)) * 100;
    const limits = limitsOf(item).filter(([, v]) => v >= lo && v <= hi)
      .map(([k, v]) => `<span class="ll" style="top:${pct(v).toFixed(1)}%">${hh.esc(k.replace('_', ' '))} ${hh.esc(fmt(v, item.unit))}</span>`).join('');
    const ys = `<span class="yl" style="top:0%">${hh.esc(num(hi))}</span><span class="yl" style="top:100%">${hh.esc(num(lo))}</span>`;
    return `<div class="d4-chart"><div class="d4-chart-h"><span>${hh.esc(item.key)}</span><span class="v">${tinted(item.level, hh.esc(fmt(item.value, item.unit)))}</span></div>`
      + `<div class="d4-plot">${ys}${limits}${spark(series, from, to, { h, area: true })}</div>${axis(to - from)}</div>`;
  }

  function diagnosticPage(detail, ctx, check) {
    const p = ctx.parts;
    const s = ctx.section;
    const h = ctx.h;
    const node = ctx.node;
    const histEntry = want('hist', node.path);
    const hist = histEntry.data && histEntry.data.path === node.path ? histEntry.data : null;
    const hs = hist ? (hist.statuses || []).find((x) => x.name === check.name) : null;
    const items = itemsOf(node.path, { checks: [check] }, hist);
    const level = check.effective_level;
    const back = `<button class="d4-back" data-d4-back="1">← ${h.esc((ctx.row && ctx.row.label) || node.name)} · all ${detail.checks.length} statuses</button>`;
    const title = `<div class="d4-dh"${PROBLEM.has(level) ? ' data-diagnostic' : ''}><div class="t">${h.shape(level, level)}<span>${h.esc(shortName(check.name, ctx))}</span>${h.pill(level)}</div>`
      + `<div class="m">${h.esc(check.message || '—')}${check.code ? `<span class="code">${h.esc(check.code)}</span>` : ''}</div></div>`;

    let trend = '';
    if (hist && hs) {
      trend += `<div class="d4-chart"><div class="d4-chart-h"><span>level</span><span>${h.esc(hs.expected_period_s ? `every ${hs.expected_period_s} s · stale after ${hs.stale_after_s} s` : 'event-driven')}</span></div>`
        + `<div class="d4-plot">${strip(hs.levels, hist.from, hist.to, 'd4-strip', 12)}</div>${axis(hist.window_s)}</div>`;
      const big = items.slice(0, 4);
      trend += big.map((item) => chart(item, hist.from, hist.to, ctx)).join('');
      const small = items.slice(4);
      if (small.length) {
        trend += `<div class="d4-rows">${small.map((item) => `<div class="d4-r" title="${h.esc(limitWords(item))}"><span class="n">${h.shape(item.level, item.level)}<span>${h.esc(item.key)}</span></span>`
          + `<span class="v">${tinted(item.level, h.esc(fmt(item.value, item.unit)))}</span><span>${spark([item], hist.from, hist.to, { h: 18, area: false })}</span></div>`).join('')}</div>`;
      }
    } else {
      trend = `<div class="d4-quiet">${histEntry.error ? `No history: ${h.esc(histEntry.error)}` : hist ? 'No history for this status in the window.' : 'history loading…'}</div>`;
    }

    // newest first; "no data" spans stay in the list as their own state, never as a level
    const spans = hs ? [...hs.levels].reverse() : [];
    const msgs = spans.length ? `<table class="d4-msgs">${spans.map((span) => `<tr${PROBLEM.has(span.level) ? ' data-diagnostic' : ''}>`
      + `<td class="tm">${clock(span.from)} – ${span.to >= hist.to - 0.5 ? 'now' : clock(span.to)}<br>${spanText(span.to - span.from)}</td>`
      + `<td class="lv">${span.level ? (PROBLEM.has(span.level) ? `${h.shape(span.level, span.level)}<span${lvClass(span.level)}>${h.esc(span.level)}</span>` : h.esc(span.level)) : ''}</td>`
      + `<td class="ms${span.level ? '' : ' none'}">${span.level ? h.esc(span.message || '—') : 'no data'}${span.code ? `<span class="code">${h.esc(span.code)}</span>` : ''}</td></tr>`).join('')}</table>` : '';

    const values = Object.fromEntries((check.values || []).map((v) => [v.key, v.value]));
    // the plugin numbers its own steps ("1. Motor supply on…"): its lines as written, never renumbered
    const why = values.what_to_check ? `<div class="d4-why">${String(values.what_to_check).split('\n').filter((line) => line.trim()).map((line) => `<div>${h.esc(line)}</div>`).join('')}</div>`
      + (values.kernel_evidence ? `<div class="d4-quiet mono">${h.esc(values.kernel_evidence)}</div>` : '') : '';
    // the measurements exactly as the host sent them (text, unit, limits): one step away from the rounded rows
    const mtable = (check.measurements || []).length ? `<table class="d4-mt"><tr><th>measurement</th><th>value</th><th>limits</th></tr>${check.measurements.map((m) => `<tr${PROBLEM.has(m.level) ? ' data-diagnostic' : ''}><td>${h.esc(m.key)}</td>`
      + `<td>${h.shape(m.level, m.level)}<span${lvClass(m.level)}>${h.esc(m.text)}</span></td><td>${h.esc(limitWords(m) || '—')}</td></tr>`).join('')}</table>` : '';
    const vals = h.kv((check.values || []).filter((v) => !HIDDEN_VALUES.has(v.key)).map((v) => [v.key, h.esc(v.value)]));
    const causes = (check.caused_by || []).map((path) => `<a class="link" data-go="${h.esc(path)}">${h.esc((ctx.byPath.get(path) || {}).name || path)}</a>`).join(', ');
    const meta = h.kv([
      ['name', h.esc(check.name)],
      ['level', `${h.esc(level)}${check.level !== level ? ` <span class="muted">(reported ${h.esc(check.level)})</span>` : ''}`],
      ['code', check.code ? `<span class="mono">${h.esc(check.code)}</span>` : '<span class="muted">none</span>'],
      ['source', h.esc(check.source || '—')],
      ['expected period', check.expected_period_s ? `every ${h.esc(check.expected_period_s)} s · stale after ${h.esc(check.stale_after_s)} s` : 'event-driven (never stale)'],
      ['last received', `${h.esc(check.received_iso || '—')} · ${h.esc(h.ago(check.age_s))} ago`],
      check.hardware_id ? ['hardware id', `<span class="mono">${h.esc(check.hardware_id)}</span>`] : null,
      check.uptime_s !== null && check.uptime_s !== undefined ? ['uptime', h.esc(spanText(check.uptime_s))] : null,
      causes ? ['caused by', causes] : null,
      ['entity', `<a class="link" data-go="${h.esc(node.path)}">${h.esc(node.name)}</a> <span class="mono muted">${h.esc(node.path)}</span>`],
    ]);
    return `${p.head()}<div class="d-body d4-root">${back}${title}`
      + s('d4-trend', 'Trend', trend, { right: hist ? `last ${spanText(hist.window_s)} · limits as dashed lines` : '' })
      + s('d4-why', 'What to check', why)
      + s('d4-msgs', `Message history · ${spans.filter((span) => span.level).length}`, msgs, { right: 'newest first · each span held until it went stale' })
      + s('d4-meas', 'Values', mtable + vals)
      + s('d4-meta', 'Metadata', meta)
      + '</div>';
  }

  // ------------------------------------------------------------------------------ layout ----
  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts[ID] = {
    name: 'Resource monitor',
    description: 'The ROS2 System Webview / nvtop stacked top to bottom: the diagnostics status table first, then one row per measurement group with its big value, a sparkline over the history window and its limits; a status or a measurement opens its own page (trend, message history). The Computer\'s default.',
    order: 4,
    render(container, detail, ctx) {
      injectStyle();
      wire(container);
      last = { container, detail, ctx };
      const p = ctx.parts;
      const s = ctx.section;
      if (ctx.group) {
        ctx.draw(`${p.head()}<div class="d-body d4-root">${s('members', `Members · ${ctx.counts.members}`, p.members())}`
          + `${s('links', 'Link health', p.links(), { right: 'the computer diagnoses its own wires' })}</div>`);
        return;
      }
      if (!detail) {
        ctx.draw(`${p.head()}${p.banners()}<div class="d-body d4-root"><div class="muted">loading…</div></div>`);
        return;
      }
      // the page's drill: a diagnostic of this entity (a part's opens as that part's own page: openDiagnostic select)
      const open = ctx.diagnostic && ctx.diagnostic.path === detail.node.path ? ctx.diagnostic : null;
      const check = open && detail.checks.find((c) => c.name === open.name);
      ctx.draw(check ? diagnosticPage(detail, ctx, check) : entityPage(detail, ctx));
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
