// Real fixture acceptance for lazy State rendering and the classic SSE consumer.
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('/home/bam/aruco-studio/web/node_modules/playwright');
const { startFixture, stopFixture } = require('../tools/bench_classic');
const H = require('./entity_harness');
(async () => {
  const root = H.fs.mkdtempSync(H.path.join(H.os.tmpdir(), 'classic-accept-'));
  let host, browser;
  try {
    host = await startFixture(root, true);
    browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    let stateGets = 0, valuesGets = 0, beats = 0, activityGets = 0;
    page.on('request', request => {
      const pathname = new URL(request.url()).pathname;
      if (pathname === '/api/v1/state') stateGets++;
      if (pathname === '/api/v1/values') valuesGets++;
      if (pathname === '/api/v1/watch') beats++;
      if (pathname === '/api/v1/activity') activityGets++;
    });
    await H.open(page, host.base, H.BOARD, 'state');
    await page.waitForFunction(() => window.IOClassicLive && IOClassicLive.connected && IODetailLayouts.d6.internals.Live.loaded);
    await H.wait(1200);
    await page.waitForSelector('.d6-sr');
    const rows = await page.locator('.d6-sr').count();
    assert(rows > 0 && rows <= 64, `virtual window has ${rows}, not all 207 rows`);
    const initial = { stateGets, valuesGets };
    const cursor = await page.evaluate(() => IODetailLayouts.d6.internals.Live.cursor);
    await H.wait(3200);
    assert.equal(stateGets, initial.stateGets, 'no full-state polling beside healthy SSE');
    assert.equal(valuesGets, initial.valuesGets, 'no values polling beside healthy SSE');
    assert(await page.evaluate(previous => IODetailLayouts.d6.internals.Live.cursor > previous, cursor), 'actual SSE points advance');
    // Exact sample identity, partial payloads and replay must not delete a sibling row or append twice.
    await page.evaluate(() => {
      const layout = IODetailLayouts.d6, live = layout.internals.Live;
      const groups = live.groups, first = groups[0], row = first.rows[0];
      const count = groups.reduce((total, group) => total + group.rows.length, 0);
      const payload = { path: live.path, cursor: live.cursor + 1, now: live.now, streams: live.streams,
        watch: live.watch, heard: live.heard, groups: [{ ...first, rows: [{ ...row, points: [] }] }] };
      layout.samples(payload);
      if (live.groups.reduce((total, group) => total + group.rows.length, 0) !== count) throw Error('partial SSE lost sibling rows');
      layout.samples(payload);
      if (live.cursor !== payload.cursor) throw Error('replay changed cursor');
    });
    await page.evaluate(() => { const detail = document.getElementById('detail'); detail.scrollTop = detail.scrollHeight; });
    await page.waitForSelector('.d6-sr[data-er-id="voltage"]');
    assert(await page.locator('.d6-sr').count() <= 64, 'scroll swaps the virtual window');
    await page.evaluate(() => { document.getElementById('detail').scrollTop = 0; });
    await page.waitForSelector('[data-er-fold="load"]');
    await page.click('[data-er-fold="load"]');
    await page.waitForFunction(() => document.querySelectorAll('.d6-sr[data-er-id^="sensor_"]').length === 0);
    assert.equal(await page.locator('.d6-sr[data-er-id^="sensor_"]').count(), 0, 'collapsed folder has no rows/sparklines');
    await page.click('[data-er-fold="load"]');
    await page.waitForSelector('.d6-sr[data-er-id^="sensor_"]');
    // A flood in one task must cause one paint, not one tree/detail rebuild per event.
    const coalesced = await page.evaluate(async () => {
      const layout = IODetailLayouts.d6, original = layout.render;
      let draws = 0;
      layout.render = function (...args) { draws++; return original.apply(this, args); };
      for (let index = 0; index < 100; index++) { render(); redrawDetail(); }
      await new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 0)));
      layout.render = original;
      return draws;
    });
    assert.equal(coalesced, 1, 'shell/detail updates share a frame');
    await H.goTab(page, 'diagnostics');
    await H.wait(1500);
    const away = { valuesGets, beats };
    await H.wait(3400);
    assert.equal(valuesGets, away.valuesGets, 'State tab departure stops values work');
    assert.equal(beats, away.beats, 'State tab departure stops watch heartbeats');
    assert.equal(await page.locator('.d6-sp').count(), 0, 'hidden State has no sparklines');
    // Fallback is exercised against the same real fixture, with only the live transport denied.
    await context.route('**/api/v1/live', route => route.abort());
    await page.evaluate(() => { IOClassicLive.source.close(); IOClassicLive.source = null; IOClassicLive.connected = false; });
    await H.goTab(page, 'state');
    const fallback = { stateGets, valuesGets };
    await H.wait(3200);
    assert(stateGets > fallback.stateGets && valuesGets > fallback.valuesGets, 'disconnected fallback stays current');
    await context.unroute('**/api/v1/live');
    await H.open(page, host.base, H.CAMERA, 'state');
    await page.waitForSelector('.d6-tile img[src]');
    await page.click('.d6-tile:nth-of-type(2) .d6-pic');
    await page.waitForSelector('.d6-lb img[src]');
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await H.wait(500);
    assert.equal(await page.locator('.d6-tile img[src], .d6-lb img[src]').count(), 0, 'background tab stops every MJPEG source, including the lightbox');
    await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); });
    await page.waitForSelector('.d6-lb img[src]');
    await page.waitForFunction(() => IOClassicLive.connected && IODetailLayouts.d6.internals.Live.loaded);
    await page.keyboard.press('Escape');
    await H.goTab(page, 'log');
    await page.waitForSelector('.al-list');
    await H.wait(500);
    const logRequests = activityGets;
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await H.wait(9000);
    assert.equal(activityGets, logRequests, 'background Log does not poll even after its 8 s refresh interval');
    await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); });
    assert(await H.waitFor(() => activityGets > logRequests), 'visible Log resumes its requests');
    assert.deepEqual(errors, []);
    console.log('PASS classic performance: virtual scroll/folds, real SSE, partial/replayed points, coalescing, lazy watch, fallback, hidden MJPEG/lightbox/Log');
  } finally {
    if (browser) await browser.close();
    if (host) await stopFixture(host.child);
    H.fs.rmSync(root, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
