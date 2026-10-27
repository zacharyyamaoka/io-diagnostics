#!/usr/bin/env node
// Acceptance for the page layouts (io_diagnostics/web/layouts/: L1 Obsidian, L2 VS Code, L3 Claude desktop, L4 Notion,
// L5 System Monitor), in the REAL page on a fixture server, headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_layouts.js
//
// It starts its own `cli.py serve --fixture F1 --port 0 --no-udev` (a free port, a temporary state dir, stopped by its
// process group) and refuses to go on unless /api/v1/plugins lists only the fixture plugin: nothing here touches hardware.
// Per layout:
//   picker      the bottom-right "Layout" select lists L1..L5, switching is live, the pick survives a reload (never a URL flag)
//   search      Zach's ask: the search input's rect lies INSIDE the tree pane's rect and ABOVE its first row (getBoundingClientRect)
//   filter      typing in that search filters the tree, a board opens from it and its page is drawn
//   styles      every tree style (v1-v6) still draws in the layout's tree pane
//   views       every view is reachable from the layout's own controls (Tree, Table, Activity, Runs) and what it shows is on screen
//   resize      the tree pane's boundary drags, the size is remembered across a reload, double-click resets, the pane folds away
//               and comes back; every other boundary the layout draws moves by the arrow keys and is remembered too
//   scroll      no horizontal scroll at 1280x720 and 1400x900
//   clean       switching through all five and back leaves one layout root, one search box, no leftover style or overlay
//   sidebar     L1's three ribbon styles (View options > Sidebar; sidebarStyles() below), measured with getBoundingClientRect: the rail's
//               width per style, Figma's label centred under its icon, Shopify's label right of its icon, no label past the rail, badges
//               visible in every style, the choice kept across a reload, the toggle (edge, collapse button, Ctrl+Alt+R) returning to
//               the LAST open style. Stills: reports/media/sidebar-styles-2026-10-26/{collapsed,figma,wide}.png (IO_DIAG_SIDEBAR_SHOTS)
//   errors      no page errors, no console errors
// Static: the layouts' CSS carries no colour literal and names no status token (warn / error / stale / unknown colours belong
// to diagnostics, drawn by the page's own shapes). Screenshots: reports/media/layouts/<id>-check.png. Exits 1 on any failure.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'layouts');
const LAYOUTS = ['l1', 'l2', 'l3', 'l4', 'l5'];
const PICK = 'io-diagnostics.page-layout';
const SIDEBAR_SHOTS = process.env.IO_DIAG_SIDEBAR_SHOTS || path.join(ROOT, 'reports', 'media', 'sidebar-styles-2026-10-26');
const SIDEBAR_KEY = 'io-diagnostics.lx.sidebar';
const RUN = process.env.IO_DIAG_LAYOUTS ? process.env.IO_DIAG_LAYOUTS.split(',') : LAYOUTS; // a subset while iterating

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(54)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
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
  await page.waitForTimeout(400);
}
async function open(page, base, layout, query = '') {
  await page.addInitScript((value) => { try { if (!sessionStorage.getItem('lx-test-seeded')) { localStorage.setItem('io-diagnostics.page-layout', value); sessionStorage.setItem('lx-test-seeded', '1'); } } catch (_) { /* private */ } }, layout);
  await page.goto(`${base}/${query}`);
  await ready(page);
}
const visible = (page, selector) => page.evaluate((sel) => [...document.querySelectorAll(sel)].some((el) => {
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
}), selector);
const rectOf = (page, selector) => page.evaluate((sel) => {
  const el = [...document.querySelectorAll(sel)].find((node) => node.getBoundingClientRect().width > 0);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom };
}, selector);
// walk one view's controls: its `via` steps, then its own control
async function reach(page, id, view) {
  const spec = await page.evaluate(([layout, name]) => IOLayouts[layout].reach[name], [id, view]);
  for (const selector of spec.via || []) { await page.click(selector); await page.waitForTimeout(250); }
  await page.click(spec.control);
  await page.waitForTimeout(450);
  return spec;
}
// the Tree view with its pane showing (an Explorer-style control may fold the pane it is already on)
async function ensureTree(page, id) { if (!(await visible(page, '[data-lx-tree]')) || !(await visible(page, '#tree-host .t6-row'))) await reach(page, id, 'tree'); }
const noScroll = (page) => page.evaluate(() => ({
  doc: document.documentElement.scrollWidth - window.innerWidth,
  body: document.body.scrollWidth - window.innerWidth,
  root: (() => { const root = document.getElementById('lx-root'); return root.scrollWidth - root.clientWidth; })(),
}));

const rowCount = (page) => page.locator('#tree-host .t6-row').count();
const BEHAVIOURS = {
  // Obsidian: the pane's toolbar row collapses, expands and shows what is hidden; the ribbon marks the view; Settings opens View options
  async l1(page, tag) {
    await page.click('.l1-ribbon [data-l1="tree"]');
    const open = await rowCount(page);
    await page.click('.l1-tools [title="Collapse all"]');
    await page.waitForTimeout(250);
    const collapsed = await rowCount(page);
    await page.click('.l1-tools [title="Expand all"]');
    await page.waitForTimeout(250);
    const expanded = await rowCount(page);
    check(`${tag} the toolbar row collapses all and expands all`, collapsed < open && expanded >= open, { open, collapsed, expanded });
    const before = await rowCount(page);
    await page.click('.l1-tools [aria-pressed]:last-child');
    await page.waitForTimeout(400);
    const withHidden = await rowCount(page);
    await page.click('.l1-tools [aria-pressed]:last-child');
    check(`${tag} the eye shows what the grouping hides (Ports)`, withHidden > before, { before, withHidden });
    await page.click('.l1-ribbon [data-l1="table"]');
    check(`${tag} the ribbon marks the open view`, await page.evaluate(() => document.querySelector('.l1-ribbon [data-l1="table"]').getAttribute('aria-pressed') === 'true' && document.querySelector('.l1-ribbon [data-l1="tree"]').getAttribute('aria-pressed') === 'false'));
    await page.click('.l1-ribbon [data-l1="tree"]');
    await page.click('.l1-ribbon [title^="Settings"]');
    await page.waitForTimeout(250);
    const pop = await rectOf(page, '#view-options');
    check(`${tag} Settings opens View options beside the ribbon`, Boolean(pop) && pop.x >= 40 && pop.right <= 1400, pop);
    await page.keyboard.press('Escape');
    check(`${tag} the view header is a breadcrumb of the selection`, /bam-GPU/.test(await page.locator('.l1-crumbs').innerText()));
  },
  // VS Code: tabs open and close, Ctrl+J and Ctrl+B fold the panel and the sidebar, the status bar counts what needs you
  async l2(page, tag) {
    await page.click('.l2-act [data-l2="explorer"]');
    await page.waitForTimeout(200);
    if (!(await visible(page, '#tree-host .t6-row'))) await page.click('.l2-act [data-l2="explorer"]');
    const rows = page.locator('#tree-host .t6-row');
    await rows.filter({ hasText: 'CAN 15' }).first().dblclick();
    await page.waitForTimeout(500);
    await rows.filter({ hasText: 'CAN 14' }).first().dblclick();
    await page.waitForTimeout(500);
    const tabs = await page.locator('.l2-tab').count();
    check(`${tag} entity pages open as tabs (a double-click keeps one)`, tabs >= 3, tabs);
    await page.locator('.l2-tab', { hasText: 'CAN 15' }).first().click();
    await page.waitForTimeout(500);
    check(`${tag} a tab switches the page`, /CAN 15/.test(await page.locator('.d-title').first().innerText()));
    await page.locator('.l2-tab.on [data-l2-close]').click();
    await page.waitForTimeout(500);
    check(`${tag} closing a tab closes it`, (await page.locator('.l2-tab').count()) === tabs - 1, await page.locator('.l2-tab').count());
    await page.keyboard.press('Control+j');
    await page.waitForTimeout(250);
    const panelFolded = !(await visible(page, '.l2-panel'));
    await page.keyboard.press('Control+j');
    await page.waitForTimeout(250);
    check(`${tag} Ctrl+J folds and shows the bottom panel`, panelFolded && await visible(page, '.l2-panel'));
    await page.keyboard.press('Control+b');
    await page.waitForTimeout(250);
    const sideFolded = !(await visible(page, '[data-lx-tree]'));
    await page.keyboard.press('Control+b');
    await page.waitForTimeout(250);
    check(`${tag} Ctrl+B folds and shows the sidebar`, sideFolded && await visible(page, '[data-lx-tree]'));
    check(`${tag} the status bar counts what needs you`, /\d/.test(await page.locator('.statusbar .l2-sb').innerText()));
  },
  // Claude desktop: Show N hidden adds exactly N rows; the side pane lists the selection's events only
  async l3(page, tag) {
    const text = await page.locator('.l3-more').innerText();
    const claimed = Number((/(\d+) hidden/.exec(text) || [])[1]);
    const count = () => page.evaluate(() => { const walk = (rows) => rows.reduce((sum, row) => sum + 1 + walk(row.children), 0); return walk(S.built.rows); });
    const before = await count();
    await page.click('.l3-more');
    await page.waitForTimeout(500);
    const after = await count();
    check(`${tag} "Show N hidden" adds exactly N rows`, claimed > 0 && after - before === claimed, { claimed, before, after });
    await page.click('.l3-more');
    await page.waitForTimeout(300);
    await page.locator('#tree-host .t6-row', { hasText: 'CAN 15' }).first().click();
    await page.waitForTimeout(500);
    await page.click('.l3-bar [data-l3-pane="activity"]');
    await page.waitForTimeout(500);
    const events = await page.evaluate(() => [...document.querySelectorAll('.l3-ract .ev')].map((el) => el.dataset.go));
    const selected = await page.evaluate(() => S.sel);
    check(`${tag} the side pane lists the selection's events (and below) only`, events.length > 0 && events.every((path) => path.startsWith(selected)), { selected, events: events.length });
    await page.click('.l3-ract [data-l3-scope="all"]');
    await page.waitForTimeout(400);
    const all = await page.locator('.l3-ract .ev').count();
    check(`${tag} "All" lists every device's events`, all > events.length, { all, events: events.length });
    await page.click('.l3-bar [data-l3-pane="terminal"]');
    await page.waitForTimeout(400);
    check(`${tag} the terminal opens in the same pane`, await visible(page, '.l3-right #runs-root.r1'));
    await page.click('.l3-rhead [title="Close the side pane"]');
    await page.waitForTimeout(300);
    check(`${tag} the side pane closes`, !(await visible(page, '.l3-right')));
  },
  // Notion: the first screen is one calm answer; Ctrl-K jumps and runs; the breadcrumb is the hierarchy
  async l4(page, tag) {
    await page.click('.l4-side [data-l4-home]');
    await page.waitForTimeout(400);
    const home = await page.locator('.l4-home').innerText();
    check(`${tag} the first screen is one calm answer`, /need you|Everything is OK/.test(home) && !(await visible(page, '#detail .d-title')), home.split('\n')[0]);
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(300);
    check(`${tag} Ctrl-K opens the palette`, await visible(page, '.lx-pal'));
    await page.keyboard.type('can 14', { delay: 30 });
    await page.waitForTimeout(400);
    const first = await page.locator('.lx-pal [data-i]').first().innerText();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(700);
    check(`${tag} the palette jumps to a device and leaves Home`, !(await visible(page, '.lx-pal')) && /CAN 14/.test(await page.locator('.d-title').first().innerText()) && !(await visible(page, '.l4-home')), first.split('\n')[0]);
    const crumbs = await page.locator('.l4-crumbs a').allInnerTexts();
    check(`${tag} the breadcrumb is the hierarchy (bam-GPU > ... > CAN 14)`, crumbs[0] === 'bam-GPU' && /CAN 14/.test(crumbs[crumbs.length - 1]) && crumbs.length >= 3, crumbs);
    await page.keyboard.press('Control+k');
    await page.keyboard.type('activity', { delay: 30 });
    await page.keyboard.press('Enter');
    await page.waitForTimeout(600);
    check(`${tag} the palette runs an action (Activity)`, await visible(page, '.act .ev'));
    await page.click('.l4-top [data-l4-view="tree"]');
    await page.keyboard.press('Control+k');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    check(`${tag} Escape closes the palette`, !(await visible(page, '.lx-pal')));
    await page.keyboard.press('Control+\\');
    await page.waitForTimeout(250);
    const folded = !(await visible(page, '[data-lx-tree]'));
    await page.keyboard.press('Control+\\');
    await page.waitForTimeout(250);
    check(`${tag} Ctrl+\\ folds and shows the sidebar`, folded && await visible(page, '[data-lx-tree]'));
  },
  // System Monitor: four tabs; the Overview lists what needs you and opens it in Devices
  async l5(page, tag) {
    await page.click('.l5-tabs [data-l5-tab="overview"]');
    await page.waitForTimeout(600);
    check(`${tag} Overview: the Computer's page beside what needs you`, /Computer|bam-GPU/.test(await page.locator('.d-title').first().innerText()) && await visible(page, '.l5-needs .l5-need') && !(await visible(page, '[data-lx-tree]')));
    await page.locator('.l5-needs .l5-need').first().click();
    await page.waitForTimeout(700);
    check(`${tag} a "needs you" row opens that device in Devices`, await page.evaluate(() => document.getElementById('lx-root').dataset.l5on === 'devices') && await visible(page, '[data-lx-tree]') && await visible(page, '.d-title'));
    await page.click('.l5-fold');
    await page.waitForTimeout(250);
    const treeFolded = !(await visible(page, '[data-lx-tree]'));
    await page.click('.l5-fold');
    await page.waitForTimeout(250);
    check(`${tag} the side pane of the tab has a fold button`, treeFolded && await visible(page, '[data-lx-tree]'));
    await page.click('.l5-tabs [data-l5-tab="activity"]');
    await page.waitForTimeout(400);
    check(`${tag} Activity is a tab of its own, the tree is not drawn twice`, await visible(page, '.act .ev') && !(await visible(page, '[data-lx-tree]')));
    await page.click('.l5-tabs [data-l5-tab="devices"]');
    await page.waitForTimeout(500);
    check(`${tag} back in Devices the last device is open again`, await visible(page, '[data-lx-tree]') && await visible(page, '.d-title'));
    await page.reload();
    await ready(page);
    check(`${tag} the tab is remembered across a reload`, await page.evaluate(() => document.getElementById('lx-root').dataset.l5on === 'devices'));
  },
};

// ---- L1's three ribbon styles (View options > Sidebar), each measured on the page ----
async function sidebarStyles(browser, base, errors) {
  fs.mkdirSync(SIDEBAR_SHOTS, { recursive: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(`sidebar: ${error}`));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(`sidebar: console ${message.text()}`); });
  await open(page, base, 'l1');
  const mode = () => page.evaluate(() => document.querySelector('.l1-ribbon').dataset.railMode);
  const railWidth = async () => (await rectOf(page, '.l1-ribbon')).w;
  // the Settings choice: the gear opens View options, the Sidebar select picks, Escape closes
  const choose = async (value) => {
    await page.click('.l1-ribbon [title^="Settings"]');
    await page.waitForTimeout(200);
    await page.selectOption('#sidebar-style', value);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(250);
  };
  // every labelled ribbon button: its icon, label and button boxes, and the rail's
  const geometry = () => page.evaluate(() => {
    const box = (el) => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, cx: r.left + r.width / 2, cy: r.top + r.height / 2, width: r.width, height: r.height }; };
    const rows = [...document.querySelectorAll('.l1-ribbon .lx-ib')].filter((button) => button.querySelector('.l1-label') && button.getBoundingClientRect().width > 0).map((button) => {
      const label = button.querySelector('.l1-label');
      return {
        text: label.textContent, title: button.title, icon: box(button.querySelector('svg')), label: box(label), button: box(button),
        clipped: label.scrollHeight > label.clientHeight + 1 || label.scrollWidth > label.clientWidth + 1,
        shown: getComputedStyle(label).display !== 'none' && label.getBoundingClientRect().width > 0,
      };
    });
    return { rail: box(document.querySelector('.l1-ribbon')), rows };
  });
  const worst = (values) => Math.max(...values, 0);
  // before a still: focus off the rail and the pointer parked, so no focus ring or hover shows
  const settle = async () => { await page.evaluate(() => document.activeElement && document.activeElement.blur()); await page.mouse.move(1000, 600); await page.waitForTimeout(250); };
  // a badge, driven through the layout's own update() the way a running utility would
  const badges = async (tag) => {
    await page.evaluate(() => { window.lxRunsCount = IOLayoutHost.ctx.runs.count; IOLayoutHost.ctx.runs.count = () => 3; IOLayoutHost.update(); });
    const found = await page.evaluate(() => {
      const badge = document.querySelector('.l1-ribbon [data-l1="runs"] .badge');
      const rail = document.querySelector('.l1-ribbon').getBoundingClientRect();
      const button = badge.closest('button').getBoundingClientRect();
      const r = badge.getBoundingClientRect();
      return {
        text: badge.textContent, shown: !badge.hidden && r.width > 0 && r.height > 0 && getComputedStyle(badge).display !== 'none',
        inButton: r.left >= button.left - 0.5 && r.right <= button.right + 0.5 && r.top >= button.top - 0.5 && r.bottom <= button.bottom + 0.5,
        inRail: r.left >= rail.left - 0.5 && r.right <= rail.right + 0.5,
      };
    });
    check(`sidebar ${tag}: the Runs badge is visible, inside its button and the rail`, found.shown && found.inButton && found.inRail && found.text === '3', found);
    await page.evaluate(() => { IOLayoutHost.ctx.runs.count = window.lxRunsCount; IOLayoutHost.update(); });
  };

  // collapsed: today's 44px icon rail, the default
  check('sidebar: the default is collapsed, 44px', (await mode()) === 'collapsed' && Math.abs((await railWidth()) - 44) <= 0.5, { mode: await mode(), width: await railWidth() });
  let g = await geometry();
  check('sidebar collapsed: no label is drawn', g.rows.length > 0 && g.rows.every((row) => !row.shown), g.rows.map((row) => row.text));
  check('sidebar: Settings > Sidebar offers the three styles', await page.evaluate(() => [...document.querySelectorAll('#sidebar-style option')].map((o) => o.textContent).join('|')) === 'Icons only|Icon + label (Figma)|Wide list (Shopify)');
  await badges('collapsed');
  await settle();
  await page.screenshot({ path: path.join(SIDEBAR_SHOTS, 'collapsed.png') });

  // expanded (Figma)
  await choose('expanded');
  check('sidebar: picking Icon + label (Figma) in Settings switches the rail', (await mode()) === 'expanded' && await page.evaluate((key) => localStorage.getItem(key), SIDEBAR_KEY) === 'expanded');
  check('sidebar figma: the rail is 72px', Math.abs((await railWidth()) - 72) <= 0.5, await railWidth());
  g = await geometry();
  check('sidebar figma: every ribbon button draws its label', g.rows.length >= 7 && g.rows.every((row) => row.shown), g.rows.map((row) => row.text));
  const figmaDx = worst(g.rows.map((row) => Math.abs(row.label.cx - row.icon.cx)));
  check(`sidebar figma: each label is centred under its icon (worst ${figmaDx.toFixed(2)}px)`, figmaDx <= 1);
  check('sidebar figma: each label sits below its icon, inside its button', g.rows.every((row) => row.label.top >= row.icon.bottom - 1 && row.label.bottom <= row.button.bottom + 0.5));
  check('sidebar figma: no label overflows the rail or its two lines', g.rows.every((row) => !row.clipped && row.label.left >= g.rail.left - 0.5 && row.label.right <= g.rail.right + 0.5));
  check('sidebar figma: every button keeps its full name in its title', g.rows.every((row) => row.title));
  // a long name wraps at word and camelCase seams, never mid-word, and stays inside the rail
  const wrapped = await page.evaluate(() => {
    const rail = document.querySelector('.l1-ribbon');
    const out = [];
    for (const name of ['ClaudeParty', 'RobotDxDashboard']) {
      const button = document.querySelector('.l1-ribbon [data-l1="table"]').cloneNode(true);
      const label = button.querySelector('.l1-label');
      label.innerHTML = IOLayouts.l1.labelHtml(name, IOLayoutHost.ctx.esc);
      rail.insertBefore(button, rail.querySelector('.gap'));
      const lines = [];
      const chars = [];
      const walker = document.createTreeWalker(label, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        for (let index = 0; index < node.length; index += 1) {
          const range = document.createRange();
          range.setStart(node, index);
          range.setEnd(node, index + 1);
          chars.push([Math.round(range.getBoundingClientRect().top), node.data[index]]);
        }
      }
      chars.forEach(([top, char], index) => { if (index === 0 || top !== chars[index - 1][0]) lines.push(''); lines[lines.length - 1] += char; });
      const r = label.getBoundingClientRect();
      const bounds = rail.getBoundingClientRect();
      out.push({ name, lines: lines.map((line) => line.trim()), inside: r.left >= bounds.left && r.right <= bounds.right, clipped: label.scrollHeight > label.clientHeight + 1 });
      button.remove();
    }
    return out;
  });
  const midWord = (lines) => lines.slice(1).some((line, index) => /[a-z]$/.test(lines[index]) && /^[a-z]/.test(line));
  check(`sidebar figma: long names wrap at word / camelCase seams, two lines at most, inside the rail (${wrapped.map((w) => w.lines.join(' / ')).join(' ; ')})`,
    wrapped.every((w) => w.lines.length <= 2 && !midWord(w.lines) && w.inside && !w.clipped) && wrapped[0].lines.join('/') === 'Claude/Party', wrapped);
  await badges('figma');
  await settle();
  await page.screenshot({ path: path.join(SIDEBAR_SHOTS, 'figma.png') });

  // wide (Shopify)
  await choose('wide');
  check('sidebar: picking Wide list (Shopify) in Settings switches the rail', (await mode()) === 'wide');
  const wideWidth = await railWidth();
  check(`sidebar wide: the rail is 238-242px (${wideWidth})`, wideWidth >= 238 && wideWidth <= 242);
  g = await geometry();
  const names = g.rows.map((row) => row.text);
  check(`sidebar wide: Devices, Table, Activity, Runs and Settings are rows (${names.join(', ')})`, ['Devices', 'Table', 'Activity', 'Runs', 'Settings'].every((name) => names.includes(name)));
  check(`sidebar wide: every label is right of its icon (min gap ${Math.min(...g.rows.map((row) => row.label.left - row.icon.right)).toFixed(1)}px)`, g.rows.every((row) => row.shown && row.label.left - row.icon.right > 0));
  const wideDy = worst(g.rows.map((row) => Math.abs(row.label.cy - row.icon.cy)));
  check(`sidebar wide: each label shares its icon's centre line (worst ${wideDy.toFixed(2)}px)`, wideDy <= 0.5);
  check('sidebar wide: no label overflows the rail', g.rows.every((row) => !row.clipped && row.label.right <= g.rail.right + 0.5 && row.label.left >= g.rail.left));
  const settings = g.rows.find((row) => row.text === 'Settings');
  check('sidebar wide: Settings is the foot of the list', settings.button.top > Math.max(...g.rows.filter((row) => row !== settings).map((row) => row.button.top)) && g.rail.bottom - settings.button.bottom <= 16, { settings: settings.button, railBottom: g.rail.bottom });
  const head = await page.evaluate(() => {
    const title = document.querySelector('.l1-rail-title');
    const collapse = document.querySelector('.l1-rail-head .lx-ib').getBoundingClientRect();
    return { text: title.textContent, titleRight: title.getBoundingClientRect().right, collapseLeft: collapse.left, collapseWidth: collapse.width };
  });
  check(`sidebar wide: the logo row shows "${head.text}" with the collapse button right of it`, head.text.length > 0 && head.collapseWidth > 0 && head.collapseLeft >= head.titleRight - 0.5, head);
  await badges('wide');
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.waitForTimeout(300);
  const scroll = await noScroll(page);
  check('sidebar wide: no horizontal scroll at 1280x720', scroll.doc <= 0 && scroll.body <= 0 && scroll.root <= 0, scroll);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(300);
  await settle();
  await page.screenshot({ path: path.join(SIDEBAR_SHOTS, 'wide.png') });

  // the choice survives a reload
  await page.reload();
  await ready(page);
  check('sidebar: the wide list survives a reload (rail 240px, Settings shows it)', (await mode()) === 'wide' && Math.abs((await railWidth()) - 240) <= 2
    && await page.evaluate(() => document.getElementById('sidebar-style').value) === 'wide', { mode: await mode(), width: await railWidth() });

  // the toggle: collapse button, Ctrl+Alt+R, the rail's edge; each returns to the LAST open style
  await page.click('.l1-rail-head .lx-ib');
  await page.waitForTimeout(250);
  check('sidebar toggle: the collapse button collapses (44px)', (await mode()) === 'collapsed' && Math.abs((await railWidth()) - 44) <= 0.5);
  await page.keyboard.press('Control+Alt+r');
  await page.waitForTimeout(250);
  check('sidebar toggle: Ctrl+Alt+R re-opens the WIDE list, not Figma', (await mode()) === 'wide' && Math.abs((await railWidth()) - 240) <= 2, await mode());
  await choose('expanded');
  await page.click('.l1-rail-edge');
  await page.waitForTimeout(250);
  check('sidebar toggle: the rail edge collapses', (await mode()) === 'collapsed');
  await page.click('.l1-rail-edge');
  await page.waitForTimeout(250);
  check('sidebar toggle: the rail edge re-opens Figma, the style last open', (await mode()) === 'expanded' && Math.abs((await railWidth()) - 72) <= 0.5, await mode());
  await choose('wide');
  await page.keyboard.press('Control+Alt+r');
  await page.waitForTimeout(250);
  await page.reload();
  await ready(page);
  check('sidebar toggle: collapsed survives a reload', (await mode()) === 'collapsed' && Math.abs((await railWidth()) - 44) <= 0.5, await mode());
  await page.keyboard.press('Control+Alt+r');
  await page.waitForTimeout(250);
  check('sidebar toggle: the last open style (wide) survives a reload while collapsed', (await mode()) === 'wide', await mode());
  await page.evaluate((key) => localStorage.setItem(key, 'sideways'), SIDEBAR_KEY);
  await page.reload();
  await ready(page);
  check('sidebar: an unknown stored value falls back to collapsed', (await mode()) === 'collapsed');

  // another layout does not carry the row, and L1 keeps the choice when it comes back
  await choose('expanded');
  await page.selectOption('#layout-pick', 'l4');
  await page.waitForTimeout(500);
  check('sidebar: the Sidebar row belongs to L1 (hidden in L4)', await page.evaluate(() => document.getElementById('sidebar-switch').hidden));
  await page.selectOption('#layout-pick', 'l1');
  await page.waitForTimeout(500);
  check('sidebar: back in L1 the chosen style is still on', (await mode()) === 'expanded' && await page.evaluate(() => !document.getElementById('sidebar-switch').hidden));
  await context.close();
}

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'layouts-'));
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const server = await startServer(stateDir);
  const base = server.base;
  const errors = [];
  try {
    const plugins = await (await fetch(`${base}/api/v1/plugins`)).json();
    const names = plugins.plugins.map((plugin) => plugin.name);
    check('only the fixture plugin is loaded', !names.some((name) => /moteus|rgbd|obsbot|computer|check_root/.test(name)), names);
    if (names.some((name) => /moteus|rgbd|obsbot/.test(name))) throw new Error('refusing to go on: a hardware plugin is loaded');

    // ---- static: the layouts' CSS ----
    const sources = ['host.js', ...LAYOUTS.map((id) => `${id}.js`)].map((file) => [file, fs.readFileSync(path.join(ROOT, 'io_diagnostics', 'web', 'layouts', file), 'utf8')]);
    const cssOf = (text) => [...text.matchAll(/(?:const (?:BASE_)?CSS = `)([\s\S]*?)`;/g)].map((match) => match[1]).join('\n');
    const literal = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/;
    const status = /--io-(warn|error|stale|unknown)/;
    for (const [file, text] of sources) {
      const css = cssOf(text);
      check(`${file}: CSS exists`, css.length > 100);
      check(`${file}: no colour literal in its CSS`, !literal.test(css), (css.match(literal) || [''])[0]);
      check(`${file}: its CSS paints no status token`, !status.test(css), (css.match(status) || [''])[0]);
    }

    // ---- the picker and the five layouts ----
    for (const id of RUN) {
      const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: 'light' });
      const page = await context.newPage();
      page.on('pageerror', (error) => errors.push(`${id}: ${error}`));
      page.on('console', (message) => { if (message.type() === 'error') errors.push(`${id}: console ${message.text()}`); });
      await open(page, base, id);
      const tag = id.toUpperCase();
      check(`${tag} mounts`, await page.evaluate((layout) => IOLayoutHost.id === layout && document.getElementById('lx-root').classList.contains(`lx-${layout}`), id));

      // picker
      const options = await page.evaluate(() => [...document.querySelectorAll('#layout-pick option')].map((option) => option.value));
      if (id === RUN[0]) check('picker lists L1..L5 (and L0 Today) in the bottom-right controls', options.join() === ['l0', ...LAYOUTS].join() && await page.evaluate(() => Boolean(document.querySelector('.statusbar .controls #layout-pick'))), options);
      check(`${tag} picker shows the layout, the pick is stored`, await page.evaluate(([layout, key]) => document.getElementById('layout-pick').value === layout && localStorage.getItem(key) === layout, [id, PICK]));

      // search inside the tree pane, above the first row (the tree pane is the place of every layout's search)
      await ensureTree(page, id);
      const pane = await rectOf(page, '[data-lx-tree]');
      const search = await rectOf(page, '#search');
      const firstRow = await rectOf(page, '#tree-host .t6-row');
      const inside = Boolean(pane && search && search.x >= pane.x - 0.5 && search.right <= pane.right + 0.5 && search.y >= pane.y - 0.5 && search.bottom <= pane.bottom + 0.5);
      check(`${tag} the search sits inside the tree pane, above its first row`, inside && firstRow && search.bottom <= firstRow.y + 0.5, { pane, search, firstRow: firstRow && firstRow.y });
      check(`${tag} the search is the page's own input (one #search, one #tree-host)`, await page.evaluate(() => document.querySelectorAll('#search').length === 1
        && document.querySelector('[data-lx-tree]').contains(document.getElementById('search')) && document.querySelector('[data-lx-tree]').contains(document.getElementById('tree-host'))));

      // View options stays reachable, out of the way: its button is on screen and its popover opens inside the window
      await page.click('#vo-btn');
      await page.waitForTimeout(250);
      const popover = await rectOf(page, '#view-options');
      check(`${tag} View options is reachable and opens on screen`, Boolean(popover) && popover.x >= 0 && popover.right <= 1400 && popover.y >= 0 && popover.bottom <= 900, popover);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(150);

      // filter + open a board
      const rowsBefore = await page.locator('#tree-host .t6-row').count();
      await page.fill('#search', '272813');
      await page.waitForTimeout(500);
      const rowsAfter = await page.locator('#tree-host .t6-row').count();
      check(`${tag} typing filters the tree`, rowsAfter > 0 && rowsAfter < rowsBefore, { before: rowsBefore, after: rowsAfter });
      await page.locator('#tree-host .t6-row').filter({ hasText: 'fdcanusb' }).first().click();
      await page.waitForTimeout(700);
      check(`${tag} the board opens and its page is drawn`, /272813EF/.test(await page.locator('.d-title').first().innerText()) && await visible(page, '.d-title'));
      await page.fill('#search', '');
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(SHOTS, `${id}-check.png`) });

      // every tree style still draws in the layout's tree pane (the styles are the page's: v1-v6; v5 has no search of its own)
      const styles = await page.evaluate(() => [...document.querySelectorAll('#tree-variant option')].map((option) => option.value));
      const drawn = [];
      for (const style of styles) {
        await page.evaluate((value) => { const select = document.getElementById('tree-variant'); select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); }, style);
        await page.waitForTimeout(350);
        await ensureTree(page, id);
        drawn.push(`${style}:${await page.evaluate(() => document.querySelectorAll('.tree-pane:not([hidden]) [role=treeitem], .tree-pane:not([hidden]) .v5-row').length)}`);
      }
      check(`${tag} every tree style draws in the tree pane`, styles.length >= 6 && drawn.every((entry) => Number(entry.split(':')[1]) > 3), drawn);
      await page.evaluate(() => { const select = document.getElementById('tree-variant'); select.value = 'v6'; select.dispatchEvent(new Event('change', { bubbles: true })); });
      await page.waitForTimeout(350);

      // every view reachable from the layout's own controls
      for (const view of ['table', 'activity', 'runs', 'tree']) {
        const spec = await reach(page, id, view);
        check(`${tag} view ${view}: reachable, on screen`, await visible(page, spec.shows), spec.shows);
        if (view === 'runs') await page.evaluate(() => IOLayoutHost.ctx.runs.set(false));
      }

      // panes: the tree pane's boundary drags, is remembered, resets, folds
      await ensureTree(page, id);
      const handles = await page.evaluate((layout) => Object.entries(IOLayouts[layout].s).filter(([, value]) => value && value.el && value.el.classList && value.el.classList.contains('lx-sash'))
        .map(([name, value]) => ({ name, cls: [...value.el.classList].find((c) => /^lx-sash-[a-z0-9]+$/.test(c) && c !== 'lx-sash-x' && c !== 'lx-sash-y') })), id);
      const side = handles.find((handle) => handle.name === 'sideSash');
      check(`${tag} has a draggable tree-pane boundary`, Boolean(side), handles.map((handle) => handle.name));
      if (side) {
        const key = side.cls.replace('lx-sash-', '');
        const sash = await rectOf(page, `.${side.cls}`);
        const before = (await rectOf(page, '[data-lx-tree]')).w;
        await page.mouse.move(sash.x + sash.w / 2, sash.y + 200);
        await page.mouse.down();
        await page.mouse.move(sash.x + sash.w / 2 + 60, sash.y + 200, { steps: 8 });
        await page.mouse.up();
        await page.waitForTimeout(250);
        const after = (await rectOf(page, '[data-lx-tree]')).w;
        const stored = Number(await page.evaluate((k) => localStorage.getItem(`io-diagnostics.lx.${k}`), key));
        check(`${tag} dragging the boundary resizes the pane and stores it`, after - before >= 40 && Math.abs(stored - after) <= 2, { before, after, stored });
        await page.reload();
        await ready(page);
        await ensureTree(page, id);
        const reloaded = (await rectOf(page, '[data-lx-tree]')).w;
        check(`${tag} the size is remembered across a reload`, Math.abs(reloaded - after) <= 2, { after, reloaded });
        const sash2 = await rectOf(page, `.${side.cls}`);
        await page.mouse.dblclick(sash2.x + sash2.w / 2, sash2.y + 200);
        await page.waitForTimeout(250);
        const reset = (await rectOf(page, '[data-lx-tree]')).w;
        check(`${tag} double-click resets it`, Math.abs(reset - before) <= 2 && !(await page.evaluate((k) => localStorage.getItem(`io-diagnostics.lx.${k}`), key)), { before, reset });
        const mainBefore = (await rectOf(page, '#main')).w;
        await page.evaluate(([layout, name]) => IOLayouts[layout].s[name].fold(true), [id, side.name]);
        await page.waitForTimeout(250);
        const folded = !(await visible(page, '[data-lx-tree]'));
        const mainFolded = (await rectOf(page, '#main')).w;
        await page.evaluate(([layout, name]) => IOLayouts[layout].s[name].fold(false), [id, side.name]);
        await page.waitForTimeout(250);
        check(`${tag} the pane folds away (the page gets its width) and comes back`, folded && mainFolded > mainBefore + 100 && await visible(page, '[data-lx-tree]'), { mainBefore, mainFolded });
      }
      for (const handle of handles.filter((item) => item !== side)) {
        const key = handle.cls.replace('lx-sash-', '');
        const folded = await page.evaluate(([layout, name]) => IOLayouts[layout].s[name].folded(), [id, handle.name]);
        if (folded) await page.evaluate(([layout, name]) => IOLayouts[layout].s[name].fold(false), [id, handle.name]);
        if (!(await visible(page, `.${handle.cls}`))) continue; // a tab's own boundary (L5 needs-you) is drawn in its tab only
        const horizontal = await page.evaluate((cls) => document.querySelector(`.${cls}`).classList.contains('lx-sash-x'), handle.cls);
        const read = () => page.evaluate(([layout, name]) => IOLayouts[layout].s[name].size(), [id, handle.name]);
        const was = await read();
        await page.focus(`.${handle.cls}`);
        // the pane grows toward the side its edge is anchored to: try both arrows, one of them grows it
        const keys = horizontal ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown'];
        await page.keyboard.press(keys[0]);
        const first = await read();
        await page.keyboard.press(keys[1]); await page.keyboard.press(keys[1]);
        const second = await read();
        const stored = Number(await page.evaluate((k) => localStorage.getItem(`io-diagnostics.lx.${k}`), key));
        check(`${tag} boundary ${handle.name} moves by the arrow keys and is remembered`, first !== was && second !== first && stored === second, { was, first, second, stored });
        await page.evaluate(([layout, name]) => IOLayouts[layout].s[name].fold(true), [id, handle.name]);
        await page.evaluate(([layout, name, wasFolded]) => IOLayouts[layout].s[name].fold(wasFolded), [id, handle.name, folded]);
      }

      // no horizontal scroll, in the Tree and in the Table (the table scrolls inside its own pane)
      for (const [width, height] of [[1400, 900], [1280, 720]]) {
        await page.setViewportSize({ width, height });
        await page.waitForTimeout(400);
        for (const view of ['tree', 'table']) {
          if (view === 'tree') await ensureTree(page, id); else await reach(page, id, view);
          const scroll = await noScroll(page);
          check(`${tag} no horizontal scroll at ${width}x${height} (${view})`, scroll.doc <= 0 && scroll.body <= 0 && scroll.root <= 0, scroll);
        }
      }
      // what makes this proposal what it is
      await BEHAVIOURS[id](page, tag);
      await context.close();
    }

    // ---- switching live through all five and back leaves nothing behind ----
    {
      const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: 'light' });
      const page = await context.newPage();
      page.on('pageerror', (error) => errors.push(`switch: ${error}`));
      page.on('console', (message) => { if (message.type() === 'error') errors.push(`switch: console ${message.text()}`); });
      await open(page, base, 'l1');
      for (const id of ['l0', ...LAYOUTS, 'l0', 'l1', 'l4', 'l1']) {
        await page.selectOption('#layout-pick', id);
        await page.waitForTimeout(500);
        const state = await page.evaluate((layout) => ({
          id: IOLayoutHost.id === layout,
          roots: document.querySelectorAll('#lx-root').length,
          search: document.querySelectorAll('#search').length,
          styles: document.querySelectorAll('style[data-lx-css]').length,
          overlays: document.querySelectorAll('.lx-pal-back').length,
          stored: localStorage.getItem('io-diagnostics.page-layout') === layout,
          drawn: Boolean(document.querySelector('.d-title')) && document.querySelectorAll('#tree-host .t6-row').length > 3,
          bar: document.querySelectorAll('.statusbar .l2-sb').length,
          home: Boolean(document.querySelector('.toolbar #search') && document.querySelector('.toolbar #views') && document.querySelector('.window > .main, .window > #main') && document.querySelector('#main > #left #tree-host')),
        }), id);
        const classic = id === 'l0';
        const palette = id === 'l1' || id === 'l4'; // the shared Ctrl-K palette (layouts/palette.js): one more style, one overlay
        check(`switch to ${id.toUpperCase()}: ${classic ? 'the page as it was, every part home' : 'one root, one search, no leftover'}`, state.id && state.roots === (classic ? 0 : 1) && state.search === 1 && state.styles === (classic ? 0 : palette ? 2 : 1)
          && state.stored && state.drawn && state.overlays === (palette ? 1 : 0) && state.bar === (id === 'l2' ? 1 : 0) && state.home === classic, state);
      }
      await context.close();
    }
    if (RUN.includes('l1')) await sidebarStyles(browser, base, errors);
    check('no page errors, no console errors', errors.length === 0, errors.slice(0, 5));
  } finally {
    await browser.close();
    stop(server.child);
  }
  console.log(`\n${passed}/${passed + failed} checks passed · screenshots in ${SHOTS}`);
  process.exit(failed ? 1 : 0);
}
main().catch((error) => { console.error(error); process.exit(1); });
