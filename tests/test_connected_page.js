// Connected only, on the page (Zach, 2026-11-09: "maybe it's simply just about adding a little button there at the top that I toggle
// to be like, just show connected things"): the Devices header's plug button, beside collapse / expand / View options / the eye.
//
//   node tests/test_connected_page.js     (a fixture host on a scratch port; no hardware, never :8820-8822)
//
// On a fixture host whose /state is patched so one board was heard long ago (stale) and one answers now (live, as a presence pass
// says): the button hides the stale board and keeps the live one, is remembered across a reload, keeps the page beating
// POST /api/v1/presence (page-only headers) while on and stops when off, and composes with Group by and the eye.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check } = H;

const STALE = { state: 'stale', age_s: 2463, restored: false, text: 'last heard 41 min ago' };
const LIVE = { state: 'live', age_s: 0.4, restored: false, text: 'answers now' };

function patch(value) {
  if (Array.isArray(value)) value.forEach(patch);
  else if (value && typeof value === 'object') {
    if (value.path === H.BOARD2 && 'presence' in value) Object.assign(value, { presence: STALE });
    if (value.path === H.BOARD && 'presence' in value) Object.assign(value, { presence: LIVE });
    Object.values(value).forEach(patch);
  }
  return value;
}

const row = (page, entity) => page.$(`#tree-host [role=treeitem][data-t6-path="${entity}"]`);
const rows = (page) => page.$$eval('#tree-host [role=treeitem][data-t6-path]', (items) => items.map((item) => item.dataset.t6Path));

async function settle(page) {
  await page.waitForTimeout(1300); // one poll of /state and a render
}

async function main() {
  const server = await H.fixtureHost();
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const context = await H.newContext(browser);
    const page = await context.newPage();
    const beats = [];
    page.on('request', (request) => {
      if (request.url().endsWith('/api/v1/presence') && request.method() === 'POST') beats.push({ at: Date.now(), headers: request.headers() });
    });
    // WHY the feed held, unanswered: a page with a feed goes by its snapshot; the patched /state reaches it on its polling fallback
    await context.route('**/api/v1/live', () => {});
    await context.route(/\/api\/v1\/(state|node)/, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: patch(await response.json()) });
    });
    await page.goto(`${server.base}/`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('#tree-host [role=treeitem]'), null, { timeout: 30000 });
    await page.evaluate(() => window.IOLayoutHost && window.IOLayoutHost.ctx && window.IOLayoutHost.ctx.tree.expandAll());
    await settle(page);

    // ---- the button, where Zach asked for it
    const tools = await page.$$eval('.l1-tools button', (buttons) => buttons.map((button) => button.id || button.getAttribute('aria-label') || button.title));
    check('the button sits in the Devices header row, beside the eye', tools[tools.length - 2] === 'connected-only' && /hidden/i.test(tools[tools.length - 1]) && tools.length >= 5, tools);
    const off = await page.$eval('#connected-only', (button) => ({ pressed: button.getAttribute('aria-pressed'), title: button.title }));
    check('off by default, and says what it does', off.pressed === 'false' && off.title === 'Connected only: show just what answers now', off);
    check('off: the stale board is in the tree (the model pads it in)', Boolean(await row(page, H.BOARD2)) && Boolean(await row(page, H.BOARD)), await rows(page));
    await page.waitForTimeout(3500);
    check('off: the page does not ask the host to look', beats.length === 0, beats.length);

    // ---- on
    await page.click('#connected-only');
    await settle(page);
    const on = await page.$eval('#connected-only', (button) => ({ pressed: button.getAttribute('aria-pressed'), title: button.title }));
    check('on: pressed, and says how to get everything back', on.pressed === 'true' && /Click to show everything/.test(on.title), on);
    check('on: the stale board is gone, the live one stays', !(await row(page, H.BOARD2)) && Boolean(await row(page, H.BOARD)), await rows(page));
    check('on: the camera (heard) stays', Boolean(await row(page, H.CAMERA)), await rows(page));
    const shot = H.path.join(H.ROOT, 'reports', 'media', 'connected-only');
    H.fs.mkdirSync(shot, { recursive: true });
    await page.screenshot({ path: H.path.join(shot, 'fixture-on.png') });
    await page.waitForTimeout(3500);
    check('on: the page beats POST /api/v1/presence, with the page-only header', beats.length >= 1 && beats.every((beat) => beat.headers['x-io-diagnostics'] === 'ui'), beats.length);
    check('on: remembered in this browser', await page.evaluate(() => localStorage.getItem('io-diagnostics.connected-only')) === '1');

    // ---- composes with the eye ("Show N hidden") and Group by
    const before = (await rows(page)).length;
    await page.click('.l1-tools button[aria-label^="Show"], .l1-tools button[aria-label^="Hide"]').catch(() => {});
    await settle(page);
    const revealed = await rows(page);
    check('on + the eye: hidden rows show, the stale board still does not', revealed.length >= before && !revealed.includes(H.BOARD2), { before, after: revealed.length });
    await page.click('.l1-tools button[aria-label^="Show"], .l1-tools button[aria-label^="Hide"]').catch(() => {});
    await page.evaluate(() => { localStorage.setItem('io-diagnostics.structure', 'p1'); });

    // ---- a reload keeps it
    await page.reload();
    await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('#tree-host [role=treeitem]'), null, { timeout: 30000 });
    await page.evaluate(() => window.IOLayoutHost && window.IOLayoutHost.ctx && window.IOLayoutHost.ctx.tree.expandAll());
    await settle(page);
    check('reload: still on, under Group by Physical, and still filtered', await page.$eval('#connected-only', (button) => button.getAttribute('aria-pressed')) === 'true'
      && !(await row(page, H.BOARD2)) && Boolean(await row(page, H.BOARD)), await rows(page));

    // ---- off again: everything back, and the beats stop
    await page.click('#connected-only');
    await settle(page);
    check('off again: the stale board is back', Boolean(await row(page, H.BOARD2)), await rows(page));
    const count = beats.length;
    await page.waitForTimeout(4000);
    check('off again: no more beats', beats.length === count, { before: count, after: beats.length });
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
}

main().catch((error) => { console.error(error); process.exit(1); });
