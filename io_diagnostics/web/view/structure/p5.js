// Structure p5 — "Bus lens": the top level is the communication buses the robot cares about, each listing its nodes
// (CAN · fdcanusb 272813EF › moteus-c1 · CAN 1 / 14 / 15; USB cameras; PCIe capture; Ethernet / EtherCAT). The
// computer's own topology (USB buses, ports, hubs, the ITE device …) folds into ONE last row, "Computer", which
// opens to the physical tree. Contract: docs/round2-seams.md; proposal: docs/round2-brief-2026-10-22.md (P5).
// Shared behaviour comes from view/core.js: `model · id` labels, parts folded into their owner, single-host elision,
// search, and the black-box rule.
'use strict';
(function (root) {
  const V = root.IOView;

  // Every API node under `top`, all edges (a plugin's bus may hang off its adapter as via OR part_of), with its parent.
  function walk(top) {
    const nodes = [];
    const parentOf = new Map();
    (function visit(node) {
      nodes.push(node);
      for (const child of node.children || []) {
        parentOf.set(child.path, node);
        visit(child);
      }
    })(top);
    return { nodes, parentOf };
  }

  function ancestors(node, parentOf) {
    const out = [];
    for (let at = parentOf.get(node.path); at; at = parentOf.get(at.path)) out.push(at);
    return out;
  }

  // A plugin's field bus: a `bus` hanging off a device (the fdcanusb), not an OS bus under the host.
  const isFieldBus = (node, parentOf) => node.kind === 'bus' && (parentOf.get(node.path) || {}).kind === 'device';
  const onUsb = (node, parentOf) => ancestors(node, parentOf).some((up) => up.kind === 'bus' && up.icon === 'usb');
  // WHY a text match: the state node carries no PCI class or capture-card flag (the host's `likely_capture_card` stays
  // in its checks), so the name / model / message words and the known frame-grabber vendors are what the page has.
  const CAPTURE_WORDS = /frame.?grabber|capture|grabber/i;
  const CAPTURE_VENDORS = /coreco|teledyne|dalsa|matrox|euresys|bitflow|active silicon|silicon software|basler|magewell|datapath|blackmagic|aja video|epiphan/i;
  const isCapture = (node) => node.kind === 'device' && node.icon === 'pci'
    && (CAPTURE_WORDS.test(`${node.name} ${(node.identity || {}).model || ''} ${node.message || ''}`) || CAPTURE_VENDORS.test((node.identity || {}).manufacturer || ''));
  // WHY no Wi-Fi: `wl*` is the computer's own uplink (Linux predictable names), never a robot segment.
  const isWiredNic = (node) => node.icon === 'net' && !/^wl/.test(String(node.name || ''));

  // "272813EF CAN bus" + the fdcanusb → `CAN · fdcanusb 272813EF` (protocol · adapter), the way Zach names the bus.
  function busRow(bus, adapter, options) {
    const adapterLabel = adapter ? V.labelOf(adapter) : null;
    const serial = String((adapter && (adapter.identity || {}).serial_number) || '').trim();
    const protocol = String(bus.name || '').replace(serial, '').replace(/\bbus\b/i, '').trim() || 'Bus';
    const ident = adapterLabel && adapterLabel.model ? `${adapterLabel.model} ${adapterLabel.ident}` : (adapter ? adapter.name : bus.name);
    const members = V.childrenOf(bus, options).map((node) => V.entityRow(node, options));
    // the adapter is the computer's own node on the bus: its USB-side report (e.g. "port held by tview") sits here.
    // WHY showParts for it when the bus is its part_of child (the live moteus plugin): the bus and its boards are
    // drawn as rows here, so folding them into the adapter as "parts" would draw the same cause twice.
    const adapterRows = adapter ? [V.entityRow(adapter, bus.edge === 'part_of' ? { ...options, showParts: true } : options)] : [];
    const row = V.entityRow(bus, options, { children: [...adapterRows, ...members], defaultOpen: true });
    const label = `${protocol} · ${ident}`;
    // WHY the bus's own message in the id slot: when boards don't answer, the bus's own words ("error-passive · no
    // ACK: no powered node on this bus") are the first thing Zach needs, and the seam has no other inline text slot
    // (status.note is the folded part's). A healthy bus says how many nodes it carries, like a USB bus's "2 devices".
    const nodes = members.length;
    return Object.assign(row, {
      label, model: protocol, ident,
      idText: row.status.own ? String(bus.message || '') : `${nodes} node${nodes === 1 ? '' : 's'}`,
      title: `${label}\n${row.title}`,
    });
  }

  function group(key, label, icon, children, noun) {
    const row = V.groupRow({ key: `bus-lens/${key}`, label, icon, children, defaultOpen: true });
    return Object.assign(row, { idText: `${children.length} ${noun}${children.length === 1 ? '' : 's'}` });
  }

  // The physical tree under "Computer", as p0 draws it (open frame, quiet branches folded).
  function physicalRow(node, options) {
    const children = V.childrenOf(node, options).filter((child) => options.showEmpty || !V.isEmpty(child)).map((child) => physicalRow(child, options));
    const row = V.entityRow(node, options, { children });
    if (!children.length) row.defaultOpen = false;
    else if (node.kind === 'group' || (node.kind === 'bus' && node.depth <= 1) || node.kind === 'port') row.defaultOpen = true;
    else row.defaultOpen = Boolean(row.status.hint);
    return row;
  }

  // One host's lens: its buses first, then the one "Computer" row. Empty bus groups are not drawn.
  function lens(host, options) {
    const { nodes, parentOf } = walk(host);
    const rows = [];
    for (const bus of nodes.filter((node) => isFieldBus(node, parentOf))) rows.push(busRow(bus, parentOf.get(bus.path), options));
    const cameras = nodes.filter((node) => node.kind === 'device' && node.icon === 'camera' && onUsb(node, parentOf));
    if (cameras.length) rows.push(group('usb-cameras', 'USB cameras', 'camera', cameras.map((node) => V.entityRow(node, options)), 'camera'));
    const capture = nodes.filter(isCapture);
    if (capture.length) rows.push(group('pcie', 'PCIe', 'pci', capture.map((node) => V.entityRow(node, options)), 'card'));
    const nics = nodes.filter(isWiredNic);
    if (nics.length) rows.push(group('ethernet', 'Ethernet / EtherCAT', 'net', nics.map((node) => V.entityRow(node, options)), 'link'));
    const physical = V.childrenOf(host, options).filter((child) => options.showEmpty || !V.isEmpty(child)).map((child) => physicalRow(child, options));
    // WHY closed: the topology is the detail one click deeper; the buses above are the answer. It repeats the bus
    // members at their physical place (core suffixes the duplicate keys; selection resolves to the bus row).
    if (physical.length) rows.push(Object.assign(V.groupRow({ key: 'bus-lens/computer', label: 'Computer', icon: 'host', children: physical, defaultOpen: false }),
      { idText: host.id_text || '' }));
    return rows;
  }

  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p5 = {
    name: 'Bus lens',
    description: 'The buses the robot talks over at the top (CAN per adapter, USB cameras, PCIe capture, Ethernet / EtherCAT), each listing its nodes; the computer\'s own USB / PCIe topology folds into one "Computer" row.',
    order: 5,
    toggles: [
      { key: 'showEmpty', label: 'Empty ports', default: false, title: 'Under Computer: show empty USB ports and buses with nothing on them' },
    ],
    rows(state, options) {
      const top = V.topLevel(state, { ...options, showParts: true });
      if (top.elided) return lens(top.hosts[0], options);
      return top.hosts.map((host) => V.entityRow(host, options, { children: lens(host, options), defaultOpen: true }));
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
