// Composites in the tree (docs/composition-v1.md): a composite row is an ordinary entity row (nested under its parent composite by the API's
// `via` edge, so every structure already draws "Test rack > Robot 1 > Arm 1"); what the API tree does NOT put under it are its leaf
// children, which keep their physical place (a board hangs under its adapter). This is the one function that moves them: for each composite in
// GET /api/v1/state `composition.composites`, the rows of its `members` come out of wherever the structure drew them and go under the
// composite's row, in the order the file lists its children (a sub-composite, a camera, a board). An entity appears once, as p6's rig
// members do; a member whose row is not drawn (hidden by Visibility) is skipped.
//
// WHY data-driven and apart from the structures: it reads only `composition` (the same list the rig rows have, `expected_setup.rigs[].members`),
// knows no kind, and a structure calls it once on the rows it built (p1 does; the nested p6 builds on p1's rows).
'use strict';
(function (root) {
  // take the first row for `entity` out of the rows (any depth); `lost` = the rows that lost a child, deepest first
  function detach(rows, entity, lost = []) {
    for (let at = 0; at < rows.length; at += 1) {
      if (rows[at].entity === entity) { const [row] = rows.splice(at, 1); return { row, lost }; }
      const inner = detach(rows[at].children || [], entity, [rows[at], ...lost]);
      if (inner.row) return inner;
    }
    return { row: null, lost };
  }
  function find(rows, entity) {
    for (const row of rows) {
      if (row.entity === entity) return row;
      const inner = find(row.children || [], entity);
      if (inner) return inner;
    }
    return null;
  }

  function nest(rows, state) {
    const composites = (state && state.composition && state.composition.composites) || [];
    if (!composites.length) return rows;
    const lost = new Set();
    // children first: a composite's own row must exist (it is a tree row) before its members go under it
    for (const composite of composites) {
      const target = find(rows, composite.path);
      if (!target) continue;
      // WHY a composite draws its own level even when it is a consequence of a child (the API's knock_on_of, which keeps the chip's count
      // honest): a tree row that is a knock-on draws no mark, so a collapsed rack over a failed joint would look healthy. The composite's level
      // is its worst child's, as an icon only (the status carries counts and names, never a verdict of its own); the arm, robot and rack icons
      // are the way down to the joint, not more faults.
      target.knock_on_of = null;
      for (const path of composite.members || []) {
        if (find(target.children || [], path)) continue; // already drawn under it
        const taken = detach(rows, path);
        if (!taken.row || taken.row === target) continue;
        taken.lost.forEach((row) => lost.add(row));
        target.children.push(taken.row);
      }
      // the file's order: a sub-composite, a board, a camera, as the children list says them
      const order = new Map(composite.children.map((child, at) => [child.path, at]));
      target.children.sort((a, b) => (order.has(a.entity) ? order.get(a.entity) : 1e6) - (order.has(b.entity) ? order.get(b.entity) : 1e6));
      target.defaultOpen = target.children.length > 0;
    }
    // the roll-up hints of the rows that lost a child say what the row now draws below it (core.setRollup, where the view has it)
    const V = root.IOView;
    if (V && typeof V.setRollup === 'function') [...lost].reverse().forEach((row) => V.setRollup(row));
    return rows;
  }

  // The root composites (the ones no other composite contains) out of the Computer's row, for a structure whose top level is "whatever
  // nothing contains" (p6 Nested: the computer, the rigs and these). Returns {rows, lost}; `lost` = the rows that lost a child, deepest first.
  function lift(tops, state) {
    const composites = (state && state.composition && state.composition.composites) || [];
    const rows = [];
    const lost = new Set();
    for (const composite of composites) {
      if (composite.parent) continue;
      const taken = detach(tops, composite.path);
      if (!taken.row) continue;
      taken.lost.forEach((row) => lost.add(row));
      rows.push(taken.row);
    }
    return { rows, lost: [...lost] };
  }

  root.IOComposites = { nest, lift, detach, find };
})(typeof window !== 'undefined' ? window : globalThis);
