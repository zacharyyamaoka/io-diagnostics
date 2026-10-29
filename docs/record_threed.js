#!/usr/bin/env node
// The 3D view's media (docs/robot-description.md): 1440x900 screenshots of the five info layers on the fixture robot, light and dark,
// the empty state without a description, the real rack's Cad2Robot bundle (examples/one-dof-test-rack/robot-description.toml) over a
// recorded replay, and a hero video recorded from the REAL page: 3D, hover card, Open, the device page, Back to the same 3D, a tree
// click that highlights and frames a part. Every host here is a fixture (frame F1 with tests/fixtures/robot-description/plugins, or
// frame F3 with the replay's own fixture plugin): nothing touches hardware.
//
//   /usr/bin/node /home/bam/io-diagnostics-lanes/threed-view/docs/record_threed.js [--out /abs/dir]
//
// Writes <out>/*.png, hero.mp4 + hero.gif and media.json (default /home/bam/io-diagnostics/reports/media/threed-view-2026-10-29).
'use strict';

const H = require('../tests/entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const outIndex = process.argv.indexOf('--out');
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : '/home/bam/io-diagnostics/reports/media/threed-view-2026-10-29';
const FFMPEG = '/usr/bin/ffmpeg';
const VIEW = { width: 1440, height: 900 };
const ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'];
const FIXTURE = path.join(H.ROOT, 'tests', 'fixtures', 'robot-description');
const RACK = path.join(H.ROOT, 'examples', 'one-dof-test-rack', 'robot-description.toml');
const ELBOW = 'host/usb1/port-1-3/usb-1-3/can/2';
const WRIST = 'host/usb1/port-1-5/usb-1-5/can/1';
const CAN14 = 'host/usb1/port-1-3/usb-1-3/can/14';
const media = { shots: [], steps: [], timings: {} };
const step = (name, ok, detail) => { media.steps.push({ step: name, ok: Boolean(ok), detail }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  · ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`); };

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

function configWith(root, text) {
  const config = path.join(root, 'config');
  fs.mkdirSync(config, { recursive: true });
  if (text !== null) fs.writeFileSync(path.join(config, 'robot-description.toml'), text);
  return config;
}
const fixtureText = () => fs.readFileSync(path.join(FIXTURE, 'robot-description.toml'), 'utf8').replace('urdf = "robot.urdf"', `urdf = "${path.join(FIXTURE, 'robot.urdf')}"`);
const ready3d = (page) => page.waitForFunction(() => { const d = window.IOThreeD && IOThreeD.debug(); return d && d.scene && d.scene.loaded() && d.states && d.states.length; }, null, { timeout: 90000 });
const project = (page, index) => page.evaluate((i) => IOThreeD.debug().scene.project(i), index);
async function shoot(page, name) { await page.screenshot({ path: path.join(OUT, `${name}.png`) }); media.shots.push(name); }
async function newPage(browser, scheme, variant, extra = {}) {
  const context = await browser.newContext({ viewport: VIEW, colorScheme: scheme, deviceScaleFactor: 1, ...extra });
  await context.addInitScript(([v, mode]) => { try { localStorage.setItem('io-diagnostics.threed.variant', v); localStorage.setItem('io-diagnostics.mode', mode); } catch (_) { /* private */ } }, [variant, scheme]);
  return { context, page: await context.newPage() };
}
async function open3d(page, base, sel) {
  await page.goto(`${base}/?view=threed${sel ? `&sel=${encodeURIComponent(sel)}` : ''}`);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.data, null, { timeout: 30000 });
  await ready3d(page);
  await page.waitForTimeout(900);
}
async function treeClick(page, entity) {
  const key = await page.evaluate((p) => S.built.byEntity.get(p), entity);
  await page.click(`.t6-row[data-t6-path="${key}"] .t6-name`);
}
async function glideTo(page, x, y, steps = 16) { await page.mouse.move(x, y, { steps }); await page.waitForTimeout(150); }

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ARGS });
  const rootFx = fs.mkdtempSync(path.join(os.tmpdir(), 'threed-rec-'));
  configWith(rootFx, fixtureText());
  const levels = path.join(rootFx, 'levels.json');
  fs.writeFileSync(levels, JSON.stringify({ '272813EF/can/2': 'warn', '28B3A25D/can/1': 'error' }));
  const fixture = await H.startServer(rootFx, 'F1', { RD_LEVELS: levels }, { plugins: path.join(FIXTURE, 'plugins') });
  const rootRack = fs.mkdtempSync(path.join(os.tmpdir(), 'threed-rack-'));
  configWith(rootRack, fs.readFileSync(RACK, 'utf8'));
  const rack = await H.startServer(rootRack, 'F3', {}, { plugins: path.join(H.ROOT, 'tests', 'fixtures', 'plugins') });
  try {
    await H.waitFor(async () => { const node = await H.getJson(fixture.base, `/api/v1/node?path=${encodeURIComponent(ELBOW)}`).catch(() => ({})); return node.node && node.node.level === 'warn'; }, 40000);

    // ---- the five info layers, light, then dark for each -------------------------------------------------------------------------
    for (const scheme of ['light', 'dark']) {
      for (const variant of ['v1', 'v2', 'v3', 'v4', 'v5']) {
        const { context, page } = await newPage(browser, scheme, variant);
        await open3d(page, fixture.base, ELBOW);
        if (variant === 'v1') {
          const at = await project(page, 1);
          await glideTo(page, at.x, at.y);
          await page.waitForFunction(() => /comms/.test(document.querySelector('.td-card').textContent), null, { timeout: 8000 }).catch(() => {});
          await page.waitForTimeout(500);
        }
        if (variant === 'v5') { const at = await project(page, 2); await glideTo(page, at.x, at.y); await page.waitForTimeout(400); }
        if (variant === 'v4') await page.waitForFunction(() => document.querySelector('.td-split-slot .d6-tabs'), null, { timeout: 10000 }).catch(() => {});
        await page.waitForTimeout(600);
        await shoot(page, `fixture-${variant}-${scheme}`);
        await context.close();
      }
    }
    step('ten fixture screenshots (five layers, light and dark)', media.shots.length === 10, media.shots.length);

    // ---- the real rack: the Cad2Robot bundle with the example description, over replay F3 --------------------------------------------
    for (const scheme of ['light', 'dark']) {
      const { context, page } = await newPage(browser, scheme, scheme === 'light' ? 'v2' : 'v1');
      const started = Date.now();
      await open3d(page, rack.base, CAN14);
      const timings = await page.evaluate(() => IOThreeD.debug().scene.timings());
      media.timings[`rack-${scheme}`] = { ...timings, wall_ms: Date.now() - started };
      step(`rack loads (${scheme})`, timings.meshes > 200 && timings.firstFrame < 3000, { meshes: timings.meshes, firstFrame: Math.round(timings.firstFrame), all: Math.round(timings.done), triangles: timings.triangles });
      if (scheme === 'light') {
        await page.click('[data-td-labels]');
        await page.waitForTimeout(300);
        await page.evaluate(() => { const d = document.querySelector('.td-unmapped'); if (d) d.open = true; });
      } else {
        const at = await project(page, 1);
        await glideTo(page, at.x, at.y);
        await page.waitForTimeout(700);
      }
      await page.waitForTimeout(500);
      await shoot(page, `rack-${scheme}`);
      if (scheme === 'light') {
        // a close-up on the GIM43 plate, CAN 14's part highlighted (a tree click frames it)
        await page.click('[data-td-labels]');
        await treeClick(page, 'host/usb1/port-1-3/usb-1-3');
        await page.waitForTimeout(300);
        await treeClick(page, CAN14);
        await page.waitForTimeout(1200);
        await shoot(page, 'rack-can14');
      }
      await context.close();
    }

    // ---- the empty state: no description -----------------------------------------------------------------------------------------
    fs.renameSync(path.join(rootFx, 'config', 'robot-description.toml'), path.join(rootFx, 'config', 'off.toml'));
    for (const scheme of ['light', 'dark']) {
      const { context, page } = await newPage(browser, scheme, 'v1');
      await page.goto(`${fixture.base}/?view=threed`);
      await page.waitForFunction(() => { const e = document.querySelector('.td-empty'); return e && !e.hidden && /No robot description/.test(e.textContent); }, null, { timeout: 20000 });
      await page.waitForTimeout(500);
      await shoot(page, `empty-${scheme}`);
      await context.close();
    }
    fs.renameSync(path.join(rootFx, 'config', 'off.toml'), path.join(rootFx, 'config', 'robot-description.toml'));
    step('empty state recorded', true);

    // ---- the hero: 3D, hover card, Open, the device page, Back, a tree click highlights -------------------------------------------
    const videoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'threed-video-'));
    const context = await browser.newContext({ viewport: VIEW, colorScheme: 'light', deviceScaleFactor: 1, recordVideo: { dir: videoDir, size: VIEW } });
    await context.addInitScript(CURSOR_SCRIPT);
    await context.addInitScript(() => { try { localStorage.setItem('io-diagnostics.threed.variant', 'v1'); localStorage.setItem('io-diagnostics.mode', 'light'); } catch (_) { /* private */ } });
    const page = await context.newPage();
    const t0 = Date.now();
    await page.goto(`${fixture.base}/?sel=${encodeURIComponent(WRIST)}`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.data && document.querySelector('.d6-tabs'), null, { timeout: 30000 });
    // warm the 3D module so the clip shows the view, not the loader: open 3D once, go back to the tree
    await page.click('.l1-ribbon [data-l1="threed"]');
    await ready3d(page);
    await page.click('.l1-ribbon [data-l1="tree"]');
    await page.waitForTimeout(400);
    const trim = (Date.now() - t0) / 1000;
    await page.mouse.move(40, 40);
    await page.waitForTimeout(300);
    const ribbon = await page.$('.l1-ribbon [data-l1="threed"]');
    const rb = await ribbon.boundingBox();
    await glideTo(page, rb.x + rb.width / 2, rb.y + rb.height / 2, 12);
    await page.mouse.click(rb.x + rb.width / 2, rb.y + rb.height / 2);
    await page.waitForTimeout(1300);
    const elbow = await project(page, 1);
    await glideTo(page, elbow.x, elbow.y, 22);
    await page.waitForFunction(() => /temperature/.test(document.querySelector('.td-card').textContent), null, { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(1200);
    const open = await page.$('.td-card [data-td-open]');
    const ob = open ? await open.boundingBox() : null;
    step('hero: the hover card shows Open', Boolean(ob));
    if (ob) { await glideTo(page, ob.x + ob.width / 2, ob.y + ob.height / 2, 10); await page.waitForTimeout(300); await page.mouse.click(ob.x + ob.width / 2, ob.y + ob.height / 2); }
    await page.waitForFunction((p) => S.view === 'tree' && S.node && S.node.node.path === p && document.querySelector('.d6-tabs'), ELBOW, { timeout: 10000 }).catch(() => {});
    step('hero: Open went to the device page', await page.evaluate(() => S.view === 'tree'));
    await page.waitForTimeout(1400);
    const back = await page.$('.l1-title button[aria-label="Back"]');
    const bb = await back.boundingBox();
    await glideTo(page, bb.x + bb.width / 2, bb.y + bb.height / 2, 16);
    await page.mouse.click(bb.x + bb.width / 2, bb.y + bb.height / 2);
    await page.waitForTimeout(1300);
    step('hero: Back returned to 3D with the same selection', await page.evaluate((p) => S.view === 'threed' && S.sel === p, ELBOW));
    const key = await page.evaluate((p) => S.built.byEntity.get(p), WRIST);
    const row = page.locator(`.t6-row[data-t6-path="${key}"] .t6-name`);
    await page.evaluate(() => { const pane = document.querySelector('.lx-tree-scroll'); if (pane) pane.scrollTop = 0; }); // clear of the sticky header
    await page.waitForTimeout(200);
    const wb = await row.boundingBox();
    // come in from the viewport side, level with the row: the tree header's level summary opens a popover on hover
    await glideTo(page, 640, wb.y + wb.height / 2, 14);
    await glideTo(page, wb.x + Math.min(40, wb.width / 2), wb.y + wb.height / 2, 12);
    await row.click();
    await page.waitForTimeout(1800);
    step('hero: a tree click highlighted the wrist', await page.evaluate(() => IOThreeD.debug().states[2].selected));
    await page.waitForTimeout(300);
    const video = page.video();
    await context.close();
    const raw = await video.path();
    const mp4 = path.join(OUT, 'hero.mp4');
    const done = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-ss', String(trim), '-i', raw, '-an', '-vf', 'fps=15,scale=1200:-2', '-c:v', 'libx264', '-preset', 'slow', '-crf', '30', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
    step('hero.mp4 encoded', done.status === 0, String(done.stderr || '').slice(0, 200));
    const gif = path.join(OUT, 'hero.gif');
    const palette = spawnSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', mp4, '-vf', 'fps=6,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=none', gif]);
    step('hero.gif fallback encoded', palette.status === 0, String(palette.stderr || '').slice(0, 200));
    const probe = spawnSync('/usr/bin/ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4]);
    const seconds = Number(String(probe.stdout).trim());
    media.hero_s = seconds;
    step('hero: the loop is 10-20 s', seconds >= 10 && seconds <= 20, `${seconds.toFixed(1)} s`);
  } finally {
    await browser.close();
    H.stopServer(fixture.child);
    H.stopServer(rack.child);
  }
  fs.writeFileSync(path.join(OUT, 'media.json'), JSON.stringify(media, null, 2));
  const failed = media.steps.filter((s) => !s.ok).length;
  console.log(`\n${media.steps.length - failed}/${media.steps.length} steps · ${media.shots.length} screenshots · ${OUT}`);
  process.exit(failed ? 1 : 0);
})();
