#!/usr/bin/env node
// Acceptance for run view r3 "Runs as jobs" (io_diagnostics/web/runs/r3.js, docs/runs-v1.md section 7), in the REAL page on
// the fixture host, headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_runs_r3.js
//
// Checks:
//   seam      r3 is in the Runs select; the status-bar Runs toggle is mounted, the panel hidden
//   flow      Utilities ▾ -> Run calibration… -> confirm: the panel opens on the run's job: the list row, the header (action,
//             entity, state, Stop (motor goes limp), Run again, the recording), the facts, the steps (Preconditions from the
//             dry run you read, Run with the command and its live terminal open, Exit pending); typing y in the log finishes
//             it: the pill says done, Exit opens with the code; each step's log folds; the recording is an asciinema file
//   again     Run again leaves the panel, takes the page to the entity and shows the dry-run sheet: nothing has run until the
//             confirm; confirming starts a new run and the panel opens on it
//   stop      a hang run: Stopping…, then stopped (the Exit step says -9); a crash run: failed with exit 3 (the error shape)
//   two runs  both going: two rows, badge 2; picking a row shows that run's own terminal; answering the first leaves the other going
//   reload    mid-run a reload finds the panel open on the run, its log replayed, Stop there, the kept plan still in Preconditions
//   filter    the device filter narrows the list (a second device is a synthetic run in the page's own store)
//   panel     the three boundaries (panel top edge, list edge, log edge) drag, are remembered across a reload, reset on double-click
//             and move by arrow keys
//   dark      the log follows the skin
// Screenshots: reports/media/runs/r3-*.png. Exits 1 on any failed check. Nothing opens a CAN adapter.
'use strict';

const H = require('./runs_harness.js');
const { check, SHOTS } = H;

const logText = (page) => page.evaluate(() => [...document.querySelectorAll('.r3-log .xterm-rows > div')].map((row) => row.textContent).join('\n'));
const waitLog = (page, pattern, timeout = 20000) => page.waitForFunction((source) => new RegExp(source).test([...document.querySelectorAll('.r3-log .xterm-rows > div')].map((row) => row.textContent).join('\n')),
  pattern.source, { timeout });
const rowTitles = (page) => page.evaluate(() => [...document.querySelectorAll('.r3-row .r3-t')].map((el) => el.textContent));
const selectedRun = (page) => page.evaluate(() => { const row = document.querySelector('.r3-row[aria-selected="true"]'); return row ? row.dataset.r3Run : null; });
const typeIntoLog = async (page, text) => {
  await page.locator('.r3-log .xterm-helper-textarea').focus();
  await page.keyboard.type(text);
  await page.keyboard.press('Enter');
};

async function main() {
  H.fs.mkdirSync(SHOTS, { recursive: true });
  const { chromium } = require(H.PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  const host = await H.fixtureHost();
  const { base, CAN1, adapter } = host;
  try {
    const context = await H.newContext(browser, 'r3');
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    await H.open(page, base, CAN1);

    // ---- seam
    const seam = await page.evaluate(() => ({ ids: [...document.getElementById('run-view').options].map((o) => o.value), chosen: S.runView, toggle: Boolean(document.querySelector('#runs-status .r3-toggle')),
      hidden: document.getElementById('runs-root').hidden, badgeHidden: document.querySelector('.r3-badge').hidden }));
    check('seam: r3 is in the Runs select and selected', seam.ids.includes('r3') && seam.chosen === 'r3', seam);
    check('seam: the Runs toggle is in the status bar, the panel hidden', seam.toggle && seam.hidden && seam.badgeHidden, seam);

    // ---- flow
    await H.startCalibration(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r3-job h3', { timeout: 10000 });
    const runId = await H.currentRunId(page);
    await waitLog(page, /Continue\? \[y\/N\]/);
    const job = await page.evaluate(() => {
      const q = (selector) => document.querySelector(selector);
      return { title: q('.r3-job h3').textContent, pill: q('.r3-pill').textContent, selected: q('.r3-row[aria-selected="true"] .r3-t').textContent,
        stop: q('[data-r3-stop]').textContent, stopHidden: q('[data-r3-stop]').hidden, moves: q('[data-r3-stop]').classList.contains('moves'), again: q('[data-r3-again]').textContent,
        cast: q('[data-r3-cast]').getAttribute('href'), facts: Object.fromEntries([...document.querySelectorAll('.r3-fact')].map((f) => [f.querySelector('dt').textContent, f.querySelector('dd').textContent])),
        steps: [...document.querySelectorAll('.r3-step')].map((step) => ({ name: step.querySelector('.r3-step-n').textContent, open: step.querySelector('.r3-step-h').getAttribute('aria-expanded'), sub: step.querySelector('.r3-step-s').textContent })),
        badge: q('.r3-badge').textContent };
    });
    check('flow: the panel opens on the run\'s job: header, state, Stop, Run again, recording', job.title === 'Run calibration on CAN 1' && /^running/.test(job.pill) && job.selected === 'CAN 1 · Run calibration'
      && job.stop === 'Stop (motor goes limp)' && !job.stopHidden && job.moves && job.again === 'Run again' && job.cast === `/api/v1/runs/${runId}/cast` && job.badge === '1', job);
    check('flow: the facts say what, where, with what, when', job.facts.Device === 'usb:1-3' && job.facts.Plugin === 'terminal_fixture' && job.facts.Danger === 'moves' && /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(job.facts.Started) && job.facts['Exit code'] === '—' && job.facts.Run === runId, job.facts);
    check('flow: three steps: Preconditions folded, Run open with the command, Exit pending', job.steps.map((s) => s.name).join() === 'Preconditions,Run,Exit' && job.steps[0].open === 'false' && job.steps[1].open === 'true' && job.steps[2].open === 'false'
      && /act --device usb:1-3 --action calibrate/.test(job.steps[1].sub) && /waiting for the process to end/.test(job.steps[2].sub), job.steps);
    await page.screenshot({ path: `${SHOTS}/r3-running-prompt.png` });
    // Preconditions: the dry run's plan the person read
    await page.click(`[data-r3-step="pre:${runId}"]`);
    const pre = await page.evaluate(() => ({ open: document.querySelector('[data-step="pre"] .r3-step-h').getAttribute('aria-expanded'), text: document.querySelector('[data-step="pre"] .r3-step-b').textContent,
      sub: document.querySelector('[data-step="pre"] .r3-step-s').textContent }));
    check('flow: Preconditions holds the dry run\'s plan (the joint, the command it would run)', pre.open === 'true' && /the joint is free to spin/.test(pre.text) && /fixture-tool --calibrate/.test(pre.text) && /1 precondition/.test(pre.sub), pre);
    await page.screenshot({ path: `${SHOTS}/r3-preconditions.png` });
    await page.click(`[data-r3-step="pre:${runId}"]`);

    // the log folds and unfolds without losing the terminal
    await page.evaluate(() => { document.querySelector('.r3-log .xterm').__tag = 'same'; });
    await page.click(`[data-r3-step="run:${runId}"]`);
    const folded = await page.evaluate(() => ({ open: document.querySelector('[data-step="run"] .r3-step-h').getAttribute('aria-expanded'), hidden: document.querySelector('[data-step="run"] .r3-step-b').hidden }));
    await page.click(`[data-r3-step="run:${runId}"]`);
    await page.waitForTimeout(1500);
    const unfolded = await page.evaluate(() => ({ hidden: document.querySelector('[data-step="run"] .r3-step-b').hidden, tag: document.querySelector('.r3-log .xterm').__tag }));
    check('flow: a step\'s log folds, and comes back as the same terminal', folded.open === 'false' && folded.hidden && !unfolded.hidden && unfolded.tag === 'same', { folded, unfolded });

    // the log is a live terminal: y finishes it
    await typeIntoLog(page, 'y');
    await H.waitEnded(page, runId);
    await page.waitForFunction(() => /^done/.test(document.querySelector('.r3-pill').textContent), null, { timeout: 10000 });
    await waitLog(page, /saved the calibration to CAN 1/);
    await page.waitForTimeout(500);
    const done = await page.evaluate(() => ({ pill: document.querySelector('.r3-pill').textContent, stopHidden: document.querySelector('[data-r3-stop]').hidden, badgeHidden: document.querySelector('.r3-badge').hidden,
      exitOpen: document.querySelector('[data-step="exit"] .r3-step-h').getAttribute('aria-expanded'), exit: document.querySelector('[data-step="exit"] .r3-step-b').textContent, exitSub: document.querySelector('[data-step="exit"] .r3-step-s').textContent,
      facts: Object.fromEntries([...document.querySelectorAll('.r3-fact')].map((f) => [f.querySelector('dt').textContent, f.querySelector('dd').textContent])), row: document.querySelector('.r3-row[aria-selected="true"] .r3-m').textContent }));
    check('flow: typing y in the log finishes it: done, Stop gone, Exit opens with the code', /^done · exit 0/.test(done.pill) && done.stopHidden && done.badgeHidden && done.exitOpen === 'true' && /exit code 0/.test(done.exit) && done.exitSub === 'done · exit 0', done);
    check('flow: the facts and the list row follow the end', done.facts['Exit code'] === '0' && done.facts.Ended !== '—' && /^done · /.test(done.row), done);
    await page.screenshot({ path: `${SHOTS}/r3-done.png` });
    const cast = await (await fetch(`${base}/api/v1/runs/${runId}/cast`)).text();
    check('flow: the recording is an asciinema v2 file', JSON.parse(cast.split('\n')[0]).version === 2 && cast.includes('saved the calibration'), cast.slice(0, 80));

    // ---- run again: the normal dry run -> confirm
    const before = await page.evaluate(() => window.IORuns.list().length);
    await page.click('[data-r3-again]');
    await page.waitForSelector('.ent-sheet [data-util-run]', { timeout: 15000 });
    const sheet = await page.evaluate(() => ({ title: document.querySelector('.ent-sheet-h b').textContent, panelHidden: document.getElementById('runs-root').hidden, runs: window.IORuns.list().length,
      util: S.util && S.util.name, phase: S.util && S.util.phase, sel: S.sel }));
    check('again: the dry-run sheet is shown, nothing has run, the panel got out of the way', sheet.title === 'Run calibration' && sheet.panelHidden && sheet.runs === before && sheet.phase === 'shown' && sheet.sel === CAN1, sheet);
    await page.screenshot({ path: `${SHOTS}/r3-run-again-sheet.png` });
    await page.click('.ent-sheet [data-util-run]');
    await page.waitForSelector('#modal [data-util-tick]');
    await page.check('#modal [data-util-tick]');
    await page.click('#modal [data-util-confirm]');
    await page.waitForSelector('#runs-root:not([hidden]) .r3-job h3', { timeout: 10000 });
    const second = await H.currentRunId(page);
    check('again: confirming starts a new run and the panel opens on it', second && second !== runId && (await selectedRun(page)) === second, { second, runId });
    await waitLog(page, /Continue\? \[y\/N\]/);
    await typeIntoLog(page, 'n');
    await H.waitEnded(page, second);
    const aborted = await page.evaluate(() => ({ pill: document.querySelector('.r3-pill').textContent, err: document.querySelector('.r3-pill').classList.contains('err') }));
    check('again: answering n in the log ends it failed (exit 1), the pill is an error', /^failed · exit 1/.test(aborted.pill) && aborted.err, aborted);

    // ---- stop a hang run; a crash run
    await H.goTo(page, adapter);
    await H.runAction(page, 'hang', 'changes');
    await page.waitForSelector('#runs-root:not([hidden]) .r3-job h3');
    const hang = await H.currentRunId(page);
    await waitLog(page, /hanging: SIGINT and SIGTERM are ignored/);
    const plainStop = await page.locator('[data-r3-stop]').innerText();
    await page.click('[data-r3-stop]');
    const stopping = await page.locator('[data-r3-stop]').innerText();
    await H.waitEnded(page, hang, 30000);
    await page.waitForFunction(() => /^stopped/.test(document.querySelector('.r3-pill').textContent), null, { timeout: 10000 });
    await page.waitForTimeout(400);
    const hung = await page.evaluate(() => ({ pill: document.querySelector('.r3-pill').textContent, warn: document.querySelector('.r3-pill').classList.contains('warn'), exit: document.querySelector('[data-step="exit"] .r3-step-b').textContent,
      mark: Boolean(document.querySelector('[data-step="run"] .r3-st .shape.lv-warn')) }));
    check('stop: Stop, Stopping…, then stopped with the host\'s -9 in Exit', plainStop === 'Stop' && stopping === 'Stopping…' && /^stopped · exit -9/.test(hung.pill) && hung.warn && /exit code -9/.test(hung.exit) && hung.mark, { plainStop, stopping, hung });
    await page.screenshot({ path: `${SHOTS}/r3-stopped.png` });
    await H.runAction(page, 'crash', 'changes');
    await page.waitForFunction((previous) => { const row = document.querySelector('.r3-row[aria-selected="true"]'); return row && row.dataset.r3Run !== previous; }, hang, { timeout: 15000 });
    const crash = await H.currentRunId(page);
    await H.waitEnded(page, crash);
    await page.waitForFunction(() => /^failed/.test(document.querySelector('.r3-pill').textContent), null, { timeout: 10000 });
    await page.waitForTimeout(400);
    const crashed = await page.evaluate(() => ({ pill: document.querySelector('.r3-pill').textContent, err: document.querySelector('.r3-pill').classList.contains('err'), exit: document.querySelector('[data-step="exit"] .r3-step-b').textContent,
      mark: Boolean(document.querySelector('[data-step="run"] .r3-st .shape.lv-error')) }));
    check('stop: a crash run is failed with exit 3, drawn as the error shape', /^failed · exit 3/.test(crashed.pill) && crashed.err && /exit code 3/.test(crashed.exit) && crashed.mark, crashed);
    await page.screenshot({ path: `${SHOTS}/r3-failed.png` });

    // ---- two runs at once
    await H.goTo(page, CAN1);
    await H.startCalibration(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r3-job h3');
    await page.waitForFunction(() => document.querySelector('.r3-pill').textContent.startsWith('running'));
    const first = await H.currentRunId(page);
    await waitLog(page, /Continue\? \[y\/N\]/);
    await H.goTo(page, adapter);
    await H.runAction(page, 'hang', 'changes');
    await page.waitForFunction((id) => { const row = document.querySelector('.r3-row[aria-selected="true"]'); return row && row.dataset.r3Run !== id && window.IORuns.list().filter((run) => run.active).length === 2; }, first, { timeout: 15000 });
    const other = await selectedRun(page);
    await waitLog(page, /hanging/);
    const two = await page.evaluate(() => ({ badge: document.querySelector('.r3-badge').textContent, going: [...document.querySelectorAll('.r3-row')].filter((row) => /^running/.test(row.querySelector('.r3-m').textContent)).length,
      count: document.querySelector('.r3-count').textContent }));
    check('two runs: two rows are going, the badge says 2', two.going === 2 && two.badge === '2' && /2 going/.test(two.count), two);
    await page.screenshot({ path: `${SHOTS}/r3-two-runs.png` });
    await page.click(`.r3-row[data-r3-run="${first}"]`);
    await waitLog(page, /Continue\? \[y\/N\]/);
    const picked = await page.evaluate(() => ({ title: document.querySelector('.r3-job h3').textContent, log: [...document.querySelectorAll('.r3-log .xterm-rows > div')].map((r) => r.textContent).join('\n') }));
    check('two runs: picking a row shows that run\'s own terminal', picked.title === 'Run calibration on CAN 1' && /calibrating/.test(picked.log) && !/hanging/.test(picked.log), picked);
    await typeIntoLog(page, 'y');
    await H.waitEnded(page, first);
    check('two runs: answering the first leaves the other going', (await H.runState(page, first)).state === 'done' && (await H.runState(page, other)).active === true, '');
    await page.click(`.r3-row[data-r3-run="${other}"]`);
    await page.waitForSelector('[data-r3-stop]:not([hidden])');
    await page.click('[data-r3-stop]');
    await H.waitEnded(page, other, 30000);

    // ---- reload mid-run
    await H.goTo(page, CAN1);
    await H.startCalibration(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r3-job h3');
    await page.waitForFunction(() => document.querySelector('.r3-pill').textContent.startsWith('running'));
    const third = await H.currentRunId(page);
    await waitLog(page, /Continue\? \[y\/N\]/);
    await page.reload();
    await H.ready(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r3-job h3', { timeout: 15000 });
    await waitLog(page, /Continue\? \[y\/N\]/);
    const back = await page.evaluate(() => ({ selected: (document.querySelector('.r3-row[aria-selected="true"]') || {}).dataset.r3Run, pill: document.querySelector('.r3-pill').textContent, stop: document.querySelector('[data-r3-stop]').textContent,
      pre: document.querySelector('[data-step="pre"] .r3-step-s').textContent, hidden: document.getElementById('runs-root').hidden }));
    check('reload: the panel is open on the run again, its log replayed, Stop there', back.selected === third && /^running/.test(back.pill) && back.stop === 'Stop (motor goes limp)' && !back.hidden, back);
    check('reload: the dry run\'s plan is still kept for the run', /1 precondition/.test(back.pre), back.pre);
    await page.screenshot({ path: `${SHOTS}/r3-reloaded.png` });
    await typeIntoLog(page, 'y');
    await H.waitEnded(page, third);

    // ---- the device filter (a second device is a synthetic run in this page's own store; the host has one device)
    await page.evaluate(() => window.IORuns.ingest([{ id: '20261024T000000-fake', path: 'host/usb9/dev', device_id: 'usb:9-9', device_path: 'host/usb9/dev', plugin: 'other', action: 'check_comms', title: 'Check communication',
      label: 'Other device · Check communication', danger: 'query', state: 'done', active: false, exit_code: 0, elapsed_s: 1.2, started_iso: '2026-10-21T18:20:00-07:00', ended_iso: '2026-10-21T18:20:01-07:00', interactive: false, argv: ['x'] }]));
    await page.waitForFunction(() => document.querySelectorAll('.r3-filter option').length === 3);
    const options = await page.evaluate(() => [...document.querySelectorAll('.r3-filter option')].map((o) => ({ value: o.value, text: o.textContent })));
    const allRows = (await rowTitles(page)).length;
    await page.selectOption('.r3-filter select', 'usb:9-9');
    const filtered = await rowTitles(page);
    await page.selectOption('.r3-filter select', 'usb:1-3');
    const mine = await rowTitles(page);
    await page.selectOption('.r3-filter select', '');
    check('filter: the list narrows by device (all, this device, the other one)', options[0].text.startsWith('All devices') && filtered.length === 1 && /Other device/.test(filtered[0]) && mine.length === allRows - 1 && (await rowTitles(page)).length === allRows,
      { options, allRows, filtered, mine: mine.length });
    await page.screenshot({ path: `${SHOTS}/r3-filter.png` });

    // ---- the three boundaries
    await page.dblclick('.r3-sash');
    const panelH = () => page.evaluate(() => document.getElementById('runs-root').getBoundingClientRect().height);
    const defaultH = await panelH();
    const sashBox = await (await page.$('.r3-sash')).boundingBox();
    await page.mouse.move(sashBox.x + sashBox.width / 2, sashBox.y + 4);
    await page.mouse.down();
    await page.mouse.move(sashBox.x + sashBox.width / 2, sashBox.y - 96, { steps: 6 });
    await page.mouse.up();
    const draggedH = await panelH();
    check('panel: the top edge drags', Math.abs(draggedH - (defaultH + 100)) <= 6 && Math.abs(defaultH - 380) <= 2, { defaultH, draggedH });
    const vsash = await (await page.$('.r3-vsash')).boundingBox();
    const listW = () => page.evaluate(() => document.querySelector('.r3-list').getBoundingClientRect().width);
    const listBefore = await listW();
    await page.mouse.move(vsash.x + 0.5, vsash.y + 40);
    await page.mouse.down();
    await page.mouse.move(vsash.x + 80, vsash.y + 40, { steps: 6 });
    await page.mouse.up();
    const listAfter = await listW();
    check('panel: the list\'s edge drags', Math.abs(listAfter - (listBefore + 80)) <= 6 && Math.abs(listBefore - 300) <= 2, { listBefore, listAfter });
    const logH = () => page.evaluate(() => document.querySelector('.r3-log').getBoundingClientRect().height);
    const gripBox = await (await page.$('.r3-grip')).boundingBox();
    const logBefore = await logH();
    await page.mouse.move(gripBox.x + gripBox.width / 2, gripBox.y + 4);
    await page.mouse.down();
    await page.mouse.move(gripBox.x + gripBox.width / 2, gripBox.y - 56, { steps: 6 });
    await page.mouse.up();
    const logAfter = await logH();
    check('panel: the log\'s edge drags', Math.abs(logAfter - (logBefore - 60)) <= 6 && Math.abs(logBefore - 260) <= 2, { logBefore, logAfter });
    await page.reload();
    await H.ready(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r3-job h3', { timeout: 15000 });
    const remembered = await page.evaluate(() => ({ h: document.getElementById('runs-root').getBoundingClientRect().height, list: document.querySelector('.r3-list').getBoundingClientRect().width,
      log: document.querySelector('.r3-log').getBoundingClientRect().height, device: document.querySelector('.r3-filter select').value }));
    check('panel: all three sizes are remembered across a reload', Math.abs(remembered.h - draggedH) <= 2 && Math.abs(remembered.list - listAfter) <= 2 && Math.abs(remembered.log - logAfter) <= 2, { remembered, draggedH, listAfter, logAfter });
    await page.dblclick('.r3-sash');
    await page.dblclick('.r3-vsash');
    await page.dblclick('.r3-grip');
    const reset = await page.evaluate(() => ({ h: document.getElementById('runs-root').getBoundingClientRect().height, list: document.querySelector('.r3-list').getBoundingClientRect().width, log: document.querySelector('.r3-log').getBoundingClientRect().height }));
    check('panel: double-click restores each default', Math.abs(reset.h - 380) <= 2 && Math.abs(reset.list - 300) <= 2 && Math.abs(reset.log - 260) <= 2, reset);
    await page.focus('.r3-sash');
    await page.keyboard.press('ArrowUp');
    await page.focus('.r3-vsash');
    await page.keyboard.press('ArrowRight');
    await page.focus('.r3-grip');
    await page.keyboard.press('ArrowDown');
    const keyed = await page.evaluate(() => ({ h: document.getElementById('runs-root').getBoundingClientRect().height, list: document.querySelector('.r3-list').getBoundingClientRect().width, log: document.querySelector('.r3-log').getBoundingClientRect().height }));
    check('panel: the arrow keys move a focused edge', Math.abs(keyed.h - 396) <= 2 && Math.abs(keyed.list - 316) <= 2 && Math.abs(keyed.log - 276) <= 2, keyed);
    await page.dblclick('.r3-sash');
    await page.dblclick('.r3-vsash');
    await page.dblclick('.r3-grip');
    const arrows = await page.evaluate(() => { document.querySelector('.r3-rows').focus(); return document.querySelector('.r3-row[aria-selected="true"]').dataset.r3Run; });
    await page.keyboard.press('ArrowDown');
    check('panel: the list moves its selection with the arrow keys', (await selectedRun(page)) !== arrows, arrows);

    // ---- dark
    const dark = await H.newContext(browser, 'r3', 'dark');
    const darkPage = await dark.newPage();
    await H.open(darkPage, base, CAN1);
    await darkPage.evaluate(() => runsCtx.setPref('r3.open', '1'));
    await darkPage.reload();
    await H.ready(darkPage);
    await darkPage.waitForSelector('#runs-root:not([hidden]) .r3-job h3', { timeout: 15000 });
    await darkPage.waitForTimeout(800);
    await darkPage.screenshot({ path: `${SHOTS}/r3-dark.png` });
    const themed = await darkPage.evaluate(() => getComputedStyle(document.querySelector('.r3-log')).backgroundColor);
    check('dark: the log follows the skin\'s colours', themed !== 'rgba(0, 0, 0, 0)' && themed !== 'rgb(255, 255, 255)', themed);
    await dark.close();

    check('no page errors', watch.real().length === 0, { errors: watch.real().slice(0, 3), benign_409_resize: watch.benign.length });
    await context.close();
  } finally {
    H.stopServer(host.server.child);
    await browser.close();
    H.fs.rmSync(host.stateDir, { recursive: true, force: true });
  }
  process.exit(H.summary(` · screenshots in ${SHOTS}`));
}

main().catch((error) => { console.error(error); process.exit(1); });
