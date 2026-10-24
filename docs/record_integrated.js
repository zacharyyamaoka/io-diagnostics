#!/usr/bin/env node
// The integrated picks' media (docs/build_integrated_report.py): screenshots at 1400x900, light and dark, and one hero video of the
// real page on a fixture host (never hardware, never ports 8820-8822):
//
//   /usr/bin/node /home/bam/io-diagnostics-lanes/integrate-picks/docs/record_integrated.js [--out /abs/dir]
//
// The host is `cli.py serve --fixture F1 --port 0 --no-udev --expected <rig file>` with three fixture plugins in one temporary plugin
// directory: state_joint and state_camera (tests/fixtures/plugins-state: a joint at 10 Hz and a camera with colour and depth frames) and
// charts_fixture (tests/fixtures/plugins-charts: the Computer's statuses with moving series), plus a rig file that expects the fdcanusb and
// both cameras. It refuses to go on unless /api/v1/plugins lists only those three.
// Screenshots (light and dark): the L1 shell with the tabbed entity page; the Nested tree; the State tab with c2 open and the c5 / pin
// buttons on the row beside it; the Dashboard view; the tree stepped into an adapter with its breadcrumb; Group by Type; Only inside.
// Hero video (hero.mp4 + hero.gif, 6-20 s, muted, looping): open the app, search in the tree pane, open a board, unfold a State chart,
// step into an adapter, step back out.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const FFMPEG = '/usr/bin/ffmpeg';
const { chromium } = require(PLAYWRIGHT);
const outIndex = process.argv.indexOf('--out');
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : '/home/bam/io-diagnostics/reports/media/integrated-picks';
const VIEWPORT = { width: 1400, height: 900 };
const ADAPTER = 'host/usb1/port-1-3/usb-1-3';
const BOARD = `${ADAPTER}/can/1`;
const STATE_SRC = path.join(ROOT, 'tests', 'fixtures', 'plugins-state');
const CHARTS_SRC = path.join(ROOT, 'tests', 'fixtures', 'plugins-charts');
const RIG_FILE = `version = 1

[[rig]]
name = "1-DOF test rack"

[[rig.device]]
name = "fdcanusb 272813EF"
kind = "adapter"
hardware_id = "usb:0483:5740:272813EF"
nodes = ["can/1", "can/14", "can/15"]

[[rig.device]]
name = "RealSense D405 218622276036"
kind = "camera"
hardware_id = "usb:8086:0b5b:219323070276"
nodes = []

[[rig.device]]
name = "RealSense D405 218622274411"
kind = "camera"
hardware_id = "usb:8086:0b5b:130523071221"
nodes = []
`;

const steps = [];
const step = (name, ok, detail) => { steps.push({ step: name, ok: Boolean(ok), detail }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  · ${detail}` : ''}`); };
const stop = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };

function startServer(root) {
  const plugins = path.join(root, 'plugins');
  fs.mkdirSync(plugins, { recursive: true });
  const copy = (source, name, period) => {
    let text = fs.readFileSync(path.join(source, name), 'utf8').replace(/\{here\}/g, source);
    if (period) text = text.replace(/period_s = \d+/, `period_s = ${period}`);
    fs.writeFileSync(path.join(plugins, name), text);
  };
  copy(STATE_SRC, 'state_joint.toml', 2);
  copy(STATE_SRC, 'state_camera.toml', 2);
  copy(CHARTS_SRC, 'charts.toml', 0);
  const rig = path.join(root, 'expected.toml');
  fs.writeFileSync(rig, RIG_FILE);
  fs.writeFileSync(path.join(root, 'mode'), '10');
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: path.join(root, 'state'), IO_DIAGNOSTICS_CONFIG_DIR: path.join(root, 'config'),
    STATE_CONFIG_FILE: path.join(root, 'board.json'), STATE_MODE_FILE: path.join(root, 'mode'), STATE_LOG: path.join(root, 'plugin.log') };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', 'F1', '--plugins', plugins,
    '--port', '0', '--no-udev', '--expected', rig], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stop(child); reject(new Error(`server did not start: ${output}`)); }, 60000);
    const onData = (chunk) => {
      output += chunk;
      const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//);
      if (found) { clearTimeout(timer); resolve({ child, base: found[1] }); }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`server exited (${code}): ${output}`)); });
  });
}

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
async function glide(page, locator, { dx = 0, dy = 0, click = true } = {}) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) throw new Error('target not visible');
  const x = box.x + Math.min(box.width / 2, 90) + dx;
  const y = box.y + box.height / 2 + dy;
  await page.mouse.move(x, y, { steps: 16 });
  await page.waitForTimeout(220);
  if (click) await page.mouse.click(x, y);
}
const rowOf = (page, text) => page.locator('#tree-host .t6-row', { hasText: text }).first();
async function rowMenu(page, text, item) {
  const row = rowOf(page, text);
  await glide(page, row, { click: false });
  await row.locator('[data-t6-more]').dispatchEvent('click');
  await page.waitForSelector('.t6-menu:not([hidden])');
  await page.waitForTimeout(500);
  await glide(page, page.locator(`.t6-menu [data-t6-mi="${item}"]`));
  await page.waitForTimeout(700);
}
async function groupBy(page, id) {
  await page.evaluate(() => setViewOptions(true));
  await page.selectOption('#structure', id);
  await page.evaluate(() => setViewOptions(false));
  await page.waitForTimeout(700);
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
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'io-diagnostics-integrated-'));
  const server = await startServer(root);
  const base = server.base;
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const errors = [];
  const wire = (page) => { page.on('pageerror', (e) => errors.push(String(e))); page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); }); };
  const shoot = async (page, name) => { await page.screenshot({ path: path.join(OUT, `${name}.png`) }); };
  try {
    const plugins = (await (await fetch(`${base}/api/v1/plugins`)).json()).plugins.map((plugin) => plugin.name).sort().join();
    if (plugins !== 'charts_fixture,state_camera,state_joint') throw new Error(`refusing to go on: plugins are ${plugins}`);
    step('only the three fixture plugins are loaded', true, plugins);
    // the moving series fill (a minute at 2 Hz), the joint reports its configuration
    for (let i = 0; i < 90; i += 1) {
      const history = await (await fetch(`${base}/api/v1/history?path=host`)).json();
      const cpu = (history.statuses || []).find((s) => /CPU Information/.test(s.name));
      const node = await (await fetch(`${base}/api/v1/node?path=${encodeURIComponent(BOARD)}`)).json().catch(() => ({}));
      if (cpu && cpu.measurements[0].points.length >= 120 && (node.config || []).length) break;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }

    // ---- screenshots, light and dark ----
    for (const scheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: scheme });
      const page = await context.newPage();
      wire(page);
      await page.goto(`${base}/?sel=${encodeURIComponent(BOARD)}`);
      await page.waitForFunction(() => typeof S !== 'undefined' && S.built && S.node && document.querySelector('.d6-tabs'), null, { timeout: 30000 });
      await page.waitForTimeout(1500);
      await shoot(page, `l1-shell-${scheme}`);
      step(`l1-shell-${scheme}`, await page.evaluate(() => document.getElementById('layout-pick').value === 'l1' && Boolean(document.querySelector('.d6-tabs'))));

      // the State tab on the Computer: one row unfolded (c2), the next row's expand and pin buttons showing
      await page.goto(`${base}/?sel=host&page=state`);
      await page.waitForFunction(() => document.querySelector('.d6-panel .ch-spark'), null, { timeout: 30000 });
      await page.waitForTimeout(1500);
      const sparks = page.locator('.d6-panel .ch-spark');
      const at = await page.evaluate(() => [...document.querySelectorAll('.d6-panel .ch-spark')].findIndex((el) => JSON.parse(el.dataset.spark).l === 'RAM Load Average'));
      await glide(page, sparks.nth(at).locator('.ch-line'), { dx: -40 });
      await page.waitForTimeout(1200);
      await page.evaluate(() => document.querySelector('.d6-panel .ch-spark[data-open]').scrollIntoView({ block: 'center' }));
      await page.waitForTimeout(400);
      await glide(page, sparks.nth(at + 1).locator('.ch-line'), { click: false, dx: 60 });
      await page.waitForTimeout(500);
      await shoot(page, `state-unfolded-${scheme}`);
      step(`state-unfolded-${scheme}`, await page.evaluate((i) => Boolean(document.querySelector('.d6-panel .ch-spark[data-open]')) && getComputedStyle(document.querySelectorAll('.d6-panel .ch-spark')[i].querySelector('.ch-btn[data-ch-btn="focus"]')).opacity === '1', at + 1));

      // pin three values, then the Dashboard
      for (const label of ['swap used %', 'CPU Load Average', 'eth0 rx']) {
        const index = await page.evaluate((wanted) => [...document.querySelectorAll('.d6-panel .ch-spark')].findIndex((el) => JSON.parse(el.dataset.spark).l === wanted), label);
        if (index < 0) continue;
        const row = sparks.nth(index);
        await row.scrollIntoViewIfNeeded();
        await row.hover();
        await row.locator('[data-ch-btn="pin"]').click();
        await page.waitForTimeout(300);
      }
      await page.waitForTimeout(500);
      await page.click('.l1-ribbon [data-l1="dashboard"]');
      await page.waitForSelector('#ch4-dash .ch4-tile');
      await page.waitForTimeout(5000); // the "Pinned ... Open dashboard" toast goes after 4 s
      await shoot(page, `dashboard-${scheme}`);
      step(`dashboard-${scheme}`, await page.evaluate(() => document.querySelectorAll('#ch4-dash .ch4-tile').length >= 2));
      await page.evaluate(() => fetch('/api/v1/settings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify({ dashboard: [] }) }));
      await page.click('.l1-ribbon [data-l1="tree"]'); // closes the board (it is remembered as open otherwise)
      await page.evaluate(() => { localStorage.removeItem('io-diagnostics.chart.c4.tiles'); localStorage.setItem('io-diagnostics.chart.c4.view', '0'); });

      // the tree: Nested, only inside, stepped into an adapter, Type
      await page.goto(`${base}/?sel=${encodeURIComponent(BOARD)}`);
      await page.waitForFunction(() => typeof S !== 'undefined' && S.built && S.node && document.querySelector('.t6-row'), null, { timeout: 30000 });
      await page.waitForTimeout(1200);
      await shoot(page, `tree-nested-${scheme}`);
      await rowMenu(page, '1-DOF test rack', 'nav:inside');
      await shoot(page, `tree-inside-${scheme}`);
      await page.locator('#tree-host .tn-chip-x').first().dispatchEvent('click');
      await page.waitForTimeout(500);
      await rowMenu(page, 'fdcanusb · fixture', 'nav:into');
      await page.waitForTimeout(500);
      await shoot(page, `tree-into-${scheme}`);
      step(`tree-into-${scheme}`, await page.evaluate(() => Boolean(document.querySelector('.tree-nav .tn-here')) && /fdcanusb/.test(document.querySelector('.tn-here').textContent)));
      await page.locator('.tree-nav [data-tn-into=""]').dispatchEvent('click');
      await page.waitForTimeout(400);
      await groupBy(page, 'p7');
      await shoot(page, `tree-type-${scheme}`);
      step(`tree-type-${scheme}`, (await page.evaluate(() => [...document.querySelectorAll('#tree-host .t6-row')].map((r) => r.querySelector('.t6-name').textContent))).includes('Adapters'));
      await groupBy(page, 'p6');
      await context.close();
    }

    // ---- the hero video, light ----
    {
      const videoDir = path.join(root, 'video');
      const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light', recordVideo: { dir: videoDir, size: VIEWPORT } });
      await context.addInitScript(CURSOR_SCRIPT);
      const page = await context.newPage();
      wire(page);
      const started = Date.now();
      await page.goto(`${base}/`);
      await page.waitForFunction(() => typeof S !== 'undefined' && S.built && S.node && document.querySelector('.t6-row'), null, { timeout: 30000 });
      await page.waitForTimeout(1500);
      const loaded = (Date.now() - started) / 1000;
      // search in the tree pane
      await glide(page, page.locator('#search'));
      await page.keyboard.type('CAN 1', { delay: 90 });
      await page.waitForTimeout(1200);
      // open a board
      await glide(page, rowOf(page, 'moteus c1 · CAN 1'));
      await page.waitForSelector('.d6-tabs');
      await page.waitForTimeout(900);
      await glide(page, page.locator('[data-d6-tab="state"]'));
      await page.waitForSelector('.d6-panel .ch-spark', { timeout: 20000 });
      await page.waitForTimeout(1500);
      // unfold a State chart
      await glide(page, page.locator('.d6-panel .ch-spark .ch-line').first(), { dx: -30 });
      await page.waitForTimeout(2200);
      // step into the adapter and back out
      await page.fill('#search', '');
      await page.waitForTimeout(700);
      await rowMenu(page, 'fdcanusb · fixture', 'nav:into');
      await page.waitForTimeout(1600);
      await glide(page, page.locator('.tree-nav [data-tn-up]'));
      await page.waitForTimeout(1100);
      await glide(page, page.locator('.tree-nav [data-tn-into=""]'));
      await page.waitForTimeout(1200);
      const video = page.video();
      await context.close();
      const raw = await video.path();
      const seconds = await encode(raw, 'hero', Math.max(0, loaded - 0.6));
      step('hero video recorded', seconds > 0, `${seconds.toFixed(1)} s`);
    }
    step('no page errors, no console errors', errors.length === 0, errors.slice(0, 3).join(' | '));
    fs.writeFileSync(path.join(OUT, 'media.json'), JSON.stringify({ steps, base: 'fixture F1', plugins }, null, 1));
  } finally {
    await browser.close();
    stop(server.child);
    fs.rmSync(root, { recursive: true, force: true });
  }
  process.exitCode = steps.some((item) => !item.ok) ? 1 : 0;
})().catch((error) => { console.error(error); process.exitCode = 1; });
