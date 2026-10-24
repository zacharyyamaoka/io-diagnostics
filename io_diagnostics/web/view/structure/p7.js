// Structure p7 — "Type": the top-level entities grouped by what kind they are (Zach, 2026-10-24: "if you group by type ... that is
// where we can have like a group of computers, Cells, etc ...").
//
// Built on p6 (Nested): the computers and rigs stay top-level; the devices that hang directly under them (a rig's adapters and
// cameras, the computer's own adapters, capture cards, NICs) are lifted to the top level too, and the top level is then sorted into
// one folder per kind: Computers, Rigs, Adapters, Cameras, Network ... Each device keeps everything below it (an adapter its boards),
// so only the first level moves. A folder is a structure's own grouping row (no entity); it rolls up the worst of its members.
// The kinds are read from the rows (kind / icon), never listed per device: a new kind of entity adds its own folder.
//
// WHY the folder list is derived, not fixed (Zach: "I don't think we should separate in the computer rigs, etc" by default): the
// default tree (p6) has no sections at all; here, and only here, the person asked for them.
'use strict';
(function (root) {
  const V = root.IOView;

  // [folder title, in the order folders are listed]: a title not listed here sorts after these, by name
  const ORDER = ['Computers', 'Rigs', 'Adapters', 'Cameras', 'Network', 'PCIe devices', 'USB devices'];
  const BY_KIND = { host: 'Computers', rig: 'Rigs', bus: 'Buses', port: 'Ports', group: 'Groups' };
  const BY_ICON = { adapter: 'Adapters', camera: 'Cameras', webcam: 'Cameras', net: 'Network', pci: 'PCIe devices', usb: 'USB devices',
    tty: 'Serial ports', video: 'Video devices', hid: 'Input devices', servo: 'Actuators', motor: 'Actuators', hub: 'Hubs' };
  const OPEN_BY_DEFAULT = new Set(['Computers', 'Rigs', 'Adapters', 'Cameras']);

  const typeOf = (row) => BY_KIND[row.kind] || BY_ICON[row.icon] || 'Other';
  const slug = (title) => title.toLowerCase().replace(/[^a-z0-9]+/g, '-');

  function byType(state, options) {
    const tops = root.IOStructures.p6.rows(state, options);
    const lifted = [];
    for (const top of tops) {
      lifted.push(top);
      if (top.kind === 'host' || top.kind === 'rig') {
        lifted.push(...top.children);
        top.children = [];
        top.defaultOpen = false;
        V.setRollup(top);
      }
    }
    const folders = new Map();
    for (const row of lifted) {
      const title = typeOf(row);
      if (!folders.has(title)) folders.set(title, []);
      folders.get(title).push(row);
    }
    const rank = (title) => (ORDER.includes(title) ? ORDER.indexOf(title) : ORDER.length);
    return [...folders.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)).map((title) => V.groupRow({
      key: `type:${slug(title)}`, label: title, icon: 'group', children: folders.get(title), defaultOpen: OPEN_BY_DEFAULT.has(title),
    }));
  }

  root.IOStructures = root.IOStructures || {};
  root.IOStructures.p7 = {
    view: 'type',
    name: 'Type',
    rootRow: true,
    description: 'Folders by kind: Computers, Rigs, Adapters, Cameras, Network ... The devices under a computer or a rig are lifted to the top level and grouped; each keeps what is below it.',
    order: 7,
    get toggles() { return root.IOStructures.p1.toggles; },
    get columns() { return root.IOStructures.p1.columns; },
    rows: byType,
  };
})(typeof window !== 'undefined' ? window : globalThis);
