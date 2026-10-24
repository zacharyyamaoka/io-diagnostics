// Shared by tests/test_entity_page.js and docs/record_entity_hero.js: a fixture host on a scratch port with the state fixture plugins
// (tests/fixtures/plugins-state: a joint at 10 Hz and a camera with colour and depth frames), the page, and small helpers.
//
// The host it starts is `cli.py serve --fixture F1|F2 --plugins <copy of plugins-state, period 2 s> --port 0 --no-udev` with its own
// state and config directories (so the arrangement file, the run history and the event log are the test's), and the fixture
// "board" (its live and stored settings, its mode) in files the test can read and write. It never touches hardware: the plugin is
// stdlib python that only reads and writes those files. Stopped by its process group.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'entity-page');
const VIEWPORT = { width: 1400, height: 900 };
const SOURCE = path.join(ROOT, 'tests', 'fixtures', 'plugins-state');
const BOARD = 'host/usb1/port-1-3/usb-1-3/can/1';       // the joint: CAN 1 on the fdcanusb in port 1-3
const BOARD2 = 'host/usb1/port-1-5/usb-1-5/can/1';      // a second board of the same kind (a second fdcanusb)
const CAMERA = 'host/usb6/port-6-1/usb-6-1';            // a RealSense-like camera on USB 3
const ADAPTER = 'host/usb1/port-1-3/usb-1-3';

const tally = { passed: 0, failed: 0 };
function check(name, ok, detail = '') {
  if (ok) tally.passed += 1; else tally.failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(64)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}
function summary(extra = '') {
  console.log(`\n${tally.passed}/${tally.passed + tally.failed} checks passed${extra}`);
  return tally.failed ? 1 : 0;
}

// a scratch copy of the plugin directory, so a test may change the declaration (the periodic diagnose is 2 s, not 30)
function pluginDir(root, periodS = 2) {
  const dir = path.join(root, 'plugins');
  fs.mkdirSync(dir, { recursive: true });
  for (const name of ['state_joint.toml', 'state_camera.toml']) {
    const text = fs.readFileSync(path.join(SOURCE, name), 'utf8')
      .replace('{here}', SOURCE).replace(/period_s = \d+/, `period_s = ${periodS}`);
    fs.writeFileSync(path.join(dir, name), text);
  }
  return dir;
}

function startServer(root, fixture = 'F1', extraEnv = {}) {
  const board = { file: path.join(root, 'board.json'), mode: path.join(root, 'mode'), log: path.join(root, 'plugin.log') };
  if (!fs.existsSync(board.mode)) fs.writeFileSync(board.mode, '10');
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: path.join(root, 'state'), IO_DIAGNOSTICS_CONFIG_DIR: path.join(root, 'config'),
    STATE_CONFIG_FILE: board.file, STATE_MODE_FILE: board.mode, STATE_LOG: board.log, ...extraEnv };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', fixture,
    '--plugins', pluginDir(root), '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stopServer(child); reject(new Error(`server did not start: ${output}`)); }, 60000);
    const onData = (chunk) => {
      output += chunk;
      const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//);
      if (found) { clearTimeout(timer); resolve({ child, base: found[1], board, root }); }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`server exited (${code}): ${output}`)); });
  });
}
const stopServer = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };

async function fixtureHost(fixture = 'F1', extraEnv = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'entity-page-'));
  const server = await startServer(root, fixture, extraEnv);
  const plugins = await (await fetch(`${server.base}/api/v1/plugins`)).json();
  const names = plugins.plugins.map((plugin) => plugin.name).sort().join();
  if (names !== 'state_camera,state_joint') { stopServer(server.child); throw new Error(`refusing to go on: loaded ${names}, not only the two fixture plugins`); }
  for (let i = 0; i < 120; i += 1) {
    const node = await fetch(`${server.base}/api/v1/node?path=${encodeURIComponent(BOARD)}`).then((r) => r.json()).catch(() => ({}));
    if ((node.config || []).length) return server;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  stopServer(server.child);
  throw new Error('the board never reported its configuration');
}

const getJson = async (base, url) => (await fetch(`${base}${url}`)).json();
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const waitFor = async (fn, ms = 10000, step = 150) => { const end = Date.now() + ms; for (;;) { const found = await fn(); if (found) return found; if (Date.now() > end) return false; await wait(step); } };

async function newContext(browser, colorScheme = 'light', extra = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme, ...extra });
  return context;
}
async function ready(page) {
  await page.waitForFunction(() => typeof S !== 'undefined' && S.built && S.node && document.querySelector('.d6-tabs'), null, { timeout: 30000 });
  await page.waitForTimeout(300);
}
async function open(page, base, sel, tab) {
  await page.goto(`${base}/?sel=${encodeURIComponent(sel)}${tab ? `&page=${tab}` : ''}`);
  await ready(page);
  await page.waitForFunction((p) => S.node && S.node.node.path === p, sel, { timeout: 15000 });
}
const tabOf = (page) => page.evaluate(() => (document.querySelector('.d6-tab.on') || {}).dataset && document.querySelector('.d6-tab.on').dataset.d6Tab);
async function goTab(page, id) {
  await page.click(`[data-d6-tab="${id}"]`);
  await page.waitForFunction((t) => document.querySelector('.d6-panel') && document.querySelector('.d6-panel').dataset.d6Panel === t, id, { timeout: 8000 });
  await page.waitForTimeout(250);
}
function watchErrors(page, base) {
  const watch = { errors: [] };
  page.on('console', (message) => { if (message.type() === 'error') watch.errors.push(message.text()); });
  page.on('pageerror', (error) => watch.errors.push(String(error)));
  page.on('response', (response) => { if (response.status() >= 400) watch.errors.push(`${response.status()} ${response.url().replace(base, '')}`); });
  return watch;
}
const goTo = (page, entityPath) => page.evaluate((target) => { select(target); pushNav(); render(); }, entityPath).then(() => page.waitForFunction((p) => S.node && S.node.node.path === p, entityPath, { timeout: 15000 }));

module.exports = { ROOT, PLAYWRIGHT, CHROME, SHOTS, VIEWPORT, BOARD, BOARD2, CAMERA, ADAPTER, check, tally, summary, fixtureHost, startServer, stopServer, getJson, wait, waitFor,
  newContext, ready, open, tabOf, goTab, watchErrors, goTo, fs, os, path };
