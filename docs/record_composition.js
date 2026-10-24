#!/usr/bin/env node
// The composites' media (docs/composition-v1.md): screenshots in light and dark at 1400x900 of the hierarchy in the tree, an arm's State with its
// "↳ CAN n" links, the rollup's Diagnostics, "Calibrate all 6…" (the Utilities list, its dry-run sheet, the confirm, the six runs queued and
// running), a broken file as one UNKNOWN on the Computer; and a hero video recorded from the REAL page on the fixture host
// (tests/fixtures/plugins-state, F1: two fdcanusb adapters of three joints, an arm plugin, two cameras; it never touches hardware):
// open an arm, follow a child link, open "Calibrate all 6…".
//
//   /usr/bin/node /home/bam/io-diagnostics-lanes/composition/docs/record_composition.js [--out /abs/dir]
//
// Writes <out>/*.png, hero.mp4 + hero.gif and media.json (default <checkout>/reports/media/composition).
'use strict';

const H = require('../tests/composition_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const outIndex = process.argv.indexOf('--out');
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : path.join(H.ROOT, 'reports', 'media', 'composition');
const FFMPEG = '/usr/bin/ffmpeg';
const { PATHS } = H;
const steps = [];
const step = (name, ok, detail) => { steps.push({ step: name, ok: Boolean(ok), detail }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  · ${detail}` : ''}`); };

const CURSOR_SCRIPT = `
  window.addEventListener('DOMContentLoaded', () => {
    const dot = document.createElement('div');
    dot.style.cssText = 'position:fixed;left:-40px;top:-40px;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;'
      + 'background:rgba(255,255,255,.85);border:2px solid #111;box-shadow:0 0 0 3px rgba(255,255,255,.25);pointer-events:none;z-index:2147483647;transition:transform .12s ease';
    document.body.appendChild(dot);
    document.addEventListener('mousemove', (e) => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', () => { dot.style.transform = 'scale(.6)'; }, true);
    document.addEventListener('mouseup', () => { dot.style.transform = 'scale(1)'; }, true);
  });`;
async function glide(page, locator, dx = 0) {
  const box = await locator.boundingBox();
  if (!box) throw new Error('target not visible');
  const x = box.x + Math.min(box.width / 2, 90) + dx;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y, { steps: 14 });
  await page.waitForTimeout(220);
  await page.mouse.click(x, y);
}
async function encode(videoPath, name, trimS) {
  const mp4 = path.join(OUT, `${name}.mp4`);
  const done = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-ss', String(trimS), '-i', videoPath, '-an', '-vf', 'fps=15', '-c:v', 'libx264', '-preset', 'slow', '-crf', '31', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
  step(`${name}.mp4 encoded`, done.status === 0, String(done.stderr || '').slice(0, 200));
  const gif = path.join(OUT, `${name}.gif`);
  const palette = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', mp4, '-vf', 'fps=6,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=80[p];[b][p]paletteuse=dither=none', gif]);
  step(`${name}.gif fallback encoded`, palette.status === 0, String(palette.stderr || '').slice(0, 200));
  const probe = spawnSync('/usr/bin/ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4]);
  const seconds = Number(String(probe.stdout).trim());
  step(`${name}: the loop is 6-15 s`, seconds >= 6 && seconds <= 15, `${seconds.toFixed(1)} s`);
  return seconds;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const server = await H.compositionHost({ actS: 1.4 });
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  const shots = [];
  const shoot = async (page, name) => { await page.screenshot({ path: path.join(OUT, `${name}.png`) }); shots.push(name); };
  const armReady = () => H.waitFor(async () => ((await H.getJson(server.base, `/api/v1/node?path=${encodeURIComponent(PATHS.arm1)}`)).checks || []).some((c) => c.name === 'Arm 1: all joints answer' && c.level === 'ok'), 40000, 400);
  try {
    await armReady();
    // ---- screenshots, light and dark ------------------------------------------------------------------------------------------------------------
    for (const scheme of ['light', 'dark']) {
      const context = await H.newContext(browser, scheme, { deviceScaleFactor: 1 });
      const page = await context.newPage();
      await H.open(page, server.base, PATHS.rack, 'diagnostics');
      await page.waitForTimeout(900);
      await shoot(page, `hierarchy-${scheme}`);
      await H.goTo(page, PATHS.arm1);
      await H.ready(page);
      await page.waitForTimeout(500);
      await shoot(page, `arm-diagnostics-${scheme}`);
      await H.goTab(page, 'state');
      await H.waitFor(() => page.evaluate(() => document.querySelectorAll('.d6-src').length >= 3), 15000, 200);
      await page.waitForTimeout(1800);
      await shoot(page, `arm-state-${scheme}`);
      await H.goTab(page, 'utilities');
      await page.waitForTimeout(300);
      await shoot(page, `arm-utilities-${scheme}`);
      await H.goTo(page, PATHS.rack);
      await H.ready(page);
      await H.goTab(page, 'utilities');
      await page.waitForTimeout(300);
      await page.click('.d6-panel [data-util="all:calibrate"]');
      await page.waitForSelector('.ent-sheet [data-batch-runs]', { timeout: 20000 });
      await page.waitForTimeout(400);
      await shoot(page, `batch-dry-run-${scheme}`);
      await context.close();
    }
    // the batch itself runs last: a run on a board stops that board's watch for its length, and the State charts of the shots above show no gap
    {
      const context = await H.newContext(browser, 'light', { deviceScaleFactor: 1 });
      const page = await context.newPage();
      await H.open(page, server.base, PATHS.rack, 'utilities');
      await page.waitForTimeout(500);
      await page.click('.d6-panel [data-util="all:calibrate"]');
      await page.waitForSelector('.ent-sheet [data-batch-runs]', { timeout: 20000 });
      await page.click('.ent-sheet [data-util-run]');
      await page.waitForSelector('#modal:not([hidden]) [data-util-tick]', { timeout: 5000 });
      await page.waitForTimeout(300);
      await shoot(page, 'batch-confirm-light');
      await page.click('#modal [data-util-tick]');
      await page.click('#modal [data-util-confirm]');
      await page.waitForTimeout(3300);
      await shoot(page, 'batch-running-light');
      await H.waitFor(() => page.evaluate(() => S.util && S.util.phase === 'done'), 30000, 300);
      await page.waitForTimeout(500);
      await shoot(page, 'batch-done-light');
      await context.close();
    }
    step('screenshots: hierarchy, arm (diagnostics, state, utilities), the batch (dry run, confirm, running, done), light and dark', shots.length >= 13, `${shots.length}`);

    // ---- a collapsed hierarchy keeps its icon; a followed link; a broken file ------------------------------------------------------------------------------
    const context = await H.newContext(browser, 'light', { deviceScaleFactor: 1 });
    const page = await context.newPage();
    await H.open(page, server.base, PATHS.rack, 'diagnostics');
    await page.waitForTimeout(500);
    await page.click(`[data-t6-toggle="${PATHS.rack}"]`);
    await page.waitForTimeout(300);
    await shoot(page, 'collapsed-light');
    await page.click(`[data-t6-toggle="${PATHS.rack}"]`);
    await H.goTo(page, PATHS.arm1);
    await H.goTab(page, 'state');
    await H.waitFor(() => page.evaluate(() => document.querySelectorAll('.d6-src').length >= 3), 15000, 200);
    await page.click(`a.d6-src[data-go="${PATHS.joint(PATHS.adapter1, 2)}"]`);
    await page.waitForFunction((p) => S.node && S.node.node.path === p, PATHS.joint(PATHS.adapter1, 2), { timeout: 15000 });
    await page.waitForTimeout(700);
    await shoot(page, 'followed-link-light');
    const original = fs.readFileSync(server.expected, 'utf8');
    fs.writeFileSync(server.expected, `${original}\n[[composite]]\nid = "rack/robot-1"\nkind = "robot"\nchildren = ["rack/robot-1/arm"]\n`);
    await H.waitFor(async () => (await H.getJson(server.base, '/api/v1/state')).composition.problems.length > 0, 15000, 300);
    await H.goTo(page, 'host');
    await H.ready(page);
    await page.waitForTimeout(1200);
    await shoot(page, 'broken-file-light');
    fs.writeFileSync(server.expected, original);
    await H.waitFor(async () => (await H.getJson(server.base, '/api/v1/state')).composition.composites.length === 5, 15000, 300);
    await context.close();

    // ---- the hero: open an arm, follow a child link, open "Calibrate all 6…" -----------------------------------------------------------------------------------------
    const videoDir = fs.mkdtempSync(path.join(require('os').tmpdir(), 'composition-video-'));
    const heroContext = await browser.newContext({ viewport: H.VIEWPORT, colorScheme: 'light', recordVideo: { dir: videoDir, size: H.VIEWPORT } });
    await heroContext.addInitScript(CURSOR_SCRIPT);
    const hero = await heroContext.newPage();
    await H.open(hero, server.base, PATHS.rack, 'diagnostics');
    await hero.waitForTimeout(1000);
    await glide(hero, hero.locator(`[data-t6-path="${PATHS.arm1}"]`)); // open the arm from the tree
    await hero.waitForFunction((p) => S.node && S.node.node.path === p, PATHS.arm1, { timeout: 15000 });
    await hero.waitForTimeout(900);
    await glide(hero, hero.locator('[data-d6-tab="state"]'));
    await H.waitFor(() => hero.evaluate(() => document.querySelectorAll('.d6-src').length >= 3), 15000, 200);
    await hero.waitForTimeout(1500);
    await glide(hero, hero.locator(`a.d6-src[data-go="${PATHS.joint(PATHS.adapter1, 3)}"]`), -10); // follow a child link
    await hero.waitForFunction((p) => S.node && S.node.node.path === p, PATHS.joint(PATHS.adapter1, 3), { timeout: 15000 });
    await hero.waitForTimeout(1200);
    await glide(hero, hero.locator(`[data-t6-path="${PATHS.rack}"]`)); // up to the rack
    await hero.waitForFunction((p) => S.node && S.node.node.path === p, PATHS.rack, { timeout: 15000 });
    await hero.waitForTimeout(500);
    await glide(hero, hero.locator('[data-d6-tab="utilities"]'));
    await hero.waitForTimeout(500);
    await glide(hero, hero.locator('.d6-panel [data-util="all:calibrate"]'), 40); // open "Calibrate all 6…"
    await hero.waitForSelector('.ent-sheet [data-batch-runs]', { timeout: 20000 });
    await hero.waitForTimeout(2800);
    const video = await hero.video().path();
    await heroContext.close();
    const seconds = await encode(video, 'hero', 1.0);
    step('hero: an arm opened, a child link followed, "Calibrate all 6…" opened', seconds > 0);
    fs.writeFileSync(path.join(OUT, 'media.json'), JSON.stringify({ fixture: 'F1 + tests/fixtures/plugins-state (arm composite)', viewport: H.VIEWPORT, media: shots, steps }, null, 2));
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(steps.every((s) => s.ok) ? 0 : 1);
})().catch((error) => { console.error(error); process.exit(1); });
