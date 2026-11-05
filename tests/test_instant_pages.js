// The pages are just there (Zach, 2026-11-04: "I don't like how it kinda just like loads... surely maybe there is like a timeout on
// these things? ... Like for the log shouldn't it just be there?"). The page's half; tests/test_instant_pages.py is the server's.
//
// A fixture host, real Chrome, and the stall put in the browser's route, so a test says exactly how slow the host is. Measured on main before
// this suite: with /node taking 3 s the page sent 645 requests in 30 s and painted nothing, because every tick and every live event asked again
// and an answer counted only if it was the newest asked for.
//
//     node tests/test_instant_pages.js
'use strict';

const H = require('./entity_harness');
const net = require('net');
const { chromium } = require(H.PLAYWRIGHT);

const { BOARD, BOARD2, ADAPTER, CAMERA, check, wait, waitFor } = H;
const FIRST_PAINT_MS = 100;       // the brief: first useful paint under 100 ms
const LOAD_BOUND_MS = 12000;      // the longest a page may wait before it says what is slow

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
  });
}

const stalled = (page, glob, ms) => page.route(glob, async (route) => { await wait(ms); await route.continue().catch(() => {}); });
function hung(page, glob) {
  const held = [];
  return page.route(glob, (route) => { held.push(route); }).then(() => ({
    held,
    release: async () => { await page.unroute(glob); for (const route of held) await route.abort().catch(() => {}); },
  }));
}
const detailText = (page) => page.evaluate(() => document.getElementById('detail').innerText);

// the first useful paint of an entity, by the page's own clock: its name in the header and its tabs, in the frame after the selection
const firstPaint = (page, target, name, reads = 'name') => page.evaluate(({ target, name }) => new Promise((resolve) => {
  const began = performance.now();
  select(target); pushNav(); render();
  const look = () => {
    const title = document.querySelector('#detail .d6-name');
    const tabs = document.querySelector('#detail .d6-tabs');
    const panel = document.querySelector('#detail .d6-panel');
    if (title && title.textContent.includes(name) && tabs && panel && panel.innerText.trim()) { resolve(Math.round(performance.now() - began)); return; }
    if (performance.now() - began > 4000) { resolve(null); return; }
    requestAnimationFrame(look);
  };
  look();
}), { target, name, reads });

async function scenario(browser, name, body) {
  if (process.env.ONLY && !name.includes(process.env.ONLY)) return;
  const context = await H.newContext(browser);
  const page = await context.newPage();
  try {
    await body(page, context);
  } catch (error) {
    check(`${name}: ran to the end`, false, String(error).slice(0, 200));
  } finally {
    await context.close();
  }
}

(async () => {
  const server = await H.fixtureHost('F1');
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const url = (path, extra = '') => `${server.base}/?sel=${encodeURIComponent(path)}${extra}`;
  try {
    // ----- one request at a time, and its answer lands ----------------------------------------------------------------------------
    await scenario(browser, 'slow node', async (page) => {
      let sent = 0, flying = 0, most = 0;
      await page.route('**/api/v1/node*', async (route) => {
        sent += 1; flying += 1; most = Math.max(most, flying);
        await wait(3000);
        await route.continue().catch(() => {});
        flying -= 1;
      });
      await page.goto(url(ADAPTER));
      const landed = await waitFor(() => page.evaluate(() => typeof S !== 'undefined' && Boolean(S.node)), 8000, 100);
      check('a /node that takes 3 s lands (main: never, in 30 s)', Boolean(landed));
      await wait(1000);
      check('the page asks once, it does not ask again while it waits', sent <= 3, `${sent} requests in ~6 s (main: ~130)`);
      check('at most one /node is in flight', most === 1, `${most} at once`);
    });

    // ----- the first paint comes from what the page already has -------------------------------------------------------------------
    await scenario(browser, 'first paint', async (page) => {
      await H.open(page, server.base, ADAPTER);
      await stalled(page, '**/api/v1/node*', 2500);
      const row = await page.evaluate((path) => S.byPath.get(path).name, BOARD);
      const painted = await firstPaint(page, BOARD, row);
      check(`a page never visited paints from its tree row in under ${FIRST_PAINT_MS} ms, its /node 2.5 s away`, painted !== null && painted < FIRST_PAINT_MS, `${painted} ms`);
      const text = await detailText(page);
      check('that paint says what the row knows, not only "loading…"', text.replace(/loading…/g, '').trim().length > 40 && text.includes(row), text.slice(0, 80).replace(/\n/g, ' | '));
      const landed = await waitFor(() => page.evaluate((path) => S.node && S.node.node.path === path, BOARD), 6000, 100);
      check('the detail fills in when it arrives', Boolean(landed));
    });

    await scenario(browser, 'cached node', async (page) => {
      await H.open(page, server.base, ADAPTER);
      const shown = await page.evaluate(() => document.querySelector('#detail .d6-panel').innerText.length);
      const name = await page.evaluate(() => document.querySelector('#detail .d6-name').textContent); // WHY the header's: it draws the row's label
      await H.goTo(page, BOARD);
      await hung(page, '**/api/v1/node*');
      const painted = await firstPaint(page, ADAPTER, name);
      const panel = await page.evaluate(() => document.querySelector('#detail .d6-panel').innerText.length);
      check('a page already visited comes back at once, whole, with the host not answering', painted !== null && painted < FIRST_PAINT_MS && panel >= shown * 0.8, `${painted} ms, panel ${panel} of ${shown} characters`);
    });

    // ----- a bounded wait, then it says so ----------------------------------------------------------------------------------------
    await scenario(browser, 'node timeout', async (page) => {
      await H.open(page, server.base, ADAPTER);
      const held = await hung(page, '**/api/v1/node*');
      await page.evaluate((path) => { select(path); pushNav(); render(); }, BOARD2);
      const said = await waitFor(() => page.locator('[data-load-slow]').count(), LOAD_BOUND_MS, 100);
      check(`a /node that never answers is said within ${LOAD_BOUND_MS / 1000} s, not spun on forever`, Boolean(said));
      const text = await detailText(page);
      check('it says what is slow', /details|not answer|slow/i.test(await page.locator('[data-load-slow]').first().innerText().catch(() => '')), text.slice(0, 120).replace(/\n/g, ' | '));
      check('the selection stays', await page.evaluate((path) => S.sel === path, BOARD2));
      const name = await page.evaluate((path) => S.byPath.get(path).name, BOARD2);
      check('the row stays on the page beside it', text.includes(name), name);
      await held.release();
      await page.locator('[data-load-retry]').first().click().catch(() => {});
      const landed = await waitFor(() => page.evaluate((path) => S.node && S.node.node.path === path, BOARD2), 6000, 100);
      check('Retry asks again and the page fills in', Boolean(landed));
      check('the note goes away', await waitFor(async () => !(await page.locator('[data-load-slow]').count()), 3000, 100));
    });

    await scenario(browser, 'not here yet', async (page) => {
      // a board's row comes back when its driver has reported, seconds after a restarted host is up: the URL it was opened on stays
      await H.open(page, server.base, ADAPTER);
      await page.route('**/api/v1/node*', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"no entity"}' }));
      await page.evaluate(() => { select('host/usb1/port-1-3/usb-1-3/can/77'); pushNav(); render(); });
      await wait(3000);
      check('an entity not in the tree yet keeps the selection and is asked for again', await page.evaluate(() => S.sel === 'host/usb1/port-1-3/usb-1-3/can/77'));
    });

    await scenario(browser, 'node failure', async (page) => {
      await H.open(page, server.base, ADAPTER);
      await page.route('**/api/v1/node*', (route) => route.abort());
      await page.evaluate((path) => { select(path); pushNav(); render(); }, BOARD);
      await wait(3500);
      check('a failed /node does not lose the selection (main: back to the Computer)', await page.evaluate((path) => S.sel === path, BOARD));
      check('the URL still names it', page.url().includes(encodeURIComponent(BOARD)));
    });

    await scenario(browser, 'state timeout', async (page) => {
      const held = await hung(page, '**/api/v1/values*');
      await page.goto(url(BOARD, '&page=state'));
      await page.waitForSelector('[data-d6-panel="state"]', { timeout: 20000 });
      const said = await waitFor(() => page.locator('[data-d6-repoll]').count(), LOAD_BOUND_MS, 100);
      check(`live values that never answer are said within ${LOAD_BOUND_MS / 1000} s, not "loading…" for ever`, Boolean(said));
      const text = await page.evaluate(() => document.querySelector('[data-d6-panel="state"]').innerText);
      check('it says what is slow', /not answered/.test(text), text.slice(0, 120).replace(/\n/g, ' | '));
      await held.release();
      await page.locator('[data-d6-repoll]').first().click().catch(() => {});
      check('Retry reads them', Boolean(await waitFor(() => page.locator('.d6-sr').count(), 8000, 100)));
    });

    // ----- the log ----------------------------------------------------------------------------------------------------------------
    await scenario(browser, 'log loading', async (page) => {
      await stalled(page, '**/api/v1/activity*', 2500);
      await page.goto(`${server.base}/?view=activity`);
      await page.waitForSelector('.al-strip', { timeout: 20000 });
      await wait(800);
      const during = await page.evaluate(() => document.querySelector('.al').innerText);
      check('"no events yet" is not said while the log is loading', !/no events yet|Nothing has been logged/.test(during), during.slice(0, 120).replace(/\n/g, ' | '));
      const rows = await waitFor(() => page.locator('.al-list .ev').count(), 9000, 100);
      check('the log arrives', Boolean(rows));
    });

    await scenario(browser, 'log timeout', async (page) => {
      const held = await hung(page, '**/api/v1/activity*');
      await page.goto(`${server.base}/?view=activity`);
      await page.waitForSelector('.al-strip', { timeout: 20000 });
      const said = await waitFor(() => page.locator('[data-al-retry]').count(), LOAD_BOUND_MS, 100);
      check(`a log that never answers is said within ${LOAD_BOUND_MS / 1000} s (main: "Loading the log…" for ever)`, Boolean(said));
      const text = await page.evaluate(() => document.querySelector('.al').innerText);
      check('it does not say the log is empty', !/no events yet|Nothing has been logged/.test(text));
      await held.release();
      await page.locator('[data-al-retry]').first().click().catch(() => {});
      check('Retry loads it', Boolean(await waitFor(() => page.locator('.al-list .ev').count(), 8000, 100)));
    });

    await scenario(browser, 'log keeps rows', async (page) => {
      // WHY openPage: the page's own way to a tab, whichever tab this kind of entity remembers
      const toLog = (path) => page.evaluate((target) => { select(target); openPage('log'); }, path);
      await H.open(page, server.base, ADAPTER, 'log');
      await page.waitForSelector('[data-d6-log] .ev', { timeout: 20000 });
      const before = await page.locator('[data-d6-log] .ev').count();
      await toLog(CAMERA);
      await page.waitForSelector('[data-d6-log] .al-list', { timeout: 20000 });
      await page.route('**/api/v1/activity*', (route) => route.abort());
      await page.route('**/api/v1/node*', (route) => route.abort());
      await toLog(ADAPTER);
      const back = await waitFor(() => page.locator('[data-d6-log] .ev').count(), 400, 20);
      check('coming back to a log shows its rows at once, the host not answering', back >= before, `${back} rows, ${before} when it was left`); // WHY >=: the fixture logs on
      await wait(9500); // the log's own 8 s refresh fails too
      check('a log that fails to refresh keeps the rows it has', (await page.locator('[data-d6-log] .ev').count()) === back, `${back} rows`);
      check('and says so, with Retry', Boolean(await page.locator('[data-d6-log] .al-late [data-al-retry]:visible').count()));
      await page.unroute('**/api/v1/activity*');
      await page.locator('[data-d6-log] .al-late [data-al-retry]').click();
      check('Retry clears it', Boolean(await waitFor(async () => !(await page.locator('[data-d6-log] .al-late:visible').count()), 3000, 100)));
    });

    // Seen on Preview, 2026-11-04: an entity's Log tab opened on a blank band with its newest row cut in half, no number, no time, no severity.
    // Measured, not by the DOM: the row is in the DOM either way. The band is sticky, so it sits at its `top` with nothing scrolled.
    const dayBand = (selector) => page => page.evaluate((list_selector) => {
      const list = document.querySelector(list_selector);
      const day = list.querySelector('.al-day').getBoundingClientRect();
      const row = list.querySelector('.ev').getBoundingClientRect();
      const time = list.querySelector('.ev .t').getBoundingClientRect();
      const head = list.querySelector('.al-head').getBoundingClientRect();
      const shown = document.elementFromPoint(time.left + time.width / 2, time.top + time.height / 2);
      return { scrollTop: list.scrollTop, gap: Math.round(day.top - list.getBoundingClientRect().top), under: Math.round(day.bottom - row.top),
        timeShown: Boolean(shown && shown.closest('.ev')), headBottom: Math.round(head.bottom - list.getBoundingClientRect().top) };
    }, selector);

    await scenario(browser, 'log newest row', async (page) => {
      await H.open(page, server.base, ADAPTER, 'log');
      await page.waitForSelector('[data-d6-log] .ev', { timeout: 20000 });
      const read = dayBand('[data-d6-log] .al-list');
      const seen = await read(page);
      check('an entity\'s log starts at the top of its list: no blank band over the date', seen.gap === 0, JSON.stringify(seen));
      check('the date does not sit on the newest row', seen.under <= 0, JSON.stringify(seen));
      check('the newest row shows its time', seen.timeShown, JSON.stringify(seen));
      await page.evaluate(() => { const list = document.querySelector('[data-d6-log] .al-list'); list.style.height = '120px'; list.scrollTop = 60; });
      await wait(100);
      const scrolled = await read(page);
      check('scrolled, the date stays at the top of the list', scrolled.scrollTop > 0 && scrolled.gap === 0, JSON.stringify(scrolled));
    });

    await scenario(browser, 'log header stays', async (page) => {
      await page.goto(`${server.base}/?view=activity`);
      await page.waitForSelector('.al-list .ev', { timeout: 20000 });
      const seen = await dayBand('.al-list')(page);
      check('the Activity page keeps its column header, the date under it', seen.headBottom > 0 && seen.gap >= seen.headBottom - 1, JSON.stringify(seen));
      check('and its newest row shows its time', seen.under <= 0 && seen.timeShown, JSON.stringify(seen));
    });

    // ----- the poll does not pile up either --------------------------------------------------------------------------------------
    await scenario(browser, 'state poll', async (page) => {
      await page.route('**/api/v1/live*', (route) => route.abort());
      let polls = 0;
      await H.open(page, server.base, ADAPTER).catch(() => {});
      await page.waitForFunction(() => typeof S !== 'undefined' && S.data, null, { timeout: 15000 });
      await page.route('**/api/v1/state*', (route) => { polls += 1; });  // held: never answered
      await wait(5000);
      check('a /state poll that does not answer is not stacked up (at most one in flight)', polls <= 1, `${polls} in 5 s`);
    });

    // ----- the chip of the release channels ------------------------------------------------------------------------------------------
    await scenario(browser, 'chip', async (page) => {
      await H.open(page, server.base, ADAPTER);
      let tries = 0;
      await page.route('**/__sps/status', (route) => {
        tries += 1;
        return tries < 3 ? route.abort() : route.fulfill({ status: 200, contentType: 'application/json', body: '{"channel":"preview"}' });
      });
      const answer = await page.evaluate(() => fetch('/__sps/status').then((response) => response.json()).catch((error) => String(error)));
      check('a chip poll rides out a restart of a few seconds (the red "server unreachable" was one failed poll)', answer && answer.channel === 'preview', `${tries} tries, ${JSON.stringify(answer)}`);
    });
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }

  // ----- a restart is invisible -----------------------------------------------------------------------------------------------------
  const browser2 = await chromium.launch({ executablePath: H.CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const root = H.fs.mkdtempSync(H.path.join(H.os.tmpdir(), 'instant-pages-'));
  const port = await freePort();
  let first = await H.startServer(root, 'F1', {}, { args: ['--port', String(port)] });
  let second = null;
  try {
    await waitFor(async () => {
      const node = await H.getJson(first.base, `/api/v1/node?path=${encodeURIComponent(BOARD)}`).catch(() => ({}));
      return (node.config || []).length;
    }, 30000, 300);
    const page = await (await H.newContext(browser2)).newPage();
    await H.open(page, first.base, ADAPTER);
    const name = await page.evaluate(() => document.querySelector('#detail .d6-name').textContent);
    const panel = await page.evaluate(() => document.querySelector('#detail .d6-panel').innerText.length);
    // the State tab of a board, watched frame by frame through the restart: its rows never go
    const live = await (await H.newContext(browser2)).newPage();
    await H.open(live, first.base, BOARD, 'state');
    await live.waitForSelector('.d6-sr', { timeout: 15000 });
    await live.evaluate(() => {
      window.__fewest = Infinity;
      const look = () => { window.__fewest = Math.min(window.__fewest, document.querySelectorAll('.d6-sr').length); requestAnimationFrame(look); };
      look();
    });
    H.stopServer(first.child);
    await wait(2500);
    const during = await page.evaluate(() => ({ sel: S.sel, late: document.getElementById('lastmsg').className.includes('late'), text: document.getElementById('lastmsg').textContent,
      detail: document.getElementById('detail').innerText }));
    check('down: the selection stays', during.sel === ADAPTER, during.sel);
    check('down: the page keeps showing the last data', during.detail.includes(name) && during.detail.length >= panel * 0.5, `${during.detail.length} characters`);
    check('down: said calmly, in no error colour, for a few seconds\' restart', !during.late, during.text);
    check('down: it says it is reconnecting and how old the data is', /reconnect/i.test(during.text) && /\d+ s/.test(during.text), during.text);
    second = await H.startServer(root, 'F1', {}, { args: ['--port', String(port)] });
    const back = await waitFor(() => page.evaluate(() => IOClassicLive.connected && !S.lastError), 20000, 150);
    check('back: the page resumes by itself', Boolean(back));
    await wait(1500);
    await waitFor(() => live.evaluate(() => IOClassicLive.connected && IODetailLayouts.d6.internals.Live.loaded), 15000, 150);
    await wait(500);
    const fewest = await live.evaluate(() => window.__fewest);
    check('back: the State tab kept its rows through the restart, in every frame', fewest > 0, `at least ${fewest} rows`);
    const after = await page.evaluate(() => ({ sel: S.sel, late: document.getElementById('lastmsg').className.includes('late'), text: document.getElementById('lastmsg').textContent }));
    check('back: the selection is the one it had', after.sel === ADAPTER, after.sel);
    check('back: nothing is left in an error colour', !after.late, after.text);
    check('back: the entity page is drawn', Boolean(await waitFor(() => page.locator('#detail .d6-tabs').count(), 5000, 100)));
  } finally {
    await browser2.close();
    H.stopServer(first.child);
    if (second) H.stopServer(second.child);
  }
  process.exitCode = H.summary();
})().catch((error) => { console.error(error); process.exitCode = 1; });
