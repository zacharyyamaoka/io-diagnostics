// The entity header leads with the worse problem below it. Zach, 2026-11-13, of a plugged-in fdcanusb whose header said only who held its
// port while its CAN bus said nobody ACKs: "it connects, nothing is connected ... it can say nothing detected, make sure CAN ids plugged in
// and power is turned on".
//
//   node tests/test_header_hint.js
'use strict';
const path = require('path');
const assert = require('assert');
require(path.join(__dirname, '..', 'io_diagnostics', 'web', 'detail', 'd6.js'));
const layout = Object.values(globalThis).find((v) => v && v.internals && v.internals.headMessage)
  || (globalThis.IODetailLayouts && Object.values(globalThis.IODetailLayouts).find((v) => v.internals && v.internals.headMessage));
assert(layout, 'd6 exposes headMessage');
const { headMessage } = layout.internals;
const h = { esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;') };

const noAck = 'error-passive and no board answered: nobody ACKs this adapter\'s frames (motor supply off, or CAN_H/CAN_L not connected)';
const withHint = headMessage({ message: 'probed 08:18:01 · 8.0 s', hint: { level: 'error', message: noAck },
  rollup: { origin_name: 'CAN bus' } }, h);
assert(withHint.indexOf('data-d6-hint') >= 0, 'a problem below gets the lead line');
assert(withHint.indexOf('CAN bus: error-passive') >= 0, 'it says where it is');
assert(withHint.indexOf('motor supply off') >= 0, 'and the plugin\'s own hint');
assert(withHint.indexOf('d6-own-sub') > withHint.indexOf('data-d6-hint'), 'the row\'s own words follow, quieter');

const plain = headMessage({ message: 'probed 08:18:01 · 8.0 s', hint: null }, h);
assert(plain.indexOf('data-d6-hint') < 0 && plain.indexOf('probed 08:18:01') >= 0, 'no problem below: the row\'s own words alone');
console.log('PASS header hint: 6 checks');
process.exit(0); // d6 keeps its live timers; nothing else to wait for
