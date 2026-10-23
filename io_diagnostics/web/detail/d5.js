// Detail layout d5 · Drill (round 3 proposal; contract: docs/round2-seams.md §6).
// Zach's "computer -> entity -> diagnostic" (Daily Note 2026-10-23) as push navigation INSIDE the detail pane, after
// macOS Finder's column view and iOS Settings: level 1 is the entity (its level, its status table, its children as
// rows); a status row pushes that diagnostic's own page (message, health bar and message history over the app-wide
// window, every measurement's trend, values, metadata, what_to_check); a measurement pushes its trend; a child row
// pushes that entity. A breadcrumb at the top (Computer › fdcanusb · 0607623A › moteus-c1 · CAN 21 › firmware) and
// Back (the ‹ button, Esc, Backspace) walk it back up. The utilities are the page's, in the entity header's
// Utilities ▾ (round 3.5); the expected setup sits behind one quiet row.
// WHY entity levels are real selections (data-go) and the levels below an entity are the page's drill (round 3.5:
// ctx.diagnostic {name, path, measurement} for a diagnostic and its measurement, ctx.page for About / Events / Link
// health / Expected setup): every level is a browser-history entry, so the mouse's back / forward buttons walk the same
// trail as the breadcrumb, and the tree keeps showing where you are. The history comes from the page's shared cache.
'use strict';
(function (root) {
  const doc = root.document;
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const BINS = 60;
  const MINI_BINS = 30;
  const API = '/api/v1';

  // ------------------------------------------------------------------------------ style ----
  const CSS = `
/* WHY a mix for OK: quiet by default (Zach); --io-border is pure black / white in Bare Metal, and a bar of it shouted */
.d5-root { min-height: 100%; --d5-ok: color-mix(in srgb, var(--io-text) 22%, var(--io-surface)); }
.d5-crumbs { position: sticky; top: 0; z-index: 4; display: flex; align-items: center; gap: 2px; padding: 7px 18px 7px 10px; background: var(--io-surface); border-bottom: 1px solid var(--io-border-soft); font-size: 12px; white-space: nowrap; min-width: 0; }
.d5-back { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 22px; border: 0; border-radius: var(--io-radius-sm); background: transparent; color: var(--io-muted); cursor: pointer; padding: 0; }
.d5-back:hover { background: var(--io-hover); color: var(--io-text); }
.d5-back .ic { width: 14px; height: 14px; transform: rotate(180deg); }
.d5-back[disabled] { visibility: hidden; }
.d5-crumb { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; border: 0; background: transparent; padding: 2px 4px; border-radius: var(--io-radius-sm); color: var(--io-muted); font: inherit; cursor: pointer; }
.d5-crumb:hover { color: var(--io-text); background: var(--io-hover); }
.d5-crumb.cur { flex: 0 0 auto; max-width: 60%; color: var(--io-text-strong); font-weight: 600; cursor: default; background: transparent; }
.d5-sep { flex: none; color: var(--io-faint); padding: 0 1px; }
.d5-kicker { color: var(--io-muted); font-size: 12px; margin-bottom: 2px; }
.d5-roll { margin-top: 6px; color: var(--io-muted); font-size: 12px; display: flex; flex-direction: column; gap: 2px; }
.d5-roll .shape { width: 12px; height: 12px; vertical-align: -2px; }
.d5-health { margin-top: 10px; display: grid; grid-template-columns: minmax(60px, max-content) 1fr; gap: 3px 10px; align-items: center; font-size: 11.5px; }
.d5-health .hl { color: var(--io-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 160px; }
.d5-axis { grid-column: 2; display: flex; justify-content: space-between; color: var(--io-faint); font-size: 10.5px; font-variant-numeric: tabular-nums; }
.d5-bar { display: flex; height: 8px; gap: 1px; min-width: 0; }
.d5-bar.mini { height: 5px; width: 72px; flex: none; }
.d5-bar > i { flex: 1; min-width: 0; border-radius: 1px; background: transparent; box-shadow: inset 0 -1px var(--io-border-soft); }
.d5-bar > i.ok { background: var(--d5-ok); box-shadow: none; }
.d5-bar > i.warn { background: var(--io-warn); box-shadow: none; }
.d5-bar > i.error { background: var(--io-error); box-shadow: none; }
.d5-bar > i.stale { background: var(--io-stale); box-shadow: none; }
.d5-bar > i.unknown { background: var(--io-unknown); box-shadow: none; }
.d5-span { position: relative; height: 8px; min-width: 0; box-shadow: inset 0 -1px var(--io-border-soft); }
.d5-span.mini { height: 5px; width: 72px; flex: none; }
.d5-span > i { position: absolute; top: 0; bottom: 0; border-radius: 1px; }
.d5-span > i.ok { background: var(--d5-ok); }
.d5-span > i.warn { background: var(--io-warn); }
.d5-span > i.error { background: var(--io-error); }
.d5-span > i.stale { background: var(--io-stale); }
.d5-span > i.unknown { background: var(--io-unknown); }
.d5-list { display: flex; flex-direction: column; margin: 0 -8px; }
.d5-row { display: flex; align-items: center; gap: 8px; width: 100%; min-height: var(--io-row-h); padding: 4px 8px; border: 0; border-radius: var(--io-radius-sm); background: transparent; color: var(--io-text); font: inherit; text-align: left; cursor: pointer; }
.d5-row + .d5-row { box-shadow: 0 -1px var(--io-border-soft); }
.d5-row:hover { background: var(--io-hover); box-shadow: none; }
.d5-row:hover + .d5-row { box-shadow: none; }
.d5-row.static { cursor: default; }
.d5-row.static:hover { background: transparent; }
.d5-row > .ic { color: var(--io-icon); flex: none; }
.d5-row .st { width: 16px; flex: none; display: inline-flex; justify-content: center; }
.d5-row .st .shape { width: 13px; height: 13px; }
.d5-tx { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.d5-nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-text); }
.d5-pre { color: var(--io-faint); }
.d5-ms { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-muted); font-size: 12px; }
.d5-row.bad .d5-nm { color: var(--io-text-strong); font-weight: 600; }
.d5-row.bad .d5-ms { color: var(--io-text); }
.d5-row.knock .d5-nm { font-weight: 400; color: var(--io-text); }
.d5-side { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 3px; width: 84px; }
.d5-val { color: var(--io-muted); font-size: 11.5px; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 84px; }
.d5-go { flex: none; width: 12px; height: 12px; color: var(--io-faint); }
.d5-row.static .d5-go { visibility: hidden; }
.d5-spark { display: block; width: 100%; height: 16px; overflow: visible; }
.d5-spark path { fill: none; stroke: var(--io-muted); stroke-width: 1.25; stroke-linejoin: round; stroke-linecap: round; }
.d5-spark path.dot { stroke-width: 3.5; }
.d5-spark path.lim { stroke: var(--io-faint); stroke-dasharray: 3 3; stroke-width: 1; }
.d5-row:hover .d5-spark path:not(.lim) { stroke: var(--io-text); }
.d5-side .d5-spark { width: 72px; }
.d5-m { display: grid; grid-template-columns: minmax(0, 38%) 1fr 92px 12px; align-items: center; gap: 10px; }
.d5-m .d5-nm { font-size: 12.5px; }
.d5-m .d5-val { max-width: none; text-align: right; color: var(--io-text); }
.d5-nodata { color: var(--io-faint); font-size: 11px; }
.d5-quiet { margin-top: 6px; }
.d5-quiet .d5-row { color: var(--io-muted); }
.d5-quiet .d5-nm { color: var(--io-muted); }
.d5-count { color: var(--io-faint); font-size: 11.5px; flex: none; }
.d5-msg { font-size: 13.5px; margin-top: 4px; overflow-wrap: anywhere; }
.d5-code { font-family: var(--io-font-mono); color: var(--io-muted); font-size: 11.5px; }
.d5-hist { display: flex; flex-direction: column; font-size: 12px; }
.d5-h { display: grid; grid-template-columns: 136px 16px 1fr; gap: 8px; padding: 4px 0; border-top: 1px solid var(--io-border-soft); align-items: baseline; }
.d5-h:first-child { border-top: 0; }
.d5-h .t { color: var(--io-faint); font-variant-numeric: tabular-nums; font-family: var(--io-font-mono); font-size: 11px; }
.d5-h .shape { width: 12px; height: 12px; vertical-align: -2px; }
.d5-h .m { overflow-wrap: anywhere; }
.d5-h.none .m { color: var(--io-faint); }
.d5-big { position: relative; padding: 4px 0 0 0; }
.d5-big .d5-spark { height: 150px; }
.d5-yl { display: flex; justify-content: space-between; color: var(--io-faint); font-size: 10.5px; font-variant-numeric: tabular-nums; margin-top: 2px; }
.d5-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 4px 12px; margin-top: 10px; font-size: 12px; }
.d5-stats .k { color: var(--io-muted); font-size: 11px; }
.d5-stats .v { font-variant-numeric: tabular-nums; color: var(--io-text-strong); }
.d5-empty { color: var(--io-muted); font-size: 12.5px; padding: 6px 0; }
`;
  function injectStyle() {
    if (!doc || doc.head.querySelector('style[data-layout="d5"]')) return;
    const style = doc.createElement('style');
    style.dataset.layout = 'd5';
    style.textContent = CSS;
    doc.head.appendChild(style);
  }

  // -------------------------------------------------------------------- fetch cache ----
  // A folded part's /api/v1/node is read here: one cache entry per URL, refreshed at most every `everyMs`; a finished
  // fetch redraws the pane. Entries nobody asked for in a minute are dropped. The history and the health bars are the
  // page's shared cache (ctx.history, ctx.bar); these helpers hand back their data.
  const cache = new Map();
  function want(url, everyMs) {
    const now = Date.now();
    let entry = cache.get(url);
    if (!entry) { entry = { data: null, at: 0, loading: false, used: now, failed: false }; cache.set(url, entry); }
    entry.used = now;
    if (!entry.loading && now - entry.at > everyMs) {
      entry.loading = true;
      fetch(url).then((response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
        .then((data) => { entry.data = data; entry.failed = false; })
        .catch(() => { entry.failed = true; })
        .finally(() => { entry.loading = false; entry.at = Date.now(); redraw(); });
    }
    return entry.data;
  }
  function sweep() {
    const now = Date.now();
    for (const [url, entry] of cache) if (now - entry.used > 60000) cache.delete(url);
  }
  const q = encodeURIComponent;
  const nodeOf = (path) => want(`${API}/node?path=${q(path)}`, 2500);
  const historyOf = (path) => last.ctx.history(path, { everyMs: 5000 }).data;
  const statusHistory = (path, name) => last.ctx.history(path, { status: name, everyMs: 2000 }).data;
  const barOf = (path, bins = BINS) => last.ctx.bar(path, bins, { everyMs: 3000 }).data;

  // ------------------------------------------------------------------ navigation state ----
  // trail: the entity paths of the breadcrumb (the last one is the selection); pending: the child a row click is
  // selecting, so the trail grows by exactly that row even where it is not a canonical descendant. The levels below the
  // entity are the page's drill: N.sub is derived from ctx on every render ({kind: diag | metric | setup | about |
  // events | links, path, name, key, label}).
  const N = { trail: [], sub: [], owner: null, pending: null, pendingAt: 0 };
  let last = null; // {container, ctx}: what the next redraw() draws from
  const PAGES = { setup: 'Expected setup', about: 'About', events: 'Events', links: 'Link health' };

  const active = () => Boolean(last && last.container.isConnected && last.container.querySelector('.d5-root')
    && (!doc.getElementById('detail-layout') || doc.getElementById('detail-layout').value === 'd5'));
  function redraw() {
    if (!active()) return;
    last.ctx.redraw();
  }
  // the drill levels below the entity, from the page's drill state
  function subOf(ctx) {
    if (ctx.group || !ctx.node) return [];
    if (ctx.page && PAGES[ctx.page]) return [{ kind: ctx.page, label: PAGES[ctx.page] }];
    const open = ctx.diagnostic;
    if (!open) return [];
    const diag = { kind: 'diag', path: open.path, name: open.name, label: shortName(open.name, ownerName(ctx, open.path)), title: open.name };
    return open.measurement ? [diag, { kind: 'metric', path: open.path, name: open.name, key: open.measurement, label: open.measurement, title: open.measurement }] : [diag];
  }

  // The layers p1 hides (a USB bus, a port, a hub, an interface, a plugin's CAN bus): never a breadcrumb of their own.
  const INTERFACE_ICONS = new Set(['tty', 'video', 'net']);
  const isLayer = (node) => ['host', 'bus', 'port', 'group'].includes(node.kind) || (node.kind === 'device' && (node.icon === 'hub' || INTERFACE_ICONS.has(node.icon)));
  function chainOf(path, ctx) {
    const host = ctx.state && ctx.state.tree ? ctx.state.tree.path : null;
    const segments = String(path).split('/');
    const out = host && host !== path ? [host] : [];
    for (let at = 1; at < segments.length; at += 1) {
      const prefix = segments.slice(0, at).join('/');
      const node = ctx.byPath.get(prefix);
      if (node && prefix !== host && !isLayer(node) && node.edge !== 'part_of') out.push(prefix);
    }
    out.push(path);
    return out;
  }
  function syncTrail(current, ctx) {
    if (N.pending && Date.now() - N.pendingAt > 4000) N.pending = null;
    const at = N.trail.indexOf(current);
    if (at >= 0) N.trail = N.trail.slice(0, at + 1);
    else if (N.pending === current && N.trail.length) N.trail = [...N.trail, current];
    else N.trail = current.startsWith('group:') ? [current] : chainOf(current, ctx);
    if (N.pending === current) N.pending = null;
    N.owner = current;
  }
  // one level down, through the page's drill (one browser-history entry)
  function push(item) {
    const ctx = last.ctx;
    if (item.kind === 'diag') ctx.openDiagnostic(item.name, { path: item.path });
    else if (item.kind === 'metric') ctx.openDiagnostic(item.name, { path: item.path, measurement: item.key });
    else if (PAGES[item.kind]) ctx.openPage(item.kind);
  }

  // ----------------------------------------------------------------------- formatting ----
  const esc = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const worst = (levels) => levels.filter(Boolean).reduce((a, b) => (a === null || RANK[b] > RANK[a] ? b : a), null);
  const pad = (n) => String(n).padStart(2, '0');
  const clock = (epoch) => { const date = new Date(epoch * 1000); return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`; };
  function span(seconds) {
    if (seconds < 90) return `${Math.round(seconds)} s`;
    if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
    if (seconds < 172800) return `${(seconds / 3600).toFixed(seconds < 36000 ? 1 : 0)} h`;
    return `${Math.round(seconds / 86400)} d`;
  }
  function num(value) {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    if (Number.isInteger(n)) return String(n);
    const abs = Math.abs(n);
    return abs >= 100 ? n.toFixed(0) : abs >= 10 ? n.toFixed(1) : String(Number(n.toPrecision(3)));
  }
  const withUnit = (value, unit) => `${num(value)}${unit && unit !== '1' ? ` ${unit}` : ''}`;
  // A check's name with its entity's own prefix ("bam-GPU: ") drawn quiet: every character shown, the new part legible.
  function nameHtml(name, owner) {
    const prefix = owner ? `${owner}: ` : '';
    if (prefix && String(name).startsWith(prefix) && String(name).length > prefix.length) return `<span class="d5-pre">${esc(prefix)}</span>${esc(String(name).slice(prefix.length))}`;
    return esc(name);
  }
  const shortName = (name, owner) => (owner && String(name).startsWith(`${owner}: `) ? String(name).slice(owner.length + 2) : String(name));
  const valuesOf = (check) => Object.fromEntries(((check && check.values) || []).map((value) => [value.key, value.value]));

  // -------------------------------------------------------------------------- drawing ----
  // A health bar from /api/v1/history/bar: one cell per bin, the worst level any status held there; null = no data
  // (an empty cell, never a level). WHY data-diagnostic: its colours ARE the diagnostic results (the warn-colours gate).
  function binBar(bar, mini = false) {
    if (!bar || !bar.length) return `<span class="d5-bar${mini ? ' mini' : ''}" data-diagnostic></span>`;
    return `<span class="d5-bar${mini ? ' mini' : ''}" data-diagnostic>${bar.map((level) => `<i class="${level || 'none'}"></i>`).join('')}</span>`;
  }
  // One status's own timeline from /api/v1/history levels: each span exactly where the host put it (held, stale and
  // no-data spans included), so out-of-sync statuses never gain a step between their arrivals.
  function spanBar(levels, from, to, mini = false) {
    const width = Math.max(to - from, 1e-6);
    const cells = (levels || []).filter((item) => item.level).map((item) => {
      const left = Math.max(0, (item.from - from) / width) * 100;
      const right = Math.min(1, (item.to - from) / width) * 100;
      return `<i class="${esc(item.level)}" style="left:${left.toFixed(2)}%;width:${Math.max(right - left, 0.4).toFixed(2)}%" title="${esc(`${item.level} · ${clock(item.from)}–${clock(item.to)}${item.message ? ` · ${item.message}` : ''}`)}"></i>`;
    }).join('');
    return `<span class="d5-span${mini ? ' mini' : ''}" data-diagnostic>${cells}</span>`;
  }
  const numericPoints = (series) => ((series && series.points) || []).map(([t, v]) => [t, typeof v === 'boolean' ? (v ? 1 : 0) : (v === null || v === undefined ? null : Number(v))])
    .map(([t, v]) => [t, Number.isFinite(v) ? v : null]);
  // A measurement's trend over the window: a line through its received points, broken where a value was unreadable,
  // ending at the last arrival (never drawn on to "now"). Limits are dashed and quiet: a limit is not a result.
  function sparkline(series, from, to, opts = {}) {
    const points = numericPoints(series);
    const values = points.map(([, v]) => v).filter((v) => v !== null);
    if (!values.length) return '<span class="d5-nodata">no data in the window</span>';
    const limits = opts.limits ? ['warn_low', 'warn_high', 'error_low', 'error_high'].map((key) => series[key]).filter((v) => typeof v === 'number') : [];
    let lo = Math.min(...values, ...limits);
    let hi = Math.max(...values, ...limits);
    // a percentage reads on its whole scale (the ROS 2 System Webview's CPU per core): 30 % never looks like a peak
    if (series.unit === '%' && lo >= 0 && hi <= 100) { lo = 0; hi = 100; }
    if (hi - lo < 1e-9) { const pad0 = Math.abs(hi) * 0.05 || 1; lo -= pad0; hi += pad0; }
    const W = 1000;
    const H = 100;
    const x = (t) => (Math.min(Math.max((t - from) / Math.max(to - from, 1e-6), 0), 1) * W).toFixed(1);
    const y = (v) => (H - ((v - lo) / (hi - lo)) * H).toFixed(1);
    let d = '';
    let pen = false;
    for (const [t, v] of points) {
      if (v === null) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${x(t)} ${y(v)}`;
      pen = true;
    }
    // a lone point (one arrival, or one between gaps) still shows as a dot
    const lone = points.filter(([, v], at) => v !== null && (at === 0 || points[at - 1][1] === null) && (at === points.length - 1 || points[at + 1][1] === null));
    const dots = lone.length ? `<path class="dot" d="${lone.map(([t, v]) => `M${x(t)} ${y(v)}h0.01`).join('')}" vector-effect="non-scaling-stroke"/>` : '';
    const lim = limits.filter((v) => v >= lo && v <= hi).map((v) => `<path class="lim" d="M0 ${y(v)}H${W}" vector-effect="non-scaling-stroke"/>`).join('');
    return `<svg class="d5-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${lim}<path d="${d}" vector-effect="non-scaling-stroke"/>${dots}</svg>`
      + (opts.axis ? `<div class="d5-yl"><span>${esc(withUnit(lo, series.unit))} … ${esc(withUnit(hi, series.unit))}</span></div>` : '');
  }
  const lastValue = (series) => { const points = numericPoints(series).filter(([, v]) => v !== null); return points.length ? points[points.length - 1][1] : null; };
  const axis = (from, to) => `<div class="d5-axis"><span>${esc(span(to - from))} ago · ${esc(clock(from))}</span><span>now · ${esc(clock(to))}</span></div>`;
  const chev = '<svg class="ic d5-go"><use href="#i-chev"/></svg>';
  const shapeOf = (level) => (last ? last.ctx.h.shape(level, level) : '');

  function crumbsHtml(ctx) {
    const items = N.trail.map((path, at) => {
      const label = labelFor(path, ctx);
      const own = at === N.trail.length - 1;
      const current = own && !N.sub.length;
      // the selection's own crumb (below it a drill level is open) is the selection again: the page closes the drill
      // and keeps the node loaded
      const go = path.startsWith('group:') ? '' : ` data-go="${esc(path)}"`;
      return current ? `<span class="d5-crumb cur" title="${esc(titleFor(path, ctx))}">${esc(label)}</span>`
        : `<button class="d5-crumb"${go} data-d5-sub="0" data-d5-crumb="${at}" title="${esc(titleFor(path, ctx))}">${esc(label)}</button>`;
    });
    N.sub.forEach((item, at) => {
      const current = at === N.sub.length - 1;
      items.push(current ? `<span class="d5-crumb cur" title="${esc(item.title || item.label)}">${esc(item.label)}</span>`
        : `<button class="d5-crumb" data-d5-sub="${at + 1}" title="${esc(item.title || item.label)}">${esc(item.label)}</button>`);
    });
    const parent = N.trail.length > 1 ? N.trail[N.trail.length - 2] : null;
    const back = N.sub.length ? '<button class="d5-back" data-d5-back title="Back (Esc)"><svg class="ic"><use href="#i-chev"/></svg></button>'
      : parent && !parent.startsWith('group:') ? `<button class="d5-back" data-d5-back data-go="${esc(parent)}" title="Back to ${esc(labelFor(parent, ctx))} (Esc)"><svg class="ic"><use href="#i-chev"/></svg></button>`
        : '<button class="d5-back" disabled aria-hidden="true"><svg class="ic"><use href="#i-chev"/></svg></button>';
    return `<nav class="d5-crumbs" aria-label="Where you are">${back}${items.join('<span class="d5-sep">›</span>')}</nav>`;
  }
  function labelFor(path, ctx) {
    if (path.startsWith('group:')) return ctx.group ? ctx.group.label : path.slice(6);
    const node = ctx.byPath.get(path);
    if (!node) return path.split('/').pop();
    if (node.kind === 'host') return 'Computer';
    return root.IOView ? root.IOView.labelOf(node).label : node.name;
  }
  function titleFor(path, ctx) {
    const node = ctx.byPath.get(path);
    return node ? `${node.name} · ${node.path}` : path;
  }

  // ------------------------------------------------------------------- level 1: entity ----
  // The parts whose statuses roll into this row: the structure's folded parts (composition and folded transport
  // layers), else the API's part_of children of a selected part.
  function partsFor(ctx) {
    if (ctx.row) return ctx.row.parts || [];
    const node = ctx.node && ctx.byPath.get(ctx.node.path);
    return node && root.IOView ? root.IOView.partsOf(node) : [];
  }
  // The children as rows: the structure's child rows, else the API's via children of a selected part / layer.
  function childrenFor(ctx) {
    if (ctx.group) return ctx.group.children.map((child) => ({ entity: child.entity, label: child.label, icon: child.icon, message: child.message, mark: ctx.h.statusMark(child), problem: Boolean(child.status && (child.status.own || child.status.part)), knock: Boolean(child.knock_on_of) }));
    if (ctx.row) return ctx.row.children.map((child) => ({ entity: child.entity, label: child.label, icon: child.icon, message: (child.status && child.status.note) || child.message, mark: ctx.h.statusMark(child), problem: Boolean(child.status && (child.status.own || child.status.part)), knock: Boolean(child.knock_on_of) }));
    const node = ctx.node && ctx.byPath.get(ctx.node.path);
    const kids = node && root.IOView ? root.IOView.childrenOf(node, {}) : [];
    return kids.map((child) => ({ entity: child.path, label: root.IOView.labelOf(child).label, icon: child.icon, message: child.message, mark: `<span class="st">${ctx.h.shape(root.IOView.ownProblem(child), child.message)}</span>`, problem: Boolean(root.IOView.ownProblem(child)), knock: Boolean(child.knock_on_of) }));
  }
  const parentPath = (path) => String(path).slice(0, Math.max(String(path).lastIndexOf('/'), 0));
  // Health bars by entity path: the selection's own answer covers it and its direct API children (its folded parts);
  // boards lifted from a folded CAN bus come from their bus's answer.
  function barsFor(paths, base) {
    const out = new Map();
    const parents = [...new Set(paths.filter(Boolean).map(parentPath).filter((p) => p && p !== base))].slice(0, 4);
    for (const answer of [base ? barOf(base) : null, ...parents.map((p) => barOf(p))]) {
      for (const row of (answer && answer.rows) || []) if (!out.has(row.path)) out.set(row.path, { bar: row.bar, basis: row.basis, from: answer.from, to: answer.to });
    }
    return out;
  }

  function statusRows(ctx) {
    const node = ctx.node;
    const own = (ctx.detail ? ctx.detail.checks : []).map((check) => ({ check, path: node.path, owner: node.name }));
    const parts = partsFor(ctx).map((part) => {
      const answer = nodeOf(part.entity);
      return (answer ? answer.checks : []).map((check) => ({ check, path: part.entity, owner: part.name, part }));
    }).flat();
    const all = [...own, ...parts];
    // problems first (worst first); otherwise the order the API sent
    return all.map((item, at) => ({ ...item, at })).sort((a, b) => (RANK[b.check.effective_level] || 0) - (RANK[a.check.effective_level] || 0) || a.at - b.at);
  }

  function statusTable(ctx, rows) {
    if (!rows.length) return '';
    const histories = new Map();
    for (const path of new Set(rows.map((item) => item.path))) {
      const answer = historyOf(path);
      histories.set(path, answer ? { from: answer.from, to: answer.to, byName: new Map(answer.statuses.map((status) => [status.name, status])) } : null);
    }
    return `<div class="d5-list" role="list">${rows.map((item) => {
      const check = item.check;
      const level = check.effective_level;
      const bad = PROBLEM.has(level);
      const knock = bad && ctx.byPath.get(item.path) && ctx.byPath.get(item.path).knock_on_of;
      const history = histories.get(item.path);
      const status = history && history.byName.get(check.name);
      const headline = status && (status.measurements || []).find((series) => numericPoints(series).some(([, v]) => v !== null));
      const value = headline ? lastValue(headline) : null;
      const strip = status ? spanBar(status.levels, history.from, history.to, true) : '<span class="d5-span mini"></span>';
      const spark = headline ? sparkline(headline, history.from, history.to) : '';
      const measured = (check.measurements || []).find((m) => headline && m.key === headline.key);
      const valueText = measured ? measured.text : value !== null ? withUnit(value, headline.unit) : '';
      return `<button class="d5-row${bad ? ' bad' : ''}${knock ? ' knock' : ''}" role="listitem"${bad ? ' data-diagnostic' : ''} data-d5-push="diag" data-d5-path="${esc(item.path)}" data-d5-name="${esc(check.name)}" title="${esc(`${check.name}\n${check.message || ''}`)}">`
        + `<span class="st">${ctx.h.shape(level, check.message)}</span>`
        + `<span class="d5-tx"><span class="d5-nm">${nameHtml(check.name, item.owner)}</span><span class="d5-ms">${esc(check.message || '—')}${check.code ? ` <span class="d5-code">${esc(check.code)}</span>` : ''}</span></span>`
        + `<span class="d5-side">${spark ? `${spark}` : ''}${valueText ? `<span class="d5-val" title="${esc(headline.key)}">${esc(valueText)}</span>` : ''}${strip}</span>${chev}</button>`;
    }).join('')}</div>`;
  }

  function childrenTable(ctx, children, bars) {
    if (!children.length) return '';
    return `<div class="d5-list" role="list">${children.map((child) => {
      const drill = Boolean(child.entity);
      const bar = drill && bars.get(child.entity);
      const attrs = drill ? ` data-go="${esc(child.entity)}" data-d5-child="${esc(child.entity)}"` : ' disabled';
      return `<button class="d5-row${drill ? '' : ' static'}${child.problem && !child.knock ? ' bad' : ''}" role="listitem"${child.problem ? ' data-diagnostic' : ''}${attrs} title="${esc(child.label)}">`
        + `<svg class="ic"><use href="#i-${esc(child.icon || 'device')}"/></svg>`
        + `<span class="d5-tx"><span class="d5-nm">${esc(child.label)}</span><span class="d5-ms">${esc(child.message || '')}</span></span>`
        + `${child.mark}<span class="d5-side">${bar ? binBar(bar.bar.slice(-MINI_BINS), true) : ''}</span>${chev}</button>`;
    }).join('')}</div>`;
  }

  // The quiet rows at the end: everything that is not the entity's diagnostics, one push away (Zach: "I don't like
  // having the utilities here front and center"; the utilities themselves are the header's Utilities ▾).
  function quietRows(ctx) {
    const rows = [];
    const detail = ctx.detail;
    if (ctx.node) rows.push(['about', 'About', 'identity, connection, parts', '']);
    if (detail && (detail.events.length || detail.faults.length)) rows.push(['events', 'Events', 'this row and below', `${detail.events.length}${detail.faults.length ? ` · ${detail.faults.length} fault${detail.faults.length === 1 ? '' : 's'}` : ''}`]);
    if (ctx.isComputer && ctx.parts.links()) rows.push(['links', 'Link health', 'the computer diagnoses its own wires', '']);
    if (detail && ctx.parts.setup()) rows.push(['setup', 'Expected setup', 'the rig file, never the device', '']);
    if (!rows.length) return '';
    return `<div class="d5-list d5-quiet" role="list">${rows.map(([kind, label, note, count]) => `<button class="d5-row" role="listitem" data-d5-push="${kind}" data-d5-label="${esc(label)}">`
      + `<span class="d5-tx"><span class="d5-nm">${esc(label)}</span><span class="d5-ms">${esc(note)}</span></span>${count ? `<span class="d5-count">${esc(count)}</span>` : ''}${chev}</button>`).join('')}</div>`;
  }

  // What rolls up to the level in the head: the worst own / part status (push to it), the API's worst-below hint, the
  // knock-on cause and a composite's caused_by (each a link to that entity).
  function rollLine(ctx, rows) {
    const node = ctx.node;
    const bits = [];
    const top = rows.find((item) => PROBLEM.has(item.check.effective_level));
    if (top) {
      const where = top.path === node.path ? '' : ` on ${esc(top.part ? top.part.label : top.owner)}`;
      bits.push(`<span>level from ${ctx.h.shape(top.check.effective_level)} <a class="link" data-d5-push="diag" data-d5-path="${esc(top.path)}" data-d5-name="${esc(top.check.name)}">${esc(shortName(top.check.name, top.owner))}</a>${where}</span>`);
      const causes = (top.check.caused_by || []).filter((path) => ctx.byPath.has(path));
      if (causes.length) bits.push(`<span>caused by ${causes.map((path) => `<a class="link" data-go="${esc(path)}">${esc(labelFor(path, ctx))}</a>`).join(', ')}</span>`);
    } else if (rows.length) bits.push(`<span>${rows.length} status${rows.length === 1 ? '' : 'es'}, all OK</span>`);
    // the row's hint, not the node's: a hint a folded part explains is already the status row above (core.js nulls it)
    const hint = ctx.row ? ctx.row.status.hint : node.hint;
    if (hint) bits.push(`<span>below: ${ctx.h.shape(hint.level)} <a class="link" data-go="${esc(hint.origin)}">${esc(hint.origin_name || labelFor(hint.origin, ctx))}</a>${hint.message ? ` · ${esc(hint.message)}` : ''}</span>`);
    if (node.knock_on_of) bits.push(`<span>explained by <a class="link" data-go="${esc(node.knock_on_of)}">${esc(labelFor(node.knock_on_of, ctx))}</a> above: counted once, as that cause</span>`);
    // WHY here and not the page's banners: the banners stood between the head and the status table, and the table is
    // the first thing the entity shows (Zach, 2026-10-23); the pill already says STALE, this line says since when
    if (node.stale) bits.push(`<span>nothing received for ${ctx.h.ago(node.age_s)} (expected every ${esc(num(node.expected_period_s))} s); the last values stay, with their age</span>`);
    if (node.connected === false) bits.push('<span>not connected</span>');
    return bits.length ? `<div class="d5-roll">${bits.join('')}</div>` : '';
  }

  function healthBlock(ctx, bars) {
    const node = ctx.node;
    const own = bars.get(node.path);
    if (!own) return '';
    const lines = [[node.kind === 'host' ? 'Computer' : 'own', own]];
    for (const part of partsFor(ctx)) if (bars.has(part.entity)) lines.push([part.label, bars.get(part.entity)]);
    return `<div class="d5-health">${lines.map(([label, bar]) => `<span class="hl" title="${esc(bar.basis === 'below' ? `${label}: everything reported below` : label)}">${esc(label)}</span>${binBar(bar.bar)}`).join('')}${axis(own.from, own.to)}</div>`;
  }

  function entityPage(ctx) {
    const node = ctx.node;
    const detail = ctx.detail;
    const title = ctx.row ? ctx.row.label : (root.IOView ? root.IOView.labelOf(node).label : node.name);
    const rows = statusRows(ctx);
    const children = childrenFor(ctx);
    const bars = barsFor([node.path, ...children.map((child) => child.entity)], node.path);
    const part = ctx.row && ctx.row.status && ctx.row.status.part;
    const head = `<div class="d-head"><div class="d-title"><svg class="ic"><use href="#i-${esc(node.icon || 'device')}"/></svg><span>${esc(title)}</span>${ctx.h.pill(node.level)}</div>`
      + `<div class="d-msg">${esc(node.message || '—')}${part && part.text && part.text !== node.message ? `<span class="muted"> · ${esc(part.text)}</span>` : ''}</div>`
      + rollLine(ctx, rows) + healthBlock(ctx, bars) + '</div>';
    const s = ctx.section;
    const table = detail ? statusTable(ctx, rows) : '<div class="d5-empty">loading…</div>';
    return head + '<div class="d-body">'
      + s('d5-status', `Status · ${rows.length || (detail ? 0 : '…')}`, table || '<div class="d5-empty">No statuses of its own.</div>', { right: 'what rolls up to its level · click one for its page' })
      + s('d5-children', `Children · ${children.length}`, childrenTable(ctx, children, bars), { right: 'click to drill in' })
      + quietRows(ctx) + '</div>';
  }

  function groupPage(ctx) {
    const group = ctx.group;
    const children = childrenFor(ctx);
    const bars = barsFor(children.map((child) => child.entity), null);
    const head = `<div class="d-head"><div class="d-title"><svg class="ic"><use href="#i-${esc(group.icon || 'group')}"/></svg><span>${esc(group.label)}</span></div>`
      + `<div class="d-msg">${group.status.hint ? `${ctx.h.shape(group.status.hint.level)} ${esc(group.status.hint.level)} below · ${esc(group.status.hint.message)}` : esc(group.message || 'grouping row')}</div></div>`;
    return head + '<div class="d-body">'
      + ctx.section('d5-children', `Members · ${children.length}`, childrenTable(ctx, children, bars), { right: 'click to drill in' })
      + ctx.section('links', 'Link health', ctx.parts.links(), { right: 'the computer diagnoses its own wires' }) + '</div>';
  }

  // -------------------------------------------------------------- level 2: a diagnostic ----
  function checkFor(ctx, item) {
    if (ctx.node && item.path === ctx.node.path) return ctx.detail ? ctx.detail.checks.find((check) => check.name === item.name) || null : null;
    const answer = nodeOf(item.path);
    return answer ? answer.checks.find((check) => check.name === item.name) || null : null;
  }
  const ownerName = (ctx, path) => (ctx.byPath.get(path) || {}).name || '';

  function measurementRows(check, status, from, to, item, loaded) {
    const byKey = new Map(((status && status.measurements) || []).map((series) => [series.key, series]));
    const keys = [...new Set([...((check && check.measurements) || []).map((m) => m.key), ...byKey.keys()])];
    if (!keys.length) return '';
    return `<div class="d5-list" role="list">${keys.map((key) => {
      const series = byKey.get(key);
      const now = ((check && check.measurements) || []).find((m) => m.key === key);
      const bad = now && PROBLEM.has(now.level);
      const text = now ? now.text : series ? withUnit(lastValue(series), series.unit) : '—';
      return `<button class="d5-row d5-m${bad ? ' bad' : ''}" role="listitem"${bad ? ' data-diagnostic' : ''} data-d5-push="metric" data-d5-path="${esc(item.path)}" data-d5-name="${esc(item.name)}" data-d5-key="${esc(key)}" title="${esc(key)}">`
        + `<span class="d5-nm">${bad ? `${shapeOf(now.level)} ` : ''}${esc(key)}</span>`
        + `<span>${series ? sparkline(series, from, to) : `<span class="d5-nodata">${loaded ? 'no history in the window' : 'loading…'}</span>`}</span>`
        + `<span class="d5-val">${esc(text)}</span>${chev}</button>`;
    }).join('')}</div>`;
  }

  function messageHistory(status, from) {
    const levels = ((status && status.levels) || []).slice().reverse().slice(0, 60);
    if (!levels.length) return '';
    return `<div class="d5-hist">${levels.map((item) => {
      // a span the window cuts began before it: "≤ 18:18:32", never a start time it did not have
      const when = `${item.from <= from + 0.5 ? '≤ ' : ''}${clock(item.from)} · ${span(item.to - item.from)}${item.from <= from + 0.5 ? '+' : ''}`;
      if (!item.level) return `<div class="d5-h none"><span class="t">${esc(when)}</span><span></span><span class="m">no data</span></div>`;
      const bad = PROBLEM.has(item.level);
      return `<div class="d5-h"${bad ? ' data-diagnostic' : ''}><span class="t">${esc(when)}</span><span>${shapeOf(item.level)}</span>`
        + `<span class="m">${bad ? `<span class="lvword lv-${esc(item.level)}">${esc(item.level)}</span>` : '<span class="muted">ok · </span>'}${esc(item.message || '')}${item.code ? ` <span class="d5-code">${esc(item.code)}</span>` : ''}</span></div>`;
    }).join('')}</div>`;
  }

  function whatToCheck(check) {
    const values = valuesOf(check);
    if (!values.what_to_check) return '';
    const lines = String(values.what_to_check).split('\n').filter((line) => line.trim());
    return `<div class="why-lines">${lines.map((line) => `<div>${esc(line)}</div>`).join('')}</div>`
      + (values.kernel_evidence ? `<div class="why-ev">kernel evidence: ${esc(values.kernel_evidence)}</div>` : '');
  }

  function metadata(ctx, check, status) {
    const h = ctx.h;
    const period = check.expected_period_s ? `every ${num(check.expected_period_s)} s · stale after ${num(check.stale_after_s)} s` : 'event-driven: holds until its next report (never stale)';
    const causes = (check.caused_by || []).map((path) => (ctx.byPath.has(path) ? `<a class="link" data-go="${esc(path)}">${esc(labelFor(path, ctx))}</a>` : `<span class="mono">${esc(path)}</span>`));
    return h.kv([
      ['code', check.code ? `<span class="mono">${esc(check.code)}</span>` : '<span class="muted">none</span>'],
      ['source', esc(check.source || '—')],
      ['expected period', esc(period)],
      ['last received', `${esc((check.received_iso || '').replace('T', ' ').slice(0, 19) || '—')} <span class="muted">· ${h.ago(check.age_s)} ago</span>`],
      check.stamp && check.received && Math.abs(check.stamp - check.received) > 1 ? ['measured at', `${esc(clock(check.stamp))} <span class="muted">(device stamp)</span>`] : null,
      check.level !== check.effective_level ? ['reported', `${esc(check.level)} <span class="muted">· shown ${esc(check.effective_level)} (held / stale rule)</span>`] : null,
      causes.length ? ['caused by', causes.join(', ')] : null,
      check.mode ? ['mode', esc(check.mode)] : null,
      check.uptime_s !== null && check.uptime_s !== undefined ? ['uptime', esc(span(check.uptime_s))] : null,
      check.hardware_id ? ['hardware id', `<span class="mono">${esc(check.hardware_id)}</span>`] : null,
      status ? ['history', `${esc(String(status.levels.filter((l) => l.level).length))} span${status.levels.filter((l) => l.level).length === 1 ? '' : 's'} · ${esc(String((status.measurements || []).length))} measurement series`] : null,
    ]);
  }

  function diagPage(ctx, item) {
    const check = checkFor(ctx, item);
    const answer = statusHistory(item.path, item.name);
    const status = answer && answer.statuses[0];
    const owner = ownerName(ctx, item.path);
    const s = ctx.section;
    const level = check ? check.effective_level : null;
    const problem = PROBLEM.has(level);
    const onPart = ctx.node && item.path !== ctx.node.path;
    const head = `<div class="d-head"><div class="d5-kicker">${onPart ? `on <a class="link" data-go="${esc(item.path)}">${esc(labelFor(item.path, ctx))}</a> · ` : ''}diagnostic</div>`
      + `<div class="d-title"><span>${nameHtml(item.name, owner)}</span>${ctx.h.pill(level)}</div>`
      + `<div class="d5-msg">${check ? `${esc(check.message || '—')}${check.code ? ` <span class="d5-code">${esc(check.code)}</span>` : ''}` : '<span class="muted">not reported now: its source stopped computing it (the history below keeps what it said)</span>'}</div>`
      + (answer ? `<div class="d5-health"><span class="hl">health</span>${spanBar(status ? status.levels : [], answer.from, answer.to)}${axis(answer.from, answer.to)}</div>` : '')
      + '</div>';
    if (!check && !answer) return `${head}<div class="d-body"><div class="d5-empty">loading…</div></div>`;
    const why = check ? whatToCheck(check) : '';
    const trends = measurementRows(check, status, answer ? answer.from : 0, answer ? answer.to : 1, item, Boolean(answer));
    const hidden = new Set(['what_to_check', 'kernel_evidence']);
    const values = check ? ctx.h.kv(check.values.filter((value) => !hidden.has(value.key)).map((value) => [value.key, esc(value.value)])) : '';
    const sections = [
      s('why', 'What to check', why, { right: 'in this order' }),
      s('d5-trend', `Measurements · ${(trends.match(/data-d5-key=/g) || []).length}`, trends, { right: 'over the window · click one for its trend' }),
      s('d5-values', 'Values', values),
      s('d5-history', 'Message history', messageHistory(status, answer ? answer.from : 0), { right: 'newest first · each span as the host held it' }),
      s('d5-meta', 'Metadata', check ? metadata(ctx, check, status) : ''),
    ];
    // WHY what to check first only on a problem: on an OK status the trend is what you came for (the Computer)
    if (!problem) sections.push(sections.shift());
    return `${head}<div class="d-body">${sections.join('')}</div>`;
  }

  // --------------------------------------------------------- level 3: one measurement ----
  function metricPage(ctx, item) {
    const check = checkFor(ctx, item);
    const answer = statusHistory(item.path, item.name);
    const status = answer && answer.statuses[0];
    const series = status && status.measurements.find((m) => m.key === item.key);
    const now = check && check.measurements.find((m) => m.key === item.key);
    const head = `<div class="d-head"><div class="d5-kicker">${esc(shortName(item.name, ownerName(ctx, item.path)))} · measurement</div>`
      + `<div class="d-title"><span>${esc(item.key)}</span>${now ? ctx.h.pill(now.level) : ''}</div>`
      + `<div class="d5-msg">${esc(now ? now.text : series ? withUnit(lastValue(series), series.unit) : '—')}</div></div>`;
    if (!answer) return `${head}<div class="d-body"><div class="d5-empty">loading…</div></div>`;
    const values = series ? numericPoints(series).map(([, v]) => v).filter((v) => v !== null) : [];
    const stats = values.length ? `<div class="d5-stats">${[['latest', values[values.length - 1]], ['min', Math.min(...values)], ['mean', values.reduce((a, b) => a + b, 0) / values.length], ['max', Math.max(...values)]]
      .map(([k, v]) => `<div><div class="k">${k}</div><div class="v">${esc(withUnit(v, series.unit))}</div></div>`).join('')}</div>` : '';
    const limits = series ? ['warn_low', 'warn_high', 'error_low', 'error_high'].filter((key) => typeof series[key] === 'number').map((key) => [key.replace('_', ' '), esc(withUnit(series[key], series.unit))]) : [];
    const chart = series ? `<div class="d5-big">${sparkline(series, answer.from, answer.to, { limits: true, axis: true })}${axis(answer.from, answer.to)}</div>${stats}` : '<div class="d5-empty">No history for this measurement in the window.</div>';
    return `${head}<div class="d-body">`
      + ctx.section('d5-chart', 'Trend', chart, { right: `${values.length} point${values.length === 1 ? '' : 's'} · dashed = limits` })
      + ctx.section('d5-limits', 'Limits', ctx.h.kv(limits))
      + '</div>';
  }

  // ------------------------------------------------------------------- quiet levels ----
  function setupPage(ctx) {
    return `<div class="d-head"><div class="d5-kicker">${esc(labelFor(N.owner, ctx))}</div><div class="d-title"><span>Expected setup</span></div></div><div class="d-body">`
      + ctx.section('setup', 'Expected setup', ctx.parts.setup(), { right: 'the rig file, never the device' })
      + (ctx.detail && !ctx.parts.setup() ? '<div class="d5-empty">Not in the expected setup.</div>' : '') + '</div>';
  }
  function aboutPage(ctx) {
    const node = ctx.node;
    const facts = ctx.h.kv([
      ['name', esc(node.name)],
      ['path', `<span class="mono">${esc(node.path)}</span>`],
      ['owner', esc(node.owner || '—')],
      ['last received', node.age_s === null ? '<span class="muted">no records of its own</span>' : `${ctx.h.ago(node.age_s)} ago`],
    ]);
    return `<div class="d-head"><div class="d5-kicker">${esc(labelFor(N.owner, ctx))}</div><div class="d-title"><span>About</span></div></div><div class="d-body">`
      + ctx.section('identity', 'Identity', ctx.parts.identity())
      + ctx.section('d5-facts', 'Row', facts)
      + ctx.section('connection', `Connection (folded) · ${ctx.counts.connection}`, ctx.parts.connection(), { right: 'the layers this row is reached through' })
      + ctx.section('parts', `Parts · ${ctx.counts.parts}`, ctx.parts.parts(), { right: 'composition (part_of), folded into this row' }) + '</div>';
  }
  function eventsPage(ctx) {
    const faults = ctx.parts.faults();
    return `<div class="d-head"><div class="d5-kicker">${esc(labelFor(N.owner, ctx))}</div><div class="d-title"><span>Events</span></div></div><div class="d-body">`
      + ctx.section('faults', 'Faults', faults ? `<div data-diagnostic>${faults}</div>` : '')
      + ctx.section('events', 'Events · this row and below', ctx.parts.events()) + '</div>';
  }
  function linksPage(ctx) {
    return `<div class="d-head"><div class="d5-kicker">Computer</div><div class="d-title"><span>Link health</span></div></div><div class="d-body">`
      + ctx.section('links', 'Link health', ctx.parts.links(), { right: 'the computer diagnoses its own wires' }) + '</div>';
  }

  // ------------------------------------------------------------------------- events ----
  function wire(container) {
    if (container.dataset.d5Wired) return;
    container.dataset.d5Wired = '1';
    // before the page's own document handler (bubbling reaches #detail first): this file's pushes and pops.
    // WHY they stop the event: the redraw morphs the clicked element in place, and by the time the page's handler saw
    // it, the Back button could already carry the parent's data-go (one Esc walked up two levels).
    const own = (event) => { event.preventDefault(); event.stopPropagation(); };
    container.addEventListener('click', (event) => {
      if (!active()) return;
      const t = event.target;
      const pushEl = t.closest('[data-d5-push]');
      if (pushEl) {
        own(event);
        // a diagnostic sits right below its entity, also from a link on a deeper page (openDiagnostic replaces the drill)
        push({ kind: pushEl.dataset.d5Push, path: pushEl.dataset.d5Path || N.owner, name: pushEl.dataset.d5Name || '', key: pushEl.dataset.d5Key || '' });
        return;
      }
      // a crumb between the entity and the current level, and the ‹ button: one level up (the browser's Back when the
      // viewer came down from it). The entity's own crumb carries data-go: the page closes the drill.
      const crumb = t.closest('[data-d5-sub]');
      if (crumb && !crumb.dataset.go) { own(event); last.ctx.closeDiagnostic(); return; }
      const back = t.closest('[data-d5-back]');
      if (back && !back.dataset.go) { own(event); last.ctx.closeDiagnostic(); return; }
      const child = t.closest('[data-d5-child]');
      if (child) { N.pending = child.dataset.d5Child; N.pendingAt = Date.now(); }
    });
  }
  // Back on Esc / Backspace. WHY capture on the window, yielding to every open layer (a utility menu's popover or
  // palette handles its Escape first and marks it, the confirm modal, the chip, View options): one key, one layer.
  // In the Table, Esc at an entity's own level still folds the inspector (the page's rule); Backspace walks up.
  if (doc) {
    root.addEventListener('keydown', (event) => {
      if (event.defaultPrevented || !active() || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key !== 'Escape' && event.key !== 'Backspace') return;
      const typing = event.target.closest && event.target.closest('input, select, textarea, [contenteditable]');
      if (typing) return;
      const open = (id) => { const element = doc.getElementById(id); return element && !element.hidden; };
      if (open('modal') || open('chip-pop') || open('view-options')) return;
      // the header's Utilities ▾ menu or its dry-run sheet take their Escape first (the page's handler)
      if (doc.querySelector('.ent-menu, .ent-sheet, .u4-pal:not([hidden]), .u5-menu:not([hidden])')) return;
      const back = last.container.querySelector('[data-d5-back]:not([disabled])');
      if (!back) return;
      const table = doc.getElementById('main') && doc.getElementById('main').classList.contains('v-table');
      if (event.key === 'Escape' && table && !N.sub.length) return;
      event.preventDefault();
      event.stopPropagation();
      back.click();
    }, true);
  }

  // ------------------------------------------------------------------------- layout ----
  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d5 = {
    name: 'Drill',
    description: 'Computer → entity → diagnostic, pushed inside the pane (Finder columns, iOS Settings): the entity\'s status table and its children as rows; a status opens its own page (message, health bar and history, every measurement\'s trend, values, metadata, what to check); breadcrumb + Esc / Backspace (and the browser\'s back / forward) go back; the expected setup behind a quiet row.',
    order: 5,
    render(container, detail, ctx) {
      injectStyle();
      wire(container);
      last = { container, ctx };
      sweep();
      if (!ctx.group && !ctx.node) { ctx.draw(`${ctx.parts.head()}<div class="d-body"><div class="muted">loading…</div></div>`); return; }
      const current = ctx.group ? `group:${ctx.group.key}` : ctx.node.path;
      syncTrail(current, ctx);
      N.sub = subOf(ctx);
      const top = N.sub[N.sub.length - 1];
      let body;
      if (ctx.group) body = groupPage(ctx);
      else if (!top) body = entityPage(ctx);
      else if (top.kind === 'diag') body = diagPage(ctx, top);
      else if (top.kind === 'metric') body = metricPage(ctx, top);
      else if (top.kind === 'setup') body = setupPage(ctx);
      else if (top.kind === 'about') body = aboutPage(ctx);
      else if (top.kind === 'events') body = eventsPage(ctx);
      else if (top.kind === 'links') body = linksPage(ctx);
      else body = entityPage(ctx);
      // the scroll position of each level is the page's (it restores it on back / forward)
      ctx.draw(`<div class="d5-root" data-d5-level="${N.sub.length ? esc(top.kind) : 'entity'}">${crumbsHtml(ctx)}${body}</div>`);
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
