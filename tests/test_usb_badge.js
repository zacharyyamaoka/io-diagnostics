// A camera's USB generation as a badge right after its name in the tree (Zach, 2026-11-10: "at a glance I want to be able to quickly
// see if its usb 2.1 or 3.1 ... a little badge ... right after the text and before the small grey info text").
//
//   node tests/test_usb_badge.js     (a fixture host on a scratch port; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check } = H;

async function measure(page) {
  return page.$$eval('#tree-host [role=treeitem][data-t6-path]', (rows) => rows.map((row) => {
    const badge = row.querySelector('[data-t6-usb]');
    const name = row.querySelector('.t6-name');
    const after = [...row.children].indexOf(badge);
    const hint = row.querySelector('.t6-heard, .t6-lead, .t6-note');
    const b = badge && badge.getBoundingClientRect();
    const r = row.getBoundingClientRect();
    return { path: row.dataset.t6Path, icon: row.querySelector('svg use') && row.querySelector('svg use').getAttribute('href'), badge: badge ? badge.textContent : null,
      tip: badge ? badge.title : null, afterName: Boolean(badge) && b.left >= name.getBoundingClientRect().right - 0.5,
      beforeHint: !badge || !hint || [...row.children].indexOf(hint) > after, inside: !badge || b.right <= r.right + 0.5 };
  }));
}

async function main() {
  const server = await H.fixtureHost();
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    for (const width of [280, 600]) {
      const context = await H.newContext(browser);
      await context.addInitScript((w) => { try { localStorage.setItem('io-diagnostics.lx.l1side', w); } catch (_) {} }, String(width));
      const page = await context.newPage();
      await page.goto(`${server.base}/`);
      await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('#tree-host [role=treeitem]'), null, { timeout: 30000 });
      await page.waitForTimeout(800);
      const rows = await measure(page);
      const camera = rows.find((row) => row.path === H.CAMERA);
      const adapter = rows.find((row) => row.path === H.ADAPTER);
      check(`${width}px: the camera row carries its USB generation`, camera && /^USB (\d(\.\d)?|4)\b/.test(camera.badge || ''), camera);
      check(`${width}px: right after the name, before any grey hint, inside the row`, camera && camera.afterName && camera.beforeHint && camera.inside, camera);
      check(`${width}px: the tooltip keeps the raw link`, camera && /link USB/.test(camera.tip || ''), camera && camera.tip);
      check(`${width}px: an fdcanusb row has no badge`, adapter && adapter.badge === null, adapter);
      await context.close();
    }
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
}

main().catch((error) => { console.error(error); process.exit(1); });
