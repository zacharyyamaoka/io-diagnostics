#!/usr/bin/env node
// Acceptance for the three small gaps (lane/small-gaps), in the REAL page on a fixture server, headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_small_gaps.js
//
// It starts its own `cli.py serve --fixture F1 --port 0 --no-udev` (a free port, a temporary state dir, stopped by its
// process group) and refuses to go on unless /api/v1/plugins lists only the fixture plugin: nothing here touches hardware.
//   usb      Zach (2026-10-24): "instead of saying 480 Mb/s say 2.1 or 3.2". On the F1 fixture no VISIBLE text says "Mb/s" or
//            "Gb/s" (the raw speed lives in tooltips only), in the tree, the breadcrumb, the entity header and the Table, in every
//            layout's breadcrumb; the generation is said instead ("USB bus 1 · USB 2.0"), and the tooltip keeps the API's words.
//   rate     Zach (2026-10-22): "they should also show stuff like Frequency". The Table has a Rate column: "0.20 /s · expected 0.20";
//            a row under half of its expected rate gets a quiet note (no status shape, no warning colour); a row still being
//            measured reads "—". A row of the live fixture reads a real, measured rate.
// Screenshots (light and dark, 1400x900): reports/media/small-gaps/. Exits 1 on any failure.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'small-gaps');
const SPEED = /\d\s*[kMG]b\/s/;

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(62)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

function startServer(stateDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--fixture', 'F1', '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
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
const stop = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };

async function ready(page) {
  await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title') && window.IOLayoutHost && IOLayoutHost.id, null, { timeout: 30000 });
  await page.waitForTimeout(500);
}
async function open(page, base, layout, query = '') {
  await page.addInitScript((value) => { try { localStorage.setItem('io-diagnostics.page-layout', value); } catch (_) { /* private */ } }, layout);
  await page.goto(`${base}/${query}`);
  await ready(page);
}
// the text a person reads: innerText leaves out tooltips (title attributes), hidden and folded parts
const readable = (page) => page.evaluate(() => document.body.innerText);
const speedLines = (text) => text.split('\n').filter((line) => SPEED.test(line));

async function main() {
  const { chromium } = require(PLAYWRIGHT);
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'io-small-gaps-'));
  const server = await startServer(stateDir);
  const base = server.base;
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const errors = [];
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    const names = (plugins.plugins || plugins).map((plugin) => plugin.name);
    if (names.length !== 1 || !/fixture|f1|scenario|live/i.test(names[0])) throw new Error(`refusing to go on: plugins are ${JSON.stringify(names)}`);

    // ------------------------------------------------------------------------------------------- 1. USB labels
    const SELECTIONS = ['host', 'host/usb1', 'host/usb1/port-1-3', 'host/usb1/port-1-3/usb-1-3', 'host/usb6', 'host/usb6/port-6-1/usb-6-1', 'host/usb7/port-7-1'];
    {
      const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: 'light' });
      const page = await context.newPage();
      page.on('pageerror', (error) => errors.push(`usb: ${error}`));
      page.on('console', (message) => { if (message.type() === 'error') errors.push(`usb: console ${message.text()}`); });
      for (const view of ['tree', 'table']) {
        const leaks = [];
        for (const selection of SELECTIONS) {
          await open(page, base, 'l1', `?view=${view}&sel=${encodeURIComponent(selection)}`);
          leaks.push(...speedLines(await readable(page)).map((line) => `${selection}: ${line}`));
        }
        check(`usb: no visible Mb/s or Gb/s in the ${view} view (L1, ${SELECTIONS.length} selections: tree, crumbs, header)`, leaks.length === 0, leaks.slice(0, 6));
      }
      // the generation is said, in each place the speed used to be
      await open(page, base, 'l1', '?sel=host%2Fusb1%2Fport-1-3');
      const crumbs = await page.locator('.l1-crumbs a').allInnerTexts();
      check('usb: the breadcrumb names the bus by its generation', crumbs.includes('USB bus 1 · USB 2.0'), crumbs);
      const crumbTip = await page.locator('.l1-crumbs a', { hasText: 'USB bus 1' }).getAttribute('title');
      check('usb: the breadcrumb tooltip keeps the raw speed', crumbTip === 'USB bus 1 · 480 Mb/s', crumbTip);
      check('usb: the port header says the generation', /1 device · USB 1\.1/.test(await page.locator('.d6-head .d-msg').innerText()), await page.locator('.d6-head .d-msg').innerText());
      const headerTip = await page.locator('.d6-head .d-msg').getAttribute('title');
      check('usb: the header message tooltip keeps the raw speed', /12 Mb\/s/.test(headerTip || ''), headerTip);
      await open(page, base, 'l1', '?sel=host%2Fusb1');
      check('usb: a bus header names itself by its generation', (await page.locator('.d6-name').first().innerText()) === 'USB bus 1 · USB 2.0', await page.locator('.d6-name').first().innerText());
      await open(page, base, 'l1', '?view=table');
      const messages = await page.locator('td.quiet').allInnerTexts();
      check('usb: the Table message of a device on a port says the generation', messages.some((text) => /on port 1-3 · USB 1\.1/.test(text)) && messages.some((text) => /on port 7-1\.1\.1 · USB 2\.0/.test(text)), messages.filter((text) => /on port/.test(text)).slice(0, 4));
      const tip = await page.locator('td.quiet', { hasText: 'on port 1-3' }).first().getAttribute('title');
      check('usb: the Table message tooltip keeps the raw speed', /USB full-speed · 12 Mb\/s/.test(tip || ''), tip);
      await context.close();

      // every other layout's breadcrumb too (the host's one crumbs() feeds all of them)
      for (const layout of ['l2', 'l3', 'l4', 'l5']) {
        const other = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: 'light' });
        const tab = await other.newPage();
        tab.on('pageerror', (error) => errors.push(`usb ${layout}: ${error}`));
        const leaksHere = [];
        for (const selection of ['host', 'host/usb1/port-1-3', 'host/usb6/port-6-1/usb-6-1']) {
          await open(tab, base, layout, `?sel=${encodeURIComponent(selection)}`);
          leaksHere.push(...speedLines(await readable(tab)).map((line) => `${selection}: ${line}`));
        }
        check(`usb: no visible Mb/s or Gb/s in ${layout.toUpperCase()}`, leaksHere.length === 0, leaksHere.slice(0, 4));
        await other.close();
      }
    }

    // ------------------------------------------------------------------------------------------- 2. the Rate column
    const RATES = {
      'fdcanusb 272813EF': { received_per_s: 0.2, expected_per_s: 0.2, window_s: 30.0 },
      'D405 ·6036': { received_per_s: 0.05, expected_per_s: 0.5, window_s: 30.0 },
      'CAN 14': { received_per_s: null, expected_per_s: 0.0056, window_s: null },
    };
    const patchState = async (page) => {
      // WHY the feed held, unanswered: a page with a feed goes by its snapshot, so what is injected here reaches the page only on its polling fallback. Unanswered, not aborted: an abort logs a console error
      await page.route('**/api/v1/live', () => {});
      await page.route('**/api/v1/state*', async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        const walk = (node) => { if (RATES[node.name]) node.rate = RATES[node.name]; (node.children || []).forEach(walk); };
        walk(body.tree);
        await route.fulfill({ response, json: body });
      });
    };
    const rowCells = (page, label) => page.evaluate((text) => {
      const tr = [...document.querySelectorAll('table.grid tbody tr')].find((row) => row.querySelector('.row .name') && row.querySelector('.row .name').textContent.trim().startsWith(text));
      if (!tr) return null;
      const cell = tr.querySelector('td.ratecell');
      return cell ? { text: cell.innerText.replace(/\s+/g, ' ').trim(), title: cell.title, classes: [...cell.querySelectorAll('*')].map((el) => el.getAttribute('class') || ''), colours: [...cell.querySelectorAll('.ratenote')].map((el) => getComputedStyle(el).color), shapes: cell.querySelectorAll('svg').length } : { text: '', none: true };
    }, label);
    for (const scheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: scheme });
      const page = await context.newPage();
      page.on('pageerror', (error) => errors.push(`rate ${scheme}: ${error}`));
      page.on('console', (message) => { if (message.type() === 'error') errors.push(`rate ${scheme}: console ${message.text()}`); });
      await patchState(page);
      await open(page, base, 'l1', '?view=table');
      check(`rate ${scheme}: the Table has a Rate column`, (await page.locator('table.grid thead th', { hasText: /^Rate$/ }).count()) === 1);
      const steady = await rowCells(page, 'fdcanusb · 272813EF');
      check(`rate ${scheme}: "0.20 /s · expected 0.20" for a row that arrives as promised`, steady && steady.text === '0.20 /s · expected 0.20', steady);
      check(`rate ${scheme}: the raw numbers are the tooltip`, steady && /0\.2 records\/s received over the last 30 s; promised 0\.2 records\/s/.test(steady.title), steady && steady.title);
      const slow = await rowCells(page, 'RealSense D405 · 6036');
      check(`rate ${scheme}: under half of expected adds a quiet note`, slow && slow.text === '0.050 /s · expected 0.50 · slow' && /under half of what it promised/.test(slow.title), slow);
      check(`rate ${scheme}: the note is not a warning (no status shape, no level class, no tint)`,
        slow && slow.shapes === 0 && slow.classes.every((name) => !/\blv-|warn|error|stale/.test(name)) && new Set(slow.colours).size === 1, slow);
      const measuring = await rowCells(page, 'moteus-c1 · CAN 14');
      check(`rate ${scheme}: a row still being measured reads "—" and what it promises`, measuring && measuring.text === '— expected 0.0056', measuring);
      const none = await rowCells(page, 'ITE Device');
      check(`rate ${scheme}: structure with no periodic record shows nothing`, none && none.text === '', none);
      await page.mouse.move(5, 5);
      await page.screenshot({ path: path.join(SHOTS, `rate-column-${scheme}.png`) });
      // the same page on a USB selection: breadcrumb and header in the new words
      await page.unroute('**/api/v1/state*');
      await page.unroute('**/api/v1/live');
      await open(page, base, 'l1', '?sel=host%2Fusb1%2Fport-1-3');
      await page.screenshot({ path: path.join(SHOTS, `usb-labels-tree-${scheme}.png`) });
      await open(page, base, 'l1', '?view=table&sel=host%2Fusb1');
      await page.screenshot({ path: path.join(SHOTS, `usb-labels-table-${scheme}.png`) });
      await context.close();
    }

    // a live row reads a real, measured rate end to end (host receipt times -> history.rates -> node.rate -> cell)
    {
      const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: 'light' });
      const page = await context.newPage();
      page.on('pageerror', (error) => errors.push(`live rate: ${error}`));
      await open(page, base, 'l1', '?view=table');
      await page.waitForFunction(() => /\/s ·/.test([...document.querySelectorAll('td.ratecell')].map((cell) => cell.innerText).join(' ')), null, { timeout: 40000 }).catch(() => {});
      const live = await rowCells(page, 'fdcanusb · 272813EF');
      check('rate live: a fixture row reads a measured rate against its expected one', live && /^\d+(\.\d+)? \/s · expected 0\.50/.test(live.text), live);
      await context.close();
    }
    check('no page errors, no console errors', errors.length === 0, errors.slice(0, 5));
  } finally {
    await browser.close();
    stop(server.child);
  }
  console.log(`\n${passed}/${passed + failed} checks passed · screenshots in ${SHOTS}`);
  process.exit(failed ? 1 : 0);
}
main().catch((error) => { console.error(error); process.exit(1); });
