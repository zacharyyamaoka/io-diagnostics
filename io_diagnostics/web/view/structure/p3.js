// Structure p3 — "Path on demand": the entity tree (an adapter holds its boards directly, cameras and the other
// devices are its siblings, the single host is elided); the connection path the physical layers used to draw
// (USB bus › port › hub › tty › CAN bus) is one step away instead: the row's tooltip, the Table's Path column, the
// "Paths" toggle (a muted suffix on every row) and the detail panel (entity path + the folded layers as its parts).
// Shared behaviour comes from view/core.js (docs/round2-seams.md): `model · id` labels, parts folded into their
// owner, single-host elision, search, and the black-box rule.
'use strict';
(function (root) {
  const V = root.IOView;
  const SEP = ' › ';
  const HANDLE_ICONS = new Set(['tty', 'video', 'net']); // the OS's handle for a device (/dev/ttyACM0, /dev/video0, enp6s0)
  const CONDUIT_ICONS = new Set(['tty', 'net']);          // a handle the device's own bus is reached through

  // A layer is transport, not an entity: buses (OS, CAN, PCI), ports, groups, hubs, and an entity's OS handles.
  // A handle with no entity above it (/dev/ttyS1 under "On-board serial ports") IS the entity.
  function isLayer(node, owner) {
    if (node.kind === 'bus' || node.kind === 'port' || node.kind === 'group') return true;
    if (node.kind === 'device' && node.icon === 'hub') return true;
    return Boolean(owner) && node.kind === 'device' && HANDLE_ICONS.has(node.icon);
  }

  // A composition part (driver, motor, stream) stays core's to fold; a part_of BUS is a layer (its nodes are rows).
  const isCompositionPart = (node) => node.edge === 'part_of' && node.kind !== 'bus';

  const item = (node, layerRow = false) => ({ node, layerRow, up: [], down: [], via: [], children: [] });

  // API children -> entity items. Under an entity (`owner`) every layer folds into it and its entities become the
  // owner's children; above any entity a layer folds into the one entity it serves. Problems on hidden layers so
  // surface on the entity they serve (as core's status.part); a layer serving no entity, or several, that reports a
  // problem of its own stays a row (it is then the thing to act on: an empty flapping port, a failing hub).
  function walk(nodes, owner) {
    const out = [];
    for (const node of nodes || []) {
      if (isCompositionPart(node)) continue;
      if (!isLayer(node, owner)) {
        const entity = item(node);
        entity.children = walk(node.children, entity);
        out.push(entity);
        continue;
      }
      const below = walk(node.children, owner);
      if (owner) {
        owner.down.push(node);
        below.forEach((entity) => entity.via.unshift(node));
        out.push(...below);
      } else if (below.length === 1 && !(below[0].layerRow && V.ownProblem(node))) {
        below[0].up.unshift(node);
        below[0].via.unshift(node);
        out.push(below[0]);
      } else if (V.ownProblem(node)) {
        const layer = item(node, true);
        layer.children = below;
        out.push(layer);
      } else {
        below.forEach((entity) => entity.via.unshift(node));
        out.push(...below);
      }
    }
    return out;
  }

  // "/dev/video0 · capture", "/dev/video1", … -> "/dev/video0–5"; anything else is listed verbatim.
  function handleText(handles) {
    const numbers = handles.map((node) => (String(node.name).match(/^\/dev\/video(\d+)(?: · .*)?$/) || [])[1]);
    if (handles.length > 1 && numbers.every(Boolean)) {
      const sorted = [...new Set(numbers.map(Number))].sort((a, b) => a - b);
      const contiguous = sorted.every((n, i) => !i || n === sorted[i - 1] + 1);
      return contiguous ? `/dev/video${sorted[0]}–${sorted[sorted.length - 1]}` : `/dev/video${sorted.join(', ')}`;
    }
    return handles.map((node) => node.name).join(', ');
  }

  // The entity's API node with its folded layers added as part_of children (each without its own children, which are
  // rows), so core's entityRow lists them in row.parts, draws the worst one as status.part, maps their entity paths
  // to this row and lets search find the row by them. Nothing else of the API node changes.
  function withLayers(entity) {
    const layers = [...entity.up, ...entity.down];
    if (!layers.length) return entity.node;
    const flat = (layer) => ({ ...layer, edge: 'part_of', children: (layer.children || []).filter(isCompositionPart) });
    const parts = (entity.node.children || []).filter(isCompositionPart);
    return { ...entity.node, children: [...parts, ...layers.map(flat)] };
  }

  const segment = (node) => ({ name: node.name, kind: node.kind, depth: node.depth });
  const joined = (segments) => segments.map((s) => s.name).join(SEP);

  // The short form the column and the suffix show: a port's name already says its bus and hub chain ("Port 7-1.1.1"),
  // so the path starts at the deepest port; without a port, an OS bus that leads to something more specific goes.
  // The full path (host first) stays in the tooltip.
  function compact(segments) {
    let last = -1;
    segments.forEach((s, i) => { if (s.kind === 'port') last = i; });
    if (last >= 0) return segments.slice(last);
    let at = 0;
    while (at < segments.length - 1 && segments[at].kind === 'bus' && segments[at].depth <= 1) at += 1;
    return segments.slice(at);
  }

  // conduit = the path segments (host first) a child of the parent row continues from: the parent's own path plus
  // the handle its bus is reached through (an adapter's /dev/ttyACM0 before its CAN bus), or a layer row itself.
  function rowFor(entity, conduit, options) {
    const own = entity.down.filter((layer) => HANDLE_ICONS.has(layer.icon) && entity.node.children.includes(layer));
    const via = entity.via.map(segment);
    const handles = own.length ? [{ name: handleText(own), kind: 'handle', depth: null }] : [];
    const full = [...conduit, ...via, ...handles];
    const next = entity.layerRow ? [...conduit, ...via, segment(entity.node)]
      : [...conduit, ...via, ...own.filter((layer) => CONDUIT_ICONS.has(layer.icon)).map(segment)];
    // the column: where it is, read on its own (no host: it is the tree's root or elided); the suffix: only what is
    // hidden between this row and the row above it (a board under its adapter: "272813EF CAN bus")
    const suffix = joined(compact([...via, ...handles]));
    const children = entity.children.map((child) => rowFor(child, next, options));
    const row = V.entityRow(withLayers(entity), options, {
      children,
      columns: { path: joined(compact(full.slice(1))) },
      // WHY open: Zach reads "fdcanusb → moteus-c1 · CAN 15, moteus-c1 · CAN 14" — an adapter shows its boards
      defaultOpen: children.length > 0,
    });
    row.node = entity.node; // the raw API node, as every structure hands it to the renderers
    // the path on demand: the tooltip's second line, and with "Paths" the muted suffix (the id stays in the tooltip)
    if (full.length > 1) {
      const lines = row.title.split('\n');
      lines.splice(1, 0, joined(full));
      row.title = lines.join('\n');
    }
    if (options.showPaths && suffix) row.idText = suffix;
    return row;
  }

  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p3 = {
    name: 'Path on demand',
    description: 'Entities only: an adapter holds its boards, cameras and other devices are siblings, one host elided. The connection path (USB bus › port › tty › CAN bus) is in the tooltip, the Table\'s Path column, the "Paths" suffix and the detail panel; a hidden layer\'s problem shows on the entity it serves.',
    order: 3,
    toggles: [
      { key: 'showPaths', label: 'Paths', default: false, title: 'Show each row\'s connection path (USB bus › port › tty › CAN bus) as a muted suffix' },
    ],
    columns: [
      { key: 'path', title: 'Path', width: '240px' },
    ],
    rows(state, options) {
      const top = V.topLevel(state, options);
      if (top.elided) return walk(top.nodes, null).map((entity) => rowFor(entity, [segment(top.hosts[0])], options));
      // several hosts: each is a row, its entities below it, every tooltip path opening with its host's name
      return top.nodes.map((host) => {
        const children = walk(host.children, null).map((entity) => rowFor(entity, [segment(host)], options));
        return V.entityRow(host, options, { children, columns: { path: '' }, defaultOpen: true });
      });
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
