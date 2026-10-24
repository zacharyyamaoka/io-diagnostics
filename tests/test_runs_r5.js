#!/usr/bin/env node
// Acceptance for run view r5 "A real terminal (tmux)" (io_diagnostics/web/runs/r5.js, docs/runs-v1.md section 7), in the REAL
// page on the fixture host, headless Chrome:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_runs_r5.js
//
// r5 is an in-page imitation of tmux over the runs stream: no tmux server is started and the host runs acts as before. Checks:
//   seam      r5 is in the Runs select; the status-bar tmux toggle is mounted, the session detached (nothing to show yet)
//   flow      Utilities ▾ -> Run calibration… -> confirm: the session attaches on its own: the status line "[robotdx] 0:calibrate@1*",
//             the window's terminal full size with the progress as printed and the prompt waiting, Stop (motor goes limp); typing
//             y in the window finishes it (the window stays, dead, as with remain-on-exit)
//   keys      with a second window: Ctrl-b n / p / l / 0 / w (a list: arrows, Enter) / ? (the keys) / & (closes a finished window, refuses a
//             running one), the ^B indicator while the prefix is pressed, Ctrl-b Ctrl-b sends a literal Ctrl-b to the process (the
//             tty echoes ^B), Ctrl-b d detaches (the runs go on, the toggle says detached) and the toggle attaches again; `#` marks
//             output in a window you are not looking at and `!` one that ended there; both clear when you select it
//   stop      a hang run: Stop, Stopping…, then stopped (the window's warn shape)
//   attach    "Attach from a terminal" puts `tmux attach -t robotdx` on the clipboard and says it only answers once the host runs
//             acts in tmux; it starts nothing
//   two runs  two windows going at once, each keeps its own terminal
//   reload    mid-run a reload finds the session attached, the windows back, the log replayed, Stop there
//   panel     the top edge drags, is remembered across a reload, resets on double-click, moves by arrow keys
//   dark      the terminal follows the skin
// Screenshots: reports/media/runs/r5-*.png. Exits 1 on any failed check. Nothing opens a CAN adapter.
'use strict';

const H = require('./runs_harness.js');
const { check, SHOTS } = H;

const paneText = (page, id) => page.evaluate((runId) => {
  const pane = document.querySelector(`[data-r5-pane="${runId}"]`);
  return pane ? [...pane.querySelectorAll('.xterm-rows > div')].map((row) => row.textContent).join('\n') : null;
}, id);
const waitPane = (page, id, pattern, timeout = 20000) => page.waitForFunction(([runId, source]) => {
  const pane = document.querySelector(`[data-r5-pane="${runId}"]`);
  return pane && new RegExp(source).test([...pane.querySelectorAll('.xterm-rows > div')].map((row) => row.textContent).join('\n'));
}, [id, pattern.source], { timeout });
const windows = (page) => page.evaluate(() => [...document.querySelectorAll('.r5-win')].map((win) => ({ text: win.querySelector('.r5-name').textContent + win.querySelector('.r5-flag').textContent, selected: win.getAttribute('aria-selected') === 'true',
  id: win.dataset.r5Win, dead: win.classList.contains('dead') })));
const selectedWindow = async (page) => (await windows(page)).find((win) => win.selected);
const focusPane = (page, id) => page.locator(`[data-r5-pane="${id}"] .xterm-helper-textarea`).focus();
const typeInto = async (page, id, text) => { await focusPane(page, id); await page.keyboard.type(text); await page.keyboard.press('Enter'); };

async function main() {
  H.fs.mkdirSync(SHOTS, { recursive: true });
  const { chromium } = require(H.PLAYWRIGHT);
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  const host = await H.fixtureHost();
  const { base, CAN1, adapter } = host;
  try {
    const context = await H.newContext(browser, 'r5');
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    await H.open(page, base, CAN1);

    // ---- seam
    const seam = await page.evaluate(() => ({ ids: [...document.getElementById('run-view').options].map((o) => o.value), chosen: S.runView, toggle: Boolean(document.querySelector('#runs-status .r5-toggle')),
      hidden: document.getElementById('runs-root').hidden, badgeHidden: document.querySelector('.r5-badge').hidden, detached: document.querySelector('.r5-detached').hidden }));
    check('seam: r5 is in the Runs select and selected', seam.ids.includes('r5') && seam.chosen === 'r5', seam);
    check('seam: the tmux toggle is in the status bar; nothing is attached yet', seam.toggle && seam.hidden && seam.badgeHidden && seam.detached, seam);

    // ---- flow: a moves run attaches the session
    await H.startCalibration(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r5-win', { timeout: 10000 });
    const first = await H.currentRunId(page);
    await waitPane(page, first, /Continue\? \[y\/N\]/);
    const status = await page.evaluate(() => ({ left: document.querySelector('.r5-left').textContent, wins: [...document.querySelectorAll('.r5-win')].map((w) => w.textContent), title: document.querySelector('.r5-title').textContent,
      stop: document.querySelector('.r5-stop').textContent, stopMoves: document.querySelector('.r5-stop').classList.contains('moves'), attach: document.querySelector('.r5-attach').textContent,
      badge: document.querySelector('.r5-badge').textContent, pressed: document.querySelector('.r5-toggle').getAttribute('aria-pressed'), clock: /^\d\d:\d\d$/.test(document.querySelector('.r5-clock').textContent),
      focus: Boolean(document.activeElement && document.activeElement.closest('.r5-pane')) }));
    check('flow: the session attaches with the run as window 0', status.left === '[robotdx]' && status.wins.join() === '0:calibrate@1*' && status.title === '"CAN 1 · Run calibration"' && status.pressed === 'true' && status.badge === '1' && status.clock, status);
    check('flow: Stop (motor goes limp) and Attach from a terminal are in the status line', status.stop === 'Stop (motor goes limp)' && status.stopMoves && status.attach === 'Attach from a terminal', status);
    const fullSize = await page.evaluate((id) => { const pane = document.querySelector(`[data-r5-pane="${id}"]`).getBoundingClientRect(); const body = document.querySelector('.r5-body').getBoundingClientRect();
      const screen = document.querySelector(`[data-r5-pane="${id}"] .xterm-screen`).getBoundingClientRect(); return { pane: [Math.round(pane.width), Math.round(pane.height)], body: [Math.round(body.width), Math.round(body.height)], screen: Math.round(screen.width) }; }, first);
    check('flow: the window fills the panel', fullSize.pane[0] === fullSize.body[0] && fullSize.pane[1] === fullSize.body[1] && fullSize.screen > fullSize.body[0] * 0.8, fullSize);
    await page.screenshot({ path: `${SHOTS}/r5-running-prompt.png` });

    // live latency: a second calibration's progress against the page clock is measured in r1/r2; here the stream is read through the window
    // a second window: a hang run on the adapter (window 1)
    await H.goTo(page, adapter);
    await H.runAction(page, 'hang', 'changes');
    await page.waitForFunction(() => document.querySelectorAll('.r5-win').length === 2, null, { timeout: 15000 });
    const hang = await H.currentRunId(page);
    await waitPane(page, hang, /hanging: SIGINT and SIGTERM are ignored/);
    let list = await windows(page);
    check('keys: a second run is window 1 and the current one; the first shows `-` (last)', list.map((w) => w.text).join() === '0:calibrate@1-,1:hang*' && list[1].selected, list.map((w) => w.text));

    // the first window is in the background: answer it from outside the view; its output is activity, its end rings
    await page.evaluate((id) => runsCtx.sendInput(id, 'y\n'), first);
    await H.waitEnded(page, first);
    await page.waitForFunction(() => /0:calibrate@1.*#.*!|0:calibrate@1.*!.*#/.test(document.querySelector('.r5-win .r5-name').textContent + document.querySelector('.r5-win .r5-flag').textContent), null, { timeout: 10000 });
    list = await windows(page);
    check('keys: `#` marks output in a window you are not looking at, `!` one that ended there', /#/.test(list[0].text) && /!/.test(list[0].text) && list[0].dead, list.map((w) => w.text));
    await page.screenshot({ path: `${SHOTS}/r5-flags.png` });

    // Ctrl-b n / p: the prefix is shown, the key is the session's, not the process's
    await focusPane(page, hang);
    await page.keyboard.press('Control+b');
    const prefixShown = await page.evaluate(() => !document.querySelector('.r5-prefix').hidden);
    await page.keyboard.press('p');
    const afterP = await selectedWindow(page);
    const prefixGone = await page.evaluate(() => document.querySelector('.r5-prefix').hidden);
    check('keys: Ctrl-b shows ^B; Ctrl-b p goes to the previous window and clears its flags', prefixShown && prefixGone && afterP.id === first && afterP.text === '0:calibrate@1*', { prefixShown, prefixGone, afterP });
    await waitPane(page, first, /saved the calibration to CAN 1/);
    await page.keyboard.press('Control+b');
    await page.keyboard.press('n');
    check('keys: Ctrl-b n goes to the next window (the hang run)', (await selectedWindow(page)).id === hang, '');
    await page.keyboard.press('Control+b');
    await page.keyboard.press('l');
    check('keys: Ctrl-b l goes to the last window', (await selectedWindow(page)).id === first, '');
    await page.keyboard.press('Control+b');
    await page.keyboard.press('1');
    check('keys: Ctrl-b 1 goes to window 1', (await selectedWindow(page)).id === hang, '');
    await page.keyboard.press('Control+b');
    await page.keyboard.press('0');
    check('keys: Ctrl-b 0 goes to window 0', (await selectedWindow(page)).id === first, '');
    // Ctrl-b w: a list
    await page.keyboard.press('Control+b');
    await page.keyboard.press('w');
    const choose = await page.evaluate(() => ({ shown: !document.querySelector('.r5-pop').hidden, rows: [...document.querySelectorAll('.r5-pop-row')].map((row) => row.textContent), at: document.querySelector('.r5-pop-row[aria-selected="true"]').dataset.r5Choose }));
    await page.screenshot({ path: `${SHOTS}/r5-choose.png` });
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    const chosen = await selectedWindow(page);
    check('keys: Ctrl-b w lists the windows; arrows and Enter choose one', choose.shown && choose.rows.length === 2 && /0: calibrate@1/.test(choose.rows[0]) && chosen.id === hang && (await page.evaluate(() => document.querySelector('.r5-pop').hidden)), { choose, chosen });
    // Ctrl-b ? : the keys
    await page.keyboard.press('Control+b');
    await page.keyboard.press('?');
    const keys = await page.evaluate(() => ({ shown: !document.querySelector('.r5-pop').hidden, text: document.querySelector('.r5-pop').textContent }));
    await page.screenshot({ path: `${SHOTS}/r5-keys.png` });
    await page.keyboard.press('Escape');
    check('keys: Ctrl-b ? lists the keys; any key closes the list', keys.shown && /Ctrl-b d/.test(keys.text) && /detach/.test(keys.text) && (await page.evaluate(() => document.querySelector('.r5-pop').hidden)), keys.text.slice(0, 80));
    // Ctrl-b &: refuses a running window, closes a finished one
    await page.keyboard.press('Control+b');
    await page.keyboard.press('&');
    const refused = await page.evaluate(() => ({ msg: document.querySelector('.r5-msg').textContent, wins: document.querySelectorAll('.r5-win').length }));
    check('keys: Ctrl-b & refuses a window that is still running', /still running/.test(refused.msg) && refused.wins === 2, refused);
    // Stop the hang run from the status line
    const plainStop = await page.locator('.r5-stop').innerText();
    await page.click('.r5-stop');
    const stopping = await page.locator('.r5-stop').innerText();
    await H.waitEnded(page, hang, 30000);
    await page.waitForTimeout(500);
    const stopped = await page.evaluate(() => ({ warn: Boolean(document.querySelector('.r5-win[aria-selected="true"] .shape.lv-warn')), stopHidden: document.querySelector('.r5-stop').hidden }));
    check('stop: Stop, Stopping…, then stopped (the window keeps a warn shape, Stop goes)', plainStop === 'Stop' && stopping === 'Stopping…' && (await H.runState(page, hang)).state === 'stopped' && stopped.warn && stopped.stopHidden, { plainStop, stopping, stopped });
    await page.screenshot({ path: `${SHOTS}/r5-stopped.png` });

    // Ctrl-b Ctrl-b sends a literal Ctrl-b to the process: a calibration waiting at its prompt, window 2
    await H.goTo(page, CAN1);
    await H.startCalibration(page);
    await page.waitForFunction(() => document.querySelectorAll('.r5-win').length === 3, null, { timeout: 15000 });
    const third = await H.currentRunId(page);
    await waitPane(page, third, /Continue\? \[y\/N\]/);
    await focusPane(page, third);
    await page.keyboard.press('Control+b');
    await page.keyboard.press('Control+b');
    await waitPane(page, third, /\^B/);
    check('keys: Ctrl-b Ctrl-b sends a literal Ctrl-b to the process (the tty echoes ^B)', /\^B/.test(await paneText(page, third)), '');
    await page.keyboard.press('Backspace'); // the ^B is in the process's input line: take it back out before the answer later
    // Ctrl-b & on a finished window closes it
    await page.keyboard.press('Control+b');
    await page.keyboard.press('1');
    check('keys: Ctrl-b 1 selects window 1 (finished hang)', (await selectedWindow(page)).id === hang, '');
    await page.keyboard.press('Control+b');
    await page.keyboard.press('&');
    list = await windows(page);
    check('keys: Ctrl-b & closes a finished window; the others keep their numbers', list.map((w) => w.text.replace(/[*\-#!]/g, '')).join() === '0:calibrate@1,2:calibrate@1' && !list.some((w) => w.id === hang), list.map((w) => w.text));
    // a new window takes the lowest free number
    const selectedNow = await selectedWindow(page);
    check('keys: after closing, the last window is current', selectedNow && selectedNow.id !== hang, selectedNow);

    // two runs at once: the window with the prompt keeps its own terminal; the first window's terminal is its own
    await page.keyboard.press('Control+b');
    await page.keyboard.press('2');
    const twoText = await paneText(page, third);
    check('two runs: each window keeps its own terminal', /Continue\? \[y\/N\]/.test(twoText) && /saved the calibration/.test(await paneText(page, first)) && !/saved the calibration/.test(twoText), '');
    await page.screenshot({ path: `${SHOTS}/r5-two-windows.png` });

    // ---- attach from a terminal
    const runsBefore = await page.evaluate(() => window.IORuns.list().length);
    await page.click('.r5-attach');
    const copied = await page.evaluate(async () => ({ clip: await navigator.clipboard.readText(), msg: document.querySelector('.r5-msg').textContent }));
    check('attach: the button copies `tmux attach -t robotdx` and says it answers once the host runs acts in tmux', copied.clip === 'tmux attach -t robotdx' && /copied: tmux attach -t robotdx/.test(copied.msg) && /once the host runs acts in tmux/.test(copied.msg), copied);
    check('attach: it starts nothing', (await page.evaluate(() => window.IORuns.list().length)) === runsBefore, '');
    await page.screenshot({ path: `${SHOTS}/r5-attach.png` });

    // ---- detach / attach
    await focusPane(page, third);
    await page.keyboard.press('Control+b');
    await page.keyboard.press('d');
    const detached = await page.evaluate((id) => ({ hidden: document.getElementById('runs-root').hidden, label: document.querySelector('.r5-detached').hidden ? '' : document.querySelector('.r5-detached').textContent,
      pressed: document.querySelector('.r5-toggle').getAttribute('aria-pressed'), going: window.IORuns.get(id).active, badge: document.querySelector('.r5-badge').textContent }), third);
    check('keys: Ctrl-b d detaches: the panel goes, the run goes on, the toggle says detached', detached.hidden && detached.label === 'detached' && detached.pressed === 'false' && detached.going && detached.badge === '1', detached);
    await page.screenshot({ path: `${SHOTS}/r5-detached.png` });
    await page.click('.r5-toggle');
    await page.waitForSelector('#runs-root:not([hidden]) .r5-win');
    const attached = await page.evaluate(() => ({ hidden: document.getElementById('runs-root').hidden, wins: document.querySelectorAll('.r5-win').length, sel: document.querySelector('.r5-win[aria-selected="true"]').dataset.r5Win }));
    await waitPane(page, third, /Continue\? \[y\/N\]/);
    check('keys: the toggle attaches again: the same windows, the same current one, the output still there', !attached.hidden && attached.wins === 2 && attached.sel === third, attached);

    // ---- reload mid-run
    await page.reload();
    await H.ready(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r5-win', { timeout: 15000 });
    await waitPane(page, third, /Continue\? \[y\/N\]/);
    const back = await page.evaluate(() => ({ wins: [...document.querySelectorAll('.r5-win')].map((w) => w.querySelector('.r5-name').textContent), sel: document.querySelector('.r5-win[aria-selected="true"]').dataset.r5Win,
      stop: document.querySelector('.r5-stop').textContent, hidden: document.getElementById('runs-root').hidden }));
    check('reload: the session is attached again with the windows and the live run current, Stop there', !back.hidden && back.sel === third && back.stop === 'Stop (motor goes limp)' && back.wins.length === 2, back);
    await page.screenshot({ path: `${SHOTS}/r5-reloaded.png` });
    await typeInto(page, third, 'y');
    await H.waitEnded(page, third);
    check('reload: the answer typed after the reload finished the run', (await H.runState(page, third)).state === 'done', '');

    // ---- the top edge
    await page.dblclick('.r5-sash');
    const panelH = () => page.evaluate(() => document.getElementById('runs-root').getBoundingClientRect().height);
    const defaultH = await panelH();
    const sashBox = await (await page.$('.r5-sash')).boundingBox();
    await page.mouse.move(sashBox.x + sashBox.width / 2, sashBox.y + 4);
    await page.mouse.down();
    await page.mouse.move(sashBox.x + sashBox.width / 2, sashBox.y - 96, { steps: 6 });
    await page.mouse.up();
    const draggedH = await panelH();
    check('panel: the top edge drags', Math.abs(draggedH - (defaultH + 100)) <= 6 && Math.abs(defaultH - 320) <= 2, { defaultH, draggedH });
    await page.reload();
    await H.ready(page);
    await page.waitForSelector('#runs-root:not([hidden]) .r5-win', { timeout: 15000 });
    check('panel: the height is remembered across a reload', Math.abs((await panelH()) - draggedH) <= 2, await panelH());
    await page.dblclick('.r5-sash');
    check('panel: double-click restores the default height', Math.abs((await panelH()) - 320) <= 2, await panelH());
    await page.focus('.r5-sash');
    await page.keyboard.press('ArrowUp');
    check('panel: the arrow keys move a focused edge', Math.abs((await panelH()) - 336) <= 2, await panelH());
    await page.dblclick('.r5-sash');

    // ---- dark
    const dark = await H.newContext(browser, 'r5', 'dark');
    const darkPage = await dark.newPage();
    await H.open(darkPage, base, CAN1);
    await darkPage.evaluate((id) => runsCtx.open(id), first);
    await darkPage.waitForSelector('#runs-root:not([hidden]) .r5-win', { timeout: 15000 });
    await waitPane(darkPage, first, /saved the calibration/);
    await darkPage.waitForTimeout(800);
    await darkPage.screenshot({ path: `${SHOTS}/r5-dark.png` });
    const themed = await darkPage.evaluate(() => getComputedStyle(document.querySelector('.r5-body')).backgroundColor);
    check('dark: the window follows the skin\'s colours', themed !== 'rgba(0, 0, 0, 0)' && themed !== 'rgb(255, 255, 255)', themed);
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
