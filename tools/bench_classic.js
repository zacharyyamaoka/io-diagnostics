// The actual classic page, real fixture host and real Chrome. No hardware or fixed ports.
// node tools/bench_classic.js --seconds 60 --label baseline
'use strict';

const { fs, os, path, ROOT, PLAYWRIGHT, CHROME, BOARD, BOARD2, open, wait } = require('../tests/entity_harness');
const { spawn, execFile } = require('child_process');
const { createHash } = require('crypto');
const execute = require('util').promisify(execFile);
const { chromium } = require(PLAYWRIGHT);
const MEDIA = path.join(ROOT, 'reports/media/classic-perf');
const VIEWPORT = { width: 1440, height: 900 };
const LOAD = { boards_per_adapter: 300, extra_measurements: 200, watch_hz: 100 };

function stats(values) {
  const sorted = values.slice().sort((left, right) => left - right);
  const percentile = (fraction) => sorted.length
    ? sorted[Math.min(sorted.length - 1, Math.ceil(fraction * sorted.length) - 1)] : null;
  return { p50: percentile(.5), p95: percentile(.95), max: percentile(1) };
}

function targets(result) {
  if (!result || result.lag_ms.p95 === null || result.heard.jitter_ms.p95 === null) return null;
  return result.lag_ms.p95 < 250 && result.long_task_max_ms < 50
    && result.heard.ticks >= 8 && result.heard.ticks <= 11 && result.heard.jitter_ms.p95 < 100
    && result.heard.skipped_seconds === 0 && result.errors.length === 0;
}

async function stopFixture(child) {
  // WHY: stop only our process group, never the current owner of a port.
  try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { return; }
  if (child.exitCode === null && child.signalCode === null) {
    await Promise.race([new Promise((resolve) => child.once('exit', resolve)), wait(2000)]);
  }
  try { process.kill(-child.pid, 'SIGKILL'); } catch (_) { /* group has exited */ }
}

async function startFixture(root, loaded) {
  const plugins = path.join(root, 'plugins');
  const source = path.join(ROOT, 'tests/fixtures/plugins-state');
  fs.mkdirSync(plugins);
  for (const name of ['state_joint.toml', 'state_camera.toml']) {
    fs.writeFileSync(path.join(plugins, name), fs.readFileSync(path.join(source, name), 'utf8')
      .replace('{here}', source).replace('period_s = 30', 'period_s = 120'));
  }
  const environment = { ...process.env };
  // WHY: inherited fixture knobs or a real config path must not change the workload.
  for (const key of Object.keys(environment)) {
    if (key.startsWith('STATE_') || key.startsWith('IO_DIAGNOSTICS_')) delete environment[key];
  }
  Object.assign(environment, {
    UV_CACHE_DIR: path.join(root, 'uv-cache'),
    UV_PROJECT_ENVIRONMENT: path.join(root, 'venv'),
    IO_DIAGNOSTICS_STATE_DIR: path.join(root, 'state'),
    IO_DIAGNOSTICS_CONFIG_DIR: path.join(root, 'config'),
    STATE_CONFIG_FILE: path.join(root, 'board.json'),
    STATE_PAUSE_FILE: path.join(root, 'pause'),
    STATE_NODES: loaded ? String(LOAD.boards_per_adapter) : '1',
    STATE_EXTRA_ROWS: loaded ? String(LOAD.extra_measurements) : '0',
    STATE_PERIOD_S: loaded ? String(1 / LOAD.watch_hz) : '0.1',
  });
  delete environment.VIRTUAL_ENV;
  const child = spawn('/home/bam/.local/bin/uv', [
    'run', '--project', ROOT, 'python', path.join(ROOT, 'tools/bench_fixture.py'),
    'serve', '--fixture', 'F1', '--plugins', plugins, '--port', '0', '--no-udev',
  ], { cwd: ROOT, detached: true, env: environment, stdio: ['ignore', 'pipe', 'pipe'] });
  try {
    const base = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(Error(`fixture startup timeout: ${output}`)), 60000);
      const finish = (callback, value) => { clearTimeout(timer); callback(value); };
      const read = (chunk) => {
        output = (output + chunk).slice(-16000);
        const match = output.match(/IO diagnostics on (http:\/\/127.0.0.1:(\d+))/);
        if (match) {
          if ([8820, 8821, 8822].includes(Number(match[2]))) finish(reject, Error('reserved port'));
          else finish(resolve, match[1]);
        }
      };
      child.stdout.on('data', read);
      child.stderr.on('data', read);
      child.once('error', (error) => finish(reject, error));
      child.once('exit', (code) => finish(reject, Error(`fixture exited ${code}: ${output}`)));
    });
    const listing = await fetch(`${base}/api/v1/plugins`).then((response) => response.json());
    const names = listing.plugins.map((plugin) => plugin.name).sort().join(',');
    if (names !== 'state_camera,state_joint') throw Error(`unexpected plugins: ${names}`);
    return { child, base };
  } catch (error) {
    await stopFixture(child);
    throw error;
  }
}

function installRuler() {
  const bench = window.__bench = {
    emissions: new Map(), painted: 0, pending: 0, lag: [], frames: [], tasks: [], sampleAge: [], arrivals: [], paints: [], renders: 0,
  };
  const noteEmission = (data) => {
    if (!data || !data.emitted || !data.path || !Number.isFinite(data.cursor)) return;
    bench.emissions.set(`${data.path}:${data.cursor}`, data.emitted);
    if (bench.record && data.path === bench.path) bench.arrivals.push({ emitted: data.emitted, received: Date.now() });
    // WHY bounded: the measurement apparatus must not manufacture a heap leak.
    if (bench.emissions.size > 1024) bench.emissions.delete(bench.emissions.keys().next().value);
    if (bench.record && data.heard && data.heard.age_s !== null) bench.sampleAge.push(data.heard.age_s * 1000);
  };
  // JSON.parse observes the application's own SSE decoding without a second parse.
  const originalParse = JSON.parse;
  JSON.parse = function (...args) { const data = originalParse.apply(this, args); noteEmission(data); return data; };
  const originalJson = Response.prototype.json;
  Response.prototype.json = async function (...args) {
    const data = await originalJson.apply(this, args);
    if (this.url.includes('/api/v1/values')) noteEmission(data);
    return data;
  };
  new PerformanceObserver((list) => {
    if (bench.record) for (const entry of list.getEntries()) bench.tasks.push(entry.duration);
  }).observe({ type: 'longtask', buffered: true });
  let previous = null;
  function frame(now) {
    if (bench.record) {
      if (previous !== null) bench.frames.push(now - previous);
      if (bench.painted) bench.lag.push(Date.now() - bench.painted);
    }
    previous = now;
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function instrumentPaint() {
  const layout = IODetailLayouts.d6;
  const original = layout.render;
  const bench = __bench;
  bench.path = layout.internals.Live.path;
  layout.render = function (...args) {
    const before = S.detailHtml;
    const result = original.apply(this, args);
    bench.renders += 1;
    // WHY count only changed markup: receiving or merging an event is not proof of a paint.
    if (S.detailHtml !== before) {
      const live = layout.internals.Live;
      const state = document.querySelector('[data-d6-panel="state"]');
      const emitted = state && live.loaded
        ? bench.emissions.get(`${live.path}:${live.cursor}`) || 0 : S.data.emitted || 0;
      if (emitted > bench.pending) {
        bench.pending = emitted;
        // rAF precedes paint. The following task is a conservative paint opportunity;
        // CDP frame/long-task metrics independently expose missed frames and stalls.
        requestAnimationFrame(() => setTimeout(() => { bench.painted = Math.max(bench.painted, emitted); if (bench.record) bench.paints.push(Date.now() - emitted); }, 0));
      }
    }
    return result;
  };
}

async function heardProbe(page, root) {
  // WHY: stay on the expensive State page, with warm history. Pausing the fixture's
  // receipts ages the real label; no application clock or DOM text is fabricated.
  fs.writeFileSync(path.join(root, 'pause'), '1');
  try {
    await page.evaluate(() => {
      __bench.ticks = [];
      let previous = document.querySelector('[data-d6-heard]').textContent;
      const observer = new MutationObserver(() => {
        const element = document.querySelector('[data-d6-heard]');
        if (element && element.textContent !== previous) {
          previous = element.textContent;
          __bench.ticks.push({ at: performance.now(), text: previous });
        }
      });
      observer.observe(document.getElementById('detail'), { subtree: true, characterData: true, childList: true });
      __bench.stopHeard = () => observer.disconnect();
    });
    await wait(10000);
    const ticks = await page.evaluate(() => { __bench.stopHeard(); return __bench.ticks; });
    const seconds = ticks.map((tick) => /heard (\d+) s ago/.exec(tick.text)).map((match) => match ? Number(match[1]) : null);
    const skipped = seconds.slice(1).reduce((total, value, index) =>
      total + (value !== null && seconds[index] !== null ? Math.max(0, value - seconds[index] - 1) : 0), 0);
    return { ticks: ticks.length,
      jitter_ms: stats(ticks.slice(1).map((tick, index) => Math.abs(tick.at - ticks[index].at - 1000))),
      skipped_seconds: skipped, texts: ticks.map((tick) => tick.text) };
  } finally { fs.rmSync(path.join(root, 'pause'), { force: true }); }
}

async function measure(browser, loaded, seconds, label) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'classic-bench-'));
  let host, context;
  try {
    host = await startFixture(root, loaded);
    context = await browser.newContext({ viewport: VIEWPORT });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    await page.addInitScript(installRuler);
    const cdp = await context.newCDPSession(page);
    await cdp.send('Performance.enable');
    await cdp.send('Network.enable');
    let sseBytes = 0, sseEvents = 0, httpBytes = 0, recording = false;
    const liveRequests = new Set();
    cdp.on('Network.responseReceived', (event) => {
      if (new URL(event.response.url).pathname === '/api/v1/live') liveRequests.add(event.requestId);
    });
    cdp.on('Network.dataReceived', (event) => {
      if (!recording) return;
      httpBytes += event.dataLength;
      if (liveRequests.has(event.requestId)) sseBytes += event.dataLength;
    });
    cdp.on('Network.eventSourceMessageReceived', (event) => {
      if (recording && liveRequests.has(event.requestId)) sseEvents += 1;
    });
    await open(page, host.base, BOARD, 'state');
    await page.waitForFunction(() => {
      const live = IODetailLayouts.d6.internals.Live;
      return live.loaded && live.watch && live.watch.state === 'running' && live.groups.length;
    }, null, { timeout: 30000 });
    const observed = await page.evaluate(() => ({ entities: S.byPath.size,
      rows: IODetailLayouts.d6.internals.Live.groups.reduce((total, group) => total + group.rows.length, 0) }));
    if (loaded && (observed.entities < 600 || observed.rows !== 207)) throw Error(`load not established: ${JSON.stringify(observed)}`);
    await page.evaluate(instrumentPaint);
    await wait(6000);
    await cdp.send('HeapProfiler.collectGarbage');
    const metricMap = (answer) => Object.fromEntries(answer.metrics.map((metric) => [metric.name, metric.value]));
    const initial = metricMap(await cdp.send('Performance.getMetrics'));
    await cdp.send('Profiler.enable');
    await cdp.send('Profiler.start');
    await page.evaluate(() => { Object.assign(__bench, { record: true, lag: [], frames: [], tasks: [], sampleAge: [], arrivals: [], paints: [], renders: 0 }); });
    const started = Date.now();
    recording = true;
    await wait(seconds * 1000);
    recording = false;
    const elapsed = (Date.now() - started) / 1000;
    const raw = await page.evaluate(() => {
      __bench.record = false;
      return { lag: __bench.lag, frames: __bench.frames, tasks: __bench.tasks, sampleAge: __bench.sampleAge,
        arrivals: __bench.arrivals, paints: __bench.paints, renders: __bench.renders, nodes: document.querySelectorAll('*').length };
    });
    const { profile } = await cdp.send('Profiler.stop');
    const mode = loaded ? 'loaded' : 'idle';
    fs.writeFileSync(path.join(MEDIA, `${label}-${mode}.cpuprofile`), JSON.stringify(profile));
    const final = metricMap(await cdp.send('Performance.getMetrics'));
    await cdp.send('HeapProfiler.collectGarbage');
    const retained = metricMap(await cdp.send('Performance.getMetrics'));
    const heard = await heardProbe(page, root);
    await page.screenshot({ path: path.join(MEDIA, `${label}-${mode}.png`) });
    const counts = new Map();
    for (const id of profile.samples || []) counts.set(id, (counts.get(id) || 0) + 1);
    const hot = profile.nodes.map((node) => ({
      function: node.callFrame.functionName, file: node.callFrame.url.replace(host.base, ''),
      line: node.callFrame.lineNumber + 1, samples: counts.get(node.id) || 0,
    })).sort((left, right) => right.samples - left.samples).slice(0, 18);
    const result = {
      mode, seconds: elapsed, observed,
      delivery_ms: stats(raw.arrivals.map((event) => event.received - event.emitted)),
      emission_gap_ms: stats(raw.arrivals.slice(1).map((event, index) => event.emitted - raw.arrivals[index].emitted)),
      paint_delay_ms: stats(raw.paints), lag_ms: stats(raw.lag), sample_age_ms: stats(raw.sampleAge),
      long_tasks_per_min: raw.tasks.length * 60 / elapsed,
      blocked_ms: raw.tasks.reduce((total, duration) => total + duration, 0),
      long_task_max_ms: stats(raw.tasks).max || 0, frame_ms: stats(raw.frames), heard,
      sse_bytes_per_min: sseBytes * 60 / elapsed, sse_events: sseEvents,
      all_response_bytes_per_min: httpBytes * 60 / elapsed,
      heap: { start: initial.JSHeapUsedSize, end: final.JSHeapUsedSize,
        growth: final.JSHeapUsedSize - initial.JSHeapUsedSize,
        retained_growth: retained.JSHeapUsedSize - initial.JSHeapUsedSize },
      render_count: raw.renders, dom_nodes: raw.nodes,
      performance: { task_s: final.TaskDuration - initial.TaskDuration,
        layout_s: final.LayoutDuration - initial.LayoutDuration, script_s: final.ScriptDuration - initial.ScriptDuration },
      hot, errors,
    };
    result.targets_met = targets(result);
    return result;
  } finally {
    if (context) await context.close();
    if (host) await stopFixture(host.child);
    fs.rmSync(root, { recursive: true, force: true });
  }
}

async function main() {
  const option = (name, fallback) => process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : fallback;
  const seconds = Number(option('--seconds', '60'));
  const label = option('--label', 'run');
  if (!Number.isFinite(seconds) || seconds <= 0 || !/^[a-zA-Z0-9_-]+$/.test(label)) throw Error('invalid duration or label');
  fs.mkdirSync(MEDIA, { recursive: true });
  const sha = (await execute('git', ['rev-parse', '--short', 'HEAD'], { cwd: ROOT, encoding: 'utf8' })).stdout.trim();
  const report = {
    t: new Date().toISOString(), sha, label, working_tree: ROOT,
    source_sha256: createHash('sha256').update(['io_diagnostics/live.py', 'io_diagnostics/web/app.js',
      'io_diagnostics/web/detail/d6.js', 'io_diagnostics/web/detail/d6-rows.js', 'io_diagnostics/web/activity/log.js',
      ...['core', 'c1', 'c2', 'c3', 'c4', 'c5'].map((name) => `io_diagnostics/web/charts/${name}.js`)]
      .map((name) => name + '\0' + fs.readFileSync(path.join(ROOT, name), 'utf8')).join('\0')).digest('hex'), viewport: VIEWPORT, load: LOAD, status: 'unmeasured', results: [],
    ruler: 'Age of newest emitted in changed detail markup, sampled each rAF after a paint opportunity. Poll responses receive a bench-only send clock; SSE keeps its real emitted. Idle is a State page without synthetic load. Heard pauses fixture receipts for a separate 10 s on the same warm State page. Long tasks exclude warmup. SSE is page traffic only. Heap reports used and post-GC growth; profiler files are ignored media.',
  };
  let browser;
  try {
    browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    for (const loaded of [false, true]) {
      const result = await measure(browser, loaded, seconds, label);
      report.results.push(result);
      console.log(JSON.stringify(result, null, 2));
    }
    report.status = 'measured';
  } catch (error) {
    report.error = String(error.message || error).split('\n').filter((line) => !line.includes('--')).slice(0, 18).join('\n');
    process.exitCode = 1;
    console.error(report.error);
  } finally {
    if (browser) await browser.close();
    const output = path.join(ROOT, `bench/classic/${new Date().toISOString().slice(0, 10)}-${sha}-${label}.json`);
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
    console.log(output);
  }
}

module.exports = { stats, targets, startFixture, stopFixture };
if (require.main === module) main().catch((error) => { console.error(error); process.exitCode = 1; });
