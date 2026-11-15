#!/usr/bin/env node
// The hero clip and stills of Reset… (docs/device-reset.md) in the REAL page on a fixture host, headless Chrome. No hardware:
// `cli.py serve --fixture F1 --port 0 --no-udev` with the reset ladder pointed at a FAKE sysfs / dev / proc, a FAKE
// fdcanusb-reset and usbreset under a temp dir, and the fdcanusb's console played by a pseudo-terminal that stays silent (a
// wedged adapter) until the fake usbreset runs. It refuses to press anything unless only the fixture plugin is loaded.
//
//   /usr/bin/node /home/bam/io-diagnostics/docs/record_device_reset_hero.js
//
// Writes reports/media/device-reset/{hero.webm,hero.mp4,hero.gif,run-page-light.png,run-page-dark.png,run-done.png,captions.json}.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const PLAYWRIGHT = process.env.IO_DIAG_PLAYWRIGHT || '/home/bam/aruco-studio/web/node_modules/playwright';
const CHROME = '/usr/bin/google-chrome';
const UV = '/home/bam/.local/bin/uv';
const OUT = path.join(ROOT, 'reports', 'media', 'device-reset');
const VIEWPORT = { width: 1440, height: 900 };
const ADAPTER = 'host/usb1/port-1-3/usb-1-3'; // the fixture's fdcanusb 272813EF in port 1-3

// ----- the fake machine ---------------------------------------------------------------------------------------------------
const FAKE_FDCANUSB_RESET = `#!/usr/bin/env python3
import os, sys, time
root = os.environ["FAKE_USB_ROOT"]
open(os.path.join(root, "calls.log"), "a").write("fdcanusb-reset " + " ".join(sys.argv[1:]) + "\\n")
time.sleep(1.2)
print("reset sent to 272813EF (/dev/bus/usb/001/002); back after 1.6 s", flush=True)
`;
const FAKE_USBRESET = `#!/usr/bin/env python3
import os, sys, time
root = os.environ["FAKE_USB_ROOT"]
open(os.path.join(root, "calls.log"), "a").write("usbreset " + " ".join(sys.argv[1:]) + "\\n")
time.sleep(0.6)
open(os.path.join(root, "cured"), "w").write("1")
print("Resetting fdcanusb ... ok", flush=True)
`;
// the adapter's console: a pty whose far end answers `can status` only once the fake usbreset has "cured" it
const CONSOLE = `
import os, pty, select, sys, time
root = sys.argv[1]
master, slave = pty.openpty()
link = os.path.join(root, "dev", "ttyACM0")
if os.path.lexists(link):
    os.unlink(link)
os.symlink(os.ttyname(slave), link)
open(os.path.join(root, "console.ready"), "w").write(os.ttyname(slave))
while True:
    readable, _, _ = select.select([master], [], [], 0.5)
    if not readable:
        continue
    data = os.read(master, 1024)
    if b"can status" in data and os.path.exists(os.path.join(root, "cured")):
        os.write(master, b"lec=0 dlec=0 err=0 warn=0 busoff=0 pexc=0 tdc=13\\r\\nOK\\r\\n")
`;

function write(file, text, mode) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text); if (mode) fs.chmodSync(file, mode); }

function fakeMachine() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'device-reset-hero-'));
  const sys = path.join(root, 'sys', '1-3');
  for (const [file, value] of Object.entries({ busnum: 1, devnum: 2, idVendor: '0483', idProduct: '5740', product: 'fdcanusb',
    manufacturer: 'mjbots', serial: '272813EF', authorized: '1', bConfigurationValue: '1', speed: '12', bDeviceClass: '02' })) {
    write(path.join(sys, file), `${value}\n`);
  }
  fs.mkdirSync(path.join(sys, '1-3:1.0', 'tty', 'ttyACM0'), { recursive: true });
  fs.mkdirSync(path.join(root, 'drivers', 'cdc_acm'), { recursive: true });
  fs.symlinkSync(path.join(root, 'drivers', 'cdc_acm'), path.join(sys, '1-3:1.0', 'driver'));
  // the hub side (root hub usb1, port 3): its disable switch is root only, and the hub's power switching is unknown here
  write(path.join(root, 'sys', '1-0:1.0', 'usb1-port3', 'disable'), '0\n', 0o444);
  write(path.join(root, 'sys', 'usb1', 'idVendor'), '1d6b\n');
  write(path.join(root, 'sys', 'usb1', 'idProduct'), '0002\n');
  fs.chmodSync(path.join(sys, 'authorized'), 0o444); // root only, as on this machine before the installer
  write(path.join(root, 'dev', 'bus', 'usb', '001', '002'), '', 0o664);
  write(path.join(root, 'dev', 'serial', 'by-id', 'usb-mjbots_fdcanusb_272813EF-if00'), '');
  fs.mkdirSync(path.join(root, 'proc'), { recursive: true });
  write(path.join(root, 'proc', 'locks'), '');
  write(path.join(root, 'bin', 'fdcanusb-reset'), FAKE_FDCANUSB_RESET, 0o755);
  write(path.join(root, 'bin', 'usbreset'), FAKE_USBRESET, 0o755);
  write(path.join(root, 'calls.log'), '');
  write(path.join(root, 'hub-power.txt'), 'usb1 1d6b:0002 unknown\n');
  return root;
}

function startServer(root, stateDir) {
  const env = { ...process.env, IO_DIAGNOSTICS_STATE_DIR: stateDir, IO_DIAGNOSTICS_CONFIG_DIR: path.join(root, 'config'), FAKE_USB_ROOT: root,
    IO_DIAGNOSTICS_USB_SYSFS: path.join(root, 'sys'), IO_DIAGNOSTICS_DEV_ROOT: path.join(root, 'dev'), IO_DIAGNOSTICS_PROC: path.join(root, 'proc'),
    IO_DIAGNOSTICS_USBRESET: path.join(root, 'bin', 'usbreset'), IO_DIAGNOSTICS_UHUBCTL: path.join(root, 'no-uhubctl'),
    IO_DIAGNOSTICS_FDCANUSB_RESET: path.join(root, 'bin', 'fdcanusb-reset'), IO_DIAGNOSTICS_RACK_BUS_LOCK: path.join(root, 'bus.lock'),
    IO_DIAGNOSTICS_HUB_POWER_FACTS: path.join(root, 'hub-power.txt'), IO_DIAGNOSTICS_KERNEL_LOG_FILE: path.join(root, 'kernel.log') };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--fixture', 'F1', '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stop(child); reject(new Error(`server did not start: ${output}`)); }, 60000);
    const onData = (chunk) => {
      output += chunk;
      const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//);
      if (found) { clearTimeout(timer); resolve({ child, base: found[1] }); }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`server exited (${code}): ${output}`)); });
  });
}
const stop = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };

async function open(page, base, sel) {
  await page.goto(`${base}/?sel=${encodeURIComponent(sel)}`);
  await page.waitForFunction((p) => typeof S !== 'undefined' && S.built && S.node && S.node.node.path === p && document.querySelector('.d-title'), sel, { timeout: 30000 });
  await page.waitForTimeout(500);
}
async function runPage(page) {
  await page.waitForSelector('#ent-util-btn:not([disabled])', { timeout: 15000 });
  if (!(await page.$('.ent-menu'))) await page.click('#ent-util-btn');
  await page.waitForSelector('.ent-menu [data-util="reset"]', { timeout: 8000 });
  await page.waitForTimeout(700);
  await page.click('.ent-menu [data-util="reset"]');
  await page.waitForSelector('#modal:not([hidden]) [data-run-page="reset"] [data-ladder]', { timeout: 20000 });
  await page.waitForFunction(() => S.util && S.util.dry, null, { timeout: 20000 });
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const root = fakeMachine();
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'device-reset-hero-state-'));
  const console_ = spawn('/usr/bin/python3', ['-c', CONSOLE, root], { stdio: 'ignore' });
  for (let i = 0; i < 50 && !fs.existsSync(path.join(root, 'console.ready')); i += 1) await new Promise((r) => setTimeout(r, 100));
  const { chromium } = require(PLAYWRIGHT);
  const browser = require('../tests/pin_layout').pinDetailLayout(await chromium.launch({ executablePath: CHROME, headless: true }), 'd6');
  const server = await startServer(root, stateDir);
  const calls = () => fs.readFileSync(path.join(root, 'calls.log'), 'utf8').split('\n').filter(Boolean);
  let status = 1;
  try {
    const plugins = await (await fetch(`${server.base}/api/v1/plugins`)).json();
    const names = plugins.plugins.map((plugin) => plugin.name).join();
    if (names !== 'v6_scenario') throw new Error(`refusing to press anything: loaded ${names}`);

    // the stills, dark first (nothing runs)
    const dark = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'dark' });
    const darkPage = await dark.newPage();
    await open(darkPage, server.base, ADAPTER);
    await runPage(darkPage);
    await darkPage.screenshot({ path: path.join(OUT, 'run-page-dark.png') });
    await dark.close();

    // the clip: open the adapter, Utilities, Reset…, read the ladder, Run, the run's result
    const context = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light', recordVideo: { dir: OUT, size: VIEWPORT } });
    const page = await context.newPage();
    await open(page, server.base, ADAPTER);
    await page.waitForTimeout(900);
    await runPage(page);
    await page.waitForTimeout(1800);
    await page.screenshot({ path: path.join(OUT, 'run-page-light.png') });
    await page.evaluate(() => { const ladder = document.querySelector('#modal [data-ladder]'); if (ladder) ladder.scrollIntoView({ block: 'center', behavior: 'smooth' }); });
    await page.waitForTimeout(1800);
    if (calls().length) throw new Error(`something ran before Run: ${calls()}`);
    await page.click('#modal [data-util-confirm]');
    await page.waitForSelector('.ent-run.ok, .ent-run.fail', { timeout: 60000 });
    await page.waitForTimeout(2500);
    const run = await page.evaluate(() => ({ line: (document.querySelector('.ent-run') || {}).textContent || '', ok: !!document.querySelector('.ent-run.ok') }));
    await page.screenshot({ path: path.join(OUT, 'run-done.png') });
    const video = page.video();
    await context.close();
    const webm = path.join(OUT, 'hero.webm');
    fs.renameSync(await video.path(), webm);
    execFileSync('/usr/bin/ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-vf', 'scale=1200:-2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '30', '-movflags', '+faststart', path.join(OUT, 'hero.mp4')]);
    execFileSync('/usr/bin/ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-vf', 'fps=6,scale=900:-2:flags=lanczos', path.join(OUT, 'hero.gif')]);
    fs.writeFileSync(path.join(OUT, 'captions.json'), JSON.stringify({
      'hero.mp4': 'The real page on a fixture host, with the fdcanusb played by a fake machine and a wedged console. Utilities → Reset… shows the '
        + 'ladder for this adapter. Run tries its own reset (still silent), then a USB port reset, and stops as soon as the adapter answers. '
        + `The run line reads: “${run.line.trim()}”.`,
      'run-page-light.png': 'Reset…’s Run page: what it tries, in order, until it answers, and why each skipped rung is skipped (here the kernel deauthorize and the power cycle wait for the installer)',
      'run-page-dark.png': 'The same Run page, dark',
      'run-done.png': 'After Run: the run’s result line',
    }, null, 1));
    console.log(JSON.stringify({ calls: calls(), run }, null, 1));
    status = run.ok && calls().length === 2 && /^fdcanusb-reset /.test(calls()[0]) && /^usbreset /.test(calls()[1]) ? 0 : 1;
  } finally {
    stop(server.child);
    try { console_.kill('SIGTERM'); } catch (_) { /* gone */ }
    await browser.close();
  }
  process.exit(status);
}

main().catch((error) => { console.error(error); process.exit(1); });
