#!/usr/bin/env node
// The browser half of tools/bench_robotdx.py: drives the RobotDx page (io_diagnostics/web/dx/) in headless Chrome and prints ONE
// JSON object on stdout. Every number comes from the browser (performance.now, requestAnimationFrame, CDP). It never starts a
// host: tools/bench_robotdx.py starts a fixture host (tools/dx_demo.py: fixture plugins only) or the test-only stub
// (bench/robotdx_stub.py: synthetic, no device) and hands this script its URL.
//
//   node bench/robotdx_driver.js '{"phase": "fixture", "base": "http://127.0.0.1:5600", "board": "host/usb1/port-1-3/usb-1-3/can/1"}'
//   node bench/robotdx_driver.js '{"phase": "sweep", "base": "http://127.0.0.1:5650", "seconds": 8}'
//   node bench/robotdx_driver.js '{"phase": "heap", "base": "http://127.0.0.1:5650", "capMB": 64, "seconds": 90}'
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
const DX = '/dx/index.html';

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
      const done = () => { const el = document.querySelector('#dx-tree'); return el && el.model && el.model.rows.length >= 10 && el.querySelector('tbody tr[data-id]'); };
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
      if (ds) batches.push({ at: performance.now(), newest: ds.end });
    };
    const onDraw = (e) => { if (e.target.id === 'dx-detail-signals' && e.detail.following) draws.push({ at: performance.now(), t: e.detail.t }); };
    if (es) es.addEventListener('samples', onSamples);
    document.addEventListener('data-views-live', onDraw, true);
    await new Promise((r) => setTimeout(r, seconds * 1000));
    if (es) es.removeEventListener('samples', onSamples);
    document.removeEventListener('data-views-live', onDraw, true);
    const lat = [];
    for (const b of batches) { const d = draws.find((x) => x.at >= b.at && x.t >= b.newest - 1e-9); if (d) lat.push(d.at - b.at); }
    return { lat, batches: batches.length, mode: window.robotdx.feed.mode };
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
    const onSamples = (m) => {
      const got = performance.timeOrigin + performance.now();
      const path = (/"path":"([^"]+)"/.exec(m.data) || [])[1];
      const now = Number((/"now":([0-9.eE+-]+)/.exec(m.data) || [])[1]);
      const ds = path && window.robotdx.feed._sets.get(`samples:${path}`);
      if (!ds || !Number.isFinite(now)) return;
      // the stub's `now` is its wall clock at emit, the clock the browser reads too (one machine)
      batches.push({ emit: now * 1000, got, newest: ds.end, n: (m.data.match(/\[\d+\.\d+,/g) || []).length, path });
    };
    const onDraw = (e) => { if (e.detail.following && e.target.closest('data-dashboard')) draws.push({ at: performance.timeOrigin + performance.now(), t: e.detail.t, id: e.target.id }); };
    es.addEventListener('samples', onSamples);
    document.addEventListener('data-views-live', onDraw, true);
    const s0 = liveStats().length ? liveStats().at(-1).frame : 0;
    await new Promise((r) => setTimeout(r, seconds * 1000));
    rec = false;
    po.disconnect();
    es.removeEventListener('samples', onSamples);
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
    return { gaps, loop, longs, fromEmit, fromGot, samples_s: samples / seconds, panels, frames: frames.length, mode: window.robotdx.feed.mode };
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
