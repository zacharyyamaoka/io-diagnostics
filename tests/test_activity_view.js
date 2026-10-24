#!/usr/bin/env node
// Acceptance for the Activity log view (docs/activity-log-v1.md), logic in node and the REAL page in headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_activity_view.js
//
// It starts its own fixture hosts (a free port each, a temporary state dir, stopped by process group; never a plugin from
// plugins.d, never hardware): F1 (the rack's plug session, 22 events -> 17 rows), F1 started 30 s before its story (rows
// arrive live), and F2 with the terminal fixture plugin (a driver that prints like a tool) for the run rows. Checks:
//   core       the typed search (levels, fields, negation, quotes, regex, OR within a key / AND across keys), the filter
//              pipeline and what it hid, the volume bins, the scope rule, the terminal-output line assembler
//   view       one row per fact (a fault folded onto its cause, found-at-start grouped), calm colours, the old class names
//   filters    severity floor the search cannot lower, typed search, Hide / Highlight lists (remembered), time range
//   scope      an entity's log: children / this only / + path to host, Raw on inside it and off outside it
//   detail     a row opens, its folded events, Esc; the entity link opens its page
//   pause      the live tail: a new row arrives on its own, opening a row pauses it, "N new", Resume
//   boundaries every boundary drags, is remembered across a reload, double-click resets, arrow keys move it
//   runs (r4)  the run is one row, its output streams under it, the reply box answers the prompt, Stop, the main view
// Screenshots: reports/media/activity-log/*.png. Exits 1 on any failed check.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const core = require(path.join(ROOT, 'io_diagnostics', 'web', 'activity', 'core.js'));
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'activity-log');
const TERMINAL_PLUGINS = path.join(ROOT, 'tests', 'fixtures', 'plugins-terminal');
const VIEWPORT = { width: 1440, height: 900 };

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(54)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

// ------------------------------------------------------------------------------------------------------ core ----
function coreChecks() {
  const row = (over) => ({ id: over.id || Math.random().toString(36), severity: 'info', code: '', name: '', path: '', source: '', message: '', kind: '', type: '', port: '', hardware_id: '',
    channel: 'activity', args: {}, badges: [], members: [], stamp: 100, last_stamp: null, count: 1, ...over });
  const rows = [
    row({ id: 'a', severity: 'warn', code: 'UsbReset', name: 'Port 7-1.1.1', path: 'host/usb7/port-7-1.1.1', source: 'io_facts.kernel_log', message: '7-1.1.1: reset high-speed USB device number 4', kind: 'usb reset', type: 'usb_error', stamp: 10 }),
    row({ id: 'b', severity: 'info', code: 'Plugged', name: 'Port 1-5', path: 'host/usb1/port-1-5', source: 'io_facts.journal', message: 'fdcanusb plugged on 1-5', type: 'usb', stamp: 20 }),
    row({ id: 'c', severity: 'error', code: 'BoardFault', name: 'CAN 15 driver', path: 'host/usb1/port-1-3/usb-1-3/can/15/driver', source: 'moteus_servo', message: 'CAN 15 fault 33 (motor driver)', type: 'plugin', stamp: 30, badges: [{ fault: 'MoteusFault.33', label: 'fault confirmed' }] }),
    row({ id: 'd', severity: 'debug', code: 'LevelChanged', name: 'CAN 15', path: 'host/usb1/port-1-3/usb-1-3/can/15', source: 'history', message: 'CAN 15: ok → warn', type: 'level', stamp: 40 }),
    row({ id: 'e', severity: 'info', code: 'Unplugged', name: 'Port 1-5', path: 'host/usb1/port-1-5', source: 'io_facts.journal', message: 'fdcanusb unplugged from 1-5', type: 'usb', stamp: 50 }),
    row({ id: 'f', severity: 'info', code: '', name: 'Port 1-5', path: 'host/usb1/port-1-5', source: 'kernel', message: 'usb 1-5: USB disconnect, device number 8', channel: 'raw', type: 'raw', stamp: 55 }),
  ];
  const find = (text) => rows.filter((r) => core.matches(core.compile(text), r)).map((r) => r.id).join('');
  check('core: level:warn is that severity and above', find('level:warn') === 'ac' && find('level:error') === 'c' && find('level:debug') === 'abcdef', find('level:warn'));
  check('core: a field contains the text, case-insensitively', find('code:usb') === 'a' && find('entity:"port 1-5"') === 'bef' && find('source:moteus') === 'c' && find('type:level') === 'd');
  check('core: a minus negates, quotes keep spaces, /re/ is a regex', find('-code:Plug') === 'acdf' && find('"fdcanusb plugged"') === 'b' && find('code:/^(Plugged|Unplugged)$/') === 'be' && find('/fault\\s\\d+/') === 'c');
  check('core: one key is ORed, different keys are ANDed, bare words all must match', find('code:Plugged code:Unplugged') === 'be' && find('code:Plugged entity:CAN') === ''
    && find('fdcanusb 1-5') === 'be' && find('fdcanusb 7-1') === '');
  check('core: a fault badge is searchable, a bad regex never throws', find('fault:Moteus') === 'c' && find('code:/(/') === '');
  const lit = core.applyFilters(rows, { floor: 'info', preset: 'all', search: '', exclude: ['code:Unplugged'], highlight: ['entity:CAN'], onlyHighlighted: false, range: null });
  check('core: floor, Hide and Highlight, with the reason each row is hidden', lit.shown.map((r) => r.id).join('') === 'abcf' && lit.hidden.floor === 1 && lit.hidden.exclude === 1 && lit.lit.has('c') && lit.lit.size === 1, lit.hidden);
  const only = core.applyFilters(rows, { floor: 'debug', preset: 'all', search: '', exclude: [], highlight: ['entity:CAN'], onlyHighlighted: true, range: null });
  check('core: "highlighted only" keeps the highlighted rows', only.shown.map((r) => r.id).join('') === 'cd' && only.hidden.highlighted === 4, only.hidden);
  const preset = (name) => core.applyFilters(rows, { floor: 'debug', preset: name, search: '', exclude: [], highlight: [], range: null }).shown.map((r) => r.id).join('');
  check('core: the Faults view and the Runs view', preset('faults') === 'c' && preset('runs') === '' && preset('all') === 'abcdef');
  const ranged = core.applyFilters(rows, { floor: 'debug', preset: 'all', search: '', exclude: [], highlight: [], range: [15, 45] });
  check('core: a time range', ranged.shown.map((r) => r.id).join('') === 'bcd' && ranged.hidden.range === 3);
  const floored = core.applyFilters(rows, { floor: 'warn', preset: 'all', search: 'level:debug', exclude: [], highlight: [], range: null });
  check('core: the search cannot lower the floor', floored.shown.map((r) => r.id).join('') === 'ac', floored.shown.length);
  const bins = core.bins([...rows, row({ id: 'g', severity: 'warn', stamp: 12, count: 4 })], 0, 60, 6);
  check('core: the volume bins count a repeat as every event it stands for', bins[1].warn === 1 + 4 && bins[0].error + bins[0].warn + bins[0].quiet === 0 && bins[2].quiet === 1 && bins[3].error === 1 && bins[4].quiet === 1 && bins[5].quiet === 2,
    bins.map((b) => [b.error, b.warn, b.quiet]));
  const scope = (mode) => rows.filter((r) => core.inScope(r, { path: 'host/usb1/port-1-3/usb-1-3', mode })).map((r) => r.id).join('');
  check('core: the scope is the entity and what is below it, optionally the path up to the host', scope('children') === 'cd' && scope('self') === '' && scope('host') === 'cd'
    && rows.filter((r) => core.inScope(r, { path: 'host/usb1/port-1-5/usb-1-5', mode: 'host' })).map((r) => r.id).join('') === 'bef'
    && core.inScope({ path: 'host/x', hardware_id: 'usb:1' }, { path: 'host/y', mode: 'children', hardware_id: 'usb:1' }));
  const lines = core.createLineAssembler();
  const enc = new TextEncoder();
  lines.push(enc.encode('$ moteus_tool --calibrate\r\n\x1b[32mcalibrating\x1b[0m #     5%\rcalibrating ##    40%\rcalibrating ###  100%\r\nContinue? [y/N] '));
  check('core: output lines drop escapes, a \\r progress bar is one line, the prompt is the last line', JSON.stringify(lines.lines()) === JSON.stringify(['$ moteus_tool --calibrate', 'calibrating ###  100%', 'Continue? [y/N] ']), lines.lines());
  const bytes = enc.encode('naïve ✓\n');
  const split = core.createLineAssembler();
  split.push(bytes.slice(0, 3));
  split.push(bytes.slice(3));
  split.push('a\bb\n\x1b]0;title\x07done\n');
  check('core: a multi-byte character split across two chunks is one character; backspace and OSC are handled', JSON.stringify(split.lines()) === JSON.stringify(['naïve ✓', 'b', 'done']), split.lines());
  const many = core.createLineAssembler(5);
  for (let i = 0; i < 20; i += 1) many.push(`line ${i}\n`);
  check('core: the assembler keeps the newest lines', many.lines().length === 5 && many.lines()[4] === 'line 19');
}

// ---------------------------------------------------------------------------------------------------- servers ----
function startServer(stateDir, args, extraEnv = {}) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, ...extraEnv };
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
const tmp = (name) => fs.mkdtempSync(path.join(os.tmpdir(), `activity-${name}-`));

async function openActivity(browser, base, { storage = true, init } = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  if (init) await context.addInitScript(init);
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('request', (request) => requests.push(request.url()));
  await page.goto(`${base}/?view=activity`);
  await page.waitForSelector('#activity-host .al-row', { timeout: 30000 });
  await page.waitForTimeout(400);
  return { page, context, errors, requests };
}
const rowsOf = (page) => page.evaluate(() => [...document.querySelectorAll('#activity-host .al-row')].map((row) => ({
  id: row.dataset.alRow, code: (row.querySelector('.kind') || {}).textContent || '', name: row.querySelector('.al-entbtn').textContent, sev: row.querySelector('.al-sevw').textContent,
  diagnostic: row.hasAttribute('data-diagnostic'), raw: row.classList.contains('al-raw'), hl: row.classList.contains('al-hl'), text: row.querySelector('.al-msg').innerText })));
const codes = async (page) => (await rowsOf(page)).map((row) => row.code);
const footer = (page) => page.locator('#activity-host .al-foot').innerText();
async function search(page, text) { await page.fill('#activity-host .al-searchbox', text); await page.waitForTimeout(150); }
async function dragBy(page, selector, dx, dy) {
  const box = await page.locator(selector).first().boundingBox();
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx / 2, y + dy / 2, { steps: 5 });
  await page.mouse.move(x + dx, y + dy, { steps: 5 });
  await page.mouse.up();
  await page.waitForTimeout(150);
}
const pref = (page, key) => page.evaluate((k) => { try { return JSON.parse(localStorage.getItem(`io-diagnostics.activity.${k}`)); } catch (_) { return null; } }, key);

// ------------------------------------------------------------------------------------------------------ page ----
async function frameChecks(browser, base) {
  const { page, context, errors, requests } = await openActivity(browser, base);
  try {
    // ---- the view: one row per fact
    const rows = await rowsOf(page);
    const raw = await (await fetch(`${base}/api/v1/activity?limit=500`)).json();
    const all = rows.map((row) => row.code);
    check('view: 22 events are 17 rows (a fault rides on its cause, the start is one row)', rows.length === 17 && raw.events.length === 22, { rows: rows.length, events: raw.events.length });
    check('view: no FaultConfirmed row; FoundAtStart is one row of four faults', !all.includes('FaultConfirmed') && all.filter((code) => code === 'FoundAtStart').length === 1
      && /4 faults already confirmed/.test(rows.find((row) => row.code === 'FoundAtStart').text), all);
    const badges = await page.evaluate(() => [...document.querySelectorAll('#activity-host .al-row')].map((row) => [(row.querySelector('.kind') || {}).textContent, [...row.querySelectorAll('.al-badge')].map((b) => b.textContent)]).filter(([, b]) => b.length));
    check('view: "→ fault confirmed" is a badge on the BusErrorPassive and BoardFault rows', JSON.stringify(badges) === JSON.stringify([['BusErrorPassive', ['→ fault confirmed']], ['BoardFault', ['→ fault confirmed']]]), badges);
    check('view: newest first, the boot is a separator-like row at the end', all[0] === 'FoundAtStart' && all[all.length - 1] === 'Booted', all);
    const old = await page.evaluate(() => ({ act: Boolean(document.querySelector('.act .ev')), h2: Boolean(document.querySelector('.act h2')), filter: Boolean(document.querySelector('.act-filter')),
      kinds: document.querySelectorAll('.act .ev .kind').length }));
    check('view: keeps the class names the colour gates measure (.act .ev .kind, h2, .act-filter)', old.act && old.h2 && old.filter && old.kinds >= 10, old);
    const calm = rows.every((row) => row.diagnostic === (row.sev === 'warn' || row.sev === 'error' || row.sev === 'fatal'));
    check('view: only warn and error rows are diagnostics (the only ones that may carry a warning colour)', calm && rows.filter((row) => row.diagnostic).length === 7, rows.filter((row) => row.diagnostic).length);
    const summary = await page.locator('#activity-host .al-count').innerText();
    check('view: the first screen answers "is it OK?" in one line', /warning/.test(summary) && /last hour/.test(summary), summary);
    check('view: the page asked for the folded rows and listens to the stream', requests.some((url) => /api\/v1\/activity\?.*fold=1/.test(url)) && requests.some((url) => /api\/v1\/events\?.*fold=1/.test(url)), requests.filter((url) => /api\/v1/.test(url)).slice(0, 4));
    await page.screenshot({ path: path.join(SHOTS, 'a1-default.png') });

    // ---- the severity floor and the typed search
    await page.click('[data-al-floor="warn"]');
    const warn = await rowsOf(page);
    check('filters: Warn+ shows only warn and worse, and says what it hid', warn.length === 7 && warn.every((row) => row.diagnostic) && /10 hidden/.test(await footer(page)), { rows: warn.length, foot: await footer(page) });
    await search(page, 'level:debug');
    check('filters: the search cannot lower the severity floor', (await rowsOf(page)).length === 7 && /cannot go below the severity floor/.test(await footer(page)), await footer(page));
    await search(page, '');
    await page.click('[data-al-floor="info"]');
    await search(page, 'code:Usb');
    const usb = await codes(page);
    check('filters: code:Usb finds the three kernel USB rows', usb.length === 3 && usb.every((code) => code.startsWith('Usb')), usb);
    await search(page, '-code:Usb');
    check('filters: -code:Usb hides them', (await rowsOf(page)).length === 14);
    await search(page, 'code:Plugged code:Unplugged');
    check('filters: the same key is ORed', (await codes(page)).sort().join() === 'Plugged,Plugged,Unplugged', await codes(page));
    await search(page, 'level:warn entity:"Port 6-2"');
    check('filters: different keys are ANDed, a quoted value keeps its space', (await codes(page)).join() === 'ReEnumerated', await codes(page));
    await search(page, 'fdcanusb 1-5');
    check('filters: bare words all have to match (message, code, entity, source)', (await codes(page)).sort().join() === 'LinkSpeed,Plugged', await codes(page));
    await search(page, 'zzz-nothing');
    check('filters: nothing matches: a calm empty state says why', /not matching the search/.test(await page.locator('#activity-host .al-empty').innerText()), await page.locator('#activity-host .al-empty').innerText());
    await search(page, '');

    // ---- Hide and Highlight (rqt_console's exclude and highlight lists), remembered
    await page.click('[data-al-opts]');
    await page.fill('[data-al-add="exclude"]', 'code:LinkSpeed');
    await page.press('[data-al-add="exclude"]', 'Enter');
    await page.fill('[data-al-add="highlight"]', 'entity:"CAN 15"');
    await page.press('[data-al-add="highlight"]', 'Enter');
    await page.waitForTimeout(150);
    const lists = await rowsOf(page);
    check('filters: Hide drops the rows and shows a chip; Highlight marks the rest', !lists.some((row) => row.code === 'LinkSpeed') && lists.length === 14 && lists.filter((row) => row.hl).length >= 1
      && await page.locator('.al-chip:has-text("Hide")').count() === 1 && await page.locator('.al-chip:has-text("Highlight")').count() === 1, { rows: lists.length, hl: lists.filter((row) => row.hl).length });
    await page.check('[data-al-onlyhl]');
    const onlyHl = await rowsOf(page);
    check('filters: "highlighted only" keeps the highlighted rows', onlyHl.length >= 1 && onlyHl.every((row) => row.hl), onlyHl.length);
    await page.uncheck('[data-al-onlyhl]');
    await page.keyboard.press('Escape');
    await page.reload();
    await page.waitForSelector('#activity-host .al-row');
    check('filters: the Hide and Highlight lists survive a reload, the search does not', await page.locator('.al-chip:has-text("code:LinkSpeed")').count() === 1 && (await rowsOf(page)).length === 14
      && await page.locator('#activity-host .al-searchbox').inputValue() === '', (await rowsOf(page)).length);
    await page.click('[data-al-opts]');
    await page.click('[data-al-reset]');
    await page.keyboard.press('Escape');
    check('filters: Clear all filters brings every row back', (await rowsOf(page)).length === 17);

    // ---- the entity's own log: scope and Raw
    check('scope: Raw is off outside an entity\'s own log', (await rowsOf(page)).every((row) => !row.raw) && await page.getAttribute('[data-al-raw]', 'aria-pressed') === 'false');
    await page.click('.al-entbtn:has-text("Port 1-7") >> nth=0');
    await page.waitForFunction(() => document.querySelectorAll('#activity-host .al-raw').length > 3, null, { timeout: 15000 });
    const scoped = await rowsOf(page);
    check('scope: clicking an entity opens its log: its rows, and Raw (its kernel lines) is on', scoped.every((row) => row.name === 'Port 1-7') && scoped.some((row) => row.raw && /device descriptor read\/64, error -71/.test(row.text))
      && await page.getAttribute('[data-al-raw]', 'aria-pressed') === 'true' && /Log of\s+Port 1-7/.test(await page.locator('.al-scope').innerText()), { n: scoped.length, raw: scoped.filter((row) => row.raw).length });
    check('scope: the coded events stay (one row per fact) beside the raw lines', scoped.filter((row) => !row.raw).map((row) => row.code).sort().join() === 'UsbConfigFailed,UsbDescriptorReadError', scoped.filter((row) => !row.raw).map((row) => row.code));
    await page.screenshot({ path: path.join(SHOTS, 'a2-entity-log.png') });
    await page.click('[data-al-mode="host"]');
    await page.waitForFunction(() => [...document.querySelectorAll('#activity-host .al-entbtn')].some((b) => b.textContent === 'bam-GPU'), null, { timeout: 15000 });
    check('scope: "+ path to host" adds the ports, hubs and computer above it', (await rowsOf(page)).some((row) => row.code === 'Booted'), (await rowsOf(page)).length);
    await page.click('[data-al-mode="self"]');
    await page.waitForTimeout(700);
    check('scope: "this only" is the entity alone', (await rowsOf(page)).every((row) => row.name === 'Port 1-7'));
    await page.click('[data-al-raw]');
    await page.waitForTimeout(500);
    check('scope: Raw can be turned off inside an entity\'s log', (await rowsOf(page)).every((row) => !row.raw));
    await page.click('[data-al-clear-scope]');
    await page.waitForFunction(() => document.querySelectorAll('#activity-host .al-row').length >= 17, null, { timeout: 15000 });
    check('scope: the cross clears it, and Raw is off again', (await rowsOf(page)).every((row) => !row.raw) && await page.getAttribute('[data-al-raw]', 'aria-pressed') === 'false');

    // ---- the detail
    await page.click('.al-row:has(.kind:text-is("BusErrorPassive")) .al-msg');
    await page.waitForSelector('.al-detail-open .al-detail .al-d-title');
    const detail = await page.locator('.al-detail').innerText();
    check('detail: a row opens its detail: what it folds, its details, the actions', /Folded into this row/i.test(detail) && /NoAck confirmed/.test(detail) && /Hide BusErrorPassive/.test(detail) && /Open its page/.test(detail) && /serial/.test(detail), detail.slice(0, 200));
    check('detail: opening a row pauses the live tail', /Paused/.test(await page.locator('[data-al-live]').innerText()));
    await page.screenshot({ path: path.join(SHOTS, 'a3-detail.png') });
    await page.click('.al-detail [data-al-hide]');
    check('detail: "Hide BusErrorPassive" hides the code', !(await codes(page)).includes('BusErrorPassive'));
    await page.click('.al-chip:has-text("BusErrorPassive") .al-x-btn');
    check('detail: dropping the Hide chip brings the row back, its detail still open', (await codes(page)).includes('BusErrorPassive') && await page.locator('.al-detail-open').count() === 1);
    await page.focus('#activity-host .al-list');
    await page.keyboard.press('Escape');
    check('detail: Escape closes it', await page.locator('.al-detail-open').count() === 0);
    await page.click('.al-row:has(.kind:text-is("BusErrorPassive")) .al-msg');
    await page.waitForSelector('.al-detail a[data-go]');
    await page.click('.al-detail a[data-go]');
    await page.waitForFunction(() => S.view === 'tree' && S.sel && /\/can$/.test(S.sel), null, { timeout: 10000 });
    check('detail: "Open its page" goes to the entity', await page.evaluate(() => S.view === 'tree' && /usb-1-5\/can$/.test(S.sel)), await page.evaluate(() => S.sel));
    await page.click('[data-view="activity"]');
    await page.waitForSelector('#activity-host .al-row');

    // ---- the volume strip
    const strip = await page.evaluate(() => ({ bars: document.querySelectorAll('#activity-host .al-bars > b').length, warn: document.querySelectorAll('#activity-host .al-bars i.w').length,
      error: document.querySelectorAll('#activity-host .al-bars i.e').length, diag: document.querySelectorAll('#activity-host .al-bars i.w[data-diagnostic], #activity-host .al-bars i.e[data-diagnostic]').length }));
    check('strip: volume by severity over time', strip.bars === 72 && strip.warn >= 1 && strip.error >= 1 && strip.diag === strip.warn + strip.error, strip);
    // WHY not locator.boundingBox: the strip is redrawn when the clock moves, and that call returns null between two draws
    const box = await page.evaluate(() => { const r = document.querySelector('#activity-host .al-bars').getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; });
    await page.mouse.move(box.x + box.width * 0.55, box.y + 8);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 1.0, box.y + 8, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(200);
    const timed = (await rowsOf(page)).length;
    check('strip: dragging across it selects a time range; the chip clears it', timed < 17 && timed >= 1 && await page.locator('.al-chip:has-text("Time")').count() === 1, timed);
    await page.click('[data-al-clear-range]');
    check('strip: the range clears', (await rowsOf(page)).length === 17);

    // ---- every boundary drags and is remembered
    await page.click('.al-row:has(.kind:text-is("Plugged")) >> nth=0');
    await page.waitForSelector('.al-detail-open .al-sash');
    const w0 = await page.locator('.al-detail').evaluate((el) => el.getBoundingClientRect().width);
    await dragBy(page, '.al-sash', -120, 0);
    const w1 = await page.locator('.al-detail').evaluate((el) => el.getBoundingClientRect().width);
    check('boundaries: the detail boundary drags', w1 - w0 > 90 && w1 - w0 < 150, { w0, w1 });
    await page.focus('.al-sash');
    await page.keyboard.press('ArrowLeft');
    const w2 = await page.locator('.al-detail').evaluate((el) => el.getBoundingClientRect().width);
    check('boundaries: the arrow keys move a focused boundary', w2 > w1, { w1, w2 });
    await dragBy(page, '.al-hsash', 0, 40);
    const stripH = await pref(page, 'strip-h');
    check('boundaries: the volume strip boundary drags', stripH > 60, stripH);
    const colBefore = await page.locator('.al-list').evaluate((el) => getComputedStyle(el).getPropertyValue('--al-w-ent'));
    await dragBy(page, '[data-al-col="ent"]', 50, 0);
    const colAfter = await page.locator('.al-list').evaluate((el) => getComputedStyle(el).getPropertyValue('--al-w-ent'));
    const entCss = await page.evaluate(() => document.querySelector('#activity-host').style.getPropertyValue('--al-w-ent'));
    check('boundaries: a column drags', parseInt(colAfter, 10) - parseInt(colBefore, 10) >= 40, { colBefore, colAfter, entCss });
    await page.reload();
    await page.waitForSelector('#activity-host .al-row');
    const remembered = await page.evaluate(() => ({ detail: JSON.parse(localStorage.getItem('io-diagnostics.activity.detail-w')), strip: JSON.parse(localStorage.getItem('io-diagnostics.activity.strip-h')),
      cols: JSON.parse(localStorage.getItem('io-diagnostics.activity.cols')), ent: document.querySelector('#activity-host').style.getPropertyValue('--al-w-ent'), stripVar: document.querySelector('#activity-host').style.getPropertyValue('--al-strip-h') }));
    check('boundaries: the sizes are remembered across a reload', remembered.detail > 380 && remembered.strip > 60 && remembered.cols.ent > 150 && remembered.stripVar === `${remembered.strip}px` && parseInt(remembered.ent, 10) === remembered.cols.ent, remembered);
    await page.click('.al-row:has(.kind:text-is("Plugged")) >> nth=0');
    await page.waitForSelector('.al-detail-open .al-sash');
    await page.dblclick('.al-sash');
    await page.dblclick('.al-hsash');
    await page.dblclick('[data-al-col="ent"]');
    const reset = await page.evaluate(() => ({ detail: JSON.parse(localStorage.getItem('io-diagnostics.activity.detail-w')), strip: JSON.parse(localStorage.getItem('io-diagnostics.activity.strip-h')),
      cols: JSON.parse(localStorage.getItem('io-diagnostics.activity.cols')) }));
    check('boundaries: double-click resets each to its default', reset.detail === 380 && reset.strip === 44 && !reset.cols.ent, reset);
    check('no page errors in the frame checks', errors.length === 0, errors.slice(0, 3));
  } finally {
    await context.close();
  }
}

// What the Activity view does with the same story arriving while it is open: the live tail, and what opening a row does to it.
async function liveChecks(browser, base) {
  const { page, context, errors } = await openActivity(browser, base);
  try {
    const first = (await rowsOf(page)).length;
    // WHY the host is started 30 s before its recording: its rows arrive on the clock, so this is a plug in front of you
    await page.waitForFunction((n) => document.querySelectorAll('#activity-host .al-row').length > n, first, { timeout: 60000 });
    const grew = (await rowsOf(page)).length;
    check('pause: a row that happens while you look arrives on its own', grew > first, { first, grew });
    await page.click('#activity-host .al-row .al-msg >> nth=0');
    const frozen = (await rowsOf(page)).map((row) => row.id);
    check('pause: opening a row pauses the tail', /Paused/.test(await page.locator('[data-al-live]').innerText()), await page.locator('[data-al-live]').innerText());
    await page.waitForFunction(() => /\d+ new/.test(document.querySelector('[data-al-live]').textContent), null, { timeout: 60000 }).catch(() => {});
    const label = await page.locator('[data-al-live]').innerText();
    const still = (await rowsOf(page)).map((row) => row.id);
    check('pause: new rows are counted, not slid under your cursor', /\d+ new/.test(label) && JSON.stringify(still) === JSON.stringify(frozen), { label, frozen: frozen.length, still: still.length });
    await page.screenshot({ path: path.join(SHOTS, 'a4-paused.png') });
    await page.click('[data-al-live]');
    await page.waitForTimeout(400);
    const resumed = (await rowsOf(page)).length;
    check('pause: Resume brings them in and the tail is live again', resumed > frozen.length && (await page.locator('[data-al-live]').innerText()) === 'Live', { frozen: frozen.length, resumed });
    check('no page errors in the live checks', errors.length === 0, errors.slice(0, 3));
  } finally {
    await context.close();
  }
}

// ------------------------------------------------------------------------------------------------- r4: runs ----
async function utilities(page) {
  await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 20000 });
  if (!(await page.$('.ent-menu'))) await page.click('#ent-util-btn');
  await page.waitForSelector('.ent-menu [data-util]', { timeout: 5000 });
}
async function startCalibration(page) {
  await utilities(page);
  await page.click('[data-util="calibrate"]');
  await page.waitForSelector('.ent-sheet [data-util-run]', { timeout: 10000 });
  await page.click('.ent-sheet [data-util-run]');
  await page.waitForSelector('#modal [data-util-tick]');
  await page.check('#modal [data-util-tick]');
  await page.click('#modal [data-util-confirm]');
}
async function runChecks(browser, base) {
  const find = (node, name) => (node.name === name ? node : node.children.map((child) => find(child, name)).find(Boolean));
  let can1;
  for (let i = 0; i < 120 && !can1; i += 1) { // the host answers once its first refresh has found the fixture board
    can1 = find((await (await fetch(`${base}/api/v1/state`)).json()).tree, 'CAN 1');
    if (!can1) await new Promise((resolve) => setTimeout(resolve, 500));
  }
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  await context.addInitScript(() => { try { if (!sessionStorage.getItem('al-test')) { sessionStorage.setItem('al-test', '1'); localStorage.setItem('io-diagnostics.runs-view', 'r4'); } } catch (_) { /* no storage */ } });
  const page = await context.newPage();
  const errors = [];
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('pageerror', (error) => errors.push(String(error)));
  try {
    await page.goto(`${base}/?sel=${encodeURIComponent(can1.path)}`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.runView === 'r4' && document.querySelector('.d-title'), null, { timeout: 30000 });
    const seam = await page.evaluate(() => ({ view: Object.keys(window.IORunViews.r4).sort().join(), ids: [...document.getElementById('run-view').options].map((o) => o.value),
      name: window.IORunViews.r4.name, toggle: Boolean(document.querySelector('#runs-status .r4-toggle')), rootHidden: document.getElementById('runs-root').hidden }));
    check('r4: registered against the frozen seam and chosen in the Runs select', ['mount', 'unmount', 'update', 'open', 'runLine'].every((member) => seam.view.split(',').includes(member)) && seam.ids.includes('r4')
      && seam.name === 'Runs in the Activity log' && seam.toggle && seam.rootHidden, seam);
    const index = await (await fetch(`${base}/runs/index.json`)).json();
    check('r4: its line in runs/index.json is ready', index.runs.find((entry) => entry.id === 'r4').ready === true);

    await startCalibration(page);
    await page.waitForSelector('#runs-root:not([hidden]) .al-row', { timeout: 20000 });
    await page.waitForFunction(() => /Continue\? \[y\/N\]/.test((document.querySelector('#runs-root .al-out-pre') || {}).textContent || ''), null, { timeout: 30000 });
    const live = await page.evaluate(() => ({ rows: [...document.querySelectorAll('#runs-root .al-row')].map((row) => [(row.querySelector('.kind') || {}).textContent, row.querySelector('.al-msg').innerText]),
      out: document.querySelector('#runs-root .al-out-pre').textContent, stop: (document.querySelector('#runs-root [data-al-stop]') || {}).textContent, reply: Boolean(document.querySelector('#runs-root .al-in input')),
      title: document.querySelector('.r4-title').textContent, meta: document.querySelector('.r4-meta').textContent, badge: document.querySelector('.r4-badge').textContent }));
    check('r4: the run is ONE row of the entity\'s log, running, with Show output', live.rows.length === 1 && live.rows[0][0] === 'Run' && /Run calibration started on CAN 1/.test(live.rows[0][1]) && /running…/.test(live.rows[0][1]), live.rows);
    check('r4: its output streams under the row as lines (a \\r progress bar is one line)', /moteus_tool/.test(live.out) && (live.out.match(/calibrating #/g) || []).length === 1 && /Continue\? \[y\/N\]/.test(live.out), live.out.slice(-160));
    check('r4: the panel says whose log it is and what runs; Stop says the motor goes limp; the toggle is badged', live.title === 'Log of CAN 1' && /Run calibration · running/.test(live.meta) && live.stop === 'Stop (motor goes limp)' && live.badge === '1' && live.reply, live);
    await page.screenshot({ path: path.join(SHOTS, 'r4-running.png') });
    await page.fill('#runs-root .al-in input', 'y');
    await page.press('#runs-root .al-in input', 'Enter');
    await page.waitForFunction(() => /done/.test((document.querySelector('#runs-root .al-row .al-msg') || {}).textContent || ''), null, { timeout: 30000 });
    await page.waitForTimeout(500);
    const done = await page.evaluate(() => ({ rows: [...document.querySelectorAll('#runs-root .al-row')].map((row) => row.querySelector('.al-msg').innerText),
      out: (document.querySelector('#runs-root .al-out-pre') || {}).textContent || '', reply: Boolean(document.querySelector('#runs-root .al-in')), meta: document.querySelector('.r4-meta').textContent }));
    check('r4: typing y answers the prompt: the same row turns done, the reply box goes, the output stays', done.rows.length === 1 && /done after/.test(done.rows[0]) && !done.reply && /Continue\? \[y\/N\] y/.test(done.out) && /done/.test(done.meta), done);
    await page.screenshot({ path: path.join(SHOTS, 'r4-done.png') });

    // the same run in the Activity view: one row; Show output opens it there
    await page.click('[data-view="activity"]');
    await page.waitForSelector('#activity-host .al-row');
    const mainRuns = await page.evaluate(() => [...document.querySelectorAll('#activity-host .al-row')].filter((row) => (row.querySelector('.kind') || {}).textContent === 'Run').map((row) => row.querySelector('.al-msg').innerText));
    check('r4: the Activity view has the run as one row too (never RunStarted and RunEnded)', mainRuns.length === 1 && /done after/.test(mainRuns[0])
      && !(await codes(page)).some((code) => code === 'RunStarted' || code === 'RunEnded'), mainRuns);
    await page.click('#activity-host [data-run-open]');
    await page.waitForSelector('#activity-host .al-out-pre', { timeout: 15000 });
    await page.waitForFunction(() => /calibrating/.test(document.querySelector('#activity-host .al-out-pre').textContent), null, { timeout: 15000 });
    check('r4: "Show output" on the row opens its output in the Activity view', /Continue\? \[y\/N\] y/.test(await page.locator('#activity-host .al-out-pre').textContent()));
    await page.screenshot({ path: path.join(SHOTS, 'r4-activity.png') });
    await page.click('#activity-host [data-al-close], #activity-host .al-row:has(.kind:text-is("Run")) .al-msg');
    check('r4: the Runs filter shows runs only', await (async () => { await page.click('[data-al-preset="runs"]'); const kinds = await codes(page); await page.click('[data-al-preset="all"]'); return kinds.length >= 1 && kinds.every((code) => code === 'Run'); })());

    // Stop, from the row
    await page.click('[data-view="tree"]');
    await page.waitForSelector('#ent-util-btn:not([disabled])');
    await startCalibration(page);
    await page.waitForSelector('#runs-root .al-out [data-al-stop]', { timeout: 20000 });
    await page.click('#runs-root [data-al-stop]');
    await page.waitForFunction(() => /stopped/.test((document.querySelector('#runs-root .al-row .al-msg') || {}).textContent || ''), null, { timeout: 30000 });
    const stopped = await page.evaluate(() => ({ row: document.querySelector('#runs-root .al-row .al-msg').innerText, sev: document.querySelector('#runs-root .al-row .al-sevw').textContent,
      out: (document.querySelector('#runs-root .al-out-pre') || {}).textContent || '' }));
    check('r4: Stop on the row ends the run (stopped, a warn row) and its output says the plugin\'s stop was sent', /stopped after/.test(stopped.row) && stopped.sev === 'warn' && /CAN 1/.test(stopped.out), stopped);

    // the panel boundary drags, is remembered, resets
    const h0 = await page.locator('#runs-root').evaluate((el) => el.getBoundingClientRect().height);
    await dragBy(page, '.r4-sash', 0, -90);
    const h1 = await page.locator('#runs-root').evaluate((el) => el.getBoundingClientRect().height);
    await page.reload();
    await page.waitForFunction(() => typeof S !== 'undefined' && S.runView === 'r4' && document.querySelector('.d-title'), null, { timeout: 30000 });
    const h2 = await page.locator('#runs-root').evaluate((el) => el.getBoundingClientRect().height);
    check('r4: the panel boundary drags and the height is remembered across a reload', h1 - h0 > 60 && Math.abs(h2 - h1) < 3, { h0, h1, h2 });
    // another view mounts, r4 unmounts cleanly, and r4 comes back
    await page.selectOption('#run-view', 'r1');
    const away = await page.evaluate(() => ({ r4class: document.getElementById('runs-root').classList.contains('r4'), toggle: Boolean(document.querySelector('#runs-status .r4-toggle')), r1: Boolean(document.querySelector('#runs-status .r1-toggle')),
      attached: Boolean(window.IOActivityLog.runs) }));
    // an entity's own log shows its runs' output whichever run view is chosen (the log reads the runs runtime itself, r4 or not)
    await page.click('[data-view="activity"]');
    await page.waitForSelector('#activity-host .al-row');
    await page.click('#activity-host .al-entbtn:has-text("CAN 1") >> nth=0');
    await page.waitForSelector('#activity-host .al-out-pre', { timeout: 15000 });
    await page.waitForFunction(() => /moteus_tool/.test(document.querySelector('#activity-host .al-out-pre').textContent), null, { timeout: 15000 });
    check('runs: with r1 chosen, the entity\'s own log still shows its runs\' output under their rows (Raw is on in it)', await page.locator('#activity-host .al-out').count() >= 1
      && await page.getAttribute('[data-al-raw]', 'aria-pressed') === 'true', await page.locator('#activity-host .al-out').count());
    await page.click('[data-al-clear-scope]');
    await page.click('[data-view="tree"]');
    await page.selectOption('#run-view', 'r4');
    const back = await page.evaluate(() => ({ r4class: document.getElementById('runs-root').classList.contains('r4'), toggle: Boolean(document.querySelector('#runs-status .r4-toggle')), attached: Boolean(window.IOActivityLog.runs) }));
    check('r4: unmount removes everything it added (and the log loses its output provider); mounting again restores it', !away.r4class && !away.toggle && away.r1 && !away.attached && back.r4class && back.toggle && back.attached, { away, back });
    check('no page errors in the run checks', errors.length === 0, errors.slice(0, 3));
  } finally {
    await context.close();
  }
}

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  coreChecks();
  if (process.env.ACTIVITY_CORE_ONLY) { // the logic alone, in node: what the mutation checks run against
    console.log(`\n${passed}/${passed + failed} checks passed`);
    process.exit(failed ? 1 : 0);
  }
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const servers = [];
  try {
    const frame = await startServer(tmp('frame'), ['--fixture', 'F1']);
    servers.push(frame);
    await frameChecks(browser, frame.base);
    // WHY started only now: its story runs on the clock from the moment it starts (--offset -30: the plug is 22 s away)
    const live = await startServer(tmp('live'), ['--fixture', 'F1', '--offset', '-30']);
    servers.push(live);
    await liveChecks(browser, live.base);
    const terminal = await startServer(tmp('terminal'), ['--fixture', 'F2', '--plugins', TERMINAL_PLUGINS], { TERMINAL_STEP_S: '0.15', TERMINAL_LOG: path.join(tmp('terminal-log'), 'terminal.jsonl') });
    servers.push(terminal);
    const plugins = await (await fetch(`${terminal.base}/api/v1/plugins`)).json();
    const names = plugins.plugins.map((plugin) => plugin.name);
    check('only the fixture plugin is loaded on the run host', names.join() === 'terminal_fixture', names);
    if (names.join() !== 'terminal_fixture') throw new Error('refusing to press anything: a non-fixture plugin is loaded');
    await runChecks(browser, terminal.base);
  } finally {
    await browser.close();
    servers.forEach((server) => stopServer(server.child));
  }
  console.log(`\n${passed}/${passed + failed} checks passed · screenshots in ${SHOTS}`);
  process.exit(failed ? 1 : 0);
}
main().catch((error) => { console.error(error); process.exit(1); });
