#!/usr/bin/env node
// The Activity log's hero video (docs/activity-log-v1.md): a plug seen live, the driver's own Found and Probed rows, the bus going
// error-passive with "→ fault confirmed" folded onto that ONE row, then the log filtered to one entity (its kernel lines come with it).
// Recorded from the REAL page on the fixture host (F1 started before its story, plus tests/fixtures/plugins-activity: a driver that
// probes on connect and never touches hardware) in headless Chrome, then cut to a short muted loop with ffmpeg.
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_activity_hero.js [--out /abs/dir]
//
// Writes <out>/hero.mp4 (H.264, muted, loops), <out>/hero.gif (the fallback) and <out>/hero.json (the steps). The server it starts (a
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
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : path.join(ROOT, 'reports', 'media', 'activity-log');
const VIEWPORT = { width: 1280, height: 800 };
const FRAME_CLOCK = 1792631510; // F1's frame clock (18:11:50); the plug is in the kernel log at 18:11:42
const PLUG_AT = FRAME_CLOCK - 8;
const LEAD_S = 6; // the recording starts this long before the plug

function startServer(stateDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', 'F1', '--offset', '-40',
    '--plugins', path.join(ROOT, 'tests', 'fixtures', 'plugins-activity'), '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
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
async function glide(page, locator, where = 0.5) {
  const box = await locator.boundingBox();
  if (!box) throw new Error('target not visible');
  const x = box.x + Math.min(box.width * where, 90);
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y, { steps: 18 });
  await page.waitForTimeout(260);
  return { x, y };
}
const clockNow = async (base) => (await (await fetch(`${base}/api/v1/state`)).json()).clock.now;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const videoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'activity-hero-'));
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'activity-hero-state-'));
  const { chromium } = require(PLAYWRIGHT);
  const server = await startServer(stateDir);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  let videoPath = null;
  let readyMs = 0;
  try {
    // the replay runs on the clock from the moment the host started: wait until the plug is LEAD_S away, then start recording
    let now = 0;
    for (let i = 0; i < 240; i += 1) {
      try { now = await clockNow(server.base); } catch (_) { now = 0; }
      if (now >= PLUG_AT - LEAD_S) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    step('the replay clock is near the plug', now >= PLUG_AT - LEAD_S - 1 && now < PLUG_AT - 1, `${(PLUG_AT - now).toFixed(1)} s to the plug`);
    const startedMs = Date.now();
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, colorScheme: 'light', recordVideo: { dir: videoDir, size: VIEWPORT } });
    await context.addInitScript(CURSOR_SCRIPT);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    await page.goto(`${server.base}/?view=activity`);
    await page.waitForSelector('#activity-host .al-row', { timeout: 30000 });
    await page.mouse.move(640, 300);
    await page.waitForTimeout(500);
    readyMs = Date.now() - startedMs;
    const before = await page.locator('#activity-host .al-row').count();
    step('the log opens with what happened before the plug', before >= 8, `${before} rows; ${await page.locator('#activity-host .al-count').innerText()}`);

    // 1. the plug arrives on its own (the live tail)
    await page.waitForSelector('.al-row:has(.kind:text-is("Plugged"))', { timeout: 30000 });
    step('a plug shows live: Plugged and its link speed', await page.locator('.al-row:has(.kind:text-is("LinkSpeed"))').count() >= 1);
    await page.waitForTimeout(900);
    // 2. the driver says what it found
    await page.waitForSelector('.al-row:has(.kind:text-is("Found"))', { timeout: 30000 });
    await page.waitForSelector('.al-row:has(.kind:text-is("Probed"))', { timeout: 30000 });
    step('the driver\'s own Found and Probed rows sit beside the kernel\'s', await page.locator('.al-row:has(.kind:text-is("Found"))').count() === 1);
    await page.waitForTimeout(900);
    // 3. the bus goes error-passive, and the fault lands on THAT row
    await page.waitForSelector('.al-row:has(.kind:text-is("BusErrorPassive")) .al-badge', { timeout: 30000 });
    const folded = await page.evaluate(() => ({ codes: [...document.querySelectorAll('#activity-host .al-row .kind')].map((k) => k.textContent),
      badge: (document.querySelector('.al-row:has(.kind) .al-badge') || {}).textContent }));
    step('a fault folds onto its cause: one row with "→ fault confirmed", no second row', folded.badge === '→ fault confirmed' && !folded.codes.includes('FaultConfirmed'), `${folded.badge}`);
    await glide(page, page.locator('.al-row:has(.kind:text-is("BusErrorPassive")) .al-badge'), 0.3);
    await page.waitForTimeout(1000);
    // 4. open the entity's own log
    const port = page.locator('.al-entbtn:has-text("Port 1-5")').first();
    const at = await glide(page, port, 0.4);
    await page.mouse.click(at.x, at.y);
    await page.waitForFunction(() => document.querySelectorAll('#activity-host .al-raw').length >= 3, null, { timeout: 15000 });
    const scoped = await page.evaluate(() => ({ names: [...new Set([...document.querySelectorAll('#activity-host .al-entbtn')].map((b) => b.textContent))], raw: document.querySelectorAll('#activity-host .al-raw').length,
      chip: document.querySelector('.al-scope').innerText.replace(/\s+/g, ' ') }));
    step('filtered to one entity: its rows, and its kernel lines (Raw) come with it', scoped.raw >= 3 && /Log of Port 1-5/.test(scoped.chip), `${scoped.raw} raw lines · ${scoped.chip.slice(0, 40)}`);
    await page.waitForTimeout(2000);
    // "+ path to host" adds the ports, hubs and the computer above it
    const mode = page.locator('[data-al-mode="host"]');
    await glide(page, mode, 0.5);
    const modeBox = await mode.boundingBox();
    await page.mouse.click(modeBox.x + modeBox.width / 2, modeBox.y + modeBox.height / 2);
    await page.waitForTimeout(1800);
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
  const still = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-ss', String(Math.max(0, seconds - 1.2)), '-i', mp4, '-frames:v', '1', path.join(OUT, 'hero-last.png')]);
  step('a still of the last frame', still.status === 0);
  fs.writeFileSync(path.join(OUT, 'hero.json'), JSON.stringify({ seconds, steps }, null, 2));
  process.exit(steps.every((item) => item.ok) ? 0 : 1);
})().catch((error) => { console.error(error); process.exit(1); });
