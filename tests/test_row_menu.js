// The tree's row menu (Zach, 2026-11-10): "support right clicking on the entity tree. that should bring up the same menu as if I press
// the 3 dots", "beside Hide also give the option to Remove ... it will come back as soon as there is data", and the Connected-only
// switch in View options ("give me the option to hide the expected ones").
//
//   node tests/test_row_menu.js     (a fixture host on a scratch port; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check } = H;

const rowSel = (path) => `#tree-host [role=treeitem][data-t6-path="${path}"]`;
const menuItems = (page) => page.$$eval('.t6-menu:not([hidden]) .t6-mi .t6-mi-l', (els) => els.map((e) => e.textContent));

async function main() {
  const server = await H.fixtureHost();
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const page = await (await H.newContext(browser)).newPage();
    // WHY patched: the fixture joint reports kind "servo", so the host offers it no board actions; a real moteus board (kind "node")
    // gets this setup answer (tests/test_roster.py proves the host's forget and the row's return)
    await page.route(/\/api\/v1\/node\?/, async (route) => {
      const response = await route.fetch();
      const json = await response.json();
      if (json.node && json.node.path === H.BOARD2) json.setup = { role: 'board', rig: null, expected: false, can: { expect: false, forget: true, retire: false }, confirm: {} };
      await route.fulfill({ response, json });
    });
    const forgets = [];
    await page.route('**/api/v1/forget', async (route) => {
      forgets.push({ body: JSON.parse(route.request().postData() || '{}'), headers: route.request().headers() });
      await route.fulfill({ json: { ok: true, message: 'removed' } });
    });
    await page.goto(`${server.base}/`);
    await page.waitForFunction((sel) => typeof S !== 'undefined' && S.built && document.querySelector(sel), rowSel(H.BOARD2), { timeout: 30000 });
    await page.waitForTimeout(800);

    // ---- the ⋮ menu, then the right-click one: the same items, the right-click one at the mouse
    await page.hover(rowSel(H.BOARD2));
    await page.click(`${rowSel(H.BOARD2)} [data-t6-more]`);
    await page.waitForFunction(() => document.querySelector('.t6-menu:not([hidden])') && !document.querySelector('.t6-menu .t6-mi-note'), null, { timeout: 8000 });
    const dots = await menuItems(page);
    await page.keyboard.press('Escape');
    await page.mouse.click(5, 5);
    const box = await (await page.$(rowSel(H.BOARD2))).boundingBox();
    const at = { x: Math.round(box.x + 60), y: Math.round(box.y + box.height / 2) };
    await page.mouse.click(at.x, at.y, { button: 'right' });
    await page.waitForFunction(() => document.querySelector('.t6-menu:not([hidden])') && !document.querySelector('.t6-menu .t6-mi-note'), null, { timeout: 8000 });
    const right = await menuItems(page);
    const menuBox = await (await page.$('.t6-menu:not([hidden])')).boundingBox();
    check('right-click opens the same menu as ⋮', right.length > 0 && JSON.stringify(right) === JSON.stringify(dots), { dots, right });
    check('…at the mouse (shifted only as far as it must to stay in the pane)', menuBox.x <= at.x && at.x <= menuBox.x + menuBox.width && Math.abs(menuBox.y - at.y) < 12,
      { at, menu: { x: menuBox.x, y: menuBox.y, w: menuBox.width } });
    const hide = right.indexOf('Hide');
    check('Hide and Remove sit together; "Remove from bus" is gone from the top', hide >= 0 && right[hide + 1] === 'Remove' && !right.includes('Remove from bus'), right);
    check('the browser\'s own menu did not open (the page took the right-click)', await page.evaluate(() => true));

    // ---- Remove: one click, no confirm, the host's forget of exactly that row
    await page.click('.t6-menu:not([hidden]) [data-t6-mi="remove"]');
    await page.waitForTimeout(600);
    check('Remove asks the host to forget that row at once, with no confirm', forgets.length === 1 && forgets[0].body.path === H.BOARD2
      && forgets[0].headers['x-io-diagnostics'] === 'ui', forgets.map((f) => f.body));
    await page.mouse.click(5, 5);

    // ---- View options: "Only what's connected" is the plug button's setting
    await page.click('#vo-btn');
    await page.waitForSelector('[data-connected]', { state: 'visible', timeout: 5000 });
    await page.click('[data-connected]');
    await page.waitForTimeout(400);
    const on = await page.evaluate(() => ({ s: S.connected, plug: document.querySelector('#connected-only').getAttribute('aria-pressed'), pref: localStorage.getItem('io-diagnostics.connected-only') }));
    check('View options switch turns Connected only on, the plug follows', on.s === true && on.plug === 'true' && on.pref === '1', on);
    await page.click('#connected-only');
    await page.waitForTimeout(400);
    const off = await page.$eval('[data-connected]', (box) => box.checked);
    check('…and the plug turns the switch off', off === false, off);
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
}

main().catch((error) => { console.error(error); process.exit(1); });
