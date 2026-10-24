#!/usr/bin/env node
// The runs hero video (docs/runs-v1.md): Run calibration… on CAN 1 from the Utilities menu, the terminal panel opening on
// its own, the progress drawn live, the prompt answered by typing "y", the tab turning "done". Recorded from the REAL page on
// the fixture host (tests/fixtures/plugins-terminal: a driver that prints like a tool, never touches hardware) in headless
// Chrome, then cut to a short muted loop with ffmpeg.
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_runs_hero.js [--out /abs/dir]
//
// Writes <out>/hero.mp4 (H.264, muted, loops), <out>/hero.gif (the fallback) and <out>/hero.json. The server it starts (a
// temporary state dir, a free port) is stopped by its process group before exit.
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
const outIndex = process.argv.indexOf('--out');
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : path.join(ROOT, 'reports', 'media', 'runs');
const VIEWPORT = { width: 1280, height: 800 };

function startServer(stateDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, TERMINAL_STEP_S: '0.3' };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', 'F2',
    '--plugins', path.join(ROOT, 'tests', 'fixtures', 'plugins-terminal'), '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
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
const stop = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };

// A visible pointer: headless recordings have no cursor.
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

const steps = [];
const step = (name, ok, detail) => { steps.push({ step: name, ok: Boolean(ok), detail }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  · ${detail}` : ''}`); };
async function glide(page, locator) {
  const box = await locator.boundingBox();
  if (!box) throw new Error('target not visible');
  const x = box.x + Math.min(box.width / 2, 90);
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y, { steps: 16 });
  await page.waitForTimeout(220);
  await page.mouse.click(x, y);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const videoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'runs-hero-'));
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'runs-hero-state-'));
  const { chromium } = require(PLAYWRIGHT);
  const server = await startServer(stateDir);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  let videoPath = null;
  let readyMs = 0;
  try {
    let can1;
    const find = (node, wanted) => (node.name === wanted ? node : node.children.map((child) => find(child, wanted)).find(Boolean));
    for (let i = 0; i < 120 && !can1; i += 1) {
      can1 = find((await (await fetch(`${server.base}/api/v1/state`)).json()).tree, 'CAN 1');
      if (!can1) await new Promise((resolve) => setTimeout(resolve, 500));
    }
    const startedMs = Date.now();
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, colorScheme: 'light', recordVideo: { dir: videoDir, size: VIEWPORT } });
    await context.addInitScript(CURSOR_SCRIPT);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    await page.goto(`${server.base}/?sel=${encodeURIComponent(can1.path)}`);
    await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 30000 });
    await page.mouse.move(640, 400);
    await page.waitForTimeout(600);
    readyMs = Date.now() - startedMs;
    await page.waitForTimeout(900);

    await glide(page, page.locator('#ent-util-btn'));
    await page.waitForSelector('.ent-menu [data-util="calibrate"]');
    await page.waitForTimeout(700);
    await glide(page, page.locator('[data-util="calibrate"]'));
    await page.waitForSelector('.ent-sheet [data-util-run]');
    await page.waitForTimeout(1200);
    await glide(page, page.locator('.ent-sheet [data-util-run]'));
    await page.waitForSelector('#modal [data-util-tick]');
    await page.waitForTimeout(900);
    await glide(page, page.locator('#modal [data-util-tick]'));
    await page.waitForTimeout(500);
    await glide(page, page.locator('#modal [data-util-confirm]'));
    await page.waitForSelector('#runs-root:not([hidden]) .r1-tab');
    step('the terminal panel opens with the run\'s tab', /CAN 1 · Run calibration/.test(await page.locator('.r1-tab .r1-title').first().innerText()));
    await page.waitForFunction(() => /Continue\? \[y\/N\]/.test([...document.querySelectorAll('.xterm-rows')].map((el) => el.textContent).join(' ')), null, { timeout: 30000 });
    step('progress was drawn live and the prompt waits', true);
    await page.waitForTimeout(1600);
    await page.keyboard.type('y', { delay: 200 });
    await page.waitForTimeout(500);
    await page.keyboard.press('Enter');
    await page.waitForSelector('.r1-tab .r1-meta:has-text("done")', { timeout: 20000 });
    step('typing y finishes it: the tab says done', true);
    await page.waitForTimeout(3200);
    step('no page errors', errors.length === 0, errors.slice(0, 2).join(' | '));
    videoPath = await page.video().path();
    await context.close();
  } finally {
    await browser.close();
    stop(server.child);
  }
  const trim = Math.max(0, readyMs / 1000 - 0.2).toFixed(2);
  const mp4 = path.join(OUT, 'hero.mp4');
  const encode = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-ss', trim, '-i', videoPath, '-an', '-vf', 'fps=15', '-c:v', 'libx264', '-preset', 'slow',
    '-crf', '30', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
  step('mp4 encoded', encode.status === 0, String(encode.stderr || '').slice(0, 300));
  const gif = path.join(OUT, 'hero.gif');
  const palette = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', mp4, '-vf', 'fps=6,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=none', gif]);
  step('gif fallback encoded', palette.status === 0, String(palette.stderr || '').slice(0, 300));
  const probe = spawnSync('/usr/bin/ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4]);
  const seconds = Number(String(probe.stdout).trim());
  step('the loop is 6-20 s', seconds >= 6 && seconds <= 20, `${seconds.toFixed(1)} s`);
  fs.rmSync(videoDir, { recursive: true, force: true });
  fs.rmSync(stateDir, { recursive: true, force: true });
  fs.writeFileSync(path.join(OUT, 'hero.json'), JSON.stringify({ fixture: 'F2 + tests/fixtures/plugins-terminal', viewport: VIEWPORT, trim_s: Number(trim), duration_s: seconds, steps }, null, 2));
  const failed = steps.filter((s) => !s.ok);
  console.log(`${steps.length - failed.length}/${steps.length} steps passed · ${mp4}`);
  process.exit(failed.length ? 1 : 0);
})();
