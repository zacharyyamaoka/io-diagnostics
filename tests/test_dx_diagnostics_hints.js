#!/usr/bin/env node
// The Diagnostics tab's stock views, one per diagnostic view hint (data-ui CONTRACT §35, PEP 0010 §10; docs/peps/0001-robotdx-on-data-ui.md
// §18), in the REAL page on a fixture host, headless Chrome. Written before the page's code (the lane's rule). WHY (Codex audit
// 2026-10-29, round 2): "The newer data-ui Diagnostics tab uses a generic checks table and health history; its check-row conversion
// drops the diagnostic view hints and measurements." The oracles are outside the page: tests/fixtures/dx_hints/records.json, the
// robotdx.diagnostics library's own catalog.dress output and the record it serializes to (the state fixture plugin emits a frame of
// them on CAN 1 when $STATE_DX_RECORDS names the file), the host's own /api/v1/node and /history, and getBoundingClientRect:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_dx_diagnostics_hints.js
//
//   row       each stock diagnostic's row in the page's checks dataset keeps its view (the diagnostic's id, its title, role, family and
//             data-ui hint), its headline (metric, value, unit, the four limits, target, better) and every measurement (key, value,
//             unit), as catalog.dress made them
//   views     each hint the fixture holds draws its stock data-ui view on the entity's Diagnostics tab, on screen (a non-zero rect),
//             drawing the measurements: threshold_band a plot with the warn / error limits, kpi_trend a KPI card with its spark and
//             target, level_history a levels strip, gate_matrix a tracker matrix, rollup_tree a table tree, distribution a chart of the
//             payload, evidence a view embedded in the row's side peek; levels and verdicts as data-ui's status badges (display: status)
//   fallback  a hint no data-ui view has stays in the checks table and nowhere else; path_overlay on an entity with no pose data too,
//             and the tab says why
//   rewind    the fixture moved on (frame B): drawn at a t between the two frames, every hint's view shows frame A's numbers; live, B's
// Exits 1 on any failure.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, BOARD, fs, os, path } = H;

const FIXTURE = JSON.parse(fs.readFileSync(path.join(H.ROOT, 'tests', 'fixtures', 'dx_hints', 'records.json'), 'utf8'));
const DX = '/dx/';
const UNKNOWN = 'radar_sweep';
const of = (frame, hint) => FIXTURE.frames[frame].find((x) => x.hint === hint);
const idOf = (item) => `${BOARD}#${item.record.name}`;
const nul = (v) => (v === undefined || v === '' ? null : v);
const near = (a, b) => {
  if (nul(a) === null || nul(b) === null) return nul(a) === nul(b);
  return Math.abs(Number(a) - Number(b)) <= 1e-9 * Math.max(1, Math.abs(Number(b)));
};
const LIMITS = [['warn_lo', 'warn_low'], ['warn_hi', 'warn_high'], ['error_lo', 'error_low'], ['error_hi', 'error_high']];
const measure = (dress, key) => (key === dress.metric ? dress.value : ((dress.measurements || []).find((m) => m.key === key) || {}).value);

/** What the row lost or changed against catalog.dress's Result (an empty list when it kept everything). */
function rowDiff(row, item) {
  const d = item.dress, out = [];
  if (!row) return ['no row'];
  for (const [field, want] of [['diagnostic', d.diagnostic], ['name', d.title], ['role', d.role], ['family', d.family], ['hint', d.hint], ['metric', d.metric],
    ['unit', d.unit], ['better', d.better]]) if ((nul(row[field]) ?? null) !== (nul(want) ?? null)) out.push(`${field}: ${JSON.stringify(row[field])} != ${JSON.stringify(want)}`);
  if (!near(row.value, d.value)) out.push(`value: ${row.value} != ${d.value}`);
  if (!near(row.target, d.target)) out.push(`target: ${row.target} != ${d.target}`);
  for (const [mine, theirs] of LIMITS) if (!near(row[mine], d.limits[theirs])) out.push(`${mine}: ${row[mine]} != ${d.limits[theirs]}`);
  // every measurement, in the record's order: the headline first (its limits on it), then the Result's measurements
  const want = [{ key: d.metric, value: d.value, unit: d.unit, ...d.limits }, ...(d.measurements || [])];
  const got = Array.isArray(row._measurements) ? row._measurements : null;
  if (!got || got.length !== want.length) out.push(`_measurements: ${got ? got.length : 'none'} of ${want.length}`);
  else {
    want.forEach((m, i) => {
      const g = got[i];
      if (g.key !== m.key || !near(g.value, m.value) || (g.unit || '') !== (m.unit || '')) out.push(`measurement ${i}: ${JSON.stringify(g)} != ${JSON.stringify(m)}`);
      if (i === 0) for (const [, theirs] of LIMITS) if (!near(g[theirs], m[theirs])) out.push(`measurement 0 ${theirs}: ${g[theirs]} != ${m[theirs]}`);
    });
  }
  return out;
}

// ---- reading each hint's stock view off the page (data-ui's own markup: CONTRACT §26, §31, §35) ------------------------------------
// every reader returns {rect: {w, h}, ...what it draws}, or null while the view is not there
function readViews(names) {
  const rect = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; };
  const shown = (el) => { const r = rect(el); return r && r.w > 0 && r.h > 0; };
  const views = [...document.querySelectorAll('#dx-detail data-views')].filter((v) => v.view && shown(v));
  const out = {};
  // threshold_band: a plot whose legend has the check, its limit rules, its newest drawn value (the series' place in the legend is
  // its place in uPlot's data, after x)
  const plot = views.find((v) => v.view.type === 'plot' && [...v.querySelectorAll('[data-legend-series]')].some((b) => b.dataset.legendSeries === names.threshold_band));
  if (plot) {
    const el = [...plot.querySelectorAll('*')].find((x) => x.plotView);
    const u = el && el.uplot;
    const k = [...plot.querySelectorAll('[data-legend-series]')].map((b) => b.dataset.legendSeries).indexOf(names.threshold_band);
    const ys = u && u.data && u.data.length > k + 1 ? [...u.data[k + 1]].filter((y) => y !== null && y !== undefined) : [];
    const limits = {};
    for (const g of (el && el.plotView && el.plotView.guides) || []) {
      if (!String(g.guide).startsWith('limit:')) continue;
      if (g.series !== null && g.series !== undefined && g.series !== names.threshold_band && !(g.shared || []).includes(names.threshold_band)) continue;
      const pts = g.points || [];
      limits[String(g.guide).slice(6)] = typeof g.constant === 'number' ? g.constant : pts.length ? pts[pts.length - 1][1] : null;
    }
    out.threshold_band = { rect: rect(plot.querySelector('.dv-plot-box') || plot), points: Number(el && el.dataset.points), last: ys.length ? ys[ys.length - 1] : null, limits };
  }
  // kpi_trend: the KPI card whose label is the check
  const card = [...document.querySelectorAll('#dx-detail data-kpi')].find((k) => ((k.querySelector('.dv-kpi-label') || {}).textContent || '').trim() === names.kpi_trend);
  if (card && shown(card)) {
    const num = card.querySelector('.dv-kpi-num');
    const exact = num ? (num.dataset.exact || (num.getAttribute('title') || '').split(' ')[0] || num.textContent) : null;
    out.kpi_trend = { rect: rect(card), value: exact === null ? null : Number(String(exact).replace(/,/g, '')), foot: ((card.querySelector('.dv-kpi-foot') || {}).textContent || '').trim(),
      spark: Boolean(card.querySelector('.dv-kpi-spark svg path, .dv-kpi-spark svg polyline')) };
  }
  // level_history: the check's levels strip, the newest bin that holds a level
  const strip = [...document.querySelectorAll('#dx-detail [data-strip]')].find((s) => s.dataset.strip === names.level_history && shown(s));
  if (strip) {
    const bins = [...strip.querySelectorAll('[data-bin]')].filter((b) => b.dataset.level);
    out.level_history = { rect: rect(strip), last: bins.length ? bins[bins.length - 1].dataset.level : null, bins: bins.length };
  }
  // gate_matrix: a tracker matrix (role=grid): its columns, its rows' labels and every cell's state
  const grid = [...document.querySelectorAll('#dx-detail [role=grid]')].find((g) => shown(g) && g.querySelector('[role=gridcell]'));
  if (grid) {
    out.gate_matrix = { rect: rect(grid), columns: [...grid.querySelectorAll('[role=columnheader][data-column]')].map((h) => h.dataset.column),
      rows: [...grid.querySelectorAll('[role=row]')].filter((r) => r.querySelector('[role=rowheader]')).map((r) => ({
        label: r.querySelector('[role=rowheader]').textContent.trim(), cells: [...r.querySelectorAll('[role=gridcell]')].map((c) => c.dataset.state) })) };
  }
  // rollup_tree: a table with a tree: its rows' text and each row's status badge (display: status)
  const tree = views.find((v) => v.view.type === 'table' && v.view.tree && [...v.querySelectorAll('tbody tr')].some((tr) => tr.textContent.includes(names.rollup_tree)));
  if (tree) {
    out.rollup_tree = { rect: rect(tree.querySelector('table') || tree), rows: [...tree.querySelectorAll('tbody tr')].map((tr) => {
      const s = tr.querySelector('.dv-state');
      return { text: tr.textContent.replace(/\s+/g, ' ').trim(), level: Number(tr.getAttribute('aria-level')) || null, state: s ? s.dataset.state : null,
        glyph: s && s.querySelector('.dv-state-glyph') ? s.querySelector('.dv-state-glyph').textContent : null, word: s && s.querySelector('.dv-state-word') ? s.querySelector('.dv-state-word').textContent : null };
    }) };
  }
  // distribution: a chart (Vega-Lite) of the check (its view named for it, or its drawn rows naming it), the rows it draws
  for (const v of views.filter((x) => x.view.type === 'chart')) {
    const el = [...v.querySelectorAll('*')].find((x) => x.vegaView);
    let rows = [];
    try { rows = el ? el.vegaView.data('rows') : []; } catch (_) { rows = []; }
    const mine = v.view.name === names.distribution || rows.some((r) => Object.values(r).some((x) => typeof x === 'string' && x.includes(names.distribution)));
    if (!rows.length || !mine) continue;
    const canvas = v.querySelector('canvas');
    const yOf = (r) => (typeof r.y === 'number' ? r.y : typeof r.value === 'number' ? r.value : null);
    out.distribution = { rect: rect(canvas || v), ys: rows.map(yOf).filter((y) => typeof y === 'number').sort((a, b) => a - b), n: rows.length };
  }
  return out;
}

(async () => {
  let host;
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'dx-hints-'));
  const frameFile = path.join(scratch, 'records.json');
  const useFrame = (frame) => fs.writeFileSync(frameFile, JSON.stringify({ frame, records: FIXTURE.frames[frame].map((x) => x.record) }));
  useFrame('A');
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding'] });
  try {
    host = await H.fixtureHost('F1', { STATE_DX_RECORDS: frameFile });
    const { base } = host;
    const names = Object.fromEntries(FIXTURE.frames.A.map((x) => [x.hint, x.record.name]));
    const items = FIXTURE.frames.A;

    // ---- the host serves the fixture: each stock diagnostic on CAN 1, with its hint (the precondition) --------------------------------
    const served = await H.waitFor(async () => {
      const node = await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(BOARD)}`);
      const checks = node.checks || [];
      const hintOf = (c) => ((c.values || []).find((v) => v.key === 'dx.hint') || {}).value;
      return items.every((x) => checks.some((c) => c.name === x.record.name && hintOf(c) === x.hint)) ? checks : null;
    }, 30000);
    check(`the host serves the fixture's ${items.length} stock diagnostics on CAN 1, each with its dx.hint (its own /node)`, Boolean(served),
      { want: items.map((x) => `${x.record.name} (${x.hint})`) });

    const page = await (await H.newContext(browser, 'light')).newPage();
    const errs = H.watchErrors(page, base);
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(BOARD)}&page=diagnostics`);
    await page.waitForFunction(() => window.robotdx && document.querySelector('#dx-detail .dd-panel'), null, { timeout: 30000 });

    // ---- the row: what catalog.dress put on each Result survives the feed's conversion ---------------------------------------------------
    const rowsOf = (ids) => page.evaluate((list) => {
      const ds = window.robotdx.feed.dataset('checks');
      return list.map((id) => { const r = ds.latestAt(Infinity, id); return r ? JSON.parse(JSON.stringify(r)) : null; });
    }, ids);
    const rows = await H.waitFor(async () => { const rs = await rowsOf(items.map(idOf)); return rs.every(Boolean) ? rs : null; }, 20000) || await rowsOf(items.map(idOf));
    items.forEach((item, i) => {
      const diff = rowDiff(rows[i], item);
      check(`row: ${item.hint} (${item.record.name}) keeps its view and its ${1 + (item.dress.measurements || []).length} measurements, as catalog.dress made them`, diff.length === 0, diff.slice(0, 6));
    });

    // ---- each hint's stock view, frame A ---------------------------------------------------------------------------------------------
    const A = Object.fromEntries(items.map((x) => [x.hint, x]));
    const wantA = (v) => v.threshold_band && near(v.threshold_band.last, A.threshold_band.dress.value) && v.kpi_trend && near(v.kpi_trend.value, A.kpi_trend.dress.value) && v.kpi_trend.spark
      && v.level_history && v.gate_matrix && v.rollup_tree && v.distribution;
    let views = await H.waitFor(async () => { const v = await page.evaluate(readViews, names); return wantA(v) ? v : null; }, 30000) || await page.evaluate(readViews, names);
    const on = (r) => Boolean(r && r.w > 0 && r.h > 0);

    const tb = A.threshold_band.dress, tbv = views.threshold_band;
    const wantLimits = Object.fromEntries(LIMITS.filter(([, k]) => tb.limits[k] !== null && tb.limits[k] !== undefined).map(([mine, k]) => [mine, tb.limits[k]]));
    check('threshold_band: a plot of the check over time, on screen, its newest value the headline, a warn and an error limit drawn at the limits',
      Boolean(tbv) && on(tbv.rect) && tbv.points > 0 && near(tbv.last, tb.value) && Object.keys(wantLimits).length > 0
        && Object.entries(wantLimits).every(([k, v]) => near(tbv.limits[k], v)) && Object.keys(tbv.limits).every((k) => k in wantLimits),
      { view: tbv, want: { value: tb.value, limits: wantLimits } });

    const kt = A.kpi_trend.dress, ktv = views.kpi_trend;
    check('kpi_trend: a KPI card of the check, on screen, its number the headline, its sparkline, its target',
      Boolean(ktv) && on(ktv.rect) && near(ktv.value, kt.value) && ktv.spark && ktv.foot.includes(`target ${kt.target}`),
      { view: ktv, want: { value: kt.value, target: kt.target } });

    const lh = A.level_history.record, lhv = views.level_history;
    check('level_history: a levels strip of the check, on screen, its newest bin the check\'s level',
      Boolean(lhv) && on(lhv.rect) && lhv.last === lh.level, { view: lhv, want: lh.level });

    const gm = A.gate_matrix.dress, gmv = views.gate_matrix;
    const pass = gmv ? gmv.rows.flatMap((r) => r.cells).filter((s) => s === 'pass').length : -1;
    check('gate_matrix: a tracker matrix, on screen, its columns the gates, its rows the subjects, every cell the payload\'s verdict, as many passing as the headline',
      Boolean(gmv) && on(gmv.rect) && gmv.columns.join() === gm.payload.cols.join() && gmv.rows.map((r) => r.label).join() === gm.payload.rows.join()
        && gmv.rows.every((r, i) => r.cells.join() === gm.payload.cells[i].join()) && pass === gm.value,
      { view: gmv, want: { cols: gm.payload.cols, rows: gm.payload.rows, cells: gm.payload.cells, passing: gm.value } });

    const rt = A.rollup_tree, rtv = views.rollup_tree;
    const members = rt.dress.payload.x.map((label, i) => ({ label, verdict: rt.dress.payload.group[i] }));
    const rowFor = (label) => rtv && rtv.rows.find((r) => r.text.includes(label));
    check('rollup_tree: a table tree, on screen: the roll-up with its level, under it each member with its verdict, as many members as the headline says',
      Boolean(rtv) && on(rtv.rect) && rowFor(rt.record.name) && rowFor(rt.record.name).state === rt.record.level
        && members.every((m) => rowFor(m.label) && rowFor(m.label).state === m.verdict && rowFor(m.label).level > rowFor(rt.record.name).level)
        && members.length === measure(rt.dress, 'members'),
      { view: rtv, want: { root: [rt.record.name, rt.record.level], members, count: measure(rt.dress, 'members') } });
    check('status: the roll-up\'s levels and verdicts are data-ui\'s status badges (display: status): a glyph and the word',
      Boolean(rtv) && rtv.rows.filter((r) => r.state).length >= members.length + 1 && rtv.rows.filter((r) => r.state).every((r) => r.glyph && r.word && r.word.replace(/ /g, '_') === r.state.replace(/ /g, '_')),
      rtv && rtv.rows.map((r) => [r.state, r.glyph, r.word]));

    const ds = A.distribution.dress, dsv = views.distribution;
    const wantYs = [...ds.payload.y].sort((a, b) => a - b);
    check('distribution: a chart of the check\'s points, on screen, drawing exactly its payload\'s values, as many as the headline\'s n',
      Boolean(dsv) && on(dsv.rect) && dsv.ys.length === wantYs.length && dsv.ys.every((y, i) => near(y, wantYs[i])) && dsv.n === measure(ds, 'n'),
      { view: dsv, want: { ys: wantYs, n: measure(ds, 'n') } });

    // evidence: the row's side peek embeds a view of the check's own data (record.sections[].view)
    const ev = A.evidence;
    let evidence = null;
    try {
      const rowAt = await page.evaluateHandle((name) => [...document.querySelectorAll('#dx-detail-diagnostics tbody tr')].find((tr) => tr.textContent.includes(name)) || null, ev.record.name);
      const el = rowAt.asElement();
      if (el) await el.click();
      evidence = await H.waitFor(() => page.evaluate((name) => {
        const peek = [...document.querySelectorAll('#dx-detail .dv-peek')].find((p) => !p.hidden && p.getBoundingClientRect().width > 0);
        if (!peek || !(peek.querySelector('h2') || {}).textContent || !peek.querySelector('h2').textContent.includes(name)) return null;
        const slot = peek.querySelector('.dv-peek-view[data-section] data-views');
        if (!slot || !slot.model) return null;
        const r = slot.getBoundingClientRect();
        const values = (slot.model.rows || []).map((x) => (typeof x.value === 'number' ? x.value : x.value && typeof x.value === 'object' ? x.value.value : null)).filter((v) => typeof v === 'number');
        return values.length ? { rect: { w: Math.round(r.width), h: Math.round(r.height) }, type: slot.view && slot.view.type, rows: slot.model.rows.length, values: values.sort((a, b) => a - b) } : null;
      }, ev.record.name), 10000);
    } catch (err) { evidence = { error: err.message }; }
    const cells = ev.dress.payload.cells.flat().filter((v) => typeof v === 'number').sort((a, b) => a - b);
    check('evidence: the check\'s side peek embeds a view of its data (record.sections[].view), on screen, drawing its payload\'s values, its largest the headline\'s max_pair',
      Boolean(evidence && evidence.rect) && on(evidence.rect) && evidence.values.length === cells.length && evidence.values.every((v, i) => near(v, cells[i]))
        && near(evidence.values[evidence.values.length - 1], measure(ev.dress, 'max_pair')),
      { view: evidence && { ...evidence, values: evidence.values && evidence.values.slice(-3) }, want: { n: cells.length, max: measure(ev.dress, 'max_pair') } });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    // ---- the fallbacks: a hint no data-ui view has, and path_overlay with no pose data ------------------------------------------------
    // what draws a check besides the checks table and the Health strips (every check has one there): the views on screen, the
    // dashboard's drill peek left out (it is the checks table again, closed)
    const elsewhere = (name) => page.evaluate((n) => {
      const table = document.querySelector('#dx-detail-diagnostics');
      const listed = Boolean(table && table.model && table.model.rows.some((r) => r.name === n));
      const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !el.closest('.dd-drill'); };
      const other = [];
      if ([...document.querySelectorAll('#dx-detail [data-legend-series]')].some((b) => shown(b) && b.dataset.legendSeries === n)) other.push('a plot');
      if ([...document.querySelectorAll('#dx-detail data-kpi .dv-kpi-label')].some((l) => shown(l) && l.textContent.trim() === n)) other.push('a KPI card');
      if ([...document.querySelectorAll('#dx-detail [role=grid]')].some((g) => shown(g) && g.textContent.includes(n))) other.push('a tracker');
      for (const v of document.querySelectorAll('#dx-detail data-views')) {
        if (v === table || !v.view || !shown(v)) continue;
        if (v.view.type === 'table' && [...v.querySelectorAll('tbody tr')].some((tr) => tr.textContent.includes(n))) other.push(`a table (${v.view.name})`);
        if (v.view.type === 'chart') {
          const el = [...v.querySelectorAll('*')].find((x) => x.vegaView);
          let rows = [];
          try { rows = el ? el.vegaView.data('rows') : []; } catch (_) { rows = []; }
          if (v.view.name === n || rows.some((r) => Object.values(r).some((x) => typeof x === 'string' && x.includes(n)))) other.push('a chart');
        }
        if (/trajectory3d|scene3d/.test(v.view.type)) other.push(`a ${v.view.type}`);
      }
      // the tab's own words about the check: a panel other than the checks table that names it
      const said = [...document.querySelectorAll('#dx-detail .dd-panel')].filter((p) => shown(p) && !p.contains(table) && !p.querySelector('[data-strip]') && p.textContent.includes(n))
        .map((p) => p.textContent.replace(/\s+/g, ' ').trim());
      return { listed, other, said };
    }, name);
    const unknown = await elsewhere(names[UNKNOWN]);
    check(`a hint no data-ui view has (${UNKNOWN}) falls back to the checks table, and no stock view draws it`, unknown.listed && unknown.other.length === 0, unknown);
    const overlay = await elsewhere(names.path_overlay);
    const trajectory = await page.evaluate(() => [...document.querySelectorAll('#dx-detail data-views')].filter((v) => v.view && /trajectory3d|scene3d/.test(v.view.type)).length);
    check('path_overlay on an entity with no pose data: no 3D view, the check in the checks table, and the tab says why (no pose)',
      overlay.listed && trajectory === 0 && overlay.other.length === 0 && overlay.said.some((s) => /pose/i.test(s)), { overlay, trajectory });

    // ---- rewind: the fixture moves on (frame B); at a t between the frames every view draws frame A, and B when live ---------------------
    useFrame('B');
    const B = Object.fromEntries(FIXTURE.frames.B.map((x) => [x.hint, x]));
    const wantB = (v) => v.kpi_trend && near(v.kpi_trend.value, B.kpi_trend.dress.value) && v.threshold_band && near(v.threshold_band.last, B.threshold_band.dress.value)
      && v.gate_matrix && v.gate_matrix.rows.every((r, i) => r.cells.join() === B.gate_matrix.dress.payload.cells[i].join());
    const liveB = await H.waitFor(async () => { const v = await page.evaluate(readViews, names); return wantB(v) ? v : null; }, 30000);
    check('the fixture moved on: live, the views draw frame B (the KPI, the plot\'s newest value, the matrix)', Boolean(liveB), liveB || await page.evaluate(readViews, names));
    // the moment between the frames, from the host's own history of the KPI check (its last A point, its first B point)
    const hist = await H.getJson(base, `/api/v1/history?path=${encodeURIComponent(BOARD)}&status=${encodeURIComponent(names.kpi_trend)}`);
    const pts = (((hist.statuses || [])[0] || {}).measurements || []).find((m) => m.key === A.kpi_trend.dress.metric);
    const aT = pts ? Math.max(...pts.points.filter(([, v]) => near(v, A.kpi_trend.dress.value)).map(([t]) => t)) : NaN;
    const bT = pts ? Math.min(...pts.points.filter(([, v]) => near(v, B.kpi_trend.dress.value)).map(([t]) => t)) : NaN;
    const t = (aT + bT) / 2;
    const atT = await page.evaluate(async ({ t, names, read }) => {
      // eslint-disable-next-line no-new-func
      const readViews = new Function(`return (${read})`)();
      const draws = [];
      const onDraw = (e) => { if (e.target.closest && e.target.closest('#dx-detail')) draws.push({ t: e.detail.t, following: e.detail.following, view: e.target.view && e.target.view.type }); };
      document.addEventListener('data-views-live', onDraw, true);
      window.robotdx.clock.seek(t);
      await new Promise((r) => setTimeout(r, 1200));
      document.removeEventListener('data-views-live', onDraw, true);
      return { t: window.robotdx.clock.t, following: window.robotdx.clock.following, views: readViews(names), draws: draws.filter((d) => d.t === t && !d.following).map((d) => d.view) };
    }, { t, names, read: readViews.toString() });
    const v = atT.views;
    const rtA = (label) => v.rollup_tree && v.rollup_tree.rows.find((r) => r.text.includes(label));
    const fails = [];
    if (!(Number.isFinite(aT) && Number.isFinite(bT) && aT < bT)) fails.push(`no A then B in the host's history: ${aT}, ${bT}`);
    if (!(v.kpi_trend && near(v.kpi_trend.value, A.kpi_trend.dress.value))) fails.push(`kpi ${v.kpi_trend && v.kpi_trend.value}`);
    if (!(v.threshold_band && near(v.threshold_band.last, A.threshold_band.dress.value))) fails.push(`plot ${v.threshold_band && v.threshold_band.last}`);
    if (!(v.level_history && v.level_history.last === A.level_history.record.level)) fails.push(`levels ${v.level_history && v.level_history.last}`);
    if (!(v.gate_matrix && v.gate_matrix.rows.every((r, i) => r.cells.join() === A.gate_matrix.dress.payload.cells[i].join()))) fails.push(`matrix ${v.gate_matrix && JSON.stringify(v.gate_matrix.rows)}`);
    if (!(rtA(A.rollup_tree.record.name) && rtA(A.rollup_tree.record.name).state === A.rollup_tree.record.level
      && A.rollup_tree.dress.payload.x.every((label, i) => rtA(label) && rtA(label).state === A.rollup_tree.dress.payload.group[i]))) fails.push(`tree ${v.rollup_tree && JSON.stringify(v.rollup_tree.rows.map((r) => r.state))}`);
    const ysA = [...A.distribution.dress.payload.y].sort((x, y) => x - y);
    if (!(v.distribution && v.distribution.ys.length === ysA.length && v.distribution.ys.every((y, i) => near(y, ysA[i])))) fails.push(`chart ${v.distribution && v.distribution.ys}`);
    for (const type of ['plot', 'kpi', 'table', 'tracker', 'levels', 'chart']) if (!atT.draws.includes(type)) fails.push(`no ${type} drawn at t`);
    check('rewind: drawn at a t between the frames (the host\'s own history), every hint\'s view shows frame A: the KPI, the plot, the levels, the matrix, the tree, the chart',
      fails.length === 0 && atT.t === t && !atT.following, { t, aT, bT, fails, draws: [...new Set(atT.draws)] });
    await page.evaluate(() => window.robotdx.clock.goLive());
    const back = await H.waitFor(async () => { const x = await page.evaluate(readViews, names); return wantB(x) && x.rollup_tree && x.rollup_tree.rows.some((r) => r.text.includes(B.rollup_tree.record.name) && r.state === B.rollup_tree.record.level) ? x : null; }, 10000);
    check('…and back to live, frame B again (the KPI, the plot, the matrix, the roll-up\'s level)', Boolean(back), back ? 'B' : await page.evaluate(readViews, names));

    check('no page errors, no console errors, no failed request', errs.errors.length === 0, errs.errors.slice(0, 5));
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close().catch(() => {});
    if (host) H.stopServer(host.child);
    fs.rmSync(scratch, { recursive: true, force: true });
  }
  process.exit(H.summary());
})();
