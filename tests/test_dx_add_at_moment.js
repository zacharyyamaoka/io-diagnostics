#!/usr/bin/env node
// "At this moment" on the RobotDx page (data-ui CONTRACT §34: a layout panel's t pins it; docs/peps/0001-robotdx-on-data-ui.md §17), in
// the REAL page on a fixture host, headless Chrome. Written before the page's fix. The oracle is the host's own State history and
// /values ring read by the test (the newest point at or before t), and the saved file on disk:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_dx_add_at_moment.js
//
// A KPI card of the board's State added At this moment saves the panel with t; opened, that panel stands at t (its views' clock paused
// there) and its card shows the host's value at t, while the page's clock keeps following. Found while wiring Add to dashboard: on
// this page every dataset is live, and a live view does not follow a panel's own (plain) clock, so the panel said "at t = …" and
// drew the newest data. Exits 1 on any failure.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, BOARD, fs, path } = H;

const MOMENT = `# Moments: kept by the person
datasets:
  summary:
    data: "../datasets/summary.json"
layout:
  columns: 12
  rowHeight: 96
  panels:
    - { view: Time, w: 12, h: 1 }
views:
  - type: playback
    name: Time
    from: summary
`;
// the host's own value of `key` at t: the newest point received at or before t, from its /history State series and its /values ring
function hostValueAt(history, values, key, t) {
  let best = null;
  const consider = (when, value) => { if (when <= t && (!best || when >= best.when)) best = { when, value }; };
  for (const s of history.statuses || []) for (const m of s.measurements || []) if (m.key === key) for (const [when, value] of m.points) consider(when, value);
  for (const g of values.groups || []) for (const r of g.rows || []) if (r.key === key) for (const [when, value] of r.points) consider(when, value);
  return best;
}

(async () => {
  let host;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding'] });
  try {
    host = await H.fixtureHost('F1');
    const { base } = host;
    const dir = path.join(host.root, 'config', 'dashboards');
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, 'Moments.base');
    fs.writeFileSync(file, MOMENT);
    const page = await (await H.newContext(browser, 'light')).newPage();
    const errs = H.watchErrors(page, base);
    await page.addInitScript(() => {
      window.__adds = [];
      addEventListener('data-add-to-dashboard', (e) => window.__adds.push(JSON.parse(JSON.stringify({ panel: e.detail.panel }))), true);
    });
    await page.goto(`${base}/dx/?go=explore&sel=${encodeURIComponent(BOARD)}&page=state`);
    // the position card: a KPI card of the board's State (its label carries the key)
    const card = await H.waitFor(() => page.evaluate(() => {
      const c = [...document.querySelectorAll('#dx-detail-state data-kpi[data-i]')].find((k) => /^position\b/.test((k.querySelector('.dv-kpi-label') || {}).textContent || ''));
      return c ? c.dataset.i : null;
    }), 20000);
    await page.waitForTimeout(3000);   // a few seconds of State behind t
    await page.click(`#dx-detail-state data-kpi[data-i="${card}"]`, { button: 'right' });
    await page.click('.dv-itemmenu [data-add-dashboard]', { timeout: 5000 });
    await page.waitForSelector('.dv-addpick [data-dashboard-id="Moments"]', { timeout: 10000 });
    await page.check('.dv-addpick input[name=at-moment]');
    await page.click('.dv-addpick [data-dashboard-id="Moments"]');
    const state = await H.waitFor(() => page.evaluate(() => { const s = document.querySelector('.dv-addpick-status'); return s && /added|error/.test(s.dataset.state) ? s.dataset.state : null; }), 15000);
    const added = await page.evaluate(() => window.__adds[window.__adds.length - 1] || null);
    const t = added && added.panel ? added.panel.t : null;
    // the host's own State around t, read now (its /values ring still holds the points near t)
    const [history, values] = await Promise.all([H.getJson(base, `/api/v1/history?path=${encodeURIComponent(BOARD)}`), H.getJson(base, `/api/v1/values?path=${encodeURIComponent(BOARD)}`)]);
    const oracle = Number.isFinite(t) ? hostValueAt(history, values, 'position', t) : null;
    const saved = fs.readFileSync(file, 'utf8');
    const last = await page.evaluate((text) => { const b = window.robotdx.parseBase(text); return b.layout.panels[b.layout.panels.length - 1]; }, saved);
    check('At this moment saves the panel with its t (the instant the reader saw)', state === 'added' && Number.isFinite(t) && last && last.t === t, { state, t, panel: last });

    // ---- opened (Open ↗): the panel stands at t, the card shows the host's value at t, the page keeps following ----------------------
    // WHY Open and not a fresh load: the page then holds the State it drew at t (a fresh load's backlog is the host's 1 Hz history)
    await page.click('.dv-addpick-status a[href]', { timeout: 5000 });
    await page.waitForFunction(() => document.querySelector('data-dashboard.dx-board .dd-panel .dd-at'), null, { timeout: 15000 });
    await page.waitForTimeout(4000);   // the page follows on; a panel that follows too has moved past t by now
    const pinned = await page.evaluate(async () => {
      const { clockFor } = await import('/vendor/data-ui/data-ui.js');
      const sec = document.querySelector('data-dashboard.dx-board .dd-panel .dd-at').closest('.dd-panel');
      const v = sec.querySelector('data-views');
      const c = clockFor(v);
      const num = sec.querySelector('.dv-kpi-num');
      const exact = num ? (num.dataset.exact || (num.getAttribute('title') || '').split(' ')[0] || num.textContent) : null;
      return { at: sec.querySelector('.dd-at').textContent, t: c ? c.t : null, following: c ? c.following : null, card: exact === null ? null : Number(exact),
        pageFollowing: window.robotdx.clock.following, pageT: window.robotdx.clock.t };
    });
    check('…opened, its views stand at t (their clock paused there), while the page\'s clock follows on',
      Number.isFinite(t) && pinned.following === false && Math.abs(pinned.t - t) < 0.002 && pinned.pageFollowing === true && pinned.pageT > t + 2, { t, pinned });
    check('…and its card shows the host\'s own value at t (the newest point at or before it), not the newest one',
      oracle !== null && Number.isFinite(pinned.card) && Math.abs(pinned.card - oracle.value) <= 1e-6 + 1e-4 * Math.abs(oracle.value), { card: pinned.card, host: oracle });
    check('no page errors, no console errors, no failed request', errs.errors.length === 0, errs.errors.slice(0, 5));
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close().catch(() => {});
    if (host) H.stopServer(host.child);
  }
  process.exit(H.summary());
})();
