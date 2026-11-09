#!/usr/bin/env node
// Connected only (Zach, 2026-11-09: "maybe it's simply just about adding a little button there at the top that I toggle to be like,
// just show connected things"): the view model's `connected` option, in plain Node.
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_connected_only.js
//
// A row stays when it answers now (presence heard or live, not an empty port) or holds a row that does; a stale or restored row,
// a board a presence pass stopped hearing, goes, and so does a grouping row left with nothing in it. It composes with every
// grouping (Physical, Nested, Type), with search and with sort, and a row that lost children rolls up only what it still draws.
'use strict';

const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const V = require(path.join(ROOT, 'io_diagnostics', 'web', 'view', 'core.js'));
for (const id of ['p1', 'p6', 'p7']) require(path.join(ROOT, 'io_diagnostics', 'web', 'view', 'structure', `${id}.js`));

let failed = 0;
let passed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
}

const HEARD = { state: 'heard', age_s: 3, restored: false, text: '' };
const LIVE = { state: 'live', age_s: 0.4, restored: false, text: 'answers now' };
const STALE = { state: 'stale', age_s: 2463, restored: false, text: 'last heard 41 min ago' };
const RESTORED = { state: 'stale', age_s: null, restored: true, text: 'not heard yet this session' };

function node(path, name, extra = {}) {
  const level = extra.level === undefined ? 'ok' : extra.level;
  return {
    path, name, kind: 'device', icon: 'device', parent: path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : null, edge: 'via',
    depth: path.split('/').length - 1, level, message: '', own: { level, message: '' },
    rollup: { level, origin: null, origin_name: '', message: '', counts: {}, knock_on: [] }, hint: null, knock_on_of: null,
    stale: level === 'stale', connected: true, hardware_id: '', identity: {}, owner: 'test', id_text: '', last_received: null, age_s: null,
    expected_period_s: null, stale_after_s: null, checks: 1, actions: 0, faults: 0, presence: HEARD, claimed: true, usb: null, rate: null,
    children: [], ...extra,
  };
}

const ADAPTER = 'host/usb1/port-1-3/usb-1-3';
const OTHER = 'host/usb1/port-1-5/usb-1-5';
const EMPTY = 'host/usb1/port-1-4';
const CAMERA = 'host/usb6/port-6-1/usb-6-1';
const RIG = 'host/rig-1-dof-test-rack';
const board = (base, id, presence, extra = {}) => node(`${base}/can/${id}`, `CAN ${id}`, {
  kind: 'node', icon: 'servo', presence, hardware_id: `moteus:${base.slice(-3)}-${id}`,
  identity: { manufacturer: 'mjbots', model: 'moteus-r4.11', serial_number: `SN${id}` }, ...extra,
});

// The rack of 2026-11-09 13:47 UTC: fdcanusb 272813EF with the three new boards live and CAN 1 / CAN 15 remembered but gone;
// fdcanusb 0607623A whose boards all answer but one restored row nobody re-heard; an empty port; a camera; a rig over two devices.
function rackState() {
  const bus = node(`${ADAPTER}/can`, 'CAN bus', { kind: 'bus', icon: 'bus', edge: 'part_of', presence: STALE, level: 'stale' });
  bus.children = [board(ADAPTER, 1, STALE, { level: 'stale' }), board(ADAPTER, 15, STALE, { level: 'stale' }),
    board(ADAPTER, 12, LIVE), board(ADAPTER, 13, LIVE), board(ADAPTER, 14, LIVE, { level: 'warn' })];
  const bus2 = node(`${OTHER}/can`, 'CAN bus', { kind: 'bus', icon: 'bus', edge: 'part_of' });
  bus2.children = [board(OTHER, 4, LIVE), board(OTHER, 11, LIVE), board(OTHER, 7, RESTORED, { level: null, own: { level: null, message: '' } })];
  const port = (name, child, extra = {}) => node(name, `Port ${name.split('-').slice(-2).join('-')}`, { kind: 'port', icon: 'port', presence: null, ...extra, children: child ? [child] : [] });
  // the API's hint: the worst below it, physically (the stale boards), as /state sends it
  const adapter = node(ADAPTER, 'fdcanusb 272813EF', { icon: 'adapter', identity: { model: 'fdcanusb', serial_number: '272813EF' }, children: [bus],
    hint: { level: 'stale', origin: `${ADAPTER}/can/1`, message: 'no update for 2463 s' },
    rollup: { level: 'stale', origin: `${ADAPTER}/can/1`, origin_name: 'CAN 1', message: 'no update for 2463 s', counts: { stale: 2 }, knock_on: [] } });
  const other = node(OTHER, 'fdcanusb 0607623A', { icon: 'adapter', identity: { model: 'fdcanusb', serial_number: '0607623A' }, children: [bus2] });
  const camera = node(CAMERA, 'RealSense D405 218622274411', { icon: 'camera' });
  const usb1 = node('host/usb1', 'USB bus 1 · 480 Mb/s', { kind: 'bus', icon: 'usb', presence: null, own: { level: null, message: '' }, level: null,
    children: [port('host/usb1/port-1-3', adapter), port(EMPTY, null, { connected: false }), port('host/usb1/port-1-5', other)] });
  const usb6 = node('host/usb6', 'USB bus 6 · 10 Gb/s', { kind: 'bus', icon: 'usb', presence: null, own: { level: null, message: '' }, level: null,
    children: [port('host/usb6/port-6-1', camera)] });
  const rig = node(RIG, '1-DOF test rack · 2/2 adapters · 5/7 boards', { kind: 'rig', icon: 'group', presence: null, level: 'warn' });
  const host = node('host', 'bam-GPU', { kind: 'host', icon: 'host', presence: LIVE, children: [rig, usb1, usb6] });
  return {
    tree: host, summary: { counts: {} }, composition: { composites: [] }, clock: { now: 1000, rank: ['ok', 'warn', 'unknown', 'stale', 'error'] },
    expected_setup: { rigs: [{ name: '1-DOF test rack', path: RIG, devices: 2, members: [ADAPTER, CAMERA] }], retired: [] },
  };
}

const entities = (built) => built.flat.map((row) => row.entity).filter(Boolean);
const has = (built, entity) => entities(built).includes(entity);

// ---- off: nothing changes
{
  const state = rackState();
  const plain = V.build(state, 'p1', {});
  const off = V.build(state, 'p1', { connected: false });
  check('off: the tree is the same as without the option', JSON.stringify(entities(off)) === JSON.stringify(entities(plain)));
  check('off: the remembered boards are there (the model pads them in)', has(off, `${ADAPTER}/can/1`) && has(off, `${ADAPTER}/can/15`));
}

// ---- on, Physical
{
  const state = rackState();
  const before = JSON.stringify(state);
  const on = V.build(state, 'p1', { connected: true });
  check('on: a board that answers now stays (CAN 12, 13, 14 live)', [12, 13, 14].every((id) => has(on, `${ADAPTER}/can/${id}`)), entities(on));
  check('on: a board known but not heard lately goes (CAN 1, CAN 15)', !has(on, `${ADAPTER}/can/1`) && !has(on, `${ADAPTER}/can/15`), entities(on));
  check('on: a restored row no run has re-heard goes', !has(on, `${OTHER}/can/7`) && has(on, `${OTHER}/can/4`), entities(on));
  check('on: the adapter stays as the way to its live boards', has(on, ADAPTER) && has(on, OTHER), entities(on));
  check('on: an empty port goes', !has(on, EMPTY));
  check('on: the camera stays, and the computer', has(on, CAMERA) && has(on, 'host'));
  const ports = V.build(state, 'p1', { connected: true, showTransport: true });
  check('on, with Ports shown: the ports and USB buses on the way to a live device stay; the empty port goes',
    has(ports, 'host/usb6') && has(ports, 'host/usb1/port-1-3') && !has(ports, EMPTY), entities(ports));
  const adapter = on.byKey.get(on.byEntity.get(ADAPTER));
  check('on: a row that lost children rolls up only what it still draws (CAN 14 warn, not the hidden stale boards)',
    adapter.status.hint && adapter.status.hint.level === 'warn', adapter.status.hint);
  const full = V.build(state, 'p1', {});
  const fullAdapter = full.byKey.get(full.byEntity.get(ADAPTER));
  check('off: the same row rolls up the stale boards (the ruler for the check above)', fullAdapter.status.hint && fullAdapter.status.hint.level === 'stale', fullAdapter.status.hint);
  check('on: the input state is not changed (a build is repeatable)', JSON.stringify(state) === before);
  const again = V.build(state, 'p1', {});
  check('on then off: the remembered boards come back', has(again, `${ADAPTER}/can/1`));
}

// ---- on, Nested (the default grouping) and Type
{
  const state = rackState();
  const nested = V.build(state, 'p6', { connected: true });
  const rig = nested.byKey.get(nested.byEntity.get(RIG));
  check('Nested: the rig stays as the container of what answers, and its adapter keeps only live boards', rig && rig.children.some((row) => row.entity === ADAPTER)
    && has(nested, `${ADAPTER}/can/14`) && !has(nested, `${ADAPTER}/can/1`), entities(nested));
  const typed = V.build(state, 'p7', { connected: true });
  check('Type: the folders keep only what answers', has(typed, `${ADAPTER}/can/12`) && !has(typed, `${ADAPTER}/can/15`), entities(typed));
}

// ---- composes with search and sort
{
  const state = rackState();
  const found = V.build(state, 'p1', { connected: true, query: 'CAN 1' });
  check('search: a hidden board is not found ("CAN 1" finds CAN 12, 13, 14, 11, not CAN 1 or 15)', !has(found, `${ADAPTER}/can/1`) && !has(found, `${ADAPTER}/can/15`)
    && has(found, `${ADAPTER}/can/12`), entities(found));
  const sorted = V.build(state, 'p1', { connected: true, sort: 'name' });
  check('sort: still only what answers', !has(sorted, `${ADAPTER}/can/1`) && has(sorted, `${ADAPTER}/can/13`));
}

// ---- nothing plugged in
{
  const state = rackState();
  const quiet = JSON.parse(JSON.stringify(state));
  (function silence(row) { if (row.kind !== 'host') row.presence = row.presence ? STALE : null; row.children.forEach(silence); })(quiet.tree);
  const empty = V.build(quiet, 'p1', { connected: true });
  check('nothing answers: only the computer itself stays', entities(empty).join() === 'host' || entities(empty).length === 0, entities(empty));
}

console.log(`\n${passed}/${passed + failed} checks passed`);
process.exitCode = failed ? 1 : 0;
