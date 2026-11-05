// The hero of the instant-pages report: Preview, the real host, walked by hand speed through the rows that used to sit on "loading…". Read-only:
// it clicks tree rows and tabs and nothing that writes, and it records how long each page took to show something useful.
//
//   node docs/shoot_instant_pages.js /home/bam/io-diagnostics/reports/media/instant-pages-2026-11-04
'use strict';

const path = require('path');
const fs = require('fs');
const H = require('../tests/entity_harness');
const { chromium } = require(H.PLAYWRIGHT);

const OUT = process.argv[2];
const BASE = process.argv[3] || 'http://127.0.0.1:8821';
const SIZE = { width: 1440, height: 860 };
const STEPS = ['fdcanusb · 0607623A', 'CAN 11', 'bam-GPU', 'fdcanusb · 272813EF', 'CAN 14', 'CAN 1'];

(async () => {
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  const context = await browser.newContext({ viewport: SIZE, colorScheme: 'light', recordVideo: { dir: OUT, size: SIZE } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.goto(BASE);
  await page.waitForSelector('text=CAN 11', { timeout: 15000 });
  await page.waitForTimeout(1200);
  const paints = [];
  for (const name of STEPS) {
    const started = Date.now();
    await page.getByText(name, { exact: false }).first().click();
    // WHY a poll of the page's words: "useful" is its title being this row, its tabs drawn and no "loading…" under it, not a request finishing
    const drawn = await page.waitForFunction((title) => {
      const heading = document.querySelector('.d-title');
      return heading && heading.textContent.includes(title) && document.querySelector('.d6-tab') && !/loading…/.test(document.body.innerText);
    }, name, { timeout: 5000, polling: 'raf' }).then(() => true, () => false);
    paints.push({ name, ms: Date.now() - started, drawn, heard: await page.$eval('[data-d6-heard]', (element) => element.textContent).catch(() => null) });
    await page.waitForTimeout(1500);
    if (name === 'CAN 11') {
      await page.locator('button.d6-tab', { hasText: 'Log' }).first().click();
      await page.waitForTimeout(1500);
    }
  }
  const video = page.video();
  await context.close();
  fs.renameSync(await video.path(), path.join(OUT, 'hero.webm'));
  fs.writeFileSync(path.join(OUT, 'hero-facts.json'), JSON.stringify({ paints, errors }, null, 1));
  console.log(JSON.stringify({ paints, errors }));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
