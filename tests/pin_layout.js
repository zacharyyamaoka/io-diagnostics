// The detail panel's default is d6 (the entity page, tabs); the older acceptance suites were written against d0 (sections top to
// bottom) and are about the page's shell, the tree and the utilities, not the entity page. They pin d0 the way a person's
// Prototype picker would: every browser context they open starts with it remembered. No assertion of theirs changed.
'use strict';

function pinDetailLayout(browser, id = 'd0') {
  const original = browser.newContext.bind(browser);
  browser.newContextRaw = original; // a test of the DEFAULT layout needs a profile that remembers nothing
  browser.newContext = async (...args) => {
    const context = await original(...args);
    await context.addInitScript((layout) => { try { if (!localStorage.getItem('io-diagnostics.detail-layout')) localStorage.setItem('io-diagnostics.detail-layout', layout); } catch (_) { /* private mode */ } }, id);
    return context;
  };
  return browser;
}

// The page layout's default is L1 (Obsidian: a ribbon, the tree beside the page); the same suites measure the classic arrangement
// (the toolbar over the tree | detail panes), which is L0 "Today". Pinned the same way: every context starts with L0 remembered.
function pinPageLayout(browser, id = 'l0') {
  const original = browser.newContext.bind(browser);
  browser.newContextRaw = browser.newContextRaw || original;
  browser.newContext = async (...args) => {
    const context = await original(...args);
    await context.addInitScript((layout) => { try { if (!localStorage.getItem('io-diagnostics.page-layout')) localStorage.setItem('io-diagnostics.page-layout', layout); } catch (_) { /* private mode */ } }, id);
    return context;
  };
  return browser;
}

module.exports = { pinDetailLayout, pinPageLayout };
