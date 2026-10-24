#!/usr/bin/env node
// Acceptance for run view r2 "Under the action" (io_diagnostics/web/runs/r2.js, docs/runs-v1.md section 7), in the REAL
// page on the fixture host, headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_runs_r2.js
//
// Checks:
//   seam      r2 is in the Runs select; its status-bar Runs button is mounted
//   flow      Utilities ▾ -> Run calibration… -> sheet -> confirm: the run line under the header expands in place into the live
//             terminal (a shadow root the shell's morph cannot reach), the answer box and "Stop (motor goes limp)" beside it;
//             the progress is on screen as printed; the prompt waits and says so; the box survives the shell's redraws (focus,
//             typed text, the same terminal node); "y" typed into the box finishes it; the block folds back to the shell's one
//             line with Show output; Show output opens the finished output with its recording; Hide output folds it
//   terminal  typing in the terminal itself answers too (an "n" aborts: failed, exit 1)
//   stop      a hang run (ignores Stop): Stop says "Stopping…", the host escalates, the run ends "stopped"; a plain run says "Stop"
//   two runs  a run on CAN 1 waits at its prompt while another runs on the adapter; the status-bar Runs list holds both and
//             takes the person to the one they pick, its output open under its own action
//   reload    mid-run, a reload finds the run again under its action (output replayed, the box works), and it finishes into
//             the shell's own run line and the Activity log
//   popout    Pop out opens runs/r2-popout.html?run=<id> in its own window: the same output, an answer box, Stop; answering there
//             ends the run in the page too
//   panel     the output's bottom edge drags, is remembered across a reload, resets on double-click, moves by arrow keys
//   dark      the terminal follows the skin
// Screenshots: reports/media/runs/r2-*.png. Exits 1 on any failed check. Nothing opens a CAN adapter.
'use strict';

const H = require('./runs_harness.js');
const { check, SHOTS } = H;

// the xterm of a run block (inside the block's shadow root), as text
const hostText = (page, id) => page.evaluate((runId) => {
  const host = document.querySelector(`[data-r2-host="${runId}"]`);
  const root = host && host.shadowRoot;
  if (!root) return null;
  const rows = [...root.querySelectorAll('.xterm-rows > div')].map((row) => row.textContent);
  return rows.join('\n');
}, id);
const waitText = (page, id, pattern, timeout = 20000) => page.waitForFunction(([runId, source]) => {
  const host = document.querySelector(`[data-r2-host="${runId}"]`);
  const root = host && host.shadowRoot;
  if (!root) return false;
  return new RegExp(source).test([...root.querySelectorAll('.xterm-rows > div')].map((row) => row.textContent).join('\n'));
}, [id, pattern.source], { timeout });
const widget = (id, selector) => `[data-r2-host="${id}"] >> ${selector}`;

async function main() {
  H.fs.mkdirSync(SHOTS, { recursive: true });
  const { chromium } = require(H.PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  const host = await H.fixtureHost();
  const { base, CAN1, adapter } = host;
  try {
    const context = await H.newContext(browser, 'r2');
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    await H.open(page, base, CAN1);

    // ---- seam
    const seam = await page.evaluate(() => ({ ids: [...document.getElementById('run-view').options].map((o) => o.value), chosen: S.runView, value: document.getElementById('run-view').value,
      button: Boolean(document.querySelector('#runs-status .r2-runs')), badgeHidden: document.querySelector('#runs-status .r2-badge').hidden, view: typeof window.IORunViews.r2.runLine }));
    check('seam: r2 is in the Runs select and selected', seam.ids.includes('r2') && seam.chosen === 'r2' && seam.value === 'r2' && seam.view === 'function', seam);
    check('seam: the status-bar Runs button is there, its badge quiet', seam.button && seam.badgeHidden, seam);

    // ---- flow: a moves run
    await H.startCalibration(page);
    await page.waitForSelector('.r2-out', { timeout: 10000 });
    const runId = await H.currentRunId(page);
    const where = await page.evaluate(() => {
      const head = document.querySelector('.d-head');
      const line = document.querySelector('.r2-line');
      const out = document.querySelector('.r2-out');
      return { lineIsNext: head.nextElementSibling === line, outIsNext: line.nextElementSibling === out, gap: Math.round(line.getBoundingClientRect().top - head.getBoundingClientRect().bottom),
        text: line.textContent, inDetail: Boolean(document.getElementById('detail').contains(out)) };
    });
    check('flow: the line under the header expands in place into the output', where.lineIsNext && where.outIsNext && Math.abs(where.gap) <= 1 && where.inDetail && /^Run calibration on CAN 1 · running \d+:\d\d/.test(where.text), where);
    await waitText(page, runId, /Continue\? \[y\/N\]/);
    const inner = await page.evaluate((id) => {
      const root = document.querySelector(`[data-r2-host="${id}"]`).shadowRoot;
      const stop = root.querySelector('.stop');
      const form = root.querySelector('.in');
      return { xterm: Boolean(root.querySelector('.xterm')), form: !form.hidden, stop: stop.textContent, moves: stop.classList.contains('moves'), waiting: form.classList.contains('waiting'),
        ask: root.querySelector('.ask').textContent, quick: !root.querySelector('.quick').hidden, foot: root.querySelector('.foot').textContent,
        badge: document.querySelector('.r2-badge').textContent, stopBesideInput: stop.parentElement === form, lineStop: Boolean(document.querySelector('.r2-line .r2-stop')) };
    }, runId);
    check('flow: an answer box with Stop (motor goes limp) beside it', inner.xterm && inner.form && inner.stop === 'Stop (motor goes limp)' && inner.moves && inner.stopBesideInput && !inner.lineStop, inner);
    check('flow: the prompt says it waits, Yes / No are offered', inner.waiting && inner.ask === 'Waiting for your answer' && inner.quick && /waiting for an answer/.test(inner.foot) && inner.badge === '1', inner);
    await page.screenshot({ path: `${SHOTS}/r2-running-prompt.png` });

    // the shell redraws the detail every second: the terminal node, the typed text and the focus survive it
    await page.evaluate((id) => {
      const root = document.querySelector(`[data-r2-host="${id}"]`).shadowRoot;
      root.querySelector('.xterm').__tag = 'same-node';
    }, runId);
    await page.locator(widget(runId, 'input')).click();
    await page.keyboard.type('ye');
    await page.waitForTimeout(2600);
    const kept = await page.evaluate((id) => {
      const hostEl = document.querySelector(`[data-r2-host="${id}"]`);
      const root = hostEl.shadowRoot;
      const input = root.querySelector('input');
      return { tag: root.querySelector('.xterm').__tag, value: input.value, focus: root.activeElement === input, hostFocus: document.activeElement === hostEl };
    }, runId);
    check('flow: the terminal, the typed text and the focus survive the shell\'s redraws', kept.tag === 'same-node' && kept.value === 'ye' && kept.focus && kept.hostFocus, kept);
    await page.keyboard.press('Backspace');
    await page.keyboard.press('Backspace');
    await page.keyboard.type('y');
    await page.keyboard.press('Enter');
    await H.waitEnded(page, runId, 20000);
    await page.waitForSelector('.ent-run.ok:not(.r2-line)', { timeout: 15000 });
    const folded = await page.evaluate((id) => ({ blocks: document.querySelectorAll('.r2-out').length, line: document.querySelector('.ent-run').textContent, show: Boolean(document.querySelector(`[data-run-open="${id}"]`)),
      state: window.IORuns.get(id).state, exit: window.IORuns.get(id).exit_code, badge: document.querySelector('.r2-badge').hidden }), runId);
    check('flow: "y" in the box finishes it; the block folds to the one-line result', folded.state === 'done' && folded.exit === 0 && folded.blocks === 0 && folded.show && /ran in/.test(folded.line) && folded.badge, folded);
    const body = await page.evaluate(() => ({ dBody: Boolean(document.querySelector('#detail .d-body')), text: (document.querySelector('#detail .d-body') || {}).innerText || '', hostLeft: document.querySelectorAll('[data-r2-host]').length }));
    check('flow: folding leaves the entity page whole (its body is still drawn, no stale host)', body.dBody && /Status/i.test(body.text) && body.hostLeft === 0, { dBody: body.dBody, hostLeft: body.hostLeft, text: body.text.slice(0, 40) });
    await page.screenshot({ path: `${SHOTS}/r2-folded.png` });

    await page.click(`[data-run-open="${runId}"]`);
    await page.waitForSelector(`[data-r2-host="${runId}"]`);
    await waitText(page, runId, /saved the calibration to CAN 1/);
    const reopened = await page.evaluate((id) => {
      const root = document.querySelector(`[data-r2-host="${id}"]`).shadowRoot;
      return { form: root.querySelector('.in').hidden, foot: root.querySelector('.foot').textContent, cast: (root.querySelector('.foot a') || {}).getAttribute('href'), line: document.querySelector('.r2-line').textContent,
        hide: Boolean(document.querySelector('[data-r2-hide]')), pop: Boolean(document.querySelector('[data-r2-popout]')) };
    }, runId);
    check('flow: Show output opens the finished output, read-only, with its recording', reopened.form && /^done · exit 0/.test(reopened.foot) && reopened.cast === `/api/v1/runs/${runId}/cast` && /done · exit 0/.test(reopened.line) && reopened.hide && reopened.pop, reopened);
    await page.screenshot({ path: `${SHOTS}/r2-finished-open.png` });
    await page.click('[data-r2-hide]');
    await page.waitForSelector('.ent-run:not(.r2-line)');
    check('flow: Hide output folds it again, the page body intact', (await page.$$('.r2-out')).length === 0 && /Status/i.test(await page.evaluate(() => (document.querySelector('#detail .d-body') || {}).innerText || '')), '');

    // ---- the latency: a second calibration, each progress frame's t= against the page clock; answered by typing in the TERMINAL ("n" aborts)
    await H.startCalibration(page);
    await page.waitForSelector('.r2-out');
    const second = await H.currentRunId(page);
    const seen = new Map();
    const latencies = [];
    for (let i = 0; i < 500; i += 1) {
      const sample = await page.evaluate((id) => {
        const host = document.querySelector(`[data-r2-host="${id}"]`);
        const root = host && host.shadowRoot;
        const text = root ? [...root.querySelectorAll('.xterm-rows > div')].map((row) => row.textContent).join('\n') : '';
        return { now: Date.now() / 1000, stamps: [...text.matchAll(/t=(\d+\.\d+)/g)].map((m) => m[1]), prompt: /Continue\? \[y\/N\]/.test(text) };
      }, second);
      for (const stamp of sample.stamps) if (!seen.has(stamp)) { seen.set(stamp, true); latencies.push((sample.now - Number(stamp)) * 1000); }
      if (sample.prompt) break;
      await page.waitForTimeout(15);
    }
    latencies.sort((a, b) => a - b);
    const median = latencies[Math.floor(latencies.length / 2)];
    check('flow: progress is on screen as it is printed', latencies.length >= 5 && median < 300 && latencies[latencies.length - 1] < 700,
      { frames: latencies.length, median_ms: Math.round(median), max_ms: Math.round(latencies[latencies.length - 1]) });
    await waitText(page, second, /Continue\? \[y\/N\]/);
    await page.locator(`[data-r2-host="${second}"] >> .xterm-helper-textarea`).focus();
    await page.keyboard.type('n');
    await page.keyboard.press('Enter');
    await H.waitEnded(page, second);
    const aborted = await H.runState(page, second);
    check('terminal: typing "n" in the output answers it (aborted, exit 1)', aborted.state === 'failed' && aborted.exit === 1, aborted);
    await page.waitForSelector('.ent-run.fail:not(.r2-line)', { timeout: 15000 });
    const failLine = await page.evaluate(() => ({ line: document.querySelector('.ent-run').textContent, diagnostic: document.querySelector('.ent-run').hasAttribute('data-diagnostic') }));
    check('terminal: a failure folds to the shell\'s failure line (the one coloured line)', failLine.diagnostic && /ActionRefused|failed|you did not continue/.test(failLine.line), failLine);

    // ---- stop: a hang run on the adapter ignores SIGINT and SIGTERM
    await H.goTo(page, adapter);
    await H.runAction(page, 'hang', 'changes');
    await page.waitForSelector('.r2-out');
    const hang = await H.currentRunId(page);
    await waitText(page, hang, /hanging: SIGINT and SIGTERM are ignored/);
    const plainStop = await page.locator(widget(hang, '.stop')).innerText();
    check('stop: a run that does not move says Stop, not "motor goes limp"', plainStop === 'Stop', plainStop);
    await page.locator(widget(hang, '.stop')).click();
    const stopping = await page.locator(widget(hang, '.stop')).innerText();
    await waitText(page, hang, /ignoring SIGINT/);
    await page.screenshot({ path: `${SHOTS}/r2-stopping.png` });
    await H.waitEnded(page, hang, 30000);
    const hung = await H.runState(page, hang);
    check('stop: Stopping…, the host escalates, the run ends stopped', stopping === 'Stopping…' && hung.state === 'stopped', { stopping, hung });
    await page.waitForSelector('.ent-run:not(.r2-line):not(.running)', { timeout: 15000 });
    const stoppedLine = await page.evaluate(() => ({ line: document.querySelector('.ent-run').textContent, quiet: !document.querySelector('.ent-run').hasAttribute('data-diagnostic') }));
    check('stop: a Stop is one quiet line, not a failure', stoppedLine.quiet && /stopped after/.test(stoppedLine.line), stoppedLine);

    // ---- two runs at once: calibration on CAN 1 waits at its prompt, a hang runs on the adapter
    await H.goTo(page, CAN1);
    await H.startCalibration(page);
    await page.waitForSelector('.r2-out');
    const first = await H.currentRunId(page);
    await waitText(page, first, /Continue\? \[y\/N\]/);
    await H.goTo(page, adapter);
    await H.runAction(page, 'hang', 'changes');
    await page.waitForSelector('.r2-out');
    const other = await H.currentRunId(page);
    await waitText(page, other, /hanging/);
    const two = await page.evaluate(() => ({ badge: document.querySelector('.r2-badge').textContent, active: window.IORuns.list().filter((run) => run.active).length }));
    check('two runs: both are going, the Runs badge says 2', two.active === 2 && two.badge === '2', two);
    await page.click('.r2-runs');
    const listed = await page.evaluate(() => [...document.querySelectorAll('.r2-pop a')].map((a) => a.textContent));
    check('two runs: the Runs list holds both', listed.length >= 2 && listed.some((text) => /Run calibration/.test(text)) && listed.some((text) => /Hang/.test(text)), listed);
    await page.screenshot({ path: `${SHOTS}/r2-two-runs.png` });
    await page.click(`.r2-pop a[data-r2-show="${first}"]`);
    await page.waitForFunction((p) => S.node && S.node.node.path === p, CAN1, { timeout: 10000 });
    await page.waitForSelector(`[data-r2-host="${first}"]`);
    await waitText(page, first, /Continue\? \[y\/N\]/);
    const there = await page.evaluate((id) => ({ sel: S.sel, line: document.querySelector('.r2-line').textContent, pop: document.querySelector('.r2-pop').hidden, waiting: document.querySelector(`[data-r2-host="${id}"]`).shadowRoot.querySelector('.in').classList.contains('waiting') }), first);
    check('two runs: picking one goes to its entity with its output open', there.sel === CAN1 && /Run calibration on CAN 1/.test(there.line) && there.pop && there.waiting, there);
    await page.locator(widget(first, 'input')).fill('y');
    await page.keyboard.press('Enter');
    await H.waitEnded(page, first);
    check('two runs: the first finished while the other still runs', (await H.runState(page, first)).state === 'done' && (await H.runState(page, other)).active === true, '');
    await H.goTo(page, adapter);
    await page.waitForSelector(`[data-r2-host="${other}"]`);
    await page.locator(widget(other, '.stop')).click();
    await H.waitEnded(page, other, 30000);

    // ---- reload mid-run
    await H.goTo(page, CAN1);
    await H.startCalibration(page);
    await page.waitForSelector('.r2-out');
    const third = await H.currentRunId(page);
    await waitText(page, third, /Continue\? \[y\/N\]/);
    await page.reload();
    await H.ready(page);
    await page.waitForSelector(`[data-r2-host="${third}"]`, { timeout: 15000 });
    await waitText(page, third, /Continue\? \[y\/N\]/);
    const back = await page.evaluate((id) => {
      const root = document.querySelector(`[data-r2-host="${id}"]`).shadowRoot;
      return { line: document.querySelector('.r2-line').textContent, stop: root.querySelector('.stop').textContent, waiting: root.querySelector('.in').classList.contains('waiting'), form: !root.querySelector('.in').hidden,
        next: document.querySelector('.d-head').nextElementSibling === document.querySelector('.r2-line') };
    }, third);
    check('reload: the run is found again under its action, output replayed, still waiting', /^Run calibration on CAN 1 · running/.test(back.line) && back.stop === 'Stop (motor goes limp)' && back.waiting && back.form && back.next, back);
    await page.screenshot({ path: `${SHOTS}/r2-reloaded.png` });

    // ---- pop out
    const popupPromise = context.waitForEvent('page');
    await page.click(`[data-r2-popout="${third}"]`);
    const popup = await popupPromise;
    await popup.waitForSelector('.xterm', { timeout: 15000 });
    const popErrors = [];
    popup.on('pageerror', (error) => popErrors.push(String(error)));
    await popup.waitForFunction(() => /Continue\? \[y\/N\]/.test([...document.querySelectorAll('.xterm-rows > div')].map((row) => row.textContent).join('\n')), null, { timeout: 15000 });
    const popped = await popup.evaluate(() => ({ url: location.pathname + location.search, title: document.title, head: document.getElementById('title').textContent, state: document.getElementById('state').textContent,
      stop: document.getElementById('stop').textContent, answer: !document.getElementById('answer').hidden, quick: !document.getElementById('quick').hidden }));
    check('popout: its own window at a URL the page serves, the same output, a box, Stop', popped.url === `/runs/r2-popout.html?run=${third}` && /CAN 1 · Run calibration/.test(popped.head) && /^running/.test(popped.state)
      && popped.stop === 'Stop (motor goes limp)' && popped.answer && popped.quick, popped);
    await popup.setViewportSize({ width: 920, height: 600 });
    await popup.screenshot({ path: `${SHOTS}/r2-popout.png` });
    await popup.click('#quick [data-key="y"]');
    await H.waitEnded(page, third);
    await popup.waitForFunction(() => /done/.test(document.getElementById('state').textContent), null, { timeout: 10000 });
    const poppedEnd = await popup.evaluate(() => ({ state: document.getElementById('state').textContent, form: document.getElementById('answer').hidden, cast: (document.querySelector('#foot a') || {}).getAttribute('href') }));
    check('popout: answering there ends the run here too; the popout shows done and the recording', (await H.runState(page, third)).state === 'done' && /^done · exit 0/.test(poppedEnd.state) && poppedEnd.form && /\/cast$/.test(poppedEnd.cast), poppedEnd);
    check('popout: no page errors in the window', popErrors.length === 0, popErrors);
    await popup.close();
    // the reloaded page's run reached the shell's own flow: the run line and the Activity log
    await page.waitForSelector('.ent-run.ok:not(.r2-line)', { timeout: 15000 });
    // (the host logs RunStarted / RunEnded for every run, docs/runs-v1.md section 5: the Activity log has it however the page learned of it)
    const events = (await (await fetch(`${base}/api/v1/activity?types=run`)).json()).events || [];
    const activity = { ended: events.some((event) => event.code === 'RunEnded' && (event.args || {}).run_id === third), line: await page.evaluate(() => document.querySelector('.ent-run').textContent) };
    check('reload: the adopted run finishes into the shell\'s run line (and the host logged its end)', activity.ended && /ran in/.test(activity.line), activity);

    // ---- the output's bottom edge
    await H.startCalibration(page);
    await page.waitForSelector('.r2-out');
    const fourth = await H.currentRunId(page);
    await waitText(page, fourth, /Continue\? \[y\/N\]/);
    const gripBox = await page.locator(widget(fourth, '.grip')).boundingBox();
    const before = await page.evaluate((id) => document.querySelector(`[data-r2-host="${id}"]`).shadowRoot.querySelector('.tw').getBoundingClientRect().height, fourth);
    await page.mouse.move(gripBox.x + gripBox.width / 2, gripBox.y + 4);
    await page.mouse.down();
    await page.mouse.move(gripBox.x + gripBox.width / 2, gripBox.y + 84, { steps: 6 });
    await page.mouse.up();
    const after = await page.evaluate((id) => document.querySelector(`[data-r2-host="${id}"]`).shadowRoot.querySelector('.tw').getBoundingClientRect().height, fourth);
    check('panel: the output\'s bottom edge drags', Math.abs(after - (before + 80)) <= 6 && after > before, { before, after });
    await page.reload();
    await H.ready(page);
    await page.waitForSelector(`[data-r2-host="${fourth}"]`, { timeout: 15000 });
    await waitText(page, fourth, /Continue\? \[y\/N\]/);
    const remembered = await page.evaluate((id) => document.querySelector(`[data-r2-host="${id}"]`).shadowRoot.querySelector('.tw').getBoundingClientRect().height, fourth);
    check('panel: the height is remembered across a reload', Math.abs(remembered - after) <= 2, { remembered, after });
    await page.locator(widget(fourth, '.grip')).dblclick();
    const reset = await page.evaluate((id) => document.querySelector(`[data-r2-host="${id}"]`).shadowRoot.querySelector('.tw').getBoundingClientRect().height, fourth);
    check('panel: double-click restores the default height', Math.abs(reset - 220) <= 2, reset);
    await page.locator(widget(fourth, '.grip')).focus();
    await page.keyboard.press('ArrowDown');
    const keyed = await page.evaluate((id) => document.querySelector(`[data-r2-host="${id}"]`).shadowRoot.querySelector('.tw').getBoundingClientRect().height, fourth);
    check('panel: the arrow keys move a focused edge', Math.abs(keyed - 236) <= 2, keyed);
    await page.locator(widget(fourth, '.grip')).dblclick();

    // ---- dark
    const dark = await H.newContext(browser, 'r2', 'dark');
    const darkPage = await dark.newPage();
    await H.open(darkPage, base, CAN1);
    await darkPage.waitForSelector(`[data-r2-host="${fourth}"]`, { timeout: 15000 });
    await darkPage.waitForFunction((id) => /Continue\? \[y\/N\]/.test([...document.querySelector(`[data-r2-host="${id}"]`).shadowRoot.querySelectorAll('.xterm-rows > div')].map((row) => row.textContent).join('\n')), fourth, { timeout: 15000 });
    await darkPage.screenshot({ path: `${SHOTS}/r2-dark.png` });
    const themed = await darkPage.evaluate((id) => getComputedStyle(document.querySelector(`[data-r2-host="${id}"]`).shadowRoot.querySelector('.tw')).backgroundColor, fourth);
    check('dark: the output follows the skin\'s colours', themed !== 'rgba(0, 0, 0, 0)' && themed !== 'rgb(255, 255, 255)', themed);
    await dark.close();
    await page.locator(widget(fourth, 'input')).fill('y');
    await page.keyboard.press('Enter');
    await H.waitEnded(page, fourth);

    // ---- a quiet query stays folded
    await H.goTo(page, adapter);
    await H.runAction(page, 'check_comms', 'query');
    await page.waitForSelector('.ent-run [data-run-open]', { timeout: 10000 });
    const quietRunning = await page.evaluate(() => ({ blocks: document.querySelectorAll('.r2-out').length, line: document.querySelector('.ent-run').textContent }));
    check('quiet: a query run stays folded; its line offers Show output', quietRunning.blocks === 0 && /Show output/.test(quietRunning.line), quietRunning);

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
