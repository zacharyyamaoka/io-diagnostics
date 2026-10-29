#!/usr/bin/env node
// Add to dashboard on the RobotDx page (data-ui CONTRACT §34, PEP 0014; docs/peps/0001-robotdx-on-data-ui.md §17), in the REAL page on a
// fixture host, headless Chrome. Written before the page's code (the lane's rule). The oracles are outside the page: the host's own
// dashboards folder on disk and its GET /api/v1/dashboards, the requests Chrome sent, data-ui's own exported appendPanel, and
// getBoundingClientRect for "at the bottom":
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_dx_add_to_dashboard.js
//
//   menu      Add to dashboard… is offered on a /dx/ table and a plot (a panel's ⋯), a KPI card and a legend series (the item menu, a
//             right-click), and each opens the picker listing the host's dashboards in the host's order
//   existing  adding the board's Configuration table (browsed to from another entity) to a host dashboard PUTs it exactly once, with
//             If-Match: the etag of the text it appended to; the file is then appendPanel(old, panel) byte for byte, `panel` being
//             data-ui's own with the file its source panel reads (as the source dashboard declares it: the board's config); the picker
//             says "Added to <title>" with Open, which lands on that dashboard, the new panel at the bottom drawing the source's rows,
//             and again after a reload
//   conflict  a PUT that meets another writer's change (412) reads the file again, appends again and lands; two 412s in a row say
//             "<id> changed while you were adding; reload it and add again" and keep the other writer's text
//   new       New dashboard… creates the file with If-None-Match: * (appendPanel('', panel)); a name already on the host is refused and
//             that file is left as it was
//   open      a dashboard open on the page when an add to it lands is drawn again, the new panel at the bottom, its text the file's
// Every dashboard the page writes names its data relative to the folder the page opens dashboards from (/dx/dashboards/, as the
// starters do), so appendPanel's base here is /dx/dashboards/<name>.base. Exits 1 on any failure.
'use strict';

const crypto = require('crypto');
const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, BOARD, ADAPTER, fs, path } = H;

const DX = '/dx/';
const sha = (text) => crypto.createHash('sha256').update(text).digest('hex');
const binding = (kind) => `/dx/datasets/${kind}.json?path=${BOARD}`;   // the file a source panel of the board reads, as declared
const baseOf = (name) => `/dx/dashboards/${name}.base`;
const timeBoard = (title) => `# ${title}: kept by the person; Add to dashboard appends to it
datasets:
  summary:
    data: "../datasets/summary.json"
layout:
  columns: 12
  rowHeight: 96
  panels:
    - { view: Time, w: 12, h: 1 }
views:
  - type: playback
    name: Time
    from: summary
`;
// Servos also declares the board's config (a dataset no panel draws yet): the add must find it by its file, not add it twice
const SERVOS = `# Servos: the rack's joints, kept by the person
datasets:
  summary:
    data: "../datasets/summary.json"
  board_config:
    data: "../datasets/config.json?path=${BOARD}"
layout:
  columns: 12
  rowHeight: 96
  panels:
    - { view: Time, w: 12, h: 1 }
views:
  - type: playback
    name: Time
    from: summary
`;

(async () => {
  let host;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding'] });
  try {
    host = await H.fixtureHost('F1');
    const { base } = host;
    const dir = path.join(host.root, 'config', 'dashboards');
    fs.mkdirSync(dir, { recursive: true });
    const fileOf = (name) => path.join(dir, `${name}.base`);
    const read = (name) => (fs.existsSync(fileOf(name)) ? fs.readFileSync(fileOf(name), 'utf8') : null);
    const write = (name, text) => fs.writeFileSync(fileOf(name), text);
    const listing = () => fs.readdirSync(dir).filter((f) => f.endsWith('.base')).sort();
    write('Clash', timeBoard('Clash'));
    write('Retry', timeBoard('Retry'));
    write('Servos', SERVOS);
    const hostNames = async () => ((await H.getJson(base, '/api/v1/dashboards')).files || []).map((f) => f.name);

    const context = await H.newContext(browser, 'light');
    const page = await context.newPage();
    // data-ui's own panel for each add, taken before the page's listener runs (window, capture phase)
    await page.addInitScript(() => {
      window.__adds = [];
      addEventListener('data-add-to-dashboard', (e) => window.__adds.push(JSON.parse(JSON.stringify({ dashboard: e.detail.dashboard, panel: e.detail.panel }))), true);
    });
    const log = [];
    page.on('response', (res) => {
      const r = res.request();
      const u = new URL(r.url());
      if (u.origin !== base) return;
      const h = r.headers();
      log.push({ method: r.method(), path: decodeURIComponent(u.pathname), status: res.status(), ifMatch: h['if-match'] || null, ifNoneMatch: h['if-none-match'] || null, at: Date.now() });
    });
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(String(e)));
    const putsTo = (name, since) => log.filter((r) => r.method === 'PUT' && r.path === `/api/v1/dashboards/${name}.base` && r.at >= since);
    const lastPanel = () => page.evaluate(() => window.__adds[window.__adds.length - 1] || null);
    const withBinding = (panel, kind) => ({ ...panel, dataset: { ...panel.dataset, data: binding(kind) } });
    const append = (text, panel, opts) => page.evaluate(async ({ text, panel, opts }) => {
      try { return (await import('/vendor/data-ui/data-ui.js')).appendPanel(text, panel, opts); } catch (e) { return `appendPanel threw: ${e.message}`; }
    }, { text, panel, opts });
    const visible = (sel) => page.evaluate((s) => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? { text: el.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height) } : null; }, sel);
    // the picker as it is: the dashboards it lists, its status
    const picker = () => page.evaluate(() => {
      const p = document.querySelector('.dv-addpick');
      if (!p) return null;
      const s = p.querySelector('.dv-addpick-status');
      const a = s && s.querySelector('a[href]');
      return { ids: [...p.querySelectorAll('[data-dashboard-id]')].map((b) => b.dataset.dashboardId), state: s ? s.dataset.state : null, status: s ? s.textContent.trim() : '', link: a ? { text: a.textContent.trim(), href: a.href } : null, msg: (p.querySelector('.dv-addpick-list') || {}).textContent || '' };
    });
    const listed = async (n) => H.waitFor(async () => { const p = await picker(); return p && p.ids.length >= n ? p : null; }, 10000);
    const settled = async () => H.waitFor(async () => { const p = await picker(); return p && (p.state === 'added' || p.state === 'error') ? p : null; }, 15000);
    const closePicker = async () => { await page.keyboard.press('Escape'); await page.waitForTimeout(150); };
    // a panel's ⋯ in the open dashboard, then its Add to dashboard…
    async function panelMenu(scope, key) {
      const sec = `${scope} .dd-panel[data-panel="${key}"]`;
      await page.hover(sec);
      await page.click(`${sec} .dd-panel-menu`);
      const item = await H.waitFor(() => visible(`${sec} .dd-panel-pop:not([hidden]) [data-add-dashboard]`), 3000);
      if (item) await page.click(`${sec} .dd-panel-pop:not([hidden]) [data-add-dashboard]`);
      return item;
    }
    // the item menu of whatever is under a right-click, then its Add to dashboard…
    async function itemMenu(selector) {
      await page.click(selector, { button: 'right' });
      const item = await H.waitFor(() => visible('.dv-itemmenu [data-add-dashboard]'), 3000);
      if (item) await page.click('.dv-itemmenu [data-add-dashboard]');
      return item;
    }
    // the open board's panels in reading order, with the views each holds and its rows
    const boardPanels = () => page.evaluate(() => [...document.querySelectorAll('data-dashboard.dx-board .dd-panel')].map((p) => {
      const r = p.getBoundingClientRect();
      const v = p.querySelector('data-views');
      return { key: p.dataset.panel, top: r.top, bottom: r.bottom, w: r.width, views: v && v.dashboardPanel ? v.dashboardPanel.views.map((x) => x.name) : [],
        rows: v && v.model && v.model.rows ? v.model.rows.map((row) => String(row.id)).sort() : null };
    }));
    const atBottom = (panels, name) => {
      const p = panels.find((x) => x.views.includes(name));
      return Boolean(p) && panels.every((o) => o === p || o.bottom <= p.top + 1);
    };
    const sameRows = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length > 0 && a.length === b.length && a.every((x, i) => x === b[i]);

    // ---- browse: another entity first (the page's shared datasets are made for it), then the board's Configuration tab ---------------
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(ADAPTER)}&page=config`);
    await page.waitForFunction(() => window.robotdx && document.querySelector('#dx-detail .dd-panel'), null, { timeout: 30000 });
    await page.waitForTimeout(1500);
    await page.click(`#dx-tree [role=treeitem][data-t6-path="${BOARD}"]`);
    await page.waitForFunction((p) => new URLSearchParams(location.search).get('sel') === p, BOARD, { timeout: 10000 });
    await page.click('#dx-tabs [data-dx-tab="config"]');
    const sourceRows = await H.waitFor(() => page.evaluate((p) => {
      const v = document.querySelector('#dx-detail-configuration');
      return new URLSearchParams(location.search).get('sel') === p && v && v.model && v.model.rows.length ? v.model.rows.map((r) => String(r.id)).sort() : null;
    }, BOARD), 15000);
    check('the board\'s Configuration tab draws its settings (the source of the first add)', Boolean(sourceRows) && sourceRows.length > 0, { rows: sourceRows && sourceRows.length });

    // ---- a table: its panel's ⋯ → Add to dashboard… → the picker, listing the host's dashboards ---------------------------------------
    let names = await hostNames();
    let item = null, pick = null;
    try {
      item = await panelMenu('#dx-detail', 'configuration');
      pick = item ? await listed(names.length) : null;
    } catch (err) { pick = { error: err.message }; }
    check('a table panel\'s ⋯ offers Add to dashboard…, and it opens the picker with the host\'s dashboards in its order',
      Boolean(item) && /^Add to dashboard/.test(item.text) && pick && Array.isArray(pick.ids) && pick.ids.join('|') === names.join('|') && names.length === 3, { item, listed: pick && pick.ids, host: names });

    // ---- an existing dashboard: one PUT with If-Match, the file is appendPanel(old, panel), "Added to Servos" with Open ----------------
    const old = read('Servos');
    let t0 = Date.now();
    let done = null;
    try { await page.click('.dv-addpick [data-dashboard-id="Servos"]', { timeout: 8000 }); done = await settled(); } catch (err) { done = { error: err.message }; }
    const servosPuts = putsTo('Servos', t0);
    check('adding to an existing dashboard PUTs it exactly once, with If-Match: the etag of the text it appended to',
      servosPuts.length === 1 && servosPuts[0].ifMatch === `"${sha(old)}"` && servosPuts[0].status === 200, { puts: servosPuts, want: sha(old) });
    const panel1 = await lastPanel();
    const expected1 = panel1 ? await append(old, withBinding(panel1.panel, 'config'), { base: baseOf('Servos') }) : null;
    const after1 = read('Servos');
    check('…and the host file is then appendPanel(old, panel) byte for byte, reading the board\'s config as the source panel does',
      Boolean(panel1) && after1 === expected1 && after1 !== old, { sent: panel1 && panel1.panel.dataset, file: after1 && after1.split('\n').slice(-6), expected: expected1 && String(expected1).split('\n').slice(-6) });
    const openHref = done && done.link ? new URL(done.link.href) : null;
    check('…and the picker says "Added to Servos" with an Open link to that dashboard on /dx/',
      Boolean(done) && done.state === 'added' && /^Added to Servos\b/.test(done.status) && done.link && /Open/.test(done.link.text) && openHref && openHref.pathname === DX
        && openHref.searchParams.get('go') === 'dashboards' && openHref.searchParams.get('board') === 'Servos', { done });

    // ---- Open lands on the dashboard: the new panel at the bottom, drawing the source's rows (and after a reload, from the host alone) --
    const lastView = async (text) => page.evaluate((t) => { try { const b = window.robotdx.parseBase(t); const ps = b.layout.panels; return ps[ps.length - 1].view; } catch (e) { return null; } }, text);
    const newView = after1 ? await lastView(after1) : null;
    let landed = null;
    try {
      if (done && done.link) await page.click('.dv-addpick-status a[href]', { timeout: 5000 });
      landed = await H.waitFor(async () => {
        try {
          const q = await page.evaluate(() => ({ go: new URLSearchParams(location.search).get('go'), board: new URLSearchParams(location.search).get('board') }));
          if (q.go !== 'dashboards' || q.board !== 'Servos') return null;
          const panels = await boardPanels();
          const p = panels.find((x) => x.views.includes(newView));
          return p && p.rows && p.rows.length ? { q, panels } : null;
        } catch (_) { return null; }   // a navigation in between
      }, 15000);
    } catch (err) { landed = { error: err.message }; }
    const landedPanel = landed && landed.panels ? landed.panels.find((x) => x.views.includes(newView)) : null;
    check('Open lands on that dashboard, the new panel at the bottom (measured), drawing the same rows as the source',
      Boolean(landedPanel) && atBottom(landed.panels, newView) && sameRows(landedPanel.rows, sourceRows), { newView, panels: landed && landed.panels && landed.panels.map((p) => ({ key: p.key, top: Math.round(p.top), bottom: Math.round(p.bottom), views: p.views, rows: p.rows && p.rows.length })), source: sourceRows && sourceRows.length });
    await page.reload();
    const reloaded = await H.waitFor(async () => {
      const panels = await boardPanels().catch(() => []);
      const p = panels.find((x) => x.views.includes(newView));
      return p && p.rows && p.rows.length ? panels : null;
    }, 20000);
    const reloadedPanel = reloaded ? reloaded.find((x) => x.views.includes(newView)) : null;
    check('…and after a reload (the host\'s file and its own reads alone) the same rows, the panel still at the bottom',
      Boolean(reloadedPanel) && atBottom(reloaded, newView) && sameRows(reloadedPanel.rows, sourceRows), { rows: reloadedPanel && reloadedPanel.rows && reloadedPanel.rows.length, source: sourceRows && sourceRows.length });

    // ---- a KPI card (the item menu): one 412 is read again, appended again, and lands ---------------------------------------------
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(BOARD)}&page=state`);
    const card = await H.waitFor(() => page.evaluate(() => { const c = document.querySelector('#dx-detail-state data-kpi[data-i]'); return c ? c.dataset.i : null; }), 20000);
    const retryOld = read('Retry');
    const retryChanged = `${retryOld}# changed by another tab while the picker was open\n`;
    let firstPut = true;
    await page.route('**/api/v1/dashboards/Retry.base', async (route) => {
      if (route.request().method() === 'PUT' && firstPut) { firstPut = false; write('Retry', retryChanged); }
      await route.continue();
    });
    names = await hostNames();
    let kpiItem = null, kpiPick = null;
    try {
      kpiItem = card !== null ? await itemMenu(`#dx-detail-state data-kpi[data-i="${card}"]`) : null;
      kpiPick = kpiItem ? await listed(names.length) : null;
    } catch (err) { kpiPick = { error: err.message }; }
    check('a KPI card\'s item menu (right-click) offers Add “<card>” to dashboard…, and it opens the picker with the host\'s dashboards',
      Boolean(kpiItem) && /^Add “.+” to dashboard…$/.test(kpiItem.text) && kpiPick && Array.isArray(kpiPick.ids) && kpiPick.ids.join('|') === names.join('|'), { item: kpiItem, listed: kpiPick && kpiPick.ids, host: names });
    t0 = Date.now();
    let retried = null;
    try { await page.click('.dv-addpick [data-dashboard-id="Retry"]', { timeout: 8000 }); retried = await settled(); } catch (err) { retried = { error: err.message }; }
    await page.waitForTimeout(1000);
    const retryPuts = putsTo('Retry', t0);
    const panel2 = await lastPanel();
    const expected2 = panel2 ? await append(retryChanged, withBinding(panel2.panel, 'samples'), { base: baseOf('Retry') }) : null;
    check('a 412 (another writer changed the file) is read again, appended again and PUT once more with the new etag, and lands',
      retryPuts.length === 2 && retryPuts[0].status === 412 && retryPuts[0].ifMatch === `"${sha(retryOld)}"` && retryPuts[1].status === 200 && retryPuts[1].ifMatch === `"${sha(retryChanged)}"`
        && read('Retry') === expected2 && retried && retried.state === 'added' && /^Added to Retry\b/.test(retried.status),
      { puts: retryPuts.map((r) => [r.status, r.ifMatch]), state: retried && retried.state, status: retried && retried.status, kept: (read('Retry') || '').includes('changed by another tab'), same: read('Retry') === expected2 });
    await page.unroute('**/api/v1/dashboards/Retry.base');
    await closePicker();

    // ---- a plot (its panel's ⋯): two 412s in a row say so, and the other writer's text stays -------------------------------------------
    let clashes = 0;
    await page.route('**/api/v1/dashboards/Clash.base', async (route) => {
      if (route.request().method() === 'PUT') { clashes += 1; write('Clash', `${timeBoard('Clash')}# another writer, save ${clashes}\n`); }
      await route.continue();
    });
    names = await hostNames();
    let plotItem = null, plotPick = null;
    try {
      await H.waitFor(() => page.evaluate(() => Boolean(document.querySelector('#dx-detail .dd-panel[data-panel="signals"] data-views'))), 10000);
      plotItem = await panelMenu('#dx-detail', 'signals');
      plotPick = plotItem ? await listed(names.length) : null;
    } catch (err) { plotPick = { error: err.message }; }
    check('a plot panel\'s ⋯ offers Add to dashboard…, and it opens the picker with the host\'s dashboards',
      Boolean(plotItem) && /^Add to dashboard/.test(plotItem.text) && plotPick && Array.isArray(plotPick.ids) && plotPick.ids.join('|') === names.join('|'), { item: plotItem, listed: plotPick && plotPick.ids, host: names });
    t0 = Date.now();
    let clashed = null;
    try { await page.click('.dv-addpick [data-dashboard-id="Clash"]', { timeout: 8000 }); clashed = await settled(); } catch (err) { clashed = { error: err.message }; }
    await page.waitForTimeout(1500);   // a third PUT would be in by now
    const clashPuts = putsTo('Clash', t0);
    check('two 412s in a row: two PUTs, no third, the picker says the dashboard changed, and the other writer\'s text is kept',
      clashPuts.length === 2 && clashPuts.every((r) => r.status === 412) && clashed && clashed.state === 'error'
        && clashed.status.includes('Clash changed while you were adding; reload it and add again') && read('Clash') === `${timeBoard('Clash')}# another writer, save 2\n`,
      { puts: clashPuts.map((r) => [r.status, r.ifMatch]), state: clashed && clashed.state, status: clashed && clashed.status });
    await page.unroute('**/api/v1/dashboards/Clash.base');
    await closePicker();

    // ---- New dashboard…: created with If-None-Match: *, and a name already on the host is refused, that file untouched ----------------
    const before = { servos: read('Servos'), files: listing() };
    let fresh = null;
    try {
      await panelMenu('#dx-detail', 'signals');
      await listed(1);
      await page.click('.dv-addpick [data-dashboard-new]', { timeout: 8000 });
      await page.fill('.dv-addpick input[name=dashboard-title]', 'Fresh adds', { timeout: 8000 });
      t0 = Date.now();
      await page.click('.dv-addpick [data-dashboard-create]', { timeout: 8000 });
      fresh = await settled();
    } catch (err) { fresh = { error: err.message }; }
    const freshPuts = putsTo('Fresh adds', t0);
    const panel3 = await lastPanel();
    const expected3 = panel3 ? await append('', withBinding(panel3.panel, 'samples'), { title: 'Fresh adds', base: baseOf('Fresh adds') }) : null;
    check('New dashboard… creates the file with If-None-Match: *, its text appendPanel(\'\', panel), and says "Added to Fresh adds"',
      freshPuts.length === 1 && freshPuts[0].ifNoneMatch === '*' && !freshPuts[0].ifMatch && freshPuts[0].status === 201 && read('Fresh adds') === expected3
        && panel3 && panel3.dashboard.new === true && fresh && fresh.state === 'added' && /^Added to Fresh adds\b/.test(fresh.status),
      { puts: freshPuts.map((r) => [r.status, r.ifNoneMatch, r.ifMatch]), state: fresh && fresh.state, status: fresh && fresh.status, same: read('Fresh adds') === expected3 });
    await closePicker();
    let refused = null;
    try {
      await panelMenu('#dx-detail', 'signals');
      await listed(1);
      await page.click('.dv-addpick [data-dashboard-new]', { timeout: 8000 });
      await page.fill('.dv-addpick input[name=dashboard-title]', 'Servos', { timeout: 8000 });
      t0 = Date.now();
      await page.click('.dv-addpick [data-dashboard-create]', { timeout: 8000 });
      refused = await settled();
    } catch (err) { refused = { error: err.message }; }
    await page.waitForTimeout(500);
    const refusedPuts = putsTo('Servos', t0);
    check('…and a new dashboard named like one already there is refused (412 to If-None-Match: *), that file left as it was',
      refused && refused.state === 'error' && refusedPuts.length === 1 && refusedPuts[0].ifNoneMatch === '*' && refusedPuts[0].status === 412
        && read('Servos') === before.servos && listing().join('|') === [...before.files, 'Fresh adds.base'].sort().join('|'),
      { state: refused && refused.state, status: refused && refused.status, puts: refusedPuts.map((r) => [r.status, r.ifNoneMatch]), files: listing() });
    await closePicker();

    // ---- the dashboard added to is open: it is drawn again, the new panel at the bottom, its text the host's file ------------------------
    await page.goto(`${base}${DX}?go=dashboards&board=Servos`);
    const opened = await H.waitFor(async () => { const ps = await boardPanels().catch(() => []); return ps.length >= 2 && ps.some((p) => p.views.includes(newView) && p.rows && p.rows.length) ? ps : null; }, 20000);
    const where = () => { const u = new URL(page.url()); return `${u.pathname}${u.search}`; };   // the hash carries t, which may move
    const url0 = where();
    const prev = read('Servos');
    let selfAdd = null;
    try {
      const panelKey = opened && opened.find((p) => p.views.includes(newView)).key;
      await panelMenu('data-dashboard.dx-board', panelKey);
      await listed(1);
      t0 = Date.now();
      await page.click('.dv-addpick [data-dashboard-id="Servos"]', { timeout: 8000 });
      selfAdd = await settled();
    } catch (err) { selfAdd = { error: err.message }; }
    const after4 = read('Servos');
    const view4 = after4 ? await lastView(after4) : null;
    const redrawn = await H.waitFor(async () => { const ps = await boardPanels().catch(() => []); return ps.length === (opened ? opened.length + 1 : -1) && ps.some((p) => p.views.includes(view4)) ? ps : null; }, 10000);
    const boardText = await page.evaluate(() => (document.querySelector('data-dashboard.dx-board') || {}).baseText || null);
    const panel4 = await lastPanel();
    const expected4 = panel4 ? await append(prev, withBinding(panel4.panel, 'config'), { base: baseOf('Servos') }) : null;
    check('a dashboard open when an add to it lands is drawn again: one more panel, at the bottom, its text the host\'s file',
      selfAdd && selfAdd.state === 'added' && Boolean(redrawn) && atBottom(redrawn, view4) && boardText === after4 && after4 === expected4 && where() === url0 && putsTo('Servos', t0).length === 1,
      { state: selfAdd && selfAdd.state, view: view4, panels: redrawn && redrawn.length, was: opened && opened.length, textIsFile: boardText === after4, appended: after4 === expected4 });
    await closePicker();

    // ---- a legend series (the item menu): the joint's Velocity and torque plot has two -------------------------------------------------
    names = await hostNames();
    await page.goto(`${base}${DX}?go=dashboards&board=joint`);
    const key = await H.waitFor(() => page.evaluate(() => {
      const sec = [...document.querySelectorAll('data-dashboard.dx-board .dd-panel')].find((p) => /velocity/i.test(p.getAttribute('aria-label') || ''));
      // §31's interactive entry when the plot has one, else the entry its legend draws for the series
      const entry = sec && (sec.querySelector('[data-legend-series="velocity"]') || [...sec.querySelectorAll('.dv-legend-key, .dv-plot-key')].find((k) => /(^|\s)velocity$/.test(k.textContent.trim())));
      if (!entry) return null;
      entry.setAttribute('data-test-legend', 'velocity');
      return entry.className;
    }), 15000);
    let legendItem = null, legendPick = null;
    try {
      legendItem = key ? await itemMenu('[data-test-legend="velocity"]') : null;
      legendPick = legendItem ? await listed(names.length) : null;
    } catch (err) { legendPick = { error: err.message }; }
    check('a legend series\' item menu (right-click) offers Add “velocity” to dashboard…, and it opens the picker with the host\'s dashboards',
      Boolean(legendItem) && legendItem.text === 'Add “velocity” to dashboard…' && legendPick && Array.isArray(legendPick.ids) && legendPick.ids.join('|') === names.join('|'),
      { legendEntry: key, item: legendItem, listed: legendPick && legendPick.ids });
    await closePicker();

    // ---- the page's rules: reads, the watch heartbeat, and a dashboard PUT only from an add; the 412s above are the only errors ----------
    const writes = log.filter((r) => r.method !== 'GET');
    const offList = writes.filter((r) => !(r.method === 'POST' && r.path === '/api/v1/watch') && !(r.method === 'PUT' && /^\/api\/v1\/dashboards\/[^/]+\.base$/.test(r.path)));
    check('every write is the watch heartbeat or a dashboard file PUT (no DELETE, nothing else)', offList.length === 0, offList.slice(0, 5));
    const expected412 = (e) => /412/.test(e) && /Precondition Failed|dashboards/.test(e);
    const unexpected = errors.filter((e) => !expected412(e));
    check('no page errors, no console errors (the 412s the conflict checks ask for aside)', unexpected.length === 0, unexpected.slice(0, 5));
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close().catch(() => {});
    if (host) H.stopServer(host.child);
  }
  process.exit(H.summary());
})();
