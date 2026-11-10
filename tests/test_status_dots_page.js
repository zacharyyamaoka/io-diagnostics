#!/usr/bin/env node
// The entity tree's status dots, in the REAL page on a fixture host, headless Chrome (Zach, 2026-11-10: hollow ring = stale, solid dot =
// connected and live, pulsing = doing something now; a merely-connected idle entity must not animate):
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_status_dots_page.js     (a fixture host on a scratch port; no hardware)
//
// The host is the entity page's fixture (tests/entity_harness.js: `cli.py serve --fixture F1 --plugins tests/fixtures/plugins-state`): a
// joint-like adapter with CAN 1, a second adapter's board, and a RealSense-like camera, all from stdlib stand-ins that never touch
// hardware. Its utilities are REAL runs of the stand-in process, which sleeps STATE_ACT_S and then refuses to do anything ("the state
// fixture only dry-runs that"). Measured on computed style and the browser's own animation list, never on a class name:
//   idle      adapter, board and camera solid, the dot's animation is `none`, no animation object on it
//   stale     a board the host says is not heard (the /state patched for that one row) is a hollow ring, not animated
//   running   Run calibration on CAN 1 and Identify on the camera at once: CAN 1, its adapter and the camera pulse (a slow opacity
//             animation, never a scale); the other adapter, the stale board and the rows above the adapter do not
//   reduced   prefers-reduced-motion: no animation at all, a steady ring around the dot instead
//   ended     the runs end and every row is quiet again
// Screenshots: reports/media/status-dots/*.png (rows, native resolution). Exits 1 on a failed check.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check } = H;

const RUN_S = 14; // seconds a fixture utility takes
const SHOTS = H.path.join(H.ROOT, 'reports', 'media', 'status-dots');
const PAGE_HEADERS = { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' };
const STALE = { state: 'stale', age_s: 2463, restored: false, text: 'last heard 41 min ago' };
const LIVE = { state: 'live', age_s: 0.4, restored: false, text: 'answers now' };

let patchStale = null; // the board path to say is stale, while set
let patchLive = null;  // the adapter path to say is live (a follower streaming), while set
function patch(value) {
  if (Array.isArray(value)) value.forEach(patch);
  else if (value && typeof value === 'object') {
    if (patchStale && value.path === patchStale && 'presence' in value) value.presence = STALE;
    if (patchLive && value.path === patchLive && 'presence' in value) value.presence = LIVE;
    Object.values(value).forEach(patch);
  }
  return value;
}

const dotOf = (page, entity) => page.evaluate((target) => {
  const row = document.querySelector(`#tree-host [role=treeitem][data-t6-path="${target}"]`);
  const dot = row && row.querySelector('.t6-pres');
  if (!dot) return null;
  const style = getComputedStyle(dot);
  return { classes: [...dot.classList].join(' '), animationName: style.animationName, animationDuration: style.animationDuration,
    animations: dot.getAnimations().length, background: style.backgroundColor, boxShadow: style.boxShadow, opacity: Number(style.opacity), title: dot.title,
    box: dot.getBoundingClientRect().toJSON() };
}, entity);
const shape = (dot) => (!dot ? 'missing' : /t6-pres-solid/.test(dot.classes) ? 'solid' : /t6-pres-hollow/.test(dot.classes) ? 'hollow' : 'none');
const still = (dot) => Boolean(dot) && dot.animationName === 'none' && dot.animations === 0;
const pulsing = (dot) => Boolean(dot) && dot.animationName !== 'none' && dot.animations === 1;
const seconds = (text) => parseFloat(text) * (/ms$/.test(text) ? 0.001 : 1);
const shotRow = async (page, entity, name) => {
  H.fs.mkdirSync(SHOTS, { recursive: true });
  const element = await page.$(`#tree-host [role=treeitem][data-t6-path="${entity}"]`);
  await element.screenshot({ path: H.path.join(SHOTS, name) });
};

async function main() {
  const server = await H.fixtureHost('F1', { STATE_ACT_S: String(RUN_S) });
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const { BOARD, BOARD2, CAMERA, ADAPTER } = H;
    const ADAPTER2 = BOARD2.split('/can/')[0];
    const ABOVE = ADAPTER.split('/').slice(0, -1); // the host, the USB bus, the port: never pulse for what a board does

    const context = await H.newContext(browser);
    const page = await context.newPage();
    const errors = H.watchErrors(page, server.base);
    // WHY the feed held, unanswered: a page with a feed goes by its snapshot; the patched /state reaches it on its polling fallback
    await context.route('**/api/v1/live', () => {});
    await context.route(/\/api\/v1\/(state|node)/, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: patch(await response.json()) });
    });
    await page.goto(`${server.base}/`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('#tree-host [role=treeitem]'), null, { timeout: 30000 });
    await page.evaluate(() => window.IOLayoutHost && window.IOLayoutHost.ctx && window.IOLayoutHost.ctx.tree.expandAll());
    const settle = () => page.waitForTimeout(1500); // one poll of /state and a render
    const dots = async (...paths) => Object.fromEntries(await Promise.all(paths.map(async (path) => [path, await dotOf(page, path)])));

    // ---- idle: connected rows are solid, and nothing moves
    await settle();
    let now = await dots(ADAPTER, BOARD, CAMERA, ADAPTER2);
    check('idle: the adapter, its board, the camera and the other adapter are solid dots', [ADAPTER, BOARD, CAMERA, ADAPTER2].every((path) => shape(now[path]) === 'solid'),
      Object.fromEntries(Object.entries(now).map(([path, dot]) => [path.split('/').pop(), dot && dot.classes])));
    check('idle: none is animated (computed animation-name none, no animation object)', Object.values(now).every(still), Object.values(now).map((dot) => dot && dot.animationName));
    check('idle: solid is filled and neutral, not transparent', now[BOARD].background !== 'rgba(0, 0, 0, 0)' && !/245, 158|179, 38/.test(now[BOARD].background), now[BOARD].background);

    patchLive = ADAPTER; // the host says a follower is streaming from the adapter: it is alive, not busy
    await settle();
    now = await dots(ADAPTER);
    check('alive is not active: an adapter whose presence is "live" is solid and still', shape(now[ADAPTER]) === 'solid' && still(now[ADAPTER]), now[ADAPTER]);
    patchLive = null;

    // ---- stale: hollow
    patchStale = BOARD2;
    await settle();
    now = await dots(BOARD2);
    check('stale: the board is a hollow ring (transparent fill, a ring)', shape(now[BOARD2]) === 'hollow' && now[BOARD2].background === 'rgba(0, 0, 0, 0)' && now[BOARD2].boxShadow !== 'none', now[BOARD2]);
    check('stale: it is not animated', still(now[BOARD2]), now[BOARD2].animationName);
    check('stale: the ring says when, in the host\'s words', now[BOARD2].title === 'last heard 41 min ago', now[BOARD2].title);
    await shotRow(page, BOARD2, 'row-stale.png');
    await shotRow(page, BOARD, 'row-solid.png');
    await shotRow(page, ADAPTER, 'row-adapter-solid.png');
    await shotRow(page, CAMERA, 'row-camera-solid.png');

    // ---- running: two real runs of the fixture utility, on a board and on a camera
    const start = (path, action, node) => page.evaluate(async ({ path, action, node, headers }) => {
      const act = (body) => fetch('/api/v1/act', { method: 'POST', headers, body: JSON.stringify(body) }).then(async (response) => ({ status: response.status, body: await response.json() }));
      const dry = await act({ path, action, node, dry_run: true });
      return act({ path, action, node, token: dry.body.token, detach: true });
    }, { path, action, node, headers: PAGE_HEADERS });
    const calibrate = await start(BOARD, 'calibrate', 1);
    const identify = await start(CAMERA, 'identify', null);
    check('two real runs of the fixture utility started', calibrate.status === 202 && identify.status === 202, { calibrate: calibrate.status, identify: identify.status });
    const going = await H.waitFor(async () => { const d = await dots(ADAPTER, BOARD, CAMERA); return Object.values(d).every(pulsing) ? d : false; }, 8000);
    check('running: the board, the adapter it runs on and the camera pulse (an animation is running on each)', Boolean(going), going || Object.fromEntries(Object.entries(await dots(ADAPTER, BOARD, CAMERA)).map(([path, dot]) => [path.split('/').pop(), dot && [dot.classes, dot.animationName, dot.animations]])));
    if (going) {
      check('running: still solid underneath (connected), and the animation is slow', Object.values(going).every((dot) => shape(dot) === 'solid' && seconds(dot.animationDuration) >= 2),
        Object.values(going).map((dot) => dot.animationDuration));
      const keyframes = await page.evaluate((target) => {
        const dot = document.querySelector(`#tree-host [role=treeitem][data-t6-path="${target}"] .t6-pres`);
        return dot.getAnimations()[0].effect.getKeyframes().map((frame) => ({ opacity: frame.opacity, transform: frame.transform }));
      }, BOARD);
      check('running: a quiet breathe: opacity only, no scale, never fully out', keyframes.length >= 2 && keyframes.every((frame) => !frame.transform || frame.transform === 'none')
        && keyframes.every((frame) => frame.opacity === undefined || Number(frame.opacity) >= 0.3), keyframes);
      const samples = [];
      for (let i = 0; i < 12; i += 1) { samples.push((await dotOf(page, BOARD)).opacity); await page.waitForTimeout(250); }
      check('running: the opacity really moves over three seconds', Math.max(...samples) - Math.min(...samples) > 0.15, samples.map((value) => value.toFixed(2)).join(' '));
      check('running: the tooltip says what is happening', /Run calibration/.test(going[BOARD].title) && /Identify/.test(going[CAMERA].title), [going[BOARD].title, going[CAMERA].title]);
    }
    const others = await dots(ADAPTER2, BOARD2, ...ABOVE.map((_, at) => ABOVE.slice(0, at + 1).join('/')));
    check('running: the other adapter, the stale board and the host, USB bus and port above do not pulse', Object.values(others).every((dot) => !dot || still(dot)),
      Object.entries(others).map(([path, dot]) => [path.split('/').pop(), dot && dot.animationName]));
    check('running: the stale board is still a hollow ring', shape(others[BOARD2]) === 'hollow', others[BOARD2] && others[BOARD2].classes);
    await page.waitForTimeout(700);
    await shotRow(page, BOARD, 'row-pulsing.png');
    await shotRow(page, ADAPTER, 'row-adapter-pulsing.png');
    await shotRow(page, CAMERA, 'row-camera-pulsing.png');

    // ---- reduced motion: no animation, a steady ring around the dot
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForTimeout(400);
    const reduced = await dots(ADAPTER, BOARD, CAMERA);
    check('reduced motion: nothing animates (computed animation-name none)', Object.values(reduced).every(still), Object.values(reduced).map((dot) => dot.animationName));
    check('reduced motion: a steady ring (an outer shadow) around the dot says it is busy', Object.values(reduced).every((dot) => dot.boxShadow !== 'none' && !/inset/.test(dot.boxShadow)),
      Object.values(reduced).map((dot) => dot.boxShadow));
    const idleShadow = await page.evaluate((target) => getComputedStyle(document.querySelector(`#tree-host [role=treeitem][data-t6-path="${target}"] .t6-pres`)).boxShadow, ADAPTER2);
    check('reduced motion: and an idle solid dot has no such ring', idleShadow === 'none', idleShadow);
    await shotRow(page, BOARD, 'row-pulsing-reduced.png');
    await page.emulateMedia({ reducedMotion: 'no-preference' });

    // ---- the runs end: quiet again
    const calm = await H.waitFor(async () => { const d = await dots(ADAPTER, BOARD, CAMERA); return Object.values(d).every(still) ? d : false; }, (RUN_S + 6) * 1000);
    check('ended: every row is quiet again, solid and still', Boolean(calm) && Object.values(calm).every((dot) => shape(dot) === 'solid'), calm);
    check('the page logged no errors', errors.errors.length === 0, errors.errors);
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
}

main().catch((error) => { console.error(error); process.exit(1); });
