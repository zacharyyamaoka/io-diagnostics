// The enlarged picture is there every time (Zach, 2026-11-10: "opening the pictures is a bit buggy. sometimes it seems to work sometimes
// not", a lightbox with its caption and no image). The browser keeps 6 connections per host, shared by every tab of it, and each camera
// page held one per live tile plus its feed: with a second tab open the lightbox's stream queued behind them and drew nothing.
//
//   node tests/test_lightbox_picture.js     (a fixture host on a scratch port; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check } = H;

async function cameraPage(context, base) {
  const page = await context.newPage();
  await H.open(page, base, H.CAMERA, 'state');
  await page.waitForFunction(() => document.querySelectorAll('[data-d6-enlarge]:not([disabled])').length >= 2, null, { timeout: 30000 });
  await page.waitForTimeout(1500);
  return page;
}

async function main() {
  const server = await H.fixtureHost();
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const context = await H.newContext(browser);  // one browser profile: one connection pool for every tab
    const pages = [await cameraPage(context, server.base), await cameraPage(context, server.base)];
    const page = pages[1];
    const results = [];
    for (let i = 0; i < 6; i += 1) {
      const which = ['color', 'depth'][i % 2];
      await page.click(`[data-d6-enlarge="${which}"]`);
      const shown = await page.waitForFunction(() => {
        const img = document.querySelector('[data-d6-lb-img]');
        if (!img) return false;
        const box = img.getBoundingClientRect();
        const back = getComputedStyle(img).backgroundImage;
        const still = back && back !== 'none' && performance.getEntriesByType('resource').some((entry) => back.includes(new URL(entry.name).search) && entry.responseEnd > 0);
        return box.height > 100 && (img.naturalWidth > 0 || still) ? { live: img.naturalWidth > 0, still: Boolean(still), h: Math.round(box.height) } : false;
      }, null, { timeout: 2500 }).then((handle) => handle.jsonValue()).catch(() => null);
      results.push({ which, shown });
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
    }
    check('two tabs open: every enlarge shows a picture within 2.5 s', results.every((result) => result.shown), results);
    const tiles = await pages[1].evaluate(() => { document.querySelector('[data-d6-enlarge]').click(); return new Promise((done) => setTimeout(() => done(
      [...document.querySelectorAll('[data-d6-image]')].map((img) => img.getAttribute('src') || '')), 600)); });
    check('while it is open the tiles show single frames, holding no stream', tiles.length && tiles.every((src) => !src.includes('/frames/stream')), tiles);
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
}

main().catch((error) => { console.error(error); process.exit(1); });
