#!/usr/bin/env node
// Acceptance for the RobotDx page's timeline as a bottom panel (Zach, 2026-10-29: "The playback/rewind bar becomes a panel that
// rises from the bottom of the page, collapses, and is brought back from the bottom status bar, like the Terminal panel on the
// classic page today ... It is live by default, like a system monitor, and you can rewind at any moment. Its height is draggable
// and remembered, and it stays collapsed or open across reloads"). Written before the code; every claim is measured in the REAL
// page on a fixture host (getBoundingClientRect, computed style, the page clock's own state), never "the element exists":
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_dx_timeline.js
//
// The conventions are the classic page's Terminal panel (web/runs/r1.js): a panel between the page and a status bar, its top
// edge a horizontal separator that drags (remembered, double-click resets, arrow keys move it), a hide chevron in its head, a
// status-bar toggle (aria-pressed / aria-expanded / aria-controls) and Ctrl+` both ways, open or not kept across a reload.
// Exits 1 on any failure.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, BOARD } = H;

const DX = '/dx/';
const BROWSER_ARGS = ['--no-first-run', '--disable-extensions', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows'];

const box = (page, sel) => page.evaluate((s) => {
  const el = document.querySelector(s);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  return { x: r.x, y: r.y, w: r.width, h: r.height, top: r.top, bottom: r.bottom, left: r.left, right: r.right,
    shown: cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0 };
}, sel);
const clockOf = (page) => page.evaluate(() => { const c = window.robotdx.clock; return { following: c.following, t: c.t, liveEnd: c.liveEnd, start: c.start, end: c.end }; });
const ready = (page) => page.waitForFunction(() => window.robotdx && window.robotdx.clock && window.robotdx.clock.live && document.querySelector('#dx-main') && document.querySelector('#dx-statusbar'), null, { timeout: 30000 });
const toggleState = (page) => page.evaluate(() => {
  const b = document.querySelector('#dx-tl-toggle');
  return b ? { pressed: b.getAttribute('aria-pressed'), expanded: b.getAttribute('aria-expanded'), controls: b.getAttribute('aria-controls'), text: b.textContent.replace(/\s+/g, ' ').trim() } : null;
});
const lum = (page, sel) => page.evaluate((s) => {
  const el = document.querySelector(s);
  if (!el) return null;
  let n = el, rgb = null;
  // the first ancestor with a painted background is what the eye sees behind the element
  for (; n && n !== document.documentElement; n = n.parentElement) {
    const m = (getComputedStyle(n).backgroundColor.match(/[\d.]+/g) || []).map(Number);
    if (m.length >= 3 && (m.length < 4 || m[3] > 0.5)) { rgb = m.slice(0, 3); break; }
  }
  if (!rgb) rgb = (getComputedStyle(document.body).backgroundColor.match(/[\d.]+/g) || [255, 255, 255]).slice(0, 3).map(Number);
  return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
}, sel);

async function dragBy(page, handle, dy) {
  const x = handle.x + handle.w / 2, y = handle.y + handle.h / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + dy, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(300);
}

(async () => {
  let host;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true, args: BROWSER_ARGS });
  const requests = [];
  try {
    host = await H.fixtureHost('F1');
    const { base } = host;
    const context = await H.newContext(browser, 'light');
    const page = await context.newPage();
    const errs = H.watchErrors(page, base);
    page.on('request', (r) => { const u = new URL(r.url()); if (u.origin === base) requests.push({ method: r.method(), url: `${u.pathname}${u.search}` }); });
    await page.goto(`${base}${DX}?go=explore&sel=${encodeURIComponent(BOARD)}`);
    await ready(page);
    await page.waitForTimeout(3500);

    // ---- a panel at the bottom, on the status bar, open by default ------------------------------------------------------------
    const vh = await page.evaluate(() => innerHeight);
    const tl0 = await box(page, '#dx-timeline');
    const sb0 = await box(page, '#dx-statusbar');
    const main0 = await box(page, '#dx-main');
    check('the timeline is a panel that sits on the status bar, and the status bar on the window\'s bottom edge',
      tl0 && sb0 && main0 && tl0.shown && Math.abs(tl0.bottom - sb0.top) <= 1 && Math.abs(sb0.bottom - vh) <= 1 && sb0.h >= 22 && sb0.h <= 40,
      { timeline: tl0, statusbar: sb0, innerHeight: vh });
    check('…under the page: the page ends where the panel starts, and the panel spans the page\'s column',
      tl0 && main0 && Math.abs(main0.bottom - tl0.top) <= 1 && Math.abs(main0.left - tl0.left) <= 1 && Math.abs(main0.w - tl0.w) <= 1, { main: main0, timeline: tl0 });
    const t0 = await toggleState(page);
    check('it is open by default, and the status bar\'s Timeline toggle says so (pressed, expanded, controls the panel)',
      t0 && t0.pressed === 'true' && t0.expanded === 'true' && t0.controls === 'dx-timeline' && /timeline/i.test(t0.text), t0);
    const sash = await page.evaluate(() => { const s = document.querySelector('#dx-timeline [role=separator]'); return s ? { o: s.getAttribute('aria-orientation'), tab: s.tabIndex, label: s.getAttribute('aria-label') || '' } : null; });
    const sashBox = await box(page, '#dx-timeline [role=separator]');
    check('its top edge is a horizontal separator you can reach with the keyboard (the Terminal panel\'s sash)',
      sash && sash.o === 'horizontal' && sash.tab === 0 && sashBox && Math.abs((sashBox.top + sashBox.bottom) / 2 - tl0.top) <= 5 && sashBox.w >= tl0.w - 2, { sash, box: sashBox, panelTop: tl0 && tl0.top });
    const time0 = await box(page, '#dx-time');
    check('the playback bar is inside the panel', time0 && time0.shown && time0.top >= tl0.top - 1 && time0.bottom <= tl0.bottom + 1, { time: time0, timeline: tl0 });

    // ---- live by default, like a system monitor ---------------------------------------------------------------------------------
    const live0 = await clockOf(page);
    const thumb = await box(page, '#dx-time .dv-pb-thumb');
    const track = await box(page, '#dx-time .dv-pb-track');
    check('it is live by default: the clock follows the newest data, the bar\'s thumb at its track\'s right end',
      live0.following && Math.abs(live0.liveEnd - live0.t) < 0.5 && thumb && track && Math.abs((thumb.x + thumb.w / 2) - (track.x + track.w)) <= 4, { clock: live0, thumb, track });
    check('…and the status bar\'s toggle says LIVE', /LIVE/.test(t0.text) && !/−/.test(t0.text), t0.text);
    const hist = await page.evaluate(() => {
      const strip = document.querySelector('#dx-tl-health');
      const view = strip && strip.querySelector('[data-t1]');
      const plot = document.querySelector('#dx-tl-counts [data-points]');
      return { t1: view ? Number(view.dataset.t1) : null, strips: strip ? strip.querySelectorAll('[data-strip]').length : 0, bins: strip ? strip.querySelectorAll('[data-strip] [data-bin]').length : 0,
        points: plot ? Number(plot.dataset.points) : null, t: window.robotdx.clock.t };
    });
    check('the rack\'s history in the panel draws live: its health strip ends at the clock\'s t and its plot has points',
      hist.strips >= 1 && hist.bins > 0 && hist.t1 !== null && Math.abs(hist.t1 - hist.t) <= 1.5 && hist.points > 0, hist);
    const histBox = { health: await box(page, '#dx-tl-health'), counts: await box(page, '#dx-tl-counts') };
    check('…both inside the panel, under the bar', histBox.health && histBox.counts && histBox.health.shown && histBox.health.top >= time0.bottom - 1 && histBox.counts.bottom <= tl0.bottom + 1, histBox);

    // ---- rewind at any moment ---------------------------------------------------------------------------------------------------
    await page.mouse.move(track.x + track.w - 1, track.y + track.h / 2);
    await page.mouse.down();
    await page.mouse.move(track.x + track.w * 0.5, track.y + track.h / 2, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(600);
    const paused = await clockOf(page);
    const histAt = await page.evaluate(() => { const v = document.querySelector('#dx-tl-health [data-t1]'); return v ? Number(v.dataset.t1) : null; });
    const tPaused = await toggleState(page);
    check('a drag on the panel\'s track rewinds: the clock pauses at the dragged t, in the span',
      !paused.following && paused.t > paused.start && paused.t < paused.end - 1, paused);
    check('…the health strip\'s window ends at that t (it draws the past, not the newest data)', histAt !== null && Math.abs(histAt - paused.t) <= 0.5, { t1: histAt, t: paused.t });
    check('…and the status bar\'s toggle says paused, with how far behind', !/●\s*LIVE/.test(tPaused.text) && /−\s*\d/.test(tPaused.text), tPaused.text);
    const liveBtn = await box(page, '#dx-time .dv-pb-live');
    if (liveBtn) await page.mouse.click(liveBtn.x + liveBtn.w / 2, liveBtn.y + liveBtn.h / 2);
    await page.waitForTimeout(300);
    const back = await clockOf(page);
    check('one click on LIVE follows again', back.following && Math.abs(back.liveEnd - back.t) < 0.5, back);

    // ---- the height: drag, keep, keys, collapse, reload, bring back, reset -----------------------------------------------------
    const counts0 = await box(page, '#dx-tl-counts');
    await dragBy(page, sashBox, -80);
    const tl1 = await box(page, '#dx-timeline');
    const main1 = await box(page, '#dx-main');
    const counts1 = await box(page, '#dx-tl-counts');
    check('its top edge drags: 80 px taller, the bottom stays on the status bar, the page above gives up the same 80 px',
      Math.abs(tl1.h - tl0.h - 80) <= 3 && Math.abs(tl1.bottom - tl0.bottom) <= 1 && Math.abs(main0.h - main1.h - 80) <= 3, { before: tl0.h, after: tl1.h, main: [main0.h, main1.h] });
    check('…and the history plot takes the room (taller by most of it)', counts0 && counts1 && counts1.h - counts0.h >= 60, { before: counts0 && counts0.h, after: counts1 && counts1.h });
    await page.reload();
    await ready(page);
    await page.waitForTimeout(800);
    const tl2 = await box(page, '#dx-timeline');
    check('the height is kept across a reload', tl2 && tl2.shown && Math.abs(tl2.h - tl1.h) <= 2, { reload: tl2 && tl2.h, dragged: tl1.h });
    await page.focus('#dx-timeline [role=separator]');
    await page.keyboard.press('ArrowUp');
    await page.waitForTimeout(200);
    const tl3 = await box(page, '#dx-timeline');
    check('ArrowUp on the focused edge makes it 16 px taller', Math.abs(tl3.h - tl2.h - 16) <= 1, { before: tl2.h, after: tl3.h });
    await page.click('#dx-timeline [data-tl="hide"]');
    await page.waitForTimeout(300);
    const tlHidden = await box(page, '#dx-timeline');
    const mainOpen = await box(page, '#dx-main');
    const sbHidden = await box(page, '#dx-statusbar');
    const tHidden = await toggleState(page);
    check('its hide button collapses it: the page above takes the room down to the status bar, which stays',
      (!tlHidden || !tlHidden.shown) && Math.abs(mainOpen.bottom - sbHidden.top) <= 1 && sbHidden.shown && Math.abs(sbHidden.bottom - vh) <= 1 && tHidden.pressed === 'false' && tHidden.expanded === 'false',
      { timeline: tlHidden, main: mainOpen, statusbar: sbHidden, toggle: tHidden });
    await page.waitForTimeout(1500);
    const liveHidden = await clockOf(page);
    const tHidden2 = await toggleState(page);
    check('collapsed, the clock still follows live and the status bar says LIVE', liveHidden.following && Math.abs(liveHidden.liveEnd - liveHidden.t) < 0.5 && /LIVE/.test(tHidden2.text), { clock: liveHidden, toggle: tHidden2.text });
    await page.reload();
    await ready(page);
    await page.waitForTimeout(800);
    const tlReload = await box(page, '#dx-timeline');
    const tReload = await toggleState(page);
    check('collapsed is kept across a reload', (!tlReload || !tlReload.shown) && tReload.pressed === 'false', { timeline: tlReload, toggle: tReload });
    const toggleBox = await box(page, '#dx-tl-toggle');
    await page.mouse.click(toggleBox.x + toggleBox.w / 2, toggleBox.y + toggleBox.h / 2);
    await page.waitForTimeout(400);
    const tlBack = await box(page, '#dx-timeline');
    const sbBack = await box(page, '#dx-statusbar');
    check('the status bar\'s toggle brings it back at the height it had, on the status bar', tlBack && tlBack.shown && Math.abs(tlBack.h - tl3.h) <= 2 && Math.abs(tlBack.bottom - sbBack.top) <= 1 && (await toggleState(page)).pressed === 'true',
      { back: tlBack && tlBack.h, had: tl3.h });
    const sashBack = await box(page, '#dx-timeline [role=separator]');
    await page.mouse.dblclick(sashBack.x + sashBack.w / 2, sashBack.y + sashBack.h / 2);
    await page.waitForTimeout(300);
    const tlReset = await box(page, '#dx-timeline');
    check('a double-click on its edge resets the height', Math.abs(tlReset.h - tl0.h) <= 2, { reset: tlReset.h, default: tl0.h });
    await page.locator('#dx-main').click({ position: { x: 600, y: 20 } }).catch(() => {});
    await page.keyboard.press('Control+Backquote');
    await page.waitForTimeout(300);
    const viaKey1 = await box(page, '#dx-timeline');
    await page.keyboard.press('Control+Backquote');
    await page.waitForTimeout(300);
    const viaKey2 = await box(page, '#dx-timeline');
    check('Ctrl+` collapses it and brings it back (the Terminal panel\'s key)', (!viaKey1 || !viaKey1.shown) && viaKey2 && viaKey2.shown && Math.abs(viaKey2.h - tlReset.h) <= 2, { first: viaKey1 && viaKey1.shown, second: viaKey2 && viaKey2.shown });
    await context.close();

    // ---- dark, and a phone's width ---------------------------------------------------------------------------------------------
    const dark = await H.newContext(browser, 'dark');
    const darkPage = await dark.newPage();
    await darkPage.goto(`${base}${DX}?go=explore`);
    await ready(darkPage);
    await darkPage.waitForTimeout(1500);
    const darkLum = { timeline: await lum(darkPage, '#dx-timeline'), statusbar: await lum(darkPage, '#dx-statusbar'), toggle: await toggleState(darkPage) };
    check('dark: the panel and the status bar draw dark', darkLum.timeline !== null && darkLum.timeline < 0.2 && darkLum.statusbar !== null && darkLum.statusbar < 0.2, darkLum);
    await dark.close();
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
    const phonePage = await phone.newPage();
    await phonePage.goto(`${base}${DX}?go=explore`);
    await ready(phonePage);
    await phonePage.waitForTimeout(1500);
    const narrow = await phonePage.evaluate(() => {
      const col = document.querySelector('#dx-main').getBoundingClientRect();
      const tl = document.querySelector('#dx-timeline').getBoundingClientRect();
      const sb = document.querySelector('#dx-statusbar');
      const items = [...sb.children].filter((c) => { const r = c.getBoundingClientRect(); const cs = getComputedStyle(c); return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden' && cs.position !== 'absolute'; })
        .map((c) => ({ id: c.id || c.className, top: Math.round(c.getBoundingClientRect().top), h: Math.round(c.getBoundingClientRect().height) }));
      return { scroll: document.scrollingElement.scrollWidth, width: innerWidth, col: { left: col.left, w: col.width }, tl: { left: tl.left, w: tl.width, h: tl.height }, sbH: sb.getBoundingClientRect().height, items };
    });
    const tops = narrow.items.map((i) => i.top + i.h / 2);
    check('at 390 px: the panel spans the page\'s column, nothing scrolls sideways, the status bar is one row',
      narrow.scroll <= narrow.width + 1 && Math.abs(narrow.tl.w - narrow.col.w) <= 1 && narrow.tl.h > 40 && narrow.sbH <= 40 && tops.length >= 2 && Math.max(...tops) - Math.min(...tops) <= 4, narrow);
    await phone.close();

    // ---- read-only, no errors ---------------------------------------------------------------------------------------------------
    const writes = requests.filter((r) => r.method !== 'GET' && !(r.method === 'POST' && r.url === '/api/v1/watch'));
    check('the page sends no write but the State heartbeat (GET only otherwise)', writes.length === 0, writes.slice(0, 5));
    check('no page errors, no console errors, no failed request', errs.errors.length === 0, errs.errors.slice(0, 5));
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close().catch(() => {});
    if (host) H.stopServer(host.child);
  }
  process.exit(H.summary());
})();
