#!/usr/bin/env node
// Proof shots of the whole dashboard, served by the real host (`cli.py serve`), in headless Chrome (never a window).
//
//   /usr/bin/node /home/bam/io-diagnostics/tools/shoot_dashboard.js [--out /abs/dir]
//
// Four servers, one after the other, each stopped by its process group before the next starts:
//   F1   fixture 18:11 · plug       -> tree (default proposal v1, fresh profile), chip popover, table, activity
//   F2   fixture 18:25 · bus busy   -> the fdcanusb held by tview: its gated "Run comms check" confirm dialog
//   live this machine, passive     -> the real skeleton (USB, PCI, NICs, ttys, video nodes) with the REAL
//                                     moteus_servo + intel_rgbd plugins run `diagnose --passive` behind
//                                     tests/fixtures/plugins-live-passive/passive_guard.py: no adapter or camera opened
//   machine  lane B's fake rack PC  -> PCIe frame grabber without its driver, a capture card trained x1 of x4, two
//                                     EtherCAT NICs: hardware this PC lacks, through the real io_facts sources
// Writes <out>/<name>.png and <out>/shots.json (what each shot proved), exits 1 if any check fails.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const outIndex = process.argv.indexOf('--out');
const OUT = outIndex > 0 ? process.argv[outIndex + 1] : path.join(ROOT, 'reports', 'media', 'integration');
const VIEWPORT = { width: 1600, height: 1000 };

function startServer(label, extra) {
  const env = { ...process.env };
  delete env.VIRTUAL_ENV; // uv run picks the project's own environment
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--port', '0', '--no-udev', ...extra], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stopServer(child); reject(new Error(`${label} server did not start: ${output}`)); }, 90000);
    const onData = (chunk) => {
      output += chunk;
      const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//);
      if (found) { clearTimeout(timer); resolve({ child, base: found[1], output: () => output }); }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`${label} server exited (${code}): ${output}`)); });
  });
}

function stopServer(child) {
  // WHY the process group: `uv run` starts python as a child; the group is exactly what this script started
  try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* already gone */ }
}

const results = [];
function check(shot, name, ok, detail) {
  results.push({ shot, check: name, ok: Boolean(ok), detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${shot}  ${name}${detail ? `  · ${detail}` : ''}`);
}

async function openPage(browser, base) {
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 }); // fresh: no stored proposal
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto(`${base}/`);
  await page.waitForSelector('#tree-host .t1-row', { timeout: 30000 });
  await page.waitForTimeout(600);
  return { context, page, errors };
}

async function shoot(page, name) {
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: file });
  return file;
}

async function chipSelect(page, row, title) {
  // the chip popover lists the causes; clicking one selects it and the tree opens the path to it (real user path).
  // `row` matches the cause's bold text ("device › name" or "name"), `title` the detail panel's title after the click.
  await page.click('#chip');
  await page.locator('#chip-pop .pop-row').filter({ has: page.locator('b', { hasText: row }) }).first().click();
  await page.waitForFunction((wanted) => document.querySelector('#detail .d-title')?.textContent.includes(wanted), title);
  await page.waitForSelector('#detail .cards .card', { timeout: 10000 });
  await page.waitForTimeout(400);
}

async function f1(browser) {
  const server = await startServer('F1', ['--fixture', 'F1']);
  try {
    const { context, page, errors } = await openPage(browser, server.base);
    check('f1-tree', 'default proposal is v1', (await page.inputValue('#tree-variant')) === 'v1');
    await chipSelect(page, /(^|› )CAN 15 driver$/, 'CAN 15 driver');
    const selected = await page.locator('#tree-host .t1-row[aria-selected="true"]').count();
    check('f1-tree', 'CAN 15 driver selected in the tree and the detail panel', selected === 1);
    const greens = await page.evaluate(() => [...document.querySelectorAll('#tree-host *')].filter((element) => {
      const [r, g, b] = (getComputedStyle(element).color.match(/\d+/g) || []).map(Number);
      return g > 120 && g > r + 40 && g > b + 20;
    }).length);
    check('f1-tree', 'quiet when OK: no green in the tree', greens === 0, `${greens} green elements`);
    const shapes = await page.evaluate(() => [...document.querySelectorAll('#tree-host .t1-row')].map((row) => {
      const name = row.querySelector('.t1-name'); const mark = row.querySelector('.t1-shape');
      return mark && name ? mark.getBoundingClientRect().left > name.getBoundingClientRect().right : null;
    }).filter((value) => value !== null));
    check('f1-tree', 'every status shape sits right of its name', shapes.length > 0 && shapes.every(Boolean), `${shapes.length} shapes`);
    await shoot(page, 'f1-tree');
    await page.click('#chip');
    await page.waitForSelector('#chip-pop:not([hidden])');
    check('f1-chip', 'chip popover lists the causes', (await page.locator('#chip-pop .pop-row[data-go]').count()) >= 3);
    await shoot(page, 'f1-chip');
    await page.keyboard.press('Escape');
    await page.click('#views [data-view="table"]');
    await page.waitForTimeout(400);
    check('f1-table', 'table renders rows', (await page.locator('#left-other tr').count()) > 10);
    await shoot(page, 'f1-table');
    await page.click('#views [data-view="activity"]');
    await page.waitForTimeout(400);
    const activity = await page.locator('#left-other').innerText();
    check('f1-activity', 'activity shows plug events and link health', /plug|unplug|enumerat/i.test(activity));
    await shoot(page, 'f1-activity');
    check('f1', 'no page or console errors', errors.length === 0, errors.slice(0, 2).join(' | '));
    await context.close();
  } finally { stopServer(server.child); }
}

async function f2(browser) {
  const server = await startServer('F2', ['--fixture', 'F2']);
  try {
    const { context, page, errors } = await openPage(browser, server.base);
    await chipSelect(page, /^fdcanusb 272813EF$/, 'fdcanusb 272813EF');
    const button = page.locator('#detail .abtn', { hasText: 'Run comms check' });
    check('f2-action', 'the driver registered "Run comms check" on the adapter', (await button.count()) === 1);
    await button.click();
    await page.waitForSelector('#modal:not([hidden]) .modal');
    const modal = await page.locator('#modal .modal').innerText();
    check('f2-action', 'the confirm shows it blocked by the port holder', /blocked/i.test(modal) && /tview/i.test(modal),
      modal.split('\n').find((line) => /blocked/i.test(line)));
    check('f2-action', 'the run button is disabled (v1 never executes)',
      await page.locator('#modal footer .btn.primary').isDisabled());
    await shoot(page, 'f2-action-dialog');
    check('f2', 'no page or console errors', errors.length === 0, errors.slice(0, 2).join(' | '));
    await context.close();
  } finally { stopServer(server.child); }
}

async function live(browser) {
  const plugins = path.join(ROOT, 'tests', 'fixtures', 'plugins-live-passive');
  const server = await startServer('live', ['--plugins', plugins]);
  try {
    const { context, page, errors } = await openPage(browser, server.base);
    // the drivers run in the background scheduler; wait until both have hung their children under the devices
    await page.waitForFunction(async () => {
      const state = await (await fetch('/api/v1/state')).json();
      const owners = new Set();
      const walk = (node) => { owners.add(node.owner); node.children.forEach(walk); };
      walk(state.tree);
      return owners.has('moteus_servo') && owners.has('intel_rgbd');
    }, null, { timeout: 120000, polling: 1000 });
    await page.waitForTimeout(1500);
    const state = await page.evaluate(async () => (await fetch('/api/v1/state')).json());
    const owners = new Set();
    const walk = (node) => { owners.add(node.owner); node.children.forEach(walk); };
    walk(state.tree);
    for (const source of ['io_facts.usb', 'io_facts.pci', 'io_facts.netdev', 'io_facts.tty', 'io_facts.v4l']) {
      check('live-tree', `beyond USB: ${source} rows in the live tree`, owners.has(source));
    }
    const runs = await page.evaluate(async () => (await fetch('/api/v1/plugins')).json());
    for (const plugin of runs.plugins) {
      const outcomes = plugin.jobs.map((job) => job.last_run && job.last_run.outcome);
      check('live-tree', `${plugin.name} ran passively through the host`, outcomes.length > 0 && outcomes.every((o) => o === 'ok'),
        `${plugin.jobs.length} device(s): ${outcomes.join(', ')}`);
    }
    await chipSelect(page, /^RealSense D405/, 'RealSense D405');
    await shoot(page, 'live-tree');
    await page.click('#views [data-view="table"]');
    await page.waitForTimeout(400);
    await shoot(page, 'live-table');
    check('live', 'no page or console errors', errors.length === 0, errors.slice(0, 2).join(' | '));
    await context.close();
  } finally { stopServer(server.child); }
}

async function machine(browser) {
  // hardware this PC does not have: lane B's fake rack machine through the real io_facts sources
  const spec = path.join(ROOT, 'tests', 'data', 'sysfs-rack-fixture.json');
  const server = await startServer('machine', ['--machine', spec]);
  try {
    const { context, page, errors } = await openPage(browser, server.base);
    await chipSelect(page, /^Likely frame grabber/, 'Likely frame grabber');
    const text = await page.locator('#tree-host').innerText();
    check('machine-beyond-usb', 'the capture card and the grabber are tree rows', /Pro Capture Endpoint/.test(text) && /Likely frame grabber/.test(text));
    check('machine-beyond-usb', 'the grabber says it has no kernel driver',
      /no kernel driver bound/.test(await page.locator('#detail').innerText()));
    await shoot(page, 'machine-beyond-usb');
    await page.fill('#search', 'EtherCAT');  // the NICs sit under their (quiet, collapsed) I210 functions
    await page.waitForTimeout(500);
    const found = await page.locator('#tree-host').innerText();
    check('machine-ethercat', 'search finds both EtherCAT segments under their NICs',
      (found.match(/EtherCAT segment \(not scanned\)/g) || []).length === 2, found.replace(/\s+/g, ' ').slice(0, 160));
    await shoot(page, 'machine-ethercat');
    check('machine', 'no page or console errors', errors.length === 0, errors.slice(0, 2).join(' | '));
    await context.close();
  } finally { stopServer(server.child); }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  try {
    for (const run of [f1, f2, live, machine]) {
      try { await run(browser); } catch (error) { check(run.name, 'ran', false, String(error).slice(0, 300)); }
    }
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(OUT, 'shots.json'), JSON.stringify(results, null, 2));
  const failed = results.filter((result) => !result.ok);
  console.log(`${results.length - failed.length}/${results.length} checks passed · shots in ${OUT}`);
  process.exit(failed.length ? 1 : 0);
})();
