// Headless proof that Lichtblick's STOCK Diagnostics panels show our records (run by lichtblick.py --shoot).
//
//   node lichtblick_shoot.mjs <lichtblick url with ds params> <out dir> <playwright index.mjs>
//
// Headless Chrome only (never a window on Zach's screen). Writes to <out dir>:
//   lichtblick-summary-detail.png   Summary (sorted by level) | Detail on CAN 15's fault 33
//   lichtblick-stale-detail.png     after clicking the stale board (CAN 14) in the Summary
//   lichtblick-topics.png           the data source's topic list: /diagnostics, diagnostic_msgs/msg/DiagnosticArray
//   lichtblick-hero.webm            the whole run as a video
//   lichtblick-rows.json            every Summary row as rendered: name, message, the colour Lichtblick gave it
//   lichtblick-console.json         browser console errors and page errors
import fs from "node:fs";
import path from "node:path";

const [url, outDir, playwrightPath] = process.argv.slice(2);
const { chromium } = await import(playwrightPath);
fs.mkdirSync(outDir, { recursive: true });
const videoDir = path.join(outDir, "video-tmp");

const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1600, height: 900 },
  colorScheme: "light",
  recordVideo: { dir: videoDir, size: { width: 1600, height: 900 } },
});
const page = await context.newPage();
const problems = [];
page.on("console", (message) => {
  if (message.type() === "error") problems.push({ kind: "console", text: message.text() });
});
page.on("pageerror", (error) => problems.push({ kind: "pageerror", text: String(error) }));

const rows = async () =>
  page.$$eval('[data-testid="diagnostic-row"]', (items) =>
    items.map((item) => {
      const [primary, secondary] = item.querySelectorAll(".MuiListItemText-primary, .MuiListItemText-secondary");
      return {
        name: primary?.textContent ?? "",
        message: secondary?.textContent ?? "",
        color: secondary ? getComputedStyle(secondary).color : "",
      };
    }),
  );

let status = 0;
try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.getByText("CAN 15: driver").first().waitFor({ timeout: 60_000 });
  await page.getByText("no update for", { exact: false }).first().waitFor({ timeout: 20_000 });
  await page.waitForTimeout(2500); // let the 1 Hz stream refresh the panels at least twice
  await page.screenshot({ path: path.join(outDir, "lichtblick-summary-detail.png") });
  const summary = await rows();

  await page.locator('[data-testid="diagnostic-row"]', { hasText: "CAN 14: comms" }).first().click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(outDir, "lichtblick-stale-detail.png") });

  // the left sidebar's Topics tab lists what the data source advertised
  const topicsTab = page.getByRole("tab", { name: /topics/i }).first();
  if (await topicsTab.count()) {
    await topicsTab.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(outDir, "lichtblick-topics.png") });
  }
  fs.writeFileSync(path.join(outDir, "lichtblick-rows.json"), JSON.stringify(summary, null, 1) + "\n");
  console.log(`lichtblick_shoot: ${summary.length} summary rows`);
  for (const row of summary) console.log(`  ${row.color}  ${row.name}  |  ${row.message}`);
} catch (error) {
  status = 1;
  console.error(`lichtblick_shoot: ${error}`);
  await page.screenshot({ path: path.join(outDir, "lichtblick-failure.png") }).catch(() => {});
} finally {
  fs.writeFileSync(path.join(outDir, "lichtblick-console.json"), JSON.stringify(problems, null, 1) + "\n");
  const video = page.video();
  await context.close();
  await browser.close();
  if (video) {
    fs.renameSync(await video.path(), path.join(outDir, "lichtblick-hero.webm"));
    fs.rmSync(videoDir, { recursive: true, force: true });
  }
}
process.exit(status);
