#!/usr/bin/env node
// The entity page's media (docs/state-config-v1.md): screenshots of a joint and a camera entity in light and dark at 1400x900, the
// Configuration tab's edit story, the Computer's Processes tab, the BEFORE (the d0 detail panel the page had), and two hero videos
// recorded from the REAL page on the fixture host (tests/fixtures/plugins-state: a joint at 10 Hz, a camera with colour and depth
// frames; it never touches hardware): hero.mp4 (open a board, State live, Configuration, Log; then the camera and a stream
// toggle) and config.mp4 (edit live: accepted, rejected, not saved, Save, a safety key's second confirm).
//
//   /usr/bin/node /home/bam/io-diagnostics-lanes/entity-page/docs/record_entity_page.js [--out /abs/dir]
//
// Writes <out>/*.png, hero.mp4 + hero.gif, config.mp4 + config.gif and media.json (default <checkout>/reports/media/entity-page).
'use strict';

const H = require('../tests/entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const outIndex = process.argv.indexOf('--out');
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : H.SHOTS;
const FFMPEG = '/usr/bin/ffmpeg';
const { BOARD, CAMERA } = H;
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
  await page.waitForTimeout(200);
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
  step(`${name}: the loop is 6-20 s`, seconds >= 6 && seconds <= 20, `${seconds.toFixed(1)} s`);
  return seconds;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const server = await H.fixtureHost('F1');
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  const shots = [];
  const shoot = async (page, name) => { await page.screenshot({ path: path.join(OUT, `${name}.png`) }); shots.push(name); };
  try {
    // ---- screenshots: a joint and a camera, light and dark ------------------------------------------------------------------
    for (const scheme of ['light', 'dark']) {
      const context = await H.newContext(browser, scheme, { deviceScaleFactor: 1 });
      const page = await context.newPage();
      await H.open(page, server.base, BOARD, 'diagnostics');
      await page.waitForTimeout(700);
      await shoot(page, `joint-diagnostics-${scheme}`);
      await H.goTab(page, 'state');
      await page.waitForFunction(() => (document.querySelector('[data-d6-watch]') || {}).dataset.d6Watch === 'running', null, { timeout: 15000 });
      await page.waitForTimeout(5500);
      await shoot(page, `joint-state-${scheme}`);
      await H.goTab(page, 'config');
      await page.waitForTimeout(500);
      await shoot(page, `joint-config-${scheme}`);
      await H.goTo(page, CAMERA);
      await H.goTab(page, 'state');
      await page.waitForSelector('.d6-tile img', { timeout: 20000 });
      await page.waitForTimeout(3500);
      await shoot(page, `camera-state-${scheme}`);
      await H.goTab(page, 'diagnostics');
      await page.waitForTimeout(400);
      await shoot(page, `camera-diagnostics-${scheme}`);
      step(`screenshots ${scheme}`, true);
      await context.close();
    }

    // ---- the rest, light: log, utilities, the edit story, the Computer's Processes -----------------------------------------------
    {
      const context = await H.newContext(browser, 'light', { deviceScaleFactor: 1 });
      const page = await context.newPage();
      await H.open(page, server.base, BOARD, 'config');
      await page.waitForTimeout(500);
      await page.click('[data-er-start]');
      await page.waitForSelector('[data-er-modal]');
      await shoot(page, 'config-edit-confirm');
      await page.click('[data-er-confirm="start"]');
      await page.waitForSelector('input[data-er-cfg]');
      const timeout = 'input[data-er-cfg="servo.default_timeout_s"]';
      await page.fill(timeout, '0.3'); await page.press(timeout, 'Enter');
      await page.waitForFunction(() => /accepted/.test(document.querySelector('.d6-panel').textContent), null, { timeout: 15000 });
      await page.fill(timeout, '50'); await page.press(timeout, 'Enter');
      await page.waitForFunction(() => /rejected/.test(document.querySelector('.d6-panel').textContent), null, { timeout: 15000 });
      await page.fill(timeout, '0.3'); await page.press(timeout, 'Enter');
      await page.waitForFunction(() => /1 change live, not saved/.test(document.querySelector('.er-bar').textContent), null, { timeout: 15000 });
      const mode = 'input[data-er-cfg="servo.max_current_A"]';
      await page.fill(mode, '15'); await page.press(mode, 'Enter');
      await page.waitForSelector('[data-er-modal]');
      await shoot(page, 'config-safety-confirm');
      await page.click('[data-er-cancel]');
      await page.click('.er-cfgrow:has([title="servo.default_timeout_s"]) [data-er-resetopen]');
      await page.waitForTimeout(300);
      await shoot(page, 'config-edit-live');
      await page.click('[data-er-save]');
      await page.waitForFunction(() => !document.querySelector('[data-er-save]'), null, { timeout: 15000 });
      await H.goTab(page, 'log');
      await page.waitForSelector('.d6-log .al-row', { timeout: 15000 });
      await page.waitForTimeout(600);
      await shoot(page, 'joint-log-light');
      await H.goTab(page, 'utilities');
      await page.waitForTimeout(300);
      await shoot(page, 'joint-utilities-light');
      // the BEFORE: the detail panel the page had (d0, sections top to bottom), on the same board and camera
      await page.evaluate(() => localStorage.setItem('io-diagnostics.detail-layout', 'd0'));
      await H.open(page, server.base, BOARD).catch(() => {});
      await page.goto(`${server.base}/?sel=${encodeURIComponent(BOARD)}`);
      await page.waitForFunction(() => document.querySelector('.d-title') && S.node, null, { timeout: 20000 });
      await page.waitForTimeout(900);
      await shoot(page, 'before-joint');
      await page.goto(`${server.base}/?sel=${encodeURIComponent(CAMERA)}`);
      await page.waitForFunction(() => document.querySelector('.d-title') && S.node, null, { timeout: 20000 });
      await page.waitForTimeout(900);
      await shoot(page, 'before-camera');
      await context.close();
      step('edit story, log, utilities and the before', true);
    }
    const computer = await startComputer();
    if (computer) {
      const context = await H.newContext(browser, 'light', { deviceScaleFactor: 1 });
      const page = await context.newPage();
      await page.goto(`${computer.base}/?sel=host&page=processes`);
      await page.waitForSelector('.d6-tbl tbody tr', { timeout: 60000 });
      await page.waitForTimeout(800);
      await shoot(page, 'computer-processes');
      await context.close();
      H.stopServer(computer.child);
      step('the Computer\'s Processes tab', true);
    }

    // ---- hero 1: a board's tabs, State live, then the camera and a stream toggle ----------------------------------------------------
    {
      const videoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'entity-hero-'));
      const started = Date.now();
      const context = await browser.newContext({ viewport: H.VIEWPORT, deviceScaleFactor: 1, colorScheme: 'light', recordVideo: { dir: videoDir, size: H.VIEWPORT } });
      await context.addInitScript(CURSOR_SCRIPT);
      const page = await context.newPage();
      await page.goto(`${server.base}/?sel=${encodeURIComponent(BOARD)}`);
      await page.waitForSelector('.d6-tabs', { timeout: 30000 });
      await page.mouse.move(700, 450);
      await page.waitForTimeout(500);
      const readyMs = Date.now() - started;
      await page.waitForTimeout(1300);
      await glide(page, page.locator('[data-d6-tab="state"]'));
      await page.waitForFunction(() => (document.querySelector('[data-d6-watch]') || {}).dataset.d6Watch === 'running', null, { timeout: 15000 });
      await page.waitForTimeout(4200);
      await glide(page, page.locator('[data-d6-tab="config"]'));
      await page.waitForTimeout(1700);
      await glide(page, page.locator('.er-cfgrow:has([title="servo.default_timeout_s"]) [data-er-pin]'));
      await page.waitForTimeout(1300);
      await glide(page, page.locator('[data-d6-tab="log"]'));
      await page.waitForTimeout(1700);
      await H.goTo(page, CAMERA);
      await glide(page, page.locator('[data-d6-tab="state"]'));
      await page.waitForSelector('.d6-tile img', { timeout: 20000 });
      await page.waitForTimeout(3000);
      await glide(page, page.locator('input[data-d6-stream="color"]'), -40);
      await page.waitForTimeout(1600);
      await glide(page, page.locator('input[data-d6-stream="color"]'), -40);
      await page.waitForTimeout(1500);
      const videoPath = await page.video().path();
      await context.close();
      const seconds = await encode(videoPath, 'hero', Math.max(0, readyMs / 1000 - 0.2).toFixed(2));
      fs.rmSync(videoDir, { recursive: true, force: true });
      shots.push(`hero:${seconds.toFixed(1)}s`);
    }
    // un-pin what the hero pinned, so the config clip starts from the default arrangement
    await fetch(`${server.base}/api/v1/arrangements`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui', 'Sec-Fetch-Site': 'same-origin' }, body: JSON.stringify({ kind: 'servo', section: 'config', value: {} }) }).catch(() => {});

    // ---- hero 2: editing a setting live ------------------------------------------------------------------------------------------------
    {
      const videoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'entity-config-'));
      const started = Date.now();
      const context = await browser.newContext({ viewport: H.VIEWPORT, deviceScaleFactor: 1, colorScheme: 'light', recordVideo: { dir: videoDir, size: H.VIEWPORT } });
      await context.addInitScript(CURSOR_SCRIPT);
      const page = await context.newPage();
      await page.goto(`${server.base}/?sel=${encodeURIComponent(BOARD)}&page=config`);
      await page.waitForSelector('.er-cfgrow', { timeout: 30000 });
      await page.mouse.move(700, 450);
      await page.waitForTimeout(500);
      const readyMs = Date.now() - started;
      await page.waitForTimeout(1000);
      await glide(page, page.locator('[data-er-start]'));
      await page.waitForSelector('[data-er-modal]');
      await page.waitForTimeout(1500);
      await glide(page, page.locator('[data-er-confirm="start"]'));
      await page.waitForSelector('input[data-er-cfg]');
      await page.waitForTimeout(600);
      const timeout = page.locator('input[data-er-cfg="servo.default_timeout_s"]');
      await glide(page, timeout);
      await page.keyboard.press('Control+a');
      await page.keyboard.type('0.3', { delay: 120 });
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => /accepted/.test(document.querySelector('.d6-panel').textContent), null, { timeout: 15000 });
      await page.waitForTimeout(1500);
      await page.keyboard.press('Control+a');
      await page.keyboard.type('50', { delay: 120 });
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => /rejected/.test(document.querySelector('.d6-panel').textContent), null, { timeout: 15000 });
      await page.waitForTimeout(1800);
      await glide(page, page.locator('[data-er-save]'));
      await page.waitForFunction(() => !document.querySelector('[data-er-save]'), null, { timeout: 15000 });
      await page.waitForTimeout(1500);
      const safe = page.locator('input[data-er-cfg="servo.max_current_A"]');
      await glide(page, safe);
      await page.keyboard.press('Control+a');
      await page.keyboard.type('15', { delay: 120 });
      await page.keyboard.press('Enter');
      await page.waitForSelector('[data-er-modal]');
      await page.waitForTimeout(1800);
      await glide(page, page.locator('[data-er-cancel]'));
      await page.waitForTimeout(800);
      const videoPath = await page.video().path();
      await context.close();
      const seconds = await encode(videoPath, 'config', Math.max(0, readyMs / 1000 - 0.2).toFixed(2));
      fs.rmSync(videoDir, { recursive: true, force: true });
      shots.push(`config:${seconds.toFixed(1)}s`);
    }
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  fs.writeFileSync(path.join(OUT, 'media.json'), JSON.stringify({ fixture: 'F1 + tests/fixtures/plugins-state', viewport: H.VIEWPORT, media: shots, steps }, null, 2));
  const failed = steps.filter((s) => !s.ok);
  console.log(`${steps.length - failed.length}/${steps.length} steps passed · ${OUT}`);
  process.exit(failed.length ? 1 : 0);
})();

async function startComputer() {
  try {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'entity-computer-'));
    const dir = path.join(root, 'plugins');
    fs.mkdirSync(dir);
    fs.writeFileSync(path.join(dir, 'computer.toml'), fs.readFileSync(path.join(H.ROOT, 'plugins.d', 'computer.toml'), 'utf8').split('{root}').join(H.ROOT));
    const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: path.join(root, 'state'), IO_DIAGNOSTICS_CONFIG_DIR: path.join(root, 'config') };
    delete env.VIRTUAL_ENV;
    const child = require('child_process').spawn('/home/bam/.local/bin/uv', ['run', '--project', H.ROOT, 'python', path.join(H.ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--plugins', dir, '--port', '0', '--no-udev'],
      { cwd: H.ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    return await new Promise((resolve) => {
      let output = '';
      const timer = setTimeout(() => { H.stopServer(child); resolve(null); }, 60000);
      const onData = (chunk) => { output += chunk; const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//); if (found) { clearTimeout(timer); resolve({ child, base: found[1] }); } };
      child.stdout.on('data', onData); child.stderr.on('data', onData);
      child.on('exit', () => { clearTimeout(timer); resolve(null); });
    });
  } catch (_) { return null; }
}
