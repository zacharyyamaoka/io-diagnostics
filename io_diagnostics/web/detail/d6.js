// Detail layout d6 · Entity page (docs/state-config-v1.md; Zach, 2026-10-24, "My feedback on the entity page").
// Like realsense-viewer's device panel and moteus tview, plus the diagnostics view: a header that is always visible (the
// name and model, the connection badge, the ticking "heard 2 s ago", the Utilities menu the shell draws, the hardware line),
// then TABS, not one long scroll: Diagnostics (health only), State (the entity's live values: numbers as one-line rows with a
// sparkline, images as stream tiles), Configuration (read-only, the same for every driver), Log (this entity and below, with
// its Raw channel), Utilities (every action with its danger and last run), then a driver's own tabs (the Computer's Processes).
// State and Diagnostics never show each other's field: a value is never drawn as health, health never as a number.
//
// The live data: while the State tab shows, the page sends the host a heartbeat (POST /api/v1/watch, every 3 s) and polls
// GET /api/v1/values (250 ms while the watch runs); leaving the tab, the entity or the page stops both, and the host stops the
// plugin's watch about 10 s later. Images are <img> elements of the MJPEG stream (/api/v1/frames/stream).
// The tab is the page's `page` drill (?page=<id>: back / forward work), remembered per entity kind.
// WHY the Diagnostics rendering is d1's, copied: a parallel lane designs five variants of that section and owns the history
// bar; diagnosticsPanel() is the one function to replace when its winner lands.
'use strict';
(function (root) {
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const BINS = 60;
  const HIDDEN_VALUES = new Set(['what_to_check', 'kernel_evidence', 'fault_code', 'fault_meaning']);
  const BASE_TABS = [
    { id: 'diagnostics', title: 'Diagnostics' }, { id: 'state', title: 'State' }, { id: 'config', title: 'Configuration' },
    { id: 'log', title: 'Log' }, { id: 'utilities', title: 'Utilities' },
  ];
  const BEAT_MS = 3000;
  const SPARK_WINDOW_S = 60;
  const RING_MAX = 1500;
  const WATCH_MS = 250;
  const IDLE_MS = 1000;
  const STOP_AFTER_MS = 3500; // no render for this long (another layout, another entity's page): the live loops stop themselves
  const LOG_EVERY_MS = 2000;
  const KEYS = { name: 'io-diagnostics.d6.name-w', tile: 'io-diagnostics.d6.tile-w', folded: 'io-diagnostics.d6.folded', off: 'io-diagnostics.d6.streams-off', tab: 'io-diagnostics.entity-tab.' };

  // ---------------------------------------------------------------------------------------- storage ----
  const read = (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch (_) { /* private mode: lasts this page only */ } };
  const readSet = (key) => { try { return new Set(JSON.parse(read(key) || '[]')); } catch (_) { return new Set(); } };

  // ---------------------------------------------------------------------------------------- state ----
  const U = {
    nameW: Number(read(KEYS.name)) || 190, tileW: Number(read(KEYS.tile)) || 240,
    folded: readSet(KEYS.folded),        // 'images', 'state:<group>': folded blocks
    off: readSet(KEYS.off),              // `${path}\u0001${stream}`: streams the viewer switched off
    expanded: new Set(),                 // `${path}\u0001${check}`: Diagnostics rows opened in place
    more: new Set(),                     // entity paths whose hardware line is opened
    query: new Map(),                    // entity path -> the Configuration search text
    raw: false, warnOnly: false,         // the Log's Raw channel and filter
    looked: new Map(), seen: new Map(),  // entity path -> host time the Log was last shown / first drawn (the Log badge)
    sort: new Map(),                     // table key -> {col, desc}
    flash: null, forced: null, lightbox: null, copied: 0,
  };
  const Live = { path: null, cursor: 0, rings: new Map(), groups: [], streams: [], watch: null, heard: null, loaded: false, none: false,
    error: '', inflight: false, rendered: 0, beat: null, poll: null, now: 0 };
  const Heard = { base: null, at: 0, source: null };
  let last = null;
  const wired = new WeakSet();

  // ---------------------------------------------------------------------------------- formatting ----
  const num = (v) => (typeof v === 'boolean' ? String(v) : Number.isInteger(v) ? String(v) : String(+Number(v).toPrecision(4)));
  const rankOf = (level) => (level in RANK ? RANK[level] : -1);
  const valueOf = (v) => (typeof v === 'boolean' ? (v ? 1 : 0) : typeof v === 'number' && Number.isFinite(v) ? v : null);
  const span = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);
  const bytes = (n) => { const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let i = 0; let v = Number(n); while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; } return `${i ? v.toFixed(v < 10 ? 2 : 1) : v} ${u[i]}`; };
  const enc = encodeURIComponent;
  function nameHtml(name, h) {
    const at = String(name).indexOf(': ');
    return at > 0 ? `<span class="d6-pre">${h.esc(name.slice(0, at + 2))}</span>${h.esc(name.slice(at + 2))}` : h.esc(name);
  }

  // ----------------------------------------------------------------------- the ticking last-heard ----
  function setHeard(age) {
    if (typeof age !== 'number') return;
    Heard.base = age; Heard.at = performance.now();
  }
  const heardAge = () => (Heard.base === null ? null : Heard.base + (performance.now() - Heard.at) / 1000);
  function heardText(h) {
    const age = heardAge();
    return age === null ? 'never heard' : `heard ${h.ago(Math.max(0, age))} ago`;
  }
  if (typeof setInterval !== 'undefined') {
    setInterval(() => {
      if (typeof document === 'undefined' || !last) return;
      const text = heardText(last.ctx.h);
      document.querySelectorAll('[data-d6-heard]').forEach((el) => { if (el.textContent !== text) el.textContent = text; });
    }, 250);
  }

  // ------------------------------------------------------------------------------------ live data ----
  // The page's half of docs/state-config-v1.md section 4: a heartbeat and a poll, only while the State tab shows.
  function stopLive() {
    if (Live.beat) clearInterval(Live.beat);
    if (Live.poll) clearTimeout(Live.poll);
    Live.beat = Live.poll = null;
    Live.path = null;
  }
  const liveAlive = (path) => Live.path === path && !(typeof document !== 'undefined' && document.hidden) && performance.now() - Live.rendered < STOP_AFTER_MS;

  async function heartbeat(path) {
    try {
      const response = await fetch('/api/v1/watch', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify({ path }) });
      const body = await response.json();
      if (Live.path === path && body.watch) Live.watch = body.watch;
    } catch (_) { /* the poll shows the host is unreachable */ }
  }

  function merge(data) {
    if (data.cursor < Live.cursor) Live.rings.clear(); // the host restarted: its serial numbers began again
    Live.cursor = data.cursor;
    Live.now = data.now;
    Live.groups = data.groups;
    Live.streams = data.streams;
    Live.watch = data.watch;
    Live.heard = data.heard;
    Live.loaded = true;
    if (data.heard && typeof data.heard.age_s === 'number') setHeard(data.heard.age_s);
    for (const group of data.groups) {
      for (const row of group.rows) {
        const key = `${group.name}\u0001${row.key}`;
        const ring = Live.rings.get(key) || [];
        for (const point of row.points) ring.push(point);
        while (ring.length > RING_MAX || (ring.length && ring[0][0] < data.now - SPARK_WINDOW_S * 2)) ring.shift();
        Live.rings.set(key, ring);
      }
    }
  }

  async function pollOnce(path) {
    if (Live.inflight) return;
    Live.inflight = true;
    try {
      const response = await fetch(`/api/v1/values?path=${enc(path)}&since=${Live.cursor}`, { cache: 'no-store' });
      if (response.status === 404) {
        if (Live.path === path) { Live.none = true; Live.loaded = true; }
      } else {
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
        if (Live.path === path) { Live.none = false; Live.error = ''; merge(data); }
      }
    } catch (error) {
      if (Live.path === path) Live.error = String(error.message || error);
    } finally {
      Live.inflight = false;
    }
    if (Live.path === path && last) last.ctx.redraw();
  }

  function startLive(path, supported) {
    stopLive();
    Object.assign(Live, { path, cursor: 0, rings: new Map(), groups: [], streams: [], watch: null, heard: null, loaded: false, none: false, error: '', rendered: performance.now() });
    if (supported) {
      heartbeat(path);
      Live.beat = setInterval(() => { if (!liveAlive(path)) { stopLive(); return; } heartbeat(path); }, BEAT_MS);
    }
    const loop = async () => {
      if (!liveAlive(path)) { stopLive(); return; }
      await pollOnce(path);
      if (Live.path !== path) return;
      Live.poll = setTimeout(loop, Live.none ? 5000 : Live.watch && Live.watch.state === 'running' ? WATCH_MS : IDLE_MS);
    };
    loop();
  }

  // ------------------------------------------------------------------------------------- the marks ----
  // The health bar and a status's strip are d1's, drawn the same (the variants lane owns their styling).
  function bar(bins, from, binS, cls, h) {
    if (!bins || !bins.length) return '';
    return `<div class="d6-hb ${cls}" data-diagnostic role="img" aria-label="health over the window">${bins.map((level, at) => {
      return `<i class="${level ? h.esc(level) : 'none'}" title="${h.esc(level || 'no data')} · worst per ${span(binS)}"></i>`;
    }).join('')}</div>`;
  }
  function spark(points, now, maxS, series) {
    // the line grows from the left until it fills the last `maxS` seconds, then scrolls (a minimum of 10 s so a first sample is not a smear)
    const windowS = Math.min(maxS, Math.max(10, points.length ? now - points[0][0] : 0));
    const from = now - windowS;
    const numeric = points.filter(([t]) => t >= from - 1e-6).map(([t, v]) => [t, valueOf(v)]).filter(([, v]) => v !== null);
    if (!numeric.length) return '<span class="d6-nospark">no values</span>';
    const W = 1000;
    const H = 20;
    let lo = Math.min(...numeric.map(([, v]) => v));
    let hi = Math.max(...numeric.map(([, v]) => v));
    if (hi - lo < 1e-12) { const pad = Math.abs(hi) * 0.05 || 1; lo -= pad; hi += pad; }
    const x = (t) => ((t - from) / windowS) * W;
    const y = (v) => (H - 2) - ((v - lo) / (hi - lo)) * (H - 4);
    const step = Math.max(1, Math.ceil(numeric.length / 220));
    const dts = numeric.slice(1).map(([t], at) => t - numeric[at][0]).sort((a, b) => a - b);
    const gapS = Math.max(1.5, 6 * (dts[Math.floor(dts.length / 2)] || 0));
    let d = '';
    let prev = null;
    numeric.forEach(([t, v], at) => {
      if (at % step && at !== numeric.length - 1) return;
      d += `${prev === null || t - prev > gapS * step ? 'M' : 'L'}${x(t).toFixed(1)} ${y(v).toFixed(2)} `;
      prev = t;
    });
    const limits = ['warn_low', 'warn_high', 'error_low', 'error_high'].map((k) => series[k]).filter((v) => typeof v === 'number' && v > lo && v < hi)
      .map((v) => `<path class="lim" d="M0 ${y(v).toFixed(2)} H${W}" vector-effect="non-scaling-stroke"/>`).join('');
    const [lt, lv] = numeric[numeric.length - 1];
    const tip = `last ${Math.round(windowS)} s · ${numeric.length} values · min ${num(Math.min(...numeric.map(([, v]) => v)))} · max ${num(Math.max(...numeric.map(([, v]) => v)))}`;
    return `<svg class="d6-sp" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${tip}"><title>${tip}</title>${limits}`
      + `<path class="ln" d="${d.trim()}" vector-effect="non-scaling-stroke"/><path class="dot" d="M${x(lt).toFixed(1)} ${y(lv).toFixed(2)} l0 0" vector-effect="non-scaling-stroke"/></svg>`;
  }
  const grip = (id, label) => `<span class="d6-grip" data-d6-grip="${id}" role="separator" aria-orientation="vertical" tabindex="0" aria-label="${label}" title="Drag to resize; double-click resets"></span>`;

  // ------------------------------------------------------------------------------------- the header ----
  function hardwareItems(node) {
    const id = node.identity || {};
    return [['serial', id.serial_number], ['firmware', id.sw_version], ['model id', id.model_id]].filter(([, v]) => v);
  }
  function headerHtml(node, detail, ctx, row) {
    const h = ctx.h;
    const title = row ? row.label : node.name;
    const id = node.identity || {};
    const model = [id.manufacturer, id.model].filter(Boolean).join(' ');
    const showModel = model && !title.toLowerCase().includes(String(id.model || '').toLowerCase());
    const badge = detail && detail.badge ? `<span class="d6-badge-c" title="${h.esc(detail.badge.title || '')}">${h.esc(detail.badge.text)}</span>` : '';
    const stale = node.stale ? ' · stale' : '';
    const hw = hardwareItems(node);
    const open = U.more.has(node.path);
    const parent = detail && detail.ancestors && detail.ancestors[detail.ancestors.length - 1];
    const rest = [['hardware version', id.hw_version], ['hardware id', node.hardware_id ? `<span class="mono">${h.esc(node.hardware_id)}</span>` : ''],
      ['path', `<span class="mono">${h.esc(node.path)}</span>`], ['owner', node.owner], ['manufacturer', id.manufacturer],
      [node.edge === 'part_of' ? 'part of' : 'via', parent ? `<a class="link" data-go="${h.esc(parent.path)}">${h.esc(parent.name)}</a>` : '']]
      .filter(([, v]) => v).map(([k, v]) => [k, /^<(span|a)/.test(String(v)) ? v : h.esc(v)]);
    return `<div class="d-head d6-head"><div class="d-title">${h.icon(node.icon)}<span class="d6-name">${h.esc(title)}</span>${showModel ? `<span class="d6-model">${h.esc(model)}</span>` : ''}${h.pill(node.level)}</div>`
      + `<div class="d6-line">${badge}<span class="d6-heard" data-d6-heard title="when the host last received anything from this entity">${h.esc(heardText(h))}${stale}</span></div>`
      + `<div class="d-msg">${h.esc(node.message || '—')}</div>`
      + `<div class="d6-hw">${hw.map(([k, v]) => `<span><span class="d6-k">${h.esc(k)}</span> <span class="mono">${h.esc(v)}</span></span>`).join('')}`
      + `<button class="d6-more" data-d6-more aria-expanded="${open}">${open ? 'Less' : 'More'} ${open ? '▴' : '▾'}</button></div>`
      + (open ? `<div class="d6-hw-more">${h.kv(rest)}</div>` : '') + '</div>';
  }

  // --------------------------------------------------------------------------------- the tab bar ----
  const kindKey = (node) => KEYS.tab + (node.kind || 'device');
  function tabsFor(detail) {
    return [...BASE_TABS, ...((detail && detail.tabs) || []).map((tab) => ({ id: tab.id, title: tab.title, driver: tab }))];
  }
  function activeTab(ctx, node, tabs) {
    const ids = tabs.map((tab) => tab.id);
    if (U.forced && U.forced.path === node.path && ids.includes(U.forced.tab)) return U.forced.tab;
    if (ctx.page && ids.includes(ctx.page)) return ctx.page;
    const remembered = read(kindKey(node));
    return ids.includes(remembered) ? remembered : 'diagnostics';
  }
  function logBadge(node, ctx) {
    const events = (ctx.activity && ctx.activity.events) || [];
    const since = U.looked.get(node.path) ?? U.seen.get(node.path) ?? 0;
    const hits = events.filter((e) => inScope(e, node.path) && (e.severity === 'warn' || e.severity === 'error') && typeof e.stamp === 'number' && e.stamp > since);
    return hits.length;
  }
  const inScope = (event, path) => event.path === path || (event.path || '').startsWith(`${path}/`) || (path === 'host' && !event.path);
  function tabBar(ctx, node, detail, tabs, active) {
    const h = ctx.h;
    const checks = (detail && detail.checks) || [];
    const bad = checks.filter((c) => PROBLEM.has(c.effective_level));
    const worst = bad.reduce((w, c) => (rankOf(c.effective_level) > rankOf(w) ? c.effective_level : w), 'ok');
    const badges = {
      diagnostics: bad.length ? `<span class="d6-bd" data-diagnostic title="${bad.length} not OK, worst ${h.esc(worst)}">${h.shape(worst, worst)}<span>${bad.length}</span></span>` : '',
      log: (() => { const n = active === 'log' ? 0 : logBadge(node, ctx); return n ? `<span class="d6-bd plain" title="${n} warning${n === 1 ? '' : 's'} or error${n === 1 ? '' : 's'} since you last looked">${n}</span>` : ''; })(),
    };
    return `<div class="d6-tabs" role="tablist" aria-label="${h.esc(node.name)}" data-d6-tabs>${tabs.map((tab, at) => {
      const on = tab.id === active;
      return `<button class="d6-tab${on ? ' on' : ''}" role="tab" id="d6-tab-${h.esc(tab.id)}" aria-selected="${on}" aria-controls="d6-panel" tabindex="${on ? 0 : -1}" data-d6-tab="${h.esc(tab.id)}" title="${at < 9 ? `${at + 1} · ` : ''}${h.esc(tab.title)}">${h.esc(tab.title)}${badges[tab.id] || ''}</button>`;
    }).join('')}</div>`;
  }

  // ------------------------------------------------------------------------------- Diagnostics ----
  function checklist(check, h) {
    const values = Object.fromEntries((check.values || []).map((v) => [v.key, v.value]));
    if (!values.what_to_check) return '';
    const lines = String(values.what_to_check).split('\n').filter((line) => line.trim());
    return `<div class="why-lines">${lines.map((line) => `<div>${h.esc(line)}</div>`).join('')}</div>${values.kernel_evidence ? `<div class="why-ev">kernel evidence: ${h.esc(values.kernel_evidence)}</div>` : ''}`;
  }
  function faultOf(check) {
    const values = Object.fromEntries((check.values || []).map((v) => [v.key, v.value]));
    return values.fault_code ? { code: values.fault_code, meaning: values.fault_meaning || '' } : null;
  }
  function rollsUp(node, checks, ctx) {
    const h = ctx.h;
    if (isGrouping(node)) {
      const origin = node.rollup.origin && node.rollup.origin !== node.path
        ? ` — <a class="link" data-go="${h.esc(node.rollup.origin)}">${h.esc(node.rollup.origin_name)}</a> is ${h.esc(node.rollup.level)}` : '';
      return `<div class="d6-why">No diagnostics of its own: it shows the worst below${node.rollup.level ? origin : ' (nothing below reports)'}.</div>`;
    }
    const worst = checks[0];
    if (!worst || !PROBLEM.has(node.level)) return `<div class="d6-why">${checks.length} diagnostic${checks.length === 1 ? '' : 's'}${checks.length ? ', all OK' : ''}: its level is the worst of them.</div>`;
    const setters = checks.filter((c) => c.effective_level === node.level).length;
    return `<div class="d6-why">Set by its own worst diagnostic${setters > 1 ? `s (${setters})` : ''}: <b>${h.esc(worst.name)}</b>${node.knock_on_of ? ` — explained by <a class="link" data-go="${h.esc(node.knock_on_of)}">${h.esc((ctx.byPath.get(node.knock_on_of) || {}).name || node.knock_on_of)}</a>, counted once there` : ''}.</div>`;
  }
  const isGrouping = (node) => !node.own || node.own.level === null || node.own.level === undefined;
  function healthBar(path, ctx) {
    const h = ctx.h;
    const entry = ctx.bar(path, BINS);
    const data = entry.data && entry.data.path === path ? entry.data : null;
    if (!data) return `<div class="d6-bar"><div class="d6-axis"><span>${entry.error ? `no history: ${h.esc(entry.error)}` : 'history loading…'}</span></div></div>`;
    const own = data.rows.find((row) => row.depth === 0);
    const basis = !own || own.basis === 'none' ? 'nothing in the history yet' : own.basis === 'below' ? `everything below · ${own.statuses} status${own.statuses === 1 ? '' : 'es'}` : `its own ${own.statuses} diagnostic${own.statuses === 1 ? '' : 's'}`;
    return `<div class="d6-bar">${own ? bar(own.bar, data.from, data.bin_s, '', h) : ''}<div class="d6-axis"><span>−${span(data.window_s)}</span><span>${h.esc(basis)} · worst per ${span(data.bin_s)}</span><span>now</span></div></div>`;
  }
  function statusTable(node, checks, detail, ctx) {
    const h = ctx.h;
    if (!checks.length) return '';
    const sets = PROBLEM.has(node.level) && !isGrouping(node) ? node.level : null;
    const rows = checks.map((check) => {
      const level = check.effective_level;
      const bad = PROBLEM.has(level);
      const key = `${node.path}\u0001${check.name}`;
      const open = U.expanded.has(key);
      const fault = faultOf(check);
      const known = detail.faults.some((f) => f.code === check.code);
      const word = bad ? `<span class="lvword lv-${h.esc(level)}">${h.esc(level)}</span>` : '<span class="d6-okw">ok</span>';
      const flash = U.flash && U.flash.tab === 'diagnostics' && U.flash.code && (U.flash.code === check.code || U.flash.code === (fault && fault.code)) ? ' d6-flash' : '';
      const tag = sets && level === sets ? '<span class="d6-tag">sets the level</span>' : '';
      const explain = fault ? `<span class="d6-fx" title="fault code and what it means">${h.esc(fault.code)}${fault.meaning ? ` · ${h.esc(fault.meaning)}` : ''}</span>` : '';
      const log = known && check.code ? ` <a class="link d6-lg" data-d6-jump="log" data-code="${h.esc(check.code)}" tabindex="0" title="the Log lines of this fault">in the log →</a>` : '';
      let body = '';
      if (open) {
        const values = (check.values || []).filter((v) => !HIDDEN_VALUES.has(v.key));
        const caused = (check.caused_by || []).map((path) => `<a class="link" data-go="${h.esc(path)}">${h.esc((ctx.byPath.get(path) || {}).name || path)}</a>`).join(', ');
        const timing = check.expected_period_s ? `every ${num(check.expected_period_s)} s · STALE after ${num(check.stale_after_s)} s` : 'event-driven (never stale)';
        body = `<div class="d6-x">${bad ? checklist(check, h) : ''}${h.kv(values.map((v) => [v.key, `<span class="d6-v">${h.esc(v.value)}</span>`]))}`
          + `<div class="d6-meta">received ${h.esc((check.received_iso || '').slice(11, 19) || '—')} · ${h.ago(check.age_s)} ago · ${h.esc(timing)}${check.level !== check.effective_level ? ` · reported ${h.esc(check.level)}` : ''}${check.code ? ` · code <span class="mono">${h.esc(check.code)}</span>` : ''}${caused ? `<br>caused by ${caused}` : ''}<br>${h.esc(check.source || '')}</div></div>`;
      }
      return `<div class="d6-r${bad ? ' bad' : ''}${open ? ' open' : ''}${flash}"${bad ? ' data-diagnostic' : ''} data-code="${h.esc(check.code || '')}">`
        + `<div class="d6-rl" data-d6-expand="${h.esc(check.name)}" role="button" tabindex="0" aria-expanded="${open}"><svg class="ic d6-chev"><use href="#i-chev"/></svg>`
        + `<span class="d6-lv">${h.shape(level, level)}${word}</span><span class="d6-rn">${nameHtml(check.name, h)}</span>`
        + `<span class="d6-rm">${h.esc(check.message || '—')}${explain ? ` ${explain}` : ''}${check.code && bad ? ` <span class="mono d6-code">${h.esc(check.code)}</span>` : ''}${tag}${log}</span></div>${body}</div>`;
    }).join('');
    return `<div class="d6-tab-t"><div class="d6-hd"><span></span><span>Level</span><span>Name</span><span>Message</span></div>${rows}</div>`;
  }
  function childrenList(node, ctx) {
    const h = ctx.h;
    const children = node.children || [];
    if (!children.length) return '';
    const entry = ctx.bar(node.path, BINS);
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
      const hint = child.hint ? `<span class="d6-hint" title="below: ${h.esc(child.hint.level)}">${h.shape(child.hint.level, `below: ${child.hint.level}`)}</span>` : '';
      return `<div class="d6-ch${child.connected === false ? ' off' : ''}" data-go="${h.esc(child.path)}" title="${h.esc(child.name)}">${h.icon(child.icon)}<span class="d6-cn">${h.esc(child.name)}</span>`
        + `<span class="d6-cm">${h.esc(child.message || '')}</span>${row && row.basis !== 'none' ? bar(row.bar, data.from, data.bin_s, 'd6-mini', h) : '<span class="d6-mini d6-nobar">no history</span>'}`
        + `<span class="d6-cs">${h.shape(child.level, child.message)}${hint}</span></div>`;
    }).join('');
  }
  const plain = (id, title, body, right) => (body ? `<section class="sec ${id}"><h3 class="sec-h d6-h"><span>${title}</span>${right ? `<span class="right">${right}</span>` : ''}</h3><div class="sec-b">${body}</div></section>` : '');
  const fold = (ctx, id, title, body, right) => ctx.section(id, title, body, { right, defaultOpen: false });
  // The one function the variants lane replaces: the Diagnostics tab's body.
  function diagnosticsPanel(node, detail, ctx) {
    const p = ctx.parts;
    const checks = detail.checks.map((check, at) => ({ check, at })).sort((a, b) => (rankOf(b.check.effective_level) - rankOf(a.check.effective_level)) || (a.at - b.at)).map(({ check }) => check);
    const problems = checks.filter((c) => PROBLEM.has(c.effective_level)).length;
    const kids = (node.children || []).length;
    return rollsUp(node, checks, ctx) + healthBar(node.path, ctx)
      + plain('d6-diag', `Diagnostics · ${checks.length}`, statusTable(node, checks, detail, ctx) || '<div class="muted">No diagnostics of its own.</div>',
        problems ? `${problems} problem${problems === 1 ? '' : 's'} first · click a row for its detail` : 'click a row for its detail')
      + plain('d6-children', `Below · ${kids}`, childrenList(node, ctx), 'each row\'s own health over the same window')
      + fold(ctx, 'faults', `Faults · ${detail.faults.length}`, p.faults()) + fold(ctx, 'links', 'Link health', p.links(), 'the computer diagnoses its own wires')
      + fold(ctx, 'setup', 'Expected setup', p.setup(), 'the rig file, never the device') + fold(ctx, 'status', 'Rollup', p.status())
      + fold(ctx, 'connection', `Connection · ${ctx.counts.connection}`, p.connection(), 'the layers this row is reached through');
  }

  // ------------------------------------------------------------------------------------------ State ----
  function watchNote(detail) {
    const watch = Live.watch || (detail && detail.watch) || { supported: false, state: 'unsupported' };
    if (!watch.supported) return { cls: 'plain', text: 'Not live: this driver does not stream. The values are from its last check.' };
    switch (watch.state) {
      case 'running': return { cls: 'live', text: `live · every ${num(watch.period_s)} s` };
      case 'paused': return { cls: 'paused', text: `paused: ${watch.reason || 'something else is using the device'}`, watch };
      case 'restarting': return { cls: 'paused', text: `the live stream stopped${watch.reason ? ` (${watch.reason})` : ''}; starting it again` };
      case 'starting': return { cls: 'plain', text: 'starting the live stream…' };
      default: return { cls: 'plain', text: 'waiting for the live stream…' };
    }
  }
  function tile(stream, path, h) {
    const key = `${path}\u0001${stream.name}`;
    const on = !U.off.has(key);
    const size = stream.width ? `${stream.width}×${stream.height}` : '—';
    const stale = stream.age_s !== null && stream.age_s > 3;
    const rate = stream.fps ? `${stream.fps} fps` : stale ? `last frame ${h.ago(stream.age_s)} ago` : 'no frames';
    const src = `/api/v1/frames/stream?path=${enc(path)}&stream=${enc(stream.name)}&fps=10`;
    const picture = !on ? '<div class="d6-ph">off</div>' : stream.seq === null ? '<div class="d6-ph">waiting for the first frame</div>'
      : `<img src="${src}" alt="${h.esc(stream.title)}" draggable="false">`;
    return `<figure class="d6-tile${on ? '' : ' off'}"><figcaption><label class="d6-sw"><input type="checkbox" role="switch" data-d6-stream="${h.esc(stream.name)}"${on ? ' checked' : ''} aria-label="${h.esc(stream.title)} stream"><span class="d6-sw-t">${h.esc(stream.title)}</span></label>`
      + `<span class="d6-dim" title="${stream.colormap ? `colormap ${h.esc(stream.colormap)}` : ''}">${h.esc(size)} · ${h.esc(rate)}</span></figcaption>`
      + `<button class="d6-pic" data-d6-enlarge="${h.esc(stream.name)}" aria-label="Enlarge ${h.esc(stream.title)}"${on && stream.seq !== null ? '' : ' disabled'}>${picture}</button></figure>`;
  }
  function stateRows(group, h) {
    return group.rows.map((row) => {
      const ring = Live.rings.get(`${group.name}\u0001${row.key}`) || [];
      const bad = PROBLEM.has(row.level);
      const value = row.meaning ? `${h.esc(typeof row.value === 'number' ? num(row.value) : row.text)} · ${h.esc(row.meaning)}` : h.esc(row.text);
      return `<div class="d6-sr${bad ? ' bad' : ''}"${bad ? ' data-diagnostic' : ''}><span class="d6-sn" title="${h.esc(row.key)}">${h.esc(row.key)}</span>`
        + `<span class="d6-sv${row.meaning ? ' txt' : ''}">${bad ? h.shape(row.level, `${row.key}: ${row.level}`) : ''}${value}</span>`
        + `<span class="d6-ss">${spark(ring, Live.now, SPARK_WINDOW_S, row)}</span></div>`;
    }).join('');
  }
  function statePanel(node, detail, ctx) {
    const h = ctx.h;
    const note = watchNote(detail);
    const streams = Live.streams && Live.streams.length ? Live.streams : (detail.streams || []);
    const head = `<div class="d6-note ${note.cls}" role="status" data-d6-watch="${h.esc((Live.watch || detail.watch || {}).state || '')}">${note.cls === 'paused' ? '⏸ ' : note.cls === 'live' ? '<span class="d6-dot"></span>' : ''}${h.esc(note.text)}</div>`;
    if (!Live.loaded) return `${head}<div class="muted">loading…</div>`;
    if (Live.none && !streams.length) return `${head}<div class="muted">This entity reports no state of its own.</div>`;
    const images = streams.filter((s) => s.kind === 'image');
    const open = !U.folded.has('images');
    const imageBlock = images.length ? `<section class="d6-block"><h3 class="d6-bh"><button class="d6-fold" data-d6-fold="images" aria-expanded="${open}"><svg class="ic sec-chev"><use href="#i-chev"/></svg>Images · ${images.length}</button><span class="right">click an image to enlarge</span></h3>`
      + (open ? `<div class="d6-tiles">${images.map((s) => tile(s, node.path, h)).join('')}${grip('tile', 'Resize the image tiles')}</div>` : '') + '</section>' : '';
    const groups = (Live.groups || []).filter((g) => g.rows.length);
    const multi = groups.length > 1 || groups.some((g) => g.name);
    const numbers = groups.length ? `<section class="d6-block"><div class="d6-sh"><span>Name${grip('name', 'Resize the name column')}</span><span>Value</span><span>Recent · up to ${SPARK_WINDOW_S} s</span></div>`
      + groups.map((group) => {
        const key = `state:${group.name}`;
        const isOpen = !U.folded.has(key);
        const stale = group.stale || (group.age_s !== null && group.age_s > 5 && !(Live.watch && Live.watch.state === 'running'));
        const heading = multi ? `<h3 class="d6-gh"><button class="d6-fold" data-d6-fold="${h.esc(key)}" aria-expanded="${isOpen}"><svg class="ic sec-chev"><use href="#i-chev"/></svg>${h.esc(group.name || 'values')}</button>`
          + `${stale ? `<span class="right d6-staleq">last values ${h.ago(group.age_s)} ago</span>` : ''}</h3>` : (stale ? `<div class="d6-staleq">last values ${h.ago(group.age_s)} ago</div>` : '');
        return heading + (isOpen ? stateRows(group, h) : '');
      }).join('') + '</section>' : '';
    const empty = !images.length && !groups.length ? '<div class="muted">This entity reports no state of its own.</div>' : '';
    return head + imageBlock + numbers + empty + (Live.error ? `<div class="d6-staleq">the host did not answer: ${h.esc(Live.error)}</div>` : '');
  }

  // ------------------------------------------------------------------------------- Configuration ----
  function configRows(detail) {
    return (detail.config || []).map((row) => ({ ...row, group: row.group || 'general' }));
  }
  const cfgValue = (row) => `${row.value === null || row.value === undefined ? '—' : String(row.value)}${row.unit ? ` ${row.unit}` : ''}`;
  const cfgExpected = (row) => `${String(row.expected)}${row.unit ? ` ${row.unit}` : ''}`;
  function configText(rows) {
    const out = [];
    let group = null;
    for (const row of rows) {
      if (row.group !== group) { group = row.group; out.push(`[${group}]`); }
      out.push(`${row.key} = ${cfgValue(row)}${row.differs ? `   # expected ${cfgExpected(row)} (${row.expected_source || 'expected'})` : ''}`);
    }
    return out.join('\n');
  }
  function configPanel(node, detail, ctx) {
    const h = ctx.h;
    const all = configRows(detail);
    if (!all.length) return '<div class="muted">No configuration read from this entity. A driver sends it with its checks.</div>';
    const query = (U.query.get(node.path) || '').trim().toLowerCase();
    const rows = query ? all.filter((r) => `${r.key} ${cfgValue(r)} ${r.group} ${r.expected_source}`.toLowerCase().includes(query)) : all;
    const differs = all.filter((r) => r.differs).length;
    const groups = [...new Set(rows.map((r) => r.group))];
    const body = groups.map((group) => `<h3 class="d6-gh plain-h">${h.esc(group)}</h3>` + rows.filter((r) => r.group === group).map((row) => {
      const mark = row.differs ? `<span class="d6-diff" title="differs from what is expected">≠</span>` : '';
      const expected = row.differs ? `<div class="d6-exp">expected <b>${h.esc(cfgExpected(row))}</b>${row.expected_source ? ` · ${h.esc(row.expected_source)}` : ''}</div>` : '';
      return `<div class="d6-cr${row.differs ? ' differs' : ''}"><span class="d6-sn mono" title="${h.esc(row.key)}">${h.esc(row.key)}</span>`
        + `<span class="d6-cv"><span class="mono">${mark}${h.esc(cfgValue(row))}</span>${expected}</span>`
        + `<span class="d6-ca" title="read ${row.age_s === null ? '—' : `${h.ago(row.age_s)} ago`}">${row.age_s === null ? '' : `${h.ago(row.age_s)}`}</span></div>`;
    }).join('')).join('');
    return `<div class="d6-ctl"><input type="search" class="d6-search" data-d6-search placeholder="Search ${all.length} settings" value="${h.esc(U.query.get(node.path) || '')}" aria-label="Search the configuration">`
      + `<span class="d6-dim">${rows.length === all.length ? `${all.length} settings` : `${rows.length} of ${all.length}`}${differs ? ` · ${differs} differ${differs === 1 ? 's' : ''} from what is expected` : ''}</span>`
      + `<button class="btn d6-copy" data-d6-copy>${U.copied && Date.now() - U.copied < 2000 ? 'Copied' : 'Copy as text'}</button></div>`
      + `<div class="d6-ch-h"><span>Setting${grip('name', 'Resize the name column')}</span><span>Value</span><span>Read</span></div>` + (body || '<div class="muted">No setting matches.</div>');
  }

  // ------------------------------------------------------------------------------------------- Log ----
  const logCache = { path: null, at: 0, loading: false, data: null, error: '' };
  function logData(path, redraw) {
    if (logCache.path !== path) Object.assign(logCache, { path, at: 0, data: null, error: '' });
    if (!logCache.loading && Date.now() - logCache.at > LOG_EVERY_MS) {
      logCache.loading = true;
      fetch('/api/v1/activity?limit=3000', { cache: 'no-store' }).then((r) => r.json()).then((data) => { if (logCache.path === path) { logCache.data = data; logCache.error = ''; } })
        .catch((error) => { logCache.error = String(error); }).finally(() => { logCache.loading = false; logCache.at = Date.now(); redraw(); });
    }
    return logCache;
  }
  // WHY isolated: the Activity lane's per-entity view mounts here when it lands on main; this is the same scope (the entity and
  // below, newest first) filtered from /api/v1/activity until then.
  function logPanel(node, detail, ctx) {
    const h = ctx.h;
    const entry = logData(node.path, ctx.redraw);
    const events = ((entry.data && entry.data.events) || (ctx.activity && ctx.activity.events) || []).filter((e) => inScope(e, node.path));
    const shown = U.warnOnly ? events.filter((e) => e.severity === 'warn' || e.severity === 'error') : events;
    const faultCodes = new Set(detail.faults.map((f) => f.code));
    const line = (event) => {
      const diagnostic = PROBLEM.has(event.severity);
      const code = (event.args || {}).fault;
      const flash = U.flash && U.flash.tab === 'log' && U.flash.code && String(code) === String(U.flash.code) ? ' d6-flash' : '';
      const link = code && /^Fault/.test(event.code || '') ? ` <a class="link d6-lg" data-d6-jump="diagnostics" data-path="${h.esc(event.path)}" data-code="${h.esc(code)}" tabindex="0">${faultCodes.has(code) || event.path === node.path ? '' : ''}${h.esc(code)} in Diagnostics →</a>` : '';
      const where = event.path && event.path !== node.path ? `<span class="d6-evw" title="${h.esc(event.path)}">${h.esc(event.name || event.path.split('/').pop())}</span>` : '';
      const raw = U.raw ? `<div class="d6-raw mono">${h.esc(event.source || '')} · ${h.esc(event.id)}${event.seq !== null && event.seq !== undefined ? ` · seq ${h.esc(event.seq)}` : ''}${(event.args || {}).kernel ? `\n${h.esc(event.args.kernel)}` : ''}\n${h.esc(JSON.stringify(event.args || {}))}</div>` : '';
      return `<div class="d6-ev${diagnostic ? ` sev-${h.esc(event.severity)}` : ''}${flash}"${diagnostic ? ' data-diagnostic' : ''} data-code="${h.esc(code || '')}"><span class="d6-evt">${h.esc(event.time)}</span>`
        + `<span class="d6-evk">${h.esc(event.kind)}</span><span class="d6-evm">${where}${h.esc(event.message)}${link}</span><span class="d6-evs">${h.esc(event.severity === 'info' ? '' : event.severity)}</span></div>${raw}`;
    };
    return `<div class="d6-ctl"><label class="d6-sw"><input type="checkbox" role="switch" data-d6-raw${U.raw ? ' checked' : ''}><span class="d6-sw-t">Raw</span></label>`
      + `<label class="d6-sw"><input type="checkbox" role="switch" data-d6-warn${U.warnOnly ? ' checked' : ''}><span class="d6-sw-t">Warnings and errors only</span></label>`
      + `<span class="d6-dim">${shown.length} event${shown.length === 1 ? '' : 's'} · this entity and below · newest first</span></div>`
      + (shown.map(line).join('') || `<div class="muted">${entry.data || entry.error ? 'No events for this entity.' : 'loading…'}</div>`);
  }

  // ------------------------------------------------------------------------------------- Utilities ----
  const DANGER = { query: 'reads only', interrupts: 'interrupts what runs', changes: 'changes what the device stores', moves: 'makes hardware move' };
  const DANGER_ORDER = ['query', 'interrupts', 'changes', 'moves'];
  function utilitiesPanel(node, detail, ctx) {
    const h = ctx.h;
    const actions = [...(detail.actions || [])].sort((a, b) => (DANGER_ORDER.indexOf(a.danger) - DANGER_ORDER.indexOf(b.danger)) || a.title.localeCompare(b.title));
    if (!actions.length) return '<div class="muted">This entity registers no utilities.</div>';
    const now = ctx.state && ctx.state.clock ? ctx.state.clock.now : null;
    const rows = actions.map((action) => {
      const runnable = action.state === 'ready' || action.state === 'unknown';
      const run = action.last_run;
      const when = run && run.ended !== null && run.ended !== undefined && now !== null ? `${h.ago(Math.max(0, now - run.ended))} ago` : run && run.active ? 'running now' : '';
      const last = run ? `${h.esc(run.state)}${run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${h.esc(run.exit_code)}` : ''}${when ? ` · ${h.esc(when)}` : ''}` : 'not run from here';
      const reason = action.state === 'ready' ? 'ready' : action.reason || action.state;
      return `<div class="d6-ur"><span class="d6-un"><b>${h.esc(action.title)}</b><small>${h.esc(action.description || '')}</small></span>`
        + `<span class="d6-ud">${h.esc(DANGER[action.danger] || action.danger)}</span><span class="d6-us" title="${h.esc(reason)}">${h.esc(reason)}</span><span class="d6-ul">${last}</span>`
        + `<button class="btn d6-run" data-util="${h.esc(action.name)}"${runnable ? '' : ' disabled'} title="every click is a dry run first">${h.esc(action.title.replace(/…$/, ''))}…</button></div>`;
    }).join('');
    return `<div class="d6-dim d6-ulead">${actions.length} utilit${actions.length === 1 ? 'y' : 'ies'} · ${h.esc(actions[0].owner || 'its plugin')} runs them · a click shows exactly what would run first (the Utilities menu in the header does the same)</div>${rows}`;
  }

  // -------------------------------------------------------------------------------- driver tabs ----
  function parseTsv(text) {
    const lines = String(text || '').split('\n').filter((line) => line.trim());
    if (!lines.length) return { head: [], rows: [] };
    return { head: lines[0].split('\t'), rows: lines.slice(1).map((line) => line.split('\t')) };
  }
  function cell(head, value) {
    if (/_bytes$/.test(head) && /^\d+$/.test(value)) return bytes(Number(value));
    if (/_percent$/.test(head) && value !== '') return `${value} %`;
    return value;
  }
  const titleOf = (key) => ({ top_cpu: 'Busiest by CPU', top_memory: 'Largest by memory' }[key] || key.replace(/_/g, ' '));
  function driverPanel(node, detail, ctx, tab) {
    const h = ctx.h;
    const check = detail.checks.find((c) => c.name === tab.status || c.name.endsWith(`: ${tab.status}`)); // a status reads "<who>: <what>"
    if (!check) return `<div class="muted">${h.esc(tab.title)}: the driver has not reported ${h.esc(tab.status || 'it')} yet.</div>`;
    const values = Object.fromEntries((check.values || []).map((v) => [v.key, v.value]));
    const tables = (tab.values || []).filter((key) => values[key]).map((key) => {
      const { head, rows } = parseTsv(values[key]);
      const id = `${node.path}|${tab.id}|${key}`;
      const sort = U.sort.get(id) || { col: -1, desc: false };
      const numeric = head.map((_, col) => rows.length > 0 && rows.every((r) => r[col] !== '' && !Number.isNaN(Number(r[col]))));
      const ordered = sort.col < 0 ? rows : [...rows].sort((a, b) => {
        const x = numeric[sort.col] ? Number(a[sort.col]) : String(a[sort.col]).toLowerCase();
        const y = numeric[sort.col] ? Number(b[sort.col]) : String(b[sort.col]).toLowerCase();
        return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1);
      });
      return `<section class="d6-block"><h3 class="d6-bh"><span>${h.esc(titleOf(key))}</span></h3><table class="d6-tbl"><thead><tr>${head.map((name, col) => `<th class="${numeric[col] ? 'num' : ''}" aria-sort="${sort.col === col ? (sort.desc ? 'descending' : 'ascending') : 'none'}"><button data-d6-sort="${h.esc(id)}" data-col="${col}">${h.esc(name.replace(/_(bytes|percent)$/, ''))}${sort.col === col ? (sort.desc ? ' ▾' : ' ▴') : ''}</button></th>`).join('')}</tr></thead>`
        + `<tbody>${ordered.map((r) => `<tr>${r.map((v, col) => `<td class="${numeric[col] ? 'num mono' : ''}">${h.esc(cell(head[col], v))}</td>`).join('')}</tr>`).join('')}</tbody></table></section>`;
    }).join('');
    return `<div class="d6-dim">${h.esc(check.message || tab.description || '')} · ${h.ago(check.age_s)} ago</div>${tables || '<div class="muted">Nothing to show yet.</div>'}`;
  }

  // ------------------------------------------------------------------------------------ the page ----
  const CSS = `
.d6-top { position: sticky; top: 0; z-index: 6; background: var(--io-surface); }
.d6-top .d-head { border-bottom: 0; padding-bottom: 8px; }
.d6-name { min-width: 0; overflow-wrap: anywhere; }
.d6-model { font: 400 13px var(--io-font-ui); color: var(--io-muted); }
.d6-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 6px; font-size: 12.5px; }
.d6-badge-c { display: inline-flex; align-items: center; padding: 1px 9px; border: 1px solid var(--io-border); border-radius: var(--io-radius-pill); font: 600 11.5px var(--io-font-ui); color: var(--io-text-strong); background: var(--io-control); }
.d6-heard { color: var(--io-muted); font-variant-numeric: tabular-nums; }
.d6-hw { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 16px; margin-top: 6px; font-size: 12px; color: var(--io-muted); }
.d6-k { color: var(--io-faint); }
.d6-more { margin-left: auto; background: transparent; border: 0; padding: 0 2px; color: var(--io-accent-text); cursor: pointer; font-size: 12px; }
.d6-more:hover { text-decoration: underline; }
.d6-hw-more { margin-top: 8px; max-width: var(--io-detail-max); }
.d6-tabs { display: flex; gap: 2px; padding: 0 12px; border-bottom: 1px solid var(--io-border); overflow-x: auto; scrollbar-width: none; }
.d6-tab { position: relative; display: inline-flex; align-items: center; gap: 6px; padding: 8px 12px 9px; background: transparent; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; color: var(--io-muted); cursor: pointer; font-size: 13px; white-space: nowrap; }
.d6-tab:hover { color: var(--io-text-strong); }
.d6-tab.on { color: var(--io-text-strong); border-bottom-color: var(--io-text-strong); font-weight: 600; }
.d6-tab:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: -2px; border-radius: var(--io-radius-sm); }
.d6-bd { display: inline-flex; align-items: center; gap: 3px; font-size: 11px; font-weight: 600; font-variant-numeric: tabular-nums; }
.d6-bd .shape { width: 12px; height: 12px; }
.d6-bd.plain { padding: 0 6px; border-radius: var(--io-radius-pill); background: var(--io-control); color: var(--io-muted); }
.d6-panel { padding: 12px 18px 28px; max-width: 1100px; min-width: 0; }
.d6-note { display: flex; align-items: center; gap: 6px; margin: 0 0 10px; font-size: 12px; color: var(--io-muted); }
.d6-note.paused { padding: 6px 10px; border: 1px dashed var(--io-border); border-radius: var(--io-radius-sm); color: var(--io-text-strong); background: var(--io-control); }
.d6-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--io-text-strong); animation: d6-pulse 1.4s ease-in-out infinite; }
@keyframes d6-pulse { 50% { opacity: .25; } }
.d6-staleq { color: var(--io-faint); font-size: 11.5px; font-style: italic; }
.d6-block { margin: 0 0 14px; min-width: 0; }
.d6-bh, .d6-gh { display: flex; align-items: center; gap: 8px; margin: 10px 0 4px; font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: var(--io-muted); font-weight: 600; }
.d6-bh .right, .d6-gh .right { margin-left: auto; text-transform: none; letter-spacing: 0; font-weight: 400; color: var(--io-faint); font-size: 11.5px; }
.d6-gh.plain-h { margin-top: 14px; }
.d6-fold { display: inline-flex; align-items: center; gap: 4px; background: transparent; border: 0; padding: 2px 6px 2px 0; color: inherit; font: inherit; letter-spacing: inherit; text-transform: inherit; cursor: pointer; border-radius: var(--io-radius-sm); }
.d6-fold:hover { color: var(--io-text); }
.d6-fold[aria-expanded="true"] .sec-chev { transform: rotate(90deg); }
.d6-tiles { position: relative; display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--d6-tile-w, 240px), 1fr)); gap: 10px; padding-bottom: 6px; }
.d6-tile { margin: 0; border: 1px solid var(--io-border); border-radius: var(--io-radius); background: var(--io-surface-alt); overflow: hidden; min-width: 0; }
.d6-tile figcaption { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 10px; font-size: 12px; }
.d6-tile.off { opacity: .7; }
.d6-pic { display: block; width: 100%; padding: 0; border: 0; background: var(--io-field); cursor: zoom-in; aspect-ratio: 4 / 3; }
.d6-pic[disabled] { cursor: default; }
.d6-pic img { display: block; width: 100%; height: 100%; object-fit: contain; image-rendering: auto; }
.d6-ph { display: grid; place-items: center; height: 100%; min-height: 90px; color: var(--io-faint); font-size: 12px; }
.d6-dim { color: var(--io-faint); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.d6-sw { display: inline-flex; align-items: center; gap: 7px; cursor: pointer; font-size: 12px; color: var(--io-text); }
.d6-sw input { appearance: none; position: relative; width: 28px; height: 16px; margin: 0; border-radius: 999px; background: var(--io-border); cursor: pointer; transition: background .12s; flex: none; }
.d6-sw input::after { content: ""; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; border-radius: 50%; background: var(--io-surface); transition: transform .12s; }
.d6-sw input:checked { background: var(--io-accent); }
.d6-sw input:checked::after { transform: translateX(12px); }
.d6-sw input:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 2px; }
.d6-sw-t { font-weight: 500; }
.d6-sh, .d6-sr, .d6-ch-h, .d6-cr { display: grid; gap: 4px 12px; align-items: center; }
.d6-sh, .d6-sr { grid-template-columns: var(--d6-name-w, 190px) minmax(110px, 1.1fr) minmax(120px, 1.5fr); }
.d6-ch-h, .d6-cr { grid-template-columns: var(--d6-name-w, 190px) minmax(0, 1fr) 54px; }
.d6-sh, .d6-ch-h { padding: 2px 4px 4px; color: var(--io-faint); font-size: 11px; border-bottom: 1px solid var(--io-border-soft); }
.d6-sh > :first-child, .d6-ch-h > :first-child { position: relative; }
.d6-grip { position: absolute; top: -2px; bottom: -2px; right: -8px; width: 9px; cursor: col-resize; touch-action: none; }
.d6-grip::after { content: ""; position: absolute; left: 4px; top: 2px; bottom: 2px; width: 1px; background: var(--io-border); }
.d6-grip:hover::after, .d6-grip:focus-visible::after { width: 3px; left: 3px; background: var(--io-accent); }
.d6-tiles .d6-grip { top: auto; bottom: -4px; right: 0; width: 22px; height: 12px; cursor: ew-resize; }
.d6-tiles .d6-grip::after { left: 2px; right: 2px; top: 5px; bottom: auto; width: auto; height: 2px; border-radius: 2px; }
.d6-sr, .d6-cr { padding: 5px 4px; border-bottom: 1px solid var(--io-border-soft); font-size: 12.5px; }
.d6-sn { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-muted); }
.d6-sv { font: 12px var(--io-font-mono); color: var(--io-text-strong); font-variant-numeric: tabular-nums; min-width: 0; overflow-wrap: anywhere; display: inline-flex; align-items: center; gap: 4px; }
.d6-sv.txt { font-family: var(--io-font-ui); }
.d6-sv .shape { width: 12px; height: 12px; }
.d6-ss { min-width: 0; }
.d6-sp { display: block; width: 100%; height: 20px; overflow: visible; }
.d6-sp .ln { fill: none; stroke: var(--io-muted); stroke-width: 1.25; stroke-linejoin: round; }
.d6-sp .dot { fill: none; stroke: var(--io-text-strong); stroke-width: 4; stroke-linecap: round; }
.d6-sp .lim { fill: none; stroke: var(--io-border); stroke-width: 1; stroke-dasharray: 3 3; }
.d6-nospark { color: var(--io-faint); font-size: 11px; }
.d6-ctl { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 14px; margin: 2px 0 10px; }
.d6-search { flex: 1 1 200px; max-width: 320px; padding: 5px 9px; font: inherit; color: var(--io-text-strong); background: var(--io-field); border: 1px solid var(--io-border); border-radius: var(--io-radius-sm); }
.d6-copy { margin-left: auto; }
.d6-cv { min-width: 0; overflow-wrap: anywhere; }
.d6-cv .mono { font-size: 12px; color: var(--io-text-strong); }
.d6-cr.differs { background: color-mix(in srgb, var(--io-text) 5%, transparent); box-shadow: inset 2px 0 0 var(--io-text-strong); }
.d6-diff { display: inline-block; margin-right: 6px; font-weight: 700; color: var(--io-accent-text); }
.d6-exp { margin-top: 2px; font-size: 11.5px; color: var(--io-muted); }
.d6-exp b { color: var(--io-text-strong); font-family: var(--io-font-mono); font-weight: 600; }
.d6-ca { color: var(--io-faint); font-size: 11px; text-align: right; font-variant-numeric: tabular-nums; }
.d6-ev { display: grid; grid-template-columns: 64px 92px minmax(0, 1fr) 44px; gap: 8px; align-items: baseline; padding: 4px 4px; border-bottom: 1px solid var(--io-border-soft); font-size: 12.5px; }
.d6-evt { font: 11.5px var(--io-font-mono); color: var(--io-faint); }
.d6-evk { color: var(--io-muted); font-size: 11.5px; }
.d6-evw { margin-right: 6px; color: var(--io-faint); }
.d6-evw::after { content: " ›"; }
.d6-evs { color: var(--io-faint); font-size: 11px; text-align: right; }
.d6-raw { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0 0 2px 164px; padding: 4px 8px; font-size: 11px; color: var(--io-muted); background: var(--io-surface-alt); border-radius: var(--io-radius-sm); }
.d6-flash { animation: d6-flash 2.4s ease-out; }
@keyframes d6-flash { 0%, 55% { background: var(--io-accent-soft); outline: 1px solid var(--io-accent-line); } 100% { background: transparent; outline-color: transparent; } }
.d6-ur { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1.2fr) minmax(0, 1.3fr) minmax(0, 1.2fr) auto; gap: 6px 12px; align-items: center; padding: 8px 4px; border-bottom: 1px solid var(--io-border-soft); font-size: 12.5px; }
.d6-un { display: grid; min-width: 0; }
.d6-un small { color: var(--io-faint); font-size: 11.5px; }
.d6-ud, .d6-us, .d6-ul { min-width: 0; overflow-wrap: anywhere; color: var(--io-muted); font-size: 12px; }
.d6-ulead { margin-bottom: 6px; }
.d6-tbl { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 12.5px; }
.d6-tbl th.num { width: 112px; }
.d6-tbl th, .d6-tbl td { padding: 4px 8px; text-align: left; border-bottom: 1px solid var(--io-border-soft); }
.d6-tbl th.num, .d6-tbl td.num { text-align: right; }
.d6-tbl th button { background: transparent; border: 0; padding: 0; font: 600 11px var(--io-font-ui); letter-spacing: .4px; text-transform: uppercase; color: var(--io-muted); cursor: pointer; }
.d6-tbl th button:hover { color: var(--io-text-strong); }
.d6-lb { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; background: var(--io-overlay); cursor: zoom-out; }
.d6-lb figure { margin: 0; max-width: 92vw; max-height: 92vh; background: var(--io-surface); border: 1px solid var(--io-border); border-radius: var(--io-radius); padding: 10px; box-shadow: var(--io-shadow-pop); }
.d6-lb img { display: block; max-width: 88vw; max-height: 82vh; min-width: 480px; object-fit: contain; }
.d6-lb figcaption { padding-top: 6px; font-size: 12px; color: var(--io-muted); }
.d6-why { margin: 2px 0 0; font-size: 12.5px; color: var(--io-muted); }
.d6-why b { color: var(--io-text-strong); font-weight: 600; }
.d6-bar { margin: 8px 0 4px; }
.d6-hb { display: flex; gap: 1px; height: 12px; }
.d6-hb i { flex: 1 1 0; min-width: 1px; border-radius: 1px; background: var(--io-guide); }
.d6-hb i.none { background: transparent; box-shadow: inset 0 -2px 0 var(--io-border); }
.d6-hb i.warn { background: var(--io-warn); } .d6-hb i.error { background: var(--io-error); } .d6-hb i.stale { background: var(--io-stale); } .d6-hb i.unknown { background: var(--io-unknown); }
.d6-axis { display: flex; justify-content: space-between; gap: 10px; margin-top: 3px; font-size: 11px; color: var(--io-faint); font-variant-numeric: tabular-nums; }
.d6-axis span:nth-child(2) { text-align: center; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.d6-h { cursor: default; }
.sec.d6-diag .sec-b { padding-top: 0; }
.d6-tab-t { font-size: 12.5px; }
.d6-hd, .d6-rl { display: grid; grid-template-columns: 12px 64px minmax(0, 36%) minmax(0, 1fr); gap: 8px; }
.d6-hd { padding: 2px 4px 4px; color: var(--io-faint); font-size: 11px; border-bottom: 1px solid var(--io-border-soft); }
.d6-r { border-bottom: 1px solid var(--io-border-soft); }
.d6-rl { align-items: baseline; padding: 5px 4px; border-radius: var(--io-radius-sm); cursor: pointer; }
.d6-rl:hover { background: var(--io-hover); }
.d6-rl:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.d6-chev { width: 12px; height: 12px; color: var(--io-faint); align-self: start; margin-top: 2px; transition: transform .12s; }
.d6-r.open .d6-chev { transform: rotate(90deg); }
.d6-lv { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.d6-lv .shape { width: 12px; height: 12px; }
.d6-lv .lvword { margin: 0; }
.d6-okw { color: var(--io-faint); font-size: 11px; letter-spacing: .3px; text-transform: uppercase; }
.d6-rn { min-width: 0; overflow-wrap: anywhere; color: var(--io-muted); }
.d6-r.bad .d6-rn { color: var(--io-text-strong); font-weight: 600; }
.d6-pre { color: var(--io-faint); font-weight: 400; }
.d6-rm { min-width: 0; overflow-wrap: anywhere; color: var(--io-muted); }
.d6-r.bad .d6-rm { color: var(--io-text); }
.d6-fx { display: inline-block; margin-left: 4px; padding: 0 6px; border-radius: var(--io-radius-sm); background: var(--io-control); color: var(--io-text-strong); font-size: 12px; }
.d6-code { color: var(--io-faint); font-size: 11px; }
.d6-lg { font-size: 11.5px; margin-left: 6px; white-space: nowrap; }
.d6-tag { margin-left: 6px; padding: 0 6px; border: 1px solid var(--io-border); border-radius: var(--io-radius-pill); color: var(--io-muted); font-size: 10.5px; white-space: nowrap; }
.d6-x { padding: 2px 4px 10px 26px; display: grid; gap: 8px; min-width: 0; }
.d6-x .kv { grid-template-columns: 120px 1fr; }
.d6-v { white-space: pre-line; }
.d6-meta { color: var(--io-faint); font-size: 11px; overflow-wrap: anywhere; }
.d6-ch { display: flex; align-items: center; gap: 8px; height: var(--io-row-h); padding: 0 6px; margin: 0 -6px; border-radius: var(--io-radius-sm); cursor: pointer; white-space: nowrap; }
.d6-ch:hover { background: var(--io-hover); }
.d6-ch .ic { color: var(--io-icon); flex: none; }
.d6-ch.off { color: var(--io-off); }
.d6-cn { flex: none; max-width: 45%; overflow: hidden; text-overflow: ellipsis; color: var(--io-text); }
.d6-cm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--io-muted); font-size: 12px; }
.d6-mini { flex: none; width: 120px; height: 8px; gap: 0; }
.d6-nobar { color: var(--io-faint); font-size: 11px; height: auto; text-align: right; }
.d6-cs { flex: none; width: 34px; display: inline-flex; justify-content: flex-end; gap: 2px; }
.d6-hint .shape { width: 10px; height: 10px; opacity: .8; }
`;
  function injectCss() {
    if (typeof document === 'undefined' || document.querySelector('style[data-layout="d6"]')) return;
    const style = document.createElement('style');
    style.dataset.layout = 'd6';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function lightboxHtml(node, h) {
    if (!U.lightbox || U.lightbox.path !== node.path) return '';
    const stream = (Live.streams || []).find((s) => s.name === U.lightbox.stream);
    const title = stream ? stream.title : U.lightbox.stream;
    const src = `/api/v1/frames/stream?path=${enc(node.path)}&stream=${enc(U.lightbox.stream)}&fps=15`;
    return `<div class="d6-lb" data-d6-lb role="dialog" aria-label="${h.esc(title)}"><figure><img src="${src}" alt="${h.esc(title)}">`
      + `<figcaption>${h.esc(title)}${stream && stream.width ? ` · ${stream.width}×${stream.height}` : ''}${stream && stream.fps ? ` · ${stream.fps} fps` : ''} · click or Esc to close</figcaption></figure></div>`;
  }

  function panelHtml(active, tabs, node, detail, ctx) {
    switch (active) {
      case 'diagnostics': return diagnosticsPanel(node, detail, ctx);
      case 'state': return statePanel(node, detail, ctx);
      case 'config': return configPanel(node, detail, ctx);
      case 'log': return logPanel(node, detail, ctx);
      case 'utilities': return utilitiesPanel(node, detail, ctx);
      default: return driverPanel(node, detail, ctx, tabs.find((tab) => tab.id === active).driver);
    }
  }

  function afterDraw(container) {
    if (!U.flash) return;
    if (Date.now() > U.flash.until) { U.flash = null; return; }
    if (!U.flash.scrolled) {
      const target = container.querySelector('.d6-flash');
      if (target) { U.flash.scrolled = true; target.scrollIntoView({ block: 'center' }); }
      setTimeout(() => { if (U.flash && Date.now() > U.flash.until - 50) { U.flash = null; if (last) last.ctx.redraw(); } }, 2600);
    }
  }

  function render(container, detail, ctx) {
    injectCss();
    wire(container);
    last = { container, detail, ctx };
    const p = ctx.parts;
    const h = ctx.h;
    if (ctx.group) { // a structure's grouping row: its members, no tabs
      stopLive();
      ctx.draw(`${p.head()}<div class="d-body" data-d6>${plain('members', `Members · ${ctx.counts.members}`, p.members())}${fold(ctx, 'links', 'Link health', p.links(), 'the computer diagnoses its own wires')}</div>`);
      return;
    }
    const node = ctx.node;
    if (!detail) {
      stopLive();
      ctx.draw(`${p.head()}${p.banners()}<div class="d-body" data-d6><div class="muted">loading…</div></div>`);
      return;
    }
    if (Heard.source !== detail) { // a new answer from the host: its age is the freshest we know, unless the live poll has a newer one
      Heard.source = detail;
      const age = detail.heard && typeof detail.heard.age_s === 'number' ? detail.heard.age_s : node.age_s;
      if (!(Live.path === node.path && Live.heard)) {
        if (typeof age === 'number') setHeard(age); else Heard.base = null;
      }
    }
    const tabs = tabsFor(detail);
    const active = activeTab(ctx, node, tabs);
    if (U.forced && U.forced.path === node.path) U.forced = null;
    if (!U.seen.has(node.path)) U.seen.set(node.path, ctx.state && ctx.state.clock ? ctx.state.clock.now : 0);
    if (active === 'log' && ctx.state && ctx.state.clock) U.looked.set(node.path, ctx.state.clock.now);
    // the live loops: only while the State tab shows (docs/state-config-v1.md section 7)
    if (active === 'state') {
      Live.rendered = performance.now();
      if (Live.path !== node.path) startLive(node.path, Boolean(detail.watch && detail.watch.supported));
    } else if (Live.path) stopLive();
    const row = ctx.row;
    const banners = p.banners();
    const style = `--d6-name-w:${U.nameW}px;--d6-tile-w:${U.tileW}px`;
    ctx.draw(`<div class="d6-top">${headerHtml(node, detail, ctx, row)}${banners ? `<div class="d6-banners">${banners}</div>` : ''}${tabBar(ctx, node, detail, tabs, active)}</div>`
      + `<div class="d6-panel" role="tabpanel" id="d6-panel" aria-labelledby="d6-tab-${h.esc(active)}" data-d6 data-d6-panel="${h.esc(active)}" style="${style}">${panelHtml(active, tabs, node, detail, ctx)}</div>${lightboxHtml(node, h)}`);
    afterDraw(container);
  }

  // ---------------------------------------------------------------------------------- the clicks ----
  function chooseTab(ctx, node, tabs, id) {
    if (!tabs.some((tab) => tab.id === id)) return;
    write(kindKey(node), id);
    ctx.openPage(id);
  }
  function stepTab(delta) {
    if (!last || !last.container.querySelector('[data-d6-panel]') || !last.detail) return false;
    const tabs = tabsFor(last.detail);
    const at = tabs.findIndex((tab) => tab.id === activeTab(last.ctx, last.ctx.node, tabs));
    chooseTab(last.ctx, last.ctx.node, tabs, tabs[(at + delta + tabs.length) % tabs.length].id);
    return true;
  }
  function jump(ctx, node, tab, path, code) {
    U.flash = { tab, code, until: Date.now() + 2500, scrolled: false };
    if (path && path !== node.path && ctx.byPath.has(path)) {
      U.forced = { path, tab };
      const link = document.createElement('a'); // the page's own selection (data-go): history entry, node load and all
      link.dataset.go = path;
      last.container.appendChild(link);
      link.click();
      link.remove();
      return;
    }
    chooseTab(ctx, node, tabsFor(last.detail), tab);
  }

  function wire(container) {
    if (wired.has(container)) return;
    wired.add(container);
    const live = () => last && container.querySelector('[data-d6-panel]') ? last : null;
    container.addEventListener('click', (event) => {
      const target = event.target;
      if (!(target instanceof Element) || target.closest('.ent-tools, .u-panel, [data-util]') || !live()) return;
      const { ctx, detail } = live();
      const node = ctx.node;
      const tab = target.closest('[data-d6-tab]');
      if (tab) { chooseTab(ctx, node, tabsFor(detail), tab.dataset.d6Tab); return; }
      if (target.closest('[data-d6-lb]')) { U.lightbox = null; ctx.redraw(); return; }
      const enlarge = target.closest('[data-d6-enlarge]');
      if (enlarge) { U.lightbox = { path: node.path, stream: enlarge.dataset.d6Enlarge }; ctx.redraw(); return; }
      const more = target.closest('[data-d6-more]');
      if (more) { if (U.more.has(node.path)) U.more.delete(node.path); else U.more.add(node.path); ctx.redraw(); return; }
      const expand = target.closest('[data-d6-expand]');
      if (expand && !target.closest('a')) {
        const key = `${node.path}\u0001${expand.dataset.d6Expand}`;
        if (U.expanded.has(key)) U.expanded.delete(key); else U.expanded.add(key);
        ctx.redraw();
        return;
      }
      const toJump = target.closest('[data-d6-jump]');
      if (toJump) { jump(ctx, node, toJump.dataset.d6Jump, toJump.dataset.path || node.path, toJump.dataset.code); event.stopPropagation(); return; }
      const folder = target.closest('[data-d6-fold]');
      if (folder) {
        const id = folder.dataset.d6Fold;
        if (U.folded.has(id)) U.folded.delete(id); else U.folded.add(id);
        write(KEYS.folded, JSON.stringify([...U.folded]));
        ctx.redraw();
        return;
      }
      const sortBtn = target.closest('[data-d6-sort]');
      if (sortBtn) {
        const id = sortBtn.dataset.d6Sort;
        const col = Number(sortBtn.dataset.col);
        const now = U.sort.get(id) || { col: -1, desc: false };
        U.sort.set(id, now.col === col ? { col, desc: !now.desc } : { col, desc: true });
        ctx.redraw();
        return;
      }
      if (target.closest('[data-d6-copy]')) {
        const text = configText(configRows(detail));
        const done = () => { U.copied = Date.now(); ctx.redraw(); setTimeout(() => ctx.redraw(), 2100); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, done);
        else done();
      }
    });
    container.addEventListener('change', (event) => {
      const target = event.target;
      if (!live() || !(target instanceof Element)) return;
      const { ctx, detail } = live();
      const stream = target.closest('[data-d6-stream]');
      if (stream) {
        const key = `${ctx.node.path}\u0001${stream.dataset.d6Stream}`;
        if (stream.checked) U.off.delete(key); else U.off.add(key);
        write(KEYS.off, JSON.stringify([...U.off]));
        ctx.redraw();
      } else if (target.matches('[data-d6-raw]')) { U.raw = target.checked; ctx.redraw(); }
      else if (target.matches('[data-d6-warn]')) { U.warnOnly = target.checked; ctx.redraw(); }
      void detail;
    });
    container.addEventListener('input', (event) => {
      const target = event.target;
      if (!live() || !(target instanceof Element) || !target.matches('[data-d6-search]')) return;
      U.query.set(live().ctx.node.path, target.value);
      live().ctx.redraw();
    });
    container.addEventListener('keydown', (event) => {
      const target = event.target;
      if (!live() || !(target instanceof Element)) return;
      const { ctx, detail } = live();
      if (event.key === 'Escape' && U.lightbox) { U.lightbox = null; ctx.redraw(); event.stopPropagation(); return; }
      const expand = target.closest('[data-d6-expand]');
      if (expand && (event.key === 'Enter' || event.key === ' ')) { expand.click(); event.preventDefault(); return; }
      if (target.closest('[data-d6-tabs]')) {
        const tabs = tabsFor(detail);
        const at = tabs.findIndex((tab) => tab.id === activeTab(ctx, ctx.node, tabs));
        let to = null;
        if (/^[1-9]$/.test(event.key) && tabs[Number(event.key) - 1]) to = Number(event.key) - 1;
        else if (event.key === 'ArrowRight') to = (at + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') to = (at - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') to = 0;
        else if (event.key === 'End') to = tabs.length - 1;
        if (to !== null) {
          chooseTab(ctx, ctx.node, tabs, tabs[to].id);
          event.preventDefault();
          setTimeout(() => { const el = container.querySelector(`[data-d6-tab="${tabs[to].id}"]`); if (el) el.focus(); }, 0);
        }
      }
      const gripEl = target.closest('[data-d6-grip]');
      if (gripEl && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
        const step = event.key === 'ArrowRight' ? 16 : -16;
        if (gripEl.dataset.d6Grip === 'name') { U.nameW = Math.max(90, Math.min(420, U.nameW + step)); write(KEYS.name, String(U.nameW)); }
        else { U.tileW = Math.max(150, Math.min(640, U.tileW + step)); write(KEYS.tile, String(U.tileW)); }
        ctx.redraw();
        event.preventDefault();
      }
    });
    // the draggable boundaries: the name column (State and Configuration) and the image tiles' width; both remembered
    let drag = null;
    container.addEventListener('pointerdown', (event) => {
      const el = event.target instanceof Element && event.target.closest('[data-d6-grip]');
      if (!el || event.button !== 0) return;
      const panel = container.querySelector('[data-d6-panel]');
      const first = el.dataset.d6Grip === 'name' ? el.parentElement : container.querySelector('.d6-tile');
      drag = { kind: el.dataset.d6Grip, id: event.pointerId, left: first.getBoundingClientRect().left, panel, el };
      el.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    container.addEventListener('pointermove', (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      if (drag.kind === 'name') U.nameW = Math.round(Math.max(90, Math.min(420, event.clientX - drag.left)));
      else U.tileW = Math.round(Math.max(150, Math.min(640, event.clientX - drag.left)));
      drag.panel.style.setProperty(drag.kind === 'name' ? '--d6-name-w' : '--d6-tile-w', `${drag.kind === 'name' ? U.nameW : U.tileW}px`);
    });
    const end = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      if (drag.kind === 'name') write(KEYS.name, String(U.nameW)); else write(KEYS.tile, String(U.tileW));
      drag = null;
    };
    container.addEventListener('pointerup', end);
    container.addEventListener('pointercancel', end);
    container.addEventListener('dblclick', (event) => {
      const el = event.target instanceof Element && event.target.closest('[data-d6-grip]');
      if (!el || !live()) return;
      if (el.dataset.d6Grip === 'name') { U.nameW = 190; write(KEYS.name, '190'); } else { U.tileW = 240; write(KEYS.tile, '240'); }
      live().ctx.redraw();
    });
  }
  if (typeof document !== 'undefined') {
    // Ctrl+PageUp / Ctrl+PageDown, as browsers and terminals switch tabs (a browser may keep the keys for its own tabs)
    document.addEventListener('keydown', (event) => {
      if (!(event.ctrlKey && !event.altKey && !event.metaKey && (event.key === 'PageUp' || event.key === 'PageDown'))) return;
      const typing = event.target instanceof Element && event.target.closest('input, textarea, select, .xterm');
      if (typing || !last || !document.querySelector('[data-d6-panel]')) return;
      if (stepTab(event.key === 'PageDown' ? 1 : -1)) event.preventDefault();
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && U.lightbox && last) { U.lightbox = null; last.ctx.redraw(); } });
    document.addEventListener('visibilitychange', () => { if (document.hidden) stopLive(); });
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d6 = {
    name: 'Entity page',
    description: 'A header that is always visible (name, model, connection badge, the ticking "heard 2 s ago", hardware line) and tabs: Diagnostics (health only), State (live numbers as one-line rows with a sparkline, images as stream tiles), Configuration (read-only, searchable, expected vs read), Log (this entity and below, with Raw), Utilities, and a driver\'s own tabs (the Computer\'s Processes).',
    order: 0,
    render,
    // for tests and the next lane
    internals: { Live, U, Heard, tabsFor },
  };
})(typeof window !== 'undefined' ? window : globalThis);
