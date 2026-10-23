// Structure p2 — "Two views, one toggle": Devices (by what they are for) ↔ Connections (the physical tree).
// Precedents: Windows Device Manager "Devices by type / Devices by connection"; ros2_medkit's Logical / Functional
// views. The brief: docs/round2-brief-2026-10-22.md (P2); the seam: docs/round2-seams.md §3.
//
// Devices (toggle off, the default): quiet folder rows Actuators · Cameras · Network · Computer, each listing the
// entities it holds. A CAN adapter is a folder of its boards (`fdcanusb · 272813EF` → `moteus-c1 · CAN 15`); the
// layers in between (the port above it, its tty, its CAN bus; a camera's /dev/video nodes) fold into the entity they
// serve, like a composition part, so their problems surface on it ("CAN bus: error-passive …" on the adapter).
// Connections (toggle on): p0's physical tree, single host elided, empty ports and buses behind "Empty ports".
// Both views key an entity's row by its entity path, so a selection survives the toggle.
'use strict';
(function (root) {
  const V = root.IOView;
  const isPart = (node) => node.edge === 'part_of';
  const STRUCTURAL = new Set(['host', 'bus', 'port', 'group']);
  // OS device nodes of the device above them: attributes of that entity, never entities of their own
  const INTERFACE_ICONS = new Set(['tty', 'video']);

  const GROUPS = [
    { id: 'actuators', label: 'Actuators', open: true },
    { id: 'cameras', label: 'Cameras', open: true },
    { id: 'network', label: 'Network', open: true },
    { id: 'computer', label: 'Computer', open: false },
  ];

  // ------------------------------------------------------------------------ Devices view ----
  // A layer folded into the entity it serves, drawn by core as a part (entityRow / partsOf). Only its own part_of
  // descendants come with it: entities below it (a CAN bus's boards) stay rows of their own.
  function asPart(node) {
    return { ...node, edge: 'part_of', children: (node.children || []).filter(isPart).map(asPart) };
  }

  // A field bus (a plugin's CAN bus under its adapter): it holds boards, not OS ports or devices.
  const isFieldBus = (node) => node.kind === 'bus' && !(node.children || []).some((child) => child.kind === 'device' || child.kind === 'port');
  const holdsBoards = (node) => isFieldBus(node) && (node.children || []).some((child) => child.kind === 'node');

  // What one entity is made of in the Devices view: the layers that fold into it, and the entities drawn below it.
  function anatomy(node, portAbove) {
    const folded = [];
    const below = [];
    const lift = (part) => { // a real part (the live frame's CAN bus is part_of) can carry entities: lift them out
      for (const child of part.children || []) (isPart(child) ? lift(child) : below.push(child));
    };
    for (const child of node.children || []) {
      if (isPart(child)) { folded.push(child); lift(child); } else if (child.kind === 'port') continue; // a hub's ports: walked as the physical frame
      else if (isFieldBus(child)) { folded.push(child); below.push(...(child.children || []).filter((grand) => !isPart(grand))); } else if (INTERFACE_ICONS.has(child.icon)) folded.push(child);
      else if (STRUCTURAL.has(child.kind)) continue;
      else below.push(child);
    }
    if (portAbove) folded.push(portAbove);
    return { folded, below };
  }

  function entityRowFor(node, options, portAbove, parts = anatomy(node, portAbove)) {
    const { folded, below } = parts;
    // WHY a copy with the layers re-edged part_of: core folds parts into their owner and surfaces the worst one's
    // own problem as status.part, so the structural layer's problem is drawn on the entity it serves, by core's
    // rule, never by hand. The copy only changes children; every API field is the node's own.
    const shaped = { ...node, children: folded.map(asPart) };
    const children = below.map((child) => entityRowFor(child, options, null));
    const row = V.entityRow(shaped, options, { children });
    row.defaultOpen = children.length > 0; // an adapter shows its boards, a NIC its links: they ARE the entities
    return row;
  }

  const NETWORK_CONTROLLER = /^network controller\b/i;
  const CAPTURE = /frame grabber|capture card/i;

  // What the entity is for (Device Manager's "by type"), from the API fields only. `parts` = anatomy(node): only the
  // entity's own boards count (a hub is not an actuator because an adapter hangs off it).
  function groupOf(node, parts) {
    if (node.kind === 'node' || node.kind === 'bus' || node.icon === 'adapter' || parts.below.some((child) => child.kind === 'node')) return 'actuators';
    if (node.icon === 'camera' || node.icon === 'webcam' || node.icon === 'video' || CAPTURE.test(`${node.name} ${node.message || ''}`)
      || (node.children || []).some((child) => !isPart(child) && child.icon === 'video')) return 'cameras';
    if (node.icon === 'net' || NETWORK_CONTROLLER.test(node.message || '') || (node.children || []).some((child) => !isPart(child) && child.icon === 'net')) return 'network';
    return 'computer';
  }

  // Walk the physical frame under one host: every entity once, in API order, with the port directly above it.
  // A structural layer that reports a problem of its own and serves no entity (an empty, flapping port) is kept as
  // a row in Computer, so its problem is still drawn somewhere.
  function entitiesUnder(host) {
    const found = [];
    // below an entity only its physical frame continues (a hub's ports); its other children are its anatomy
    const frameOf = (device) => ({ children: (device.children || []).filter((child) => !isPart(child) && STRUCTURAL.has(child.kind) && !isFieldBus(child)) });
    (function walk(node) {
      for (const child of node.children || []) {
        if (isPart(child)) continue;
        if (child.kind === 'port') {
          const devices = (child.children || []).filter((grand) => !isPart(grand) && !STRUCTURAL.has(grand.kind));
          for (const device of devices) found.push({ node: device, port: child });
          if (!devices.length && V.ownProblem(child)) found.push({ node: child, port: null, stray: true });
          for (const device of devices) walk(frameOf(device));
          walk({ children: (child.children || []).filter((grand) => !isPart(grand) && STRUCTURAL.has(grand.kind)) });
        } else if (holdsBoards(child)) {
          found.push({ node: child, port: null });       // a field bus with no adapter above it: its own folder
        } else if (STRUCTURAL.has(child.kind)) {
          if (V.ownProblem(child)) found.push({ node: child, port: null, stray: true });
          walk(child);
        } else {
          found.push({ node: child, port: null });
          walk(frameOf(child));
        }
      }
    })(host);
    return found;
  }

  function countText(groupId, rows) {
    const entities = (list) => list.reduce((sum, row) => sum + (row.entity ? 1 : 0) + entities(row.children), 0);
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    if (groupId === 'actuators') {
      const boards = entities(rows) - rows.length;
      return boards ? `${plural(rows.length, 'adapter')} · ${plural(boards, 'board')}` : plural(rows.length, 'adapter');
    }
    return plural(entities(rows), 'device'); // a capture card is not a camera, so no "N cameras"
  }

  const needsAttention = (row) => Boolean(row.status.own || row.status.part) || row.children.some(needsAttention);

  function devicesFor(host, options, keyPrefix) {
    const buckets = new Map(GROUPS.map((group) => [group.id, []]));
    for (const { node, port, stray } of entitiesUnder(host)) {
      if (stray) { buckets.get('computer').push(V.entityRow(node, options, { children: [] })); continue; }
      const parts = anatomy(node, port);
      buckets.get(groupOf(node, parts)).push(entityRowFor(node, options, port, parts));
    }
    return GROUPS.filter((group) => buckets.get(group.id).length).map((group) => {
      const children = buckets.get(group.id);
      const row = V.groupRow({
        key: `${keyPrefix}${group.id}`, label: group.label, icon: 'group', children,
        // Computer is the quiet drawer (hubs, controllers, keyboards): folded, unless something in it needs you
        defaultOpen: group.open || children.some(needsAttention),
      });
      row.idText = countText(group.id, children);
      return row;
    });
  }

  // --------------------------------------------------------------------- Connections view ----
  // p0's physical tree. One difference: a part that carries entities (the live frame's part_of CAN bus holding
  // CAN 1/14/15) lifts them out as rows of its owner, so every board is a row in both views (same key, selection
  // survives the toggle) and is never collected as a part of the adapter.
  function physicalDefaultOpen(node, row) {
    if (!row.children.length) return false;
    if (node.kind === 'host' || node.kind === 'group') return true;
    if (node.kind === 'bus' && node.depth <= 1) return true;
    if (node.kind === 'port') return true;
    return Boolean(row.status.hint);
  }

  function physicalRow(node, options) {
    const lifted = [];
    const lift = (part) => { for (const child of part.children || []) (isPart(child) ? lift(child) : lifted.push(child)); };
    if (!options.showParts) (node.children || []).filter(isPart).forEach(lift);
    const shaped = lifted.length ? { ...node, children: (node.children || []).map((child) => (isPart(child) ? asPart(child) : child)) } : node;
    const children = [...V.childrenOf(node, options), ...lifted]
      .filter((child) => options.showEmpty || !V.isEmpty(child)).map((child) => physicalRow(child, options));
    const row = V.entityRow(shaped, options, { children });
    // the lifted boards sit under a part's problem (the bus no-ACK), which core draws as status.part, not a hint
    row.defaultOpen = physicalDefaultOpen(node, row) || Boolean(lifted.length && row.status.part);
    return row;
  }

  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p2 = {
    name: 'Two views, one toggle',
    description: 'Devices (default): Actuators · Cameras · Network · Computer, each CAN adapter a folder of its boards, the layers between folded into the entity they serve. Connections: the physical USB / PCIe tree. Like Device Manager\'s "by type / by connection".',
    order: 2,
    toggles: [
      { key: 'connections', label: 'Connections', default: false, title: 'Off: devices by what they are for (Actuators, Cameras, Network, Computer). On: the physical tree, as connected (USB / PCIe ports and buses)' },
      { key: 'showEmpty', label: 'Empty ports', default: false, title: 'Connections view: show empty USB ports and buses with nothing on them' },
    ],
    rows(state, options) {
      const top = V.topLevel(state, options);
      if (options.connections) {
        return top.nodes.filter((node) => options.showEmpty || !V.isEmpty(node)).map((node) => physicalRow(node, options));
      }
      if (top.elided) return devicesFor(top.hosts[0], options, 'p2:');
      // several hosts (or elision off): a host row each, its groups below
      return top.nodes.map((host) => {
        const row = V.entityRow({ ...host, children: (host.children || []).filter(isPart) }, options, { children: devicesFor(host, options, `p2:${host.path}:`) });
        row.defaultOpen = true;
        return row;
      });
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
