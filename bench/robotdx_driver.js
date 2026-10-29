#!/usr/bin/env node
// The browser half of tools/bench_robotdx.py: drives the RobotDx page (io_diagnostics/web/dx/) in headless Chrome and prints ONE
// JSON object on stdout. Every number comes from the browser (performance.now, requestAnimationFrame, CDP). It never starts a
// host: tools/bench_robotdx.py starts a fixture host (tools/dx_demo.py: fixture plugins only) or the test-only stub
// (bench/robotdx_stub.py: synthetic, no device) and hands this script its URL.
//
//   node bench/robotdx_driver.js '{"phase": "fixture", "base": "http://127.0.0.1:5600", "board": "host/usb1/port-1-3/usb-1-3/can/1"}'
//   node bench/robotdx_driver.js '{"phase": "sweep", "base": "http://127.0.0.1:5650", "seconds": 8}'
//   node bench/robotdx_driver.js '{"phase": "heap", "base": "http://127.0.0.1:5650", "capMB": 64, "seconds": 90}'
//   node bench/robotdx_driver.js '{"phase": "profile", "base": "http://127.0.0.1:5650", "seconds": 6}'
//
// Stamps (PEP 0001 §15): ingest_to_pixel_ms is timed from each /live event's `emitted` (the host's wall clock at send, unix ms,
// on the real host since 5cdb4b9 and on the stub), else from the stub's `now`, else from the adapter's receive; every result
// says which (`stamp`), and a sweep counts its stream's reconnects (a resumed stream replays events with their first stamp).
//
// Options in the JSON: software (true: headless Chrome's CPU raster; default the GPU, data-ui bench/runner.py's GPU_ARGS, with
// the WebGL renderer string recorded so a software number is never read as a GPU one), loads, seeks, lives, idleS, ingestS.
'use strict';

const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const { chromium } = require(PLAYWRIGHT);

const CHROME = '/usr/bin/google-chrome';
const VIEWPORT = { width: 1400, height: 900 };
// data-ui bench/runner.py: no background throttling (a headless page is otherwise a background tab), and the GPU args
const BROWSER_ARGS = ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows'];
const GPU_ARGS = ['--use-angle=vulkan', '--enable-features=Vulkan', '--enable-gpu', '--ignore-gpu-blocklist'];
const SOFTWARE = /swiftshader|llvmpipe|softpipe|software|basic render/i;
const DX = '/dx/';   // 4e2ec56: a path ending in / is its index.html

const cfg = JSON.parse(process.argv[2] || '{}');
const pct = (xs, p) => { const v = xs.filter((x) => typeof x === 'number' && Number.isFinite(x)).sort((a, b) => a - b); return v.length ? v[Math.max(1, Math.ceil((p / 100) * v.length)) - 1] : null; };
const r1 = (x) => (x === null || x === undefined ? null : Math.round(x * 10) / 10);
const errors = [];

async function newPage(browser) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  const page = await context.newPage();
  page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 200)}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text().slice(0, 200)}`); });
  return page;
}
const ready = (page) => page.waitForFunction(() => window.robotdx && window.robotdx.clock && document.querySelector('#dx-main'), null, { timeout: 30000 });

/** A fixed amount of JavaScript work, timed: the machine's speed now (data-ui bench/harness.js's calibration, same idea). */
async function calibrate(browser) {
  const page = await newPage(browser);
  await page.setContent('<!doctype html><title>calibrate</title>');
  const runs = [];
  for (let i = 0; i < 3; i += 1) {
    runs.push(await page.evaluate(() => { const t0 = performance.now(); let s = 0; for (let k = 1; k < 4e7; k += 1) s += Math.sqrt(k) % 7; return { ms: performance.now() - t0, s }; }));
  }
  await page.context().close();
  return Math.min(...runs.map((x) => x.ms));
}

// ---- K7 / first_paint_ms: navigation start to the tree drawn (tests/test_dx_page.js's in-page timer) ------------------------------
async function firstPaint(browser, base, loads) {
  const out = [];
  for (let i = 0; i < loads; i += 1) {
    const page = await newPage(browser);
    await page.addInitScript(() => {
      window.__fp = null;
      // the tree drawn: since PEP 0001 §16 the classic page's own (web/tree/v6.js rows, tests/test_dx_page.js's K7 timer: five);
      // before it, data-ui's table tree (its model's rows and a drawn row), so the same driver times either page
      const done = () => {
        if (document.querySelectorAll('#dx-tree [role=treeitem][data-t6-path]').length >= 5) return true;
        const el = document.querySelector('#dx-tree');
        return el && el.model && el.model.rows.length >= 10 && el.querySelector('tbody tr[data-id]');
      };
      const poll = () => { if (done()) window.__fp = performance.now(); else if (performance.now() < 15000) setTimeout(poll, 10); };
      poll();
    });
    await page.goto(`${base}${DX}`);
    let fp = null;
    for (let k = 0; k < 150 && fp === null; k += 1) { await page.waitForTimeout(100); fp = await page.evaluate(() => window.__fp); }
    out.push(fp === null ? null : r1(fp));
    await page.context().close();
  }
  return out;
}

// ---- idle_traffic_kb_s: every byte from /api/ (streams included, CDP Network.dataReceived) on Explore with nothing selected --------
async function idleTraffic(browser, base, seconds) {
  const page = await newPage(browser);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  const ids = new Set();
  let bytes = 0, requests = 0, counting = false;
  cdp.on('Network.requestWillBeSent', (e) => { const u = new URL(e.request.url); if (u.pathname.startsWith('/api/')) { ids.add(e.requestId); if (counting) requests += 1; } });
  cdp.on('Network.dataReceived', (e) => { if (counting && ids.has(e.requestId)) bytes += e.dataLength; });
  await page.goto(`${base}${DX}?go=explore`);
  await ready(page);
  await page.waitForTimeout(3000);
  counting = true;
  await page.waitForTimeout(seconds * 1000);
  counting = false;
  const mode = await page.evaluate(() => window.robotdx.feed.mode);
  await page.context().close();
  return { kb_s: r1(bytes / 1024 / seconds), requests_s: Math.round((requests / seconds) * 100) / 100, seconds, mode };
}

/** In the page: move the clock (`move`, a function body of c), then every live panel's FIRST draw at the new t (or following),
    and the frame that shows the last one. The live panels are the <data-views> under `prefix` that carry data-live. */
async function panelsAt(page, prefix, move, { following = false, timeout = 2000 } = {}) {
  return page.evaluate(async ({ prefix, following, timeout, move }) => {
    const draws = [];
    const on = (e) => { if (e.target.id && e.target.id.startsWith(prefix)) draws.push({ id: e.target.id, t: e.detail.t, following: e.detail.following, at: performance.now() }); };
    document.addEventListener('data-views-live', on, true);
    const panels = [...document.querySelectorAll(`data-views[id^="${prefix}"][data-live]`)].map((el) => el.id);
    const c = window.robotdx.clock;
    const t0 = performance.now();
    // eslint-disable-next-line no-new-func
    const target = new Function('c', move)(c);
    const want = c.t;
    const first = new Map();
    for (;;) {
      await new Promise((r) => requestAnimationFrame(r));
      // paused: a draw at exactly the sought t; live: a following draw at (or past) the newest data when it went live
      for (const d of draws) if (d.at >= t0 && !first.has(d.id) && d.following === following && (following ? d.t >= target - 1e-9 : d.t === want)) first.set(d.id, d.at);
      if (panels.length && panels.every((id) => first.has(id))) break;
      if (performance.now() - t0 > timeout) break;
    }
    // the frame that shows the last draw: the next animation frame after it
    await new Promise((r) => requestAnimationFrame(r));
    const painted = performance.now();
    document.removeEventListener('data-views-live', on, true);
    const last = first.size ? Math.max(...first.values()) : null;
    const complete = panels.length > 0 && panels.every((id) => first.has(id));
    return { ms: complete ? last - t0 : null, painted_ms: complete ? painted - t0 : null, panels: first.size, of: panels.length, target };
  }, { prefix, following, timeout, move });
}

// ---- scrub_ms and back_to_live_ms on Explore (the fixture's joint: seven live panels) -------------------------------------------
async function scrubAndLive(browser, base, board, seeks, lives) {
  const page = await newPage(browser);
  await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(board)}`);
  await ready(page);
  await page.waitForFunction((p) => { const d = window.robotdx.feed.dataset('samples', p); return d && d.end && d.start && d.end - d.start > 20; }, board, { timeout: 60000 }).catch(() => {});
  const span = await page.evaluate((p) => { const d = window.robotdx.feed.dataset('samples', p); return { start: d.start, end: d.end }; }, board);
  const scrub = [];
  for (let i = 0; i < seeks; i += 1) {
    const f = 0.15 + 0.7 * ((i * 0.618034) % 1);
    const t = span.start + f * (span.end - span.start);
    scrub.push(await panelsAt(page, 'dx-detail-', `c.seek(${t}); return ${t};`));
  }
  const back = [];
  for (let i = 0; i < lives; i += 1) {
    await page.evaluate(() => { const c = window.robotdx.clock; c.seek(c.liveEnd - 5); });
    await page.waitForTimeout(2000);
    back.push(await panelsAt(page, 'dx-detail-', 'c.goLive(); return c.liveEnd;', { following: true }));
  }
  await page.context().close();
  return { scrub, back, span_s: r1(span.end - span.start) };
}

// ---- ingest_to_pixel_ms on a real host: the adapter's receive (the samples event in the page) to the plot drawn at it -------------
// WHY the adapter's receive: the fixture host's samples carry its replay clock (`now`), not a clock the browser shares; the stub's
// carry the wall clock, so the sweep measures from the host's emit (below)
async function ingestFixture(browser, base, board, seconds) {
  const page = await newPage(browser);
  await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(board)}`);
  await ready(page);
  await page.waitForTimeout(4000);
  const res = await page.evaluate(async ({ board, seconds }) => {
    const es = window.robotdx.feed._live;
    const batches = [], draws = [];
    const onSamples = (m) => {
      if (!m.data.includes(`"path": "${board}"`) && !m.data.includes(`"path":"${board}"`)) return;
      const ds = window.robotdx.feed._sets.get(`samples:${board}`);
      const emitted = Number((/"emitted":\s*([0-9.eE+-]+)/.exec(m.data) || [])[1]);
      if (ds) batches.push({ at: performance.now(), wall: performance.timeOrigin + performance.now(), emitted: Number.isFinite(emitted) ? emitted : null, newest: ds.end });
    };
    const onDraw = (e) => { if (e.target.id === 'dx-detail-signals' && e.detail.following) draws.push({ at: performance.now(), t: e.detail.t }); };
    if (es) es.addEventListener('samples', onSamples);
    document.addEventListener('data-views-live', onDraw, true);
    await new Promise((r) => setTimeout(r, seconds * 1000));
    if (es) es.removeEventListener('samples', onSamples);
    document.removeEventListener('data-views-live', onDraw, true);
    const lat = [], latEmit = [];
    for (const b of batches) {
      const d = draws.find((x) => x.at >= b.at && x.t >= b.newest - 1e-9);
      if (!d) continue;
      lat.push(d.at - b.at);
      // the host's emit to the draw: the draw's wall time (timeOrigin + its performance.now) minus `emitted` (one machine, one clock)
      if (b.emitted !== null) latEmit.push(performance.timeOrigin + d.at - b.emitted);
    }
    return { lat, latEmit, batches: batches.length, stamped: batches.filter((b) => b.emitted !== null).length, mode: window.robotdx.feed.mode };
  }, { board, seconds });
  await page.context().close();
  return res;
}

// ---- frame_time_ms and ingest_to_pixel (host emit) on the stub's dashboard ------------------------------------------------------
async function sweep(browser, base, seconds) {
  const page = await newPage(browser);
  await page.goto(`${base}${DX}?go=dashboards&board=bench`);
  await ready(page);
  await page.waitForFunction(() => { const d = document.querySelector('data-dashboard.dx-board'); return d && d.querySelectorAll('[data-points]').length > 0; }, null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(3000);   // warm: every plot's window is full
  const res = await page.evaluate(async ({ seconds }) => {
    const { liveStats } = await import('/vendor/data-ui/data-ui.js');
    const es = window.robotdx.feed._live;
    const frames = [], batches = [], draws = [];
    let rec = true;
    const tick = (ts) => { if (rec) { frames.push(ts); requestAnimationFrame(tick); } };
    requestAnimationFrame(tick);
    const longs = [];
    const po = new PerformanceObserver((l) => { for (const e of l.getEntries()) longs.push(e.duration); });
    po.observe({ type: 'longtask' });
    // WHY no JSON.parse here: the page's own handler (added first, so it ran first) has parsed and appended the batch; a
    // second parse per event would bill the page for the bench's work. The stamp and the path are read with a regex, the
    // batch's newest sample from the dataset the page just appended it to.
    let reconnects = 0;
    const onError = () => { reconnects += 1; };
    const onSamples = (m) => {
      const got = performance.timeOrigin + performance.now();
      const path = (/"path":"([^"]+)"/.exec(m.data) || [])[1];
      // `emitted` (unix ms, the host's wall clock at send), else the stub's `now` (its wall clock in seconds): one machine, one clock
      const emitted = Number((/"emitted":([0-9.eE+-]+)/.exec(m.data) || [])[1]);
      const now = Number((/"now":([0-9.eE+-]+)/.exec(m.data) || [])[1]);
      const ds = path && window.robotdx.feed._sets.get(`samples:${path}`);
      const emit = Number.isFinite(emitted) ? emitted : Number.isFinite(now) ? now * 1000 : null;
      if (!ds || emit === null) return;
      batches.push({ emit, stamp: Number.isFinite(emitted) ? 'emitted' : 'stub-now', got, newest: ds.end, n: (m.data.match(/\[\d+\.\d+,/g) || []).length, path });
    };
    const onDraw = (e) => { if (e.detail.following && e.target.closest('data-dashboard')) draws.push({ at: performance.timeOrigin + performance.now(), t: e.detail.t, id: e.target.id }); };
    es.addEventListener('samples', onSamples);
    es.addEventListener('error', onError);
    document.addEventListener('data-views-live', onDraw, true);
    const s0 = liveStats().length ? liveStats().at(-1).frame : 0;
    await new Promise((r) => setTimeout(r, seconds * 1000));
    rec = false;
    po.disconnect();
    es.removeEventListener('samples', onSamples);
    es.removeEventListener('error', onError);
    document.removeEventListener('data-views-live', onDraw, true);
    const gaps = frames.slice(1).map((t, i) => t - frames[i]);
    const loop = liveStats().filter((s) => s.frame > s0).map((s) => s.ms);
    const fromEmit = [], fromGot = [];
    for (const b of batches) {
      const d = draws.find((x) => x.at >= b.got && x.t >= b.newest - 1e-9);
      if (d) { fromEmit.push(d.at - b.emit); fromGot.push(d.at - b.got); }
    }
    const samples = batches.reduce((a, b) => a + b.n, 0);
    const panels = document.querySelectorAll('data-dashboard.dx-board [data-points]').length;
    const stamps = [...new Set(batches.map((b) => b.stamp))];
    return { gaps, loop, longs, fromEmit, fromGot, stamp: stamps.length === 1 ? stamps[0] : stamps.join('+') || null, reconnects,
      events_s: batches.length / seconds, samples_s: samples / seconds, panels, frames: frames.length, mode: window.robotdx.feed.mode };
  }, { seconds });
  await page.context().close();
  return res;
}

// ---- heap_mb at the retention cap (short: a small cap and window; long: 10 minutes into 256 MB) ---------------------------------
async function heap(browser, base, capMB, seconds) {
  const page = await newPage(browser);
  await page.goto(`${base}${DX}?go=dashboards&board=bench`);
  await ready(page);
  await page.evaluate(async (cap) => { const kit = await import('/vendor/data-ui/data-ui.js'); kit.setLiveBudget(`${cap}MB`); }, capMB);
  const cdp = await page.context().newCDPSession(page);
  const t0 = Date.now();
  let atCap = null;
  while (Date.now() - t0 < seconds * 1000) {
    await page.waitForTimeout(5000);
    const bytes = await page.evaluate(() => Object.values(window.robotdx.feed.datasets()).reduce((a, d) => a + (d.bytes || 0), 0));
    if (atCap === null && bytes >= 0.9 * capMB * 2 ** 20) atCap = (Date.now() - t0) / 1000;
  }
  await cdp.send('HeapProfiler.collectGarbage').catch(() => {});
  const usage = await cdp.send('Runtime.getHeapUsage').catch(() => null);
  const store = await page.evaluate(() => Object.values(window.robotdx.feed.datasets()).reduce((a, d) => a + (d.bytes || 0), 0));
  await page.context().close();
  return { heap_mb: usage ? r1(usage.usedSize / 2 ** 20) : null, store_mb: r1(store / 2 ** 20), cap_mb: capMB, seconds, at_cap_s: atCap };
}

// ---- profile: where the main thread's time goes while streaming (PEP 0001 §15's one frame-time pass) ------------------------------
// Chrome's trace (devtools.timeline: the tasks, their EventSource dispatches, animation frames, style / layout / paint, GC) and V8's
// sampling profiler (200 µs) over the same seconds of the stub's dashboard. The profiler's samples are put in classes by their stack:
// the /live handler (its JSON.parse, the adapter's rows, the store's append), the frame loop's publish and clock, the views' draw
// (the canvas calls, the plot view's own code (uPlot and its data), data-ui's reads), GC, native (style, layout, paint), idle.
const CANVAS = new Set(['stroke', 'fill', 'lineTo', 'moveTo', 'beginPath', 'closePath', 'clearRect', 'fillRect', 'strokeRect', 'rect', 'arc', 'fillText',
  'strokeText', 'measureText', 'drawImage', 'save', 'restore', 'setTransform', 'translate', 'scale', 'rotate', 'clip', 'setLineDash', 'getImageData', 'putImageData',
  'createLinearGradient', 'bezierCurveTo', 'quadraticCurveTo', 'roundRect']);
// cfg.maps (optional): a data-ui tree whose dist/ holds the .map files of the build that was vendored (vendor/data-ui/VERSION
// names its commit); a minified frame is then named by its source file and line
function sourceMapper(dir) {
  if (!dir) return () => null;
  const { SourceMap } = require('module');
  const fs = require('fs'), path = require('path');
  const maps = new Map();
  return (url, line, col) => {
    const m = /\/vendor\/data-ui\/(.+\.js)$/.exec(url || '');
    if (!m || line < 0) return null;
    if (!maps.has(m[1])) { try { maps.set(m[1], new SourceMap(JSON.parse(fs.readFileSync(path.join(dir, 'dist', `${m[1]}.map`), 'utf8')))); } catch (_) { maps.set(m[1], null); } }
    const sm = maps.get(m[1]);
    const e = sm && sm.findEntry(line, col);
    return e && e.originalSource ? `${e.originalSource.replace(/^.*\/(packages|node_modules)\//, '$1/')}:${e.originalLine + 1}${e.name ? ` ${e.name}` : ''}` : null;
  };
}
function profileClasses(cpu, mapper = () => null) {
  const byId = new Map(cpu.nodes.map((n) => [n.id, n]));
  const parent = new Map();
  for (const n of cpu.nodes) for (const c of n.children || []) parent.set(c, n.id);
  const ms = {};
  const add = (k, us) => { ms[k] = (ms[k] || 0) + us / 1000; };
  const fns = new Map();
  for (let i = 0; i < cpu.samples.length; i += 1) {
    const us = cpu.timeDeltas[i + 1] ?? 0;   // a sample's time is the gap to the next one
    const leaf = byId.get(cpu.samples[i]);
    if (!leaf) continue;
    const name = leaf.callFrame.functionName, url = leaf.callFrame.url || '';
    if (name === '(idle)') { add('idle', us); continue; }
    if (name === '(program)') { add('native: style, layout, paint, parse (non-JS)', us); continue; }
    if (name === '(garbage collector)') { add('GC', us); continue; }
    const stack = [];
    for (let id = leaf.id; id !== undefined; id = parent.get(id)) { const n = byId.get(id); if (n) stack.push(n.callFrame); }
    const has = (pred) => stack.some(pred);
    const inFeed = has((f) => (f.url || '').endsWith('/dx/feed.js'));
    let k;
    if (inFeed) k = url === '' && name === 'parse' ? '/live handler: JSON.parse' : url.endsWith('/data-ui.js') ? '/live handler: store append' : '/live handler: adapter rows';
    else if (url === '' && CANVAS.has(name)) k = 'draw: canvas calls';
    else if (url.endsWith('/views/live.js')) k = 'draw: plot view (uPlot, its data)';
    else if (has((f) => f.functionName === '_draw')) k = "draw: data-ui's reads and models";
    else if (has((f) => f.functionName === '_publish' || f.functionName === '_sync')) k = 'frame loop: publish, clock';
    else if (url.endsWith('/data-ui.js') || url.includes('/vendor/data-ui/')) k = 'data-ui: other';
    else k = 'other JS';
    add(k, us);
    // where: a JS leaf by its source line; a native leaf (a canvas call, getBoundingClientRect, JSON.parse) by the line that called it
    const where = (f) => mapper(f.url, f.lineNumber, f.columnNumber) || `${f.functionName || '(anonymous)'} ${(f.url || '').split('/').slice(-2).join('/')}:${f.lineNumber + 1}`;
    const caller = stack.find((f, i) => i > 0 && f.url);
    const fk = url ? where(leaf.callFrame) : `${name} ← ${caller ? where(caller) : '(no JS caller)'}`;
    fns.set(fk, (fns.get(fk) || 0) + us / 1000);
  }
  const top = [...fns.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25).map(([f, t]) => ({ fn: f, ms: r1(t) }));
  return { ms: Object.fromEntries(Object.entries(ms).map(([k, v]) => [k, r1(v)])), top };
}
function traceSplit(events, seconds) {
  const names = new Map();
  for (const e of events) if (e.ph === 'M' && e.name === 'thread_name') names.set(`${e.pid}:${e.tid}`, e.args && e.args.name);
  // the page's main thread: the CrRendererMain with the most animation frames
  const count = new Map();
  for (const e of events) if (e.name === 'FireAnimationFrame') { const k = `${e.pid}:${e.tid}`; count.set(k, (count.get(k) || 0) + 1); }
  const main = [...count.entries()].filter(([k]) => names.get(k) === 'CrRendererMain').sort((a, b) => b[1] - a[1])[0];
  if (!main) return null;
  const [pid, tid] = main[0].split(':').map(Number);
  const on = events.filter((e) => e.pid === pid && e.tid === tid && e.ph === 'X' && typeof e.dur === 'number');
  const sum = (pred) => r1(on.filter(pred).reduce((a, e) => a + e.dur, 0) / 1000 / seconds);
  const LIVE_TYPES = new Set(['samples', 'snapshot', 'upsert', 'remove']);
  const render = new Set(['UpdateLayoutTree', 'Layout', 'PrePaint', 'Paint', 'Layerize', 'Commit', 'UpdateLayer', 'CompositeLayers', 'IntersectionObserverController::computeIntersections']);
  return {
    per_second_ms: {
      'tasks (all)': sum((e) => e.name === 'RunTask' || e.name === 'ThreadControllerImpl::RunTask'),
      '/live dispatch (EventSource handlers)': sum((e) => e.name === 'EventDispatch' && e.args && e.args.data && LIVE_TYPES.has(e.args.data.type)),
      'animation frame callbacks (the frame loop, draws)': sum((e) => e.name === 'FireAnimationFrame'),
      'style, layout, paint, commit': sum((e) => render.has(e.name)),
      'GC (minor + major)': sum((e) => e.name === 'MinorGC' || e.name === 'MajorGC' || e.name === 'V8.GC_SCAVENGER' || e.name === 'V8.GC_MARK_COMPACTOR'),
      timers: sum((e) => e.name === 'TimerFire'),
    },
    live_events_s: r1(on.filter((e) => e.name === 'EventDispatch' && e.args && e.args.data && LIVE_TYPES.has(e.args.data.type)).length / seconds),
    animation_frames_s: r1(on.filter((e) => e.name === 'FireAnimationFrame').length / seconds),
    // what the main thread spent its time in, by trace event name (nested events counted in each), and the dispatched event types
    names_top: Object.entries(on.reduce((m, e) => { m[e.name] = (m[e.name] || 0) + e.dur; return m; }, {})).sort((a, b) => b[1] - a[1]).slice(0, 25).map(([n, us]) => [n, r1(us / 1000 / seconds)]),
    dispatch_types: Object.entries(on.filter((e) => e.name === 'EventDispatch').reduce((m, e) => { const t = (e.args && e.args.data && e.args.data.type) || '?'; m[t] = (m[t] || 0) + 1; return m; }, {})).sort((a, b) => b[1] - a[1]).slice(0, 12),
  };
}
async function profile(browser, base, seconds) {
  const page = await newPage(browser);
  await page.goto(`${base}${DX}?go=dashboards&board=bench`);
  await ready(page);
  await page.waitForFunction(() => { const d = document.querySelector('data-dashboard.dx-board'); return d && d.querySelectorAll('[data-points]').length > 0; }, null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(3000);
  const cdp = await page.context().newCDPSession(page);
  const events = [];
  cdp.on('Tracing.dataCollected', (e) => { for (const x of e.value) events.push(x); });
  const complete = new Promise((r) => cdp.once('Tracing.tracingComplete', r));
  await cdp.send('Profiler.enable');
  await cdp.send('Profiler.setSamplingInterval', { interval: 200 });
  await cdp.send('Tracing.start', { transferMode: 'ReportEvents', traceConfig: { includedCategories: ['devtools.timeline', 'disabled-by-default-devtools.timeline', 'toplevel', 'v8', 'blink.user_timing'] } });
  await cdp.send('Profiler.start');
  const frames = page.evaluate(async (s) => {
    const { liveStats } = await import('/vendor/data-ui/data-ui.js');
    const ts = [];
    let rec = true;
    const tick = (t) => { if (rec) { ts.push(t); requestAnimationFrame(tick); } };
    requestAnimationFrame(tick);
    const s0 = liveStats().length ? liveStats().at(-1).frame : 0;
    await new Promise((r) => setTimeout(r, s * 1000));
    rec = false;
    const loop = liveStats().filter((x) => x.frame > s0);
    return { gaps: ts.slice(1).map((t, i) => t - ts[i]), loop: loop.map((x) => x.ms), draws: loop.map((x) => x.draws) };
  }, seconds);
  await page.waitForTimeout(seconds * 1000);
  const { profile: cpu } = await cdp.send('Profiler.stop');
  await cdp.send('Tracing.end');
  await complete;
  const f = await frames;
  await page.context().close();
  const classes = profileClasses(cpu, sourceMapper(cfg.maps));
  const wall = seconds * 1000;
  const share = Object.fromEntries(Object.entries(classes.ms).map(([k, v]) => [k, { ms_s: r1(v / seconds), pct: r1((100 * v) / wall) }]));
  return { seconds, frames: { n: f.gaps.length, p50: r1(pct(f.gaps, 50)), p95: r1(pct(f.gaps, 95)), dropped: f.gaps.filter((g) => g > 25).length, loop_p95: r1(pct(f.loop, 95)), draws_per_frame_p50: pct(f.draws, 50) },
    cpu: share, top: classes.top, trace: traceSplit(events, seconds), trace_events: events.length };
}

(async () => {
  const args = cfg.software ? BROWSER_ARGS : [...BROWSER_ARGS, ...GPU_ARGS];
  const browser = await chromium.launch({ executablePath: CHROME, headless: true, args });
  const out = { phase: cfg.phase, base: cfg.base, started: new Date().toISOString() };
  try {
    const probe = await newPage(browser);
    out.renderer = await probe.evaluate(() => { const gl = document.createElement('canvas').getContext('webgl2') || document.createElement('canvas').getContext('webgl'); if (!gl) return null; const ext = gl.getExtension('WEBGL_debug_renderer_info'); return String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER)); }).catch(() => null);
    out.renderer_kind = out.renderer ? (SOFTWARE.test(out.renderer) ? 'software' : 'gpu') : 'none';
    out.browser = `chrome ${browser.version()}`;
    await probe.context().close();
    out.calibration_ms = [r1(await calibrate(browser))];
    if (cfg.phase === 'fixture') {
      out.first_paint = await firstPaint(browser, cfg.base, cfg.loads || 5);
      out.idle = await idleTraffic(browser, cfg.base, cfg.idleS || 30);
      const sl = await scrubAndLive(browser, cfg.base, cfg.board, cfg.seeks || 30, cfg.lives || 10);
      out.scrub = sl.scrub; out.back = sl.back; out.state_span_s = sl.span_s;
      out.ingest = await ingestFixture(browser, cfg.base, cfg.board, cfg.ingestS || 20);
    } else if (cfg.phase === 'sweep') {
      out.sweep = await sweep(browser, cfg.base, cfg.seconds || 8);
    } else if (cfg.phase === 'heap') {
      out.heap = await heap(browser, cfg.base, cfg.capMB || 64, cfg.seconds || 90);
    } else if (cfg.phase === 'profile') {
      out.profile = await profile(browser, cfg.base, cfg.seconds || 6);
    }
    out.calibration_ms.push(r1(await calibrate(browser)));
  } catch (err) {
    out.failed = String(err && err.stack || err).slice(0, 600);
  } finally {
    out.errors = errors.slice(0, 20);
    out.finished = new Date().toISOString();
    await browser.close().catch(() => {});
  }
  process.stdout.write(JSON.stringify(out) + '\n');
})();
