// Detail layout d6 · Entity page (docs/state-config-v1.md; Zach, 2026-10-24, "My feedback on the entity page").
// Like realsense-viewer's device panel and moteus tview, plus the diagnostics view: a header that is always visible (the
// name and model, the connection badge, the ticking "heard 2 s ago", the Utilities menu the shell draws, the hardware line),
// then TABS, not one long scroll: Diagnostics (health only), State (the entity's live values: numbers as one-line rows with a
// sparkline, images as stream tiles), Configuration (read-only, the same for every driver), Log (this entity and below, with
// its Raw channel), Utilities (every action with its danger and last run), then a driver's own tabs (the Computer's Processes).
// State and Diagnostics never show each other's field: a value is never drawn as health, health never as a number.
//
// The live data: while State shows, POST /api/v1/watch (every 3 s) keeps its query watch alive. One /values bootstrap loads
// history, then the shell's /live SSE feed supplies points; /values polling is a disconnected fallback. Leaving stops it; the host stops the
// plugin's watch about 10 s later. Images are <img> elements of the MJPEG stream (/api/v1/frames/stream).
// The tab is the page's `page` drill (?page=<id>: back / forward work), remembered per entity kind.
// The rows of Diagnostics, State and Configuration share ONE row model (d6-rows.js, window.IOEntityRows): dot-notation names are
// folders, A-Z or manual order, starred rows first, all saved per entity kind; the Configuration tab edits live (section 8).
// The Log tab embeds the Activity log (activity/log.js, IOActivityLog.create({scope, compact})) scoped to the entity.
// WHY the Diagnostics rendering is d1's, copied: a parallel lane designs five variants of that section (d1a-d1e); diagnosticsPanel()
// is the one function to replace when its winner lands. Its history is NOT a copy: every health bar here is detail/health.js.
'use strict';
(function (root) {
  const usbText = (text) => (root.IOUsb ? root.IOUsb.label(text) : String(text ?? '')); // a USB link is named by web/usb.js, never "480 Mb/s"
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
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
    more: new Set(),                     // entity paths whose hardware line is opened
    query: new Map(),                    // entity path -> the Configuration search text
    raw: false, warnOnly: false,         // the Log's Raw channel and filter
    looked: new Map(), seen: new Map(),  // entity path -> host time the Log was last shown / first drawn (the Log badge)
    sort: new Map(),                     // table key -> {col, desc}
    flash: null, forced: null, lightbox: null, copied: 0,
  };
  const Live = { path: null, cursor: 0, rings: new Map(), groups: [], streams: [], watch: null, heard: null, loaded: false, none: false,
    error: '', inflight: false, rendered: 0, beat: null, poll: null, now: 0 };
  const Heard = { base: null, at: 0, source: null, text: null, path: null, stale: '' };
  let last = null;
  const wired = new WeakSet();
  const ER = () => root.IOEntityRows;
  const CONFIG_ACTIONS = new Set(['set_config', 'persist_config', 'reset_config']); // driven by the Configuration tab's edit session

  // ---------------------------------------------------------------------------------- formatting ----
  const num = (v) => (typeof v === 'boolean' ? String(v) : Number.isInteger(v) ? String(v) : String(+Number(v).toPrecision(4)));
  const rankOf = (level) => (level in RANK ? RANK[level] : -1);
  const valueOf = (v) => (typeof v === 'boolean' ? (v ? 1 : 0) : typeof v === 'number' && Number.isFinite(v) ? v : null);
  const span = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);
  const bytes = (n) => { const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let i = 0; let v = Number(n); while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; } return `${i ? v.toFixed(v < 10 ? 2 : 1) : v} ${u[i]}`; };
  const enc = encodeURIComponent;
  // ----------------------------------------------------------------------- the ticking last-heard ----
  function setHeard(age) {
    if (typeof age !== 'number') return;
    Heard.base = age; Heard.at = performance.now();
  }
  const heardAge = () => (Heard.base === null ? null : Heard.base + (performance.now() - Heard.at) / 1000);
  function heardText(h) {
    if (Heard.text === null) updateHeard(h);
    return Heard.text;
  }
  function updateHeard(h) {
    const age = heardAge();
    Heard.text = (age === null ? 'never heard' : `heard ${h.ago(Math.max(0, age))} ago`) + Heard.stale;
  }
  if (typeof setInterval !== 'undefined') {
    setInterval(() => {
      if (typeof document === 'undefined' || document.hidden || !last || !last.container.checkVisibility()) return;
      // WHY: only this 1 Hz ticker owns relative-time text; full renders cannot introduce extra, uneven ticks.
      updateHeard(last.ctx.h);
      const text = heardText(last.ctx.h);
      document.querySelectorAll('[data-d6-heard]').forEach((el) => { if (el.textContent !== text) el.textContent = text; });
    }, 1000);
  }

  // ------------------------------------------------------------------------------------ live data ----
  // One history bootstrap, then SSE; a heartbeat exists only while the State pane is visible.
  function stopLive() {
    if (Live.beat) clearInterval(Live.beat);
    if (Live.poll) clearTimeout(Live.poll);
    Live.beat = Live.poll = null;
    if (typeof document !== 'undefined' && document.hidden) for (const image of document.querySelectorAll('.d6-lb img[src]')) {
      // WHY: the enlarged stream is also a decoder; preserve its URL and suspend it with the browser tab.
      image.d6Resume = image.getAttribute('src'); image.removeAttribute('src');
    }
    // WHY: an MJPEG URL keeps decoding after its pane/tab disappears unless its source is removed.
    if (typeof document !== 'undefined') for (const image of document.querySelectorAll('img[data-d6-image]')) {
      image.removeAttribute('src');
      if (StateWindow.observer) StateWindow.observer.unobserve(image);
      StateWindow.nodes.delete(image); StateWindow.visible.delete(`image:${image.dataset.d6Image}`);
    }
    if (Live.abort) Live.abort.abort();
    Live.inflight = false;
    Live.pending = [];
    Live.path = null;
    Live.generation = (Live.generation || 0) + 1;
  }
  const liveAlive = (path) => Live.path === path && !document.hidden && last && last.container.checkVisibility()
    && last.container.querySelector('[data-d6-panel="state"]');

  async function heartbeat(path) {
    const generation = Live.generation;
    try {
      const response = await fetch('/api/v1/watch', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify({ path }) });
      const body = await response.json();
      if (Live.path === path && Live.generation === generation && body.watch) Live.watch = body.watch;
    } catch (_) { /* the poll shows the host is unreachable */ }
  }

  function merge(data) {
    if (data.cursor < Live.cursor) Live.rings.clear(); // the host restarted: its serial numbers began again
    Live.cursor = data.cursor;
    Live.now = data.now;
    // WHY: SSE samples may omit unchanged rows/groups. Merge by identity instead of losing their latest value.
    if (!Live.loaded) Live.groups = data.groups;
    else {
      const groups = new Map(Live.groups.map((group) => [group.name, group]));
      for (const group of data.groups) {
        const previous = groups.get(group.name);
        const rows = new Map((previous ? previous.rows : []).map((row) => [row.key, row]));
        for (const row of group.rows) rows.set(row.key, row);
        groups.set(group.name, { ...group, rows: [...rows.values()] });
      }
      Live.groups = [...groups.values()];
    }
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
    const generation = Live.generation;
    const abort = Live.abort = new AbortController();
    try {
      const response = await fetch(`/api/v1/values?path=${enc(path)}&since=${Live.cursor}`, { cache: 'no-store', signal: abort.signal });
      if (response.status === 404) {
        if (Live.path === path && Live.generation === generation) { Live.none = true; Live.loaded = true; }
      } else {
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
        if (Live.path === path && Live.generation === generation) {
          // WHY: a fallback response already in flight cannot rewind a resumed SSE cursor.
          if (Live.loaded && root.IOClassicLive && root.IOClassicLive.connected && data.cursor < Live.cursor) return;
          Live.none = false; Live.error = ''; merge(data);
          for (const pending of Live.pending || []) if (pending.cursor > Live.cursor) merge(pending);
          Live.pending = [];
        }
      }
    } catch (error) {
      if (Live.path === path && Live.generation === generation) Live.error = String(error.message || error);
    } finally {
      if (Live.generation === generation) Live.inflight = false;
    }
    if (Live.path === path && Live.generation === generation && last) last.ctx.redraw();
  }

  function startLive(path, supported, framePeriod = 0) {
    stopLive();
    Object.assign(Live, { path, cursor: 0, rings: new Map(), groups: [], streams: [], watch: null, heard: null, loaded: false, none: false, error: '', rendered: performance.now() });
    if (supported) {
      heartbeat(path);
      Live.beat = setInterval(() => { if (!liveAlive(path)) { stopLive(); return; } heartbeat(path); }, BEAT_MS);
    }
    const generation = Live.generation;
    const loop = async () => {
      if (Live.generation !== generation) return;
      if (Live.loaded && !liveAlive(path)) { stopLive(); return; }
      if (!Live.loaded || !root.IOClassicLive || !root.IOClassicLive.connected) await pollOnce(path);
      if (Live.path !== path || Live.generation !== generation) return;
      Live.poll = setTimeout(loop, root.IOClassicLive && root.IOClassicLive.connected ? IDLE_MS : Live.none ? 5000 : Live.watch && Live.watch.state === 'running' ? WATCH_MS : IDLE_MS);
    };
    // WHY: retain the watch's original startup cadence so the one bootstrap contains coherent frame/rate metadata.
    // The page says “starting” meanwhile; steady-state samples never wait for this timer.
    Live.poll = setTimeout(loop, framePeriod ? Math.min(1000, framePeriod * 2000) : 0);
  }

  // ------------------------------------------------------------------------------------- the marks ----
  // The health bar and the children's mini bars are the shared discrete state timeline (detail/health.js; the one history
  // renderer, with one palette: Zach, 2026-10-24), the same cells d1 and the d1a-d1e variants draw.
  const windowOf = (ctx) => (ctx.settings && Number(ctx.settings.history_window_s)) || 900;
  function bar(bins, from, binS, kind, cls) {
    return root.IOHealth.html(root.IOHealth.fromBar(bins, from, binS), { kind, cls });
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
    const title = row ? row.label : usbText(node.name);
    const id = node.identity || {};
    const model = [id.manufacturer, id.model].filter(Boolean).join(' ');
    const showModel = model && !title.toLowerCase().includes(String(id.model || '').toLowerCase());
    // the USB words are web/usb.js's (the one mapping: "USB 2.1", "USB 3.2 Gen 1"; the raw speed in the tooltip); a board adds its CAN id
    const usb = detail && detail.badge && detail.badge.usb && root.IOUsb ? root.IOUsb.describe(detail.badge.usb) : { text: '', tip: '' };
    const badgeText = detail && detail.badge ? (detail.badge.kind === 'can' ? detail.badge.text : usb.text) : '';
    const badge = badgeText ? `<span class="d6-badge-c" title="${h.esc([usb.tip, detail.badge.kind === 'can' ? `via ${detail.badge.text.split(' · ')[1] || ''}` : ''].filter(Boolean).join(' · '))}">${h.esc(badgeText)}</span>` : '';
    Heard.stale = node.stale ? ' · stale' : '';
    if (Heard.path !== node.path) { Heard.path = node.path; Heard.text = null; }
    const hw = hardwareItems(node);
    const open = U.more.has(node.path);
    const parent = detail && detail.ancestors && detail.ancestors[detail.ancestors.length - 1];
    const rest = [['hardware version', id.hw_version], ['hardware id', node.hardware_id ? `<span class="mono">${h.esc(node.hardware_id)}</span>` : ''],
      ['path', `<span class="mono">${h.esc(node.path)}</span>`], ['owner', node.owner], ['manufacturer', id.manufacturer],
      [node.edge === 'part_of' ? 'part of' : 'via', parent ? `<a class="link" data-go="${h.esc(parent.path)}" title="${h.esc(parent.name)}">${h.esc(usbText(parent.name))}</a>` : '']]
      .filter(([, v]) => v).map(([k, v]) => [k, /^<(span|a)/.test(String(v)) ? v : h.esc(v)]);
    return `<div class="d-head d6-head"><div class="d-title">${h.icon(node.icon)}<span class="d6-name">${h.esc(title)}</span>${showModel ? `<span class="d6-model">${h.esc(model)}</span>` : ''}${h.pill(node.level)}</div>`
      + `<div class="d6-line">${badge}<span class="d6-heard" data-d6-heard title="when the host last received anything from this entity">${h.esc(heardText(h))}</span></div>`
      + `<div class="d-msg" title="${h.esc(node.message || '')}">${h.esc(usbText(node.message || '—'))}</div>`
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
  function faultOf(check) {
    const values = Object.fromEntries((check.values || []).map((v) => [v.key, v.value]));
    return values.fault_code ? { code: values.fault_code, meaning: values.fault_meaning || '' } : null;
  }
  function rollsUp(node, checks, ctx) {
    const h = ctx.h;
    if (isGrouping(node)) {
      const origin = node.rollup.origin && node.rollup.origin !== node.path
        ? ` — <a class="link" data-go="${h.esc(node.rollup.origin)}">${h.esc(usbText(node.rollup.origin_name))}</a> is ${h.esc(node.rollup.level)}` : '';
      return `<div class="d6-why">No diagnostics of its own: it shows the worst below${node.rollup.level ? origin : ' (nothing below reports)'}.</div>`;
    }
    const worst = checks[0];
    if (!worst || !PROBLEM.has(node.level)) return `<div class="d6-why">${checks.length} diagnostic${checks.length === 1 ? '' : 's'}${checks.length ? ', all OK' : ''}: its level is the worst of them.</div>`;
    const setters = checks.filter((c) => c.effective_level === node.level).length;
    return `<div class="d6-why">Set by its own worst diagnostic${setters > 1 ? `s (${setters})` : ''}: <b>${h.esc(worst.name)}</b>${node.knock_on_of ? ` — explained by <a class="link" data-go="${h.esc(node.knock_on_of)}">${h.esc((ctx.byPath.get(node.knock_on_of) || {}).name || node.knock_on_of)}</a>, counted once there` : ''}.</div>`;
  }
  const isGrouping = (node) => !node.own || node.own.level === null || node.own.level === undefined;
  function childrenList(node, ctx) {
    const h = ctx.h;
    const children = node.children || [];
    if (!children.length) return '';
    const entry = ctx.bar(node.path, root.IOHealth.count(windowOf(ctx), 120, 'spark'));
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
      return `<div class="d6-ch${child.connected === false ? ' off' : ''}" data-go="${h.esc(child.path)}" title="${h.esc(child.name)}">${h.icon(child.icon)}<span class="d6-cn">${h.esc(usbText(child.name))}</span>`
        + `<span class="d6-cm">${h.esc(usbText(child.message || ''))}</span>${row && row.basis !== 'none' ? bar(row.bar, data.from, data.bin_s, 'spark', 'd6-mini') : '<span class="d6-mini d6-nobar">no history</span>'}`
        + `<span class="d6-cs">${h.shape(child.level, child.message)}${hint}</span></div>`;
    }).join('');
  }
  const plain = (id, title, body, right) => (body ? `<section class="sec ${id}"><h3 class="sec-h d6-h"><span>${title}</span>${right ? `<span class="right">${right}</span>` : ''}</h3><div class="sec-b">${body}</div></section>` : '');
  const fold = (ctx, id, title, body, right) => ctx.section(id, title, body, { right, defaultOpen: false });
  // The one function the variants lane replaces: the Diagnostics tab's body.
  function diagnosticsPanel(node, detail, ctx) {
    const p = ctx.parts;
    const checks = detail.checks;
    const kids = (node.children || []).length;
    // Zach picked d1a (2026-10-24): the Diagnostics tab IS d1a's section, drawn by detail/d1a.js through the one history renderer
    // (health.js); its rows come from the shared row model (d6-rows.js), the same one State and Configuration use. The table this page drew
    // itself is gone (Zach, 2026-10-24: one row model).
    const d1a = root.IODetailLayouts && root.IODetailLayouts.d1a;
    // the entity page's own marks on a row: a fault code with its meaning, its line in the Log, the light when the Log links here
    const marks = (check) => {
      const fault = faultOf(check);
      const known = detail.faults.some((f) => f.code === check.code);
      const h = ctx.h;
      const flash = U.flash && U.flash.tab === 'diagnostics' && U.flash.code && (U.flash.code === check.code || U.flash.code === (fault && fault.code)) ? ' d6-flash' : '';
      const explain = fault ? `<span class="d6-fx" title="fault code and what it means">${h.esc(fault.code)}${fault.meaning ? ` · ${h.esc(fault.meaning)}` : ''}</span>` : '';
      const log = known && check.code ? `<a class="link d6-lg" data-d6-jump="log" data-code="${h.esc(check.code)}" tabindex="0" title="the Log lines of this fault">in the log →</a>` : '';
      return { flash, extra: `${explain}${log}` };
    };
    // a stock diagnostic's row (source robotdx.diagnostics) also gets a chevron and its drill-in (d6-rows.js), drawn under the row's own detail
    const dx = ER() && ER().dx;
    const hooks = { rowClass: (row) => marks(row.check).flash, extra: (row) => marks(row.check).extra,
      lead: (row, open) => (dx ? dx.lead(row, open) : ''), body: (row) => (dx ? dx.body(row, ctx) : '') };
    const section = d1a && d1a.embed ? d1a.embed(document.getElementById('detail'), node, detail, ctx, hooks)
      : '<div class="muted">The Diagnostics section (detail/d1a.js) did not load.</div>';
    return rollsUp(node, [...checks].sort((a, b) => rankOf(b.effective_level) - rankOf(a.effective_level)), ctx) + section
      + plain('d6-children', `Below · ${kids}`, childrenList(node, ctx), 'each row\'s own health over the same window')
      + fold(ctx, 'faults', `Faults · ${detail.faults.length}`, p.faults()) + fold(ctx, 'links', 'Link health', p.links(), 'the computer diagnoses its own wires')
      + fold(ctx, 'setup', 'Expected setup', p.setup(), 'the rig file, never the device') + fold(ctx, 'status', 'Rollup', p.status())
      + fold(ctx, 'connection', `Connection · ${ctx.counts.connection}`, p.connection(), 'the layers this row is reached through');
  }

  // ------------------------------------------------------------------------------------------ State ----
  function watchNote(detail) {
    const watch = Live.watch || (detail && detail.watch) || { supported: false, state: 'unsupported' };
    if (!watch.supported) return { cls: 'plain', text: 'Not live: this driver does not stream. The values are from its last check.' };
    if (!Live.loaded && watch.state === 'running') return { cls: 'plain', text: 'starting the live stream…' };
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
      : `<img data-d6-image="${enc(key)}" data-d6-src="${src}"${StateWindow.visible.has(`image:${enc(key)}`) ? ` src="${src}"` : ''} alt="${h.esc(stream.title)}" draggable="false">`;
    return `<figure class="d6-tile${on ? '' : ' off'}"><figcaption><label class="d6-sw"><input type="checkbox" role="switch" data-d6-stream="${h.esc(stream.name)}"${on ? ' checked' : ''} aria-label="${h.esc(stream.title)} stream"><span class="d6-sw-t">${h.esc(stream.title)}</span></label>`
      + `<span class="d6-dim" title="${stream.colormap ? `colormap ${h.esc(stream.colormap)}` : ''}">${h.esc(size)} · ${h.esc(rate)}</span></figcaption>`
      + `<button class="d6-pic" data-d6-enlarge="${h.esc(stream.name)}" aria-label="Enlarge ${h.esc(stream.title)}"${on && stream.seq !== null ? '' : ' disabled'}>${picture}</button></figure>`;
  }
  // a composite's value that is a child's (docs/composition-v1.md section 5): "↳ CAN 13" after it, a click selects that child through the page's own
  // data-go selection. The name is the tree's own; a path the tree does not know is plain text, never a dead link.
  let childName = () => null;
  function sourceLink(row, h) {
    if (!row.source || !row.source.path) return '';
    const name = childName(row.source.path);
    const title = `${row.source.key ? `${row.source.key} of ` : ''}${name || row.source.path}`;
    return name ? ` <a class="link d6-src" data-go="${h.esc(row.source.path)}" title="${h.esc(title)}">↳ ${h.esc(name)}</a>` : ` <span class="d6-src muted" title="${h.esc(title)}">↳ ${h.esc(row.source.path.split('/').pop())}</span>`;
  }
  // The sparkline's grid cell. With the chart helper (charts/core.js, IOCharts.wrap) the cell is the wrapper: the page says which
  // series the sparkline draws (the state record's name, the measurement) and the selected chart view (c6 by default) makes it
  // unfold, open on a page of its own or pin to the Dashboard. WHY the wrapper replaces `.d6-ss`: an unfolded chart spans the
  // whole row (grid-column 1 / -1), so the wrapper must be the row grid's direct child, as d4 draws it.
  function sparkCell(group, row, ring) {
    const line = spark(ring, Live.now, SPARK_WINDOW_S, row);
    if (!window.IOCharts || !Live.path) return `<span class="d6-ss">${line}</span>`;
    return window.IOCharts.wrap({ p: Live.path, l: row.key, s: [[group.name, row.key]] }, line);
  }
  // one State row, as a leaf of the shared row model: name (with its star and handle), value, sparkline
  function stateLeaf(group, h) {
    const er = ER();
    return (row, d) => {
      const ring = Live.rings.get(`${group.name}\u0001${row.key}`) || [];
      const bad = PROBLEM.has(row.level);
      const value = row.meaning ? `${h.esc(typeof row.value === 'number' ? num(row.value) : row.text)} · ${h.esc(row.meaning)}` : h.esc(row.text);
      const id = d.id === null ? row.key : d.id;
      return `<div class="d6-sr${bad ? ' bad' : ''}"${bad ? ' data-diagnostic' : ''} ${er.rowAttrs(d.parent, id, 'state')} style="--er-depth:${d.depth}"><span class="d6-sn" title="${h.esc(row.key)}"><span class="er-ctl">${er.star(stateId(group, row), d.pinned)}${er.handle(d.parent, id)}</span>${h.esc(d.label === null ? row.key : d.label)}</span>`
        + `<span class="d6-sv${row.meaning ? ' txt' : ''}">${bad ? h.shape(row.level, `${row.key}: ${row.level}`) : ''}${value}${sourceLink(row, h)}</span>`
        + sparkCell(group, row, ring) + '</div>';
    };
  }
  const StateWindow = { path: null, visible: new Set(), heights: new Map(), counts: new Map(), nodes: new Set(), observer: null, resize: null };
  function stateWindow(group, rows) {
    if (rows.length <= 64 || typeof IntersectionObserver === 'undefined') return rows.map((draw) => draw()).join('');
    const prefix = enc(group.name);
    if (StateWindow.counts.get(prefix) !== rows.length) {
      StateWindow.counts.set(prefix, rows.length);
      for (const key of StateWindow.heights.keys()) if (key.startsWith(`${prefix}:`)) StateWindow.heights.delete(key);
    }
    const chunks = [];
    for (let start = 0; start < rows.length; start += 16) {
      const key = `${prefix}:${start}`;
      const visible = StateWindow.visible.has(key);
      const height = StateWindow.heights.get(key) || Math.min(16, rows.length - start) * 32;
      chunks.push(`<div data-d6-window="${key}"${visible ? ' data-d6-filled' : ''} style="${visible ? '' : `height:${height}px`}">`
        + (visible ? rows.slice(start, start + 16).map((draw) => draw()).join('') : '') + '</div>');
    }
    return chunks.join('');
  }
  function bindStateWindow(container, path) {
    if (StateWindow.path !== path) {
      StateWindow.path = path; StateWindow.visible.clear(); StateWindow.heights.clear(); StateWindow.counts.clear();
    }
    if (!StateWindow.observer && typeof IntersectionObserver !== 'undefined') {
      // WHY: native intersection and resize observations virtualize variable-height rows without layout reads after writes.
      // Folded folders and enlarged charts keep their normal geometry; only visible blocks generate SVGs/DOM.
      StateWindow.observer = new IntersectionObserver((entries) => {
        let changed = false;
        for (const entry of entries) {
          if (!entry.target.isConnected) continue;
          if (entry.target.hasAttribute('data-d6-image')) {
            const image = entry.target;
            const key = `image:${image.dataset.d6Image}`;
            const visible = entry.isIntersecting && !document.hidden;
            if (visible !== StateWindow.visible.has(key)) {
              if (visible) { StateWindow.visible.add(key); image.src = image.dataset.d6Src; }
              else { StateWindow.visible.delete(key); image.removeAttribute('src'); }
              changed = true;
            }
            continue;
          }
          const key = entry.target.dataset.d6Window;
          if (entry.isIntersecting !== StateWindow.visible.has(key)) {
            if (entry.isIntersecting) StateWindow.visible.add(key); else StateWindow.visible.delete(key);
            changed = true;
          }
        }
        if (changed && last) last.ctx.redraw();
      }, { root: container });
      StateWindow.resize = new ResizeObserver((entries) => {
        for (const entry of entries) if (entry.target.hasAttribute('data-d6-filled')) {
          StateWindow.heights.set(entry.target.dataset.d6Window, entry.borderBoxSize[0].blockSize);
        }
      });
    }
    if (!StateWindow.observer) return;
    const current = new Set(container.querySelectorAll('[data-d6-window], img[data-d6-image]'));
    for (const node of StateWindow.nodes) if (!current.has(node)) { StateWindow.observer.unobserve(node); StateWindow.resize.unobserve(node); }
    for (const node of current) if (!StateWindow.nodes.has(node)) { StateWindow.observer.observe(node); StateWindow.resize.observe(node); }
    StateWindow.nodes = current;
  }
  const stateId = (group, row) => (group.name ? `${group.name}::${row.key}` : row.key);
  function statePanel(node, detail, ctx) {
    const h = ctx.h;
    childName = (path) => (ctx.byPath && ctx.byPath.has(path) ? ctx.byPath.get(path).name : null);
    const note = watchNote(detail);
    const streams = Live.streams && Live.streams.length ? Live.streams : (detail.streams || []);
    const head = `<div class="d6-note ${note.cls}" role="status" data-d6-watch="${h.esc(!Live.loaded && (Live.watch || detail.watch || {}).supported ? 'starting' : (Live.watch || detail.watch || {}).state || '')}">${note.cls === 'paused' ? '⏸ ' : note.cls === 'live' ? '<span class="d6-dot"></span>' : ''}${h.esc(note.text)}</div>`;
    if (!Live.loaded) return `${head}<div class="muted">loading…</div>`;
    if (Live.none && !streams.length) return `${head}<div class="muted">This entity reports no state of its own.</div>`;
    const images = streams.filter((s) => s.kind === 'image');
    const open = !U.folded.has('images');
    const imageBlock = images.length ? `<section class="d6-block"><h3 class="d6-bh"><button class="d6-fold" data-d6-fold="images" aria-expanded="${open}"><svg class="ic sec-chev"><use href="#i-chev"/></svg>Images · ${images.length}</button><span class="right">click an image to enlarge</span></h3>`
      + (open ? `<div class="d6-tiles">${images.map((s) => tile(s, node.path, h)).join('')}${grip('tile', 'Resize the image tiles')}</div>` : '') + '</section>' : '';
    const groups = (Live.groups || []).filter((g) => g.rows.length);
    const multi = groups.length > 1 || groups.some((g) => g.name);
    const er = ER();
    const kind = node.kind || 'device';
    const sp = er.spec(kind, 'state');
    const mode = sp.sort || 'name';
    const pinnedRows = sp.pins.map((id) => { for (const g of groups) { const r = g.rows.find((row) => stateId(g, row) === id); if (r) return { g, r }; } return null; }).filter(Boolean);
    const pinnedHtml = pinnedRows.length ? `<div class="er-pinned" data-er-pinned="state"><h4 class="er-pinh">★ Pinned</h4>${pinnedRows.map(({ g, r }) => stateLeaf(g, h)(r, { label: multi && g.name ? `${g.name} › ${r.key}` : null, depth: 0, parent: '__pins__', id: null, pinned: true })).join('')}</div>` : '';
    const numbers = groups.length ? `<section class="d6-block" data-er-section="state"><div class="d6-sh"><span>Name${grip('name', 'Resize the name column')}</span><span>Value</span><span class="er-sortc">${er.sortControl('state', sp, ['name', 'manual'], 'name')}</span></div>${pinnedHtml}`
      + groups.map((group) => {
        const key = `state:${group.name}`;
        const isOpen = !U.folded.has(key);
        const stale = group.stale || (group.age_s !== null && group.age_s > 5 && !(Live.watch && Live.watch.state === 'running'));
        const heading = multi ? `<h3 class="d6-gh"><button class="d6-fold" data-d6-fold="${h.esc(key)}" aria-expanded="${isOpen}"><svg class="ic sec-chev"><use href="#i-chev"/></svg>${h.esc(group.name || 'values')}</button>`
          + `${stale ? `<span class="right d6-staleq">last values ${h.ago(group.age_s)} ago</span>` : ''}</h3>` : (stale ? `<div class="d6-staleq">last values ${h.ago(group.age_s)} ago</div>` : '');
        const rows = group.rows.filter((r) => !sp.pins.includes(stateId(group, r)));
        const tree = er.build(rows, { idOf: (r) => r.key, nameOf: (r) => r.key, sp: { ...sp, pins: [] }, mode });
        return heading + (isOpen ? er.renderTree(tree, { section: 'state', sp, leaf: stateLeaf(group, h), windowRows: (rows) => stateWindow(group, rows) }) : '');
      }).join('') + '</section>' : '';
    const empty = !images.length && !groups.length ? '<div class="muted">This entity reports no state of its own.</div>' : '';
    return head + imageBlock + numbers + empty + (Live.error ? `<div class="d6-staleq">the host did not answer: ${h.esc(Live.error)}</div>` : '');
  }

  // ------------------------------------------------------------------------------- Configuration ----
  const configPanel = (node, detail, ctx) => ER().config.panel(node, detail, ctx);

  // ------------------------------------------------------------------------------------------- Log ----
  // The Activity log's own compact view (activity/log.js, docs/activity-log-v1.md), scoped to the entity and what is below it, with its
  // Raw channel. WHY the snapshot: the shell's morph() would empty a mount it does not know, so the panel carries the mount's own
  // current markup back (equal nodes are left alone). A fault's badge in the log opens that fault in Diagnostics, and a Diagnostics
  // row with a fault opens its line here (found by the code in the badge's own words).
  const LogView = { inst: null, el: null, path: null };
  function logPanel(node) {
    if (!window.IOActivityLog) return '<div class="muted">The Activity log did not load.</div>';
    const keep = LogView.inst && LogView.el && LogView.el.isConnected && LogView.path === node.path ? LogView.el.innerHTML : '';
    return `<div class="d6-log" data-d6-log>${keep}</div>`;
  }
  function bindLog(container, node, active) {
    const el = active === 'log' ? container.querySelector('[data-d6-log]') : null;
    if (!el || !window.IOActivityLog) {
      if (LogView.inst) { LogView.inst.unmount(); Object.assign(LogView, { inst: null, el: null, path: null }); }
      if (logWatcher) { logWatcher.observer.disconnect(); logWatcher = null; }
      return;
    }
    const scope = { path: node.path, name: node.name, mode: 'children' };
    if (LogView.inst && LogView.el === el) {
      if (LogView.path !== node.path) { LogView.path = node.path; LogView.inst.setScope(scope); }
    } else {
      if (LogView.inst) LogView.inst.unmount();
      LogView.inst = window.IOActivityLog.create({ compact: true, scope, raw: true, fixedScope: true, floor: 'info' });
      LogView.inst.mount(el);
      Object.assign(LogView, { el, path: node.path });
    }
    LogView.inst.start();
    watchLog(el);
    flashLog(el);
  }
  const faultCodeOf = (text) => ((String(text).match(/[A-Z][A-Za-z]+\.[0-9A-Za-z_]+/) || [])[0] || '');
  // a line that raised or cleared a fault (its own row, or the badge on its cause's row) gets a link to the fault in Diagnostics; the
  // log redraws its rows whole, so the links are put back whenever it does
  let logWatcher = null;
  function decorateLog(el) {
    for (const row of el.querySelectorAll('.al-row')) {
      if (row.querySelector('.d6-lg')) continue;
      const kind = (row.querySelector('.al-code .kind') || {}).textContent || '';
      const code = faultCodeOf(row.querySelector('.al-msg') ? row.querySelector('.al-msg').textContent : '');
      if (!code || !(row.querySelector('.al-badge') || /^Fault/.test(kind))) continue;
      const where = row.querySelector('[data-al-scope]');
      const link = document.createElement('a');
      link.className = 'link d6-lg';
      link.dataset.d6Jump = 'diagnostics';
      link.dataset.code = code;
      link.dataset.path = where ? where.dataset.alScope : '';
      link.tabIndex = 0;
      link.textContent = 'in Diagnostics →';
      (row.querySelector('.al-msg') || row).appendChild(link);
    }
  }
  function watchLog(el) {
    if (logWatcher && logWatcher.el === el) return;
    if (logWatcher) logWatcher.observer.disconnect();
    const observer = new MutationObserver(() => { observer.disconnect(); decorateLog(el); observer.observe(el, { childList: true, subtree: true }); });
    observer.observe(el, { childList: true, subtree: true });
    logWatcher = { el, observer };
    decorateLog(el);
  }
  function flashLog(el, tries = 6) {
    if (!U.flash || U.flash.tab !== 'log' || !U.flash.code || U.flash.scrolled) return;
    const row = [...el.querySelectorAll('.al-row')].find((r) => [...r.querySelectorAll('.al-badge')].some((b) => faultCodeOf(`${b.title} ${b.textContent}`) === U.flash.code) || faultCodeOf(r.textContent) === U.flash.code);
    if (row) { row.classList.add('d6-flash'); row.scrollIntoView({ block: 'center' }); U.flash.scrolled = true; return; }
    if (tries > 0) setTimeout(() => { if (LogView.el === el) flashLog(el, tries - 1); }, 400);
  }

  // ------------------------------------------------------------------------------------- Utilities ----
  const DANGER = { query: 'reads only', interrupts: 'interrupts what runs', changes: 'changes what the device stores', moves: 'makes hardware move' };
  const DANGER_ORDER = ['query', 'interrupts', 'changes', 'moves'];
  function utilitiesPanel(node, detail, ctx) {
    const h = ctx.h;
    const actions = [...(detail.actions || [])].filter((action) => !CONFIG_ACTIONS.has(action.name)).sort((a, b) => (DANGER_ORDER.indexOf(a.danger) - DANGER_ORDER.indexOf(b.danger)) || a.title.localeCompare(b.title));
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
.d6-panel { padding: 12px 18px 28px; min-width: 0; }
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
.d6-src { margin-left: 6px; font-size: 11.5px; white-space: nowrap; }
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
.d6-h { cursor: default; }
.sec.d6-diag .sec-b { padding-top: 0; }
.d6-fx { display: inline-block; margin-left: 4px; padding: 0 6px; border-radius: var(--io-radius-sm); background: var(--io-control); color: var(--io-text-strong); font-size: 12px; }
.d6-lg { font-size: 11.5px; margin-left: 6px; white-space: nowrap; }
.d6-ch { display: flex; align-items: center; gap: 8px; height: var(--io-row-h); padding: 0 6px; margin: 0 -6px; border-radius: var(--io-radius-sm); cursor: pointer; white-space: nowrap; }
.d6-ch:hover { background: var(--io-hover); }
.d6-ch .ic { color: var(--io-icon); flex: none; }
.d6-ch.off { color: var(--io-off); }
.d6-cn { flex: none; max-width: 45%; overflow: hidden; text-overflow: ellipsis; color: var(--io-text); }
.d6-cm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--io-muted); font-size: 12px; }
.d6-mini { flex: none; width: 120px; }
.d6-nobar { color: var(--io-faint); font-size: 11px; height: auto; text-align: right; }
.d6-cs { flex: none; width: 34px; display: inline-flex; justify-content: flex-end; gap: 2px; }
.d6-hint .shape { width: 10px; height: 10px; opacity: .8; }
/* the shared row model (d6-rows.js): folders, stars, handles, the sort switch, the edit state of a setting */
.d6-sr .d6-sn, .er-cfgrow .er-name { padding-left: calc(var(--er-depth, 0) * 16px); }
.er-tools { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.er-tools .d6-copy { margin-left: 0; }
.er-name { min-width: 0; display: flex; align-items: center; gap: 4px; overflow: hidden; }
.er-name .d6-sn { flex: 0 1 auto; }
.er-safe, .er-lock { flex: none; font-size: 10.5px; color: var(--io-faint); border: 1px solid var(--io-border); border-radius: var(--io-radius-pill); padding: 0 6px; }
.er-lock { border: 0; padding: 0; }
.er-cfgrow { grid-template-columns: var(--d6-name-w, 190px) minmax(0, 1fr) auto; align-items: start; }
.er-right { display: inline-flex; align-items: center; gap: 6px; justify-self: end; }
.er-edit { display: inline-flex; align-items: center; gap: 6px; }
.er-in { width: 150px; max-width: 100%; padding: 3px 7px; font: 12px var(--io-font-mono); color: var(--io-text-strong); background: var(--io-field); border: 1px solid var(--io-border); border-radius: var(--io-radius-sm); }
.er-in:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.er-unit { color: var(--io-faint); font-size: 11.5px; }
.er-sw { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; }
.er-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 3px; }
.er-chip { display: inline-flex; align-items: center; gap: 4px; padding: 0 8px; border-radius: var(--io-radius-pill); font-size: 11.5px; border: 1px solid var(--io-border); color: var(--io-muted); background: var(--io-control); }
.er-chip.ok { color: var(--io-text-strong); }
.er-chip.unsaved { color: var(--io-text-strong); border-style: dashed; }
.er-chip.pending { font-style: italic; }
.er-chip.bad { color: var(--io-error-text); border-color: var(--io-error-line); background: var(--io-error-soft); }
.er-chip .shape { width: 11px; height: 11px; }
.er-rb { background: transparent; border: 0; color: var(--io-accent-text); cursor: pointer; font-size: 14px; padding: 0 3px; }
.er-resetrow { padding: 0 4px 6px calc(var(--er-depth, 0) * 16px + 20px); }
.er-reset { display: flex; flex-wrap: wrap; gap: 6px; font-size: 12px; }
.er-rbtn { padding: 2px 10px; font-size: 12px; }
/* a stock diagnostic's drill-in (d6-rows.js): unfolds under its Diagnostics row; health colours only on the band's warn / error zones */
.dxc-chev { margin-right: 3px; flex: none; }
.dx-x.dxc { margin: 0 4px 10px 18px; padding: 10px 12px; border: 1px solid var(--io-border-soft); border-radius: var(--io-radius); background: var(--io-surface-alt); gap: 10px; }
.dxc-h { color: var(--io-text-strong); }
.dxc-id { color: var(--io-faint); font-size: 11px; margin-left: 4px; }
.dxc-doc p { margin: 0 0 3px; color: var(--io-muted); }
.dxc-g { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 14px; align-items: center; }
.dxc-gb { color: var(--io-muted); font-size: 12px; }
.dxc-k { color: var(--io-faint); }
.dxc-band { grid-column: 1 / -1; position: relative; height: 8px; border-radius: var(--io-radius-pill); background: var(--io-control); border: 1px solid var(--io-border-soft); overflow: hidden; }
.dxc-z { position: absolute; top: 0; bottom: 0; }
.dxc-z.warn { background: var(--io-warn-soft); border-left: 1px solid var(--io-warn-line); }
.dxc-z.err { background: var(--io-error-soft); border-left: 1px solid var(--io-error-line); }
.dxc-v { position: absolute; top: -1px; bottom: -1px; width: 3px; margin-left: -1px; background: var(--io-text-strong); border-radius: 1px; }
.dxc-w { color: var(--io-muted); font-size: 12px; }
.dxc-form { display: grid; gap: 2px; border-top: 1px solid var(--io-border-soft); padding-top: 6px; }
.dxc-f { display: grid; grid-template-columns: minmax(120px, 190px) minmax(0, 1fr) 64px 24px; gap: 10px; align-items: center; min-height: 28px; }
.dxc-fn { font-size: 12px; color: var(--io-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dxc-src { font-size: 11px; color: var(--io-faint); text-align: right; }
.dxc-src.edited { color: var(--io-accent-text); font-weight: 600; }
.dxc-reset { padding-left: 200px; }
.dxc-foot { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 11.5px; }
.dxc-all { padding: 2px 10px; font-size: 12px; }
.dxc-parts { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px; }
.dxc-part { display: inline-grid; gap: 1px; }
.dxc-part .er-in { width: 84px; }
.dxc-pl { font-size: 10.5px; color: var(--io-faint); }
.dxc-pc { display: inline-flex; align-items: center; gap: 4px; }
.dxc-pc > .dxc-pl { white-space: nowrap; }
.dxc-doc code { font: 11.5px var(--io-font-mono); }
.er-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin: 0 0 8px; padding: 7px 10px; border: 1px solid var(--io-border); border-radius: var(--io-radius); background: var(--io-control); font-size: 12.5px; }
.er-bar.dirty { border-style: dashed; color: var(--io-text-strong); }
.er-bar.bad { color: var(--io-error-text); border-color: var(--io-error-line); background: var(--io-error-soft); }
.er-bar-b { display: inline-flex; gap: 8px; }
.er-edit-btn.on { background: var(--io-pressed); color: var(--io-text-strong); font-weight: 600; }
.d6-modal { position: fixed; inset: 0; z-index: 70; display: grid; place-items: center; background: var(--io-overlay); }
.d6-modal-c { width: min(520px, 92vw); padding: 18px 20px; background: var(--io-popover); color: var(--io-text); border: 1px solid var(--io-border); border-radius: var(--io-radius-lg); box-shadow: var(--io-shadow-pop); }
.d6-modal-c h3 { margin: 0 0 8px; font-size: 15px; color: var(--io-text-strong); }
.d6-modal-c p { margin: 6px 0; font-size: 12.5px; }
.d6-modal-b { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.d6-log { min-height: 320px; margin: 0 -18px; }
.d6-log .al-badge { cursor: pointer; }
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
      case 'log': return logPanel(node);
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
      if (Live.path !== node.path) startLive(node.path, Boolean(detail.watch && detail.watch.supported), (detail.streams || []).length ? Number((detail.watch || {}).period_s) || 0 : 0);
    } else if (Live.path) stopLive();
    if (StateWindow.path !== node.path) {
      StateWindow.path = node.path; StateWindow.visible.clear(); StateWindow.heights.clear(); StateWindow.counts.clear();
    }
    const row = ctx.row;
    const er = ER();
    if (er && !er.arrangement.started) er.loadArrangements(() => { if (last) last.ctx.redraw(); });
    if (er && er.config.editing && (active !== 'config' || !er.config.editing(node.path))) er.config.leave(); // editing ends with the tab
    const banners = p.banners();
    const style = `--d6-name-w:${U.nameW}px;--d6-tile-w:${U.tileW}px`;
    ctx.draw(`<div class="d6-top">${headerHtml(node, detail, ctx, row)}${banners ? `<div class="d6-banners">${banners}</div>` : ''}${tabBar(ctx, node, detail, tabs, active)}</div>`
      + `<div class="d6-panel" role="tabpanel" id="d6-panel" aria-labelledby="d6-tab-${h.esc(active)}" data-d6 data-d6-panel="${h.esc(active)}" style="${style}">${panelHtml(active, tabs, node, detail, ctx)}</div>${lightboxHtml(node, h)}${active === 'config' && er ? er.config.modalHtml(node, ctx) : ''}`);
    bindStateWindow(container, node.path);
    afterDraw(container);
    bindLog(container, node, active);
    if (er) er.config.afterDraw(container);
    if (er && er.dx) er.dx.afterDraw(container);
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
      const er = ER();
      if (er && er.dx && container.querySelector('[data-d6-panel="diagnostics"]') && er.dx.click(target, ctx)) return;
      if (er && er.onClick(target, { kind: node.kind || 'device', redraw: ctx.redraw })) return;
      if (er && container.querySelector('[data-d6-panel="config"]') && er.config.click(target, ctx, detail)) return;
      const badge = target.closest('.d6-log .al-badge'); // a fault's badge in the log: that fault, in Diagnostics
      if (badge) {
        const code = faultCodeOf(`${badge.title} ${badge.textContent}`);
        const where = badge.closest('.al-row') && badge.closest('.al-row').querySelector('[data-al-scope]');
        if (code) { jump(ctx, node, 'diagnostics', where ? where.dataset.alScope : node.path, code); return; }
      }
      const tab = target.closest('[data-d6-tab]');
      if (tab) { chooseTab(ctx, node, tabsFor(detail), tab.dataset.d6Tab); return; }
      if (target.closest('[data-d6-lb]')) { U.lightbox = null; ctx.redraw(); return; }
      const enlarge = target.closest('[data-d6-enlarge]');
      if (enlarge) { U.lightbox = { path: node.path, stream: enlarge.dataset.d6Enlarge }; ctx.redraw(); return; }
      const more = target.closest('[data-d6-more]');
      if (more) { if (U.more.has(node.path)) U.more.delete(node.path); else U.more.add(node.path); ctx.redraw(); return; }
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
        const text = ER().config.copyText(detail);
        const done = () => { U.copied = Date.now(); ctx.redraw(); setTimeout(() => ctx.redraw(), 2100); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, done);
        else done();
      }
    });
    container.addEventListener('change', (event) => {
      const target = event.target;
      if (!live() || !(target instanceof Element)) return;
      const { ctx, detail } = live();
      const er = ER();
      if (er && er.dx && container.querySelector('[data-d6-panel="diagnostics"]') && er.dx.change(target, ctx)) return;
      if (er && er.onChange(target, { kind: ctx.node.kind || 'device', redraw: ctx.redraw })) return;
      if (er && container.querySelector('[data-d6-panel="config"]') && er.config.change(target, ctx, detail)) return;
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
      if (!live() || !(target instanceof Element)) return;
      if (ER().dx && ER().dx.input(target, live().ctx)) return;
      ER().config.input(target, live().ctx);
    });
    container.addEventListener('keydown', (event) => {
      const target = event.target;
      if (!live() || !(target instanceof Element)) return;
      const { ctx, detail } = live();
      if (ER().config.key(event, ctx, detail)) return;
      if (ER().dx && ER().dx.key(event, ctx)) return;
      if (event.key === 'Escape' && U.lightbox) { U.lightbox = null; ctx.redraw(); event.stopPropagation(); return; }
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
    // dragging a row (by its handle) to reorder it inside its folder: the shared row model's
    container.addEventListener('dragstart', (event) => { if (live()) ER().onDragStart(event, { kind: live().ctx.node.kind || 'device' }); });
    container.addEventListener('dragover', (event) => { if (live()) ER().onDragOver(event); });
    container.addEventListener('drop', (event) => { if (live()) ER().onDrop(event, { kind: live().ctx.node.kind || 'device', redraw: live().ctx.redraw }, container); });
    container.addEventListener('dragend', () => ER().clearDrag(container));
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
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { stopLive(); if (LogView.inst) LogView.inst.stop(); }
      else for (const image of document.querySelectorAll('.d6-lb img')) if (image.d6Resume) { image.src = image.d6Resume; delete image.d6Resume; }
    });
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d6 = {
    name: 'Entity page',
    description: 'A header that is always visible (name, model, connection badge, the ticking "heard 2 s ago", hardware line) and tabs: Diagnostics (health only), State (live numbers as one-line rows with a sparkline, images as stream tiles), Configuration (read-only, searchable, expected vs read), Log (this entity and below, with Raw), Utilities, and a driver\'s own tabs (the Computer\'s Processes).',
    order: 0,
    render,
    // WHY: /values establishes a coherent cursor/history before incremental SSE points can advance it.
    samples(data) {
      if (!Live.path || data.path !== Live.path || !liveAlive(Live.path)) return;
      if (!Live.loaded) {
        // WHY: preserve points that arrive while the one-time history bootstrap is in flight.
        Live.pending.push(data);
        if (Live.pending.length > 100) { if (Live.abort) Live.abort.abort(); stopLive(); if (last) last.ctx.redraw(); }
        return;
      }
      if (data.cursor <= Live.cursor) return;
      merge(data);
      if (last) last.ctx.redraw();
    },
    resetLive() { stopLive(); },
    pause() { stopLive(); if (LogView.inst) LogView.inst.stop(); },
    // for tests and the next lane
    internals: { Live, U, Heard, tabsFor },
  };
})(typeof window !== 'undefined' ? window : globalThis);
