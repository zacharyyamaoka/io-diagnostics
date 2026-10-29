#!/usr/bin/env node
// Two fixes of 2026-10-29 (Zach), in the REAL page on a fixture server (F1), headless Chrome, measured not DOM-presence:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_fill_and_rollup.js
//
//   fill     the entity page fills the screen: its head, tabs and tab panel reach the main area's right edge (was a 760 px
//            reading column and an 1100 px panel: "the entity page doesn't seem to expand to fill the entire screen")
//   roll-up  an OPEN parent row never shows its derived roll-up mark, not when hovered and not when selected ("the roll up
//            icon IF its derived from the children shouldn't show"); a collapsed row still shows it
// Exits 1 on any failure.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check } = H;

(async () => {
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  let server;
  try {
    server = await H.fixtureHost('F1');
    const context = await browser.newContext({ viewport: { width: 1920, height: 1000 } });
    const page = await context.newPage();
    await H.open(page, server.base, H.BOARD);

    for (const tab of ['diagnostics', 'log']) {
      await H.goTab(page, tab);
      await page.waitForTimeout(400);
      const box = await page.evaluate(() => {
        const main = document.querySelector('.d6-tabs').parentElement.getBoundingClientRect();
        const right = (selector) => { const el = document.querySelector(selector); return el ? el.getBoundingClientRect().right : null; };
        const panelEl = document.querySelector('.d6-panel');
        return { main: main.right, tabs: right('.d6-tabs'), panel: right('.d6-panel'), width: panelEl ? panelEl.getBoundingClientRect().width : 0, head: right('.d6-head .d-title') ? document.querySelector('.d6-head').getBoundingClientRect().right : null };
      });
      check(`fill · ${tab}: the tab panel reaches the page's right edge (panel ${box.panel?.toFixed(0)} vs ${box.main.toFixed(0)})`, box.panel !== null && box.main - box.panel <= 2);
      check(`fill · ${tab}: the head reaches the page's right edge (head ${box.head?.toFixed(0)} vs ${box.main.toFixed(0)})`, box.head !== null && box.main - box.head <= 2);
      check(`fill · ${tab}: wider than the old 1100 px cap (panel width ${box.width.toFixed(0)})`, box.width > 1300);
    }

    // the adapter row is open (its boards show below it): select it, hover it, and read the roll-up mark's real opacity
    await H.open(page, server.base, H.ADAPTER);
    const row = await H.waitFor(() => page.evaluate((p) => {
      const el = [...document.querySelectorAll('#tree-host .t6-row')].find((r) => r.dataset.t6Path === p);
      return el ? true : false;
    }, H.ADAPTER), 10000);
    check('roll-up: the adapter row is in the tree', !!row);
    const read = () => page.evaluate((p) => {
      const el = [...document.querySelectorAll('#tree-host .t6-row')].find((r) => r.dataset.t6Path === p);
      const mark = el && el.querySelector('.t6-mark.t6-roll');
      const rect = el && el.getBoundingClientRect();
      return { open: !!(el && el.querySelector('.t6-chev.t6-open')), selected: !!(el && el.classList.contains('t6-sel')),
        mark: !!mark, opacity: mark ? Number(getComputedStyle(mark).opacity) : null, x: rect && rect.x + rect.width / 2, y: rect && rect.y + rect.height / 2 };
    }, H.ADAPTER);
    let state = await read();
    if (!state.open) {
      await page.evaluate((p) => { const el = [...document.querySelectorAll('#tree-host .t6-row')].find((r) => r.dataset.t6Path === p); el.querySelector('.t6-chev').dispatchEvent(new MouseEvent('click', { bubbles: true })); }, H.ADAPTER);
      await page.waitForTimeout(300);
      state = await read();
    }
    check('roll-up: the adapter row is open and selected', state.open && state.selected);
    check(`roll-up: selected open row shows no derived mark (opacity ${state.opacity})`, !state.mark || state.opacity === 0);
    await page.mouse.move(state.x, state.y);
    await page.waitForTimeout(250);
    state = await read();
    check(`roll-up: hovered open row shows no derived mark (opacity ${state.opacity})`, !state.mark || state.opacity === 0);
  } catch (error) {
    check(`no exception: ${error && error.stack}`, false);
  } finally {
    if (server) H.stopServer(server.child);
    await browser.close();
  }
  process.exitCode = H.summary();
})();
