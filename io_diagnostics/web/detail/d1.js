// Detail layout d1 · Status monitor (round 3 proposal; contract: docs/round2-seams.md §6).
// rqt_robot_monitor / the ROS diagnostics viewer, done quietly. The entity's level and its one-line reason, then a
// health bar over the app-wide history window (/api/v1/history/bar), then the diagnostics status table: level · name
// · message, problems first, OK rows quiet. A row expands in place to its values, measurements (one sparkline each,
// from /api/v1/history) and metadata; its name opens that one diagnostic's own page (message history, trends,
// metadata, what to check) with a back link. Below: the children with their own mini bars, then everything else folded
// (identity, connection, utilities last).
// WHY this order: Zach, 2026-10-23: "the first thing you see on the entity is the table that shows the different
// diagnostics, their current status and the message. and then if you click it will maybe drop down and you can see the
// attached values etc or meta data … perhaps little spark lines? or if you click to expand into the diagnostic that
// should bring you to a page that really gets more in depth on that specific diagnostic." The step down is
// computer → entity → diagnostic.
'use strict';
(function (root) {
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const BINS = 60;            // the entity's health bar; the children's mini bars reuse the same bins
  const BAR_EVERY_MS = 3000;  // the bar and the history refresh at the API's suggested 2-5 s
  const HISTORY_EVERY_MS = 3000;
  const OPEN_PREF = 'io-diagnostics.d1.open'; // the folded sections this viewer opened (they start folded)
  const HIDDEN_VALUES = new Set(['what_to_check', 'kernel_evidence']); // drawn as the checklist, not as values

  // ------------------------------------------------------------------------------------------ state ----
  const expanded = new Set();   // `${entity path}\u0001${check name}`: rows opened in place
  let drill = null;             // {path, name}: the diagnostic whose own page is shown
  let opened = new Set();
  try { opened = new Set(JSON.parse(localStorage.getItem(OPEN_PREF) || '[]')); } catch (_) { opened = new Set(); }
  const saveOpened = () => { try { localStorage.setItem(OPEN_PREF, JSON.stringify([...opened])); } catch (_) { /* this page only */ } };
  let last = null;              // {container, detail, ctx}: the last draw, so a click or a fetch can draw again
  const wired = new WeakSet();

  // ------------------------------------------------------------------------------- history fetches ----
  // One small cache per URL; a fetch that lands draws again (only while this layout owns the panel).
  const cache = new Map();
  function want(url, everyMs) {
    const now = Date.now();
    const known = cache.get(url);
    if (known && (known.loading || now - known.at < everyMs)) return known;
    const entry = { data: known ? known.data : null, error: known ? known.error : '', at: now, loading: true };
    cache.set(url, entry);
    fetch(url).then((response) => response.json()).then((data) => {
      entry.loading = false; entry.at = Date.now();
      if (data && data.error) { entry.error = String(data.error); entry.data = null; } else { entry.data = data; entry.error = ''; }
      redraw();
    }).catch((error) => { entry.loading = false; entry.at = Date.now(); entry.error = String(error); redraw(); });
    if (cache.size > 64) for (const [key, value] of cache) if (now - value.at > 60000) cache.delete(key);
    return entry;
  }
  const barUrl = (path) => `/api/v1/history/bar?path=${encodeURIComponent(path)}&bins=${BINS}`;
  const historyUrl = (path, status) => `/api/v1/history?path=${encodeURIComponent(path)}${status ? `&status=${encodeURIComponent(status)}` : ''}`;

  function redraw() {
    if (!last || !last.container.querySelector('[data-d1]')) return; // another layout draws the panel now
    root.IODetailLayouts.d1.render(last.container, last.detail, last.ctx);
  }

  // ---------------------------------------------------------------------------------- formatting ----
  const pad = (n) => String(n).padStart(2, '0');
  const clock = (t) => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
  const span = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);
  const unitOf = (unit) => (unit && unit !== '1' ? ` ${unit}` : '');
  const num = (v) => (typeof v === 'boolean' ? String(v) : Number.isInteger(v) ? String(v) : String(+Number(v).toPrecision(4)));
  const rankOf = (level) => (level in RANK ? RANK[level] : -1);
  const valueOf = (v) => (typeof v === 'boolean' ? (v ? 1 : 0) : typeof v === 'number' && Number.isFinite(v) ? v : null);
  // A check name reads "<who>: <what>"; the who is drawn muted, nothing is cut (truthful rendering).
  function nameHtml(name, h) {
    const at = String(name).indexOf(': ');
    return at > 0 ? `<span class="d1-pre">${h.esc(name.slice(0, at + 2))}</span>${h.esc(name.slice(at + 2))}` : h.esc(name);
  }
  // A level pill IS a diagnostic result (the entity's or the check's level): mark it so, as the warn-colours gate reads.
  const tagPills = (html) => html.replace(/<span class="pill (warn|error|stale|unknown)">/g, '<span class="pill $1" data-diagnostic>')
    .replace(/<div class="banner stale">/g, '<div class="banner stale" data-diagnostic>');

  // ---------------------------------------------------------------------------------- the marks ----
  // The health bar: one cell per bin, the worst level any status held in it, as the API computed it. null = no data
  // (a baseline only, never a level); OK is a quiet grey; only a problem has a colour.
  function bar(bins, from, binS, cls, h) {
    if (!bins || !bins.length) return '';
    return `<div class="d1-hb ${cls}" data-diagnostic role="img" aria-label="health over the window">${bins.map((level, at) => {
      const t0 = from + at * binS;
      return `<i class="${level ? h.esc(level) : 'none'}" title="${clock(t0)}–${clock(t0 + binS)} · ${level ? h.esc(level) : 'no data'}"></i>`;
    }).join('')}</div>`;
  }

  // One status's own timeline, span by span (exact, not binned): the diagnostic page's strip.
  function strip(levels, from, to, h) {
    const width = Math.max(to - from, 1e-9);
    return `<div class="d1-strip" data-diagnostic role="img" aria-label="level over the window">${levels.map((item) => {
      const left = Math.max(0, (item.from - from) / width) * 100;
      const right = Math.min(1, (item.to - from) / width) * 100;
      if (right <= left) return '';
      return `<i class="${item.level ? h.esc(item.level) : 'none'}" style="left:${left.toFixed(3)}%;width:${(right - left).toFixed(3)}%"`
        + ` title="${clock(item.from)}–${clock(item.to)} · ${item.level ? h.esc(item.level) : 'no data'}${item.message ? ` · ${h.esc(item.message)}` : ''}"></i>`;
    }).join('')}</div>`;
  }

  // A sparkline over [from, to] on the host clock: a point only where a value arrived, the line broken where a value
  // was unreadable or the status went quiet for longer than its stale threshold. Neutral ink; limits dashed and faint.
  function spark(series, from, to, staleAfter, h, big) {
    const points = ((series && series.points) || []).filter(([t]) => t >= from - 1e-6 && t <= to + 1e-6);
    const numeric = points.map(([t, v]) => [t, valueOf(v)]).filter(([, v]) => v !== null);
    if (!numeric.length) return '<span class="d1-nospark">no data in the window</span>';
    const W = 1000;
    const H = big ? 40 : 18;
    let lo = Math.min(...numeric.map(([, v]) => v));
    let hi = Math.max(...numeric.map(([, v]) => v));
    if (series.unit === '%') { lo = Math.min(lo, 0); hi = Math.max(hi, 100); }
    if (hi - lo < 1e-12) { const padY = Math.abs(hi) * 0.05 || 1; lo -= padY; hi += padY; }
    const x = (t) => ((t - from) / Math.max(to - from, 1e-9)) * W;
    const y = (v) => (H - 2) - ((v - lo) / (hi - lo)) * (H - 4);
    let d = '';
    let prev = null;
    for (const [t, raw] of points) {
      const v = valueOf(raw);
      if (v === null) { prev = null; continue; }
      const gap = prev && staleAfter && t - prev > staleAfter;
      d += `${!prev || gap ? 'M' : 'L'}${x(t).toFixed(1)} ${y(v).toFixed(2)} `;
      prev = t;
    }
    const limits = ['warn_low', 'warn_high', 'error_low', 'error_high'].map((k) => series[k])
      .filter((v) => typeof v === 'number' && v > lo && v < hi)
      .map((v) => `<path class="lim" d="M0 ${y(v).toFixed(2)} H${W}" vector-effect="non-scaling-stroke"/>`).join('');
    const [lt, lv] = numeric[numeric.length - 1];
    const tip = `${numeric.length} value${numeric.length === 1 ? '' : 's'} · min ${num(Math.min(...numeric.map(([, v]) => v)))} · max ${num(Math.max(...numeric.map(([, v]) => v)))}${unitOf(series.unit)}`;
    return `<svg class="d1-sp${big ? ' big' : ''}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${h.esc(tip)}"><title>${h.esc(tip)}</title>`
      + `${limits}<path class="ln" d="${d.trim()}" vector-effect="non-scaling-stroke"/>`
      + `<path class="dot" d="M${x(lt).toFixed(1)} ${y(lv).toFixed(2)} l0 0" vector-effect="non-scaling-stroke"/></svg>`;
  }

  // --------------------------------------------------------------------------------- the pieces ----
  function fold(id, title, body, right) {
    if (!body) return '';
    const open = opened.has(id);
    return `<section class="sec ${id}" data-sec="${id}"><h3 class="sec-h"><button class="sec-t" data-d1-fold="${id}" aria-expanded="${open}">`
      + `<svg class="ic sec-chev"><use href="#i-chev"/></svg><span>${title}</span></button>${right ? `<span class="right">${right}</span>` : ''}</h3>`
      + `${open ? `<div class="sec-b">${body}</div>` : ''}</section>`;
  }
  const plain = (id, title, body, right) => (body ? `<section class="sec ${id}"><h3 class="sec-h d1-h"><span>${title}</span>${right ? `<span class="right">${right}</span>` : ''}</h3><div class="sec-b">${body}</div></section>` : '');

  function checklist(check, h) {
    const values = Object.fromEntries((check.values || []).map((v) => [v.key, v.value]));
    if (!values.what_to_check) return '';
    const lines = String(values.what_to_check).split('\n').filter((line) => line.trim());
    return `<div class="why-lines">${lines.map((line) => `<div>${h.esc(line)}</div>`).join('')}</div>`
      + (values.kernel_evidence ? `<div class="why-ev">kernel evidence: ${h.esc(values.kernel_evidence)}</div>` : '');
  }

  // received 08:55:30 · 1 s ago · every 10 s · STALE after 30 s (the quiet line Zach liked, …085552)
  function metaLine(check, ctx) {
    const h = ctx.h;
    const timing = check.expected_period_s ? `every ${num(check.expected_period_s)} s · STALE after ${num(check.stale_after_s)} s` : 'event-driven (never stale)';
    const caused = (check.caused_by || []).map((path) => {
      const node = ctx.byPath.get(path);
      return `<a class="link" data-go="${h.esc(path)}">${h.esc(node ? node.name : path)}</a>`;
    }).join(', ');
    return `<div class="d1-meta">received ${h.esc((check.received_iso || '').slice(11, 19) || '—')} · ${h.ago(check.age_s)} ago · ${h.esc(timing)}`
      + `${check.level !== check.effective_level ? ` · reported ${h.esc(check.level)}` : ''}${check.code ? ` · code <span class="mono">${h.esc(check.code)}</span>` : ''}`
      + `${caused ? `<br>caused by ${caused}` : ''}<br>${h.esc(check.source || '')}</div>`;
  }

  function statusOf(history, name) {
    return history && history.data ? (history.data.statuses || []).find((status) => status.name === name) || null : null;
  }

  function measurementRows(check, status, data, ctx, big) {
    const h = ctx.h;
    if (!check.measurements.length) return '';
    const series = new Map(((status && status.measurements) || []).map((m) => [m.key, m]));
    return `<div class="d1-ms${big ? ' big' : ''}">${check.measurements.map((m) => {
      const s = series.get(m.key);
      const line = !data ? '<span class="d1-nospark">…</span>'
        : spark(s || { points: [], unit: m.unit }, data.from, data.to, status && status.stale_after_s, h, big);
      let stats = '';
      if (big && s) {
        const vals = s.points.map(([, v]) => valueOf(v)).filter((v) => v !== null);
        if (vals.length) stats = `<span class="d1-mm">${num(Math.min(...vals))} – ${num(Math.max(...vals))}${h.esc(unitOf(s.unit))}</span>`;
      }
      const tint = PROBLEM.has(m.level) ? ' data-diagnostic' : '';
      return `<span class="d1-mk" title="${h.esc(m.key)}">${h.esc(m.key)}</span>${line}`
        + `<span class="d1-mv"${tint}>${h.shape(m.level, m.level)}${h.esc(m.text)}${stats}</span>`;
    }).join('')}</div>`;
  }

  function valuesKv(check, h) {
    return h.kv(check.values.filter((v) => !HIDDEN_VALUES.has(v.key)).map((v) => [v.key, `<span class="d1-v">${h.esc(v.value)}</span>`]));
  }

  // The one-line "what sets this level": the API's own reading (a grouping row shows the worst below; a black box its
  // own worst check, which may name the members that caused it in caused_by; a knock-on is explained above).
  function rollsUp(node, checks, ctx) {
    const h = ctx.h;
    if (!node) return '';
    if (node.own.level === null || node.own.level === undefined) {
      const origin = node.rollup.origin && node.rollup.origin !== node.path
        ? ` — <a class="link" data-go="${h.esc(node.rollup.origin)}">${h.esc(node.rollup.origin_name)}</a> is ${h.esc(node.rollup.level)}` : '';
      return `<div class="d1-why">No diagnostics of its own: it shows the worst below${node.rollup.level ? origin : ' (nothing below reports)'}.</div>`;
    }
    const worst = checks[0];
    if (!worst || !PROBLEM.has(node.level)) {
      return `<div class="d1-why">${checks.length} diagnostic${checks.length === 1 ? '' : 's'}${checks.length ? ', all OK' : ''}: its level is the worst of them.</div>`;
    }
    const caused = (worst.caused_by || []).map((path) => {
      const n = ctx.byPath.get(path);
      return `<a class="link" data-go="${h.esc(path)}">${h.esc(n ? n.name : path)}</a>`;
    }).join(', ');
    const setters = checks.filter((c) => c.effective_level === node.level).length;
    return `<div class="d1-why">Set by its own worst diagnostic${setters > 1 ? `s (${setters})` : ''}: <b>${h.esc(worst.name)}</b>`
      + `${caused ? `, caused by ${caused}` : ''}${node.knock_on_of ? ` — explained by <a class="link" data-go="${h.esc(node.knock_on_of)}">${h.esc((ctx.byPath.get(node.knock_on_of) || {}).name || node.knock_on_of)}</a>, counted once there` : ''}.</div>`;
  }

  function healthBar(path, ctx, label) {
    const h = ctx.h;
    const entry = want(barUrl(path), BAR_EVERY_MS);
    const data = entry.data && entry.data.path === path ? entry.data : null;
    if (!data) return `<div class="d1-bar"><div class="d1-axis"><span>${entry.error ? `no history: ${h.esc(entry.error)}` : 'history loading…'}</span></div></div>`;
    const own = data.rows.find((row) => row.depth === 0);
    const basis = !own || own.basis === 'none' ? 'nothing in the history yet'
      : own.basis === 'below' ? `everything below · ${own.statuses} status${own.statuses === 1 ? '' : 'es'}`
        : `${label || 'its own'} ${own.statuses} diagnostic${own.statuses === 1 ? '' : 's'}`;
    return `<div class="d1-bar">${own ? bar(own.bar, data.from, data.bin_s, '', h) : ''}`
      + `<div class="d1-axis"><span>−${span(data.window_s)}</span><span>${h.esc(basis)} · worst per ${span(data.bin_s)}</span><span>now</span></div></div>`;
  }

  // ------------------------------------------------------------------------------- the entity page ----
  function statusTable(node, checks, ctx) {
    const h = ctx.h;
    if (!checks.length) return '';
    const sets = node && PROBLEM.has(node.level) && node.own.level !== null ? node.level : null;
    const anyOpen = checks.some((c) => expanded.has(`${node.path}\u0001${c.name}`));
    const history = anyOpen ? want(historyUrl(node.path), HISTORY_EVERY_MS) : null;
    const data = history && history.data && history.data.path === node.path ? history.data : null;
    const rows = checks.map((check) => {
      const level = check.effective_level;
      const bad = PROBLEM.has(level);
      const key = `${node.path}\u0001${check.name}`;
      const open = expanded.has(key);
      const word = bad ? `<span class="lvword lv-${h.esc(level)}">${h.esc(level)}</span>` : '<span class="d1-okw">ok</span>';
      const tag = sets && level === sets ? '<span class="d1-tag">sets the level</span>' : '';
      let body = '';
      if (open) {
        const status = statusOf(history, check.name);
        body = `<div class="d1-x">${bad ? checklist(check, h) : ''}${measurementRows(check, status, data, ctx, false)}${valuesKv(check, h)}${metaLine(check, ctx)}`
          + `<div class="d1-more"><a class="link" data-d1-open="${h.esc(check.name)}" tabindex="0">Open this diagnostic →</a></div></div>`;
      }
      return `<div class="d1-r${bad ? ' bad' : ''}${open ? ' open' : ''}"${bad ? ' data-diagnostic' : ''}>`
        + `<div class="d1-line" data-d1-expand="${h.esc(check.name)}" role="button" tabindex="0" aria-expanded="${open}">`
        + `<svg class="ic d1-chev"><use href="#i-chev"/></svg><span class="d1-lv">${h.shape(level, level)}${word}</span>`
        + `<span class="d1-name"><a class="d1-open" data-d1-open="${h.esc(check.name)}" tabindex="0" title="Open ${h.esc(check.name)}">${nameHtml(check.name, h)}</a></span>`
        + `<span class="d1-msg">${h.esc(check.message || '—')}${check.code && bad ? ` <span class="mono d1-code">${h.esc(check.code)}</span>` : ''}${tag}</span></div>${body}</div>`;
    }).join('');
    return `<div class="d1-tab"><div class="d1-hd"><span></span><span>Level</span><span>Name</span><span>Message</span></div>${rows}</div>`;
  }

  function childrenList(node, ctx) {
    const h = ctx.h;
    const children = (node && node.children) || [];
    if (!children.length) return '';
    const entry = cache.get(barUrl(node.path));
    const data = entry && entry.data && entry.data.path === node.path ? entry.data : null;
    const bars = new Map(data ? data.rows.filter((row) => row.depth === 1).map((row) => [row.path, row]) : []);
    const worst = (child) => Math.max(rankOf(child.level), child.hint ? rankOf(child.hint.level) : -1);
    const order = children.map((child, at) => ({ child, at })).sort((a, b) => {
      const pa = PROBLEM.has(a.child.level) || a.child.hint ? worst(a.child) : -1;
      const pb = PROBLEM.has(b.child.level) || b.child.hint ? worst(b.child) : -1;
      return (pb - pa) || (a.at - b.at);
    });
    return order.map(({ child }) => {
      const row = bars.get(child.path);
      const hint = child.hint ? `<span class="d1-hint" title="below: ${h.esc(child.hint.level)}">${h.shape(child.hint.level, `below: ${child.hint.level}`)}</span>` : '';
      return `<div class="d1-ch${child.connected === false ? ' off' : ''}" data-go="${h.esc(child.path)}" title="${h.esc(child.name)}">${h.icon(child.icon)}`
        + `<span class="d1-cn">${h.esc(child.name)}</span><span class="d1-cm">${h.esc(child.message || '')}</span>`
        + `${row && row.basis !== 'none' ? bar(row.bar, data.from, data.bin_s, 'd1-mini', h) : '<span class="d1-mini d1-nobar">no history</span>'}`
        + `<span class="d1-cs${child.knock_on_of ? ' knock' : ''}">${h.shape(child.level, child.message)}${hint}</span></div>`;
    }).join('');
  }

  function entityPage(node, detail, ctx) {
    const p = ctx.parts;
    const h = ctx.h;
    const checks = detail.checks.map((check, at) => ({ check, at }))
      .sort((a, b) => (rankOf(b.check.effective_level) - rankOf(a.check.effective_level)) || (a.at - b.at)).map(({ check }) => check);
    const problems = checks.filter((c) => PROBLEM.has(c.effective_level)).length;
    const owner = (detail.actions[0] || {}).owner || '';
    const kids = (node.children || []).length;
    return `${tagPills(p.head())}${tagPills(p.banners())}<div class="d-body" data-d1>`
      + rollsUp(node, checks, ctx)
      + healthBar(node.path, ctx)
      + plain('d1-diag', `Diagnostics · ${checks.length}`, statusTable(node, checks, ctx) || '<div class="muted">No diagnostics of its own.</div>',
        problems ? `${problems} problem${problems === 1 ? '' : 's'} first · click a row for its values` : 'click a row for its values, a name for its page')
      + plain('d1-children', `Below · ${kids}`, childrenList(node, ctx), 'each row\'s own health over the same window')
      + fold('faults', `Faults · ${detail.faults.length}`, p.faults())
      + fold('events', `Events · ${detail.events.length}`, detail.events.length ? p.events() : '')
      + fold('links', 'Link health', p.links(), 'the computer diagnoses its own wires')
      + fold('setup', 'Expected setup', p.setup(), 'the rig file, never the device')
      + fold('status', 'Rollup', p.status())
      + fold('identity', 'Identity', p.identity())
      + fold('connection', `Connection · ${ctx.counts.connection}`, p.connection(), 'the layers this row is reached through')
      + fold('utilities', `Utilities · ${detail.actions.length}`, p.utilities(), owner ? `${h.esc(owner)} runs them · every click is a dry run first` : '')
      + '</div>';
  }

  // --------------------------------------------------------------------------- the diagnostic page ----
  function diagnosticPage(node, detail, check, ctx) {
    const h = ctx.h;
    const name = drill.name;
    const back = `<div class="d1-back"><a class="link" data-d1-back tabindex="0">← ${h.esc(ctx.row ? ctx.row.label : node.name)}</a><span class="d1-crumb"> › diagnostic</span></div>`;
    const history = want(historyUrl(node.path, name), HISTORY_EVERY_MS / 1.5);
    const data = history.data && history.data.path === node.path ? history.data : null;
    const status = data ? (data.statuses || []).find((s) => s.name === name) || null : null;
    const level = check ? check.effective_level : null;
    const head = `<div class="d-head">${back}<div class="d-title">${h.icon(node.icon)}<span>${h.esc(name)}</span>${tagPills(h.pill(level))}</div>`
      + `<div class="d-msg">${check ? h.esc(check.message || '—') : '<span class="muted">This diagnostic is no longer reported; its history stays.</span>'}`
      + `${check && check.code ? ` <span class="mono muted">${h.esc(check.code)}</span>` : ''}</div>`
      + `<div class="d-path">${h.esc(node.name)} · ${h.esc(node.path)}</div></div>`;
    let timeline = '<div class="d1-axis"><span>history loading…</span></div>';
    if (history.error && !data) timeline = `<div class="d1-axis"><span>no history: ${h.esc(history.error)}</span></div>`;
    else if (data && !status) timeline = '<div class="d1-axis"><span>nothing in the history for this diagnostic yet</span></div>';
    else if (data) {
      timeline = strip(status.levels, data.from, data.to, h)
        + `<div class="d1-axis"><span>−${span(data.window_s)} · ${clock(data.from)}</span><span>each level held until stale · empty = no data</span><span>now · ${clock(data.to)}</span></div>`;
    }
    const changes = status ? status.levels.slice().reverse().map((item) => {
      const bad = PROBLEM.has(item.level);
      const lv = item.level ? (bad ? `${h.shape(item.level, item.level)}<span class="lvword lv-${h.esc(item.level)}">${h.esc(item.level)}</span>` : '<span class="d1-okw">ok</span>') : '<span class="d1-okw">no data</span>';
      return `<div class="d1-hr${item.level ? '' : ' nodata'}"${bad ? ' data-diagnostic' : ''}><span class="d1-ht">${clock(item.from)}–${clock(item.to)} <span class="d1-hd2">${span(item.to - item.from)}</span></span>`
        + `<span class="d1-hl">${lv}</span><span class="d1-hm">${h.esc(item.message || '')}${item.code ? ` <span class="mono d1-code">${h.esc(item.code)}</span>` : ''}</span></div>`;
    }).join('') : '';
    const meta = check ? h.kv([
      ['level now', `${tagPills(h.pill(check.effective_level))}${check.level !== check.effective_level ? ` <span class="muted">reported ${h.esc(check.level)}</span>` : ''}`],
      ['code', check.code ? `<span class="mono">${h.esc(check.code)}</span>` : '<span class="muted">—</span>'],
      ['source', h.esc(check.source || '—')],
      check.hardware_id ? ['hardware id', `<span class="mono">${h.esc(check.hardware_id)}</span>`] : null,
      ['expected period', check.expected_period_s ? `every ${num(check.expected_period_s)} s · STALE after ${num(check.stale_after_s)} s` : 'event-driven (never stale)'],
      ['last received', `${h.esc((check.received_iso || '').replace('T', ' ').slice(0, 19) || '—')} · ${h.ago(check.age_s)} ago`],
      (check.caused_by || []).length ? ['caused by', check.caused_by.map((path) => { const n = ctx.byPath.get(path); return `<a class="link" data-go="${h.esc(path)}">${h.esc(n ? n.name : path)}</a>`; }).join(', ')] : null,
      ['entity', `<a class="link" data-go="${h.esc(node.path)}" data-d1-back>${h.esc(node.name)}</a>`],
    ]) : '';
    return `${head}<div class="d-body" data-d1>`
      + plain('d1-timeline', 'History', timeline, status ? `${status.levels.filter((l) => l.level).length} span${status.levels.filter((l) => l.level).length === 1 ? '' : 's'} with data` : '')
      + (check && PROBLEM.has(level) ? plain('why', 'What to check', checklist(check, h)) : '')
      + (check ? plain('d1-trends', `Measurements · ${check.measurements.length}`, measurementRows(check, status, data, ctx, true), 'now · min – max over the window') : '')
      + plain('d1-history', `Message history · ${status ? status.levels.length : 0}`, changes, 'newest first; a run of equal reports is one line')
      + (check ? plain('d1-values', 'Values', valuesKv(check, h)) : '')
      + plain('d1-metadata', 'Metadata', meta)
      + (check && !PROBLEM.has(level) ? fold('why', 'What to check', checklist(check, h)) : '')
      + '</div>';
  }

  // ----------------------------------------------------------------------------------- the CSS ----
  const CSS = `
.d1-why { margin: 10px 0 0; font-size: 12.5px; color: var(--io-muted); }
.d1-why b { color: var(--io-text-strong); font-weight: 600; }
.d1-bar { margin: 8px 0 4px; }
.d1-hb { display: flex; gap: 1px; height: 12px; }
.d1-hb i { flex: 1 1 0; min-width: 1px; border-radius: 1px; background: var(--io-guide); }
.d1-hb i.none, .d1-strip i.none { background: transparent; box-shadow: inset 0 -2px 0 var(--io-border); }
.d1-hb i.warn, .d1-strip i.warn { background: var(--io-warn); }
.d1-hb i.error, .d1-strip i.error { background: var(--io-error); }
.d1-hb i.stale, .d1-strip i.stale { background: var(--io-stale); }
.d1-hb i.unknown, .d1-strip i.unknown { background: var(--io-unknown); }
.d1-axis { display: flex; justify-content: space-between; gap: 10px; margin-top: 3px; font-size: 11px; color: var(--io-faint); font-variant-numeric: tabular-nums; }
.d1-axis span:nth-child(2) { text-align: center; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.d1-strip { position: relative; height: 14px; box-shadow: inset 0 -2px 0 var(--io-border); }
.d1-strip i { position: absolute; top: 0; bottom: 0; background: var(--io-guide); }
.d1-h { cursor: default; }
.sec.d1-diag .sec-b, .sec.d1-history .sec-b { padding-top: 0; }
.d1-tab { font-size: 12.5px; }
.d1-hd, .d1-line { display: grid; grid-template-columns: 12px 64px minmax(0, 36%) minmax(0, 1fr); gap: 8px; }
.d1-hd { padding: 2px 4px 4px; color: var(--io-faint); font-size: 11px; border-bottom: 1px solid var(--io-border-soft); }
.d1-r { border-bottom: 1px solid var(--io-border-soft); }
.d1-line { align-items: baseline; padding: 5px 4px; border-radius: var(--io-radius-sm); cursor: pointer; }
.d1-line:hover { background: var(--io-hover); }
.d1-line:focus-visible, .d1-open:focus-visible, [data-d1-open]:focus-visible, [data-d1-back]:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.d1-chev { width: 12px; height: 12px; color: var(--io-faint); align-self: start; margin-top: 2px; transition: transform .12s; }
.d1-r.open .d1-chev { transform: rotate(90deg); }
.d1-lv { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.d1-lv .shape { width: 12px; height: 12px; }
.d1-lv .lvword { margin: 0; }
.d1-okw { color: var(--io-faint); font-size: 11px; letter-spacing: .3px; text-transform: uppercase; }
.d1-name { min-width: 0; overflow-wrap: anywhere; }
.d1-open { color: var(--io-muted); cursor: pointer; text-decoration: none; }
.d1-open:hover { text-decoration: underline; color: var(--io-text-strong); }
.d1-r.bad .d1-open { color: var(--io-text-strong); font-weight: 600; }
.d1-pre { color: var(--io-faint); font-weight: 400; }
.d1-msg { min-width: 0; overflow-wrap: anywhere; color: var(--io-muted); }
.d1-r.bad .d1-msg { color: var(--io-text); }
.d1-code { color: var(--io-faint); font-size: 11px; }
.d1-tag { margin-left: 6px; padding: 0 6px; border: 1px solid var(--io-border); border-radius: var(--io-radius-pill); color: var(--io-muted); font-size: 10.5px; white-space: nowrap; }
.d1-x { padding: 2px 4px 10px 26px; display: grid; gap: 8px; min-width: 0; }
.d1-x .kv { grid-template-columns: 120px 1fr; }
.d1-v { white-space: pre-line; }
.d1-ms { display: grid; grid-template-columns: minmax(0, 30%) minmax(60px, 1fr) auto; gap: 1px 10px; align-items: center; font-size: 12px; }
.d1-ms.big { gap: 6px 12px; }
.d1-mk { color: var(--io-muted); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.d1-mv { justify-self: end; display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; font: 11.5px var(--io-font-mono); color: var(--io-text); font-variant-numeric: tabular-nums; }
.d1-mv .shape { width: 11px; height: 11px; }
.d1-mm { margin-left: 8px; color: var(--io-faint); }
.d1-sp { display: block; width: 100%; height: 18px; overflow: visible; }
.d1-sp.big { height: 40px; box-shadow: inset 0 -1px 0 var(--io-border-soft); }
.d1-sp .ln { fill: none; stroke: var(--io-muted); stroke-width: 1.25; stroke-linejoin: round; }
.d1-sp .dot { fill: none; stroke: var(--io-text-strong); stroke-width: 4; stroke-linecap: round; }
.d1-sp .lim { fill: none; stroke: var(--io-border); stroke-width: 1; stroke-dasharray: 3 3; }
.d1-nospark { color: var(--io-faint); font-size: 11px; }
.d1-meta { color: var(--io-faint); font-size: 11px; overflow-wrap: anywhere; }
.d1-more { font-size: 12px; }
.d1-ch { display: flex; align-items: center; gap: 8px; height: var(--io-row-h); padding: 0 6px; margin: 0 -6px; border-radius: var(--io-radius-sm); cursor: pointer; white-space: nowrap; }
.d1-ch:hover { background: var(--io-hover); }
.d1-ch .ic { color: var(--io-icon); flex: none; }
.d1-ch.off { color: var(--io-off); }
.d1-cn { flex: none; max-width: 45%; overflow: hidden; text-overflow: ellipsis; color: var(--io-text); }
.d1-cm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--io-muted); font-size: 12px; }
.d1-mini { flex: none; width: 120px; height: 8px; gap: 0; }
.d1-nobar { color: var(--io-faint); font-size: 11px; height: auto; text-align: right; }
.d1-cs { flex: none; width: 34px; display: inline-flex; justify-content: flex-end; gap: 2px; }
.d1-hint .shape { width: 10px; height: 10px; opacity: .8; }
.d1-back { margin-bottom: 8px; font-size: 12.5px; }
.d1-crumb { color: var(--io-faint); }
.d1-hr { display: grid; grid-template-columns: 170px 70px minmax(0, 1fr); gap: 8px; padding: 4px 0; border-top: 1px solid var(--io-border-soft); font-size: 12px; align-items: baseline; }
.d1-hr:first-child { border-top: 0; }
.d1-hr.nodata { color: var(--io-faint); }
.d1-ht { font: 11.5px var(--io-font-mono); color: var(--io-muted); font-variant-numeric: tabular-nums; }
.d1-hd2 { color: var(--io-faint); }
.d1-hl { display: inline-flex; align-items: center; gap: 4px; }
.d1-hl .shape { width: 11px; height: 11px; }
.d1-hl .lvword { margin: 0; }
.d1-hm { min-width: 0; overflow-wrap: anywhere; }
`;
  function injectCss() {
    if (typeof document === 'undefined' || document.querySelector('style[data-layout="d1"]')) return;
    const style = document.createElement('style');
    style.dataset.layout = 'd1';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  // ---------------------------------------------------------------------------------- the clicks ----
  // Only this layout's own hooks (data-d1-*); data-go, data-util and the rest stay the page's.
  function wire(container) {
    if (wired.has(container)) return;
    wired.add(container);
    const act = (target) => {
      const openEl = target.closest('[data-d1-open]');
      const backEl = target.closest('[data-d1-back]');
      const expandEl = target.closest('[data-d1-expand]');
      const foldEl = target.closest('[data-d1-fold]');
      const path = last && last.ctx.node && last.ctx.node.path;
      if (backEl) { drill = null; if (!backEl.hasAttribute('data-go')) redraw(); return true; }
      if (openEl && path) { drill = { path, name: openEl.dataset.d1Open }; redraw(); if (last) last.container.scrollTop = 0; return true; }
      if (expandEl && path) {
        const key = `${path}\u0001${expandEl.dataset.d1Expand}`;
        if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
        redraw();
        return true;
      }
      if (foldEl) {
        const id = foldEl.dataset.d1Fold;
        if (opened.has(id)) opened.delete(id); else opened.add(id);
        saveOpened();
        redraw();
        return true;
      }
      return false;
    };
    container.addEventListener('click', (event) => {
      const target = event.target;
      if (!(target instanceof Element) || target.closest('[data-util], .u-panel, input, select, textarea')) return;
      act(target);
    });
    container.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const target = event.target;
      if (!(target instanceof Element) || !target.matches('[data-d1-open], [data-d1-back], [data-d1-expand]')) return;
      if (act(target)) event.preventDefault();
    });
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d1 = {
    name: 'Status monitor',
    description: 'rqt_robot_monitor done quietly: level and reason, a health bar over the history window, then the diagnostics table (problems first, OK quiet); a row expands to its values and sparklines, a name opens that diagnostic\'s own page. Children with mini bars below; the rest folded, utilities last.',
    order: 1,
    // container = #detail; detail = GET /api/v1/node for the selection (null while it loads, or for a grouping row);
    // ctx = the page's building blocks (docs/round2-seams.md §6). Called every second: ctx.draw() skips an unchanged page.
    render(container, detail, ctx) {
      injectCss();
      wire(container);
      last = { container, detail, ctx };
      const p = ctx.parts;
      if (ctx.group) {
        drill = null;
        ctx.draw(`${tagPills(p.head())}<div class="d-body" data-d1>${plain('members', `Members · ${ctx.counts.members}`, p.members())}`
          + `${fold('links', 'Link health', p.links(), 'the computer diagnoses its own wires')}</div>`);
        return;
      }
      const node = ctx.node;
      if (drill && (!node || drill.path !== node.path)) drill = null;
      if (!detail) {
        ctx.draw(`${tagPills(p.head())}${tagPills(p.banners())}<div class="d-body" data-d1>${node ? healthBar(node.path, ctx) : ''}<div class="muted">loading…</div></div>`);
        return;
      }
      if (drill) {
        const check = detail.checks.find((c) => c.name === drill.name) || null;
        ctx.draw(diagnosticPage(node, detail, check, ctx));
        return;
      }
      ctx.draw(entityPage(node, detail, ctx));
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
