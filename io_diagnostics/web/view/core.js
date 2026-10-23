// IO diagnostics view model (round 2 foundation; the contract is docs/round2-seams.md).
//
// API state (GET /api/v1/state, docs/api-v1.md) -> display rows. The tree renderers that declare
// consumes: 'rows' and the page's table draw THESE rows, never the raw API tree: a structure
// (window.IOStructures[id]) decides what the rows are, a renderer only decides how they look.
//
// What this file does for every structure, the same way:
//   - entity labels `model · id`                                     -> labelOf()
//   - composition parts (edge part_of) folded into their owner row:  -> partsOf(), entityRow()
//     the worst part problem surfaces on the owner as status.part ("driver: fault 33 …"), the parts are listed
//     in row.parts (the detail panel shows them)
//   - single-host elision: one host -> its children are the top level -> topLevel()
//   - search: matching rows plus their ancestors                     -> applyQuery()
//   - the black-box rule, unchanged: a row's own level is the API's self-report; the worst-below hint is the API's
//     `hint` (a grouping row's `level`, which is its rollup), passed through and never recomputed -> hintOf()
//
// Loads in the page (window.IOView) and in Node (module.exports) so tests/test_view_core.js runs without a browser.
'use strict';
(function (root) {
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const DEFAULT_OPTIONS = { query: '', showTransport: false, showEmpty: true, showParts: false, elideSingleHost: true };
  const rank = (level) => (level in RANK ? RANK[level] : -1);

  // ------------------------------------------------------------------------- black box ----
  const isGrouping = (node) => !node || !node.own || node.own.level === null || node.own.level === undefined;
  const ownProblem = (node) => (!isGrouping(node) && PROBLEM.has(node.level) ? node.level : null);

  // The API's "something below is worse" hint, as one shape: a self-reporting row's `hint`; a pure grouping row has
  // no self-report, so its `level` (= its rollup, api-v1 "level") is only a hint too.
  function hintOf(node) {
    const rollup = node.rollup || {};
    if (isGrouping(node)) {
      const level = node.level || rollup.level;
      if (!PROBLEM.has(level)) return null;
      return { level, origin: rollup.origin || null, origin_name: rollup.origin_name || '', message: rollup.message || node.message || '' };
    }
    if (!node.hint || !PROBLEM.has(node.hint.level)) return null;
    return { level: node.hint.level, origin: node.hint.origin || null, origin_name: rollup.origin_name || '', message: node.hint.message || '' };
  }

  // ---------------------------------------------------------------------------- labels ----
  // `model · id` for identified hardware (a serial number, or a plugin's bus node): "fdcanusb · 272813EF",
  // "moteus-c1 · CAN 15", "RealSense D405 · 6036". Everything else keeps its API name verbatim.
  // WHY the model is not prettified ("moteus-c1", not "Moteus c1"): stored text renders as authored
  // (feedback_truthful_property_rendering); the full API name stays in the tooltip and the detail panel.
  function labelOf(node) {
    const identity = node.identity || {};
    const name = String(node.name || '');
    const model = String(identity.model || '').trim();
    const serial = String(identity.serial_number || '').trim();
    if (!model || !(serial || node.kind === 'node')) return { label: name, model: '', ident: '' };
    let ident;
    if (name === model) ident = serial;
    else if (name.toLowerCase().startsWith(model.toLowerCase())) ident = name.slice(model.length);
    else {
      // drop the leading words of the name that the model already says ("D405" of "RealSense D405")
      const modelWords = new Set(model.toLowerCase().split(/[\s\-_/]+/).filter(Boolean));
      const words = name.split(/\s+/);
      let at = 0;
      while (at < words.length - 1 && modelWords.has(words[at].toLowerCase())) at += 1;
      ident = words.slice(at).join(' ');
    }
    ident = ident.replace(/^[\s·:,\-]+/, '').trim();
    // a parenthesis that wraps the whole id is punctuation of the name, not part of the id: the live RealSense name
    // "RealSense D405 (USB 219323070276)" reads "RealSense D405 · USB 219323070276" (the full name stays in the tooltip)
    const wrapped = /^\(([^()]*)\)$/.exec(ident);
    if (wrapped) ident = wrapped[1].trim();
    ident = ident || serial;
    if (!ident) return { label: name, model: '', ident: '' };
    return { label: `${model} · ${ident}`, model, ident };
  }

  // A part's name relative to its owner: "CAN 15 driver" under "CAN 15" -> "driver".
  function partLabel(part, owner) {
    const name = String(part.name || '');
    const prefix = `${owner.name} `;
    return name.startsWith(prefix) && name.length > prefix.length ? name.slice(prefix.length) : name;
  }

  // ----------------------------------------------------------------------------- parts ----
  const isPart = (node) => node.edge === 'part_of';

  // Every composition part under an owner (a part's own parts fold with it), in API order.
  // WHY never a part's `via` children: the live moteus plugin reports the CAN bus as part_of its adapter and the
  // boards as via the bus; the boards are entities (rows, lifted by childrenOf), never parts of the adapter.
  // `folded: 'path'` marks a transport layer a structure folded in (node.layer / node.folded set on the copy it hands
  // in), so the detail card can say "Connection (folded)" rather than "composition (part_of)".
  function partsOf(node) {
    const parts = [];
    (function collect(owner, children) {
      for (const child of children || []) {
        if (!isPart(child)) continue;
        parts.push({
          entity: child.path,
          name: child.name,
          label: partLabel(child, node),
          icon: child.icon,
          kind: child.kind,
          level: child.level,
          message: child.message || '',
          own: child.own,
          problem: ownProblem(child),
          knock_on_of: child.knock_on_of || null,
          connected: child.connected,
          folded: child.folded === 'path' || child.layer === true ? 'path' : '',
          node: child,
        });
        collect(child, child.children);
      }
    })(node, node.children);
    return parts;
  }

  function worstPart(parts) {
    let worst = null;
    for (const part of parts) if (part.problem && (!worst || rank(part.problem) > rank(worst.problem))) worst = part;
    if (!worst) return null;
    return { level: worst.problem, entity: worst.entity, label: worst.label, message: worst.message,
      text: worst.message ? `${worst.label}: ${worst.message}` : worst.label, knock_on_of: worst.knock_on_of };
  }

  // ---------------------------------------------------------------------------- tree walk ----
  // The children a structure should walk: via edges always; part_of edges only with options.showParts. Without it a
  // folded part's own via children (the boards of a part_of CAN bus) are lifted to the owner, in API order, so
  // folding the part never hides an entity.
  function childrenOf(node, options = {}) {
    if (options.showParts) return node.children || [];
    const out = [];
    (function lift(children) {
      for (const child of children || []) {
        if (isPart(child)) lift(child.children);
        else out.push(child);
      }
    })(node.children);
    return out;
  }

  // An empty slot: a port or bus with nothing connected or nothing under it, that reports no problem of its own.
  function isEmpty(node) {
    if (ownProblem(node)) return false;
    if (node.kind === 'port') return node.connected === false || !(node.children || []).length;
    if (node.kind === 'bus' || node.kind === 'group') return !(node.children || []).length;
    return false;
  }

  // Single-host elision (VS Code shows the workspace root only once there are several folders): one host -> its
  // children are the top level; several hosts -> host rows. api-v1 roots the tree at one `host` today.
  function topLevel(state, options = {}) {
    const tree = state && state.tree;
    if (!tree) return { nodes: [], hosts: [], elided: false };
    const hosts = tree.kind === 'host' ? [tree] : (tree.children || []).filter((child) => child.kind === 'host');
    const elide = options.elideSingleHost !== false && hosts.length === 1;
    if (elide) return { nodes: childrenOf(hosts[0], options), hosts, elided: true };
    return { nodes: tree.kind === 'host' ? [tree] : hosts, hosts, elided: false };
  }

  // ------------------------------------------------------------------------------- rows ----
  // The API fields a row carries unchanged (so a renderer can read the same names it read from the API node).
  const API_FIELDS = ['kind', 'icon', 'edge', 'level', 'message', 'own', 'rollup', 'knock_on_of', 'stale', 'connected',
    'hardware_id', 'identity', 'owner', 'id_text', 'last_received', 'age_s', 'expected_period_s', 'stale_after_s',
    'checks', 'actions', 'faults'];

  // A row for one API entity. `extra` may set key, children, defaultOpen, columns (and anything a structure adds).
  function entityRow(node, options = {}, extra = {}) {
    const { label, model, ident } = labelOf(node);
    const parts = options.showParts ? [] : partsOf(node);
    const partEntities = new Set(parts.map((part) => part.entity));
    const part = worstPart(parts);
    const apiHint = hintOf(node);
    // a hint whose origin is one of this row's folded parts is already drawn as status.part
    const hint = apiHint && apiHint.origin && partEntities.has(apiHint.origin) ? null : apiHint;
    const row = { key: node.path };
    for (const field of API_FIELDS) row[field] = node[field] === undefined ? null : node[field];
    Object.assign(row, {
      entity: node.path,
      label,
      model,
      ident,
      apiName: node.name,
      apiDepth: node.depth,
      idText: node.id_text && label.includes(node.id_text) ? '' : (node.id_text || ''),
      parts,
      // note: the part's words beside the label, only when the part is what the row's mark is about (no own problem,
      // or a worse part); otherwise the part waits in the detail panel
      status: { own: ownProblem(node), hint, part, note: part && (!ownProblem(node) || rank(part.level) > rank(ownProblem(node))) ? part.text : '' },
      // the API `hint` field, minus a hint explained by a folded part (renderers ported from v1 read it directly)
      hint: isGrouping(node) ? node.hint : (hint ? node.hint : null),
      title: tooltip(node, label, hint, part),
      columns: {},
      // the seams a structure may fill (docs/round2-seams.md §3): subtitle = a muted second line under the selected /
      // hovered row; lead = an inline note beside the label; suffix / attrs = muted text after the label that is not
      // the id (the Table's ID column keeps idText)
      subtitle: '',
      lead: '',
      suffix: '',
      attrs: [],
      children: [],
      node,
    }, extra);
    return row;
  }

  // A structure's own grouping row (no entity): e.g. P2's "Actuators", P5's "Computer". Its hint is the worst
  // API rollup among its members (each member's rollup as the API sent it; nothing is re-derived from checks).
  function groupRow({ key, label, icon = 'group', kind = 'group', children = [], message = '', defaultOpen, columns = {} }) {
    let hint = null;
    for (const child of children) {
      const level = child.rollup ? child.rollup.level : null;
      const own = child.status ? child.status.own : null;
      // a member's folded part counts too: a structure may fold a layer the API rollup does not place under it
      // (P2's port above an adapter), and its problem is still a problem in this group
      const part = child.status && child.status.part ? child.status.part : null;
      let worst = rank(own) > rank(level) ? own : level;
      if (part && rank(part.level) > rank(worst)) worst = part.level;
      if (PROBLEM.has(worst) && (!hint || rank(worst) > rank(hint.level))) {
        hint = worst === (part && part.level) && rank(part.level) > rank(level)
          ? { level: worst, origin: part.entity, origin_name: part.label, message: part.text }
          : { level: worst, origin: (child.rollup && child.rollup.origin) || child.entity, origin_name: (child.rollup && child.rollup.origin_name) || child.label, message: (child.rollup && child.rollup.message) || child.message || '' };
      }
    }
    return {
      key, entity: null, label, model: '', ident: '', apiName: label, apiDepth: null, kind, icon, edge: null,
      level: hint ? hint.level : null, message, own: { level: null, message: '' }, rollup: hint ? { level: hint.level, origin: hint.origin, origin_name: hint.origin_name, message: hint.message, counts: {}, knock_on: [] } : { level: null, counts: {}, knock_on: [] },
      hint: null, knock_on_of: null, stale: false, connected: true, hardware_id: null, identity: {}, owner: null,
      id_text: '', idText: '', last_received: null, age_s: null, expected_period_s: null, stale_after_s: null,
      checks: 0, actions: 0, faults: 0, parts: [], status: { own: null, hint, part: null, note: '' },
      title: [label, hint ? `below: ${hint.level} · ${hint.message}` : ''].filter(Boolean).join('\n'),
      columns, subtitle: '', lead: '', suffix: '', attrs: [], children, defaultOpen, node: null,
    };
  }

  function tooltip(node, label, hint, part) {
    const lines = [label];
    if (label !== node.name) lines.push(node.name);
    if (node.message) lines.push(node.message);
    if (part) lines.push(`part · ${part.text}`);
    if (hint && !(isGrouping(node) && hint.message === node.message)) lines.push(`below: ${hint.level} · ${hint.message}`);
    if (node.knock_on_of) lines.push('knock-on: explained by a failed ancestor, counted once there');
    if (node.connected === false) lines.push('not connected');
    if (node.id_text) lines.push(`id: ${node.id_text}`);
    return lines.join('\n');
  }

  // Depth, parent, display aliases and the default expansion, filled in after a structure returns its rows.
  function finalize(rows) {
    const seen = new Set();
    (function walk(list, depth, parent) {
      for (const row of list) {
        if (seen.has(row.key)) row.key = `${row.key}#${seen.size}`; // a structure may list one entity twice
        seen.add(row.key);
        row.path = row.key;   // renderer compatibility: v1/v2 address rows by `path`
        row.name = row.label; // renderer compatibility: v1/v2 draw `name`
        row.depth = depth;
        row.parent = parent;
        row.children = row.children || [];
        if (typeof row.defaultOpen !== 'boolean') row.defaultOpen = row.children.length > 0 && Boolean(row.status && row.status.hint);
        if (!row.children.length) row.defaultOpen = false;
        walk(row.children, depth + 1, row.key);
      }
    })(rows, 0, null);
    return rows;
  }

  // ------------------------------------------------------------------------------ query ----
  // Everything a row draws or holds one hover away: label, names, ids, path, message, hardware id, its folded parts,
  // its Table columns and the seam texts (subtitle, lead, suffix, attrs), so "ttyACM0" finds the adapter in P1.
  function searchText(row) {
    const parts = (row.parts || []).map((part) => `${part.name} ${part.label} ${part.message}`);
    const columns = Object.values(row.columns || {}).map((value) => String(value ?? ''));
    return [row.label, row.apiName, row.idText, row.id_text, row.entity, row.message, row.hardware_id, ...parts, ...columns,
      row.subtitle, row.lead, row.suffix, ...(row.attrs || [])].join('\u0000').toLowerCase();
  }

  // Keep the rows that match plus their ancestors; mark matches `hit` and open every kept branch.
  function applyQuery(rows, query) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return rows;
    const keep = (list) => list.reduce((out, row) => {
      const children = keep(row.children || []);
      const hit = searchText(row).includes(q);
      if (hit || children.length) out.push(Object.assign(row, { children, hit, defaultOpen: children.length > 0 }));
      return out;
    }, []);
    return keep(rows);
  }

  // hintScope: 'children' (a structure flag): a worst-below hint is only drawn on a row that shows children. P5 lists
  // an adapter as a leaf beside its bus's boards; the API's hint on it points at rows drawn elsewhere, a stray dot.
  function dropLeafHints(rows) {
    for (const row of rows) {
      if (!row.children.length && row.status && row.status.hint) {
        row.status = { ...row.status, hint: null };
        if (row.entity) row.hint = null;
      }
      dropLeafHints(row.children);
    }
  }

  // toggles[].when: a toggle that only means something in one mode of its structure (P2's Empty ports, Connections
  // only). `when` is an option key (shown while that option is on) or a function(options) -> boolean.
  function toggleShown(toggle, options = {}) {
    if (!toggle || toggle.when === undefined || toggle.when === null) return true;
    if (typeof toggle.when === 'function') return Boolean(toggle.when(options));
    return Boolean(options[toggle.when]);
  }

  // ------------------------------------------------------------------------------ index ----
  function indexRows(rows) {
    const byKey = new Map();
    const byEntity = new Map();
    const flat = [];
    (function walk(list) {
      for (const row of list) {
        byKey.set(row.key, row);
        flat.push(row);
        if (row.entity && !byEntity.has(row.entity)) byEntity.set(row.entity, row.key);
        for (const part of row.parts || []) if (!byEntity.has(part.entity)) byEntity.set(part.entity, row.key);
        walk(row.children);
      }
    })(rows);
    return { byKey, byEntity, flat };
  }

  // ---------------------------------------------------------------------------- registry ----
  root.IOStructures = root.IOStructures || {};
  const structures = () => root.IOStructures;
  const structureIds = () => Object.keys(structures()).filter((id) => typeof structures()[id].rows === 'function')
    .sort((a, b) => ((structures()[a].order ?? 50) - (structures()[b].order ?? 50)) || a.localeCompare(b));

  // The option values a structure starts with: the shared defaults, then its own declared toggles.
  function defaultOptions(structure) {
    const options = { ...DEFAULT_OPTIONS };
    for (const toggle of (structure && structure.toggles) || []) options[toggle.key] = Boolean(toggle.default);
    return options;
  }

  // The one entry point the page uses: rows of one structure for one API state, plus the lookups it needs.
  function build(state, structureId, options = {}) {
    const id = structures()[structureId] ? structureId : structureIds()[0];
    const structure = structures()[id];
    const opts = { ...defaultOptions(structure), ...options };
    const top = topLevel(state, opts);
    let rows = structure ? finalize(structure.rows(state, opts) || []) : [];
    if (opts.query && !(structure && structure.handlesQuery)) rows = applyQuery(rows, opts.query);
    if (structure && structure.hintScope === 'children') dropLeafHints(rows);
    const host = top.hosts[0] || null;
    return {
      structure: id,
      options: opts,
      columns: (structure && structure.columns) || [],
      rows,
      ...indexRows(rows),
      summary: (state && state.summary) || null,
      host: host ? { entity: host.path, name: host.name, id_text: host.id_text, elided: top.elided } : null,
    };
  }

  const IOView = {
    RANK, PROBLEM, DEFAULT_OPTIONS,
    isGrouping, ownProblem, hintOf, labelOf, partLabel, partsOf, worstPart, childrenOf, isEmpty, topLevel,
    entityRow, groupRow, finalize, applyQuery, searchText, indexRows, defaultOptions, structureIds, build, toggleShown,
  };
  root.IOView = IOView;
  if (typeof module !== 'undefined' && module.exports) module.exports = IOView;
})(typeof window !== 'undefined' ? window : globalThis);
