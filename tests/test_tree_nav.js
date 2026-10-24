#!/usr/bin/env node
// Acceptance for the integrated picks' TREE work (Zach, 2026-10-24), in the REAL page on a fixture server, headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_tree_nav.js [--shots DIR]
//
// It starts its own `cli.py serve --fixture F1 --port 0 --no-udev --expected <temporary rig file>` (a free port, temporary state and
// config dirs, stopped by its process group) and refuses to go on unless /api/v1/plugins lists only the fixture plugin: nothing here
// touches hardware, and it never uses ports 8820-8822. Checked:
//   defaults     the page starts on layout L1, the Nested tree (p6), run view r1, chart view c6, entity page d6; the Layout picker still lists L0-L5
//   nested       the top level is the computer and the rig side by side, the rig holds its adapter and cameras, no section rows
//   roll-ups     a collapsed row's roll-up is ONE shape and no number (in the DOM, not only in the model); the header counts stay
//   group by     Nested · Physical · Type · Port · Bus · API tree · Purpose; Type draws one folder per kind and survives a reload
//   only inside  a row's menu adds a removable chip, the tree keeps that row's subtree and the way down to it, the URL and a reload keep it
//   step into    a row's menu / Ctrl+Shift+Enter re-roots the tree, the breadcrumb and the up button step out, Ctrl+Shift+Backspace too,
//                back / forward walk through it, the URL says where the tree starts, a reload keeps it
//   palette      Ctrl-K opens L4's command palette in L1; it jumps to a device and offers Step into
//   layout       the breadcrumb bar sits inside L1's tree pane under the search; no horizontal scroll at 1400x900 and 1280x720
//   errors       no page errors, no console errors
// Exits 1 on any failure.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const BIG = { width: 1400, height: 900 };
const SMALL = { width: 1280, height: 720 };
const args = process.argv.slice(2);
const SHOTS = args.includes('--shots') ? args[args.indexOf('--shots') + 1] : null;

const RIG = 'host/rig-1-dof-test-rack';
const ADAPTER = 'host/usb1/port-1-3/usb-1-3';
const CAMERA = 'host/usb6/port-6-1/usb-6-1';
const BOARD = `${ADAPTER}/can/14`;
const RIG_FILE = `version = 1

[[rig]]
name = "1-DOF test rack"

[[rig.device]]
name = "fdcanusb 272813EF"
kind = "adapter"
hardware_id = "usb:0483:5740:272813EF"
nodes = ["can/1", "can/14", "can/15"]

[[rig.device]]
name = "RealSense D405 218622276036"
kind = "camera"
hardware_id = "usb:8086:0b5b:219323070276"
nodes = []

[[rig.device]]
name = "RealSense D405 218622274411"
kind = "camera"
hardware_id = "usb:8086:0b5b:130523071221"
nodes = []
`;

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(74)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const stop = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };

function startServer(dir) {
  const rigFile = path.join(dir, 'expected.toml');
  fs.writeFileSync(rigFile, RIG_FILE);
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: path.join(dir, 'state'), IO_DIAGNOSTICS_CONFIG_DIR: path.join(dir, 'config') };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', 'F1',
    '--port', '0', '--no-udev', '--expected', rigFile], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stop(child); reject(new Error(`server did not start: ${output}`)); }, 60000);
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

// ---- helpers run in the page ----
const ready = async (page) => {
  await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.t6-row') && S.node, null, { timeout: 30000 });
  await page.waitForTimeout(500);
};
const rowNames = (page) => page.evaluate(() => [...document.querySelectorAll('#tree-host .t6-row:not(.t6-bundle)')].map((row) => ({ name: row.querySelector('.t6-name').textContent, depth: Number(row.style.getPropertyValue('--t6-d')) })));
const topNames = async (page) => (await rowNames(page)).filter((row) => row.depth === 0).map((row) => row.name);
const search = (page) => page.evaluate(() => location.search);
const navText = (page) => page.evaluate(() => { const bar = document.querySelector('#tree-host > .tree-nav'); return bar && !bar.hidden ? bar.innerText.replace(/\s+/g, ' ').trim() : ''; });
const rowOf = (page, text) => page.locator('#tree-host .t6-row', { hasText: text }).first();
// the row's ⋮ menu (a click event on the button: the pane is narrow and Playwright's own scroll-into-view would shift it sideways)
async function openMenu(page, text, wait = 'nav:into') {
  const row = rowOf(page, text);
  await row.hover();
  await row.locator('[data-t6-more]').dispatchEvent('click');
  await page.waitForSelector(`.t6-menu:not([hidden]) ${wait ? `[data-t6-mi="${wait}"]` : '[data-t6-mi]'}`, { timeout: 5000 });
}
async function menuPick(page, text, item) {
  await openMenu(page, text);
  await page.locator(`.t6-menu [data-t6-mi="${item}"]`).dispatchEvent('click');
  await page.waitForTimeout(700);
}
// Group by lives in View options (the popover); it is opened and closed through the page's own function, then the select is used
async function groupBy(page, id) {
  await page.evaluate(() => setViewOptions(true));
  await page.selectOption('#structure', id);
  await page.evaluate(() => setViewOptions(false));
  await page.waitForTimeout(700);
}
const hScroll = (page) => page.evaluate(() => ({ doc: document.documentElement.scrollWidth - innerWidth, body: document.body.scrollWidth - innerWidth }));
const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name) }); } };

async function main() {
  const { chromium } = require(PLAYWRIGHT);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'io-diagnostics-tree-nav-'));
  const server = await startServer(dir);
  const base = server.base;
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const errors = [];
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    check('only the fixture plugin is loaded (nothing touches hardware)', plugins.plugins.map((plugin) => plugin.name).join() === 'v6_scenario', plugins.plugins.map((p) => p.name));
    const state = await (await fetch(`${base}/api/v1/state`)).json();
    const rigs = state.expected_setup.rigs;
    check('the API says which rows belong to the rig (expected_setup.rigs[].members)', rigs.length === 1 && rigs[0].path === RIG && rigs[0].members.includes(ADAPTER) && rigs[0].members.includes(CAMERA), rigs);

    const context = await browser.newContext({ viewport: BIG, colorScheme: 'light' });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    await page.goto(`${base}/`);
    await ready(page);

    // ---- defaults ----
    const defaults = await page.evaluate(() => ({ layout: document.getElementById('layout-pick').value, layouts: [...document.querySelectorAll('#layout-pick option')].map((o) => o.value).join(),
      structure: S.structure, runView: S.runView, chart: document.documentElement.dataset.chart, detail: S.layout, tree: S.variant, l1: Boolean(document.querySelector('.l1-ribbon')) }));
    check('defaults: layout L1 (the Layout picker still lists L0-L5), Nested tree, run view r1, chart view c6, entity page d6',
      defaults.layout === 'l1' && defaults.layouts === 'l0,l1,l2,l3,l4,l5' && defaults.structure === 'p6' && defaults.runView === 'r1' && defaults.chart === 'c6' && defaults.detail === 'd6' && defaults.l1, defaults);
    check('defaults: the tabbed entity page is what the page shows', await page.evaluate(() => Boolean(document.querySelector('.d6-tabs'))));

    // ---- nested ----
    const top = await topNames(page);
    check('nested: the top level is the computer and the rig, side by side', top.length === 2 && top.includes('bam-GPU') && top.some((name) => /^1-DOF test rack/.test(name)), top);
    const names = await rowNames(page);
    const rigAt = names.findIndex((row) => /^1-DOF test rack/.test(row.name));
    const under = [];
    for (let at = rigAt + 1; at < names.length && names[at].depth > 0; at += 1) under.push(`${names[at].depth}:${names[at].name}`);
    check('nested: the rig holds its adapter (and its boards, one level deeper) and its two cameras', under.includes('1:fdcanusb · 272813EF') && under.includes('2:moteus-c1 · CAN 14')
      && under.filter((text) => /RealSense D405/.test(text)).length === 2, under);
    check('nested: no section rows (every row is an entity)', await page.evaluate(() => [...S.built.flat].every((row) => row.entity)));
    check('nested: the computer no longer lists what the rig holds', !(names.slice(0, rigAt).concat(names.slice(rigAt + 1 + under.length)).some((row) => /fdcanusb · 272813EF|RealSense D405/.test(row.name))), names.map((row) => row.name));
    await shot(page, 'l1-nested-light.png');

    // ---- roll-ups: one shape, no number ----
    await page.evaluate(() => { localStorage.removeItem('io-diagnostics.tree.open'); });
    const collapsed = await page.evaluate(() => {
      // close every open branch the page's own way (its chevrons), deepest first
      for (let pass = 0; pass < 5; pass += 1) for (const chevron of [...document.querySelectorAll('#tree-host .t6-chev.t6-open')].reverse()) chevron.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      return true;
    });
    await page.waitForTimeout(500);
    const rolls = await page.evaluate(() => [...document.querySelectorAll('#tree-host .t6-roll')].map((mark) => ({ text: mark.textContent.trim(), shapes: mark.querySelectorAll('svg').length, row: mark.closest('.t6-row').querySelector('.t6-name').textContent })));
    check('roll-ups: collapsed rows draw a roll-up, each ONE shape and no number', collapsed && rolls.length >= 1 && rolls.every((roll) => roll.shapes === 1 && !/\d/.test(roll.text)), rolls);
    const header = await page.evaluate(() => document.querySelector('#tree-host .t6-counts').innerText.replace(/\s+/g, ' ').trim());
    check('roll-ups: the header counts stay (they are the totals)', /\d/.test(header), header);

    // the rows were collapsed above (the page remembers that): open them again for what follows
    await page.evaluate(() => { localStorage.removeItem('io-diagnostics.tree.open'); });
    await page.reload();
    await ready(page);

    // ---- group by ----
    const groupNames = await page.evaluate(() => [...document.querySelectorAll('#structure option')].map((o) => o.textContent).join());
    check('group by: Nested · Physical · Type · Port · Bus · API tree · Purpose', groupNames === 'Nested,Physical,Type,Port,Bus,API tree,Purpose', groupNames);
    await groupBy(page, 'p7');
    const typed = await topNames(page);
    // the folders are rows like any other, so the page's Sort by (problems first) orders them: compared as a set
    check('group by Type: one folder per kind of top-level entity (Computers, Rigs, Adapters, Cameras ...), each once', ['Computers', 'Rigs', 'Adapters', 'Cameras'].every((name) => typed.includes(name)) && new Set(typed).size === typed.length, typed);
    await shot(page, 'l1-type-light.png');
    await page.reload();
    await ready(page);
    check('group by Type: remembered across a reload', (await topNames(page)).includes('Adapters') && (await page.evaluate(() => S.structure)) === 'p7');
    await groupBy(page, 'p1');
    const physical = await page.evaluate(() => ({ top: [...document.querySelectorAll('#tree-host .t6-row')].filter((r) => r.style.getPropertyValue('--t6-d') === '0').map((r) => r.querySelector('.t6-name').textContent),
      rigDepth: Number([...document.querySelectorAll('#tree-host .t6-row')].find((r) => /^1-DOF/.test(r.querySelector('.t6-name').textContent)).style.getPropertyValue('--t6-d')) }));
    check('group by Physical: the old tree, the computer is the one root and the rig a row under it', physical.top.length === 1 && physical.top[0] === 'bam-GPU' && physical.rigDepth === 1, physical);
    await groupBy(page, 'p6');

    // ---- only inside ----
    await menuPick(page, '1-DOF test rack', 'nav:inside');
    const chip = await page.evaluate(() => [...document.querySelectorAll('#tree-host .tn-chip')].map((c) => c.innerText.replace(/\s+/g, ' ').trim()));
    const inside = await topNames(page);
    check('only inside: the row menu adds a chip, the tree keeps the rig and what it holds', chip.length === 1 && /^Inside 1-DOF test rack/.test(chip[0]) && inside.length === 1 && /^1-DOF test rack/.test(inside[0])
      && (await rowNames(page)).some((row) => row.name === 'moteus-c1 · CAN 14') && !(await rowNames(page)).some((row) => row.name === 'bam-GPU'), { chip, inside });
    check('only inside: the URL says it, so a reload keeps it', /inside=host%2Frig-1-dof-test-rack/.test(await search(page)));
    await shot(page, 'l1-inside-light.png');
    await page.reload();
    await ready(page);
    check('only inside: kept across a reload', (await navText(page)).includes('Inside 1-DOF test rack') && (await topNames(page)).length === 1);
    await page.locator('#tree-host .tn-chip-x').first().dispatchEvent('click');
    await page.waitForTimeout(600);
    check('only inside: removing the chip brings the whole tree back and clears the URL', (await topNames(page)).length === 2 && !/inside=/.test(await search(page)) && (await navText(page)) === '');
    await menuPick(page, 'fdcanusb · 272813EF', 'nav:inside');
    const deep = (await rowNames(page)).map((row) => row.name);
    check('only inside: an adapter keeps its boards and the way down to it (rig > adapter), and drops the cameras beside it', deep.length === 5 && /^1-DOF test rack/.test(deep[0]) && deep[1] === 'fdcanusb · 272813EF'
      && deep.includes('moteus-c1 · CAN 14') && !deep.some((name) => /RealSense|bam-GPU/.test(name)), deep);
    await page.locator('#tree-host .tn-chip-x').first().dispatchEvent('click');
    await page.waitForTimeout(500);

    // ---- step into ----
    await menuPick(page, 'fdcanusb · 272813EF', 'nav:into');
    const stepped = { nav: await navText(page), rows: (await rowNames(page)).map((row) => `${row.depth}:${row.name}`), url: await search(page) };
    check('step into: the adapter is the top of the tree, its boards below it', stepped.rows[0] === '0:fdcanusb · 272813EF' && stepped.rows.includes('1:moteus-c1 · CAN 14') && stepped.rows.length === 4, stepped.rows);
    check('step into: the breadcrumb says where the tree starts (rig › adapter), with an up and a home button', /1-DOF test rack.*›.*fdcanusb · 272813EF/.test(stepped.nav)
      && await page.evaluate(() => Boolean(document.querySelector('.tree-nav [data-tn-up]') && document.querySelector('.tree-nav [data-tn-into=""]'))), stepped.nav);
    check('step into: the URL remembers it', /into=host%2Fusb1%2Fport-1-3%2Fusb-1-3/.test(stepped.url), stepped.url);
    await shot(page, 'l1-into-light.png');
    await page.reload();
    await ready(page);
    check('step into: kept across a reload', (await rowNames(page))[0].name === 'fdcanusb · 272813EF' && (await rowNames(page)).length === 4);
    await page.locator('.tree-nav [data-tn-up]').dispatchEvent('click');
    await page.waitForTimeout(600);
    check('step out: the up button goes to the parent (the rig), once', (await rowNames(page))[0].name.startsWith('1-DOF test rack') && /into=host%2Frig-1-dof-test-rack/.test(await search(page)), await search(page));
    await page.goBack();
    await page.waitForTimeout(800);
    check('step out: the browser Back returns to the adapter', (await rowNames(page))[0].name === 'fdcanusb · 272813EF', (await rowNames(page))[0]);
    await page.goForward();
    await page.waitForTimeout(800);
    check('step out: and Forward steps out again (back / forward walk the steps)', (await rowNames(page))[0].name.startsWith('1-DOF test rack'));
    await page.locator('.tree-nav [data-tn-into=""]').dispatchEvent('click');
    await page.waitForTimeout(600);
    check('step out: the home button shows the whole tree', (await topNames(page)).length === 2 && !/into=/.test(await search(page)) && (await navText(page)) === '');
    // keys: select the adapter row, Ctrl+Shift+Enter steps in, Ctrl+Shift+Backspace steps out
    await rowOf(page, 'fdcanusb · 272813EF').click();
    await page.waitForTimeout(500);
    await page.locator('#tree-host .t6-rows').focus();
    await page.keyboard.press('Control+Shift+Enter');
    await page.waitForTimeout(700);
    check('keys: Ctrl+Shift+Enter steps into the selected row', (await rowNames(page))[0].name === 'fdcanusb · 272813EF' && /into=/.test(await search(page)));
    await page.keyboard.press('Control+Shift+Backspace');
    await page.waitForTimeout(700);
    check('keys: Ctrl+Shift+Backspace steps back out (to the rig, one level up)', (await rowNames(page))[0].name.startsWith('1-DOF test rack'), (await rowNames(page))[0]);
    await page.keyboard.press('Control+Shift+Backspace');
    await page.waitForTimeout(700);
    check('keys: and once more to the whole tree', (await topNames(page)).length === 2);
    // a leaf has nothing to enter or filter by: its menu has neither item
    await openMenu(page, 'moteus-c1 · CAN 14', 'copy');
    const leafItems = await page.evaluate(() => [...document.querySelectorAll('.t6-menu [data-t6-mi]')].map((item) => item.dataset.t6Mi).filter((id) => id.startsWith('nav:')));
    check('step into: a board with nothing below it has neither Step into nor Show only what\'s inside (only a folder can be entered)', leafItems.length === 0, leafItems);
    await page.keyboard.press('Escape');
    // step into a folder row of the Type grouping (no entity behind it)
    await groupBy(page, 'p7');
    await menuPick(page, 'Adapters', 'nav:into');
    check('step into: a folder row of the Type grouping can be entered too (by its key)', (await rowNames(page))[0].name === 'Adapters' && /into=type%3Aadapters/.test(await search(page)), await search(page));
    await groupBy(page, 'p6');
    check('step into: a row the new grouping does not have drops the step quietly', (await topNames(page)).length === 2 && !/into=/.test(await search(page)), await search(page));

    // ---- palette ----
    await page.keyboard.press('Control+k');
    await page.waitForSelector('.lx-pal:not([hidden])', { timeout: 3000 }).catch(() => {});
    check('palette: Ctrl-K opens L4\'s command palette in L1', await page.evaluate(() => { const back = document.querySelector('.lx-pal-back'); return Boolean(back && !back.hidden); }));
    await page.keyboard.type('CAN 14');
    await page.waitForTimeout(400);
    const hit = await page.locator('.lx-pal [data-i]').first().innerText();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(900);
    check('palette: it jumps to the device it found', /CAN 14/.test(hit) && /CAN 14/.test(await page.locator('.d-title, .d6-title, .d6-head').first().innerText().catch(() => '')) && !(await page.evaluate(() => !document.querySelector('.lx-pal-back').hidden)), hit.split('\n')[0]);
    await page.keyboard.press('Control+k');
    await page.keyboard.type('step into');
    await page.waitForTimeout(300);
    const leafAction = await page.locator('.lx-pal [data-i]').first().innerText().catch(() => '');
    check('palette: a board (a leaf) is not offered Step into', !/Step into/.test(leafAction), leafAction);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Control+k');
    await page.keyboard.type('272813EF');
    await page.waitForTimeout(300);
    await page.locator('.lx-pal [data-i]', { hasText: 'fdcanusb · 272813EF' }).first().click();
    await page.waitForTimeout(900);
    await page.keyboard.press('Control+k');
    await page.keyboard.type('step into');
    await page.waitForTimeout(300);
    const action = await page.locator('.lx-pal [data-i]').first().innerText().catch(() => '');
    check('palette: it offers Step into for a selected adapter', /Step into .*272813EF/.test(action), action);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(800);
    check('palette: and doing it re-roots the tree at the adapter', (await rowNames(page))[0].name === 'fdcanusb · 272813EF' && /into=/.test(await search(page)), await search(page));
    await page.locator('.tree-nav [data-tn-into=""]').dispatchEvent('click').catch(() => {});
    await page.waitForTimeout(500);

    // ---- layout ----
    await menuPick(page, 'fdcanusb · 272813EF', 'nav:into');
    const bar = await page.evaluate(() => { const b = document.querySelector('#tree-host > .tree-nav').getBoundingClientRect(); const pane = document.querySelector('[data-lx-tree]').getBoundingClientRect(); const search = document.querySelector('[data-lx-tree] .search').getBoundingClientRect(); return { inside: b.left >= pane.left - 1 && b.right <= pane.right + 1 && b.top >= pane.top, belowSearch: b.top >= search.bottom - 1, sticky: getComputedStyle(document.querySelector('#tree-host > .tree-nav')).position }; });
    check('layout: the breadcrumb bar is inside the tree pane, under the search, and sticky', bar.inside && bar.belowSearch && bar.sticky === 'sticky', bar);
    check('layout: no horizontal scroll at 1400x900', (await hScroll(page)).doc <= 0 && (await hScroll(page)).body <= 0, await hScroll(page));
    await page.setViewportSize(SMALL);
    await page.waitForTimeout(600);
    check('layout: no horizontal scroll at 1280x720', (await hScroll(page)).doc <= 0 && (await hScroll(page)).body <= 0, await hScroll(page));
    await page.setViewportSize(BIG);
    await page.locator('.tree-nav [data-tn-into=""]').dispatchEvent('click');

    // ---- dark ----
    await page.evaluate(() => document.querySelector('#mode-switch [data-mode="dark"]').click());
    await page.waitForTimeout(500);
    await menuPick(page, 'fdcanusb · 272813EF', 'nav:into');
    await shot(page, 'l1-into-dark.png');
    const darkNav = await page.evaluate(() => { const style = getComputedStyle(document.querySelector('#tree-host > .tree-nav')); return { bg: style.backgroundColor, color: getComputedStyle(document.querySelector('.tn-here')).color }; });
    check('dark: the breadcrumb bar takes the skin\'s dark tokens (not a light bar)', /rgb\(\d+, \d+, \d+\)/.test(darkNav.bg) && darkNav.bg !== 'rgb(255, 255, 255)' && darkNav.color !== darkNav.bg, darkNav);
    check('no page errors, no console errors', errors.length === 0, errors.slice(0, 4));
    await context.close();
  } finally {
    await browser.close();
    stop(server.child);
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(`\n${passed}/${passed + failed} checks passed`);
  process.exitCode = failed ? 1 : 0;
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
