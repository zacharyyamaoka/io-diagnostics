#!/usr/bin/env node
// The Computer as THE host entity (2026-10-23, the computer plugin), in plain Node over the live rack frame:
//
//   /usr/bin/node /home/bam/io-diagnostics/tests/test_p1_computer.js
//
// The live 17:41 frame's host gets a self-report (what the computer plugin adds: a hot CPU) while its rollup still points
// at the dead CAN bus. P1's closed Computer row must BE the host entity (selecting it opens the host's detail; the
// v6 header's elided host name resolves to the same row), show its own problem beside the label, never borrow the
// host's worst-anywhere hint, and every structure must show the host at most once. Exits 1 on a failure.
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

// ---- P1: the Computer row is the host
const built = V.build(hot, 'p1', {});
const computer = built.rows[built.rows.length - 1];
check('p1: the last row is the Computer and it is the host entity', computer.label === 'Computer' && computer.entity === 'host'
  && computer.key === 'p1:computer' && computer.kind === 'host', { label: computer.label, entity: computer.entity, key: computer.key });
check('p1: the Computer shows its own problem beside its label', computer.status.own === 'warn' && computer.lead === HOT
  && computer.title.includes(HOT) && computer.title.includes(hot.tree.name), { own: computer.status.own, lead: computer.lead });
check('p1: no borrowed hint (the CAN bus error is drawn on its own rows, not on the Computer)', computer.status.hint === null
  && computer.hint === null, computer.status.hint);
check('p1: the built-in PCI devices stay its children, closed', computer.children.some((row) => /IOMMU/.test(row.label))
  && computer.defaultOpen === false, computer.children.map((row) => row.label));
check('p1: selecting the elided host (v6 header) lands on the Computer row', built.host && built.host.entity === 'host' && built.host.elided
  && built.byEntity.get('host') === 'p1:computer', built.host);
check('p1: the Computer row is searchable by the host name', V.build(hot, 'p1', { query: hot.tree.name }).flat.some((row) => row.entity === 'host'));

// without a self-report the Computer is still the host row, quiet
const quiet = V.build(plain, 'p1', {}).rows.at(-1);
check('p1: a host without checks keeps a quiet Computer row (the built-ins message)', quiet.entity === 'host' && quiet.status.own === null
  && /built-in PCI device/.test(quiet.message) && quiet.lead === '', { own: quiet.status.own, message: quiet.message });

// ---- P1 open by default: every entity row with children, in both modes; only the Computer stays closed
for (const showTransport of [false, true]) {
  const rows = V.build(hot, 'p1', { showTransport }).flat;
  const closed = rows.filter((row) => row.children.length && !row.defaultOpen).map((row) => row.label);
  check(`p1 (Ports ${showTransport ? 'on' : 'off'}): every row with children opens, only the Computer stays closed`,
    closed.every((label) => label === 'Computer'), closed);
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
