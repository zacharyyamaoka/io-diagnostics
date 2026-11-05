// A stock check that did NOT RUN says nothing about its entity (io_diagnostics/not_run.py), in the real page on a fixture host: the board's
// level, the header counts, the tray's attention list, the tree dot and the 3D view's colour for it stay what its real checks say (OK),
// while the entity page's Diagnostics tab folds the rows that did not run into ONE quiet line ("N checks did not run") that opens to
// them, grey, each with its own reason, never as OK and never as a problem. Colours are pixels read back from the canvas.
//
//   node tests/test_not_run_page.js [--shots DIR]     (a fixture host on a scratch port; no hardware, never :8820-8822)
//
// The fixture joint has two boards (STATE_NODES=2): CAN 2 answers and its firmware is the latest, so its own checks are all OK; CAN 1
// carries the fixture's driver fault. Two bindings on both boards cannot run: servo.hold_effort has no source anywhere (NoSource) and a
// 600 s board temperature has a few seconds of State history (NotEnoughHistory).
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, fs, os, path } = H;

const FIXTURE = path.join(H.ROOT, 'tests', 'fixtures', 'robot-description');
const ADAPTER = 'host/usb1/port-1-3/usb-1-3';
const SHOULDER = `${ADAPTER}/can/1`;
const ELBOW = `${ADAPTER}/can/2`;
const PART = { shoulder: 0, elbow: 1 };
const ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']; // WebGL in headless Chrome (software)
const SHOTS = (() => { const at = process.argv.indexOf('--shots'); return at > 0 ? process.argv[at + 1] : path.join(H.ROOT, 'reports', 'media', 'not-run'); })();
const TOML = `
[[diagnostic]]
id = "servo.hold_effort"
diagnostic = "stock.share"
title = "hold convergence"
path = "*/can/*"
window_s = 60
[diagnostic.config]
limits = { error_low = 0.8 }

[[diagnostic]]
id = "driver.board_temp"
diagnostic = "stock.box"
title = "board temperature"
path = "*/can/*"
state = "motor"
window_s = 600
[diagnostic.config]
channel = "temp_c"
unit = "Cel"
max = { warn_high = 60.0, error_high = 70.0 }
`;
const blueish = ([r, g, b]) => b - r;
const reddish = ([r, g, b]) => r - Math.max(g, b);
const project = (page, index) => page.evaluate((i) => IOThreeD.debug().scene.project(i), index);
const pixel = (page, x, y) => page.evaluate(([px, py]) => IOThreeD.debug().scene.pixel(px, py), [x, y]);

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'not-run-page-'));
  const config = path.join(root, 'config');
  fs.mkdirSync(config, { recursive: true });
  fs.writeFileSync(path.join(config, 'diagnostics.toml'), TOML);
  fs.writeFileSync(path.join(config, 'robot-description.toml'), fs.readFileSync(path.join(FIXTURE, 'robot-description.toml'), 'utf8')
    .replace('urdf = "robot.urdf"', `urdf = "${path.join(FIXTURE, 'robot.urdf')}"`));
  const server = await H.startServer(root, 'F1', { STATE_NODES: '2' });
  const { base } = server;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ARGS });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    // the entity page of CAN 2 keeps the board watched (its heartbeat), so its State history exists and the bindings are judged
    await H.open(page, base, ELBOW, 'diagnostics');
    const notRun = (node) => (node.checks || []).filter((c) => c.not_run);
    const node = await H.waitFor(async () => { const found = await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(ELBOW)}`); return notRun(found).length >= 2 ? found : null; }, 90000, 500);
    check('the two bindings that cannot run are posted on CAN 2, as unknown rows marked not_run', node && notRun(node).map((c) => `${c.code}:${c.effective_level}`).sort().join() === 'NotEnoughHistory:unknown,NotWired:unknown', node && notRun(node).map((c) => c.code));
    check('every other check of CAN 2 says it ran', node && node.checks.filter((c) => !c.not_run).every((c) => c.not_run === false && c.effective_level === 'ok'), node && node.checks.map((c) => [c.name, c.effective_level]));
    check('CAN 2 reads OK although two of its rows are unknown', node && node.node.level === 'ok' && node.node.own.level === 'ok' && node.node.hint === null && node.node.checks === node.checks.length && node.node.not_run === 2, node && node.node.level);

    const state = await H.getJson(base, '/api/v1/state');
    const attention = state.summary.attention.map((item) => item.path);
    check('CAN 2 is not in the attention list, and the header counts hold no unknown', !attention.includes(ELBOW) && !state.summary.counts.unknown, { attention, counts: state.summary.counts });
    check('CAN 1 (the fixture fault) stays an error', attention.includes(SHOULDER), attention);

    // ---- the page: the tree dot, the tab, the one quiet line ----------------------------------------------------------------
    const tree = await page.evaluate((p) => ({ level: S.byPath.get(p).level, own: S.byPath.get(p).own.level }), ELBOW);
    check('the tree row of CAN 2 is ok', tree.level === 'ok' && tree.own === 'ok', tree);
    await page.waitForSelector('[data-dx-notrun]', { timeout: 20000 });
    const closed = await page.evaluate(() => {
      const line = document.querySelector('[data-dx-notrun]');
      const why = document.querySelector('.d6-why');
      return { text: line.textContent.trim(), rows: line.querySelectorAll('.dx-r').length, expanded: line.querySelector('[data-dx-toggle="notrun"]').getAttribute('aria-expanded'),
        badge: Boolean(document.querySelector('[data-d6-tab="diagnostics"] .d6-bd')), why: why ? why.textContent : '', title: document.querySelector('.sec.dx-diag .sec-h').textContent,
        listed: [...document.querySelectorAll('.dx-tab .dx-nt')].map((e) => e.textContent) };
    });
    check('the rows that did not run are ONE line "2 checks did not run", folded', /^2 checks did not run$/.test(closed.text) && closed.rows === 0 && closed.expanded === 'false', closed);
    check('the Diagnostics tab counts only the checks that ran, with no problem badge, and says all OK', /Diagnostics · 2/.test(closed.title) && !closed.badge && /all OK/.test(closed.why)
      && !closed.listed.some((name) => /hold convergence|board temperature/.test(name)), closed);
    await page.screenshot({ path: path.join(SHOTS, '01-diagnostics-folded.png') });

    await page.click('[data-dx-notrun] [data-dx-toggle="notrun"]');
    await page.waitForSelector('[data-dx-notrun] .dx-r', { timeout: 5000 });
    const opened = await page.evaluate(() => [...document.querySelectorAll('[data-dx-notrun] .dx-r')].map((row) => ({
      name: row.querySelector('.dx-nt').textContent, message: row.querySelector('.dx-mt').textContent, tag: row.querySelector('.dx-s').textContent.trim(),
      tagClass: row.querySelector('.dx-s .pill').className, problem: Boolean(row.querySelector('[data-diagnostic]')) || row.classList.contains('bad'),
      color: getComputedStyle(row.querySelector('.dx-s .pill')).color, bg: getComputedStyle(row.querySelector('.dx-s .pill')).backgroundColor,
      box: (() => { const b = row.getBoundingClientRect(); return { w: b.width, h: b.height }; })() })));
    check('opened, each row is grey with its own reason and is never OK', opened.length === 2
      && opened.every((row) => row.tag === 'not run' && /pill none/.test(row.tagClass) && !row.problem && row.bg === 'rgba(0, 0, 0, 0)' && row.box.h > 0)
      && opened.some((row) => /^CAN 2: hold convergence$/.test(row.name) && /^no hold events source on CAN 2/.test(row.message))
      && opened.some((row) => /^CAN 2: board temperature$/.test(row.name) && /600 s window/.test(row.message)), opened);
    await page.screenshot({ path: path.join(SHOTS, '02-diagnostics-opened.png') });
    await page.click('[data-dx-notrun] [data-dx-toggle="notrun"]');
    await page.waitForFunction(() => !document.querySelector('[data-dx-notrun] .dx-r'), null, { timeout: 5000 });
    check('the line folds again', true);

    // ---- a page revisited paints from the page's cache before any answer (test_instant_pages.js): the same split, or the rows come back as problems ----
    await page.evaluate((p) => select(p), SHOULDER);
    await page.waitForFunction((p) => S.node && S.node.node.path === p, SHOULDER, { timeout: 20000 });
    await page.route('**/api/v1/node*', () => {}); // never answered: only the cache can paint CAN 2
    await page.evaluate((p) => select(p), ELBOW);
    await page.waitForFunction(() => /CAN 2/.test((document.querySelector('.d6-top') || {}).textContent || '') && document.querySelector('.dx-tab'), null, { timeout: 10000 }); // CAN 1's page has the same line
    await page.waitForTimeout(300);
    const cached = await page.evaluate(() => ({ text: (document.querySelector('[data-dx-notrun]') || {}).textContent?.trim(), badge: Boolean(document.querySelector('[data-d6-tab="diagnostics"] .d6-bd')),
      listed: [...document.querySelectorAll('.dx-tab .dx-nt')].map((e) => e.textContent) }));
    check('a page painted from the cache folds them too: one line, no problem badge', /^2 checks did not run$/.test(cached.text) && !cached.badge
      && !cached.listed.some((name) => /hold convergence|board temperature/.test(name)), cached);
    await page.unroute('**/api/v1/node*');

    // ---- the live feed the 3D view paints from, and the 3D view's colour ------------------------------------------------------
    const live = await page.evaluate(() => new Promise((resolve) => {
      const source = new EventSource('/api/v1/live');
      source.addEventListener('snapshot', (event) => { source.close(); resolve(JSON.parse(event.data)); });
    }));
    const find = (row, wanted) => (row.path === wanted ? row : (row.children || []).map((child) => find(child, wanted)).find(Boolean));
    check('the live feed sends CAN 2 as ok', find(live.tree, ELBOW).level === 'ok' && find(live.tree, SHOULDER).level === 'error', [find(live.tree, ELBOW).level, find(live.tree, SHOULDER).level]);

    const view = await context.newPage();
    watch.errors.length = 0;
    await view.goto(`${base}/`);
    await view.waitForFunction(() => typeof S !== 'undefined' && S.data && document.querySelector('.l1-ribbon [data-l1="threed"]'), null, { timeout: 30000 });
    await view.click('.l1-ribbon [data-l1="threed"]');
    await view.waitForFunction(() => { const d = window.IOThreeD && IOThreeD.debug(); return d && d.scene && d.scene.loaded() && d.states && d.states.length; }, null, { timeout: 60000 });
    await view.waitForTimeout(800);
    const stillThere = notRun(await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(ELBOW)}`)).length;
    const parts = await view.evaluate(() => IOThreeD.debug().states.map((s) => ({ level: s.level, ghost: s.ghost })));
    const elbowAt = await project(view, PART.elbow);
    const shoulderAt = await project(view, PART.shoulder);
    const elbowPx = await pixel(view, elbowAt.x, elbowAt.y);
    const shoulderPx = await pixel(view, shoulderAt.x, shoulderAt.y);
    check('the 3D view paints the elbow as ok (not unknown) while its two rows did not run (pixels read back)', stillThere === 2 && parts[PART.elbow].level === 'ok' && !parts[PART.elbow].ghost
      && blueish(elbowPx) < 20 && reddish(elbowPx) < 20 && reddish(shoulderPx) > 60, { rows: stillThere, parts, elbow: elbowPx, shoulder: shoulderPx });
    await view.screenshot({ path: path.join(SHOTS, '03-threed.png') });
    check('no console errors', watch.errors.length === 0, watch.errors);
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
})().catch((error) => { console.error(error); process.exit(1); });
