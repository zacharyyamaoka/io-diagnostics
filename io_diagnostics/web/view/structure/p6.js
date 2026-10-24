// Structure p6 — "Nested": the default tree (Zach, 2026-10-24: "I don't think we should separate in the computer rigs, etc ... By
// default it should be infinitely nestable, you should just see from the top level down .. and there could be multiple things on the
// top level").
//
// One rule, any depth: an entity is drawn under the entity that contains it, and the top level is whatever nothing contains: the
// computer, and a rig (an expected setup's "1-DOF test rack") that contains devices. There is no fixed section list (no "Computer /
// Rigs / Devices" folders): a rig's adapters hold their boards, a board holds its driver, and the same walk goes as deep as the
// hierarchy does. Containment here is
//   - a rig contains the devices its expected setup names and that are here (GET /api/v1/state expected_setup.rigs[].members);
//   - everything else is contained the way p1 draws it: devices under the computer, boards under their adapter.
// An entity appears once: a rig's adapter is under the rig, not also under the computer. p1 stays the PHYSICAL view (the computer
// is the one root and the rig is a row under it); this structure builds on p1's rows and only moves the rig and its members.
//
// WHY the roll-up hints are recomputed for the rows that lost a child (core.js setRollup): the API's "worst below" counts what is
// physically below a row; the tree must say what it draws below it. Moving a rig's adapters out from under the computer leaves
// the computer with an API hint about rows it no longer shows.
'use strict';
(function (root) {
  const V = root.IOView;

  // Take the first row matching `match` out of the tree; returns it and the rows that lost a child (deepest first).
  function detach(rows, match, lost = []) {
    for (let at = 0; at < rows.length; at += 1) {
      if (match(rows[at])) { const [row] = rows.splice(at, 1); return { row, lost }; }
      const inner = detach(rows[at].children, match, [rows[at], ...lost]);
      if (inner.row) return inner;
    }
    return { row: null, lost };
  }

  function nest(state, options) {
    const tops = root.IOStructures.p1.rows(state, options);
    const rigs = (state && state.expected_setup && state.expected_setup.rigs) || [];
    const rigRows = [];
    const lost = new Set();
    for (const rig of rigs) {
      const taken = detach(tops, (row) => row.entity === rig.path);
      if (!taken.row) continue; // the rig row is hidden (its host is not drawn): nothing to nest
      taken.lost.forEach((row) => lost.add(row));
      const rigRow = taken.row;
      for (const path of rig.members || []) {
        const member = detach(tops, (row) => row.entity === path);
        if (!member.row) continue;
        member.lost.forEach((row) => lost.add(row));
        rigRow.children.push(member.row);
      }
      rigRow.defaultOpen = rigRow.children.length > 0;
      rigRows.push(rigRow);
    }
    // a composite (docs/composition-v1.md) is a top-level row too when nothing contains it: it comes out of the computer like a rig; its
    // own members and sub-composites were nested under it by p1's rows
    const composites = root.IOComposites ? root.IOComposites.lift(tops, state) : { rows: [], lost: [] };
    composites.lost.forEach((row) => lost.add(row));
    // deepest first: a parent's roll-up reads its children's new ones
    [...lost].reverse().forEach((row) => V.setRollup(row));
    rigRows.forEach((row) => { if (row.children.length) V.setRollup(row); });
    return [...tops, ...rigRows, ...composites.rows];
  }

  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p6 = {
    view: 'nested',
    name: 'Nested',
    rootRow: true,
    description: 'Infinitely nestable from the top: the computer and any rig that contains devices are the top level; a rig holds its adapters and cameras, an adapter its boards, to any depth. No fixed sections. Group by Physical keeps the computer as the one root.',
    order: 0,
    get toggles() { return root.IOStructures.p1.toggles; },
    get columns() { return root.IOStructures.p1.columns; },
    rows: nest,
  };
})(typeof window !== 'undefined' ? window : globalThis);
