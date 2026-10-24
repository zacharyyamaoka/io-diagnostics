#!/usr/bin/env node
// The hero videos of run views r2, r3 and r5 (docs/runs-v1.md section 7; r1's is docs/record_runs_hero.js). Each is recorded from
// the REAL page on the fixture host (tests/fixtures/plugins-terminal: a driver that prints like a tool, never touches hardware) in
// headless Chrome, then cut to a short muted loop with ffmpeg:
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_runs_hero_views.js r2 [--out /abs/dir]
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_runs_hero_views.js r3
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_runs_hero_views.js r5
//
// Writes <out>/<view>-hero.mp4 (H.264, muted, loops), <view>-hero.gif (the fallback) and <view>-hero.json; <out> defaults to
// reports/media/runs. The server it starts (a temporary state dir, a free port) is stopped by its process group before exit.
//   r2  Run calibration… from the Utilities menu; the run line expands in place into the live output under the header; "y" typed
//       into the inline box; the block folds back to the one-line result; Show output opens it again
//   r3  Run calibration…; the Runs panel opens on the job (steps, live log); "y" typed in the log; Exit opens with the code; Run again
//       shows the dry-run sheet (nothing runs until the confirm)
//   r5  Run calibration…; the tmux session attaches (0:calibrate@1); a hang run on the adapter is window 1; Ctrl-b p / n switch; `#`
//       and `!` flag the window answered in the background; Ctrl-b n back, Stop on the hang run
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
const VIEW = process.argv[2];
const outIndex = process.argv.indexOf('--out');
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : path.join(ROOT, 'reports', 'media', 'runs');
const VIEWPORT = { width: 1280, height: 800 };
if (!['r2', 'r3', 'r5'].includes(VIEW)) { console.error('usage: record_runs_hero_views.js r2|r3|r5 [--out DIR]'); process.exit(2); }

function startServer(stateDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, TERMINAL_STEP_S: '0.2' };
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
  await page.mouse.move(x, y, { steps: 12 });
  await page.waitForTimeout(140);
  await page.mouse.click(x, y);
}
// Utilities ▾ -> the utility -> its sheet -> Run / Continue… -> the confirm modal (a moves box ticked)
async function runUtility(page, name, moves) {
  await glide(page, page.locator('#ent-util-btn'));
  await page.waitForSelector(`.ent-menu [data-util="${name}"]`);
  await page.waitForTimeout(400);
  await glide(page, page.locator(`[data-util="${name}"]`));
  await page.waitForSelector('.ent-sheet [data-util-run]');
  await page.waitForTimeout(700);
  await glide(page, page.locator('.ent-sheet [data-util-run]'));
  await page.waitForSelector('#modal [data-util-confirm]');
  await page.waitForTimeout(500);
  if (moves) { await glide(page, page.locator('#modal [data-util-tick]')); await page.waitForTimeout(400); }
  await glide(page, page.locator('#modal [data-util-confirm]'));
}
const promptShown = (page, selector) => page.waitForFunction((css) => /Continue\? \[y\/N\]/.test([...document.querySelectorAll(css)].map((el) => el.textContent).join('\n')), selector, { timeout: 30000 });

const FLOWS = {
  async r2(page) {
    await runUtility(page, 'calibrate', true);
    await page.waitForSelector('.r2-out');
    await page.waitForFunction(() => { const host = document.querySelector('.r2-out'); return host && host.shadowRoot && /Continue\? \[y\/N\]/.test([...host.shadowRoot.querySelectorAll('.xterm-rows > div')].map((r) => r.textContent).join('\n')); }, null, { timeout: 30000 });
    step('r2: the run line expanded under the header into the live output; the prompt waits', true);
    await page.waitForTimeout(1100);
    await glide(page, page.locator('.r2-out >> input'));
    await page.keyboard.type('y', { delay: 250 });
    await page.waitForTimeout(500);
    await page.keyboard.press('Enter');
    await page.waitForSelector('.ent-run.ok:not(.r2-line)', { timeout: 20000 });
    step('r2: the block folded back to the one-line result', true);
    await page.waitForTimeout(1500);
    await glide(page, page.locator('[data-run-open]'));
    await page.waitForSelector('.r2-out');
    await page.waitForTimeout(2000);
  },
  async r3(page) {
    await runUtility(page, 'calibrate', true);
    await page.waitForSelector('#runs-root:not([hidden]) .r3-job h3');
    await promptShown(page, '.r3-log .xterm-rows > div');
    step('r3: the Runs panel opened on the job; the live log waits at its prompt', true);
    await page.waitForTimeout(900);
    await glide(page, page.locator('[data-step="pre"] .r3-step-h'));
    await page.waitForTimeout(1100);
    await glide(page, page.locator('[data-step="pre"] .r3-step-h'));
    await page.locator('.r3-log .xterm-helper-textarea').focus();
    await page.keyboard.type('y', { delay: 250 });
    await page.waitForTimeout(400);
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => /^done/.test(document.querySelector('.r3-pill').textContent), null, { timeout: 20000 });
    step('r3: the job is done; Exit opened with the code', true);
    await page.waitForTimeout(1300);
    await glide(page, page.locator('[data-r3-again]'));
    await page.waitForSelector('.ent-sheet [data-util-run]', { timeout: 20000 });
    step('r3: Run again shows the dry-run sheet; nothing has run', true);
    await page.waitForTimeout(1600);
  },
  async r5(page) {
    await runUtility(page, 'calibrate', true);
    await page.waitForSelector('#runs-root:not([hidden]) .r5-win');
    await promptShown(page, '.r5-pane:not([hidden]) .xterm-rows > div');
    step('r5: the session attached with the run as window 0; the prompt waits', true);
    await page.waitForTimeout(600);
    await glide(page, page.locator('#tree-host').getByText('fdcanusb · fixture').first());
    await page.waitForSelector('#ent-util-btn:not([disabled])');
    await page.waitForTimeout(300);
    await runUtility(page, 'hang', false);
    await page.waitForFunction(() => document.querySelectorAll('.r5-win').length === 2);
    await page.waitForFunction(() => /hanging/.test([...document.querySelectorAll('.r5-pane:not([hidden]) .xterm-rows > div')].map((r) => r.textContent).join('\n')), null, { timeout: 20000 });
    step('r5: the hang run is window 1', true);
    await page.waitForTimeout(700);
    await page.locator('.r5-pane:not([hidden]) .xterm-helper-textarea').focus();
    await page.keyboard.press('Control+b');
    await page.waitForTimeout(500);
    await page.keyboard.press('p');
    await page.waitForTimeout(800);
    await page.keyboard.type('y', { delay: 200 });
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => /saved the calibration/.test([...document.querySelectorAll('.r5-pane:not([hidden]) .xterm-rows > div')].map((r) => r.textContent).join('\n')), null, { timeout: 20000 });
    await page.waitForTimeout(900);
    step('r5: Ctrl-b p, the answer typed in window 0 while window 1 hung in the background', true);
    await page.keyboard.press('Control+b');
    await page.keyboard.press('n');
    await page.waitForTimeout(600);
    await glide(page, page.locator('.r5-stop'));
    await page.waitForFunction(() => document.querySelector('.r5-stop').textContent === 'Stopping…');
    step('r5: Stop pressed on the hang run (it ignores SIGINT; the host escalates)', true);
    await page.waitForTimeout(1200);
  },
};

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
    await context.addInitScript((id) => { try { localStorage.setItem('io-diagnostics.runs-view', id); } catch (_) { /* private mode */ } }, VIEW);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    await page.goto(`${server.base}/?sel=${encodeURIComponent(can1.path)}`);
    await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 30000 });
    await page.mouse.move(640, 400);
    await page.waitForTimeout(500);
    readyMs = Date.now() - startedMs;
    await page.waitForTimeout(500);
    await FLOWS[VIEW](page);
    step('no page errors', errors.length === 0, errors.slice(0, 2).join(' | '));
    videoPath = await page.video().path();
    await context.close();
  } finally {
    await browser.close();
    stop(server.child);
  }
  const trim = Math.max(0, readyMs / 1000 - 0.2).toFixed(2);
  const mp4 = path.join(OUT, `${VIEW}-hero.mp4`);
  const encode = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-ss', trim, '-i', videoPath, '-an', '-vf', 'fps=15', '-c:v', 'libx264', '-preset', 'slow',
    '-crf', '30', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
  step('mp4 encoded', encode.status === 0, String(encode.stderr || '').slice(0, 300));
  const gif = path.join(OUT, `${VIEW}-hero.gif`);
  const palette = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', mp4, '-vf', 'fps=6,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=none', gif]);
  step('gif fallback encoded', palette.status === 0, String(palette.stderr || '').slice(0, 300));
  const probe = spawnSync('/usr/bin/ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4]);
  const seconds = Number(String(probe.stdout).trim());
  step('the loop is 6-20 s', seconds >= 6 && seconds <= 20, `${seconds.toFixed(1)} s`);
  fs.rmSync(videoDir, { recursive: true, force: true });
  fs.rmSync(stateDir, { recursive: true, force: true });
  fs.writeFileSync(path.join(OUT, `${VIEW}-hero.json`), JSON.stringify({ view: VIEW, fixture: 'F2 + tests/fixtures/plugins-terminal', viewport: VIEWPORT, trim_s: Number(trim), duration_s: seconds, steps }, null, 2));
  const failed = steps.filter((s) => !s.ok);
  console.log(`${steps.length - failed.length}/${steps.length} steps passed · ${mp4}`);
  process.exit(failed.length ? 1 : 0);
})().catch((error) => { console.error(error); process.exit(1); });
