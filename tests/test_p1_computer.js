#!/usr/bin/env node
// The Computer as THE host entity (2026-10-23, the computer plugin), in plain Node over the live rack frame:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_p1_computer.js
//
// The live 17:41 frame's host gets a self-report (what the computer plugin adds: a hot CPU) while its rollup still points
// at the dead CAN bus. P1's root row must BE the host entity (selecting it opens the Computer page), show its own problem
// beside the label, carry the worst-below roll-up, hold every plugged thing as an ordinary child (the built-in PCI
// devices included, no second Computer row), and every structure must show the host at most once. Exits 1 on a failure.
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const V = require(path.join(ROOT, 'io_diagnostics', 'web', 'view', 'core.js'));
const STRUCTURES = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5'];
for (const id of STRUCTURES) require(path.join(ROOT, 'io_diagnostics', 'web', 'view', 'structure', `${id}.js`));
const LIVE = path.join(ROOT, 'tests', 'fixtures', 'live', 'rack-state-2026-10-22-1741.json');

let failed = 0;
let passed = 0;
function check(name, ok, detail = '') {
  if (ok) passed += 1; else failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
}

const HOT = 'CPU Tctl 92 °C: High Temperature (above 85 °C, default: the sensor reports none)';

// the live frame, its host self-reporting as the computer plugin makes it (rollup untouched: still the CAN bus)
function hotState() {
  const state = JSON.parse(fs.readFileSync(LIVE, 'utf8'));
  Object.assign(state.tree, { level: 'warn', message: HOT, own: { level: 'warn', message: HOT }, checks: 14,
    hint: { level: state.tree.rollup.level, origin: state.tree.rollup.origin, message: state.tree.rollup.message } });
  return state;
}

const plain = JSON.parse(fs.readFileSync(LIVE, 'utf8'));
const hot = hotState();

// ---- P1: the root row IS the host (Zach, 2026-10-24: "the computer should be the BAM-GPU … as its own row")
const built = V.build(hot, 'p1', {});
const [root] = built.rows;
check('p1: the one top-level row is the host entity, named as the machine is; no second Computer row, no Built-in group',
  built.rows.length === 1 && root.label === hot.tree.name && root.entity === 'host' && root.kind === 'host' && root.key === `p1:${hot.tree.path}`
  && !built.flat.some((row) => /^(Computer|Built-in)$/.test(row.label)), { rows: built.rows.map((row) => row.label), key: root.key });
check('p1: the host shows its own problem beside its label', root.status.own === 'warn' && root.lead === HOT
  && root.title.includes(HOT) && root.title.includes(hot.tree.name), { own: root.status.own, lead: root.lead });
check('p1: its roll-up is the worst thing below it (the dead CAN bus), the same hint every row carries',
  root.status.hint && root.status.hint.level === 'error' && /CAN bus/.test(root.status.hint.origin_name || root.status.hint.message), root.status.hint);
check('p1: the built-in PCI devices are ordinary children, level with the adapters and cameras',
  root.children.some((row) => /IOMMU/.test(row.label)) && root.children.some((row) => /fdcanusb/.test(row.label))
  && root.children.every((row) => row.depth === 1) && root.defaultOpen === true, root.children.map((row) => row.label).slice(0, 8));
check('p1: selecting the host (the header, a link) lands on the root row', built.host && built.host.entity === 'host' && built.host.elided === false
  && built.byEntity.get('host') === root.key, built.host);
check('p1: the host row is searchable by the host name', V.build(hot, 'p1', { query: hot.tree.name }).flat.some((row) => row.entity === 'host'));

// without a self-report the host is still the root row, quiet
const quiet = V.build(plain, 'p1', {}).rows[0];
check('p1: a host without checks is still the root row, with no problem of its own and no lead', quiet.entity === 'host' && quiet.status.own === null
  && quiet.lead === '', { own: quiet.status.own, lead: quiet.lead });

// ---- P1 open by default: the root and every entity row with children, in both modes
for (const showTransport of [false, true]) {
  const rows = V.build(hot, 'p1', { showTransport }).flat;
  const closed = rows.filter((row) => row.children.length && !row.defaultOpen).map((row) => row.label);
  check(`p1 (Ports ${showTransport ? 'on' : 'off'}): every row with children opens`, closed.length === 0, closed);
}

// ---- every structure: the host at most once, and the structures still build
for (const id of STRUCTURES) {
  let result;
  try {
    result = V.build(hot, id, {});
  } catch (error) {
    check(`${id}: builds with a self-reporting host`, false, String(error));
    continue;
  }
  const hostRows = result.flat.filter((row) => row.entity === 'host');
  check(`${id}: builds with a self-reporting host, the host shown at most once`, hostRows.length <= 1 && result.rows.length > 0,
    { rows: result.rows.length, hostRows: hostRows.map((row) => row.label) });
}

console.log(`\n${passed}/${passed + failed} checks passed`);
process.exit(failed ? 1 : 0);
