// Headless screenshots of the RobotDiagnostics logo gallery (round 3, 2026-10-23), light and dark.
// Run: node /home/bam/io-diagnostics/brand/render_logos_gallery.mjs
import { chromium } from '/home/bam/aruco-studio/web/node_modules/playwright/index.mjs';

const page_url = 'file:///home/bam/io-diagnostics/reports/media/robotdiagnostics-logos-2026-10-23.html';
const out = '/home/bam/io-diagnostics/reports/media/round3';
const cards = process.argv.slice(2);

const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true });
for (const scheme of ['light', 'dark']) {
  const context = await browser.newContext({ viewport: { width: 1240, height: 900 }, colorScheme: scheme, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const failed = [];
  page.on('requestfailed', (request) => failed.push(request.url()));
  page.on('request', (request) => { if (!request.url().startsWith('file:') && !request.url().startsWith('data:')) failed.push('external: ' + request.url()); });
  await page.goto(page_url);
  await page.screenshot({ path: `${out}/logos-gallery-${scheme}.png`, fullPage: true });
  await page.locator('.top5').screenshot({ path: `${out}/logos-top5-${scheme}.png` });
  await page.locator('.overview').screenshot({ path: `${out}/logos-overview-${scheme}.png` });
  for (const num of cards.length ? cards : Array.from({ length: 20 }, (_, i) => String(i + 1).padStart(2, '0'))) {
    await page.locator(`#logo-${num}`).screenshot({ path: `${out}/logos-card-${num}-${scheme}.png` });
  }
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  console.log(scheme, 'page height', height, 'horizontal overflow', overflow, 'failed/external requests', failed.length ? failed : 'none');
  await context.close();
}
// phone width: no horizontal scroll
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
const phone_page = await phone.newPage();
await phone_page.goto(page_url);
console.log('phone horizontal overflow', await phone_page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth));
await phone_page.screenshot({ path: `${out}/logos-phone-light.png` });
await browser.close();
