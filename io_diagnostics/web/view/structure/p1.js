// Structure p1 — "Devices, transport as columns" (docs/round2-brief-2026-10-22.md, P1; seam: docs/round2-seams.md).
//
// The tree holds only entities: an adapter holds its boards directly (fdcanusb · 272813EF › moteus-c1 · CAN 15), and
// cameras, capture cards and NICs are top-level siblings. The layers in between (USB bus, port, hub, tty / video /
// net interface, a plugin's CAN bus) are not rows; their physical path becomes a quiet attribute — the Table's
// "Connection" column and a line of the row tooltip (`USB 1-3 · 12 Mb/s · ttyACM0`, `PCIe 0000:01:00.0 · x1 of x4`).
// The "Ports" toggle (off by default) brings the layers back as rows: the physical tree, one click away.
// The root row is the computer itself (bam-GPU), the host entity: its detail is the Computer page.
//
// WHY a hidden layer's problem is folded into the entity it serves (Zach, 2026-10-22: "see at a glance at the
// entities BUT also provide the detailed information"): hiding a layer must never hide its report. A layer with an
// own problem is handed to core.js as a folded part of that entity, so its words land on the entity row's note
// ("272813EF CAN bus: error-passive · no ACK …") through the same status.part path a driver fault uses — the
// adapter's CAN bus, its tty, a port that serves exactly one device. A layer with a problem that serves no entity or
// several (an empty port that flaps, a hub) stays a row: there is no single entity to tell.
'use strict';
(function (root) {
  const V = root.IOView;

  // ------------------------------------------------------------------------ classification ----
  const TRANSPORT_KINDS = new Set(['host', 'bus', 'port', 'group']);
  const INTERFACE_ICONS = new Set(['tty', 'video', 'net']);
  const INTERFACE_OWNERS = new Set(['io_facts.tty', 'io_facts.v4l', 'io_facts.netdev']);

  // An OS interface node (/dev/ttyACM0, /dev/video0, enp6s0): an attribute of the device above it.
  const isInterface = (node) => node.kind === 'device' && (INTERFACE_ICONS.has(node.icon) || INTERFACE_OWNERS.has(node.owner));
  // A layer the tree hides: buses (OS and plugin), ports, groups, hubs and interfaces.
  const isLayer = (node) => TRANSPORT_KINDS.has(node.kind) || (node.kind === 'device' && node.icon === 'hub') || isInterface(node);
  // A composition part core.js folds (driver, motor, stream) — but not a layer a plugin declared part_of (the live
  // moteus plugin reports the adapter's CAN bus as part_of; F1-F3 say via): that one is walked like any layer.
  const isFoldedPart = (node) => node.edge === 'part_of' && !isLayer(node);

  // The children to walk: every child but the folded composition parts.
  const walkable = (node) => (node.children || []).filter((child) => !isFoldedPart(child));
  const countEntities = (node) => walkable(node).reduce((n, child) => n + (isLayer(child) ? countEntities(child) : 1), 0);

  // A layer handed to core.js as a folded part of the entity it serves: same entity, no children (what sits below
  // it is drawn as its own rows), part_of so partsOf() folds it.
  const asPart = (layer) => ({ ...layer, edge: 'part_of', folded: 'path', children: [] });

  // The node core.js sees for an entity row: its own composition parts, plus the hidden layers that report a problem.
  function seen(node, layers) {
    const parts = (node.children || []).filter(isFoldedPart);
    return { ...node, children: [...layers.map(asPart), ...parts] };
  }

  // ---------------------------------------------------------------------------- connection ----
  const SPEED = /\d\s*[kMG]?b\/s$/;
  const PCI_ADDRESS = /@([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7])/i;
  const PCI_WIDTH = /\bx\d+ of x\d+\b/;

  // "/dev/ttyACM0" -> "ttyACM0"; "/dev/video0 · capture" -> "video0"; a network interface keeps its whole name
  // ("enp6s0 · EtherCAT segment (not scanned)"). Consecutive videoN collapse to "video0–5".
  function interfaceText(interfaces) {
    const names = [];
    const videos = [];
    for (const node of interfaces) {
      const name = String(node.name || '');
      const dev = name.replace(/^\/dev\//, '');
      const video = /^video(\d+)(\s·\scapture)?$/.exec(dev);
      if (video) videos.push(Number(video[1]));
      else names.push(node.icon === 'net' ? name : dev.split(' · ')[0]);
    }
    videos.sort((a, b) => a - b);
    for (let at = 0; at < videos.length;) {
      let end = at;
      while (end + 1 < videos.length && videos[end + 1] === videos[end] + 1) end += 1;
      names.push(end > at ? `video${videos[at]}–${videos[end]}` : `video${videos[at]}`);
      at = end + 1;
    }
    return names;
  }

  // The physical path of one entity, from the layers between it and the entity (or host) above it.
  // trail = those raw layer nodes, outermost first.
  function connectionOf(node, trail) {
    const bits = [];
    const port = [...trail].reverse().find((layer) => layer.kind === 'port');
    const viaUsb = trail.some((layer) => layer.icon === 'usb' && layer.kind === 'bus');
    const pci = String(node.hardware_id || '').startsWith('pci:') ? PCI_ADDRESS.exec(node.hardware_id) : null;
    const pluginBus = [...trail].reverse().find((layer) => layer.kind === 'bus' && !(layer.depth <= 1));
    if (port) {
      const name = String(port.name || '');
      bits.push(viaUsb && name.startsWith('Port ') ? `USB ${name.slice(5)}` : name);
      // the USB generation, not the link speed; the device's own bcdUSB wins where it says more (its row's `usb`)
      const own = viaUsb && root.IOUsb && node.usb ? root.IOUsb.describe(node.usb) : null;
      if (own && own.text) bits.push(own.text);
      else if (SPEED.test(String(port.id_text || ''))) bits.push(root.IOUsb ? root.IOUsb.relabel(port.id_text) : port.id_text);
    } else if (pci) {
      bits.push(`PCIe ${pci[1]}`);
      const width = PCI_WIDTH.exec(String(node.message || ''));
      if (width) bits.push(width[0]);
    } else if (pluginBus) {
      bits.push(pluginBus.name);
    } else {
      const outer = [...trail].reverse().find((layer) => layer.kind !== 'host');
      if (outer) bits.push(outer.name);
    }
    bits.push(...interfaceText((node.children || []).filter(isInterface)));
    return bits.filter(Boolean).join(' · ');
  }

  function withConnection(row, connection) {
    row.columns = { ...row.columns, connection };
    if (connection) {
      const lines = String(row.title || row.label).split('\n');
      lines.splice(1, 0, connection);
      row.title = lines.join('\n');
    }
    return row;
  }

  // -------------------------------------------------------------------- devices (Ports off) ----
  // One entity row. trail = the hidden layers above it (for the connection), carried = the hidden layers above it
  // that report a problem and serve only this entity.
  function deviceRow(node, options, trail, carried) {
    const layers = [...carried];
    const children = [];
    for (const child of walkable(node)) {
      if (isLayer(child)) {
        // the device's own tty / video / net interface or its CAN bus: a problem there is this device's to show
        // (even when the bus serves several boards: it is the adapter's bus), what sits below are this row's children
        if (V.ownProblem(child)) layers.push(child);
        children.push(...lift(child, options, [child], []));
      } else {
        children.push(deviceRow(child, options, [], []));
      }
    }
    const row = V.entityRow(seen(node, layers), options, { node, children, defaultOpen: children.length > 0 });
    return withConnection(row, connectionOf(node, trail));
  }

  // The entity rows under a hidden layer, with the layer's path appended to their trail.
  function lift(layer, options, trail, carried) {
    const rows = [];
    for (const child of walkable(layer)) {
      if (!isLayer(child)) {
        rows.push(deviceRow(child, options, trail, carried));
        continue;
      }
      const below = countEntities(child);
      const problem = V.ownProblem(child);
      if (problem && below !== 1) {
        // nowhere single to surface it (an empty port that flaps, a hub feeding several): the layer stays a row
        const children = lift(child, options, [], []);
        const row = V.entityRow(seen(child, []), options, { node: child, children, defaultOpen: children.length > 0 });
        rows.push(withConnection(row, connectionOf(child, [...trail, child])));
        continue;
      }
      rows.push(...lift(child, options, [...trail, child], problem ? [...carried, child] : carried));
    }
    return rows;
  }

  // ---------------------------------------------------------------------- physical (Ports on) ----
  // The physical tree as p0 draws it, with one difference: a layer a plugin declared part_of (the live CAN bus) is a
  // row like the via one in the fixtures, so its boards stay rows. Entity rows keep the Connection column.
  // trail = the layers between this node and the entity (or host) above it, as in deviceRow.
  function physicalRow(node, options, trail) {
    const entity = !isLayer(node);
    const children = walkable(node).filter((child) => options.showEmpty || !V.isEmpty(child))
      .map((child) => physicalRow(child, options, entity || node.kind === 'host' ? [] : [...trail, node]));
    // WHY open whenever it has children (Zach, 2026-10-23: "by default it should be expanded so you see the things
    // inside"): the frame, every device and an adapter with its bus and boards
    const row = V.entityRow(seen(node, []), options, { node, children, defaultOpen: children.length > 0 });
    return entity ? withConnection(row, connectionOf(node, trail)) : row;
  }

  // ------------------------------------------------------------------------ the computer is the root ----
  // Zach, 2026-10-24: "the computer should be the BAM-GPU … as its own row". The root row IS the host entity, named as
  // the machine is (bam-GPU); selecting it opens the Computer page (CPU, memory, temperatures, its checks), and
  // everything plugged in hangs under it. The built-in PCI functions are ordinary children beside the RealSense and the
  // fdcanusbs: no driver claims them, so the page hides them by default (the Visibility control), not a second
  // "Computer" row or a "Built-in" group.
  function computerRow(host, children, key) {
    const row = V.entityRow(seen({ ...host, children: [] }, []), {}, { key, node: host, children, defaultOpen: true });
    // its own problem shows beside its name like any entity's ("CPU Tctl 92 °C: High Temperature"); its hint is the worst
    // anywhere below, rolled up with the same shape as every collapsed row
    if (V.ownProblem(host)) row.lead = String(host.message || '');
    return row;
  }

  // THE SEAM (orchestrator, 2026-10-24: "one entity interface for hardware and software, the hierarchy is a choice of view"):
  // a structure is a hierarchy builder, registered by id and picked in View options' Group by; `view` names which view of
  // the entities it draws. This one is the PHYSICAL view (computer -> ports -> devices): the only place that knows the
  // computer is the root row and that a port is a layer, so a System view (areas -> functions -> components, SOVD) or a
  // Software view (computer -> processes / nodes) plugs in beside it as another structure over the same entity rows,
  // the same detail tabs and the same health marks. Ports exist only here.
  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p1 = {
    view: 'physical',
    name: 'Devices, transport as columns',
    rootRow: true,
    description: 'The computer is the root row; below it only entities: an adapter holds its boards, cameras / capture cards / NICs are siblings. The physical path (USB 1-3 · 12 Mb/s · ttyACM0, PCIe 0000:01:00.0) is the Connection column and the row tooltip; a hidden layer\'s problem shows on the entity it serves. "Ports" brings the layers back as rows.',
    order: 1,
    toggles: [
      { key: 'showTransport', label: 'Ports', default: false, title: 'Show the transport layers (USB bus, port, hub, tty / video, CAN bus) as rows' },
    ],
    columns: [
      { key: 'connection', title: 'Connection', width: '240px', mono: true },
    ],
    rows(state, options) {
      const top = V.topLevel(state, { ...options, elideSingleHost: false });
      let rows;
      if (options.showTransport) {
        rows = top.nodes.filter((node) => options.showEmpty || !V.isEmpty(node)).map((node) => physicalRow(node, options, []));
      } else {
        // each host is a root row (usually exactly one: the machine itself), its entities below
        rows = top.nodes.map((host) => computerRow(host, lift(host, options, [], []), `p1:${host.path}`));
      }
      // a composite's leaf members (a board, a camera) come out of where they were drawn and go under it (docs/composition-v1.md)
      return root.IOComposites ? root.IOComposites.nest(rows, state) : rows; // core's search reads the Connection column too (row.columns), so "ttyACM0" finds the adapter
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
