#!/usr/bin/env node
// Acceptance for the RobotDx page on data-ui (io_diagnostics/web/dx/, docs/peps/0001-robotdx-on-data-ui.md §12), in the REAL page on a
// fixture host, headless Chrome. Written before the page's code (the lane's rule); every claim is measured (getBoundingClientRect,
// performance.now), never "the element exists":
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_dx_page.js
//
// It starts `cli.py serve --fixture F1 --plugins <copy of tests/fixtures/plugins-state> --port 0 --no-udev` (entity_harness.js: a free
// port, scratch state and config, stopped by its process group) and refuses to go on unless only the two fixture plugins loaded.
//   explorer   the tree holds every entity the host reports (its own /api/v1/state is the oracle), nested; a click selects (URL + Back);
//              the selected entity's Diagnostics, Configuration and State show the host's own /api/v1/node and /values answers
//   live       State streams at 10 Hz once its heartbeat runs; the clock follows (t = the newest data) and the time bar's thumb sits at
//              its right end
//   rewind     scrubbing the time bar back pauses at the scrubbed t; the State card shows the host's value AT t (latest-at, from the
//              test's own /values and /history reads) and holds it while data keeps arriving; the plot's playhead is at t; one click on
//              LIVE follows again within a frame or two
//   dashboards a starter opens and draws; a new one is made, gets a panel, is arranged (a measured resize), survives a reload with the
//              same panel rectangles, is saved as a file (the download parses back), and comes back from a picked and a dropped .base
//   rail       its three styles (Settings select) measured: 48 / 72 / 240 px, labels hidden / under the icon / beside it; the wide style's
//              edge drags, is kept on reload, double-click resets, Ctrl+Alt+R folds and opens it
//   tree pane  its edge drags, is kept on reload, double-click resets, the fold button hides it and brings it back at its width
//   requests   every request of every page is on the allow-list of §12.2 (GET the page's files and the read endpoints; POST only
//              /api/v1/watch {path} while State is on screen); the heartbeat stops when State leaves the screen or the tab hides
//   kpis       K2 bytes / requests per second idle (beside today's page on the same host), K5 scrub to drawn, K7 cold load (§8)
//   phase 3c   (§15) the snapshot's `emitted` is not kept as a state section; the tree scrubbed back before the page opened is today's
//              rows with the health the host kept (its own /history/tree read by the test is the oracle), and on a host without
//              /history/tree it is empty with the phase 3 note; State at the watch's rate stops the /values poll (the fixture host,
//              and the test-only stub at the host's cadence, at an older host's tick and with a slow watch); the Diagnostics starter
//              is the host's GET /api/v1/checks, worst first, with no per-entity /node; the page's address is /dx/
//   rework     (§16, Zach 2026-10-28) the tree is the classic page's own (web/tree/v6.js: the same rows, labels, depths, hidden rows and
//              counts as the classic page on the same host; collapse / expand all, search, Tree | Table) and still rewinds; each entity
//              has tabs (Overview, Diagnostics, State, Configuration, Log, Utilities; ?page=, Back, remembered per kind, each rewinds);
//              a bottom-right picker switches the playback bar between today's, Rerun (simple) and Foxglove, live, remembered
// Exits 1 on any failure.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, BOARD, fs, os, path } = H;

const DX = '/dx/';   // 4e2ec56: a path ending in / serves its index.html (PEP 0001 §15)
const BROWSER_ARGS = ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows'];
const GPU_ARGS = ['--use-angle=vulkan', '--enable-features=Vulkan', '--enable-gpu', '--ignore-gpu-blocklist'];
const RENDERER_JS = () => { const gl = document.createElement('canvas').getContext('webgl2') || document.createElement('canvas').getContext('webgl'); if (!gl) return null; const ext = gl.getExtension('WEBGL_debug_renderer_info'); return String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER)); };
const SOFTWARE = /swiftshader|llvmpipe|softpipe|software|basic render/i;
const IDLE_S = Number(process.env.DX_IDLE_S || 15);
const RAIL_WIDTHS = { collapsed: 48, expanded: 72, wide: 240 };
const STARTERS_MIN = 2;

// ---- the request allow-list (PEP 0001 §12.2, §14) -------------------------------------------------------------------------------
// the classic page's own tree, reused (PEP 0001 §16): its scripts and styles are GETs of the same web root
const GET_OK = [/^\/dx\//, /^\/vendor\/data-ui\//, /^\/usb\.js$/, /^\/(tokens\.css|summary\.js|status-dot\.js|visibility\.js|skins\/[a-z0-9-]+\.css|tree\/v6\.(js|css)|view\/(core|composites)\.js|view\/structure\/p[0-9]+\.js)$/, /^\/api\/v1\/(state|node|values|history|history\/tree|activity|events|settings|health|live|checks)(\?|$)/,
  /^\/api\/v1\/dashboards(\/[^/?]+\.base)?$/];
const DASHBOARD_FILE = /^\/api\/v1\/dashboards\/[^/?]+\.base$/;
// the dashboard writes (PUT, DELETE) are allowed only while the saving checks run, against this fixture host's own config dir
const phase = { saving: false };
function onList(request) {
  const url = request.url;
  if (request.method === 'GET') return GET_OK.some((re) => re.test(url));
  if (request.method === 'POST' && url === '/api/v1/watch') {
    try { const body = JSON.parse(request.body || ''); return typeof body.path === 'string' && body.path.length > 0 && Object.keys(body).length === 1; } catch (_) { return false; }
  }
  if ((request.method === 'PUT' || request.method === 'DELETE') && DASHBOARD_FILE.test(url)) return request.saving === true;
  return false;
}
function recorder(page, base) {
  const seen = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.protocol === 'data:' || u.protocol === 'blob:') return;
    seen.push({ method: r.method(), url: u.origin === base ? `${u.pathname}${u.search}` : r.url(), body: r.postData(), headers: r.headers(), at: Date.now(), saving: phase.saving });
  });
  return seen;
}
// every byte the page receives from /api/, streams included (CDP Network.dataReceived), counted from before its first navigation:
// a Server-Sent Events stream opened at load (/live, /events) counts by what it delivers, not as one request
async function meter(page, base) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  const urls = new Map();
  const tally = { requests: 0, bytes: 0 };
  cdp.on('Network.requestWillBeSent', (e) => {
    const u = new URL(e.request.url);
    if (u.origin === base && u.pathname.startsWith('/api/')) { urls.set(e.requestId, u.pathname); tally.requests += 1; }
  });
  cdp.on('Network.dataReceived', (e) => { if (urls.has(e.requestId)) tally.bytes += e.dataLength; });
  return tally;
}
// bytes and requests of /api/ per second over the next `seconds`
async function traffic(tally, page, seconds) {
  const r0 = tally.requests, b0 = tally.bytes;
  await page.waitForTimeout(seconds * 1000);
  const bytes = tally.bytes - b0;
  return { rps: +((tally.requests - r0) / seconds).toFixed(2), kbps: +(bytes / 1024 / seconds).toFixed(1), kb_per_min: Math.round(bytes / 1024 / seconds * 60) };
}

// ---- the page --------------------------------------------------------------------------------------------------------------------
const ready = (page) => page.waitForFunction(() => window.robotdx && window.robotdx.clock && document.querySelector('#dx-main'), null, { timeout: 30000 });
const dest = (page) => page.evaluate(() => document.querySelector('#dx-main').dataset.dest);
const rect = (page, selector) => page.evaluate((s) => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; }, selector);
const clockState = (page) => page.evaluate(() => { const c = window.robotdx.clock; return { following: c.following, t: c.t, liveEnd: c.liveEnd, liveStart: c.liveStart, start: c.start, end: c.end }; });
// the tree's rows as the classic tree style draws them (web/tree/v6.js, PEP 0001 §16): every [role=treeitem] in the DOM, in order,
// with its row key (the entity path), its label, its depth and the health SHAPE at its right (none for OK: rule 3)
const TREE_ROWS = (sel) => [...document.querySelectorAll(`${sel} [role=treeitem][data-t6-path]`)].map((r) => {
  // WHY both spellings (orchestrator fix): the classic tree draws `svg.t6-shape.t6-<level>` since 854c0f0 (summary.js), the
  // older `svg.shape.lv-<level>` before it; this read only the old one, so a drawn shape read as no shape
  const mark = r.querySelector('.t6-st svg.t6-shape, .t6-mark svg.t6-shape, .t6-st svg.shape, .t6-mark svg.shape');
  const cls = mark ? [...mark.classList].find((c) => c.startsWith('lv-') || (c.startsWith('t6-') && c !== 't6-shape')) : null;
  const level = mark ? (cls || '').replace(/^(lv|t6)-/, '') : null;
  return { id: r.getAttribute('data-t6-path'), name: ((r.querySelector('.t6-name') || {}).textContent || '').trim(), depth: Number(r.getAttribute('aria-level')) - 1,
    level, open: r.getAttribute('aria-expanded'), selected: r.getAttribute('aria-selected') === 'true' };
});
const treeRows = (page, sel = '#dx-tree') => page.evaluate(`(${TREE_ROWS})(${JSON.stringify(sel)})`);
const kpiCards = (page, scope) => page.evaluate((s) => [...document.querySelectorAll(`${s} .dv-kpi-card`)].map((c) => {
  const num = c.querySelector('.dv-kpi-num');
  const exact = num ? (num.dataset.exact || (num.getAttribute('title') || '').split(' ')[0] || num.textContent) : '';
  return { label: (c.querySelector('.dv-kpi-label') || {}).textContent || '', shown: num ? num.textContent : '', exact: Number(exact) };
}), scope);

function flatten(node, out = []) { out.push(node); for (const child of node.children || []) flatten(child, out); return out; }
// the host's own value of `key` at t: the newest point received at or before t, from its /history State series and its /values ring
function hostValueAt(history, values, key, t) {
  let best = null;
  const consider = (when, value) => { if (when <= t && (!best || when >= best.when)) best = { when, value }; };
  for (const s of history.statuses || []) for (const m of s.measurements || []) if (m.key === key) for (const [when, value] of m.points) consider(when, value);
  for (const g of values.groups || []) for (const r of g.rows || []) if (r.key === key) for (const [when, value] of r.points) consider(when, value);
  return best;
}

// Scrub the time bar from its right end (following) to 60 % of the entity's State span, and measure K5: the clock's last seek of the
// drag to the last live draw of a detail panel at that t, with the test's own listeners. The target lies inside the entity's
// State (it starts when this host started; the event backlog reaches further back).
async function scrubBack(page) {
  const stateSpan = await page.evaluate((p) => { const d = window.robotdx.feed.dataset('samples', p); return { start: d.start, end: d.end }; }, BOARD);
  const span = await clockState(page);
  const target = stateSpan.start + 0.6 * (stateSpan.end - stateSpan.start);
  const trackBefore = await rect(page, '#dx-time .dv-pb-track');
  const xTarget = trackBefore.x + trackBefore.w * ((target - span.start) / (span.end - span.start));
  await page.evaluate(() => {
    window.__k5 = { times: [], draws: [] };
    window.robotdx.clock.addEventListener('time', (e) => window.__k5.times.push({ at: performance.now(), t: e.detail.t, source: e.detail.source }));
    document.addEventListener('data-views-live', (e) => window.__k5.draws.push({ at: performance.now(), t: e.detail.t, following: e.detail.following, id: e.target.id }), true);
  });
  await page.mouse.move(trackBefore.x + trackBefore.w - 1, trackBefore.y + trackBefore.h / 2);
  await page.mouse.down();
  await page.mouse.move(xTarget, trackBefore.y + trackBefore.h / 2, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(500);
  // each panel's FIRST draw at t after the seek: a keyed panel draws again at the same t whenever a new version arrives (even
  // one after t), and those later draws are not the scrub's
  const k5 = await page.evaluate(() => {
    const seeks = window.__k5.times.filter((x) => x.source !== 'live' && x.source !== 'play');
    const last = seeks[seeks.length - 1];
    if (!last) return null;
    const first = new Map(), lastDraw = new Map();
    let redraws = 0;
    for (const d of window.__k5.draws) {
      if (d.t !== last.t || d.following || d.at < last.at || !d.id.startsWith('dx-detail-')) continue;
      if (!first.has(d.id)) first.set(d.id, d.at); else redraws += 1;
      lastDraw.set(d.id, d.at);
    }
    // two readings (data-ui 3289ab5): the first draw of each panel at t (879cade's), and every draw at t in the 500 ms after the
    // seek (phase 3's first reading, which a paused keyed panel's redraws for news after t inflated); with the fix they agree
    return first.size ? { ms: Math.max(...first.values()) - last.at, every_ms: Math.max(...lastDraw.values()) - last.at, redraws, panels: [...first.keys()] } : { ms: null, every_ms: null, redraws, panels: [] };
  });
  return { stateSpan, span, target, trackBefore, xTarget, k5 };
}

async function dragBy(page, handleRect, dx, dy = 0, y = null) {
  const sx = handleRect.x + handleRect.w / 2, sy = y ?? handleRect.y + handleRect.h / 2;
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(sx + dx, sy + dy, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(250);
}

// ---- phase 3c helpers (PEP 0001 §15) -------------------------------------------------------------------------------------------
// The host's first /live event, read by the test itself (not the page): the oracle that `emitted` is there to be left out.
function firstLiveEvent(base) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const req = require('http').get(`${base}/api/v1/live`, (res) => {
      let buf = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => {
        buf += chunk;
        const at = buf.indexOf('event: snapshot');
        const end = at >= 0 ? buf.indexOf('\n\n', at) : -1;
        if (end < 0) return;
        const line = buf.slice(at, end).split('\n').find((l) => l.startsWith('data: '));
        req.destroy();
        try { finish(JSON.parse(line.slice(6))); } catch (_) { finish(null); }
      });
    });
    req.on('error', () => finish(null));
    setTimeout(() => { req.destroy(); finish(null); }, 8000);
  });
}

// The test-only stub (bench/robotdx_stub.py: synthetic State, no plugin, no device) on a free port of 5600-5699, stopped by its group.
const STUB = path.join(H.ROOT, 'bench', 'robotdx_stub.py');
async function freePort(first = 5600, last = 5699) {
  const net = require('net');
  for (let port = first; port <= last; port += 1) {
    const ok = await new Promise((res) => { const s = net.createServer(); s.once('error', () => res(false)); s.listen(port, '127.0.0.1', () => s.close(() => res(true))); });
    if (ok) return port;
  }
  throw new Error('no free port in 5600-5699');
}
async function startStub(args) {
  const port = await freePort();
  const child = require('child_process').spawn('/usr/bin/python3', [STUB, '--port', String(port), ...args], { cwd: H.ROOT, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let out = '';
    const timer = setTimeout(() => { H.stopServer(child); reject(new Error(`the stub did not start: ${out}`)); }, 20000);
    const on = (chunk) => { out += chunk; const m = out.match(/bench stub on (http:\/\/\S+?)\/ /); if (m) { clearTimeout(timer); resolve({ child, base: m[1] }); } };
    child.stdout.on('data', on);
    child.stderr.on('data', on);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`the stub exited (${code}): ${out}`)); });
  });
}
// Explore on the stub's entity with its State on screen: once its watch runs (the heartbeat went), `settle` waits, then for `seconds`
// the /values polls of the entity and the gaps between the frames that brought it new State (its samples dataset's `change` events).
async function stubCadence(browser, stubArgs, { settle, seconds = 4 }, sink) {
  const stub = await startStub(stubArgs);
  let ctx = null;
  try {
    // the page is opened once the synthetic entity has State (the case of State that comes later is its own check)
    await H.waitFor(async () => { const v = await H.getJson(stub.base, '/api/v1/values?path=stub%2F0&since=0'); return (v.groups || []).some((g) => (g.rows || []).some((r) => (r.points || []).length)); }, 15000, 200);
    ctx = await H.newContext(browser, 'light');
    const p = await ctx.newPage();
    const seen = recorder(p, stub.base);
    sink.requests.push(seen);
    const errs = H.watchErrors(p, stub.base);
    await p.goto(`${stub.base}${DX}?go=explore&sel=stub/0`);
    await ready(p);
    const beat = await H.waitFor(() => seen.some((r) => r.method === 'POST' && r.url === '/api/v1/watch'), 10000);
    const settled = await settle(p);
    const from = Date.now();
    const m = await p.evaluate(async (s) => {
      const ds = window.robotdx.feed.dataset('samples', 'stub/0');
      const at = [];
      const on = () => at.push(performance.now());
      ds.addEventListener('change', on);
      await new Promise((r) => setTimeout(r, s * 1000));
      ds.removeEventListener('change', on);
      const gaps = at.slice(1).map((t, i) => t - at[i]);
      const e = window.robotdx.feed.entity('stub/0');
      return { frames: at.length, max_gap_ms: gaps.length ? Math.round(Math.max(...gaps)) : null, keepsUp: e ? e.feedKeepsUp : null };
    }, seconds);
    const polls = seen.filter((r) => r.at >= from && r.url.startsWith(`/api/v1/values?path=${encodeURIComponent('stub/0')}`)).length;
    sink.errors.push(...errs.errors);
    return { beat: Boolean(beat), settled: Boolean(settled), ...m, polls, polls_s: +(polls / seconds).toFixed(2) };
  } finally {
    if (ctx) await ctx.close().catch(() => {});
    H.stopServer(stub.child);
  }
}
const keepsUpSoon = (ms) => (p) => H.waitFor(() => p.evaluate(() => { const e = window.robotdx.feed.entity('stub/0'); return Boolean(e && e.feedKeepsUp); }), ms);

(async () => {
  let main;
  // WHY these flags: data-ui's bench (bench/runner.py BROWSER_ARGS) runs headless without timer and renderer backgrounding, so
  // frame times are the page's and not a throttled background tab's
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: BROWSER_ARGS });
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'dx-page-'));
  const everyRequest = [];
  try {
    main = await H.fixtureHost('F1');
    const { base } = main;
    const plugins = await H.getJson(base, '/api/v1/plugins');
    check('only the two fixture plugins are loaded', plugins.plugins.map((p) => p.name).sort().join() === 'state_camera,state_joint');

    // ---- K2 before: today's page on the same host, idle, for the comparison ------------------------------------------------------
    const oldContext = await H.newContext(browser, 'light');
    const oldPage = await oldContext.newPage();
    const oldMeter = await meter(oldPage, base);
    await oldPage.route('**/*', (route) => (route.request().method() === 'GET' ? route.continue() : route.abort()));  // GET only, as §11
    await oldPage.goto(`${base}/`);
    await oldPage.waitForTimeout(3000);
    // the classic page's own tree, as it draws it with its defaults (structure p6, tree style v6): the oracle for the new page's tree
    const classicTree = await treeRows(oldPage, '#tree-host');
    const classicHidden = await oldPage.evaluate(() => [...document.querySelectorAll('#tree-host .t6-bundle')].map((b) => b.textContent.trim()));
    const before = await traffic(oldMeter, oldPage, IDLE_S);
    await oldContext.close();

    // ---- K7: cold load to the tree drawn --------------------------------------------------------------------------------------------
    const context = await H.newContext(browser, 'light', { acceptDownloads: true });
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    everyRequest.push(recorder(page, base));
    const pageMeter = await meter(page, base);
    // measured in the page from its navigation start (performance.now()'s origin), by a 10 ms timer started before any script
    // runs: the first moment the tree has its rows drawn (a raf poll stalls once nothing animates, and a MutationObserver can
    // fire before the element's model is set)
    await page.addInitScript(() => {
      window.__k7 = null;
      const done = () => document.querySelectorAll('#dx-tree [role=treeitem][data-t6-path]').length >= 5;
      const poll = () => { if (done()) window.__k7 = performance.now(); else if (performance.now() < 15000) setTimeout(poll, 10); };
      poll();
    });
    await page.goto(`${base}${DX}`);
    await ready(page);
    const trackAtLoad = await rect(page, '#dx-time .dv-pb-track');
    const drawn = await H.waitFor(() => page.evaluate(() => window.__k7), 15000, 100);
    const coldMs = drawn ? Math.round(drawn) : null;
    check('K7: cold load to the tree drawn ≤ 1.5 s (from navigation start)', coldMs !== null && coldMs <= 1500, `${coldMs} ms`);
    check('the page opens on Explore', (await dest(page)) === 'explore', await dest(page));
    // the live feed (GET /api/v1/live, PEP 0001 §14) in place of the /state poll: one stream, no /state request
    await page.waitForTimeout(5000);
    // the time bar's track stays put while the connection line beside it fills in and its "last data" age changes length (a
    // scrub started in that second pressed past the track's end; PEP 0001 §15.1)
    const trackLater = await rect(page, '#dx-time .dv-pb-track');
    const statusText = await page.evaluate(() => (document.querySelector('#dx-status') || {}).textContent || '');
    check('the time bar\'s track does not move or resize as the connection line fills in', trackAtLoad && trackLater && Math.abs(trackAtLoad.x - trackLater.x) <= 1 && Math.abs(trackAtLoad.w - trackLater.w) <= 1 && statusText.length > 0,
      { atLoad: trackAtLoad, later: trackLater, status: statusText.slice(0, 80) });
    const firstReqs = everyRequest[0];
    const liveFeed = { mode: await page.evaluate(() => window.robotdx.feed.mode), live: firstReqs.filter((r) => r.method === 'GET' && r.url === '/api/v1/live').length,
      state: firstReqs.filter((r) => r.url === '/api/v1/state').length };
    check('the page reads the host through GET /api/v1/live: one stream, no /state poll', liveFeed.mode === 'live' && liveFeed.live === 1 && liveFeed.state === 0, liveFeed);
    // 5cdb4b9: the snapshot's `emitted` sits beside /state's own keys; the page keeps /state's sections only (PEP 0001 §15)
    const snap = await firstLiveEvent(base);
    const mirror = await page.evaluate(() => { const m = window.robotdx.feed._mirror; return m ? { keys: Object.keys(m.state).sort(), emitted: 'emitted' in m.state } : null; });
    check('the host stamps its snapshot with emitted, and the page does not keep it as a state section', snap && Number.isFinite(snap.emitted) && mirror && !mirror.emitted && mirror.keys.includes('clock') && mirror.keys.includes('summary'),
      { host: snap ? { emitted: snap.emitted, keys: Object.keys(snap).length } : null, mirror });

    // ---- explorer: the tree is the classic page's own tree (web/tree/v6.js over web/view's rows; Zach, 2026-10-28: "please switch back
    // to that old entity tree"; PEP 0001 §16). The oracle is the classic page on the same host, read above
    const state = await H.getJson(base, '/api/v1/state');
    await page.waitForTimeout(1500);
    const rows = await treeRows(page);
    const sameTree = rows.length === classicTree.length && rows.every((r, i) => r.id === classicTree[i].id && r.name === classicTree[i].name && r.depth === classicTree[i].depth);
    check('the tree is the classic page\'s tree: the same rows, labels, depths and order on the same host', rows.length >= 5 && sameTree,
      { dx: rows.map((r) => `${r.depth}:${r.name}`).slice(0, 8), classic: classicTree.map((r) => `${r.depth}:${r.name}`).slice(0, 8), n: [rows.length, classicTree.length] });
    const hiddenRows = await page.evaluate(() => [...document.querySelectorAll('#dx-tree .t6-bundle')].map((b) => b.textContent.trim()));
    check('…with its "Show N hidden" rows as the classic page has them', JSON.stringify(hiddenRows) === JSON.stringify(classicHidden), { dx: hiddenRows, classic: classicHidden });
    const counts = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('#dx-tree [data-t6-count]')].map((c) => [c.getAttribute('data-t6-count'), Number((c.textContent.match(/\d+/) || [0])[0])])));
    const hostCounts = (state.summary && state.summary.counts) || {};
    check('…and its header counts are the host\'s (errors, warnings)', ['error', 'warn'].every((l) => (counts[l] || 0) === (hostCounts[l] || 0)) && (counts.error || 0) + (counts.warn || 0) > 0, { dx: counts, host: hostCounts });
    // the pane's toolbar (the classic Obsidian layout's: Collapse all, Expand all, Show what is hidden) and its search on top
    const nRows = async () => (await treeRows(page)).length;
    await page.click('#dx-tree-collapse');
    await page.waitForTimeout(300);
    const collapsed = await nRows();
    await page.click('#dx-tree-expand');
    await page.waitForTimeout(400);
    const expanded = await treeRows(page);
    check('Collapse all and Expand all fold and open the tree (rows counted)', collapsed < expanded.length && expanded.some((r) => r.id === BOARD), { collapsed, expanded: expanded.length });
    await page.fill('#dx-tree-search', 'ttyACM0');
    await page.waitForTimeout(500);
    const found = await treeRows(page);
    await page.fill('#dx-tree-search', '');
    await page.waitForTimeout(400);
    check('the search on top of the tree keeps the rows that match and the rows above them', found.length >= 1 && found.length < expanded.length, { found: found.map((r) => r.name) });
    // nesting drawn: the board's name starts right of its adapter's (measured, after scrolling it into view)
    const indent = await page.evaluate(([board, adapter]) => {
      const left = (id) => { const n = document.querySelector(`#dx-tree [data-t6-path="${CSS.escape(id)}"] .t6-name`); if (!n) return null; n.scrollIntoView({ block: 'center' }); return n.getBoundingClientRect().left; };
      return { board: left(board), adapter: left(adapter) };
    }, [BOARD, H.ADAPTER]);
    check('the nesting is drawn: the board starts right of its adapter', indent.board !== null && indent.adapter !== null && indent.board - indent.adapter >= 12, indent);
    // Tree | Table: the table stays data-ui's (Zach: "the old entity tree for when you are looking at it as a tree")
    await page.click('#dx-tree-mode [data-mode="table"]');
    const tableRows = await H.waitFor(() => page.evaluate(() => { const t = document.querySelector('#dx-tree-table'); return t && t.offsetParent && t.model && t.model.rows.length ? t.model.rows.length : null; }), 8000);
    await page.click('#dx-tree-mode [data-mode="tree"]');
    await page.waitForTimeout(300);
    check('Tree | Table: the table is data-ui\'s, the tree comes back as it was', tableRows >= 5 && (await nRows()) === expanded.length, { tableRows });

    // a click selects: the URL names it, the detail is that entity's, Back goes to the one before
    const clickRow = async (id) => {
      await page.evaluate((x) => document.querySelector(`#dx-tree [data-t6-path="${CSS.escape(x)}"]`)?.scrollIntoView({ block: 'center' }), id);
      const r = await rect(page, `#dx-tree [data-t6-path="${id.replace(/"/g, '\\"')}"] .t6-name`);
      if (!r) return false;
      await page.mouse.click(r.x + Math.min(40, r.w / 2), r.y + r.h / 2);
      return page.waitForFunction((x) => new URLSearchParams(location.search).get('sel') === x, id, { timeout: 8000 }).then(() => true, () => false);
    };
    check('a click on a tree row selects it (the URL says sel=)', await clickRow(H.ADAPTER));
    check('then another', await clickRow(H.CAMERA));
    await page.goBack();
    const backTo = await page.waitForFunction((x) => new URLSearchParams(location.search).get('sel') === x, H.ADAPTER, { timeout: 8000 }).then(() => true, () => false);
    check('Back goes to the selection before (a history entry each)', backTo, await page.evaluate(() => location.search));
    const marked = await page.evaluate((x) => { const r = document.querySelector(`#dx-tree [data-t6-path="${CSS.escape(x)}"]`); return r ? r.getAttribute('aria-selected') : null; }, H.ADAPTER);
    check('…and the tree marks the selected row', marked === 'true', marked);

    // ---- the tree's past (GET /api/v1/history/tree, PEP 0001 §15): scrubbed back to before the page opened, the tree is today's rows
    // with the health the host kept for each. The oracle is the test's own /history/tree answer, read now; a row is compared where the
    // host's bar holds one level for five bins around t (the page's bins were cut at another moment, so their edges differ)
    const openedAt = await page.evaluate(() => window.robotdx.feed.openedAt);
    const win = (await H.getJson(base, '/api/v1/settings')).history_window_s || 900;
    const bins = Math.min(1000, Math.ceil(win / 2));
    const binText = `${+(win / bins).toFixed(1)} s`;
    const oracle = await H.getJson(base, `/api/v1/history/tree?bins=${bins}`);
    const tPast = openedAt - 6;
    // the tree's own draws (dx-tree-draw {t, following, rows}): it must draw AT the clock's t, not its newest rows
    await page.evaluate(() => { window.__treeDraws = []; document.querySelector('#dx-tree').addEventListener('dx-tree-draw', (e) => window.__treeDraws.push({ t: e.detail.t, following: e.detail.following, rows: e.detail.rows })); });
    await page.evaluate((t) => window.robotdx.clock.seek(t), tPast);
    await page.waitForTimeout(600);
    const pastClock = await clockState(page);
    const treeDraws = await page.evaluate(() => window.__treeDraws);
    const past = await treeRows(page);
    const pastIds = new Set(past.map((r) => r.id));
    const lostRows = expanded.filter((r) => !pastIds.has(r.id)).map((r) => r.id);
    check('scrubbed back, the tree draws at the clock\'s t (paused), not its newest rows', treeDraws.some((d) => d.t === pastClock.t && !d.following) && !treeDraws.some((d) => d.t !== pastClock.t),
      { t: pastClock.t, draws: treeDraws.slice(0, 4) });
    check('scrubbed back before the page opened, the tree is today\'s rows (every row it drew live is there)', Number.isFinite(openedAt) && !pastClock.following && pastClock.t < openedAt && past.length >= 5 && lostRows.length === 0,
      { t: pastClock.t, opened: openedAt, rows: past.length, live: expanded.length, lost: lostRows.slice(0, 5) });
    const bin = Math.floor((tPast - oracle.from) / oracle.bin_s);
    let compared = 0;
    const wrong = [];
    // a leaf row's shape is its own level (an open parent's would be a roll-up); the tree draws a shape for warn, unknown, error only
    const MARKS = new Set(['warn', 'unknown', 'error']);
    const leaves = new Set(past.filter((r, i) => !(past[i + 1] && past[i + 1].depth > r.depth)).map((r) => r.id));
    for (const row of oracle.rows || []) {
      const shown = past.find((r) => r.id === row.path);
      const near = row.bar.slice(Math.max(0, bin - 2), bin + 3);
      if (!shown || !leaves.has(row.path) || near.length < 5 || near.some((x) => x !== near[0])) continue;
      compared += 1;
      const want = MARKS.has(near[0]) ? near[0] : null;
      if ((shown.level ?? null) !== want) wrong.push({ path: row.path, shown: shown.level, host: near[0] });
    }
    check('…each leaf with the shape of the level the host kept for it then (its /history/tree bar, where it holds one level around t)', compared >= 3 && wrong.length === 0, { compared, wrong: wrong.slice(0, 5), bin_s: oracle.bin_s });
    const pastNote = await page.evaluate(() => { const n = document.querySelector('#dx-before'); return n && !n.hidden && n.getBoundingClientRect().height > 0 ? n.textContent : null; });
    check(`…and the note says what the tree is then (today's rows, the host's ${binText} bins)`, Boolean(pastNote) && /today/i.test(pastNote) && pastNote.includes(binText), pastNote);
    await page.evaluate(() => window.robotdx.clock.goLive());

    // ---- the selected entity: Diagnostics, Configuration, State from the host's own answers -----------------------------------------
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(BOARD)}`);
    await ready(page);
    const node = await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(BOARD)}`);
    const shownChecks = await H.waitFor(() => page.evaluate(() => {
      const el = document.querySelector('#dx-detail-diagnostics');
      return el && el.model && el.model.rows.length ? el.model.rows.map((r) => r.name) : null;
    }), 15000);
    check('Diagnostics lists the host\'s checks of the entity', shownChecks && node.checks.every((c) => shownChecks.includes(c.name)) && shownChecks.length === node.checks.length, { shown: shownChecks, host: node.checks.map((c) => c.name) });
    const shownConfig = await H.waitFor(() => page.evaluate(() => {
      const el = document.querySelector('#dx-detail-configuration');
      return el && el.model && el.model.rows.length ? el.model.rows.map((r) => r.key) : null;
    }), 15000);
    check('Configuration lists the host\'s settings of the entity', shownConfig && node.config.every((c) => shownConfig.includes(c.key)), { shown: shownConfig && shownConfig.length, host: node.config.length });

    // ---- the entity's tabs (Zach, 2026-10-28: "on each entity we had these tabs ... I still want that tabbed model on each entity";
    // PEP 0001 §16): the overview first, then the classic page's five, each a dashboard of its own panels; the tab is in the URL
    // (?page=, the classic page's own name for it), each a history entry, and remembered per kind of entity as the classic page does
    const TABS = ['overview', 'diagnostics', 'state', 'config', 'log', 'utilities'];
    const tabBar = await page.evaluate(() => [...document.querySelectorAll('#dx-tabs [data-dx-tab]')].map((b) => ({ id: b.dataset.dxTab, text: b.textContent.trim(), on: b.getAttribute('aria-selected') === 'true' })));
    check('the entity has its tabs: Overview, Diagnostics, State, Configuration, Log, Utilities (the overview first and open)', tabBar.map((t) => t.id).join() === TABS.join() && tabBar[0].on, tabBar);
    const shownPanels = () => page.evaluate(() => [...document.querySelectorAll('#dx-detail .dd-panel')].filter((x) => x.getBoundingClientRect().height > 20).map((x) => x.dataset.panel));
    const tabPanels = {};
    for (const id of TABS.slice(1)) {
      await page.click(`#dx-tabs [data-dx-tab="${id}"]`);
      await page.waitForFunction((x) => new URLSearchParams(location.search).get('page') === x, id, { timeout: 8000 }).catch(() => {});
      await page.waitForFunction((x) => { const b = document.querySelector(`#dx-tabs [data-dx-tab="${x}"]`); const d = document.querySelector('#dx-detail'); return b && b.getAttribute('aria-selected') === 'true' && d && d.dash; }, id, { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(800);
      tabPanels[id] = await shownPanels();
    }
    const WANT = { diagnostics: ['diagnostics', 'health'], state: ['state', 'signals'], config: ['configuration'], log: ['log'], utilities: ['utilities'] };
    check('each tab draws its own panels (measured: on screen, taller than 20 px)', Object.entries(WANT).every(([id, keys]) => keys.every((k) => (tabPanels[id] || []).includes(k))), tabPanels);
    const utilRows = await H.waitFor(() => page.evaluate(() => { const el = document.querySelector('#dx-detail-utilities'); return el && el.model && el.model.rows.length ? el.model.rows.map((r) => r.name) : null; }), 8000);
    check('Utilities lists the host\'s utilities of the entity (read here; run on the classic page)', utilRows && (node.actions || []).every((a) => utilRows.includes(a.name)) && utilRows.length === (node.actions || []).length,
      { shown: utilRows, host: (node.actions || []).map((a) => a.name) });
    const classicLink = await page.evaluate(() => (document.querySelector('#dx-classic') || {}).getAttribute && document.querySelector('#dx-classic').getAttribute('href'));
    check('…and "Open on the classic page" opens the same tab there', classicLink === `/?sel=${encodeURIComponent(BOARD)}&page=utilities`, classicLink);
    await page.goBack();
    const backTab = await page.waitForFunction(() => new URLSearchParams(location.search).get('page') === 'log', null, { timeout: 8000 }).then(() => true, () => false);
    const logShown = await page.waitForFunction(() => { const b = document.querySelector('#dx-tabs [data-dx-tab="log"]'); return b && b.getAttribute('aria-selected') === 'true'; }, null, { timeout: 8000 }).then(() => true, () => false);
    check('Back goes to the tab before (a history entry each)', backTab && logShown, await page.evaluate(() => location.search));
    // remembered per kind: another entity of the same kind opens on the tab last shown for that kind
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(H.BOARD2)}`);
    await ready(page);
    const remembered = await page.waitForFunction(() => { const b = document.querySelector('#dx-tabs [data-dx-tab][aria-selected="true"]'); return b ? b.dataset.dxTab : null; }, null, { timeout: 10000 }).then((h) => h.jsonValue(), () => null);
    check('another board opens on the tab last shown for its kind (Log)', remembered === 'log', remembered);
    // a tab rewinds like the overview: the Diagnostics tab's table draws at a scrubbed t
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(BOARD)}&page=diagnostics`);
    await ready(page);
    await page.waitForFunction(() => { const el = document.querySelector('#dx-detail-diagnostics'); return el && el.model && el.model.rows.length; }, null, { timeout: 15000 }).catch(() => {});
    const tabRewind = await page.evaluate(async () => {
      const draws = [];
      const on = (e) => { if (e.target.id === 'dx-detail-diagnostics') draws.push({ t: e.detail.t, following: e.detail.following }); };
      document.addEventListener('data-views-live', on, true);
      const c = window.robotdx.clock;
      const t = c.liveEnd - 8;
      c.seek(t);
      await new Promise((r) => setTimeout(r, 600));
      document.removeEventListener('data-views-live', on, true);
      const out = { t: c.t, draws };
      c.goLive();
      return out;
    });
    check('a tab rewinds too: the Diagnostics tab\'s table draws at the scrubbed t', tabRewind.draws.some((d) => d.t === tabRewind.t && !d.following), tabRewind);
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(BOARD)}&page=overview`);
    await ready(page);
    await page.waitForFunction(() => { const el = document.querySelector('#dx-detail-diagnostics'); return el && el.model && el.model.rows.length; }, null, { timeout: 15000 }).catch(() => {});

    // ---- live: the heartbeat, 10 Hz State, the clock following ----------------------------------------------------------------------
    const beat = await H.waitFor(() => everyRequest[0].some((r) => r.method === 'POST' && r.url === '/api/v1/watch' && JSON.parse(r.body || '{}').path === BOARD), 8000);
    check('State on screen sends the watch heartbeat for the entity', Boolean(beat));
    const rate = await page.evaluate(async (p) => {
      const ds = window.robotdx.feed.dataset('samples', p);
      const wait = (ms) => new Promise((r) => setTimeout(r, ms));
      for (let i = 0; i < 40 && !(ds && ds.end); i += 1) await wait(250);
      await wait(2500);   // the watch takes a moment to start
      const n0 = ds.rowCount, e0 = ds.end;
      await wait(3000);
      return { rows: ds.rowCount - n0, advanced: ds.end - e0, streams: ds.streams().length };
    }, BOARD);
    const perStream = rate.rows / Math.max(1, rate.streams) / 3;
    check('State streams at about 10 Hz per value once the watch runs', perStream >= 6 && rate.advanced >= 2, { ...rate, per_stream_hz: +perStream.toFixed(1) });
    // 5cc429f: the host sends a watched entity's State at the watch's rate, so the page stops its /values poll (PEP 0001 §15)
    const keepsUp = await H.waitFor(() => page.evaluate((p) => Boolean((window.robotdx.feed.entity(p) || {}).feedKeepsUp), BOARD), 8000);
    const quietFrom = Date.now();
    const endFrom = await page.evaluate((p) => window.robotdx.feed.dataset('samples', p).end, BOARD);
    await page.waitForTimeout(4000);
    const valuesPolls = everyRequest[0].filter((r) => r.at >= quietFrom && r.url.startsWith(`/api/v1/values?path=${encodeURIComponent(BOARD)}`)).length;
    const endTo = await page.evaluate((p) => window.robotdx.feed.dataset('samples', p).end, BOARD);
    check('the feed keeps the watch\'s pace, so the page stops polling /values for the entity (and State still moves)', Boolean(keepsUp) && valuesPolls === 0 && endTo - endFrom >= 3,
      { keepsUp: Boolean(keepsUp), valuesPolls_4s: valuesPolls, state_advanced_s: +(endTo - endFrom).toFixed(2) });
    const follow = await clockState(page);
    check('the clock follows live: t is the newest data', follow.following && Math.abs(follow.liveEnd - follow.t) < 0.5, follow);
    const thumb = await rect(page, '#dx-time .dv-pb-thumb');
    const track = await rect(page, '#dx-time .dv-pb-track');
    check('the time bar follows: its thumb sits at the track\'s right end', thumb && track && Math.abs((thumb.x + thumb.w / 2) - (track.x + track.w)) <= 4, { thumb, track });
    const cardsLive = await kpiCards(page, '#dx-detail');
    check('State shows one card per value of the entity', ['position', 'velocity', 'torque'].every((k) => cardsLive.some((c) => c.label.includes(k))), cardsLive.map((c) => c.label));

    // ---- rewind: scrub back, the views show t, data keeps arriving --------------------------------------------------------------
    const scrub = await scrubBack(page);
    const { stateSpan, target, trackBefore, xTarget, k5 } = scrub;
    const scrubMs = k5 && k5.ms;
    const paused = await clockState(page);
    // the oracle is the card's own geometry at the drag's end: the pointer's place on the track, over the span the clock holds
    const trackAfter = await rect(page, '#dx-time .dv-pb-track');
    const expected = paused.start + ((xTarget - trackAfter.x) / trackAfter.w) * (paused.end - paused.start);
    check('scrubbing the time bar pauses at the scrubbed t', !paused.following && Math.abs(paused.t - expected) <= Math.max(0.5, 0.002 * (paused.end - paused.start)), { t: paused.t, expected, target });
    check('the track did not move under the pointer (the LIVE pill keeps its width)', Math.abs(trackAfter.x - trackBefore.x) <= 1 && Math.abs(trackAfter.w - trackBefore.w) <= 1, { before: trackBefore, after: trackAfter });
    check('the scrubbed t lies inside the entity\'s State', paused.t > stateSpan.start && paused.t < stateSpan.end, { t: paused.t, state: stateSpan });
    const liveBtn = await page.evaluate(() => { const b = document.querySelector('#dx-time .dv-pb-live'); return b ? { pressed: b.getAttribute('aria-pressed'), text: b.textContent } : null; });
    check('LIVE says paused, with how far behind', liveBtn && liveBtn.pressed === 'false' && /−|-/.test(liveBtn.text), liveBtn);
    check('K5: the last seek to every live panel of the detail drawn at t ≤ 50 ms', typeof scrubMs === 'number' && scrubMs <= 50 && k5.panels.length >= 3, k5);
    check('K5 read both ways agrees: no panel draws again at t while paused (first draw = every draw, data-ui 3289ab5)', k5 && k5.redraws === 0 && k5.every_ms === k5.ms, k5);
    const history = await H.getJson(base, `/api/v1/history?path=${encodeURIComponent(BOARD)}`);
    const values = await H.getJson(base, `/api/v1/values?path=${encodeURIComponent(BOARD)}`);
    await page.waitForTimeout(400);
    const cardsAt = await kpiCards(page, '#dx-detail');
    const pos = cardsAt.find((c) => /position/.test(c.label));
    const want = hostValueAt(history, values, 'position', paused.t);
    check('paused, State shows the host\'s value at t (latest-at)', pos && want && Math.abs(pos.exact - want.value) <= 1e-6 + 1e-4 * Math.abs(want.value), { shown: pos && pos.exact, host: want });
    const playhead = await page.evaluate((t) => {
      const plot = document.querySelector('#dx-detail-signals');
      const head = plot && plot.querySelector('[data-playhead]');
      const box = plot && plot.querySelector('.dv-plot-box');
      if (!head || !box) return null;
      const h = head.getBoundingClientRect(), b = box.getBoundingClientRect();
      return { x: h.left + h.width / 2, right: b.right, left: b.left, hidden: head.hidden };
    }, paused.t);
    check('the plot\'s window ends at t: its playhead is at its right edge', playhead && !playhead.hidden && Math.abs(playhead.x - playhead.right) <= 40, playhead);
    const endBefore = await page.evaluate((p) => window.robotdx.feed.dataset('samples', p).end, BOARD);
    await page.waitForTimeout(2500);
    const held = await clockState(page);
    const endAfter = await page.evaluate((p) => window.robotdx.feed.dataset('samples', p).end, BOARD);
    const cardsHeld = await kpiCards(page, '#dx-detail');
    const posHeld = cardsHeld.find((c) => /position/.test(c.label));
    check('paused, data keeps arriving underneath (the store grows, t stays)', endAfter - endBefore >= 1.5 && held.t === paused.t && !held.following, { grew: endAfter - endBefore, t: held.t, was: paused.t });
    check('paused, the State card holds its value while rows arrive', posHeld && pos && posHeld.exact === pos.exact, { now: posHeld && posHeld.exact, was: pos && pos.exact });
    // back to live in one click
    const goLive = await page.evaluate(async () => {
      const b = document.querySelector('#dx-time .dv-pb-live');
      const start = performance.now();
      b.click();
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const c = window.robotdx.clock;
      return { ms: performance.now() - start, following: c.following, lag: c.liveEnd - c.t, pressed: b.getAttribute('aria-pressed') };
    });
    check('one click on LIVE follows again (t = the newest data within two frames)', goLive.following && goLive.lag < 0.5 && goLive.pressed === 'true' && goLive.ms < 100, goLive);
    await page.waitForTimeout(1500);
    const cardsAgain = await kpiCards(page, '#dx-detail');
    const posAgain = cardsAgain.find((c) => /position/.test(c.label));
    check('live again, the State card moves on', posAgain && pos && posAgain.exact !== pos.exact, { now: posAgain && posAgain.exact, paused: pos && pos.exact });

    // ---- the playback bar: three to compare (Zach, 2026-10-28: "please give the option to switch between today's bar, rerun simple and
    // foxglove bar"): a bottom-right picker that switches live and is remembered (no URL flag); each bar seeks, and goes live again
    const picker = await page.evaluate(() => { const sel = document.querySelector('#dx-playback-variant'); if (!sel) return null; const r = sel.getBoundingClientRect(); return { options: [...sel.options].map((o) => o.value), value: sel.value, right: innerWidth - r.right, bottom: innerHeight - r.bottom, w: r.width }; });
    check('a bottom-right picker offers the three playback bars: today\'s, Rerun (simple), Foxglove', picker && picker.options.join() === 'bar,rerun,foxglove' && picker.right <= 48 && picker.bottom <= 72 && picker.w > 0, picker);
    const bars = {};
    for (const v of ['rerun', 'foxglove', 'bar']) {
      await page.selectOption('#dx-playback-variant', v);
      await page.waitForTimeout(500);
      const root = { bar: '#dx-time .dv-pb-track', rerun: '#dx-time .dv-rr [data-scrub]', foxglove: '#dx-time .dv-fx [data-scrub]' }[v];
      const scrub = await rect(page, root);
      const variant = await page.evaluate(() => { const el = document.querySelector('#dx-time [data-variant]'); return el ? el.dataset.variant : null; });
      let seeked = null, back = null;
      if (scrub && scrub.w > 40) {
        await page.mouse.click(scrub.x + scrub.w * 0.4, scrub.y + scrub.h / 2);
        await page.waitForTimeout(300);
        seeked = await clockState(page);
        const live = v === 'bar' ? '#dx-time .dv-pb-live' : '#dx-live';
        const lr = await rect(page, live);
        if (lr && lr.w > 0) await page.mouse.click(lr.x + lr.w / 2, lr.y + lr.h / 2);
        await page.waitForTimeout(300);
        back = await clockState(page);
      }
      bars[v] = { variant, scrub: scrub && Math.round(scrub.w), paused: seeked && !seeked.following && seeked.t < seeked.liveEnd - 0.5, live: back && back.following };
    }
    check('each bar draws its own control, its track seeks (paused at the click) and LIVE follows again', ['bar', 'rerun', 'foxglove'].every((v) => bars[v].variant === v && bars[v].paused && bars[v].live), bars);
    await page.selectOption('#dx-playback-variant', 'foxglove');
    await page.waitForTimeout(300);
    await page.reload();
    await ready(page);
    await page.waitForTimeout(800);
    const kept = await page.evaluate(() => ({ picked: (document.querySelector('#dx-playback-variant') || {}).value, variant: (document.querySelector('#dx-time [data-variant]') || { dataset: {} }).dataset.variant }));
    check('the pick is remembered across a reload (this browser), the bar drawn with it', kept.picked === 'foxglove' && kept.variant === 'foxglove', kept);
    await page.selectOption('#dx-playback-variant', 'bar');
    await page.waitForTimeout(300);

    // ---- the heartbeat stops when State leaves the screen, and while the tab is hidden -------------------------------------------
    await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' }); Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
    await page.waitForTimeout(800);
    const hiddenFrom = Date.now();
    await page.waitForTimeout(4500);
    const beatsHidden = everyRequest[0].filter((r) => r.at >= hiddenFrom && r.method === 'POST').length;
    const pollsHidden = everyRequest[0].filter((r) => r.at >= hiddenFrom && /^\/api\/v1\/(state|node|values|history|activity)/.test(r.url)).length;
    check('a hidden tab sends no heartbeat and polls nothing', beatsHidden === 0 && pollsHidden === 0, { beats: beatsHidden, polls: pollsHidden });
    await page.evaluate(() => { delete document.visibilityState; delete document.hidden; Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' }); Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange')); });
    await page.click('#dx-rail .dv-rail-dest[data-id="runs"]');
    await page.waitForFunction(() => document.querySelector('#dx-main').dataset.dest === 'runs', null, { timeout: 8000 });
    await page.waitForTimeout(1000);
    const leftAt = Date.now();
    await page.waitForTimeout(4500);
    const beatsAfter = everyRequest[0].filter((r) => r.at >= leftAt && r.method === 'POST').length;
    check('leaving State (another destination) stops the heartbeat', beatsAfter === 0, { beats: beatsAfter });
    check('Runs shows the host\'s runs as a table', await page.evaluate(() => Boolean(document.querySelector('#dx-main data-views[data-view]'))));

    // ---- K2 after: the new page idle on Explore with nothing selected --------------------------------------------------------------
    await page.goto(`${base}${DX}?go=explore`);
    await ready(page);
    await page.waitForTimeout(3000);
    const after = await traffic(pageMeter, page, IDLE_S);
    check('K2: idle, the new page costs fewer bytes per second than today\'s', after.kbps < before.kbps, { new: after, today: before });

    // ---- the rail: three styles, measured --------------------------------------------------------------------------------------------
    await page.click('#dx-rail .dv-rail-dest[data-id="settings"]');
    await page.waitForFunction(() => document.querySelector('#dx-main').dataset.dest === 'settings', null, { timeout: 8000 });
    const select = '#dx-main select[data-setting="rail"]';
    for (const mode of ['collapsed', 'expanded', 'wide']) {
      await page.selectOption(select, mode);
      await page.waitForTimeout(250);
      const m = await page.evaluate(() => {
        const rail = document.querySelector('#dx-rail');
        const dest = rail.querySelector('.dv-rail-dest[data-id="explore"]');
        const icon = dest.querySelector('.dv-rail-icon').getBoundingClientRect();
        const label = dest.querySelector('.dv-rail-label');
        const l = label.getBoundingClientRect();
        const visible = getComputedStyle(label).display !== 'none' && getComputedStyle(label).visibility !== 'hidden' && l.width > 0 && l.height > 0;
        return { mode: rail.mode, width: rail.getBoundingClientRect().width, visible, icon: { cx: icon.x + icon.width / 2, cy: icon.y + icon.height / 2, right: icon.right, bottom: icon.bottom }, label: { cx: l.x + l.width / 2, cy: l.y + l.height / 2, left: l.left, top: l.top } };
      });
      const shaped = mode === 'collapsed' ? !m.visible
        : mode === 'expanded' ? m.visible && m.label.top >= m.icon.bottom - 0.5 && Math.abs(m.label.cx - m.icon.cx) <= 1
          : m.visible && m.label.left >= m.icon.right - 0.5 && Math.abs(m.label.cy - m.icon.cy) <= 2;
      check(`rail ${mode}: ${RAIL_WIDTHS[mode]} px wide, labels ${mode === 'collapsed' ? 'hidden' : mode === 'expanded' ? 'under the icons' : 'beside the icons'}`, m.mode === mode && Math.abs(m.width - RAIL_WIDTHS[mode]) <= 2 && shaped, m);
    }
    const railEdge = await rect(page, '#dx-rail + .dv-splitter, #dx-rail ~ .dv-splitter');
    const railBefore = (await rect(page, '#dx-rail')).w;
    if (railEdge) await dragBy(page, railEdge, 60);
    const railAfter = (await rect(page, '#dx-rail')).w;
    check('rail wide: its edge drags (+60 px)', railEdge && Math.abs(railAfter - railBefore - 60) <= 3, { before: railBefore, after: railAfter });
    await page.reload();
    await ready(page);
    await page.waitForTimeout(500);
    const railReload = (await rect(page, '#dx-rail')).w;
    check('rail wide: the width is kept on reload', Math.abs(railReload - railAfter) <= 2, { reload: railReload, dragged: railAfter });
    const edgeAgain = await rect(page, '#dx-rail + .dv-splitter, #dx-rail ~ .dv-splitter');
    await page.mouse.dblclick(edgeAgain.x + edgeAgain.w / 2, edgeAgain.y + 200);
    await page.waitForTimeout(250);
    check('rail wide: double-click resets to 240 px', Math.abs((await rect(page, '#dx-rail')).w - 240) <= 2, (await rect(page, '#dx-rail')).w);
    await page.locator('body').click({ position: { x: 700, y: 10 } }).catch(() => {});
    await page.keyboard.press('Control+Alt+KeyR');
    await page.waitForTimeout(250);
    const folded = await page.evaluate(() => ({ mode: document.querySelector('#dx-rail').mode, w: document.querySelector('#dx-rail').getBoundingClientRect().width }));
    await page.keyboard.press('Control+Alt+KeyR');
    await page.waitForTimeout(250);
    const opened = await page.evaluate(() => ({ mode: document.querySelector('#dx-rail').mode, w: document.querySelector('#dx-rail').getBoundingClientRect().width }));
    check('Ctrl+Alt+R folds the rail and opens it again in the last open style', folded.mode === 'collapsed' && Math.abs(folded.w - 48) <= 2 && opened.mode === 'wide', { folded, opened });

    // ---- the tree pane: drag, keep, reset, fold ------------------------------------------------------------------------------------
    await page.click('#dx-rail .dv-rail-dest[data-id="explore"]');
    await page.waitForFunction(() => document.querySelector('#dx-main').dataset.dest === 'explore', null, { timeout: 8000 });
    await page.waitForTimeout(500);
    const pane0 = await rect(page, '#dx-tree-pane');
    const paneEdge = await rect(page, '#dx-tree-pane + .dv-splitter');
    if (paneEdge) await dragBy(page, paneEdge, 80, 0, pane0.y + 300);
    const pane1 = await rect(page, '#dx-tree-pane');
    check('tree pane: its edge drags (+80 px)', paneEdge && Math.abs(pane1.w - pane0.w - 80) <= 3, { before: pane0.w, after: pane1.w });
    await page.reload();
    await ready(page);
    await page.waitForTimeout(500);
    const pane2 = await rect(page, '#dx-tree-pane');
    check('tree pane: the width is kept on reload', Math.abs(pane2.w - pane1.w) <= 2, { reload: pane2.w, dragged: pane1.w });
    const paneEdge2 = await rect(page, '#dx-tree-pane + .dv-splitter');
    await page.mouse.dblclick(paneEdge2.x + paneEdge2.w / 2, paneEdge2.y + 300);
    await page.waitForTimeout(250);
    const pane3 = await rect(page, '#dx-tree-pane');
    check('tree pane: double-click resets it', Math.abs(pane3.w - pane0.w) <= 2, { reset: pane3.w, default: pane0.w });
    await page.click('#dx-tree-fold');
    await page.waitForTimeout(250);
    const foldedPane = await page.evaluate(() => { const p = document.querySelector('#dx-tree-pane'); const r = p.getBoundingClientRect(); return { w: r.width, hidden: p.hidden || getComputedStyle(p).display === 'none' }; });
    const detailWide = await rect(page, '#dx-detail');
    await page.click('#dx-tree-fold');
    await page.waitForTimeout(250);
    const unfolded = await rect(page, '#dx-tree-pane');
    check('tree pane: the fold button hides it (the detail takes the room) and brings it back at its width', (foldedPane.hidden || foldedPane.w === 0) && detailWide && Math.abs(unfolded.w - pane3.w) <= 2, { foldedPane, detailWide, unfolded: unfolded.w });

    // ---- dashboards: a starter, and one of your own --------------------------------------------------------------------------------
    await page.goto(`${base}${DX}?go=dashboards`);
    await ready(page);
    const starters = await H.waitFor(() => page.evaluate(() => { const items = [...document.querySelectorAll('#dx-main [data-board-id][data-board-kind="starter"]')]; return items.length ? items.map((a) => a.dataset.boardId) : null; }), 10000);
    check(`the Dashboards list shows the starters (${STARTERS_MIN}+)`, starters && starters.length >= STARTERS_MIN, starters);
    const starterId = starters && (starters.find((s) => /joint/.test(s)) || starters[0]);
    await page.click(`#dx-main [data-board-id="${starterId}"]`);
    const opened1 = await page.waitForFunction(() => { const d = document.querySelector('data-dashboard.dx-board'); return d && d.dash && d.querySelectorAll('.dd-panel').length > 0 && new URLSearchParams(location.search).get('board'); }, null, { timeout: 15000 }).then(() => true, () => false);
    const panels = await page.evaluate(() => { const d = document.querySelector('data-dashboard.dx-board'); return { drawn: d.querySelectorAll('.dd-panel').length, declared: d.dash.panels.length }; });
    check('a starter dashboard opens and draws every panel', opened1 && panels.drawn === panels.declared && panels.drawn > 0, panels);
    const points = await H.waitFor(() => page.evaluate(() => { const p = document.querySelector('data-dashboard.dx-board [data-points]'); return p && Number(p.dataset.points) > 0 ? Number(p.dataset.points) : null; }), 12000);
    check('its live plot draws points and follows', Boolean(points) && (await clockState(page)).following, { points });

    // ---- every check in one answer (GET /api/v1/checks, PEP 0001 §15): the Diagnostics starter is the host's own list, worst first,
    // read with /checks and no per-entity /node
    const diagFrom = Date.now();
    await page.goto(`${base}${DX}?go=dashboards&board=diagnostics`);
    await ready(page);
    const listed = await H.waitFor(() => page.evaluate(() => {
      const el = document.querySelector('data-dashboard.dx-board data-views[id$="-checks"]');
      return el && el.model && el.model.rows && el.model.rows.length ? el.model.rows.map((r) => String(r.id)) : null;
    }), 15000);
    const allChecks = await H.getJson(base, '/api/v1/checks');
    const hostChecks = Object.entries(allChecks.checks || {}).flatMap(([p, list]) => list.map((c) => ({ id: `${p}#${c.name}`, level: c.effective_level || c.level })));
    const notListed = hostChecks.filter((c) => !(listed || []).includes(c.id)).map((c) => c.id);
    check('the Diagnostics starter lists every check the host reports (its own GET /api/v1/checks)', Boolean(listed) && hostChecks.length >= 5 && notListed.length === 0 && listed.length === hostChecks.length,
      { listed: listed && listed.length, host: hostChecks.length, missing: notListed.slice(0, 5) });
    await page.waitForTimeout(3000);
    const diagReqs = everyRequest[0].filter((r) => r.at >= diagFrom);
    const order = await page.evaluate(() => { const el = document.querySelector('data-dashboard.dx-board data-views[id$="-checks"]'); return el ? [...el.querySelectorAll('tbody tr[data-id]')].map((tr) => tr.dataset.id) : []; });
    const rankOf = Object.fromEntries(hostChecks.map((c) => [c.id, ['ok', 'warn', 'unknown', 'stale', 'error'].indexOf(c.level)]));
    const ranks = order.map((id) => rankOf[id] ?? -1);
    check('…worst first, from one /checks request and no per-entity /node', diagReqs.some((r) => r.url === '/api/v1/checks') && !diagReqs.some((r) => r.url.startsWith('/api/v1/node')) && ranks.length >= 5 && ranks.every((x, i) => i === 0 || x <= ranks[i - 1]),
      { checks: diagReqs.filter((r) => r.url === '/api/v1/checks').length, node: diagReqs.filter((r) => r.url.startsWith('/api/v1/node')).length, ranks: ranks.slice(0, 12) });
    // F1 has two adapters with a "CAN 1" each: a row is told from its twin by its path, shown inside the panel (measured)
    const pathCol = await page.evaluate(() => {
      const el = document.querySelector('data-dashboard.dx-board data-views[id$="-checks"]');
      if (!el) return null;
      const box = el.getBoundingClientRect();
      const th = [...el.querySelectorAll('thead th, [role=columnheader]')].find((h) => h.textContent.trim().startsWith('Path'));
      const r = th && th.getBoundingClientRect();
      const twins = el.model ? el.model.rows.filter((x) => x.entity === 'CAN 1' && /#CAN 1: driver$/.test(String(x.id))).map((x) => x.path) : [];
      return { header: Boolean(th), inside: Boolean(r && r.left >= box.left && r.right <= box.right + 1 && r.width > 40), twins };
    });
    check('…each row with its path in view, so two entities of one name are told apart', pathCol && pathCol.header && pathCol.inside && new Set(pathCol.twins).size === pathCol.twins.length && pathCol.twins.length >= 2, pathCol);

    // ---- your own, on the host (GET | PUT | DELETE /api/v1/dashboards/<name>.base, PEP 0001 §14): files in THIS fixture host's config
    // dir (the harness gives it IO_DIAGNOSTICS_CONFIG_DIR under its scratch root), never Zach's ~/.config/io-diagnostics
    const hostDir = path.join(main.root, 'config', 'dashboards');
    const realDir = path.join(os.homedir(), '.config', 'io-diagnostics', 'dashboards');
    const realBefore = fs.existsSync(realDir) ? fs.readdirSync(realDir).sort().join('|') : null;
    const fileOf = (name) => path.join(hostDir, `${name}.base`);
    const readHost = (name) => (fs.existsSync(fileOf(name)) ? fs.readFileSync(fileOf(name), 'utf8') : null);
    phase.saving = true;
    // a draft left in this browser by phase 3 (localStorage) moves to the host on its first save
    await page.goto(`${base}${DX}?go=dashboards`);
    await ready(page);
    await page.evaluate((text) => localStorage.setItem('robotdx.dx.boards', JSON.stringify([{ id: 'my-old-draft', title: 'Old draft', text, updated: '2026-10-27T12:00:00Z' }])),
      'datasets:\n  summary:\n    data: "../datasets/summary.json"\nlayout:\n  columns: 12\n  panels:\n    - { view: Time, w: 12, h: 1 }\nviews:\n  - type: playback\n    name: Time\n    from: summary\n');
    await page.reload();
    await ready(page);
    const draft = await page.waitForFunction(() => { const a = document.querySelector('#dx-main [data-board-kind="draft"]'); return a ? a.dataset.boardId : null; }, null, { timeout: 8000 }).then((h) => h.jsonValue(), () => null);
    check('a dashboard left in this browser is listed as a draft', Boolean(draft), draft);
    if (draft) {
      await page.click(`#dx-main [data-board-id="${draft}"]`);
      await page.waitForFunction(() => { const d = document.querySelector('data-dashboard.dx-board'); return d && d.dash; }, null, { timeout: 10000 });
      await page.click('#dx-main [data-board-act="add"]');
      await page.waitForSelector('#dx-add-panel[open]', { timeout: 5000 });
      await page.selectOption('#dx-add-panel [name="type"]', 'levels');
      await page.selectOption('#dx-add-panel [name="entity"]', BOARD);
      await page.click('#dx-add-panel [data-add-panel="ok"]');
      await H.waitFor(() => readHost('Old draft'), 8000);
    }
    const moved = { host: readHost('Old draft'), local: await page.evaluate(() => localStorage.getItem('robotdx.dx.boards')), url: await page.evaluate(() => new URLSearchParams(location.search).get('board')) };
    check('its first save writes it to the host and takes it out of this browser', moved.host !== null && /type: levels/.test(moved.host) && !/my-old-draft/.test(moved.local || '') && moved.url === 'Old draft',
      { host: moved.host && moved.host.length, local: moved.local, url: moved.url });

    // make one: New, Add panel (a plot of the board's position), Arrange (resize it), Done
    await page.goto(`${base}${DX}?go=dashboards`);
    await ready(page);
    await page.click('#dx-main [data-board-act="new"]');
    const mine = await page.waitForFunction(() => { const id = new URLSearchParams(location.search).get('board'); const d = document.querySelector('data-dashboard.dx-board'); return id && d && d.dash ? id : null; }, null, { timeout: 10000 }).then((h) => h.jsonValue(), () => null);
    check('New makes a dashboard of your own on the host and opens it', Boolean(mine) && readHost(mine) !== null && /type: playback/.test(readHost(mine)), { mine, file: mine && fileOf(mine) });
    await page.click('#dx-main [data-board-act="add"]');
    await page.waitForSelector('#dx-add-panel[open]', { timeout: 5000 });
    await page.selectOption('#dx-add-panel [name="type"]', 'plot');
    await page.selectOption('#dx-add-panel [name="entity"]', BOARD);
    await page.waitForTimeout(800);
    await page.selectOption('#dx-add-panel [name="field"]', 'position').catch(() => {});
    await page.click('#dx-add-panel [data-add-panel="ok"]');
    const added = await page.waitForFunction(() => { const d = document.querySelector('data-dashboard.dx-board'); return d && [...d.querySelectorAll('.dd-panel')].some((p) => p.dataset.type === 'plot'); }, null, { timeout: 10000 }).then(() => true, () => false);
    await H.waitFor(() => /type: plot/.test(readHost(mine) || ''), 8000);
    check('Add panel puts a plot of the entity on it, and the host file has it', added && /type: plot/.test(readHost(mine) || '') && (readHost(mine) || '').includes(`samples.json?path=${BOARD}`));
    const puts = everyRequest[0].filter((r) => r.method === 'PUT' && r.url === `/api/v1/dashboards/${encodeURIComponent(mine).replace(/%20/g, '%20')}.base`);
    check('a save of an existing file sends If-Match (the etag the page last saw)', puts.length >= 2 && puts.slice(1).every((r) => r.headers['if-match']), puts.map((r) => r.headers['if-match'] || '(none)'));
    const plotPanel = 'data-dashboard.dx-board .dd-panel[data-type="plot"]';
    const w0 = (await rect(page, plotPanel)).w;
    await page.click('data-dashboard.dx-board .dd-act[data-act="arrange"]');
    await page.waitForSelector('data-dashboard.dx-board[data-arranging] .grid-stack-item', { timeout: 10000 });
    // gridstack shows a panel's resize handle only under the pointer (ui-resizable-autohide): hover the panel first
    await page.hover(plotPanel);
    await page.waitForTimeout(150);
    const handle = await rect(page, `${plotPanel} .ui-resizable-se`);
    if (handle) await dragBy(page, handle, -Math.round(w0 / 2));
    await page.click('data-dashboard.dx-board .dd-act[data-act="arrange"]');
    await page.waitForTimeout(1200);
    const arranged = await page.evaluate(() => [...document.querySelectorAll('data-dashboard.dx-board .dd-panel')].map((p) => { const r = p.getBoundingClientRect(); return { key: p.dataset.panel, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; }));
    const w1 = (await rect(page, plotPanel)).w;
    check('Arrange resizes the panel (measured)', handle && w1 < w0 * 0.75, { before: w0, after: w1 });
    const stored = readHost(mine) || '';
    const storedBase = await page.evaluate((text) => { try { return window.robotdx.parseBase(text); } catch (e) { return null; } }, stored);
    const plotEntry = storedBase && (storedBase.layout.panels || []).find((p) => (storedBase.views || []).some((v) => v.type === 'plot' && v.name === p.view));
    check('the arrangement is written into the host file\'s layout:', plotEntry && Number(plotEntry.w) <= 7, { panel: plotEntry });
    await page.reload();
    await ready(page);
    await page.waitForFunction(() => { const d = document.querySelector('data-dashboard.dx-board'); return d && d.dash && d.querySelectorAll('.dd-panel').length; }, null, { timeout: 15000 });
    await page.waitForTimeout(800);
    const reloaded = await page.evaluate(() => [...document.querySelectorAll('data-dashboard.dx-board .dd-panel')].map((p) => { const r = p.getBoundingClientRect(); return { key: p.dataset.panel, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; }));
    const same = arranged.length === reloaded.length && arranged.every((a, i) => ['x', 'y', 'w', 'h'].every((k) => Math.abs(a[k] - reloaded[i][k]) <= 2));
    const readBack = everyRequest[0].some((r) => r.method === 'GET' && r.url.startsWith('/api/v1/dashboards/') && r.url.endsWith('.base'));
    check('after a reload it is read back from the host with the same panels in the same places', same && readBack, { arranged, reloaded, readBack });
    // a conflict: another tab saves first; this tab's next save is refused (412) and shown, never written over
    const tab2 = await context.newPage();
    everyRequest.push(recorder(tab2, base));
    await tab2.goto(`${base}${DX}?go=dashboards&board=${encodeURIComponent(mine)}`);
    await ready(tab2);
    await tab2.waitForFunction(() => { const d = document.querySelector('data-dashboard.dx-board'); return d && d.dash; }, null, { timeout: 10000 });
    await tab2.click('#dx-main [data-board-act="add"]');
    await tab2.waitForSelector('#dx-add-panel[open]', { timeout: 5000 });
    await tab2.selectOption('#dx-add-panel [name="type"]', 'table');
    await tab2.selectOption('#dx-add-panel [name="entity"]', BOARD);
    await tab2.click('#dx-add-panel [data-add-panel="ok"]');
    await H.waitFor(() => /type: table/.test(readHost(mine) || ''), 8000);
    const theirs = readHost(mine);
    await page.click('#dx-main [data-board-act="add"]');
    await page.waitForSelector('#dx-add-panel[open]', { timeout: 5000 });
    await page.selectOption('#dx-add-panel [name="type"]', 'kpi');
    await page.selectOption('#dx-add-panel [name="entity"]', BOARD);
    await page.click('#dx-add-panel [data-add-panel="ok"]');
    const conflict = await page.waitForFunction(() => { const c = document.querySelector('#dx-main [data-board-conflict]'); return c && !c.hidden && c.getBoundingClientRect().height > 0 ? c.textContent : null; }, null, { timeout: 8000 }).then((h) => h.jsonValue(), () => null);
    const refused = everyRequest[0].some((r) => r.method === 'PUT' && r.at > Date.now() - 9000) ;
    check('a save over a newer version is refused (412) and shown as a conflict; the host keeps the other tab\'s text', Boolean(conflict) && readHost(mine) === theirs && refused && !/type: kpi/.test(readHost(mine) || ''), { conflict: conflict && conflict.slice(0, 120) });
    await tab2.close();
    // Save as file: the download is the .base, and it parses back
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 8000 }).catch(() => null), page.click('#dx-main [data-board-act="save"]')]);
    const saved = download ? fs.readFileSync(await download.path(), 'utf8') : '';
    const parsed = await page.evaluate((text) => { try { const b = window.robotdx.parseBase(text); return { panels: (b.layout && b.layout.panels || []).length, views: (b.views || []).map((v) => v.type) }; } catch (e) { return { error: e.message }; } }, saved);
    check('Save as file downloads the .base, and it parses back with its panels', download && /\.base$/.test(download.suggestedFilename()) && parsed.panels >= 1 && parsed.views.includes('plot'), { name: download && download.suggestedFilename(), parsed });
    // Load .base: from the picker, and dropped on the page; each becomes a host file
    const file = path.join(scratch, 'from-a-file.base');
    fs.writeFileSync(file, saved || 'layout:\n  panels: []\n');
    await page.goto(`${base}${DX}?go=dashboards`);
    await ready(page);
    await page.setInputFiles('#dx-load-input', file);
    const picked = await page.waitForFunction(() => [...document.querySelectorAll('#dx-main [data-board-id][data-board-kind="mine"]')].some((a) => /from-a-file/.test(a.textContent)), null, { timeout: 8000 }).then(() => true, () => false);
    check('Load .base: a picked file becomes one of yours, on the host', picked && readHost('from-a-file') !== null);
    const dropped = await page.evaluate(async (text) => {
      const dt = new DataTransfer();
      dt.items.add(new File([text], 'dropped-board.base', { type: 'text/plain' }));
      const target = document.querySelector('#dx-main');
      for (const type of ['dragenter', 'dragover', 'drop']) target.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
      for (let i = 0; i < 40; i += 1) { await new Promise((r) => setTimeout(r, 100)); if ([...document.querySelectorAll('#dx-main [data-board-kind="mine"]')].some((a) => /dropped-board/.test(a.textContent))) return { ok: true }; }
      return { ok: false };
    }, saved);
    check('Load .base: a file dropped on the page becomes one of yours, on the host', dropped.ok && readHost('dropped-board') !== null, dropped);
    // Delete: moved to the host's .trash/, gone from the list (after a reload too)
    await page.click(`#dx-main [data-board-id="${mine}"]`);
    await page.waitForFunction((id) => new URLSearchParams(location.search).get('board') === id, mine, { timeout: 8000 });
    page.once('dialog', (d) => d.accept());
    await page.click('#dx-main [data-board-act="delete"]');
    await page.waitForTimeout(800);
    await page.goto(`${base}${DX}?go=dashboards`);
    await ready(page);
    await page.waitForTimeout(500);
    const gone = await page.evaluate((id) => !document.querySelector(`#dx-main [data-board-id="${id}"]`), mine);
    const trash = path.join(hostDir, '.trash');
    const trashed = fs.existsSync(trash) && fs.readdirSync(trash).some((f) => f.startsWith(`${mine}.`) && f.endsWith('.base'));
    check('Delete moves it to the host\'s .trash/ and takes it off the list (after a reload too)', gone && trashed && readHost(mine) === null, { gone, trashed });
    phase.saving = false;
    const realAfter = fs.existsSync(realDir) ? fs.readdirSync(realDir).sort().join('|') : null;
    check('every write landed in the fixture host\'s scratch config dir; ~/.config/io-diagnostics/dashboards is untouched', realAfter === realBefore && hostDir.startsWith(main.root), { realDir, unchanged: realAfter === realBefore });

    // ---- dark: the page and a dashboard draw dark --------------------------------------------------------------------------------
    const dark = await H.newContext(browser, 'dark');
    const darkPage = await dark.newPage();
    everyRequest.push(recorder(darkPage, base));
    await darkPage.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(BOARD)}`);
    await ready(darkPage);
    await darkPage.waitForTimeout(1500);
    const lum = await darkPage.evaluate(() => {
      const rgb = (el) => (getComputedStyle(el).backgroundColor.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
      const l = ([r, g, b]) => (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
      const panel = document.querySelector('#dx-detail .dd-panel');
      return { body: l(rgb(document.body)), panel: panel ? l(rgb(panel)) : null };
    });
    check('dark: the page and its panels are dark', lum.body < 0.2 && (lum.panel === null || lum.panel < 0.25), lum);
    await dark.close();

    // ---- an older host: no /api/v1/live, no /api/v1/dashboards (404) -> the /state poll and this browser's own store -----------------
    const older = await H.newContext(browser, 'light');
    await older.route('**/api/v1/live', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"error": "no endpoint \'/api/v1/live\'"}' }));
    await older.route(/\/api\/v1\/dashboards(\/.*)?$/, (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"error": "no endpoint"}' }));
    await older.route(/\/api\/v1\/history\/tree/, (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"error": "no endpoint"}' }));
    const oldHostPage = await older.newPage();
    everyRequest.push(recorder(oldHostPage, base));
    const oldHostMeter = await meter(oldHostPage, base);
    await oldHostPage.goto(`${base}${DX}?go=explore`);
    await ready(oldHostPage);
    const polled = await H.waitFor(async () => (await treeRows(oldHostPage)).length >= 5 && everyRequest[everyRequest.length - 1].filter((r) => r.url === '/api/v1/state').length >= 2, 15000);
    const pollMode = await oldHostPage.evaluate(() => window.robotdx.feed.mode);
    check('with no /api/v1/live (404) the page polls /state instead, and draws the same tree', Boolean(polled) && pollMode === 'poll', { mode: pollMode });
    // with no /history/tree (404) the tree before the page opened is empty, and phase 3's note says why
    const oldOpened = await oldHostPage.evaluate(() => window.robotdx.feed.openedAt);
    await oldHostPage.evaluate(() => { window.__treeDraws = []; document.querySelector('#dx-tree').addEventListener('dx-tree-draw', (e) => window.__treeDraws.push({ t: e.detail.t, following: e.detail.following, rows: e.detail.rows })); });
    await oldHostPage.evaluate((t) => window.robotdx.clock.seek(t), oldOpened - 5);
    await oldHostPage.waitForTimeout(600);
    const oldPast = { clock: await clockState(oldHostPage), rows: (await treeRows(oldHostPage)).length, draws: await oldHostPage.evaluate(() => window.__treeDraws),
      note: await oldHostPage.evaluate(() => { const n = document.querySelector('#dx-before'); return n && !n.hidden ? n.textContent : null; }) };
    check('with no /api/v1/history/tree (404) the tree before the page opened is empty (drawn at t, no rows), and the note says the host keeps no tree',
      oldPast.clock.t < oldOpened && oldPast.rows === 0 && oldPast.draws.some((d) => d.t === oldPast.clock.t && d.rows === 0) && /no tree/.test(oldPast.note || ''),
      { t: oldPast.clock.t, following: oldPast.clock.following, opened: oldOpened, rows: oldPast.rows, draws: oldPast.draws.slice(0, 3), note: oldPast.note });
    await oldHostPage.evaluate(() => window.robotdx.clock.goLive());
    const polling = await traffic(oldHostMeter, oldHostPage, IDLE_S);
    await oldHostPage.goto(`${base}${DX}?go=dashboards`);
    await ready(oldHostPage);
    await oldHostPage.click('#dx-main [data-board-act="new"]');
    const localId = await oldHostPage.waitForFunction(() => new URLSearchParams(location.search).get('board'), null, { timeout: 8000 }).then((h) => h.jsonValue(), () => null);
    const localText = await oldHostPage.evaluate((id) => { try { return (JSON.parse(localStorage.getItem('robotdx.dx.boards') || '[]').find((b) => b.id === id) || {}).text || ''; } catch (_) { return ''; } }, localId);
    const localPuts = everyRequest[everyRequest.length - 1].filter((r) => r.method === 'PUT' || r.method === 'DELETE').length;
    check('with no /api/v1/dashboards (404) a new dashboard lives in this browser, and nothing is written to the host', /type: playback/.test(localText) && localPuts === 0, { localId, localPuts });
    await older.close();

    // ---- K5 again in a GPU browser (data-ui bench/runner.py GPU_ARGS: ANGLE on Vulkan), its renderer checked --------------------------
    let gpu = { renderer: null, k5: null };
    const gpuBrowser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: [...BROWSER_ARGS, ...GPU_ARGS] }).catch(() => null);
    if (gpuBrowser) {
      const gpage = await (await H.newContext(gpuBrowser, 'light')).newPage();
      everyRequest.push(recorder(gpage, base));
      await gpage.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(BOARD)}`);
      await ready(gpage);
      gpu.renderer = await gpage.evaluate(RENDERER_JS).catch(() => null);
      await gpage.waitForFunction((p) => { const d = window.robotdx.feed.dataset('samples', p); return d && d.end && d.start && d.end - d.start > 10; }, BOARD, { timeout: 30000 }).catch(() => {});
      gpu.k5 = (await scrubBack(gpage)).k5;
      await gpuBrowser.close();
    }
    const gpuKind = gpu.renderer ? (SOFTWARE.test(gpu.renderer) ? 'software' : 'gpu') : 'none';
    check(`K5 in the GPU browser (${gpuKind}): the last seek to every live panel drawn at t ≤ 50 ms`, gpu.k5 && typeof gpu.k5.ms === 'number' && gpu.k5.ms <= 50 && gpu.k5.panels.length >= 3, { renderer: gpu.renderer, k5: gpu.k5 });
    check(`K5 in the GPU browser read both ways agrees (no redraw at t while paused)`, gpu.k5 && gpu.k5.redraws === 0 && gpu.k5.every_ms === gpu.k5.ms, gpu.k5);

    // ---- State's pace, three hosts (PEP 0001 §15), on the test-only stub (synthetic State, no plugin, no device): 50 samples/s over
    // one entity's 5 keys, 10 points a second each. The page must poll /values exactly when the feed lags the data: never with the
    // host's watch-rate samples, at 250 ms with an older host's 1 s tick (State then still moves faster than the tick), and not
    // for long with a watch slower than the cadence rule can see (1 point a second each: the "who comes first" rule)
    const stubSink = { requests: [], errors: [] };
    const atRate = await stubCadence(browser, ['--rate', '50', '--entities', '1', '--keys', '5', '--coalesce', '0.1'], { settle: keepsUpSoon(8000) }, stubSink);
    check('stub, the host\'s watch-rate samples: the feed keeps up, no /values poll, State in steps of the watch', atRate.beat && atRate.keepsUp === true && atRate.polls === 0 && atRate.max_gap_ms !== null && atRate.max_gap_ms <= 350, atRate);
    const atTick = await stubCadence(browser, ['--rate', '50', '--entities', '1', '--keys', '5', '--coalesce', '0'], { settle: (p) => p.waitForTimeout(4000).then(() => true) }, stubSink);
    check('stub, an older host (samples only with its 1 s tick): the page polls /values about every 250 ms and State moves faster than the tick', atTick.beat && atTick.keepsUp === false && atTick.polls_s >= 3 && atTick.max_gap_ms !== null && atTick.max_gap_ms <= 600, atTick);
    const slow = await stubCadence(browser, ['--rate', '5', '--entities', '1', '--keys', '5', '--coalesce', '0.1'], { settle: keepsUpSoon(10000) }, stubSink);
    check('stub, a slow watch (1 point a second each): the polls that bring nothing first stop, the feed keeps up', slow.beat && slow.keepsUp === true && slow.polls === 0, slow);
    // the tree's past where it differs from now (F1's rows hold one level for the whole run, so there the levels cannot tell past from
    // present): the stub's /history/tree says stub/0 and the host were ▲ warn for the stub's first 6 s; both are OK now
    {
      const stub = await startStub(['--rate', '50', '--entities', '2', '--keys', '5', '--past-warn', '6']);
      const ctx = await H.newContext(browser, 'light');
      try {
        await H.wait(11000);
        const p = await ctx.newPage();
        stubSink.requests.push(recorder(p, stub.base));
        const errs = H.watchErrors(p, stub.base);
        await p.goto(`${stub.base}${DX}?go=explore`);
        await ready(p);
        await H.waitFor(() => p.evaluate(() => Boolean(window.robotdx.feed.treeBack)), 10000);
        const opened = await p.evaluate(() => window.robotdx.feed.openedAt);
        const bars = await H.getJson(stub.base, '/api/v1/history/tree?bins=450');
        const bar = (bars.rows.find((r) => r.path === 'stub/0') || { bar: [] }).bar;
        const warnBins = bar.map((x, i) => (x === 'warn' ? i : -1)).filter((i) => i >= 0);
        const tWarn = bars.from + (warnBins[Math.floor(warnBins.length / 2)] + 0.5) * bars.bin_s;
        const tOk = bars.from + (warnBins[warnBins.length - 1] + 2.5) * bars.bin_s;
        const at = async (t) => {
          await p.evaluate((x) => window.robotdx.clock.seek(x), t);
          await p.waitForTimeout(500);
          const r = await treeRows(p);
          return { t: await p.evaluate(() => window.robotdx.clock.t), stub0: (r.find((x) => x.id === 'stub/0') || {}).level, stub1: (r.find((x) => x.id === 'stub/1') || {}).level, rows: r.length };
        };
        const thenWarn = await at(tWarn);
        const thenOk = await at(tOk);
        await p.evaluate(() => window.robotdx.clock.goLive());
        await p.waitForTimeout(500);
        const nowRows = await treeRows(p);
        stubSink.errors.push(...errs.errors);
        check('the tree\'s past is the host\'s, where it differs from now: stub/0 drawn ▲ warn then (stub/1 not), no mark a few seconds later and now',
          warnBins.length >= 2 && tWarn < opened && tOk < opened && thenWarn.stub0 === 'warn' && thenWarn.stub1 === null && thenOk.stub0 === null
            && (nowRows.find((x) => x.id === 'stub/0') || {}).level === null && nowRows.some((x) => x.id === 'stub/0'),
          { opened, warn: thenWarn, ok: thenOk, warn_bins: warnBins.length });
      } finally { await ctx.close().catch(() => {}); H.stopServer(stub.child); }
    }
    // an entity selected before it has any State (a device whose first point comes a few seconds later) gets its State panels when
    // the point comes, without a new selection: the stub's first point 5 s after it starts (1 sample/s over 5 keys)
    {
      const stub = await startStub(['--rate', '1', '--entities', '1', '--keys', '5', '--coalesce', '0.1']);
      const ctx = await H.newContext(browser, 'light');
      try {
        const p = await ctx.newPage();
        stubSink.requests.push(recorder(p, stub.base));
        const errs = H.watchErrors(p, stub.base);
        await p.goto(`${stub.base}${DX}?go=explore&sel=stub/0`);
        await ready(p);
        const early = await H.waitFor(() => p.evaluate(() => { const d = document.querySelector('#dx-detail'); return d && d.dash ? { panels: [...d.querySelectorAll('.dd-panel')].map((x) => x.dataset.panel) } : null; }), 10000);
        const t0 = Date.now();
        const late = await H.waitFor(() => p.evaluate(() => { const c = document.querySelectorAll('#dx-detail .dv-kpi-card').length; return c > 0 ? c : null; }), 15000, 200);
        const wait = Date.now() - t0;
        const sel = await p.evaluate(() => new URLSearchParams(location.search).get('sel'));
        stubSink.errors.push(...errs.errors);
        check('State that comes after the selection gets its panels (no new selection): the State cards appear with the first point', Boolean(early) && !early.panels.includes('state') && Boolean(late) && sel === 'stub/0',
          { first: early, cards: late, waited_ms: wait });
      } finally { await ctx.close().catch(() => {}); H.stopServer(stub.child); }
    }
    const stubAll = stubSink.requests.flat();
    const stubOff = stubAll.filter((r) => !onList(r));
    check('on the stub too: every request on the allow-list, the only write the heartbeat for its entity, no page error', stubOff.length === 0 && stubAll.filter((r) => r.method === 'POST').every((r) => JSON.parse(r.body).path === 'stub/0') && stubSink.errors.length === 0,
      { off: stubOff.slice(0, 5).map((r) => `${r.method} ${r.url}`), errors: stubSink.errors.slice(0, 5) });

    // ---- every request, every page ------------------------------------------------------------------------------------------------
    const all = everyRequest.flat();
    const off = all.filter((r) => !onList(r));
    check('every request is on the allow-list (§12.2): GETs of the page and the read endpoints, POST /api/v1/watch only', off.length === 0, off.slice(0, 6).map((r) => `${r.method} ${r.url}`));
    const posts = all.filter((r) => r.method === 'POST');
    check('the only write is the watch heartbeat, and only for the entity on screen', posts.length > 0 && posts.every((r) => JSON.parse(r.body).path === BOARD), { posts: posts.length });
    // the conflict check's own 412 (and the console line Chrome writes for it) is the answer that check asks for
    const expected412 = (e) => /412/.test(e) && (/\/api\/v1\/dashboards\//.test(e) || /Precondition Failed/.test(e));
    const unexpected = watch.errors.filter((e) => !expected412(e));
    check('no page errors, no console errors, no failed request (the conflict check\'s own 412 aside)', unexpected.length === 0 && watch.errors.filter(expected412).length <= 2, unexpected.slice(0, 5));
    console.log(`\nKPIs  K2 idle: live feed ${after.kbps} KB/s (${after.kb_per_min} KB/min, ${after.rps} req/s) · /state polling fallback ${polling.kbps} KB/s (${polling.kb_per_min} KB/min) · today's page ${before.kbps} KB/s (${before.kb_per_min} KB/min)` +
      `   K5 scrub (first draw / every draw): ${typeof scrubMs === 'number' ? `${Math.round(scrubMs)} / ${Math.round(k5.every_ms)} ms` : 'n/a'} software, ${gpu.k5 && typeof gpu.k5.ms === 'number' ? `${Math.round(gpu.k5.ms)} / ${Math.round(gpu.k5.every_ms)} ms` : 'n/a'} ${gpuKind} (${gpu.renderer || 'no renderer'})   K7 cold load: ${coldMs} ms`);
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close().catch(() => {});
    if (main) H.stopServer(main.child);
    fs.rmSync(scratch, { recursive: true, force: true });
  }
  process.exit(H.summary());
})();
