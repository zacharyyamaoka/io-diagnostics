#!/usr/bin/env node
// Acceptance for the tree switcher: the tree styles (web/tree/v1..v6.js) inside the REAL dashboard
// (web/index.html + app.js), at the 18:11 (F1) and 18:25 (F2) fixture frames, in headless Chrome; then the round 2
// FOUNDATION checks (docs/round2-seams.md): tree style v6 and the Table, each in light and dark.
//
// Runner note: this is a Node + Playwright script, not a pytest test (pytest does not collect .js). Run it with
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js
//
// It starts its own fixture servers (`cli.py serve --fixture F1|F2 --port 0`, a free port each, stopped by their
// process group at the end), drives headless Chrome (/usr/bin/google-chrome; never a visible window), prints one
// line per check, writes reports/media/v1/tree-variants-report.json plus screenshots
// reports/media/v1/tree-<variant>-<1811|1825>.png (+ -hover.png crops), the foundation screenshots
// reports/media/round2/foundation-<tree|table>-<1811|1825>-<light|dark>.png, and exits 1 on any failed check.
// Nothing here opens a CAN adapter or a camera: the fixture host replays recorded frames.
//
// Checks per variant and frame (Zach's tree rules, 2026-10-22):
//   switch      the bottom-right drop-down switches the Tree view live and a reload keeps the choice
//   no-green    no green computed colour anywhere in the tree; no status mark on a row that shows OK / nothing
//   shapes      every visible row whose OWN report is a problem carries a visible shape element
//   right       that shape sits right of the row's name (getBoundingClientRect x order)
//   chevrons    v1-v5: no chevron visible at rest on unselected rows; hovering a parent row shows its chevron
//               v6 (IDE × Notion): every folder shows a quiet chevron at rest (opacity <= .6), no leaf does; hover = full
//   greyed      a not-connected row's name is grey (low saturation) and recedes: closer to the pane's background
//               luminance than connected rows' names (round 2: holds in light and dark)
//   select      clicking a row updates the detail panel (title + path) and the renderer marks it selected
//   search      the search box filters the tree (v5 has none: the box is disabled and says so)
//   tabs        Table and Activity still render, and Tree comes back on the same proposal
//   errors      no page errors or console errors
//
// Foundation checks (stylesheets once, then per frame × {v6 tree, table} × {light, dark}):
//   literals    no colour literal (hex, rgb/hsl, named colour) in a declaration of app.css or tree/*.css; every
//               var(--io-*) any stylesheet reads is in the token list of tokens.css
//   mode        the Light / Dark control sets <html data-mode>, a reload keeps it; Auto follows prefers-color-scheme
//   tokens      every token of tokens.css resolves to a value on <html>, and the skin's own rule for this mode
//               declares every BASE token (nothing silently falls back)
//   quiet-ok    an OK row (no own problem, no folded-part problem, no hint) paints no colour at all (chroma of every
//               text, fill, stroke, background and border < .12)
//   shapes      every row whose own report or folded part is a problem carries a visible shape
//   right       that shape sits right of the row's name (getBoundingClientRect)
//   labels      rows show `model · id` labels and no composition part is a row (parts folded)
//   contrast    WCAG 2.x, measured on the browser's own resolved tokens, for every skin in both modes: every text
//               token >= 4.5:1 on surface, surface-2, surface-alt and popover; the problem shapes >= 3:1 on the
//               surface and on the selected row (round 2 integration)
//   message     the Table's Message column keeps >= MESSAGE_MIN px at 1600 px with the detail pane open, in every
//               structure with its extra columns (Zach's screenshot showed "ME"); round 3: the inspector is folded
//               until a row is clicked, so the gate clicks one first
//   warn-colours (round 3, default skin, light + dark) outside status marks and true diagnostic rows / messages, no
//               element computes to a warn / error / stale / unknown colour or tint: the header's Utilities ▾ menu at
//               rest, a moves dry run (the sheet) with its preconditions, its confirm modal, a blocked dry run, the
//               quiet run line (round 3.5: progress, then an OK result), the activity log's non-diagnostic tags, View
//               options and every control button. --mutate paints each of those with a gated token (WARN_MUTATIONS)
//               and the gate must name all seven, in both modes
// Round 3: Group by, tree style, skin and mode live in the View options popover (pick() opens it); the default skin is
// the skin manifest's (shadcn/ui since round 3.5; round 3 had Bare Metal); P3 left the Group by picker, so the foundation gates the picker's structures.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const SHOTS = path.join(ROOT, 'reports', 'media', 'v1');
const ROUND2 = path.join(ROOT, 'reports', 'media', 'round2');
const WEB = path.join(ROOT, 'io_diagnostics', 'web');
const FRAMES = [{ fixture: 'F1', label: '1811' }, { fixture: 'F2', label: '1825' }];
const VARIANTS = ['v1', 'v2', 'v3', 'v4', 'v5', 'v6'];
const QUIET_CHEVRONS = new Set(['v6']); // chevrons visible (quiet) at rest on every folder
const VIEWPORT = { width: 1600, height: 1000 };
const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
// --mutate proves the ruler can fail: it paints every name green and pins every chevron visible (v6: hides them), so
// `no-green` and `chevrons` must FAIL for every variant, and the foundation's `quiet-ok` must FAIL for the tree and
// the table (no screenshots or report are written in that mode).
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_tree_variants.js --mutate
const MUTATE = process.argv.includes('--mutate');
const MUTATION_CSS = '#tree-host [class*="-name"] { color: #5cb85c !important; }'
  + ' #tree-host .t1-chev, #tree-host .v3-chev, #tree-host .v4-chev, #tree-host .v5-chev { opacity: 1 !important; }'
  + ' #tree-host .t2-chev { display: block !important; }'
  + ' #tree-host .t6-chev { opacity: 0 !important; }'
  + ' table.grid .name { color: #5cb85c !important; }'
  // the contrast gate must fail on a faint token that reads too light (light) / too dark (dark)
  + ' html[data-mode="light"][data-skin] { --io-faint: #b8b8b8 !important; } html[data-mode="dark"][data-skin] { --io-faint: #4a4a4a !important; }'
  // the Message gate must fail when the table may squeeze it again
  + ' table.grid { min-width: 0 !important; }';
// round 3: the warning-colour gate must fail when a utility's reason, a danger word, a precondition, a primary button,
// a non-diagnostic activity tag or a View options control is painted with a warn / error / stale / unknown token
const WARN_MUTATIONS = {
  'u-reason': '.u-reason { color: var(--io-warn-text) !important; }',
  danger: '.ent-tools .danger { background: var(--io-error-soft) !important; }',
  precondition: '.pre .st.unknown, .pre .st.fail { color: var(--io-warn-text) !important; }',
  button: '.btn.primary { border-color: var(--io-error-line) !important; }',
  'activity tag': '.ev:not([data-diagnostic]) .kind { background: var(--io-stale-soft) !important; }',
  'view options': '.vo-btn { color: var(--io-unknown) !important; }',
  // round 3.5: the quiet run line under the entity header (progress, then an OK result) is never a warning colour
  'run line': '.ent-run:not([data-diagnostic]) .ent-run-t { color: var(--io-warn-text) !important; }',
};
const MUTATION_CSS_ALL = () => MUTATION_CSS + Object.values(WARN_MUTATIONS).join(' ');
const MESSAGE_MIN = 240; // px: app.js keeps 280; a few px of padding / rounding below that still reads a sentence
const CONTRAST = {
  text: ['--io-text', '--io-text-strong', '--io-muted', '--io-faint', '--io-accent-text', '--io-warn-text', '--io-error-text', '--io-stale-text',
    '--io-unknown-text', '--io-info-text', '--io-ok-text'],
  panes: ['--io-surface', '--io-surface-2', '--io-surface-alt', '--io-popover'],
  shapes: ['--io-warn', '--io-error', '--io-stale', '--io-unknown'],
  shapeOn: ['--io-surface', '--io-selected'],
};

// What each proposal calls its parts. `own` is the shape of the row's OWN self-report (not the advisory hint).
const SPEC = {
  v1: { row: '.t1-row[data-t1-path]', attr: 'data-t1-path', name: '.t1-name', own: '.t1-st > .t1-mark:not(.t1-part) > .t1-shape', chev: '.t1-chev:not(.t1-leaf)' },
  v2: { row: '.t2-row[data-t2-path]', attr: 'data-t2-path', name: '.t2-name', own: '.t2-status .t2-shape', chev: '.t2-chev' },
  v3: { row: '.v3-row[data-v3-path]', attr: 'data-v3-path', name: '.v3-name', own: '.v3-status > .v3-shape', chev: '.v3-chev' },
  v4: { row: '.v4-row[data-path]', attr: 'data-path', name: '.v4-name', own: '.v4-status > .v4-glyph-box > .v4-self, .v4-status > .v4-glyph-box > .v4-knock', chev: '.v4-chev' },
  v5: { row: '.v5-row[data-v5-path]', attr: 'data-v5-path', name: '.v5-name', own: '.v5-c-status > .v5-shape', chev: '.v5-chev' },
  v6: { row: '.t6-row[data-t6-path]', attr: 'data-t6-path', name: '.t6-name', own: '.t6-st > .t6-mark.t6-own > .t6-shape', part: '.t6-st > .t6-mark.t6-part > .t6-shape', chev: '.t6-chev:not(.t6-leaf)' },
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
  // the pane's background luminance (first opaque background up the tree): "greyed" = closer to it
  let bg = null;
  for (let e = pane; e && !bg; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.5) bg = c; }
  const bgLum = luminance(bg || { r: 255, g: 255, b: 255 });
  for (const row of rows) row.nameDistance = row.nameLuminance === null ? null : Math.abs(row.nameLuminance - bgLum);
  return { greens, rows, scroll: document.getElementById('left').scrollTop, bgLum };
}

// ------------------------------------------------------------------------------------------- checks ----
const results = [];
// `all`: the full evidence behind a truncated detail (the --mutate summary reads it), kept out of the printed line
function check(frame, variant, name, ok, detail, all = null) {
  results.push({ frame, variant, check: name, ok: Boolean(ok), detail, ...(all ? { all } : {}) });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${frame} ${variant}  ${name.padEnd(9)}  ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}
const ONLY_FOUNDATION = process.argv.includes('--foundation'); // skip the per-variant checks

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
  // WHY dark: this per-variant regression was written against the dark v1 page; light is the foundation phase's job
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, colorScheme: 'dark' });
  const page = await context.newPage();
  // WHY p0: these per-style rules (greyed empty ports, hover chevrons on ports) are about the physical tree; the
  // default structure (p1) hides the layers, and the foundation phase below gates every structure
  await page.addInitScript(() => { try { if (!localStorage.getItem('io-diagnostics.structure')) localStorage.setItem('io-diagnostics.structure', 'p0'); } catch (_) { /* none */ } });
  if (MUTATE) {
    await page.addInitScript((css) => document.addEventListener('DOMContentLoaded', () => {
      const style = document.createElement('style');
      style.textContent = css;
      document.head.appendChild(style);
    }), MUTATION_CSS_ALL());
  }
  const shoot = (options) => (MUTATE ? Promise.resolve() : page.screenshot(options));
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  try {
    // ---- switch live from the default, then reload: the choice must stick
    await page.goto(`${base}/`);
    await page.waitForFunction(() => document.querySelectorAll('#tree-variant option').length >= 6 && document.querySelector('.d-title'));
    const firstLoad = await page.evaluate(() => document.getElementById('tree-variant').value);
    await pick(page, '#tree-variant', variant);
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
      corner: (() => { const r = document.getElementById('controls').getBoundingClientRect(); return { right: Math.round(innerWidth - r.right), bottom: Math.round(innerHeight - r.bottom) }; })(),
    }));
    check(frame, variant, 'switch', live === variant && kept.picker === variant && kept.pane === variant && kept.panes === 1 && kept.stored === variant
      && kept.corner.right < 40 && kept.corner.bottom < 40,
    `first load ${firstLoad} · live ${live} · after reload picker ${kept.picker}, pane ${kept.pane}, stored ${kept.stored} · controls ${kept.corner.right}px from right, ${kept.corner.bottom}px from bottom`);

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

    // chevrons: none visible at rest on unselected rows (v6: a quiet one on every folder); hovering a parent row shows its own
    const quietStyle = QUIET_CHEVRONS.has(variant);
    const restingChevrons = quietStyle
      ? rest.rows.filter((r) => !r.selected && r.chevOpacity !== null && !(r.chevShown && r.chevOpacity <= 0.6))
      : rest.rows.filter((r) => r.chevShown && !r.selected);
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
    check(frame, variant, 'chevrons', restingChevrons.length === 0 && hover.name && (quietStyle ? hover.after > 0.95 : hover.shownBefore === false) && hover.shown,
      `at rest ${restingChevrons.length} ${quietStyle ? 'folders without a quiet chevron' : 'visible on unselected rows'}${restingChevrons.length ? ` (${restingChevrons.map((r) => r.name).join(', ')})` : ''} · `
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
    const connectedNames = reference.filter((r) => r.connected !== false && r.nameDistance !== null && !(r.own && PROBLEM.has(r.own.level)) && !r.selected)
      .map((r) => r.nameDistance).sort((a, b) => a - b);
    const typical = connectedNames.length ? connectedNames[Math.floor(connectedNames.length / 2)] : null;
    const notGrey = greyRows.filter((r) => !(r.nameSaturation < 0.15 && typical !== null && r.nameDistance < typical - 20));
    check(frame, variant, 'greyed', greyRows.length > 0 && notGrey.length === 0,
      `${greyRows.map((r) => `${r.name}: ${r.nameColor} (sat ${r.nameSaturation.toFixed(2)}, ${Math.round(r.nameDistance)} from the background)`).join(' · ')} vs connected median ${Math.round(typical)}`);

    // clicking a row updates the detail panel, and the renderer marks it selected
    const now = await page.evaluate(measureRows, { spec, nodes });
    const current = now.rows.find((r) => r.selected);
    const clickable = now.rows.filter((r) => !r.selected && r.path !== (current && current.path));
    const target = clickable.find((r) => r.own && PROBLEM.has(r.own.level)) || clickable.find((r) => r.name === 'CAN 14') || clickable[0];
    const rowSelector = rowSelectorFor(spec, target.path);
    await page.click(`${rowSelector} ${spec.name}`);
    const label = await page.evaluate((nodePath) => (window.IOView ? window.IOView.labelOf(S.data && index(S.data.tree).get(nodePath)).label : null), target.path);
    let selectOk = true;
    try {
      await page.waitForFunction(({ name, nodePath }) => {
        const title = document.querySelector('.d-title span');
        const where = document.querySelector('.d-path');
        return title && title.textContent === name && where && where.textContent.includes(nodePath) && document.querySelector('#detail .d-body .sec');
      }, { name: label, nodePath: target.path }, { timeout: 6000 });
    } catch (_) { selectOk = false; }
    const picked = await page.evaluate(({ selector }) => {
      const row = document.querySelector(`#tree-host > .tree-pane:not([hidden]) ${selector}`);
      return { selected: row && row.getAttribute('aria-selected'), title: (document.querySelector('.d-title span') || {}).textContent, cards: document.querySelectorAll('#detail .d-body .sec').length };
    }, { selector: rowSelector });
    check(frame, variant, 'select', selectOk && picked.selected === 'true',
      `clicked "${target.name}" → detail "${picked.title}" (label "${label}") with ${picked.cards} cards · row aria-selected ${picked.selected}`);

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
    check(frame, variant, 'tabs', table.rows > 0 && table.treeHidden && table.okPills === 0 && activity.events > 0 && back.pane === variant && back.title === label,
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

// ------------------------------------------------------------------------------- foundation ----
// The token list, read from tokens.css itself: BASE tokens come before the first "DERIVED" section.
function tokenList() {
  const text = fs.readFileSync(path.join(WEB, 'tokens.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, (c) => (c.includes('---- DERIVED') ? '@@DERIVED@@' : ''));
  const [basePart] = text.split('@@DERIVED@@');
  const names = (part) => [...new Set([...part.matchAll(/(--io-[a-z0-9-]+)\s*:/g)].map((m) => m[1]))];
  return { base: names(basePart), all: names(text) };
}

// The ids a manifest marks ready (skins/index.json, view/structure/index.json).
const readyIds = (manifest, key) => (JSON.parse(fs.readFileSync(path.join(WEB, manifest), 'utf8'))[key] || []).filter((entry) => entry.ready).map((entry) => entry.id);
// round 3: the default skin is the manifest's (round 3.5: shadcn; round 3: Bare Metal = contrast); Group by lists the ready structures that are not
// picker=false (P3 left the picker, its file stays: test_view_core.js still gates it)
const DEFAULT_SKIN = JSON.parse(fs.readFileSync(path.join(WEB, 'skins', 'index.json'), 'utf8')).default;
const pickerIds = () => (JSON.parse(fs.readFileSync(path.join(WEB, 'view', 'structure', 'index.json'), 'utf8')).structures || [])
  .filter((entry) => entry.ready && entry.picker !== false).map((entry) => entry.id);
// Group by, tree style, skin and mode live in the View options popover since round 3
async function viewOptions(page) {
  if (await page.evaluate(() => document.getElementById('view-options').hidden)) await page.click('#vo-btn');
  await page.waitForSelector('#view-options:not([hidden])');
}
async function pick(page, selector, value) {
  await viewOptions(page);
  await page.selectOption(selector, value);
}

// Colour literals in declarations (comments stripped): hex, rgb()/hsl() and the common named colours.
const NAMED = /\b(white|black|red|green|blue|gr[ae]y|orange|yellow|purple|pink|silver|navy|teal|maroon)\b/i;
function literalsIn(file) {
  const text = fs.readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  const found = [];
  for (const block of text.matchAll(/\{([^{}]*)\}/g)) {
    for (const declaration of block[1].split(';')) {
      const colon = declaration.indexOf(':');
      if (colon < 0) continue;
      const value = declaration.slice(colon + 1);
      const hit = value.match(/#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/) || value.match(NAMED);
      if (hit) found.push(`${path.basename(file)}: ${declaration.trim().slice(0, 80)}`);
    }
  }
  return found;
}

function staticChecks() {
  const sheets = [path.join(WEB, 'app.css'), ...fs.readdirSync(path.join(WEB, 'tree')).filter((f) => f.endsWith('.css')).map((f) => path.join(WEB, 'tree', f))];
  const literals = sheets.flatMap(literalsIn);
  check('static', 'css', 'literals', literals.length === 0 && sheets.length >= 7,
    `${sheets.length} stylesheets (app.css + tree/*.css), ${literals.length} colour literals${literals.length ? `: ${literals.slice(0, 4).join(' | ')}` : ''}`);
  const { all } = tokenList();
  const readers = [...sheets, path.join(WEB, 'skins', 'claude.css'), path.join(WEB, 'index.html'), ...fs.readdirSync(path.join(WEB, 'tree')).filter((f) => f.endsWith('.js')).map((f) => path.join(WEB, 'tree', f))];
  const unknown = new Set();
  for (const file of readers) for (const m of fs.readFileSync(file, 'utf8').matchAll(/var\((--io-[a-z0-9-]+)/g)) if (!all.includes(m[1])) unknown.add(`${path.basename(file)} ${m[1]}`);
  check('static', 'css', 'var-refs', unknown.size === 0, `${all.length} tokens; unknown var(--io-*) reads: ${[...unknown].join(', ') || 'none'}`);
}

// Runs in the page: what the foundation checks need, from the rows the page actually drew (S.built).
function foundationMeasure({ view, tokens, base }) {
  const html = document.documentElement;
  const parse = (text) => {
    const m = String(text || '').match(/rgba?\(([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+%?))?\)/);
    if (!m) return null;
    let a = m[4] === undefined ? 1 : parseFloat(m[4]);
    if (String(m[4]).endsWith('%')) a /= 100;
    return { r: +m[1], g: +m[2], b: +m[3], a };
  };
  // chroma in sRGB (max - min channel): neutrals, warm or cool, stay under .12; a status colour is far above
  const chroma = (c) => (c && c.a >= 0.1 ? (Math.max(c.r, c.g, c.b) - Math.min(c.r, c.g, c.b)) / 255 : 0);
  const opacity = (element) => { let v = 1; for (let e = element; e && e !== document.body; e = e.parentElement) v *= parseFloat(getComputedStyle(e).opacity); return v; };
  const shown = (element) => {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width >= 1 && rect.height >= 1 && style.visibility !== 'hidden' && style.display !== 'none' && opacity(element) > 0.05;
  };
  const tree = view === 'tree';
  const pane = tree ? document.querySelector('#tree-host > .tree-pane:not([hidden])') : document.getElementById('left-other');
  const domRows = tree ? [...pane.querySelectorAll('.t6-row[data-t6-path]')] : [...pane.querySelectorAll('table.grid tbody tr[data-key]')];
  const keyOf = (element) => element.getAttribute(tree ? 'data-t6-path' : 'data-key');
  const nameSel = tree ? '.t6-name' : 'td.namecell .name';
  const shapeSel = tree ? '.t6-st .t6-shape' : 'td.namecell .st .shape';
  const coloured = [];
  const shapeless = [];
  const leftOf = [];
  const labelMismatch = [];
  let okRows = 0;
  let problemRows = 0;
  for (const element of domRows) {
    const row = S.built.byKey.get(keyOf(element));
    if (!row) continue;
    const status = row.status || {};
    const name = element.querySelector(nameSel);
    if (!name || name.textContent !== row.label) labelMismatch.push(`${row.label} ≠ ${name && name.textContent}`);
    if (!status.own && !status.part && !status.hint) {
      okRows += 1;
      for (const e of [element, ...element.querySelectorAll('*')]) {
        if (!shown(e)) continue;
        const style = getComputedStyle(e);
        const samples = [['background', style.backgroundColor]];
        if ([...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) samples.push(['color', style.color]);
        if (e instanceof SVGElement) {
          if (style.fill && style.fill !== 'none') samples.push(['fill', style.fill]);
          if (style.stroke && style.stroke !== 'none') samples.push(['stroke', style.stroke]);
        }
        for (const side of ['Top', 'Right', 'Bottom', 'Left']) if (parseFloat(style[`border${side}Width`]) > 0) samples.push([`border-${side}`, style[`border${side}Color`]]);
        for (const [prop, value] of samples) if (chroma(parse(value)) >= 0.12) coloured.push(`${row.label}: ${e.tagName.toLowerCase()}.${String(e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className).trim()} ${prop} ${value}`);
      }
    }
    if (status.own || status.part) {
      problemRows += 1;
      const mark = [...element.querySelectorAll(shapeSel)].find(shown);
      if (!mark) shapeless.push(row.label);
      else if (name && mark.getBoundingClientRect().left < name.getBoundingClientRect().right - 0.5) leftOf.push(row.label);
    }
  }
  const style = getComputedStyle(html);
  const unset = tokens.filter((token) => !style.getPropertyValue(token).trim());
  // the skin's OWN rule for this mode must declare every base token
  const skin = html.dataset.skin;
  const mode = html.dataset.mode;
  const declared = new Set();
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (_) { continue; }
    for (const rule of rules) {
      if (rule.selectorText === `[data-skin="${skin}"][data-mode="${mode}"]`) for (let i = 0; i < rule.style.length; i += 1) declared.add(rule.style[i]);
    }
  }
  const undeclared = base.filter((token) => !declared.has(token));
  return {
    skin, mode, rows: domRows.length, okRows, problemRows, coloured, shapeless, leftOf, labelMismatch, unset, undeclared,
    // a composition part (driver, motor, stream) as a row; WHY not edge === 'part_of': the live moteus plugin (and
    // F1-F3 since round 2) reports the CAN bus part_of its adapter, and P5 draws that bus as its top-level row
    partRows: S.built.flat.filter((row) => row.kind === 'part' || row.kind === 'stream').length,
    folded: S.built.flat.filter((row) => row.parts.length).map((row) => row.label),
    labels: S.built.flat.filter((row) => row.model).map((row) => row.label),
  };
}

// Runs in the page: WCAG 2.x contrast of the active skin's tokens, resolved by the browser itself (color-mix and
// all). Text tokens on the four reading surfaces (>= 4.5:1); the problem SHAPES on the surface and on the selected
// row (>= 3:1). A see-through token is composited over the surface it sits on.
function contrastMeasure({ text, panes, shapes, shapeOn }) {
  const probe = document.createElement('div');
  probe.style.display = 'none';
  document.body.appendChild(probe);
  const parse = (value) => {
    let m = String(value).match(/^rgba?\(([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+%?))?\)$/);
    if (m) return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : (String(m[4]).endsWith('%') ? parseFloat(m[4]) / 100 : +m[4]) };
    m = String(value).match(/^color\(srgb ([\d.e-]+) ([\d.e-]+) ([\d.e-]+)(?: \/ ([\d.]+%?))?\)$/);
    if (m) return { r: 255 * m[1], g: 255 * m[2], b: 255 * m[3], a: m[4] === undefined ? 1 : (String(m[4]).endsWith('%') ? parseFloat(m[4]) / 100 : +m[4]) };
    return null;
  };
  const resolve = (token) => { probe.style.color = `var(${token})`; return parse(getComputedStyle(probe).color); };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const lin = (c) => { const v = c / 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const surface = resolve('--io-surface');
  const solid = (token, under = surface) => { const c = resolve(token); return c && c.a < 1 ? over(c, under) : c; };
  const pairs = [];
  const unresolved = [];
  for (const pane of panes) {
    const bg = solid(pane);
    for (const fg of text) {
      const c = solid(fg, bg);
      if (!c || !bg) { unresolved.push(`${fg} / ${pane}`); continue; }
      pairs.push({ fg, bg: pane, ratio: ratio(c, bg), min: 4.5 });
    }
  }
  for (const on of shapeOn) {
    const bg = solid(on);
    for (const fg of shapes) {
      const c = solid(fg, bg);
      if (!c || !bg) { unresolved.push(`${fg} / ${on}`); continue; }
      pairs.push({ fg, bg: on, ratio: ratio(c, bg), min: 3 });
    }
  }
  probe.remove();
  const failing = pairs.filter((pair) => pair.ratio < pair.min).map((pair) => `${pair.fg} on ${pair.bg} ${pair.ratio.toFixed(2)} < ${pair.min}`);
  const lowest = pairs.reduce((low, pair) => (!low || pair.ratio / pair.min < low.ratio / low.min ? pair : low), null);
  return { pairs: pairs.length, failing, unresolved, lowest: lowest ? `${lowest.fg} on ${lowest.bg} ${lowest.ratio.toFixed(2)}` : '' };
}

// ---------------------------------------------------------------------- the warning-colour gate ----
// Round 3 (Zach, 2026-10-23: "reserve that color for annotating true diagnostics warning and errors and nothing
// else"): outside the status marks (the shapes, the top-bar chip's level) and true diagnostic rows / messages (an
// activity event the host logged at a problem severity carries data-diagnostic), no element may compute to a warn /
// error / stale / unknown colour or one of its tints (-text, -soft, -line), measured on the browser's own resolved
// tokens. A gated colour that equals a neutral token in this skin cannot be told apart and is not gated.
// Runs in the page over one named scope: every shown element's text colour (where it has text), background, borders,
// and an SVG's fill / stroke.
function warnColours({ scope, selectors }) {
  const probe = document.createElement('div');
  document.body.appendChild(probe);
  const parse = (value) => {
    let m = String(value).match(/^rgba?\(([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+%?))?\)$/);
    if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : (String(m[4]).endsWith('%') ? parseFloat(m[4]) / 100 : +m[4])];
    m = String(value).match(/^color\(srgb ([\d.e-]+) ([\d.e-]+) ([\d.e-]+)(?: \/ ([\d.]+%?))?\)$/);
    if (m) return [255 * m[1], 255 * m[2], 255 * m[3], m[4] === undefined ? 1 : (String(m[4]).endsWith('%') ? parseFloat(m[4]) / 100 : +m[4])];
    return null;
  };
  const resolve = (token) => { probe.style.color = `var(${token})`; return parse(getComputedStyle(probe).color); };
  const same = (a, b) => a && b && Math.abs(a[0] - b[0]) < 1.5 && Math.abs(a[1] - b[1]) < 1.5 && Math.abs(a[2] - b[2]) < 1.5 && Math.abs(a[3] - b[3]) < 0.02;
  const levels = ['warn', 'error', 'stale', 'unknown'];
  const gated = levels.flatMap((level) => ['', '-text', '-soft', '-line'].map((suffix) => `--io-${level}${suffix}`))
    .map((token) => ({ token, value: resolve(token) })).filter((item) => item.value && item.value[3] > 0.01);
  const neutral = ['--io-text', '--io-text-strong', '--io-muted', '--io-faint', '--io-off', '--io-icon', '--io-border', '--io-border-soft', '--io-hover',
    '--io-selected', '--io-selected-line', '--io-control', '--io-control-hover', '--io-pressed', '--io-surface', '--io-surface-2', '--io-surface-alt',
    '--io-field', '--io-popover', '--io-bg'].map(resolve);
  const watch = gated.filter((item) => !neutral.some((value) => same(value, item.value)));
  probe.remove();
  const shown = (element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width >= 1 && rect.height >= 1 && style.visibility !== 'hidden' && style.display !== 'none' && parseFloat(style.opacity) > 0.05;
  };
  const exempt = (element) => Boolean(element.closest('.shape, [data-diagnostic], #chip'));
  const offenders = [];
  let elements = 0;
  const roots = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]);
  for (const root of roots) {
    for (const element of [root, ...root.querySelectorAll('*')]) {
      if (exempt(element) || !shown(element)) continue;
      elements += 1;
      const style = getComputedStyle(element);
      const samples = [['background', style.backgroundColor]];
      if ([...element.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) samples.push(['color', style.color]);
      if (element instanceof SVGElement) {
        if (style.fill && style.fill !== 'none') samples.push(['fill', style.fill]);
        if (style.stroke && style.stroke !== 'none') samples.push(['stroke', style.stroke]);
      }
      for (const side of ['Top', 'Right', 'Bottom', 'Left']) if (parseFloat(style[`border${side}Width`]) > 0) samples.push([`border-${side.toLowerCase()}`, style[`border${side}Color`]]);
      for (const [prop, value] of samples) {
        const hit = watch.find((item) => same(parse(value), item.value));
        if (hit) offenders.push(`${scope}: ${element.tagName.toLowerCase()}.${String(element.className && element.className.baseVal !== undefined ? element.className.baseVal : element.className).trim().replace(/\s+/g, '.')} ${prop} = ${hit.token}`);
      }
    }
  }
  return { scope, elements, watched: watch.length, offenders };
}

// Drives the page through every non-diagnostic surface the brief names and measures each: the Utilities section at
// rest (blocked reasons, danger words), a moves dry run and its preconditions, its confirm modal, a blocked dry run's
// banner, the activity log's non-diagnostic tags, View options and every control button. Returns one result per scope.
async function warnGate(page, base) {
  const CAN15 = 'host/usb1/port-1-3/usb-1-3/can/15';
  const scopes = [];
  const measure = async (scope, selectors) => scopes.push(await page.evaluate(warnColours, { scope, selectors }));
  const controls = ['.toolbar button', '.toolbar select', '#controls', '.insp-bar', '.statusbar'];
  await page.goto(`${base}/?sel=${encodeURIComponent(CAN15)}`);
  // round 3.5: the utilities live in the entity header's Utilities ▾ menu; the dry run reads in the sheet under it
  await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 20000 });
  await page.click('#ent-util-btn');
  await page.waitForSelector('.ent-menu [data-util="calibrate"]', { timeout: 20000 });
  await page.mouse.move(1590, 600);
  await page.waitForTimeout(400);
  await measure('utilities', ['.ent-tools', '.sec.setup', '#detail button']);
  await page.click('[data-util="calibrate"]');
  await page.waitForSelector('.ent-sheet .u-panel .pre', { timeout: 15000 });
  await page.waitForTimeout(300);
  await measure('dry run', ['.ent-sheet', '.ent-tools']);
  // the confirm modal opens only from a runnable dry run (F1: calibrate is; F2's busy bus blocks it, so F1 covers it)
  if (await page.evaluate(() => { const run = document.querySelector('[data-util-run]'); return Boolean(run && !run.disabled); })) {
    await page.click('[data-util-run]');
    await page.waitForSelector('#modal:not([hidden]) .modal', { timeout: 5000 });
    await measure('modal', ['#modal .modal']);
    await page.click('[data-util-cancel]');
  }
  // a blocked utility (update_firmware: "up to date" = nothing_to_do): its dry run shows the blocked banner
  await page.click('#ent-util-btn');
  await page.click('.ent-menu [data-util="update_firmware"]');
  await page.waitForSelector('.ent-sheet .u-panel .banner.blocked', { timeout: 15000 });
  await measure('blocked dry run', ['.ent-sheet']);
  await page.click('[data-util-close]');
  // a query run on the adapter: the quiet line under the header while it runs (held at the network), then its result
  await page.goto(`${base}/?sel=${encodeURIComponent(CAN15.replace(/\/can\/15$/, ''))}`);
  await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 20000 });
  await page.route('**/api/v1/act', async (route) => {
    if (!JSON.parse(route.request().postData() || '{}').dry_run) await new Promise((resolve) => setTimeout(resolve, 2200));
    await route.continue();
  });
  await page.click('#ent-util-btn');
  await page.waitForSelector('.ent-menu', { timeout: 5000 });
  // (F1: check_comms runs; a frame whose bus is busy blocks it, and F1 covers the line)
  if (await page.$('.ent-menu [data-util="check_comms"]:not([disabled])')) {
    await page.click('.ent-menu [data-util="check_comms"]');
    await page.waitForSelector('.ent-sheet [data-util-run]', { timeout: 15000 });
    if (await page.$('.ent-sheet [data-util-run]:not([disabled])')) {
      await page.click('[data-util-run]');
      await page.waitForSelector('.ent-run.running', { timeout: 5000 });
      await measure('run line', ['.ent-run']);
      await page.waitForSelector('.ent-run.ok, .ent-run.fail', { timeout: 20000 });
      if (await page.$('.ent-run.ok')) await measure('run line', ['.ent-run']);
    } else await page.click('[data-util-close]');
  } else await page.keyboard.press('Escape');
  await page.unroute('**/api/v1/act');
  await page.click('[data-view="activity"]');
  await page.waitForSelector('.act .ev', { timeout: 6000 });
  await measure('activity', ['.act .ev:not([data-diagnostic])', '.act h2', '.act-filter']);
  await viewOptions(page);
  await measure('view options', ['#view-options', ...controls]);
  await page.keyboard.press('Escape');
  await page.click('[data-view="table"]');
  await page.waitForSelector('table.grid tbody tr');
  await page.keyboard.press(']');
  await page.waitForTimeout(450);
  await measure('table chrome', controls);
  await page.keyboard.press(']');
  await page.click('[data-view="tree"]');
  return scopes;
}

async function runFoundation(browser, base, frame) {
  const { all, base: baseTokens } = tokenList();
  // the OS says dark: Auto must follow it until a mode is picked
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, colorScheme: 'dark' });
  const page = await context.newPage();
  if (MUTATE) {
    await page.addInitScript((css) => document.addEventListener('DOMContentLoaded', () => {
      const style = document.createElement('style');
      style.textContent = css;
      document.head.appendChild(style);
    }), MUTATION_CSS_ALL());
  }
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  const ready = async () => {
    await page.waitForFunction(() => typeof S !== "undefined" && S.built && S.built.flat.some((row) => row.parts.length) && document.querySelector('.t6-row') && document.querySelector('.d-title'), null, { timeout: 30000 });
    await page.mouse.move(1590, 600);
    await page.waitForTimeout(500);
  };
  try {
    await page.goto(`${base}/`);
    await ready();
    const fresh = await page.evaluate(() => ({ mode: document.documentElement.dataset.mode, skin: document.documentElement.dataset.skin,
      variant: document.getElementById('tree-variant').value, structure: document.getElementById('structure').value,
      skins: [...document.querySelectorAll('#skin option')].map((o) => o.value), structures: [...document.querySelectorAll('#structure option')].map((o) => o.value) }));
    check(frame, 'found', 'defaults', fresh.mode === 'dark' && fresh.skin === DEFAULT_SKIN && fresh.variant === 'v6' && fresh.structure === 'p1',
      `fresh browser, OS dark: mode ${fresh.mode} (Auto), skin ${fresh.skin} of [${fresh.skins}], tree ${fresh.variant}, structure ${fresh.structure} of [${fresh.structures}]`);
    // every READY skin (with the default structure p1) and every READY structure (with the default skin): a lane that
    // flips its manifest line gets these gates without touching this file (docs/round2-seams.md)
    const combos = [{ skin: DEFAULT_SKIN, structure: 'p1' },
      ...pickerIds().filter((id) => id !== 'p1').map((structure) => ({ skin: DEFAULT_SKIN, structure })),
      ...readyIds('skins/index.json', 'skins').filter((id) => id !== DEFAULT_SKIN).map((skin) => ({ skin, structure: 'p1' }))];
    for (const combo of combos) {
      const isDefault = combo.skin === DEFAULT_SKIN && combo.structure === 'p1';
      if (!isDefault) {
        await pick(page, '#skin', combo.skin);
        await pick(page, '#structure', combo.structure);
      }
      for (const mode of ['light', 'dark']) {
        await viewOptions(page);
        await page.click(`#mode-switch [data-mode="${mode}"]`);
        await page.reload();
        await ready();
        const tag = isDefault ? `found-${mode}` : `found-${combo.skin}-${combo.structure}-${mode}`;
        const kept = await page.evaluate(() => ({ mode: document.documentElement.dataset.mode, stored: localStorage.getItem('io-diagnostics.mode'),
          on: (document.querySelector('#mode-switch button.on') || {}).textContent, skin: document.documentElement.dataset.skin, structure: S.structure }));
        check(frame, tag, 'mode', kept.mode === mode && kept.stored === mode && kept.on.toLowerCase() === mode && kept.skin === combo.skin && kept.structure === combo.structure,
          `picked ${mode} (OS dark) → html data-mode ${kept.mode} after reload, stored ${kept.stored}, control shows ${kept.on} · skin ${kept.skin}, structure ${kept.structure}`);
        for (const view of ['tree', 'table']) {
          if (view === 'table') {
            await page.click('[data-view="table"]');
            await page.waitForSelector('table.grid tbody tr', { timeout: 5000 });
            await page.mouse.move(1590, 600);
            await page.waitForTimeout(400);
          }
          const m = await page.evaluate(foundationMeasure, { view, tokens: all, base: baseTokens });
          const label = `${view.padEnd(5)}`;
          check(frame, tag, `${label} quiet-ok`, m.okRows > 0 && m.coloured.length === 0,
            `${m.okRows} OK rows of ${m.rows}: ${m.coloured.length ? `coloured paint: ${m.coloured.slice(0, 3).join(' | ')}` : 'no colour'}`);
          check(frame, tag, `${label} shapes`, m.problemRows > 0 && m.shapeless.length === 0,
            `${m.problemRows - m.shapeless.length}/${m.problemRows} problem rows carry a shape${m.shapeless.length ? ` · missing: ${m.shapeless.join(', ')}` : ''}`);
          check(frame, tag, `${label} right`, m.problemRows > 0 && m.leftOf.length === 0, m.leftOf.length ? `LEFT of name: ${m.leftOf.join(', ')}` : 'every shape right of its name');
          check(frame, tag, `${label} labels`, m.labelMismatch.length === 0 && m.partRows === 0 && m.folded.length > 0 && m.labels.some((l) => /^fdcanusb · /.test(l)),
            `model · id rows: ${m.labels.slice(0, 4).join(' | ')} · parts folded into ${m.folded.length} rows, ${m.partRows} part rows${m.labelMismatch.length ? ` · mismatch ${m.labelMismatch.slice(0, 2).join(', ')}` : ''}`);
          if (view === 'table') {
            // round 3: the inspector is folded until a row is clicked; the gate measures with it open
            await page.locator('table.grid tbody tr[data-key] td.namecell .name').first().click();
            await page.waitForTimeout(450);
            const widths = await page.evaluate(() => {
              const head = document.querySelector('table.grid th.msgcol');
              const left = document.getElementById('left').getBoundingClientRect();
              return { message: head ? Math.round(head.getBoundingClientRect().width) : 0, pane: Math.round(left.width),
                detail: Math.round(document.getElementById('inspector').getBoundingClientRect().width), columns: S.built.columns.map((column) => column.key) };
            });
            check(frame, tag, 'table message', widths.message >= MESSAGE_MIN && widths.detail > 0,
              `Message column ${widths.message} px (>= ${MESSAGE_MIN}) in a ${widths.pane} px pane beside a ${widths.detail} px detail pane · extra columns [${widths.columns}]`);
          }
          if (view === 'tree' && combo.structure === 'p1') {
            const c = await page.evaluate(contrastMeasure, CONTRAST);
            check(frame, tag, 'contrast', c.pairs === 52 && c.failing.length === 0 && c.unresolved.length === 0,
              `${c.pairs} pairs (text 4.5:1 on 4 surfaces, shapes 3:1 on surface + selected) · lowest ${c.lowest}${c.failing.length ? ` · FAIL ${c.failing.join(' | ')}` : ''}${c.unresolved.length ? ` · unresolved ${c.unresolved.join(', ')}` : ''}`);
          }
          if (view === 'tree') {
            check(frame, tag, 'tokens', m.unset.length === 0 && m.undeclared.length === 0,
              `skin ${m.skin} ${m.mode}: ${all.length - m.unset.length}/${all.length} tokens resolve; skin rule declares ${baseTokens.length - m.undeclared.length}/${baseTokens.length} base tokens${m.unset.length ? ` · unset ${m.unset.join(', ')}` : ''}${m.undeclared.length ? ` · undeclared ${m.undeclared.join(', ')}` : ''}`);
          }
          if (!MUTATE) {
            const prefix = isDefault ? 'foundation' : `foundation-${combo.skin}-${combo.structure}`;
            await page.screenshot({ path: path.join(ROUND2, `${prefix}-${view}-${frame}-${mode}.png`) });
            if (view === 'tree' && isDefault) {
              const box = await page.locator('#controls').boundingBox();
              if (box) await page.screenshot({ path: path.join(ROUND2, `foundation-controls-${frame}-${mode}.png`), clip: { x: Math.max(0, box.x - 20), y: box.y - 12, width: Math.min(VIEWPORT.width - Math.max(0, box.x - 20), box.width + 28), height: box.height + 24 } });
            }
          }
          if (view === 'table') await page.click('[data-view="tree"]');
        }
        if (isDefault) {
          const scopes = await warnGate(page, base);
          const offenders = scopes.flatMap((scope) => scope.offenders);
          check(frame, tag, 'warn-colours', offenders.length === 0 && scopes.every((scope) => scope.elements > 0) && scopes[0].watched >= 8,
            { scopes: scopes.map((scope) => `${scope.scope} ${scope.elements} el`).join(' · '), watched: scopes[0].watched, offenders: offenders.slice(0, 12) }, offenders);
        }
      }
    }
    await viewOptions(page);
    await page.click('#mode-switch [data-mode="auto"]');
    await page.waitForTimeout(200);
    const auto = await page.evaluate(() => ({ mode: document.documentElement.dataset.mode, stored: localStorage.getItem('io-diagnostics.mode') }));
    check(frame, 'found', 'auto', auto.mode === 'dark' && auto.stored === 'auto', `Auto again → data-mode ${auto.mode} (OS dark), stored ${auto.stored}`);
    check(frame, 'found', 'errors', errors.length === 0, errors.length ? errors.slice(0, 3).join(' | ') : 'none');
  } catch (error) {
    check(frame, 'found', 'crashed', false, String(error && error.stack ? error.stack.split('\n').slice(0, 3).join(' ') : error));
  } finally {
    await context.close();
  }
}

// ---------------------------------------------------------------------------------------------- main ----
async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  fs.mkdirSync(ROUND2, { recursive: true });
  staticChecks();
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
      if (!ONLY_FOUNDATION) for (const variant of VARIANTS) await runVariant(browser, server.base, frame.label, variant);
      await runFoundation(browser, server.base, frame.label);
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
    const quiet = ['tree ', 'table'].filter((view) => ['light', 'dark'].every((mode) => failed.some((r) => r.variant === `found-${mode}` && r.check === `${view} quiet-ok`)));
    const skins = readyIds('skins/index.json', 'skins');
    const contrast = skins.filter((skin) => ['light', 'dark'].every((mode) => failed.some((r) => r.variant === (skin === DEFAULT_SKIN ? `found-${mode}` : `found-${skin}-p1-${mode}`) && r.check === 'contrast')));
    const message = ['light', 'dark'].filter((mode) => failed.some((r) => r.variant === `found-${mode}` && r.check === 'table message'));
    // the warning-colour gate: every mutated surface (WARN_MUTATIONS) is named among the offenders, in both modes
    const marks = { 'u-reason': /\.u-reason[.\s]/, danger: /\.danger[.\s]/, precondition: /span\.st\.(unknown|fail)[.\s]/, button: /\.btn\.primary/, 'activity tag': /^activity: span\.kind[.\s]/, 'view options': /^(view options|table chrome): (button\.vo-btn|svg\.ic)/,
      'run line': /^run line: span\.ent-run-t[.\s]/ };
    const warn = ['light', 'dark'].map((mode) => {
      const result = failed.find((r) => r.variant === `found-${mode}` && r.check === 'warn-colours');
      const offenders = result ? result.all || [] : [];
      return Object.keys(WARN_MUTATIONS).filter((name) => offenders.some((text) => marks[name].test(text)));
    });
    const warnCaught = warn.every((names) => names.length === Object.keys(WARN_MUTATIONS).length);
    console.log(`\nmutation caught in ${caught.length}/${VARIANTS.length} variants (${caught.join(', ')}) · foundation quiet-ok caught for ${quiet.length}/2 views`
      + ` · contrast caught for ${contrast.length}/${skins.length} skins · table message caught in ${message.length}/2 modes`
      + ` · warn-colours caught ${warn.map((names, at) => `${['light', 'dark'][at]} ${names.length}/${Object.keys(WARN_MUTATIONS).length} (${names.join(', ')})`).join(' · ')}`);
    process.exitCode = (ONLY_FOUNDATION || caught.length === VARIANTS.length) && quiet.length === 2 && contrast.length === skins.length && message.length === 2 && warnCaught ? 0 : 1;
    return;
  }
  const report = { run: new Date().toISOString(), viewport: VIEWPORT, frames: FRAMES, variants: VARIANTS, passed: results.length - failed.length, failed: failed.length, results };
  fs.writeFileSync(path.join(SHOTS, 'tree-variants-report.json'), `${JSON.stringify(report, null, 1)}\n`);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed · screenshots + report in ${SHOTS}`);
  process.exitCode = failed.length ? 1 : 0;
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
