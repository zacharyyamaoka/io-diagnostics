#!/usr/bin/env node
// The tree's past on a host that has been up longer than its window (PEP 0001 §15, §16): the case the other checks never see, since
// their fixture host is seconds old when the page opens. Then the host's /history/tree starts at the window's start, the page's
// version of each row before it opened sits at the edge of what the page keeps, and a keyed store that lets go of everything older
// than its window but each key's newest (data-ui 5b4e793) took it within a tick: scrubbed back, the tree was empty ("Nothing
// matches"), and so was the timeline's Rack strip. Measured in the REAL page (the tree's own rows, the strip's own bins):
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_dx_tree_past.js
//
// A fixture host (F1, the two fixture plugins only) with a 60 s window (its smallest), up for 70 s before the page opens.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, fs, os, path } = H;

const WINDOW_S = 60;
const rowsOf = (page) => page.evaluate(() => [...document.querySelectorAll('#dx-tree [role=treeitem][data-t6-path]')].map((r) => r.getAttribute('data-t6-path')));

(async () => {
  let server;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding'] });
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dx-tree-past-'));
  try {
    server = await H.startServer(root, 'F1', {}, { args: ['--history-window', `${WINDOW_S}s`] });
    const { base } = server;
    const plugins = await H.getJson(base, '/api/v1/plugins');
    check('only the two fixture plugins are loaded', plugins.plugins.map((p) => p.name).sort().join() === 'state_camera,state_joint');
    const settings = await H.getJson(base, '/api/v1/settings');
    check(`the host keeps a ${WINDOW_S} s window`, settings.history_window_s === WINDOW_S, settings.history_window_s);
    await H.wait((WINDOW_S + 10) * 1000);   // the host's history now spans its whole window
    const page = await (await H.newContext(browser, 'light')).newPage();
    const errs = H.watchErrors(page, base);
    await page.goto(`${base}/dx/?go=explore`);
    await page.waitForFunction(() => window.robotdx && window.robotdx.feed.treeBack && document.querySelectorAll('#dx-tree [role=treeitem][data-t6-path]').length >= 5, null, { timeout: 30000 });
    const back = await page.evaluate(() => ({ ...window.robotdx.feed.treeBack, opened: window.robotdx.feed.openedAt }));
    check('the host\'s tree history starts at its window\'s start (the case under test)', back.supported && Number.isFinite(back.from) && back.opened - back.from >= WINDOW_S - 5, back);
    const now = await rowsOf(page);
    await page.waitForTimeout(12000);   // the page's window moves on 12 s past where the first past versions were written
    // the bins of the Rack strip between `from` and the page's open: what the host kept for the rack before the page opened
    const stripBins = (from) => page.evaluate((lo) => {
      const v = document.querySelector('#dx-tl-health [data-t0]');
      if (!v) return null;
      const t0 = Number(v.dataset.t0), t1 = Number(v.dataset.t1), opened = window.robotdx.feed.openedAt;
      const bins = [...v.querySelectorAll('[data-strip] [data-bin]')];
      const a = Math.ceil(((Math.max(lo, t0) - t0) / (t1 - t0)) * bins.length), b = Math.floor(((Math.min(opened, t1) - t0) / (t1 - t0)) * bins.length);
      const span = bins.slice(Math.max(0, a), Math.max(0, b));
      return { t0, t1, from: lo, filled: span.filter((x) => x.dataset.level).length, of: span.length };
    }, from);
    const at = async (t) => {
      await page.evaluate((x) => window.robotdx.clock.seek(x), t);
      await page.waitForTimeout(600);
      const rows = await rowsOf(page);
      // the clock must land where it was sent: a span that starts later clamps t (then the tree would be today's at the page's open)
      const landed = await page.evaluate(() => window.robotdx.clock.t);
      return { asked: t, t: landed, landed: Math.abs(landed - t) < 0.5, rows: rows.length, lost: now.filter((id) => !rows.includes(id)).slice(0, 4) };
    };
    // what the page keeps starts at its window's far edge: the newest data less the window
    const cut = await page.evaluate(() => Math.max(window.robotdx.feed.dataset('entities').end, window.robotdx.feed.dataset('summary').end)) - WINDOW_S;
    const near = await at(back.opened - 15);
    check('scrubbed back to before the page opened, 12 s on, the clock lands there and the tree is today\'s rows (none lost)', near.landed && near.rows >= 5 && near.lost.length === 0, near);
    const strip = await stripBins(cut + 8);
    check('…and the timeline\'s Rack strip has the host\'s level in its bins from what the page keeps to the page\'s open', strip && strip.of >= 5 && strip.filled >= strip.of - 1, strip);
    const edge = await at(cut + 8);
    check('…and near the far edge of what the page keeps (8 s inside it), the clock lands there and the tree is today\'s rows', edge.landed && edge.rows >= 5 && edge.lost.length === 0, { ...edge, cut });
    await page.evaluate(() => window.robotdx.clock.goLive());
    check('no page errors, no console errors, no failed request', errs.errors.length === 0, errs.errors.slice(0, 5));
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close().catch(() => {});
    if (server) H.stopServer(server.child);
    fs.rmSync(root, { recursive: true, force: true });
  }
  process.exit(H.summary());
})();
