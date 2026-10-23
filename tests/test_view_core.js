#!/usr/bin/env node
// Unit tests for the round 2 view model (io_diagnostics/web/view/core.js + the p0 structure), in plain Node.
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_view_core.js
//
// Synthetic states pin each rule (labels, single-host elision, parts folding, query, the black-box hint passed
// through); then the same invariants run over the REAL fixture frames F1 / F2 / F3 (`cli.py serve --fixture <F>
// --port 0`, a free port each, stopped by their process group). Prints one line per check and exits 1 on a failure.
// Nothing here opens a CAN adapter or a camera.
'use strict';

const path = require('path');
const http = require('http');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const UV = '/home/bam/.local/bin/uv';
const V = require(path.join(ROOT, 'io_diagnostics', 'web', 'view', 'core.js'));
require(path.join(ROOT, 'io_diagnostics', 'web', 'view', 'structure', 'p0.js'));

let failed = 0;
let passed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
}

// ------------------------------------------------------------------------------- fixtures ----
// The node shape of api-v1 (only the fields core.js reads).
function node(path, name, extra = {}) {
  return {
    path, name, kind: 'device', icon: 'device', parent: null, edge: 'via', depth: path.split('/').length - 1,
    level: 'ok', message: '', own: { level: 'ok', message: '' }, rollup: { level: 'ok', origin: null, origin_name: '', message: '', counts: {}, knock_on: [] },
    hint: null, knock_on_of: null, stale: false, connected: true, hardware_id: null, identity: {}, owner: 'test', id_text: '',
    last_received: null, age_s: null, expected_period_s: null, stale_after_s: null, checks: 0, actions: 0, faults: 0, children: [], ...extra,
  };
}
const grouping = (path, name, extra = {}) => node(path, name, { kind: 'bus', icon: 'usb', own: { level: null, message: '' }, level: null, ...extra });

// one host · USB bus 1 · port · fdcanusb (unknown, error below) · CAN bus · CAN 15 (ok, error below from its driver part)
function rackState() {
  const errorRollup = (origin) => ({ level: 'error', origin, origin_name: 'CAN 15 driver', message: 'CAN 15 driver: fault 33 (motor driver)', counts: { error: 1 }, knock_on: [] });
  const driverPath = 'host/usb1/port-1-3/usb-1-3/can/15/driver';
  const driver = node(driverPath, 'CAN 15 driver', { kind: 'part', icon: 'chip', edge: 'part_of', level: 'error', own: { level: 'error', message: 'fault 33 (motor driver)' }, message: 'fault 33 (motor driver)', rollup: errorRollup(driverPath) });
  const motor = node('host/usb1/port-1-3/usb-1-3/can/15/motor', 'CAN 15 motor', { kind: 'part', icon: 'motor', edge: 'part_of' });
  const can15 = node('host/usb1/port-1-3/usb-1-3/can/15', 'CAN 15', {
    kind: 'node', icon: 'servo', identity: { model: 'moteus-c1', serial_number: 'ABoAQVQwUBUgOTNO' }, id_text: 'ABoAQVQwUBUgOTNO',
    rollup: errorRollup(driverPath), hint: { level: 'error', origin: driverPath, message: 'CAN 15 driver: fault 33 (motor driver)' }, children: [driver, motor],
  });
  const can14 = node('host/usb1/port-1-3/usb-1-3/can/14', 'CAN 14', { kind: 'node', icon: 'servo', identity: { model: 'moteus-c1', serial_number: 'AFoANlQwUBcgOTNO' }, id_text: 'AFoANlQwUBcgOTNO' });
  const bus = node('host/usb1/port-1-3/usb-1-3/can', '272813EF CAN bus', { kind: 'bus', icon: 'bus', rollup: errorRollup(driverPath), hint: { level: 'error', origin: driverPath, message: 'CAN 15 driver: fault 33' }, children: [can14, can15] });
  const tty = node('host/usb1/port-1-3/usb-1-3/tty', '/dev/ttyACM0', { icon: 'tty', identity: { model: '/dev/ttyACM0' } });
  const adapter = node('host/usb1/port-1-3/usb-1-3', 'fdcanusb 272813EF', {
    icon: 'adapter', level: 'unknown', own: { level: 'unknown', message: 'port held' }, message: 'port held', id_text: '272813EF',
    identity: { manufacturer: 'mjbots', model: 'fdcanusb', serial_number: '272813EF' }, rollup: errorRollup(driverPath),
    hint: { level: 'error', origin: driverPath, message: 'CAN 15 driver: fault 33' }, children: [tty, bus],
  });
  const port = node('host/usb1/port-1-3', 'Port 1-3', { kind: 'port', icon: 'port', id_text: '12 Mb/s', rollup: errorRollup(driverPath), hint: { level: 'error', origin: driverPath, message: 'x' }, children: [adapter] });
  const empty = node('host/usb1/port-1-5', 'Port 1-5', { kind: 'port', icon: 'port', id_text: 'empty', connected: false });
  const usb1 = grouping('host/usb1', 'USB bus 1 · 480 Mb/s', { level: 'error', rollup: errorRollup(driverPath), id_text: '1 devices', children: [port, empty] });
  const usb2 = grouping('host/usb2', 'USB bus 2 · 10 Gb/s', { id_text: '0 devices', rollup: { level: null, counts: {}, knock_on: [] } });
  const camera = node('host/usb6/port-6-1/usb-6-1', 'D405 ·6036', {
    icon: 'camera', level: 'warn', own: { level: 'warn', message: 'moved' }, message: 'moved', id_text: '218622276036 (SDK)',
    identity: { model: 'RealSense D405', serial_number: '218622276036 (SDK)' },
    children: [node('host/usb6/port-6-1/usb-6-1/video0', '/dev/video0 · capture', { icon: 'video', identity: { model: 'Intel(R) RealSense(TM) Depth Ca' } }),
      node('host/usb6/port-6-1/usb-6-1/depth', 'D405 ·6036 depth', { kind: 'stream', icon: 'stream', edge: 'part_of' })],
  });
  const usb6 = grouping('host/usb6', 'USB bus 6 · 10 Gb/s', { level: 'warn', children: [node('host/usb6/port-6-1', 'Port 6-1', { kind: 'port', icon: 'port', hint: { level: 'warn', origin: camera.path, message: 'moved' }, children: [camera] })] });
  const host = node('host', 'bam-GPU', { kind: 'host', icon: 'host', edge: null, depth: 0, own: { level: null, message: '' }, level: 'error', id_text: '3 devices', rollup: errorRollup(driverPath), children: [usb1, usb2, usb6] });
  return { api: 'io-diagnostics/1', tree: host, summary: { level: 'error', counts: { error: 1, unknown: 1, warn: 1 }, attention: [] } };
}

const labels = (rows) => rows.map((row) => row.label);
const find = (built, label) => built.flat.find((row) => row.label === label);

// ------------------------------------------------------------------------------ unit cases ----
function unit() {
  // labels: model · id for identified hardware; everything else verbatim
  const L = (n) => V.labelOf(n).label;
  check('label fdcanusb', L(node('a', 'fdcanusb 272813EF', { identity: { model: 'fdcanusb', serial_number: '272813EF' } })) === 'fdcanusb · 272813EF');
  check('label moteus node', L(node('a', 'CAN 15', { kind: 'node', identity: { model: 'moteus-c1', serial_number: 'X' } })) === 'moteus-c1 · CAN 15');
  check('label RealSense', L(node('a', 'D405 ·6036', { identity: { model: 'RealSense D405', serial_number: '218622276036 (SDK)' } })) === 'RealSense D405 · 6036');
  check('label serial when the name is the model', L(node('a', 'Logi Webcam C920e', { identity: { model: 'Logi Webcam C920e', serial_number: 'AFF1789F' } })) === 'Logi Webcam C920e · AFF1789F');
  check('label verbatim without a serial', L(node('a', 'ITE Device', { identity: { model: 'ITE Device' } })) === 'ITE Device'
    && L(node('a', '/dev/video0 · capture', { identity: { model: 'Intel(R) RealSense(TM) Depth Ca' } })) === '/dev/video0 · capture'
    && L(node('a', 'Port 1-3', {})) === 'Port 1-3');

  const state = rackState();
  const built = V.build(state, 'p0', {});
  // single-host elision
  check('elision: one host -> its children are the top level', labels(built.rows).join(' | ') === 'USB bus 1 · 480 Mb/s | USB bus 2 · 10 Gb/s | USB bus 6 · 10 Gb/s'
    && built.rows.every((row) => row.depth === 0 && row.parent === null) && built.host.elided === true && built.host.name === 'bam-GPU', labels(built.rows));
  const kept = V.build(state, 'p0', { elideSingleHost: false });
  check('elision off -> the host row', kept.rows.length === 1 && kept.rows[0].label === 'bam-GPU' && kept.rows[0].children.length === 3);
  const two = { ...state, tree: grouping('fleet', 'fleet', { kind: 'group', children: [state.tree, { ...rackState().tree, path: 'host2', name: 'bam-Pi' }] }) };
  const fleet = V.build(two, 'p0', {});
  check('several hosts -> host rows appear', labels(fleet.rows).join(' | ') === 'bam-GPU | bam-Pi' && fleet.host.elided === false, labels(fleet.rows));

  // labels inside the built rows; the id column drops an id the label already says
  const adapter = find(built, 'fdcanusb · 272813EF');
  const can15 = find(built, 'moteus-c1 · CAN 15');
  check('rows carry labels; id not repeated', Boolean(adapter) && adapter.idText === '' && can15.idText === 'ABoAQVQwUBUgOTNO' && can15.apiName === 'CAN 15'
    && Boolean(find(built, 'RealSense D405 · 6036')));

  // parts folding
  const partRows = built.flat.filter((row) => row.edge === 'part_of');
  check('parts are not rows', partRows.length === 0, partRows.map((row) => row.label));
  check('parts listed on the owner', can15.parts.map((part) => part.label).join(',') === 'driver,motor' && can15.children.length === 0);
  check('worst part surfaces on the owner', can15.status.part && can15.status.part.level === 'error' && can15.status.part.text === 'driver: fault 33 (motor driver)'
    && can15.status.note === 'driver: fault 33 (motor driver)' && can15.status.own === null, can15.status);
  check('hint explained by a folded part is not repeated', can15.status.hint === null && can15.hint === null);
  const bus = find(built, '272813EF CAN bus');
  check('ancestors keep the API hint', bus.status.hint && bus.status.hint.level === 'error' && adapter.status.hint.level === 'error' && adapter.status.own === 'unknown');
  check('a folded part maps to its owner row', built.byEntity.get('host/usb1/port-1-3/usb-1-3/can/15/driver') === can15.key
    && built.byEntity.get('host/usb6/port-6-1/usb-6-1/depth') === find(built, 'RealSense D405 · 6036').key);
  check('streams fold, via children stay', find(built, 'RealSense D405 · 6036').children.map((row) => row.label).join(',') === '/dev/video0 · capture');
  const withParts = V.build(state, 'p0', { showParts: true });
  check('showParts draws parts as rows (v1 tree)', Boolean(find(withParts, 'CAN 15 driver')) && find(withParts, 'moteus-c1 · CAN 15').parts.length === 0
    && find(withParts, 'moteus-c1 · CAN 15').status.hint.level === 'error');

  // query
  const q = V.build(state, 'p0', { query: 'fault 33' });
  const hits = q.flat.filter((row) => row.hit).map((row) => row.label);
  check('query finds an owner by its part', hits.includes('moteus-c1 · CAN 15') && !q.flat.some((row) => row.label === 'moteus-c1 · CAN 14')
    && !q.flat.some((row) => row.label === 'USB bus 2 · 10 Gb/s'), q.flat.map((row) => `${row.label}${row.hit ? '*' : ''}`));
  check('query keeps ancestors, open', q.flat.filter((row) => !row.hit).every((row) => row.defaultOpen === true));
  const q2 = V.build(state, 'p0', { query: '6036' });
  check('query by label id', q2.flat.some((row) => row.hit && row.label === 'RealSense D405 · 6036'), q2.flat.map((row) => row.label));

  // the black-box rule: own level is the API's self-report; the hint is the API's, passed through
  const usb1 = find(built, 'USB bus 1 · 480 Mb/s');
  check('grouping row: no own level, its rollup is the hint', usb1.status.own === null && usb1.status.hint.level === 'error' && usb1.level === 'error');
  const camera = find(built, 'RealSense D405 · 6036');
  check('self-report kept, never replaced by the rollup', camera.status.own === 'warn' && camera.level === 'warn' && adapter.level === 'unknown');

  // empty slots and defaults
  check('p0 default expansion', usb1.defaultOpen && find(built, 'Port 1-3').defaultOpen && adapter.defaultOpen && bus.defaultOpen && !camera.defaultOpen);
  const noEmpty = V.build(state, 'p0', { showEmpty: false });
  check('showEmpty off hides empty ports and buses', !find(noEmpty, 'Port 1-5') && !find(noEmpty, 'USB bus 2 · 10 Gb/s') && Boolean(find(noEmpty, 'Port 1-3')));

  // registry + group rows
  check('registry: p0 registered with toggles', V.structureIds().includes('p0') && V.defaultOptions(globalThis.IOStructures.p0).showParts === false
    && V.build(state, 'nope', {}).structure === V.structureIds()[0]);
  const group = V.finalize([V.groupRow({ key: 'g:actuators', label: 'Actuators', children: [can15, find(built, 'moteus-c1 · CAN 14')] })]);
  check('groupRow: no entity, worst member as hint', group[0].entity === null && group[0].status.hint.level === 'error' && group[0].children[0].depth === 1
    && group[0].children[0].parent === 'g:actuators');
}

// ------------------------------------------------------------------- real fixture frames ----
function startServer(fixture) {
  const env = { ...process.env };
  delete env.VIRTUAL_ENV;
  const child = spawn(UV, ['run', '--project', ROOT, 'python', path.join(ROOT, 'io_diagnostics', 'cli.py'),
    'serve', '--fixture', fixture, '--port', '0', '--no-udev'], { cwd: ROOT, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => { stop(child); reject(new Error(`${fixture} did not start: ${output}`)); }, 60000);
    const onData = (chunk) => {
      output += chunk;
      const found = output.match(/IO diagnostics on (http:\/\/127\.0\.0\.1:\d+)\//);
      if (found) { clearTimeout(timer); resolve({ child, base: found[1] }); }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`${fixture} exited (${code}): ${output}`)); });
  });
}
const stop = (child) => { try { process.kill(-child.pid, 'SIGTERM'); } catch (_) { /* gone */ } };
const getJson = (url) => new Promise((resolve, reject) => http.get(url, (res) => {
  let body = '';
  res.on('data', (chunk) => { body += chunk; });
  res.on('end', () => { try { resolve(JSON.parse(body)); } catch (error) { reject(error); } });
}).on('error', reject));

function invariants(frame, state) {
  const nodes = new Map();
  (function visit(n) { nodes.set(n.path, n); n.children.forEach(visit); })(state.tree);
  for (const showParts of [false, true]) {
    const tag = `${frame} showParts=${showParts}`;
    const built = V.build(state, 'p0', { showParts });
    const bad = [];
    for (const row of built.flat) {
      const api = nodes.get(row.entity);
      if (!api) { bad.push(`${row.label}: no entity`); continue; }
      if (row.level !== api.level || row.own !== api.own) bad.push(`${row.label}: level ${row.level} vs api ${api.level}`);
      if (row.status.own !== V.ownProblem(api)) bad.push(`${row.label}: own ${row.status.own}`);
      const apiHint = V.hintOf(api);
      const partEntities = new Set(row.parts.map((part) => part.entity));
      const expected = apiHint && !(apiHint.origin && partEntities.has(apiHint.origin)) ? apiHint.level : null;
      if ((row.status.hint ? row.status.hint.level : null) !== expected) bad.push(`${row.label}: hint ${row.status.hint && row.status.hint.level} vs api ${expected}`);
    }
    check(`${tag}: black box passed through on every row`, bad.length === 0 && built.flat.length > 0, bad.slice(0, 4).join(' · ') || `${built.flat.length} rows`);
    const counted = built.flat.length + built.flat.reduce((n, row) => n + row.parts.length, 0) + 1; // + the elided host
    check(`${tag}: every entity is a row or a folded part`, counted === nodes.size, `${counted} of ${nodes.size}`);
    if (!showParts) check(`${tag}: no part_of rows`, built.flat.every((row) => row.edge !== 'part_of'));
  }
  const built = V.build(state, 'p0', {});
  check(`${frame}: single host elided`, built.host && built.host.elided && built.rows.every((row) => row.kind !== 'host'), built.rows.map((row) => row.label).slice(0, 3));
  // every attention item lands on a visible row (its own, or its owner's when it is a folded part)
  const lost = state.summary.attention.filter((item) => !built.byEntity.has(item.path));
  check(`${frame}: every attention item maps to a row`, lost.length === 0, lost.map((item) => item.name));
}

const countParts = (n) => (n.edge === 'part_of' ? 1 : 0) + n.children.reduce((sum, child) => sum + countParts(child), 0);
const countNodes = (n) => 1 + n.children.reduce((sum, child) => sum + countNodes(child), 0);
const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

// WHY poll: the replayed plugins report a moment after the server starts; the first /state has the OS rows only
async function settledState(base) {
  let last = null;
  let previous = -1;
  for (let i = 0; i < 40; i += 1) {
    last = await getJson(`${base}/api/v1/state`);
    const size = countNodes(last.tree);
    if (countParts(last.tree) > 0 && size === previous) return last;
    previous = size;
    await sleep(500);
  }
  return last;
}

async function frames() {
  for (const fixture of ['F1', 'F2', 'F3']) {
    const server = await startServer(fixture);
    try {
      const state = await settledState(server.base);
      check(`${fixture}: plugin entities with parts present`, countParts(state.tree) > 0, `${countNodes(state.tree)} entities, ${countParts(state.tree)} parts`);
      invariants(fixture, state);
    } finally {
      stop(server.child);
    }
  }
}

(async () => {
  unit();
  if (!process.argv.includes('--unit')) await frames();
  console.log(`\n${passed}/${passed + failed} checks passed`);
  process.exitCode = failed ? 1 : 0;
})().catch((error) => { console.error(error); process.exitCode = 1; });
