// The 3D view (web/threed/, docs/robot-description.md), measured in headless Chrome on a fixture host: frame F1 with
// tests/fixtures/robot-description/plugins (stdlib, made-up boards whose levels the test sets through $RD_LEVELS) and the fixture
// description (tests/fixtures/robot-description). Never hardware. Positions are getBoundingClientRect, colours are pixels read back
// from the canvas, never DOM presence alone.
//
//   node tests/test_threed_view.js
//
// Zach, 2026-10-29: a "3D" item in the activity bar; the tree stays; clicking a tree row highlights the part, clicking a part selects
// the row; hovering shows a card with Open; Back returns to the same selection and camera; one bad board colours its part; a device
// that is not detected is a ghost; five info layers behind a bottom-right picker; every boundary drags and is remembered; and without a
// description RobotDx works exactly as before.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, fs, os, path } = H;

const FIXTURE = path.join(H.ROOT, 'tests', 'fixtures', 'robot-description');
const ADAPTER = 'host/usb1/port-1-3/usb-1-3';
const SHOULDER = `${ADAPTER}/can/1`;
const ELBOW = `${ADAPTER}/can/2`;
const WRIST = 'host/usb1/port-1-5/usb-1-5/can/1';
const PART = { shoulder: 0, elbow: 1, wrist: 2, tool: 3, camera: 4 };
const VIEW = { width: 1440, height: 900 };
const ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']; // WebGL in headless Chrome (software)

function setup() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'threed-view-'));
  const config = path.join(root, 'config');
  fs.mkdirSync(config, { recursive: true });
  const text = fs.readFileSync(path.join(FIXTURE, 'robot-description.toml'), 'utf8').replace('urdf = "robot.urdf"', `urdf = "${path.join(FIXTURE, 'robot.urdf')}"`);
  fs.writeFileSync(path.join(config, 'robot-description.toml'), text);
  const levels = path.join(root, 'levels.json');
  fs.writeFileSync(levels, '{}');
  return { root, config, levels };
}

const ready3d = (page) => page.waitForFunction(() => { const d = window.IOThreeD && IOThreeD.debug(); return d && d.scene && d.scene.loaded() && d.states && d.states.length; }, null, { timeout: 60000 });
const debug = (page) => page.evaluate(() => { const d = IOThreeD.debug(); return { variant: d.variant, pinned: d.pinned, states: (d.states || []).map((s) => ({ level: s.level, ghost: s.ghost, selected: s.selected })), cam: d.scene ? d.scene.cameraText() : null }; });
const project = (page, index) => page.evaluate((i) => IOThreeD.debug().scene.project(i), index);
const pixel = (page, x, y) => page.evaluate(([px, py]) => IOThreeD.debug().scene.pixel(px, py), [x, y]);
const rectOf = (page, selector) => page.evaluate((s) => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom, visible: r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden' }; }, selector);
const blueish = ([r, g, b]) => b - r;
const reddish = ([r, g, b]) => r - Math.max(g, b);
async function setVariant(page, id) {
  await page.selectOption('#threed-variant', id);
  await page.waitForFunction((v) => IOThreeD.debug().variant === v && document.querySelector('.td-root').dataset.variant === v, id);
  await page.waitForTimeout(300);
}
async function openThreed(page, base, query = '') {
  await page.goto(`${base}/${query}`);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.data && document.querySelector('.l1-ribbon [data-l1="threed"]'), null, { timeout: 30000 });
  await page.click('.l1-ribbon [data-l1="threed"]');
  await ready3d(page);
  await page.waitForTimeout(400);
}
async function treeRow(page, entity) {
  const key = await page.evaluate((p) => S.built.byEntity.get(p), entity);
  return `.t6-row[data-t6-path="${key}"]`;
}

(async () => {
  const env = setup();
  const server = await H.startServer(env.root, 'F1', { RD_LEVELS: env.levels }, { plugins: path.join(FIXTURE, 'plugins') });
  const base = server.base;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ARGS });
  let failed = false;
  try {
    // the boards are up
    await H.waitFor(async () => { const node = await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(ELBOW)}`).catch(() => ({})); return node.node && node.node.level === 'ok'; }, 30000);

    // ---------------------------------------------------------------------------------- 1. the 3D item ----
    const context = await browser.newContext({ viewport: VIEW, colorScheme: 'light' });
    const page = await context.newPage();
    const errors = H.watchErrors(page, base);
    const vendor = [];
    page.on('request', (request) => { if (/\/vendor\/(three|urdf-loader)\//.test(request.url())) vendor.push(request.url()); });
    await page.goto(`${base}/`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.data && document.querySelector('.d6-tabs'), null, { timeout: 30000 });
    await page.waitForTimeout(800);
    check('the Tree view loads no 3D library (three.js loads only for the 3D view)', vendor.length === 0, `${vendor.length} requests`);
    const item = await rectOf(page, '.l1-ribbon [data-l1="threed"]');
    const label = await page.$eval('.l1-ribbon [data-l1="threed"]', (el) => `${el.title} | ${el.textContent.trim()}`);
    check('the activity bar has a 3D item (icon and label)', Boolean(item && item.visible) && /3D/.test(label) && Boolean(await page.$('.l1-ribbon [data-l1="threed"] svg')), label);
    const started = Date.now();
    await page.click('.l1-ribbon [data-l1="threed"]');
    await ready3d(page);
    const timings = await page.evaluate(() => IOThreeD.debug().scene.timings());
    const viewport = await rectOf(page, '.td-view');
    const tree = await rectOf(page, '#tree-host');
    const canvas = await rectOf(page, '.td-canvas');
    check('3D opens a viewport where the device page sits, the tree kept on the left', viewport && viewport.width > 700 && viewport.height > 600 && tree && tree.visible && tree.right <= viewport.x + 2
      && canvas && Math.abs(canvas.width - viewport.width) < 2, { viewport, tree: tree && { right: tree.right }, ms: Date.now() - started });
    check('the URL says view=threed', await page.evaluate(() => new URLSearchParams(location.search).get('view') === 'threed'));
    check('the ribbon marks 3D pressed', await page.$eval('.l1-ribbon [data-l1="threed"]', (el) => el.getAttribute('aria-pressed') === 'true'));
    check('the model draws its geometry (meshes, a first frame)', timings.meshes >= 1 && timings.firstFrame > 0, timings);
    // a mapped part is on screen in the colour of its level (OK: the grey tinted to the ok token), the background is the skin's
    const shoulderAt = await project(page, PART.shoulder);
    const shoulderPx = await pixel(page, shoulderAt.x, shoulderAt.y);
    const corner = await pixel(page, viewport.x + viewport.width - 40, viewport.y + viewport.height - 30);
    check('a mapped OK part is drawn (not the background) and tinted, not plain grey', shoulderAt.inside && Math.abs(shoulderPx[0] - corner[0]) + Math.abs(shoulderPx[1] - corner[1]) > 30 && shoulderPx[1] > shoulderPx[0], { shoulderPx, corner });

    // ---------------------------------------------------------------- 2. tree click highlights and frames ----
    const camBefore = (await debug(page)).cam;
    const beforePx = await pixel(page, (await project(page, PART.elbow)).x, (await project(page, PART.elbow)).y);
    await page.click(`${await treeRow(page, ELBOW)} .t6-name`);
    await page.waitForFunction((p) => S.sel === p && IOThreeD.debug().states[1].selected, ELBOW);
    await page.waitForTimeout(900); // the fly-to
    const afterTree = await debug(page);
    const elbowAt = await project(page, PART.elbow);
    const elbowPx = await pixel(page, elbowAt.x, elbowAt.y);
    check('a tree click highlights the mapped part (blue tint, measured)', afterTree.states[PART.elbow].selected && blueish(elbowPx) > blueish(beforePx) + 15, { before: beforePx, after: elbowPx });
    check('... and frames it (the camera moved; the part is near the centre)', afterTree.cam !== camBefore && Math.abs(elbowAt.x - (viewport.x + viewport.width / 2)) < viewport.width * 0.2, { cam: afterTree.cam, elbowAt });
    check('only the selected part is selected', afterTree.states.filter((s) => s.selected).length === 1);
    // a tree row above the mapped boards (their adapter) highlights every part below it
    await page.click(`${await treeRow(page, ADAPTER)} .t6-name`);
    await page.waitForFunction(() => IOThreeD.debug().states.filter((s) => s.selected).length === 2);
    check('selecting the adapter highlights both of its boards\' parts (shoulder, elbow), nothing else', (await debug(page)).states.map((s) => s.selected).join() === 'true,true,false,false,false');

    // ------------------------------------------------------------------- 3. a scene click selects the row ----
    await page.mouse.move(viewport.x + 30, viewport.y + viewport.height / 2);
    const wristAt = await project(page, PART.wrist);
    await page.mouse.click(wristAt.x, wristAt.y);
    await page.waitForFunction((p) => S.sel === p, WRIST, { timeout: 8000 }).catch(() => {});
    const selRow = await page.evaluate(() => { const row = document.querySelector('#tree-host .t6-row[aria-selected="true"]'); return row ? { key: row.dataset.t6Path, text: row.textContent } : null; });
    const wantKey = await page.evaluate((p) => S.built.byEntity.get(p), WRIST);
    check('clicking a part (its projected centre) selects its entity in the tree', (await page.evaluate(() => S.sel)) === WRIST && selRow && selRow.key === wantKey, selRow);
    const rowRect = await rectOf(page, '#tree-host .t6-row[aria-selected="true"]');
    check('... and the selected row is on screen in the tree pane', rowRect && rowRect.visible && rowRect.x < viewport.x && rowRect.y > 0 && rowRect.bottom < VIEW.height, rowRect);
    // a click on the background unpins / does not select
    await page.mouse.click(viewport.x + viewport.width - 60, viewport.y + viewport.height - 120);
    await page.waitForTimeout(250);
    check('a click on empty space changes no selection', (await page.evaluate(() => S.sel)) === WRIST);

    // --------------------------------------------- 4. hover card, Open, the device page, Back to the same 3D ----
    await page.mouse.move(viewport.x + 20, viewport.y + 200);
    await page.waitForTimeout(200);
    const elbowNow = await project(page, PART.elbow);
    await page.mouse.move(elbowNow.x, elbowNow.y, { steps: 4 });
    await page.waitForFunction(() => { const card = document.querySelector('.td-card'); return card && !card.hidden && card.dataset.part === '1'; }, null, { timeout: 8000 }).catch(() => {});
    const card = await rectOf(page, '.td-card');
    const cardText = await page.$eval('.td-card', (el) => el.textContent);
    check('hovering a part shows a small card in the viewport (name, level, Open)', card && card.visible && card.x >= viewport.x && card.right <= viewport.right + 1 && card.y >= viewport.y
      && /CAN 2/.test(cardText) && /ok/i.test(cardText) && Boolean(await page.$('.td-card [data-td-open]')), { card, cardText: cardText.slice(0, 120) });
    check('the card sits by the cursor (within 40 px)', card && Math.abs(card.x - elbowNow.x - 14) < 40, { card: card && card.x, cursor: elbowNow.x });
    await page.waitForFunction(() => /comms/.test(document.querySelector('.td-card').textContent) && /temperature/.test(document.querySelector('.td-card').textContent), null, { timeout: 6000 }).catch(() => {});
    const rich = await page.$eval('.td-card', (el) => el.textContent);
    check('v1\'s card carries the worst checks and key State values', /comms/.test(rich) && /temperature/.test(rich) && /°C/.test(rich), rich.slice(0, 200));
    const camOpen = (await debug(page)).cam;
    const openBtn = await rectOf(page, '.td-card [data-td-open]');
    await page.mouse.move(openBtn.x + openBtn.width / 2, openBtn.y + openBtn.height / 2);
    await page.mouse.click(openBtn.x + openBtn.width / 2, openBtn.y + openBtn.height / 2);
    await page.waitForFunction((p) => S.view === 'tree' && S.sel === p && S.node && S.node.node.path === p && document.querySelector('.d6-tabs'), ELBOW, { timeout: 15000 }).catch(() => {});
    const page2 = await rectOf(page, '#detail .d6-tabs');
    check('Open goes to that entity\'s normal device page (its tabs on screen)', (await page.evaluate(() => S.view)) === 'tree' && page2 && page2.visible && page2.x >= viewport.x - 2, page2);
    check('... and the 3D viewport is gone', !(await rectOf(page, '.td-view')).visible);
    await page.click('.l1-title button[aria-label="Back"]');
    await page.waitForFunction(() => S.view === 'threed', null, { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(500);
    const back = await debug(page);
    check('Back (the breadcrumb\'s <) returns to the 3D view', (await page.evaluate(() => S.view)) === 'threed' && (await rectOf(page, '.td-view')).visible);
    check('... with the same selection', (await page.evaluate(() => S.sel)) === ELBOW && back.states[PART.elbow].selected, await page.evaluate(() => S.sel));
    check('... and the same camera', back.cam === camOpen, { before: camOpen, after: back.cam });
    await page.click('.l1-title button[aria-label="Forward"]');
    await page.waitForFunction(() => S.view === 'tree', null, { timeout: 8000 }).catch(() => {});
    check('Forward goes to the device page again', (await page.evaluate(() => S.view)) === 'tree');
    await page.goBack();
    await page.waitForFunction(() => S.view === 'threed', null, { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(400);
    check('the browser\'s own Back (the mouse\'s back button) does the same', (await debug(page)).cam === camOpen && (await page.evaluate(() => S.sel)) === ELBOW);
    // a pinned card (v1: a click pins it)
    const shoulderNow = await project(page, PART.shoulder);
    await page.mouse.click(shoulderNow.x, shoulderNow.y);
    await page.waitForTimeout(300);
    await page.mouse.move(viewport.x + 30, viewport.y + 60);
    await page.waitForTimeout(500);
    const pinned = await page.evaluate(() => ({ pinned: IOThreeD.debug().pinned, hidden: document.querySelector('.td-card').hidden, part: document.querySelector('.td-card').dataset.part }));
    check('v1: a click pins the card (it stays when the pointer leaves)', pinned.pinned === PART.shoulder && !pinned.hidden && pinned.part === '0', pinned);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    check('Escape unpins it', (await debug(page)).pinned === -1);

    // ------------------------------------------------------------------- 5. a level change colours its part ----
    await page.click(`${await treeRow(page, WRIST)} .t6-name`); // select something else so the shoulder carries no selection tint
    await page.waitForTimeout(700);
    await page.evaluate(() => IOThreeD.debug().scene.home()); // the whole model in view
    await page.waitForTimeout(700);
    const shoulderAt2 = await project(page, PART.shoulder);
    const okPx = await pixel(page, shoulderAt2.x, shoulderAt2.y);
    fs.writeFileSync(env.levels, JSON.stringify({ '272813EF/can/1': 'error' }));
    await page.waitForFunction(() => IOThreeD.debug().states[0].level === 'error', null, { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(400);
    const errPx = await pixel(page, shoulderAt2.x, shoulderAt2.y);
    const others = (await debug(page)).states;
    check('a board turning error turns its part red (pixel read back)', others[PART.shoulder].level === 'error' && reddish(errPx) > 60 && reddish(okPx) < 20, { ok: okPx, error: errPx });
    check('... only its own part: no roll-up onto the other parts', others[PART.elbow].level === 'ok' && others[PART.wrist].level === 'ok', others.map((s) => s.level));
    const live = await page.evaluate(() => IOThreeD.debug().source && IOThreeD.debug().live > 0);
    check('colours come from the live feed (/api/v1/live open while 3D shows)', live);
    fs.writeFileSync(env.levels, JSON.stringify({ '272813EF/can/1': 'warn' }));
    await page.waitForFunction(() => IOThreeD.debug().states[0].level === 'warn', null, { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(400);
    const warnPx = await pixel(page, shoulderAt2.x, shoulderAt2.y);
    check('... and warn turns it the warn colour (orange: red over blue, unlike error\'s)', (await debug(page)).states[0].level === 'warn' && warnPx[0] > warnPx[2] + 60 && warnPx[1] > errPx[1] + 15, warnPx);
    fs.writeFileSync(env.levels, '{}');

    // ---------------------------------------------------------------------------------- 6. a ghost ----
    const ghost = await page.evaluate((i) => { const m = IOThreeD.debug().scene.partMaterial(i); return { opacity: m.opacity, transparent: m.transparent, depthWrite: m.depthWrite }; }, PART.tool);
    const toolAt = await project(page, PART.tool);
    const ghostPx = await pixel(page, toolAt.x, toolAt.y);
    // the same pixel with the part drawn solid for one frame (the page's next paint puts the ghost back)
    const solidPx = await page.evaluate(async ([x, y]) => {
      const scene = IOThreeD.debug().scene;
      scene.setStates(IOThreeD.debug().states.map((state) => ({ level: state.ghost ? 'ok' : state.level, ghost: false, selected: state.selected })));
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const at = scene.pixel(x, y);
      render();
      return at;
    }, [toolAt.x, toolAt.y]);
    await page.waitForTimeout(300);
    const ghostAgain = await pixel(page, toolAt.x, toolAt.y);
    check('a device that is not detected is a ghost (15 % opacity)', (await debug(page)).states[PART.tool].ghost && ghost.transparent && Math.abs(ghost.opacity - 0.15) < 1e-6 && !ghost.depthWrite, ghost);
    check('... measured: drawn solid, its pixel changes by more than 40; as a ghost it shows what is behind it', Math.max(...ghostPx.map((v, i) => Math.abs(v - solidPx[i]))) > 40
      && Math.max(...ghostPx.map((v, i) => Math.abs(v - ghostAgain[i]))) < 6, { ghostPx, solidPx, ghostAgain });

    // ------------------------------------------------------------------------------- 7. five info layers ----
    const pick = await rectOf(page, '#threed-switch');
    check('the 3D info picker sits at the bottom-right', pick && pick.visible && pick.bottom > VIEW.height - 40 && pick.right > VIEW.width - 260, pick);
    // v2: callout labels for what is not OK (the ghost; then every part on the toggle)
    fs.writeFileSync(env.levels, JSON.stringify({ '272813EF/can/2': 'warn' }));
    await page.waitForFunction(() => IOThreeD.debug().states[1].level === 'warn', null, { timeout: 20000 }).catch(() => {});
    await setVariant(page, 'v2');
    const chips = await page.$$eval('.td-chip', (els) => els.map((el) => { const r = el.getBoundingClientRect(); return { text: el.textContent, x: r.x, y: r.y, w: r.width, h: r.height }; }));
    const lines = await page.$$eval('.td-lines line', (els) => els.length);
    check('v2: a callout per device that is not OK (the warn board, the ghost), each with its leader line', chips.length === 2 && lines === 2 && chips.some((c) => /CAN 2/.test(c.text)) && chips.some((c) => /not detected/.test(c.text)), chips.map((c) => c.text));
    check('v2: the labels do not overlap and sit inside the viewport', chips.every((a, i) => chips.every((b, j) => i === j || a.y + a.h <= b.y || b.y + b.h <= a.y || a.x + a.w <= b.x || b.x + b.w <= a.x))
      && chips.every((c) => c.x >= viewport.x && c.x + c.w <= viewport.right + 1), chips);
    await page.evaluate(() => IOThreeD.debug().scene.home());
    await page.waitForTimeout(700);
    await page.click('[data-td-labels]');
    await page.waitForTimeout(200);
    const onScreen = await page.evaluate(() => [0, 1, 2, 3, 4].filter((i) => { const at = IOThreeD.debug().scene.project(i); return at && at.inside; }).length);
    check('v2: All labels draws every mapped part that is on screen', onScreen === 5 && (await page.$$('.td-chip')).length === onScreen, { onScreen, chips: (await page.$$('.td-chip')).length });
    await page.click('[data-td-labels]');
    // v3: the peek drawer
    await setVariant(page, 'v3');
    await page.click(`${await treeRow(page, ELBOW)} .t6-name`);
    await page.waitForFunction(() => /CAN 2/.test(document.querySelector('.td-drawer').textContent) && /Diagnostics/.test(document.querySelector('.td-drawer').textContent), null, { timeout: 8000 }).catch(() => {});
    const drawer = await rectOf(page, '.td-drawer');
    const drawerText = await page.$eval('.td-drawer', (el) => el.textContent);
    check('v3: selecting opens a narrow right drawer (summary, Diagnostics rows, Open)', drawer && drawer.visible && Math.abs(drawer.right - VIEW.width) < 2 && drawer.width > 200 && drawer.width < 500
      && /comms/.test(drawerText) && /warn/i.test(drawerText) && Boolean(await page.$('.td-drawer [data-td-open]')), { drawer, text: drawerText.slice(0, 100) });
    // v4: the split view
    await setVariant(page, 'v4');
    await page.waitForFunction(() => document.querySelector('.td-split-slot .d6-tabs'), null, { timeout: 8000 }).catch(() => {});
    const top = await rectOf(page, '.td-view');
    const bottom = await rectOf(page, '.td-split-slot');
    const tabs = await rectOf(page, '.td-split-slot .d6-tabs');
    check('v4: the viewport on top, the selected entity\'s real device page (its tabs) below', top.visible && bottom.visible && tabs && tabs.visible && bottom.y >= top.bottom - 1 && tabs.y >= bottom.y, { top, bottom, tabs });
    // v5: the problems tray
    await setVariant(page, 'v5');
    const items = await page.$$eval('.td-tray .td-item', (els) => els.map((el) => el.textContent));
    const tray = await rectOf(page, '.td-bottom');
    check('v5: a bottom strip lists every device that is not OK', items.length === 2 && tray.visible && tray.bottom <= VIEW.height && items.some((t) => /CAN 2/.test(t)) && items.some((t) => /Tool/.test(t)), items);
    await page.click('.td-tray .td-item:has-text("Tool")');
    await page.waitForTimeout(700);
    const toolFly = await project(page, PART.tool);
    const v5view = await rectOf(page, '.td-view');
    check('v5: clicking a tray row flies to its part (it lands near the centre)', Math.abs(toolFly.x - (v5view.x + v5view.width / 2)) < 60 && Math.abs(toolFly.y - (v5view.y + v5view.height / 2)) < 60, { toolFly, v5view });
    await page.mouse.move(toolFly.x, toolFly.y, { steps: 3 });
    await page.waitForTimeout(400);
    const tip = await page.evaluate(() => ({ tip: !document.querySelector('.td-tip').hidden && document.querySelector('.td-tip').textContent, card: !document.querySelector('.td-card').hidden }));
    check('v5: hovering a part shows only a tooltip (no card)', Boolean(tip.tip) && !tip.card, tip);
    await page.dblclick('.td-tray .td-item:has-text("CAN 2")');
    await page.waitForFunction((p) => S.view === 'tree' && S.sel === p, ELBOW, { timeout: 8000 }).catch(() => {});
    check('v5: double-clicking a tray row opens its device page', (await page.evaluate(() => S.view)) === 'tree');
    await page.goBack();
    await page.waitForFunction(() => S.view === 'threed', null, { timeout: 8000 }).catch(() => {});
    check('every variant uses the same scene (one canvas, one loaded model)', (await page.$$('.td-canvas')).length === 1);
    // the choice is remembered
    await page.reload();
    await page.waitForFunction(() => window.IOThreeD && IOThreeD.debug().variant, null, { timeout: 30000 });
    check('the picker\'s choice is remembered (localStorage io-diagnostics.threed.variant)', (await page.evaluate(() => [IOThreeD.debug().variant, localStorage.getItem('io-diagnostics.threed.variant')].join())) === 'v5,v5');
    await ready3d(page);

    // ------------------------------------------------------------------------ 8. every boundary drags, remembered ----
    async function drag(selector, dx, dy) {
      const r = await rectOf(page, selector);
      const x = r.x + r.width / 2;
      const y = r.y + r.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + dx, y + dy, { steps: 6 });
      await page.mouse.up();
      await page.waitForTimeout(200);
    }
    // the tray (v5)
    const trayBefore = (await rectOf(page, '.td-bottom')).height;
    await drag('[data-td-sash="tray"]', 0, -80);
    const trayAfter = (await rectOf(page, '.td-bottom')).height;
    check('v5: the tray\'s boundary drags (+80 px measured)', Math.abs(trayAfter - trayBefore - 80) <= 3, { trayBefore, trayAfter });
    // the drawer (v3)
    await setVariant(page, 'v3');
    const drawerBefore = (await rectOf(page, '.td-drawer')).width;
    await drag('[data-td-sash="drawer"]', -100, 0);
    const drawerAfter = (await rectOf(page, '.td-drawer')).width;
    check('v3: the drawer\'s boundary drags (+100 px measured)', Math.abs(drawerAfter - drawerBefore - 100) <= 3, { drawerBefore, drawerAfter });
    // the split (v4)
    await setVariant(page, 'v4');
    const splitBefore = (await rectOf(page, '.td-bottom')).height;
    await drag('[data-td-sash="split"]', 0, 90);
    const splitAfter = (await rectOf(page, '.td-bottom')).height;
    check('v4: the split\'s boundary drags (-90 px measured)', Math.abs(splitBefore - splitAfter - 90) <= 3, { splitBefore, splitAfter });
    // the tree pane beside the 3D view (L1's own)
    const treeBefore = (await rectOf(page, '.l1-side')).width;
    await drag('.lx-sash-l1side', 60, 0);
    const treeAfter = (await rectOf(page, '.l1-side')).width;
    check('the tree pane\'s boundary drags in the 3D view too (+60 px)', Math.abs(treeAfter - treeBefore - 60) <= 3, { treeBefore, treeAfter });
    await page.reload();
    await ready3d(page);
    await page.waitForTimeout(400);
    const kept = { split: (await rectOf(page, '.td-bottom')).height, tree: (await rectOf(page, '.l1-side')).width };
    await setVariant(page, 'v3');
    kept.drawer = (await rectOf(page, '.td-drawer')).width;
    await setVariant(page, 'v5');
    kept.tray = (await rectOf(page, '.td-bottom')).height;
    check('every size is remembered across a reload', Math.abs(kept.split - splitAfter) <= 2 && Math.abs(kept.drawer - drawerAfter) <= 2 && Math.abs(kept.tray - trayAfter) <= 2 && Math.abs(kept.tree - treeAfter) <= 2, kept);
    // fold and unfold (the inspector folds: Zach's rule)
    await setVariant(page, 'v3');
    await page.click('.td-drawer [data-td-fold="drawer"]');
    await page.waitForTimeout(200);
    const folded = await rectOf(page, '.td-drawer');
    const reopen = await rectOf(page, '[data-td-reopen="drawer"]');
    const wider = (await rectOf(page, '.td-view')).width;
    check('v3: the drawer folds away (the viewport takes its width) and a tab brings it back', (!folded || !folded.visible) && reopen && reopen.visible && wider > VIEW.width - 600, { folded, reopen, wider });
    await page.click('[data-td-reopen="drawer"]');
    await page.waitForTimeout(200);
    check('... unfolded at its remembered width', Math.abs((await rectOf(page, '.td-drawer')).width - drawerAfter) <= 2);
    await setVariant(page, 'v4');
    await page.click('.td-bar [data-td-fold="bottom"]');
    await page.waitForTimeout(200);
    check('v4: the device page below folds away (the viewport takes the height) and a tab brings it back', !(await rectOf(page, '.td-bottom')).visible
      && (await rectOf(page, '[data-td-reopen="bottom"]')).visible && (await rectOf(page, '.td-view')).height > VIEW.height - 160);
    await page.click('[data-td-reopen="bottom"]');
    await page.waitForTimeout(200);
    check('... unfolded at its remembered height', Math.abs((await rectOf(page, '.td-bottom')).height - splitAfter) <= 2);
    await setVariant(page, 'v5');
    await page.click('.td-bar [data-td-fold="bottom"]');
    await page.waitForTimeout(200);
    check('v5: the tray folds away and a tab brings it back', !(await rectOf(page, '.td-bottom')).visible && (await rectOf(page, '[data-td-reopen="bottom"]')).visible);
    await page.click('[data-td-reopen="bottom"]');
    await page.waitForTimeout(200);
    check('... unfolded at its remembered height', Math.abs((await rectOf(page, '.td-bottom')).height - trayAfter) <= 2);
    await setVariant(page, 'v1');

    // ------------------------------------------------------------------------------ 9. dark and light ----
    const bgLight = await pixel(page, (await rectOf(page, '.td-view')).right - 40, (await rectOf(page, '.td-view')).y + 140);
    await page.evaluate(() => { document.querySelector('#mode-switch [data-mode="dark"]').click(); });
    await page.waitForTimeout(500);
    const bgDark = await pixel(page, (await rectOf(page, '.td-view')).right - 40, (await rectOf(page, '.td-view')).y + 140);
    check('the scene follows light / dark (the skin\'s surface token)', bgLight[0] > 200 && bgDark[0] < 70, { bgLight, bgDark });
    await page.evaluate(() => { document.querySelector('#mode-switch [data-mode="light"]').click(); });
    check('no page errors in the 3D view', errors.errors.length === 0, errors.errors.slice(0, 5));
    await context.close();

    // ------------------------------------------------------- 10. without a description: as before, plus a calm empty state ----
    fs.renameSync(path.join(env.config, 'robot-description.toml'), path.join(env.config, 'off.toml'));
    const bare = await browser.newContext({ viewport: VIEW, colorScheme: 'light' });
    const page3 = await bare.newPage();
    const errors3 = H.watchErrors(page3, base);
    const vendor3 = [];
    page3.on('request', (request) => { if (/\/vendor\/(three|urdf-loader)\//.test(request.url())) vendor3.push(request.url()); });
    await page3.goto(`${base}/`);
    await page3.waitForFunction(() => typeof S !== 'undefined' && S.data && document.querySelector('.d6-tabs'), null, { timeout: 30000 });
    const answer = await H.getJson(base, '/api/v1/robot-description');
    check('GET /api/v1/robot-description says present: false', answer.present === false && answer.file === path.join(env.config, 'robot-description.toml'), answer);
    // every existing view, unchanged: the tree with the device page, the table, activity
    const inspector = await rectOf(page3, '#inspector');
    check('Tree view: the device page as before (the 3D host hidden)', inspector.visible && inspector.width > 700 && !(await rectOf(page3, '#threed-host')).visible, inspector);
    await page3.click('.l1-ribbon [data-l1="table"]');
    await page3.waitForTimeout(400);
    check('Table view: the table as before', (await rectOf(page3, 'table.grid')).visible);
    await page3.click('.l1-ribbon [data-l1="activity"]');
    await page3.waitForTimeout(400);
    check('Activity view: the log as before', (await rectOf(page3, '#activity-host')).visible);
    await page3.click('.l1-ribbon [data-l1="tree"]');
    await page3.waitForTimeout(300);
    check('the 3D item still shows', (await rectOf(page3, '.l1-ribbon [data-l1="threed"]')).visible);
    await page3.click('.l1-ribbon [data-l1="threed"]');
    await page3.waitForFunction(() => { const e = document.querySelector('.td-empty'); return e && !e.hidden && /No robot description/.test(e.textContent); }, null, { timeout: 10000 }).catch(() => {});
    const empty = await rectOf(page3, '.td-empty-in');
    const emptyText = await page3.$eval('.td-empty', (el) => el.textContent).catch(() => '');
    check('3D without a description: a calm empty state (what, where, the format, the docs)', empty && empty.visible && /robot-description\.toml/.test(emptyText) && emptyText.includes(path.join(env.config, 'robot-description.toml'))
      && /\[\[part\]\]/.test(emptyText) && /docs\/robot-description\.md/.test(emptyText), emptyText.slice(0, 160));
    check('... no canvas and no 3D library loaded', (await page3.$$('.td-canvas')).length === 0 && vendor3.length === 0, vendor3.length);
    check('... the tree stays beside it', (await rectOf(page3, '#tree-host')).visible);
    check('no page errors without a description', errors3.errors.length === 0, errors3.errors.slice(0, 5));
    await bare.close();
    fs.renameSync(path.join(env.config, 'off.toml'), path.join(env.config, 'robot-description.toml'));
  } catch (error) {
    failed = true;
    console.error(error);
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary() || (failed ? 1 : 0));
})();
