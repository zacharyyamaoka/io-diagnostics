// The entity header's last-heard line tells what the host knows. After a host restart with the bus held (a rack session), a board has no fresh
// report, so node.age_s is null, while presence remembers when it was last heard: "never heard" there is false, beside a log full of the board.
// Seen on Preview, 2026-11-04, on CAN 11. The host's answers are patched to that shape, on a fixture host.
//
//   node tests/test_heard_restored.js     (a fixture host on a scratch port; no hardware, never :8820-8822)
'use strict';

const H = require('./entity_harness');
const { chromium } = require(H.PLAYWRIGHT);
const { check } = H;

function patchBoard(value, shape) {
  if (Array.isArray(value)) value.forEach((item) => patchBoard(item, shape));
  else if (value && typeof value === 'object') {
    if (value.path === H.BOARD && 'age_s' in value) Object.assign(value, shape);
    // WHY: the board's own answers carry the age too, and with the bus held they have nothing to say
    if (shape.age_s === null && value.heard) value.heard = { received: null, age_s: null };
    Object.values(value).forEach((item) => patchBoard(item, shape));
  }
  return value;
}

async function heardWith(page, base, shape) {
  const patch = async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: patchBoard(await response.json(), shape) });
  };
  await page.route(/\/api\/v1\/(state|node|live\/|entity)/, patch);
  await H.open(page, base, H.BOARD, 'diagnostics');
  await page.waitForTimeout(1300); // the 1 Hz ticker owns the text, so one tick has passed
  const text = await page.$eval('[data-d6-heard]', (element) => element.textContent);
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.route('**/api/v1/live', () => {});
  return text;
}

async function main() {
  const server = await H.fixtureHost();
  const browser = await chromium.launch({ executablePath: H.CHROME, headless: true });
  try {
    const page = await (await H.newContext(browser)).newPage();
    // WHY the feed held, unanswered: a page with a feed goes by its snapshot, so what is injected here reaches the page only on its polling fallback
    await page.route('**/api/v1/live', () => {});
    const silent = { age_s: null, last_received: null };

    const remembered = await heardWith(page, server.base, { ...silent, presence: { state: 'stale', age_s: 1022, restored: true, text: 'last heard 17 min ago (before this session)' } });
    check('restored: says when the host last heard it, never "never heard"', /^heard 17 min ago/.test(remembered) && !/never/.test(remembered), remembered);
    check('restored: says the answer is not fresh', /stale/.test(remembered), remembered);

    const unstamped = await heardWith(page, server.base, { ...silent, presence: { state: 'stale', age_s: null, restored: true, text: 'not heard yet this session' } });
    check('restored with no date: says what the host says', unstamped.startsWith('not heard yet this session'), unstamped);

    const unknown = await heardWith(page, server.base, { ...silent, presence: null });
    check('a board the host has no record of: never heard', unknown.startsWith('never heard'), unknown);

    const fresh = await heardWith(page, server.base, {});
    check('a fresh board is unchanged', /^heard /.test(fresh) && !/stale/.test(fresh), fresh);
  } finally {
    await browser.close();
    H.stopServer(server.child);
  }
  process.exit(H.summary());
}

main().catch((error) => { console.error(error); process.exit(1); });
