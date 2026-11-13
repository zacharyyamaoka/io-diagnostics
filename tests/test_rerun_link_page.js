// "Open in Rerun" on the real entity page, over a fixture host and a fixture discovery doc (what the recorder would write). Zach, 2026-11-13,
// via the sprint dispatcher: each pluggable device (moteus board, D435, OBSBOT) opens a live Rerun view, plus a robot/cell view; RobotDx only reads.
//
//   node tests/test_rerun_link_page.js        (a fixture host on a scratch port, RERUN_DISCOVERY in its env; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, fs, os, path } = H;

const CAMERA_KEY = 'usb:8086:0b5b:219323070276';   // the fixture RealSense: its identity serial
const CAMERA_URL = 'http://127.0.0.1:9876/?entity=d405';
const CELL_URL = 'http://127.0.0.1:9876/?entity=cell';
const START_CMD = 'data-recorder serve --cell rack';
const SHOT = '/home/bam/io-diagnostics/reports/media/rerun-link-2026-11-13.png';

// the recorder rewrites its doc every second; a test that wrote it once would watch it go stale after 10 s
function recorder(file) {
  const write = () => fs.writeFileSync(file, JSON.stringify({
    schema: 'rerun-discovery/1', updated: Date.now() / 1000, viewer_url: 'http://127.0.0.1:9876', cell_url: CELL_URL, start_cmd: START_CMD,
    devices: [{ key: CAMERA_KEY, label: 'RealSense D405', entity_root: '/cell/d405', url: CAMERA_URL, live: true, last_t: Date.now() / 1000 }] }));
  write();
  return setInterval(write, 1000);
}

const rerunOf = (page) => page.evaluate(() => {
  const el = document.querySelector('.d6-line [data-d6-rerun]');
  if (!el) return null;
  const a = el.querySelector('a');
  const dot = el.querySelector('.d6-rr-dot');
  return { state: el.dataset.d6Rerun, text: el.textContent.trim(), href: a && a.getAttribute('href'), target: a && a.target, rel: a && a.rel,
    dotOn: dot ? dot.classList.contains('on') : null, dotColor: dot ? getComputedStyle(dot).backgroundColor : null,
    code: [...el.querySelectorAll('code')].map((c) => c.textContent), box: el.getBoundingClientRect().toJSON() };
});

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rerun-link-'));
  const file = path.join(root, 'rerun.json');
  let timer = recorder(file);
  const server = await H.fixtureHost('F1', { RERUN_DISCOVERY: file });
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const { base } = server;
    const context = await H.newContext(browser, 'light');
    await context.route('http://127.0.0.1:9876/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Rerun</title>' }));
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);

    // ---- the API: what the page reads -----------------------------------------------------------------------------------------
    const node = (await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(H.CAMERA)}`)).node;
    check('the camera node carries rerun {url, label, live}', node.rerun && node.rerun.url === CAMERA_URL && node.rerun.live === true, node.rerun);
    const board = (await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(H.BOARD)}`)).node;
    check('the moteus board is eligible and not listed', board.kind === 'servo' && board.rerun && board.rerun.absent === 'Not in the recorder\'s device list', [board.kind, board.rerun]);
    const state = await H.getJson(base, '/api/v1/state');
    check('the state tree carries it too (host links to the cell)', state.tree.rerun && state.tree.rerun.url === CELL_URL, state.tree.rerun);

    // ---- a live device: the link, new tab, the lit dot -----------------------------------------------------------------------
    await H.open(page, base, H.CAMERA, 'diagnostics');
    await page.waitForSelector('.d6-line [data-d6-rerun]', { timeout: 8000 });
    const live = await rerunOf(page);
    check('the badge line has an "Open in Rerun" link to the device', live.href === CAMERA_URL && /Open in Rerun/.test(live.text), live);
    check('it opens a new tab without handing over the opener', live.target === '_blank' && /\bnoopener\b/.test(live.rel), live);
    check('the dot is lit while the recorder says live (an existing token, --io-ok)', live.state === 'live' && live.dotOn === true && live.dotColor !== 'rgba(0, 0, 0, 0)', live);
    const shot = await page.locator('.d6-top').first().boundingBox().catch(() => null);
    fs.mkdirSync(path.dirname(SHOT), { recursive: true });
    await page.screenshot({ path: SHOT, clip: { x: 0, y: 0, width: 1400, height: Math.min(420, shot ? shot.y + shot.height + 60 : 420) } });
    console.log(`screenshot: ${SHOT}`);

    // ---- the host row links to the cell ---------------------------------------------------------------------------------------
    await H.goTo(page, 'host');
    await page.waitForSelector('.d6-line [data-d6-rerun]', { timeout: 8000 });
    const cell = await rerunOf(page);
    check('the host page links to the cell view', cell.href === CELL_URL, cell);

    // ---- the palette: Ctrl+K, Ctrl+P and Ctrl+O all open it; "rerun" and "open" bring up the Rerun commands ------------------------
    // Zach, 2026-11-13: "bind ctrl P ctrl K and ctrl O to open the search/command ... if I search for rerun or open etc than I want
    // to see thoose commands come up"
    const palOpen = () => page.evaluate(() => { const b = document.querySelector('.lx-pal-back'); return Boolean(b && !b.hidden); });
    const palRows = () => page.evaluate(() => [...document.querySelectorAll('.lx-pal-i')].map((el) => el.querySelector('.lab').textContent));
    const palSubs = () => page.evaluate(() => [...document.querySelectorAll('.lx-pal-i')].map((el) => `${el.querySelector('.lab').textContent} | ${el.querySelector('.sub').textContent}`));
    for (const key of ['k', 'p', 'o']) {
      await page.keyboard.press(`Control+${key}`);
      await page.waitForTimeout(150);
      const opened = await palOpen();
      await page.keyboard.press('Escape');
      await page.waitForTimeout(100);
      check(`Ctrl+${key.toUpperCase()} opens the palette`, opened && !(await palOpen()), opened);
    }
    await page.keyboard.press('Control+p');
    await page.keyboard.type('rerun');
    await page.waitForTimeout(150);
    const rerunRows = await palRows();
    await page.screenshot({ path: SHOT.replace('.png', '-palette.png'), clip: { x: 300, y: 80, width: 800, height: 360 } });
    check('typing "rerun" lists the cell and the device in Rerun, each row telling its device apart (the fixture has two D405s)', rerunRows.includes('Open the cell in Rerun') && rerunRows.some((t) => /^Open .*D405.* in Rerun$/.test(t))
      && new Set(await palSubs()).size === rerunRows.length, await palSubs());
    await page.fill('.lx-pal-in input', 'open');
    await page.waitForTimeout(150);
    const openRows = await palRows();
    check('typing "open" lists them too', openRows.includes('Open the cell in Rerun') && openRows.some((t) => /D405.* in Rerun$/.test(t)), openRows);
    await page.fill('.lx-pal-in input', 'rerun d405');
    await page.waitForTimeout(150);
    const [tab] = await Promise.all([context.waitForEvent('page', { timeout: 8000 }).catch(() => null), page.keyboard.press('Enter')]);
    check('Enter on it opens the device\'s Rerun view in a new tab', tab && tab.url() === CAMERA_URL, tab && tab.url());
    if (tab) await tab.close();

    // ---- a device the recorder does not list: muted words, no link ----------------------------------------------------------------
    await H.open(page, base, H.BOARD, 'diagnostics');
    await page.waitForSelector('.d6-line [data-d6-rerun]', { timeout: 8000 });
    const unlisted = await rerunOf(page);
    check('an unlisted board says so in muted words, with no link', unlisted.state === 'absent' && unlisted.href === null && /Not in the recorder's device list/.test(unlisted.text), unlisted);
    await page.screenshot({ path: SHOT.replace('.png', '-absent.png'), clip: { x: 0, y: 0, width: 1400, height: 260 } });

    // ---- a recorder that stopped: more than 10 s without a rewrite; the doc's start command is shown ------------------------------
    clearInterval(timer);
    timer = null;
    const stale = await H.waitFor(async () => {
      const found = (await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(H.BOARD)}`)).node;
      return found.rerun && found.rerun.absent === 'Recorder not running' && found;
    }, 20000, 500);
    check('without a rewrite for 10 s the node says the recorder is not running, with its start command', stale && stale.rerun.start_cmd === START_CMD, stale && stale.rerun);
    await H.open(page, base, H.CAMERA, 'diagnostics');
    await page.waitForSelector('.d6-line [data-d6-rerun="absent"]', { timeout: 8000 });
    const gone = await rerunOf(page);
    check('the page shows no link, the reason, and the command in a code span', gone.href === null && /Recorder not running/.test(gone.text) && gone.code.join() === START_CMD, gone);

    // ---- a doc that is not there at all: no command to show ------------------------------------------------------------------------
    fs.unlinkSync(file);
    const none = await H.waitFor(async () => {
      const found = (await H.getJson(base, `/api/v1/node?path=${encodeURIComponent(H.CAMERA)}`)).node;
      return found.rerun && found.rerun.absent && !found.rerun.start_cmd && found;
    }, 8000, 500);
    check('with no doc the node has the reason and no start command', Boolean(none), none && none.rerun);

    const pageErrors = watch.errors.filter((text) => !/^4\d\d /.test(text));
    check('no page error', pageErrors.length === 0, pageErrors);
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    if (timer) clearInterval(timer);
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
})();
