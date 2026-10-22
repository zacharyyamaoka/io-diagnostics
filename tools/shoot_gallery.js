// Headless Chrome walk of the babble gallery: every hero through its 5 states (stepper AND direct
// controls), sync assertions, console errors, and screenshots to look at.
//   node /home/bam/io-diagnostics/tools/shoot_gallery.js <gallery.html> <out-dir>
const path = require('path');
const fs = require('fs');
const { chromium } = require('/home/bam/claude-hub/node_modules/playwright-core');

async function main() {
  const [file, outDir] = process.argv.slice(2);
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1500 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', (err) => errors.push(String(err)));
  await page.goto('file://' + path.resolve(file));
  await page.waitForSelector('.variant-card');
  await page.addStyleTag({ content: '#decision-dock{display:none!important}' });  // screenshots only: the fixed dock covers the frames
  await page.screenshot({ path: path.join(outDir, '00-top.png') });
  await page.evaluate(() => document.getElementById('variant-grid').scrollIntoView());
  await page.screenshot({ path: path.join(outDir, '01-grid.png') });

  const report = { layout: await page.evaluate(() => document.getElementById('variant-grid').className), variants: {} };
  for (const id of ['v1', 'v2', 'v3', 'v4', 'v5']) {
    const card = `#card-${id}`;
    await page.click('[data-layout="grid"]');  // Focus hides the other cards; return to the Grid first
    await page.waitForTimeout(200);
    await page.click(`${card} [data-grid-focus-target]`);
    await page.waitForTimeout(300);
    const result = { steps: [], direct: [], labelMatchesId: false };
    result.labelMatchesId = await page.$eval(card, (el) => el.querySelector('.variant-index').textContent.trim().toLowerCase() === el.dataset.variant);
    const count = await page.$$eval(`${card} [data-story-index]`, (els) => els.length);
    for (let i = 0; i < count; i++) {
      await page.click(`${card} [data-story-index="${i}"]`);
      await page.waitForTimeout(150);
      const state = await page.$eval(`${card} .prototype`, (el) => el.dataset.storyState);
      const visible = await page.$eval(`${card} .prototype`, (el) => {
        const scene = [...el.querySelectorAll('.scene')].find((s) => getComputedStyle(s).display !== 'none');
        return scene ? scene.className : 'none';
      });
      const target = await page.$eval(`${card} .prototype`, (el) => {
        const t = el.querySelector('.story-target');
        if (!t) return 'none';
        const r = t.getBoundingClientRect();
        return r.width > 0 && r.height > 0 ? 'visible' : 'hidden';
      });
      result.steps.push({ i, state, visible, target });
      const frame = await page.$(`${card} .prototype-frame`);
      await frame.screenshot({ path: path.join(outDir, `${id}-${i + 1}-${state}.png`) });
    }
    // Direct manipulation: from step 1, click the visible control that leads on, and check the stepper followed.
    await page.click(`${card} [data-story-index="0"]`);
    for (const want of ['buses', 'device']) {
      const clicked = await page.evaluate(([sel, want]) => {
        const proto = document.querySelector(sel + ' .prototype');
        const scene = [...proto.querySelectorAll('.scene')].find((s) => getComputedStyle(s).display !== 'none');
        const control = scene && scene.querySelector(`[data-story-to="${want}"]`);
        if (!control) return 'no control';
        control.click();
        return proto.dataset.storyState + ' / step ' + document.querySelector(sel).dataset.storyIndex;
      }, [card, want]);
      result.direct.push({ want, got: clicked });
    }
    await page.click(`${card} [data-story-action="back"]`);
    result.backTo = await page.$eval(`${card} .prototype`, (el) => el.dataset.storyState);
    const chip = await page.evaluate((sel) => {
      const proto = document.querySelector(sel + ' .prototype');
      proto.querySelector('.chip.c3').click();
      return proto.dataset.storyState;
    }, card);
    result.chipNow = chip;
    report.variants[id] = result;
  }
  await page.click('[data-layout="grid"]');
  await page.click('#card-v2 [data-grid-focus-target]');
  await page.click('#card-v2 [data-story-index="1"]');
  await page.waitForTimeout(300);
  await (await page.$('#card-v2')).screenshot({ path: path.join(outDir, '80-focus-v2-card.png') });
  await page.click('[data-layout="grid"]').catch(() => {});
  await page.waitForTimeout(300);
  await (await page.$('#ai-prune')).screenshot({ path: path.join(outDir, '90-prune.png') });
  await (await page.$('#score-matrix')).screenshot({ path: path.join(outDir, '91-matrix.png') });
  const hero = await page.$('.hero');
  if (hero) await hero.screenshot({ path: path.join(outDir, '02-hero.png') });
  report.errors = errors;
  report.overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 1));
  console.log(JSON.stringify(report, null, 1));
  await browser.close();
}

main().catch((err) => { console.error(err); process.exit(1); });
