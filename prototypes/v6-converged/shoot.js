// Headless Chrome proof shots of the running V6 dashboard (never a visible window).
//   node /home/bam/io-diagnostics/prototypes/v6-converged/shoot.js http://127.0.0.1:8812 /home/bam/io-diagnostics/reports/media/converged
const path = require('path');
const fs = require('fs');
const { chromium } = require('/home/bam/claude-hub/node_modules/playwright-core');

const CAN15 = 'host/usb-1-3/fdcanusb-272813EF/can/15';
const ADAPTER = 'host/usb-1-3/fdcanusb-272813EF';
const SHOTS = [
  { name: 'hero', query: { frame: 'F1', view: 'tree', sel: CAN15 } },
  { name: 'table', query: { frame: 'F2', t: '355', view: 'table', sel: ADAPTER } },
  { name: 'activity', query: { frame: 'F1', view: 'activity' } },
  { name: 'action', query: { frame: 'F2', t: '20', view: 'tree', sel: CAN15, dialog: 'reset' } },
  { name: 'chip', query: { frame: 'F1', view: 'tree', sel: ADAPTER, chip: '1' } },
];

async function main() {
  const [base, outDir] = process.argv.slice(2);
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', (err) => errors.push(String(err)));
  const report = {};
  for (const shot of SHOTS) {
    await page.goto(`${base}/?${new URLSearchParams(shot.query)}`);
    await page.waitForSelector('.row, table.grid, .act');
    await page.waitForTimeout(5200);  // two polls + the activity fetch, so strips and history are in
    const facts = await page.evaluate(() => ({
      chip: document.getElementById('chip-label').textContent,
      clock: document.getElementById('clock').textContent,
      selected: (document.querySelector('.d-title span') || {}).textContent,
      stale: document.querySelectorAll('.pill.stale').length,
      rows: document.querySelectorAll('#left .row').length,
      modal: !document.getElementById('modal').hidden,
      blocked: (document.querySelector('.modal .banner.blocked') || document.querySelector('.detail .banner.blocked') || {}).textContent || '',
      overflowX: document.documentElement.scrollWidth > window.innerWidth,
    }));
    report[shot.name] = facts;
    await page.screenshot({ path: path.join(outDir, `${shot.name}.png`) });
  }
  report.errors = errors;
  console.log(JSON.stringify(report, null, 1));
  await browser.close();
}

main().catch((err) => { console.error(err); process.exit(1); });
