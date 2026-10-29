#!/usr/bin/env node
// The time bar spans the host's window, whatever an entity's Log reads (PEP 0001 §16). A Log reads the entity's raw lines (kernel
// lines, run output), which go back to the device's plug: on F1 the adapter's are from its boot, 12 h before the replay. The page's
// one clock spans what its datasets hold, so lines that old stretched the time bar from 15 min to 12 h the moment a Log opened, and
// the window the page can rewind shrank to a sliver of the track. The page keeps raw lines inside the host's window, as it keeps the
// activity backlog (§12.1). Measured in the REAL page (the clock's own span, the track's seconds per pixel, the Log table's own rows);
// the host's own raw answer is the oracle that the case is there:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_dx_time_span.js
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, ADAPTER } = H;

(async () => {
  let host;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding'] });
  try {
    host = await H.fixtureHost('F1');
    const { base } = host;
    const page = await (await H.newContext(browser, 'light')).newPage();
    const errs = H.watchErrors(page, base);
    // the span the clock's bound data covers (liveStart .. liveEnd: what the time bar spans while it follows, whether or not it
    // plays now), and the bar's own start and end
    const span = () => page.evaluate(() => {
      const c = window.robotdx.clock;
      return { liveStart: c.liveStart, liveEnd: c.liveEnd, s: c.liveEnd - c.liveStart, start: c.start, end: c.end, window: window.robotdx.feed.host.window };
    });
    await page.goto(`${base}/dx/?go=explore&sel=${encodeURIComponent(ADAPTER)}`);
    await page.waitForFunction(() => window.robotdx && window.robotdx.clock.live && document.querySelector('#dx-time .dv-pb-track'), null, { timeout: 30000 });
    await page.waitForTimeout(3000);
    const before = await span();
    check('on the adapter\'s overview the time bar spans the host\'s window', before.s >= 0 && before.s <= before.window + 60, before);
    // the oracle: the host's raw lines for the adapter's Log (the scope it reads), and how far back they go
    const q = new URLSearchParams({ fold: '1', channel: 'raw', limit: '1000', severity: 'debug', path: ADAPTER, scope: 'host' });
    const hostRaw = (await H.getJson(base, `/api/v1/activity?${q}`)).events || [];
    const oldestRaw = Math.min(...hostRaw.map((e) => e.received ?? e.stamp));
    check('the host has raw lines for the adapter from long before the window (the case under test)', hostRaw.length > 0 && before.liveEnd - before.window - oldestRaw > 3600, { lines: hostRaw.length, oldest: oldestRaw, windowStart: before.liveEnd - before.window });
    await page.click('#dx-tabs [data-dx-tab="log"]');
    await page.waitForFunction(() => { const t = document.querySelector('#dx-detail-log'); return t && t.model && t.model.rows.length > 0; }, null, { timeout: 15000 });
    await page.waitForTimeout(6500);   // the Log's raw read, and one more (every 5 s)
    const after = await span();
    check('with the Log open the time bar still spans the host\'s window (the clock\'s data from its start to its newest, at most the window)',
      after.s >= 0 && after.s <= after.window + 60 && after.liveStart >= before.liveStart - 1, { before, after });
    const held = await page.evaluate(() => { const t = document.querySelector('#dx-detail-log'); const ts = t.model.rows.map((r) => r.t); return { rows: ts.length, oldest: Math.min(...ts), newest: Math.max(...ts) }; });
    const windowStart = after.liveEnd - after.window;
    check('…and the Log holds nothing from before the host\'s window (the classic Log keeps the older lines)', held.rows > 0 && held.oldest >= windowStart - 1, { ...held, windowStart });
    // a scrub still rewinds the Log: paused just before its newest row, the row is not there; just after it, it is
    const rewind = await page.evaluate(async (newest) => {
      const c = window.robotdx.clock, t = document.querySelector('#dx-detail-log');
      const at = async (x) => { c.seek(x); await new Promise((r) => setTimeout(r, 600)); return { t: c.t, rows: t.model.rows.length, newest: t.model.rows.length ? Math.max(...t.model.rows.map((r) => r.t)) : null }; };
      const out = { before: await at(newest - 1), after: await at(newest + 0.5) };
      c.goLive();
      return out;
    }, held.newest);
    check('…and the Log rewinds with it (a second before its newest row it is not there, half a second after it is)',
      Math.abs(rewind.before.t - (held.newest - 1)) < 0.2 && (rewind.before.newest === null || rewind.before.newest <= rewind.before.t) && rewind.after.newest === held.newest, rewind);
    check('no page errors, no console errors, no failed request', errs.errors.length === 0, errs.errors.slice(0, 5));
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close().catch(() => {});
    if (host) H.stopServer(host.child);
  }
  process.exit(H.summary());
})();
