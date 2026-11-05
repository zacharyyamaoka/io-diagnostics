// The 3D view on Preview, the real host with the real rack, read-only: it opens the page, reads what it shows and takes stills. It clicks
// nothing that writes (no Map devices, no upload), and hovering a chip is the only input.
//
//   node docs/shoot_rack_preview.js /home/bam/io-diagnostics/reports/media/rack-3d-2026-11-04
'use strict';

const path = require('path');
const fs = require('fs');
const H = require('../tests/entity_harness');
const { chromium } = require(H.PLAYWRIGHT);

const OUT = process.argv[2];
const BASE = process.argv[3] || 'http://127.0.0.1:8821';
const ARGS = ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'];

(async () => {
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ARGS });
  const page = await (await browser.newContext({ viewport: { width: 1600, height: 950 }, colorScheme: 'light' })).newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto(`${BASE}/?view=threed`);
  await page.waitForFunction(() => { const d = window.IOThreeD && IOThreeD.debug(); return d && d.scene && d.scene.loaded() && d.states && d.states.length; }, null, { timeout: 180000 });
  await page.waitForTimeout(2500);
  const facts = await page.evaluate(() => {
    const d = IOThreeD.debug();
    return { robot: d.desc.robot, name: d.desc.name, timings: d.scene.timings(), fill: d.scene.viewFill(), live: d.live, source: d.source,
      parts: d.desc.parts.map((part, index) => ({ name: part.name, path: part.path, problem: part.problem, level: d.states[index] && d.states[index].level, ghost: d.states[index] && d.states[index].ghost })),
      hardware: d.desc.hardware.map((item) => ({ name: item.name, kind: item.kind, parts: item.parts.length })), not_in_model: d.desc.not_in_model.map((item) => item.name) };
  });
  await page.screenshot({ path: path.join(OUT, 'preview-home.png') });
  const chip = await page.$('.td-hw-chip[data-td-hw="host/usb1/port-1-5/usb-1-5"]');
  if (chip) {
    await chip.hover();
    await page.waitForTimeout(1500);
    facts.leaders = await page.$$eval('.td-leaders line', (lines) => lines.length);
    await page.screenshot({ path: path.join(OUT, 'preview-chip.png') });
  }
  facts.errors = errors;
  fs.writeFileSync(path.join(OUT, 'preview-facts.json'), JSON.stringify(facts, null, 1));
  console.log(JSON.stringify(facts).slice(0, 1500));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
