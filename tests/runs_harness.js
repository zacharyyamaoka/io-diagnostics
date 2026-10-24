// Shared by tests/test_runs_r2.js, test_runs_r3.js and test_runs_r5.js: the fixture host, the page, the Utilities flow.
// (tests/test_runs_view.js keeps its own copy: it predates this file and covers r1.)
//
// The host it starts is `cli.py serve --fixture F2 --plugins tests/fixtures/plugins-terminal --port 0 --no-udev` with a
// temporary state dir, stopped by its process group; it refuses to press anything unless /api/v1/plugins lists only the
// fixture plugin terminal_fixture (a driver that prints like a tool and never touches hardware).
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'runs');
const PLUGINS = path.join(ROOT, 'tests', 'fixtures', 'plugins-terminal');
const VIEWPORT = { width: 1440, height: 900 };

const tally = { passed: 0, failed: 0 };
function check(name, ok, detail = '') {
  if (ok) tally.passed += 1; else tally.failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(52)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

function startServer(stateDir, extraEnv = {}) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, TERMINAL_STEP_S: '0.15', TERMINAL_LOG: path.join(stateDir, 'terminal.jsonl'), ...extraEnv };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--fixture', 'F2', '--plugins', PLUGINS, '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
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

// The fixture host up, only the fixture plugin loaded, CAN 1 and its adapter found: {server, base, stateDir, CAN1, adapter}.
async function fixtureHost(extraEnv = {}) {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'runs-view-'));
  const server = await startServer(stateDir, extraEnv);
  const base = server.base;
  const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
  const names = plugins.plugins.map((plugin) => plugin.name);
  check('only the fixture plugin is loaded', names.join() === 'terminal_fixture', names);
  if (names.join() !== 'terminal_fixture') { stopServer(server.child); throw new Error('refusing to press anything: a non-fixture plugin is loaded'); }
  const find = (node, wanted) => (node.name === wanted ? node : node.children.map((child) => find(child, wanted)).find(Boolean));
  let can1;
  for (let i = 0; i < 120 && !can1; i += 1) {
    can1 = find((await (await fetch(`${base}/api/v1/state`)).json()).tree, 'CAN 1');
    if (!can1) await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (!can1) { stopServer(server.child); throw new Error('CAN 1 never appeared: the fixture plugin did not diagnose'); }
  return { server, base, stateDir, CAN1: can1.path, adapter: can1.path.split('/can/')[0] };
}

// A browser context whose page starts on run view `view` (the Prototype picker's remembered choice).
async function newContext(browser, view, colorScheme = 'light', extra = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme, ...extra });
  await context.addInitScript((id) => { try { if (!localStorage.getItem('io-diagnostics.runs-view')) localStorage.setItem('io-diagnostics.runs-view', id); } catch (_) { /* private mode */ } }, view);
  return context;
}

async function ready(page) {
  await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title') && window.IORuns && S.runView, null, { timeout: 30000 });
  await page.waitForTimeout(300);
}
async function open(page, base, sel) {
  await page.goto(`${base}/${sel ? `?sel=${encodeURIComponent(sel)}` : ''}`);
  await ready(page);
  if (sel) await page.waitForFunction((p) => S.node && S.node.node.path === p, sel, { timeout: 15000 });
}
async function utilities(page) {
  await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 15000 });
  if (!(await page.$('.ent-menu'))) await page.click('#ent-util-btn');
  await page.waitForSelector('.ent-menu [data-util]', { timeout: 5000 });
}
// Utilities ▾ -> the action -> its dry-run sheet -> Run (a query) or Continue… and the confirm modal (a moves action's box
// ticked). Nothing runs before the confirm.
async function runAction(page, name, danger) {
  await utilities(page);
  await page.click(`[data-util="${name}"]`);
  await page.waitForSelector('.ent-sheet [data-util-run]', { timeout: 10000 });
  await page.click('.ent-sheet [data-util-run]');
  if (danger === 'query') return;
  await page.waitForSelector('#modal [data-util-confirm]');
  if (danger === 'moves') await page.check('#modal [data-util-tick]');
  await page.click('#modal [data-util-confirm]');
}
const startCalibration = (page) => runAction(page, 'calibrate', 'moves');
// the run the page just started: waits for the host's answer (the util keeps no run id until the detached start is acknowledged)
const currentRunId = (page) => page.waitForFunction(() => S.util && S.util.runId, null, { timeout: 15000 }).then(() => page.evaluate(() => S.util.runId));
const runState = (page, id) => page.evaluate((runId) => { const run = window.IORuns.get(runId); return run ? { state: run.state, active: run.active, exit: run.exit_code, interactive: run.interactive } : null; }, id);
const waitEnded = (page, id, timeout = 20000) => page.waitForFunction((runId) => { const run = window.IORuns.get(runId); return run && !run.active; }, id, { timeout });

// Page errors: JS exceptions and console errors count. WHY a 409 on /runs/<id>/resize is set apart: runs/core.js posts a
// terminal's new size while its store copy says the run is still active; a /state poll that was in flight when the run ended
// can put that copy back for up to a second, and the host answers NotRunning. Harmless (nothing was written), core.js is
// frozen for the proposals, and the browser logs it as a console error: it is counted and shown, not failed.
function watchErrors(page, base) {
  const watch = { errors: [], benign: [], phase: 'start' };
  page.on('console', (message) => { if (message.type() === 'error') watch.errors.push(message.text()); });
  page.on('pageerror', (error) => watch.errors.push(String(error)));
  page.on('response', (response) => {
    if (response.status() === 409 && /\/api\/v1\/runs\/[^/]+\/resize$/.test(response.url())) watch.benign.push(`409 ${response.url().replace(base, '')} during ${watch.phase}`);
    else if (response.status() >= 400) watch.errors.push(`${response.status()} ${response.request().method()} ${response.url().replace(base, '')} during ${watch.phase}`);
  });
  watch.real = () => {
    let spare = watch.benign.length;
    return watch.errors.filter((text) => { if (spare > 0 && /status of 409/.test(text)) { spare -= 1; return false; } return true; });
  };
  return watch;
}

// Move the page to another entity the way a click on its row does (the shell's own select + history entry).
const goTo = (page, entityPath) => page.evaluate((target) => { select(target); pushNav(); render(); }, entityPath).then(() => page.waitForFunction((p) => S.node && S.node.node.path === p, entityPath, { timeout: 15000 }));

function summary(extra = '') {
  console.log(`\n${tally.passed}/${tally.passed + tally.failed} checks passed${extra}`);
  return tally.failed ? 1 : 0;
}

module.exports = { ROOT, PLAYWRIGHT, CHROME, SHOTS, VIEWPORT, check, tally, summary, startServer, stopServer, fixtureHost, newContext, ready, open, utilities, runAction, startCalibration,
  currentRunId, runState, waitEnded, goTo, watchErrors, fs, os, path };
