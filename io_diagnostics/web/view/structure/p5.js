// STUB (orchestrator, 2026-10-22): a copy of p0 until the p5 lane replaces this file.
// Structure p0 — "Physical (today)": the v1 physical tree (USB bus › port › device › tty / CAN bus › node) drawn
// through the round 2 seam (docs/round2-seams.md), so nothing regresses while P1..P5 land beside it.
// Shared behaviour comes from view/core.js: `model · id` labels, parts folded into their owner, single-host elision,
// search, and the black-box rule.
'use strict';
(function (root) {
  const V = root.IOView;

  // The expansion the v1 page had: the frame (groups, OS buses, ports) open; a device, node or plugin bus opens only
  // when something below it needs attention, so quiet branches stay folded.
  function defaultOpen(node, row) {
    if (!row.children.length) return false;
    if (node.kind === 'host' || node.kind === 'group') return true;
    if (node.kind === 'bus' && node.depth <= 1) return true; // an OS bus (api depth 1, under the host)
    if (node.kind === 'port') return true;                   // a port is "where"; its device is the row that matters
    return Boolean(row.status.hint);
  }

  function rowFor(node, options) {
    const children = V.childrenOf(node, options).filter((child) => options.showEmpty || !V.isEmpty(child)).map((child) => rowFor(child, options));
    const row = V.entityRow(node, options, { children });
    row.defaultOpen = defaultOpen(node, row);
    return row;
  }

  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p5 = {
    name: 'Bus lens (stub: p0 copy)',
    description: 'The physical tree as v1 drew it (USB bus › port › device › tty / CAN bus › node), with the round 2 rules: model · id labels, parts folded into their owner, one host elided.',
    order: 5,
    toggles: [
      { key: 'showParts', label: 'Parts as rows', default: false, title: 'Draw driver / motor / stream parts as their own rows (v1) instead of on their owner' },
      { key: 'showEmpty', label: 'Empty ports', default: true, title: 'Show empty USB ports and buses with nothing on them' },
    ],
    rows(state, options) {
      return V.topLevel(state, options).nodes.filter((node) => options.showEmpty || !V.isEmpty(node)).map((node) => rowFor(node, options));
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
