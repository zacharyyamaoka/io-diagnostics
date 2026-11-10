#!/usr/bin/env node
// The status dots' captures (reports/status-dots-2026-11-10.html), from the REAL page on a fixture host (the entity page's: tests/entity_harness.js,
// stand-in plugins that never touch hardware, utilities that are real runs of a stand-in process that sleeps and refuses), headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_status_dots.js --root <checkout> --label before|after [--out /abs/dir] [--clip]
//
// Run once on the commit before the change (a worktree of it: --label before) and once on the change (--label after), the same story:
//   idle      the adapter's presence is "live" (a follower streaming: alive), CAN 1 and the camera heard, the other adapter's board stale
//   running   Run calibration on CAN 1 and Identify on the camera, both real runs
// Writes <out>/<label>-<state>-<row>.png (a row's left 340 px at deviceScaleFactor 2, native pixels), <label>-<state>-tree.png, facts.json
// (computed style of every dot) and, with --clip, <label>-pulse.mp4 (+ .gif): the tree pane over ~7 s, frames taken at native resolution.
// The server it starts (a scratch state dir, a free port) is stopped by its process group.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const arg = (name, fallback) => { const at = process.argv.indexOf(`--${name}`); return at > 0 ? process.argv[at + 1] : fallback; };
const ROOT = path.resolve(arg('root', path.join(__dirname, '..')));
const LABEL = arg('label', 'after');
const OUT = arg('out', path.join(ROOT, 'reports', 'media', 'status-dots'));
const CLIP = process.argv.includes('--clip');
const H = require(path.join(ROOT, 'tests', 'entity_harness'));
const { chromium } = require(H.PLAYWRIGHT);
const FFMPEG = '/usr/bin/ffmpeg';
const HEADERS = { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' };
const STALE = { state: 'stale', age_s: 2463, restored: false, text: 'last heard 41 min ago' };
const LIVE = { state: 'live', age_s: 0.4, restored: false, text: 'data arriving now' };
const ROWS = { adapter: H.ADAPTER, board: H.BOARD, board2: H.BOARD2, camera: H.CAMERA, adapter2: H.BOARD2.split('/can/')[0] };

const patch = (value) => {
  if (Array.isArray(value)) value.forEach(patch);
  else if (value && typeof value === 'object') {
    if (value.path === H.BOARD2 && 'presence' in value) value.presence = STALE;
    if (value.path === H.ADAPTER && 'presence' in value) value.presence = LIVE;
    Object.values(value).forEach(patch);
  }
  return value;
};

const dotStyle = (page, target) => page.evaluate((entity) => {
  const dot = document.querySelector(`#tree-host [role=treeitem][data-t6-path="${entity}"] .t6-pres`);
  if (!dot) return null;
  const style = getComputedStyle(dot);
  return { classes: [...dot.classList].join(' '), animationName: style.animationName, animationDuration: style.animationDuration, opacity: Number(style.opacity),
    background: style.backgroundColor, boxShadow: style.boxShadow, title: dot.title };
}, target);

async function rowShot(page, key, name) {
  const box = await (await page.$(`#tree-host [role=treeitem][data-t6-path="${ROWS[key]}"]`)).boundingBox();
  await page.screenshot({ path: path.join(OUT, `${LABEL}-${name}-${key}.png`), clip: { x: box.x, y: box.y, width: Math.min(box.width, 340), height: box.height } });
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const server = await H.fixtureHost('F1', { STATE_ACT_S: '60' });
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  const facts = { label: LABEL, root: ROOT, states: {} };
  try {
    const context = await browser.newContext({ viewport: H.VIEWPORT, colorScheme: 'light', deviceScaleFactor: 2 });
    const page = await context.newPage();
    await context.route('**/api/v1/live', () => {}); // WHY held: the patched /state reaches the page on its polling fallback
    await context.route(/\/api\/v1\/(state|node)/, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: patch(await response.json()) });
    });
    await page.goto(`${server.base}/`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('#tree-host [role=treeitem]'), null, { timeout: 30000 });
    await page.evaluate(() => window.IOLayoutHost.ctx.tree.expandAll());
    await page.waitForTimeout(1800);

    const keys = Object.keys(ROWS);
    for (const key of keys) await rowShot(page, key, 'idle');
    facts.states.idle = Object.fromEntries(await Promise.all(keys.map(async (key) => [key, await dotStyle(page, ROWS[key])])));
    await page.screenshot({ path: path.join(OUT, `${LABEL}-idle-tree.png`), clip: { x: 0, y: 0, width: 420, height: 620 } });

    const start = (target, action, node) => page.evaluate(async ({ target, action, node, headers }) => {
      const act = (body) => fetch('/api/v1/act', { method: 'POST', headers, body: JSON.stringify(body) }).then(async (response) => ({ status: response.status, body: await response.json() }));
      const dry = await act({ path: target, action, node, dry_run: true });
      return act({ path: target, action, node, token: dry.body.token, detach: true });
    }, { target, action, node, headers: HEADERS });
    const runs = [await start(H.BOARD, 'calibrate', 1), await start(H.CAMERA, 'identify', null)];
    facts.runs = runs.map((run) => run.status);
    await page.waitForTimeout(3500);
    // a pulse caught at its dimmest, so a still shows the breathe: wait for the board's dot to be below 0.55
    for (let i = 0; i < 80; i += 1) { const dot = await dotStyle(page, ROWS.board); if (dot && dot.opacity < 0.55) break; await page.waitForTimeout(100); }
    for (const key of keys) await rowShot(page, key, 'running');
    facts.states.running = Object.fromEntries(await Promise.all(keys.map(async (key) => [key, await dotStyle(page, ROWS[key])])));
    await page.screenshot({ path: path.join(OUT, `${LABEL}-running-tree.png`), clip: { x: 0, y: 0, width: 420, height: 620 } });

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForTimeout(500);
    for (const key of ['adapter', 'board', 'camera']) await rowShot(page, key, 'reduced');
    facts.states.reduced = Object.fromEntries(await Promise.all(['adapter', 'board', 'camera'].map(async (key) => [key, await dotStyle(page, ROWS[key])])));
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.waitForTimeout(500);

    if (CLIP) {
      const boxes = await Promise.all(keys.map(async (key) => (await page.$(`#tree-host [role=treeitem][data-t6-path="${ROWS[key]}"]`)).boundingBox()));
      const top = Math.min(...boxes.map((box) => box.y)) - 4;
      const bottom = Math.max(...boxes.map((box) => box.y + box.height)) + 4;
      const region = { x: 0, y: Math.max(0, top), width: 380, height: Math.min(bottom, 900) - Math.max(0, top) };
      const frames = path.join(OUT, `${LABEL}-frames`);
      fs.rmSync(frames, { recursive: true, force: true });
      fs.mkdirSync(frames, { recursive: true });
      const began = Date.now();
      let count = 0;
      while (Date.now() - began < 7000) { await page.screenshot({ path: path.join(frames, `${String(count).padStart(4, '0')}.png`), clip: region }); count += 1; }
      const fps = count / ((Date.now() - began) / 1000);
      facts.clip = { frames: count, fps: Number(fps.toFixed(2)), region };
      const mp4 = path.join(OUT, `${LABEL}-pulse.mp4`);
      spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-framerate', fps.toFixed(2), '-i', path.join(frames, '%04d.png'), '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
        '-c:v', 'libx264', '-crf', '30', '-preset', 'veryslow', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', mp4]);
      const gif = path.join(OUT, `${LABEL}-pulse.gif`);
      spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-framerate', fps.toFixed(2), '-i', path.join(frames, '%04d.png'), '-vf',
        `scale=iw/2:ih/2:flags=lanczos,fps=8,split[a][b];[a]palettegen=max_colors=16[p];[b][p]paletteuse=dither=none`, '-loop', '0', gif]);
      fs.rmSync(frames, { recursive: true, force: true });
    }
    fs.writeFileSync(path.join(OUT, `${LABEL}-facts.json`), JSON.stringify(facts, null, 1));
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
}

main().then(() => process.exit(0)).catch((error) => { console.error(error); process.exit(1); });
