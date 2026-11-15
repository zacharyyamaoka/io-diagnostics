#!/usr/bin/env node
// Acceptance for the host's "Reset…" (docs/device-reset.md) in the REAL page (web/index.html + app.js) on a fixture server,
// headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_device_reset_page.js
//
// It starts its own `cli.py serve --fixture F1 --port 0` (a free port, stopped by its process group) with the reset modules pointed
// at a FAKE sysfs / dev / proc tree and a FAKE fdcanusb-reset under a temp dir, and refuses to press anything unless
// /api/v1/plugins lists only the fixture plugin v6_scenario. It never presses Run: every check reads the Run page, which only asks
// for the dry run. Checks:
//   offered     Reset… is in the Utilities menu of a USB device row, under Maintenance with the plain word `interrupts`, and of a
//               CAN board below the adapter; not on the port row (which keeps Reset USB port) nor on a bus row
//   ladder      the Run page lists the recovery module's ladder rung by rung, in its order: each rung's title, `runs` or
//               `skipped` and why it is skipped, its why and its expect; only one POST, the dry run
//   holders     a process with the adapter open is what pressing Run confirms ("tview (pid 4242) will lose ...")
//   board       a board's Run page names the board on its adapter and lists the board's own rungs
// Screenshots: reports/media/device-reset/*.png (the Run page light and dark, the board's, the menu). Exits 1 on any failed check.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'device-reset');
const VIEWPORT = { width: 1600, height: 1000 };
const ADAPTER = 'host/usb1/port-1-3/usb-1-3'; // fdcanusb 272813EF, writable node, held by "tview"
const ADAPTER_PORT = 'host/usb1/port-1-3';
const BOARD = `${ADAPTER}/can/1`;

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(48)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

// ----------------------------------------------------------------------------------- the fake machine ----
// WHY a fake fdcanusb-reset that fails loudly: the page only ever asks for the dry run, which looks the tool up and never runs it
const FAKE_TOOL = '#!/bin/sh\necho "fake fdcanusb-reset must not run in this test" >&2\nexit 9\n';

function port(root, name, bus, devnum, vid, pid, product, serial, interfaces = {}) {
  const directory = path.join(root, 'sys', name);
  fs.mkdirSync(directory, { recursive: true });
  for (const [file, value] of Object.entries({ busnum: bus, devnum, idVendor: vid, idProduct: pid, product, serial, authorized: '1' })) {
    if (value !== '') fs.writeFileSync(path.join(directory, file), `${value}\n`);
  }
  fs.chmodSync(path.join(directory, 'authorized'), 0o444); // root only, as on a real machine
  const node = path.join(root, 'dev', 'bus', 'usb', String(bus).padStart(3, '0'));
  fs.mkdirSync(node, { recursive: true });
  fs.writeFileSync(path.join(node, String(devnum).padStart(3, '0')), '');
  for (const [kind, device] of Object.entries(interfaces)) {
    fs.mkdirSync(path.join(directory, `${name}:1.0`, kind, device), { recursive: true });
    fs.writeFileSync(path.join(root, 'dev', device), '');
  }
}

function holder(root, pid, program, device) {
  const directory = path.join(root, 'proc', String(pid));
  fs.mkdirSync(path.join(directory, 'fd'), { recursive: true });
  fs.symlinkSync(path.join(root, 'dev', device), path.join(directory, 'fd', '3'));
  fs.writeFileSync(path.join(directory, 'cmdline'), `python3\0/home/bam/bin/${program}\0`);
  fs.writeFileSync(path.join(directory, 'comm'), 'python3\n');
  fs.writeFileSync(path.join(directory, 'stat'), `${pid} (python3) S 1 ${pid} ${pid} 0 0\n`);
}

function fakeMachine() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'device-reset-page-'));
  fs.mkdirSync(path.join(root, 'bin'), { recursive: true });
  fs.mkdirSync(path.join(root, 'proc'), { recursive: true });
  fs.writeFileSync(path.join(root, 'bin', 'fdcanusb-reset'), FAKE_TOOL, { mode: 0o755 });
  port(root, '1-3', 1, 2, '0483', '5740', 'fdcanusb', '272813EF', { tty: 'ttyACM0' });
  port(root, '6-1', 6, 2, '8086', '0b5b', 'Intel(R) RealSense(TM) Depth Camera 405', '218622274411');
  holder(root, 4242, 'tview', 'ttyACM0');
  return root;
}

function startServer(root, stateDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir,
    IO_DIAGNOSTICS_USB_SYSFS: path.join(root, 'sys'), IO_DIAGNOSTICS_DEV_ROOT: path.join(root, 'dev'), IO_DIAGNOSTICS_PROC: path.join(root, 'proc'),
    IO_DIAGNOSTICS_USBRESET: path.join(root, 'bin', 'no-usbreset'), IO_DIAGNOSTICS_UHUBCTL: path.join(root, 'no-uhubctl'),
    IO_DIAGNOSTICS_FDCANUSB_RESET: path.join(root, 'bin', 'fdcanusb-reset'), IO_DIAGNOSTICS_HUB_POWER_FACTS: path.join(root, 'no-hub-facts.txt'),
    IO_DIAGNOSTICS_RACK_BUS_LOCK: path.join(root, 'rack-bus.lock') };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--fixture', 'F1', '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
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

// ----------------------------------------------------------------------------------- the page ----
async function open(page, base, sel) {
  await page.goto(`${base}/?sel=${encodeURIComponent(sel)}`);
  await page.waitForFunction((p) => typeof S !== 'undefined' && S.built && S.node && S.node.node.path === p && document.querySelector('.d-title'), sel, { timeout: 30000 });
  await page.waitForTimeout(400);
}
async function menu(page) {
  await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 15000 });
  if (!(await page.$('.ent-menu'))) await page.click('#ent-util-btn');
  await page.waitForSelector('.ent-menu [data-util]', { timeout: 5000 });
}
// Reset…'s Run page, once its dry run has answered: what it says, and its ladder as drawn
async function sheet(page) {
  await menu(page);
  await page.click('.ent-menu [data-util="reset"]');
  await page.waitForSelector('#modal:not([hidden]) [data-run-page="reset"]', { timeout: 20000 });
  await page.waitForFunction(() => S.util && S.util.dry && document.querySelector('#modal [data-ladder]'), null, { timeout: 30000 });
  await page.waitForTimeout(300);
  await page.mouse.move(5, 995);
  return page.evaluate(() => ({
    confirm: (document.querySelector('#modal [data-run-says]') || {}).textContent || '',
    confirms: [...document.querySelectorAll('#modal .m-confirms li')].map((li) => li.textContent),
    danger: (document.querySelector('#modal header .danger') || {}).textContent || '',
    rungs: [...document.querySelectorAll('#modal [data-ladder] li')].map((li) => ({ kind: li.dataset.rung, runs: li.dataset.runs,
      title: (li.querySelector('.u-r-t') || {}).textContent || '', tag: (li.querySelector('.tag') || {}).textContent || '',
      skip: (li.querySelector('.u-r-skip') || {}).textContent || '', lines: [...li.querySelectorAll('.u-r-l')].map((line) => line.textContent),
      how: (li.querySelector('code') || {}).textContent || '', color: getComputedStyle(li).color })),
    plan: (S.util.dry.result.action_result || {}).steps || [],
    run: (() => { const button = document.querySelector('#modal [data-util-confirm]'); return button ? { text: button.textContent, disabled: button.disabled } : null; })(),
    fits: (() => { const box = document.querySelector('#modal .modal').getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight; })(),
  }));
}
const shot = async (page, name) => page.screenshot({ path: path.join(SHOTS, name) });

function ladderMatchesPlan(drawn) {
  return drawn.rungs.length === drawn.plan.length && drawn.plan.length > 2 && drawn.rungs.every((rung, index) => {
    const step = drawn.plan[index];
    return rung.kind === step.kind && rung.title === step.title && rung.tag === (step.runs ? 'runs' : 'skipped')
      && rung.lines[0] === `Why${step.why}` && rung.lines[1] === `Expect${step.expect}` && (step.runs || rung.skip === step.skip_why);
  });
}

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const root = fakeMachine();
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'device-reset-state-'));
  const { chromium } = require(PLAYWRIGHT);
  const browser = require('./pin_layout').pinDetailLayout(await chromium.launch({ executablePath: CHROME, headless: true }), 'd6');
  const server = await startServer(root, stateDir);
  const base = server.base;
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    const names = plugins.plugins.map((plugin) => plugin.name);
    check('only the fixture plugin is loaded', names.join() === 'v6_scenario', names);
    if (names.join() !== 'v6_scenario') throw new Error('refusing to press anything: a non-fixture plugin is loaded');

    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
    const page = await context.newPage();
    const posts = [];
    page.on('request', (request) => { if (request.method() === 'POST' && request.url().endsWith('/api/v1/act')) posts.push(JSON.parse(request.postData() || '{}')); });

    // ---- offered: where, and where not
    await open(page, base, ADAPTER);
    await menu(page);
    const offered = await page.evaluate(() => {
      const button = document.querySelector('.ent-menu [data-util="reset"]');
      return button ? { title: button.querySelector('.u-title').textContent, danger: button.querySelector('.danger').textContent, group: button.closest('.ugroup').className,
        disabled: button.disabled, port: Boolean(document.querySelector('.ent-menu [data-util="reset_port"]')) } : null;
    });
    check('offered: on a USB device, under Maintenance', offered && offered.title === 'Reset…' && offered.danger === 'interrupts'
      && /ugroup-maintenance/.test(offered.group) && !offered.disabled && offered.port, offered);
    await page.screenshot({ path: path.join(SHOTS, 'menu-light.png'), clip: await page.locator('#inspector').boundingBox() });
    await page.keyboard.press('Escape');
    await open(page, base, ADAPTER_PORT);
    const portRow = await page.evaluate(() => (S.node.actions || []).map((a) => a.name));
    check('offered: not on its port row (Reset USB port stays)', !portRow.includes('reset') && portRow.includes('reset_port'), portRow);
    await open(page, base, 'host/usb1');
    const bus = await page.evaluate(() => (S.node.actions || []).map((a) => a.name));
    check('offered: not on a bus row', !bus.includes('reset'), bus);

    // ---- the adapter's Run page: the ladder, rung by rung, and who loses it
    await open(page, base, ADAPTER);
    const dry = await sheet(page);
    check('ladder: every rung of the plan, in order, as the plan says', ladderMatchesPlan(dry), dry.rungs.map((rung) => `${rung.kind}:${rung.tag}`));
    const byKind = Object.fromEntries(dry.rungs.map((rung) => [rung.kind, rung]));
    check('ladder: the fdcanusb\'s own reset runs, with how', byKind.native && byKind.native.runs === 'yes' && /fdcanusb-reset --serial 272813EF/.test(byKind.native.how), byKind.native);
    check('ladder: a skipped rung says why, muted', byKind.reopen && byKind.reopen.runs === 'no' && /already reopened/.test(byKind.reopen.skip)
      && byKind.authorized && /root only/.test(byKind.authorized.skip) && byKind.reopen.color !== byKind.native.color, { reopen: byKind.reopen, authorized: byKind.authorized });
    check('ladder: the confirm is the plan\'s, naming the device', /^Reset fdcanusb 272813EF\?/.test(dry.confirm) && /fdcanusb-reset/.test(dry.confirm), dry.confirm);
    check('holders: Run confirms that tview loses it, enabled', dry.confirms.some((text) => /^tview \(pid 4242\) will lose fdcanusb 272813EF$/.test(text))
      && dry.run && dry.run.text === 'Run' && !dry.run.disabled, dry.confirms);
    check('the page fits the window', dry.fits);
    check('one POST, the dry run', posts.length === 1 && posts[0].dry_run === true && posts[0].action === 'reset', posts);
    await shot(page, 'run-page-adapter-light.png');
    await page.click('#modal [data-util-cancel]');

    // ---- a CAN board below the adapter
    await open(page, base, BOARD);
    await menu(page);
    const boardItem = await page.evaluate(() => { const button = document.querySelector('.ent-menu [data-util="reset"]');
      return button ? { title: button.querySelector('.u-title').textContent, disabled: button.disabled } : null; });
    check('offered: on a CAN board below the adapter', boardItem && boardItem.title === 'Reset…' && !boardItem.disabled, boardItem);
    await page.keyboard.press('Escape');
    const board = await sheet(page);
    check('board: the plan is the board\'s, on its adapter', ladderMatchesPlan(board) && /^Reset CAN 1 on fdcanusb 272813EF\?/.test(board.confirm)
      && board.rungs.some((rung) => rung.kind === 'native' && /d reset/.test(rung.title)), { confirm: board.confirm, rungs: board.rungs.map((rung) => `${rung.kind}:${rung.tag}`) });
    check('board: its node rides on the dry run', posts.length === 2 && posts[1].path === BOARD && posts[1].dry_run === true, posts[1]);
    await shot(page, 'run-page-board-light.png');
    await page.click('#modal [data-util-cancel]');
    await context.close();

    const dark = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'dark' });
    const darkPage = await dark.newPage();
    await open(darkPage, base, ADAPTER);
    await sheet(darkPage);
    await shot(darkPage, 'run-page-adapter-dark.png');
    await darkPage.click('#modal [data-util-cancel]');
    await dark.close();
  } catch (error) {
    check('no unexpected error', false, String(error && error.stack || error));
  } finally {
    stopServer(server.child);
    await browser.close();
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(stateDir, { recursive: true, force: true });
  }
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main();
