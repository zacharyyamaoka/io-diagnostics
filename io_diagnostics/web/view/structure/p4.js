// Structure p4 — "Compact folders": the physical tree with its pass-through nodes folded away, the way VS Code's
// `explorer.compactFolders` joins a folder that holds one folder into a single row.
//   - a single-child chain of pass-throughs (bus, port, hub, group) joins into its last node's row: the entity's own
//     label leads, the folded path follows as muted text (`fdcanusb · 272813EF   /dev/ttyACM0 · Port 1-3`)
//   - a device's one bus (an adapter's CAN bus) folds into the device, so the boards sit directly under it
//   - a device's OS nodes (tty, video) are attributes: the "Device nodes" column and the muted text, not rows
//   - empty buses and ports hide behind "Empty ports" (off here); the single host is elided (core.js)
// A folded node is handed to core.js as one of the row's folded parts, so its OWN problem (a CAN bus error-passive,
// a port's enumeration flaps) is drawn on the joined row with its own level and words, `byEntity` maps it to that
// row, and the detail panel lists it one click from its own detail. Levels and hints stay the API's (entityRow).
'use strict';
(function (root) {
  const V = root.IOView;

  // The nodes that only carry what is below them. A hub is a device to the OS but a pass-through to the reader.
  const isPassThrough = (node) => node.kind === 'port' || node.kind === 'bus' || node.kind === 'group' || node.icon === 'hub';
  // A slot is hidden while empty: nothing visible below it and no problem of its own.
  const isSlot = (node) => node.kind === 'port' || node.kind === 'bus' || node.kind === 'group';
  const ATTRIBUTE_ICONS = new Set(['tty', 'video']);

  // One API node's children, sorted into what this structure does with them: `lifted` is the device's one bus (the
  // bus folds, its members become the device's children), `attrs` are its tty / video nodes, `kids` stay rows.
  // Composition parts (edge part_of) are left out: entityRow folds them. A part_of bus is still lifted (the live rack
  // reports the CAN bus as part_of its adapter), otherwise its boards would fold away as parts.
  function sort(node) {
    const raw = node.children || [];
    const buses = node.kind === 'device' ? raw.filter((child) => child.kind === 'bus') : [];
    const lifted = buses.length === 1 ? buses[0] : null; // two buses on one device keep their own rows
    const attrs = [];
    const kids = [];
    for (const child of raw) {
      if (child === lifted) kids.push(...V.childrenOf(child));
      else if (child.edge === 'part_of') continue;
      else if (node.kind === 'device' && ATTRIBUTE_ICONS.has(child.icon)) attrs.push(child);
      else kids.push(child);
    }
    return { lifted, attrs, kids };
  }

  // The compacted tree: {node, chain (folded ancestors, top first), lifted, attrs, children}, or null when hidden.
  function compact(node, options) {
    const { lifted, attrs, kids } = sort(node);
    const children = kids.map((kid) => compact(kid, options)).filter(Boolean);
    if (!options.showEmpty && isSlot(node) && !children.length && !V.ownProblem(node)) return null;
    if (isPassThrough(node) && children.length === 1 && !lifted && !attrs.length) {
      children[0].chain.unshift(node);
      return children[0];
    }
    return { node, chain: [], lifted, attrs, children };
  }

  // A folded node as core.js's folded part: edge part_of, without the children that are rows elsewhere.
  const asPart = (node) => Object.assign({}, node, { edge: 'part_of', children: (node.children || []).filter((child) => child.edge === 'part_of') });

  // "/dev/video0 · capture", "/dev/video1" … "/dev/video5" -> "/dev/video0–5"; anything else verbatim.
  function nodesText(attrs) {
    const runs = [];
    for (const attr of attrs) {
      const match = /^(\/dev\/[A-Za-z]+)(\d+)(?:\s·\s.*)?$/.exec(attr.name);
      const last = runs[runs.length - 1];
      if (match && last && last.prefix === match[1] && Number(match[2]) === last.to + 1) last.to += 1;
      else runs.push(match ? { prefix: match[1], from: Number(match[2]), to: Number(match[2]) } : { text: attr.name });
    }
    return runs.map((run) => run.text || (run.from === run.to ? `${run.prefix}${run.from}` : `${run.prefix}${run.from}–${run.to}`)).join(', ');
  }

  function rowOf(view, options) {
    const node = view.node;
    const folded = [...view.chain, ...(view.lifted ? [view.lifted] : []), ...view.attrs];
    const own = (node.children || []).filter((child) => child.edge === 'part_of' && child !== view.lifted);
    const shown = Object.assign({}, node, { children: [...folded.map(asPart), ...own] });
    const children = view.children.map((child) => rowOf(child, options));
    const path = view.chain.map((link) => link.name).join(' › ');
    const attrs = nodesText(view.attrs);
    const row = V.entityRow(shown, options, { children, node, columns: { nodes: attrs } });
    // WHY the folded path and the device nodes ride in idText: it is the one muted slot the tree and the Table both
    // draw beside the label (docs/round2-seams.md has no `via` field yet; see the p4 lane report). The device node
    // goes first: the slot ellipsizes its end, and /dev/ttyACM0 is what a script opens.
    row.idText = [attrs, path, row.idText].filter(Boolean).join(' · ');
    row.title = [row.title, path ? `folded: ${path}` : '', view.lifted ? `bus folded in: ${view.lifted.name}` : '', attrs ? `device nodes: ${view.attrs.map((attr) => attr.name).join(', ')}` : ''].filter(Boolean).join('\n');
    // pass-throughs that kept a row (they branch) and an adapter's boards are open; a quiet device stays folded
    row.defaultOpen = children.length > 0 && (isPassThrough(node) || Boolean(view.lifted) || Boolean(row.status.hint));
    return row;
  }

  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p4 = {
    name: 'Compact folders',
    description: 'The physical tree with pass-throughs folded (VS Code compact folders): a single-child chain joins into one row with its path muted, an adapter\'s CAN bus folds into the adapter, tty / video nodes become attributes, one host elided.',
    order: 4,
    toggles: [
      { key: 'showEmpty', label: 'Empty ports', default: false, title: 'Show empty USB ports and buses with nothing on them' },
    ],
    columns: [
      // narrow: the Table's columns are fixed-width and the Message column takes only what is left
      { key: 'nodes', title: 'Device nodes', width: '120px' },
    ],
    rows(state, options) {
      // parts as rows would put the folded nodes back as rows beside their joined row: this structure keeps them folded
      const opts = Object.assign({}, options, { showParts: false });
      return V.topLevel(state, opts).nodes.map((node) => compact(node, opts)).filter(Boolean).map((view) => rowOf(view, opts));
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
