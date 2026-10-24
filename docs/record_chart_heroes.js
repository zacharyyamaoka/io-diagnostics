#!/usr/bin/env node
// Screenshots and hero videos of the five chart proposals (io_diagnostics/web/charts/c1.js .. c5.js), recorded from the REAL page in
// headless Chrome on a fixture host with moving series (tests/fixtures/plugins-charts: never touches hardware):
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_chart_heroes.js --base http://127.0.0.1:PORT [--out DIR] [c1 c2 ...] [--no-video]
//
// The host must have been running for a while (the series fill at 2 Hz; the history window is 15 min), so --base names a host the
// caller started: `cli.py serve --fixture F1 --plugins tests/fixtures/plugins-charts --port N --no-udev`, with IO_DIAGNOSTICS_CONFIG_DIR
// set to a scratch dir (c4 writes the app's settings). Writes, per proposal, <out>/<id>-<state>-<light|dark>.png at 1400x900 and, for
// light only, <id>-hero.mp4 (H.264, muted, loops, 1280x800), <id>-hero.gif (the fallback) and <id>-hero.json (its length).
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const FFMPEG = '/usr/bin/ffmpeg';
const args = process.argv.slice(2);
const flag = (name) => { const at = args.indexOf(name); return at >= 0 ? args[at + 1] : null; };
const BASE = flag('--base');
const OUT = flag('--out') || path.join(ROOT, 'reports', 'media', 'chart-enlarge');
const NO_VIDEO = args.includes('--no-video');
const WANT = args.filter((arg) => /^c[1-5]$/.test(arg));
if (!BASE) { console.error('usage: record_chart_heroes.js --base http://127.0.0.1:PORT [--out DIR] [c1..c5] [--no-video]'); process.exit(2); }

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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---- driving helpers ----
const at = (page, label, inner = '.ch-line') => page.evaluate(([wanted, sel]) => {
  const el = [...document.querySelectorAll('.ch-spark')].find((node) => JSON.parse(node.dataset.spark).l === wanted);
  if (!el) return null;
  el.scrollIntoView({ block: 'center' });
  const r = (el.querySelector(sel) || el).getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + Math.min(r.height / 2, 14), w: r.width, h: r.height };
}, [label, inner]);
async function glide(page, x, y, steps = 14) { await page.mouse.move(x, y, { steps }); await page.waitForTimeout(120); }
async function toSpark(page, label, inner) {
  const c = await at(page, label, inner);
  if (!c) throw new Error(`no sparkline ${label}`);
  await page.waitForTimeout(200);
  const again = await at(page, label, inner); // scrolling settles the layout
  await glide(page, again.x - 60, again.y, 10);
  await glide(page, again.x, again.y, 6);
  return again;
}
const settle = (page, ms = 700) => page.waitForTimeout(ms);
// scroll the Computer page's Resources section to the top of the detail
const toResources = (page) => page.evaluate(() => { const g = document.querySelector('[data-d4-group="cpu"]'); if (g) g.scrollIntoView({ block: 'start' }); });
const wheel = async (page, x, y, dy, n = 1) => { await page.mouse.move(x, y); for (let i = 0; i < n; i += 1) { await page.mouse.wheel(0, dy); await page.waitForTimeout(90); } };

// ================================ scenarios: ctl.snap(name) takes a screenshot; ctl.pace(ms) waits (video only paces) ================
const SCENARIOS = {
  async c1(page, ctl) {
    await toResources(page);
    await ctl.pace(600);
    const cpu = await toSpark(page, 'CPU');
    await page.waitForSelector('.ch1-pop .chp-svg');
    await ctl.pace(900);
    // read out along the line: the cursor in the popover
    const plot = await page.evaluate(() => { const r = document.querySelector('.ch1-pop .chp-svg').getBoundingClientRect(); return { l: r.left, w: r.width, y: r.top + r.height * 0.5 }; });
    await glide(page, plot.l + plot.w * 0.62, plot.y + 30, 10);
    await glide(page, plot.l + plot.w * 0.78, plot.y + 30, 12);
    await ctl.pace(500);
    await glide(page, plot.l + plot.w * 0.93, plot.y + 30, 12);
    await ctl.pace(700);
    await ctl.snap('hover');
    await page.click('.ch1-pop [data-w="300"]');
    await ctl.pace(1400);
    await page.mouse.move(cpu.x - 30, cpu.y + 2, { steps: 10 });
    await page.mouse.click(cpu.x - 30, cpu.y + 2); // a press pins
    await ctl.pace(700);
    await ctl.snap('pinned');
    await page.mouse.move(40, 600, { steps: 10 });
    await ctl.pace(900);
    await page.keyboard.press('Escape');
    await ctl.pace(500);
    await toSpark(page, 'Temperatures');
    await page.waitForSelector('.ch1-pop .chp-svg');
    await ctl.pace(1500);
    await ctl.snap('limits');
    await page.mouse.move(40, 600, { steps: 10 });
    await ctl.pace(700);
  },
  async c2(page, ctl) {
    await toResources(page);
    await ctl.pace(500);
    const cpu = await toSpark(page, 'CPU');
    await ctl.pace(500);
    await page.mouse.click(cpu.x, cpu.y);
    await page.waitForSelector('.ch-spark[data-open] .ch-host');
    await ctl.pace(1500);
    await glide(page, cpu.x - 200, cpu.y + 130, 12);
    await ctl.pace(900);
    await ctl.snap('open');
    const temps = await toSpark(page, 'Temperatures');
    await ctl.pace(400);
    await page.mouse.click(temps.x, temps.y);
    await ctl.pace(1600);
    await page.evaluate(() => { const g = document.querySelector('[data-d4-group="temps"]'); if (g) g.scrollIntoView({ block: 'center' }); });
    await ctl.pace(900);
    await ctl.snap('two-open');
    // fold the CPU one again from its header
    await page.evaluate(() => { const g = document.querySelector('[data-d4-group="cpu"]'); g.scrollIntoView({ block: 'start' }); });
    await ctl.pace(500);
    const head = await page.evaluate(() => { const r = document.querySelector('[data-d4-group="cpu"] .ch-host').shadowRoot.querySelector('.ch2-head').getBoundingClientRect(); return { x: r.left + 60, y: r.top + r.height / 2 }; });
    await glide(page, head.x, head.y, 14);
    await ctl.pace(400);
    await page.mouse.click(head.x, head.y);
    await ctl.pace(1200);
  },
  async c3(page, ctl) {
    await toResources(page);
    await ctl.pace(500);
    const mem = await toSpark(page, 'Memory');
    await ctl.pace(400);
    await page.mouse.click(mem.x, mem.y);
    await page.waitForSelector('#ch3-pane .chp-svg');
    await ctl.pace(1100);
    const temp = await toSpark(page, 'Temperatures');
    await ctl.pace(300);
    await page.mouse.click(temp.x, temp.y);
    await ctl.pace(1000);
    const net = await toSpark(page, 'Network');
    await ctl.pace(300);
    await page.mouse.click(net.x, net.y, { button: 'right' });
    await page.waitForSelector('.ch3-menu');
    await ctl.pace(900);
    await ctl.snap('menu');
    await page.click('.ch3-menu [data-m="R"]');
    await ctl.pace(1100);
    const pl = await page.evaluate(() => { const r = document.querySelector('#ch3-pane .chp-svg').getBoundingClientRect(); return { l: r.left, w: r.width, t: r.top, h: r.height }; });
    await glide(page, pl.l + pl.w * 0.9, pl.t + pl.h * 0.5, 12);
    await glide(page, pl.l + pl.w * 0.96, pl.t + pl.h * 0.5, 12);
    await ctl.pace(700);
    await ctl.snap('bottom');
    await page.click('#ch3-pane [data-act="dock"]');
    await ctl.pace(900);
    const sash = await page.evaluate(() => { const r = document.querySelector('#ch3-pane .ch3-sash').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
    await glide(page, sash.x, sash.y, 8);
    await page.mouse.down();
    await page.mouse.move(sash.x - 120, sash.y, { steps: 14 });
    await page.mouse.up();
    await ctl.pace(1000);
    await ctl.snap('right');
  },
  async c4(page, ctl) {
    await page.evaluate(async () => { await IOCharts.ctx.saveSettings({ dashboard: [] }); localStorage.removeItem('io-diagnostics.chart.c4.tiles'); });
    await page.reload();
    await page.waitForSelector('.ch-spark');
    await settle(page, 1500);
    await toResources(page);
    await ctl.pace(500);
    const cpu = await toSpark(page, 'CPU');
    await ctl.pace(500);
    await ctl.snap('pin-button');
    await page.locator('.ch-spark').nth(await page.evaluate(() => [...document.querySelectorAll('.ch-spark')].findIndex((e) => JSON.parse(e.dataset.spark).l === 'CPU'))).locator('.ch-btn').click();
    await ctl.pace(700);
    for (const label of ['Temperatures', 'Network']) {
      const spark = await toSpark(page, label);
      await ctl.pace(150);
      await page.mouse.click(spark.x, spark.y);
      await ctl.pace(300);
    }
    await ctl.pace(200);
    const tab = await page.evaluate(() => { const r = document.querySelector('button[data-ch4]').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
    await glide(page, tab.x, tab.y, 16);
    await page.mouse.click(tab.x, tab.y);
    await page.waitForSelector('#ch4-dash .ch4-tile .chp-svg');
    await ctl.pace(1300);
    await ctl.snap('board');
    // resize the first tile, reorder two others
    const h = await page.evaluate(() => { const r = document.querySelector('.ch4-tile [data-rs]').getBoundingClientRect(); return { x: r.left + 9, y: r.top + 9 }; });
    await glide(page, h.x, h.y, 10);
    await page.mouse.down();
    await page.mouse.move(h.x + 220, h.y + 90, { steps: 16 });
    await page.mouse.up();
    await ctl.pace(600);
    const heads = await page.evaluate(() => [...document.querySelectorAll('.ch4-tile .ch4-th')].map((e) => { const r = e.getBoundingClientRect(); return { x: r.left + 140, y: r.top + 12 }; }));
    await glide(page, heads[2].x, heads[2].y, 10);
    await page.mouse.down();
    await page.mouse.move(heads[2].x - 40, heads[2].y + 30, { steps: 6 });
    await page.mouse.move(heads[0].x + 40, heads[0].y + 60, { steps: 16 });
    await page.mouse.up();
    await ctl.pace(900);
    await ctl.snap('arranged');
    await page.reload();
    await page.waitForSelector('#ch4-dash .ch4-tile .chp-svg');
    await ctl.pace(1300);
    await ctl.snap('after-reload');
    await page.evaluate(async () => { await IOCharts.ctx.saveSettings({ dashboard: [] }); });
  },
  async c5(page, ctl) {
    await toResources(page);
    await ctl.pace(500);
    const cpu = await toSpark(page, 'Temperatures'); // WHY Temperatures: its data crosses the warn limit, so the health strip has spans to show
    await ctl.pace(500);
    await page.mouse.click(cpu.x, cpu.y);
    await page.waitForSelector('#ch5-page .chp-svg');
    await ctl.pace(1800);
    await ctl.snap('page');
    const area = await page.evaluate(() => { const r = document.querySelector('#ch5-page .chp-svg').getBoundingClientRect(); return { l: r.left, w: r.width, t: r.top, h: r.height }; });
    await glide(page, area.l + area.w * 0.9, area.t + area.h * 0.35, 14);
    await ctl.pace(700);
    await wheel(page, area.l + area.w * 0.93, area.t + area.h * 0.35, -120, 5);
    await ctl.pace(1000);
    await ctl.snap('zoomed');
    await page.mouse.move(area.l + area.w * 0.6, area.t + area.h * 0.4);
    await page.mouse.down();
    await page.mouse.move(area.l + area.w * 0.35, area.t + area.h * 0.4, { steps: 14 });
    await page.mouse.up();
    await ctl.pace(1200);
    await page.dblclick('#ch5-page .chp-svg', { position: { x: area.w * 0.5, y: 120 } });
    await ctl.pace(1300);
    const back = await page.evaluate(() => { const r = document.querySelector('#ch5-page [data-back]').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
    await glide(page, back.x, back.y, 14);
    await page.mouse.click(back.x, back.y);
    await ctl.pace(1500);
  },
};

async function run(chromium, id, mode, video) {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const size = video ? { width: 1280, height: 800 } : { width: 1400, height: 900 };
  const tmp = path.join(OUT, `.rec-${id}-${mode}`);
  const context = await browser.newContext({ viewport: size, colorScheme: mode, ...(video ? { recordVideo: { dir: tmp, size } } : {}) });
  await context.addInitScript(([v, m]) => { try { if (!sessionStorage.getItem('rec-init')) { sessionStorage.setItem('rec-init', '1'); localStorage.setItem('io-diagnostics.chart-view', v); localStorage.setItem('io-diagnostics.mode', m); } } catch (_) { /* private mode */ } }, [id, mode]);
  if (video) await context.addInitScript(CURSOR_SCRIPT);
  const page = await context.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${BASE}/?sel=host`);
  await page.waitForSelector('.ch-spark', { timeout: 30000 });
  await sleep(2500);
  const started = Date.now();
  const ctl = {
    snap: async (name) => { fs.mkdirSync(OUT, { recursive: true }); await page.screenshot({ path: path.join(OUT, `${id}-${name}-${mode}.png`) }); },
    pace: (ms) => page.waitForTimeout(video ? ms : Math.min(ms, 500)),
  };
  await SCENARIOS[id](page, ctl);
  const seconds = (Date.now() - started) / 1000;
  await context.close();
  await browser.close();
  if (errors.length) console.log(`  ${id} ${mode}: console errors`, errors.slice(0, 3));
  return { tmp, seconds };
}

(async () => {
  const { chromium } = require(PLAYWRIGHT);
  fs.mkdirSync(OUT, { recursive: true });
  for (const id of (WANT.length ? WANT : ['c1', 'c2', 'c3', 'c4', 'c5'])) {
    console.log(`# ${id}`);
    for (const mode of ['dark', 'light']) {
      const video = mode === 'light' && !NO_VIDEO;
      const { tmp, seconds } = await run(chromium, id, mode, video);
      console.log(`  ${id} ${mode}: done in ${seconds.toFixed(1)} s`);
      if (!video) continue;
      const webm = fs.readdirSync(tmp).filter((name) => name.endsWith('.webm')).map((name) => path.join(tmp, name))[0];
      const mp4 = path.join(OUT, `${id}-hero.mp4`); const gif = path.join(OUT, `${id}-hero.gif`);
      // cut the page load off the front (2.5 s of settle + load), keep the demonstration, mute, loop in the page
      const cut = ['-ss', '2.6'];
      const a = spawnSync(FFMPEG, ['-y', '-v', 'error', ...cut, '-i', webm, '-an', '-vf', 'fps=25,scale=1280:-2', '-c:v', 'libx264', '-preset', 'slow', '-crf', '24', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
      const b = spawnSync(FFMPEG, ['-y', '-v', 'error', ...cut, '-i', webm, '-an', '-vf', 'fps=8,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4', gif]);
      if (a.status !== 0 || b.status !== 0) console.log('  ffmpeg failed', String(a.stderr), String(b.stderr));
      const probe = spawnSync('/usr/bin/ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4]);
      const length = parseFloat(String(probe.stdout));
      fs.writeFileSync(path.join(OUT, `${id}-hero.json`), JSON.stringify({ id, seconds: length, mp4: path.basename(mp4), gif: path.basename(gif) }, null, 1));
      console.log(`  ${id}: hero ${length.toFixed(1)} s · ${(fs.statSync(mp4).size / 1024).toFixed(0)} KB mp4 · ${(fs.statSync(gif).size / 1024).toFixed(0)} KB gif`);
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }
})().catch((error) => { console.error(error); process.exit(1); });
