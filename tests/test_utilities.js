#!/usr/bin/env node
// Acceptance for IO diagnostics round 2 integration, in the REAL page (web/index.html + app.js) on a fixture server,
// headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_utilities.js
//
// It starts its own `cli.py serve --fixture F1 --port 0` (a free port, stopped by its process group), and refuses to
// press anything unless /api/v1/plugins lists only the fixture plugin v6_scenario (which never touches hardware: its
// act answers from the recording). Checks:
//   defaults     a fresh browser: structure p1, tree v6, skin claude, mode follows the OS; a remembered choice wins
//   attention    the top line states each cause's plain words and the first one's numbered what_to_check
//   why          the detail panel shows what_to_check for the selection and for a folded part (the CAN bus)
//   absent       an expected-but-absent device (ExpectedAbsent, injected into /state and /node) is a row in every
//                structure, on the attention line with its checklist and kernel evidence
//   utilities    the printer-utility card: icon + title + description per action, Checks first, Maintenance, Motion
//                (set apart), More; every click is a dry run (POST /api/v1/act dry_run) showing argv, steps,
//                preconditions and the confirm text; a query runs with one more click and shows its result; moves
//                needs the modal's box ticked and a button labelled with the action
//   set_id       its row's "New id (1-126)" box keeps the dry run disabled until it holds a valid id; the dry run,
//                the modal ("CAN 15 → CAN 7") and the run carry that id; typing another closes the dry run
//   server       a fetch without the page header, or without a dry run's token, is refused with a plain reason
//   seams        subtitle / suffix (p3), attrs (p4), lead (p5), toggles[].when (p2), box guides (Terminal), mono
//                columns and the folded Connection card (p1)
//   expected     (a second F1 server with its own temporary rig file, --expected) the rig row first with its counts and
//                what is missing, the retired 28B3A25D a warn row with its reason, the Expected setup card's Retire…
//                (reason first, Cancel writes nothing), Expect what's here and Forget rewriting the file
// Screenshots: reports/media/round2/integration-*.png, expected-*.png. Exits 1 on any failed check. Nothing opens a
// CAN adapter.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'round2');
const VIEWPORT = { width: 1600, height: 1000 };
const ADAPTER = 'host/usb1/port-1-3/usb-1-3';
const CAN15 = `${ADAPTER}/can/15`;
const SECOND = 'host/usb1/port-1-5/usb-1-5';
const ABSENT = 'host/expected/usb-0483-5740-28b3a25d-test';

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(34)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

function startServer(fixture, extra = []) {
  const env = { ...process.env };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--fixture', fixture, '--port', '0', '--no-udev', ...extra], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stopServer(child); reject(new Error(`${fixture} did not start: ${output}`)); }, 60000);
    const onData = (chunk) => {
      output += chunk;
      const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//);
      if (found) { clearTimeout(timer); resolve({ child, base: found[1] }); }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`${fixture} exited (${code}): ${output}`)); });
  });
}
const stopServer = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };

async function ready(page, selector = '.t6-row') {
  await page.waitForFunction((sel) => typeof S !== 'undefined' && S.built && S.built.flat.some((row) => row.parts.length)
    && document.querySelector(sel) && document.querySelector('.d-title'), selector, { timeout: 30000 });
  await page.waitForTimeout(400);
}

async function open(page, base, sel) {
  await page.goto(`${base}/${sel ? `?sel=${encodeURIComponent(sel)}` : ''}`);
  await ready(page);
  if (sel) await page.waitForFunction((p) => S.node && S.node.node.path === p, sel, { timeout: 15000 });
  await page.waitForTimeout(300);
}

// ----------------------------------------------------------------------------------- ExpectedAbsent ----
function injectAbsent(state) {
  const node = {
    path: ABSENT, name: 'fdcanusb 28B3A25D', kind: 'device', icon: 'usb', parent: 'host/expected', edge: 'via', depth: 2,
    level: 'warn', message: 'expected (last seen 2026-10-21 18:24, rack_inventory) · not on USB · the kernel saw no plug event since boot: try a data cable or another port',
    own: { level: 'warn', message: 'expected · not on USB' }, rollup: { level: 'warn', origin: ABSENT, origin_name: 'fdcanusb 28B3A25D', message: 'not on USB', counts: { warn: 1 }, knock_on: [] },
    hint: null, knock_on_of: null, stale: false, connected: true, hardware_id: 'usb:0483:5740:28B3A25D', identity: {}, owner: 'moteus_servo', id_text: '',
    last_received: null, age_s: 0, expected_period_s: null, stale_after_s: null, checks: 1, actions: 0, faults: 0, children: [],
  };
  const group = { ...node, path: 'host/expected', name: 'Expected, not connected', kind: 'group', icon: 'group', parent: 'host', depth: 1, own: { level: null, message: '' },
    hardware_id: null, owner: 'io_diagnostics', message: '', rollup: { ...node.rollup }, children: [node] };
  state.tree.children.push(group);
  state.summary.attention.unshift({ path: ABSENT, name: node.name, kind: 'device', icon: 'usb', level: 'warn', message: node.message, device: { path: ABSENT, name: node.name } });
  return state;
}
const ABSENT_DETAIL = {
  node: { path: ABSENT, name: 'fdcanusb 28B3A25D', kind: 'device', icon: 'usb', edge: 'via', level: 'warn', message: 'expected · not on USB', own: { level: 'warn', message: '' },
    rollup: { level: 'warn', origin: ABSENT, origin_name: '', counts: { warn: 1 }, knock_on: [] }, hint: null, knock_on_of: null, stale: false, connected: true,
    hardware_id: 'usb:0483:5740:28B3A25D', identity: {}, owner: 'moteus_servo', id_text: '', age_s: 0, children: [] },
  ancestors: [{ path: 'host', name: 'bam-GPU', edge: null }, { path: 'host/expected', name: 'Expected, not connected', edge: 'via' }],
  checks: [{ name: 'fdcanusb 28B3A25D: presence', level: 'warn', effective_level: 'warn', stale: false, message: 'expected · not on USB · the kernel saw no plug event since boot',
    code: 'ExpectedAbsent', source: 'io_diagnostics.expected', received_iso: '', age_s: 0, expected_period_s: null, stale_after_s: null,
    values: [{ key: 'what_to_check', value: '1. A data cable, not a charge-only one\n2. Another USB port\n3. The device is powered' }, { key: 'kernel_evidence', value: 'never' }],
    measurements: [] }],
  actions: [], faults: [], events: [],
};

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const server = await startServer('F1');
  const base = server.base;
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    const names = plugins.plugins.map((plugin) => plugin.name);
    check('only the fixture plugin is loaded', names.join() === 'v6_scenario', names);
    if (names.join() !== 'v6_scenario') throw new Error('refusing to press anything: a non-fixture plugin is loaded');

    // ---- defaults, and a remembered choice wins
    for (const scheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: scheme });
      const page = await context.newPage();
      await open(page, base);
      const fresh = await page.evaluate(() => ({ structure: S.structure, variant: S.variant, skin: document.documentElement.dataset.skin, mode: document.documentElement.dataset.mode, pref: S.mode }));
      check(`defaults (OS ${scheme})`, fresh.structure === 'p1' && fresh.variant === 'v6' && fresh.skin === 'claude' && fresh.mode === scheme && fresh.pref === 'auto', fresh);
      if (scheme === 'light') {
        await page.evaluate(() => { localStorage.setItem('io-diagnostics.structure', 'p3'); localStorage.setItem('io-diagnostics.skin', 'notion'); });
        await page.reload();
        await ready(page);
        const kept = await page.evaluate(() => ({ structure: S.structure, skin: document.documentElement.dataset.skin }));
        check('a remembered choice wins', kept.structure === 'p3' && kept.skin === 'notion', kept);
      }
      await context.close();
    }

    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    const posts = [];
    page.on('request', (request) => {
      if (request.url().endsWith('/api/v1/act') && request.method() === 'POST') posts.push({ body: JSON.parse(request.postData() || '{}'), headers: request.headers() });
    });

    // ---- the attention line
    await open(page, base);
    await page.waitForFunction(() => document.querySelector('#attention .why-lines'), null, { timeout: 15000 });
    const attention = await page.evaluate(() => {
      const first = document.querySelector('#attention .att-item');
      return { hidden: document.getElementById('attention').hidden, where: first.querySelector('.att-where').textContent, message: first.querySelector('.att-msg').textContent,
        lines: [...first.querySelectorAll('.why-lines > div')].map((line) => line.textContent), items: document.querySelectorAll('#attention .att-item').length,
        shape: Boolean(first.querySelector('.att-st .shape')), top: Math.round(document.getElementById('attention').getBoundingClientRect().top),
        mainTop: Math.round(document.getElementById('main').getBoundingClientRect().top) };
    });
    check('attention: plain words + numbered checks', !attention.hidden && /28B3A25D CAN bus/.test(attention.where) && /error-passive/.test(attention.message)
      && attention.lines.length === 4 && /^1\. Motor supply on/.test(attention.lines[0]) && /^4\. CAN ground/.test(attention.lines[3]) && attention.shape
      && attention.top < attention.mainTop, attention);
    const box = await page.locator('#attention').boundingBox();
    await page.screenshot({ path: path.join(SHOTS, 'integration-attention.png'), clip: { x: 0, y: 0, width: VIEWPORT.width, height: Math.min(VIEWPORT.height, box.y + box.height + 230) } });
    await page.click('#attention .att-item:nth-child(2) .att-where');
    await page.waitForTimeout(600);
    check('attention: a cause opens its row', await page.evaluate(() => S.sel && S.sel.endsWith('/can/15/driver')), await page.evaluate(() => S.sel));

    // ---- what to check in the detail panel: the adapter whose CAN bus is folded into it (p1)
    await open(page, base, SECOND);
    await page.waitForFunction(() => document.querySelector('.card.why .why-lines'), null, { timeout: 15000 });
    const why = await page.evaluate(() => ({ head: document.querySelector('.card.why .why-head').textContent, lines: [...document.querySelectorAll('.card.why .why-lines > div')].map((d) => d.textContent),
      connection: [...document.querySelectorAll('#detail .card h3')].map((h) => h.textContent).find((t) => /^Connection \(folded\)/.test(t)) || '' }));
    check('why: a folded part\'s checklist on its owner', /CAN bus/.test(why.head) && why.lines.length === 4 && /^1\. Motor supply/.test(why.lines[0]), why);
    check('seams: folded layers say Connection (folded)', /28B3A25D CAN bus|Port 1-5/.test(await page.evaluate(() => document.getElementById('detail').textContent)) && Boolean(why.connection), why.connection);

    // ---- utilities on CAN 15: grouping, icons, the dry run of a moves action, its modal
    await open(page, base, CAN15);
    await page.waitForSelector('.card.utilities .urow');
    const card = await page.evaluate(() => {
      const groups = [...document.querySelectorAll('.card.utilities .ugroup')].map((group) => ({ id: group.className.replace('ugroup ugroup-', ''),
        names: [...group.querySelectorAll('[data-util]')].map((button) => button.dataset.util) }));
      const rows = [...document.querySelectorAll('.card.utilities .u-btn')].map((button) => ({ name: button.dataset.util, icon: button.querySelector('svg.u-ic').innerHTML,
        title: button.querySelector('.u-title').textContent, desc: button.querySelector('.u-desc').textContent }));
      const motion = document.querySelector('.ugroup-motion');
      const style = motion && getComputedStyle(motion);
      return { groups, rows, motionBorder: style ? parseFloat(style.borderTopWidth) : 0, motionGap: motion ? motion.getBoundingClientRect().top - motion.previousElementSibling.getBoundingClientRect().bottom : 0 };
    });
    const ids = card.groups.map((group) => group.id);
    const standardIcons = new Set(card.rows.filter((row) => row.name !== 'reset').map((row) => row.icon));
    check('utilities: groups and order', ids.join() === 'maintenance,motion,more' && card.groups[1].names.join() === 'calibrate' && card.groups[2].names.join() === 'reset'
      && card.groups[0].names.join() === 'update_firmware,update_config,clear_faults,set_id', card.groups);
    check('utilities: icon + title + description', card.rows.every((row) => row.icon && row.title && row.desc) && standardIcons.size === card.rows.length - 1, card.rows.map((row) => row.title));
    check('utilities: motion set apart', card.motionBorder >= 1 && card.motionGap >= 6, { border: card.motionBorder, gap: card.motionGap });
    await page.click('[data-util="calibrate"]');
    await page.waitForSelector('.u-panel .u-cmd', { timeout: 15000 });
    await page.waitForTimeout(300);
    const dry = await page.evaluate(() => ({ cmd: document.querySelector('.u-panel .u-cmd').textContent, steps: document.querySelectorAll('.u-panel .u-steps li').length,
      pre: [...document.querySelectorAll('.u-panel .pre li')].map((li) => li.textContent), confirm: (document.querySelector('.u-panel .confirm') || {}).textContent,
      argv: document.querySelector('.u-panel .u-argv').textContent, button: (document.querySelector('[data-util-run]') || {}).textContent }));
    check('dry run: what will run', dry.cmd === 'moteus_tool --target 15 --calibrate --fdcanusb /dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00'
      && dry.steps === 1 && dry.pre.some((text) => /joint is free to spin/.test(text) && /person confirms/.test(text)) && /rotor spins/.test(dry.confirm)
      && /--action calibrate --node 15 --dry-run$/.test(dry.argv) && dry.button === 'Continue…', dry);
    check('dry run: one POST, dry_run, from the page', posts.length === 1 && posts[0].body.dry_run === true && posts[0].headers['x-io-diagnostics'] === 'ui', posts.map((p) => p.body));
    const detail = await page.locator('#detail').boundingBox();
    const showActive = async () => { await page.evaluate(() => document.querySelector('.urow.active').scrollIntoView({ block: 'end' })); await page.mouse.move(5, 995); await page.waitForTimeout(300); };
    await showActive();
    await page.screenshot({ path: path.join(SHOTS, 'integration-utilities-dry-run.png'), clip: detail });
    await page.click('[data-util-run]');
    await page.waitForSelector('#modal:not([hidden]) .modal', { timeout: 5000 });
    const modal = await page.evaluate(() => ({ danger: document.querySelector('#modal .danger').textContent, words: document.querySelector('#modal .m-danger').textContent,
      tick: document.querySelector('#modal .m-tick').textContent, checked: document.querySelector('#modal [data-util-tick]').checked,
      disabled: document.querySelector('#modal [data-util-confirm]').disabled, label: document.querySelector('#modal [data-util-confirm]').textContent }));
    check('moves modal: the danger, a box, a labelled button', modal.danger === 'moves' && /Makes hardware move/.test(modal.words) && /I checked: CAN 15's joint is free to spin/.test(modal.tick)
      && !modal.checked && modal.disabled && modal.label === 'Run calibration', modal);
    await page.screenshot({ path: path.join(SHOTS, 'integration-utilities-moves-modal.png') });
    await page.click('#modal [data-util-confirm]', { force: true }); // disabled: nothing may happen
    await page.waitForTimeout(500);
    check('moves: no run without the box', posts.length === 1, posts.length);
    await page.check('#modal [data-util-tick]');
    await page.waitForTimeout(200);
    const enabled = await page.evaluate(() => !document.querySelector('#modal [data-util-confirm]').disabled);
    await page.click('#modal [data-util-confirm]');
    await page.waitForSelector('.u-panel .u-result', { timeout: 20000 });
    const moved = await page.evaluate(() => ({ head: document.querySelector('.u-panel .u-result-h').textContent, records: document.querySelector('.u-panel .u-records').textContent,
      modal: document.getElementById('modal').hidden }));
    check('moves: ticked, pressed, the fixture answers', enabled && posts.length === 2 && posts[1].body.token && !('dry_run' in posts[1].body) && moved.modal
      && /ActionCompleted/.test(moved.records) && /nothing ran/.test(moved.records), { enabled, run: posts[1] && posts[1].body, moved });

    // ---- a query: one more click, the result inline
    await open(page, base, ADAPTER);
    await page.waitForSelector('[data-util="check_comms"]');
    const adapterGroups = await page.evaluate(() => [...document.querySelectorAll('.card.utilities .ugroup')].map((group) => group.className.replace('ugroup ugroup-', '')));
    check('utilities: checks come first', adapterGroups[0] === 'checks' && adapterGroups.join() === 'checks,maintenance', adapterGroups);
    await page.click('[data-util="check_comms"]');
    await page.waitForSelector('[data-util-run]');
    const runLabel = await page.textContent('[data-util-run]');
    await page.click('[data-util-run]');
    await page.waitForSelector('.u-panel .u-result.ok', { timeout: 20000 });
    await page.waitForTimeout(300);
    const query = await page.evaluate(() => ({ head: document.querySelector('.u-panel .u-result-h').textContent, records: document.querySelector('.u-panel .u-records').textContent,
      modalShown: !document.getElementById('modal').hidden }));
    check('query: runs with one more click', runLabel === 'Run Check communication' && !query.modalShown && /ActionCompleted/.test(query.records) && posts.length === 4, { runLabel, query, posts: posts.length });
    await showActive();
    await page.screenshot({ path: path.join(SHOTS, 'integration-utilities-query-result.png'), clip: detail });

    // ---- set_id: the new id in its row gates the dry run; the dry run, the modal and the run all carry it
    await open(page, base, CAN15);
    await page.waitForSelector('[data-util-row="set_id"] [data-util-newid]');
    const idRow = async () => page.evaluate(() => {
      const row = document.querySelector('[data-util-row="set_id"]');
      const box = row.querySelector('[data-util-newid]');
      return { label: row.querySelector('.u-newid').textContent, value: box.value, type: box.type, min: box.min, max: box.max,
        disabled: row.querySelector('[data-util="set_id"]').disabled, focused: document.activeElement === box };
    });
    const empty = await idRow();
    const sent = posts.length;
    await page.click('[data-util="set_id"]', { force: true }); // disabled: nothing may happen
    await page.waitForTimeout(400);
    check('set_id: an input, the dry run disabled', empty.label === 'New id (1-126)' && empty.type === 'number' && empty.min === '1' && empty.max === '126'
      && empty.value === '' && empty.disabled && posts.length === sent, { empty, posts: posts.length - sent });
    const gate = {};
    for (const typed of ['0', '127', '15', '1.5', '12']) {
      await page.fill('[data-util-row="set_id"] [data-util-newid]', '');
      await page.type('[data-util-row="set_id"] [data-util-newid]', typed, { delay: 60 });
      await page.waitForTimeout(1300); // past a poll: the detail is rewritten, the caret stays
      gate[typed] = await idRow();
    }
    check('set_id: only a valid new id enables it', ['0', '127', '15', '1.5'].every((typed) => gate[typed].disabled) && !gate['12'].disabled
      && gate['12'].value === '12' && gate['12'].focused, gate);
    await page.fill('[data-util-row="set_id"] [data-util-newid]', '7');
    await page.waitForTimeout(300);
    const rowBox = await page.locator('[data-util-row="set_id"]').boundingBox();
    await page.evaluate(() => document.activeElement.blur()); // the resting row, not its focus ring
    await page.mouse.move(5, 995);
    await page.screenshot({ path: path.join(SHOTS, 'fix-set-id.png'), clip: { x: rowBox.x - 12, y: rowBox.y - 30, width: rowBox.width + 24, height: rowBox.height + 42 } });
    await page.click('[data-util="set_id"]');
    await page.waitForSelector('[data-util-row="set_id"] .u-panel .u-cmd', { timeout: 15000 });
    await page.waitForTimeout(300);
    const idDry = await page.evaluate(() => ({ cmd: document.querySelector('.u-panel .u-cmd').textContent, confirm: (document.querySelector('.u-panel .confirm') || {}).textContent,
      argv: document.querySelector('.u-panel .u-argv').textContent }));
    check('set_id: the dry run carries the new id', posts.length === sent + 1 && posts[sent].body.new_id === 7 && posts[sent].body.dry_run === true
      && /--new 7$/.test(idDry.cmd) && /New id: CAN 7\./.test(idDry.confirm) && /--node 15 --new-id 7 --dry-run$/.test(idDry.argv), { body: posts[sent] && posts[sent].body, idDry });
    await page.click('[data-util-run]');
    await page.waitForSelector('#modal:not([hidden]) .modal', { timeout: 5000 });
    const idModal = await page.evaluate(() => (document.querySelector('#modal .m-newid') || {}).textContent);
    check('set_id: the modal repeats CAN A → CAN B', idModal === 'CAN 15 → CAN 7', idModal);
    await page.screenshot({ path: path.join(SHOTS, 'fix-set-id-modal.png') });
    await page.click('#modal [data-util-confirm]');
    await page.waitForSelector('[data-util-row="set_id"] .u-panel .u-result', { timeout: 20000 });
    const idRun = await page.evaluate(() => (document.querySelector('[data-util-row="set_id"] .u-panel .u-records')
      || document.querySelector('[data-util-row="set_id"] .u-panel .u-result') || {}).textContent);
    check('set_id: the run carries the dry run\'s new id', posts.length === sent + 2 && posts[sent + 1].body.new_id === 7 && posts[sent + 1].body.token
      && !('dry_run' in posts[sent + 1].body) && /ActionCompleted/.test(idRun), { body: posts[sent + 1] && posts[sent + 1].body, idRun });
    await page.click('[data-util-again]');
    await page.waitForSelector('[data-util-row="set_id"] .u-panel .u-cmd', { timeout: 15000 });
    await page.fill('[data-util-row="set_id"] [data-util-newid]', '9');
    await page.waitForTimeout(300);
    check('set_id: another new id closes the dry run', await page.evaluate(() => !document.querySelector('[data-util-row="set_id"] .u-panel') && S.util === null), '');

    // ---- the server refuses anything but the page's dry-run-then-click (the browser logs each 403: expected here)
    const before = errors.length;
    const refusals = await page.evaluate(async ({ path: entity }) => {
      const send = async (headers, body) => { const response = await fetch('/api/v1/act', { method: 'POST', headers, body: JSON.stringify(body) }); return { status: response.status, ...(await response.json()) }; };
      return {
        noHeader: await send({ 'Content-Type': 'application/json' }, { path: entity, action: 'check_comms', node: null, token: 'x' }),
        noToken: await send({ 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, { path: entity, action: 'check_comms', node: null }),
      };
    }, { path: ADAPTER });
    errors.splice(before, errors.length - before, ...errors.slice(before).filter((text) => !/status of 403/.test(text)));
    check('server: refusals with a plain reason', refusals.noHeader.status === 403 && refusals.noHeader.refused === 'NotFromPage' && refusals.noToken.refused === 'NoDryRun'
      && /dry-run/.test(refusals.noToken.error), refusals);

    // ---- ExpectedAbsent: injected into /state and /node, in every structure
    await page.route('**/api/v1/state', async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: injectAbsent(await response.json()) });
    });
    await page.route(`**/api/v1/node?path=${encodeURIComponent(ABSENT)}`, (route) => route.fulfill({ json: ABSENT_DETAIL }));
    await open(page, base);
    await page.waitForFunction(() => /data cable/.test(document.getElementById('attention').textContent), null, { timeout: 15000 });
    const absentLine = await page.evaluate(() => document.querySelector('#attention .att-item').textContent);
    check('absent: on the attention line with its checks', /fdcanusb 28B3A25D/.test(absentLine) && /1\. A data cable/.test(absentLine) && /kernel evidence: never/.test(absentLine), absentLine.slice(0, 200));
    const inEvery = [];
    for (const structure of ['p0', 'p1', 'p2', 'p3', 'p4', 'p5']) {
      await page.selectOption('#structure', structure);
      await page.waitForTimeout(400);
      inEvery.push(await page.evaluate((entity) => {
        const key = S.built.byEntity.get(entity);
        const row = key && S.built.byKey.get(key);
        return { structure: S.structure, row: Boolean(row && row.entity === entity && row.status.own === 'warn') };
      }, ABSENT));
    }
    check('absent: a warn row in every structure', inEvery.every((item) => item.row), inEvery);
    await page.unroute('**/api/v1/state');
    await page.selectOption('#structure', 'p1');

    // ---- the seams, drawn
    const seam = async (structure, toggles, selected) => {
      await page.evaluate(({ id, on }) => { localStorage.setItem('io-diagnostics.structure', id); localStorage.setItem('io-diagnostics.structure-toggles', JSON.stringify({ [id]: on })); }, { id: structure, on: toggles });
      await open(page, base, selected);
    };
    await seam('p3', { showPaths: true }, CAN15);
    const p3 = await page.evaluate((key) => { const row = document.querySelector(`.t6-row[data-t6-path="${key}"]`); return { suffix: (row.querySelector('.t6-suffix') || {}).textContent, sub: (row.nextElementSibling || {}).className, subText: (row.nextElementSibling || {}).textContent, id: (row.querySelector('.t6-id') || {}).textContent }; }, CAN15);
    check('seams: p3 subtitle + suffix, id kept', /CAN bus/.test(p3.suffix || '') && p3.sub === 't6-subline' && /Port 1-3/.test(p3.subText) && p3.id === 'ABoAQVQwUBUgOTNO', p3);
    const others = {};
    for (const [variant, prefix] of [['v1', 't1'], ['v2', 't2']]) {
      await page.selectOption('#tree-variant', variant);
      await page.waitForTimeout(700);
      others[variant] = await page.evaluate(({ key, pre }) => { const row = document.querySelector(`.${pre}-row[data-${pre}-path="${key}"]`); return row ? { suffix: (row.querySelector(`.${pre}-suffix`) || {}).textContent, sub: (row.nextElementSibling || {}).className } : null; }, { key: CAN15, pre: prefix });
    }
    await page.selectOption('#tree-variant', 'v6');
    check('seams: v1 / v2 draw suffix + subtitle', ['v1', 'v2'].every((v) => others[v] && /CAN bus/.test(others[v].suffix || '') && others[v].sub === `${v === 'v1' ? 't1' : 't2'}-subline`), others);
    await page.click('[data-view="table"]');
    await page.waitForSelector('table.grid tbody tr');
    const tableSub = await page.evaluate((key) => { const tr = document.querySelector(`table.grid tr[data-key="${key}"]`); const next = tr && tr.nextElementSibling; return { sub: next && next.className, text: next && next.textContent, suffix: tr && (tr.querySelector('.suffix') || {}).textContent }; }, CAN15);
    check('seams: Table subtitle row + suffix', tableSub.sub === 'subrow' && /Port 1-3/.test(tableSub.text || '') && /CAN bus/.test(tableSub.suffix || ''), tableSub);
    await page.click('[data-view="tree"]');
    await seam('p4', {}, ADAPTER);
    const p4 = await page.evaluate((key) => { const row = document.querySelector(`.t6-row[data-t6-path="${key}"]`); return { attrs: (row.querySelector('.t6-attrs') || {}).textContent, suffix: (row.querySelector('.t6-suffix') || {}).textContent, id: (row.querySelector('.t6-id') || {}).textContent || '' }; }, ADAPTER);
    check('seams: p4 attrs + suffix, not the id', p4.attrs === '/dev/ttyACM0' && /Port 1-3/.test(p4.suffix || '') && !/ttyACM0/.test(p4.id), p4);
    await seam('p5', {}, `${SECOND}/can`);
    const p5 = await page.evaluate((key) => { const row = document.querySelector(`.t6-row[data-t6-path="${key}"]`); return { lead: (row.querySelector('.t6-lead') || {}).textContent, id: (row.querySelector('.t6-id') || {}).textContent }; }, `${SECOND}/can`);
    check('seams: p5 bus words as the lead', /error-passive/.test(p5.lead || '') && /node/.test(p5.id || ''), p5);
    await seam('p2', {}, CAN15);
    const p2off = await page.evaluate(() => [...document.querySelectorAll('#structure-toggles [data-opt]')].map((box) => box.dataset.opt));
    await page.check('#structure-toggles [data-opt="connections"]');
    await page.waitForTimeout(300);
    const p2on = await page.evaluate(() => [...document.querySelectorAll('#structure-toggles [data-opt]')].map((box) => box.dataset.opt));
    check('seams: toggles[].when (p2 Empty ports)', p2off.join() === 'connections' && p2on.join() === 'connections,showEmpty', { p2off, p2on });
    await seam('p1', {}, CAN15);
    await page.selectOption('#skin', 'terminal');
    await page.waitForTimeout(1300);
    const guides = await page.evaluate((key) => (document.querySelector(`.t6-row[data-t6-path="${key}"] .t6-guide`) || {}).textContent, CAN15);
    check('seams: Terminal draws box guides', /^[├└]─ $/.test(guides || ''), guides);
    await page.screenshot({ path: path.join(SHOTS, 'integration-terminal-guides.png'), clip: { x: 0, y: 300, width: 470, height: 420 } });
    await page.selectOption('#skin', 'contrast');
    const focus = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--io-focus-width').trim());
    check('seams: --io-focus-width per skin', focus === '3px', focus);
    await page.selectOption('#skin', 'claude');
    await page.click('[data-view="table"]');
    await page.waitForSelector('table.grid tbody tr');
    const mono = await page.evaluate(() => { const td = document.querySelector('table.grid tbody tr[data-key] td.xcol'); return td ? getComputedStyle(td).fontFamily : ''; });
    const monoRef = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--io-font-mono').trim());
    check('seams: mono extra column', Boolean(mono) && mono.replace(/\s/g, '') === monoRef.replace(/\s/g, ''), { mono, monoRef });
    check('no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
    await context.close();
  } finally {
    stopServer(server.child);
  }
  try {
    await expectedSetup(browser);
  } finally {
    await browser.close();
  }
  console.log(`\n${passed}/${passed + failed} checks passed · screenshots in ${SHOTS}`);
  process.exitCode = failed ? 1 : 0;
}

// ---- the expected setup (docs/expected-setup.md), on F1 with a rig file of its own (a temporary copy) ----
// F1: 272813EF answers CAN 1, 14, 15 (the file also expects 16), 28B3A25D is plugged in on 1-5 (the file retires it),
// fdcanusb 99990000 is expected and never there.
const RIG = 'host/rig-1-dof-test-rack';
const LOST = 'host/expected/usb-0483-5740-99990000';
const RIG_FILE = `version = 1

[[rig]]
name = "1-DOF test rack"

[[rig.device]]
name = "fdcanusb 272813EF"
kind = "adapter"
hardware_id = "usb:0483:5740:272813EF"
nodes = ["can/1", "can/14", "can/15", "can/16"]

[[rig.device]]
name = "fdcanusb 99990000"
kind = "adapter"
hardware_id = "usb:0483:5740:99990000"
nodes = []

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

[[retired]]
name = "fdcanusb 28B3A25D"
hardware_id = "usb:0483:5740:28B3A25D"
date = "2026-10-23"
reason = "frames never reached the bus (2026-10-22); replaced by 0607623A"
`;

async function expectedSetup(browser) {
  const os = require('os');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'io-diagnostics-expected-'));
  const file = path.join(dir, 'expected.toml');
  fs.writeFileSync(file, RIG_FILE);
  const server = await startServer('F1', ['--expected', file]);
  const base = server.base;
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  const rigRow = () => page.evaluate((key) => {
    const row = document.querySelector(`.t6-row[data-t6-path="${key}"]`);
    return row ? { name: row.querySelector('.t6-name').textContent, shape: Boolean(row.querySelector('.t6-st .t6-shape')), first: row === document.querySelector('.t6-row') } : null;
  }, RIG);
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    check('expected: only the fixture plugin is loaded', plugins.plugins.map((plugin) => plugin.name).join() === 'v6_scenario');

    // the rig row: first, the counts in its name, a warn shape; its card offers Expect what's here
    await open(page, base, RIG);
    await page.waitForSelector('.card.setup [data-setup="expect"]', { timeout: 15000 });
    const rig = await rigRow();
    const rigCard = await page.evaluate(() => ({ buttons: [...document.querySelectorAll('.card.setup [data-setup]')].map((b) => b.dataset.setup), why: (document.querySelector('.card.why') || {}).textContent || '' }));
    check('expected: the rig row counts at a glance', rig && rig.name === '1-DOF test rack · 1/2 adapters · 3/4 boards · 2/2 cameras' && rig.shape && rig.first, rig);
    check('expected: the rig row names what is missing', /can\/16 on fdcanusb 272813EF/.test(rigCard.why) && /fdcanusb 99990000: not on this PC/.test(rigCard.why)
      && rigCard.buttons.join() === 'expect', rigCard);
    await page.screenshot({ path: path.join(SHOTS, 'expected-rig-row.png'), clip: { x: 0, y: 0, width: VIEWPORT.width, height: 560 } });

    // the retired adapter, plugged in again (F1's 28B3A25D on 1-5): a warning on its own row, the reason in its card
    await open(page, base, SECOND);
    await page.waitForSelector('.card.setup .s-retired', { timeout: 15000 });
    const retired = await page.evaluate((entity) => {
      const row = S.built.byKey.get(S.built.byEntity.get(entity));
      return { own: row.status.own, card: document.querySelector('.card.setup .s-retired').textContent,
        check: [...document.querySelectorAll('.card.check')].map((card) => card.textContent).find((text) => /: retired/.test(text)) || '' };
    }, SECOND);
    check('expected: a retired adapter plugged in again is a warn row with its reason', retired.own === 'warn'
      && retired.card === 'Retired 2026-10-23: frames never reached the bus (2026-10-22); replaced by 0607623A' && /RetiredDevice/.test(retired.check), retired);
    await page.screenshot({ path: path.join(SHOTS, 'expected-retired-row.png'), clip: { x: 0, y: 0, width: VIEWPORT.width, height: 760 } });

    // Retire…: a reason first (typed key by key: the box keeps its focus while the card redraws), Cancel writes nothing
    await open(page, base, ADAPTER);
    await page.click('.card.setup [data-setup="retire"]');
    await page.waitForSelector('.card.setup [data-setup-reason]');
    const disabledFirst = await page.$eval('[data-setup-confirm]', (button) => button.disabled);
    await page.click('[data-setup-reason]');
    await page.keyboard.type('loose USB socket', { delay: 30 });
    const typed = await page.evaluate(() => ({ value: document.querySelector('[data-setup-reason]').value, focus: document.activeElement.matches('[data-setup-reason]'),
      enabled: !document.querySelector('[data-setup-confirm]').disabled, confirm: document.querySelector('.s-panel .confirm').textContent }));
    check('expected: Retire… needs a reason and says it in the confirm', disabledFirst && typed.value === 'loose USB socket' && typed.focus && typed.enabled
      && typed.confirm === 'Retire fdcanusb 272813EF? It leaves 1-DOF test rack. Plugged in again, its row says “retired 2026-10-21: loose USB socket” (a warning).', { disabledFirst, ...typed });
    await page.locator('.card.setup').scrollIntoViewIfNeeded();
    await page.locator('.card.setup').screenshot({ path: path.join(SHOTS, 'expected-controls-retire.png') });
    await page.click('[data-setup-cancel]');
    check('expected: Cancel writes nothing', fs.readFileSync(file, 'utf8') === RIG_FILE);

    // Expect what's here on the adapter: the confirm names the change; the file and the rig row follow
    await page.click('.card.setup [data-setup="expect"]');
    await page.waitForSelector('.s-panel .confirm');
    const expectText = await page.textContent('.s-panel .confirm');
    check('expected: Expect what\'s here says exactly what changes', expectText === 'Expect fdcanusb 272813EF in 1-DOF test rack with can/1, can/14, can/15 (can/16 no longer expected)?', expectText);
    await page.locator('.card.setup').screenshot({ path: path.join(SHOTS, 'expected-controls-expect.png') });
    await page.click('[data-setup-confirm]');
    await page.waitForSelector('.s-panel .u-result.ok', { timeout: 15000 });
    await page.waitForFunction((key) => /3\/3 boards/.test((document.querySelector(`.t6-row[data-t6-path="${key}"] .t6-name`) || {}).textContent || ''), RIG, { timeout: 15000 });
    const written = fs.readFileSync(file, 'utf8');
    check('expected: Expect what\'s here rewrote the file and the rig row', /"can\/15"/.test(written) && !/can\/16/.test(written)
      && (await rigRow()).name === '1-DOF test rack · 1/2 adapters · 3/3 boards · 2/2 cameras', (await rigRow()).name);

    // Forget on the adapter that is never there
    await open(page, base, LOST);
    await page.waitForSelector('.card.setup [data-setup="forget"]', { timeout: 15000 });
    const lostButtons = await page.evaluate(() => [...document.querySelectorAll('.card.setup [data-setup]')].map((b) => b.dataset.setup));
    await page.click('.card.setup [data-setup="forget"]');
    const forgetText = await page.textContent('.s-panel .confirm');
    await page.locator('.card.setup').screenshot({ path: path.join(SHOTS, 'expected-controls-forget.png') });
    await page.click('[data-setup-confirm]');
    await page.waitForSelector('.s-panel .u-result.ok', { timeout: 15000 });
    await page.waitForFunction((key) => /1\/1 adapter /.test((document.querySelector(`.t6-row[data-t6-path="${key}"] .t6-name`) || {}).textContent || ''), RIG, { timeout: 15000 });
    const lostRowGone = await page.evaluate(({ entity, rig }) => !S.built.byEntity.has(entity) && !S.data.summary.attention.some((item) => item.path === entity)
      && S.sel === rig && /Saved/.test(document.querySelector('.card.setup .u-result.ok').textContent), { entity: LOST, rig: RIG });
    await page.locator('.card.setup').screenshot({ path: path.join(SHOTS, 'expected-controls-forget-done.png') });
    check('expected: Forget on an absent row drops it from the file and the page', lostButtons.join() === 'forget,retire' && lostRowGone
      && forgetText.startsWith('Forget fdcanusb 99990000? It leaves the expected setup (1-DOF test rack)') && !/99990000/.test(fs.readFileSync(file, 'utf8')), { lostButtons, forgetText });
    await open(page, base, RIG);
    await page.screenshot({ path: path.join(SHOTS, 'expected-rig-row-after.png'), clip: { x: 0, y: 0, width: VIEWPORT.width, height: 560 } });
    check('expected: no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
  } finally {
    await context.close();
    stopServer(server.child);
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
