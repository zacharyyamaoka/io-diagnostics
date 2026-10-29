#!/usr/bin/env node
// Acceptance for runs (docs/runs-v1.md section 7) and its default view r1, in the REAL page on a fixture server,
// headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_runs_view.js
//
// It starts its own `cli.py serve --fixture F2 --plugins tests/fixtures/plugins-terminal --port 0` (a free port, a
// temporary state dir, stopped by its process group), and refuses to press anything unless /api/v1/plugins lists only
// the fixture plugin terminal_fixture (a driver that prints like a tool and never touches hardware). Checks:
//   seam        window.IORunViews / IORuns, the Runs select in the Prototype picker, the documented ctx members, a second
//               view mounted and unmounted cleanly (and r1 again afterwards)
//   flow        Utilities ▾ -> Run calibration… -> dry-run sheet -> the confirm modal (ticked) -> the panel opens on its own
//               with a tab "CAN 1 · Run calibration", a spinner, an elapsed timer, "Stop (motor goes limp)" and a badge on the
//               status-bar toggle; the xterm shows the progress as it is printed (page-side latency measured), the prompt
//               waits; "y" typed in the terminal finishes it: tab "done", Stop gone, a recording link, the run line's result
//   stop        a second run, Stop pressed: "Stopping…", then the tab is "stopped" (warn shape) and the terminal says the
//               plugin's stop was sent to CAN 1; both finished tabs stay until closed; a running tab has no close button
//   panel       the boundary drags (remembered across a reload), double-click resets, Ctrl+` hides and shows it, "Show
//               output" on the run line opens the tab again
//   quiet       a query run keeps the panel as the person left it (closed), its tab is there, the badge counts it
// Screenshots: reports/media/runs/r1-*.png. Exits 1 on any failed check. Nothing opens a CAN adapter.
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

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(46)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

function startServer(stateDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, TERMINAL_STEP_S: '0.15', TERMINAL_LOG: path.join(stateDir, 'terminal.jsonl') };
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

async function ready(page) {
  await page.waitForFunction(() => typeof S !== "undefined" && S.built
    && document.querySelector('.d-title') && window.IORuns && S.runView, null, { timeout: 30000 });
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
// the xterm of a run, read from its buffer (the view keeps the Terminal objects)
const terminalText = (page, id) => page.evaluate((runId) => {
  const bound = window.IORunViews.r1.s && window.IORunViews.r1.s.terminals.get(runId);
  if (!bound) return null;
  const buffer = bound.term.buffer.active;
  const lines = [];
  for (let i = 0; i < buffer.length; i += 1) lines.push(buffer.getLine(i).translateToString(true));
  return lines.join('\n');
}, id);
const waitTerminal = (page, id, pattern, timeout = 20000) => page.waitForFunction(([runId, source]) => {
  const bound = window.IORunViews.r1.s && window.IORunViews.r1.s.terminals.get(runId);
  if (!bound) return false;
  const buffer = bound.term.buffer.active;
  let text = '';
  for (let i = 0; i < buffer.length; i += 1) text += `${buffer.getLine(i).translateToString(true)}\n`;
  return new RegExp(source).test(text);
}, [id, pattern.source], { timeout });

// Run Run calibration… on CAN 1 from the Utilities menu: its Run page, then Run (2026-10-29: no sheet, no ticked box).
async function startCalibration(page) {
  await utilities(page);
  await page.click('[data-util="calibrate"]');
  await page.waitForSelector('#modal:not([hidden]) [data-util-confirm]:not([disabled])', { timeout: 10000 });
  await page.click('#modal [data-util-confirm]');
}
const currentRunId = (page) => page.evaluate(() => S.util && S.util.runId);

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'runs-view-'));
  const { chromium } = require(PLAYWRIGHT);
  const browser = require('./pin_layout').pinDetailLayout(await chromium.launch({ executablePath: CHROME, headless: true }));
  const server = await startServer(stateDir);
  const base = server.base;
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    const names = plugins.plugins.map((plugin) => plugin.name);
    check('only the fixture plugin is loaded', names.join() === 'terminal_fixture', names);
    if (names.join() !== 'terminal_fixture') throw new Error('refusing to press anything: a non-fixture plugin is loaded');
    // the plugin's first diagnose lands a moment after the server starts
    const find = (node, wanted) => (node.name === wanted ? node : node.children.map((child) => find(child, wanted)).find(Boolean));
    let can1;
    for (let i = 0; i < 120 && !can1; i += 1) {
      can1 = find((await (await fetch(`${base}/api/v1/state`)).json()).tree, 'CAN 1');
      if (!can1) await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (!can1) throw new Error('CAN 1 never appeared: the fixture plugin did not diagnose');
    const CAN1 = can1.path;
    const adapter = CAN1.split('/can/')[0];

    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
    const page = await context.newPage();
    const errors = [];
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('pageerror', (error) => errors.push(String(error)));
    await open(page, base, CAN1);

    // ---- the seam
    const seam = await page.evaluate(() => {
      const select = document.getElementById('run-view');
      const toggle = document.querySelector('#runs-status .r1-toggle');
      return { views: Object.keys(window.IORunViews), ids: [...select.options].map((o) => o.value), value: select.value, chosen: S.runView,
        protoShown: !document.getElementById('proto-switch').hidden, runs: typeof window.IORuns.stream, toggle: Boolean(toggle),
        badgeHidden: toggle && toggle.querySelector('.r1-badge').hidden, rootHidden: document.getElementById('runs-root').hidden,
        xterm: typeof window.Terminal, fit: typeof (window.FitAddon && window.FitAddon.FitAddon), title: select.title };
    });
    check('seam: r1 registered, the Runs select lists it', seam.views.includes('r1') && seam.ids[0] === 'r1' && seam.value === 'r1' && seam.chosen === 'r1' && seam.protoShown, seam);
    check('seam: xterm.js and its fit addon are vendored', seam.xterm === 'function' && seam.fit === 'function' && seam.runs === 'function', seam);
    check('seam: the toggle is in the status bar, the panel hidden', seam.toggle && seam.badgeHidden && seam.rootHidden, seam);
    const ctxShape = await page.evaluate(() => Object.fromEntries(['runs', 'run', 'onChange', 'stream', 'sendInput', 'interrupt', 'resize', 'terminal', 'castUrl', 'open', 'redraw', 'css', 'pref', 'setPref']
      .map((name) => [name, typeof runsCtx[name]]).concat([['root', runsCtx.root && runsCtx.root.id], ['statusSlot', runsCtx.statusSlot && runsCtx.statusSlot.id],
        ['h', Object.keys(runsCtx.h).sort().join()]])));
    check('seam: the documented ctx', Object.entries(ctxShape).every(([name, kind]) => (name === 'root' ? kind === 'runs-root' : name === 'statusSlot' ? kind === 'runs-status'
      : name === 'h' ? kind === 'elapsed,esc,plainTitle,shape,stateWords' : kind === 'function')), ctxShape);

    // a second view mounts and r1 unmounts cleanly, and r1 comes back
    const swap = await page.evaluate(() => {
      const log = [];
      window.IORunViews.rx = { name: 'Probe', description: 'test', order: 9, mount(ctx) { log.push(['mount', typeof ctx.terminal, ctx.root.id]); ctx.root.hidden = false; ctx.root.textContent = 'rx'; },
        unmount(ctx) { log.push(['unmount']); ctx.root.textContent = ''; ctx.root.hidden = true; }, update() { log.push(['update']); } };
      S.runView = 'rx'; mountRunView();
      const during = { root: document.getElementById('runs-root').textContent, toggle: Boolean(document.querySelector('#runs-status .r1-toggle')), r1class: document.getElementById('runs-root').classList.contains('r1') };
      render();
      S.runView = 'r1'; mountRunView();
      delete window.IORunViews.rx;
      return { log, during, after: { toggle: Boolean(document.querySelector('#runs-status .r1-toggle')), r1class: document.getElementById('runs-root').classList.contains('r1'),
        hidden: document.getElementById('runs-root').hidden } };
    });
    check('seam: another view mounts, updates, unmounts; r1 returns', swap.log[0][0] === 'mount' && swap.log[0][1] === 'function' && swap.log.some((e) => e[0] === 'update') && swap.log.at(-1)[0] === 'unmount'
      && swap.during.root === 'rx' && !swap.during.toggle && !swap.during.r1class && swap.after.toggle && swap.after.r1class && swap.after.hidden, swap);

    // ---- the flow: a moves run from the Utilities menu
    await startCalibration(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r1-tab', { timeout: 10000 });
    const runId = await currentRunId(page);
    const started = await page.evaluate(() => {
      const tab = document.querySelector('.r1-tab');
      return { title: tab.querySelector('.r1-title').textContent, selected: tab.getAttribute('aria-selected'), stop: tab.querySelector('.r1-stop').textContent,
        stopMoves: tab.querySelector('.r1-stop').classList.contains('moves'), spin: Boolean(tab.querySelector('.r1-spin')), close: tab.querySelector('.r1-close').hidden,
        badge: document.querySelector('.r1-badge').textContent, badgeHidden: document.querySelector('.r1-badge').hidden,
        pressed: document.querySelector('.r1-toggle').getAttribute('aria-pressed'), line: (document.querySelector('.ent-run') || {}).textContent || '',
        modal: document.getElementById('modal').hidden };
    });
    check('flow: the panel opens with the run\'s tab', started.title === 'CAN 1 · Run calibration' && started.selected === 'true' && started.spin && started.close && started.modal, started);
    check('flow: Stop says the motor goes limp, the toggle is badged', started.stop === 'Stop (motor goes limp)' && started.stopMoves && started.badge === '1' && !started.badgeHidden && started.pressed === 'true', started);
    check('flow: the run line offers the output', /^Running calibration on CAN 1… \d+ s · Show output$/.test(started.line), started.line);

    // live output: poll the terminal buffer every 15 ms and compare each progress frame's t= stamp with the clock when first seen
    await waitTerminal(page, runId, /calibrating/);
    const seen = new Map();
    const latencies = [];
    for (let i = 0; i < 400; i += 1) {
      const sample = await page.evaluate((id) => {
        const bound = window.IORunViews.r1.s.terminals.get(id);
        const buffer = bound.term.buffer.active;
        let text = '';
        for (let n = 0; n < buffer.length; n += 1) text += `${buffer.getLine(n).translateToString(true)}\n`;
        return { now: Date.now() / 1000, stamps: [...text.matchAll(/t=(\d+\.\d+)/g)].map((m) => m[1]), prompt: /Continue\? \[y\/N\]/.test(text) };
      }, runId);
      for (const stamp of sample.stamps) if (!seen.has(stamp)) { seen.set(stamp, true); latencies.push((sample.now - Number(stamp)) * 1000); }
      if (sample.prompt) break;
      await page.waitForTimeout(15);
    }
    latencies.sort((a, b) => a - b);
    const median = latencies[Math.floor(latencies.length / 2)];
    check('flow: progress is on screen as it is printed', latencies.length >= 6 && median < 250 && latencies[latencies.length - 1] < 600,
      { frames: latencies.length, median_ms: Math.round(median), max_ms: Math.round(latencies[latencies.length - 1]) });
    await waitTerminal(page, runId, /Continue\? \[y\/N\]/);
    const waiting = await page.evaluate((id) => ({ state: window.IORuns.get(id).state, meta: document.querySelector('.r1-tab .r1-meta').textContent,
      foot: document.querySelector('.r1-foot').textContent, focus: Boolean(document.activeElement && document.activeElement.closest('.xterm')) }), runId);
    check('flow: the prompt waits for a typed answer', waiting.state === 'running' && /^\d+:\d\d$/.test(waiting.meta) && /Typing goes to the process/.test(waiting.foot) && waiting.focus, waiting);
    await page.screenshot({ path: path.join(SHOTS, 'r1-running-prompt.png') });
    await page.keyboard.type('y');
    await page.keyboard.press('Enter');
    await page.waitForFunction((id) => window.IORuns.get(id) && !window.IORuns.get(id).active, runId, { timeout: 15000 });
    await page.waitForSelector('.ent-run.ok', { timeout: 15000 });
    await waitTerminal(page, runId, /saved the calibration to CAN 1/);
    await page.waitForTimeout(400);
    const done = await page.evaluate((id) => {
      const tab = document.querySelector('.r1-tab');
      return { meta: tab.querySelector('.r1-meta').textContent, stopHidden: tab.querySelector('.r1-stop').hidden, close: !tab.querySelector('.r1-close').hidden, spin: Boolean(tab.querySelector('.r1-spin')),
        badgeHidden: document.querySelector('.r1-badge').hidden, foot: document.querySelector('.r1-foot').textContent, cast: (document.querySelector('.r1-foot a') || {}).getAttribute('href'),
        line: document.querySelector('.ent-run').textContent, run: window.IORuns.get(id).state, exit: window.IORuns.get(id).exit_code };
    }, runId);
    check('flow: typing y finishes it: tab done, Stop gone', done.run === 'done' && done.exit === 0 && /^done · \d+:\d\d$/.test(done.meta) && done.stopHidden && done.close && !done.spin && done.badgeHidden, done);
    check('flow: the foot links the recording, the line shows the result', /recording \(\.cast\)/.test(done.foot) && done.cast === `/api/v1/runs/${runId}/cast` && /CAN 1 calibrated|ran in/.test(done.line) && /Show output/.test(done.line), done);
    await page.screenshot({ path: path.join(SHOTS, 'r1-done.png') });
    const cast = await (await fetch(`${base}/api/v1/runs/${runId}/cast`)).text();
    check('flow: the recording is an asciinema v2 file', JSON.parse(cast.split('\n')[0]).version === 2 && cast.includes('saved the calibration'), cast.slice(0, 90));

    // ---- Stop on a second run; the first tab stays
    await startCalibration(page);
    await page.waitForFunction((first) => window.IORuns.list().some((run) => run.active && run.id !== first), runId, { timeout: 10000 });
    const second = await currentRunId(page);
    await waitTerminal(page, second, /Continue\? \[y\/N\]/);
    check('stop: two tabs, the finished one stays', (await page.$$('.r1-tab')).length === 2, await page.evaluate(() => [...document.querySelectorAll('.r1-tab .r1-title')].map((el) => el.textContent)));
    await page.screenshot({ path: path.join(SHOTS, 'r1-two-tabs.png') });
    await page.click(`[data-r1-stop="${second}"]`);
    const stopping = await page.evaluate((id) => document.querySelector(`[data-r1-stop="${id}"]`).textContent, second);
    await page.waitForFunction((id) => window.IORuns.get(id) && !window.IORuns.get(id).active, second, { timeout: 15000 });
    await waitTerminal(page, second, /stop: CAN 1 mode 0/);
    await page.waitForSelector('.ent-run:not(.running)', { timeout: 10000 }); // the run line learns the end on the next poll
    await page.waitForTimeout(300);
    const stopped = await page.evaluate((id) => {
      const tab = document.querySelector(`[data-r1-tab="${id}"]`);
      const run = window.IORuns.get(id);
      return { state: run.state, meta: tab.querySelector('.r1-meta').textContent, warn: Boolean(tab.querySelector('.r1-shape.lv-warn')), foot: document.querySelector('.r1-foot').textContent,
        plugin: run.stop && run.stop.plugin_stop && run.stop.plugin_stop.ran, line: (document.querySelector('.ent-run') || {}).textContent || '',
        quiet: !document.querySelector('.ent-run').hasAttribute('data-diagnostic') };
    }, second);
    const text = await terminalText(page, second);
    check('stop: Stopping…, then stopped and the plugin\'s stop sent', stopping === 'Stopping…' && stopped.state === 'stopped' && /^stopped · \d+:\d\d$/.test(stopped.meta) && stopped.warn && stopped.plugin === true
      && /stopping CAN 1/.test(text) && /stop: CAN 1 mode 0/.test(text) && /the plugin's stop: CAN 1 mode 0/.test(stopped.foot) && !/Traceback/.test(text), { stopping, stopped });
    check('stop: a Stop is one quiet line, not a failure', stopped.quiet && /^Run calibration stopped after [\d.]+ s \(exit 130\)/.test(stopped.line), stopped.line);
    await page.screenshot({ path: path.join(SHOTS, 'r1-stopped.png') });
    // a finished tab closes; a running one cannot
    await page.click(`[data-r1-close="${runId}"]`);
    check('stop: a finished tab closes', (await page.$$('.r1-tab')).length === 1 && !(await page.$(`[data-r1-tab="${runId}"]`)), await page.evaluate(() => document.querySelectorAll('.r1-tab').length));

    // ---- the panel: drag, remember, reset, toggle, Show output
    const sashBox = await (await page.$('.r1-sash')).boundingBox();
    const before = await page.evaluate(() => document.getElementById('runs-root').getBoundingClientRect().height);
    await page.mouse.move(sashBox.x + sashBox.width / 2, sashBox.y + 4);
    await page.mouse.down();
    await page.mouse.move(sashBox.x + sashBox.width / 2, sashBox.y - 96, { steps: 6 });
    await page.mouse.up();
    const after = await page.evaluate(() => document.getElementById('runs-root').getBoundingClientRect().height);
    check('panel: the boundary drags', Math.abs(after - (before + 100)) <= 6 && after > before, { before, after });
    await page.reload();
    await ready(page);
    await page.waitForTimeout(500);
    const remembered = await page.evaluate(() => ({ h: document.getElementById('runs-root').getBoundingClientRect().height, hidden: document.getElementById('runs-root').hidden, tabs: document.querySelectorAll('.r1-tab').length }));
    check('panel: height and open state are remembered across a reload', Math.abs(remembered.h - after) <= 2 && !remembered.hidden, remembered);
    await page.dblclick('.r1-sash');
    const reset = await page.evaluate(() => document.getElementById('runs-root').getBoundingClientRect().height);
    check('panel: double-click restores the default height', Math.abs(reset - 280) <= 2, reset);
    await page.focus('.r1-sash');
    await page.keyboard.press('ArrowUp');
    const keyed = await page.evaluate(() => document.getElementById('runs-root').getBoundingClientRect().height);
    check('panel: the arrow keys move a focused boundary', Math.abs(keyed - 296) <= 2, keyed);
    await page.dblclick('.r1-sash');

    await page.keyboard.press('Control+`');
    const hiddenNow = await page.evaluate(() => ({ hidden: document.getElementById('runs-root').hidden, pressed: document.querySelector('.r1-toggle').getAttribute('aria-pressed') }));
    await page.keyboard.press('Control+`');
    const shownNow = await page.evaluate(() => ({ hidden: document.getElementById('runs-root').hidden, pressed: document.querySelector('.r1-toggle').getAttribute('aria-pressed') }));
    check('panel: Ctrl+` hides and shows it', hiddenNow.hidden && hiddenNow.pressed === 'false' && !shownNow.hidden && shownNow.pressed === 'true', { hiddenNow, shownNow });
    await page.click('.r1-hide');
    const viaButton = await page.evaluate(() => document.getElementById('runs-root').hidden);
    await page.click('.r1-toggle');
    const viaToggle = await page.evaluate(() => !document.getElementById('runs-root').hidden);
    check('panel: the hide button and the status-bar toggle', viaButton && viaToggle, { viaButton, viaToggle });
    // after a reload the page-local run line is gone (it belongs to the page), but the host still lists the finished runs
    const earlierRuns = await page.evaluate(() => [...document.querySelectorAll('.r1-earlier option')].map((option) => ({ value: option.value, text: option.textContent })).filter((option) => option.value));
    check('panel: earlier runs are one pick away after a reload', earlierRuns.length === 2 && earlierRuns.every((option) => /CAN 1 · Run calibration · (done|stopped)/.test(option.text)), earlierRuns);
    await page.selectOption('.r1-earlier', earlierRuns[0].value);
    await page.waitForSelector('.r1-tab[aria-selected="true"]');
    const replayed = await page.evaluate(() => ({ tabs: document.querySelectorAll('.r1-tab').length, foot: document.querySelector('.r1-foot').textContent }));
    await waitTerminal(page, earlierRuns[0].value, /\[exit /);
    check('panel: a past run opens as a read-only scrollback', replayed.tabs === 1 && /recording/.test(replayed.foot), replayed);

    // ---- a query run is quiet: the panel stays closed, the tab exists
    await open(page, base, adapter);
    await page.evaluate(() => { const root = document.getElementById('runs-root'); if (!root.hidden) document.querySelector('.r1-hide').click(); });
    await utilities(page);
    await page.click('[data-util="check_comms"]'); // 2026-10-29: a query runs on the click
    await page.waitForSelector('.ent-run.ok', { timeout: 20000 });
    const quiet = await page.evaluate(() => ({ hidden: document.getElementById('runs-root').hidden, line: document.querySelector('.ent-run').textContent, badge: document.querySelector('.r1-badge').hidden }));
    check('quiet: a query run leaves the panel closed, its line offers the output', quiet.hidden && /Show output/.test(quiet.line) && quiet.badge, quiet);
    await page.click('[data-run-open]');
    await page.waitForSelector('#runs-root:not([hidden]) .r1-pane:not([hidden]) .xterm');
    const shown = await page.evaluate(() => ({ title: document.querySelector('.r1-tab[aria-selected="true"] .r1-title').textContent, hidden: document.getElementById('runs-root').hidden }));
    const runId3 = await currentRunId(page);
    await waitTerminal(page, runId3, /line 4 t=/);
    check('quiet: Show output opens the run\'s tab with its output', /Check communication/.test(shown.title) && !shown.hidden, shown);
    await page.screenshot({ path: path.join(SHOTS, 'r1-show-output.png') });

    // ---- dark
    const dark = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'dark' });
    const darkPage = await dark.newPage();
    await open(darkPage, base, CAN1);
    await darkPage.evaluate(() => { runsCtx.setPref('r1.open', '1'); });
    await darkPage.reload();
    await ready(darkPage);
    await darkPage.waitForTimeout(600);
    const earlier = await darkPage.$('.r1-earlier');
    if (earlier) {
      const options = await darkPage.$$eval('.r1-earlier option', (nodes) => nodes.map((n) => n.value).filter(Boolean));
      if (options.length) await darkPage.selectOption('.r1-earlier', options[options.length - 1]);
      await darkPage.waitForTimeout(800);
    }
    await darkPage.screenshot({ path: path.join(SHOTS, 'r1-dark.png') });
    const themed = await darkPage.evaluate(() => { const row = document.querySelector('.xterm-screen'); return row ? getComputedStyle(document.querySelector('.r1-body')).backgroundColor : ''; });
    check('dark: the terminal follows the skin\'s colours', Boolean(themed) && themed !== 'rgba(0, 0, 0, 0)', themed);
    await dark.close();

    check('no page errors', errors.length === 0, errors.slice(0, 3));
    await context.close();
  } finally {
    stopServer(server.child);
    await browser.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
  }
  console.log(`\n${passed}/${passed + failed} checks passed · screenshots in ${SHOTS}`);
  process.exit(failed ? 1 : 0);
}

main().catch((error) => { console.error(error); process.exit(1); });
