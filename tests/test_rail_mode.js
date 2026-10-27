#!/usr/bin/env node
// The L1 ribbon's sidebar-style store (layouts/l1.js createRailStore), ported case for case from Clank's
// src/workbench/store.test.ts (clank-workbench edbd2c1), over a fake storage. No browser:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_rail_mode.js
'use strict';

const assert = require('assert');
const path = require('path');

global.window = {};
require(path.resolve(__dirname, '..', 'io_diagnostics', 'web', 'layouts', 'l1.js'));
const { createRailStore } = window.IOLayouts.l1;

const MODE = 'sidebar';
const LAST = 'sidebar.last';
function fresh(stored = {}) {
  const values = new Map(Object.entries(stored));
  const store = createRailStore({ get: (key) => (values.has(key) ? values.get(key) : null), set: (key, value) => { values.set(key, value); } });
  return { store, values };
}

let passed = 0;
let failed = 0;
function test(name, body) {
  try { body(); passed += 1; console.log(`PASS  ${name}`); } catch (error) { failed += 1; console.log(`FAIL  ${name}\n      ${error.message}`); }
}

test("starts on the icon rail with Figma's rail as the open style", () => {
  const { store } = fresh();
  assert.strictEqual(store.mode(), 'collapsed');
  assert.strictEqual(store.lastOpen(), 'expanded');
});

test("reads a persisted 'wide' back, and anything unknown as collapsed", () => {
  assert.strictEqual(fresh({ [MODE]: 'wide' }).store.mode(), 'wide');
  assert.strictEqual(fresh({ [MODE]: 'sideways' }).store.mode(), 'collapsed');
  assert.strictEqual(fresh({ [MODE]: '' }).store.mode(), 'collapsed');
});

test('the toggle collapses and re-opens the LAST open style, not always Figma', () => {
  const { store, values } = fresh();
  store.set('wide');
  store.toggle();
  assert.strictEqual(store.mode(), 'collapsed');
  store.toggle();
  assert.strictEqual(store.mode(), 'wide');
  store.set('expanded');
  store.toggle();
  store.toggle();
  assert.strictEqual(store.mode(), 'expanded');
  assert.strictEqual(values.get(MODE), 'expanded');
  assert.strictEqual(values.get(LAST), 'expanded');
});

test('remembers the open style across a reload while collapsed', () => {
  const { store } = fresh({ [MODE]: 'collapsed', [LAST]: 'wide' });
  assert.strictEqual(store.lastOpen(), 'wide');
  store.toggle();
  assert.strictEqual(store.mode(), 'wide');
});

test('collapsing never overwrites the last open style; an unknown value is ignored', () => {
  const { store, values } = fresh();
  store.set('wide');
  store.set('collapsed');
  assert.strictEqual(values.get(LAST), 'wide');
  store.set('sideways');
  assert.strictEqual(store.mode(), 'collapsed');
  assert.strictEqual(values.get(MODE), 'collapsed');
});

test('a throwing storage still gives a working store (private mode)', () => {
  const store = createRailStore({ get: () => { throw new Error('denied'); }, set: () => { throw new Error('denied'); } });
  assert.strictEqual(store.mode(), 'collapsed');
  store.set('wide');
  assert.strictEqual(store.mode(), 'wide');
});

console.log(`\n${passed}/${passed + failed} checks passed`);
process.exit(failed ? 1 : 0);
