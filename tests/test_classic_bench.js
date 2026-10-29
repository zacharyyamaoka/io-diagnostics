// Guard the ruler: a slow or unmeasured page must never pass the performance target.
'use strict';
const assert = require('node:assert/strict');
const { stats, targets } = require('../tools/bench_classic');

// A fixed distribution with an independently obvious nearest-rank percentile.
const input = Array.from({ length: 100 }, (_, index) => index + 1).reverse();
assert.deepEqual(stats(input), { p50: 50, p95: 95, max: 100 });
assert.equal(input[0], 100, 'measuring must not reorder the raw samples');
assert.deepEqual(stats([]), { p50: null, p95: null, max: null });

const passing = { lag_ms: { p95: 100 }, long_task_max_ms: 0,
  heard: { ticks: 10, jitter_ms: { p95: 12 }, skipped_seconds: 0 }, errors: [] };
assert.equal(targets(passing), true);
assert.equal(targets(null), null);
assert.equal(targets({ ...passing, lag_ms: stats([]) }), null);
// Mutation checks: each failing family independently turns the acceptance gate red.
assert.equal(targets({ ...passing, lag_ms: { p95: 250 } }), false);
assert.equal(targets({ ...passing, long_task_max_ms: 50 }), false);
assert.equal(targets({ ...passing, heard: { ...passing.heard, ticks: 1 } }), false);
assert.equal(targets({ ...passing, heard: { ...passing.heard, jitter_ms: { p95: 300 } } }), false);
assert.equal(targets({ ...passing, heard: { ...passing.heard, skipped_seconds: 1 } }), false);
assert.equal(targets({ ...passing, errors: ['script failed'] }), false);
console.log('12/12 benchmark ruler checks passed (including six failing mutations)');
