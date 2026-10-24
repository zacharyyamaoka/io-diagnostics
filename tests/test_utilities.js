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
//   attention    no list over the panes (Zach, 2026-10-23): the toolbar meets the panes, and each cause the API lists
//                is a marked tree row whose click shows its message in the detail
//   why          the detail panel shows what_to_check for the selection and for a folded part (the CAN bus)
//   absent       an expected-but-absent device (ExpectedAbsent, injected into /state and /node) is a row in every
//                structure, whose detail shows its checklist and kernel evidence
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
//   round 3      (docs/round3-brief-2026-10-23.md) defaults skin contrast ("Bare Metal"; shadcn since round 3.5), layout d0, menu u0, sort
//                status; Group by's list and a remembered p3 falling back; u0's description tooltip (hover + keyboard
//                focus), danger as a plain word, the greyed busy row with its quiet line; the panes: each sash dragged,
//                reloaded and measured, minimum widths, double-click reset, the Table's inspector folding / unfolding /
//                pinning (`]`, the toolbar toggle). Selects live in the View options popover: pick() opens it.
//   round 3.5    (Zach, 2026-10-23) the default skin is stock shadcn/ui (statically linked, light / dark from the OS) and a
//                skin switcher is back in the bottom-right bar, listing every skin; the utilities live ONLY in the entity
//                header's Utilities ▾ (the same place on every layout and entity), a dry run reads in a sheet under it,
//                a run is one quiet line under the header that becomes its result (Details ▸), lands in Activity and
//                re-reads the entity; a failure is the one coloured (diagnostic-level) line; every drill-down is a
//                browser-history entry (page.goBack / goForward and the mouse's back / forward buttons, as CDP mouse
//                events); every entity opens in d6 (the entity page) unless the Prototype picker says otherwise; the history window in
//                View options (POST /api/v1/settings, page-only, on a server with its own config dir); the depth colour map beside it
//                (select, equalization switch, range): changes persist, survive a reload, and are shot light and dark.
// Screenshots: reports/media/round2/integration-*.png, expected-*.png, reports/media/round3/a-*-<light|dark>.png.
// Exits 1 on any failed check. Nothing opens a CAN adapter.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'round2');
const ROUND3 = path.join(ROOT, 'reports', 'media', 'round3');
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

function startServer(fixture, extra = [], extraEnv = {}) {
  const env = { ...process.env, ...extraEnv };
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

// Round 3: Group by (#structure), the structure's toggles, tree style, skin and mode live in the View options popover.
async function viewOptions(page) {
  if (await page.evaluate(() => document.getElementById('view-options').hidden)) await page.click('#vo-btn');
  await page.waitForSelector('#view-options:not([hidden])');
}
async function pick(page, selector, value) {
  await viewOptions(page);
  await page.selectOption(selector, value);
}

async function open(page, base, sel) {
  await page.goto(`${base}/${sel ? `?sel=${encodeURIComponent(sel)}` : ''}`);
  await ready(page);
  if (sel) await page.waitForFunction((p) => S.node && S.node.node.path === p, sel, { timeout: 15000 });
  await page.waitForTimeout(300);
}

// Round 3.5: the utilities live in the entity header's Utilities ▾ menu (drawn by the page for every layout).
async function utilities(page) {
  await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 15000 });
  if (!(await page.$('.ent-menu'))) await page.click('#ent-util-btn');
  await page.waitForSelector('.ent-menu [data-util]', { timeout: 5000 });
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
  fs.mkdirSync(ROUND3, { recursive: true });
  const { chromium } = require(PLAYWRIGHT);
  const browser = require('./pin_layout').pinDetailLayout(await chromium.launch({ executablePath: CHROME, headless: true }));
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
      const fresh = await page.evaluate(() => ({ structure: S.structure, variant: S.variant, skin: document.documentElement.dataset.skin, mode: document.documentElement.dataset.mode, pref: S.mode,
        layout: S.layout, menu: S.menu, sort: S.sort }));
      // round 3.5: the default skin is stock shadcn/ui (round 3 had Bare Metal = contrast); the layout d0 (the first
      // selection is the worst cause's CAN bus, not the Computer) and the menu u0; sort by status
      check(`defaults (OS ${scheme})`, fresh.structure === 'p1' && fresh.variant === 'v6' && fresh.skin === 'shadcn' && fresh.mode === scheme && fresh.pref === 'auto'
        && fresh.layout === 'd0' && fresh.menu === 'u0' && fresh.sort === 'status', fresh);
      // round 3.5: shadcn is the default skin, in light and dark following the OS (above), linked by index.html itself
      {
        const surface = await page.evaluate(() => {
          const probe = document.createElement('i');
          probe.style.color = 'var(--io-surface)';
          document.body.appendChild(probe);
          const rgb = getComputedStyle(probe).color.match(/[\d.]+/g).slice(0, 3).map(Number);
          probe.remove();
          return { rgb, lum: (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255 };
        });
        check(`skin: shadcn ${scheme} follows the OS`, scheme === 'dark' ? surface.lum < 0.25 : surface.lum > 0.85, surface);
        await page.mouse.move(5, 995);
        await page.screenshot({ path: path.join(ROUND3, `r35-shadcn-${scheme}.png`) });
      }
      if (scheme === 'light') {
        const html = await (await fetch(`${base}/`)).text();
        const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'io_diagnostics', 'web', 'skins', 'index.json'), 'utf8'));
        const readySkins = manifest.skins.filter((skin) => skin.ready).map((skin) => skin.id);
        check('skin: shadcn is the default, linked statically', manifest.default === 'shadcn' && /<link rel="stylesheet" href="skins\/shadcn\.css" data-skin-css="shadcn">/.test(html)
          && /html\.dataset\.skin = skin \|\| 'shadcn'/.test(html), { default: manifest.default });
        // the skin switcher is back in the bottom-right bar: every ready skin (shadcn/ui, Pretty, the Bare Metal family, the rest)
        const bar = await page.evaluate(() => {
          const select = document.getElementById('skin-bar');
          const rect = select.getBoundingClientRect();
          return { groups: [...select.children].map((el) => (el.tagName === 'OPTGROUP' ? `${el.label}:${[...el.children].map((o) => o.value).join('+')}` : el.value)),
            ids: [...select.options].map((o) => o.value), value: select.value, inControls: Boolean(select.closest('#controls .ctl')),
            right: Math.round(innerWidth - rect.right), bottom: Math.round(innerHeight - rect.bottom), width: Math.round(rect.width) };
        });
        check('skin: the bottom bar lists every skin', bar.inControls && bar.width > 0 && bar.bottom < 40 && bar.right < 420 && bar.value === 'shadcn'
          && bar.ids.length === readySkins.length && readySkins.every((id) => bar.ids.includes(id)) && bar.groups[0] === 'shadcn' && bar.groups[1] === 'notion'
          && bar.groups[2] === 'Bare Metal:contrast+bare-1+bare-2+bare-3+bare-4+bare-5' && /^More skins:/.test(bar.groups[3] || ''), bar);
        const box = await page.locator('#controls').boundingBox();
        await page.screenshot({ path: path.join(ROUND3, 'r35-skin-switcher.png'), clip: { x: Math.max(0, box.x - 180), y: box.y - 10, width: Math.min(VIEWPORT.width - Math.max(0, box.x - 180), box.width + 190), height: box.height + 20 } });
        await page.selectOption('#skin-bar', 'bare-3');
        await page.waitForTimeout(300);
        const switched = await page.evaluate(() => ({ skin: document.documentElement.dataset.skin, vo: document.getElementById('skin').value, stored: localStorage.getItem('io-diagnostics.skin') }));
        await page.reload();
        await ready(page);
        const keptBar = await page.evaluate(() => ({ skin: document.documentElement.dataset.skin, bar: document.getElementById('skin-bar').value }));
        check('skin: the bottom switcher switches, follows View options, is remembered', switched.skin === 'bare-3' && switched.vo === 'bare-3' && switched.stored === 'bare-3'
          && keptBar.skin === 'bare-3' && keptBar.bar === 'bare-3', { switched, keptBar });
        await page.evaluate(() => localStorage.removeItem('io-diagnostics.skin'));
        // round 3: p3 left the Group by picker, so a remembered p3 falls back to the default; p2 (Type) is remembered
        await page.evaluate(() => { localStorage.setItem('io-diagnostics.structure', 'p2'); localStorage.setItem('io-diagnostics.skin', 'notion'); });
        await page.reload();
        await ready(page);
        const kept = await page.evaluate(() => ({ structure: S.structure, skin: document.documentElement.dataset.skin }));
        check('a remembered choice wins', kept.structure === 'p2' && kept.skin === 'notion', kept);
        await page.evaluate(() => { localStorage.setItem('io-diagnostics.structure', 'p3'); });
        await page.reload();
        await ready(page);
        const dropped = await page.evaluate(() => ({ structure: S.structure, listed: [...document.querySelectorAll('#structure option')].map((o) => `${o.value}:${o.textContent}`) }));
        check('Group by lists Devices · Type · Port · Bus · Physical; a remembered p3 falls back', dropped.structure === 'p1'
          && dropped.listed.join() === 'p1:Devices,p2:Type,p4:Port,p5:Bus,p0:Physical', dropped);
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

    // ---- no list over the panes (Zach, 2026-10-23: "get rid of this activity stream here at the top"): the toolbar
    // meets the panes, and every cause the API lists (summary.attention) is a marked tree row whose detail states it
    await open(page, base);
    const top = await page.evaluate(() => ({ attention: Boolean(document.getElementById('attention')), story: Boolean(document.getElementById('story')),
      sourcesHidden: document.getElementById('sources').hidden, toolbar: Math.round(document.querySelector('.toolbar').getBoundingClientRect().bottom),
      main: Math.round(document.getElementById('main').getBoundingClientRect().top),
      items: S.data.summary.attention.map((item) => ({ path: item.path, message: item.message, key: S.built.byEntity.get(item.path) })) }));
    const reached = [];
    for (const item of top.items) {
      const selector = `.t6-row[data-t6-path="${item.key}"]`;
      const mark = await page.evaluate((sel) => Boolean(document.querySelector(`${sel} .t6-st .t6-mark`)), selector);
      if (mark) {
        await page.click(selector);
        await page.waitForFunction(({ key, message }) => S.sel === key && document.getElementById('detail').textContent.includes(message), item, { timeout: 10000 }).catch(() => {});
      }
      reached.push({ path: item.path, mark, detail: await page.evaluate(({ key, message }) => S.sel === key && document.getElementById('detail').textContent.includes(message), item) });
    }
    check('no attention list; each cause on its row', !top.attention && !top.story && top.sourcesHidden && Math.abs(top.main - top.toolbar) <= 1
      && reached.length >= 4 && reached.every((item) => item.mark && item.detail), { ...top, items: undefined, reached: reached.filter((item) => !item.mark || !item.detail), causes: reached.length });

    // ---- what to check in the detail panel: the adapter whose CAN bus is folded into it (p1)
    await open(page, base, SECOND);
    await page.waitForFunction(() => document.querySelector('.sec.why .why-lines'), null, { timeout: 15000 });
    const why = await page.evaluate(() => ({ head: document.querySelector('.sec.why .why-head').textContent, lines: [...document.querySelectorAll('.sec.why .why-lines > div')].map((d) => d.textContent),
      connection: [...document.querySelectorAll('#detail .sec h3')].map((h) => h.textContent).find((t) => /^Connection \(folded\)/.test(t)) || '' }));
    check('why: a folded part\'s checklist on its owner', /CAN bus/.test(why.head) && why.lines.length === 4 && /^1\. Motor supply/.test(why.lines[0]), why);
    check('seams: folded layers say Connection (folded)', /28B3A25D CAN bus|Port 1-5/.test(await page.evaluate(() => document.getElementById('detail').textContent)) && Boolean(why.connection), why.connection);

    // ---- utilities on CAN 15: grouping, icons, the dry run of a moves action, its modal (round 3.5: in the header's menu)
    await open(page, base, CAN15);
    await utilities(page);
    const card = await page.evaluate(() => {
      const groups = [...document.querySelectorAll('.ent-menu .ugroup')].map((group) => ({ id: group.className.replace('ugroup ugroup-', ''),
        names: [...group.querySelectorAll('[data-util]')].map((button) => button.dataset.util) }));
      const rows = [...document.querySelectorAll('.ent-menu .u-btn')].map((button) => ({ name: button.dataset.util, icon: button.querySelector('svg.u-ic').innerHTML,
        title: button.querySelector('.u-title').textContent, desc: button.querySelector('.u-desc').textContent }));
      const motion = document.querySelector('.ugroup-motion');
      const style = motion && getComputedStyle(motion);
      return { groups, rows, motionBorder: style ? parseFloat(style.borderTopWidth) : 0, motionGap: motion ? motion.getBoundingClientRect().top - motion.previousElementSibling.getBoundingClientRect().bottom : 0 };
    });
    const ids = card.groups.map((group) => group.id);
    const standardIcons = new Set(card.rows.filter((row) => row.name !== 'reset').map((row) => row.icon));
    check('utilities: groups and order', ids.join() === 'maintenance,motion,more' && card.groups[1].names.join() === 'calibrate,identify' && card.groups[2].names.join() === 'reset'
      && card.groups[0].names.join() === 'update_firmware,update_config,clear_faults,set_id', card.groups);
    check('utilities: icon + title + description', card.rows.every((row) => row.icon && row.title && row.desc) && standardIcons.size === card.rows.length - 1, card.rows.map((row) => row.title));
    // round 3 (u0): the description is a tooltip (hidden at rest, shown on hover and on keyboard focus); danger is a word
    const tip = async () => page.evaluate(() => { const desc = document.querySelector('[data-util="update_firmware"] .u-desc'); const style = getComputedStyle(desc); return style.visibility === 'visible' && parseFloat(style.opacity) > 0.9; });
    await page.mouse.move(5, 995);
    await page.waitForTimeout(300);
    const atRest = await tip();
    await page.hover('[data-util="update_firmware"] .u-title');
    await page.waitForTimeout(700);
    const onHover = await tip();
    await page.mouse.move(5, 995);
    await page.waitForTimeout(300);
    await page.focus('[data-util="update_firmware"]');
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    await page.waitForTimeout(400);
    const onFocus = await tip();
    await page.evaluate(() => document.activeElement.blur());
    const words = await page.evaluate(() => [...document.querySelectorAll('.ent-menu .u-btn .danger')].map((el) => `${el.textContent}:${getComputedStyle(el).backgroundColor}`));
    check('utilities: description is a hover / focus tooltip', !atRest && onHover && onFocus, { atRest, onHover, onFocus });
    check('utilities: danger is a plain word', words.length > 0 && words.every((word) => /^(interrupts|changes|moves):rgba\(0, 0, 0, 0\)$/.test(word)), words);
    check('utilities: motion set apart', card.motionBorder >= 1 && card.motionGap >= 6, { border: card.motionBorder, gap: card.motionGap });
    await page.mouse.move(5, 995);
    await page.waitForTimeout(300);
    const detail = await page.locator('#inspector').boundingBox();
    await page.screenshot({ path: path.join(ROUND3, 'r35-header-menu.png'), clip: detail });
    await page.click('[data-util="calibrate"]');
    await page.waitForSelector('.ent-sheet .u-panel .u-cmd', { timeout: 15000 });
    await page.waitForTimeout(300);
    // round 3.5: the dry run reads in a small sheet under the button (the menu closes); its steps and the plugin
    // command line are behind "How it runs ▸"
    const dry = await page.evaluate(() => ({ cmd: document.querySelector('.u-panel .u-cmd').textContent, steps: document.querySelectorAll('.u-panel .u-steps li').length,
      pre: [...document.querySelectorAll('.u-panel .pre li')].map((li) => li.textContent), confirm: (document.querySelector('.u-panel .confirm') || {}).textContent,
      argv: document.querySelector('.u-panel .u-argv').textContent, button: (document.querySelector('[data-util-run]') || {}).textContent,
      cancel: (document.querySelector('.ent-sheet [data-util-close]') || {}).textContent, menu: Boolean(document.querySelector('.ent-menu')),
      under: (() => { const b = document.getElementById('ent-util-btn').getBoundingClientRect(); const s = document.querySelector('.ent-sheet').getBoundingClientRect(); return Math.round(s.top - b.bottom) >= 0 && Math.round(s.top - b.bottom) <= 8 && Math.abs(Math.round(s.right - b.right)) <= 1; })() }));
    check('dry run: what will run', dry.cmd === 'moteus_tool --target 15 --calibrate --fdcanusb /dev/serial/by-id/usb-mjbots_fdcanusb_272813EF-if00'
      && dry.steps === 1 && dry.pre.some((text) => /joint is free to spin/.test(text) && /person confirms/.test(text)) && /rotor spins/.test(dry.confirm)
      && /--action calibrate --node 15 --dry-run$/.test(dry.argv) && dry.button === 'Continue…', dry);
    check('dry run: a sheet under the button, Run / Cancel', dry.under && !dry.menu && dry.cancel === 'Cancel', dry);
    check('dry run: one POST, dry_run, from the page', posts.length === 1 && posts[0].body.dry_run === true && posts[0].headers['x-io-diagnostics'] === 'ui', posts.map((p) => p.body));
    await page.mouse.move(5, 995);
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(SHOTS, 'integration-utilities-dry-run.png'), clip: detail });
    await page.screenshot({ path: path.join(ROUND3, 'r35-dry-run-sheet.png'), clip: detail });
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
    // round 3.5: the result is the line under the header (Details ▸ opens its records), not a panel
    await page.waitForSelector('.ent-run.ok, .ent-run.fail', { timeout: 20000 });
    await page.click('[data-run-details]');
    await page.waitForSelector('.ent-run-details .u-records');
    const moved = await page.evaluate(() => ({ line: document.querySelector('.ent-run').textContent, records: document.querySelector('.ent-run-details .u-records').textContent,
      modal: document.getElementById('modal').hidden, sheet: Boolean(document.querySelector('.ent-sheet')) }));
    check('moves: ticked, pressed, the fixture answers', enabled && posts.length === 2 && posts[1].body.token && !('dry_run' in posts[1].body) && moved.modal && !moved.sheet
      && /ActionCompleted/.test(moved.records) && /nothing ran/.test(moved.records), { enabled, run: posts[1] && posts[1].body, moved });
    await page.click('[data-run-details]');

    // ---- a query: one more click, then the quiet run line under the header and its result (round 3.5)
    await open(page, base, ADAPTER);
    await utilities(page);
    const adapterGroups = await page.evaluate(() => [...document.querySelectorAll('.ent-menu .ugroup')].map((group) => group.className.replace('ugroup ugroup-', '')));
    check('utilities: checks come first', adapterGroups[0] === 'checks' && adapterGroups.join() === 'checks,tools,maintenance,motion', adapterGroups);
    await page.click('[data-util="check_comms"]');
    await page.waitForSelector('[data-util-run]');
    const runLabel = await page.textContent('[data-util-run]');
    // the run is held 2.6 s at the network so the running state can be read: one quiet muted line under the header
    // ("Checking communication on fdcanusb · 272813EF… N s"), the sheet gone, no live output, no other modal; in the
    // menu its row greys out
    await page.route('**/api/v1/act', async (route) => {
      if (!JSON.parse(route.request().postData() || '{}').dry_run) await new Promise((resolve) => setTimeout(resolve, 2600));
      await route.continue();
    });
    const nodeAfter = [];
    page.on('request', (request) => { if (request.url().includes(`/api/v1/node?path=${encodeURIComponent(ADAPTER)}`)) nodeAfter.push(Date.now()); });
    await page.click('[data-util-run]');
    await page.waitForTimeout(1400);
    const busy = await page.evaluate(() => {
      const line = document.querySelector('.ent-run.running');
      const head = document.querySelector('#detail .d-head');
      const color = (token) => { const probe = document.createElement('i'); probe.style.color = `var(${token})`; document.body.appendChild(probe); const c = getComputedStyle(probe).color; probe.remove(); return c; };
      return { line: line ? line.textContent : '', under: Boolean(line && head && head.nextElementSibling === line), diagnostic: Boolean(line && line.hasAttribute('data-diagnostic')),
        color: line ? getComputedStyle(line.querySelector('.ent-run-t')).color : '', muted: color('--io-muted'),
        sheet: Boolean(document.querySelector('.ent-sheet')), modal: !document.getElementById('modal').hidden,
        stream: document.querySelectorAll('#detail .u-tail, #detail .u-records').length };
    });
    await page.screenshot({ path: path.join(ROUND3, 'r35-progress-line.png'), clip: detail });
    await page.click('#ent-util-btn');
    const greyed = await page.evaluate(() => { const button = document.querySelector('.ent-menu [data-util="check_comms"]'); const row = button.closest('.urow');
      return { disabled: button.disabled, busy: row.classList.contains('busy'), line: (row.querySelector('.u-busy') || {}).textContent || '' }; });
    await page.keyboard.press('Escape');
    check('running: one quiet line under the header, no live output', /^Checking communication on fdcanusb · 272813EF… \d+ s$/.test(busy.line) && busy.under && !busy.diagnostic
      && busy.color === busy.muted && !busy.sheet && !busy.modal && busy.stream === 0, busy);
    check('running: its menu row greys out', greyed.disabled && greyed.busy && /^Checking communication… \d+ s$/.test(greyed.line), greyed);
    await page.waitForSelector('.ent-run.ok', { timeout: 20000 });
    const doneAt = Date.now();
    await page.unroute('**/api/v1/act');
    await page.waitForTimeout(300);
    const result = await page.evaluate(() => ({ line: document.querySelector('.ent-run.ok').textContent, diagnostic: document.querySelector('.ent-run.ok').hasAttribute('data-diagnostic'),
      modalShown: !document.getElementById('modal').hidden, details: Boolean(document.querySelector('.ent-run-details')) }));
    await page.screenshot({ path: path.join(ROUND3, 'r35-result-line.png'), clip: detail });
    await page.click('[data-run-details]');
    await page.waitForSelector('.ent-run-details .u-records');
    const records = await page.evaluate(() => document.querySelector('.ent-run-details').textContent);
    await page.screenshot({ path: path.join(ROUND3, 'r35-result-details.png'), clip: detail });
    check('query: runs with one more click, its result is the line', runLabel === 'Run Check communication' && !result.modalShown && /· just now · (Show output · )?Details ▸$/.test(result.line)
      && !result.diagnostic && !result.details && /ActionCompleted/.test(records) && posts.length === 4, { runLabel, result, records: records.slice(0, 160), posts: posts.length });
    check('query: the entity re-reads its diagnostics at once', nodeAfter.some((at) => at >= doneAt - 400 && at <= doneAt + 600), { doneAt, nodeAfter: nodeAfter.map((at) => at - doneAt) });
    await page.click('[data-view="activity"]');
    await page.waitForSelector('.act .ev');
    // activity log v1 (2026-10-24): the host logs the run itself, and RunStarted + RunEnded are ONE `Run` row of the entity it ran on
    // (it was the page's own "utility" line, with the entity in data-go: the page no longer adds lines of its own)
    const logged = await page.evaluate(() => { const first = document.querySelector('.act .ev'); return { kind: first.querySelector('.kind').textContent, text: first.textContent, go: first.querySelector('[data-al-scope]').dataset.alScope }; });
    check('query: the result lands in Activity as the run\'s one row', logged.kind === 'Run' && /Check communication/.test(logged.text) && logged.go === ADAPTER, logged);
    await page.click('[data-view="tree"]');
    // a failed run is the one coloured line: diagnostic-level (data-diagnostic, the error shape, --io-error-text)
    await utilities(page);
    await page.click('[data-util="check_comms"]');
    await page.waitForSelector('.ent-sheet [data-util-run]');
    await page.route('**/api/v1/act', async (route) => {
      if (JSON.parse(route.request().postData() || '{}').dry_run) return route.continue();
      return route.fulfill({ status: 403, json: { refused: 'TokenExpired', error: 'the dry run is 400 s old (a token lasts 300 s): click the utility again' } });
    });
    const beforeFail = errors.length;
    await page.click('[data-util-run]');
    await page.waitForSelector('.ent-run.fail', { timeout: 10000 });
    await page.unroute('**/api/v1/act');
    errors.splice(beforeFail, errors.length - beforeFail, ...errors.slice(beforeFail).filter((text) => !/status of 403/.test(text))); // the refusal we injected
    const fail = await page.evaluate(() => {
      const line = document.querySelector('.ent-run.fail');
      const probe = document.createElement('i'); probe.style.color = 'var(--io-error-text)'; document.body.appendChild(probe); const error = getComputedStyle(probe).color; probe.remove();
      return { text: line.textContent, diagnostic: line.hasAttribute('data-diagnostic'), shape: Boolean(line.querySelector('.shape.lv-error')), color: getComputedStyle(line.querySelector('.ent-run-t')).color, error };
    });
    check('a failed run: a diagnostic-level line, the only colour', fail.diagnostic && fail.shape && fail.color === fail.error && /Check communication failed · not run · TokenExpired/.test(fail.text), fail);
    await page.screenshot({ path: path.join(ROUND3, 'r35-result-failed.png'), clip: detail });

    // ---- set_id: the new id in its row gates the dry run; the dry run, the modal and the run all carry it
    await open(page, base, CAN15);
    await utilities(page);
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
    await page.waitForSelector('.ent-sheet .u-panel .u-cmd', { timeout: 15000 });
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
    await page.waitForSelector('.ent-run.ok, .ent-run.fail', { timeout: 20000 });
    await page.click('[data-run-details]');
    await page.waitForSelector('.ent-run-details');
    const idRun = await page.evaluate(() => document.querySelector('.ent-run-details').textContent);
    check('set_id: the run carries the dry run\'s new id', posts.length === sent + 2 && posts[sent + 1].body.new_id === 7 && posts[sent + 1].body.token
      && !('dry_run' in posts[sent + 1].body) && /ActionCompleted/.test(idRun), { body: posts[sent + 1] && posts[sent + 1].body, idRun });
    // round 3.5: a dry run again is the menu again (the sheet has no "Dry run again"); typing another id in the menu closes it
    await utilities(page);
    await page.click('[data-util="set_id"]');
    await page.waitForSelector('.ent-sheet .u-panel .u-cmd', { timeout: 15000 });
    await utilities(page);
    await page.fill('[data-util-row="set_id"] [data-util-newid]', '9');
    await page.waitForTimeout(300);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    check('set_id: another new id closes the dry run', await page.evaluate(() => !document.querySelector('.ent-sheet') && S.util === null), '');

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
    await page.click(`.t6-row[data-t6-path="${await page.evaluate((entity) => S.built.byEntity.get(entity), ABSENT)}"]`);
    await page.waitForFunction(() => /data cable/.test((document.querySelector('.sec.why') || {}).textContent || ''), null, { timeout: 15000 }).catch(() => {});
    const absentWhy = await page.evaluate(() => ({ title: (document.querySelector('.d-title') || {}).textContent || '', why: (document.querySelector('.sec.why') || {}).textContent || '' }));
    check('absent: its row\'s What to check', /fdcanusb 28B3A25D/.test(absentWhy.title) && /1\. A data cable/.test(absentWhy.why) && /kernel evidence: never/.test(absentWhy.why), { title: absentWhy.title, why: absentWhy.why.slice(0, 200) });
    const inEvery = [];
    for (const structure of ['p0', 'p1', 'p2', 'p3', 'p4', 'p5']) {
      // p3 is off the Group by picker (round 3) but its file still loads: switch to it in the page
      if (structure === 'p3') await page.evaluate(() => { S.structure = 'p3'; render(); });
      else await pick(page, '#structure', structure);
      await page.waitForTimeout(400);
      inEvery.push(await page.evaluate((entity) => {
        const key = S.built.byEntity.get(entity);
        const row = key && S.built.byKey.get(key);
        return { structure: S.structure, row: Boolean(row && row.entity === entity && row.status.own === 'warn') };
      }, ABSENT));
    }
    check('absent: a warn row in every structure', inEvery.every((item) => item.row), inEvery);
    await page.unroute('**/api/v1/state');
    await pick(page, '#structure', 'p1');

    // ---- the seams, drawn
    const seam = async (structure, toggles, selected) => {
      await page.evaluate(({ id, on }) => { localStorage.setItem('io-diagnostics.structure', id); localStorage.setItem('io-diagnostics.structure-toggles', JSON.stringify({ [id]: on })); }, { id: structure, on: toggles });
      await open(page, base, selected);
      // p3 (the subtitle / suffix seams) is off the Group by picker since round 3: a remembered p3 falls back, so the
      // test switches to it in the page; its file and the seams stay
      if (structure === 'p3') {
        await page.evaluate(() => { S.structure = 'p3'; render(); });
        await page.waitForTimeout(500);
      }
    };
    await seam('p3', { showPaths: true }, CAN15);
    const p3 = await page.evaluate((key) => { const row = document.querySelector(`.t6-row[data-t6-path="${key}"]`); return { suffix: (row.querySelector('.t6-suffix') || {}).textContent, sub: (row.nextElementSibling || {}).className, subText: (row.nextElementSibling || {}).textContent, id: (row.querySelector('.t6-id') || {}).textContent }; }, CAN15);
    check('seams: p3 subtitle + suffix, id kept', /CAN bus/.test(p3.suffix || '') && p3.sub === 't6-subline' && /Port 1-3/.test(p3.subText) && p3.id === 'ABoAQVQwUBUgOTNO', p3);
    const others = {};
    for (const [variant, prefix] of [['v1', 't1'], ['v2', 't2']]) {
      await pick(page, '#tree-variant', variant);
      await page.waitForTimeout(700);
      others[variant] = await page.evaluate(({ key, pre }) => { const row = document.querySelector(`.${pre}-row[data-${pre}-path="${key}"]`); return row ? { suffix: (row.querySelector(`.${pre}-suffix`) || {}).textContent, sub: (row.nextElementSibling || {}).className } : null; }, { key: CAN15, pre: prefix });
    }
    await pick(page, '#tree-variant', 'v6');
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
    await viewOptions(page);
    await page.check('#structure-toggles [data-opt="connections"]');
    await page.waitForTimeout(300);
    const p2on = await page.evaluate(() => [...document.querySelectorAll('#structure-toggles [data-opt]')].map((box) => box.dataset.opt));
    check('seams: toggles[].when (p2 Empty ports)', p2off.join() === 'connections' && p2on.join() === 'connections,showEmpty', { p2off, p2on });
    await seam('p1', {}, CAN15);
    await pick(page, '#skin', 'terminal');
    await page.waitForTimeout(1300);
    const guides = await page.evaluate((key) => (document.querySelector(`.t6-row[data-t6-path="${key}"] .t6-guide`) || {}).textContent, CAN15);
    check('seams: Terminal draws box guides', /^(?:[│ ]  )*[├└]─ $/.test(guides || ''), guides);
    await page.screenshot({ path: path.join(SHOTS, 'integration-terminal-guides.png'), clip: { x: 0, y: 300, width: 470, height: 420 } });
    await pick(page, '#skin', 'contrast');
    const focus = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--io-focus-width').trim());
    check('seams: --io-focus-width per skin', focus === '3px', focus);
    await pick(page, '#skin', 'contrast');
    await page.click('[data-view="table"]');
    await page.waitForSelector('table.grid tbody tr');
    const mono = await page.evaluate(() => { const td = document.querySelector('table.grid tbody tr[data-key] td.xcol'); return td ? getComputedStyle(td).fontFamily : ''; });
    const monoRef = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--io-font-mono').trim());
    check('seams: mono extra column', Boolean(mono) && mono.replace(/\s/g, '') === monoRef.replace(/\s/g, ''), { mono, monoRef });
    check('no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
    await context.close();
    await panes(browser, base);
    await headerEverywhere(browser, base);
    await navigation(browser, base);
    await computerDefault(browser, base);
    for (const scheme of ['light', 'dark']) await round3Shots(browser, base, scheme);
  } finally {
    stopServer(server.child);
  }
  try {
    await historyWindow(browser);
    await depthColormap(browser);
    await expectedSetup(browser);
  } finally {
    await browser.close();
  }
  console.log(`\n${passed}/${passed + failed} checks passed · screenshots in ${SHOTS}`);
  process.exitCode = failed ? 1 : 0;
}

// ---- round 3: the panes (Settings/Memory/feedback_resizable_panels.md) ----
// Drag each sash with the mouse in headless Chrome, reload, and read the width back; a double-click restores the
// default; every pane keeps its minimum; the Table's inspector folds and unfolds (row click, the same row, the table's
// empty space, Escape, its close button, `]` to pin, remembered across a reload). Widths are measured
// (getBoundingClientRect), never assumed.
async function panes(browser, base) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const width = (selector) => page.evaluate((sel) => Math.round(document.querySelector(sel).getBoundingClientRect().width), selector);
  const stored = (key) => page.evaluate((k) => localStorage.getItem(k), key);
  const drag = async (dx, x = null) => {
    const box = await page.locator('#sash').boundingBox();
    const from = { x: box.x + box.width / 2, y: box.y + 200 };
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(x === null ? from.x + dx / 2 : (from.x + x) / 2, from.y, { steps: 4 });
    await page.mouse.move(x === null ? from.x + dx : x, from.y, { steps: 4 });
    await page.mouse.up();
    await page.waitForTimeout(400);
  };
  const settle = () => page.waitForTimeout(400); // the inspector's .18 s slide
  try {
    await open(page, base);
    const tree0 = await width('#left');
    await drag(150);
    const dragged = await width('#left');
    const saved = await stored('io-diagnostics.pane.tree');
    await page.reload();
    await ready(page);
    const reloaded = await width('#left');
    check('panes: the tree|detail sash drags and is remembered', tree0 === 470 && Math.abs(dragged - 620) <= 1 && saved === String(dragged) && reloaded === dragged, { tree0, dragged, saved, reloaded });
    await drag(0, 20);
    const least = await width('#left');
    await drag(0, VIEWPORT.width - 20);
    const detailLeast = await width('#inspector');
    await page.dblclick('#sash');
    await page.waitForTimeout(400);
    const reset = { tree: await width('#left'), stored: await stored('io-diagnostics.pane.tree') };
    await page.reload();
    await ready(page);
    check('panes: minimum widths; a double-click restores the default', least === 280 && detailLeast >= 360 && reset.tree === 470 && reset.stored === null && (await width('#left')) === 470,
      { least, detailLeast, reset });

    // the Table: the inspector folded, a row slides it in, and it folds again five ways
    await page.click('[data-view="table"]');
    await page.waitForSelector('table.grid tbody tr');
    await settle();
    const folded = { inspector: await width('#inspector'), table: await width('#left'), sash: await page.evaluate(() => getComputedStyle(document.getElementById('sash')).display) };
    const row = 'table.grid tbody tr[data-key] td.namecell .name';
    await page.locator(row).nth(2).click();
    await settle();
    const opened = { inspector: await width('#inspector'), key: await page.evaluate(() => (document.querySelector('table.grid tr.sel') || {}).dataset.key), sel: await page.evaluate(() => S.sel) };
    check('inspector: folded by default, a row click slides it in on that row', folded.inspector === 0 && folded.table >= VIEWPORT.width - 2 && folded.sash === 'none'
      && opened.inspector === 460 && opened.key === opened.sel, { folded, opened });
    const ways = {};
    await page.locator('table.grid tr.sel td.namecell .name').click();
    await settle();
    ways.sameRow = await width('#inspector');
    await page.locator(row).nth(2).click();
    await settle();
    await page.keyboard.press('Escape');
    await settle();
    ways.escape = await width('#inspector');
    await page.locator(row).nth(2).click();
    await settle();
    await page.mouse.click(400, 960); // the table's empty space below its rows
    await settle();
    ways.emptySpace = await width('#inspector');
    await page.locator(row).nth(2).click();
    await settle();
    await page.click('#insp-close');
    await settle();
    ways.closeButton = await width('#inspector');
    check('inspector: folds on the same row, Escape, the empty space, its close button', Object.values(ways).every((w) => w === 0), ways);
    await page.keyboard.press(']');
    await settle();
    const pinned = { width: await width('#inspector'), pressed: await page.getAttribute('#insp-toggle', 'aria-pressed') };
    await page.locator(row).nth(3).click();
    await settle();
    await page.mouse.click(400, 960);
    await page.keyboard.press('Escape');
    await settle();
    pinned.stays = await width('#inspector');
    await drag(-120);
    pinned.dragged = await width('#inspector');
    pinned.saved = await stored('io-diagnostics.pane.inspector');
    await page.reload();
    // round 3.5: the URL carries the view (?view=table), so the reload comes back in the Table
    await ready(page, 'table.grid tbody tr');
    await page.click('[data-view="table"]');
    await settle();
    pinned.reloaded = await width('#inspector');
    await page.dblclick('#sash');
    await page.waitForTimeout(400);
    pinned.reset = await width('#inspector');
    await page.keyboard.press(']');
    await settle();
    pinned.unpinned = await width('#inspector');
    check('inspector: `]` pins it open (remembered), its sash drags and resets', pinned.width === 460 && pinned.pressed === 'true' && pinned.stays === 460
      && Math.abs(pinned.dragged - 580) <= 1 && pinned.saved === String(pinned.dragged) && pinned.reloaded === pinned.dragged && pinned.reset === 460 && pinned.unpinned === 0, pinned);
    await page.click('#insp-toggle');
    await settle();
    const toggled = await width('#inspector');
    await page.click('#insp-toggle');
    await settle();
    check('inspector: the toolbar toggle pins and unpins it', toggled === 460 && (await width('#inspector')) === 0, { toggled });
    check('panes: no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
  } finally {
    await context.close();
  }
}

// ---- round 3.5: the Utilities ▾ is the shell's, at the same place in every layout's header, and nowhere else ----
const LAYOUTS = ['d0', 'd1', 'd2', 'd3', 'd4', 'd5'];
async function headerEverywhere(browser, base) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const seen = [];
  try {
    await page.goto(`${base}/`);
    for (const layout of LAYOUTS) {
      await page.evaluate((id) => localStorage.setItem('io-diagnostics.detail-layout', id), layout);
      for (const entity of [CAN15, ADAPTER, 'host']) {
        await open(page, base, entity);
        await page.waitForTimeout(700); // the layout's history answers land
        const at = await page.evaluate(() => {
          const button = document.getElementById('ent-util-btn');
          const head = button && button.closest('.d-head');
          const pane = document.getElementById('detail');
          const b = button.getBoundingClientRect();
          const right = pane.getBoundingClientRect().left + pane.clientWidth;
          return { layout: S.layout, buttons: document.querySelectorAll('#ent-util-btn').length, inHead: Boolean(head), dx: Math.round(right - b.right),
            dy: head ? Math.round(b.top - head.getBoundingClientRect().top) : null, label: button.textContent.trim(),
            outside: [...document.querySelectorAll('[data-util], .u-btn, .urow, [data-util-row], .u-panel')].filter((el) => !el.closest('.ent-tools')).length,
            sections: document.querySelectorAll('#detail .sec.utilities, #detail [data-sec="utilities"]').length };
        });
        seen.push({ entity: entity.split('/').slice(-2).join('/'), ...at });
      }
      // the menu opens there and holds every utility; still nothing outside it
      await open(page, base, CAN15);
      await utilities(page);
      const menu = await page.evaluate(() => ({ items: document.querySelectorAll('.ent-menu [data-util]').length, actions: S.node.actions.length,
        outside: [...document.querySelectorAll('[data-util]')].filter((el) => !el.closest('.ent-menu')).length, layout: S.layout }));
      seen.push({ entity: 'menu', ...menu });
      if (layout === 'd5' || layout === 'd1') {
        await page.mouse.move(5, 995);
        await page.screenshot({ path: path.join(ROUND3, `r35-header-menu-${layout}.png`), clip: await page.locator('#inspector').boundingBox() });
      }
    }
    const rows = seen.filter((item) => item.entity !== 'menu');
    const menus = seen.filter((item) => item.entity === 'menu');
    check('header: Utilities ▾ in every layout\'s header', rows.length === 18 && rows.every((item) => item.buttons === 1 && item.inHead && item.label === 'Utilities')
      && LAYOUTS.every((id) => rows.some((item) => item.layout === id)), rows.map((item) => `${item.layout} ${item.entity}: ${item.inHead ? 'head' : 'NOT in head'}`).join(' · '));
    check('header: the same place on every layout and entity', rows.every((item) => item.dx === rows[0].dx && item.dy === rows[0].dy) && rows[0].dx === 14 && rows[0].dy === 12,
      rows.map((item) => `${item.layout} ${item.entity} ${item.dx},${item.dy}`).join(' · '));
    check('header: the utilities live only there', rows.every((item) => item.outside === 0 && item.sections === 0)
      && menus.length === 6 && menus.every((item) => item.items === item.actions && item.items >= 5 && item.outside === 0), { rows: rows.filter((item) => item.outside || item.sections), menus });
    check('header: no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
  } finally {
    await context.close();
  }
}

// ---- round 3.5: back / forward through the drill-downs (pushState / popstate), and the mouse's own buttons ----
async function navigation(browser, base) {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const now = () => page.evaluate(() => ({ query: decodeURIComponent(location.search).replace(/\+/g, ' '), sel: S.sel, diag: S.drill.diag, m: S.drill.m, view: S.view,
    title: (document.querySelector('#detail .d-title span') || {}).textContent || '', entityPage: Boolean(document.querySelector('#detail .d1-tab')),
    level: (document.querySelector('#detail .d5-root') || { dataset: {} }).dataset.d5Level || '' }));
  const settle = () => page.waitForTimeout(700);
  // the mouse's back / forward buttons, as real input events through the browser (CDP): Chrome navigates on them
  const cdp = await context.newCDPSession(page);
  const mouse = async (button) => {
    for (const type of ['mousePressed', 'mouseReleased']) {
      await cdp.send('Input.dispatchMouseEvent', { type, x: 900, y: 700, button, buttons: type === 'mousePressed' ? (button === 'back' ? 8 : 16) : 0, clickCount: 1 });
    }
    await settle();
  };
  try {
    await page.goto(`${base}/`);
    await page.evaluate(() => localStorage.setItem('io-diagnostics.detail-layout', 'd1'));
    await open(page, base, CAN15);
    const start = await now();
    await page.click(`.t6-row[data-t6-path="${ADAPTER}"] .t6-name`);
    await page.waitForFunction((p) => S.node && S.node.node.path === p && document.querySelector('[data-d1-open]'), ADAPTER, { timeout: 15000 });
    await settle();
    const adapter = await now();
    const name = await page.getAttribute('[data-d1-open]', 'data-d1-open');
    await page.click(`a.d1-open[data-d1-open="${name}"]`);
    await settle();
    const drilled = await now();
    await page.click('[data-view="table"]');
    await settle();
    const table = await now();
    check('history: each step pushes a URL that says it', start.query === `?sel=${CAN15}&view=tree` && adapter.query === `?sel=${ADAPTER}&view=tree`
      && drilled.query === `?sel=${ADAPTER}&diag=${name}&view=tree` && drilled.title === name && table.query === `?sel=${ADAPTER}&diag=${name}&view=table`,
      { start: start.query, adapter: adapter.query, drilled: drilled.query, table: table.query });
    const back = [];
    for (let at = 0; at < 3; at += 1) { await page.goBack(); await settle(); back.push(await now()); }
    check('history: page.goBack() walks view → diagnostic → entity → entity', back[0].view === 'tree' && back[0].diag === name && back[0].title === name
      && back[1].sel === ADAPTER && !back[1].diag && back[1].entityPage && back[2].sel === CAN15 && !back[2].diag, back.map((item) => `${item.query} [${item.title}]`));
    const forward = [];
    for (let at = 0; at < 2; at += 1) { await page.goForward(); await settle(); forward.push(await now()); }
    check('history: page.goForward() restores the entity, then its diagnostic page', forward[0].sel === ADAPTER && forward[0].entityPage
      && forward[1].diag === name && forward[1].title === name && forward[1].view === 'tree', forward.map((item) => `${item.query} [${item.title}]`));
    // the layout's own back link is the browser's Back when the viewer came down from that level: one linear list
    await page.click('[data-d1-back]:not([data-go])');
    await settle();
    const link = await now();
    await page.goForward();
    await settle();
    const relinked = await now();
    check('history: the layout\'s ← is Back (forward returns)', link.sel === ADAPTER && !link.diag && link.entityPage && relinked.diag === name, { link: link.query, relinked: relinked.query });
    await mouse('back');
    const mouseBack = await now();
    await mouse('forward');
    const mouseForward = await now();
    check('history: the mouse\'s back / forward buttons', !mouseBack.diag && mouseBack.sel === ADAPTER && mouseBack.entityPage && mouseForward.diag === name && mouseForward.title === name,
      { back: mouseBack.query, forward: mouseForward.query });
    await page.reload();
    await ready(page);
    await settle();
    const reloaded = await now();
    check('history: a reload lands on the same drill step', reloaded.sel === ADAPTER && reloaded.diag === name && reloaded.title === name, reloaded.query);
    // a measurement page (d5): diagnostic → measurement → back → back
    const target = await page.evaluate(async (paths) => {
      for (const entity of paths) {
        const node = await (await fetch(`/api/v1/node?path=${encodeURIComponent(entity)}`)).json();
        const found = (node.checks || []).find((item) => (item.measurements || []).length);
        if (found) return { entity, name: found.name, key: found.measurements[0].key };
      }
      return null;
    }, [CAN15, ADAPTER, SECOND, `${SECOND}/can`, 'host', `${CAN15}/driver`]);
    if (!target) check('history: a measurement page', false, 'no check with measurements in F1');
    else {
      await page.evaluate(() => localStorage.setItem('io-diagnostics.detail-layout', 'd5'));
      await open(page, base, target.entity);
      await page.waitForSelector(`[data-d5-push="diag"][data-d5-name="${target.name}"]`, { timeout: 15000 });
      await page.click(`[data-d5-push="diag"][data-d5-name="${target.name}"]`);
      await page.waitForSelector(`[data-d5-push="metric"][data-d5-key="${target.key}"]`, { timeout: 15000 });
      await page.click(`[data-d5-push="metric"][data-d5-key="${target.key}"]`);
      await settle();
      const metric = await now();
      await page.goBack();
      await settle();
      const up1 = await now();
      await page.keyboard.press('Escape'); // d5's own Back: one level up
      await settle();
      const up2 = await now();
      await page.goForward();
      await settle();
      const again = await now();
      check('history: a measurement page is a step too', metric.level === 'metric' && metric.m === target.key && new RegExp(`&m=${target.key}`).test(metric.query)
        && up1.level === 'diag' && up1.diag === target.name && !up1.m && up2.level === 'entity' && !up2.diag && again.level === 'diag', { target, metric: metric.query, up1: up1.level, up2: up2.level, again: again.level });
    }
    check('history: no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
  } finally {
    await context.close();
  }
}

// ---- every entity, the Computer too, opens in d6 "Entity page" by default (2026-10-24: tabs; the Computer's Processes tab); an explicit Prototype pick wins everywhere ----
async function computerDefault(browser, base) {
  const context = await (browser.newContextRaw || browser.newContext)({ viewport: VIEWPORT, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const state = () => page.evaluate(() => ({ layout: S.layout, picker: document.getElementById('detail-layout').value, d4: Boolean(document.querySelector('#detail .d6-tabs')),
    stored: localStorage.getItem('io-diagnostics.detail-layout') }));
  try {
    await open(page, base, 'host');
    await page.waitForSelector('#detail .d6-tabs', { timeout: 10000 });
    const host = await state();
    await page.mouse.move(5, 995);
    await page.screenshot({ path: path.join(ROUND3, 'r35-computer-d6.png') });
    await page.click(`.t6-row[data-t6-path="${CAN15}"] .t6-name`);
    await page.waitForFunction((p) => S.node && S.node.node.path === p, CAN15, { timeout: 15000 });
    await page.waitForTimeout(400);
    const other = await state();
    check('layout: a fresh profile opens every entity, the Computer too, in d6 (the entity page)', host.layout === 'd6' && host.picker === 'd6' && host.d4 && host.stored === null
      && other.layout === 'd6' && other.picker === 'd6' && other.d4 && other.stored === null, { host, other });
    await page.goBack();
    await page.waitForFunction(() => S.node && S.node.node.path === 'host', null, { timeout: 15000 });
    await page.selectOption('#detail-layout', 'd2');
    await page.waitForTimeout(500);
    await page.reload();
    await ready(page);
    await page.waitForTimeout(500);
    const picked = await state();
    await page.click(`.t6-row[data-t6-path="${CAN15}"] .t6-name`);
    await page.waitForFunction((p) => S.node && S.node.node.path === p, CAN15, { timeout: 15000 });
    await page.waitForTimeout(400);
    const pickedOther = await state();
    check('layout: an explicit pick sticks, on the Computer and everywhere', picked.layout === 'd2' && picked.picker === 'd2' && !picked.d4 && picked.stored === 'd2'
      && pickedOther.layout === 'd2', { picked, pickedOther });
    check('layout: no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
  } finally {
    await context.close();
  }
}

// ---- the history window in View options: GET / POST /api/v1/settings (page-only), on a server with its own config dir ----
async function historyWindow(browser) {
  const os = require('os');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'io-diagnostics-settings-'));
  const server = await startServer('F1', [], { IO_DIAGNOSTICS_CONFIG_DIR: dir });
  const base = server.base;
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const posts = [];
  page.on('request', (request) => { if (request.url().endsWith('/api/v1/settings') && request.method() === 'POST') posts.push({ body: JSON.parse(request.postData() || '{}'), headers: request.headers() }); });
  try {
    await page.goto(`${base}/`);
    await page.evaluate(() => localStorage.setItem('io-diagnostics.detail-layout', 'd1'));
    await open(page, base, ADAPTER);
    await viewOptions(page);
    await page.waitForSelector('#window-switch:not([hidden]) #history-window option', { state: 'attached' });
    const before = await page.evaluate(() => ({ value: document.getElementById('history-window').value, options: [...document.querySelectorAll('#history-window option')].map((o) => o.textContent) }));
    await page.screenshot({ path: path.join(ROUND3, 'r35-view-options-window.png'), clip: { x: 0, y: 0, width: 900, height: 620 } });
    await page.selectOption('#history-window', '600');
    await page.waitForFunction(() => S.settings && S.settings.history_window_s === 600, null, { timeout: 10000 });
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => /−10 min/.test((document.querySelector('#detail .d1-axis') || {}).textContent || ''), null, { timeout: 15000 });
    const host = await (await fetch(`${base}/api/v1/settings`)).json();
    const file = fs.existsSync(path.join(dir, 'settings.toml')) ? fs.readFileSync(path.join(dir, 'settings.toml'), 'utf8') : '';
    await page.reload();
    await ready(page);
    await viewOptions(page);
    const after = await page.evaluate(() => document.getElementById('history-window').value);
    check('history window: View options sets it for the app', before.value === '900' && before.options.includes('15 min') && posts.length === 1 && posts[0].body.history_window_s === 600
      && posts[0].headers['x-io-diagnostics'] === 'ui' && host.history_window_s === 600 && host.source === 'page' && /history_window_s = 600/.test(file) && after === '600',
    { before, posts: posts.map((post) => post.body), host: { value: host.history_window_s, source: host.source }, file: file.trim(), after });
    check('history window: no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
  } finally {
    await context.close();
    stopServer(server.child);
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// ---- the depth colour map in View options (Zach, 2026-10-24): POST /api/v1/settings, light and dark shots ----
const DEPTH_SHOTS = path.join(ROOT, 'reports', 'media', 'depth-colormap');
async function depthColormap(browser) {
  const os = require('os');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'io-diagnostics-depth-'));
  const server = await startServer('F1', [], { IO_DIAGNOSTICS_CONFIG_DIR: dir });
  const base = server.base;
  fs.mkdirSync(DEPTH_SHOTS, { recursive: true });
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const posts = [];
  page.on('request', (request) => { if (request.url().endsWith('/api/v1/settings') && request.method() === 'POST') posts.push(JSON.parse(request.postData() || '{}')); });
  const hostSettings = async () => (await fetch(`${base}/api/v1/settings`)).json();
  const read = () => page.evaluate(() => ({
    map: document.getElementById('depth-colormap').value, names: [...document.querySelectorAll('#depth-colormap option')].map((o) => o.textContent),
    equalize: document.getElementById('depth-equalize').checked, rangeHidden: document.getElementById('depth-range-switch').hidden,
    min: document.getElementById('depth-min').value, max: document.getElementById('depth-max').value }));
  const shoot = async (scheme) => {
    const pop = await page.locator('#view-options').boundingBox();
    await page.screenshot({ path: path.join(DEPTH_SHOTS, `view-options-depth-${scheme}.png`),
      clip: { x: 0, y: 0, width: Math.min(VIEWPORT.width, pop.x + pop.width + 40), height: pop.y + pop.height + 30 } });
  };
  try {
    await page.goto(`${base}/`);
    await page.evaluate(() => localStorage.setItem('io-diagnostics.detail-layout', 'd1'));
    await open(page, base, ADAPTER);
    await viewOptions(page);
    await page.waitForSelector('#depthmap-switch:not([hidden]) #depth-colormap option', { state: 'attached' });
    const before = await read();
    check('depth colour map: View options lists realsense-viewer\'s names, Jet first and chosen, equalization on, no range yet',
      before.map === 'Jet' && before.names.slice(0, 10).join(',') === 'Jet,Classic,White to Black,Black to White,Bio,Cold,Warm,Quantized,Pattern,Hue'
      && before.names.length === 11 && before.equalize && before.rangeHidden, before);
    await page.selectOption('#depth-colormap', 'Hue');
    await page.waitForFunction(() => S.settings && S.settings.depth_colormap === 'Hue', null, { timeout: 10000 });
    await page.uncheck('#depth-equalize');
    await page.waitForFunction(() => S.settings && S.settings.depth_equalize === false, null, { timeout: 10000 });
    await page.waitForSelector('#depth-range-switch:not([hidden])');
    await page.fill('#depth-max', '3');
    await page.press('#depth-max', 'Tab');
    await page.waitForFunction(() => S.settings && S.settings.depth_max_m === 3, null, { timeout: 10000 });
    await page.waitForTimeout(150);
    const host = await hostSettings();
    const file = fs.existsSync(path.join(dir, 'settings.toml')) ? fs.readFileSync(path.join(dir, 'settings.toml'), 'utf8') : '';
    check('depth colour map: each change is one page-only POST, lands in the host and the settings file',
      posts.length === 3 && posts[0].depth_colormap === 'Hue' && posts[1].depth_equalize === false && posts[2].depth_min_m === 0 && posts[2].depth_max_m === 3
      && host.depth_colormap === 'Hue' && host.depth_equalize === false && host.depth_max_m === 3
      && /depth_colormap = "Hue"/.test(file) && /depth_equalize = false/.test(file) && /depth_max_m = 3/.test(file),
    { posts, host: { map: host.depth_colormap, eq: host.depth_equalize, max: host.depth_max_m }, file: file.trim().split('\n').slice(-4) });
    const bad = await page.evaluate(async () => {
      const response = await fetch('/api/v1/settings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify({ depth_colormap: 'Rainbow' }) });
      return response.status;
    });
    check('depth colour map: a name that is not listed is refused (400) and nothing changes', bad === 400 && (await hostSettings()).depth_colormap === 'Hue', bad);
    await page.reload();
    await ready(page);
    await viewOptions(page);
    const after = await read();
    check('depth colour map: the choice survives a reload (equalization off shows the range)',
      after.map === 'Hue' && !after.equalize && !after.rangeHidden && after.min === '0' && after.max === '3', after);
    await page.mouse.move(5, 995);
    await shoot('light');
    check('depth colour map: no page errors', errors.length === 0, errors.slice(0, 3).join(' | ') || 'none');
  } finally {
    await context.close();
  }
  const dark = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'dark' });
  const darkPage = await dark.newPage();
  try {
    await darkPage.goto(`${base}/`);
    await darkPage.evaluate(() => localStorage.setItem('io-diagnostics.detail-layout', 'd1'));
    await open(darkPage, base, ADAPTER);
    await viewOptions(darkPage);
    await darkPage.waitForSelector('#depthmap-switch:not([hidden]) #depth-colormap option', { state: 'attached' });
    await darkPage.mouse.move(5, 995);
    const pop = await darkPage.locator('#view-options').boundingBox();
    await darkPage.screenshot({ path: path.join(DEPTH_SHOTS, 'view-options-depth-dark.png'),
      clip: { x: 0, y: 0, width: Math.min(VIEWPORT.width, pop.x + pop.width + 40), height: pop.y + pop.height + 30 } });
  } finally {
    await dark.close();
    stopServer(server.child);
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// ---- round 3 screenshots for the report: reports/media/round3/a-*.png, light and dark ----
async function round3Shots(browser, base, scheme) {
  fs.mkdirSync(ROUND3, { recursive: true });
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: scheme });
  const page = await context.newPage();
  const shot = (name, clip) => page.screenshot({ path: path.join(ROUND3, `a-${name}-${scheme}.png`), ...(clip ? { clip } : {}) });
  try {
    await open(page, base, CAN15);
    await page.mouse.move(5, 995);
    await shot('tree-inspector');
    await viewOptions(page);
    await page.waitForTimeout(200);
    const pop = await page.locator('#view-options').boundingBox();
    await shot('view-options', { x: 0, y: 0, width: Math.min(VIEWPORT.width, pop.x + pop.width + 40), height: pop.y + pop.height + 30 });
    await page.keyboard.press('Escape');
    // round 3.5: the utilities are the header's Utilities ▾ menu; the shots frame the menu (and the header above it)
    await utilities(page);
    const util = async () => { const box = await page.locator('.ent-menu').boundingBox(); return { x: box.x - 10, y: box.y - 46, width: box.width + 20, height: box.height + 56 }; };
    await page.mouse.move(5, 995);
    await page.waitForTimeout(200);
    await shot('utilities-quiet', await util());
    await page.hover('[data-util="update_firmware"] .u-title');
    await page.waitForTimeout(700);
    const tipBox = await util();
    await shot('utilities-tooltip', { ...tipBox, height: tipBox.height + 60 });
    // busy: a query held 6 s at the network: the quiet line under the header
    await open(page, base, ADAPTER);
    await page.route('**/api/v1/act', async (route) => {
      if (!JSON.parse(route.request().postData() || '{}').dry_run) await new Promise((resolve) => setTimeout(resolve, 6000));
      await route.continue();
    });
    await utilities(page);
    await page.click('[data-util="check_comms"]');
    await page.waitForSelector('[data-util-run]');
    await page.click('[data-util-run]');
    await page.waitForTimeout(3200);
    await page.mouse.move(5, 995);
    await shot('utilities-busy', await page.locator('#inspector').boundingBox());
    await page.waitForSelector('.ent-run.ok, .ent-run.fail', { timeout: 20000 });
    await page.unroute('**/api/v1/act');
    await page.click('[data-view="table"]');
    await page.waitForTimeout(500);
    await shot('table-folded');
    await page.locator('table.grid tbody tr[data-key] td.namecell .name').nth(2).click();
    await page.waitForTimeout(500);
    await page.mouse.move(5, 995);
    await shot('table-open');
    await page.click('[data-view="activity"]');
    await page.waitForTimeout(500);
    await shot('activity');
    // activity log v1 (2026-10-24): the All / Faults / Runs views are data-al-preset buttons (it was data-act-filter="faults")
    await page.click('[data-al-preset="faults"]');
    await page.waitForTimeout(300);
    await shot('activity-faults');
  } finally {
    await context.close();
  }
}

// ---- the expected setup (docs/expected-setup.md), on F1 with a rig file of its own (a temporary copy) ----
// F1: 272813EF answers CAN 1, 14, 15 (the file also expects 16), 28B3A25D is plugged in on 1-5 (the file retires it),
// fdcanusb 99990000 is expected and never there.
const RIG = 'host/rig-1-dof-test-rack';
const LOST_NAME = 'fdcanusb 99990000: presence'; // an absent expected device is a check on the Computer, never a row (Zach, 2026-10-24)
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
    // "first": the first row under the computer's root row (the root, bam-GPU, is the very first row)
    return row ? { name: row.querySelector('.t6-name').textContent, shape: Boolean(row.querySelector('.t6-st .t6-shape')), first: row === document.querySelectorAll('.t6-row')[1] } : null;
  }, RIG);
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    check('expected: only the fixture plugin is loaded', plugins.plugins.map((plugin) => plugin.name).join() === 'v6_scenario');

    // the rig row: first, the counts in its name, a warn shape; its card offers Expect what's here
    await open(page, base, RIG);
    await page.waitForSelector('.sec.setup [data-setup="expect"]', { timeout: 15000 });
    const rig = await rigRow();
    const rigCard = await page.evaluate(() => ({ buttons: [...document.querySelectorAll('.sec.setup [data-setup]')].map((b) => b.dataset.setup), why: (document.querySelector('.sec.why') || {}).textContent || '' }));
    check('expected: the rig row counts at a glance', rig && rig.name === '1-DOF test rack · 1/2 adapters · 3/4 boards · 2/2 cameras' && rig.shape && rig.first, rig);
    check('expected: the rig row names what is missing', /CAN 16 on fdcanusb 272813EF/.test(rigCard.why) && /fdcanusb 99990000: not on this PC/.test(rigCard.why)
      && rigCard.buttons.join() === 'expect', rigCard);
    await page.screenshot({ path: path.join(SHOTS, 'expected-rig-row.png'), clip: { x: 0, y: 0, width: VIEWPORT.width, height: 560 } });

    // the retired adapter, plugged in again (F1's 28B3A25D on 1-5): a warning on its own row, the reason in its card
    await open(page, base, SECOND);
    await page.waitForSelector('.sec.setup .s-retired', { timeout: 15000 });
    const retired = await page.evaluate((entity) => {
      const row = S.built.byKey.get(S.built.byEntity.get(entity));
      return { own: row.status.own, card: document.querySelector('.sec.setup .s-retired').textContent,
        check: [...document.querySelectorAll('#detail .check')].map((card) => card.textContent).find((text) => /: retired/.test(text)) || '' };
    }, SECOND);
    check('expected: a retired adapter plugged in again is a warn row with its reason', retired.own === 'warn'
      && retired.card === 'Retired 2026-10-23: frames never reached the bus (2026-10-22); replaced by 0607623A' && /RetiredDevice/.test(retired.check), retired);
    await page.screenshot({ path: path.join(SHOTS, 'expected-retired-row.png'), clip: { x: 0, y: 0, width: VIEWPORT.width, height: 760 } });

    // Retire…: a reason first (typed key by key: the box keeps its focus while the card redraws), Cancel writes nothing
    await open(page, base, ADAPTER);
    await page.click('.sec.setup [data-setup="retire"]');
    await page.waitForSelector('.sec.setup [data-setup-reason]');
    const disabledFirst = await page.$eval('[data-setup-confirm]', (button) => button.disabled);
    await page.click('[data-setup-reason]');
    await page.keyboard.type('loose USB socket', { delay: 30 });
    const typed = await page.evaluate(() => ({ value: document.querySelector('[data-setup-reason]').value, focus: document.activeElement.matches('[data-setup-reason]'),
      enabled: !document.querySelector('[data-setup-confirm]').disabled, confirm: document.querySelector('.s-panel .confirm').textContent }));
    check('expected: Retire… needs a reason and says it in the confirm', disabledFirst && typed.value === 'loose USB socket' && typed.focus && typed.enabled
      && typed.confirm === 'Retire fdcanusb 272813EF? It leaves 1-DOF test rack. Plugged in again, its row says “retired 2026-10-21: loose USB socket” (a warning).', { disabledFirst, ...typed });
    await page.locator('.sec.setup').scrollIntoViewIfNeeded();
    await page.locator('.sec.setup').screenshot({ path: path.join(SHOTS, 'expected-controls-retire.png') });
    await page.click('[data-setup-cancel]');
    check('expected: Cancel writes nothing', fs.readFileSync(file, 'utf8') === RIG_FILE);

    // Expect what's here on the adapter: the confirm names the change; the file and the rig row follow
    await page.click('.sec.setup [data-setup="expect"]');
    await page.waitForSelector('.s-panel .confirm');
    const expectText = await page.textContent('.s-panel .confirm');
    check('expected: Expect what\'s here says exactly what changes', expectText === 'Expect fdcanusb 272813EF in 1-DOF test rack with can/1, can/14, can/15 (can/16 no longer expected)?', expectText);
    await page.locator('.sec.setup').screenshot({ path: path.join(SHOTS, 'expected-controls-expect.png') });
    await page.click('[data-setup-confirm]');
    await page.waitForSelector('.s-panel .u-result.ok', { timeout: 15000 });
    await page.waitForFunction((key) => /3\/3 boards/.test((document.querySelector(`.t6-row[data-t6-path="${key}"] .t6-name`) || {}).textContent || ''), RIG, { timeout: 15000 });
    const written = fs.readFileSync(file, 'utf8');
    check('expected: Expect what\'s here rewrote the file and the rig row', /"can\/15"/.test(written) && !/can\/16/.test(written)
      && (await rigRow()).name === '1-DOF test rack · 1/2 adapters · 3/3 boards · 2/2 cameras', (await rigRow()).name);

    // the adapter that is never there: no row (a missing thing is a diagnostic on its parent); the Computer says it
    await open(page, base, 'host');
    const computer = await (await fetch(`${base}/api/v1/node?path=host`)).json();
    const lostCheck = computer.checks.find((each) => each.name === LOST_NAME) || {};
    const lostRow = await page.evaluate(() => [...S.built.byKey.values()].some((row) => /99990000/.test(`${row.key} ${row.label}`) && row.kind !== 'rig'));
    check('expected: an absent expected device is a warning on the Computer and has no row', !lostRow && computer.node.own.level === 'warn'
      && lostCheck.code === 'ExpectedAbsent' && /fdcanusb 99990000 expected \(1-DOF test rack\)/.test(lostCheck.message || ''), { lostRow, own: computer.node.own.level, lostCheck });
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
