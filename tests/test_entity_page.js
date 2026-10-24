// The entity page in the real page (docs/state-config-v1.md): the header above the tabs, the tabs and their URL / memory / keys, the
// ticking last-heard clock, Diagnostics (health only, a fault's meaning inline), State (live numbers, image tiles that update and
// toggle, paused in plain words, the heartbeat that stops), Configuration (dot-notation folders, expected vs read, pins and manual
// order that survive a reload and are shared by every board of a kind, live editing with its accepted / rejected / not saved
// states, Save, Revert, reset, a safety key's second confirm), Log, Utilities, a driver's own tab, and the warn-colour rule.
//
//   node tests/test_entity_page.js          (a fixture host on a scratch port; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, BOARD, BOARD2, CAMERA, ADAPTER } = H;

(async () => {
  const servers = [];
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const main = await H.fixtureHost('F1');
    servers.push(main);
    const { base } = main;
    const only = await H.getJson(base, '/api/v1/plugins');
    check('only the two fixture plugins are loaded', only.plugins.map((p) => p.name).sort().join() === 'state_camera,state_joint');

    const context = await H.newContext(browser, 'light', { permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    await H.open(page, base, BOARD);

    // ---- the header and the tabs ----------------------------------------------------------------------------------------
    const head = await page.evaluate(() => ({
      title: document.querySelector('.d6-head .d-title').textContent.trim(),
      badge: (document.querySelector('.d6-badge-c') || {}).textContent, badgeTitle: (document.querySelector('.d6-badge-c') || {}).title,
      heard: (document.querySelector('[data-d6-heard]') || {}).textContent, hw: document.querySelector('.d6-hw').textContent,
      tabs: [...document.querySelectorAll('.d6-tab')].map((t) => t.firstChild.textContent.trim()), active: document.querySelector('.d6-tab.on').dataset.d6Tab,
      utilities: Boolean(document.querySelector('#ent-util-btn')),
    }));
    check('header: name and model, connection badge, heard clock, hardware line, Utilities', /moteus c1 · CAN 1/.test(head.title) && head.badge === 'CAN 1 · fdcanusb fixture'
      && /^heard /.test(head.heard) && /serial 9A31/.test(head.hw) && /firmware 1\.0\.0/.test(head.hw) && /model id mjbots-moteus-c1/.test(head.hw) && head.utilities, head);
    check('tabs in order, Diagnostics the default', head.tabs.join() === 'Diagnostics,State,Configuration,Log,Utilities' && head.active === 'diagnostics', head.tabs);
    check('the board badge names its adapter, its tooltip the USB link as a version and the raw speed', /link USB 1\.1 · 12 Mb\/s/.test(head.badgeTitle), head.badgeTitle);

    // the last-heard clock is first-class and ticks: it counts up between two receipts, and starts again when the host hears the board again
    const ages = [];
    for (let i = 0; i < 14; i += 1) { ages.push(await page.evaluate(() => Number((document.querySelector('[data-d6-heard]').textContent.match(/(\d+)/) || [])[1]))); await page.waitForTimeout(400); }
    const climbs = ages.filter((age, at) => at > 0 && age === ages[at - 1] + 1).length;
    check('the heard clock ticks', climbs >= 2, ages);

    // the header stays above the tabs, always visible: it does not scroll away
    await H.goTab(page, 'config');
    await page.evaluate(() => { document.querySelector('.d6-panel').style.minHeight = '2400px'; document.getElementById('detail').scrollTop = 400; });
    await page.waitForTimeout(200);
    const stuck = await page.evaluate(() => { const top = document.querySelector('.d6-top').getBoundingClientRect(); const detail = document.getElementById('detail').getBoundingClientRect(); return { top: Math.round(top.top - detail.top), scrolled: document.getElementById('detail').scrollTop, sticky: getComputedStyle(document.querySelector('.d6-top')).position }; });
    check('the header and the tab bar stay in view while the panel scrolls', stuck.sticky === 'sticky' && stuck.top <= 1 && stuck.scrolled > 0, stuck);
    await page.evaluate(() => { document.querySelector('.d6-panel').style.minHeight = ''; document.getElementById('detail').scrollTop = 0; });

    // ---- the tab is in the URL, remembered per entity kind, and has keys -----------------------------------------------------------
    await H.goTab(page, 'state');
    check('the chosen tab is in the URL', /[?&]page=state/.test(await page.evaluate(() => location.search)), await page.evaluate(() => location.search));
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('.d6-tab.on') && document.querySelector('.d6-tab.on').dataset.d6Tab === 'config', null, { timeout: 5000 });
    check('Back goes to the tab before', (await H.tabOf(page)) === 'config');
    await page.goForward();
    await page.waitForFunction(() => document.querySelector('.d6-tab.on').dataset.d6Tab === 'state', null, { timeout: 5000 });
    await H.goTab(page, 'config');
    await H.goTo(page, BOARD2);
    await page.waitForFunction(() => document.querySelector('.d6-tab.on'), null, { timeout: 8000 });
    check('another board of the same kind opens on the tab you chose', (await H.tabOf(page)) === 'config', await H.tabOf(page));
    await H.goTo(page, BOARD);
    await page.focus('.d6-tab.on');
    await page.keyboard.press('2');
    await page.waitForFunction(() => document.querySelector('.d6-tab.on').dataset.d6Tab === 'state', null, { timeout: 5000 });
    check('keys: 2 with the tab bar focused opens the second tab', (await H.tabOf(page)) === 'state');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('.d6-tab.on').dataset.d6Tab === 'config', null, { timeout: 5000 });
    await page.keyboard.press('Control+PageDown');
    await page.waitForFunction(() => document.querySelector('.d6-tab.on').dataset.d6Tab === 'log', null, { timeout: 5000 }).catch(() => {});
    check('keys: ArrowRight and Ctrl+PageDown move one tab on', (await H.tabOf(page)) === 'log', await H.tabOf(page));

    // ---- Diagnostics: health only ---------------------------------------------------------------------------------------------
    await H.goTab(page, 'diagnostics');
    const diag = await page.evaluate(() => ({ text: document.querySelector('.d6-panel').textContent, badge: (document.querySelector('[data-d6-tab="diagnostics"] .d6-bd') || {}).textContent,
      fx: (document.querySelector('.d6-fx') || {}).textContent, stateBadge: Boolean(document.querySelector('[data-d6-tab="state"] .d6-bd')), cfgBadge: Boolean(document.querySelector('[data-d6-tab="config"] .d6-bd')) }));
    check('Diagnostics: a fault code reads with its meaning inline', diag.fx === '33 · motor driver fault on bridge enable', diag.fx);
    check('Diagnostics tab badge: the worst mark and the count; no badge elsewhere when all is well', diag.badge === '1' && !diag.stateBadge && !diag.cfgBadge, diag);
    check('Diagnostics shows no live number (a value is never health)', !/24\.\d+ V|rev\/s|N\.m/.test(diag.text), diag.text.slice(0, 120));

    // ---- State: live numbers --------------------------------------------------------------------------------------------------
    await H.goTab(page, 'state');
    await page.waitForFunction(() => (document.querySelector('[data-d6-watch]') || {}).dataset.d6Watch === 'running', null, { timeout: 15000 });
    const note = await page.evaluate(() => document.querySelector('.d6-note').textContent);
    check('State: live, said plainly', /live · every 0\.1 s/.test(note), note);
    const samples = [];
    for (let i = 0; i < 4; i += 1) { samples.push(await page.evaluate(() => [...document.querySelectorAll('.d6-sr')].map((r) => [r.querySelector('.d6-sn').title, r.querySelector('.d6-sv').textContent.trim()]))); await page.waitForTimeout(400); }
    const byName = (s, name) => (s.find((r) => r[0] === name) || [])[1];
    check('State: single-line rows name · value · sparkline', await page.evaluate(() => [...document.querySelectorAll('.d6-sr')].every((r) => r.children.length === 3 && r.querySelector('.d6-ss svg, .d6-ss .d6-nospark'))), samples[0].length);
    check('State: the numbers update live', new Set(samples.map((s) => byName(s, 'position'))).size >= 3 && new Set(samples.map((s) => byName(s, 'voltage'))).size >= 2, samples.map((s) => byName(s, 'position')));
    check('State: an enumeration and a code read as their meaning', byName(samples[0], 'mode') === '10 · position' && byName(samples[0], 'fault') === '33 · motor driver fault on bridge enable');
    check('State: a sparkline per number', (await page.evaluate(() => document.querySelectorAll('.d6-sr .d6-sp .ln').length)) >= 5);
    check('State: no health word on a value row (state is not health)', !(await page.evaluate(() => /\b(ok|error|warn)\b/i.test([...document.querySelectorAll('.d6-sr')].map((r) => r.textContent).join(' ')))));
    const watching = await H.getJson(base, `/api/v1/values?path=${encodeURIComponent(BOARD)}`);
    check('the host runs the plugin watch while the State tab is showing', watching.watch.state === 'running' && watching.watch.pid > 0, watching.watch);
    const pid = watching.watch.pid;

    // leaving the tab stops the heartbeat: the host stops the watch about its grace (10 s) after the last one
    await H.goTab(page, 'diagnostics');
    const stopped = await H.waitFor(async () => (await H.getJson(base, `/api/v1/values?path=${encodeURIComponent(BOARD)}`)).watch.state === 'idle', 20000, 500);
    const alive = require('fs').existsSync(`/proc/${pid}`) && !/^[ZX]/.test(require('fs').readFileSync(`/proc/${pid}/stat`, 'utf8').split(') ')[1] || '');
    check('leaving the State tab ends the watch within its grace period', stopped && !alive, { stopped, alive });
    await H.goTab(page, 'state');
    await page.waitForFunction(() => (document.querySelector('[data-d6-watch]') || {}).dataset.d6Watch === 'running', null, { timeout: 15000 });
    await page.evaluate(() => { select('host/usb1/port-1-6/usb-1-6'); pushNav(); render(); });
    const leftEntity = await H.waitFor(async () => (await H.getJson(base, `/api/v1/values?path=${encodeURIComponent(BOARD)}`)).watch.state === 'idle', 20000, 500);
    check('leaving the entity ends its watch too', leftEntity);

    // ---- State of a camera: images ---------------------------------------------------------------------------------------------
    await H.goTo(page, CAMERA);
    await H.goTab(page, 'state');
    await page.waitForSelector('.d6-tile img', { timeout: 20000 });
    const camera = await page.evaluate(() => ({ order: [...document.querySelector('.d6-panel').children].map((c) => c.className.split(' ')[0] || c.tagName).join(' '),
      tiles: [...document.querySelectorAll('.d6-tile')].map((t) => ({ name: t.querySelector('.d6-sw-t').textContent, meta: t.querySelector('.d6-dim').textContent, on: t.querySelector('input').checked })),
      badge: document.querySelector('.d6-badge-c').textContent }));
    check('camera State: image tiles come before the numbers', camera.order.indexOf('d6-block') < camera.order.lastIndexOf('d6-block') && await page.evaluate(() => document.querySelector('.d6-tiles').compareDocumentPosition(document.querySelector('.d6-sh')) & Node.DOCUMENT_POSITION_FOLLOWING), camera.order);
    check('camera tiles: toggle, name, resolution and fps', camera.tiles.length === 2 && camera.tiles.every((t) => t.on && /160×120 · [\d.]+ fps/.test(t.meta)) && camera.tiles.map((t) => t.name).join() === 'Color,Depth', camera.tiles);
    check('the USB link reads as a version, the raw speed in the tooltip', camera.badge === 'USB 3.2' && /link USB 3\.2 Gen 1 · 5 Gb\/s/.test(await page.evaluate(() => document.querySelector('.d6-badge-c').title)), camera.badge);
    const hashOf = () => page.evaluate(() => { const img = document.querySelector('.d6-tile img'); const c = document.createElement('canvas'); c.width = 40; c.height = 30; const g = c.getContext('2d'); g.drawImage(img, 0, 0, 40, 30); const d = g.getImageData(0, 0, 40, 30).data; let h = 0; for (let i = 0; i < d.length; i += 7) h = (h * 31 + d[i]) | 0; return h; });
    const hashes = [];
    for (let i = 0; i < 5; i += 1) { hashes.push(await hashOf()); await page.waitForTimeout(450); }
    check('the image tiles update live (the picture changes)', new Set(hashes).size >= 3, hashes);
    const seq1 = Number((await fetch(`${base}/api/v1/frames?path=${encodeURIComponent(CAMERA)}&stream=color`)).headers.get('x-frame-seq'));
    await page.waitForTimeout(1200);
    const seq2 = Number((await fetch(`${base}/api/v1/frames?path=${encodeURIComponent(CAMERA)}&stream=color`)).headers.get('x-frame-seq'));
    check('the host serves newer frames', seq2 > seq1, [seq1, seq2]);
    await page.click('input[data-d6-stream="color"]', { force: true });
    await page.waitForTimeout(400);
    const toggled = await page.evaluate(() => ({ imgs: document.querySelectorAll('.d6-tile img').length, ph: (document.querySelector('.d6-tile.off .d6-ph') || {}).textContent, depth: document.querySelectorAll('.d6-tile:not(.off) img').length }));
    check('a stream toggles off (its tile says off) and the other keeps going', toggled.imgs === 1 && toggled.ph === 'off' && toggled.depth === 1, toggled);
    await page.reload(); await H.ready(page); await page.waitForSelector('.d6-tile');
    check('the toggle is remembered', await page.evaluate(() => document.querySelector('.d6-tile.off') !== null && document.querySelectorAll('.d6-tile img').length <= 1));
    await page.click('input[data-d6-stream="color"]', { force: true });
    await page.waitForSelector('.d6-tile:not(.off) img');
    await page.click('.d6-tile:nth-of-type(2) .d6-pic');
    await page.waitForSelector('.d6-lb img');
    const lightbox = await page.evaluate(() => ({ src: document.querySelector('.d6-lb img').getAttribute('src'), w: document.querySelector('.d6-lb img').getBoundingClientRect().width }));
    check('click enlarges a stream; Esc closes it', /stream=depth/.test(lightbox.src) && lightbox.w >= 400, lightbox);
    await page.keyboard.press('Escape');
    check('Esc closes the enlarged image', await H.waitFor(() => page.evaluate(() => !document.querySelector('.d6-lb')), 3000));
    check('a camera has no Processes or driver tab', (await page.evaluate(() => [...document.querySelectorAll('.d6-tab')].map((t) => t.dataset.d6Tab))).join() === 'diagnostics,state,config,log,utilities');
    await H.goTo(page, ADAPTER);
    const adapterBadge = await page.evaluate(() => (document.querySelector('.d6-badge-c') || {}).textContent);
    check('a full-speed USB 2 device reads as its version and its link', adapterBadge === 'USB 2.0 · on a USB 1.1 link', adapterBadge);

    // ---- Configuration: folders, pins, order -----------------------------------------------------------------------------------
    await H.goTo(page, BOARD);
    await H.goTab(page, 'config');
    const cfg = await page.evaluate(() => ({ folders: [...document.querySelectorAll('.er-fold .er-fn')].map((f) => f.textContent), rows: [...document.querySelectorAll('.er-cfgrow .d6-sn')].map((r) => r.textContent.trim()),
      expected: [...document.querySelectorAll('.er-cfgrow.differs .d6-exp')].map((e) => e.textContent), mark: document.querySelectorAll('.er-cfgrow.differs .d6-diff').length }));
    check('config: dot notation parses into folders (a chain of single folders is one row)', cfg.folders.join() === 'aux1.pins.0,id,servo,servo.motor_position.output,servo.pid_position,servopos'.replace('servo.motor_position.output,servo.pid_position,', 'motor_position.output,pid_position,') || (cfg.folders.includes('aux1.pins.0') && cfg.folders.includes('servo') && cfg.folders.includes('servopos') && cfg.folders.includes('motor_position.output')), cfg.folders);
    check('config: a row shows its last segment; the full name is its tooltip', cfg.rows.includes('max_current_A') && cfg.rows.includes('kp') && !cfg.rows.includes('servo.max_current_A'), cfg.rows);
    check('config: a value that differs is marked with the expected value and its source', cfg.mark === 2 && cfg.expected.some((e) => /expected 25 A · deployment record rack-2026-10-21/.test(e)), cfg.expected);
    await page.fill('[data-d6-search]', 'timeout');
    check('config: search filters the rows', await H.waitFor(() => page.evaluate(() => document.querySelectorAll('.er-cfgrow').length === 1), 3000));
    await page.fill('[data-d6-search]', '');
    await page.click('[data-d6-copy]');
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    check('config: Copy as text copies the settings', /servo\.max_current_A = 20 A/.test(clip) && /expected 25 A \(deployment record rack-2026-10-21\)/.test(clip), clip.split('\n').slice(0, 3));

    // pin a row: it moves to the top, in a Pinned block
    const pinKey = 'servo.default_timeout_s';
    await page.hover(`.er-cfgrow:has([title="${pinKey}"])`);
    await page.click(`.er-cfgrow:has([title="${pinKey}"]) [data-er-pin]`);
    await page.waitForSelector('.er-pinned .er-cfgrow');
    const firstRow = await page.evaluate(() => ({ pinned: document.querySelector('.er-pinned .d6-sn').title, first: document.querySelector('.d6-panel .er-cfgrow .d6-sn').title }));
    check('config: a starred row moves to the top, in a Pinned block', firstRow.pinned === pinKey && firstRow.first === pinKey, firstRow);
    const saved = await H.waitFor(async () => { const a = await H.getJson(base, '/api/v1/arrangements'); return ((a.arrangements.servo || {}).config || {}).pins ? a : false; }, 5000, 200) || await H.getJson(base, '/api/v1/arrangements');
    check('the pin is saved by the host per entity kind', (saved.arrangements.servo || {}).config && saved.arrangements.servo.config.pins.includes(pinKey) && /arrangements\.json$/.test(saved.file || ''), saved);
    // a brand-new browser profile (no localStorage) shows it, and so does another board of the same kind
    const fresh = await (await H.newContext(browser)).newPage();
    await H.open(fresh, base, BOARD2, 'config');
    await fresh.waitForSelector('.er-pinned .er-cfgrow', { timeout: 10000 });
    check('pins are shared by every board of a kind, and by every browser', await fresh.evaluate(() => document.querySelector('.er-pinned .d6-sn').title) === pinKey);
    await fresh.context().close();

    // manual order: drag a row above another inside its folder; it survives a reload
    await page.selectOption('[data-er-sort="config"]', 'manual');
    await page.waitForTimeout(300);
    const rowsServo = await page.evaluate(() => [...document.querySelectorAll('.er-cfgrow[data-er-parent="servopos"]')].map((r) => r.dataset.erId));
    const target = rowsServo[0];
    const mover = rowsServo[rowsServo.length - 1];
    await page.dragAndDrop(`.er-cfgrow[data-er-parent="servopos"][data-er-id="${mover}"] .er-drag`, `.er-cfgrow[data-er-parent="servopos"][data-er-id="${target}"]`, { targetPosition: { x: 120, y: 2 } });
    await page.waitForTimeout(500);
    const afterDrag = await page.evaluate(() => [...document.querySelectorAll('.er-cfgrow[data-er-parent="servopos"]')].map((r) => r.dataset.erId));
    check('config: dragging a row by its handle reorders it inside its folder', afterDrag[0] === mover && afterDrag.length === rowsServo.length, { rowsServo, afterDrag });
    await page.reload(); await H.ready(page); await page.waitForSelector('.er-cfgrow');
    const afterReload = await page.evaluate(() => [...document.querySelectorAll('.er-cfgrow[data-er-parent="servopos"]')].map((r) => r.dataset.erId));
    check('the manual order survives a reload', afterReload.join() === afterDrag.join() && (await page.evaluate(() => document.querySelector('[data-er-sort="config"]').value)) === 'manual', afterReload);
    await page.selectOption('[data-er-sort="config"]', 'name');
    await page.waitForTimeout(300);
    const alpha = await page.evaluate(() => [...document.querySelectorAll('.er-cfgrow[data-er-parent="servopos"]')].map((r) => r.dataset.erId));
    check('alphabetical is the default order', alpha.join() === [...alpha].sort((a, b) => a.localeCompare(b)).join(), alpha);
    // a folder folds, and it is remembered
    await page.click('[data-er-fold="servo"]');
    check('a folder folds', await page.evaluate(() => !document.querySelector('.er-cfgrow[data-er-parent="servo"]')));
    await page.reload(); await H.ready(page); await page.waitForSelector('.er-fold');
    check('the folded folder is remembered', await page.evaluate(() => !document.querySelector('.er-cfgrow[data-er-parent="servo"]') && document.querySelector('[data-er-fold="servo"]').getAttribute('aria-expanded') === 'false'));
    await page.click('[data-er-fold="servo"]');
    await page.hover(`.er-pinned .er-cfgrow`);
    await page.click('.er-pinned [data-er-pin]');

    // ---- Configuration: editing, live and safe ---------------------------------------------------------------------------------
    const nodeOf = () => H.getJson(base, `/api/v1/node?path=${encodeURIComponent(BOARD)}`);
    const rowOf = async (key) => (await nodeOf()).config.find((r) => r.key === key);
    const rowDom = (key) => page.evaluate((k) => { const r = document.querySelector(`.er-cfgrow:has([title="${k}"])`); return r ? r.textContent.replace(/\s+/g, ' ').trim() : null; }, key);
    check('editing is off until asked', (await page.evaluate(() => !document.querySelector('input[data-er-cfg]') && Boolean(document.querySelector('[data-er-start]')))) && !(await nodeOf()).editing.active);
    await page.click('[data-er-start]');
    await page.waitForSelector('[data-er-modal]');
    const confirmText = await page.evaluate(() => document.querySelector('[data-er-modal]').textContent);
    check('Edit… asks one confirm that names the board and says it is live', /CAN 1/.test(confirmText) && /running board/.test(confirmText) && /Save to board/.test(confirmText), confirmText.slice(0, 160));
    await page.click('[data-er-cancel]');
    check('cancelling starts no session', !(await nodeOf()).editing.active);
    await page.click('[data-er-start]');
    await page.click('[data-er-confirm="start"]');
    await page.waitForSelector('input[data-er-cfg]');
    check('confirming opens an edit session on the host', (await nodeOf()).editing.active === true);
    check('only writable settings take an edit; the id is locked', await page.evaluate(() => !document.querySelector('input[data-er-cfg="id.id"]') && Boolean(document.querySelector('.er-cfgrow:has([title="id.id"]) .er-lock'))));
    const timeoutKey = 'servo.default_timeout_s';
    const input = `input[data-er-cfg="${timeoutKey}"]`;
    await page.fill(input, '0.3');
    await page.press(input, 'Enter');
    await page.waitForFunction((k) => { const r = document.querySelector(`.er-cfgrow:has([title="${k}"])`); return r && /accepted/.test(r.textContent); }, timeoutKey, { timeout: 15000 });
    let line = await rowDom(timeoutKey);
    check('an edit applies live and the row says accepted, and not saved', /✓ accepted/.test(line) && /not saved · stored 0\.1 s/.test(line), line);
    check('the host holds it live but not stored', await (async () => { const r = await rowOf(timeoutKey); return r.value === 0.3 && r.stored === 0.1 && r.unsaved; })());
    check('the bar counts the unsaved changes with Save and Revert', await page.evaluate(() => /1 change live, not saved/.test(document.querySelector('.er-bar').textContent) && Boolean(document.querySelector('[data-er-save]') && document.querySelector('[data-er-revert]'))));
    await page.fill(input, '50');
    await page.press(input, 'Enter');
    await page.waitForFunction((k) => /rejected/.test((document.querySelector(`.er-cfgrow:has([title="${k}"])`) || {}).textContent || ''), timeoutKey, { timeout: 15000 });
    line = await rowDom(timeoutKey);
    check('a rejected edit says so and what the board kept', /rejected: the board kept 10\.0 \(asked 50\.0\)/.test(line), line);
    check('the rejected chip is a true diagnostic result', await page.evaluate((k) => Boolean(document.querySelector(`.er-cfgrow:has([title="${k}"]) .er-chip.bad[data-diagnostic]`)), timeoutKey));
    await page.fill(input, '0.3'); await page.press(input, 'Enter');
    await page.waitForFunction((k) => /accepted/.test((document.querySelector(`.er-cfgrow:has([title="${k}"])`) || {}).textContent || ''), timeoutKey, { timeout: 15000 });
    // reset to the deployment record's value, with its source
    await page.click(`.er-cfgrow:has([title="${timeoutKey}"]) [data-er-resetopen]`);
    const menu = await page.evaluate(() => document.querySelector('.er-resetrow').textContent);
    check('reset offers stored, the deployment record (with its source) and the default', /to the stored value: 0\.1 s/.test(menu) && /to the deployment record: 0\.5 s · deployment record rack-2026-10-21/.test(menu), menu);
    await page.click('[data-er-reset="expected"]');
    await page.waitForFunction((k) => /reset to expected/.test((document.querySelector(`.er-cfgrow:has([title="${k}"])`) || {}).textContent || ''), timeoutKey, { timeout: 15000 });
    check('reset puts the record value live', (await rowOf(timeoutKey)).value === 0.5);
    // Save keeps every live change on the board
    await page.click('[data-er-save]');
    await page.waitForFunction(() => !document.querySelector('[data-er-save]'), null, { timeout: 15000 });
    const savedRow = await rowOf(timeoutKey);
    check('Save to board stores the live settings; nothing is unsaved after', savedRow.stored === 0.5 && !savedRow.unsaved && /^Editing /.test(await page.evaluate(() => document.querySelector('.er-bar').textContent)), savedRow);
    // Revert all
    await page.fill(input, '0.7'); await page.press(input, 'Enter');
    await page.waitForFunction(() => /1 change live, not saved/.test((document.querySelector('.er-bar') || {}).textContent || ''), null, { timeout: 15000 });
    await page.click('[data-er-revert]');
    await page.waitForFunction(() => !document.querySelector('[data-er-revert]'), null, { timeout: 15000 });
    check('Revert all puts the stored values back live', (await rowOf(timeoutKey)).value === 0.5);

    // a safety key: a second confirm, and the host refuses a board that is not stopped
    const safeKey = 'servo.max_current_A';
    const safeInput = `input[data-er-cfg="${safeKey}"]`;
    const logBefore = require('fs').readFileSync(main.board.log, 'utf8').split('\n').filter((l) => /"verb": "act"/.test(l) && /set_config/.test(l) && /max_current/.test(l)).length;
    await page.fill(safeInput, '15'); await page.press(safeInput, 'Enter');
    await page.waitForSelector('[data-er-modal][role="alertdialog"]');
    check('a safety key asks a second confirm naming it', /servo\.max_current_A/.test(await page.evaluate(() => document.querySelector('[data-er-modal]').textContent)) && /stopped/.test(await page.evaluate(() => document.querySelector('[data-er-modal]').textContent)));
    await page.click('[data-er-cancel]');
    check('cancelling the second confirm sends nothing', (await rowOf(safeKey)).value === 20 && !(await rowOf(safeKey)).unsaved);
    await page.fill(safeInput, '15'); await page.press(safeInput, 'Enter');
    await page.waitForSelector('[data-er-modal]');
    await page.click('[data-er-confirm="safety"]');
    await page.waitForFunction((k) => /rejected/.test((document.querySelector(`.er-cfgrow:has([title="${k}"])`) || {}).textContent || ''), safeKey, { timeout: 15000 });
    line = await rowDom(safeKey);
    const logAfter = require('fs').readFileSync(main.board.log, 'utf8').split('\n').filter((l) => /"verb": "act"/.test(l) && /set_config/.test(l) && /max_current/.test(l)).length;
    check('a safety key is refused while the board is not stopped, and nothing was sent', /safety setting: the board must be stopped \(mode 0\) and it reports mode 10 \(position\)/.test(line) && logAfter === logBefore && (await rowOf(safeKey)).value === 20, line);
    // stop the board (mode 0): after its next check the same write goes through
    require('fs').writeFileSync(main.board.mode, '0');
    await H.waitFor(async () => (await H.getJson(base, `/api/v1/values?path=${encodeURIComponent(BOARD)}`)).groups.some((g) => g.rows.some((r) => r.key === 'mode' && r.value === 0)), 15000, 400);
    await page.fill(safeInput, '15'); await page.press(safeInput, 'Enter');
    await page.waitForSelector('[data-er-modal]');
    await page.click('[data-er-confirm="safety"]');
    await page.waitForFunction((k) => /accepted/.test((document.querySelector(`.er-cfgrow:has([title="${k}"])`) || {}).textContent || ''), safeKey, { timeout: 20000 });
    check('a stopped board takes the confirmed safety write', (await rowOf(safeKey)).value === 15);
    await page.click('[data-er-revert]');
    await page.waitForSelector('[data-er-modal]');
    await page.click('[data-er-confirm="revert-safety"]');
    await page.waitForFunction(() => !document.querySelector('[data-er-revert]'), null, { timeout: 20000 });
    check('reverting safety settings asks the second confirm too, then reverts', (await rowOf(safeKey)).value === 20);
    require('fs').writeFileSync(main.board.mode, '10');

    // every write is in the Log; the session ends when the tab is left
    await H.goTab(page, 'log');
    check('leaving the tab ends the edit session', await H.waitFor(async () => !(await nodeOf()).editing.active, 5000, 200));
    await page.waitForSelector('.d6-log .al-row', { timeout: 15000 });
    const logText = await page.evaluate(() => document.querySelector('.d6-log').textContent);
    check('Log: the embedded Activity log, scoped to this board with Raw', /Log of\s*CAN 1/.test(logText) && Boolean(await page.$('.d6-log [data-al-raw], .d6-log .al-toggle')), logText.slice(0, 120));
    check('Log: every write is a line (who, key, old → new, accepted or not)', /servo\.default_timeout_s set 0\.1 → 0\.3: accepted by the board/.test(logText) && /the board kept 10\.0/.test(logText) && /servo\.max_current_A → 15\.0 refused before anything was sent|servo\.max_current_A → 15 refused/.test(logText) && /saved to the board/.test(logText), logText.slice(0, 400));
    const events = await H.getJson(base, '/api/v1/activity?limit=500');
    check('the host logs ConfigSet / ConfigRejected / ConfigPersisted as events', ['ConfigSet', 'ConfigRejected', 'ConfigPersisted'].every((code) => events.events.some((e) => e.code === code)), events.events.map((e) => e.code).filter((c) => /Config/.test(c)));

    // the badge of the Log tab: warnings that arrived since the tab was last shown
    await H.goTab(page, 'diagnostics');
    check('the Log tab carries no badge when you have looked', !(await page.$('[data-d6-tab="log"] .d6-bd')));

    // ---- Diagnostics → the log, and back -------------------------------------------------------------------------------------------
    await page.click('.d6-lg[data-d6-jump="log"]');
    await page.waitForFunction(() => document.querySelector('.d6-tab.on').dataset.d6Tab === 'log', null, { timeout: 5000 });
    check('a fault in Diagnostics links to its line in the Log', (await H.tabOf(page)) === 'log');

    // and back: a line that raised a fault links to it in Diagnostics, lighting its row. WHY a drawn line: the fixture board's fault is
    // "found at start", which the Activity log files under the host; the page's own decoration is what is measured here, on a row
    // shaped like the log's (its code, its message, the entity button).
    await H.goTab(page, 'log');
    await page.waitForSelector('.d6-log .al-list', { timeout: 15000 });
    await page.evaluate((board) => {
      const row = document.createElement('div');
      row.className = 'al-row';
      row.innerHTML = `<span class="al-ent"><button data-al-scope="${board}">CAN 1</button></span><span class="al-code"><span class="kind">FaultConfirmed</span></span><span class="al-msg">MoteusFault.33 confirmed on ${board}: the driver is in fault</span>`;
      document.querySelector('.d6-log .al-list').appendChild(row);
    }, BOARD);
    const lineLink = await page.waitForSelector('.d6-log .d6-lg', { timeout: 5000 }).catch(() => null);
    check('Log: a line that raised a fault carries a link to it', Boolean(lineLink));
    if (lineLink) {
      await lineLink.click();
      await page.waitForFunction(() => document.querySelector('.d6-tab.on').dataset.d6Tab === 'diagnostics', null, { timeout: 5000 }).catch(() => {});
      check('the link opens that fault in Diagnostics, its row lit', (await H.tabOf(page)) === 'diagnostics' && await H.waitFor(() => page.evaluate(() => Boolean(document.querySelector('.d6-r.d6-flash'))), 3000), await H.tabOf(page));
    }

    // ---- Utilities -------------------------------------------------------------------------------------------------------------------
    await H.goTab(page, 'utilities');
    const utilities = await page.evaluate(() => ({ rows: [...document.querySelectorAll('.d6-ur')].map((r) => ({ title: r.querySelector('b').textContent, danger: r.querySelector('.d6-ud').textContent, last: r.querySelector('.d6-ul').textContent })), buttons: document.querySelectorAll('.d6-run[data-util]').length }));
    check('Utilities: every action with its danger and last run', utilities.rows.length >= 2 && utilities.rows.some((r) => r.title === 'Run calibration…' && /makes hardware move/.test(r.danger)) && utilities.rows.every((r) => r.last), utilities.rows);
    check('Utilities: the configuration writes are not menu items', !utilities.rows.some((r) => /configuration/i.test(r.title)));
    await page.click('#ent-util-btn');
    check('the Utilities menu stays the quick way in and lists no configuration write either', await page.evaluate(() => Boolean(document.querySelector('.ent-menu [data-util]')) && ![...document.querySelectorAll('.ent-menu [data-util]')].some((b) => /set_config|persist_config|reset_config/.test(b.dataset.util))));
    await page.keyboard.press('Escape');

    // ---- paused: a person's tool holds the port -------------------------------------------------------------------------------------
    const held = await H.fixtureHost('F2');
    servers.push(held);
    const pausedPage = await (await H.newContext(browser)).newPage();
    await H.open(pausedPage, held.base, BOARD, 'state');
    await pausedPage.waitForFunction(() => /paused: /.test((document.querySelector('.d6-note') || {}).textContent || ''), null, { timeout: 20000 });
    const pausedNote = await pausedPage.evaluate(() => document.querySelector('.d6-note').textContent);
    check('State says plainly why it is paused', /paused: tview has the port \(pid 640623\)/.test(pausedNote), pausedNote);
    check('nothing runs while it is paused', !require('fs').existsSync(held.board.log) || !require('fs').readFileSync(held.board.log, 'utf8').split('\n').some((l) => /"verb": "watch"/.test(l)));
    await pausedPage.context().close();

    // ---- cross-origin and script writes are refused ---------------------------------------------------------------------------------
    const bad = [];
    for (const [endpoint, body] of [['config/edit', { path: BOARD, op: 'open', confirm: true }], ['watch', { path: BOARD }], ['arrangements', { kind: 'servo', section: 'config', value: { pins: ['x'] } }]]) {
      for (const headers of [{ 'Content-Type': 'application/json' }, { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui', 'Sec-Fetch-Site': 'cross-site' }]) {
        const response = await fetch(`${base}/api/v1/${endpoint}`, { method: 'POST', headers, body: JSON.stringify(body) });
        if (response.status !== 403) bad.push([endpoint, response.status]);
      }
    }
    check('writes from a script or another site are refused', bad.length === 0 && !(await nodeOf()).editing.active, bad);

    // ---- every boundary drags and is remembered ---------------------------------------------------------------------------------------
    await H.goTo(page, BOARD);
    await H.goTab(page, 'state');
    const grip = await page.$('.d6-sh [data-d6-grip="name"]');
    const box = await grip.boundingBox();
    const w0 = await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.d6-panel')).getPropertyValue('--d6-name-w')));
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 120, box.y + box.height / 2, { steps: 6 });
    await page.mouse.up();
    const w1 = await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.d6-panel')).getPropertyValue('--d6-name-w')));
    await page.reload(); await H.ready(page); await H.goTab(page, 'state').catch(() => {});
    const w2 = await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.d6-panel')).getPropertyValue('--d6-name-w')));
    check('the name column drags and the width is remembered', w1 > w0 + 50 && w2 === w1, [w0, w1, w2]);
    await page.dblclick('.d6-sh [data-d6-grip="name"]');
    check('double-click resets it', await page.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.d6-panel')).getPropertyValue('--d6-name-w'))) === 190);

    // ---- warn colours only on true diagnostics ------------------------------------------------------------------------------------------
    for (const tab of ['diagnostics', 'state', 'config', 'log', 'utilities']) {
      await H.goTab(page, tab);
      await page.waitForTimeout(700);
      const result = await page.evaluate(warnColours, { scope: tab, selectors: ['#detail'] });
      check(`warn colours stay on true diagnostics: ${tab}`, result.offenders.length === 0 && result.elements > 20 && result.watched >= 8, { elements: result.elements, watched: result.watched, offenders: result.offenders.slice(0, 5) });
    }

    check('no page errors', watch.errors.length === 0, watch.errors.slice(0, 4));
    await context.close();

    // ---- the Computer's own tab -----------------------------------------------------------------------------------------------------
    // (a live read-only computer plugin on its own host: psutil, sysfs, nvidia-smi; it opens no device node)
    const computer = await startComputer();
    if (computer) {
      servers.push(computer);
      const cpage = await (await H.newContext(browser)).newPage();
      await cpage.goto(`${computer.base}/?sel=host`);
      await cpage.waitForFunction(() => typeof S !== 'undefined' && S.node && document.querySelector('.d6-tabs'), null, { timeout: 30000 });
      await cpage.waitForSelector('[data-d6-tab="processes"]', { timeout: 20000 });
      const tabs = await cpage.evaluate(() => [...document.querySelectorAll('.d6-tab')].map((t) => t.dataset.d6Tab));
      check('the Computer gets a Processes tab after Utilities', tabs.join() === 'diagnostics,state,config,log,utilities,processes', tabs);
      await cpage.click('[data-d6-tab="processes"]');
      await cpage.waitForSelector('.d6-tbl tbody tr', { timeout: 40000 });
      const table = await cpage.evaluate(() => ({ heads: [...document.querySelectorAll('.d6-tbl')[0].querySelectorAll('th')].map((t) => t.textContent.trim()), rows: document.querySelectorAll('.d6-tbl')[0].querySelectorAll('tbody tr').length }));
      check('Processes: a table of the busiest processes', table.heads.join() === 'pid,name,cpu,rss' && table.rows >= 5, table);
      await cpage.click('.d6-tbl th:nth-child(2) button');
      const names = await cpage.evaluate(() => [...document.querySelectorAll('.d6-tbl')[0].querySelectorAll('tbody tr td:nth-child(2)')].map((t) => t.textContent.toLowerCase()));
      check('Processes: sortable by a column', names.join() === [...names].sort().reverse().join() || names.join() === [...names].sort().join(), names.slice(0, 4));
      await cpage.context().close();
    } else console.log('SKIP  the Computer plugin could not start here');
  } catch (error) {
    check('the run itself', false, String(error && error.stack || error));
  } finally {
    await browser.close();
    for (const server of servers) H.stopServer(server.child);
  }
  process.exitCode = H.summary();
})();

// a live Computer on its own scratch port (only the read-only computer plugin, copied with an absolute command)
async function startComputer() {
  try {
    const root = H.fs.mkdtempSync(H.path.join(H.os.tmpdir(), 'entity-computer-'));
    const dir = H.path.join(root, 'plugins');
    H.fs.mkdirSync(dir);
    H.fs.writeFileSync(H.path.join(dir, 'computer.toml'), H.fs.readFileSync(H.path.join(H.ROOT, 'plugins.d', 'computer.toml'), 'utf8').split('{root}').join(H.ROOT));
    const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: H.path.join(root, 'state'), IO_DIAGNOSTICS_CONFIG_DIR: H.path.join(root, 'config') };
    delete env.VIRTUAL_ENV;
    const child = require('child_process').spawn('/home/bam/.local/bin/uv', ['run', '--project', H.ROOT, 'python', H.path.join(H.ROOT, 'io_diagnostics', 'cli.py'), 'serve', '--plugins', dir, '--port', '0', '--no-udev'],
      { cwd: H.ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    return await new Promise((resolve) => {
      let output = '';
      const timer = setTimeout(() => { H.stopServer(child); resolve(null); }, 60000);
      const onData = (chunk) => { output += chunk; const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//); if (found) { clearTimeout(timer); resolve({ child, base: found[1] }); } };
      child.stdout.on('data', onData); child.stderr.on('data', onData);
      child.on('exit', () => { clearTimeout(timer); resolve(null); });
    });
  } catch (_) { return null; }
}

// the warn-colour gate, as tests/test_tree_variants.js measures it (the same function, run in the page)
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
