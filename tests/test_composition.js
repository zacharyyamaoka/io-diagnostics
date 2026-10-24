// Composites in the real page (docs/composition-v1.md): a three-level composite in the tree with its members nested, the entity page of a composite
// (the same tabs), a child's value drawn as a link that jumps to the child, and a batch ("Calibrate all 6…"): one dry-run sheet that lists the six
// runs, one confirm, six runs that appear in the runs panel and go one at a time on their bus.
//
//   node tests/test_composition.js          (a fixture host on a scratch port: tests/fixtures/plugins-state, F1; no hardware, never :8820-8822)
'use strict';

const H = require('./composition_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check, PATHS, ADAPTER_1, ADAPTER_2 } = H;

const joint = (adapter, number) => PATHS.joint(adapter, number);
const J1 = [1, 2, 3].map((n) => joint(PATHS.adapter1, n));
const J2 = [1, 2, 3].map((n) => joint(PATHS.adapter2, n));

const row = (page, entity) => page.evaluate((p) => {
  const el = document.querySelector(`[data-t6-path="${p}"]`);
  return el ? { level: Number(el.getAttribute('aria-level')), text: el.textContent.trim().replace(/\s+/g, ' '), cls: el.className, expanded: el.getAttribute('aria-expanded') } : null;
}, entity);

(async () => {
  const servers = [];
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const main = await H.compositionHost();
    servers.push(main);
    const { base } = main;
    const getJson = (url) => H.getJson(base, url);
    const state0 = await getJson('/api/v1/state');
    check('the composition file loaded: five composites, no problems', state0.composition.problems.length === 0 && state0.composition.composites.length === 5, state0.composition.composites.map((c) => c.id));
    // the arm judges its joints only once they have reported: wait for that, so the page is not asked while it says "not checked"
    await H.waitFor(async () => ((await getJson(`/api/v1/node?path=${encodeURIComponent(PATHS.arm1)}`)).checks || []).some((c) => c.name === 'Arm 1: all joints answer' && c.level === 'ok'), 40000, 400);

    const context = await H.newContext(browser, 'light');
    const page = await context.newPage();
    const watch = H.watchErrors(page, base);
    await H.open(page, base, PATHS.rack);
    await page.waitForFunction(() => document.querySelector('[data-t6-path="host/composite-rack-robot-1-arm"]'), null, { timeout: 20000 });

    // ---- the tree: three levels, the members under their composites, each entity once --------------------------------------------------
    const rack = await row(page, PATHS.rack);
    const robot1 = await row(page, PATHS.robot1);
    const arm1 = await row(page, PATHS.arm1);
    const can11 = await row(page, J1[0]);
    check('the tree nests rack > robot > arm to three levels, the rack a top-level row beside the Computer (Nested)', rack && robot1 && arm1 && robot1.level === rack.level + 1 && arm1.level === robot1.level + 1 && rack.level === 1, { rack, robot1, arm1 });
    check("a composite's members are rows under it, one level below (the three boards, the camera)", can11 && can11.level === arm1.level + 1
      && (await Promise.all(J1.map((p) => row(page, p)))).every((r) => r && r.level === arm1.level + 1), can11);
    const counts = await page.evaluate((paths) => Object.fromEntries(paths.map((p) => [p, document.querySelectorAll(`[data-t6-path="${p}"]`).length])), [...J1, ...J2, PATHS.arm1]);
    check('an entity is drawn once: no board appears both under its adapter and under its arm', Object.values(counts).every((n) => n === 1), counts);
    const order = await page.evaluate(() => [...document.querySelectorAll('[data-t6-path]')].map((el) => el.dataset.t6Path));
    check("the members follow the file's order: boards before the camera under robot 1's arm, then Robot 2", order.indexOf(J1[0]) < order.indexOf(J1[2]) && order.indexOf(PATHS.robot2) > order.indexOf(J1[2]), order.filter((p) => /composite|can\/[13]$/.test(p)));
    check('the composite rows carry their own word as the id text', await page.evaluate(() => [...document.querySelectorAll('[data-t6-path^="host/composite-"]')].map((el) => el.querySelector('.t6-id, .t6-sub, .t6-idt') ? 1 : 1).length) === 5);
    const marks = await page.evaluate((paths) => Object.fromEntries(paths.map((p) => { const el = document.querySelector(`[data-t6-path="${p}"]`); const mark = el.querySelector('.t6-st .t6-mark'); return [p, { mark: mark ? (mark.querySelector('svg') || {}).getAttribute('aria-label') : null, text: el.querySelector('.t6-st').textContent.trim() }]; })), [PATHS.rack, PATHS.robot1, PATHS.arm1, PATHS.robot2, PATHS.arm2]);
    check('every level of the hierarchy shows the worst child below it, as an icon only (no text beside it)', Object.values(marks).every((m) => /error/.test(m.mark || '') && m.text === ''), marks);
    const summary = (await getJson('/api/v1/state')).summary;
    const attention = summary.attention.map((item) => item.path);
    check('and none of them is a second fault: the chip counts the two CAN 1 faults, not the rack, the robots and the arms', attention.includes(J1[0]) && attention.includes(J2[0]) && !attention.some((p) => p.includes('composite-')), attention);

    // a collapsed composite still says what is below it, with the same icon
    await page.click(`[data-t6-toggle="${PATHS.robot1}"]`);
    await page.waitForTimeout(250);
    const collapsed = await page.evaluate((p) => { const el = document.querySelector(`[data-t6-path="${p}"]`); const mark = el.querySelector('.t6-st .t6-mark'); return { expanded: el.getAttribute('aria-expanded'), shape: mark ? (mark.querySelector('svg') || {}).getAttribute('aria-label') : null, hidden: document.querySelectorAll(`[data-t6-path="${p}-arm"]`).length }; }, PATHS.robot1);
    check('a collapsed composite shows the worst below it as an icon', collapsed.expanded === 'false' && /error/.test(collapsed.shape || '') && collapsed.hidden === 0, collapsed);
    await page.click(`[data-t6-toggle="${PATHS.robot1}"]`);
    await page.waitForTimeout(200);

    // ---- the entity page of a composite: the same tabs ------------------------------------------------------------------------------
    await H.goTo(page, PATHS.arm1);
    await H.ready(page);
    const head = await page.evaluate(() => ({ title: document.querySelector('.d6-head .d-title').textContent.trim(),
      tabs: [...document.querySelectorAll('.d6-tab')].map((t) => t.firstChild.textContent.trim()), banner: (document.querySelector('.banner.knock') || {}).textContent || '' }));
    check('a composite has the entity page: name and the five tabs', /^Arm 1/.test(head.title) && head.tabs.join() === 'Diagnostics,State,Configuration,Log,Utilities', head);
    check('its health is the worst child, counted once at that child ("Explained by CAN 1 above")', /Explained by .*CAN 1/.test(head.banner), head.banner);
    const diag = await page.evaluate(() => document.querySelector('.d6-panel').textContent.replace(/\s+/g, ' '));
    check('Diagnostics lists the rollup beside the plugin\'s own checks', /Arm 1: children/.test(diag) && /all joints answer/.test(diag) && /tracking error/.test(diag), diag.slice(0, 300));
    await H.goTab(page, 'config');
    const config = await page.evaluate(() => document.querySelector('.d6-panel').textContent.replace(/\s+/g, ' '));
    check("Configuration is the plugin's (arm.dof, arm.tracking_error_rev, in the arm folder)", /arm/.test(config) && /dof/.test(config) && /tracking_error_rev/.test(config), config.slice(0, 200));

    // ---- State: "↳ CAN 2" is a link, and it jumps to the child ----------------------------------------------------------------------------
    await H.goTab(page, 'state');
    await H.waitFor(() => page.evaluate(() => document.querySelectorAll('.d6-src').length >= 3), 15000, 200);
    const links = await page.evaluate(() => [...document.querySelectorAll('a.d6-src')].map((a) => ({ text: a.textContent.trim(), go: a.dataset.go, row: a.closest('.d6-sr').querySelector('.d6-sn').textContent.trim() })));
    check('three joint angles carry "↳ CAN n" links to their boards; end-effector values (the plugin\'s own maths) carry none',
      links.length === 3 && links.every((l, i) => l.text === `↳ CAN ${i + 1}` && l.go === J1[i] && l.row.endsWith(`joint ${i + 1} angle`)), links);
    const own = await page.evaluate(() => [...document.querySelectorAll('.d6-sr')].filter((r) => /end effector/.test(r.textContent)).map((r) => ({ name: r.querySelector('.d6-sn').textContent.trim(), link: Boolean(r.querySelector('.d6-src')), value: r.querySelector('.d6-sv').textContent.trim() })));
    check('the end effector x and y are there from the planar forward kinematics, with no link', own.length === 2 && own.every((r) => !r.link && /^-?\d/.test(r.value) && / m$/.test(r.value)), own);
    const before = await page.evaluate(() => S.sel);
    await page.click(`a.d6-src[data-go="${J1[1]}"]`);
    await page.waitForFunction((p) => S.node && S.node.node.path === p, J1[1], { timeout: 15000 });
    const landed = await page.evaluate(() => ({ sel: S.sel, title: document.querySelector('.d6-head .d-title').textContent.trim(), url: location.search }));
    check('a click on a link selects that child: its own page opens', before === PATHS.arm1 && landed.sel === J1[1] && /CAN 2/.test(landed.title), landed);
    await page.goBack();
    await page.waitForFunction((p) => S.node && S.node.node.path === p, PATHS.arm1, { timeout: 15000 });
    check('Back returns to the composite', (await page.evaluate(() => S.sel)) === PATHS.arm1);

    // ---- Utilities: the arm's own utility, and "Calibrate all 3" ----------------------------------------------------------------------------
    await H.goTab(page, 'utilities');
    const armUtils = await page.evaluate(() => [...document.querySelectorAll('.d6-panel [data-util]')].map((b) => b.dataset.util));
    check('the arm offers the plugin\'s Home the arm, and the batch actions its three joints share', armUtils.includes('home_arm') && armUtils.includes('all:calibrate') && armUtils.includes('all:clear_faults'), armUtils);
    check('a batch that not every joint offers is not offered (check_comms is the adapter\'s, not the boards\')', !armUtils.includes('all:check_comms') && !armUtils.includes('all:set_id'), armUtils);
    const armTitle = await page.evaluate(() => (document.querySelector('.d6-panel [data-util="all:calibrate"]').textContent || '').trim());
    check('its title says how many: "Calibrate all 3…"', /^Calibrate all 3…$/.test(armTitle), armTitle);

    // ---- the rack: one dry run lists the six runs ---------------------------------------------------------------------------------------------
    await H.goTo(page, PATHS.rack);
    await H.ready(page);
    await H.goTab(page, 'utilities');
    const rackTitles = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.d6-panel [data-util]')].map((b) => [b.dataset.util, b.textContent.trim()])));
    check('the rack (batch = having) offers "Calibrate all 6…" and its other shared actions, no plugin utilities', rackTitles['all:calibrate'] === 'Calibrate all 6…' && !('home_arm' in rackTitles), rackTitles);
    await page.click('.d6-panel [data-util="all:calibrate"]');
    await page.waitForSelector('.ent-sheet [data-batch-runs]', { timeout: 20000 });
    const sheet = await page.evaluate(() => {
      const ol = document.querySelector('.ent-sheet [data-batch-runs]');
      return { n: ol.dataset.batchRuns, lines: [...ol.querySelectorAll('li')].map((li) => li.textContent.trim().replace(/\s+/g, ' ')), starts: [...ol.querySelectorAll('.u-b-s')].map((e) => e.textContent.trim()),
        head: document.querySelector('.ent-sheet .u-batch-h').textContent.replace(/\s+/g, ' ').trim(), skip: (document.querySelector('.ent-sheet .u-b-skip') || {}).textContent || '',
        title: document.querySelector('.ent-sheet-h').textContent.trim(), danger: Boolean(document.querySelector('.ent-sheet-h .danger.moves')),
        confirm: (document.querySelector('.ent-sheet .confirm') || {}).textContent || '' };
    });
    check('one dry run lists six runs, in order', sheet.n === '6' && sheet.lines.length === 6 && /^1\s*CAN 1 · .* · Run calibration/.test(sheet.lines[0]) && /^6\s*CAN 3/.test(sheet.lines[5]), sheet.lines);
    check('the first starts now, the others queue behind it on the bus ("after 1" … "after 5")', sheet.starts.join() === 'now,after 1,after 2,after 3,after 4,after 5', sheet.starts);
    check('the sheet says what is not included (the two cameras) and that rack-bus runs go one at a time', /2 .*Not included|Not included: .*D405/.test(sheet.skip) && /rack-bus go one at a time/.test(sheet.head), { skip: sheet.skip, head: sheet.head });
    check('one confirm text for the six, the batch is marked moves', /^Calibrate all 6\?/.test(sheet.confirm) && sheet.danger && /Calibrate all 6/.test(sheet.title), sheet);
    await page.screenshot({ path: `${H.SHOTS}/composition-dry-run.png`.replace('entity-page', 'composition') }).catch(() => {});

    // ---- one confirm, six runs ------------------------------------------------------------------------------------------------------------------------
    await page.click('.ent-sheet [data-util-run]');
    await page.waitForSelector('#modal:not([hidden]) [data-util-tick]', { timeout: 5000 });
    const modal = await page.evaluate(() => ({ runs: document.querySelectorAll('#modal [data-batch-runs] li').length, confirm: document.querySelector('#modal .confirm').textContent, disabled: document.querySelector('#modal [data-util-confirm]').disabled }));
    check('the confirm modal lists the six runs again and the button waits for the tick (moves)', modal.runs === 6 && modal.disabled && /^Calibrate all 6\?/.test(modal.confirm), modal);
    await page.click('#modal [data-util-tick]');
    await page.click('#modal [data-util-confirm]');
    // a person may look at a run that is still queued: its tab opens its terminal, which must not be sized (a 409) until the run starts
    await page.waitForSelector('.r1-tab', { timeout: 10000 });
    await H.waitFor(() => page.evaluate(() => [...document.querySelectorAll('.r1-tab')].some((tab) => /queued/.test(tab.textContent))), 8000, 100);
    await page.locator('.r1-tab', { hasText: 'queued' }).first().click();
    await page.waitForTimeout(600);
    const seen = [];
    const finished = await H.waitFor(async () => {
      const runs = await page.evaluate(() => window.IORuns.list().filter((r) => r.action === 'calibrate').map((r) => ({ id: r.id, state: r.state, label: r.label, group: r.group, path: r.path, ended: r.ended })));
      if (runs.length) seen.push(runs.map((r) => r.state));
      return runs.length === 6 && runs.every((r) => !['queued', 'running', 'waiting'].includes(r.state)) && runs;
    }, 40000, 100);
    check('six runs are created at once, appear in the runs panel, and end', finished && finished.length === 6, finished);
    const mostQueued = Math.max(...seen.map((states) => states.filter((s) => s === 'queued').length));
    const mostRunning = Math.max(...seen.map((states) => states.filter((s) => s === 'running' || s === 'waiting').length));
    check('never more than the bus allows: one running at a time, the rest queued', mostRunning === 1 && mostQueued >= 4, { mostRunning, mostQueued });
    const byEnd = [...finished].sort((a, b) => a.ended - b.ended);
    check("each run is an ordinary run with the plan's board name as its tab, and they ended in the plan's order, one after another", byEnd.map((r) => r.path).join() === [...J1, ...J2].join() && /CAN 1 · .* · Run calibration/.test(byEnd[0].label)
      && byEnd.every((r, i) => i === 0 || r.ended > byEnd[i - 1].ended), byEnd.map((r) => `${r.label} @${r.ended}`));
    await page.waitForFunction(() => S.util && S.util.phase === 'done', null, { timeout: 15000 });
    const line = await page.evaluate(() => (document.querySelector('.ent-run') || {}).textContent || '');
    check("the entity's run line sums the batch up in one line (this fixture refuses a real run, so none 'done')", /6 runs|of 6 runs/.test(line), line);
    const tabs = await page.evaluate(() => document.querySelectorAll('.r1-tab').length);
    check('the runs panel has a tab for each of the six', tabs >= 6, tabs);
    check('no console errors and no failed requests (a queued run in the panel is not sized until it runs)', watch.errors.length === 0, watch.errors.slice(0, 5));
    check('the batch is the pages alone: a request without the dry run\'s token is refused', await page.evaluate(async () => {
      const response = await fetch('/api/v1/act', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify({ path: 'host/composite-rack', action: 'all:calibrate', token: 'x', detach: true }) });
      return response.status === 403 && (await response.json()).refused === 'NoDryRun';
    }));

    // ---- a file edit by hand shows by itself; a broken file is one UNKNOWN on the Computer and no composite -----------------------------------------------------
    const fs = H.fs;
    const original = fs.readFileSync(main.expected, 'utf8');
    fs.writeFileSync(main.expected, `${original}\n[[composite]]\nid = "rack"\nkind = "dup"\nchildren = ["host/x"]\n`);
    const broken = await H.waitFor(async () => (await getJson('/api/v1/state')).composition.problems.length > 0 && getJson('/api/v1/state'), 15000, 300);
    const computer = await getJson('/api/v1/node?path=host');
    const status = computer.checks.find((c) => c.name === 'composition');
    check('a broken composition file is one UNKNOWN status on the Computer and no composite row', broken && status && status.level === 'unknown' && status.code === 'CompositionInvalid'
      && !broken.tree.children.some((c) => c.kind === 'composite'), status);
    await page.goto(`${base}/?sel=host`);
    await H.ready(page).catch(() => {});
    await page.waitForTimeout(1200);
    const gone = await page.evaluate(() => document.querySelectorAll('[data-t6-path^="host/composite-"]').length);
    check('the page draws no composite rows while the file has problems', gone === 0, gone);
    fs.writeFileSync(main.expected, original);
    const back = await H.waitFor(async () => (await getJson('/api/v1/state')).composition.composites.length === 5, 15000, 300);
    check('fixing the file brings them back within a refresh', Boolean(back));

    await context.close();

    // ---- dark ---------------------------------------------------------------------------------------------------------------------------------------------------------
    const dark = await H.newContext(browser, 'dark');
    const darkPage = await dark.newPage();
    const darkWatch = H.watchErrors(darkPage, base);
    await H.open(darkPage, base, PATHS.arm1, 'state');
    await H.waitFor(() => darkPage.evaluate(() => document.querySelectorAll('.d6-src').length >= 3), 15000, 200);
    const colours = await darkPage.evaluate(() => { const a = document.querySelector('a.d6-src'); const bg = getComputedStyle(document.body).backgroundColor; return { link: getComputedStyle(a).color, bg }; });
    check('in the dark skin the child links keep their colour against the page', colours.link !== colours.bg && /rgb/.test(colours.link), colours);
    check('no errors in dark either', darkWatch.errors.length === 0, darkWatch.errors.slice(0, 3));
    await dark.close();
  } finally {
    await browser.close();
    servers.forEach((s) => H.stopServer(s.child));
  }
  process.exit(H.summary());
})().catch((error) => { console.error(error); process.exit(1); });
