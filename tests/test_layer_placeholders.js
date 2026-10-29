// The placeholder workspaces for the layers that are decided but not built (docs/peps/0002-robotdx-robot-health-super-app.md):
// Deploy (software, OTA and config), Teleop and Data (storage), measured in headless Chrome on a fixture host (frame F1 with the
// state fixture plugins). Never hardware. Positions are getBoundingClientRect, "leads somewhere" is a click and a changed page.
//
//   node tests/test_layer_placeholders.js
//
// Zach, 2026-10-29: honest placeholders: a calm page that says what the layer will do and what it builds on, the device tree still on
// the left, and no fake control: every link and button on them goes somewhere that exists, there are no fake numbers.
// IO_DIAG_LAYER_SHOTS=<dir> also writes each page's screenshot (1440x900, light and dark) there.
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, fs, path } = H;

const LAYERS = [
  { id: 'deploy', label: 'Deploy', title: /Software deploy, OTA and config/i, text: [/firmware, software, config/i, /neural networks/i, /versioned together/i, /fully tracked/i, /one device/i, /facility, state or fleet/i] },
  { id: 'teleop', label: 'Teleop', title: /Teleop/i, text: [/live camera/i, /WebRTC/, /end effector/i, /Leap Motion/] },
  { id: 'data', label: 'Data', title: /Data storage/i, text: [/where recorded data/i, /retention/i, /cost/i, /recent history/i, /system monitor/i] },
];
const SHOTS = process.env.IO_DIAG_LAYER_SHOTS || '';
const BOARD = H.BOARD;

const rectOf = (page, selector) => page.evaluate((s) => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom, visible: r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden' }; }, selector);
const viewOf = (page) => page.evaluate(() => new URLSearchParams(location.search).get('view') || 'tree');
const stateOf = (page) => page.evaluate(() => `${location.search}|${S.view}|${S.sel}|${S.drill.page}`);
const item = (id) => `.l1-ribbon [data-l1="${id}"]`;
async function start(page, base, query = '') {
  await page.goto(`${base}/${query}`);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.data && S.built && document.querySelector('.l1-ribbon [data-l1="tree"]'), null, { timeout: 30000 });
  await page.waitForTimeout(500);
}
// WHY the class too: since the perf work a popstate renders in the next animation frame, so S.view changes a frame before the page is drawn;
// #main's v-<view> class is set by the render that draws the view, so waiting on both measures the page, not the state
const waitView = (page, view) => page.waitForFunction((v) => S.view === v && document.getElementById('main').classList.contains(`v-${v}`), view, { timeout: 8000 });
async function openPalette(page, text) {
  await page.keyboard.press('Control+k');
  await page.waitForSelector('.lx-pal-back:not([hidden]) input');
  await page.fill('.lx-pal-back input', text);
  await page.waitForTimeout(150);
}
async function openLayer(page, id) {
  await page.click(item('tree'));
  await waitView(page, 'tree');
  await page.click(item(id));
  await waitView(page, id);
  await page.waitForTimeout(200);
}

(async () => {
  const server = await H.fixtureHost('F1');
  const base = server.base;
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  let failed = false;
  try {
    const context = await H.newContext(browser, 'light');
    const page = await context.newPage();
    const errors = H.watchErrors(page, base);
    await start(page, base);

    // ---------------------------------------------------------------- the rail: after the existing items, in order ----
    const order = await page.evaluate(() => [...document.querySelectorAll('.l1-ribbon [data-l1]')].map((b) => b.dataset.l1));
    check('the rail lists Devices, 3D, Table, Activity, Runs, then Deploy, Teleop, Data', order.slice(0, 8).join() === 'tree,threed,table,activity,runs,deploy,teleop,data', order);
    for (const layer of LAYERS) {
      const rect = await rectOf(page, item(layer.id));
      const text = await page.$eval(item(layer.id), (el) => `${el.title} | ${el.textContent.trim()}`).catch(() => '');
      check(`${layer.label}: a rail item with an icon and the label`, Boolean(rect && rect.visible) && text.includes(layer.label) && Boolean(await page.$(`${item(layer.id)} svg`)), text);
    }

    // --------------------------------------------------------------------------------------------- each page ----
    for (const layer of LAYERS) {
      const tag = layer.label;
      await openLayer(page, layer.id);
      const host = await rectOf(page, '#layers-host');
      const tree = await rectOf(page, '#tree-host');
      check(`${tag}: the rail item opens its page in the main area`, host && host.visible && host.width > 600 && host.height > 400 && (await page.$(`.ly-root[data-layer="${layer.id}"]`)) !== null, host);
      check(`${tag}: the device tree stays on the left`, tree && tree.visible && tree.right <= host.x + 2 && (await rectOf(page, '#tree-host .t6-row')).visible, { tree: tree && tree.right, host: host && host.x });
      check(`${tag}: the entity page and the other views are not drawn`, !(await rectOf(page, '#inspector')).visible && !(await rectOf(page, '#threed-host')).visible && !(await rectOf(page, '#activity-host')).visible);
      check(`${tag}: the URL says view=${layer.id}, the rail marks it pressed`, (await viewOf(page)) === layer.id && (await page.$eval(item(layer.id), (el) => el.getAttribute('aria-pressed') === 'true')));
      // the selected device's own name (a line that can carry a number from its label) is not the page's text
      const body = await page.$eval('#layers-host', (el) => { const copy = el.cloneNode(true); copy.querySelectorAll('.ly-device').forEach((n) => n.remove()); return copy.textContent; });
      const heading = await page.$eval('#layers-host h1', (el) => el.textContent);
      check(`${tag}: a title and a one-line purpose`, layer.title.test(heading) && (await page.$$('#layers-host .ly-purpose')).length === 1, heading);
      check(`${tag}: what it will do, in plain words`, layer.text.every((re) => re.test(body)), layer.text.filter((re) => !re.test(body)).map(String));
      check(`${tag}: what it builds on (Device management, then Diagnostics) and a Today line`, body.indexOf('Device management') >= 0 && body.indexOf('Device management') < body.indexOf('Diagnostics')
        && /not built yet/i.test(await page.$eval('#layers-host .ly-today', (el) => el.textContent)), body.slice(0, 200));
      check(`${tag}: no numbers on the page (nothing to fake)`, !/\d/.test(body), (body.match(/.{0,20}\d.{0,20}/) || [''])[0]);
      check(`${tag}: no buttons, inputs or other controls, only links`, (await page.$$('#layers-host button, #layers-host input, #layers-host select, #layers-host textarea, #layers-host [role="button"], #layers-host [onclick]')).length === 0);

      // every visible link leads somewhere: click it, the page changes (measured), then come back for the next
      const links = await page.$$eval('#layers-host a', (as) => as.map((a) => ({ text: a.textContent.trim(), href: a.getAttribute('href'), visible: a.getBoundingClientRect().width > 0 })));
      const dead = [];
      for (let at = 0; at < links.length; at += 1) {
        if (!links[at].visible || !links[at].href || links[at].href === '#') { dead.push({ ...links[at], why: 'no target' }); continue; }
        await openLayer(page, layer.id);
        const before = await stateOf(page);
        await page.locator('#layers-host a').nth(at).click();
        await page.waitForTimeout(500);
        const after = await stateOf(page);
        const drawn = await page.evaluate(() => (S.view === 'tree' ? Boolean(document.querySelector('.d6-tabs')) : S.view === 'activity' ? Boolean(document.querySelector('#activity-host .act')) : true));
        if (after === before || !drawn) dead.push({ ...links[at], why: 'nothing happened', before, after, drawn });
      }
      check(`${tag}: every link on the page leads somewhere (measured by clicking, ${links.length} links)`, links.length >= 1 && dead.length === 0, dead);
    }

    // ---------------------------------------------------------------------------------------------- back / forward ----
    for (const layer of LAYERS) {
      await page.click(item('tree'));
      await waitView(page, 'tree');
      await page.click(item(layer.id));
      await waitView(page, layer.id);
      await page.goBack();
      await waitView(page, 'tree');
      check(`${layer.label}: Back returns to the view before it (the tree)`, (await viewOf(page)) === 'tree' && (await rectOf(page, '#inspector')).visible);
      await page.goForward();
      await waitView(page, layer.id);
      await page.waitForTimeout(150);
      check(`${layer.label}: Forward returns to the page`, (await rectOf(page, `.ly-root[data-layer="${layer.id}"]`)).visible);
    }
    await page.click(item('deploy')); await waitView(page, 'deploy');
    await page.click(item('data')); await waitView(page, 'data');
    await page.goBack(); await waitView(page, 'deploy');
    check('Deploy, then Data, then Back is Deploy again', (await rectOf(page, '.ly-root[data-layer="deploy"]')).visible);
    await start(page, base, '?view=teleop');
    await waitView(page, 'teleop');
    check('a reload of ?view=teleop opens Teleop', (await rectOf(page, '.ly-root[data-layer="teleop"]')).visible && await page.$eval(item('teleop'), (el) => el.getAttribute('aria-pressed') === 'true'));

    // -------------------------------------------------------------------------------------------------- palette ----
    await start(page, base);
    for (const layer of LAYERS) {
      await openPalette(page, layer.label);
      const rows = await page.$$eval('.lx-pal-i', (els) => els.map((el) => el.querySelector('.lab').textContent));
      check(`palette: "${layer.label}" is offered under Go to`, rows.includes(layer.label), rows);
      await page.keyboard.press('Enter');
      await waitView(page, layer.id);
      check(`palette: Enter on ${layer.label} opens its page`, (await rectOf(page, `.ly-root[data-layer="${layer.id}"]`)).visible && (await viewOf(page)) === layer.id);
      await page.goBack();
      await waitView(page, 'tree');
    }

    // ------------------------------------------------- the selection stays and the breadcrumb names the view ----
    await start(page, base);
    await page.click(item('deploy'));
    await waitView(page, 'deploy');
    check('breadcrumb: "Deploy" first', /^Deploy/.test(await page.$eval('.l1-crumbs', (el) => el.textContent.trim())));
    const key = await page.evaluate((p) => S.built.byEntity.get(p), BOARD);
    await page.click(`.t6-row[data-t6-path="${key}"] .t6-name`);
    await page.waitForFunction((p) => S.sel === p, BOARD);
    await page.waitForTimeout(250);
    const crumb = await page.$eval('.l1-crumbs', (el) => el.textContent.trim());
    check('a tree click keeps the view on Deploy and the breadcrumb reads Deploy › the selection', (await viewOf(page)) === 'deploy' && /^Deploy\s*›/.test(crumb) && /moteus|CAN 1/.test(crumb), crumb);
    const up = await page.$$eval('.l1-crumbs a[data-go]', (as) => as.map((a) => a.dataset.go));
    check('the breadcrumb has a parent crumb to click', up.length >= 1, up);
    if (up.length) {
      await page.click('.l1-crumbs a[data-go]');
      await page.waitForTimeout(300);
      check('a parent crumb selects that row and the view stays Deploy', (await viewOf(page)) === 'deploy' && (await page.evaluate(() => S.sel)) === up[0], up);
    }
    await start(page, base, `?view=deploy&sel=${encodeURIComponent(BOARD)}`);
    check('a reload of ?view=deploy&sel=… keeps both', (await viewOf(page)) === 'deploy' && (await page.evaluate(() => S.sel)) === BOARD && /^Deploy/.test(await page.$eval('.l1-crumbs', (el) => el.textContent.trim())));

    // ----------------------------------------------------- Deploy's link into the entity page's Utilities tab ----
    const util = page.locator('#layers-host a[data-ly-go="utilities"]').first();
    check('Deploy has a Utilities link naming Update firmware', (await util.count()) === 1 && /Utilities\s*›\s*Update firmware/.test(await util.textContent()));
    await util.click();
    await page.waitForFunction(() => S.view === 'tree' && S.drill.page === 'utilities', null, { timeout: 8000 });
    await page.waitForSelector('.d6-panel[data-d6-panel="utilities"]', { timeout: 8000 });
    check('the link opens the selected board\'s Utilities tab (entity page, the board selected)', (await H.tabOf(page)) === 'utilities' && (await page.evaluate(() => S.sel)) === BOARD
      && (await page.evaluate(() => S.node && S.node.node.path)) === BOARD, await H.tabOf(page));
    check('... the URL says so (sel, page=utilities)', await page.evaluate((p) => { const q = new URLSearchParams(location.search); return q.get('sel') === p && q.get('page') === 'utilities' && (q.get('view') || 'tree') === 'tree'; }, BOARD));
    await page.goBack();
    await waitView(page, 'deploy');
    check('Back from the Utilities tab returns to Deploy with the board still selected', (await rectOf(page, '.ly-root[data-layer="deploy"]')).visible && (await page.evaluate(() => S.sel)) === BOARD);
    await page.goForward();
    await page.waitForSelector('.d6-panel[data-d6-panel="utilities"]', { timeout: 8000 });
    check('Forward returns to the Utilities tab', (await H.tabOf(page)) === 'utilities');

    // ------------------------------------------------------------------------------ light and dark, screenshots ----
    if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
    for (const scheme of ['light', 'dark']) {
      const themed = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: scheme });
      const shot = await themed.newPage();
      const themedErrors = H.watchErrors(shot, base);
      for (const layer of LAYERS) {
        await start(shot, base, `?view=${layer.id}&sel=${encodeURIComponent(BOARD)}`);
        await shot.waitForTimeout(500);
        const colours = await shot.evaluate(() => {
          const lum = (css) => { const m = css.match(/[\d.]+/g).map(Number); const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2]); };
          const root = document.querySelector('.ly-root');
          const bg = (() => { let el = root; while (el) { const c = getComputedStyle(el).backgroundColor; if (c && !/rgba\(0, 0, 0, 0\)|transparent/.test(c)) return c; el = el.parentElement; } return 'rgb(255,255,255)'; })();
          const contrast = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
          const of = (selector) => contrast(getComputedStyle(root.querySelector(selector)).color, bg);
          return { mode: document.documentElement.dataset.mode, head: of('h1'), purpose: of('.ly-purpose'), link: of('a'), today: of('.ly-today'), dark: lum(bg) < 0.3 };
        });
        check(`${layer.label} in ${scheme}: the page follows the theme and its text reads (contrast >= 4.5)`, colours.mode === scheme && colours.dark === (scheme === 'dark') && colours.head >= 4.5 && colours.purpose >= 4.5 && colours.link >= 4.5 && colours.today >= 4.5, colours);
        if (SHOTS) await shot.screenshot({ path: path.join(SHOTS, `${layer.id}-${scheme}.png`) });
      }
      check(`no page errors in ${scheme}`, themedErrors.errors.length === 0, themedErrors.errors.slice(0, 5));
      await themed.close();
    }
    check('no page errors', errors.errors.length === 0, errors.errors.slice(0, 5));
  } catch (error) {
    failed = true;
    console.error(error);
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary() || (failed ? 1 : 0));
})();
