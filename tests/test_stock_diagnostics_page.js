// A stock diagnostic's drill-in on the entity page (io_diagnostics/stock_diagnostics.py, detail/d6-rows.js "a stock diagnostic's drill-in"),
// in the real page: a Diagnostics row whose source is robotdx.diagnostics has a chevron and unfolds in place (measured) to its title and
// doc, its config as a small form, the measured value against its warn / error Limits and the window it ran on; a limit edited there is
// accepted and written to diagnostics.toml, and the status recomputes on the next beat; an invalid value is rejected with the reason and
// nothing is written; ↺ resets a value to the dataclass default. Screenshots at 1440x900.
//
//   node tests/test_stock_diagnostics_page.js [--shots DIR]     (a fixture host on a scratch port; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, BOARD, fs, os, path } = H;

const SHOTS = (() => { const at = process.argv.indexOf('--shots'); return at > 0 ? process.argv[at + 1] : path.join(H.ROOT, 'reports', 'media', 'robotdx-diagnostics'); })();
const VIEWPORT = { width: 1440, height: 900 };
// the default set's two moteus rows, on a short window so the fixture's 2 s diagnose fills one quickly (a replay never reads the default set)
const TOML = fs.readFileSync(path.join(H.ROOT, 'io_diagnostics', 'diagnostics_default.toml'), 'utf8').replace(/window_s = 60/g, 'window_s = 6');

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'stock-dx-page-'));
  fs.mkdirSync(path.join(root, 'config'), { recursive: true });
  const file = path.join(root, 'config', 'diagnostics.toml');
  fs.writeFileSync(file, TOML);
  const server = await H.startServer(root, 'F1');
  const { base } = server;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const plugins = await H.getJson(base, '/api/v1/plugins');
    check('only the two fixture plugins are loaded', plugins.plugins.map((p) => p.name).sort().join() === 'state_camera,state_joint');
    const stock = (checks) => (checks || []).find((c) => c.name === 'CAN 1: board temperature');
    const ready = await H.waitFor(async () => { const node = await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(BOARD)}`); const c = stock(node.checks); return c && c.measurements[0] && c.measurements[0].value !== null ? c : null; }, 60000, 500);
    check('the board temperature runs on the board, from the library', ready && ready.source === 'robotdx.diagnostics' && ready.effective_level === 'ok', ready && { level: ready.effective_level, message: ready.message });

    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    await H.open(page, base, BOARD, 'diagnostics');
    await page.waitForSelector('[data-dxc-chev]', { timeout: 15000 });

    // ---- the chevron: only on the library's rows ---------------------------------------------------------------------------
    const rows = await page.evaluate(() => [...document.querySelectorAll('.dx-r')].map((r) => ({ name: r.querySelector('.dx-nt').textContent, chev: Boolean(r.querySelector('[data-dxc-chev]')) })));
    const chevrons = rows.filter((r) => r.chev).map((r) => r.name).sort();
    check('a chevron on each robotdx.diagnostics row and on no other', chevrons.join() === 'board temperature,fault and limiter codes' && rows.some((r) => !r.chev), rows);
    await page.screenshot({ path: path.join(SHOTS, '01-diagnostics-tab.png') });

    // ---- unfold in place, measured ---------------------------------------------------------------------------------------
    const rowOf = '.dx-r:has([data-dxc-chev]):has(.dx-nt:text-is("board temperature"))';
    const before = await page.evaluate(() => { const r = [...document.querySelectorAll('.dx-r')].find((x) => x.querySelector('.dx-nt').textContent === 'board temperature'); const b = r.getBoundingClientRect(); return { top: b.top, height: b.height }; });
    await page.click(`${rowOf} .dx-line`);
    await page.waitForSelector('.dxc .dxc-form', { timeout: 10000 });
    await page.waitForTimeout(400);
    const open = await page.evaluate(() => {
      const r = [...document.querySelectorAll('.dx-r')].find((x) => x.querySelector('.dx-nt').textContent === 'board temperature');
      const box = r.getBoundingClientRect(); const body = r.querySelector('.dxc').getBoundingClientRect();
      const fields = [...r.querySelectorAll('[data-dxc-row]')].map((f) => ({ field: f.dataset.dxcRow, source: f.querySelector('.dxc-src').textContent,
        values: Object.fromEntries([...f.querySelectorAll('input[data-dxc-field]')].map((i) => [i.dataset.dxcField, i.value])) }));
      return { top: box.top, height: box.height, body: { top: body.top, bottom: body.bottom, left: body.left, right: body.right }, rowBox: { left: box.left, right: box.right, bottom: box.bottom },
        chevOpen: r.querySelector('[data-dxc-chev]').classList.contains('open'), title: r.querySelector('.dxc-h').textContent, doc: r.querySelector('.dxc-doc').textContent,
        gauge: r.querySelector('.dxc-g').textContent, band: Boolean(r.querySelector('[data-dxc-band] .dxc-v')), window: r.querySelector('[data-dxc-window]').textContent, fields };
    });
    check('it unfolds in place: the row grows, its top stays, the drill-in sits inside it', open.height > before.height + 120 && Math.abs(open.top - before.top) < 2
      && open.body.top >= open.top && open.body.bottom <= open.rowBox.bottom + 1 && open.body.left >= open.rowBox.left - 1 && open.body.right <= open.rowBox.right + 1 && open.chevOpen, { before, open: { top: open.top, height: open.height, body: open.body } });
    check('title and doc', /board temperature/.test(open.title) && /stock\.box · driver\.board_temp/.test(open.title) && /min\/max\/mean\/std/.test(open.doc), { title: open.title, doc: open.doc });
    check('the measured value against its warn / error Limits, with a band', /max/.test(open.gauge) && /warn above 60 Cel/.test(open.gauge) && /error above 70 Cel/.test(open.gauge) && open.band, open.gauge);
    check('the window it ran on', /Ran on the last 6 s of State history \(motor\)/.test(open.window) && /samples/.test(open.window) && /re-run every 5 s/.test(open.window), open.window);
    const max = open.fields.find((f) => f.field === 'max');
    check('its config as a form: a Limits field is one row of four boxes, each value with where it came from', max && max.source === 'toml'
      && max.values['max.warn_high'] === '60' && max.values['max.error_high'] === '70' && max.values['max.warn_low'] === '' && Object.keys(max.values).length === 4
      && open.fields.some((f) => f.field === 'channel' && f.source === 'toml' && f.values.channel === 'temp_c') && !open.fields.some((f) => f.field === 'min'), open.fields);
    await page.click(`${rowOf} [data-dxc-all]`);
    const everything = await page.evaluate(() => [...document.querySelectorAll('.dxc[data-dxc="driver.board_temp"] [data-dxc-row]')].map((f) => [f.dataset.dxcRow, f.querySelector('.dxc-src').textContent]));
    check('Show all: the fields at their dataclass default too', everything.some(([f, src]) => f === 'min' && src === 'default') && everything.some(([f, src]) => f === 'skip_prelude' && src === 'default'), everything);
    await page.click(`${rowOf} [data-dxc-all]`);
    await page.screenshot({ path: path.join(SHOTS, '02-drill-in-open.png') });

    // ---- edit a limit: accepted, written, and the status recomputes on the next beat --------------------------------------------
    const input = `${rowOf} input[data-dxc-field="max.warn_high"]`;  // one box of the max row
    await page.fill(input, '30');
    await page.press(input, 'Enter');
    await page.waitForSelector(`${rowOf} [data-dxc-chips="max.warn_high"] .er-chip.ok`, { timeout: 10000 });
    const accepted = await page.evaluate(() => { const f = document.querySelector('[data-dxc-row="max"]'); return { chip: f.querySelector('[data-dxc-chips="max.warn_high"] .er-chip.ok').textContent, source: f.querySelector('.dxc-src').textContent, value: f.querySelector('input[data-dxc-field="max.warn_high"]').value }; });
    check('the edit is accepted, read back, and marked edited', /accepted/.test(accepted.chip) && accepted.source === 'edited' && accepted.value === '30', accepted);
    const written = fs.readFileSync(file, 'utf8');
    check('it is written to diagnostics.toml (atomically, the whole set)', /max = \{ warn_high = 30\.0, error_high = 70\.0 \}/.test(written) && /edited = \["max\.warn_high"\]/.test(written) && /driver\.faults/.test(written), written.slice(0, 400));
    const t0 = Date.now();
    const recomputed = await H.waitFor(() => page.evaluate(() => { const r = [...document.querySelectorAll('.dx-r')].find((x) => x.querySelector('.dx-nt').textContent === 'board temperature'); return r && /warn/i.test(r.querySelector('.dx-s').textContent) ? { pill: r.querySelector('.dx-s').textContent.trim(), gauge: r.querySelector('.dxc-g').textContent } : null; }), 20000, 250);
    check('the status recomputes on the next beat: max above the new warn limit is WARN', recomputed && /warn above 30 Cel/.test(recomputed.gauge), { recomputed, after_ms: Date.now() - t0 });
    await page.screenshot({ path: path.join(SHOTS, '03-edit-accepted-warn.png') });

    // ---- an invalid value: rejected with its reason, nothing written ----------------------------------------------------------
    await page.fill(input, '80');
    await page.press(input, 'Enter');
    await page.waitForSelector(`${rowOf} [data-dxc-chips="max.warn_high"] .er-chip.bad`, { timeout: 10000 });
    const rejected = await page.evaluate(() => document.querySelector('[data-dxc-chips="max.warn_high"] .er-chip.bad').textContent);
    check('warn above its own error limit is rejected, with the Limits ordering as the reason', /rejected/.test(rejected) && /error_low <= warn_low <= warn_high <= error_high/.test(rejected), rejected);
    check('a rejected value is not written', fs.readFileSync(file, 'utf8') === written);
    // a number box takes no text in the page (the browser refuses it); the host refuses it too, by the field's type
    const direct = await page.evaluate(async (p) => (await fetch('/api/v1/diagnostics/config', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify({ path: p, id: 'driver.board_temp', field: 'max.warn_high', value: 'hot' }) })).json(), BOARD);
    check('the host rejects it too, by the dataclass field type', direct.state === 'rejected' && /not a number/.test(direct.message), direct);
    await page.screenshot({ path: path.join(SHOTS, '04-edit-rejected.png') });

    // ---- reset to the dataclass default ------------------------------------------------------------------------------------------
    await page.click(`${rowOf} [data-dxc-row="max"] [data-dxc-resetopen]`);
    await page.click(`${rowOf} [data-dxc-reset][data-dxc-field="max.warn_high"]`);
    await page.waitForFunction(() => { const f = document.querySelector('[data-dxc-row="max"]'); return f && f.querySelector('.dxc-src').textContent === 'toml' && f.querySelector('input[data-dxc-field="max.warn_high"]').value === ''; }, null, { timeout: 10000 });
    check('↺ resets a limit to the dataclass default (no warn limit) and says so', /reset to default/.test(await page.evaluate(() => document.querySelector('[data-dxc-chips="max.warn_high"] .er-chip.ok').textContent)));

    // ---- the other one, folded; and a dark screenshot -----------------------------------------------------------------------
    await page.click('.dx-r:has(.dx-nt:text-is("fault and limiter codes")) .dx-line');
    await page.waitForSelector('.dxc[data-dxc="driver.faults"] .dxc-form', { timeout: 10000 });
    const faults = await page.evaluate(() => ({ gauge: document.querySelector('.dxc[data-dxc="driver.faults"] .dxc-g').textContent, fields: [...document.querySelectorAll('.dxc[data-dxc="driver.faults"] [data-dxc-row]')].map((f) => f.dataset.dxcRow) }));
    check('the fault codes drill-in: its value against its limit, its toml value', /faults/.test(faults.gauge) && /error above 0/.test(faults.gauge) && faults.fields.includes('limiter_share_warn'), faults);
    await page.screenshot({ path: path.join(SHOTS, '05-two-open.png') });
    const dark = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'dark' });
    const darkPage = await dark.newPage();
    await H.open(darkPage, base, BOARD, 'diagnostics');
    await darkPage.waitForSelector('[data-dxc-chev]', { timeout: 15000 });
    await darkPage.click(`${rowOf} .dx-line`);
    await darkPage.waitForSelector('.dxc .dxc-form', { timeout: 10000 });
    await darkPage.waitForTimeout(400);
    await darkPage.screenshot({ path: path.join(SHOTS, '06-drill-in-dark.png') });
    await dark.close();

    check('no console errors or failed requests', watch.errors.length === 0, watch.errors);
    await context.close();
  } catch (error) {
    check('the suite ran to the end', false, String(error && error.stack || error));
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary(` · screenshots in ${SHOTS}`));
})();
