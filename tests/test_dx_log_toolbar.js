#!/usr/bin/env node
// Acceptance for the RobotDx page's Log toolbar: five one-line proposals (Zach, 2026-10-29: "If you are using data-ui then the ways
// of doing this are already quite supported. for example we don't want to tool bar to split onto two lines"). The classic Log tab
// wraps onto two lines (count, the level segment, Raw, search, Live, then "Log of CAN 13 + children / this only / + path to host");
// on the data-ui page each proposal is data-ui's one-row bar (CONTRACT §15: toolbar="bar", its presets and attributes) with the
// Log's level, scope and raw lines drawn as data-ui's own views, filters, query tokens or facets:
//
//   overflow  1 · Overflow menu          Shopify's preset (views="dropdown" promote=""): the level is the view chip, the scope and the
//                                        raw lines are filter clauses kept in Display, the bar's one menu
//   chip      2 · Scope as a chip        the scope is the view chip; the level and the raw lines are typed in the query box (level:>=2,
//                                        -channel:raw: the classic search's own words, with data-ui's hints)
//   search    3 · Search on focus        the level chip, icons only, and the search folded behind its icon until it is used
//   crumb     4 · Scope in the breadcrumb the scope picked in the entity's breadcrumb, the level as tabs
//   facet     5 · Level as a facet       the scope as tabs (the classic chip's segment), the level and the raw lines as facets in the
//                                        dashboard's rail
//
// Live is the page's one LIVE in every proposal (the timeline and the status bar: Rerun's single way back), the count data-ui's
// status line under the table. The raw lines are kept inside the host's window (PEP 0001 §16.5); that rule, and what the scope, the
// level and the raw-lines controls keep, are measured on the test-only stub (bench/robotdx_stub.py --raw --raw-from --raw-old), whose
// entity has raw lines from a boot 12 h back and, a little later, inside the window: F1 has none inside its window.
//
// A temporary bottom-right picker switches them live and remembers the pick (variant 1 by default). Written before the code; every
// claim measured in the REAL page on a fixture host (getBoundingClientRect, computed style, the table's own rows):
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_dx_log_toolbar.js
//
// Exits 1 on any failure.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, ADAPTER } = H;

const DX = '/dx/';
const BROWSER_ARGS = ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows'];
const VARIANTS = ['overflow', 'chip', 'search', 'crumb', 'facet'];
const FLOORS = ['Debug+', 'Info+', 'Warn+', 'Error+'];
const SEV = { debug: 0, info: 1, warn: 2, error: 3, fatal: 3 };

const ready = (page) => page.waitForFunction(() => window.robotdx && window.robotdx.clock && document.querySelector('#dx-main'), null, { timeout: 30000 });
// the Log tab drawn in this variant: its table has data-ui's bar and the detail says which proposal it is
const logShown = (page, v) => page.waitForFunction((x) => {
  const d = document.querySelector('#dx-detail');
  const t = document.querySelector('#dx-detail-log');
  return d && d.dataset.logVariant === x && t && t.getAttribute('toolbar') === 'bar' && t.querySelector(':scope > .dv-bar') && t.model ? true : null;
}, v, { timeout: 15000 }).then(() => true, () => false);
const rows = (page) => page.evaluate(() => { const t = document.querySelector('#dx-detail-log'); return t && t.model ? t.model.rows.map((r) => ({ path: r.path, severity: r.severity, channel: r.channel })) : null; });
// one line: every box of the bar (its views, its query box, each tool) has the same top within 2 px, the bar no taller than one
// row, and nothing under it (data-ui's pill row is a second row by design: it must stay empty)
const ONE_LINE = () => {
  const t = document.querySelector('#dx-detail-log');
  const bar = t && t.querySelector(':scope > .dv-bar');
  if (!bar) return null;
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden'; };
  const boxes = [...bar.querySelectorAll(':scope > .dv-bar-views > *, :scope > .dv-bar-query > *, :scope > .dv-bar-tools > *, :scope > .dv-bar-views [data-bar=tab]')].filter(vis)
    .map((el) => { const r = el.getBoundingClientRect(); return { what: el.dataset.bar || el.className.split(' ')[0] || el.tagName, top: Math.round(r.top * 10) / 10, h: Math.round(r.height), left: Math.round(r.left), right: Math.round(r.right) }; });
  const tops = boxes.map((b) => b.top);
  const sub = t.querySelector(':scope > .dv-subbar');
  const b = bar.getBoundingClientRect(), tb = t.getBoundingClientRect();
  return { boxes, spread: tops.length ? Math.max(...tops) - Math.min(...tops) : null, barH: Math.round(b.height), sub: sub && vis(sub) ? Math.round(sub.getBoundingClientRect().height) : 0,
    inside: boxes.every((x) => x.left >= Math.floor(tb.left) - 1 && x.right <= Math.ceil(tb.right) + 1), scroll: document.scrollingElement.scrollWidth, width: innerWidth };
};
const oneLine = (page) => page.evaluate(`(${ONE_LINE})()`);
const pick = async (page, v) => { await page.selectOption('#dx-log-variant', v); return logShown(page, v); };
// the test-only stub (bench/robotdx_stub.py: synthetic State and raw lines, no plugin, no device) on a free port of 5600-5699
const STUB = H.path.join(H.ROOT, 'bench', 'robotdx_stub.py');
async function freePort(first = 5600, last = 5699) {
  const net = require('net');
  for (let port = first; port <= last; port += 1) {
    const ok = await new Promise((res) => { const s = net.createServer(); s.once('error', () => res(false)); s.listen(port, '127.0.0.1', () => s.close(() => res(true))); });
    if (ok) return port;
  }
  throw new Error('no free port in 5600-5699');
}
async function startStub(args) {
  const port = await freePort();
  const child = require('child_process').spawn('/usr/bin/python3', [STUB, '--port', String(port), ...args], { cwd: H.ROOT, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let out = '';
    const timer = setTimeout(() => { H.stopServer(child); reject(new Error(`the stub did not start: ${out}`)); }, 20000);
    const on = (chunk) => { out += chunk; const m = out.match(/bench stub on (http:\/\/\S+?)\/ /); if (m) { clearTimeout(timer); resolve({ child, base: m[1] }); } };
    child.stdout.on('data', on);
    child.stderr.on('data', on);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`the stub exited (${code}): ${out}`)); });
  });
}

(async () => {
  let host;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: BROWSER_ARGS });
  const errors = [];
  try {
    host = await H.fixtureHost('F1');
    const { base } = host;

    // ---- the picker: bottom right, five proposals, variant 1 by default -------------------------------------------------------
    const ctx = await H.newContext(browser, 'light');
    const page = await ctx.newPage();
    errors.push(H.watchErrors(page, base));
    await page.goto(`${base}${DX}?go=explore&sel=host&page=log`);
    await ready(page);
    const picker = await page.evaluate(() => {
      const sel = document.querySelector('#dx-log-variant');
      if (!sel) return null;
      const r = sel.getBoundingClientRect();
      return { options: [...sel.options].map((o) => o.value), labels: [...sel.options].map((o) => o.textContent.trim()), value: sel.value, right: innerWidth - r.right, bottom: innerHeight - r.bottom, w: r.width };
    });
    check('a bottom-right picker offers the five Log toolbars, variant 1 (Overflow menu) by default',
      picker && picker.options.join() === VARIANTS.join() && picker.value === 'overflow' && picker.right <= 320 && picker.bottom <= 48 && picker.w > 0, picker);
    check('the Log tab draws variant 1 on data-ui\'s bar (toolbar="bar")', await logShown(page, 'overflow'));

    // ---- each proposal: what makes it itself, measured; its level control narrows the rows -------------------------------------
    // 1 · overflow: the view chip is the level, nothing promoted, Display the bar's one menu
    const v1 = await page.evaluate(() => {
      const t = document.querySelector('#dx-detail-log');
      const chip = t.querySelector('.dv-bar-views [data-bar=views]');
      return { chip: chip ? chip.textContent.trim() : null, words: t.querySelectorAll('.dv-bar-tools [data-bar=menu]').length, tabs: t.querySelectorAll('.dv-bar-views [data-bar=tab]').length,
        display: Boolean(t.querySelector('.dv-bar-tools [data-bar=display]')) };
    });
    check('1 · Overflow menu: the level is the view chip (Info+ by default), no promoted words, no tabs, Display holds the rest',
      v1.chip && /Info\+/.test(v1.chip) && v1.words === 0 && v1.tabs === 0 && v1.display, v1);
    const before1 = await rows(page);
    await page.click('#dx-detail-log .dv-bar-views [data-bar=views]');
    await page.click('.dv-vm-row[data-name="Warn+"]');
    await page.waitForTimeout(500);
    const after1 = await rows(page);
    check('…its chip\'s Warn+ leaves warnings and errors only (fewer rows)', after1 && after1.length > 0 && after1.length < before1.length && after1.every((r) => SEV[r.severity] >= 2), { before: before1 && before1.length, after: after1 && after1.length });

    // 2 · chip: the scope is the view chip; the level and the raw lines are typed in the query box (the classic search's level:)
    check('2 · Scope as a chip: drawn live from the picker (no reload)', await pick(page, 'chip'));
    const v2 = await page.evaluate(() => {
      const t = document.querySelector('#dx-detail-log');
      const chip = t.querySelector('.dv-bar-views [data-bar=views]');
      return { chip: chip ? chip.textContent.trim() : null, tabs: t.querySelectorAll('.dv-bar-views [data-bar=tab]').length, words: t.querySelectorAll('.dv-bar-tools [data-bar=menu]').length,
        query: Boolean(t.querySelector('.dv-bar-query input')) };
    });
    check('…the scope is the view chip (+ children by default), the query box beside it, nothing promoted',
      v2.chip && /children/.test(v2.chip) && v2.tabs === 0 && v2.words === 0 && v2.query, v2);
    const before2 = await rows(page);
    await page.click('#dx-detail-log .dv-bar-views [data-bar=views]');
    await page.click('.dv-vm-row[data-name="this only"]');
    await page.waitForTimeout(500);
    const self2 = await rows(page);
    check('…its chip\'s "this only" keeps the entity\'s own lines', self2 && self2.length > 0 && self2.length < before2.length && self2.every((r) => r.path === 'host'), { before: before2 && before2.length, after: self2 && self2.length });
    await page.click('#dx-detail-log .dv-bar-views [data-bar=views]');
    await page.click('.dv-vm-row[data-name="+ children"]');
    await page.waitForTimeout(400);
    await page.click('#dx-detail-log .dv-bar-query input');
    await page.keyboard.type('level:>=2 ');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(600);
    const typed2 = await rows(page);
    check('…and typing level:>=2 in the query box leaves warnings and errors only', typed2 && typed2.length > 0 && typed2.length < before2.length && typed2.every((r) => SEV[r.severity] >= 2), { before: before2 && before2.length, after: typed2 && typed2.length });
    await page.keyboard.press('Control+a');
    await page.keyboard.press('Backspace');
    await page.keyboard.press('Escape');

    // 3 · search: the query box folded behind its icon until it is used, the tools as icons
    check('3 · Search on focus: drawn live from the picker', await pick(page, 'search'));
    // the query box (data-ui's .dv-query) as the eye sees it: its width while it is drawn, 0 while it is folded away
    const QUERY_W = "(() => { const q = document.querySelector('#dx-detail-log .dv-bar-query .dv-query'); if (!q) return 0; const cs = getComputedStyle(q); "
      + "return cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0 ? 0 : q.getBoundingClientRect().width; })()";
    const folded = {
      icon: await page.evaluate(() => { const i = document.querySelector('#dx-detail-log .dv-bar-tools [data-bar=search]'); return i ? i.getBoundingClientRect().width : 0; }),
      box: await page.evaluate(QUERY_W),
      labels: await page.evaluate(() => [...document.querySelectorAll('#dx-detail-log .dv-bar-tools .dv-bar-label')].filter((l) => l.getBoundingClientRect().width > 0 && getComputedStyle(l).display !== 'none').length),
    };
    await page.click('#dx-detail-log .dv-bar-tools [data-bar=search]');
    await page.waitForTimeout(300);
    const opened = { box: await page.evaluate(QUERY_W), focused: await page.evaluate(() => document.activeElement === document.querySelector('#dx-detail-log .dv-bar-query input')) };
    await page.keyboard.type('Plugged');
    await page.waitForTimeout(400);
    const searched = await rows(page);
    check('…the search is an icon until used: a click grows the box in place and focuses it, the tools are icons only',
      folded.icon > 0 && folded.box < 40 && opened.box >= 120 && opened.focused && folded.labels === 0, { folded, opened });
    check('…and what you type searches the log', searched && searched.length >= 1 && searched.length < (before2 || []).length, { rows: searched && searched.length });
    await page.keyboard.press('Control+a');
    await page.keyboard.press('Backspace');
    await page.keyboard.press('Escape');

    // 4 · crumb: the scope picked in the entity's breadcrumb (so the bar has none), the levels as tabs
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(ADAPTER)}&page=log`);
    await ready(page);
    check('4 · Scope in the breadcrumb: drawn live from the picker', await pick(page, 'crumb'));
    const crumb = await page.evaluate(() => {
      const c = document.querySelector('#dx-crumbs [data-log-scope]');
      const t = document.querySelector('#dx-detail-log');
      const r = c && c.getBoundingClientRect(), cr = document.querySelector('#dx-crumbs').getBoundingClientRect();
      return c ? { options: [...c.querySelectorAll('option')].map((o) => o.value), value: c.value, inCrumbs: r.top >= cr.top - 2 && r.bottom <= cr.bottom + 2,
        tokens: t.querySelectorAll('.dv-bar-query .dv-qtoken').length, tabs: t.querySelectorAll('.dv-bar-views [data-bar=tab]').length } : null;
    });
    check('…the scope is a picker in the breadcrumb (+ children, this only, + path to host), none in the bar; the levels are tabs',
      crumb && crumb.options.join() === 'children,self,host' && crumb.value === 'children' && crumb.inCrumbs && crumb.tokens === 0 && crumb.tabs >= 4, crumb);
    // (what each scope keeps is measured on the stub below, whose entity has lines inside the window)

    // 5 · facet: the scope as tabs; the level and the raw lines as facets in the dashboard's rail (a side panel, no toolbar row)
    check('5 · Level as a facet: drawn live from the picker', await pick(page, 'facet'));
    await page.waitForTimeout(400);
    const rail = async () => page.evaluate(() => {
      const f = document.querySelector('#dx-detail data-facets');
      const t = document.querySelector('#dx-detail-log');
      const btn = t.querySelector('.dv-bar-tools [data-bar=menu][data-name=filter]');
      const r = f ? f.getBoundingClientRect() : null, tr = t.getBoundingClientRect();
      return { shown: Boolean(r && r.width > 100 && r.height > 40 && getComputedStyle(f).display !== 'none'), beside: Boolean(r && (r.right <= tr.left + 1 || r.left >= tr.right - 1)),
        level: f ? /Level/.test(f.textContent) && ['Warn+', 'Error+'].every((x) => f.textContent.includes(x)) : false, raw: f ? /raw/i.test(f.textContent) : false,
        filterWord: Boolean(btn), tabs: [...t.querySelectorAll('.dv-bar-views [data-bar=tab]')].map((x) => x.dataset.name) };
    });
    const r1 = await rail();
    check('…a facet rail beside the table holds Level (Debug+ … Error+) and the raw lines (the one filter: no Filter on the bar), and the scopes are tabs',
      r1.shown && r1.beside && r1.level && r1.raw && !r1.filterWord && ['+ children', 'this only', '+ path to host'].every((x) => r1.tabs.includes(x)), r1);
    // the rail is a side panel (Zach's standing rule: it drags, CONTRACT §11), so the facets never take a row of the toolbar
    const railBox = async () => page.evaluate(() => { const r = document.querySelector('#dx-detail .dd-rail'); if (!r) return null; const b = r.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; });
    const edgeBox = await page.evaluate(() => { const s = document.querySelector('#dx-detail .dd-rail + .dv-splitter'); if (!s) return null; const b = s.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; });
    const rail0 = await railBox();
    if (edgeBox) {
      await page.mouse.move(edgeBox.x + edgeBox.w / 2, edgeBox.y + Math.min(80, edgeBox.h / 2));
      await page.mouse.down();
      await page.mouse.move(edgeBox.x + edgeBox.w / 2 + 40, edgeBox.y + Math.min(80, edgeBox.h / 2), { steps: 8 });
      await page.mouse.up();
      await page.waitForTimeout(300);
    }
    const rail1 = await railBox();
    check('…the rail is a side panel: its edge drags 40 px wider', Boolean(edgeBox) && rail0 && rail1 && Math.abs(rail1.w - rail0.w - 40) <= 3, { before: rail0 && rail0.w, after: rail1 && rail1.w });

    // ---- remembered across a reload --------------------------------------------------------------------------------------------
    await page.reload();
    await ready(page);
    const kept = await logShown(page, 'facet');
    check('the pick is remembered across a reload (this browser), drawn with it', kept && (await page.evaluate(() => document.querySelector('#dx-log-variant').value)) === 'facet');
    await ctx.close();

    // ---- raw lines and the host's window (PEP 0001 §16.5), on the test-only stub. Its entity stub/0 has three raw lines from a boot 12 h
    // back, and from 25 s after the stub starts a line every 2 s at its own path, below it and above it ("stub"), info, warn and error.
    // WHY the stub and not F1: F1's kernel lines are all from the rack's boot, 12 h before the replay, so on F1 no Log has a line inside
    // the window. WHY the old lines first and alone: that is F1's case (an entity whose raw lines are all older than the window), the one
    // where the page held them and its time bar spanned 12 h; once newer lines of the same stream come, data-ui's own retention takes
    // older chunks, so a check made only then could not tell the page's rule from data-ui's
    {
      const stub = await startStub(['--rate', '50', '--entities', '2', '--keys', '5', '--raw', '2', '--raw-from', '25', '--raw-old', '43200']);
      const c = await H.newContext(browser, 'light');
      try {
        const p = await c.newPage();
        errors.push(H.watchErrors(p, stub.base));
        const q = new URLSearchParams({ fold: '1', channel: 'raw', limit: '1000', severity: 'debug', path: 'stub/0', scope: 'host' });
        const answer = async () => { const a = await H.getJson(stub.base, `/api/v1/activity?${q}`); const t0 = a.clock.now - 900; return { now: a.clock.now, inside: a.events.filter((e) => e.received >= t0), older: a.events.filter((e) => e.received < t0) }; };
        const rawRows = () => p.evaluate(() => { const t = document.querySelector('#dx-detail-log'); return t && t.model ? t.model.rows.filter((r) => r.channel === 'raw').map((r) => ({ t: r.t, path: r.path, severity: r.severity })) : []; });
        const clockOf = () => p.evaluate(() => { const k = window.robotdx.clock; return { t: k.t, liveStart: k.liveStart, liveEnd: k.liveEnd, following: k.following, window: window.robotdx.feed.host.window }; });
        const heldIds = () => p.evaluate(() => window.robotdx.feed.dataset('events').snapshot(Infinity, { at: 'all', limit: 100000 }).map((r) => String(r.id)));
        await p.goto(`${stub.base}${DX}?go=explore&sel=stub/0&page=log`);
        await ready(p);
        await p.waitForFunction(() => { const s = window.robotdx.feed.stats()['/activity']; return s && s.requests >= 2; }, null, { timeout: 10000 });
        await p.waitForTimeout(1500);
        const first = await answer();
        check('stub: the host\'s raw answer for stub/0\'s Log is three lines from 12 h back and none inside the window (F1\'s case)',
          first.inside.length === 0 && first.older.length === 3 && first.older.every((e) => e.received < first.now - 43000), { inside: first.inside.length, older: first.older.length });
        const held0 = await heldIds(), k0 = await clockOf();
        check('the lines older than the window are not held by the page (the host\'s three 12 h old lines are in no row it keeps)',
          first.older.every((e) => !held0.includes(e.id)) && (await rawRows()).length === 0, { olderIds: first.older.map((e) => e.id), held: held0.length });
        check('…and the time bar\'s span is not stretched by them: the clock\'s data spans at most the window', k0.liveEnd - k0.liveStart <= k0.window + 60, k0);
        // the lines inside the window, from 25 s on: a few of each kind by 40 s
        await H.waitFor(async () => (await answer()).inside.length >= 7, 45000, 1000);
        await pick(p, 'crumb');
        await H.waitFor(async () => (await rawRows()).length >= 3, 12000, 300);
        // 4 · the breadcrumb's scope: what each keeps is where a line is
        const scoped = {};
        for (const sc of ['self', 'children', 'host']) {
          await p.selectOption('#dx-crumbs [data-log-scope]', sc);
          await p.waitForTimeout(700);
          scoped[sc] = await rows(p);
        }
        const below = (x) => x === 'stub/0' || x.startsWith('stub/0/');
        check('4 · on a host with lines inside its window: this only is the entity\'s own lines, + children adds the ones below it, + path to host the ones above it',
          scoped.self.length > 0 && scoped.self.every((r) => r.path === 'stub/0') && scoped.children.length > scoped.self.length && scoped.children.every((r) => below(r.path))
            && scoped.host.length > scoped.children.length && scoped.host.some((r) => r.path === 'stub'),
          { self: scoped.self.length, children: scoped.children.length, host: scoped.host.length });
        await p.selectOption('#dx-crumbs [data-log-scope]', 'children');
        await p.waitForTimeout(400);
        // 5 · the Level facet narrows them
        await pick(p, 'facet');
        await p.waitForTimeout(700);
        const every = await rows(p);
        await p.locator('#dx-detail data-facets').getByText('Warn+', { exact: true }).first().click();
        await p.waitForTimeout(600);
        const warnOnly = await rows(p);
        check('5 · its Level facet\'s Warn+ leaves warnings and errors only', warnOnly.length > 0 && warnOnly.length < every.length && warnOnly.every((r) => SEV[r.severity] >= 2),
          { before: every.length, after: warnOnly.length });
        await p.locator('#dx-detail data-facets').getByText('Warn+', { exact: true }).first().click();   // back to Info+
        await p.waitForTimeout(500);
        // the lines inside the window draw in the Log and rewind with the clock
        const live = await rawRows();
        const newest = Math.max(...live.map((r) => r.t));
        const at = async (x) => { await p.evaluate((y) => window.robotdx.clock.seek(y), x); await p.waitForTimeout(600); return (await rawRows()).some((r) => r.t === newest); };
        const before = await at(newest - 0.5), after = await at(newest + 0.3);
        await p.evaluate(() => window.robotdx.clock.goLive());
        await p.waitForTimeout(400);
        const k1 = await clockOf();
        check('raw lines inside the host\'s window draw in the entity\'s Log and rewind with the clock (not there half a second before their time, there after it)',
          live.length > 0 && live.every((r) => r.t >= k1.liveEnd - k1.window - 1) && !before && after, { lines: live.length, newest, before, after });
        const held1 = await heldIds();
        check('…and still, with them, the old lines are not held and the time bar spans at most the window',
          first.older.every((e) => !held1.includes(e.id)) && k1.liveEnd - k1.liveStart <= k1.window + 60, { held: held1.length, span: k1.liveEnd - k1.liveStart });
        // the rail's Raw lines facet hides them
        await p.locator('#dx-detail data-facets').getByText(/hide raw/i).first().click();
        await p.waitForTimeout(600);
        const hidden = (await rawRows()).length;
        check('…and the rail\'s Raw lines facet hides them', live.length > 0 && hidden === 0, { shown: live.length, hidden });
      } finally {
        await c.close().catch(() => {});
        H.stopServer(stub.child);
      }
    }

    // ---- one line, every proposal, at 1280 and 390 px, light and dark, on a board's Log (the classic example: "Log of CAN 13") -----
    for (const [width, height] of [[1280, 900], [390, 844]]) {
      for (const scheme of ['light', 'dark']) {
        const c = await browser.newContext({ viewport: { width, height }, colorScheme: scheme });
        const p = await c.newPage();
        errors.push(H.watchErrors(p, base));
        await p.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(H.BOARD)}&page=log`);
        await ready(p);
        if (width < 700) {
          // a phone: the tree folded and the rail to its icons, so the Log has the width (both remembered, as a person does once)
          await p.click('#dx-tree-fold').catch(() => {});
          await p.evaluate(() => { const r = document.querySelector('#dx-rail'); if (r) r.mode = 'collapsed'; });
        }
        for (const v of VARIANTS) {
          const shown = await pick(p, v);
          await p.waitForTimeout(500);
          const m = await oneLine(p);
          check(`one line at ${width} px, ${scheme}: ${v}`, shown && m && m.boxes.length >= 2 && m.spread <= 2 && m.barH <= 60 && m.sub === 0 && m.inside && m.scroll <= m.width + 1,
            m ? { spread: m.spread, barH: m.barH, sub: m.sub, inside: m.inside, boxes: m.boxes.map((b) => `${b.what}@${b.top}`).slice(0, 10) } : null);
        }
        await c.close();
      }
    }
    const all = errors.flatMap((e) => e.errors);
    check('no page errors, no console errors, no failed request', all.length === 0, all.slice(0, 5));
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close().catch(() => {});
    if (host) H.stopServer(host.child);
  }
  process.exit(H.summary());
})();
