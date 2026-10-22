#!/usr/bin/env node
// Acceptance for the tree switcher: the five tree proposals (web/tree/v1..v5.js) inside the REAL dashboard
// (web/index.html + app.js), at the 18:11 (F1) and 18:25 (F2) fixture frames, in headless Chrome.
//
// Runner note: this is a Node + Playwright script, not a pytest test (pytest does not collect .js). Run it with
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js
//
// It starts its own fixture servers (`cli.py serve --fixture F1|F2 --port 0`, a free port each, stopped by their
// process group at the end), drives headless Chrome (/usr/bin/google-chrome; never a visible window), prints one
// line per check, writes reports/media/v1/tree-variants-report.json plus screenshots
// reports/media/v1/tree-<variant>-<1811|1825>.png (+ -hover.png crops), and exits 1 on any failed check.
// Nothing here opens a CAN adapter or a camera: the fixture host replays recorded frames.
//
// Checks per variant and frame (Zach's tree rules, 2026-10-22):
//   switch      the bottom-right drop-down switches the Tree view live and a reload keeps the choice
//   no-green    no green computed colour anywhere in the tree; no status mark on a row that shows OK / nothing
//   shapes      every visible row whose OWN report is a problem carries a visible shape element
//   right       that shape sits right of the row's name (getBoundingClientRect x order)
//   chevrons    no chevron visible at rest on unselected rows; hovering a parent row shows its chevron
//   greyed      a not-connected row's name is grey (low saturation) and dimmer than connected rows' names
//   select      clicking a row updates the detail panel (title + path) and the renderer marks it selected
//   search      the search box filters the tree (v5 has none: the box is disabled and says so)
//   tabs        Table and Activity still render, and Tree comes back on the same proposal
//   errors      no page errors or console errors
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'v1');
const FRAMES = [{ fixture: 'F1', label: '1811' }, { fixture: 'F2', label: '1825' }];
const VARIANTS = ['v1', 'v2', 'v3', 'v4', 'v5'];
const VIEWPORT = { width: 1600, height: 1000 };
const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
// --mutate proves the ruler can fail: it paints every name green and pins every chevron visible, so `no-green` and
// `chevrons` must FAIL for every variant (no screenshots or report are written in that mode).
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js --mutate
const MUTATE = process.argv.includes('--mutate');
const MUTATION_CSS = '#tree-host [class*="-name"] { color: #5cb85c !important; }'
  + ' #tree-host .t1-chev, #tree-host .v3-chev, #tree-host .v4-chev, #tree-host .v5-chev { opacity: 1 !important; }'
  + ' #tree-host .t2-chev { display: block !important; }';

// What each proposal calls its parts. `own` is the shape of the row's OWN self-report (not the advisory hint).
const SPEC = {
  v1: { row: '.t1-row[data-t1-path]', attr: 'data-t1-path', name: '.t1-name', own: '.t1-st > .t1-mark > .t1-shape', chev: '.t1-chev:not(.t1-leaf)' },
  v2: { row: '.t2-row[data-t2-path]', attr: 'data-t2-path', name: '.t2-name', own: '.t2-status .t2-shape', chev: '.t2-chev' },
  v3: { row: '.v3-row[data-v3-path]', attr: 'data-v3-path', name: '.v3-name', own: '.v3-status > .v3-shape', chev: '.v3-chev' },
  v4: { row: '.v4-row[data-path]', attr: 'data-path', name: '.v4-name', own: '.v4-status > .v4-glyph-box > .v4-self, .v4-status > .v4-glyph-box > .v4-knock', chev: '.v4-chev' },
  v5: { row: '.v5-row[data-v5-path]', attr: 'data-v5-path', name: '.v5-name', own: '.v5-c-status > .v5-shape', chev: '.v5-chev' },
};

// ------------------------------------------------------------------------------------------ servers ----
function startServer(fixture) {
  const env = { ...process.env };
  delete env.VIRTUAL_ENV; // uv run picks the project's own environment
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--fixture', fixture, '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stopServer(child); reject(new Error(`${fixture} server did not start: ${output}`)); }, 60000);
    const onData = (chunk) => {
      output += chunk;
      const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//);
      if (found) { clearTimeout(timer); resolve({ child, base: found[1] }); }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`${fixture} server exited (${code}): ${output}`)); });
  });
}

function stopServer(child) {
  // WHY the process group: `uv run` starts python as a child; the group is exactly what this script started
  try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* already gone */ }
}

// --------------------------------------------------------------------------------------- in the page ----
// Runs inside the page: facts about the visible rows of the active proposal, measured, never assumed.
function measureRows({ spec, nodes }) {
  const pane = document.querySelector('#tree-host > .tree-pane:not([hidden])');
  const parse = (text) => {
    const m = String(text || '').match(/rgba?\(([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+%?))?\)/);
    if (!m) return null;
    let a = m[4] === undefined ? 1 : parseFloat(m[4]);
    if (String(m[4]).endsWith('%')) a /= 100;
    return { r: +m[1], g: +m[2], b: +m[3], a };
  };
  const hsl = ({ r, g, b }) => {
    const [R, G, B] = [r / 255, g / 255, b / 255];
    const max = Math.max(R, G, B);
    const min = Math.min(R, G, B);
    const l = (max + min) / 2;
    if (max === min) return { h: 0, s: 0, l };
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    let h = max === R ? (G - B) / d + (G < B ? 6 : 0) : max === G ? (B - R) / d + 2 : (R - G) / d + 4;
    return { h: h * 60, s, l };
  };
  // "green" = hue 75-165°, clearly saturated, neither black nor white, and not see-through
  const isGreen = (c) => { if (!c || c.a < 0.1) return false; const { h, s, l } = hsl(c); return h >= 75 && h <= 165 && s >= 0.25 && l >= 0.12 && l <= 0.9; };
  const luminance = (c) => (c ? 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b : null);
  const effectiveOpacity = (element) => {
    let value = 1;
    for (let e = element; e && e !== pane.parentElement; e = e.parentElement) value *= parseFloat(getComputedStyle(e).opacity);
    return value;
  };
  const shown = (element) => {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none' && effectiveOpacity(element) > 0.05;
  };

  // 1. no green anywhere in the active pane (text colour only where there is text; fill/stroke on svg parts)
  const greens = [];
  for (const element of pane.querySelectorAll('*')) {
    if (!shown(element)) continue;
    const style = getComputedStyle(element);
    const samples = [['background-color', style.backgroundColor]];
    const hasText = [...element.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (hasText) samples.push(['color', style.color]);
    if (element instanceof SVGElement) samples.push(['fill', style.fill], ['stroke', style.stroke]);
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      if (parseFloat(style[`border${side}Width`]) > 0) samples.push([`border-${side.toLowerCase()}`, style[`border${side}Color`]]);
    }
    for (const [prop, value] of samples) {
      if (isGreen(parse(value))) greens.push({ prop, value, tag: element.tagName.toLowerCase(), cls: String(element.className && element.className.baseVal !== undefined ? element.className.baseVal : element.className) });
    }
  }

  // 2. per row: what it is (from the API), what it shows (measured)
  const rows = [];
  for (const row of pane.querySelectorAll(spec.row)) {
    const rowPath = row.getAttribute(spec.attr);
    const node = nodes[rowPath];
    if (!node) continue;
    const name = row.querySelector(spec.name);
    const nameRect = name ? name.getBoundingClientRect() : null;
    const marks = [...row.querySelectorAll(spec.own)].filter(shown).map((mark) => {
      const rect = mark.getBoundingClientRect();
      return { left: rect.left, right: rect.right, color: getComputedStyle(mark).color };
    });
    const nameColor = name ? parse(getComputedStyle(name).color) : null;
    const chev = row.querySelector(spec.chev);
    rows.push({
      path: rowPath,
      name: node.name,
      own: node.own,
      level: node.level,
      connected: node.connected,
      knock: node.knock_on_of,
      selected: row.getAttribute('aria-selected') === 'true',
      nameRight: nameRect ? nameRect.right : null,
      marks,
      nameColor: name ? getComputedStyle(name).color : null,
      nameSaturation: nameColor ? hsl(nameColor).s : null,
      nameLuminance: luminance(nameColor),
      chevShown: chev ? shown(chev) : null,
      chevOpacity: chev ? effectiveOpacity(chev) : null,
      chevDisplay: chev ? getComputedStyle(chev).display : null,
    });
  }
  return { greens, rows, scroll: document.getElementById('left').scrollTop };
}

// ------------------------------------------------------------------------------------------- checks ----
const results = [];
function check(frame, variant, name, ok, detail) {
  results.push({ frame, variant, check: name, ok: Boolean(ok), detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${frame} ${variant}  ${name.padEnd(9)}  ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

async function waitForTree(page, spec) {
  await page.waitForFunction((selector) => {
    const pane = document.querySelector('#tree-host > .tree-pane:not([hidden])');
    return pane && pane.querySelectorAll(selector).length > 0 && document.querySelector('.d-title');
  }, spec.row, { timeout: 15000 });
}

async function nodeIndex(page) {
  return page.evaluate(async () => {
    const state = await (await fetch('/api/v1/state')).json();
    const nodes = {};
    (function visit(node) { nodes[node.path] = { ...node, children: node.children.map((c) => c.path) }; node.children.forEach(visit); })(state.tree);
    return { nodes, sel: state.summary.attention.length ? state.summary.attention[0].path : state.tree.path };
  });
}

async function runVariant(browser, base, frame, variant) {
  const spec = SPEC[variant];
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 });
  const page = await context.newPage();
  if (MUTATE) {
    await page.addInitScript((css) => document.addEventListener('DOMContentLoaded', () => {
      const style = document.createElement('style');
      style.textContent = css;
      document.head.appendChild(style);
    }), MUTATION_CSS);
  }
  const shoot = (options) => (MUTATE ? Promise.resolve() : page.screenshot(options));
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  try {
    // ---- switch live from the default, then reload: the choice must stick
    await page.goto(`${base}/`);
    await page.waitForFunction(() => document.querySelectorAll('#tree-variant option').length >= 5 && document.querySelector('.d-title'));
    const firstLoad = await page.evaluate(() => document.getElementById('tree-variant').value);
    await page.selectOption('#tree-variant', variant);
    await waitForTree(page, spec);
    const live = await page.evaluate(() => document.querySelector('#tree-host > .tree-pane:not([hidden])').dataset.variant);
    await page.reload();
    await waitForTree(page, spec);
    await page.waitForTimeout(1500); // two polls: renderers settle, v5's lazy rate/firmware cells arrive
    const kept = await page.evaluate(() => ({
      picker: document.getElementById('tree-variant').value,
      pane: document.querySelector('#tree-host > .tree-pane:not([hidden])').dataset.variant,
      panes: document.querySelectorAll('#tree-host > .tree-pane:not([hidden])').length,
      stored: (() => { try { return localStorage.getItem('io-diagnostics.tree-variant'); } catch (_) { return null; } })(),
      corner: (() => { const r = document.getElementById('variant-switch').getBoundingClientRect(); return { right: Math.round(innerWidth - r.right), bottom: Math.round(innerHeight - r.bottom) }; })(),
    }));
    check(frame, variant, 'switch', live === variant && kept.picker === variant && kept.pane === variant && kept.panes === 1 && kept.stored === variant
      && kept.corner.right < 40 && kept.corner.bottom < 40,
    `first load ${firstLoad} · live ${live} · after reload picker ${kept.picker}, pane ${kept.pane}, stored ${kept.stored} · switch ${kept.corner.right}px from right, ${kept.corner.bottom}px from bottom`);

    // ---- park the pointer outside the tree, measure the resting state, shoot it
    await page.mouse.move(1560, 960);
    await page.waitForTimeout(400);
    const { nodes } = await nodeIndex(page);
    const rest = await page.evaluate(measureRows, { spec, nodes });
    await shoot({ path: path.join(SHOTS, `tree-${variant}-${frame}.png`) });

    // no green; no own mark on rows that show OK / nothing
    const quietRows = rest.rows.filter((r) => r.level === 'ok' || r.level === null);
    const marked = quietRows.filter((r) => r.marks.length);
    check(frame, variant, 'no-green', rest.greens.length === 0 && marked.length === 0,
      `${rest.greens.length} green paints${rest.greens.length ? ` (${rest.greens.slice(0, 3).map((g) => `${g.tag}.${g.cls} ${g.prop} ${g.value}`).join('; ')})` : ''} · `
      + `${quietRows.length} OK rows, status-mark colours sampled: ${marked.length ? marked.map((r) => `${r.name}: ${r.marks.map((m) => m.color).join(',')}`).join('; ') : 'none rendered'}`);

    // problem rows carry a shape, right of the name
    const problems = rest.rows.filter((r) => r.own && PROBLEM.has(r.own.level));
    const shapeless = problems.filter((r) => !r.marks.length);
    check(frame, variant, 'shapes', problems.length > 0 && shapeless.length === 0,
      `${problems.length - shapeless.length}/${problems.length} problem rows carry a shape${shapeless.length ? ` · missing: ${shapeless.map((r) => r.name).join(', ')}` : ''}`);
    const leftOfName = problems.filter((r) => r.marks.length && r.marks.some((m) => m.left < r.nameRight - 0.5));
    check(frame, variant, 'right', problems.length > 0 && leftOfName.length === 0,
      `${problems.filter((r) => r.marks.length).map((r) => `${r.name}: name ends ${Math.round(r.nameRight)} < mark ${Math.round(r.marks[0].left)}`).join(' · ')}`
      + `${leftOfName.length ? ` · LEFT of name: ${leftOfName.map((r) => r.name).join(', ')}` : ''}`);

    // chevrons: none visible at rest on unselected rows; hovering a parent row shows its own
    const restingChevrons = rest.rows.filter((r) => r.chevShown && !r.selected);
    const candidates = rest.rows.filter((r) => r.chevOpacity !== null && !r.selected && nodes[r.path].children.length && nodes[r.path].depth > 0);
    let hover = { name: null };
    if (candidates.length) {
      const hoverTarget = candidates.find((r) => /CAN 14|fdcanusb|D405/.test(r.name)) || candidates[0];
      const hoverRow = rowSelectorFor(spec, hoverTarget.path);
      await page.hover(`${hoverRow} ${spec.name}`);
      await page.waitForTimeout(400); // the opacity transitions are 80-150 ms
      const hovered = await page.evaluate(measureRows, { spec, nodes });
      const after = hovered.rows.find((r) => r.path === hoverTarget.path);
      hover = { name: hoverTarget.name, shownBefore: hoverTarget.chevShown, before: hoverTarget.chevOpacity, beforeDisplay: hoverTarget.chevDisplay,
        after: after && after.chevOpacity, shown: Boolean(after && after.chevShown) };
      const box = await page.locator(hoverRow).boundingBox();
      if (box) await shoot({ path: path.join(SHOTS, `tree-${variant}-${frame}-hover.png`), clip: { x: 0, y: Math.max(0, box.y - 60), width: Math.min(VIEWPORT.width, box.x + box.width + 4), height: 150 } });
      await page.mouse.move(1560, 960);
      await page.waitForTimeout(300);
    }
    check(frame, variant, 'chevrons', restingChevrons.length === 0 && hover.name && hover.shownBefore === false && hover.shown,
      `at rest ${restingChevrons.length} visible on unselected rows${restingChevrons.length ? ` (${restingChevrons.map((r) => r.name).join(', ')})` : ''} · `
      + `hover "${hover.name}": before opacity ${fmt(hover.before)} (display ${hover.beforeDisplay}) → after ${fmt(hover.after)}, shown ${hover.shown}`);

    // not connected = greyed: grey (low saturation) and dimmer than the connected rows' names
    let greyRows = rest.rows.filter((r) => r.connected === false);
    let reference = rest.rows;
    if (!greyRows.length) {
      // the proposal folded it away: select one from outside (as the chip does) so the renderer reveals it
      const greyTarget = Object.values(nodes).find((n) => n.connected === false);
      if (greyTarget) {
        await page.goto(`${base}/?sel=${encodeURIComponent(greyTarget.path)}`);
        await waitForTree(page, spec);
        await page.waitForTimeout(800);
        const revealed = await page.evaluate(measureRows, { spec, nodes });
        greyRows = revealed.rows.filter((r) => r.connected === false);
        reference = revealed.rows;
      }
    }
    const connectedNames = reference.filter((r) => r.connected !== false && r.nameLuminance !== null && !(r.own && PROBLEM.has(r.own.level)) && !r.selected)
      .map((r) => r.nameLuminance).sort((a, b) => a - b);
    const typical = connectedNames.length ? connectedNames[Math.floor(connectedNames.length / 2)] : null;
    const notGrey = greyRows.filter((r) => !(r.nameSaturation < 0.15 && typical !== null && r.nameLuminance < typical - 20));
    check(frame, variant, 'greyed', greyRows.length > 0 && notGrey.length === 0,
      `${greyRows.map((r) => `${r.name}: ${r.nameColor} (sat ${r.nameSaturation.toFixed(2)}, lum ${Math.round(r.nameLuminance)})`).join(' · ')} vs connected median lum ${Math.round(typical)}`);

    // clicking a row updates the detail panel, and the renderer marks it selected
    const now = await page.evaluate(measureRows, { spec, nodes });
    const current = now.rows.find((r) => r.selected);
    const clickable = now.rows.filter((r) => !r.selected && r.path !== (current && current.path));
    const target = clickable.find((r) => r.own && PROBLEM.has(r.own.level)) || clickable.find((r) => r.name === 'CAN 14') || clickable[0];
    const rowSelector = rowSelectorFor(spec, target.path);
    await page.click(`${rowSelector} ${spec.name}`);
    let selectOk = true;
    try {
      await page.waitForFunction(({ name, nodePath }) => {
        const title = document.querySelector('.d-title span');
        const where = document.querySelector('.d-path');
        return title && title.textContent === name && where && where.textContent.startsWith(nodePath) && document.querySelector('#detail .cards .card');
      }, { name: target.name, nodePath: target.path }, { timeout: 6000 });
    } catch (_) { selectOk = false; }
    const picked = await page.evaluate(({ selector }) => {
      const row = document.querySelector(`#tree-host > .tree-pane:not([hidden]) ${selector}`);
      return { selected: row && row.getAttribute('aria-selected'), title: (document.querySelector('.d-title span') || {}).textContent, cards: document.querySelectorAll('#detail .cards .card').length };
    }, { selector: rowSelector });
    check(frame, variant, 'select', selectOk && picked.selected === 'true',
      `clicked "${target.name}" → detail "${picked.title}" with ${picked.cards} cards · row aria-selected ${picked.selected}`);

    // search
    if (variant === 'v5') {
      const box = await page.evaluate(() => ({ disabled: document.getElementById('search').disabled, placeholder: document.getElementById('search').placeholder }));
      check(frame, variant, 'search', box.disabled && /No search/.test(box.placeholder), `v5 has no search: box disabled, says "${box.placeholder}"`);
    } else {
      const before = (await page.evaluate(measureRows, { spec, nodes })).rows.length;
      await page.fill('#search', 'CAN 14');
      await page.waitForTimeout(500);
      const found = (await page.evaluate(measureRows, { spec, nodes })).rows;
      await page.fill('#search', '');
      await page.waitForTimeout(400);
      check(frame, variant, 'search', found.some((r) => r.name === 'CAN 14') && found.length < before,
        `"CAN 14": ${found.length} rows (${found.map((r) => r.name).join(' › ')}) of ${before}`);
    }

    // the other tabs keep working, and the Tree comes back on the same proposal with the same selection
    await page.click('[data-view="table"]');
    await page.waitForSelector('table.grid tbody tr', { timeout: 5000 });
    const table = await page.evaluate(() => ({ rows: document.querySelectorAll('table.grid tbody tr').length, treeHidden: document.getElementById('tree-host').hidden,
      okPills: [...document.querySelectorAll('table.grid .pill.ok, table.grid .dot.ok')].length }));
    await page.click('[data-view="activity"]');
    await page.waitForSelector('.act .ev', { timeout: 6000 });
    const activity = await page.evaluate(() => ({ events: document.querySelectorAll('.act .ev').length, links: document.querySelectorAll('table.lh tbody tr').length }));
    await page.click('[data-view="tree"]');
    await waitForTree(page, spec);
    const back = await page.evaluate(({ selector }) => ({
      pane: document.querySelector('#tree-host > .tree-pane:not([hidden])').dataset.variant,
      selected: (document.querySelector(`#tree-host > .tree-pane:not([hidden]) ${selector}`) || { getAttribute: () => null }).getAttribute('aria-selected'),
      title: (document.querySelector('.d-title span') || {}).textContent,
    }), { selector: rowSelector });
    check(frame, variant, 'tabs', table.rows > 0 && table.treeHidden && table.okPills === 0 && activity.events > 0 && back.pane === variant && back.title === target.name,
      `table ${table.rows} rows (OK marks ${table.okPills}) · activity ${activity.events} events, ${activity.links} link rows · back on ${back.pane}, detail "${back.title}", row selected ${back.selected}`);

    check(frame, variant, 'errors', errors.length === 0, errors.length ? errors.slice(0, 3).join(' | ') : 'none');
  } catch (error) {
    check(frame, variant, 'crashed', false, String(error && error.stack ? error.stack.split('\n').slice(0, 3).join(' ') : error));
  } finally {
    await context.close();
  }
}

const fmt = (value) => (typeof value === 'number' ? value.toFixed(2) : String(value));
// the row element of one entity, e.g. .t1-row[data-t1-path="host/usb1/port-1-7"]
const rowSelectorFor = (spec, entityPath) => `${spec.row.split('[')[0]}[${spec.attr}="${entityPath.replace(/"/g, '\\"')}"]`;

// ---------------------------------------------------------------------------------------------- main ----
async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const servers = [];
  const stopAll = () => servers.forEach((server) => stopServer(server.child));
  process.on('SIGINT', () => { stopAll(); process.exit(130); });
  try {
    for (const frame of MUTATE ? FRAMES.slice(0, 1) : FRAMES) {
      // one server per frame, started right before its checks so its clock is still at the recorded moment
      const server = await startServer(frame.fixture);
      servers.push(server);
      console.log(`--- ${frame.fixture} (${frame.label}) on ${server.base}${MUTATE ? ' · MUTATED (expect no-green + chevrons to FAIL)' : ''}`);
      for (const variant of VARIANTS) await runVariant(browser, server.base, frame.label, variant);
      stopServer(server.child);
    }
  } finally {
    await browser.close();
    stopAll();
  }
  const failed = results.filter((r) => !r.ok);
  if (MUTATE) {
    // the ruler works when every variant fails both mutated checks
    const caught = VARIANTS.filter((v) => ['no-green', 'chevrons'].every((name) => failed.some((r) => r.variant === v && r.check === name)));
    console.log(`\nmutation caught in ${caught.length}/${VARIANTS.length} variants (${caught.join(', ')})`);
    process.exitCode = caught.length === VARIANTS.length ? 0 : 1;
    return;
  }
  const report = { run: new Date().toISOString(), viewport: VIEWPORT, frames: FRAMES, variants: VARIANTS, passed: results.length - failed.length, failed: failed.length, results };
  fs.writeFileSync(path.join(SHOTS, 'tree-variants-report.json'), `${JSON.stringify(report, null, 1)}\n`);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed · screenshots + report in ${SHOTS}`);
  process.exitCode = failed.length ? 1 : 0;
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
