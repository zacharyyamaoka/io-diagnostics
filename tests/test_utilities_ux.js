#!/usr/bin/env node
// Acceptance for the utilities' two states and one step (Zach, 2026-10-29), in the REAL page on fixture hosts, headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_utilities_ux.js
//
// Two fixture servers on free ports (`cli.py serve --fixture F1 --port 0 --no-udev`, the recorded v6_scenario plugin; and
// `--fixture F2 --plugins tests/fixtures/plugins-terminal`, a stdlib plugin that prints like a tool). Neither touches hardware;
// the test refuses to press anything when another plugin is loaded. Checks:
//   menu       no "can't tell" anywhere (the menu, and no action of any entity is "unknown"); every item is either enabled with
//              its name only, or disabled with one line of why under it; "All utilities…" is the last item and opens the tab
//   same page  the menu's and the Utilities tab's button open the same Run page for the same utility
//   one step   a Run-page utility runs on exactly one more click (the Run button), never before it; set_id's new id is typed
//              on its Run page, a taken id keeps Run disabled, and the run carries the id
//   immediate  Check communication and Open console (tview) run on the click: no Run page, one dry run and one run
//   table      the tab is a table with headers (Utility, Effect, Status, Last run); every row has the menu's icon; each column's
//              x is the same in every row (getBoundingClientRect)
//   walk       the adapter's Identify (wiggle each board): the Run page says "press Enter to move on", Run starts it in the
//              terminal, "Wiggling CAN ID 1", Enter typed in the terminal reaches the process and moves to CAN 14
// Screenshots (1440x900, light and dark): reports/media/utilities-ux-2026-10-29/ of the MAIN checkout (Zach's reports dir).
// IO_DIAG_ROOT=<checkout> serves another checkout's page (main, to see these assertions fail there). Exits 1 on any failure.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = process.env.IO_DIAG_ROOT || path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = process.env.IO_DIAG_SHOTS || '/home/bam/io-diagnostics/reports/media/utilities-ux-2026-10-29';
const VIEWPORT = { width: 1440, height: 900 };
const ADAPTER = 'host/usb1/port-1-3/usb-1-3';
const CAN15 = `${ADAPTER}/can/15`;

let passed = 0;
let failed = 0;
const reported = new Set();
function check(name, ok, detail = '') {
  reported.add(name);
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(58)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

// One part of the page under test: when it cannot run at all (main has no "All utilities…", no Run page), each of its
// assertions not yet reported fails with the reason, so every assertion is seen red on a checkout without the feature.
async function section(scheme, names, run) {
  try {
    await run();
  } catch (error) {
    if (scheme !== 'light') return;
    for (const name of names) if (!reported.has(name)) check(name, false, `could not run: ${String(error).split('\n')[0].slice(0, 120)}`);
  }
}

function startServer(args, extraEnv = {}) {
  const env = { ...process.env, ...extraEnv };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', ...args, '--port', '0', '--no-udev'],
    { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stopServer(child); reject(new Error(`server did not start: ${output}`)); }, 60000);
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
const stopServer = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };

async function onlyFixture(base, wanted) {
  const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
  const names = plugins.plugins.map((plugin) => plugin.name).sort().join();
  if (names !== wanted) throw new Error(`refusing to press anything: loaded ${names}, not only ${wanted}`);
}

async function open(page, base, sel) {
  await page.goto(`${base}/?sel=${encodeURIComponent(sel)}`);
  await page.waitForFunction((p) => typeof S !== 'undefined' && S.built && S.node && S.node.node.path === p && document.querySelector('#ent-util-btn'), sel, { timeout: 30000 });
  await page.waitForTimeout(400);
}
async function menu(page) {
  await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 15000 });
  if (!(await page.$('.ent-menu'))) await page.click('#ent-util-btn');
  await page.waitForSelector('.ent-menu [data-util]', { timeout: 5000 });
}
async function tab(page) {
  await page.click('[data-d6-tab="utilities"]');
  await page.waitForSelector('[data-d6-panel="utilities"] [data-util]', { timeout: 8000 });
  await page.waitForTimeout(200);
}
// every POST /api/v1/act the page sends, in order: {dry, token, body}
function recordActs(page) {
  const posts = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/v1/act')) {
      const body = JSON.parse(request.postData() || '{}');
      posts.push({ dry: Boolean(body.dry_run), real: Boolean(body.token) && !body.dry_run, body });
    }
  });
  return posts;
}
const runPage = (page) => page.evaluate(() => {
  const modal = document.querySelector('#modal:not([hidden]) .modal');
  if (!modal) return null;
  return { name: modal.dataset.runPage, title: modal.querySelector('h2').textContent, says: modal.querySelector('[data-run-says]').textContent,
    confirms: [...modal.querySelectorAll('.m-confirms li')].map((li) => li.textContent),
    buttons: [...modal.querySelectorAll('footer button')].map((button) => button.textContent), run: !modal.querySelector('[data-util-confirm]').disabled };
});
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, name) });

async function f1Checks(browser, base, scheme) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: scheme });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const posts = recordActs(page);
  const light = scheme === 'light';
  await open(page, base, CAN15);
  await menu(page);

  // ---- menu: two states only
  await section(scheme, ["menu: no \"can't tell\" in the menu", "menu: each item is its name, or greyed with one line of why", "menu: no recorded configuration is a greyed reason", "api: no action of any entity is unknown", "menu: \"All utilities…\" is the last item"], async () => {
    const items = await page.evaluate(() => [...document.querySelectorAll('.ent-menu [data-util]')].map((button) => ({
      name: button.dataset.util, disabled: button.disabled, title: button.querySelector('.u-title').textContent,
      lines: [...button.querySelectorAll('.u-reason, .u-busy')].map((el) => el.textContent.trim()),
      shown: (() => { const reason = button.querySelector('.u-reason'); return reason ? reason.getBoundingClientRect().height > 0 : false; })() })));
    const text = await page.evaluate(() => document.querySelector('.ent-menu').innerText);
    if (light) {
      check("menu: no \"can't tell\" in the menu", !/can't tell|can’t tell/i.test(text), text.slice(0, 120));
      const twoStates = items.every((item) => (item.disabled ? item.lines.length === 1 && item.lines[0].length > 0 && item.shown : item.lines.length === 0));
      check('menu: each item is its name, or greyed with one line of why', twoStates && items.some((i) => i.disabled) && items.some((i) => !i.disabled),
        items.map((item) => `${item.title}${item.disabled ? ` [${item.lines[0]}]` : ''}`));
      const greyed = items.find((item) => item.name === 'update_config');
      check('menu: no recorded configuration is a greyed reason', greyed && greyed.disabled && /no recorded configuration/.test(greyed.lines[0]), greyed);
      const states = await page.evaluate(async () => {
        const found = [];
        for (const entity of S.byPath.keys()) {
          const node = await (await fetch(`/api/v1/node?path=${encodeURIComponent(entity)}`)).json().catch(() => ({}));
          for (const action of node.actions || []) found.push({ entity, name: action.name, state: action.state, reason: action.reason });
        }
        return found;
      });
      const unknown = states.filter((item) => item.state === 'unknown' || /can't tell/.test(item.reason || ''));
      check('api: no action of any entity is unknown', states.length > 10 && unknown.length === 0, { actions: states.length, unknown });
      const last = await page.evaluate(() => { const all = [...document.querySelectorAll('.ent-menu .u-btn, .ent-menu .u-all')]; return all[all.length - 1].textContent.trim(); });
      check('menu: "All utilities…" is the last item', last === 'All utilities…', last);
    }
    await shot(page, `menu-${scheme}.png`);

  });
  // ---- "All utilities…" opens the Utilities tab
  await section(scheme, ["menu: \"All utilities…\" opens the Utilities tab"], async () => {
    await page.click('.ent-menu [data-util-all]');
    await page.waitForTimeout(400);
    const opened = await page.evaluate(() => ({ panel: (document.querySelector('.d6-panel') || {}).dataset && document.querySelector('.d6-panel').dataset.d6Panel, menu: Boolean(document.querySelector('.ent-menu')) }));
    if (light) check('menu: "All utilities…" opens the Utilities tab', opened.panel === 'utilities' && !opened.menu, opened);

  });
  // ---- the table
  await section(scheme, ["table: proper headers", "table: every column at one x in every row", "table: every row has an icon"], async () => {
    await page.waitForSelector('[data-d6-panel="utilities"] table');
    const table = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('[data-d6-panel="utilities"] table tr')];
      const lefts = rows.map((row) => [...row.children].map((cell) => Math.round(cell.getBoundingClientRect().left * 10) / 10));
      const columns = lefts[0].map((_, index) => lefts.map((row) => row[index]));
      return { heads: [...document.querySelectorAll('[data-d6-panel="utilities"] thead th')].map((th) => th.textContent.trim()), rows: rows.length - 1,
        spread: columns.map((xs) => Math.max(...xs) - Math.min(...xs)),
        icons: [...document.querySelectorAll('[data-d6-panel="utilities"] tbody tr')].map((row) => Boolean(row.querySelector('td:first-child svg.u-ic'))),
        menuIcons: [...document.querySelectorAll('[data-d6-panel="utilities"] tbody tr')].map((row) => row.querySelector('svg.u-ic').innerHTML) };
    });
    if (light) {
      check('table: proper headers', ['Utility', 'Effect', 'Status', 'Last run'].every((head) => table.heads.includes(head)), table.heads);
      check('table: every column at one x in every row', table.rows >= 5 && table.spread.every((spread) => spread < 0.5), table.spread);
      check('table: every row has an icon', table.icons.length === table.rows && table.icons.every(Boolean), table.icons);
    }
    await shot(page, `tab-${scheme}.png`);

  });
  // ---- the same Run page from the menu and from the tab, and one more click runs it
  await section(scheme, ["same page: menu and tab open the same Run page", "one step: the Run page says it, confirms it, Run / Cancel", "one step: nothing runs before Run", "one step: Run runs it (one real act, the token, detached)"], async () => {
    await page.click('[data-d6-panel="utilities"] [data-util="identify"]');
    await page.waitForSelector('#modal:not([hidden]) .modal');
    await page.waitForFunction(() => S.util && S.util.dry, null, { timeout: 15000 });
    const fromTab = await runPage(page);
    await shot(page, `identify-run-page-${scheme}.png`);
    await page.click('#modal [data-util-cancel]');
    await menu(page);
    const realBefore = posts.filter((post) => post.real).length;
    await page.click('.ent-menu [data-util="identify"]');
    await page.waitForSelector('#modal:not([hidden]) .modal');
    await page.waitForFunction(() => S.util && S.util.dry, null, { timeout: 15000 });
    const fromMenu = await runPage(page);
    await page.waitForTimeout(500);
    const realAfterOpen = posts.filter((post) => post.real).length;
    if (light) {
      check('same page: menu and tab open the same Run page', fromTab && fromMenu && JSON.stringify(fromTab) === JSON.stringify(fromMenu), { fromTab, fromMenu });
      check('one step: the Run page says it, confirms it, Run / Cancel', fromMenu && /wiggles about \+-5 degrees/.test(fromMenu.says) && fromMenu.confirms.length === 1
        && /Nothing is in the way/.test(fromMenu.confirms[0]) && fromMenu.buttons.join() === 'Cancel,Run' && fromMenu.run, fromMenu);
      check('one step: nothing runs before Run', realAfterOpen === realBefore, { realBefore, realAfterOpen });
    }
    await page.click('#modal [data-util-confirm]');
    await page.waitForTimeout(800);
    const real = posts.filter((post) => post.real);
    if (light) {
      check('one step: Run runs it (one real act, the token, detached)', real.length === realBefore + 1 && real[real.length - 1].body.action === 'identify'
        && real[real.length - 1].body.detach === true && !(await page.evaluate(() => !document.getElementById('modal').hidden)), real.map((post) => post.body.action));
    }

  });
  // ---- set_id: the new id is typed on its Run page
  await section(scheme, ["set_id: an id already on the bus keeps Run disabled", "set_id: a free id reads CAN 15 → CAN 7, the box keeps focus", "set_id: Run asks for the token and runs with the new id"], async () => {
    await page.waitForTimeout(800);
    await menu(page);
    await page.click('.ent-menu [data-util="set_id"]');
    await page.waitForSelector('#modal:not([hidden]) [data-util-newid]');
    await page.fill('#modal [data-util-newid]', '14');
    await page.waitForTimeout(200);
    const taken = await page.evaluate(() => ({ run: !document.querySelector('#modal [data-util-confirm]').disabled, says: document.querySelector('#modal .m-newid').textContent }));
    await page.fill('#modal [data-util-newid]', '7');
    await page.waitForTimeout(200);
    const free = await page.evaluate(() => ({ run: !document.querySelector('#modal [data-util-confirm]').disabled, says: document.querySelector('#modal .m-newid').textContent,
      focused: document.activeElement === document.querySelector('#modal [data-util-newid]') }));
    if (light) {
      check('set_id: an id already on the bus keeps Run disabled', !taken.run && /CAN 14 is already on this bus/.test(taken.says), taken);
      check('set_id: a free id reads CAN 15 → CAN 7, the box keeps focus', free.run && free.says === 'CAN 15 → CAN 7' && free.focused, free);
    }
    const before = posts.length;
    await page.click('#modal [data-util-confirm]');
    await page.waitForTimeout(1500);
    const idPosts = posts.slice(before);
    if (light) {
      check('set_id: Run asks for the token and runs with the new id', idPosts.length === 2 && idPosts[0].dry && idPosts[0].body.new_id === 7
        && idPosts[1].real && idPosts[1].body.new_id === 7, idPosts.map((post) => post.body));
    }

  });
  // ---- immediate: one click on the adapter
  await section(scheme, ["immediate: labels have no \"…\"", "immediate: Check communication runs on one click", "immediate: Open console from the tab runs on one click", "no page errors"], async () => {
    await open(page, base, ADAPTER);
    await menu(page);
    const labels = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.ent-menu [data-util]')].map((b) => [b.dataset.util, b.querySelector('.u-title').textContent])));
    const start = posts.length;
    let modalSeen = false;
    const watch = setInterval(() => { page.evaluate(() => !document.getElementById('modal').hidden).then((shown) => { modalSeen = modalSeen || shown; }).catch(() => {}); }, 30);
    await page.click('.ent-menu [data-util="check_comms"]');
    await page.waitForTimeout(250);
    await shot(page, `one-click-run-${scheme}.png`);
    await page.waitForFunction(() => S.util && ['running', 'done'].includes(S.util.phase), null, { timeout: 15000 });
    await page.waitForTimeout(600);
    clearInterval(watch);
    const comms = posts.slice(start);
    if (light) {
      check('immediate: labels have no "…"', labels.check_comms === 'Check communication' && labels.open_console === 'Open console', labels);
      check('immediate: Check communication runs on one click', comms.length === 2 && comms[0].dry && comms[1].real && comms[1].body.action === 'check_comms' && !modalSeen,
        { posts: comms.map((post) => (post.dry ? 'dry' : 'real')), modalSeen });
    }
    await page.waitForTimeout(1500);
    await tab(page);
    const consoleStart = posts.length;
    await page.click('[data-d6-panel="utilities"] [data-util="open_console"]');
    await page.waitForTimeout(1500);
    const consolePosts = posts.slice(consoleStart);
    if (light) {
      check('immediate: Open console from the tab runs on one click', consolePosts.length === 2 && consolePosts[1].real && consolePosts[1].body.action === 'open_console'
        && (await page.evaluate(() => document.getElementById('modal').hidden)), consolePosts.map((post) => post.body.action));
      check('no page errors', errors.length === 0, errors);
    }
  });
  await context.close();
}

async function walkChecks(browser, base, scheme) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: scheme });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const light = scheme === 'light';
  await section(scheme, ["walk: the adapter offers Identify (wiggle each board)", "walk: its Run page says press Enter, confirms the area", "walk: Run starts it in the terminal: Wiggling CAN ID 1", "walk: Enter in the terminal reaches it: on to CAN 14", "walk: it ends with the board last wiggled"], async () => {
    const find = (node, wanted) => (node.name === wanted ? node : node.children.map((child) => find(child, wanted)).find(Boolean));
    let can1;
    for (let i = 0; i < 120 && !can1; i += 1) {
      can1 = find((await (await fetch(`${base}/api/v1/state`)).json()).tree, 'CAN 1');
      if (!can1) await new Promise((resolve) => setTimeout(resolve, 500));
    }
    const adapter = can1.path.split('/can/')[0];
    await open(page, base, adapter);
    await menu(page);
    const label = await page.evaluate(() => (document.querySelector('.ent-menu [data-util="identify"] .u-title') || {}).textContent);
    await page.click('.ent-menu [data-util="identify"]');
    await page.waitForSelector('#modal:not([hidden]) .modal');
    await page.waitForFunction(() => S.util && S.util.dry, null, { timeout: 15000 });
    const sheet = await runPage(page);
    if (light) {
      check('walk: the adapter offers Identify (wiggle each board)', label === 'Identify (wiggle each board)…', label);
      check('walk: its Run page says press Enter, confirms the area', sheet && /press Enter to move on/.test(sheet.says) && /Nothing is in the way of any joint/.test(sheet.confirms.join()), sheet);
    }
    await page.click('#modal [data-util-confirm]');
    await page.waitForFunction(() => S.util && S.util.runId, null, { timeout: 15000 });
    const runId = await page.evaluate(() => S.util.runId);
    const terminal = (pattern) => page.waitForFunction(([id, source]) => {
      const bound = window.IORunViews.r1.s && window.IORunViews.r1.s.terminals.get(id);
      if (!bound) return false;
      const buffer = bound.term.buffer.active;
      let text = '';
      for (let i = 0; i < buffer.length; i += 1) text += `${buffer.getLine(i).translateToString(true)}\n`;
      return new RegExp(source).test(text);
    }, [runId, pattern.source], { timeout: 20000 }).then(() => true, () => false);
    const first = await terminal(/Wiggling CAN ID 1\b/);
    await page.waitForTimeout(400);
    await shot(page, `walk-terminal-${scheme}.png`);
    await page.click('#runs-root .xterm');
    await page.keyboard.press('Enter');
    const second = await terminal(/Wiggling CAN ID 14/);
    const advanced = await terminal(/CAN 1 stopped \(mode 0\) after Enter/);
    await page.waitForTimeout(300);
    if (light) {
      check('walk: Run starts it in the terminal: Wiggling CAN ID 1', first, runId);
      check('walk: Enter in the terminal reaches it: on to CAN 14', second && advanced, { second, advanced });
    }
    await shot(page, `walk-terminal-enter-${scheme}.png`);
    await page.keyboard.press('Enter');
    await terminal(/Wiggling CAN ID 15/);
    await page.keyboard.press('Enter');
    const done = await terminal(/last wiggled CAN 15; 3 of 3 wiggled/);
    if (light) check('walk: it ends with the board last wiggled', done, '');
    await page.waitForTimeout(800);
  });
  await context.close();
}

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'utilities-ux-'));
  const f1 = await startServer(['--fixture', 'F1'], { IO_DIAGNOSTICS_STATE_DIR: path.join(stateDir, 'f1') });
  const runs = await startServer(['--fixture', 'F2', '--plugins', path.join(ROOT, 'tests', 'fixtures', 'plugins-terminal')],
    { IO_DIAGNOSTICS_STATE_DIR: path.join(stateDir, 'runs'), TERMINAL_WALK_S: '60', TERMINAL_LOG: path.join(stateDir, 'terminal.jsonl') });
  try {
    await onlyFixture(f1.base, 'v6_scenario');
    await onlyFixture(runs.base, 'terminal_fixture');
    for (const scheme of ['light', 'dark']) {
      await f1Checks(browser, f1.base, scheme);
      await walkChecks(browser, runs.base, scheme);
    }
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    stopServer(f1.child);
    stopServer(runs.child);
    await browser.close();
  }
  console.log(`\n${passed}/${passed + failed} checks passed · screenshots in ${SHOTS}`);
  process.exit(failed ? 1 : 0);
}

main();
