// The hero video and the stills of the 3D view with the rack's real model and real description, from the running page: a fixture host
// (docs/rack_3d_harness.js) whose boards carry the rack's own CAN ids, so a board can go bad on cue and nothing touches hardware. The
// live page on Preview is shot separately by docs/shoot_rack_preview.js.
//
//   node docs/record_rack_3d.js /home/bam/io-diagnostics/reports/media/rack-3d-2026-11-04
//
// Writes hero.mp4, hero.gif and shot-*.png into that folder.
'use strict';

const { spawnSync } = require('child_process');
const H = require('../tests/entity_harness');
const { rackHost } = require('./rack_3d_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { fs, os, path } = H;

const OUT = process.argv[2];
const SIZE = { width: 1500, height: 900 };
const ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'];
const SECOND_ADAPTER = 'host/usb1/port-1-5/usb-1-5';
const CAN4 = `${SECOND_ADAPTER}/can/4`;

const ready = (page) => page.waitForFunction(() => { const d = window.IOThreeD && IOThreeD.debug(); return d && d.scene && d.scene.loaded() && d.states && d.states.length; }, null, { timeout: 180000 });

// a small robot for the upload, zipped: a URDF of primitives and one STL folder would do, but the real thing reads better
function smallRobotZip(root) {
  const folder = path.join(root, 'fixture_arm');
  fs.cpSync(path.join(H.ROOT, 'tests', 'fixtures', 'robot-description'), folder, { recursive: true, filter: (source) => !source.includes('plugins') && !source.endsWith('.toml') });
  const out = path.join(root, 'fixture_arm.zip');
  const made = spawnSync('python3', ['-c', `import os, sys, zipfile
folder, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
    for base, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(base, name)
            archive.write(path, os.path.relpath(path, os.path.dirname(folder)))
`, folder, out]);
  if (made.status !== 0) throw new Error(String(made.stderr));
  return out;
}
const glide = async (page, x, y, steps = 4) => { await page.mouse.move(x, y, { steps }); };
const HERO_SECONDS = 18;
const seconds = (file) => Number(spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file]).stdout.toString().trim());

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const host = await rackHost();
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ARGS });
  const videos = fs.mkdtempSync(path.join(os.tmpdir(), 'rack-3d-video-'));
  const context = await browser.newContext({ viewport: SIZE, colorScheme: 'light', recordVideo: { dir: videos, size: SIZE } });
  const page = await context.newPage();
  const shot = (name) => page.screenshot({ path: path.join(OUT, `shot-${name}.png`) });
  const started = Date.now();
  const mark = (text) => console.log(`${((Date.now() - started) / 1000).toFixed(1)} s  ${text}`);
  try {
    await page.goto(`${host.base}/?view=threed`);
    await ready(page);
    await page.waitForTimeout(600);
    // the rack, a problem colour on a real part
    fs.writeFileSync(host.levels, JSON.stringify({ '28B3A25D/can/4': 'warn', '272813EF/can/1': 'error', '28B3A25D/can/13': 'unknown' }));
    await page.waitForFunction(() => IOThreeD.debug().states.some((s) => s.level === 'error'), null, { timeout: 30000 });
    await page.waitForTimeout(1500);
    mark('the rack, three boards not OK');
    await shot('rack-status');

    // hover the fdcanusb chip: leader lines to its boards
    const chip = await page.$(`.td-hw-chip[data-td-hw="${SECOND_ADAPTER}"]`);
    const box = await chip.boundingBox();
    await glide(page, box.x + box.width / 2, box.y + box.height / 2);
    await page.waitForTimeout(1800);
    mark('the chip hovered');
    await shot('rack-chip');

    // a part: its card
    const at = await page.evaluate(() => { const scene = IOThreeD.debug().scene; return scene.project(0); });
    await glide(page, at.x, at.y);
    await page.waitForTimeout(1500);
    await shot('rack-card');
    mark('a part hovered');

    // Map devices: take CAN 4 off the left JMC, put it back
    await page.evaluate((p) => { select(p); pushNav(); render(); }, CAN4);
    await page.waitForTimeout(1200);
    await page.evaluate(() => IOThreeD.debug().scene.home());
    await page.waitForTimeout(900);
    await page.click('[data-td-map]');
    await page.waitForTimeout(500);
    const body = await page.evaluate(() => IOThreeD.debug().scene.meshAt('JMC60_Plate_1__JMC60_Aruco_Holder_1__0.stl'));
    await glide(page, body.x, body.y);
    await page.waitForTimeout(1200);
    await shot('map-hover');
    await page.mouse.click(body.x, body.y);
    await page.waitForFunction(() => /taken off/.test(document.querySelector('.td-mapbar').textContent), null, { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await page.mouse.click(body.x, body.y);
    await page.waitForFunction(() => /is on/.test(document.querySelector('.td-mapbar').textContent), null, { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1000);
    await shot('map-placed');
    mark('assigned by click');
    await page.click('[data-td-map-done]');
    await page.waitForTimeout(300);

    // bring another robot in
    await page.click('[data-td-model]');
    await page.waitForTimeout(900);
    await shot('robot-menu');
    await page.setInputFiles('.td-file-zip', smallRobotZip(host.root));
    await page.waitForFunction(() => { const d = IOThreeD.debug(); return d.desc && d.desc.robot === 'fixture_arm' && d.scene && d.scene.loaded(); }, null, { timeout: 60000 });
    await page.waitForTimeout(1600);
    await shot('uploaded');
    mark('a URDF zip brought in');
    await page.click('[data-td-model]');
    await page.waitForTimeout(700);
    await page.click('.td-menu [data-td-use="one-dof-test-rack"]');
    await page.waitForFunction(() => { const d = IOThreeD.debug(); return d.desc && d.desc.robot === 'one_dof_test_rack' && d.scene && d.scene.loaded() && d.states && d.states.length === 10; }, null, { timeout: 120000 });
    await page.waitForTimeout(1500);
    mark('back on the rack');
  } finally {
    const video = await page.video().path();
    await context.close();
    // a still in dark, outside the film
    const dark = await browser.newContext({ viewport: SIZE, colorScheme: 'dark' });
    const darkPage = await dark.newPage();
    await darkPage.goto(`${host.base}/?view=threed`);
    await ready(darkPage);
    await darkPage.evaluate(() => { document.querySelector('#mode-switch [data-mode="dark"]').click(); });
    await darkPage.waitForTimeout(800);
    const hovered = await darkPage.$(`.td-hw-chip[data-td-hw="${SECOND_ADAPTER}"]`);
    if (hovered) await hovered.hover();
    await darkPage.waitForTimeout(1200);
    await darkPage.screenshot({ path: path.join(OUT, 'shot-dark.png') });
    await dark.close();
    await browser.close();
    H.stopServer(host.server.child);
    const mp4 = path.join(OUT, 'hero.mp4');
    // WHY sped up: software WebGL draws the rack's 1.4 M triangles at a few frames a second; a person on a GPU sees it at full speed
    const speed = (HERO_SECONDS / seconds(video)).toFixed(4);
    const encode = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', video, '-an', '-vf', `setpts=${speed}*PTS,fps=15,scale=1100:-2`, '-c:v', 'libx264', '-preset', 'slow', '-crf', '30', '-pix_fmt', 'yuv420p', '-g', '30', '-movflags', '+faststart', mp4]);
    if (encode.status !== 0) console.error(String(encode.stderr));
    const gif = path.join(OUT, 'hero.gif');
    const palette = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', mp4, '-vf', 'fps=4,scale=560:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=48[p];[b][p]paletteuse=dither=bayer:bayer_scale=4', gif]);
    if (palette.status !== 0) console.error(String(palette.stderr));
    console.log('hero', speed, fs.statSync(mp4).size, fs.statSync(gif).size);
    fs.rmSync(videos, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exit(1); });
