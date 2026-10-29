// Observed acceptance journey for the retained HTML review; fixture only, no fixed ports.
'use strict';
const { chromium } = require('/home/bam/aruco-studio/web/node_modules/playwright');
const H = require('../tests/entity_harness');
const { startFixture, stopFixture } = require('./bench_classic');
(async () => {
  const media = H.path.join(H.ROOT, 'reports/media/classic-perf');
  const root = H.fs.mkdtempSync(H.path.join(H.os.tmpdir(), 'classic-video-'));
  let host, browser;
  try {
    host = await startFixture(root, true);
    browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: media, size: { width: 1440, height: 900 } } });
    const created = Date.now();
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await H.open(page, host.base, H.BOARD, 'state');
    await page.waitForSelector('.d6-sp');
    await H.wait(1200);
    const begin = (Date.now() - created) / 1000;
    await H.wait(3000);
    await page.evaluate(() => { const detail = document.getElementById('detail'); detail.scrollTop = detail.scrollHeight; });
    await page.waitForSelector('.d6-sr[data-er-id="voltage"]');
    await H.wait(2000);
    await page.screenshot({ path: H.path.join(media, 'accept-bottom.png') });
    await page.evaluate(() => { document.getElementById('detail').scrollTop = 0; });
    await page.waitForSelector('[data-er-fold="load"]');
    await page.click('[data-er-fold="load"]');
    await H.wait(1200);
    await page.screenshot({ path: H.path.join(media, 'accept-folded.png') });
    await page.click('[data-er-fold="load"]');
    await page.waitForSelector('.d6-sp');
    await H.wait(1800);
    H.fs.writeFileSync(H.path.join(root, 'pause'), '1');
    await H.wait(5000);
    await page.screenshot({ path: H.path.join(media, 'accept-heard.png') });
    const seconds = (Date.now() - created) / 1000 - begin;
    const raw = await page.video().path();
    await context.close();
    H.fs.writeFileSync(H.path.join(media, 'hero.json'), JSON.stringify({ raw, begin, seconds, errors }, null, 2));
    if (errors.length) throw Error(errors.join('\n'));
    console.log(JSON.stringify({ raw, begin, seconds }));
  } finally {
    if (browser) await browser.close();
    if (host) await stopFixture(host.child);
    H.fs.rmSync(root, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
