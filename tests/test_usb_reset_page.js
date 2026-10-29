#!/usr/bin/env node
// Acceptance for "Reset USB port" (docs/usb-reset.md) in the REAL page (web/index.html + app.js) on a fixture server,
// headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_usb_reset_page.js
//
// It starts its own `cli.py serve --fixture F1 --port 0` (a free port, stopped by its process group) with the host's reset
// module pointed at a FAKE sysfs / dev / proc tree and a FAKE usbreset under a temp dir, so no real device is ever touched,
// and refuses to press anything unless /api/v1/plugins lists only the fixture plugin v6_scenario. Checks:
//   offered     Reset USB port is in the Utilities menu of a USB device row, its port row and a hub, under Maintenance with the
//               plain word `interrupts`, and not on a bus row
//   run page    (2026-10-29: one Run page, no dry-run sheet, no box to tick) the exact command (the fake usbreset, by bus/device
//               number) under "How it runs", the confirm text naming the device and warning that tview, a camera stream or an
//               experiment loses it
//   holders     a process with the device open is what pressing Run confirms ("tview (pid 4242) will lose ..."); nothing runs
//               before Run; Run goes through the fake tool exactly once, with confirm_holders
//   privilege   a device whose node this user cannot write: Reset USB port… is greyed with why, the Utilities tab's "How to allow
//               it" shows the udev rule for THAT vendor:product, and the fake tool never ran
//   hub         a hub's Run page names a holder of something plugged in behind it
// Screenshots: reports/media/usb-reset/*.png (sheets light and dark, the modal). Exits 1 on any failed check.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'usb-reset');
const VIEWPORT = { width: 1600, height: 1000 };
const ADAPTER = 'host/usb1/port-1-3/usb-1-3'; // fdcanusb 0483:5740, writable node, held by "tview"
const ADAPTER_PORT = 'host/usb1/port-1-3';
const LOCKED = 'host/usb1/port-1-5/usb-1-5'; // fdcanusb whose node this user cannot write
const HUB = 'host/usb7/port-7-1/usb-7-1'; // a hub with a webcam behind it that "ffplay" has open

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(48)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

// ----------------------------------------------------------------------------------- the fake machine ----
const FAKE_TOOL = `#!/usr/bin/env python3
import os, sys
root = os.environ["FAKE_USB_ROOT"]
open(os.path.join(root, "calls.log"), "a").write(" ".join(sys.argv[1:]) + "\\n")
print("fake usbreset: resetting", sys.argv[1:], flush=True)
`;

function port(root, name, bus, devnum, vid, pid, product, interfaces = {}) {
  const directory = path.join(root, 'sys', name);
  fs.mkdirSync(directory, { recursive: true });
  for (const [file, value] of Object.entries({ busnum: bus, devnum, idVendor: vid, idProduct: pid, product, authorized: '1' })) {
    fs.writeFileSync(path.join(directory, file), `${value}\n`);
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
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'usb-reset-page-'));
  fs.mkdirSync(path.join(root, 'bin'), { recursive: true });
  fs.mkdirSync(path.join(root, 'proc'), { recursive: true });
  fs.writeFileSync(path.join(root, 'bin', 'usbreset'), FAKE_TOOL, { mode: 0o755 });
  fs.writeFileSync(path.join(root, 'calls.log'), '');
  port(root, '1-3', 1, 2, '0483', '5740', 'fdcanusb', { tty: 'ttyACM0' });
  port(root, '1-5', 1, 8, '0483', '5740', 'fdcanusb', { tty: 'ttyACM1' });
  port(root, '6-1', 6, 2, '8086', '0b5b', 'Intel(R) RealSense(TM) Depth Camera 405');
  port(root, '7-1', 7, 2, '1a40', '0101', 'USB2.0 HUB');
  port(root, '7-1.1', 7, 3, '05e3', '0610', 'USB2.1 Hub');
  port(root, '7-1.1.1', 7, 4, '046d', '08b6', 'Logi Webcam C920e', { video4linux: 'video12' });
  fs.chmodSync(path.join(root, 'dev', 'bus', 'usb', '001', '008'), 0o444); // 1-5: this user cannot open its node
  holder(root, 4242, 'tview', 'ttyACM0');
  holder(root, 5151, 'ffplay', 'video12');
  return root;
}

function startServer(root, stateDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, FAKE_USB_ROOT: root,
    IO_DIAGNOSTICS_USB_SYSFS: path.join(root, 'sys'), IO_DIAGNOSTICS_DEV_ROOT: path.join(root, 'dev'), IO_DIAGNOSTICS_PROC: path.join(root, 'proc'),
    IO_DIAGNOSTICS_USBRESET: path.join(root, 'bin', 'usbreset'), IO_DIAGNOSTICS_UHUBCTL: path.join(root, 'no-uhubctl'),
    IO_DIAGNOSTICS_RESET_WAIT_S: '3' };
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
// Reset USB port…'s Run page (2026-10-29: the one step between the click and the run), once its dry run has answered
async function sheet(page) {
  await menu(page);
  await page.click('[data-util="reset_port"]');
  await page.waitForSelector('#modal:not([hidden]) [data-run-page="reset_port"]', { timeout: 20000 });
  await page.waitForFunction(() => S.util && S.util.dry, null, { timeout: 20000 });
  await page.waitForTimeout(300);
  await page.mouse.move(5, 995);
  return page.evaluate(() => ({
    cmd: (document.querySelector('#modal .u-cmd') || {}).textContent || '',
    confirm: (document.querySelector('#modal [data-run-says]') || {}).textContent || '',
    confirms: [...document.querySelectorAll('#modal .m-confirms li')].map((li) => li.textContent),
    run: (() => { const button = document.querySelector('#modal [data-util-confirm]'); return button ? { text: button.textContent, disabled: button.disabled } : null; })(),
  }));
}
// a reset_port that cannot run: its greyed menu line, and the Utilities tab's "How to allow it" opened
async function blocked(page) {
  await menu(page);
  const item = await page.evaluate(() => { const button = document.querySelector('.ent-menu [data-util="reset_port"]');
    return { disabled: button.disabled, reason: (button.querySelector('.u-reason') || {}).textContent || '' }; });
  await page.keyboard.press('Escape');
  await page.click('[data-d6-tab="utilities"]');
  await page.waitForSelector('[data-util-row="reset_port"] [data-d6-fix]', { timeout: 10000 });
  await page.click('[data-util-row="reset_port"] [data-d6-fix]');
  await page.waitForSelector('[data-util-row="reset_port"] pre.d6-fix');
  return { ...item, fix: await page.textContent('[data-util-row="reset_port"] pre.d6-fix'),
    button: await page.evaluate(() => document.querySelector('[data-util-row="reset_port"] [data-util]').disabled) };
}
const shot = async (page, name) => page.screenshot({ path: path.join(SHOTS, name), clip: await page.locator('#inspector').boundingBox() });

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const root = fakeMachine();
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'usb-reset-state-'));
  const { chromium } = require(PLAYWRIGHT);
  // d6 (the entity page): a greyed reset's "How to allow it" lives in its Utilities tab since 2026-10-29
  const browser = require('./pin_layout').pinDetailLayout(await chromium.launch({ executablePath: CHROME, headless: true }), 'd6');
  const server = await startServer(root, stateDir);
  const base = server.base;
  const calls = () => fs.readFileSync(path.join(root, 'calls.log'), 'utf8').split('\n').filter(Boolean);
  const tool = path.join(root, 'bin', 'usbreset');
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    const names = plugins.plugins.map((plugin) => plugin.name);
    check('only the fixture plugin is loaded', names.join() === 'v6_scenario', names);
    if (names.join() !== 'v6_scenario') throw new Error('refusing to press anything: a non-fixture plugin is loaded');

    // ---- dark first: the sheets, before the real run moves a device number
    const dark = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'dark' });
    const darkPage = await dark.newPage();
    await open(darkPage, base, ADAPTER);
    await sheet(darkPage);
    await shot(darkPage, 'sheet-holder-dark.png');
    await darkPage.keyboard.press('Escape');
    await open(darkPage, base, LOCKED);
    await blocked(darkPage);
    await shot(darkPage, 'sheet-blocked-dark.png');
    await dark.close();

    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
    const page = await context.newPage();
    const posts = [];
    page.on('request', (request) => { if (request.method() === 'POST' && request.url().endsWith('/api/v1/act')) posts.push(JSON.parse(request.postData() || '{}')); });

    // ---- offered: where, and where not
    await open(page, base, ADAPTER);
    await menu(page);
    const offered = await page.evaluate(() => {
      const button = document.querySelector('.ent-menu [data-util="reset_port"]');
      return button ? { title: button.querySelector('.u-title').textContent, danger: button.querySelector('.danger').textContent, group: button.closest('.ugroup').className,
        word: getComputedStyle(button.querySelector('.danger')).backgroundColor } : null;
    });
    check('offered: on a USB device, under Maintenance', offered && offered.title === 'Reset USB port…' && offered.danger === 'interrupts' && /ugroup-maintenance/.test(offered.group)
      && offered.word === 'rgba(0, 0, 0, 0)', offered);
    await shot(page, 'menu.png');
    await page.keyboard.press('Escape');
    for (const [label, sel] of [['its port row', ADAPTER_PORT], ['a hub', HUB]]) {
      await open(page, base, sel);
      await menu(page);
      check(`offered: on ${label}`, Boolean(await page.$('.ent-menu [data-util="reset_port"]')));
    }
    await open(page, base, 'host/usb1');
    const bus = await page.evaluate(() => ({ disabled: document.getElementById('ent-util-btn').disabled, actions: (S.node.actions || []).map((a) => a.name) }));
    check('offered: not on a bus row', bus.disabled && !bus.actions.includes('reset_port'), bus);

    // ---- the Run page of a device something holds: pressing Run confirms who loses it, then the run (2026-10-29: one step)
    await open(page, base, ADAPTER);
    const dry = await sheet(page);
    check('run page: the exact command, by bus/device number', dry.cmd === `${tool} 001/002`, dry.cmd);
    check('run page: the confirm names the device and who loses it', /fdcanusb 272813EF/.test(dry.confirm) && /tview, a camera stream, an experiment/.test(dry.confirm) && /1-3/.test(dry.confirm), dry.confirm);
    check('run page: Run confirms the holder, enabled', dry.confirms.some((text) => /^tview \(pid 4242\) will lose fdcanusb 272813EF$/.test(text)) && dry.run && dry.run.text === 'Run' && !dry.run.disabled, dry);
    check('run page: one POST, a dry run, and the tool never ran', posts.length === 1 && posts[0].dry_run === true && posts[0].action === 'reset_port' && calls().length === 0, posts);
    await shot(page, 'sheet-holder-light.png');
    await page.screenshot({ path: path.join(SHOTS, 'modal-light.png') });
    await page.click('#modal [data-util-confirm]');
    await page.waitForSelector('.ent-run.ok, .ent-run.fail', { timeout: 30000 });
    const run = await page.evaluate(() => ({ line: document.querySelector('.ent-run').textContent, fail: document.querySelector('.ent-run').classList.contains('fail') }));
    check('run: the second POST carries the confirmation, the fake tool ran once', posts.length === 2 && posts[1].confirm_holders === true && posts[1].detach === true && !posts[1].dry_run
      && calls().join() === '001/002', { posts: posts.map((post) => Object.keys(post).join()), calls: calls(), run });
    check('run: the line says what happened', !run.fail && /^fdcanusb 272813EF at 1-3 reset with usbreset: (back after [\d.]+ s as device \d+|reset in place)/.test(run.line), run);

    // ---- no privilege: greyed, with the rule for THAT device in the Utilities tab
    await open(page, base, LOCKED);
    const locked = await blocked(page);
    check('privilege: greyed in the menu, and says why', locked.disabled && /no way to reset it as your user/.test(locked.reason) && locked.button, locked);
    check('privilege: the udev rule for this vendor:product', /ATTR\{idVendor\}=="0483", ATTR\{idProduct\}=="5740", GROUP="plugdev", MODE="0664"/.test(locked.fix)
      && /\/etc\/udev\/rules\.d\/70-robotdx-usb-reset\.rules/.test(locked.fix) && /udevadm trigger/.test(locked.fix), locked.fix);
    check('privilege: the tool never ran for it', calls().join() === '001/002', calls());
    await shot(page, 'sheet-blocked-light.png');

    // ---- a hub: the holder is behind it
    await open(page, base, HUB);
    const hub = await sheet(page);
    check('hub: a holder behind it is named', hub.confirms.some((text) => /ffplay \(pid 5151\)/.test(text)) && /A hub takes everything/.test(hub.confirm), hub);
    await shot(page, 'sheet-hub-light.png');
    await context.close();
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
