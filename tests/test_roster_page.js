#!/usr/bin/env node
// Acceptance for the roster rules in the REAL page (Zach, 2026-10-24): what the entity tree shows when things are not
// all there. tests/fixtures/roster_scenario.py serves a fake rack: fdcanusb 272813EF with CAN 1 and CAN 14 answering,
// CAN 15 expected and never heard (no row), CAN 21 heard once and then silent (its row stays, hollow); fdcanusb 0607623A
// expected and not on USB (a status on the Computer, no row); a RealSense; a keyboard, a mouse and a webcam no driver claims.
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_roster_page.js            (--mutate proves the ruler can fail)
//
// Headless Chrome only; the scenario host is a subprocess on a free port, stopped by its process group. Screenshots:
// reports/media/roster/tree-<before|after>-<light|dark>.png (1400 x 900). "before" is the tree as the previous page drew
// it, rebuilt from the same data: the old page is not run again, so the before shots come from `--before-from DIR`
// (a checkout of the commit before) when given.
//
// Checks: rows (present / absent), dots (filled, hollow, neutral colours), health marks (levels only, none for stale or
// knock-on, the roll-up is the same shape), the header (order, legend, filter), the row menu (Remove from bus makes the
// stale board's row go), visibility (hidden by default, "Show N hidden", the eye in the status bar, persisted with the
// settings, a reload keeps it), expansion (keyed by hardware id, survives a path change and a reload) and a host restart
// with a blocked run (rows and expansion unchanged).
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'roster');
const VIEWPORT = { width: 1400, height: 900 };
// --mutate NAME proves the ruler can fail: each mutant breaks one rule in the page and the check named for it must go red
//   dot-amber      the presence dots are painted amber          -> "dots: neutral by construction"
//   header-reversed  the header counts are drawn right to left  -> "header: stale then unknown then warn then error"
//   stale-mark     a stale shape is drawn on the quiet board     -> "marks: only the diagnostics levels" and "no health mark"
//   unhide         every row is shown, hidden or not             -> "visibility: devices no driver claims are hidden by default"
const MUTATE = process.argv.includes('--mutate') ? process.argv[process.argv.indexOf('--mutate') + 1] : '';
const EXPECTED_RED = {
  'dot-amber': ['dots: neutral by construction'],
  'header-reversed': ['header: stale then unknown then warn then error'],
  'stale-mark': ['marks: only the diagnostics levels', 'marks: a stale board row has no health mark'],
  unhide: ['visibility: devices no driver claims are hidden by default'],
};
const RED = [];
const { chromium } = require(PLAYWRIGHT);

let passed = 0;
let failed = 0;
function check(name, ok, detail) {
  if (ok) passed += 1; else failed += 1;
  if (!ok) RED.push(name);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok && !process.env.VERBOSE ? '' : `  ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`}`);
}

// ------------------------------------------------------------------------------------------ server ----
function startScenario(dir, args = [], port = 0) {
  const env = { ...process.env };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'tests', 'fixtures', 'roster_scenario.py'),
    '--dir', dir, '--port', String(port), ...args], { cwd: ROOT, env: { ...env, IO_DIAGNOSTICS_STATE_DIR: path.join(dir, 'state'), IO_DIAGNOSTICS_CONFIG_DIR: path.join(dir, 'cfg') },
    detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stop(child); reject(new Error(`scenario did not start: ${output}`)); }, 60000);
    const onData = (chunk) => {
      output += chunk;
      const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//);
      if (found) { clearTimeout(timer); resolve({ child, base: found[1] }); }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`scenario exited (${code}): ${output}`)); });
  });
}
function stop(child) {
  try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* already gone */ }
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function control(dir) { fs.writeFileSync(path.join(dir, 'control.json'), JSON.stringify({ seq: Date.now() })); }

// ------------------------------------------------------------------------------------------- page ----
async function load(page, base) {
  await page.goto(`${base}/`);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.t6-row') && S.data && S.data.tree, null, { timeout: 30000 });
  await page.waitForTimeout(500);
}
// the visible rows: label, depth, presence dot, health shapes, bundle
const rows = (page) => page.evaluate(() => [...document.querySelectorAll('.t6-row')].map((row) => ({
  key: row.getAttribute('data-t6-path'), name: row.querySelector('.t6-name').textContent, depth: Number(getComputedStyle(row).getPropertyValue('--t6-d')),
  pres: (row.querySelector('.t6-pres') || {}).className ? [...row.querySelector('.t6-pres').classList].find((name) => name.startsWith('t6-pres-')).slice(8) : '',
  shapes: [...row.querySelectorAll('.t6-st .t6-shape')].map((shape) => [...shape.classList].find((name) => /^t6-(warn|error|unknown|stale)$/.test(name)).slice(3)),
  roll: row.querySelectorAll('.t6-st .t6-roll').length, bundle: row.classList.contains('t6-bundle'), dim: row.classList.contains('t6-dim'),
  open: row.getAttribute('aria-expanded'), text: row.textContent })));
const named = (list, name) => list.find((row) => row.name === name);
const names = (list) => list.map((row) => row.name);

async function hover(page, key) {
  await page.hover(`.t6-row[data-t6-path="${key}"]`);
}

async function shot(page, mode, file) {
  await page.emulateMedia({ colorScheme: mode });
  await page.evaluate((value) => { document.documentElement.dataset.mode = value; }, mode);
  await page.waitForTimeout(300);
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, file.replace('{mode}', mode)), clip: { x: 0, y: 0, width: VIEWPORT.width, height: VIEWPORT.height } });
}

async function main() {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'io-roster-'));
  const servers = [];
  try {
    // ---------------------------------------------------------------- the aged scenario ----
    const dir = path.join(tmp, 'aged');
    const aged = await startScenario(dir, ['--aged', '--ledger']);
    servers.push(aged);
    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
    // WHY Physical (p1): the rows below are asserted as the physical tree (the computer is the one root row, the absent adapter's warning is on it);
    // the page's default grouping is Nested (p6), which draws the rig beside the computer (tests/test_tree_nav.js covers it)
    await context.addInitScript(() => { try { localStorage.setItem('io-diagnostics.structure', 'p1'); } catch (_) { /* private mode */ } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('response', (response) => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`); });
    await load(page, aged.base);
    if (MUTATE === 'dot-amber') await page.addStyleTag({ content: '.t6-pres-heard, .t6-pres-stale, .t6-pres-live { background: #f59e0b !important; box-shadow: 0 0 0 2px #f59e0b !important; }' });
    if (MUTATE === 'header-reversed') await page.addStyleTag({ content: '.t6-sum { flex-direction: row-reverse !important; }' });
    if (MUTATE === 'stale-mark') await page.evaluate(() => { for (const slot of document.querySelectorAll('.t6-row')) if (/CAN 21/.test(slot.textContent)) slot.querySelector('.t6-st').innerHTML = IOSummary.shape('stale', 'stale'); });
    if (MUTATE === 'unhide') await page.evaluate(() => { IOVisibility.arrange = (rows) => rows; IOVisibility.toggleMode(false); });
    await page.waitForTimeout(1200);

    let list = await rows(page);
    const labels = names(list);
    check('rows: the computer is the root row, plugged things hang under it; no Computer or Built-in row',
      list[0].name === 'bam-GPU' && list[0].depth === 0 && list.slice(1).every((row) => row.depth >= 1) && !labels.includes('Computer') && !labels.includes('Built-in'), labels);
    check('rows: a board that answered then went quiet keeps its row; one that never answered has none; an unplugged adapter has none',
      labels.includes('moteus-c1 · CAN 21') || labels.includes('CAN 21'), labels);
    check('rows: no row for CAN 15 (never heard) or for fdcanusb 0607623A (expected, not on USB)',
      !labels.some((label) => /CAN 15|0607623A/.test(label)), labels);
    const can1 = list.find((row) => /CAN 1$/.test(row.name));
    const can21 = list.find((row) => /CAN 21$/.test(row.name));
    check('dots: heard = filled, not heard lately = hollow ring', can1 && can1.pres === 'heard' && can21 && can21.pres === 'stale', { can1: can1 && can1.pres, can21: can21 && can21.pres });
    check('dots: the hollow row says when, in words', /last heard 10 min ago/.test(can21.text), can21.text);
    const dotColours = await page.evaluate(() => [...document.querySelectorAll('.t6-pres:not(.t6-pres-none)')].map((dot) => {
      const style = getComputedStyle(dot);
      return [style.backgroundColor, style.boxShadow];
    }));
    const chroma = (css) => { const m = /rgba?\((\d+)[ ,]+(\d+)[ ,]+(\d+)/.exec(css || ''); if (!m) return 0; const v = [+m[1], +m[2], +m[3]]; return (Math.max(...v) - Math.min(...v)) / 255; };
    check('dots: neutral by construction, never a warning colour', dotColours.length > 0 && dotColours.every(([fill, ring]) => chroma(fill) < 0.12 && chroma(ring) < 0.12), dotColours);

    const marks = list.flatMap((row) => row.shapes);
    check('marks: only the diagnostics levels (warn, error, unknown) draw a shape; stale never does', marks.length > 0 && marks.every((level) => ['warn', 'error', 'unknown'].includes(level)), marks);
    check('marks: the Computer carries the absent adapter\'s warning', list[0].shapes.includes('warn'), list[0].shapes);
    check('marks: a stale board row has no health mark (its hollow dot says it)', can21.shapes.length === 0, can21.shapes);

    // ----------------------------------------------------------------------- header ----
    const head = await page.evaluate(() => [...document.querySelectorAll('.t6-count')].map((button) => ({ level: button.getAttribute('data-t6-count'), n: button.textContent.trim(), x: button.getBoundingClientRect().x })));
    check('header: stale then unknown then warn then error, left to right; zeros hidden',
      head.map((item) => item.level).join() === 'stale,warn' && head.every((item, at) => at === 0 || head[at - 1].x < item.x) && head[0].n === '1' && head[1].n === '2', head);
    await page.hover('.t6-sum');
    const legend = await page.evaluate(() => { const element = document.querySelector('.t6-legend'); const style = getComputedStyle(element); return { shown: style.display !== 'none', text: element.textContent }; });
    check('header: hovering explains every mark in plain words', legend.shown && /stale/.test(legend.text) && /couldn't check it \(for example tview has the port\)/.test(legend.text) && /Broken/.test(legend.text), legend);
    await page.mouse.move(700, 600);
    // the tree as it is on first load, light and dark (Zach looks at these)
    await page.evaluate((key) => { document.activeElement && document.activeElement.blur(); }, null);
    await shot(page, 'light', 'tree-after-{mode}.png');
    await shot(page, 'dark', 'tree-after-{mode}.png');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.evaluate(() => { document.documentElement.dataset.mode = 'light'; });

    // clicking a count filters the tree to those rows
    await page.click('.t6-count[data-t6-count="stale"]');
    await page.waitForTimeout(300);
    const filtered = names(await rows(page));
    check('header: a click on a count filters the tree to those rows (and their ancestors)', filtered.some((label) => /CAN 21/.test(label)) && !filtered.some((label) => /CAN 14|RealSense/.test(label)), filtered);
    await page.click('.t6-count[data-t6-count="stale"]');
    await page.waitForTimeout(300);
    check('header: a second click clears the filter', names(await rows(page)).length === labels.length, names(await rows(page)));

    // --------------------------------------------------------------------- visibility ----
    const bundle = list.find((row) => row.bundle);
    check('visibility: devices no driver claims are hidden by default, behind one quiet "Show N hidden" row',
      bundle && bundle.name === 'Show 3 hidden' && !labels.some((label) => /DURGOD|G203|Logi/.test(label)), { bundle: bundle && bundle.name, labels });
    await page.click('.t6-row.t6-bundle');
    await page.waitForTimeout(300);
    list = await rows(page);
    check('visibility: open, the hidden rows show dimmed and the row reads "Hide 3 hidden"',
      list.filter((row) => row.dim).length === 3 && list.some((row) => row.bundle && row.name === 'Hide 3 hidden'), list.filter((row) => row.dim || row.bundle).map((row) => row.name));
    await page.click('.t6-row.t6-bundle');
    await page.waitForTimeout(200);
    const eyesAtRest = await page.evaluate(() => [...document.querySelectorAll('.t6-eye')].some((eye) => getComputedStyle(eye).display !== 'none'));
    await page.click('#vis-toggle');
    await page.waitForTimeout(200);
    const eyesOn = await page.evaluate(() => [...document.querySelectorAll('.t6-eye')].filter((eye) => getComputedStyle(eye).display !== 'none').length);
    check('visibility: the eye appears only while the status bar\'s Visibility is on', !eyesAtRest && eyesOn > 0, { eyesAtRest, eyesOn });
    // bring the webcam back with its eye: kept with the app settings, and a reload keeps it
    await page.click('.t6-row.t6-bundle');
    await page.waitForTimeout(200);
    const webcamKey = (await rows(page)).find((row) => /Logi/.test(row.name)).key;
    await page.click(`.t6-row[data-t6-path="${webcamKey}"] .t6-eye`);
    await page.waitForTimeout(900);
    const settings = await (await fetch(`${aged.base}/api/v1/settings`)).json();
    check('visibility: the choice is kept with the app settings (POST /api/v1/settings)', settings.visibility && settings.visibility['usb:046d:08e5:WC1'] === 'show', settings.visibility);
    await page.reload();
    await load(page, aged.base);
    list = await rows(page);
    check('visibility: a reload keeps it; the other two stay hidden', names(list).some((label) => /Logi/.test(label)) && list.some((row) => row.bundle && row.name === 'Show 2 hidden'), names(list));
    await page.evaluate(() => { IOVisibility.toggleMode(false); });

    // ------------------------------------------------------------- the row menu ----
    await hover(page, can21.key);
    await page.click(`.t6-row[data-t6-path="${can21.key}"] .t6-more`);
    await page.waitForSelector('.t6-menu:not([hidden]) .t6-mi', { timeout: 10000 });
    await page.waitForFunction(() => /Remove from bus/.test(document.querySelector('.t6-menu').textContent), null, { timeout: 10000 });
    const menu = await page.evaluate(() => [...document.querySelectorAll('.t6-menu .t6-mi')].map((item) => item.textContent.replace(/\s+/g, ' ').trim()));
    check('menu: Copy id, Remove from bus (it comes back if it answers), Expect / Don\'t expect, the row\'s utilities, Hide',
      menu.some((text) => text.startsWith('Copy id')) && menu.some((text) => /^Remove from bus.*comes back/.test(text)) && menu.some((text) => /^Don't expect/.test(text))
      && menu.some((text) => /^Identify/.test(text)) && menu.some((text) => /^Open console \(tview\)/.test(text)) && menu.some((text) => /^Hide/.test(text)), menu);
    await shot(page, 'light', 'menu-{mode}.png');
    // a utility shortcut starts that row's dry run (needs app.js's onRowAction hook; without it the row is only selected)
    if (await page.evaluate(() => typeof onRowAction === 'function')) {
      await page.click('.t6-menu [data-t6-mi="util:identify"]');
      await page.waitForFunction(() => typeof S !== 'undefined' && S.util && S.util.name === 'identify', null, { timeout: 8000 });
      check('menu: Identify (a utility shortcut) opens that row\'s dry run', await page.evaluate(() => S.util.path.endsWith('/can/21')), await page.evaluate(() => S.util && S.util.path));
      await page.evaluate(() => { S.util = null; });
      await hover(page, can21.key);
      await page.click(`.t6-row[data-t6-path="${can21.key}"] .t6-more`);
      await page.waitForFunction(() => /Remove from bus/.test(document.querySelector('.t6-menu').textContent), null, { timeout: 10000 });
    } else console.log('SKIP  menu: utility shortcut (app.js has no onRowAction hook yet)');
    await page.click('.t6-menu [data-t6-mi="setup:forget"]');
    const confirmText = await page.textContent('.t6-menu .t6-mi-confirm');
    check('menu: the confirm is the host\'s own sentence', /^Remove CAN 21 from the bus\? RobotDx forgets the row; if CAN 21 answers again, it comes back\.$/.test(confirmText), confirmText);
    await page.click('.t6-menu [data-t6-mi="confirm"]');
    await page.waitForFunction((key) => !document.querySelector(`.t6-row[data-t6-path="${key}"]`), can21.key, { timeout: 10000 });
    list = await rows(page);
    check('menu: Remove from bus makes the stale board\'s row disappear', !names(list).some((label) => /CAN 21/.test(label)), names(list));
    const counts = await page.evaluate(() => S.data.summary.counts);
    check('menu: and the header no longer counts it as stale', !counts.stale, counts);
    // it comes back when it answers again
    fs.writeFileSync(path.join(dir, 'rack.json'), JSON.stringify({ ...JSON.parse(fs.readFileSync(path.join(dir, 'rack.json'), 'utf8')), '272813EF': { name: 'fdcanusb 272813EF', model_id: 'mjbots-fdcanusb', answer: [1, 14, 21] } }));
    control(dir);
    await page.waitForFunction(() => [...document.querySelectorAll('.t6-name')].some((name) => /CAN 21$/.test(name.textContent)), null, { timeout: 15000 });
    check('menu: a removed board that answers again comes back', true);

    // -------------------------------------------------------------------- expansion ----
    const adapter = (await rows(page)).find((row) => /fdcanusb · 272813EF|fdcanusb 272813EF/.test(row.name));
    await page.click(`.t6-row[data-t6-path="${adapter.key}"] .t6-chev`);
    await page.waitForTimeout(300);
    const closed = await page.evaluate((key) => document.querySelector(`.t6-row[data-t6-path="${key}"]`).getAttribute('aria-expanded'), adapter.key);
    await page.waitForTimeout(2500); // several 1 s ticks of the page: it must not snap open again
    const stillClosed = await page.evaluate((key) => document.querySelector(`.t6-row[data-t6-path="${key}"]`).getAttribute('aria-expanded'), adapter.key);
    await page.reload();
    await load(page, aged.base);
    const afterReload = await page.evaluate((key) => document.querySelector(`.t6-row[data-t6-path="${key}"]`).getAttribute('aria-expanded'), adapter.key);
    check('expansion: a row closed by hand stays closed through ticks and a reload', closed === 'false' && stillClosed === 'false' && afterReload === 'false', { closed, stillClosed, afterReload });
    // keyed by the hardware id: the same adapter on another port (another path) keeps its state
    const keyed = await page.evaluate(() => {
      const v6 = window.IOTreeRenderers.v6;
      const box = document.createElement('div');
      document.body.appendChild(box);
      const row = (key, open) => ({ key, entity: key, label: 'adapter', hardware_id: 'usb:0483:5740:ID', depth: 0, parent: null, kind: 'device', icon: 'adapter', status: { own: null, hint: null, part: null, note: '' },
        children: [{ key: `${key}/a`, entity: `${key}/a`, label: 'child', depth: 1, parent: key, kind: 'node', icon: 'servo', status: { own: null, hint: null, part: null, note: '' }, children: [], attrs: [], parts: [], defaultOpen: false }],
        attrs: [], parts: [], defaultOpen: open });
      v6.render(box, [row('host/port-1-3/adapter', true)], { summary: { counts: {} } });
      box.querySelector('.t6-chev').click(); // close it
      v6.render(box, [row('host/port-1-5/adapter', true)], { summary: { counts: {} } }); // plugged into another port
      const state = box.querySelector('.t6-row').getAttribute('aria-expanded');
      box.remove();
      return state;
    });
    check('expansion: keyed by hardware id, so the same adapter on another port keeps it closed', keyed === 'false', keyed);

    check('page: no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
    await context.close();
    stop(aged.child);
    servers.pop();

    // --------------------------------------------- a restart with a blocked run changes nothing ----
    const dir2 = path.join(tmp, 'restart');
    const first = await startScenario(dir2, ['--ledger']);
    servers.push(first);
    const context2 = await browser.newContext({ viewport: VIEWPORT });
    const page2 = await context2.newPage();
    await load(page2, first.base);
    const before = names(await rows(page2));
    const adapter2 = (await rows(page2)).find((row) => /272813EF/.test(row.name) && row.open !== null);
    await page2.click(`.t6-row[data-t6-path="${adapter2.key}"] .t6-chev`); // close it by hand
    await page2.waitForTimeout(300);
    const shownBefore = names(await rows(page2));
    stop(first.child);
    servers.pop();
    await sleep(800);
    // tview takes the port: the first runs of the restarted host are blocked; nothing may disappear
    const rack = JSON.parse(fs.readFileSync(path.join(dir2, 'rack.json'), 'utf8'));
    rack['272813EF'] = { ...rack['272813EF'], hold: true };
    fs.writeFileSync(path.join(dir2, 'rack.json'), JSON.stringify(rack));
    const second = await startScenario(dir2, ['--ledger'], Number(new URL(first.base).port)); // the same origin: the page's own memory (localStorage) is per origin
    servers.push(second);
    await page2.goto(`${second.base}/`);
    await page2.waitForFunction(() => document.querySelector('.t6-row'), null, { timeout: 30000 });
    await page2.waitForTimeout(800);
    const after = names(await rows(page2));
    const adapterAfter = (await rows(page2)).find((row) => /272813EF/.test(row.name) && row.open !== null);
    const hollow = (await rows(page2)).filter((row) => /CAN (1|14|21)$/.test(row.name)).map((row) => row.pres);
    const withoutRig = (list) => list.filter((label) => !/^1-DOF/.test(label)); // the rig row's counts are health, not rows
    check('restart: a restarted host with a blocked run shows the same rows', withoutRig(after).join('|') === withoutRig(shownBefore).join('|'), { before, shownBefore, after });
    check('restart: the expansion is unchanged (the adapter stays closed)', adapterAfter && adapterAfter.open === 'false', adapterAfter && adapterAfter.open);
    await page2.click(`.t6-row[data-t6-path="${adapterAfter.key}"] .t6-chev`);
    await page2.waitForTimeout(300);
    const reopened = (await rows(page2)).filter((row) => /CAN (1|14|21)$/.test(row.name));
    check('restart: the boards are all there, hollow (not heard yet), and none counted as dead',
      reopened.length === 3 && reopened.every((row) => row.pres === 'stale' && row.shapes.length === 0), reopened.map((row) => [row.name, row.pres, row.shapes]));
    const countsAfter = await page2.evaluate(() => S.data.summary.counts);
    check('restart: a blocked run marks nothing dead (no stale count from it)', !countsAfter.stale && !countsAfter.error, countsAfter);
    await context2.close();
  } finally {
    for (const server of servers) stop(server.child);
    await browser.close();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  if (MUTATE) {
    const wanted = EXPECTED_RED[MUTATE] || [];
    const missed = wanted.filter((name) => !RED.some((red) => red.startsWith(name)));
    console.log(`\nmutant ${MUTATE}: ${missed.length ? `SURVIVED (${missed.join('; ')} stayed green)` : 'KILLED'} · red: ${RED.length}`);
    process.exitCode = missed.length ? 1 : 0;
    return;
  }
  console.log(`\n${passed}/${passed + failed} checks passed`);
  process.exitCode = failed ? 1 : 0;
}

main().catch((error) => {
  console.error(MUTATE ? `(mutant run stopped: ${String(error).split('\n')[0]})` : error);
  if (MUTATE) {
    const missed = (EXPECTED_RED[MUTATE] || []).filter((name) => !RED.some((red) => red.startsWith(name)));
    console.log(`\nmutant ${MUTATE}: ${missed.length ? `SURVIVED (${missed.join('; ')} stayed green)` : 'KILLED'} · red: ${RED.length}`);
    process.exitCode = missed.length ? 1 : 0;
  } else process.exitCode = 1;
});
