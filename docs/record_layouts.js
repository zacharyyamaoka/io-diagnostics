#!/usr/bin/env node
// The media of the layout comparison report (docs/build_layouts_report.py): for each proposal L1..L5 (and L0 "Today" as the
// baseline), screenshots at 1400x900 in light and dark with an entity selected, a few extra states, and a hero video of the
// real page: open the app, search a board, open it, glance at Activity. Recorded from the REAL page on a fresh fixture host
// each time (F1; never hardware: `serve --fixture F1 --no-udev`), in headless Chrome, then cut to a short muted loop with ffmpeg.
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_layouts.js [--out /abs/dir] [--only l1,l3] [--no-video]
//
// Writes <out>/<id>-<light|dark>.png, <out>/<id>-<state>-<light|dark>.png, <out>/<id>-hero.mp4 and <id>-hero.gif, and
// <out>/manifest.json. L5's Overview needs the computer's resources, so its Overview shots come from a second fixture host that
// also runs the passive, read-only computer plugin (psutil, sysfs, nvidia-smi: no /dev node). Every server it starts (a free port,
// a temporary state dir) is stopped by its process group before exit.
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
const arg = (name) => { const at = process.argv.indexOf(name); return at > 0 ? process.argv[at + 1] : null; };
const OUT = arg('--out') || path.join(ROOT, 'reports', 'media', 'layouts');
const ONLY = (arg('--only') || 'l0,l1,l2,l3,l4,l5').split(',');
const VIDEO = !process.argv.includes('--no-video');
const SHOT = { width: 1400, height: 900 };
const FILM = { width: 1280, height: 800 };
const CAN15 = 'host/usb1/port-1-3/usb-1-3/can/15';
const PICK = 'io-diagnostics.page-layout';

function startServer({ computer = false } = {}) {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'layouts-rec-'));
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir };
  delete env.VIRTUAL_ENV;
  const args = ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', 'F1', '--port', '0', '--no-udev'];
  if (computer) {
    // the fixture's driver plugin plus the computer plugin (passive: reads psutil / sysfs, opens no /dev node)
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'layouts-rec-plugins-'));
    for (const file of ['v6_scenario.py', 'v6_scenario.toml', 'v6_frames.json']) fs.copyFileSync(path.join(ROOT, 'tests', 'fixtures', 'plugins', file), path.join(dir, file));
    fs.copyFileSync(path.join(ROOT, 'plugins.d', 'computer.toml'), path.join(dir, 'computer.toml'));
    args.push('--plugins', dir);
  }
  const child = spawn(UV, args, { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
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

const ready = async (page, layout) => {
  await page.waitForFunction((id) => typeof S !== 'undefined' && S.built && document.querySelector('.d-title') && window.IOLayoutHost && IOLayoutHost.id === id, layout, { timeout: 30000 });
  await page.waitForTimeout(500);
};
const glide = async (page, locator, { dx = 0 } = {}) => {
  const box = await locator.first().boundingBox();
  if (!box) throw new Error('target not visible');
  const x = box.x + Math.min(box.width / 2, 90) + dx;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y, { steps: 16 });
  await page.waitForTimeout(200);
  await page.mouse.click(x, y);
};
const pick = (page, layout, options = {}) => page.context().addInitScript(([key, id, dark]) => {
  try { localStorage.setItem(key, id); localStorage.setItem('io-diagnostics.mode', dark ? 'dark' : 'light'); } catch (_) { /* none */ }
}, [PICK, layout, Boolean(options.dark)]);

// ---- the screenshots: an entity selected, light and dark, and the states that make each proposal what it is ----
const SHOTS = {
  l0: { main: { query: `?sel=${CAN15}` } },
  l1: { main: { query: `?sel=${CAN15}` }, table: { query: `?sel=${CAN15}`, act: async (page) => { await page.click('.l1-ribbon [data-l1="table"]'); await page.waitForTimeout(700); }, label: 'Table view' },
    search: { query: `?sel=${CAN15}`, act: async (page) => { await page.fill('#search', 'can'); await page.waitForTimeout(600); }, label: 'Search in the tree' } },
  l2: { main: { query: `?sel=${CAN15}` }, table: { query: `?sel=${CAN15}`, act: async (page) => { await page.click('.l2-act [data-l2="table"]'); await page.waitForTimeout(700); }, label: 'Table as a tab' } },
  l3: { main: { query: `?sel=${CAN15}`, act: async (page) => { await page.click('.l3-bar [data-l3-pane="activity"]'); await page.waitForTimeout(700); }, label: 'with the side pane open on Activity' },
    closed: { query: `?sel=${CAN15}`, label: 'the calm default: one list, one page' } },
  l4: { main: { query: `?sel=${CAN15}` }, home: { query: '', label: 'the first screen', stable: true }, palette: { query: `?sel=${CAN15}`, act: async (page) => { await page.keyboard.press('Control+k'); await page.keyboard.type('can', { delay: 40 }); await page.waitForTimeout(500); }, label: 'Ctrl-K palette' } },
  l5: { main: { query: `?sel=${CAN15}`, label: 'Devices tab' }, overview: { query: '', computer: true, label: 'Overview tab', stable: true } },
};

async function shoot(browser, id, servers, manifest) {
  for (const scheme of ['light', 'dark']) {
    for (const [name, spec] of Object.entries(SHOTS[id])) {
      const server = spec.computer ? servers.computer : servers.plain;
      const context = await browser.newContext({ viewport: SHOT, deviceScaleFactor: 1, colorScheme: scheme });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', (error) => errors.push(String(error)));
      await pick(page, id, { dark: scheme === 'dark' });
      await page.goto(`${server.base}/${spec.query}`);
      await ready(page, id);
      if (spec.act) await spec.act(page);
      await page.waitForTimeout(spec.computer ? 2500 : 500);
      const file = `${id}-${name === 'main' ? '' : `${name}-`}${scheme}.png`;
      await page.screenshot({ path: path.join(OUT, file) });
      manifest.shots.push({ layout: id, state: name, scheme, file, label: spec.label || '', errors });
      console.log(`${errors.length ? 'FAIL' : 'PASS'}  shot ${file}${errors.length ? `  ${errors[0]}` : ''}`);
      await context.close();
    }
  }
}

// ---- the hero flows: open the app, search a board, open it, glance at Activity ----
const FLOWS = {
  l0: async (page) => {
    await glide(page, page.locator('#search'));
    await page.keyboard.type('272813', { delay: 140 });
    await page.waitForTimeout(900);
    await glide(page, page.locator('#tree-host .t6-row', { hasText: 'fdcanusb' }));
    await page.waitForTimeout(1500);
    await glide(page, page.locator('[data-view="activity"]'));
    await page.waitForTimeout(2200);
    await glide(page, page.locator('[data-view="tree"]'));
    await page.waitForTimeout(1200);
  },
  l1: async (page) => {
    await glide(page, page.locator('#search'));
    await page.keyboard.type('272813', { delay: 140 });
    await page.waitForTimeout(900);
    await glide(page, page.locator('#tree-host .t6-row', { hasText: 'fdcanusb' }));
    await page.waitForTimeout(1500);
    await glide(page, page.locator('.l1-ribbon [data-l1="activity"]'));
    await page.waitForTimeout(2200);
    await glide(page, page.locator('.l1-ribbon [data-l1="tree"]'));
    await page.waitForTimeout(1200);
  },
  l2: async (page) => {
    await glide(page, page.locator('#search'));
    await page.keyboard.type('272813', { delay: 140 });
    await page.waitForTimeout(900);
    await glide(page, page.locator('#tree-host .t6-row', { hasText: 'fdcanusb' }));
    await page.locator('#tree-host .t6-row', { hasText: 'fdcanusb' }).first().dblclick(); // a double-click keeps the tab
    await page.waitForTimeout(1300);
    await glide(page, page.locator('.l2-act [data-l2="activity"]'));
    await page.waitForTimeout(900);
    await glide(page, page.locator('.l2-pact .ev').first());
    await page.waitForTimeout(1800);
    await glide(page, page.locator('.l2-tab', { hasText: 'fdcanusb' }).first());
    await page.waitForTimeout(1400);
  },
  l3: async (page) => {
    await glide(page, page.locator('#search'));
    await page.keyboard.type('272813', { delay: 140 });
    await page.waitForTimeout(900);
    await glide(page, page.locator('#tree-host .t6-row', { hasText: 'fdcanusb' }));
    await page.waitForTimeout(1500);
    await glide(page, page.locator('.l3-bar [data-l3-pane="activity"]'));
    await page.waitForTimeout(2600);
    await glide(page, page.locator('.l3-bar [data-l3-pane="activity"]'));
    await page.waitForTimeout(1200);
  },
  l4: async (page) => {
    await page.waitForTimeout(1600);
    await glide(page, page.locator('#search'));
    await page.keyboard.type('272813', { delay: 140 });
    await page.waitForTimeout(900);
    await glide(page, page.locator('#tree-host .t6-row', { hasText: 'fdcanusb' }));
    await page.waitForTimeout(1500);
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);
    await page.keyboard.type('activity', { delay: 120 });
    await page.waitForTimeout(700);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(2200);
  },
  l5: async (page) => {
    await page.waitForTimeout(1600);
    await glide(page, page.locator('.l5-tabs [data-l5-tab="devices"]'));
    await page.waitForTimeout(900);
    await glide(page, page.locator('#search'));
    await page.keyboard.type('272813', { delay: 140 });
    await page.waitForTimeout(900);
    await glide(page, page.locator('#tree-host .t6-row', { hasText: 'fdcanusb' }));
    await page.waitForTimeout(1500);
    await glide(page, page.locator('.l5-tabs [data-l5-tab="activity"]'));
    await page.waitForTimeout(2200);
  },
};

async function hero(browser, id, server, manifest) {
  const videoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'layouts-hero-'));
  const startedMs = Date.now();
  const context = await browser.newContext({ viewport: FILM, deviceScaleFactor: 1, colorScheme: 'light', recordVideo: { dir: videoDir, size: FILM } });
  await context.addInitScript(CURSOR_SCRIPT);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await pick(page, id);
  await page.goto(`${server.base}/`);
  await ready(page, id);
  await page.mouse.move(640, 400);
  await page.waitForTimeout(500);
  const readyMs = Date.now() - startedMs;
  await FLOWS[id](page);
  const videoPath = await page.video().path();
  await context.close();
  const trim = Math.max(0, readyMs / 1000 - 0.3).toFixed(2);
  const mp4 = path.join(OUT, `${id}-hero.mp4`);
  const encode = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-ss', trim, '-i', videoPath, '-an', '-vf', 'fps=15', '-c:v', 'libx264', '-preset', 'slow',
    '-crf', '31', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
  const gif = path.join(OUT, `${id}-hero.gif`);
  const palette = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', mp4, '-vf', 'fps=5,scale=560:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=none', gif]);
  const probe = spawnSync('/usr/bin/ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4]);
  const seconds = Number(String(probe.stdout).trim());
  const ok = encode.status === 0 && palette.status === 0 && seconds >= 6 && seconds <= 15 && errors.length === 0;
  manifest.heroes.push({ layout: id, mp4: `${id}-hero.mp4`, gif: `${id}-hero.gif`, seconds, errors });
  console.log(`${ok ? 'PASS' : 'FAIL'}  hero ${id}: ${seconds.toFixed(1)} s${errors.length ? `  ${errors[0]}` : ''}${encode.status ? `  ffmpeg ${String(encode.stderr).slice(0, 200)}` : ''}`);
  return ok;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  // a run with --only keeps what the earlier runs recorded for the other layouts
  let manifest = { recorded: new Date().toISOString(), shots: [], heroes: [] };
  try { manifest = { ...JSON.parse(fs.readFileSync(path.join(OUT, 'manifest.json'), 'utf8')), recorded: manifest.recorded }; } catch (_) { /* first run */ }
  manifest.shots = manifest.shots.filter((shot) => !ONLY.includes(shot.layout));
  manifest.heroes = manifest.heroes.filter((film) => !ONLY.includes(film.layout));
  const started = [];
  let failed = false;
  try {
    for (const id of ONLY) {
      const plain = await startServer();
      started.push(plain.child);
      const servers = { plain, computer: plain };
      if (SHOTS[id] && Object.values(SHOTS[id]).some((spec) => spec.computer)) { servers.computer = await startServer({ computer: true }); started.push(servers.computer.child); }
      await shoot(browser, id, servers, manifest);
      if (VIDEO) {
        // a fresh host for the film: the fixture's data turns stale a few minutes after it starts
        const film = await startServer(id === 'l5' ? { computer: true } : {});
        started.push(film.child);
        if (!(await hero(browser, id, film, manifest))) failed = true;
        stop(film.child);
      }
      stop(plain.child);
      if (servers.computer !== plain) stop(servers.computer.child);
    }
  } finally {
    await browser.close();
    for (const child of started) stop(child);
  }
  manifest.shots.forEach((shot) => { if (shot.errors.length) failed = true; });
  fs.writeFileSync(path.join(OUT, 'manifest.json'), JSON.stringify(manifest, null, 1));
  console.log(`\n${manifest.shots.length} screenshots, ${manifest.heroes.length} heroes in ${OUT}`);
  process.exit(failed ? 1 : 0);
})().catch((error) => { console.error(error); process.exit(1); });
