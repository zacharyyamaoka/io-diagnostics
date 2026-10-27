#!/usr/bin/env node
// Screenshots and the hero video of the RobotDx page on data-ui (io_diagnostics/web/dx/, docs/peps/0001-robotdx-on-data-ui.md §12),
// beside today's page on the same fixture host, light and dark. No hardware: tests/entity_harness.js starts
// `cli.py serve --fixture F1 --plugins <copy of tests/fixtures/plugins-state> --port 0 --no-udev` and stops it by its process group.
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_dx.js [out dir]       (default reports/media/dx, git-ignored)
//
// before-*.png  today's page (tree; the entity page's State tab)      after-*.png  the new page (Explore, a dashboard, paused at t,
// Activity, Dashboards, the rail's three styles, Add panel)              dx-rewind.webm (+ .mp4, .gif)  streaming → scrub back → LIVE
'use strict';

const H = require('../tests/entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { execFileSync } = require('child_process');
const { BOARD, fs, path } = H;

const OUT = path.resolve(process.argv[2] || path.join(H.ROOT, 'reports', 'media', 'dx'));
const DX = '/dx/index.html';
const sel = encodeURIComponent(BOARD);

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
  console.log(`  ${name}.png`);
}
const settle = (page, ms = 1200) => page.waitForTimeout(ms);
const ready = (page) => page.waitForFunction(() => window.robotdx && window.robotdx.clock && document.querySelector('#dx-main'), null, { timeout: 30000 });

async function scrubTo(page, fraction, steps = 6, stepMs = 0) {
  const track = await page.evaluate(() => { const r = document.querySelector('#dx-time .dv-pb-track').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  await page.mouse.move(track.x + track.w - 1, track.y + track.h / 2);
  await page.mouse.down();
  for (let i = 1; i <= steps; i += 1) {
    await page.mouse.move(track.x + track.w - 1 - (i / steps) * (1 - fraction) * track.w, track.y + track.h / 2);
    if (stepMs) await page.waitForTimeout(stepMs);
  }
  await page.mouse.up();
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const main = await H.fixtureHost('F1');
  const { base } = main;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    for (const scheme of ['light', 'dark']) {
      console.log(`${scheme}:`);
      // ---- before: today's page on the same host ----------------------------------------------------------------------------
      const old = await H.newContext(browser, scheme);
      const oldPage = await old.newPage();
      await oldPage.goto(`${base}/`);
      await settle(oldPage, 3000);
      await shot(oldPage, `before-tree-${scheme}`);
      await oldPage.goto(`${base}/?sel=${sel}&page=state`);
      await settle(oldPage, 5000);
      await shot(oldPage, `before-state-${scheme}`);
      await old.close();
      // ---- after: the new page ---------------------------------------------------------------------------------------------------
      const ctx = await H.newContext(browser, scheme);
      const page = await ctx.newPage();
      await page.goto(`${base}${DX}?go=explore&sel=${sel}`);
      await ready(page);
      await settle(page, 6000);
      await shot(page, `after-explore-${scheme}`);
      await scrubTo(page, 0.75);
      await settle(page, 2500);
      await shot(page, `after-paused-${scheme}`);
      await page.click('#dx-time .dv-pb-live');
      await page.goto(`${base}${DX}?go=dashboards&board=joint`);
      await ready(page);
      await settle(page, 6000);
      await shot(page, `after-dashboard-${scheme}`);
      if (scheme === 'light') {
        await page.goto(`${base}${DX}?go=explore`);
        await ready(page);
        await settle(page, 2500);
        await shot(page, 'after-explore-overview-light');
        await page.goto(`${base}${DX}?go=activity`);
        await ready(page);
        await settle(page, 2500);
        await shot(page, 'after-activity-light');
        await page.goto(`${base}${DX}?go=dashboards`);
        await ready(page);
        await settle(page, 1500);
        await shot(page, 'after-dashboards-light');
        await page.click('[data-board-act="new"]');
        await page.waitForFunction(() => new URLSearchParams(location.search).get('board'), null, { timeout: 8000 });
        await settle(page, 800);
        await page.click('[data-board-act="add"]');
        await page.waitForSelector('#dx-add-panel[open]');
        await page.selectOption('#dx-add-panel [name="entity"]', BOARD);
        await settle(page, 1200);
        await page.selectOption('#dx-add-panel [name="field"]', 'position').catch(() => {});
        await shot(page, 'after-add-panel-light');
        await page.click('#dx-add-panel [data-add-panel="ok"]');
        await settle(page, 3000);
        await shot(page, 'after-my-dashboard-light');
        // the rail's three styles, on Explore
        await page.goto(`${base}${DX}?go=settings`);
        await ready(page);
        for (const mode of ['collapsed', 'expanded', 'wide']) {
          await page.selectOption('select[data-setting="rail"]', mode);
          await page.goto(`${base}${DX}?go=explore&sel=${sel}`);
          await ready(page);
          await settle(page, 3000);
          await shot(page, `after-rail-${mode}-light`);
          await page.goto(`${base}${DX}?go=settings`);
          await ready(page);
        }
        await page.selectOption('select[data-setting="rail"]', 'expanded');
      }
      await ctx.close();
    }

    // ---- the hero video: streaming, scrub back while data keeps arriving, back to live -------------------------------------------
    console.log('video:');
    const videoDir = path.join(OUT, 'video-raw');
    fs.rmSync(videoDir, { recursive: true, force: true });
    const vctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: 'light', recordVideo: { dir: videoDir, size: { width: 1400, height: 900 } } });
    const vpage = await vctx.newPage();
    await vpage.goto(`${base}${DX}?go=explore&sel=${sel}`);
    await ready(vpage);
    await settle(vpage, 5000);                     // streaming, following live
    await scrubTo(vpage, 0.55, 30, 60);            // a slow scrub back (~2 s)
    await settle(vpage, 4500);                     // paused at t: the views hold, LIVE says how far behind, data keeps arriving
    const live = await vpage.evaluate(() => { const r = document.querySelector('#dx-time .dv-pb-live').getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
    await vpage.mouse.move(live.x, live.y, { steps: 10 });
    await settle(vpage, 400);
    await vpage.mouse.click(live.x, live.y);       // one click: back to live
    await settle(vpage, 4000);
    const video = vpage.video();
    await vctx.close();
    const raw = await video.path();
    const webm = path.join(OUT, 'dx-rewind.webm');
    fs.renameSync(raw, webm);
    fs.rmSync(videoDir, { recursive: true, force: true });
    console.log(`  dx-rewind.webm (${(fs.statSync(webm).size / 1024).toFixed(0)} KB)`);
    try {
      // skip the blank first frames (the page loading), keep the rest; H.264 for players that take no webm, a GIF fallback
      execFileSync('/usr/bin/ffmpeg', ['-y', '-loglevel', 'error', '-ss', '2', '-i', webm, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '28', '-preset', 'veryfast', '-an', path.join(OUT, 'dx-rewind.mp4')]);
      execFileSync('/usr/bin/ffmpeg', ['-y', '-loglevel', 'error', '-ss', '2', '-i', webm, '-vf', 'fps=8,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer', path.join(OUT, 'dx-rewind.gif')]);
      console.log('  dx-rewind.mp4, dx-rewind.gif');
    } catch (error) { console.log(`  ffmpeg: ${error.message}`); }
  } finally {
    await browser.close().catch(() => {});
    H.stopServer(main.child);
  }
})();
