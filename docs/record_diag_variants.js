#!/usr/bin/env node
// The screenshots and hero videos of the five Diagnostics variants d1a-d1e (detail/d1a.js ... d1e.js) and the before / after of
// the restyled health chart (detail/health.js). Everything is recorded from the REAL page on the fixture hosts in headless Chrome
// (never a window on the screen):
//
//   /usr/bin/node /home/bam/io-diagnostics-lanes/diagnostics-variants/docs/record_diag_variants.js --out /abs/dir [--f1 URL --f2 URL] [--only d1a,d1c] [--no-videos]
//
// Without --f1 / --f2 it starts the fixture hosts itself (`cli.py serve --fixture F1|F2 --no-udev`, the fixture plugins plus the
// passive Computer plugin, a temporary state dir) and waits --warm seconds (default 240) for a history; with them it attaches to hosts
// you already run. Writes to <out>: <variant>-<entity>-<light|dark>.png (1400x900), <variant>-hero.mp4 (H.264, muted, loops, 6-15 s),
// <variant>-hero.gif (the fallback), specimen-<light|dark>.png (the chart rule on a synthetic history, labelled as such) and shots.json.
// Nothing here presses a utility or touches hardware.
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
const arg = (name, fallback = null) => { const at = process.argv.indexOf(name); return at > 0 ? process.argv[at + 1] : fallback; };
const OUT = arg('--out');
const ONLY = (arg('--only') || 'd1a,d1b,d1c,d1d,d1e').split(',');
const WARM = Number(arg('--warm', '240'));
const VIDEOS = !process.argv.includes('--no-videos');
if (!OUT) { console.error('usage: record_diag_variants.js --out DIR [--f1 URL --f2 URL] [--only d1a,d1b] [--no-videos] [--warm S]'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const ENTITIES = {
  computer: { host: 'f1', sel: 'host', name: 'the Computer (17 diagnostics)' },
  fdcanusb: { host: 'f2', sel: 'host/usb1/port-1-3/usb-1-3', name: 'an fdcanusb (comms unknown: port held by tview)' },
  can15: { host: 'f1', sel: 'host/usb1/port-1-3/usb-1-3/can/15/driver', name: 'CAN 15 driver (fault 33)' },
  camera: { host: 'f1', sel: 'host/usb6/port-6-1/usb-6-1', name: 'a RealSense D405 (moved since calibration)' },
};
// each variant: the entity its hero video follows, the entities it is shot on
const PLAN = {
  d1: { video: null, shots: ['computer', 'can15'] },
  d1a: { video: 'computer', shots: ['computer', 'fdcanusb', 'can15'] },
  d1b: { video: 'fdcanusb', shots: ['computer', 'fdcanusb', 'camera'] },
  d1c: { video: 'computer', shots: ['computer', 'fdcanusb', 'can15'] },
  d1d: { video: 'camera', shots: ['computer', 'camera', 'fdcanusb'] },
  d1e: { video: 'fdcanusb', shots: ['computer', 'fdcanusb', 'camera'] },
};

function pluginsDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diag-record-plugins-'));
  fs.cpSync(path.join(ROOT, 'tests', 'fixtures', 'plugins'), dir, { recursive: true });
  fs.copyFileSync(path.join(ROOT, 'plugins.d', 'computer.toml'), path.join(dir, 'computer.toml'));
  return dir;
}
function startServer(fixture, plugins) {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'diag-record-state-'));
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, IO_DIAGNOSTICS_CONFIG_DIR: fs.mkdtempSync(path.join(os.tmpdir(), 'diag-record-config-')) };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', fixture,
    '--plugins', plugins, '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stop(child); reject(new Error(`server did not start: ${output}`)); }, 90000);
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
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

async function ready(page) {
  await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
}
async function layoutReady(page, id) {
  const selector = id === 'd1' ? '#detail [data-d1] .ioh i' : `#detail [data-dx="${id}"] .ioh i`;
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.waitForTimeout(500);
}
async function glide(page, locator, pause = 350) {
  const box = await locator.boundingBox();
  if (!box) throw new Error('target not visible');
  const x = box.x + Math.min(box.width / 2, 140);
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y, { steps: 14 });
  await page.waitForTimeout(pause);
  return { x, y };
}
async function clickOn(page, locator, pause = 350) {
  const { x, y } = await glide(page, locator, pause);
  await page.mouse.click(x, y);
}

// ---------------------------------------------------------------------------------------------------- shots
async function shoot(browser, hosts, layout, entity, scheme, shots) {
  const target = ENTITIES[entity];
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: scheme });
  await context.addInitScript((id) => { try { localStorage.setItem('io-diagnostics.detail-layout', id); } catch (_) { /* */ } }, layout);
  const page = await context.newPage();
  await page.goto(`${hosts[target.host]}/?sel=${encodeURIComponent(target.sel)}`);
  await ready(page);
  await layoutReady(page, layout);
  await page.waitForTimeout(800);
  const file = `${layout}-${entity}-${scheme}.png`;
  await page.screenshot({ path: path.join(OUT, file) });
  shots.push({ layout, entity, scheme, file, name: target.name });
  console.log(`shot  ${file}`);
  await context.close();
}

// The chart rule on a synthetic history, labelled as such: the same cells (detail/health.js) over every state, next to the old look
// (today's d1 bar: 12 px tall, 1 px gaps, flex cells) on the same bins. Real data rarely holds every state at once.
async function specimen(browser, hosts, scheme) {
  const context = await browser.newContext({ viewport: { width: 900, height: 520 }, colorScheme: scheme });
  const page = await context.newPage();
  await page.goto(`${hosts.f1}/`);
  await ready(page);
  await page.waitForFunction(() => window.IOHealth);
  await page.evaluate(() => {
    const H = window.IOHealth;
    const code = { o: 'ok', w: 'warn', e: 'error', s: 'stale', u: 'unknown', n: null };
    // a run-length pattern, e.g. [['n', 10], ['o', 50]]: 10 bins of no data, then 50 OK
    const levels = (runs) => runs.flatMap(([letter, times]) => Array(times).fill(code[letter]));
    const rows = [
      ['a quiet day', [['n', 20], ['o', 40]]],
      ['a warning that came and went', [['n', 10], ['o', 24], ['w', 8], ['o', 18]]],
      ['a fault, then a restart', [['n', 5], ['o', 19], ['e', 13], ['s', 6], ['o', 17]]],
      ['a board that went quiet', [['n', 10], ['o', 25], ['s', 25]]],
      ['not evaluated (port held)', [['n', 20], ['u', 40]]],
      ['flapping', [['o', 3], ['w', 1], ['o', 3], ['w', 1], ['o', 3], ['w', 1], ['o', 2], ['w', 2], ['o', 3], ['w', 1], ['o', 4], ['w', 1], ['o', 3], ['w', 1], ['o', 3], ['w', 2], ['o', 3], ['w', 1], ['o', 4], ['w', 1], ['o', 3], ['w', 1], ['o', 4], ['w', 1], ['o', 1]]],
    ];
    // after: as many thin bars as the width holds (health.js count()), each covering window / count; the same history resampled
    const thin = H.count(900, 650, 'timeline');
    const cells = (runs) => { const base = levels(runs); return Array.from({ length: thin }, (_, at) => ({ level: base[Math.floor((at * base.length) / thin)], from: at * (900 / thin), to: (at + 1) * (900 / thin), message: '' })); };
    const old = (runs) => `<div style="display:flex;gap:1px;height:12px">${levels(runs).map((level) => `<i style="flex:1 1 0;min-width:1px;border-radius:1px;background:${level === 'warn' ? 'var(--io-warn)' : level === 'error' ? 'var(--io-error)' : level === 'stale' ? 'var(--io-stale)' : level === 'unknown' ? 'var(--io-unknown)' : level === 'ok' ? 'var(--io-guide)' : 'transparent'};${level ? '' : 'box-shadow:inset 0 -2px 0 var(--io-border)'}"></i>`).join('')}</div>`;
    const box = document.createElement('div');
    box.id = 'specimen';
    box.style.cssText = 'position:fixed;inset:0;z-index:5000;background:var(--io-surface);color:var(--io-text);padding:18px 24px;font:13px var(--io-font-ui);overflow:hidden';
    box.innerHTML = '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--io-muted);font-weight:600;margin-bottom:10px">Synthetic history over 15 min (not a recording) · before (top): 60 bins of 15 s, 12 px · after (bottom): thin 3 px bars, 2 px apart, as many as the width holds</div>'
      + rows.map(([label, runs]) => `<div style="display:grid;grid-template-columns:190px 1fr;gap:14px;align-items:center;margin:0 0 12px"><div style="color:var(--io-muted)">${label}</div>`
        + `<div style="display:grid;gap:7px"><div data-diagnostic>${old(runs)}</div>${H.html(cells(runs), { kind: 'timeline' })}</div></div>`).join('')
      + `<div style="margin-left:204px">${H.legend()}</div>`;
    document.body.appendChild(box);
  });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, `specimen-${scheme}.png`) });
  console.log(`shot  specimen-${scheme}.png`);
  await context.close();
}

// ---------------------------------------------------------------------------------------------------- videos
const FLOWS = {
  // the Single-line table: drop the status line down into lanes, fold it, open one row
  async d1a(page) {
    await hover(page, '.sec.dx-diag .dx-h .ioh > i:nth-child(6)', 900);
    await clickOn(page, page.locator('[data-dx-toggle="sum"]'), 250);
    await page.waitForTimeout(900);
    await hover(page, '.dx-drop .dx-lane:nth-child(4) .ioh > i:nth-child(30)', 1000);
    await clickOn(page, page.locator('[data-dx-toggle="sum"]'), 250);
    await page.waitForTimeout(500);
    await clickOn(page, page.locator('[data-ro-pin="bam-GPU: RAM Information"]'), 250);        // ★ pin it to the top
    await page.waitForTimeout(1200);
    await clickOn(page, page.locator('[data-ro-sort="name"]'), 250);                              // A-Z inside the folder
    await page.waitForTimeout(1200);
    await clickOn(page, page.locator('.dx-line', { hasText: 'Uptime' }), 250);
    await page.waitForTimeout(1500);
  },
  // Problems first: the problem card with its what-to-check, the OK line opens into single-line rows
  async d1b(page) {
    await hover(page, '.db-p .ioh > i:nth-child(8)', 1500);
    await clickOn(page, page.locator('[data-dx-toggle="ok"]'));
    await page.waitForTimeout(1500);
    await clickOn(page, page.locator('[data-dx-toggle="sum"]'));
    await page.waitForTimeout(1800);
  },
  // State timeline: hover the lanes, click one to read its message under it, then another
  async d1c(page) {
    await hover(page, '.dx-lane:nth-child(6) .ioh > i:nth-child(25)', 1500);
    await clickOn(page, page.locator('.dx-lane', { hasText: 'Temperature CPU' }));
    await page.waitForTimeout(1800);
    await clickOn(page, page.locator('.dx-lane', { hasText: 'HD Usage' }));
    await page.waitForTimeout(1500);
    await clickOn(page, page.locator('.ro-fold', { hasText: 'bam-GPU' }).first());          // fold the folder
    await page.waitForTimeout(1300);
    await clickOn(page, page.locator('.ro-fold', { hasText: 'bam-GPU' }).first());
    await page.waitForTimeout(900);
  },
  // rqt_robot_monitor: open All, pick a status, read its key / value panel, pick another
  async d1d(page) {
    await page.waitForTimeout(800);
    await clickOn(page, page.locator('.dd-gh', { hasText: 'All' }));
    await page.waitForTimeout(1200);
    await clickOn(page, page.locator('.dd-leaf', { hasText: 'firmware' }).first());
    await page.waitForTimeout(1800);
    await clickOn(page, page.locator('.dd-leaf', { hasText: 'plugin run' }).first());
    await page.waitForTimeout(1800);
  },
  // Checklist: the problem is open with its steps, an OK line opens, the status line drops down
  async d1e(page) {
    await hover(page, '.de-r.bad .ioh > i:nth-child(8)', 1500);
    await clickOn(page, page.locator('.de-r.ok .de-line').first());
    await page.waitForTimeout(1600);
    await clickOn(page, page.locator('[data-dx-toggle="sum"]'));
    await page.waitForTimeout(1800);
  },
};
async function hover(page, selector, hold) {
  const cell = page.locator(selector).first();
  if (await cell.count()) await glide(page, cell, hold);
  else await page.waitForTimeout(hold);
}

async function hero(browser, hosts, layout, scheme = 'light') {
  const entity = PLAN[layout].video;
  const target = ENTITIES[entity];
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diag-record-video-'));
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: scheme, recordVideo: { dir, size: { width: 1280, height: 800 } } });
  // starts on d1 (today's); the clip picks the variant from the Prototype select, as the person would
  await context.addInitScript((id) => { try { localStorage.setItem('io-diagnostics.detail-layout', id); } catch (_) { /* */ } }, 'd1');
  await context.addInitScript(CURSOR_SCRIPT);
  const page = await context.newPage();
  const began = Date.now();
  await page.goto(`${hosts[target.host]}/?sel=${encodeURIComponent(target.sel)}`);
  await ready(page);
  await layoutReady(page, 'd1');
  await page.mouse.move(640, 400);
  const cutAt = (Date.now() - began) / 1000;
  await page.waitForTimeout(1200);
  await page.selectOption('#detail-layout', layout);
  await layoutReady(page, layout);
  await page.waitForTimeout(1200);
  await FLOWS[layout](page);
  await context.close();
  const webm = await page.video().path();
  const mp4 = path.join(OUT, `${layout}-hero.mp4`);
  const gif = path.join(OUT, `${layout}-hero.gif`);
  const trim = ['-ss', cutAt.toFixed(2), '-t', '15'];
  const encoded = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', webm, ...trim, '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '28', '-preset', 'veryfast', '-movflags', '+faststart', mp4]);
  const gifOut = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', webm, ...trim, '-vf', 'fps=8,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer', gif]);
  const seconds = spawnSync('/usr/bin/ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4]).stdout.toString().trim();
  console.log(`video ${layout}-hero.mp4 ${seconds} s (ffmpeg ${encoded.status}/${gifOut.status}), ${layout}-hero.gif ${(fs.statSync(gif).size / 1e6).toFixed(1)} MB`);
  return { layout, entity, name: target.name, mp4: path.basename(mp4), gif: path.basename(gif), seconds: Number(seconds) };
}

async function main() {
  const { chromium } = require(PLAYWRIGHT);
  const owned = [];
  const hosts = { f1: arg('--f1'), f2: arg('--f2') };
  if (!hosts.f1 || !hosts.f2) {
    const plugins = pluginsDir();
    const [f1, f2] = await Promise.all([startServer('F1', plugins), startServer('F2', plugins)]);
    owned.push(f1.child, f2.child);
    hosts.f1 = f1.base;
    hosts.f2 = f2.base;
    console.log(`warming the hosts for ${WARM} s (the history window fills)`);
    await sleep(WARM * 1000);
  }
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const manifest = { shots: [], videos: [], hosts };
  try {
    for (const layout of ['d1', ...ONLY]) {
      for (const entity of PLAN[layout].shots) for (const scheme of ['light', 'dark']) await shoot(browser, hosts, layout, entity, scheme, manifest.shots);
    }
    for (const scheme of ['light', 'dark']) await specimen(browser, hosts, scheme);
    if (VIDEOS) for (const layout of ONLY) manifest.videos.push(await hero(browser, hosts, layout));
  } finally {
    await browser.close();
    for (const child of owned) stop(child);
  }
  fs.writeFileSync(path.join(OUT, 'shots.json'), JSON.stringify(manifest, null, 1));
}

main().catch((error) => { console.error(error); process.exit(1); });
