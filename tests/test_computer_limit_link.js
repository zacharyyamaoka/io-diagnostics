// The Computer's Diagnostics on the real page, with the real computer plugin's checks over fixed readings (tests/test_computer.py
// FakeProbe): the checks fold into one folder per device, and an open row says where its limits come from, the datasheet as a link.
// Zach, 2026-11-10: "grouped by device on the computer. RAM, CPU, GPU, etc" and "I want to be able to click this expand it ... go to the
// source for that threshold values as to why it was set at that value ... Ideally it should link to like a datasheet".
//
//   node tests/test_computer_limit_link.js        (a fixture host on a scratch port; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, fs, os, path } = H;

const AMD = 'https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-9-7900x.html';

// A plugin that matches the host and prints what the computer plugin prints for FakeProbe's readings (gigabyte_wmi at 97 °C on the
// A620M S2H), plus one check whose threshold_url is not a web address: that one must stay text.
function writePlugin(root) {
  const dir = path.join(root, 'plugins');
  fs.mkdirSync(dir, { recursive: true });
  const script = path.join(root, 'computer_fake.py');
  fs.writeFileSync(script, `
import json, sys
sys.path[:0] = [${JSON.stringify(path.join(H.ROOT, 'tests'))}, ${JSON.stringify(path.join(H.ROOT, 'plugins', 'computer'))}, ${JSON.stringify(H.ROOT)}]
from test_computer import FakeProbe, gigabyte
import computer_diagnostics as computer
from contract.record import OK, SCHEMA, DiagnosticStatus, KeyValue, to_dict
verb = next(word for word in sys.argv[1:] if word in ("describe", "diagnose", "act"))
if verb == "describe":
    print(json.dumps({"name": "computer_fake", "version": "0", "schema": SCHEMA, "record_version": "0.2", "matches": ["host"], "actions": []}))
elif verb == "diagnose":
    statuses = computer.diagnose(FakeProbe(chips=[*FakeProbe().chips, gigabyte(97.0)]), computer.Options(), host="rig")
    statuses.append(DiagnosticStatus(level=OK, name="rig: Not a web link", message="a limit whose source is no web address", hardware_id="rig",
                                     values=(KeyValue("threshold_source", "a plugin's own words"), KeyValue("threshold_url", "javascript:alert(1)")),
                                     expected_period_s=10.0, source="computer"))
    for status in statuses:
        print(json.dumps(to_dict(status)))
else:
    sys.exit(2)
`);
  fs.writeFileSync(path.join(dir, 'computer_fake.toml'), `name = "computer_fake"\ncommand = ["{python}", ${JSON.stringify(script)}]\n`
    + 'timeout_s = 60\nperiod_s = 10\nmatches = ["host"]\n');
  return dir;
}

const rowOf = (page, name) => page.locator('.dx-r', { has: page.locator('.dx-nt', { hasText: name }) }).first();

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'computer-limits-'));
  const server = await H.startServer(root, 'F1', {}, { plugins: writePlugin(root) });
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const { base } = server;
    const loaded = await H.getJson(base, '/api/v1/plugins');
    check('only the fake computer plugin is loaded', loaded.plugins.map((p) => p.name).join() === 'computer_fake', loaded.plugins.map((p) => p.name));
    const ready = await H.waitFor(async () => {
      const node = await H.getJson(base, '/api/v1/node?path=host').catch(() => ({}));
      return (node.checks || []).some((c) => c.name === 'CPU: CPU Information') && node;
    }, 90000, 500);
    check('the host has the computer checks, each named after its device', Boolean(ready), ready && ready.checks.map((c) => c.name));

    const context = await H.newContext(browser, 'light');
    // the datasheet opens in a new tab: answered here, so the test never leaves this machine
    await context.route('https://www.amd.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<title>AMD Ryzen 9 7900X</title>' }));
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    await H.open(page, base, 'host', 'diagnostics');
    await page.waitForFunction(() => [...document.querySelectorAll('.er-fn')].some((el) => el.textContent === 'CPU'), null, { timeout: 20000 });

    // ---- grouped by device ---------------------------------------------------------------------------------------------------
    const folders = await page.evaluate(() => [...document.querySelectorAll('[data-dx-embedded] .er-fn')].map((el) => el.textContent));
    check('the Diagnostics list folds the checks into device folders', ['CPU', 'Disks', 'Board'].every((name) => folders.includes(name)), folders);
    const inCpu = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('[data-dx-embedded] .er-fold, [data-dx-embedded] .dx-r')];
      const at = rows.findIndex((el) => el.matches('.er-fold') && el.querySelector('.er-fn').textContent === 'CPU');
      const out = [];
      for (const el of rows.slice(at + 1)) { if (el.matches('.er-fold')) break; out.push(el.querySelector('.dx-nt').textContent); }
      return out;
    });
    check('the CPU folder holds the CPU checks under their ROS names', inCpu.includes('CPU Information') && inCpu.includes('Temperature CPU (k10temp)'), inCpu);
    const gigabyteRow = await rowOf(page, 'Temperature gigabyte_wmi').evaluate((el) => ({ level: el.querySelector('.dx-s').textContent.trim(), bad: el.classList.contains('bad') }));
    check('gigabyte_wmi at 97 °C is no error: no limit is known for it', /ok/i.test(gigabyteRow.level) && !gigabyteRow.bad, gigabyteRow);

    // ---- an open row says where its limit comes from, the datasheet a link ---------------------------------------------------------
    const cpu = rowOf(page, 'Temperature CPU (k10temp)');
    await cpu.locator('.dx-line').click();
    await page.waitForSelector('.dx-r.open .d6-lim a', { timeout: 8000 });
    const link = await cpu.locator('.d6-lim a').first().evaluate((a) => ({ href: a.getAttribute('href'), target: a.target, rel: a.rel, text: a.textContent,
      line: a.closest('.d6-lim-l').textContent }));
    check('the open row names the limit\'s source', /Tjmax/.test(link.line) && /AMD Ryzen 9 7900X/.test(link.line) && /RobotDx/.test(link.line), link.line);
    check('its URL is a link to the datasheet page, opening a new tab', link.href === AMD && link.target === '_blank' && /\bnoopener\b/.test(link.rel), link);
    check('the link shows the URL as written (never shortened)', link.text === AMD, link.text);
    const [popup] = await Promise.all([context.waitForEvent('page', { timeout: 8000 }), cpu.locator('.d6-lim a').first().click()]);
    await popup.waitForLoadState().catch(() => {});
    check('clicking it opens the page in a new tab', popup.url() === AMD, popup.url());
    await popup.close();
    check('and the row stays open', await cpu.evaluate((el) => el.classList.contains('open')));

    const board = rowOf(page, 'Temperature gigabyte_wmi');
    await board.locator('.dx-line').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.dx-r.open')].some((el) => /gigabyte_wmi/.test(el.textContent) && el.querySelector('.d6-lim')), null, { timeout: 8000 });
    const boardSrc = await board.evaluate((el) => [...el.querySelectorAll('.d6-lim-l')].map((line) => ({ kind: line.dataset.d6Lim, text: line.textContent,
      links: [...line.querySelectorAll('a')].map((a) => a.getAttribute('href')) })));
    check('gigabyte_wmi says it has no limit, and why, with the driver\'s source', boardSrc.some((l) => l.kind === 'threshold' && /^Limits\s*none/.test(l.text)
      && l.links.some((href) => /gigabyte-wmi\.c$/.test(href))), boardSrc);
    check('and where its sensor names come from', boardSrc.some((l) => l.kind === 'labels' && /probable/.test(l.text) && l.links.some((href) => /lm-sensors/.test(href))), boardSrc);
    // the long source text wraps inside the row: measured, never assumed (a "nowrap" from another rule clipped it on 2026-11-11)
    const fit = await board.evaluate((el) => {
      const row = el.getBoundingClientRect();
      const block = el.querySelector('.d6-lim');
      return { overflow: block.scrollWidth - block.clientWidth, right: Math.max(...[...block.querySelectorAll('.d6-lim-v')].map((v) => v.getBoundingClientRect().right)) - row.right,
        lines: Math.round(block.querySelector('[data-d6-lim="labels"] .d6-lim-v').getBoundingClientRect().height / 15) };
    });
    check('the long source text wraps inside the row (nothing runs past its edge)', fit.overflow <= 1 && fit.right <= 1 && fit.lines >= 3, fit);

    const odd = rowOf(page, 'Not a web link');
    await odd.locator('.dx-line').click();
    await page.waitForFunction(() => [...document.querySelectorAll('.dx-r.open')].some((el) => /Not a web link/.test(el.textContent) && el.querySelector('.d6-lim')), null, { timeout: 8000 });
    const oddSrc = await odd.evaluate((el) => ({ links: el.querySelectorAll('.d6-lim a').length, text: el.querySelector('.d6-lim').textContent }));
    check('a URL that is not http(s) stays text, never a link', oddSrc.links === 0 && oddSrc.text.includes('javascript:alert(1)'), oddSrc);

    const shot = path.join(root, 'computer-diagnostics.png');
    await page.screenshot({ path: shot, fullPage: false });
    console.log(`screenshot: ${shot}`);
    const pageErrors = watch.errors.filter((text) => !/^4\d\d /.test(text));
    check('no page error', pageErrors.length === 0, pageErrors);
  } catch (error) {
    check('the run finished', false, String(error && error.stack || error));
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
})();
