#!/usr/bin/env node
// The status dot's one mapping (io_diagnostics/web/status-dot.js), in plain Node: every row of the entity tree, at every level (adapter,
// board, camera), goes through IOStatusDot.visual(row) and gets the same three words (Zach, 2026-11-10):
//
//   hollow ring   stale: known, but not heard lately
//   solid dot     connected and live, a plugged-in port against an empty one
//   pulsing       the entity is doing something now (row.activity); merely connected and idle never pulses
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_status_dot.js
//
// The table is every presence state a row can carry (docs/api-v1.md "presence": none, heard, live, stale, restored-stale, and a state a
// newer host might send) against every activity (none, each cause the host names), plus the markup the tree draws from it.
'use strict';

const path = require('path');
const D = require(path.join(__dirname, '..', 'io_diagnostics', 'web', 'status-dot.js'));

let passed = 0;
let failed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
}

const PRESENCE = {
  none: null,
  heard: { state: 'heard', age_s: 3, restored: false, text: '' },
  live: { state: 'live', age_s: 0.4, restored: false, text: 'answers now' },
  stale: { state: 'stale', age_s: 2463, restored: false, text: 'last heard 41 min ago' },
  restored: { state: 'stale', age_s: 9000, restored: true, text: 'last heard 2 h ago (before this session)' },
  future: { state: 'dreaming', age_s: 1, restored: false, text: '' },
};
const ACTIVITY = {
  none: null,
  run: { kind: 'run', text: 'CAN 1 · Run calibration' },
  console: { kind: 'console', text: 'tview is open on it' },
  held: { kind: 'held', text: 'update_moteus_firmware has the port' },
  probe: { kind: 'probe', text: 'looking at what is plugged in' },
};
// what each presence draws, whatever the level of the row (an adapter, a board and a camera are the same here)
const SHAPE = { none: 'none', heard: 'solid', live: 'solid', stale: 'hollow', restored: 'hollow', future: 'none' };

const row = (presence, activity, extra = {}) => ({ path: 'host/x', kind: 'node', presence, activity, ...extra });

// ---- every state x every activity
for (const [presenceName, presence] of Object.entries(PRESENCE)) {
  for (const [activityName, activity] of Object.entries(ACTIVITY)) {
    const visual = D.visual(row(presence, activity));
    const shape = SHAPE[presenceName];
    const pulse = shape !== 'none' && activity !== null;
    check(`presence ${presenceName.padEnd(8)} activity ${activityName.padEnd(7)} -> ${shape}${pulse ? ' + pulse' : ''}`,
      visual.shape === shape && visual.pulse === pulse, visual);
  }
}

// ---- the rules said once, in words
check('alive is not active: a live row (a follower streaming, a presence pass answering) is solid and still',
  D.visual(row(PRESENCE.live, null)).pulse === false && D.visual(row(PRESENCE.heard, null)).pulse === false);
check('the same word for every level of the tree: kind never changes the mapping',
  ['device', 'bus', 'node', 'camera', 'sensor', 'host'].every((kind) => {
    const a = D.visual(row(PRESENCE.heard, ACTIVITY.run, { kind }));
    const b = D.visual(row(PRESENCE.heard, ACTIVITY.run));
    return a.shape === b.shape && a.pulse === b.pulse;
  }));
check('a row with no activity field at all (an older host, the past view) is idle, not an error',
  D.visual({ presence: PRESENCE.heard }).pulse === false && D.visual({ presence: PRESENCE.heard, activity: undefined }).shape === 'solid');
check('a row with no presence and no fields draws no dot', D.visual({}).shape === 'none' && D.visual(null).shape === 'none');
check('an activity that is not an object does not pulse (true, a string, an empty object)',
  [true, 'yes', {}, 1].every((value) => D.visual(row(PRESENCE.heard, value)).pulse === false));

// ---- the words a person hears (title, aria-label)
check('solid says connected', /connected/i.test(D.visual(row(PRESENCE.heard, null)).title), D.visual(row(PRESENCE.heard, null)).title);
check('hollow says when it was last heard, in the host\'s words', D.visual(row(PRESENCE.stale, null)).title === 'last heard 41 min ago');
check('pulsing says what it is doing', /Run calibration/.test(D.visual(row(PRESENCE.heard, ACTIVITY.run)).title), D.visual(row(PRESENCE.heard, ACTIVITY.run)).title);
check('hollow and pulsing says both', /last heard 41 min ago/.test(D.visual(row(PRESENCE.stale, ACTIVITY.held)).title)
  && /update_moteus_firmware/.test(D.visual(row(PRESENCE.stale, ACTIVITY.held)).title), D.visual(row(PRESENCE.stale, ACTIVITY.held)).title);

// ---- the markup the tree draws
const markup = (presence, activity) => D.html(row(presence, activity));
const classes = (html) => (/class="([^"]+)"/.exec(html) || [])[1] || '';
check('markup: solid', classes(markup(PRESENCE.heard, null)) === 't6-pres t6-pres-solid', classes(markup(PRESENCE.heard, null)));
check('markup: hollow', classes(markup(PRESENCE.stale, null)) === 't6-pres t6-pres-hollow');
check('markup: pulsing solid', classes(markup(PRESENCE.heard, ACTIVITY.run)) === 't6-pres t6-pres-solid t6-pres-pulse');
check('markup: pulsing hollow', classes(markup(PRESENCE.stale, ACTIVITY.held)) === 't6-pres t6-pres-hollow t6-pres-pulse');
check('markup: no dot keeps its slot, hidden, so icons line up', classes(markup(null, null)) === 't6-pres t6-pres-none' && /aria-hidden="true"/.test(markup(null, null)));
check('markup: a text with markup in it is escaped', !/<script/.test(D.html(row(PRESENCE.heard, { kind: 'run', text: '<script>alert(1)</script>' }))));

console.log(`\n${passed}/${passed + failed} checks passed`);
process.exit(failed ? 1 : 0);
