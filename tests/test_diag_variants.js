#!/usr/bin/env node
// The five Diagnostics variants d1a-d1e and the shared health chart (detail/health.js), in the REAL page:
//
//   /usr/bin/node /home/bam/io-diagnostics-lanes/diagnostics-variants/tests/test_diag_variants.js
//
// Starts two fixture hosts (`cli.py serve --fixture F1` and `--fixture F2`, a temporary state dir, a free port, `--no-udev`)
// whose plugin directory is the fixture plugins plus the passive Computer plugin (17 statuses on the host), drives them in
// headless Chrome at 1280x720 and checks, for every variant on the real entities (the Computer, an fdcanusb, a CAN board
// with a fault, a camera):
//   - each variant is in the bottom-right Prototype picker and switches live (never a URL flag);
//   - rows are single-line where the variant says so (d1a rows, d1b OK rows, d1c lanes, d1d leaves);
//   - HEALTH ONLY: no value sparkline, no measurement text, nothing drawn but chevrons and status shapes as svg;
//   - the history cells are separate rectangles: a gap of at least 1 px between neighbours, taller than wide, a hover
//     (title) with the time range and the worst level, at least one chart per variant;
//   - OK is neutral (no green); the status line drops down into one lane per diagnostic (d1c shows the lanes);
//   - no horizontal scroll, the detail pane included;
// and a mutation pass: with the gap closed, or a value sparkline injected, the matching check goes red.
// Nothing here presses a utility or touches hardware. Exits 1 on a failure.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const VIEWPORT = { width: 1280, height: 720 };
const VARIANTS = ['d1a', 'd1b', 'd1c', 'd1d', 'd1e'];

const tally = { passed: 0, failed: 0 };
function check(name, ok, detail = '') {
  if (ok) tally.passed += 1; else tally.failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(70)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}

// the fixture plugins + the Computer plugin, in one temporary directory
function pluginsDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diag-variants-plugins-'));
  fs.cpSync(path.join(ROOT, 'tests', 'fixtures', 'plugins'), dir, { recursive: true });
  fs.copyFileSync(path.join(ROOT, 'plugins.d', 'computer.toml'), path.join(dir, 'computer.toml'));
  return dir;
}
function startServer(fixture, plugins) {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'diag-variants-state-'));
  // WHY its own config dir: the History window test below POSTs the setting, which must never reach ~/.config/io-diagnostics
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), 'diag-variants-config-'));
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, IO_DIAGNOSTICS_CONFIG_DIR: configDir };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', fixture,
    '--plugins', plugins, '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stop(child); reject(new Error(`server did not start: ${output}`)); }, 90000);
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
const getJson = async (base, url) => (await fetch(`${base}${url}`)).json();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// what the page measures about the open Diagnostics section
function probe() {
  const section = document.querySelector('.sec.dx-diag');
  if (!section) return { section: false };
  const measure = (chart) => {
    const cells = [...chart.children].map((cell) => cell.getBoundingClientRect());
    const gaps = cells.slice(1).map((rect, at) => rect.left - cells[at].right);
    return { kind: chart.className, count: cells.length, minGap: gaps.length ? Math.min(...gaps) : null,
      tall: cells.every((rect) => rect.height > rect.width), maxWidth: Math.max(...cells.map((rect) => rect.width)), height: cells[0] ? cells[0].height : 0,
      titles: [...chart.children].every((cell) => /\d\d:\d\d:\d\d–\d\d:\d\d:\d\d · (worst: (ok|warn|error|stale|unknown)|no data)/.test(cell.dataset.tip)) };
  };
  const charts = [...section.querySelectorAll('.ioh')].map(measure);
  const pageCharts = [...document.querySelectorAll('#detail .ioh')].map(measure);
  const colour = (selector) => { const cell = section.querySelector(selector); return cell ? getComputedStyle(cell).backgroundColor : null; };
  const lineHeights = (selector) => [...section.querySelectorAll(selector)].map((row) => row.getBoundingClientRect().height);
  const cut = (selector) => [...section.querySelectorAll(selector)].filter((cell) => cell.scrollHeight > cell.clientHeight + 1).length;
  const detail = document.getElementById('detail');
  return {
    section: true,
    pageCharts,
    title: section.querySelector('.sec-h > span').textContent.trim(),
    charts,
    ok: colour('.ioh i.ok'), warn: colour('.ioh i.warn'), error: colour('.ioh i.error'),
    svgs: [...section.querySelectorAll('svg')].filter((svg) => !svg.classList.contains('ic') && !svg.classList.contains('shape')).length,
    valueMarks: section.querySelectorAll('.d1-sp, .d1-ms, .d1-mv, .vt, polyline, path.ln').length,
    a: { rows: lineHeights('.dx-tab .dx-r > .dx-line'), cut: cut('.dx-tab .dx-n, .dx-tab .dx-m'),
      head: [...section.querySelectorAll('.dx-hd > span')].map((el) => el.textContent), cols: [...(section.querySelector('.dx-line') || { children: [] }).children].map((el) => el.className) },
    okRows: lineHeights('.db-ok .dx-line'),
    lanes: lineHeights('.dx-lane'), laneCut: cut('.dx-lane .dx-lname'),
    leaves: lineHeights('.dd-leaf'),
    pills: [...section.querySelectorAll('.pill')].map((el) => el.textContent.trim()),
    scroll: { detail: detail.scrollWidth - detail.clientWidth, page: document.documentElement.scrollWidth - window.innerWidth },
    layout: S.layout,
    toggle: Boolean(section.querySelector('[data-dx-toggle="sum"]')),
  };
}


// ---------------------------------------------------------------------------------------- colour schemes ----
// hc1-hc5 (detail/health.js PALETTES): measured on real cells drawn into the page, light and dark, against the detail pane's own
// background. Rules (Zach, 2026-10-24): WARN yellow-amber and ERROR red in every palette, no data empty (no fill), nothing hatched,
// the greys distinct from each other, and every palette readable (contrast numbers are in the report and asserted as floors here).
const parseColor = (text) => {
  const nums = (text.match(/-?[\d.]+(?:e-?\d+)?/g) || []).map(Number);
  if (/^color\(/.test(text)) return { rgb: nums.slice(0, 3).map((v) => v * 255), alpha: nums.length > 3 ? nums[3] : 1 };
  return { rgb: nums.slice(0, 3), alpha: nums.length > 3 ? nums[3] : 1 };
};
const over = (fg, bg) => fg.rgb.map((v, i) => v * fg.alpha + bg.rgb[i] * (1 - fg.alpha));
const lum = (rgb) => { const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; const [r, g, b] = rgb.map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
const hsl = (rgb) => {
  const [r, g, b] = rgb.map((v) => v / 255);
  const max = Math.max(r, g, b); const min = Math.min(r, g, b); const d = max - min;
  const l = (max + min) / 2;
  const sat = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  let h = 0;
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: (h * 60 + 360) % 360, s: sat, l };
};
const labOf = (rgb) => {
  const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const [r, g, b] = rgb.map(lin);
  const x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047; const y = 0.2126 * r + 0.7152 * g + 0.0722 * b; const z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
};
const deltaE = (a, b) => Math.hypot(...labOf(a).map((v, i) => v - labOf(b)[i]));

// every level drawn once, in the page's detail pane (so it inherits its background): what the browser computes for each cell
function paletteProbe() {
  const detail = document.getElementById('detail');
  let probe = document.getElementById('palette-probe');
  if (!probe) { probe = document.createElement('div'); probe.id = 'palette-probe'; detail.appendChild(probe); }
  const cells = ['ok', 'warn', 'error', 'stale', 'unknown', null].map((level, at) => ({ level, from: at * 15, to: (at + 1) * 15, message: '' }));
  probe.innerHTML = window.IOHealth.html(cells, { kind: 'timeline' }) + window.IOHealth.legend();
  const read = (el) => { const cs = getComputedStyle(el); return { bg: cs.backgroundColor, image: cs.backgroundImage, shadow: cs.boxShadow }; };
  const chart = [...probe.querySelectorAll('.ioh > i')];
  const legend = [...probe.querySelectorAll('.ioh-legend i')];
  return { bg: getComputedStyle(detail).backgroundColor, chart: chart.map(read), legend: legend.map(read) };
}
const LEVEL_ORDER = ['ok', 'warn', 'error', 'stale', 'unknown', 'none'];

async function paletteChecks(browser, base) {
  // the one palette (health.js PALETTE): Atlassian Statuspage's yellow #f1c40f and red #e74c3c, its green #2ecc71 muted, no data empty,
  // two greys, nothing hatched; measured on real cells in the page, light and dark, against the detail pane's own background
  for (const scheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: scheme });
    const page = await context.newPage();
    await page.goto(`${base}/?sel=host`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title') && window.IOHealth, null, { timeout: 30000 });
    const label = `colours · ${scheme}`;
    check(`${label}: there is no palette picker (one convention, Statuspage's)`, await page.evaluate(() => !document.getElementById('health-palette') && !document.documentElement.dataset.hc), '');
    const m = await page.evaluate(paletteProbe);
    const bg = parseColor(m.bg);
    const cells = Object.fromEntries(m.chart.map((c, at) => [LEVEL_ORDER[at], { ...c, color: parseColor(c.bg) }]));
    const hatched = [...m.chart, ...m.legend].filter((c) => /gradient/.test(c.image)).length;
    check(`${label}: no hatching anywhere (no gradient on a cell or a legend key)`, hatched === 0, hatched);
    check(`${label}: no data is empty (transparent, no fill; a faint outline at most)`, cells.none.color.alpha === 0 && cells.none.image === 'none', { bg: cells.none.bg, image: cells.none.image });
    check(`${label}: WARN is Statuspage's yellow #f1c40f and ERROR its red #e74c3c, exactly`, cells.warn.bg === 'rgb(241, 196, 15)' && cells.error.bg === 'rgb(231, 76, 60)', { warn: cells.warn.bg, error: cells.error.bg });
    const warn = hsl(cells.warn.color.rgb); const error = hsl(cells.error.color.rgb); const ok = hsl(cells.ok.color.rgb);
    check(`${label}: WARN reads yellow (hue ${Math.round(warn.h)}), ERROR red (hue ${Math.round(error.h)})`, warn.h >= 40 && warn.h <= 56 && (error.h <= 12 || error.h >= 346), { warn: warn.h, error: error.h });
    check(`${label}: OK is Statuspage's green, muted (green hue, less saturated than #2ecc71)`, ok.h >= 130 && ok.h <= 160 && ok.s < 0.63 && ok.s > 0.2 && cells.ok.color.alpha === 1, { hue: Math.round(ok.h), s: +ok.s.toFixed(2), bg: cells.ok.bg });
    const flat = (level) => over(cells[level].color, bg);
    check(`${label}: stale and unknown are greys`, hsl(flat('stale')).s <= 0.12 && hsl(flat('unknown')).s <= 0.12, { stale: hsl(flat('stale')).s.toFixed(2), unknown: hsl(flat('unknown')).s.toFixed(2) });
    const apart = [['stale', 'unknown'], ['ok', 'stale'], ['ok', 'unknown']].map(([a, b]) => ({ pair: `${a}/${b}`, dE: +deltaE(flat(a), flat(b)).toFixed(1) }));
    check(`${label}: OK, stale and unknown are clearly different (delta E >= 12 each)`, apart.every((x) => x.dE >= 12), apart);
    const c = (level) => +ratio(flat(level), bg.rgb).toFixed(2);
    check(`${label}: contrast against the pane (${c('ok')}:1 ok, ${c('warn')}:1 warn, ${c('error')}:1 error, ${c('stale')}:1 stale, ${c('unknown')}:1 unknown)`,
      c('ok') >= 2 && c('warn') >= 1.5 && c('error') >= 3 && c('unknown') >= 3 && c('stale') >= 2.9, '');
    // one token set: the same OK colour in a spark, a lane, the status line and the entity page's own tab
    const places = {};
    for (const layout of ['d1a', 'd1c', 'd6']) {
      await page.selectOption('#detail-layout', layout);
      await page.waitForFunction((want) => S.layout === want && document.querySelector('#detail .ioh i.ok'), layout, { timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(500);
      places[layout] = await page.evaluate(() => [...document.querySelectorAll('#detail .ioh > i.ok:not(#palette-probe *)')].map((el) => getComputedStyle(el).backgroundColor).filter((v, i, all) => all.indexOf(v) === i));
    }
    check(`${label}: one token set draws every cell (d1a sparks and lanes, d1c lanes, the entity page's Diagnostics tab)`, Object.values(places).every((list) => list.length === 1 && list[0] === cells.ok.bg), places);
    if (scheme === 'light') {
      // mutations: each rule's gate goes red when its rule breaks
      await page.addStyleTag({ content: '.ioh i.stale { background: repeating-linear-gradient(135deg, #888 0 2px, transparent 2px 4px) !important; } .ioh i.none { background: #ccc !important; } :root { --hc-warn: #2255cc !important; }' });
      const bad = await page.evaluate(paletteProbe);
      check('mutation: a hatched stale, a filled no-data and a blue warn turn the hatch, no-data and exact-colour gates red',
        bad.chart.some((x) => /gradient/.test(x.image)) && parseColor(bad.chart[5].bg).alpha !== 0 && bad.chart[1].bg !== 'rgb(241, 196, 15)', { hatch: /gradient/.test(bad.chart[3].image), none: bad.chart[5].bg, warn: bad.chart[1].bg });
    }
    await context.close();
  }
}

async function main() {
  const { chromium } = require(PLAYWRIGHT);
  const plugins = pluginsDir();
  const [f1, f2] = await Promise.all([startServer('F1', plugins), startServer('F2', plugins)]);
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  try {
    // the Computer's 17 statuses need a moment: its follower starts after the host
    let host;
    for (let i = 0; i < 120; i += 1) {
      host = await getJson(f1.base, '/api/v1/node?path=host');
      if ((host.checks || []).length >= 17) break;
      await sleep(500);
    }
    check('F1 host: the Computer reports its 17 statuses', (host.checks || []).length === 17, (host.checks || []).length);
    await sleep(6000); // a few cells of history

    const targets = [
      { name: 'the Computer (17 statuses)', base: f1.base, sel: 'host', checks: 17 },
      { name: 'an fdcanusb (F2: comms unknown, port held)', base: f2.base, sel: 'host/usb1/port-1-3/usb-1-3', problem: 'unknown' },
      { name: 'a CAN board with a fault (F1: CAN 15 driver)', base: f1.base, sel: 'host/usb1/port-1-3/usb-1-3/can/15/driver', problem: 'error' },
      { name: 'a camera (F1: D405, moved since calibration)', base: f1.base, sel: 'host/usb6/port-6-1/usb-6-1', problem: 'warn' },
    ];
    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });

    // ---- the picker
    await page.goto(`${targets[0].base}/?sel=host`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
    const options = await page.$$eval('#detail-layout option', (list) => list.map((o) => o.value));
    check('picker: every variant is an option of the bottom-right Prototype select', VARIANTS.every((id) => options.includes(id)), options);
    check('picker: the select is the only switch (no URL flag)', !/layout|variant|d1/.test(new URL(page.url()).search), new URL(page.url()).search);

    const seen = new Map();
    for (const target of targets) {
      await page.goto(`${target.base}/?sel=${encodeURIComponent(target.sel)}`);
      await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
      const node = (await getJson(target.base, `/api/v1/node?path=${encodeURIComponent(target.sel)}`));
      const levels = node.checks.map((c) => c.effective_level);
      if (target.checks) check(`${target.name}: ${target.checks} diagnostics on the real entity`, node.checks.length === target.checks, node.checks.length);
      if (target.problem) check(`${target.name}: the real entity holds a ${target.problem}`, levels.includes(target.problem), levels);
      for (const id of VARIANTS) {
        const label = `${id} · ${target.name.split(' (')[0]}`;
        await page.selectOption('#detail-layout', id);
        await page.waitForFunction((want) => S.layout === want && document.querySelector(`[data-dx="${want}"] .ioh i`), id, { timeout: 20000 }).catch(() => {});
        await page.waitForTimeout(600);
        let m = await page.evaluate(probe);
        check(`${label}: the variant draws the Diagnostics section`, m.section && m.layout === id && m.title.startsWith('Diagnostics'), { layout: m.layout, title: m.title });
        if (!m.section) continue;
        check(`${label}: the section lists all ${node.checks.length} diagnostics`, m.title.endsWith(`· ${node.checks.length}`), m.title);
        // health only
        check(`${label}: health only (no value sparkline, no measurement, no chart but the cells)`, m.svgs === 0 && m.valueMarks === 0, { svgs: m.svgs, valueMarks: m.valueMarks });
        // single-line rows where the variant says so
        const single = (heights, what) => check(`${label}: ${what} are single-line`, heights.length > 0 && Math.max(...heights) < 40, { n: heights.length, max: Math.max(0, ...heights) });
        if (id === 'd1a') {
          single(m.a.rows, 'the table rows');
          check(`${label}: columns are Name | Message | Current | History, no cell wraps`, m.a.head.join() === 'Name,Message,Current,History' && /dx-n.*dx-m.*dx-s.*dx-h/.test(m.a.cols.join(' ')) && m.a.cut === 0, { head: m.a.head, cols: m.a.cols, cut: m.a.cut });
          check(`${label}: ${m.a.rows.length} rows = ${node.checks.length} diagnostics`, m.a.rows.length === node.checks.length, m.a.rows.length);
        }
        if (id === 'd1b') {
          if (levels.every((l) => l === 'ok')) single(m.okRows, 'the OK rows (open: nothing needs attention)');
          else {
            const okCount = levels.filter((l) => l === 'ok').length;
            if (okCount) {
              const opened = await page.evaluate(() => { const b = document.querySelector('[data-dx-toggle="ok"]'); if (b && b.getAttribute('aria-expanded') === 'false') { b.click(); return true; } return false; });
              await page.waitForTimeout(500);
              m = await page.evaluate(probe);
              single(m.okRows, `the ${okCount} OK rows (folded under one line, opened${opened ? ' by a click' : ''})`);
              await page.evaluate(() => { const b = document.querySelector('[data-dx-toggle="ok"]'); if (b && b.getAttribute('aria-expanded') === 'true') b.click(); });
            }
            check(`${label}: the problems are listed first, each with its what-to-check when it has one`, await page.evaluate(() => document.querySelectorAll('.db-p').length > 0), '');
          }
        }
        if (id === 'd1c') { single(m.lanes, 'the lanes'); check(`${label}: ${m.lanes.length} lanes = ${node.checks.length} diagnostics`, m.lanes.length === node.checks.length, m.lanes.length); }
        if (id === 'd1d') single(m.leaves.filter(Boolean), 'the tree leaves');
        // the status line drops down into one lane per diagnostic
        if (m.toggle) {
          await page.evaluate(() => { const b = document.querySelector('[data-dx-toggle="sum"]'); if (b.getAttribute('aria-expanded') === 'false') b.click(); });
          await page.waitForTimeout(500);
          const dropped = await page.evaluate(() => ({ lanes: document.querySelectorAll('.dx-drop .dx-lane').length, open: document.querySelector('[data-dx-toggle="sum"]').getAttribute('aria-expanded') }));
          check(`${label}: the status line drops down into one lane per diagnostic`, dropped.open === 'true' && dropped.lanes === node.checks.length, dropped);
          m = await page.evaluate(probe);
          await page.evaluate(() => { const b = document.querySelector('[data-dx-toggle="sum"]'); if (b.getAttribute('aria-expanded') === 'true') b.click(); });
          await page.waitForTimeout(300);
        } else if (id === 'd1c') {
          check(`${label}: the lanes are the section itself (no drop-down needed)`, m.lanes.length === node.checks.length, m.lanes.length);
        }
        // the chart rule
        const charts = m.charts;
        check(`${label}: draws at least one health chart`, charts.length >= 1, charts.length);
        const bad = (fn) => charts.filter((chart) => !fn(chart)).map((chart) => `${chart.kind}:${chart.count}`);
        check(`${label}: cells are separate (a gap of at least 1 px between neighbours)`, bad((c) => c.minGap === null || c.minGap >= 1).length === 0, bad((c) => c.minGap === null || c.minGap >= 1));
        check(`${label}: every bar is thin and fixed (at most 4.6 px wide, Statuspage's 3 px / 2 px)`, bad((c) => c.maxWidth <= 4.6).length === 0, bad((c) => c.maxWidth <= 4.6));
        check(`${label}: cells are taller than wide`, bad((c) => c.tall).length === 0, bad((c) => c.tall));
        const pageBad = m.pageCharts.filter((c) => !(c.minGap === null || c.minGap >= 1) || !c.tall).map((c) => `${c.kind}:${c.count}`);
        check(`${label}: every chart on the page (the children's mini bars too) is separate tall cells`, m.pageCharts.length >= m.charts.length && pageBad.length === 0, pageBad);
        check(`${label}: every cell's hover gives its time range and worst level`, bad((c) => c.titles).length === 0, bad((c) => c.titles));
        const hovered = await page.$('.sec.dx-diag .ioh > i');
        await hovered.hover();
        await page.waitForTimeout(120);
        const tip = await page.evaluate(() => { const el = document.querySelector('.ioh-tip'); return el && getComputedStyle(el).display !== 'none' ? el.textContent : ''; });
        check(`${label}: hovering a cell shows a tooltip with its time range and worst level`, /^\d\d:\d\d:\d\d–\d\d:\d\d:\d\d · (worst: \w+|no data)/.test(tip), tip);
        await page.mouse.move(2, 2);
        check(`${label}: a problem is amber or red (the palette rules are in the colour-scheme checks)`, (() => {
          const rgb = (s) => (s ? s.match(/[\d.]+/g).slice(0, 3).map(Number) : null);
          const warm = (s) => { const c = rgb(s); return !c || c[0] > c[2]; };
          return warm(m.warn) && warm(m.error);
        })(), { ok: m.ok, warn: m.warn, error: m.error });
        // problem statuses are worded
        if (target.problem) check(`${label}: the problem is named in the status column (${target.problem})`, m.pills.some((p) => p.toLowerCase() === target.problem) || id === 'd1e', m.pills.slice(0, 6));
        check(`${label}: no horizontal scroll at 1280x720 (page and detail pane)`, m.scroll.detail <= 0 && m.scroll.page <= 0, m.scroll);
        seen.set(`${id}:${target.sel}`, m);
      }
    }
    check('no page errors in any variant', errors.length === 0, errors.slice(0, 3));

    // ---- how many bars: as many 3 px bars, 2 px apart, as the width holds; the window sets what each covers (health.js count())
    const counts = await page.evaluate(() => {
      const H = window.IOHealth;
      return { w700: H.count(900, 700, 'timeline'), w700h: H.count(3600, 700, 'timeline'), w350: H.count(900, 350, 'timeline'), w1400: H.count(900, 1400, 'timeline'), spark: H.count(900, 96, 'spark'), bar: H.BAR_W, gap: H.GAP };
    });
    check('count(): bars are 3 px wide with a 2 px gap, so 700 px holds 140 (floor((700 + 2) / 5)), whatever the window', counts.bar === 3 && counts.gap === 2 && counts.w700 === 140 && counts.w700h === 140, counts);
    check('count(): a wider pane gives more bars (more resolution), a narrower one fewer; a 96 px spark gets 19', counts.w1400 > counts.w700 && counts.w700 > counts.w350 && counts.spark === 19, counts);
    // in the page: the bars really are thin and fixed, and the count follows the pane
    await page.selectOption('#detail-layout', 'd1a');
    await page.waitForFunction(() => document.querySelector('[data-dx="d1a"] .dx-bar .ioh i'), null, { timeout: 20000 });
    await page.waitForTimeout(800);
    const wide = await page.evaluate(() => { const c = document.querySelector('[data-dx="d1a"] .dx-bar .ioh'); const cells = [...c.children].map((i) => i.getBoundingClientRect()); return { n: cells.length, w: +cells[0].width.toFixed(2), h: cells[0].height, gap: +(cells[1].left - cells[0].right).toFixed(2), box: c.getBoundingClientRect().width }; });
    check('bars: about 3 px wide, 2 px apart, 24 px tall (Statuspage\'s proportions), n = what the width holds (within 2)', wide.w >= 2.9 && wide.w <= 3.6 && Math.abs(wide.gap - 2) < 0.3 && wide.h === 24 && Math.abs(wide.n - Math.floor((wide.box + 2) / 5)) <= 2, wide);
    await page.setViewportSize({ width: 900, height: 720 });
    await page.waitForTimeout(2500);
    const narrow = await page.evaluate(() => { const c = document.querySelector('[data-dx="d1a"] .dx-bar .ioh'); return { n: c ? c.children.length : 0 }; });
    check('bars: a narrower pane gets fewer bars, not wider ones', narrow.n > 0 && narrow.n < wide.n, { wide: wide.n, narrow: narrow.n });
    await page.setViewportSize(VIEWPORT);
    await page.waitForTimeout(1500);

    // ---- the History window (View options) decides the bin width: 15 min is 15 s a cell, 1 h is 60 s a cell
    await page.goto(`${targets[0].base}/?sel=host`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
    await page.selectOption('#detail-layout', 'd1a');
    await page.waitForFunction(() => document.querySelector('[data-dx="d1a"] .dx-bar .ioh i'), null, { timeout: 20000 });
    const seconds = (tipText) => { const t = tipText.match(/(\d\d):(\d\d):(\d\d)–(\d\d):(\d\d):(\d\d)/).slice(1).map(Number); return (t[3] * 3600 + t[4] * 60 + t[5]) - (t[0] * 3600 + t[1] * 60 + t[2]); };
    const binOf = () => page.evaluate(() => { const cells = document.querySelectorAll('[data-dx="d1a"] .dx-bar .ioh i'); return { n: cells.length, tip: cells[0].dataset.tip, window: S.settings && S.settings.history_window_s }; });
    // the page's own handler (View options' History select, hidden until opened), driven by its change event
    const setWindow = (value) => page.evaluate((v) => { const select = document.getElementById('history-window'); select.value = v; select.dispatchEvent(new Event('change', { bubbles: true })); }, value);
    const at15 = await binOf();
    await setWindow('3600');
    await page.waitForFunction(() => S.settings && S.settings.history_window_s === 3600, null, { timeout: 15000 });
    await page.waitForFunction(() => document.querySelector('[data-dx="d1a"] .dx-bar .ioh i') && /–/.test(document.querySelector('[data-dx="d1a"] .dx-bar .ioh i').dataset.tip), null, { timeout: 15000 });
    await page.waitForTimeout(3500);
    const at60 = await binOf();
    check('History window: the status line\'s cells cover 15 min (count x bin = the window)', at15.window === 900 && Math.abs(at15.n * seconds(at15.tip) - 900) <= at15.n, at15);
    check('History window: at 1 h the cells cover 1 h with a wider bin (the width follows the window)', at60.window === 3600 && Math.abs(at60.n * seconds(at60.tip) - 3600) <= at60.n && seconds(at60.tip) > seconds(at15.tip), { at15, at60 });
    await setWindow('900');

    // ---- row order: folders from dot-notation names, ★ pins on top, the sort in a folder (detail/roworder.js)
    const names = await page.evaluate(() => {
      const f = window.IORowOrder.folderPath;
      return { dotted: f('arm.servo.current'), dottedWho: f('arm.servo: current'), who: f('bam-GPU: CPU Information'), can: f('fdcanusb 272813EF CAN 1: firmware'), plain: f('comms'),
        version: f('D405: 5.15.1.55 installed'), canOnly: f('CAN 15: comms') };
    });
    check('names: dot notation parses into folders (arm.servo.current -> arm / servo / current)', JSON.stringify(names.dotted) === '{"path":["arm","servo"],"leaf":"current"}' && JSON.stringify(names.dottedWho) === '{"path":["arm","servo"],"leaf":"current"}', names);
    check('names: "who: what" -> who / what; "<thing> CAN n" splits in two; a version number is not a path',
      names.who.path.join() === 'bam-GPU' && names.who.leaf === 'CPU Information' && names.can.path.join('|') === 'fdcanusb 272813EF|CAN 1' && names.can.leaf === 'firmware' && names.plain.path.length === 0 && names.version.path.join() === 'D405', names);
    const orderKey = 'io-diagnostics.rows.diagnostics.host';
    for (const id of VARIANTS) {
      const fresh = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
      await fresh.addInitScript((layout) => { try { if (!localStorage.getItem('io-diagnostics.detail-layout')) localStorage.setItem('io-diagnostics.detail-layout', layout); } catch (_) { /* */ } }, id);
      const p2 = await fresh.newPage();
      const open2 = async () => {
        await p2.goto(`${targets[0].base}/?sel=host`);
        await p2.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
        await p2.waitForFunction((want) => S.layout === want && document.querySelector(`[data-dx="${want}"] .ioh i`), id, { timeout: 20000 });
        await p2.evaluate(() => {
          const ok = document.querySelector('[data-dx-toggle="ok"]');
          if (ok && ok.getAttribute('aria-expanded') === 'false') ok.click();
          const all = [...document.querySelectorAll('.dd-gh')].find((b) => /^All/.test(b.textContent.trim()));
          if (all && all.getAttribute('aria-expanded') === 'false') all.click();
        });
        await p2.waitForTimeout(500);
      };
      await open2();
      const folderBtn = p2.locator('.sec.dx-diag .ro-folder:not(.ro-pinned) > .ro-fold', { hasText: 'bam-GPU' }).last();
      const inFolder = () => folderBtn.evaluate((btn) => [...btn.parentElement.querySelectorAll('[data-ro-pin]')].map((el) => el.dataset.roPin));
      const label = `${id} · order`;
      const stars = () => p2.$$eval('.sec.dx-diag [data-ro-pin]', (list) => list.map((el) => el.dataset.roPin));
      const folded = await folderBtn.evaluate((f) => ({ text: f.textContent.trim(), open: f.getAttribute('aria-expanded'), id: f.dataset.dxFold, counted: Boolean(f.querySelector('.ro-n')) }));
      check(`${label}: "who:" names become a collapsible folder (bam-GPU), a rolled-up row shows no count`, folded.open === 'true' && folded.text === 'bam-GPU' && !folded.counted, folded);
      check(`${label}: the status line shows only the worst icon (or a quiet OK), never a count`, await p2.evaluate(() => { const el = document.querySelector('.sec.dx-diag .dx-count'); return Boolean(el) && !/\d/.test(el.textContent) && (el.textContent.trim() === 'OK' || Boolean(el.querySelector('.shape'))); }), '');
      // a one-row prefix is not a folder: "computer: plugin run" stays a row with its full name
      check(`${label}: a one-row prefix is not drawn as a folder`, await p2.evaluate(() => !document.querySelector('.ro-fold[data-dx-fold="computer"]')), '');
      const rowsIn = (await inFolder()).length;
      await folderBtn.click();
      await p2.waitForTimeout(300);
      const closedRows = (await inFolder()).length;
      await folderBtn.click();
      await p2.waitForTimeout(300);
      check(`${label}: the folder folds its rows away and back`, rowsIn >= 15 && closedRows === 0 && (await inFolder()).length === rowsIn, { rowsIn, closedRows });
      // pin
      const pinId = (await inFolder())[1];   // whichever row is second: the Computer's readings are live
      await p2.hover(`.sec.dx-diag [data-ro-pin="${pinId}"]`, { force: true }).catch(() => {});
      await p2.click(`.sec.dx-diag [data-ro-pin="${pinId}"]`, { force: true });
      await p2.waitForTimeout(400);
      const pinned = await p2.evaluate((want) => {
        const block = document.querySelector('.sec.dx-diag .ro-pinned');
        const folder = [...document.querySelectorAll('.sec.dx-diag .ro-folder:not(.ro-pinned) > .ro-fold')].find((b) => /bam-GPU/.test(b.textContent));
        const first = block && block.querySelector('[data-ro-pin]');
        return { block: Boolean(block), first: first && first.dataset.roPin, before: block && folder ? Boolean(block.compareDocumentPosition(folder) & Node.DOCUMENT_POSITION_FOLLOWING) : false, once: document.querySelectorAll(`.sec.dx-diag [data-ro-pin="${want}"]`).length };
      }, pinId);
      check(`${label}: ★ pins a row to the top (above the folders, once)`, pinned.block && pinned.first === pinId && pinned.before && pinned.once === 1, pinned);
      const stored = await p2.evaluate((key) => JSON.parse(localStorage.getItem(key) || '{}'), orderKey);
      check(`${label}: the pin is kept in localStorage per entity kind (${orderKey})`, Array.isArray(stored.pins) && stored.pins.includes(pinId), stored);
      await open2();
      check(`${label}: the pin survives a reload`, await p2.evaluate((want) => Boolean(document.querySelector(`.sec.dx-diag .ro-pinned [data-ro-pin="${want}"]`)), pinId), '');
      const other = await fresh.newPage();
      await other.goto(`${targets[3].base}/?sel=${encodeURIComponent(targets[3].sel)}`);
      await other.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
      await other.waitForFunction((want) => S.layout === want && document.querySelector('[data-dx]'), id, { timeout: 20000 }).catch(() => {});
      check(`${label}: another entity kind (a camera) has its own order: no pin there`, await other.evaluate(() => !document.querySelector('.sec.dx-diag .ro-pinned')), '');
      await other.close();
      // sort: A-Z inside the folder
      await p2.click('[data-ro-sort="name"]');
      await p2.waitForTimeout(400);
      const ids = (await inFolder()).filter((n) => n !== pinId);
      const sorted = ids.slice().sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));
      check(`${label}: A-Z sorts the rows inside the folder`, ids.length >= 14 && ids.join('|') === sorted.join('|'), ids.slice(0, 4));
      await p2.click('[data-ro-sort="severity"]');
      // manual drag (rows that draw as single-line items: d1a, d1c)
      if (id === 'd1a' || id === 'd1c') {
        await p2.click('[data-ro-sort="manual"]');
        await p2.waitForTimeout(400);
        const rowsSel = '.sec.dx-diag .ro-kids [data-ro-item]:not(.ro-folder)';
        const order = () => p2.$$eval(rowsSel, (list) => list.map((el) => el.dataset.roItem));
        await p2.evaluate(() => { const b = document.querySelector('[data-ro-sort="manual"]'); if (b.getAttribute('aria-pressed') !== 'true') b.click(); });
        const start = (await order()).filter((n) => n.startsWith('bam-GPU: '));
        const grabbed = start[1];
        await p2.locator(`${rowsSel}[data-ro-item="${grabbed}"]`).dragTo(p2.locator(`${rowsSel}[data-ro-item="${start[0]}"]`));
        await p2.waitForTimeout(500);
        const after = (await order()).filter((n) => n.startsWith('bam-GPU: '));
        check(`${label}: manual sort, dragging a row moves it (the second dropped on the first)`, after[0] === grabbed && after.length === start.length, { grabbed, first: after[0] });
        await open2();
        const kept = (await p2.$$eval(rowsSel, (list) => list.map((el) => el.dataset.roItem))).filter((n) => n.startsWith('bam-GPU: '));
        check(`${label}: the manual order survives a reload`, kept[0] === grabbed, kept.slice(0, 2));
      }
      check(`${label}: no horizontal scroll with folders, pins and the order bar`, await p2.evaluate(() => document.getElementById('detail').scrollWidth <= document.getElementById('detail').clientWidth), '');
      await fresh.close();
    }

    // ---- the five colour schemes
    await paletteChecks(browser, targets[0].base);

    // ---- mutations: the gates go red when the thing they guard breaks
    await page.goto(`${targets[2].base}/?sel=${encodeURIComponent(targets[2].sel)}`);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
    await page.selectOption('#detail-layout', 'd1a');
    await page.waitForFunction(() => document.querySelector('[data-dx="d1a"] .ioh i'), null, { timeout: 20000 });
    await page.addStyleTag({ content: '.ioh { gap: 0 !important; } .ioh > i { height: 2px !important; }' });
    await page.waitForTimeout(400);
    const flat = await page.evaluate(probe);
    check('mutation: closing the gap and flattening the cells turns the gap and aspect gates red',
      flat.charts.some((c) => c.minGap !== null && c.minGap < 1) && flat.charts.some((c) => !c.tall), flat.charts.map((c) => ({ gap: c.minGap, tall: c.tall })));
    await page.evaluate(() => {
      const spot = document.querySelector('.dx-diag .sec-b');
      spot.insertAdjacentHTML('beforeend', '<svg class="d1-sp" viewBox="0 0 10 10"><path class="ln" d="M0 0 L5 5"/></svg>');
    });
    const sparked = await page.evaluate(probe);
    check('mutation: a value sparkline in the section turns the health-only gate red', sparked.svgs > 0 && sparked.valueMarks > 0, { svgs: sparked.svgs, valueMarks: sparked.valueMarks });
    await context.close();

    // ---- a dark-mode pass: the same cells read in both modes (no page error, cells still separate)
    const dark = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'dark' });
    const darkPage = await dark.newPage();
    const darkErrors = [];
    darkPage.on('pageerror', (error) => darkErrors.push(error.message));
    await darkPage.goto(`${targets[0].base}/?sel=host`);
    await darkPage.waitForFunction(() => typeof S !== 'undefined' && S.built && document.querySelector('.d-title'), null, { timeout: 30000 });
    for (const id of VARIANTS) {
      await darkPage.selectOption('#detail-layout', id);
      await darkPage.waitForFunction((want) => S.layout === want && document.querySelector(`[data-dx="${want}"] .ioh i`), id, { timeout: 40000 }).catch(() => {});
      await darkPage.waitForTimeout(400);
      const m = await darkPage.evaluate(probe);
      check(`${id} · dark: draws the section with separate cells`, m.section && m.charts.length > 0 && m.charts.every((c) => c.minGap === null || c.minGap >= 1), { charts: m.charts.length });
    }
    check('dark: no page errors', darkErrors.length === 0, darkErrors.slice(0, 3));
    await dark.close();
  } finally {
    await browser.close();
    stop(f1.child);
    stop(f2.child);
  }
  console.log(`\n${tally.passed} passed, ${tally.failed} failed`);
  process.exit(tally.failed ? 1 : 0);
}

main().catch((error) => { console.error(error); process.exit(1); });
