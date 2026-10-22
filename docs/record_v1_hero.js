#!/usr/bin/env node
// The v1 report's hero video: the real host serving the recorded F2 frame (18:25, tview holds 272813EF), driven in
// headless Chrome (never a window), recorded by Playwright, then cut to a short muted loop with ffmpeg.
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_v1_hero.js [--out /abs/dir]
//
// Writes <out>/hero.mp4 (H.264, inlined by docs/build_v1_report.py), <out>/hero.gif (the fallback, kept beside the
// report, not inlined: the page budget is ~1.9 MB) and <out>/hero.json (what each step proved). The server it starts
// is stopped by its process group before exit. Fixture only: nothing here opens a CAN adapter or a camera.
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
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : path.join(ROOT, 'reports', 'media', 'v1-report');
// WHY 1280x800 and not the 1600x1000 of the stills: the video is shown unscaled-ish in an ~1100 px column, so the
// dashboard's 12 px text stays readable without shipping a 1600 px stream.
const VIEWPORT = { width: 1280, height: 800 };

function startServer(extra) {
  const env = { ...process.env };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--port', '0', '--no-udev', ...extra], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stopServer(child); reject(new Error(`server did not start: ${output}`)); }, 90000);
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

function stopServer(child) {
  try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* already gone */ }
}

// A visible pointer: headless recordings have no cursor, and a click nobody sees reads as a jump cut.
const CURSOR_SCRIPT = `
  window.addEventListener('DOMContentLoaded', () => {
    const dot = document.createElement('div');
    dot.id = '__hero_cursor';
    dot.style.cssText = 'position:fixed;left:-40px;top:-40px;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;'
      + 'background:rgba(255,255,255,.85);border:2px solid #111;box-shadow:0 0 0 3px rgba(255,255,255,.25);'
      + 'pointer-events:none;z-index:2147483647;transition:transform .12s ease';
    document.body.appendChild(dot);
    document.addEventListener('mousemove', (e) => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', () => { dot.style.transform = 'scale(.6)'; }, true);
    document.addEventListener('mouseup', () => { dot.style.transform = 'scale(1)'; }, true);
  });`;

const steps = [];
function step(name, ok, detail) {
  steps.push({ step: name, ok: Boolean(ok), detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  · ${detail}` : ''}`);
}

async function glide(page, locator, { click = true } = {}) {
  const box = await locator.boundingBox();
  if (!box) throw new Error('target not visible');
  const x = box.x + Math.min(box.width / 2, 120);
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y, { steps: 18 });
  await page.waitForTimeout(250);
  if (click) await page.mouse.click(x, y);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const videoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'io-hero-'));
  const { chromium } = require(PLAYWRIGHT);
  const server = await startServer(['--fixture', 'F2']);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  let videoPath = null;
  let startedMs = 0;
  let readyMs = 0;
  try {
    startedMs = Date.now();
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, recordVideo: { dir: videoDir, size: VIEWPORT } });
    await context.addInitScript(CURSOR_SCRIPT);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    await page.goto(`${server.base}/`);
    await page.waitForSelector('#tree-host .t1-row', { timeout: 30000 });
    await page.mouse.move(640, 420);
    await page.waitForTimeout(700);
    readyMs = Date.now() - startedMs;
    await page.waitForTimeout(900);

    // 1. the top-bar chip: "IO 5" and what needs you
    await glide(page, page.locator('#chip'));
    await page.waitForSelector('#chip-pop:not([hidden])');
    step('chip popover lists the causes', (await page.locator('#chip-pop .pop-row[data-go]').count()) >= 3);
    await page.waitForTimeout(1800);

    // 2. one cause -> the tree opens the path to it, the detail panel shows the adapter's own self-report
    // (WHY this cause and not CAN 15 driver: the page already selects the worst row on load, so that click shows nothing)
    const cause = page.locator('#chip-pop .pop-row').filter({ has: page.locator('b', { hasText: /^fdcanusb 272813EF$/ }) }).first();
    await glide(page, cause);
    await page.waitForFunction(() => document.querySelector('#detail .d-title')?.textContent.includes('fdcanusb 272813EF'));
    step('cause opens fdcanusb 272813EF (port held by tview)', true);
    await page.waitForTimeout(2200);

    // 4. the driver-registered button: gated, blocked by the port holder, never executes
    const button = page.locator('#detail .abtn', { hasText: 'Run comms check' });
    step('driver registered "Run comms check"', (await button.count()) === 1);
    await glide(page, button);
    await page.waitForSelector('#modal:not([hidden]) .modal');
    const modal = await page.locator('#modal .modal').innerText();
    step('confirm shows it blocked by tview', /blocked/i.test(modal) && /tview/i.test(modal));
    step('run button disabled', await page.locator('#modal footer .btn.primary').isDisabled());
    await page.waitForTimeout(2600);
    await glide(page, page.locator('#modal [data-cancel]'));
    await page.waitForTimeout(600);

    // 5. the same tree as a table, then the activity log
    await glide(page, page.locator('#views [data-view="table"]'));
    await page.waitForTimeout(1900);
    await glide(page, page.locator('#views [data-view="activity"]'));
    await page.waitForTimeout(1900);
    await glide(page, page.locator('#views [data-view="tree"]'));
    await page.waitForTimeout(500);

    // 6. search
    await glide(page, page.locator('#search'));
    await page.keyboard.type('D405', { delay: 90 });
    await page.waitForTimeout(1700);
    step('no page errors', errors.length === 0, errors.slice(0, 2).join(' | '));
    videoPath = await page.video().path();
    await context.close(); // flushes the video
  } finally {
    await browser.close();
    stopServer(server.child);
  }

  // Cut the blank load off the front, loop-friendly, small. WHY H.264 yuv420p + faststart: plays inline in Chrome,
  // Firefox and Safari from a data: URI.
  const trim = Math.max(0, readyMs / 1000 - 0.2).toFixed(2);
  const mp4 = path.join(OUT, 'hero.mp4');
  const encode = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-ss', trim, '-i', videoPath, '-an',
    '-vf', 'fps=15', '-c:v', 'libx264', '-preset', 'veryslow', '-tune', 'stillimage', '-crf', '32',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
  step('mp4 encoded', encode.status === 0, String(encode.stderr || '').slice(0, 300));
  const gif = path.join(OUT, 'hero.gif');
  const palette = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', mp4, '-vf',
    'fps=5,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=none', gif]);
  step('gif fallback encoded', palette.status === 0, String(palette.stderr || '').slice(0, 300));
  fs.rmSync(videoDir, { recursive: true, force: true });
  fs.writeFileSync(path.join(OUT, 'hero.json'), JSON.stringify({ fixture: 'F2', viewport: VIEWPORT, trim_s: Number(trim), steps }, null, 2));
  const failed = steps.filter((s) => !s.ok);
  console.log(`${steps.length - failed.length}/${steps.length} steps passed · ${mp4}`);
  process.exit(failed.length ? 1 : 0);
})();
