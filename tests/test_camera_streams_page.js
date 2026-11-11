// A camera tile whose stream the driver says it does not stream: the placeholder reads "not streaming · <why>" instead of waiting for a
// first frame for ever, and the other tiles keep their pictures. The fixture host's real camera page; the host's answer is rewritten on
// the wire for ONE stream (the fixture plugin does not say `watch streams`; the host side is tests/test_camera_streams.py).
//
//   node tests/test_camera_streams_page.js     (a fixture host on a scratch port; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, CAMERA } = H;

const WHY = 'the camera on a USB 2 link (480 Mbit/s) accepts no stream set with depth (RuntimeError: Couldn\'t resolve requests)';

(async () => {
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  const main = await H.fixtureHost('F1');
  try {
    const context = await H.newContext(browser, 'light');
    const page = await context.newPage();
    const watch = H.watchErrors(page, main.base);
    await page.route('**/api/v1/**', async (route) => {
      const url = route.request().url();
      if (!/\/api\/v1\/(values|node)\?/.test(url) || route.request().headers().accept === 'text/event-stream') return route.continue();
      const response = await route.fetch();
      let body;
      try { body = await response.json(); } catch (_) { return route.fulfill({ response }); }
      for (const stream of body.streams || []) if (stream.name === 'depth') Object.assign(stream, { off: WHY, seq: null, width: null, height: null, fps: null });
      return route.fulfill({ response, json: body });
    });
    await H.open(page, main.base, CAMERA, 'state');
    await page.waitForSelector('.d6-tile', { timeout: 20000 });
    await page.waitForFunction(() => [...document.querySelectorAll('.d6-tile')].some((t) => /not streaming/.test(t.textContent)), null, { timeout: 15000 });
    const tiles = await page.evaluate(() => [...document.querySelectorAll('.d6-tile')].map((t) => ({
      name: t.querySelector('.d6-sw-t').textContent, placeholder: (t.querySelector('.d6-ph') || {}).textContent || null, image: Boolean(t.querySelector('img')),
      enlarge: t.querySelector('[data-d6-enlarge]').disabled })));
    const depth = tiles.find((t) => t.name === 'Depth');
    const color = tiles.find((t) => t.name === 'Color');
    check('the depth tile says it is not streaming, and why', depth && depth.placeholder === `not streaming · ${WHY}` && !depth.image && depth.enlarge, depth);
    check('it no longer says it waits for a first frame', !/waiting for the first frame/.test(await page.textContent('.d6-tiles')), depth);
    check('the colour tile keeps its picture', color && color.image && !color.placeholder && !color.enlarge, color);
    const box = await page.evaluate(() => { const ph = [...document.querySelectorAll('.d6-ph')].find((e) => /not streaming/.test(e.textContent)); const r = ph.getBoundingClientRect(); const p = ph.parentElement.getBoundingClientRect(); return { inside: r.left >= p.left - 1 && r.right <= p.right + 1, scrolls: ph.scrollWidth > ph.clientWidth + 1 }; });
    check('the sentence wraps inside its tile', box.inside && !box.scrolls, box);
    check('no page errors', watch.errors.length === 0, watch.errors);
  } finally {
    H.stopServer(main.child);
    await browser.close();
  }
  H.summary();
})().catch((error) => { console.error(error); process.exit(1); });
