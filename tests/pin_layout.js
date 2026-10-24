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

module.exports = { pinDetailLayout };
