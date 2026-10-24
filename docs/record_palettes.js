#!/usr/bin/env node
// Screenshots of the health-cell palette (detail/health.js PALETTE: Atlassian Statuspage's colours, thin 3 px bars) on the same three real
// entities, light and dark, and ONE side-by-side image of them. Recorded from the real page (d1a, the Diagnostics default) on a fixture host
// in headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics-lanes/diagnostics-variants/docs/record_palettes.js --out /abs/dir --f1 http://127.0.0.1:PORT
//
// --f1 is a fixture host you run (`cli.py serve --fixture F1 --plugins <fixture plugins + the Computer plugin> --no-udev`) and have left
// running long enough for its history to show an ok -> stale step (about 10 minutes). Writes <out>/colours-<entity>-<light|dark>.png
// (1400x900), colours-crop-<entity>-<scheme>.png (the Diagnostics section's top) and colours-comparison.png. Nothing touches hardware.
'use strict';

const fs = require('fs');
const path = require('path');

const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const arg = (name) => { const at = process.argv.indexOf(name); return at > 0 ? process.argv[at + 1] : null; };
const OUT = arg('--out');
const BASE = arg('--f1');
if (!OUT || !BASE) { console.error('usage: record_palettes.js --out DIR --f1 URL'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const ENTITIES = {
  computer: { sel: 'host', name: 'the Computer', crop: 330 },
  can15: { sel: 'host/usb1/port-1-3/usb-1-3/can/15/driver', name: 'CAN 15 driver (fault 33)', crop: 200 },
  stale: { sel: 'host/usb1/port-1-3/usb-1-3/can', name: 'a CAN bus that went stale', crop: 200 },
};

async function main() {
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const crops = {};
  try {
    for (const scheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: scheme });
      await context.addInitScript(() => { try { localStorage.setItem('io-diagnostics.detail-layout', 'd1a'); } catch (_) { /* */ } });
      const page = await context.newPage();
      for (const [entity, target] of Object.entries(ENTITIES)) {
        await page.goto(`${BASE}/?sel=${encodeURIComponent(target.sel)}`);
        await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
        await page.waitForSelector('#detail [data-dx="d1a"] .ioh i', { timeout: 30000 });
        await page.waitForTimeout(1200);
        await page.screenshot({ path: path.join(OUT, `colours-${entity}-${scheme}.png`) });
        const box = await page.locator('.sec.dx-diag').boundingBox();
        const file = `colours-crop-${entity}-${scheme}.png`;
        await page.screenshot({ path: path.join(OUT, file), clip: { x: box.x, y: box.y, width: box.width, height: Math.min(target.crop, box.height) } });
        crops[`${entity}/${scheme}`] = file;
        console.log(`shot  ${entity} ${scheme}`);
      }
      await context.close();
    }
    const page = await browser.newPage({ viewport: { width: 1600, height: 400 } });
    const uri = (file) => `data:image/png;base64,${fs.readFileSync(path.join(OUT, file)).toString('base64')}`;
    const rows = Object.entries(ENTITIES).map(([entity, target]) => `<div class="row"><div class="t">${target.name}</div>`
      + `<div><div class="s">light</div><img src="${uri(crops[`${entity}/light`])}"></div><div><div class="s">dark</div><img src="${uri(crops[`${entity}/dark`])}"></div></div>`).join('');
    await page.setContent(`<style>body{margin:0;background:#777;font:12px system-ui;color:#fff}.row{display:grid;grid-template-columns:150px 1fr 1fr;gap:10px;padding:8px;border-bottom:3px solid #555;align-items:start}`
      + `.t{font:600 13px system-ui;padding-top:16px}.s{margin:0 0 2px;color:#ddd}img{width:100%;display:block;border-radius:4px}</style>${rows}`);
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, 'colours-comparison.png'), fullPage: true });
    console.log('shot  colours-comparison.png');
  } finally {
    await browser.close();
  }
}
main().catch((error) => { console.error(error); process.exit(1); });
