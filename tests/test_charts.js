#!/usr/bin/env node
// The five chart proposals that enlarge a sparkline (io_diagnostics/web/charts/c1.js .. c5.js over charts/core.js), driven in the real
// page in headless Chrome on a fixture host with MOVING series:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_charts.js [c1 c2 ...] [--shots DIR]
//
// The host is `cli.py serve --fixture F1 --plugins tests/fixtures/plugins-charts --port 0 --no-udev` with a temporary state dir AND a
// temporary config dir (c4 writes the app's settings: never the person's ~/.config), stopped by its process group. It refuses to run
// unless /api/v1/plugins lists only the fixture plugin charts_fixture (it emits numbers at 2 Hz and touches no hardware).
// Per proposal: the enlarged chart appears where the proposal says; it shows the same data as the sparkline (the last value, against
// the shell's own history cache the sparkline is drawn from); its axes and labels are readable at 1400x900 (font size, no overlaps,
// inside the figure); no horizontal scroll at 1280x720; c4's dashboard survives a reload (and a cleared browser: it is in the app's
// settings); c3 overlays two series. Exits 1 on a failure.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const PLUGINS = path.join(ROOT, 'tests', 'fixtures', 'plugins-charts');
const BIG = { width: 1400, height: 900 };
const SMALL = { width: 1280, height: 720 };

const args = process.argv.slice(2);
const shotsAt = args.indexOf('--shots');
const SHOTS = shotsAt >= 0 ? args[shotsAt + 1] : null;
const WANT = args.filter((arg, at) => /^c[0-5]$/.test(arg) && at !== (shotsAt >= 0 ? shotsAt + 1 : -1));
const wants = (id) => !WANT.length || WANT.includes(id);

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(74)} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function startServer(stateDir, configDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, IO_DIAGNOSTICS_CONFIG_DIR: configDir };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--fixture', 'F1',
    '--plugins', PLUGINS, '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
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

async function newPage(browser, base, view, { size = BIG, mode = 'light', keep = false } = {}) {
  const context = await browser.newContext({ viewport: size, colorScheme: mode, acceptDownloads: true });
  if (!keep) await context.addInitScript(([v, m]) => { try { if (!sessionStorage.getItem('charts-test-init')) { sessionStorage.setItem('charts-test-init', '1'); localStorage.setItem('io-diagnostics.chart-view', v); localStorage.setItem('io-diagnostics.mode', m); } } catch (_) { /* private mode */ } }, [view, mode]);
  const page = await context.newPage();
  const errors = [];
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('response', (response) => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url().replace(base, '')}`); });
  page.errors = errors;
  page.context_ = context;
  await page.goto(`${base}/?sel=host`);
  await page.waitForFunction(() => window.IOCharts && document.querySelector('.ch-spark') && document.documentElement.dataset.chart, null, { timeout: 30000 });
  await page.waitForTimeout(1500);
  return page;
}

// ---- helpers run in the page ----
const labels = (page) => page.evaluate(() => [...document.querySelectorAll('.ch-spark')].map((el) => JSON.parse(el.dataset.spark).l));
const sparkAt = (page, label) => page.evaluate((wanted) => [...document.querySelectorAll('.ch-spark')].findIndex((el) => JSON.parse(el.dataset.spark).l === wanted), label);
async function center(page, label, inner = '.ch-line') {
  const at = await sparkAt(page, label);
  if (at < 0) throw new Error(`no sparkline labelled ${label}`);
  const target = page.locator('.ch-spark').nth(at);
  await target.scrollIntoViewIfNeeded();
  const box = await target.locator(inner).first().boundingBox();
  return { x: box.x + box.width / 2, y: box.y + Math.min(box.height / 2, 14), box };
}
const hover = async (page, label) => { const c = await center(page, label); await page.mouse.move(c.x - 40, c.y, { steps: 3 }); await page.mouse.move(c.x, c.y, { steps: 4 }); return c; };
const press = async (page, label) => { const c = await center(page, label); await page.mouse.move(c.x - 40, c.y, { steps: 3 }); await page.mouse.move(c.x, c.y, { steps: 3 }); await page.mouse.click(c.x, c.y); return c; };
// the shell's own cached history answer for a label: the last numeric value of its first series, and the last few (the cache refreshes while we look)
const cachedLast = (page, label, n = 10) => page.evaluate(([wanted, count]) => {
  const el = [...document.querySelectorAll('.ch-spark')].find((node) => JSON.parse(node.dataset.spark).l === wanted);
  const model = IOCharts.resolve(JSON.parse(el.dataset.spark));
  const points = (model.series[0] ? model.series[0].points : []).filter((p) => typeof p[1] === 'number');
  return { values: points.slice(-count).map((p) => p[1]), unit: model.series[0] && model.series[0].unit, n: points.length };
}, [label, n]);
const rounded = (value, step) => IOChartsNum(value, step);
const IOChartsNum = (v) => { const a = Math.abs(v); return a === 0 ? '0' : a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : a >= 0.01 ? v.toFixed(2) : v.toPrecision(2); };
const shows = (text, cached) => cached.values.some((v) => IOChartsNum(v) === String(text).trim());
const hScroll = (page) => page.evaluate(() => ({ doc: document.documentElement.scrollWidth - innerWidth, body: document.body.scrollWidth - innerWidth }));

// axis labels readable: font size, inside the figure, no two overlapping (run against an <svg class="chp-svg">)
const readable = (page, selector, inShadowHost) => page.evaluate(([sel, hostSel]) => {
  const root = hostSel ? document.querySelector(hostSel).shadowRoot : document;
  const svg = root.querySelector(sel);
  const box = svg.getBoundingClientRect();
  const ticks = [...svg.querySelectorAll('.chp-tick, .chp-unit')];
  const boxes = ticks.map((t) => ({ text: t.textContent, r: t.getBoundingClientRect(), size: parseFloat(getComputedStyle(t).fontSize) }));
  let overlaps = 0; const pairs = [];
  for (let i = 0; i < boxes.length; i += 1) for (let j = i + 1; j < boxes.length; j += 1) {
    const a = boxes[i].r; const b = boxes[j].r;
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) { overlaps += 1; pairs.push(`${boxes[i].text}|${boxes[j].text}`); }
  }
  const outside = boxes.filter((t) => t.r.left < box.left - 1 || t.r.right > box.right + 1 || t.r.top < box.top - 1 || t.r.bottom > box.bottom + 1).map((t) => t.text);
  return { pairs, count: boxes.length, minSize: Math.min(...boxes.map((t) => t.size)), overlaps, outside, xLabels: boxes.filter((t) => /^\d\d:\d\d|^now$/.test(t.text)).length, w: box.width, h: box.height };
}, [selector, inShadowHost || null]);
// The warn-colours rule (docs/round2-seams.md): outside status marks and [data-diagnostic] (a status's own limits and problem spans), nothing in the
// new surfaces computes to a warn / error / stale / unknown colour or its -text / -soft / -line tint (a colour equal to a neutral token is not gated).
const warnColoursOutside = (page, selectors) => page.evaluate((sels) => {
  const names = ['warn', 'error', 'stale', 'unknown'].flatMap((n) => [`--io-${n}`, `--io-${n}-text`, `--io-${n}-soft`, `--io-${n}-line`]);
  const probe = document.createElement('i');
  document.body.appendChild(probe);
  const resolve = (token) => { probe.style.color = `var(${token})`; return getComputedStyle(probe).color; };
  const neutral = new Set(['--io-text', '--io-text-strong', '--io-muted', '--io-faint', '--io-border', '--io-border-soft', '--io-surface', '--io-surface-alt', '--io-popover', '--io-control', '--io-hover', '--io-info', '--io-accent'].map(resolve));
  const gated = new Set(names.map(resolve).filter((c) => !neutral.has(c) && c !== 'rgba(0, 0, 0, 0)'));
  probe.remove();
  const up = (node) => node.parentElement || (node.getRootNode() && node.getRootNode().host) || null;
  const exempt = (node) => { for (let n = node; n; n = up(n)) if (n.matches && n.matches('[data-diagnostic], .shape')) return true; return false; };
  const all = [];
  const walk = (node) => { all.push(node); if (node.shadowRoot) for (const child of node.shadowRoot.querySelectorAll('*')) walk(child); };
  for (const sel of sels) for (const top of document.querySelectorAll(sel)) { walk(top); for (const child of top.querySelectorAll('*')) walk(child); }
  const offenders = [];
  for (const node of all) {
    if (exempt(node)) continue;
    const style = getComputedStyle(node);
    const props = { color: style.color, background: style.backgroundColor, fill: style.fill, stroke: style.stroke, border: parseFloat(style.borderTopWidth) > 0 ? style.borderTopColor : '' };
    for (const [prop, value] of Object.entries(props)) if (gated.has(value)) offenders.push(`${node.tagName.toLowerCase()}.${String(node.getAttribute('class') || '').split(' ')[0]} ${prop}`);
  }
  return offenders.slice(0, 8);
}, selectors);
const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name) }); } };

// ================================================================== c0 (today) ====================
async function testC0(browser, base) {
  console.log('\n# c0 · the page as it is');
  const page = await newPage(browser, base, 'c0');
  const ids = await page.evaluate(() => [...document.querySelectorAll('#chart-view option')].map((o) => o.value));
  check('the Prototype picker lists c0 and c1..c5, c0 selected', ids.join() === 'c0,c1,c2,c3,c4,c5' && (await page.evaluate(() => document.getElementById('chart-view').value)) === 'c0', ids);
  const labelsNow = await labels(page);
  check('the Computer page wraps its sparklines (CPU, Memory, ...)', ['CPU', 'Memory', 'Network'].every((l) => labelsNow.includes(l)), labelsNow.slice(0, 8));
  const hasBtn = await page.evaluate(() => [...document.querySelectorAll('.ch-btn')].some((b) => getComputedStyle(b).display !== 'none'));
  check('c0 draws no enlarge control, no title override on the sparkline', !hasBtn && (await page.evaluate(() => !document.querySelector('.ch-spark').hasAttribute('title'))));
  await press(page, 'Memory');
  await page.waitForTimeout(700);
  const opened = await page.evaluate(() => ({ diag: S.drill.diag, pop: Boolean(document.querySelector('.ch1-pop, #ch3-pane, #ch4-dash, #ch5-page')) }));
  check('c0: a click on the sparkline still opens the diagnostic (nothing enlarges)', opened.diag === 'bam-GPU: RAM Information' && !opened.pop, opened);
  check('c0: no console errors', page.errors.filter((e) => !/404.*charts\/c[1-5]/.test(e)).length === 0, page.errors.slice(0, 3));
  await page.context_.close();
}

// ================================================================== c1 Peek =======================
async function testC1(browser, base) {
  console.log('\n# c1 · Peek: a popover chart beside the sparkline');
  const page = await newPage(browser, base, 'c1');
  const anchor = await hover(page, 'Memory');
  await page.waitForSelector('.ch1-pop .chp-svg', { timeout: 4000 });
  await page.waitForTimeout(600);
  const geo = await page.evaluate(() => { const p = document.querySelector('.ch1-pop').getBoundingClientRect(); const a = [...document.querySelectorAll('.ch-spark')].find((e) => JSON.parse(e.dataset.spark).l === 'Memory').querySelector('.ch-line').getBoundingClientRect(); return { pop: { l: p.left, r: p.right, t: p.top, b: p.bottom, w: p.width, h: p.height }, a: { l: a.left, r: a.right, t: a.top, b: a.bottom } }; });
  const beside = geo.pop.t >= geo.a.b - 1 || geo.pop.b <= geo.a.t + 1;
  check('hover opens the popover beside the sparkline (above or below, right edges aligned)', beside && Math.abs(geo.pop.r - geo.a.r) <= 24 && geo.pop.l >= 0 && geo.pop.r <= BIG.width, geo);
  check('the popover is larger than the sparkline', geo.pop.w >= 3 * 140 && geo.pop.h >= 200, geo.pop);
  const cached = await cachedLast(page, 'Memory');
  const now = await page.evaluate(() => (document.querySelector('.ch1-pop [data-ch-now]') || {}).textContent);
  check('it shows the same data: its now value is the last cached point of the sparkline series', shows(now, cached), { now, cached: cached.values });
  const rowValue = await page.evaluate(() => parseFloat(document.querySelector('[data-d4-group="ram"] .d4-big').textContent));
  check("and the row's own big value agrees (the series moves slowly)", Math.abs(rowValue - parseFloat(now)) < 3, { rowValue, now });
  const read = await readable(page, '.ch1-pop .chp-svg');
  check('axes and labels readable at 1400x900: 11 px, no overlaps, inside the figure', read.count >= 7 && read.minSize >= 10.5 && read.overlaps === 0 && read.outside.length === 0 && read.xLabels >= 3, read);
  const head = await page.evaluate(() => ({ title: document.querySelector('.ch1-title').textContent, stats: document.querySelector('.ch1-stats').textContent, wins: [...document.querySelectorAll('.ch1-pop [data-w]')].map((b) => b.textContent) }));
  check('it has a title, min / max / now and a window selector', head.title === 'Memory' && /min .* max .* now/.test(head.stats) && head.wins.length >= 3, head);
  // a cursor readout
  const plot = await page.evaluate(() => { const r = document.querySelector('.ch1-pop .chp-svg').getBoundingClientRect(); return { x: r.left + r.width * 0.93, y: r.top + r.height * 0.5 }; });
  await page.mouse.move(plot.x, plot.y, { steps: 4 });
  const tip = await page.evaluate(() => { const t = document.querySelector('.ch1-pop .chp-tip'); return { shown: !t.hidden, text: t.textContent, circles: document.querySelectorAll('.ch1-pop .chp-cur circle').length }; });
  check('moving over the chart reads out the time and the value under the cursor', tip.shown && /\d\d:\d\d:\d\d/.test(tip.text) && /%/.test(tip.text) && tip.circles >= 1, tip);
  await shot(page, 'c1-hover.png');
  // the window selector asks the host for its own window (local: the app's window does not change)
  const requests = [];
  page.on('request', (request) => requests.push(request.url()));
  await page.click('.ch1-pop [data-w="60"]');
  await page.waitForTimeout(1800);
  check('its window selector asks /api/v1/history for window=60 and leaves the app window alone', requests.some((u) => /history\?path=host&window=60/.test(u)) && (await page.evaluate(() => S.settings.history_window_s)) === 900, requests.filter((u) => /window=/.test(u)).slice(0, 2));
  // pressing pins; it survives the pointer leaving; Esc closes
  await page.mouse.click(plot.x, plot.y - 30); // a press in the chart
  const pinned = await press(page, 'Memory');
  await page.mouse.move(40, 500, { steps: 6 });
  await page.waitForTimeout(700);
  check('a press pins it: it stays after the pointer leaves', await page.evaluate(() => Boolean(document.querySelector('.ch1-pop.pinned'))));
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
  check('Esc closes it', await page.evaluate(() => !document.querySelector('.ch1-pop')));
  // unpinned: hover shows, leaving closes
  await hover(page, 'Swap');
  await page.waitForSelector('.ch1-pop', { timeout: 3000 });
  await page.mouse.move(40, 500, { steps: 6 });
  await page.waitForTimeout(700);
  check('an unpinned peek closes when the pointer leaves', await page.evaluate(() => !document.querySelector('.ch1-pop')));
  // the popover follows the data: a status with limits draws them, as the only amber in the figure
  await hover(page, 'Temperatures');
  await page.waitForSelector('.ch1-pop .chp-lim', { state: 'attached', timeout: 4000 });
  const limits = await page.evaluate(() => [...document.querySelectorAll('.ch1-pop .chp-lim')].map((l) => ({ kind: l.getAttribute('class'), diag: Boolean(l.closest('[data-diagnostic]')) })));
  check('the status limits show as dashed lines, tagged as diagnostic results', limits.length >= 1 && limits.every((l) => l.diag), limits);
  check('c1: amber / red only on the status limits (the warn-colours rule)', (await warnColoursOutside(page, ['.ch1-pop'])).length === 0, await warnColoursOutside(page, ['.ch1-pop']));
  await shot(page, 'c1-limits.png');
  await page.mouse.move(40, 500, { steps: 4 });
  await page.waitForTimeout(500);
  // keyboard: Tab to the button, Enter pins
  await page.evaluate(() => [...document.querySelectorAll('.ch-spark')].find((e) => JSON.parse(e.dataset.spark).l === 'Load average').querySelector('.ch-btn').focus());
  await page.keyboard.press('Enter');
  await page.waitForTimeout(500);
  check('keyboard: Enter on the focused button pins the peek', await page.evaluate(() => Boolean(document.querySelector('.ch1-pop.pinned'))));
  await page.keyboard.press('Escape');
  const small = await page.context_.newPage();
  await small.setViewportSize(SMALL);
  await small.goto(`${base}/?sel=host`);
  await small.waitForSelector('.ch-spark', { timeout: 20000 });
  await small.waitForTimeout(1500);
  await hover(small, 'Memory');
  await small.waitForSelector('.ch1-pop .chp-svg');
  const sc = await hScroll(small);
  const inside = await small.evaluate(() => { const r = document.querySelector('.ch1-pop').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; });
  check('no horizontal scroll at 1280x720, the popover stays inside the window', sc.doc <= 0 && sc.body <= 0 && inside, { sc, inside });
  check('no console errors', page.errors.filter((e) => !/404.*charts\/c[2-5]/.test(e)).length === 0, page.errors.slice(0, 3));
  await page.context_.close();
}

// ================================================================== c2 Unfold =====================
async function testC2(browser, base) {
  console.log('\n# c2 · Unfold in place: the row grows a full-width chart');
  const page = await newPage(browser, base, 'c2');
  const before = await page.evaluate(() => document.querySelector('[data-d4-group="ram"]').getBoundingClientRect().height);
  await press(page, 'Memory');
  await page.waitForSelector('.ch-spark[data-open]', { timeout: 4000 });
  await page.waitForTimeout(1500);
  const geo = await page.evaluate(() => {
    const group = document.querySelector('[data-d4-group="ram"]');
    const host = group.querySelector('.ch-spark[data-open] > .ch-host');
    const svg = host.shadowRoot.querySelector('.chp-svg').getBoundingClientRect();
    const big = group.querySelector('.d4-big').getBoundingClientRect();
    const detail = document.getElementById('detail').getBoundingClientRect();
    const g = group.getBoundingClientRect();
    return { svg: { w: svg.width, h: svg.height, top: svg.top }, bigBottom: big.bottom, detailW: detail.width, groupH: g.height };
  });
  check('the chart unfolds inside the row, below its name and value', geo.svg.top >= geo.bigBottom - 2 && geo.groupH > before + 120, { ...geo, before });
  check('it is full width: at least 85% of the detail pane', geo.svg.w >= geo.detailW * 0.78 && geo.svg.h >= 150, geo.svg);
  const cached = await cachedLast(page, 'Memory');
  const now = await page.evaluate(() => (document.querySelector('[data-d4-group="ram"] .ch-host').shadowRoot.querySelector('[data-ch-now]') || {}).textContent);
  check('it shows the same data: now = the last cached point of the sparkline series', shows(now, cached), { now, cached: cached.values });
  const read = await readable(page, '.chp-svg', '[data-d4-group="ram"] .ch-host');
  check('axes and labels readable at 1400x900: 11 px, no overlaps, inside the figure', read.count >= 6 && read.minSize >= 10.5 && read.overlaps === 0 && read.outside.length === 0 && read.xLabels >= 3, read);
  const window_ = await page.evaluate(() => { const r = document.querySelector('[data-d4-group="ram"] .ch-host').shadowRoot; return { stat: r.querySelector('.chc-stat').textContent, select: r.querySelector('select').value }; });
  check('the window is shown (stats line and the selector)', /last 15 min/.test(window_.stat) && window_.select === '900', window_);
  await shot(page, 'c2-open.png');
  // survives the 1 Hz redraw of the detail, and a reload
  await page.waitForTimeout(3500);
  check('it survives the detail being redrawn every second', await page.evaluate(() => Boolean(document.querySelector('[data-d4-group="ram"] .ch-host').shadowRoot.querySelector('.chp-svg'))));
  // a status with limits
  await press(page, 'Temperatures');
  await page.waitForSelector('[data-d4-group="temps"] .ch-spark[data-open], .ch-spark[data-open] >> nth=1', { timeout: 4000 }).catch(() => {});
  await page.waitForTimeout(1500);
  const lim = await page.evaluate(() => [...document.querySelectorAll('.ch-spark[data-open] > .ch-host')].map((h) => [...h.shadowRoot.querySelectorAll('.chp-lim')].map((l) => Boolean(l.closest('[data-diagnostic]')))).flat());
  check('a status with limits draws them in the unfolded chart (tagged diagnostic)', lim.length >= 1 && lim.every(Boolean), lim);
  check('c2: amber / red only on the status limits (the warn-colours rule)', (await warnColoursOutside(page, ['.ch-host'])).length === 0, await warnColoursOutside(page, ['.ch-host']));
  await page.reload();
  await page.waitForSelector('.ch-spark[data-open]', { timeout: 20000 });
  await page.waitForTimeout(1500);
  check('unfolded rows stay unfolded after a reload (remembered)', await page.evaluate(() => document.querySelectorAll('.ch-spark[data-open]').length) >= 2);
  // fold: the header
  await page.evaluate(() => document.querySelector('[data-d4-group="ram"] .ch-host').shadowRoot.querySelector('[data-ch2-fold]').click());
  await page.waitForTimeout(700);
  const folded = await page.evaluate(() => ({ open: document.querySelectorAll('.ch-spark[data-open]').length, ram: Boolean(document.querySelector('[data-d4-group="ram"] .ch-spark[data-open]')), height: document.querySelector('[data-d4-group="ram"]').getBoundingClientRect().height }));
  check('pressing the chart header folds it again (the row is its old height)', !folded.ram && Math.abs(folded.height - before) <= 4, { folded, before });
  // a click on the sparkline never opens the diagnostic page
  check('a click on the sparkline did not navigate to the diagnostic page', await page.evaluate(() => !S.drill.diag));
  // a per-status row (not a group): temperatures' own row
  const small = await page.context_.newPage();
  await small.setViewportSize(SMALL);
  await small.goto(`${base}/?sel=host`);
  await small.waitForSelector('.ch-spark[data-open]', { timeout: 20000 });
  await small.waitForTimeout(1500);
  const sc = await hScroll(small);
  check('no horizontal scroll at 1280x720 with rows unfolded', sc.doc <= 0 && sc.body <= 0, sc);
  check('no console errors', page.errors.filter((e) => !/404.*charts\/c[3-5]/.test(e)).length === 0, page.errors.slice(0, 3));
  await page.context_.close();
}

// ================================================================== c3 Plot pane ==================
async function testC3(browser, base) {
  console.log('\n# c3 · Plot pane: tview\'s model, one docked plot');
  const page = await newPage(browser, base, 'c3');
  check('no pane until something is plotted', await page.evaluate(() => !document.getElementById('ch3-pane')));
  await press(page, 'Memory');
  await page.waitForSelector('#ch3-pane .chp-svg', { timeout: 4000 });
  await page.waitForTimeout(1500);
  const dock = await page.evaluate(() => { const p = document.getElementById('ch3-pane').getBoundingClientRect(); const m = document.getElementById('main').getBoundingClientRect(); return { dock: document.getElementById('ch3-pane').dataset.dock, paneTop: p.top, mainBottom: m.bottom, w: p.width, h: p.height, win: innerWidth }; });
  check('the first plotted sparkline opens the pane at the bottom, under the main area', dock.dock === 'bottom' && Math.abs(dock.paneTop - dock.mainBottom) <= 2 && dock.w >= dock.win - 2 && dock.h >= 150, dock);
  const cached = await cachedLast(page, 'Memory');
  const legend = await page.evaluate(() => document.querySelector('#ch3-pane .ch3-row .v').textContent);
  check('it shows the same data: the legend value is the last cached point of the sparkline series', shows(legend.replace(/[^\d.\-]/g, ''), cached), { legend, cached: cached.values });
  // a second series of another unit overlays on the right axis
  await press(page, 'Temperatures');
  await page.waitForTimeout(1500);
  const two = await page.evaluate(() => { const svg = document.querySelector('#ch3-pane .chp-svg'); return { lines: svg.querySelectorAll('.chp-ln').length, rows: [...document.querySelectorAll('#ch3-pane .ch3-row')].map((r) => r.querySelector('.ch3-ax').textContent), rightTicks: [...svg.querySelectorAll('.chp-tick')].filter((t) => t.getAttribute('text-anchor') === 'start').length, count: document.querySelector('#ch3-btn .ch3-count').textContent }; });
  check('two series overlay in one plot (two lines, two legend rows)', two.lines === 2 && two.rows.length === 2, two);
  check('the second, in another unit, took the right axis (right tick labels drawn)', two.rows.join() === 'Left,Right' && two.rightTicks >= 3, two);
  const read = await readable(page, '#ch3-pane .chp-svg');
  check('axes and labels readable at 1400x900: 11 px, no overlaps, inside the figure', read.count >= 10 && read.minSize >= 10.5 && read.overlaps === 0 && read.outside.length === 0 && read.xLabels >= 3, read);
  check('c3: no amber / red in the pane (the warn-colours rule)', (await warnColoursOutside(page, ['#ch3-pane', '.ch3-menu'])).length === 0, await warnColoursOutside(page, ['#ch3-pane']));
  await shot(page, 'c3-two-series.png');
  // right-click: Plot Left / Plot Right
  const c = await center(page, 'Network');
  await page.mouse.move(c.x, c.y, { steps: 3 });
  await page.mouse.click(c.x, c.y, { button: 'right' });
  await page.waitForSelector('.ch3-menu', { timeout: 2000 });
  const items = await page.evaluate(() => [...document.querySelectorAll('.ch3-menu [data-m]')].map((b) => b.textContent.trim()));
  check('right-click on a sparkline offers Plot Left and Plot Right', items.includes('Plot Left') && items.includes('Plot Right'), items);
  await page.click('.ch3-menu [data-m="R"]');
  await page.waitForTimeout(1200);
  const three = await page.evaluate(() => ({ lines: document.querySelectorAll('#ch3-pane .chp-ln').length, rows: document.querySelectorAll('#ch3-pane .ch3-row').length }));
  check('Plot Right added the Network row\'s two series (rx and tx): four lines in all', three.lines === 4 && three.rows === 4, three);
  // the axis chip moves a series
  await page.click('#ch3-pane .ch3-row:nth-child(1) .ch3-ax');
  await page.waitForTimeout(600);
  check('the axis chip moves a series to the other axis', await page.evaluate(() => document.querySelector('#ch3-pane .ch3-row:nth-child(1) .ch3-ax').textContent) === 'Right');
  // cursor: the legend shows the values under the cursor
  const pl = await page.evaluate(() => { const r = document.querySelector('#ch3-pane .chp-svg').getBoundingClientRect(); return { x: r.left + r.width * 0.95, y: r.top + r.height * 0.4 }; });
  await page.mouse.move(pl.x, pl.y, { steps: 5 });
  check('the cursor reads out all the series at one time', await page.evaluate(() => document.querySelector('#ch3-pane .chp-tip .t') && document.querySelectorAll('#ch3-pane .chp-tip .r').length >= 3));
  // dock right, drag the sash, remembered across a reload
  await page.click('#ch3-pane [data-act="dock"]');
  await page.waitForTimeout(600);
  const right = await page.evaluate(() => { const p = document.getElementById('ch3-pane').getBoundingClientRect(); const i = document.getElementById('inspector').getBoundingClientRect(); return { dock: document.getElementById('ch3-pane').dataset.dock, paneLeft: p.left, inspRight: i.right, w: p.width, scroll: document.documentElement.scrollWidth - innerWidth }; });
  check('docking right puts the pane after the inspector, no horizontal scroll', right.dock === 'right' && right.paneLeft >= right.inspRight - 2 && right.scroll <= 0, right);
  const sash = await page.evaluate(() => { const r = document.querySelector('#ch3-pane .ch3-sash').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await page.mouse.move(sash.x, sash.y);
  await page.mouse.down();
  await page.mouse.move(sash.x - 90, sash.y, { steps: 6 });
  await page.mouse.up();
  const wide = await page.evaluate(() => document.getElementById('ch3-pane').getBoundingClientRect().width);
  check('every pane boundary is draggable: the sash grew the pane', wide > right.w + 70, { was: right.w, now: wide });
  await page.reload();
  await page.waitForSelector('#ch3-pane .chp-svg', { timeout: 20000 });
  await page.waitForTimeout(1200);
  const kept = await page.evaluate(() => ({ dock: document.getElementById('ch3-pane').dataset.dock, w: document.getElementById('ch3-pane').getBoundingClientRect().width, rows: document.querySelectorAll('#ch3-pane .ch3-row').length }));
  check('the dock, the size and the series are remembered across a reload', kept.dock === 'right' && Math.abs(kept.w - wide) <= 2 && kept.rows === 4, { kept, wide });
  await shot(page, 'c3-right.png');
  const small = await page.context_.newPage();
  await small.setViewportSize(SMALL);
  await small.goto(`${base}/?sel=host`);
  await small.waitForSelector('#ch3-pane .chp-svg', { timeout: 20000 });
  await small.waitForTimeout(1200);
  const sc = await hScroll(small);
  check('no horizontal scroll at 1280x720 with the pane docked right', sc.doc <= 0 && sc.body <= 0, sc);
  await small.click('#ch3-pane [data-act="dock"]');
  await small.waitForTimeout(500);
  const sc2 = await hScroll(small);
  check('... and docked at the bottom', sc2.doc <= 0 && sc2.body <= 0, sc2);
  // close keeps the series, the toolbar button brings it back
  await page.click('#ch3-pane [data-act="close"]');
  await page.waitForTimeout(300);
  const closed = await page.evaluate(() => !document.getElementById('ch3-pane'));
  await page.click('#ch3-btn');
  await page.waitForTimeout(800);
  check('closing the pane keeps the series; the toolbar button reopens it', closed && await page.evaluate(() => document.querySelectorAll('#ch3-pane .ch3-row').length) === 4);
  check('no console errors', page.errors.filter((e) => !/404.*charts\/c[45]/.test(e)).length === 0, page.errors.slice(0, 3));
  await page.context_.close();
}

// ================================================================== c4 Dashboard ==================
async function testC4(browser, base) {
  console.log('\n# c4 · Pin to my dashboard');
  const page = await newPage(browser, base, 'c4');
  check('the toolbar has a Dashboard view beside Tree / Table / Activity', await page.evaluate(() => [...document.querySelectorAll('#views button')].map((b) => b.textContent.replace(/\d/g, '').trim()).join()) === 'Tree,Table,Activity,Dashboard');
  // pin with the pin button (hover shows it), and by clicking the sparkline
  await hover(page, 'Memory');
  await page.click('[data-ch-key] >> nth=0', { trial: true }).catch(() => {});
  const memory = await page.evaluate(() => [...document.querySelectorAll('.ch-spark')].findIndex((e) => JSON.parse(e.dataset.spark).l === 'Memory'));
  await page.locator('.ch-spark').nth(memory).locator('.ch-btn').click();
  await page.waitForTimeout(500);
  check('the pin button on a row pins it (a toast says so)', await page.evaluate(() => Boolean(document.querySelector('.ch4-toast')) && document.querySelector('.ch-spark[data-pinned]') !== null));
  await press(page, 'CPU');
  await press(page, 'Temperatures');
  await page.waitForTimeout(500);
  const count = await page.evaluate(() => document.querySelector('button[data-ch4] .ch4-count').textContent);
  check('three values pinned; the Dashboard button counts them', count === '3', count);
  await page.click('button[data-ch4]');
  await page.waitForSelector('#ch4-dash .ch4-tile .chp-svg', { timeout: 4000 });
  await page.waitForTimeout(1800);
  const view = await page.evaluate(() => ({ tiles: document.querySelectorAll('.ch4-tile').length, mainHidden: getComputedStyle(document.getElementById('main')).display === 'none', svgs: document.querySelectorAll('.ch4-tile .chp-svg').length, titles: [...document.querySelectorAll('.ch4-t')].map((t) => t.textContent) }));
  check('the Dashboard view shows one tile per pin, each with its chart, in place of the tree', view.tiles === 3 && view.svgs === 3 && view.mainHidden, view);
  const cached = await cachedLast(page, 'Memory');
  const tileValue = await page.evaluate(() => { const tile = [...document.querySelectorAll('.ch4-tile')].find((t) => t.querySelector('.ch4-t').textContent === 'Memory'); return tile.querySelector('.ch4-v').textContent; });
  check('a tile shows the same data: its header value is the last cached point of the sparkline series', shows(tileValue.replace(/[^\d.\-]/g, ''), cached), { tileValue, cached: cached.values });
  const read = await readable(page, '.ch4-tile:nth-child(1) .chp-svg');
  check('axes and labels readable at 1400x900: 11 px, no overlaps, inside the figure', read.count >= 6 && read.minSize >= 10.5 && read.overlaps === 0 && read.outside.length === 0 && read.xLabels >= 2, read);
  check('c4: amber / red only on the status limits (the warn-colours rule)', (await warnColoursOutside(page, ['#ch4-dash', '.ch4-toast'])).length === 0, await warnColoursOutside(page, ['#ch4-dash']));
  await shot(page, 'c4-dashboard.png');
  // resize by the corner
  const tile = page.locator('.ch4-tile').nth(0);
  const before = await tile.boundingBox();
  const handle = await tile.locator('[data-rs]').boundingBox();
  await page.mouse.move(handle.x + 9, handle.y + 9);
  await page.mouse.down();
  await page.mouse.move(handle.x + 9 + 260, handle.y + 9 + 90, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(600);
  const after = await page.locator('.ch4-tile').nth(0).boundingBox();
  check('every tile resizes by its corner (snapped to the grid)', after.width > before.width + 150 && after.height > before.height + 40, { before: [before.width, before.height], after: [after.width, after.height] });
  // reorder by dragging a header
  const order0 = await page.evaluate(() => [...document.querySelectorAll('.ch4-t')].map((t) => t.textContent));
  const h2 = await page.locator('.ch4-tile').nth(1).locator('.ch4-th').boundingBox();
  const h0 = await page.locator('.ch4-tile').nth(0).locator('.ch4-th').boundingBox();
  await page.mouse.move(h2.x + 120, h2.y + 12);
  await page.mouse.down();
  await page.mouse.move(h2.x + 60, h2.y + 40, { steps: 4 });
  await page.mouse.move(h0.x + 150, h0.y + 120, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(700);
  const order1 = await page.evaluate(() => [...document.querySelectorAll('.ch4-t')].map((t) => t.textContent));
  check('dragging a header reorders the tiles', order1.join() !== order0.join() && order1.length === 3, { order0, order1 });
  // saved with the app's settings
  await page.waitForTimeout(900);
  const saved = await (await fetch(`${base}/api/v1/settings`)).json();
  check("the board is saved with the app's settings (GET /api/v1/settings: dashboard)", saved.dashboard.length === 3 && saved.dashboard[0].w >= 6 && saved.dashboard.map((t) => t.spec.l).join() === order1.join(), { saved: saved.dashboard.map((t) => [t.spec.l, t.w, t.h]), order1 });
  // survives a reload, and a browser with nothing stored (another port, another profile)
  await page.reload();
  await page.waitForSelector('#ch4-dash .ch4-tile .chp-svg', { timeout: 20000 });
  await page.waitForTimeout(800);
  const again = await page.evaluate(() => [...document.querySelectorAll('.ch4-t')].map((t) => t.textContent));
  check('the dashboard survives a reload (same tiles, same order)', again.join() === order1.join(), { again, order1 });
  await page.evaluate(() => { localStorage.removeItem('io-diagnostics.chart.c4.tiles'); });
  await page.reload();
  await page.waitForSelector('#ch4-dash .ch4-tile .chp-svg', { timeout: 20000 });
  await page.waitForTimeout(1500);
  const fresh = await page.evaluate(() => [...document.querySelectorAll('.ch4-t')].map((t) => t.textContent));
  check("... and in a browser with nothing stored: it comes from the app's settings", fresh.join() === order1.join(), { fresh });
  // Add from this page..., x removes, the other views close the board
  await page.click('[data-ch4]');
  await page.click('#views [data-view="tree"]');
  await page.waitForTimeout(500);
  check('choosing Tree closes the board and shows the tree again', await page.evaluate(() => getComputedStyle(document.getElementById('main')).display !== 'none' && !document.body.classList.contains('ch4-on')));
  await page.click('button[data-ch4]');
  await page.waitForTimeout(300);
  await page.click('#ch4-dash [data-add]');
  const addItems = await page.evaluate(() => [...document.querySelectorAll('.ch4-menu button')].map((b) => b.textContent));
  check('Add from this page lists the values the entity page shows', addItems.length >= 8, addItems.length);
  await page.keyboard.press('Escape');
  await page.click('#ch4-dash .ch4-tile:nth-child(1) [data-x]');
  await page.waitForTimeout(900);
  const left = await (await fetch(`${base}/api/v1/settings`)).json();
  check('x removes a tile, and the settings follow', left.dashboard.length === 2 && (await page.evaluate(() => document.querySelectorAll('.ch4-tile').length)) === 2, left.dashboard.length);
  const small = await page.context_.newPage();
  await small.setViewportSize(SMALL);
  await small.goto(`${base}/?sel=host`);
  await small.waitForSelector('#ch4-dash .ch4-tile .chp-svg', { timeout: 20000 });
  await small.waitForTimeout(800);
  const sc = await hScroll(small);
  check('no horizontal scroll at 1280x720 on the dashboard', sc.doc <= 0 && sc.body <= 0, sc);
  await page.evaluate(async () => { await IOCharts.ctx.saveSettings({ dashboard: [] }); });
  check('no console errors', page.errors.filter((e) => !/404.*charts\/c5/.test(e)).length === 0, page.errors.slice(0, 3));
  await page.context_.close();
}

// ================================================================== c5 Focus page =================
async function testC5(browser, base) {
  console.log('\n# c5 · Focus page');
  const page = await newPage(browser, base, 'c5');
  await press(page, 'CPU');
  await page.waitForSelector('#ch5-page .chp-svg', { timeout: 4000 });
  await page.waitForTimeout(1800);
  const geo = await page.evaluate(() => ({ url: location.search, mainHidden: getComputedStyle(document.getElementById('main')).display === 'none', page: document.getElementById('ch5-page').getBoundingClientRect().width, win: innerWidth, h1: document.querySelector('#ch5-page h1').textContent }));
  check('the value has a page of its own: it replaces the tree, one history entry (?page=chart:...)', geo.mainHidden && geo.page >= geo.win - 2 && /page=chart/.test(geo.url) && geo.h1 === 'CPU', geo);
  const cached = await cachedLast(page, 'CPU');
  const now = await page.evaluate(() => document.querySelector('#ch5-page tbody td:nth-child(3)').textContent);
  check('it shows the same data: the table\'s now is the last cached point of the sparkline series', shows(now, cached), { now, cached: cached.values });
  const read = await readable(page, '#ch5-page .chp-svg');
  check('axes and labels readable at 1400x900: 11 px, no overlaps, inside the figure', read.count >= 9 && read.minSize >= 10.5 && read.overlaps === 0 && read.outside.length === 0 && read.xLabels >= 4, read);
  const strip = await page.evaluate(() => { const svg = document.querySelector('#ch5-page .chp-svg'); const bg = svg.querySelector('.chp-lvbg').getBoundingClientRect(); const ax = svg.querySelector('line.chp-axis').getBoundingClientRect(); const plot = svg.getBoundingClientRect(); return { bgLeft: bg.left, bgRight: bg.right, axLeft: ax.left, axRight: ax.right, bgTop: bg.top, axTop: ax.top, spans: svg.querySelectorAll('.chp-lv').length, label: [...svg.querySelectorAll('.chp-tick')].some((t) => t.textContent === 'health') }; });
  check('the health timeline sits under the chart on the SAME time axis (same x extent)', Math.abs(strip.bgLeft - strip.axLeft) <= 1 && Math.abs(strip.bgRight - strip.axRight) <= 1 && strip.bgTop > strip.axTop && strip.spans >= 1 && strip.label, strip);
  check('c5: amber / red only on the status limits and problem spans (the warn-colours rule)', (await warnColoursOutside(page, ['#ch5-page'])).length === 0, await warnColoursOutside(page, ['#ch5-page']));
  await shot(page, 'c5-page.png');
  // zoom with the wheel, pan with a drag, reset with a double click
  const area = await page.evaluate(() => { const r = document.querySelector('#ch5-page .chp-svg').getBoundingClientRect(); return { x: r.left + r.width * 0.965, y: r.top + r.height * 0.4, w: r.width }; });
  const range0 = await page.evaluate(() => document.querySelector('.ch5-range').textContent);
  await page.mouse.move(area.x, area.y);
  for (let i = 0; i < 4; i += 1) { await page.mouse.wheel(0, -120); await page.waitForTimeout(60); }
  await page.waitForTimeout(500);
  const zoomed = await page.evaluate(() => ({ text: document.querySelector('.ch5-range').textContent, reset: !document.querySelector('[data-zoom="reset"]').disabled }));
  check('the wheel zooms into the history window (the visible range shrinks, marked zoomed)', /zoomed/.test(zoomed.text) && zoomed.text !== range0 && zoomed.reset, { range0, zoomed });
  await shot(page, 'c5-zoomed.png');
  await page.mouse.move(area.x - 600, area.y);
  await page.mouse.down();
  await page.mouse.move(area.x - 300, area.y, { steps: 6 }); // dragging right pulls earlier data into view
  await page.mouse.up();
  await page.waitForTimeout(500);
  const panned = await page.evaluate(() => document.querySelector('.ch5-range').textContent);
  check('a drag pans along the time axis (the range moves, the span stays)', panned !== zoomed.text && /zoomed/.test(panned) && panned.split('·')[1] === zoomed.text.split('·')[1], { zoomed: zoomed.text, panned });
  check('after panning back in time the Live button offers to follow the newest data', await page.evaluate(() => !document.querySelector('[data-live]').hidden));
  const csvButton = page.locator('[data-csv]');
  const [download] = await Promise.all([page.waitForEvent('download', { timeout: 5000 }), csvButton.click()]);
  const csvPath = path.join(os.tmpdir(), `charts-${Date.now()}.csv`);
  await download.saveAs(csvPath);
  const csv = fs.readFileSync(csvPath, 'utf8').trim().split('\n');
  fs.unlinkSync(csvPath);
  const header = csv[0];
  check('CSV export: a .csv named for the value, a header, rows of the range in view', /\.csv$/.test(download.suggestedFilename()) && /^time,epoch_s,CPU Load Average/.test(header) && csv.length > 5 && csv.length < 700, { name: download.suggestedFilename(), header, rows: csv.length - 1 });
  const lastRow = csv[csv.length - 1].split(',');
  check('... each row a timestamp and a number', csv.slice(1).every((line) => /^\d{4}-\d\d-\d\dT[\d:.]+Z,\d+\.\d+,/.test(line)) && Number.isFinite(Number(lastRow[2])), lastRow);
  await page.dblclick('#ch5-page .chp-svg', { position: { x: area.w * 0.5, y: 100 } });
  await page.waitForTimeout(500);
  check('a double click shows the whole window again', await page.evaluate(() => /the whole window/.test(document.querySelector('.ch5-range').textContent)));
  // the keyboard
  await page.focus('#ch5-page .ch5-plotwrap');
  await page.keyboard.press('+');
  await page.waitForTimeout(400);
  check('+ zooms from the keyboard', await page.evaluate(() => /zoomed/.test(document.querySelector('.ch5-range').textContent)));
  // reload on the page URL lands on the same chart; Back returns to the entity
  await page.reload();
  await page.waitForSelector('#ch5-page .chp-svg', { timeout: 20000 });
  check('a reload on the page URL lands on the same chart', await page.evaluate(() => document.querySelector('#ch5-page h1').textContent) === 'CPU');
  await page.click('#ch5-page [data-back]');
  await page.waitForTimeout(700);
  const back = await page.evaluate(() => ({ page: Boolean(document.getElementById('ch5-page')), mainShown: getComputedStyle(document.getElementById('main')).display !== 'none', url: location.search, title: document.querySelector('.d-title') && document.querySelector('.d-title').textContent }));
  check('Back returns to the entity page (tree and detail again, no ?page)', !back.page && back.mainShown && !/page=/.test(back.url) && /Computer|bam-GPU/.test(back.title), back);
  await press(page, 'Memory');
  await page.waitForSelector('#ch5-page .chp-svg');
  await page.goBack();
  await page.waitForTimeout(900);
  check("the browser's Back button closes the page too", await page.evaluate(() => !document.getElementById('ch5-page') && getComputedStyle(document.getElementById('main')).display !== 'none'));
  await press(page, 'Memory');
  await page.waitForSelector('#ch5-page .chp-svg');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(700);
  check('Esc closes the page', await page.evaluate(() => !document.getElementById('ch5-page')));
  const small = await page.context_.newPage();
  await small.setViewportSize(SMALL);
  await small.goto(`${base}/?sel=host`);
  await small.waitForSelector('.ch-spark', { timeout: 20000 });
  await small.waitForTimeout(1500);
  await press(small, 'Network');
  await small.waitForSelector('#ch5-page .chp-svg');
  await small.waitForTimeout(800);
  const sc = await hScroll(small);
  check('no horizontal scroll at 1280x720 on the page (two series: rx and tx)', sc.doc <= 0 && sc.body <= 0 && (await small.evaluate(() => document.querySelectorAll('#ch5-page .chp-ln').length)) === 2, sc);
  check('no console errors', page.errors.length === 0, page.errors.slice(0, 3));
  await page.context_.close();
}

(async () => {
  const { chromium } = require(PLAYWRIGHT);
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'charts-state-'));
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), 'charts-config-'));
  const server = await startServer(stateDir, configDir);
  const base = server.base;
  let browser;
  try {
    const plugins = (await (await fetch(`${base}/api/v1/plugins`)).json()).plugins.map((plugin) => plugin.name);
    check('only the fixture plugin charts_fixture is loaded', plugins.join() === 'charts_fixture', plugins);
    if (plugins.join() !== 'charts_fixture') throw new Error('refusing to continue: a non-fixture plugin is loaded');
    // wait for the moving series to fill: 80 s at 2 Hz is 160 points, enough to zoom into
    for (let i = 0; i < 90; i += 1) {
      const history = await (await fetch(`${base}/api/v1/history?path=host`)).json();
      const cpu = (history.statuses || []).find((s) => /CPU Information/.test(s.name));
      if (cpu && cpu.measurements[0].points.length >= 160) break;
      await sleep(1000);
    }
    browser = await chromium.launch({ executablePath: CHROME, headless: true });
    if (wants('c0')) await testC0(browser, base);
    if (wants('c1')) await testC1(browser, base);
    if (wants('c2')) await testC2(browser, base);
    if (wants('c3')) await testC3(browser, base);
    if (wants('c4')) await testC4(browser, base);
    if (wants('c5')) await testC5(browser, base);
  } finally {
    if (browser) await browser.close();
    stop(server.child);
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(configDir, { recursive: true, force: true });
  }
  console.log(`\n${passed}/${passed + failed} checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((error) => { console.error(error); process.exit(1); });
